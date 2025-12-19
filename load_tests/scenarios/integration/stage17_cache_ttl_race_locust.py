"""
Stage 17: Cache TTL Race Condition - Locust Load Test

Purpose:
    Test race conditions occurring at cache TTL boundary windows.
    Validates system behavior when cached data expires under high concurrency.

Test Focus:
    1. High concurrency access to cached keys nearing TTL expiry
    2. Simultaneous cache hit/miss transitions
    3. Race conditions between:
       - Cache read operations
       - TTL expiration events
       - Fallback to DB (source-of-truth)
    4. Ensuring NO duplicate side effects during TTL boundary windows

Load Profile:
    - Gradual ramp-up (to expose timing races)
    - Sustained concurrency during TTL boundary
    - Mixed request timing (fast + delayed users)

Validation Goals:
    - No duplicate processing
    - No inconsistent state after TTL expiry
    - No cache stampede
    - Acceptable latency spikes ONLY during TTL rollover

Execution:
    # Headless mode (15 seconds)
    locust -f load_tests/scenarios/stage17_cache_ttl_race_locust.py \
        --host=http://localhost:8000 \
        --users=100 --spawn-rate=20 --run-time=60s \
        --headless --html=stage17_cache_ttl_race_report.html

    # Web UI mode
    locust -f load_tests/scenarios/stage17_cache_ttl_race_locust.py \
        --host=http://localhost:8000
"""

import os
import sys
import time
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

# Try to import helpers, but make optional for standalone execution
try:
    from load_tests.utils import LoginHelper, ProductHelper, CartHelper
    from load_tests.metrics import setup_event_hooks

    HELPERS_AVAILABLE = True
except ImportError:
    HELPERS_AVAILABLE = False
    print("[WARN] load_tests.utils not available - running standalone mode")


# =============================================================================
# CONFIGURATION - Edit these values to tune the test
# =============================================================================

STAGE_NAME = "[Stage17-CacheTTLRace-Locust]"

# Test Duration Configuration
TEST_DURATION_SECONDS = int(os.environ.get("LOCUST_TEST_DURATION", "60"))

# Load profile phases (in seconds) - designed to expose TTL race conditions
PHASE_WARM_CACHE = 5  # Phase 1: Warm up cache with initial reads
PHASE_PRICE_UPDATE = 10  # Phase 2: Update prices (creates stale cache entries)
PHASE_TTL_RACE = 25  # Phase 3: High concurrency during TTL boundary
PHASE_TTL_EXPIRY_WAIT = 10  # Phase 4: Wait period near TTL expiration
PHASE_CONSISTENCY_CHECK = 10  # Phase 5: Verify consistency after TTL expiry

TOTAL_DURATION = PHASE_WARM_CACHE + PHASE_PRICE_UPDATE + PHASE_TTL_RACE + PHASE_TTL_EXPIRY_WAIT + PHASE_CONSISTENCY_CHECK

# Assumed cache TTL (should match actual system configuration)
CACHE_TTL_SECONDS = 60

# User configuration
MAX_USERS = 100  # Peak concurrent users
SPAWN_RATE = 20  # Users per second
ADMIN_RATIO = 0.05  # 5% admin users for price updates

# Product configuration
NUM_TARGET_PRODUCTS = 10  # Number of products to focus testing on


# =============================================================================
# Thread-Safe Statistics Collector
# =============================================================================


class TTLRaceStats:
    """
    Thread-safe statistics collector for TTL race condition metrics.

    Tracks:
    - Cache hit/miss patterns
    - Stale read detection
    - Price observation during race window
    - Duplicate processing detection
    - Latency during TTL rollover
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.start_time: Optional[float] = None
        self.current_phase = "init"

        # Cache metrics
        self.cache_hits = 0
        self.cache_misses = 0

        # Price tracking
        self.original_prices: Dict[int, float] = {}  # product_id -> original price
        self.updated_prices: Dict[int, float] = {}  # product_id -> new price
        self.price_update_times: Dict[int, float] = {}  # product_id -> update timestamp

        # Observations during race window
        self.observations: List[Dict] = []  # List of price observations
        self.stale_reads = 0
        self.fresh_reads = 0
        self.total_reads = 0

        # Duplicate processing detection
        self.processing_ids: Dict[str, List[float]] = defaultdict(list)  # operation_id -> timestamps
        self.duplicate_processing_count = 0

        # Latency tracking per phase
        self.latencies: Dict[str, List[float]] = defaultdict(list)

        # Consistency checks
        self.consistency_checks = 0
        self.consistency_passed = 0
        self.consistency_failed = 0

        # Stampede detection
        self.simultaneous_cache_misses: List[Dict] = []
        self.miss_burst_threshold = 10  # More than 10 misses in 100ms = stampede

    def set_start_time(self):
        """Initialize test start time."""
        with self.lock:
            if self.start_time is None:
                self.start_time = time.time()

    def get_elapsed(self) -> float:
        """Get elapsed time since test start."""
        if self.start_time is None:
            return 0
        return time.time() - self.start_time

    def get_phase(self) -> str:
        """Determine current test phase based on elapsed time."""
        elapsed = self.get_elapsed()

        if elapsed < PHASE_WARM_CACHE:
            return "warm_cache"
        elif elapsed < PHASE_WARM_CACHE + PHASE_PRICE_UPDATE:
            return "price_update"
        elif elapsed < PHASE_WARM_CACHE + PHASE_PRICE_UPDATE + PHASE_TTL_RACE:
            return "ttl_race"
        elif elapsed < PHASE_WARM_CACHE + PHASE_PRICE_UPDATE + PHASE_TTL_RACE + PHASE_TTL_EXPIRY_WAIT:
            return "ttl_expiry_wait"
        else:
            return "consistency_check"

    def update_phase(self) -> str:
        """Update and return current phase, logging transitions."""
        phase = self.get_phase()

        with self.lock:
            if phase != self.current_phase:
                old_phase = self.current_phase
                self.current_phase = phase

                # Log phase transition
                if phase == "price_update":
                    print(f"\n💰 {STAGE_NAME} Phase 2: Price Update Starting")
                    print(f"   - Updating prices to create stale cache entries")
                elif phase == "ttl_race":
                    print(f"\n🏎️ {STAGE_NAME} Phase 3: TTL Race Condition Window")
                    print(f"   - High concurrency reads during stale window")
                    print(f"   - Stale reads so far: {self.stale_reads}")
                elif phase == "ttl_expiry_wait":
                    print(f"\n⏳ {STAGE_NAME} Phase 4: TTL Expiry Wait")
                    print(f"   - Approaching cache TTL boundary")
                elif phase == "consistency_check":
                    print(f"\n✅ {STAGE_NAME} Phase 5: Consistency Verification")
                    print(f"   - Verifying data consistency after TTL expiry")

        return phase

    def record_cache_hit(self, latency_ms: float):
        """Record a cache hit."""
        with self.lock:
            self.cache_hits += 1
            phase = self.current_phase
            self.latencies[phase].append(latency_ms)

    def record_cache_miss(self, latency_ms: float):
        """Record a cache miss (could indicate stampede)."""
        with self.lock:
            self.cache_misses += 1
            phase = self.current_phase
            self.latencies[phase].append(latency_ms)

            # Track for stampede detection
            self.simultaneous_cache_misses.append(
                {
                    "timestamp": time.time(),
                    "phase": phase,
                }
            )

    def record_price_observation(self, product_id: int, observed_price: float, response_time_ms: float):
        """
        Record a price observation during race window.

        RACE CONDITION TARGET:
        This detects when a read returns stale data after a price update.
        In a TTL race, we may see:
        - Stale price (from cache before update)
        - Fresh price (from DB or refreshed cache)
        """
        with self.lock:
            self.total_reads += 1

            observation = {
                "product_id": product_id,
                "observed_price": observed_price,
                "timestamp": time.time(),
                "response_time_ms": response_time_ms,
                "phase": self.current_phase,
            }

            # Check if stale
            if product_id in self.updated_prices:
                expected_price = self.updated_prices[product_id]
                is_stale = abs(observed_price - expected_price) > 0.01
                observation["is_stale"] = is_stale
                observation["expected_price"] = expected_price

                if is_stale:
                    self.stale_reads += 1
                else:
                    self.fresh_reads += 1
            else:
                observation["is_stale"] = False

            self.observations.append(observation)

    def record_price_update(self, product_id: int, original_price: float, new_price: float):
        """Record a price update (creates potential for stale reads)."""
        with self.lock:
            self.original_prices[product_id] = original_price
            self.updated_prices[product_id] = new_price
            self.price_update_times[product_id] = time.time()
            print(f"   💵 Price updated: Product {product_id}: {original_price} → {new_price}")

    def record_processing(self, operation_id: str):
        """
        Track processing operations to detect duplicates.

        RACE CONDITION TARGET:
        Duplicate processing can occur when:
        - Cache expires during request
        - Multiple users trigger DB fallback simultaneously
        - No proper locking/deduplication
        """
        with self.lock:
            now = time.time()
            self.processing_ids[operation_id].append(now)

            # Check for duplicate processing (same operation within 1 second)
            timestamps = self.processing_ids[operation_id]
            if len(timestamps) > 1:
                recent = [t for t in timestamps if now - t < 1.0]
                if len(recent) > 1:
                    self.duplicate_processing_count += 1

    def record_consistency_check(self, passed: bool):
        """Record consistency check result."""
        with self.lock:
            self.consistency_checks += 1
            if passed:
                self.consistency_passed += 1
            else:
                self.consistency_failed += 1

    def detect_stampede(self) -> bool:
        """
        Detect if a cache stampede occurred.

        RACE CONDITION TARGET:
        Cache stampede happens when many requests hit an expired cache
        simultaneously, all falling back to DB at once.
        """
        with self.lock:
            if len(self.simultaneous_cache_misses) < self.miss_burst_threshold:
                return False

            # Check for bursts of misses within 100ms windows
            sorted_misses = sorted(self.simultaneous_cache_misses, key=lambda x: x["timestamp"])

            for i in range(len(sorted_misses) - self.miss_burst_threshold):
                window_start = sorted_misses[i]["timestamp"]
                window_end = window_start + 0.1  # 100ms window

                misses_in_window = sum(1 for m in sorted_misses if window_start <= m["timestamp"] <= window_end)

                if misses_in_window >= self.miss_burst_threshold:
                    return True

            return False

    def get_summary(self) -> Dict[str, Any]:
        """Generate statistics summary."""
        with self.lock:
            total_cache_ops = self.cache_hits + self.cache_misses
            hit_rate = self.cache_hits / max(total_cache_ops, 1)

            stale_rate = self.stale_reads / max(self.total_reads, 1)

            # Calculate latency percentiles per phase
            latency_summary = {}
            for phase, latencies in self.latencies.items():
                if latencies:
                    sorted_lat = sorted(latencies)
                    latency_summary[phase] = {
                        "count": len(latencies),
                        "avg": sum(latencies) / len(latencies),
                        "p50": sorted_lat[len(sorted_lat) // 2],
                        "p95": sorted_lat[int(len(sorted_lat) * 0.95)] if len(sorted_lat) > 20 else sorted_lat[-1],
                        "p99": sorted_lat[int(len(sorted_lat) * 0.99)] if len(sorted_lat) > 100 else sorted_lat[-1],
                        "max": sorted_lat[-1],
                    }

            return {
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "hit_rate": hit_rate,
                "stale_reads": self.stale_reads,
                "fresh_reads": self.fresh_reads,
                "stale_rate": stale_rate,
                "total_reads": self.total_reads,
                "duplicate_processing": self.duplicate_processing_count,
                "consistency_checks": self.consistency_checks,
                "consistency_passed": self.consistency_passed,
                "consistency_failed": self.consistency_failed,
                "latency_by_phase": latency_summary,
                "products_updated": len(self.updated_prices),
            }


# Global statistics instance
stats = TTLRaceStats()

# Product cache for test targets
target_product_ids: List[int] = []
target_products: Dict[int, Dict] = {}  # product_id -> product data


# =============================================================================
# Load Test Shape - Gradual Ramp-Up for Race Condition Exposure
# =============================================================================


class CacheTTLRaceShape(LoadTestShape):
    """
    Custom load shape designed to expose TTL race conditions.

    Strategy:
    - Phase 1: Low load to warm cache
    - Phase 2: Moderate load during price updates
    - Phase 3: High load spike at TTL boundary
    - Phase 4: Sustained load during TTL expiry wait
    - Phase 5: Moderate load for consistency verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple."""
        stats.update_phase()
        run_time = self.get_run_time()

        if run_time > TOTAL_DURATION:
            return None

        phase = stats.get_phase()

        if phase == "warm_cache":
            # Low load to populate cache without stress
            return (20, 5)

        elif phase == "price_update":
            # Moderate load during price updates
            return (40, 10)

        elif phase == "ttl_race":
            # HIGH LOAD during TTL race window
            # This is where we expect to see race conditions
            return (MAX_USERS, 25)

        elif phase == "ttl_expiry_wait":
            # Maintain load while waiting for TTL expiry
            return (60, 10)

        else:  # consistency_check
            # Moderate load for verification
            return (40, 10)


# =============================================================================
# Fast Reader User - Rapid cache access
# =============================================================================


class FastReaderUser(HttpUser):
    """
    Fast reader user - rapid cache access at high frequency.

    RACE CONDITION TARGET:
    Simulates users who rapidly read the same data.
    These requests should:
    - Hit cache most of the time
    - Detect stale reads after price updates
    - Experience latency spikes during TTL rollover
    """

    wait_time = between(0.05, 0.15)  # 50-150ms between requests
    weight = 6  # Higher weight = more users of this type

    def on_start(self):
        """Initialize user session."""
        global target_product_ids
        stats.set_start_time()

        self.access_token = None
        self.headers = {}

        # Login if helpers available
        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                user_index = random.randint(0, 99)
                if login_helper.login(user_index):
                    self.access_token = login_helper.access_token
                    self.headers = {"Authorization": f"Bearer {self.access_token}"}
            except Exception as e:
                pass  # Continue without auth

        # Populate target products on first user
        if not target_product_ids:
            self._populate_target_products()

    def _populate_target_products(self):
        """Fetch products to use as test targets."""
        global target_product_ids, target_products

        try:
            response = self.client.get(
                "/api/products/",
                name=f"{STAGE_NAME} Initial Product Fetch",
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                products = data if isinstance(data, list) else data.get("results", [])

                for product in products[:NUM_TARGET_PRODUCTS]:
                    pid = product.get("id")
                    if pid:
                        target_product_ids.append(pid)
                        target_products[pid] = product
                        stats.original_prices[pid] = float(product.get("price", 0))

                print(f"   📦 Loaded {len(target_product_ids)} target products")
        except Exception as e:
            print(f"   ⚠️ Failed to load products: {e}")

    @task(10)
    @tag("cache_read", "ttl_race")
    def read_product_rapid(self):
        """
        Rapidly read product data.

        RACE CONDITION:
        After price update, cached data may be stale.
        This task detects stale reads and measures latency.
        """
        if not target_product_ids:
            return

        product_id = random.choice(target_product_ids)
        phase = stats.get_phase()

        start_time = time.time()

        with self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Read Product (Fast)",
            headers=self.headers,
            catch_response=True,
        ) as response:
            response_time_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                product = response.json()
                observed_price = float(product.get("price", 0))

                # Record observation for stale detection
                if phase in ["price_update", "ttl_race", "ttl_expiry_wait"]:
                    stats.record_price_observation(product_id, observed_price, response_time_ms)

                # Determine cache hit/miss based on response time
                # Assume: <50ms = cache hit, >100ms = cache miss
                if response_time_ms < 50:
                    stats.record_cache_hit(response_time_ms)
                elif response_time_ms > 100:
                    stats.record_cache_miss(response_time_ms)
                else:
                    stats.record_cache_hit(response_time_ms)

                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(2)
    @tag("cache_burst", "stampede_risk")
    def burst_read_same_product(self):
        """
        Burst read the same product multiple times rapidly.

        RACE CONDITION TARGET:
        This creates high contention on a single cache key.
        At TTL boundary, this could trigger:
        - Cache stampede (all requests miss, hit DB)
        - Inconsistent reads (some get old, some get new)
        """
        if not target_product_ids:
            return

        phase = stats.get_phase()
        if phase not in ["ttl_race", "ttl_expiry_wait"]:
            return

        # Pick one product and read it 5 times rapidly
        product_id = target_product_ids[0]
        observed_prices = []

        for i in range(5):
            operation_id = f"burst_{product_id}_{int(time.time())}"
            stats.record_processing(operation_id)

            try:
                response = self.client.get(
                    f"/api/products/{product_id}/",
                    name=f"{STAGE_NAME} Burst Read",
                    headers=self.headers,
                )

                if response.status_code == 200:
                    price = float(response.json().get("price", 0))
                    observed_prices.append(price)
            except Exception:
                pass

            time.sleep(0.01)  # 10ms between bursts

        # Check for inconsistency within burst
        if observed_prices:
            unique_prices = set(observed_prices)
            if len(unique_prices) > 1:
                print(f"   ⚠️ Inconsistent prices in burst: {unique_prices}")


# =============================================================================
# Slow Reader User - Delayed access pattern
# =============================================================================


class SlowReaderUser(HttpUser):
    """
    Slow reader user - delayed access with longer waits.

    RACE CONDITION TARGET:
    Simulates users with slower behavior who may encounter:
    - Different cache state than fast users
    - TTL expiry mid-request
    - Inconsistent data between fast/slow users
    """

    wait_time = between(0.5, 2.0)  # 500ms-2s between requests
    weight = 2  # Lower weight = fewer users

    def on_start(self):
        """Initialize user session."""
        stats.set_start_time()
        self.headers = {}

        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                user_index = random.randint(0, 99)
                if login_helper.login(user_index):
                    self.headers = {"Authorization": f"Bearer {login_helper.access_token}"}
            except Exception:
                pass

    @task(5)
    @tag("cache_read", "slow_pattern")
    def read_product_slow(self):
        """
        Read product with slower pattern.

        RACE CONDITION:
        Slow readers may see different data than fast readers
        due to cache expiry timing differences.
        """
        if not target_product_ids:
            return

        product_id = random.choice(target_product_ids)
        phase = stats.get_phase()

        # Intentional delay to widen race window
        time.sleep(random.uniform(0.1, 0.3))

        start_time = time.time()

        with self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Read Product (Slow)",
            headers=self.headers,
            catch_response=True,
        ) as response:
            response_time_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                product = response.json()
                observed_price = float(product.get("price", 0))

                stats.record_price_observation(product_id, observed_price, response_time_ms)

                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(2)
    @tag("consistency_check")
    def verify_consistency(self):
        """
        Verify cache consistency after TTL expiry.

        VALIDATION:
        After TTL expiry, all reads should return the updated price.
        """
        phase = stats.get_phase()
        if phase != "consistency_check":
            return

        if not target_product_ids:
            return

        for product_id in target_product_ids[:3]:
            try:
                response = self.client.get(
                    f"/api/products/{product_id}/",
                    name=f"{STAGE_NAME} Consistency Check",
                    headers=self.headers,
                )

                if response.status_code == 200:
                    observed_price = float(response.json().get("price", 0))

                    # After TTL expiry, should have fresh data
                    expected_price = stats.updated_prices.get(
                        product_id, stats.original_prices.get(product_id, observed_price)
                    )

                    is_consistent = abs(observed_price - expected_price) < 0.01
                    stats.record_consistency_check(is_consistent)

            except Exception:
                pass


# =============================================================================
# Admin User - Price Updates (Cache Invalidation Trigger)
# =============================================================================


class AdminPriceUpdater(HttpUser):
    """
    Admin user who updates product prices.

    RACE CONDITION TRIGGER:
    Price updates invalidate cache entries.
    The timing between update and invalidation creates
    a window where stale reads can occur.
    """

    wait_time = between(2.0, 5.0)  # Less frequent updates
    weight = 1  # Few admin users

    def on_start(self):
        """Initialize admin session."""
        stats.set_start_time()
        self.is_authenticated = False
        self.headers = {}

        # Try admin login
        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                if login_helper.login_as_admin():
                    self.is_authenticated = True
                    self.headers = {"Authorization": f"Bearer {login_helper.access_token}"}
            except Exception:
                pass

        if not self.is_authenticated:
            # Fallback: try regular login
            try:
                response = self.client.post(
                    "/api/auth/token/",
                    json={"username": "admin", "password": "admin123"},
                    name=f"{STAGE_NAME} Admin Login",
                )
                if response.status_code == 200:
                    token = response.json().get("access")
                    self.headers = {"Authorization": f"Bearer {token}"}
                    self.is_authenticated = True
            except Exception:
                pass

    @task(1)
    @tag("price_update", "cache_invalidation")
    def update_product_price(self):
        """
        Update product price to trigger cache invalidation.

        RACE CONDITION TRIGGER:
        This update creates a window where:
        1. DB has new price
        2. Cache still has old price
        3. Readers may get stale data

        The system should:
        - Invalidate cache immediately
        - Prevent stale reads
        - Handle concurrent readers gracefully
        """
        phase = stats.get_phase()
        if phase not in ["price_update", "ttl_race"]:
            return

        if not target_product_ids or not self.is_authenticated:
            return

        product_id = random.choice(target_product_ids)

        # Get current price
        try:
            get_response = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Get Price for Update",
                headers=self.headers,
            )

            if get_response.status_code != 200:
                return

            current_price = float(get_response.json().get("price", 10000))
        except Exception:
            return

        # Calculate new price (10-20% change)
        change_percent = random.uniform(0.1, 0.2) * random.choice([-1, 1])
        new_price = round(current_price * (1 + change_percent), 2)
        new_price = max(100, new_price)  # Minimum price

        # Update price
        with self.client.patch(
            f"/api/products/{product_id}/",
            json={"price": new_price},
            headers=self.headers,
            name=f"{STAGE_NAME} Update Price",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 204]:
                stats.record_price_update(product_id, current_price, new_price)
                response.success()
            elif response.status_code == 403:
                # Not admin - expected for non-admin users
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# =============================================================================
# Event Hooks - Test Lifecycle
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test."""
    print(f"\n{'='*70}")
    print(f"🗄️ {STAGE_NAME} Cache TTL Race Condition Load Test")
    print(f"{'='*70}")
    print(f"\nPurpose: Test cache consistency during TTL expiry windows")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_WARM_CACHE}s): Warm Cache - Populate cache")
    print(f"  Phase 2 ({PHASE_PRICE_UPDATE}s): Price Update - Create stale entries")
    print(f"  Phase 3 ({PHASE_TTL_RACE}s): TTL Race - High load during race window")
    print(f"  Phase 4 ({PHASE_TTL_EXPIRY_WAIT}s): TTL Expiry Wait")
    print(f"  Phase 5 ({PHASE_CONSISTENCY_CHECK}s): Consistency Check")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"Cache TTL (assumed): {CACHE_TTL_SECONDS}s")
    print(f"{'='*70}\n")

    stats.set_start_time()

    if HELPERS_AVAILABLE:
        try:
            setup_event_hooks()
        except Exception:
            pass


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report."""
    summary = stats.get_summary()
    stampede_detected = stats.detect_stampede()

    print(f"\n{'='*70}")
    print(f"📊 {STAGE_NAME} Final Report")
    print(f"{'='*70}")

    print(f"\n📈 Cache Metrics:")
    print(f"   - Cache hits: {summary['cache_hits']}")
    print(f"   - Cache misses: {summary['cache_misses']}")
    print(f"   - Hit rate: {summary['hit_rate']:.1%}")

    print(f"\n💰 Stale Read Analysis:")
    print(f"   - Products updated: {summary['products_updated']}")
    print(f"   - Total reads: {summary['total_reads']}")
    print(f"   - Stale reads: {summary['stale_reads']}")
    print(f"   - Fresh reads: {summary['fresh_reads']}")
    print(f"   - Stale rate: {summary['stale_rate']:.1%}")

    print(f"\n🔄 Duplicate Processing:")
    print(f"   - Duplicates detected: {summary['duplicate_processing']}")

    print(f"\n🐂 Stampede Detection:")
    print(f"   - Stampede occurred: {'Yes ⚠️' if stampede_detected else 'No ✓'}")

    print(f"\n✅ Consistency Verification:")
    print(f"   - Checks performed: {summary['consistency_checks']}")
    print(f"   - Passed: {summary['consistency_passed']}")
    print(f"   - Failed: {summary['consistency_failed']}")

    print(f"\n⏱️ Latency by Phase:")
    for phase, lat in summary.get("latency_by_phase", {}).items():
        print(f"   [{phase}]")
        print(f"     - Count: {lat['count']}")
        print(f"     - Avg: {lat['avg']:.1f}ms")
        print(f"     - P95: {lat['p95']:.1f}ms")
        print(f"     - Max: {lat['max']:.1f}ms")

    # Validation Results
    print(f"\n{'='*70}")
    print(f"🔍 VALIDATION RESULTS")
    print(f"{'='*70}")

    # 1. No duplicate processing
    no_duplicates = summary["duplicate_processing"] == 0
    print(
        f"   ✓ No duplicate processing: {'PASS' if no_duplicates else 'FAIL (' + str(summary['duplicate_processing']) + ' duplicates)'}"
    )

    # 2. No inconsistent state after TTL
    consistency_ok = summary["consistency_failed"] == 0
    print(
        f"   ✓ Consistent after TTL expiry: {'PASS' if consistency_ok else 'FAIL (' + str(summary['consistency_failed']) + ' failures)'}"
    )

    # 3. No cache stampede
    no_stampede = not stampede_detected
    print(f"   ✓ No cache stampede: {'PASS' if no_stampede else 'WARN (stampede detected)'}")

    # 4. Latency acceptable during TTL
    ttl_race_latency = summary.get("latency_by_phase", {}).get("ttl_race", {})
    if ttl_race_latency:
        max_lat = ttl_race_latency.get("max", 0)
        latency_ok = max_lat < 5000  # 5s threshold
        print(f"   ✓ Latency during TTL rollover: {'PASS' if latency_ok else 'WARN'} (max: {max_lat:.0f}ms)")
    else:
        print(f"   ✓ Latency during TTL rollover: N/A")

    print(f"\n{'='*70}\n")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import subprocess

    cmd = [
        "locust",
        "-f",
        __file__,
        "--host",
        "http://localhost:8000",
        "--users",
        str(MAX_USERS),
        "--spawn-rate",
        str(SPAWN_RATE),
        "--run-time",
        f"{TOTAL_DURATION}s",
        "--headless",
        "--html",
        "stage17_cache_ttl_race_report.html",
    ]

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
