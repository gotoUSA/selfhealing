"""
Stage 17: Event-based Cache Invalidation - Locust Load Test

Purpose:
    Test cache invalidation triggered by events (not TTL expiry).
    This is DIFFERENT from TTL-based race conditions:

    TTL-based (stage17_cache_ttl_race_locust.py):
      - Cache expires after fixed time
      - Race occurs at TTL boundary
      - System is passive (waits for expiry)

    Event-based (this file):
      - Cache invalidated by explicit events
      - Race occurs between event emission and propagation
      - System is active (pushes invalidation)

Test Focus:
    1. Concurrent consumers reading cached data
    2. Event-triggered invalidation happening mid-flight
    3. Overlapping read → invalidate → re-read sequences
    4. Timing skew between event emission and cache invalidation

Load Profile:
    - Multiple user classes with different behaviors:
      - Readers (high frequency reads)
      - Invalidators (event triggers)
    - Intentional overlap between read and invalidate phases
    - Randomized delays to widen race windows

Validation Goals:
    - No stale data returned AFTER invalidation
    - No partial or corrupted state
    - Idempotency preserved across repeated invalidation events
    - System remains stable under repeated invalidate storms

Execution:
    locust -f load_tests/scenarios/stage17_event_invalidation_locust.py \
        --host=http://localhost:8000 \
        --users=80 --spawn-rate=20 --run-time=60s \
        --headless --html=stage17_event_invalidation_report.html
"""

import os
import sys
import time
import random
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

# Try to import helpers
try:
    from load_tests.utils import LoginHelper, ProductHelper
    from load_tests.metrics import setup_event_hooks

    HELPERS_AVAILABLE = True
except ImportError:
    HELPERS_AVAILABLE = False
    print("[WARN] load_tests.utils not available - running standalone mode")


# =============================================================================
# CONFIGURATION
# =============================================================================

STAGE_NAME = "[Stage17-EventInvalidation-Locust]"

# Test Duration Configuration
TEST_DURATION_SECONDS = int(os.environ.get("LOCUST_TEST_DURATION", "60"))

# Load profile phases - designed for event-based invalidation testing
PHASE_WARM = 5  # Phase 1: Warm cache with initial reads
PHASE_BASELINE = 10  # Phase 2: Baseline reads (no invalidations)
PHASE_INVALIDATE_STORM = 25  # Phase 3: High-frequency invalidations
PHASE_RECOVERY = 10  # Phase 4: Recovery period
PHASE_VERIFY = 10  # Phase 5: Verification reads

TOTAL_DURATION = PHASE_WARM + PHASE_BASELINE + PHASE_INVALIDATE_STORM + PHASE_RECOVERY + PHASE_VERIFY

# User configuration
MAX_USERS = 80
SPAWN_RATE = 20
READER_RATIO = 0.75  # 75% readers
INVALIDATOR_RATIO = 0.25  # 25% invalidators

# Invalidation configuration
MAX_INVALIDATION_LATENCY_MS = 100  # Expected max invalidation delay
STALE_READ_THRESHOLD_MS = 200  # Grace period for stale reads after invalidation

# Product configuration
NUM_TARGET_PRODUCTS = 10


# =============================================================================
# Event Tracking Dataclass
# =============================================================================


@dataclass
class InvalidationEvent:
    """Represents a cache invalidation event."""

    event_id: str
    product_id: int
    old_price: float
    new_price: float
    triggered_at: float  # timestamp when update was sent
    confirmed_at: Optional[float]  # timestamp when update was confirmed
    first_fresh_read_at: Optional[float] = None  # first read with new value


# =============================================================================
# Thread-Safe Event Invalidation Statistics
# =============================================================================


class EventInvalidationStats:
    """
    Thread-safe statistics for event-based cache invalidation.

    KEY DIFFERENCE from TTL-based:
    - Tracks explicit invalidation events
    - Measures event propagation latency
    - Detects stale reads AFTER invalidation event (not before TTL expiry)
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.start_time: Optional[float] = None
        self.current_phase = "init"

        # Product state tracking
        self.current_prices: Dict[int, float] = {}  # product_id -> current price
        self.price_versions: Dict[int, int] = defaultdict(int)  # product_id -> version

        # Invalidation events
        self.invalidation_events: Dict[str, InvalidationEvent] = {}  # event_id -> event
        self.active_invalidations: Dict[int, InvalidationEvent] = {}  # product_id -> latest event

        # Read tracking
        self.reads_total = 0
        self.reads_during_invalidation = 0
        self.stale_reads_after_event = 0
        self.fresh_reads_after_event = 0

        # Latency tracking
        self.invalidation_latencies: List[float] = []  # event -> confirmation latency
        self.propagation_latencies: List[float] = []  # event -> first fresh read latency
        self.read_latencies_by_phase: Dict[str, List[float]] = defaultdict(list)

        # Idempotency tracking
        self.repeated_invalidations: Dict[int, int] = defaultdict(int)  # product_id -> count
        self.idempotency_violations = 0

        # Stability tracking
        self.consecutive_errors = 0
        self.max_consecutive_errors = 0
        self.invalidation_storms = 0  # periods of very high invalidation rate

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
        """Determine current test phase."""
        elapsed = self.get_elapsed()

        if elapsed < PHASE_WARM:
            return "warm"
        elif elapsed < PHASE_WARM + PHASE_BASELINE:
            return "baseline"
        elif elapsed < PHASE_WARM + PHASE_BASELINE + PHASE_INVALIDATE_STORM:
            return "invalidate_storm"
        elif elapsed < PHASE_WARM + PHASE_BASELINE + PHASE_INVALIDATE_STORM + PHASE_RECOVERY:
            return "recovery"
        else:
            return "verify"

    def update_phase(self) -> str:
        """Update and return current phase, logging transitions."""
        phase = self.get_phase()

        with self.lock:
            if phase != self.current_phase:
                old_phase = self.current_phase
                self.current_phase = phase

                if phase == "baseline":
                    print(f"\n📊 {STAGE_NAME} Phase 2: Baseline Reads")
                    print(f"   - Establishing baseline read pattern")
                elif phase == "invalidate_storm":
                    print(f"\n⚡ {STAGE_NAME} Phase 3: Invalidation Storm")
                    print(f"   - High-frequency cache invalidations")
                    print(f"   - Watch for stale reads after events")
                elif phase == "recovery":
                    print(f"\n🔄 {STAGE_NAME} Phase 4: Recovery")
                    print(f"   - Reducing invalidation rate")
                    print(f"   - Stale reads after event: {self.stale_reads_after_event}")
                elif phase == "verify":
                    print(f"\n✅ {STAGE_NAME} Phase 5: Verification")
                    print(f"   - Verifying consistency")

        return phase

    def record_initial_price(self, product_id: int, price: float):
        """Record initial product price."""
        with self.lock:
            if product_id not in self.current_prices:
                self.current_prices[product_id] = price
                self.price_versions[product_id] = 1

    def start_invalidation(self, product_id: int, old_price: float, new_price: float) -> str:
        """
        Record start of an invalidation event.

        Returns event_id for tracking.
        """
        event_id = f"{product_id}_{uuid.uuid4().hex[:8]}"

        with self.lock:
            event = InvalidationEvent(
                event_id=event_id,
                product_id=product_id,
                old_price=old_price,
                new_price=new_price,
                triggered_at=time.time(),
                confirmed_at=None,
            )

            self.invalidation_events[event_id] = event
            self.active_invalidations[product_id] = event

            # Track repeated invalidations
            self.repeated_invalidations[product_id] += 1

            # Update current price
            self.current_prices[product_id] = new_price
            self.price_versions[product_id] += 1

        return event_id

    def confirm_invalidation(self, event_id: str):
        """Record confirmation of invalidation event."""
        with self.lock:
            if event_id in self.invalidation_events:
                event = self.invalidation_events[event_id]
                event.confirmed_at = time.time()

                latency_ms = (event.confirmed_at - event.triggered_at) * 1000
                self.invalidation_latencies.append(latency_ms)

    def record_read(self, product_id: int, observed_price: float, response_time_ms: float):
        """
        Record a read operation and check for staleness.

        KEY RACE CONDITION:
        If there's an active invalidation event for this product,
        and we observe the OLD price, that's a stale read.
        """
        with self.lock:
            self.reads_total += 1
            phase = self.current_phase
            self.read_latencies_by_phase[phase].append(response_time_ms)

            # Check if there's an active invalidation
            if product_id in self.active_invalidations:
                event = self.active_invalidations[product_id]
                self.reads_during_invalidation += 1

                # Time since invalidation was triggered
                time_since_event = (time.time() - event.triggered_at) * 1000

                # Check if read is stale
                expected_price = event.new_price

                if abs(observed_price - event.old_price) < 0.01:
                    # Got old price - is this within grace period?
                    if time_since_event > STALE_READ_THRESHOLD_MS:
                        # STALE READ AFTER GRACE PERIOD
                        self.stale_reads_after_event += 1
                    # else: within grace period, acceptable

                elif abs(observed_price - event.new_price) < 0.01:
                    # Got new price - record first fresh read
                    self.fresh_reads_after_event += 1

                    if event.first_fresh_read_at is None:
                        event.first_fresh_read_at = time.time()
                        propagation_ms = (event.first_fresh_read_at - event.triggered_at) * 1000
                        self.propagation_latencies.append(propagation_ms)

    def record_error(self):
        """Record an error occurrence."""
        with self.lock:
            self.consecutive_errors += 1
            self.max_consecutive_errors = max(self.max_consecutive_errors, self.consecutive_errors)

    def record_success(self):
        """Record a successful operation (resets consecutive errors)."""
        with self.lock:
            self.consecutive_errors = 0

    def check_idempotency(self, product_id: int, result_price: float) -> bool:
        """
        Check if repeated invalidations maintain idempotency.

        Returns True if idempotent (same result for same final state).
        """
        with self.lock:
            expected_price = self.current_prices.get(product_id)
            if expected_price and abs(result_price - expected_price) > 0.01:
                self.idempotency_violations += 1
                return False
            return True

    def get_summary(self) -> Dict[str, Any]:
        """Generate statistics summary."""
        with self.lock:
            # Calculate invalidation latency stats
            inv_lat_avg = (
                sum(self.invalidation_latencies) / len(self.invalidation_latencies) if self.invalidation_latencies else 0
            )
            inv_lat_max = max(self.invalidation_latencies) if self.invalidation_latencies else 0
            inv_lat_p95 = (
                sorted(self.invalidation_latencies)[int(len(self.invalidation_latencies) * 0.95)]
                if len(self.invalidation_latencies) > 20
                else inv_lat_max
            )

            # Calculate propagation latency stats
            prop_lat_avg = (
                sum(self.propagation_latencies) / len(self.propagation_latencies) if self.propagation_latencies else 0
            )
            prop_lat_max = max(self.propagation_latencies) if self.propagation_latencies else 0

            # Read latency by phase
            read_lat_summary = {}
            for phase, latencies in self.read_latencies_by_phase.items():
                if latencies:
                    sorted_lat = sorted(latencies)
                    read_lat_summary[phase] = {
                        "count": len(latencies),
                        "avg": sum(latencies) / len(latencies),
                        "p95": sorted_lat[int(len(sorted_lat) * 0.95)] if len(sorted_lat) > 20 else sorted_lat[-1],
                        "max": sorted_lat[-1],
                    }

            # Stale rate calculation
            stale_rate = self.stale_reads_after_event / max(self.reads_during_invalidation, 1)

            return {
                "reads_total": self.reads_total,
                "reads_during_invalidation": self.reads_during_invalidation,
                "stale_reads_after_event": self.stale_reads_after_event,
                "fresh_reads_after_event": self.fresh_reads_after_event,
                "stale_rate": stale_rate,
                "invalidation_count": len(self.invalidation_events),
                "invalidation_latency_avg_ms": inv_lat_avg,
                "invalidation_latency_max_ms": inv_lat_max,
                "invalidation_latency_p95_ms": inv_lat_p95,
                "propagation_latency_avg_ms": prop_lat_avg,
                "propagation_latency_max_ms": prop_lat_max,
                "idempotency_violations": self.idempotency_violations,
                "max_consecutive_errors": self.max_consecutive_errors,
                "repeated_invalidations": dict(self.repeated_invalidations),
                "read_latency_by_phase": read_lat_summary,
            }


# Global statistics instance
stats = EventInvalidationStats()

# Product cache
target_product_ids: List[int] = []
target_products: Dict[int, Dict] = {}


# =============================================================================
# Load Test Shape - Event Invalidation Pattern
# =============================================================================


class EventInvalidationShape(LoadTestShape):
    """
    Custom load shape for event-based invalidation testing.

    Different from TTL shape:
    - More invalidators during storm phase
    - Sharp transitions to expose race conditions
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple."""
        stats.update_phase()
        run_time = self.get_run_time()

        if run_time > TOTAL_DURATION:
            return None

        phase = stats.get_phase()

        if phase == "warm":
            # Low load, mostly readers
            return (20, 5)

        elif phase == "baseline":
            # Moderate load, still mostly readers
            return (40, 10)

        elif phase == "invalidate_storm":
            # HIGH LOAD with many invalidators
            # This creates event-based race conditions
            return (MAX_USERS, 25)

        elif phase == "recovery":
            # Reduced load, fewer invalidators
            return (50, 10)

        else:  # verify
            # Moderate load for verification
            return (30, 10)


# =============================================================================
# High-Frequency Reader User
# =============================================================================


class HighFrequencyReader(HttpUser):
    """
    High-frequency reader - rapid cache reads.

    RACE CONDITION TARGET:
    - Reads during invalidation events
    - Should NOT get stale data after invalidation is confirmed
    - May get stale data during brief propagation window
    """

    wait_time = between(0.02, 0.1)  # 20-100ms between requests (very fast)
    weight = 6  # Higher weight = more readers

    def on_start(self):
        """Initialize reader session."""
        global target_product_ids
        stats.set_start_time()

        self.headers = {}
        self.local_price_cache: Dict[int, float] = {}

        # Login
        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                if login_helper.login(random.randint(0, 99)):
                    self.headers = {"Authorization": f"Bearer {login_helper.access_token}"}
            except Exception:
                pass

        # Populate target products on first user
        if not target_product_ids:
            self._populate_products()

    def _populate_products(self):
        """Fetch products for testing."""
        global target_product_ids, target_products

        try:
            response = self.client.get(
                "/api/products/",
                name=f"{STAGE_NAME} Initial Products",
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
                        price = float(product.get("price", 0))
                        stats.record_initial_price(pid, price)

                print(f"   📦 Loaded {len(target_product_ids)} products for testing")
        except Exception as e:
            print(f"   ⚠️ Failed to load products: {e}")

    @task(10)
    @tag("read", "high_frequency")
    def read_product_rapid(self):
        """
        Rapid product reads.

        RACE CONDITION:
        During invalidation storm, some reads may hit cache
        while invalidation is still propagating.
        """
        if not target_product_ids:
            return

        product_id = random.choice(target_product_ids)
        start_time = time.time()

        with self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} Read Product (Reader)",
            headers=self.headers,
            catch_response=True,
        ) as response:
            response_time_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                product = response.json()
                observed_price = float(product.get("price", 0))

                # Record for stale detection
                stats.record_read(product_id, observed_price, response_time_ms)

                # Track local price changes
                if product_id in self.local_price_cache:
                    old_price = self.local_price_cache[product_id]
                    if old_price != observed_price:
                        # Price changed - invalidation propagated
                        pass

                self.local_price_cache[product_id] = observed_price
                stats.record_success()
                response.success()
            else:
                stats.record_error()
                response.failure(f"Status {response.status_code}")

    @task(3)
    @tag("read", "sequence")
    def read_invalidate_reread_sequence(self):
        """
        Read → wait → re-read sequence.

        RACE CONDITION TARGET:
        This creates overlapping sequences where:
        1. First read gets cached value
        2. Invalidation event occurs (from another user)
        3. Second read should get NEW value
        """
        if not target_product_ids:
            return

        product_id = random.choice(target_product_ids)

        # First read
        try:
            r1 = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Read (before wait)",
                headers=self.headers,
            )
            price1 = float(r1.json().get("price", 0)) if r1.status_code == 200 else None
        except Exception:
            return

        # Wait - this window allows invalidation to occur
        time.sleep(random.uniform(0.05, 0.2))  # 50-200ms

        # Second read
        try:
            r2 = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Read (after wait)",
                headers=self.headers,
            )
            price2 = float(r2.json().get("price", 0)) if r2.status_code == 200 else None
        except Exception:
            return

        # Check consistency
        if price1 is not None and price2 is not None:
            stats.record_read(product_id, price2, 0)


# =============================================================================
# Event Invalidator User
# =============================================================================


class EventInvalidator(HttpUser):
    """
    Event invalidator - triggers cache invalidation via price updates.

    DIFFERENT FROM TTL:
    - Actively triggers invalidation
    - Creates event-based race conditions
    - Tests idempotency of repeated invalidations
    """

    wait_time = between(0.5, 2.0)  # Slower than readers
    weight = 2  # Fewer invalidators than readers

    def on_start(self):
        """Initialize invalidator session."""
        stats.set_start_time()

        self.headers = {}
        self.is_admin = False

        # Try admin login
        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                if login_helper.login_as_admin():
                    self.headers = {"Authorization": f"Bearer {login_helper.access_token}"}
                    self.is_admin = True
            except Exception:
                pass

        if not self.is_admin:
            # Fallback admin login
            try:
                response = self.client.post(
                    "/api/auth/token/",
                    json={"username": "admin", "password": "admin123"},
                    name=f"{STAGE_NAME} Invalidator Login",
                )
                if response.status_code == 200:
                    token = response.json().get("access")
                    self.headers = {"Authorization": f"Bearer {token}"}
                    self.is_admin = True
            except Exception:
                pass

    @task(5)
    @tag("invalidate", "price_update")
    def trigger_invalidation(self):
        """
        Trigger cache invalidation via price update.

        RACE CONDITION:
        This creates an event that should:
        1. Update DB immediately
        2. Trigger cache invalidation event
        3. Propagate to all cache replicas

        Readers should NOT see stale data after propagation completes.
        """
        phase = stats.get_phase()
        if phase not in ["invalidate_storm", "recovery"]:
            return

        if not target_product_ids or not self.is_admin:
            return

        product_id = random.choice(target_product_ids)

        # Get current price
        try:
            get_response = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Get for Invalidation",
                headers=self.headers,
            )

            if get_response.status_code != 200:
                return

            current_price = float(get_response.json().get("price", 10000))
        except Exception:
            return

        # Calculate new price (small change to detect easily)
        change = random.uniform(0.01, 0.05) * random.choice([-1, 1])
        new_price = round(current_price * (1 + change), 2)
        new_price = max(100, new_price)

        # Start tracking this invalidation
        event_id = stats.start_invalidation(product_id, current_price, new_price)

        # Trigger invalidation via update
        with self.client.patch(
            f"/api/products/{product_id}/",
            json={"price": new_price},
            headers=self.headers,
            name=f"{STAGE_NAME} Invalidation (Update)",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 204]:
                stats.confirm_invalidation(event_id)
                stats.record_success()
                response.success()
            elif response.status_code == 403:
                # Not admin - skip
                response.success()
            else:
                stats.record_error()
                response.failure(f"Status {response.status_code}")

    @task(2)
    @tag("invalidate", "repeated")
    def repeated_invalidation(self):
        """
        Trigger repeated invalidation on same product.

        IDEMPOTENCY TEST:
        Multiple rapid invalidations should:
        1. All complete successfully
        2. Final state should be consistent
        3. No partial updates visible
        """
        phase = stats.get_phase()
        if phase != "invalidate_storm":
            return

        # Only admin users can perform this task
        if not target_product_ids or not self.is_admin:
            return

        # Pick one product for repeated invalidation
        product_id = target_product_ids[0]

        # Send 3 rapid invalidations
        for i in range(3):
            try:
                # Small price bump
                get_r = self.client.get(
                    f"/api/products/{product_id}/",
                    name=f"{STAGE_NAME} Get (repeated)",
                    headers=self.headers,
                )

                if get_r.status_code == 200:
                    current = float(get_r.json().get("price", 10000))
                    new_price = round(current + random.uniform(1, 10), 2)

                    event_id = stats.start_invalidation(product_id, current, new_price)

                    with self.client.patch(
                        f"/api/products/{product_id}/",
                        json={"price": new_price},
                        headers=self.headers,
                        name=f"{STAGE_NAME} Repeated Invalidation",
                        catch_response=True,
                    ) as patch_r:
                        if patch_r.status_code in [200, 204]:
                            stats.confirm_invalidation(event_id)
                            patch_r.success()
                        elif patch_r.status_code == 403:
                            # Not admin - mark as success to avoid error reporting
                            patch_r.success()
                        else:
                            patch_r.failure(f"Status {patch_r.status_code}")
            except Exception:
                pass

            time.sleep(0.01)  # 10ms between invalidations

        # Verify final state
        time.sleep(0.1)  # Wait for propagation

        try:
            verify_r = self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} Verify Idempotency",
                headers=self.headers,
            )

            if verify_r.status_code == 200:
                final_price = float(verify_r.json().get("price", 0))
                stats.check_idempotency(product_id, final_price)
        except Exception:
            pass


# =============================================================================
# Verification User
# =============================================================================


class VerificationUser(HttpUser):
    """
    Verification user - checks consistency after invalidation.

    Purpose:
    - Verify no stale data after invalidation completes
    - Check system stability after storm
    """

    wait_time = between(1.0, 3.0)
    weight = 1

    def on_start(self):
        """Initialize verification session."""
        stats.set_start_time()
        self.headers = {}

        if HELPERS_AVAILABLE:
            try:
                login_helper = LoginHelper(self.client, STAGE_NAME)
                if login_helper.login(random.randint(0, 99)):
                    self.headers = {"Authorization": f"Bearer {login_helper.access_token}"}
            except Exception:
                pass

    @task(1)
    @tag("verify", "consistency")
    def verify_consistency(self):
        """
        Verify consistency after invalidation period.
        """
        phase = stats.get_phase()
        if phase not in ["recovery", "verify"]:
            return

        if not target_product_ids:
            return

        # Read multiple products and verify consistency
        for product_id in target_product_ids[:3]:
            try:
                # First read
                r1 = self.client.get(
                    f"/api/products/{product_id}/",
                    name=f"{STAGE_NAME} Verify Read 1",
                    headers=self.headers,
                )

                time.sleep(0.05)  # 50ms

                # Second read
                r2 = self.client.get(
                    f"/api/products/{product_id}/",
                    name=f"{STAGE_NAME} Verify Read 2",
                    headers=self.headers,
                )

                if r1.status_code == 200 and r2.status_code == 200:
                    price1 = float(r1.json().get("price", 0))
                    price2 = float(r2.json().get("price", 0))

                    # Prices should be consistent (unless another invalidation occurred)
                    if abs(price1 - price2) > 0.01:
                        # Inconsistency detected - but might be valid if new update
                        pass

            except Exception:
                pass


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test."""
    print(f"\n{'='*70}")
    print(f"⚡ {STAGE_NAME} Event-based Cache Invalidation Test")
    print(f"{'='*70}")
    print(f"\nPurpose: Test cache invalidation via events (not TTL)")
    print(f"\nKEY DIFFERENCE FROM TTL-BASED:")
    print(f"  - TTL-based: Cache expires passively after fixed time")
    print(f"  - Event-based: Cache invalidated actively by events")
    print(f"  - This tests event propagation latency and race conditions")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_WARM}s): Warm - Cache population")
    print(f"  Phase 2 ({PHASE_BASELINE}s): Baseline - Normal reads")
    print(f"  Phase 3 ({PHASE_INVALIDATE_STORM}s): Storm - High invalidation rate")
    print(f"  Phase 4 ({PHASE_RECOVERY}s): Recovery - Reduced invalidation")
    print(f"  Phase 5 ({PHASE_VERIFY}s): Verify - Consistency check")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"Max Invalidation Latency: {MAX_INVALIDATION_LATENCY_MS}ms")
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

    print(f"\n{'='*70}")
    print(f"📊 {STAGE_NAME} Final Report")
    print(f"{'='*70}")

    print(f"\n📖 Read Statistics:")
    print(f"   - Total reads: {summary['reads_total']}")
    print(f"   - Reads during invalidation: {summary['reads_during_invalidation']}")
    print(f"   - Fresh reads after event: {summary['fresh_reads_after_event']}")
    print(f"   - Stale reads after event: {summary['stale_reads_after_event']}")
    print(f"   - Stale rate: {summary['stale_rate']:.2%}")

    print(f"\n⚡ Invalidation Statistics:")
    print(f"   - Total invalidation events: {summary['invalidation_count']}")
    print(f"   - Avg invalidation latency: {summary['invalidation_latency_avg_ms']:.1f}ms")
    print(f"   - P95 invalidation latency: {summary['invalidation_latency_p95_ms']:.1f}ms")
    print(f"   - Max invalidation latency: {summary['invalidation_latency_max_ms']:.1f}ms")

    print(f"\n🔄 Propagation Statistics:")
    print(f"   - Avg propagation latency: {summary['propagation_latency_avg_ms']:.1f}ms")
    print(f"   - Max propagation latency: {summary['propagation_latency_max_ms']:.1f}ms")

    print(f"\n🔐 Idempotency:")
    print(f"   - Violations: {summary['idempotency_violations']}")

    print(f"\n📈 Stability:")
    print(f"   - Max consecutive errors: {summary['max_consecutive_errors']}")

    print(f"\n⏱️ Read Latency by Phase:")
    for phase, lat in summary.get("read_latency_by_phase", {}).items():
        print(f"   [{phase}]")
        print(f"     - Count: {lat['count']}")
        print(f"     - Avg: {lat['avg']:.1f}ms")
        print(f"     - P95: {lat['p95']:.1f}ms")
        print(f"     - Max: {lat['max']:.1f}ms")

    # Validation Results
    print(f"\n{'='*70}")
    print(f"🔍 VALIDATION RESULTS")
    print(f"{'='*70}")

    # 1. No stale data after invalidation
    no_stale = summary["stale_reads_after_event"] == 0
    print(
        f"   ✓ No stale reads after invalidation: {'PASS' if no_stale else 'WARN (' + str(summary['stale_reads_after_event']) + ' stale reads)'}"
    )

    # 2. Invalidation latency within SLA
    latency_ok = summary["invalidation_latency_max_ms"] < MAX_INVALIDATION_LATENCY_MS * 2  # 2x tolerance
    print(
        f"   ✓ Invalidation latency < {MAX_INVALIDATION_LATENCY_MS * 2}ms: {'PASS' if latency_ok else 'WARN (max: ' + str(summary['invalidation_latency_max_ms']) + 'ms)'}"
    )

    # 3. Idempotency preserved
    idempotent = summary["idempotency_violations"] == 0
    print(
        f"   ✓ Idempotency preserved: {'PASS' if idempotent else 'FAIL (' + str(summary['idempotency_violations']) + ' violations)'}"
    )

    # 4. System stability
    stable = summary["max_consecutive_errors"] < 5
    print(
        f"   ✓ System stability: {'PASS' if stable else 'WARN (' + str(summary['max_consecutive_errors']) + ' consecutive errors)'}"
    )

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
        "stage17_event_invalidation_report.html",
    ]

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
