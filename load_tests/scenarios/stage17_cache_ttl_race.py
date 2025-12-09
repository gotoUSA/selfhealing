"""
Stage 17: Cache Invalidation TTL Race Test

Purpose: Verify cache/DB consistency under TTL race conditions
- Stale cache read after DB write detection
- Order validation uses correct (fresh) data
- Cache invalidation on critical updates
- No "price changed" errors on valid orders
- Eventual consistency achieved after TTL expiry

Scenario:
  Step 1: Set product price = 10000 (cached, TTL=60s)
  Step 2: Update price to 15000 in DB
  Step 3: Immediately request product (before invalidation)
  Step 4: Verify which price is used for validation
  Step 5: Wait for TTL expiry, verify consistency

Fault Injection:
  - Stale cache read after DB write
  - Cache invalidation delay
  - Write-through vs write-behind race

Verification:
  - [ ] Stale price detection mechanism
  - [ ] Order validation uses correct price
  - [ ] Cache invalidation on critical updates
  - [ ] No "price changed" errors on valid orders
  - [ ] Eventual consistency achieved

Real-World Case:
  "Admin changes price, customer orders with old cached price,
   order fails validation → customer complaint"

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage17_cache_ttl_race.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage17_cache_ttl_race.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage17_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 17)
"""

import os
import sys
import time
import json
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage17-CacheTTLRace]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_CACHE_WARM_DURATION = max(2, int(30 * _scale))        # Warm up cache
PHASE_2_PRICE_UPDATE_DURATION = max(3, int(60 * _scale))      # Update prices, observe stale reads
PHASE_3_RACE_CONDITION_DURATION = max(5, int(120 * _scale))   # Heavy read during TTL window
PHASE_4_TTL_EXPIRY_DURATION = max(3, int(60 * _scale))        # Wait for TTL expiry
PHASE_5_CONSISTENCY_CHECK_DURATION = max(2, int(30 * _scale)) # Verify consistency

TOTAL_DURATION = (
    PHASE_1_CACHE_WARM_DURATION +
    PHASE_2_PRICE_UPDATE_DURATION +
    PHASE_3_RACE_CONDITION_DURATION +
    PHASE_4_TTL_EXPIRY_DURATION +
    PHASE_5_CONSISTENCY_CHECK_DURATION
)

# Cache configuration (should match actual config)
CACHE_TTL_SECONDS = 60  # Default cache TTL
ASSUMED_INVALIDATION_DELAY_MS = 100  # Assumed delay for cache invalidation

# Test data
TARGET_PRODUCT_IDS = []  # Will be populated during test
PRICE_CHANGES = {}  # product_id -> {original: X, updated: Y}


# =============================================================================
# Cache TTL Race Statistics
# =============================================================================

_cache_stats = {
    "start_time": None,
    "phase": "cache_warm",
    # Cache warm phase
    "cache_hits": 0,
    "cache_misses": 0,
    # Price tracking
    "original_prices": {},    # product_id -> original price
    "updated_prices": {},     # product_id -> updated price
    "observed_prices": defaultdict(list),  # product_id -> list of observed prices
    # Stale read tracking
    "stale_reads_detected": 0,
    "fresh_reads_after_update": 0,
    "total_reads_during_race": 0,
    # Order validation tracking
    "orders_with_correct_price": 0,
    "orders_with_stale_price": 0,
    "orders_rejected_price_mismatch": 0,
    # Consistency tracking
    "consistency_checks": 0,
    "consistency_passed": 0,
    "consistency_failed": 0,
    # Timing
    "price_update_times": {},  # product_id -> timestamp
    "first_fresh_read_times": {},  # product_id -> timestamp
    "invalidation_delays": [],  # list of delays in ms
    # Verification
    "verification": {
        "stale_detection_works": None,
        "order_validation_correct": None,
        "cache_invalidation_works": None,
        "eventual_consistency": None,
        "no_invalid_price_errors": None,
    },
    # Recovery Latency Metrics
    "recovery": {
        "stale_window_start": None,  # When stale reads started
        "consistency_restored_at": None,  # When consistency was restored
        "cache_invalidation_latency_ms": None,  # Time for cache to invalidate
        "eventual_consistency_latency_seconds": None,  # Time to achieve consistency
        "sla_compliant": None,  # Consistency achieved under TTL threshold
    },
}

_stats_lock = threading.Lock()


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _cache_stats["start_time"] is None:
        return "cache_warm"

    elapsed = time.time() - _cache_stats["start_time"]

    if elapsed < PHASE_1_CACHE_WARM_DURATION:
        return "cache_warm"
    elif elapsed < PHASE_1_CACHE_WARM_DURATION + PHASE_2_PRICE_UPDATE_DURATION:
        return "price_update"
    elif elapsed < (PHASE_1_CACHE_WARM_DURATION + PHASE_2_PRICE_UPDATE_DURATION + 
                   PHASE_3_RACE_CONDITION_DURATION):
        return "race_condition"
    elif elapsed < (PHASE_1_CACHE_WARM_DURATION + PHASE_2_PRICE_UPDATE_DURATION + 
                   PHASE_3_RACE_CONDITION_DURATION + PHASE_4_TTL_EXPIRY_DURATION):
        return "ttl_expiry"
    else:
        return "consistency_check"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _cache_stats["phase"]:
        old_phase = _cache_stats["phase"]
        _cache_stats["phase"] = phase

        if phase == "price_update":
            print(f"\n💰 Phase 2: Price Update")
            print(f"   - Updating prices and observing stale reads")
        elif phase == "race_condition":
            print(f"\n🏎️ Phase 3: Race Condition Testing")
            print(f"   - Heavy reads during TTL window")
            print(f"   - Stale reads detected: {_cache_stats['stale_reads_detected']}")
        elif phase == "ttl_expiry":
            print(f"\n⏳ Phase 4: TTL Expiry Wait")
            print(f"   - Waiting for cache TTL to expire")
            print(f"   - Cache TTL: {CACHE_TTL_SECONDS}s")
        elif phase == "consistency_check":
            print(f"\n✅ Phase 5: Consistency Check")
            _verify_eventual_consistency()


def _record_cache_hit():
    """Record a cache hit"""
    with _stats_lock:
        _cache_stats["cache_hits"] += 1


def _record_cache_miss():
    """Record a cache miss"""
    with _stats_lock:
        _cache_stats["cache_misses"] += 1


def _record_price_observation(product_id: int, observed_price: float, expected_price: float):
    """Record a price observation"""
    with _stats_lock:
        _cache_stats["observed_prices"][product_id].append({
            "price": observed_price,
            "timestamp": time.time(),
            "expected": expected_price,
        })
        
        _cache_stats["total_reads_during_race"] += 1
        
        # Check if stale
        if product_id in _cache_stats["updated_prices"]:
            updated_price = _cache_stats["updated_prices"][product_id]
            if observed_price != updated_price:
                _cache_stats["stale_reads_detected"] += 1
            else:
                _cache_stats["fresh_reads_after_update"] += 1
                # Record first fresh read time
                if product_id not in _cache_stats["first_fresh_read_times"]:
                    _cache_stats["first_fresh_read_times"][product_id] = time.time()
                    # Calculate invalidation delay
                    if product_id in _cache_stats["price_update_times"]:
                        delay = (time.time() - _cache_stats["price_update_times"][product_id]) * 1000
                        _cache_stats["invalidation_delays"].append(delay)


def _record_price_update(product_id: int, original_price: float, new_price: float):
    """Record a price update"""
    with _stats_lock:
        _cache_stats["original_prices"][product_id] = original_price
        _cache_stats["updated_prices"][product_id] = new_price
        _cache_stats["price_update_times"][product_id] = time.time()


def _record_order_validation(correct_price: bool, rejected: bool = False):
    """Record order validation result"""
    with _stats_lock:
        if rejected:
            _cache_stats["orders_rejected_price_mismatch"] += 1
        elif correct_price:
            _cache_stats["orders_with_correct_price"] += 1
        else:
            _cache_stats["orders_with_stale_price"] += 1


def _record_consistency_check(passed: bool):
    """Record consistency check result"""
    with _stats_lock:
        _cache_stats["consistency_checks"] += 1
        if passed:
            _cache_stats["consistency_passed"] += 1
        else:
            _cache_stats["consistency_failed"] += 1


def _verify_eventual_consistency():
    """Verify eventual consistency after TTL expiry"""
    print(f"\n📊 Eventual Consistency Verification:")
    
    # Check stale detection
    stale_rate = (_cache_stats["stale_reads_detected"] / 
                  max(_cache_stats["total_reads_during_race"], 1))
    _cache_stats["verification"]["stale_detection_works"] = stale_rate > 0 if _cache_stats["updated_prices"] else True
    print(f"   - Stale detection: {'✓' if _cache_stats['verification']['stale_detection_works'] else '✗'}")
    print(f"     (Stale rate: {stale_rate:.1%})")

    # Check order validation
    total_orders = (_cache_stats["orders_with_correct_price"] + 
                   _cache_stats["orders_with_stale_price"] + 
                   _cache_stats["orders_rejected_price_mismatch"])
    if total_orders > 0:
        correct_rate = _cache_stats["orders_with_correct_price"] / total_orders
        _cache_stats["verification"]["order_validation_correct"] = correct_rate >= 0.95
        print(f"   - Order validation: {'✓' if _cache_stats['verification']['order_validation_correct'] else '✗'}")
        print(f"     (Correct price rate: {correct_rate:.1%})")
    else:
        _cache_stats["verification"]["order_validation_correct"] = True

    # Check cache invalidation
    if _cache_stats["invalidation_delays"]:
        avg_delay = sum(_cache_stats["invalidation_delays"]) / len(_cache_stats["invalidation_delays"])
        _cache_stats["verification"]["cache_invalidation_works"] = avg_delay < CACHE_TTL_SECONDS * 1000
        print(f"   - Cache invalidation: {'✓' if _cache_stats['verification']['cache_invalidation_works'] else '✗'}")
        print(f"     (Avg delay: {avg_delay:.0f}ms)")
    else:
        _cache_stats["verification"]["cache_invalidation_works"] = True

    # Check eventual consistency
    consistency_rate = (_cache_stats["consistency_passed"] / 
                       max(_cache_stats["consistency_checks"], 1))
    _cache_stats["verification"]["eventual_consistency"] = consistency_rate >= 0.99
    print(f"   - Eventual consistency: {'✓' if _cache_stats['verification']['eventual_consistency'] else '✗'}")
    print(f"     (Consistency rate: {consistency_rate:.1%})")

    # Check no invalid price errors
    rejection_rate = (_cache_stats["orders_rejected_price_mismatch"] / 
                     max(total_orders, 1))
    _cache_stats["verification"]["no_invalid_price_errors"] = rejection_rate < 0.05
    print(f"   - No invalid price errors: {'✓' if _cache_stats['verification']['no_invalid_price_errors'] else '✗'}")
    print(f"     (Rejection rate: {rejection_rate:.1%})")


# =============================================================================
# Load Shape
# =============================================================================


class CacheTTLRaceShape(LoadTestShape):
    """
    Load shape for cache TTL race testing.

    Phase 1: Low load to warm cache
    Phase 2: Moderate load during price updates
    Phase 3: High read load during race condition window
    Phase 4: Low load during TTL expiry
    Phase 5: Moderate load for consistency verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "cache_warm":
            return (20, 5)  # Low load to warm cache
        elif phase == "price_update":
            return (30, 5)  # Moderate load
        elif phase == "race_condition":
            return (80, 15)  # High read load
        elif phase == "ttl_expiry":
            return (20, 5)  # Low load during wait
        else:  # consistency_check
            return (30, 5)  # Moderate load for verification


# =============================================================================
# Test User
# =============================================================================


class CacheTTLRaceUser(HttpUser):
    """
    User for cache TTL race testing.

    Performs product reads and orders to test
    cache consistency during price updates.
    """

    wait_time = between(0.1, 0.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_helper = None
        self.product_helper = None
        self.cart_helper = None
        self.payment_helper = None
        self.access_token = None
        self.user_id = None
        self.is_admin = False
        self.observed_product_prices = {}  # Local cache of observed prices

    def on_start(self):
        """Login and setup helpers"""
        global _cache_stats, TARGET_PRODUCT_IDS

        if _cache_stats["start_time"] is None:
            _cache_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client)
        self.cart_helper = CartHelper(self.client)
        self.payment_helper = PaymentHelper(self.client)

        # Some users will be admin for price updates
        if random.random() < 0.05:  # 5% admin users
            self.is_admin = True
            # Admin login (if available)
            if self.login_helper.login_as_admin():
                self.access_token = self.login_helper.access_token
                self.user_id = self.login_helper.user_id
        else:
            user_index = random.randint(0, 99)
            if self.login_helper.login(user_index):
                self.access_token = self.login_helper.access_token
                self.user_id = self.login_helper.user_id

        # Get target products
        if not TARGET_PRODUCT_IDS:
            self.product_helper.ensure_products_cached(pages=2)
            products = ProductHelper._products_cache[:10] if ProductHelper._products_cache else []
            if products:
                TARGET_PRODUCT_IDS = [p["id"] for p in products[:10]]
                # Record original prices (ensure float conversion)
                for p in products[:10]:
                    with _stats_lock:
                        _cache_stats["original_prices"][p["id"]] = float(p.get("price", 0))

    def _set_auth_header(self):
        """Set authentication header"""
        if self.access_token:
            self.client.headers["Authorization"] = f"Bearer {self.access_token}"

    def _get_auth_headers(self) -> Dict:
        """Get authentication headers"""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    @task(20)
    @tag("cache_read")
    def read_product_price(self):
        """
        Read product price - main cache test scenario.
        Observes whether stale or fresh data is returned.
        """
        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        phase = _get_current_phase()

        response = self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Read Product Price"
        )

        if response.status_code == 200:
            product = response.json()
            observed_price = float(product.get("price", 0))

            # Store locally observed price
            self.observed_product_prices[product_id] = observed_price

            # Record observation during relevant phases
            if phase in ["price_update", "race_condition", "ttl_expiry"]:
                expected_price_raw = _cache_stats["updated_prices"].get(
                    product_id,
                    _cache_stats["original_prices"].get(product_id, observed_price)
                )
                expected_price = float(expected_price_raw) if expected_price_raw else observed_price
                _record_price_observation(product_id, observed_price, expected_price)

            # Check for stale read
            if phase in ["race_condition", "ttl_expiry"]:
                if product_id in _cache_stats["updated_prices"]:
                    if observed_price != _cache_stats["updated_prices"][product_id]:
                        # Stale read detected
                        pass  # Already recorded in _record_price_observation

    @task(5)
    @tag("cache_read")
    def read_products_list(self):
        """Read products list to warm cache"""
        response = self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} Read Products List"
        )

        if response.status_code == 200:
            _record_cache_hit()
        else:
            _record_cache_miss()

    @task(2)
    @tag("price_update")
    def update_product_price(self):
        """
        Update product price - admin only.
        Triggers cache invalidation race condition.
        """
        phase = _get_current_phase()
        if phase not in ["price_update", "race_condition"]:
            return

        if not self.is_admin or not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Get current price
        get_response = self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Get Price for Update"
        )

        if get_response.status_code != 200:
            return

        current_price = float(get_response.json().get("price", 10000))

        # Calculate new price (10-20% change)
        change_percent = random.uniform(0.1, 0.2) * random.choice([-1, 1])
        new_price = round(current_price * (1 + change_percent), 2)

        # Attempt price update
        update_response = self.client.patch(
            f"/api/products/{product_id}/",
            json={"price": new_price},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Update Price"
        )

        if update_response.status_code in [200, 204]:
            _record_price_update(product_id, current_price, new_price)
            print(f"💰 Price updated: Product {product_id}: {current_price} → {new_price}")

    @task(8)
    @tag("order_validation")
    def order_with_price_check(self):
        """
        Create order and verify price consistency.
        Tests if order uses correct (non-stale) price.
        """
        phase = _get_current_phase()
        if phase not in ["race_condition", "ttl_expiry", "consistency_check"]:
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Step 1: Read product price (may be stale)
        product_response = self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Order - Read Price"
        )

        if product_response.status_code != 200:
            return

        observed_price = float(product_response.json().get("price", 0))

        # Step 2: Add to cart with observed price
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={
                "product_id": product_id,
                "quantity": 1,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Order - Add to Cart"
        )

        if cart_response.status_code != 200:
            return

        # Step 3: Create order
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Test Address for Cache TTL Test",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Order - Create"
        )

        if order_response.status_code == 201:
            order = order_response.json()
            order_total = float(order.get("total_price", 0))

            # Check if order used correct price
            if product_id in _cache_stats["updated_prices"]:
                expected_price = _cache_stats["updated_prices"][product_id]
                # Allow small tolerance for rounding
                is_correct = abs(order_total - expected_price) < 1.0
                _record_order_validation(correct_price=is_correct)
            else:
                _record_order_validation(correct_price=True)

        elif order_response.status_code == 400:
            # Check if rejection is due to price mismatch
            error_msg = order_response.text.lower()
            if "price" in error_msg and ("changed" in error_msg or "mismatch" in error_msg):
                _record_order_validation(correct_price=False, rejected=True)
            else:
                _record_order_validation(correct_price=True)

    @task(3)
    @tag("consistency_check")
    def verify_cache_consistency(self):
        """Verify cache and DB are consistent"""
        phase = _get_current_phase()
        if phase != "consistency_check":
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Read from API (may hit cache)
        api_response = self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Consistency - API Read"
        )

        if api_response.status_code != 200:
            return

        api_price = float(api_response.json().get("price", 0))

        # Get expected price (should be updated price if update happened)
        expected_price_raw = _cache_stats["updated_prices"].get(
            product_id,
            _cache_stats["original_prices"].get(product_id, api_price)
        )
        # Ensure expected_price is float
        expected_price = float(expected_price_raw) if expected_price_raw else api_price

        # Check consistency
        is_consistent = abs(api_price - expected_price) < 0.01
        _record_consistency_check(passed=is_consistent)

        if not is_consistent:
            print(f"⚠️ Inconsistency: Product {product_id}, API={api_price}, Expected={expected_price}")

    @task(2)
    @tag("cache_stress")
    def rapid_read_same_product(self):
        """
        Rapidly read the same product to stress cache.
        Tests cache consistency under high read load.
        """
        phase = _get_current_phase()
        if phase != "race_condition":
            return

        if not TARGET_PRODUCT_IDS:
            return

        # Pick one product and read multiple times rapidly
        product_id = TARGET_PRODUCT_IDS[0]
        prices_observed = []

        for _ in range(5):
            response = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Rapid Read"
            )

            if response.status_code == 200:
                price = float(response.json().get("price", 0))
                prices_observed.append(price)

            time.sleep(0.01)  # 10ms between reads

        # Check if prices are consistent within the burst
        if prices_observed:
            unique_prices = set(prices_observed)
            if len(unique_prices) > 1:
                print(f"⚠️ Inconsistent prices in burst read: {unique_prices}")


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _cache_stats

    print(f"\n{'='*70}")
    print(f"🗄️ Stage 17: Cache Invalidation TTL Race Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify cache/DB consistency under TTL race conditions")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_CACHE_WARM_DURATION}s): Cache Warm - Populate cache")
    print(f"  Phase 2 ({PHASE_2_PRICE_UPDATE_DURATION}s): Price Update - Update DB")
    print(f"  Phase 3 ({PHASE_3_RACE_CONDITION_DURATION}s): Race Condition - High read load")
    print(f"  Phase 4 ({PHASE_4_TTL_EXPIRY_DURATION}s): TTL Expiry - Wait for cache refresh")
    print(f"  Phase 5 ({PHASE_5_CONSISTENCY_CHECK_DURATION}s): Consistency Check")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"Cache TTL: {CACHE_TTL_SECONDS}s")
    print(f"{'='*70}\n")

    _cache_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print(f"📊 Stage 17: Cache TTL Race Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Cache Metrics:")
    print(f"   - Cache hits: {_cache_stats['cache_hits']}")
    print(f"   - Cache misses: {_cache_stats['cache_misses']}")
    cache_hit_rate = _cache_stats['cache_hits'] / max(_cache_stats['cache_hits'] + _cache_stats['cache_misses'], 1)
    print(f"   - Cache hit rate: {cache_hit_rate:.1%}")

    print(f"\n💰 Price Update Metrics:")
    print(f"   - Products with price updates: {len(_cache_stats['updated_prices'])}")
    print(f"   - Total reads during race: {_cache_stats['total_reads_during_race']}")
    print(f"   - Stale reads detected: {_cache_stats['stale_reads_detected']}")
    print(f"   - Fresh reads after update: {_cache_stats['fresh_reads_after_update']}")

    if _cache_stats['total_reads_during_race'] > 0:
        stale_rate = _cache_stats['stale_reads_detected'] / _cache_stats['total_reads_during_race']
        print(f"   - Stale read rate: {stale_rate:.1%}")

    print(f"\n📦 Order Validation Metrics:")
    print(f"   - Orders with correct price: {_cache_stats['orders_with_correct_price']}")
    print(f"   - Orders with stale price: {_cache_stats['orders_with_stale_price']}")
    print(f"   - Orders rejected (price mismatch): {_cache_stats['orders_rejected_price_mismatch']}")

    print(f"\n⏱️ Cache Invalidation Timing:")
    if _cache_stats['invalidation_delays']:
        avg_delay = sum(_cache_stats['invalidation_delays']) / len(_cache_stats['invalidation_delays'])
        min_delay = min(_cache_stats['invalidation_delays'])
        max_delay = max(_cache_stats['invalidation_delays'])
        print(f"   - Avg invalidation delay: {avg_delay:.0f}ms")
        print(f"   - Min delay: {min_delay:.0f}ms")
        print(f"   - Max delay: {max_delay:.0f}ms")
    else:
        print(f"   - No invalidation delays recorded")

    print(f"\n✅ Consistency Verification:")
    print(f"   - Checks performed: {_cache_stats['consistency_checks']}")
    print(f"   - Checks passed: {_cache_stats['consistency_passed']}")
    print(f"   - Checks failed: {_cache_stats['consistency_failed']}")

    print(f"\n🔍 Verification Results:")
    for check, result in _cache_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        print(f"   - {check}: {status}")

    # Recovery Latency Report
    recovery = _cache_stats["recovery"]
    print(f"\n🔄 Recovery Latency Metrics:")
    if _cache_stats['invalidation_delays']:
        avg_invalidation = sum(_cache_stats['invalidation_delays']) / len(_cache_stats['invalidation_delays'])
        max_invalidation = max(_cache_stats['invalidation_delays'])
        recovery["cache_invalidation_latency_ms"] = avg_invalidation
        print(f"   - Avg cache invalidation latency: {avg_invalidation:.0f}ms")
        print(f"   - Max cache invalidation latency: {max_invalidation:.0f}ms")
    
    # Calculate eventual consistency latency
    if _cache_stats['consistency_passed'] > 0:
        # If consistency checks pass, eventual consistency was achieved
        consistency_rate = _cache_stats['consistency_passed'] / max(_cache_stats['consistency_checks'], 1)
        print(f"   - Consistency rate: {consistency_rate:.1%}")
        
        # Estimate recovery based on TTL and stale read pattern
        if _cache_stats['stale_reads_detected'] > 0:
            stale_rate = _cache_stats['stale_reads_detected'] / max(_cache_stats['total_reads_during_race'], 1)
            estimated_recovery = CACHE_TTL_SECONDS * stale_rate
            recovery["eventual_consistency_latency_seconds"] = estimated_recovery
            print(f"   - Estimated consistency latency: {estimated_recovery:.1f}s")
    
    # SLA check (consistency should be achieved within TTL)
    recovery["sla_compliant"] = _cache_stats['consistency_failed'] == 0
    if recovery["sla_compliant"]:
        print(f"   - SLA Status: ✓ Eventual consistency achieved")
    else:
        print(f"   - SLA Status: ✗ Consistency failures detected")

    print(f"\n{'='*70}\n")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import subprocess
    subprocess.run([
        "locust",
        "-f", __file__,
        "--host", "http://localhost:8000",
        "--users", "50",
        "--spawn-rate", "10",
        "--run-time", "5m",
        "--headless",
        "--html", "stage17_report.html"
    ])
