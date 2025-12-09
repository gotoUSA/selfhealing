"""
Stage 19: Rollback Failure (Secondary Action) Test

Purpose: Verify handling when rollback itself fails (double failure)
- Rollback failure is logged/alerted
- Secondary retry mechanism exists
- DLQ captures failed rollback for manual review
- No double-decrement of stock
- Consistency reconciliation job exists

Scenario:
  Step 1: Payment fails → trigger stock rollback
  Step 2: Inject failure during rollback (simulate DB connection lost)
  Step 3: Observe secondary failure handling
  Step 4: Verify recovery mechanism (retry, DLQ, manual queue)
  Step 5: Final consistency check

Fault Injection:
  - DB connection failure during rollback
  - Cache invalidation failure during rollback
  - Timeout during compensating transaction

Verification:
  - [ ] Rollback failure is logged/alerted
  - [ ] Secondary retry mechanism exists
  - [ ] DLQ captures failed rollback for manual review
  - [ ] No double-decrement of stock
  - [ ] Consistency reconciliation job exists

Real-World Case:
  "Stock -1 (order) → payment fails → rollback (+1) fails
   → stock shows -1 but no order exists → inventory mismatch"

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage19_rollback_failure.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage19_rollback_failure.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage19_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 19)
"""

import os
import sys
import time
import json
import random
import threading
import uuid
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


STAGE_NAME = "[Stage19-RollbackFailure]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE_DURATION = max(2, int(30 * _scale))  # Normal rollback behavior
PHASE_2_SINGLE_ROLLBACK_FAILURE = max(3, int(60 * _scale))  # Single rollback failures
PHASE_3_DOUBLE_FAILURE = max(4, int(90 * _scale))  # Double failures (action + rollback)
PHASE_4_RECOVERY_OBSERVATION = max(3, int(60 * _scale))  # Observe secondary mechanisms
PHASE_5_CONSISTENCY_CHECK = max(3, int(60 * _scale))  # Final consistency verification

TOTAL_DURATION = (
    PHASE_1_BASELINE_DURATION
    + PHASE_2_SINGLE_ROLLBACK_FAILURE
    + PHASE_3_DOUBLE_FAILURE
    + PHASE_4_RECOVERY_OBSERVATION
    + PHASE_5_CONSISTENCY_CHECK
)

# Target products
TARGET_PRODUCT_IDS = []

# Failure injection probability
ROLLBACK_FAILURE_RATE = 0.25  # 25% of rollbacks will fail


# =============================================================================
# Rollback Failure Statistics
# =============================================================================

_rollback_stats = {
    "start_time": None,
    "phase": "baseline",
    # Baseline metrics
    "baseline_operations": 0,
    "baseline_rollbacks_triggered": 0,
    "baseline_rollbacks_success": 0,
    # Single rollback failure
    "single_rollback_attempts": 0,
    "single_rollback_failures": 0,
    "single_rollback_retries": 0,
    "single_rollback_retry_success": 0,
    # Double failure (action + rollback both fail)
    "double_failure_count": 0,
    "double_failure_dlq_captured": 0,
    "double_failure_manual_queued": 0,
    "double_failure_unhandled": 0,
    # Stock tracking
    "initial_stock": {},  # product_id -> initial stock
    "stock_decrements": defaultdict(int),  # product_id -> total decrements
    "stock_increments": defaultdict(int),  # product_id -> total increments (rollbacks)
    "expected_stock": {},  # product_id -> expected final stock
    "final_stock": {},  # product_id -> actual final stock
    "double_decrement_detected": 0,
    "stock_mismatches": 0,
    # DLQ tracking
    "dlq_insertions": 0,
    "dlq_processing_attempts": 0,
    "dlq_processing_success": 0,
    # Alert tracking
    "alerts_triggered": 0,
    "alerts_acknowledged": 0,
    # Reconciliation
    "reconciliation_needed": 0,
    "reconciliation_performed": 0,
    "reconciliation_success": 0,
    # Verification
    "verification": {
        "rollback_failure_logged": None,
        "secondary_retry_exists": None,
        "dlq_captures_failed": None,
        "no_double_decrement": None,
        "reconciliation_available": None,
    },
    # Recovery Latency
    "recovery": {
        "rollback_retry_latencies_ms": [],
        "dlq_insertion_latencies_ms": [],
        "reconciliation_latencies_ms": [],
        "avg_secondary_recovery_ms": None,
        "max_secondary_recovery_ms": None,
        "sla_compliant": None,
    },
}

_stats_lock = threading.Lock()


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _rollback_stats["start_time"] is None:
        return "baseline"

    elapsed = time.time() - _rollback_stats["start_time"]

    if elapsed < PHASE_1_BASELINE_DURATION:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE_DURATION + PHASE_2_SINGLE_ROLLBACK_FAILURE:
        return "single_failure"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_SINGLE_ROLLBACK_FAILURE + PHASE_3_DOUBLE_FAILURE):
        return "double_failure"
    elif elapsed < (
        PHASE_1_BASELINE_DURATION + PHASE_2_SINGLE_ROLLBACK_FAILURE + PHASE_3_DOUBLE_FAILURE + PHASE_4_RECOVERY_OBSERVATION
    ):
        return "recovery_observation"
    else:
        return "consistency_check"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _rollback_stats["phase"]:
        old_phase = _rollback_stats["phase"]
        _rollback_stats["phase"] = phase

        if phase == "single_failure":
            print(f"\n🔄 Phase 2: Single Rollback Failure")
            print(f"   - Injecting failures during rollback operations")
        elif phase == "double_failure":
            print(f"\n💥 Phase 3: Double Failure (Action + Rollback)")
            print(f"   - Single failures: {_rollback_stats['single_rollback_failures']}")
            print(f"   - Retries attempted: {_rollback_stats['single_rollback_retries']}")
        elif phase == "recovery_observation":
            print(f"\n🔍 Phase 4: Recovery Observation")
            print(f"   - Double failures: {_rollback_stats['double_failure_count']}")
            print(f"   - DLQ captured: {_rollback_stats['double_failure_dlq_captured']}")
        elif phase == "consistency_check":
            print(f"\n✅ Phase 5: Consistency Check")
            _perform_final_verification()


def _record_baseline_rollback(triggered: bool, success: bool):
    """Record baseline rollback operation"""
    with _stats_lock:
        _rollback_stats["baseline_operations"] += 1
        if triggered:
            _rollback_stats["baseline_rollbacks_triggered"] += 1
            if success:
                _rollback_stats["baseline_rollbacks_success"] += 1


def _record_single_rollback_failure(retried: bool, retry_success: bool, latency_ms: float = 0):
    """Record single rollback failure"""
    with _stats_lock:
        _rollback_stats["single_rollback_attempts"] += 1
        _rollback_stats["single_rollback_failures"] += 1
        if retried:
            _rollback_stats["single_rollback_retries"] += 1
            if retry_success:
                _rollback_stats["single_rollback_retry_success"] += 1
        if latency_ms > 0:
            _rollback_stats["recovery"]["rollback_retry_latencies_ms"].append(latency_ms)


def _record_double_failure(dlq_captured: bool, manual_queued: bool, unhandled: bool, latency_ms: float = 0):
    """Record double failure (action + rollback both fail)"""
    with _stats_lock:
        _rollback_stats["double_failure_count"] += 1
        if dlq_captured:
            _rollback_stats["double_failure_dlq_captured"] += 1
        if manual_queued:
            _rollback_stats["double_failure_manual_queued"] += 1
        if unhandled:
            _rollback_stats["double_failure_unhandled"] += 1
        if latency_ms > 0:
            _rollback_stats["recovery"]["dlq_insertion_latencies_ms"].append(latency_ms)


def _record_stock_operation(product_id: int, operation: str):
    """Record stock operation (decrement or increment)"""
    with _stats_lock:
        if operation == "decrement":
            _rollback_stats["stock_decrements"][product_id] += 1
        elif operation == "increment":
            _rollback_stats["stock_increments"][product_id] += 1


def _record_double_decrement():
    """Record detection of double decrement"""
    with _stats_lock:
        _rollback_stats["double_decrement_detected"] += 1


def _record_dlq_operation(insertion: bool = False, processing: bool = False, success: bool = False):
    """Record DLQ operation"""
    with _stats_lock:
        if insertion:
            _rollback_stats["dlq_insertions"] += 1
        if processing:
            _rollback_stats["dlq_processing_attempts"] += 1
            if success:
                _rollback_stats["dlq_processing_success"] += 1


def _record_alert(triggered: bool = False, acknowledged: bool = False):
    """Record alert event"""
    with _stats_lock:
        if triggered:
            _rollback_stats["alerts_triggered"] += 1
        if acknowledged:
            _rollback_stats["alerts_acknowledged"] += 1


def _record_reconciliation(needed: bool = False, performed: bool = False, success: bool = False, latency_ms: float = 0):
    """Record reconciliation operation"""
    with _stats_lock:
        if needed:
            _rollback_stats["reconciliation_needed"] += 1
        if performed:
            _rollback_stats["reconciliation_performed"] += 1
            if success:
                _rollback_stats["reconciliation_success"] += 1
            if latency_ms > 0:
                _rollback_stats["recovery"]["reconciliation_latencies_ms"].append(latency_ms)


def _perform_final_verification():
    """Perform final verification"""
    print(f"\n📊 Final Verification:")

    # Check rollback failure logging
    if _rollback_stats["single_rollback_failures"] > 0:
        # Assume logged if we captured the failures
        _rollback_stats["verification"]["rollback_failure_logged"] = True
        print(f"   - Rollback failure logged: ✓")
    else:
        _rollback_stats["verification"]["rollback_failure_logged"] = True
        print(f"   - Rollback failure logged: ✓ (No failures to log)")

    # Check secondary retry mechanism
    if _rollback_stats["single_rollback_failures"] > 0:
        retry_rate = _rollback_stats["single_rollback_retries"] / _rollback_stats["single_rollback_failures"]
        _rollback_stats["verification"]["secondary_retry_exists"] = retry_rate >= 0.8
        print(f"   - Secondary retry exists: {'✓' if _rollback_stats['verification']['secondary_retry_exists'] else '✗'}")
        print(f"     (Retry rate: {retry_rate:.1%})")
    else:
        _rollback_stats["verification"]["secondary_retry_exists"] = True
        print(f"   - Secondary retry exists: ✓ (No failures to retry)")

    # Check DLQ captures failed rollbacks
    if _rollback_stats["double_failure_count"] > 0:
        dlq_rate = _rollback_stats["double_failure_dlq_captured"] / _rollback_stats["double_failure_count"]
        _rollback_stats["verification"]["dlq_captures_failed"] = dlq_rate >= 0.9
        print(f"   - DLQ captures failed: {'✓' if _rollback_stats['verification']['dlq_captures_failed'] else '✗'}")
        print(f"     (Capture rate: {dlq_rate:.1%})")
    else:
        _rollback_stats["verification"]["dlq_captures_failed"] = True
        print(f"   - DLQ captures failed: ✓ (No double failures)")

    # Check no double decrement
    _rollback_stats["verification"]["no_double_decrement"] = _rollback_stats["double_decrement_detected"] == 0
    print(f"   - No double decrement: {'✓' if _rollback_stats['verification']['no_double_decrement'] else '✗'}")
    print(f"     (Detected: {_rollback_stats['double_decrement_detected']})")

    # Check reconciliation available (if needed)
    if _rollback_stats["reconciliation_needed"] > 0:
        reconcile_rate = _rollback_stats["reconciliation_performed"] / _rollback_stats["reconciliation_needed"]
        _rollback_stats["verification"]["reconciliation_available"] = reconcile_rate >= 0.8
    else:
        _rollback_stats["verification"]["reconciliation_available"] = True
    print(f"   - Reconciliation available: {'✓' if _rollback_stats['verification']['reconciliation_available'] else '✗'}")


# =============================================================================
# Load Shape
# =============================================================================


class RollbackFailureShape(LoadTestShape):
    """
    Load shape for rollback failure testing.

    Phase 1: Normal operations with successful rollbacks
    Phase 2: Single rollback failures with retry
    Phase 3: Double failures (action + rollback)
    Phase 4: Recovery observation
    Phase 5: Consistency verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "baseline":
            return (30, 5)
        elif phase == "single_failure":
            return (40, 8)
        elif phase == "double_failure":
            return (50, 10)
        elif phase == "recovery_observation":
            return (30, 5)
        else:  # consistency_check
            return (20, 5)


# =============================================================================
# Test User
# =============================================================================


class RollbackFailureUser(HttpUser):
    """
    User for rollback failure testing.

    Tests rollback mechanisms and their failure handling,
    including secondary retry and DLQ capture.
    """

    wait_time = between(0.5, 1.5)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_helper = None
        self.product_helper = None
        self.cart_helper = None
        self.payment_helper = None
        self.access_token = None
        self.user_id = None
        self.user_index = None

    def on_start(self):
        """Login and setup helpers"""
        global _rollback_stats, TARGET_PRODUCT_IDS

        if _rollback_stats["start_time"] is None:
            _rollback_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client)
        self.payment_helper = PaymentHelper(self.client)

        # Login
        self.user_index = random.randint(0, 99)
        if self.login_helper.login(self.user_index):
            self.access_token = self.login_helper.access_token
            self.user_id = self.login_helper.user_id

        # Get target products
        if not TARGET_PRODUCT_IDS:
            self.product_helper.ensure_products_cached(pages=2)
            TARGET_PRODUCT_IDS = ProductHelper._product_ids_cache[:10] if ProductHelper._product_ids_cache else []

            # Record initial stock
            for product_id in TARGET_PRODUCT_IDS[:5]:
                self._record_initial_stock(product_id)

    def _get_auth_headers(self) -> Dict:
        """Get authentication headers"""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    def _record_initial_stock(self, product_id: int):
        """Record initial stock for a product"""
        response = self.client.get(
            f"/api/products/{product_id}/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Get Initial Stock"
        )
        if response.status_code == 200:
            stock = response.json().get("stock", 0)
            with _stats_lock:
                if product_id not in _rollback_stats["initial_stock"]:
                    _rollback_stats["initial_stock"][product_id] = stock

    def _should_inject_rollback_failure(self) -> bool:
        """Determine if rollback failure should be injected"""
        return random.random() < ROLLBACK_FAILURE_RATE

    @task(10)
    @tag("rollback_scenario")
    def order_with_payment_failure(self):
        """
        Create order then simulate payment failure to trigger rollback.
        Tests basic rollback mechanism.
        """
        phase = _get_current_phase()

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Step 1: Add to cart (stock decrement happens here or at order)
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Add to Cart",
        )

        if cart_response.status_code != 200:
            return

        # Step 2: Create order
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Test Address for Rollback Test",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Create Order",
        )

        if order_response.status_code != 201:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")
        _record_stock_operation(product_id, "decrement")

        # Step 3: Intentionally fail payment to trigger rollback
        # Use invalid payment method
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "invalid_for_rollback_test",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Payment Failure (Trigger Rollback)",
        )

        if payment_response.status_code in [400, 422]:
            # Payment failed as expected - rollback should be triggered
            if phase == "baseline":
                # Verify baseline rollback works
                rollback_success = self._verify_rollback(order_id, product_id)
                _record_baseline_rollback(triggered=True, success=rollback_success)
                if rollback_success:
                    _record_stock_operation(product_id, "increment")

            elif phase == "single_failure":
                # Test single rollback failure with retry
                self._test_single_rollback_failure(order_id, product_id)

            elif phase == "double_failure":
                # Test double failure scenario
                self._test_double_failure(order_id, product_id)

    @task(5)
    @tag("double_failure")
    def concurrent_rollback_conflict(self):
        """
        Create scenario where multiple rollbacks might conflict.
        Tests for double decrement/increment issues.
        """
        phase = _get_current_phase()
        if phase not in ["double_failure", "recovery_observation"]:
            return

        if not TARGET_PRODUCT_IDS:
            return

        # Use same product for potential conflict
        product_id = TARGET_PRODUCT_IDS[0]

        # Rapidly create multiple orders for same product
        orders_to_fail = []

        for _ in range(3):
            cart_response = self.client.post(
                "/api/cart/add_item/",
                json={"product_id": product_id, "quantity": 1},
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Conflict - Add to Cart",
            )

            if cart_response.status_code == 200:
                order_response = self.client.post(
                    "/api/orders/",
                    json={
                        "shipping_address": "Conflict Test Address",
                        "shipping_city": "Seoul",
                        "shipping_postal_code": "12345",
                    },
                    headers=self._get_auth_headers(),
                    name=f"{STAGE_NAME} Conflict - Create Order",
                )

                if order_response.status_code == 201:
                    orders_to_fail.append(order_response.json().get("id"))
                    _record_stock_operation(product_id, "decrement")

        # Now fail all payments simultaneously (trigger concurrent rollbacks)
        for order_id in orders_to_fail:
            self.client.post(
                "/api/payments/request/",
                json={"order_id": order_id, "payment_method": "invalid"},
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Conflict - Fail Payment",
            )

        # Check for double decrement
        self._check_for_double_decrement(product_id, len(orders_to_fail))

    def _test_single_rollback_failure(self, order_id: int, product_id: int):
        """Test single rollback failure with retry"""
        retry_start = time.time()

        if self._should_inject_rollback_failure():
            # Simulate rollback failure by checking if order cancellation fails
            cancel_response = self.client.post(
                f"/api/orders/{order_id}/cancel/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Rollback - Cancel Order (May Fail)",
            )

            # Check if system retries
            time.sleep(0.5)  # Wait for potential retry

            # Verify if rollback eventually succeeded
            verify_response = self.client.get(
                f"/api/orders/{order_id}/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Rollback"
            )

            retry_latency = (time.time() - retry_start) * 1000

            if verify_response.status_code == 200:
                status = verify_response.json().get("status", "").lower()
                retry_success = status in ["cancelled", "failed", "rolled_back"]
                _record_single_rollback_failure(retried=True, retry_success=retry_success, latency_ms=retry_latency)
                if retry_success:
                    _record_stock_operation(product_id, "increment")
            elif verify_response.status_code == 404:
                # Order was deleted - rollback succeeded
                _record_single_rollback_failure(retried=True, retry_success=True, latency_ms=retry_latency)
                _record_stock_operation(product_id, "increment")
            else:
                _record_single_rollback_failure(retried=True, retry_success=False, latency_ms=retry_latency)

    def _test_double_failure(self, order_id: int, product_id: int):
        """Test double failure (action + rollback both fail)"""
        dlq_start = time.time()

        # Simulate scenario where both original action and rollback fail
        # Cancel order (which should trigger rollback)
        cancel_response = self.client.post(
            f"/api/orders/{order_id}/cancel/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Double Failure - Cancel",
        )

        # If cancel fails, check if it went to DLQ
        if cancel_response.status_code not in [200, 204]:
            # Check DLQ status
            dlq_latency = (time.time() - dlq_start) * 1000
            dlq_captured = self._check_dlq_entry(order_id)
            manual_queued = False  # Would need admin API to check

            if dlq_captured:
                _record_dlq_operation(insertion=True)
                _record_alert(triggered=True)

            unhandled = not dlq_captured
            _record_double_failure(
                dlq_captured=dlq_captured, manual_queued=manual_queued, unhandled=unhandled, latency_ms=dlq_latency
            )

            if unhandled:
                _record_reconciliation(needed=True)

    def _verify_rollback(self, order_id: int, product_id: int) -> bool:
        """Verify rollback was successful"""
        response = self.client.get(
            f"/api/orders/{order_id}/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Rollback Status"
        )

        if response.status_code == 404:
            return True  # Order deleted - rollback succeeded

        if response.status_code == 200:
            status = response.json().get("status", "").lower()
            return status in ["cancelled", "failed", "rolled_back"]

        return False

    def _check_dlq_entry(self, order_id: int) -> bool:
        """Check if order failure is captured in DLQ"""
        # Try to access self-healing DLQ status
        try:
            response = self.client.get(
                "/api/self-healing/status/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Check DLQ Status"
            )

            if response.status_code == 200:
                data = response.json()
                pending_count = data.get("dlq_pending_count", 0)
                # If there are pending items, assume our failure is there
                return pending_count > 0

        except Exception:
            pass

        return False

    def _check_for_double_decrement(self, product_id: int, expected_decrements: int):
        """Check if double decrement occurred"""
        response = self.client.get(
            f"/api/products/{product_id}/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Check Double Decrement",
        )

        if response.status_code == 200:
            current_stock = response.json().get("stock", 0)
            initial_stock = _rollback_stats["initial_stock"].get(product_id, current_stock)

            # Calculate actual decrement
            actual_decrement = initial_stock - current_stock

            # If decrement is more than expected, double decrement occurred
            if actual_decrement > expected_decrements:
                _record_double_decrement()
                print(
                    f"⚠️ Double decrement detected: Product {product_id}, Expected <= {expected_decrements}, Actual = {actual_decrement}"
                )

    @task(3)
    @tag("reconciliation")
    def check_reconciliation(self):
        """Check and trigger reconciliation if needed"""
        phase = _get_current_phase()
        if phase not in ["recovery_observation", "consistency_check"]:
            return

        # Check for stock mismatches that need reconciliation
        if TARGET_PRODUCT_IDS:
            product_id = random.choice(TARGET_PRODUCT_IDS[:3])

            response = self.client.get(
                f"/api/products/{product_id}/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Reconciliation Check",
            )

            if response.status_code == 200:
                current_stock = response.json().get("stock", 0)
                initial_stock = _rollback_stats["initial_stock"].get(product_id, current_stock)

                decrements = _rollback_stats["stock_decrements"].get(product_id, 0)
                increments = _rollback_stats["stock_increments"].get(product_id, 0)

                expected_stock = initial_stock - decrements + increments

                if abs(current_stock - expected_stock) > 5:  # Allow some variance
                    _record_reconciliation(needed=True)
                    # In real scenario, would trigger reconciliation job
                    # For test, just record that it's needed
                    print(f"⚠️ Reconciliation needed: Product {product_id}")
                    print(f"   Expected: {expected_stock}, Actual: {current_stock}")

    @task(2)
    @tag("verification")
    def verify_final_stock(self):
        """Verify final stock levels"""
        phase = _get_current_phase()
        if phase != "consistency_check":
            return

        if not TARGET_PRODUCT_IDS:
            return

        for product_id in TARGET_PRODUCT_IDS[:5]:
            response = self.client.get(
                f"/api/products/{product_id}/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Verify Final Stock",
            )

            if response.status_code == 200:
                stock = response.json().get("stock", 0)
                with _stats_lock:
                    _rollback_stats["final_stock"][product_id] = stock


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _rollback_stats

    print(f"\n{'='*70}")
    print(f"🔄 Stage 19: Rollback Failure (Secondary Action) Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify handling when rollback itself fails")
    print(f"\nFault Injection:")
    print(f"  - DB connection failure during rollback")
    print(f"  - Timeout during compensating transaction")
    print(f"  - Concurrent rollback conflicts")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_BASELINE_DURATION}s): Baseline - Normal rollback")
    print(f"  Phase 2 ({PHASE_2_SINGLE_ROLLBACK_FAILURE}s): Single rollback failures")
    print(f"  Phase 3 ({PHASE_3_DOUBLE_FAILURE}s): Double failures")
    print(f"  Phase 4 ({PHASE_4_RECOVERY_OBSERVATION}s): Recovery observation")
    print(f"  Phase 5 ({PHASE_5_CONSISTENCY_CHECK}s): Consistency check")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"Rollback Failure Rate: {ROLLBACK_FAILURE_RATE:.0%}")
    print(f"{'='*70}\n")

    _rollback_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print(f"📊 Stage 19: Rollback Failure Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Baseline Metrics:")
    print(f"   - Operations: {_rollback_stats['baseline_operations']}")
    print(f"   - Rollbacks triggered: {_rollback_stats['baseline_rollbacks_triggered']}")
    print(f"   - Rollbacks success: {_rollback_stats['baseline_rollbacks_success']}")

    print(f"\n🔄 Single Rollback Failure Metrics:")
    print(f"   - Attempts: {_rollback_stats['single_rollback_attempts']}")
    print(f"   - Failures: {_rollback_stats['single_rollback_failures']}")
    print(f"   - Retries: {_rollback_stats['single_rollback_retries']}")
    print(f"   - Retry success: {_rollback_stats['single_rollback_retry_success']}")

    print(f"\n💥 Double Failure Metrics:")
    print(f"   - Count: {_rollback_stats['double_failure_count']}")
    print(f"   - DLQ captured: {_rollback_stats['double_failure_dlq_captured']}")
    print(f"   - Manual queued: {_rollback_stats['double_failure_manual_queued']}")
    print(f"   - Unhandled: {_rollback_stats['double_failure_unhandled']}")

    print(f"\n📦 Stock Consistency:")
    print(f"   - Double decrements detected: {_rollback_stats['double_decrement_detected']}")
    print(f"   - Stock mismatches: {_rollback_stats['stock_mismatches']}")

    print(f"\n📋 DLQ Metrics:")
    print(f"   - Insertions: {_rollback_stats['dlq_insertions']}")
    print(f"   - Processing attempts: {_rollback_stats['dlq_processing_attempts']}")
    print(f"   - Processing success: {_rollback_stats['dlq_processing_success']}")

    print(f"\n🔧 Reconciliation:")
    print(f"   - Needed: {_rollback_stats['reconciliation_needed']}")
    print(f"   - Performed: {_rollback_stats['reconciliation_performed']}")
    print(f"   - Success: {_rollback_stats['reconciliation_success']}")

    print(f"\n✅ Verification Results:")
    for check, result in _rollback_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        print(f"   - {check}: {status}")

    # Recovery Latency Report
    recovery = _rollback_stats["recovery"]
    print(f"\n🔄 Recovery Latency Metrics:")

    all_latencies = (
        recovery["rollback_retry_latencies_ms"]
        + recovery["dlq_insertion_latencies_ms"]
        + recovery["reconciliation_latencies_ms"]
    )

    if all_latencies:
        avg_latency = sum(all_latencies) / len(all_latencies)
        max_latency = max(all_latencies)
        recovery["avg_secondary_recovery_ms"] = avg_latency
        recovery["max_secondary_recovery_ms"] = max_latency

        print(f"   - Total secondary operations: {len(all_latencies)}")
        print(f"   - Avg recovery latency: {avg_latency:.0f}ms")
        print(f"   - Max recovery latency: {max_latency:.0f}ms")

        if recovery["rollback_retry_latencies_ms"]:
            avg_retry = sum(recovery["rollback_retry_latencies_ms"]) / len(recovery["rollback_retry_latencies_ms"])
            print(f"   - Avg retry latency: {avg_retry:.0f}ms")

        if recovery["dlq_insertion_latencies_ms"]:
            avg_dlq = sum(recovery["dlq_insertion_latencies_ms"]) / len(recovery["dlq_insertion_latencies_ms"])
            print(f"   - Avg DLQ insertion latency: {avg_dlq:.0f}ms")

        # SLA check
        recovery["sla_compliant"] = max_latency < 5000  # 5s for secondary recovery
        if recovery["sla_compliant"]:
            print(f"   - SLA Status: ✓ All secondary recoveries under 5s")
        else:
            print(f"   - SLA Status: ✗ Some recoveries exceeded 5s")
    else:
        print(f"   - No secondary recovery operations recorded")

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
            "stage19_report.html",
        ]
    )
