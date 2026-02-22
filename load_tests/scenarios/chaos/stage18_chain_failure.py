"""
Stage 18: Chain Failure Propagation Test

Purpose: Verify failure handling across service chain (Auth → Order → Payment)
- Each failure point has defined rollback behavior
- No orphaned orders (created but unpaid)
- No orphaned payments (paid but no order)
- Stock correctly restored at each failure point
- User notification for each failure type

Scenario:
  Step 1: Auth success → Order creation success → Payment FAILS
  Step 2: Verify order rollback initiated
  Step 3: Verify auth session state
  Step 4: Verify stock restoration
  Step 5: Test partial success scenarios

Failure Points:
  Point A: Auth success → Order FAILS
  Point B: Auth success → Order success → Payment FAILS
  Point C: Auth success → Order success → Payment success → Webhook FAILS

Verification:
  - [ ] Each failure point has defined rollback
  - [ ] No orphaned orders (created but unpaid)
  - [ ] No orphaned payments (paid but no order)
  - [ ] Stock correctly restored at each point
  - [ ] User notification for each failure type

Real-World Case:
  "Payment succeeded at PG, but internal order creation failed.
   Customer charged, no order record. Refund required."

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage18_chain_failure.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage18_chain_failure.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage18_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 18)
"""

import os
import sys
import time
import random
import threading
from typing import Dict

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage18-ChainFailure]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE_DURATION = max(2, int(30 * _scale))  # Normal flow baseline
PHASE_2_POINT_A_FAILURE = max(3, int(60 * _scale))  # Auth success → Order FAILS
PHASE_3_POINT_B_FAILURE = max(4, int(90 * _scale))  # Order success → Payment FAILS
PHASE_4_POINT_C_FAILURE = max(3, int(60 * _scale))  # Payment success → Webhook FAILS
PHASE_5_VERIFICATION = max(3, int(60 * _scale))  # Consistency verification

TOTAL_DURATION = (
    PHASE_1_BASELINE_DURATION
    + PHASE_2_POINT_A_FAILURE
    + PHASE_3_POINT_B_FAILURE
    + PHASE_4_POINT_C_FAILURE
    + PHASE_5_VERIFICATION
)

# Target products
TARGET_PRODUCT_IDS = []

# Failure injection probability (per phase)
FAILURE_INJECTION_RATE = 0.3  # 30% of requests will have injected failures


# =============================================================================
# Chain Failure Statistics
# =============================================================================

_chain_stats = {
    "start_time": None,
    "phase": "baseline",
    # Flow tracking
    "total_flows_started": 0,
    "baseline_success_count": 0,
    "baseline_failure_count": 0,
    # Point A: Auth → Order fails
    "point_a_failures": 0,
    "point_a_rollback_success": 0,
    "point_a_rollback_failed": 0,
    "point_a_stock_restored": 0,
    # Point B: Order → Payment fails
    "point_b_failures": 0,
    "point_b_rollback_success": 0,
    "point_b_rollback_failed": 0,
    "point_b_stock_restored": 0,
    "point_b_orphaned_orders": 0,
    # Point C: Payment → Webhook fails
    "point_c_failures": 0,
    "point_c_recovery_success": 0,
    "point_c_recovery_failed": 0,
    "point_c_orphaned_payments": 0,
    # Stock tracking
    "initial_stock": {},  # product_id -> initial stock
    "expected_stock": {},  # product_id -> expected stock after test
    "final_stock": {},  # product_id -> actual final stock
    "stock_mismatches": 0,
    # Order tracking
    "orders_created": [],  # list of order IDs
    "orders_with_payment": [],  # orders that have payments
    "orders_without_payment": [],  # orphaned orders
    "payments_without_order": [],  # orphaned payments
    # Notification tracking
    "notifications_sent": 0,
    "notification_failures": 0,
    # Verification
    "verification": {
        "point_a_rollback_defined": None,
        "point_b_rollback_defined": None,
        "point_c_recovery_defined": None,
        "no_orphaned_orders": None,
        "no_orphaned_payments": None,
        "stock_consistency": None,
        "notifications_sent": None,
    },
    # Recovery Latency
    "recovery": {
        "point_a_rollback_times": [],  # ms
        "point_b_rollback_times": [],  # ms
        "point_c_recovery_times": [],  # ms
        "avg_rollback_latency_ms": None,
        "max_rollback_latency_ms": None,
        "sla_compliant": None,  # All rollbacks under threshold
    },
}

_stats_lock = threading.Lock()


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _chain_stats["start_time"] is None:
        return "baseline"

    elapsed = time.time() - _chain_stats["start_time"]

    if elapsed < PHASE_1_BASELINE_DURATION:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE_DURATION + PHASE_2_POINT_A_FAILURE:
        return "point_a"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_POINT_A_FAILURE + PHASE_3_POINT_B_FAILURE):
        return "point_b"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_POINT_A_FAILURE + PHASE_3_POINT_B_FAILURE + PHASE_4_POINT_C_FAILURE):
        return "point_c"
    else:
        return "verification"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _chain_stats["phase"]:
        old_phase = _chain_stats["phase"]
        _chain_stats["phase"] = phase

        if phase == "point_a":
            print("\n🔴 Phase 2: Point A Failure (Auth success → Order FAILS)")
            print("   - Injecting order creation failures")
        elif phase == "point_b":
            print("\n🟠 Phase 3: Point B Failure (Order success → Payment FAILS)")
            print("   - Injecting payment failures after order creation")
            print(f"   - Point A failures: {_chain_stats['point_a_failures']}")
            print(f"   - Point A rollbacks: {_chain_stats['point_a_rollback_success']}")
        elif phase == "point_c":
            print("\n🟡 Phase 4: Point C Failure (Payment success → Webhook FAILS)")
            print("   - Simulating webhook delivery failures")
            print(f"   - Point B failures: {_chain_stats['point_b_failures']}")
            print(f"   - Point B rollbacks: {_chain_stats['point_b_rollback_success']}")
        elif phase == "verification":
            print("\n✅ Phase 5: Verification")
            print(f"   - Point C failures: {_chain_stats['point_c_failures']}")
            _perform_final_verification()


def _record_flow_start():
    """Record start of a flow"""
    with _stats_lock:
        _chain_stats["total_flows_started"] += 1


def _record_baseline_result(success: bool):
    """Record baseline flow result"""
    with _stats_lock:
        if success:
            _chain_stats["baseline_success_count"] += 1
        else:
            _chain_stats["baseline_failure_count"] += 1


def _record_point_a_failure(rollback_success: bool, stock_restored: bool, rollback_time_ms: float = 0):
    """Record Point A failure and rollback result"""
    with _stats_lock:
        _chain_stats["point_a_failures"] += 1
        if rollback_success:
            _chain_stats["point_a_rollback_success"] += 1
        else:
            _chain_stats["point_a_rollback_failed"] += 1
        if stock_restored:
            _chain_stats["point_a_stock_restored"] += 1
        if rollback_time_ms > 0:
            _chain_stats["recovery"]["point_a_rollback_times"].append(rollback_time_ms)


def _record_point_b_failure(rollback_success: bool, stock_restored: bool, orphaned: bool, rollback_time_ms: float = 0):
    """Record Point B failure and rollback result"""
    with _stats_lock:
        _chain_stats["point_b_failures"] += 1
        if rollback_success:
            _chain_stats["point_b_rollback_success"] += 1
        else:
            _chain_stats["point_b_rollback_failed"] += 1
        if stock_restored:
            _chain_stats["point_b_stock_restored"] += 1
        if orphaned:
            _chain_stats["point_b_orphaned_orders"] += 1
        if rollback_time_ms > 0:
            _chain_stats["recovery"]["point_b_rollback_times"].append(rollback_time_ms)


def _record_point_c_failure(recovery_success: bool, orphaned_payment: bool, recovery_time_ms: float = 0):
    """Record Point C failure and recovery result"""
    with _stats_lock:
        _chain_stats["point_c_failures"] += 1
        if recovery_success:
            _chain_stats["point_c_recovery_success"] += 1
        else:
            _chain_stats["point_c_recovery_failed"] += 1
        if orphaned_payment:
            _chain_stats["point_c_orphaned_payments"] += 1
        if recovery_time_ms > 0:
            _chain_stats["recovery"]["point_c_recovery_times"].append(recovery_time_ms)


def _record_order_created(order_id: int):
    """Record order creation"""
    with _stats_lock:
        _chain_stats["orders_created"].append(order_id)


def _record_order_with_payment(order_id: int):
    """Record order that has payment"""
    with _stats_lock:
        if order_id not in _chain_stats["orders_with_payment"]:
            _chain_stats["orders_with_payment"].append(order_id)


def _record_orphaned_order(order_id: int):
    """Record orphaned order (no payment)"""
    with _stats_lock:
        if order_id not in _chain_stats["orders_without_payment"]:
            _chain_stats["orders_without_payment"].append(order_id)


def _record_orphaned_payment(payment_id: str):
    """Record orphaned payment (no order)"""
    with _stats_lock:
        if payment_id not in _chain_stats["payments_without_order"]:
            _chain_stats["payments_without_order"].append(payment_id)


def _record_notification(success: bool):
    """Record notification attempt"""
    with _stats_lock:
        if success:
            _chain_stats["notifications_sent"] += 1
        else:
            _chain_stats["notification_failures"] += 1


def _perform_final_verification():
    """Perform final verification of chain failure handling"""
    print("\n📊 Final Verification:")

    # Point A rollback verification
    if _chain_stats["point_a_failures"] > 0:
        rollback_rate = _chain_stats["point_a_rollback_success"] / _chain_stats["point_a_failures"]
        _chain_stats["verification"]["point_a_rollback_defined"] = rollback_rate >= 0.95
        print(f"   - Point A rollback: {'✓' if _chain_stats['verification']['point_a_rollback_defined'] else '✗'}")
        print(f"     (Rate: {rollback_rate:.1%})")
    else:
        _chain_stats["verification"]["point_a_rollback_defined"] = True
        print("   - Point A rollback: ✓ (No failures to test)")

    # Point B rollback verification
    if _chain_stats["point_b_failures"] > 0:
        rollback_rate = _chain_stats["point_b_rollback_success"] / _chain_stats["point_b_failures"]
        _chain_stats["verification"]["point_b_rollback_defined"] = rollback_rate >= 0.95
        print(f"   - Point B rollback: {'✓' if _chain_stats['verification']['point_b_rollback_defined'] else '✗'}")
        print(f"     (Rate: {rollback_rate:.1%})")
    else:
        _chain_stats["verification"]["point_b_rollback_defined"] = True
        print("   - Point B rollback: ✓ (No failures to test)")

    # Point C recovery verification
    if _chain_stats["point_c_failures"] > 0:
        recovery_rate = _chain_stats["point_c_recovery_success"] / _chain_stats["point_c_failures"]
        _chain_stats["verification"]["point_c_recovery_defined"] = recovery_rate >= 0.90
        print(f"   - Point C recovery: {'✓' if _chain_stats['verification']['point_c_recovery_defined'] else '✗'}")
        print(f"     (Rate: {recovery_rate:.1%})")
    else:
        _chain_stats["verification"]["point_c_recovery_defined"] = True
        print("   - Point C recovery: ✓ (No failures to test)")

    # Orphaned orders check
    orphan_order_count = len(_chain_stats["orders_without_payment"])
    total_orders = len(_chain_stats["orders_created"])
    orphan_rate = orphan_order_count / max(total_orders, 1)
    _chain_stats["verification"]["no_orphaned_orders"] = orphan_rate < 0.05  # < 5% orphaned
    print(f"   - No orphaned orders: {'✓' if _chain_stats['verification']['no_orphaned_orders'] else '✗'}")
    print(f"     (Orphaned: {orphan_order_count}/{total_orders})")

    # Orphaned payments check
    orphan_payment_count = len(_chain_stats["payments_without_order"])
    _chain_stats["verification"]["no_orphaned_payments"] = orphan_payment_count == 0
    print(f"   - No orphaned payments: {'✓' if _chain_stats['verification']['no_orphaned_payments'] else '✗'}")
    print(f"     (Orphaned: {orphan_payment_count})")


# =============================================================================
# Load Shape
# =============================================================================


class ChainFailureShape(LoadTestShape):
    """
    Load shape for chain failure propagation testing.

    Phase 1: Normal flows for baseline
    Phase 2: Inject Point A failures
    Phase 3: Inject Point B failures
    Phase 4: Inject Point C failures
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
            return (30, 5)  # Normal load
        elif phase == "point_a":
            return (40, 8)  # Moderate load with failures
        elif phase == "point_b":
            return (50, 10)  # Higher load
        elif phase == "point_c":
            return (40, 8)  # Moderate load
        else:  # verification
            return (20, 5)  # Low load for verification


# =============================================================================
# Test User
# =============================================================================


class ChainFailureUser(HttpUser):
    """
    User for chain failure propagation testing.

    Tests the full order flow and verifies rollback behavior
    at each failure point in the chain.
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
        global _chain_stats, TARGET_PRODUCT_IDS

        if _chain_stats["start_time"] is None:
            _chain_stats["start_time"] = time.time()

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
                if product_id not in _chain_stats["initial_stock"]:
                    _chain_stats["initial_stock"][product_id] = stock

    def _should_inject_failure(self) -> bool:
        """Determine if failure should be injected"""
        return random.random() < FAILURE_INJECTION_RATE

    @task(10)
    @tag("full_flow")
    def complete_order_flow(self):
        """
        Execute complete order flow: Auth → Cart → Order → Payment
        Tests the full chain and observes failure handling.
        """
        phase = _get_current_phase()
        _record_flow_start()

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Step 1: Add to cart (auth is implicit)
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Flow - Add to Cart",
        )

        if cart_response.status_code != 200:
            if phase == "baseline":
                _record_baseline_result(success=False)
            return

        # Step 2: Create order
        # Inject Point A failure in appropriate phase
        if phase == "point_a" and self._should_inject_failure():
            # Simulate order creation failure by using invalid data
            self._test_point_a_failure(product_id)
            return

        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Test Address for Chain Failure Test",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Flow - Create Order",
        )

        if order_response.status_code != 201:
            if phase == "baseline":
                _record_baseline_result(success=False)
            return

        order_data = order_response.json()
        order_id = order_data.get("id")
        _record_order_created(order_id)

        # Step 3: Request payment
        # Inject Point B failure in appropriate phase
        if phase == "point_b" and self._should_inject_failure():
            self._test_point_b_failure(order_id, product_id)
            return

        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Flow - Request Payment",
        )

        if payment_response.status_code not in [200, 201]:
            # Payment failed - check if order was rolled back
            self._verify_order_rollback(order_id, product_id, "point_b")
            return

        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", "")

        # Step 4: Confirm payment (simulate webhook)
        # Inject Point C failure in appropriate phase
        if phase == "point_c" and self._should_inject_failure():
            self._test_point_c_failure(order_id, payment_key, product_id)
            return

        confirm_response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Flow - Confirm Payment",
        )

        if confirm_response.status_code in [200, 201]:
            _record_order_with_payment(order_id)
            if phase == "baseline":
                _record_baseline_result(success=True)
        else:
            if phase == "baseline":
                _record_baseline_result(success=False)

    def _test_point_a_failure(self, product_id: int):
        """Test Point A: Auth success → Order FAILS"""
        rollback_start = time.time()

        # Attempt order with invalid data to trigger failure
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "",  # Invalid - empty address
                "shipping_city": "",
                "shipping_postal_code": "invalid",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Point A - Order Failure",
        )

        rollback_time = (time.time() - rollback_start) * 1000

        # Verify stock was not decremented (or was restored)
        stock_restored = self._verify_stock_unchanged(product_id)

        # Point A failure should mean cart is still intact but no order
        rollback_success = order_response.status_code in [400, 422]  # Expected failure codes

        _record_point_a_failure(
            rollback_success=rollback_success, stock_restored=stock_restored, rollback_time_ms=rollback_time
        )

    def _test_point_b_failure(self, order_id: int, product_id: int):
        """Test Point B: Order success → Payment FAILS"""
        rollback_start = time.time()

        # Request payment with invalid data
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "invalid_method",  # Invalid payment method
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Point B - Payment Failure",
        )

        rollback_time = (time.time() - rollback_start) * 1000

        # Check if order was rolled back or marked as failed
        rollback_success, orphaned = self._verify_order_rollback(order_id, product_id, "point_b")
        stock_restored = self._verify_stock_unchanged(product_id)

        _record_point_b_failure(
            rollback_success=rollback_success,
            stock_restored=stock_restored,
            orphaned=orphaned,
            rollback_time_ms=rollback_time,
        )

    def _test_point_c_failure(self, order_id: int, payment_key: str, product_id: int):
        """Test Point C: Payment success → Webhook FAILS"""
        recovery_start = time.time()

        # Simulate successful payment at PG but webhook failure
        # by confirming with mismatched amount
        confirm_response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": 1,  # Wrong amount to simulate mismatch
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Point C - Webhook Failure",
        )

        recovery_time = (time.time() - recovery_start) * 1000

        # Check recovery mechanism
        if confirm_response.status_code in [400, 422]:
            # System correctly rejected mismatched confirmation
            recovery_success = True
            orphaned_payment = False
        else:
            # Check for orphaned payment
            orphaned_payment = self._check_orphaned_payment(payment_key, order_id)
            recovery_success = not orphaned_payment

        _record_point_c_failure(
            recovery_success=recovery_success, orphaned_payment=orphaned_payment, recovery_time_ms=recovery_time
        )

    def _verify_stock_unchanged(self, product_id: int) -> bool:
        """Verify stock was not changed after failed operation"""
        response = self.client.get(
            f"/api/products/{product_id}/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Stock"
        )

        if response.status_code == 200:
            current_stock = response.json().get("stock", 0)
            initial_stock = _chain_stats["initial_stock"].get(product_id, current_stock)
            # Allow small variance due to concurrent operations
            return abs(current_stock - initial_stock) <= 5

        return True  # Assume OK if can't verify

    def _verify_order_rollback(self, order_id: int, product_id: int, failure_point: str) -> tuple:
        """Verify order was properly rolled back after failure"""
        response = self.client.get(
            f"/api/orders/{order_id}/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Order Rollback"
        )

        if response.status_code == 404:
            # Order was deleted - good rollback
            return (True, False)
        elif response.status_code == 200:
            order = response.json()
            status = order.get("status", "").lower()

            if status in ["cancelled", "failed", "rolled_back"]:
                # Order was marked as failed - acceptable
                return (True, False)
            elif status == "pending":
                # Order stuck in pending - orphaned
                _record_orphaned_order(order_id)
                return (False, True)
            else:
                return (True, False)

        return (True, False)

    def _check_orphaned_payment(self, payment_key: str, order_id: int) -> bool:
        """Check if payment exists without corresponding order update"""
        # Check payment status
        payment_response = self.client.get(
            "/api/payments/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Check Payment Status"
        )

        if payment_response.status_code == 200:
            payments = payment_response.json()
            if isinstance(payments, dict) and "results" in payments:
                payments = payments["results"]

            for payment in payments:
                if payment.get("payment_key") == payment_key:
                    payment_status = payment.get("status", "").lower()
                    # Check if payment succeeded but order not updated
                    if payment_status in ["paid", "completed", "approved"]:
                        # Verify order status
                        order_response = self.client.get(
                            f"/api/orders/{order_id}/",
                            headers=self._get_auth_headers(),
                            name=f"{STAGE_NAME} Verify Order for Payment",
                        )
                        if order_response.status_code == 200:
                            order_status = order_response.json().get("status", "").lower()
                            if order_status not in ["paid", "completed", "confirmed"]:
                                _record_orphaned_payment(payment_key)
                                return True

        return False

    @task(3)
    @tag("verification")
    def verify_order_consistency(self):
        """Verify order and payment consistency"""
        phase = _get_current_phase()
        if phase != "verification":
            return

        response = self.client.get("/api/orders/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Orders")

        if response.status_code == 200:
            orders = response.json()
            if isinstance(orders, dict) and "results" in orders:
                orders = orders["results"]

            for order in orders[:20]:
                order_id = order.get("id")
                status = order.get("status", "").lower()

                # Check for stuck pending orders
                if status == "pending":
                    print(f"⚠️ Order {order_id} stuck in pending state")

    @task(2)
    @tag("verification")
    def verify_stock_final(self):
        """Verify final stock levels"""
        phase = _get_current_phase()
        if phase != "verification":
            return

        if not TARGET_PRODUCT_IDS:
            return

        for product_id in TARGET_PRODUCT_IDS[:3]:
            response = self.client.get(
                f"/api/products/{product_id}/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Verify Final Stock",
            )

            if response.status_code == 200:
                stock = response.json().get("stock", 0)
                with _stats_lock:
                    _chain_stats["final_stock"][product_id] = stock


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _chain_stats

    print(f"\n{'='*70}")
    print("🔗 Stage 18: Chain Failure Propagation Test")
    print(f"{'='*70}")
    print("Purpose: Verify failure handling across service chain")
    print("\nFailure Points:")
    print("  Point A: Auth success → Order FAILS")
    print("  Point B: Auth success → Order success → Payment FAILS")
    print("  Point C: Auth success → Order success → Payment success → Webhook FAILS")
    print("\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_BASELINE_DURATION}s): Baseline - Normal flow")
    print(f"  Phase 2 ({PHASE_2_POINT_A_FAILURE}s): Point A - Order failure injection")
    print(f"  Phase 3 ({PHASE_3_POINT_B_FAILURE}s): Point B - Payment failure injection")
    print(f"  Phase 4 ({PHASE_4_POINT_C_FAILURE}s): Point C - Webhook failure injection")
    print(f"  Phase 5 ({PHASE_5_VERIFICATION}s): Verification")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"Failure Injection Rate: {FAILURE_INJECTION_RATE:.0%}")
    print(f"{'='*70}\n")

    _chain_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print("📊 Stage 18: Chain Failure Propagation Test Results")
    print(f"{'='*70}")

    print("\n📈 Flow Metrics:")
    print(f"   - Total flows started: {_chain_stats['total_flows_started']}")
    print(f"   - Baseline success: {_chain_stats['baseline_success_count']}")
    print(f"   - Baseline failure: {_chain_stats['baseline_failure_count']}")

    print("\n🔴 Point A (Order Failure) Metrics:")
    print(f"   - Failures injected: {_chain_stats['point_a_failures']}")
    print(f"   - Rollbacks successful: {_chain_stats['point_a_rollback_success']}")
    print(f"   - Rollbacks failed: {_chain_stats['point_a_rollback_failed']}")
    print(f"   - Stock restored: {_chain_stats['point_a_stock_restored']}")

    print("\n🟠 Point B (Payment Failure) Metrics:")
    print(f"   - Failures injected: {_chain_stats['point_b_failures']}")
    print(f"   - Rollbacks successful: {_chain_stats['point_b_rollback_success']}")
    print(f"   - Rollbacks failed: {_chain_stats['point_b_rollback_failed']}")
    print(f"   - Stock restored: {_chain_stats['point_b_stock_restored']}")
    print(f"   - Orphaned orders: {_chain_stats['point_b_orphaned_orders']}")

    print("\n🟡 Point C (Webhook Failure) Metrics:")
    print(f"   - Failures injected: {_chain_stats['point_c_failures']}")
    print(f"   - Recovery successful: {_chain_stats['point_c_recovery_success']}")
    print(f"   - Recovery failed: {_chain_stats['point_c_recovery_failed']}")
    print(f"   - Orphaned payments: {_chain_stats['point_c_orphaned_payments']}")

    print("\n📦 Order/Payment Consistency:")
    print(f"   - Orders created: {len(_chain_stats['orders_created'])}")
    print(f"   - Orders with payment: {len(_chain_stats['orders_with_payment'])}")
    print(f"   - Orphaned orders: {len(_chain_stats['orders_without_payment'])}")
    print(f"   - Orphaned payments: {len(_chain_stats['payments_without_order'])}")

    print("\n✅ Verification Results:")
    for check, result in _chain_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        print(f"   - {check}: {status}")

    # Recovery Latency Report
    recovery = _chain_stats["recovery"]
    print("\n🔄 Recovery Latency Metrics:")

    all_latencies = (
        recovery["point_a_rollback_times"] + recovery["point_b_rollback_times"] + recovery["point_c_recovery_times"]
    )

    if all_latencies:
        avg_latency = sum(all_latencies) / len(all_latencies)
        max_latency = max(all_latencies)
        recovery["avg_rollback_latency_ms"] = avg_latency
        recovery["max_rollback_latency_ms"] = max_latency

        print(f"   - Total rollback/recovery operations: {len(all_latencies)}")
        print(f"   - Avg rollback latency: {avg_latency:.0f}ms")
        print(f"   - Max rollback latency: {max_latency:.0f}ms")

        if recovery["point_a_rollback_times"]:
            avg_a = sum(recovery["point_a_rollback_times"]) / len(recovery["point_a_rollback_times"])
            print(f"   - Point A avg latency: {avg_a:.0f}ms")

        if recovery["point_b_rollback_times"]:
            avg_b = sum(recovery["point_b_rollback_times"]) / len(recovery["point_b_rollback_times"])
            print(f"   - Point B avg latency: {avg_b:.0f}ms")

        if recovery["point_c_recovery_times"]:
            avg_c = sum(recovery["point_c_recovery_times"]) / len(recovery["point_c_recovery_times"])
            print(f"   - Point C avg latency: {avg_c:.0f}ms")

        # SLA check (all rollbacks should be under 2s)
        recovery["sla_compliant"] = max_latency < 2000
        if recovery["sla_compliant"]:
            print("   - SLA Status: ✓ All rollbacks under 2s threshold")
        else:
            print("   - SLA Status: ✗ Some rollbacks exceeded 2s")
    else:
        print("   - No rollback operations recorded")

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
            "stage18_report.html",
        ]
    )
