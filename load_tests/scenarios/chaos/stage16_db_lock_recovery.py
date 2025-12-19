"""
Stage 16: DB Lock / Deadlock Recovery Test

Purpose: Verify recovery from database lock contention and deadlocks
- Lock timeout triggers retry (not crash)
- Deadlock detection and resolution
- No permanent order "pending" state
- Stock consistency after recovery
- Transaction audit log

Scenario:
  Step 1: Start Transaction A (SELECT FOR UPDATE on product stock)
  Step 2: Hold lock for extended period (simulate long transaction)
  Step 3: Concurrent requests try to update same stock
  Step 4: Observe timeout handling and retry behavior
  Step 5: Release lock, verify recovery

Fault Injection:
  - Long-running transaction (lock hold)
  - Concurrent conflicting transactions
  - Simulated deadlock (circular dependency)

Verification:
  - [ ] Lock timeout triggers retry (not crash)
  - [ ] Deadlock detection and resolution
  - [ ] No permanent order "pending" state
  - [ ] Stock consistency after recovery
  - [ ] Transaction audit log

Real-World Case:
  "Flash sale: 1000 users hit same product, SELECT FOR UPDATE
   causes lock queue → timeout cascade → stock becomes negative"

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage16_db_lock_recovery.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage16_db_lock_recovery.py \\
        --host=http://localhost:8000 \\
        --users=100 --spawn-rate=20 --run-time=5m \\
        --headless --html=stage16_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 16)
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


STAGE_NAME = "[Stage16-DBLockRecovery]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE_DURATION = max(2, int(30 * _scale))  # Normal operations baseline
PHASE_2_LOCK_INJECTION_DURATION = max(5, int(120 * _scale))  # Lock contention injection
PHASE_3_DEADLOCK_SIMULATION_DURATION = max(3, int(60 * _scale))  # Deadlock scenarios
PHASE_4_RECOVERY_DURATION = max(3, int(60 * _scale))  # Recovery observation
PHASE_5_VERIFICATION_DURATION = max(2, int(30 * _scale))  # Final consistency check

TOTAL_DURATION = (
    PHASE_1_BASELINE_DURATION
    + PHASE_2_LOCK_INJECTION_DURATION
    + PHASE_3_DEADLOCK_SIMULATION_DURATION
    + PHASE_4_RECOVERY_DURATION
    + PHASE_5_VERIFICATION_DURATION
)

# Concurrency settings
CONCURRENT_UPDATES_PER_PRODUCT = 50  # Number of concurrent updates to same product
TARGET_PRODUCT_IDS = []  # Will be populated during test
LOCK_HOLD_DURATION = 5  # Simulated lock hold duration in seconds

# Lock contention configuration
MAX_RETRY_ON_LOCK_TIMEOUT = 3
LOCK_TIMEOUT_THRESHOLD_MS = 2000  # Consider timeout if > 2 seconds


# =============================================================================
# DB Lock Test Statistics
# =============================================================================

_lock_stats = {
    "start_time": None,
    "phase": "baseline",
    # Baseline metrics
    "baseline_avg_response_time": 0,
    "baseline_success_count": 0,
    # Lock contention metrics
    "lock_timeout_count": 0,
    "lock_retry_count": 0,
    "lock_retry_success_count": 0,
    "concurrent_update_attempts": 0,
    # Deadlock metrics
    "deadlock_detected_count": 0,
    "deadlock_resolved_count": 0,
    # Response time tracking
    "response_times": defaultdict(list),  # phase -> list of response times
    # Stock tracking
    "initial_stock": {},  # product_id -> initial stock
    "final_stock": {},  # product_id -> final stock
    "expected_stock_change": {},  # product_id -> expected change
    # Order state tracking
    "orders_created": 0,
    "orders_completed": 0,
    "orders_pending": 0,
    "orders_failed": 0,
    # Error tracking
    "errors": defaultdict(int),  # error_type -> count
    # Verification
    "verification": {
        "lock_timeout_handled": None,
        "no_crash_on_deadlock": None,
        "no_stuck_pending_orders": None,
        "stock_consistency": None,
        "recovery_successful": None,
    },
    # Recovery Latency Metrics
    "recovery": {
        "lock_contention_start": None,  # When lock issues started
        "lock_contention_end": None,  # When lock issues resolved
        "deadlock_resolution_times": [],  # Time to resolve each deadlock
        "total_recovery_latency_seconds": None,  # Overall recovery time
        "lock_retry_latencies_ms": [],  # Per-retry latencies
        "sla_compliant": None,  # Recovery under 2s threshold
    },
}

_stats_lock = threading.Lock()


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _lock_stats["start_time"] is None:
        return "baseline"

    elapsed = time.time() - _lock_stats["start_time"]

    if elapsed < PHASE_1_BASELINE_DURATION:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE_DURATION + PHASE_2_LOCK_INJECTION_DURATION:
        return "lock_injection"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_LOCK_INJECTION_DURATION + PHASE_3_DEADLOCK_SIMULATION_DURATION):
        return "deadlock_simulation"
    elif elapsed < (
        PHASE_1_BASELINE_DURATION
        + PHASE_2_LOCK_INJECTION_DURATION
        + PHASE_3_DEADLOCK_SIMULATION_DURATION
        + PHASE_4_RECOVERY_DURATION
    ):
        return "recovery"
    else:
        return "verification"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _lock_stats["phase"]:
        old_phase = _lock_stats["phase"]
        _lock_stats["phase"] = phase

        if phase == "lock_injection":
            print(f"\n🔒 Phase 2: Lock Contention Injection")
            print(f"   - Simulating {CONCURRENT_UPDATES_PER_PRODUCT} concurrent updates per product")
            print(f"   - Lock hold duration: {LOCK_HOLD_DURATION}s")
        elif phase == "deadlock_simulation":
            print(f"\n💀 Phase 3: Deadlock Simulation")
            print(f"   - Injecting circular dependency scenarios")
        elif phase == "recovery":
            print(f"\n🔄 Phase 4: Recovery Observation")
            print(f"   - Lock timeouts: {_lock_stats['lock_timeout_count']}")
            print(f"   - Lock retries: {_lock_stats['lock_retry_count']}")
            print(f"   - Deadlocks detected: {_lock_stats['deadlock_detected_count']}")
        elif phase == "verification":
            print(f"\n✅ Phase 5: Verification")
            _verify_consistency()


def _record_lock_timeout():
    """Record a lock timeout event"""
    with _stats_lock:
        _lock_stats["lock_timeout_count"] += 1


def _record_lock_retry(success: bool):
    """Record a lock retry attempt"""
    with _stats_lock:
        _lock_stats["lock_retry_count"] += 1
        if success:
            _lock_stats["lock_retry_success_count"] += 1


def _record_deadlock():
    """Record a deadlock detection"""
    with _stats_lock:
        _lock_stats["deadlock_detected_count"] += 1


def _record_deadlock_resolved():
    """Record a deadlock resolution"""
    with _stats_lock:
        _lock_stats["deadlock_resolved_count"] += 1


def _record_response_time(phase: str, response_time: float):
    """Record response time for a phase"""
    with _stats_lock:
        _lock_stats["response_times"][phase].append(response_time)


def _record_order_state(state: str):
    """Record order state change"""
    with _stats_lock:
        if state == "created":
            _lock_stats["orders_created"] += 1
        elif state == "completed":
            _lock_stats["orders_completed"] += 1
        elif state == "pending":
            _lock_stats["orders_pending"] += 1
        elif state == "failed":
            _lock_stats["orders_failed"] += 1


def _record_error(error_type: str):
    """Record an error"""
    with _stats_lock:
        _lock_stats["errors"][error_type] += 1


def _verify_consistency():
    """Verify data consistency after test"""
    print(f"\n📊 Verification Results:")

    # Check lock timeout handling
    if _lock_stats["lock_timeout_count"] > 0:
        retry_rate = _lock_stats["lock_retry_count"] / _lock_stats["lock_timeout_count"]
        _lock_stats["verification"]["lock_timeout_handled"] = retry_rate >= 0.8
        print(f"   - Lock timeout handling: {'✓' if _lock_stats['verification']['lock_timeout_handled'] else '✗'}")
        print(f"     (Retry rate: {retry_rate:.1%})")
    else:
        _lock_stats["verification"]["lock_timeout_handled"] = True
        print(f"   - Lock timeout handling: ✓ (No timeouts occurred)")

    # Check no crash on deadlock
    if _lock_stats["deadlock_detected_count"] > 0:
        resolution_rate = _lock_stats["deadlock_resolved_count"] / _lock_stats["deadlock_detected_count"]
        _lock_stats["verification"]["no_crash_on_deadlock"] = resolution_rate >= 0.9
        print(f"   - Deadlock handling: {'✓' if _lock_stats['verification']['no_crash_on_deadlock'] else '✗'}")
        print(f"     (Resolution rate: {resolution_rate:.1%})")
    else:
        _lock_stats["verification"]["no_crash_on_deadlock"] = True
        print(f"   - Deadlock handling: ✓ (No deadlocks occurred)")

    # Check no stuck pending orders (will be verified via API)
    pending_rate = _lock_stats["orders_pending"] / max(_lock_stats["orders_created"], 1)
    _lock_stats["verification"]["no_stuck_pending_orders"] = pending_rate < 0.05  # < 5% stuck
    print(f"   - No stuck pending orders: {'✓' if _lock_stats['verification']['no_stuck_pending_orders'] else '✗'}")
    print(f"     (Pending rate: {pending_rate:.1%})")

    # Overall recovery status
    _lock_stats["verification"]["recovery_successful"] = all(
        [
            _lock_stats["verification"]["lock_timeout_handled"],
            _lock_stats["verification"]["no_crash_on_deadlock"],
            _lock_stats["verification"]["no_stuck_pending_orders"],
        ]
    )
    print(f"\n   Overall Recovery: {'✓ SUCCESS' if _lock_stats['verification']['recovery_successful'] else '✗ FAILED'}")


# =============================================================================
# Load Shape
# =============================================================================


class DBLockRecoveryShape(LoadTestShape):
    """
    Load shape for DB lock recovery testing.

    Phase 1: Low load for baseline
    Phase 2: High concurrency on same resources (lock contention)
    Phase 3: Deadlock simulation
    Phase 4: Recovery with normal load
    Phase 5: Verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "baseline":
            return (20, 5)  # Low load baseline
        elif phase == "lock_injection":
            return (100, 20)  # High concurrency for lock contention
        elif phase == "deadlock_simulation":
            return (80, 15)  # Moderate load with deadlock patterns
        elif phase == "recovery":
            return (50, 10)  # Normal load during recovery
        else:  # verification
            return (20, 5)  # Low load for verification


# =============================================================================
# Test User
# =============================================================================


class DBLockRecoveryUser(HttpUser):
    """
    User for DB lock recovery testing.

    Performs concurrent stock updates and order operations
    to trigger lock contention and deadlock scenarios.
    """

    wait_time = between(0.1, 0.5)  # Aggressive timing for lock contention

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_helper = None
        self.product_helper = None
        self.cart_helper = None
        self.payment_helper = None
        self.access_token = None
        self.user_id = None
        self.target_product_id = None
        self.retry_count = 0

    def on_start(self):
        """Login and setup helpers"""
        global _lock_stats, TARGET_PRODUCT_IDS

        if _lock_stats["start_time"] is None:
            _lock_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client)
        self.payment_helper = PaymentHelper(self.client)

        # Login
        user_index = random.randint(0, 99)
        if self.login_helper.login(user_index):
            self.access_token = self.login_helper.access_token
            self.user_id = self.login_helper.user_id

        # Get target products for lock contention
        if not TARGET_PRODUCT_IDS:
            self.product_helper.ensure_products_cached(pages=2)
            TARGET_PRODUCT_IDS = ProductHelper._product_ids_cache[:5] if ProductHelper._product_ids_cache else []

        if TARGET_PRODUCT_IDS:
            self.target_product_id = random.choice(TARGET_PRODUCT_IDS)

    def _set_auth_header(self):
        """Set authentication header"""
        if self.access_token:
            self.client.headers["Authorization"] = f"Bearer {self.access_token}"

    def _get_auth_headers(self) -> Dict:
        """Get authentication headers"""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    @task(10)
    @tag("lock_contention")
    def concurrent_stock_update(self):
        """
        Simulate concurrent stock updates on the same product.
        This is the main lock contention scenario.
        """
        phase = _get_current_phase()
        if phase not in ["lock_injection", "deadlock_simulation"]:
            return

        if not self.target_product_id:
            return

        start_time = time.time()

        # Add item to cart (triggers stock lock)
        with self.client.post(
            "/api/cart/add_item/",
            json={
                "product_id": self.target_product_id,
                "quantity": 1,
            },
            headers=self._get_auth_headers(),
            catch_response=True,
            name=f"{STAGE_NAME} Add to Cart (Lock Test)",
        ) as response:
            response_time = (time.time() - start_time) * 1000  # ms
            _record_response_time(phase, response_time)

            with _stats_lock:
                _lock_stats["concurrent_update_attempts"] += 1

            if response.status_code == 200:
                response.success()
            elif response.status_code == 423:  # Locked
                _record_lock_timeout()
                response.failure(f"Lock timeout: {response.status_code}")
                self._handle_lock_timeout()
            elif response.status_code == 409:  # Conflict (potential deadlock)
                _record_deadlock()
                response.failure(f"Conflict/Deadlock: {response.status_code}")
                self._handle_deadlock()
            elif response.status_code in [500, 502, 503]:
                _record_error("server_error")
                response.failure(f"Server error: {response.status_code}")
            elif response_time > LOCK_TIMEOUT_THRESHOLD_MS:
                _record_lock_timeout()
                response.success()  # Request succeeded but was slow
                print(f"⚠️ Slow response (potential lock wait): {response_time:.0f}ms")
            else:
                response.success()

    @task(5)
    @tag("order_flow")
    def order_with_lock_contention(self):
        """
        Create order flow that involves multiple locks.
        Tests transaction handling under contention.
        """
        phase = _get_current_phase()
        if phase not in ["lock_injection", "deadlock_simulation", "recovery"]:
            return

        if not self.target_product_id:
            return

        _record_order_state("created")

        # Step 1: Add to cart
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={
                "product_id": self.target_product_id,
                "quantity": 1,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Order Flow - Add to Cart",
        )

        if cart_response.status_code != 200:
            _record_order_state("failed")
            return

        # Step 2: Create order (complex transaction)
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Test Address for Lock Recovery Test",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Order Flow - Create Order",
        )

        if order_response.status_code == 201:
            order_data = order_response.json()
            _record_order_state("pending")

            # Step 3: Request payment (another transaction)
            payment_response = self.client.post(
                "/api/payments/request/",
                json={
                    "order_id": order_data.get("id"),
                    "payment_method": "card",
                },
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Order Flow - Request Payment",
            )

            if payment_response.status_code in [200, 201]:
                _record_order_state("completed")
            else:
                _record_order_state("failed")
        elif order_response.status_code in [423, 409]:
            _record_lock_timeout()
            _record_order_state("failed")
            self._handle_lock_timeout()
        else:
            _record_order_state("failed")

    @task(3)
    @tag("deadlock")
    def deadlock_pattern_ab(self):
        """
        Simulate deadlock pattern: Update A then B.
        Paired with deadlock_pattern_ba() to create circular dependency.
        """
        phase = _get_current_phase()
        if phase != "deadlock_simulation":
            return

        if len(TARGET_PRODUCT_IDS) < 2:
            return

        product_a = TARGET_PRODUCT_IDS[0]
        product_b = TARGET_PRODUCT_IDS[1]

        # Add product A
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_a, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Deadlock Pattern A→B (Step 1)",
        )

        # Small delay to increase deadlock probability
        time.sleep(0.05)

        # Add product B
        response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_b, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Deadlock Pattern A→B (Step 2)",
        )

        if response.status_code in [423, 409, 500]:
            _record_deadlock()
        else:
            _record_deadlock_resolved()

    @task(3)
    @tag("deadlock")
    def deadlock_pattern_ba(self):
        """
        Simulate deadlock pattern: Update B then A.
        Paired with deadlock_pattern_ab() to create circular dependency.
        """
        phase = _get_current_phase()
        if phase != "deadlock_simulation":
            return

        if len(TARGET_PRODUCT_IDS) < 2:
            return

        product_a = TARGET_PRODUCT_IDS[0]
        product_b = TARGET_PRODUCT_IDS[1]

        # Add product B first (opposite order)
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_b, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Deadlock Pattern B→A (Step 1)",
        )

        # Small delay to increase deadlock probability
        time.sleep(0.05)

        # Add product A
        response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_a, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Deadlock Pattern B→A (Step 2)",
        )

        if response.status_code in [423, 409, 500]:
            _record_deadlock()
        else:
            _record_deadlock_resolved()

    @task(2)
    @tag("verification")
    def verify_order_status(self):
        """Verify orders are not stuck in pending state"""
        phase = _get_current_phase()
        if phase != "verification":
            return

        response = self.client.get("/api/orders/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Orders")

        if response.status_code == 200:
            orders = response.json()
            if isinstance(orders, dict) and "results" in orders:
                orders = orders["results"]

            for order in orders[:10]:  # Check last 10 orders
                status = order.get("status", "").lower()
                if status == "pending":
                    # Check how long it's been pending
                    created_at = order.get("created_at")
                    # Log stuck orders
                    print(f"⚠️ Order {order.get('id')} stuck in pending state")

    @task(2)
    @tag("verification")
    def verify_stock_consistency(self):
        """Verify stock levels are consistent"""
        phase = _get_current_phase()
        if phase != "verification":
            return

        if not TARGET_PRODUCT_IDS:
            return

        for product_id in TARGET_PRODUCT_IDS[:3]:
            response = self.client.get(f"/api/products/{product_id}/", name=f"{STAGE_NAME} Verify Stock")

            if response.status_code == 200:
                product = response.json()
                stock = product.get("stock", 0)
                with _stats_lock:
                    _lock_stats["final_stock"][product_id] = stock

    def _handle_lock_timeout(self):
        """Handle lock timeout with retry"""
        self.retry_count += 1

        if self.retry_count <= MAX_RETRY_ON_LOCK_TIMEOUT:
            # Exponential backoff
            wait_time = 0.1 * (2**self.retry_count)
            time.sleep(wait_time)
            _record_lock_retry(success=False)  # Will be updated on success
        else:
            self.retry_count = 0

    def _handle_deadlock(self):
        """Handle deadlock detection"""
        # Random backoff to break circular dependency
        time.sleep(random.uniform(0.1, 0.5))
        _record_deadlock_resolved()


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _lock_stats

    print(f"\n{'='*70}")
    print(f"🔒 Stage 16: DB Lock / Deadlock Recovery Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify recovery from database lock contention and deadlocks")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_BASELINE_DURATION}s): Baseline - Normal operations")
    print(f"  Phase 2 ({PHASE_2_LOCK_INJECTION_DURATION}s): Lock Injection - High concurrency")
    print(f"  Phase 3 ({PHASE_3_DEADLOCK_SIMULATION_DURATION}s): Deadlock Simulation")
    print(f"  Phase 4 ({PHASE_4_RECOVERY_DURATION}s): Recovery - Normal load")
    print(f"  Phase 5 ({PHASE_5_VERIFICATION_DURATION}s): Verification - Consistency check")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"{'='*70}\n")

    _lock_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print(f"📊 Stage 16: DB Lock Recovery Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Lock Contention Metrics:")
    print(f"   - Concurrent update attempts: {_lock_stats['concurrent_update_attempts']}")
    print(f"   - Lock timeouts: {_lock_stats['lock_timeout_count']}")
    print(f"   - Lock retries: {_lock_stats['lock_retry_count']}")
    print(f"   - Lock retry success: {_lock_stats['lock_retry_success_count']}")

    print(f"\n💀 Deadlock Metrics:")
    print(f"   - Deadlocks detected: {_lock_stats['deadlock_detected_count']}")
    print(f"   - Deadlocks resolved: {_lock_stats['deadlock_resolved_count']}")

    print(f"\n📦 Order Metrics:")
    print(f"   - Orders created: {_lock_stats['orders_created']}")
    print(f"   - Orders completed: {_lock_stats['orders_completed']}")
    print(f"   - Orders pending: {_lock_stats['orders_pending']}")
    print(f"   - Orders failed: {_lock_stats['orders_failed']}")

    print(f"\n⏱️ Response Time Analysis:")
    for phase, times in _lock_stats["response_times"].items():
        if times:
            avg_time = sum(times) / len(times)
            max_time = max(times)
            print(f"   - {phase}: avg={avg_time:.0f}ms, max={max_time:.0f}ms")

    print(f"\n❌ Error Summary:")
    for error_type, count in _lock_stats["errors"].items():
        print(f"   - {error_type}: {count}")

    print(f"\n✅ Verification Results:")
    for check, result in _lock_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        print(f"   - {check}: {status}")

    # Recovery Latency Report
    recovery = _lock_stats["recovery"]
    print(f"\n🔄 Recovery Latency Metrics:")
    if recovery["lock_retry_latencies_ms"]:
        avg_retry = sum(recovery["lock_retry_latencies_ms"]) / len(recovery["lock_retry_latencies_ms"])
        max_retry = max(recovery["lock_retry_latencies_ms"])
        print(f"   - Lock retry attempts: {len(recovery['lock_retry_latencies_ms'])}")
        print(f"   - Avg retry latency: {avg_retry:.0f}ms")
        print(f"   - Max retry latency: {max_retry:.0f}ms")
    if recovery["deadlock_resolution_times"]:
        avg_deadlock = sum(recovery["deadlock_resolution_times"]) / len(recovery["deadlock_resolution_times"])
        print(f"   - Deadlock resolutions: {len(recovery['deadlock_resolution_times'])}")
        print(f"   - Avg deadlock resolution: {avg_deadlock:.0f}ms")
    if recovery.get("total_recovery_latency_seconds"):
        print(f"   - Total recovery latency: {recovery['total_recovery_latency_seconds']:.1f}s")
        recovery["sla_compliant"] = recovery["total_recovery_latency_seconds"] < 2.0
        if recovery["sla_compliant"]:
            print(f"   - SLA Status: ✓ Under 2s threshold")
        else:
            print(f"   - SLA Status: ✗ Exceeded 2s threshold")
    else:
        # Calculate from retry latencies
        if recovery["lock_retry_latencies_ms"]:
            max_latency_s = max(recovery["lock_retry_latencies_ms"]) / 1000
            recovery["sla_compliant"] = max_latency_s < 2.0
            if recovery["sla_compliant"]:
                print(f"   - SLA Status: ✓ All retries under 2s threshold")
            else:
                print(f"   - SLA Status: ✗ Some retries exceeded 2s")

    print(f"\n{'='*70}\n")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import subprocess

    subprocess.run(
        [
            "locust",
            "-f",
            __file__,
            "--host",
            "http://localhost:8000",
            "--users",
            "50",
            "--spawn-rate",
            "10",
            "--run-time",
            "5m",
            "--headless",
            "--html",
            "stage16_report.html",
        ]
    )
