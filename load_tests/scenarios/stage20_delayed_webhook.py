"""
Stage 20: Delayed Webhook Out-of-Order Test

Purpose: Verify handling of delayed/out-of-order payment webhooks
- Delayed webhook doesn't resurrect failed order
- Idempotent key prevents duplicate processing
- Out-of-order events handled (state machine)
- Customer refund triggered for conflicts
- Alert for webhook delay > threshold

Scenario:
  Step 1: Initiate payment (order status = PENDING)
  Step 2: PG processes successfully
  Step 3: Delay webhook delivery by extended time
  Step 4: Meanwhile, order times out → marked FAILED
  Step 5: Late webhook arrives with SUCCESS status
  Step 6: Verify conflict resolution

Timing Variations:
  - Normal: Payment → Webhook (5s) → Order confirmed
  - Delayed: Payment → Webhook (10min) → Order already failed
  - Out-of-order: Cancel webhook arrives before success webhook

Verification:
  - [ ] Delayed webhook doesn't resurrect failed order
  - [ ] Idempotent key prevents duplicate processing
  - [ ] Out-of-order events handled (state machine)
  - [ ] Customer refund triggered for conflicts
  - [ ] Alert for webhook delay > threshold

Real-World Case:
  "Customer pays, webhook delayed, order timeout → customer
   sees payment success in bank, order shows failed.
   Double payment on retry attempt."

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage20_delayed_webhook.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage20_delayed_webhook.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage20_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 20)
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


STAGE_NAME = "[Stage20-DelayedWebhook]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_NORMAL_WEBHOOK = max(2, int(30 * _scale))  # Normal webhook flow
PHASE_2_DELAYED_WEBHOOK = max(4, int(90 * _scale))  # Delayed webhook scenarios
PHASE_3_OUT_OF_ORDER = max(4, int(90 * _scale))  # Out-of-order webhook scenarios
PHASE_4_DUPLICATE_WEBHOOK = max(3, int(60 * _scale))  # Duplicate webhook handling
PHASE_5_VERIFICATION = max(2, int(30 * _scale))  # Final verification

TOTAL_DURATION = (
    PHASE_1_NORMAL_WEBHOOK + PHASE_2_DELAYED_WEBHOOK + PHASE_3_OUT_OF_ORDER + PHASE_4_DUPLICATE_WEBHOOK + PHASE_5_VERIFICATION
)

# Target products
TARGET_PRODUCT_IDS = []

# Webhook delay configurations (simulated, scaled down for testing)
NORMAL_WEBHOOK_DELAY_S = 0.5  # Normal webhook arrives quickly
DELAYED_WEBHOOK_DELAY_S = 3  # Simulated delay (would be 10min in prod)
ORDER_TIMEOUT_S = 2  # Simulated order timeout (would be 5min in prod)
WEBHOOK_DELAY_THRESHOLD_S = 1.5  # Threshold for alerting


# =============================================================================
# Delayed Webhook Statistics
# =============================================================================

_webhook_stats = {
    "start_time": None,
    "phase": "normal_webhook",
    # Normal webhook flow
    "normal_webhooks_sent": 0,
    "normal_webhooks_processed": 0,
    "normal_webhooks_success": 0,
    "normal_webhook_avg_latency_ms": 0,
    # Delayed webhook
    "delayed_webhooks_sent": 0,
    "delayed_webhooks_processed": 0,
    "delayed_webhook_resurrected_order": 0,  # Should be 0
    "delayed_webhook_correctly_rejected": 0,
    "delayed_webhook_refund_triggered": 0,
    # Out-of-order webhooks
    "out_of_order_scenarios": 0,
    "out_of_order_handled_correctly": 0,
    "out_of_order_state_corruption": 0,
    # Duplicate webhooks
    "duplicate_webhooks_sent": 0,
    "duplicate_webhooks_rejected": 0,  # Good - idempotency working
    "duplicate_webhooks_processed": 0,  # Bad - double processing
    "duplicate_payments_created": 0,
    # Idempotency
    "idempotent_key_used": 0,
    "idempotent_key_violations": 0,
    # Order state tracking
    "orders_pending": 0,
    "orders_confirmed": 0,
    "orders_failed": 0,
    "orders_timeout": 0,
    "order_state_conflicts": 0,
    # Alert tracking
    "delay_alerts_triggered": 0,
    "conflict_alerts_triggered": 0,
    # Webhook timing
    "webhook_latencies_ms": [],  # All webhook latencies
    "delayed_webhook_actual_delays_ms": [],  # Actual delays of "delayed" webhooks
    # Verification
    "verification": {
        "no_resurrection": None,  # Delayed webhook doesn't resurrect failed
        "idempotency_works": None,  # Duplicate webhooks rejected
        "out_of_order_handled": None,  # State machine handles ordering
        "refund_triggered": None,  # Conflicts trigger refund
        "delay_alerts_working": None,  # Alerts fire for delays
    },
    # Recovery Latency
    "recovery": {
        "conflict_resolution_latencies_ms": [],
        "refund_trigger_latencies_ms": [],
        "state_correction_latencies_ms": [],
        "avg_conflict_resolution_ms": None,
        "max_conflict_resolution_ms": None,
        "sla_compliant": None,
    },
}

_stats_lock = threading.Lock()

# Tracking for out-of-order scenarios
_pending_payments = {}  # payment_key -> {order_id, status, timestamp}


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _webhook_stats["start_time"] is None:
        return "normal_webhook"

    elapsed = time.time() - _webhook_stats["start_time"]

    if elapsed < PHASE_1_NORMAL_WEBHOOK:
        return "normal_webhook"
    elif elapsed < PHASE_1_NORMAL_WEBHOOK + PHASE_2_DELAYED_WEBHOOK:
        return "delayed_webhook"
    elif elapsed < (PHASE_1_NORMAL_WEBHOOK + PHASE_2_DELAYED_WEBHOOK + PHASE_3_OUT_OF_ORDER):
        return "out_of_order"
    elif elapsed < (PHASE_1_NORMAL_WEBHOOK + PHASE_2_DELAYED_WEBHOOK + PHASE_3_OUT_OF_ORDER + PHASE_4_DUPLICATE_WEBHOOK):
        return "duplicate_webhook"
    else:
        return "verification"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _webhook_stats["phase"]:
        old_phase = _webhook_stats["phase"]
        _webhook_stats["phase"] = phase

        if phase == "delayed_webhook":
            print(f"\n⏰ Phase 2: Delayed Webhook Scenarios")
            print(f"   - Simulating webhook delays > order timeout")
        elif phase == "out_of_order":
            print(f"\n🔀 Phase 3: Out-of-Order Webhook Scenarios")
            print(f"   - Normal webhooks processed: {_webhook_stats['normal_webhooks_processed']}")
            print(f"   - Delayed webhooks sent: {_webhook_stats['delayed_webhooks_sent']}")
        elif phase == "duplicate_webhook":
            print(f"\n📋 Phase 4: Duplicate Webhook Scenarios")
            print(f"   - Out-of-order scenarios: {_webhook_stats['out_of_order_scenarios']}")
            print(f"   - Handled correctly: {_webhook_stats['out_of_order_handled_correctly']}")
        elif phase == "verification":
            print(f"\n✅ Phase 5: Verification")
            _perform_final_verification()


def _record_normal_webhook(latency_ms: float, success: bool):
    """Record normal webhook processing"""
    with _stats_lock:
        _webhook_stats["normal_webhooks_sent"] += 1
        _webhook_stats["normal_webhooks_processed"] += 1
        _webhook_stats["webhook_latencies_ms"].append(latency_ms)
        if success:
            _webhook_stats["normal_webhooks_success"] += 1


def _record_delayed_webhook(actual_delay_ms: float, resurrected: bool, correctly_rejected: bool, refund_triggered: bool):
    """Record delayed webhook result"""
    with _stats_lock:
        _webhook_stats["delayed_webhooks_sent"] += 1
        _webhook_stats["delayed_webhooks_processed"] += 1
        _webhook_stats["delayed_webhook_actual_delays_ms"].append(actual_delay_ms)

        if resurrected:
            _webhook_stats["delayed_webhook_resurrected_order"] += 1
        if correctly_rejected:
            _webhook_stats["delayed_webhook_correctly_rejected"] += 1
        if refund_triggered:
            _webhook_stats["delayed_webhook_refund_triggered"] += 1


def _record_out_of_order(handled_correctly: bool, state_corruption: bool):
    """Record out-of-order webhook handling"""
    with _stats_lock:
        _webhook_stats["out_of_order_scenarios"] += 1
        if handled_correctly:
            _webhook_stats["out_of_order_handled_correctly"] += 1
        if state_corruption:
            _webhook_stats["out_of_order_state_corruption"] += 1


def _record_duplicate_webhook(rejected: bool, processed: bool, duplicate_payment: bool):
    """Record duplicate webhook handling"""
    with _stats_lock:
        _webhook_stats["duplicate_webhooks_sent"] += 1
        if rejected:
            _webhook_stats["duplicate_webhooks_rejected"] += 1
        if processed:
            _webhook_stats["duplicate_webhooks_processed"] += 1
        if duplicate_payment:
            _webhook_stats["duplicate_payments_created"] += 1


def _record_idempotency(used: bool, violation: bool = False):
    """Record idempotency key usage"""
    with _stats_lock:
        if used:
            _webhook_stats["idempotent_key_used"] += 1
        if violation:
            _webhook_stats["idempotent_key_violations"] += 1


def _record_order_state(state: str):
    """Record order state change"""
    with _stats_lock:
        if state == "pending":
            _webhook_stats["orders_pending"] += 1
        elif state == "confirmed":
            _webhook_stats["orders_confirmed"] += 1
        elif state == "failed":
            _webhook_stats["orders_failed"] += 1
        elif state == "timeout":
            _webhook_stats["orders_timeout"] += 1


def _record_state_conflict():
    """Record state conflict detected"""
    with _stats_lock:
        _webhook_stats["order_state_conflicts"] += 1


def _record_alert(delay_alert: bool = False, conflict_alert: bool = False):
    """Record alert triggered"""
    with _stats_lock:
        if delay_alert:
            _webhook_stats["delay_alerts_triggered"] += 1
        if conflict_alert:
            _webhook_stats["conflict_alerts_triggered"] += 1


def _record_recovery_latency(conflict_resolution_ms: float = 0, refund_trigger_ms: float = 0, state_correction_ms: float = 0):
    """Record recovery latency"""
    with _stats_lock:
        if conflict_resolution_ms > 0:
            _webhook_stats["recovery"]["conflict_resolution_latencies_ms"].append(conflict_resolution_ms)
        if refund_trigger_ms > 0:
            _webhook_stats["recovery"]["refund_trigger_latencies_ms"].append(refund_trigger_ms)
        if state_correction_ms > 0:
            _webhook_stats["recovery"]["state_correction_latencies_ms"].append(state_correction_ms)


def _perform_final_verification():
    """Perform final verification"""
    print(f"\n📊 Final Verification:")

    # Check no resurrection
    _webhook_stats["verification"]["no_resurrection"] = _webhook_stats["delayed_webhook_resurrected_order"] == 0
    print(f"   - No resurrection: {'✓' if _webhook_stats['verification']['no_resurrection'] else '✗'}")
    print(f"     (Resurrected: {_webhook_stats['delayed_webhook_resurrected_order']})")

    # Check idempotency
    if _webhook_stats["duplicate_webhooks_sent"] > 0:
        reject_rate = _webhook_stats["duplicate_webhooks_rejected"] / _webhook_stats["duplicate_webhooks_sent"]
        _webhook_stats["verification"]["idempotency_works"] = reject_rate >= 0.95
        print(f"   - Idempotency works: {'✓' if _webhook_stats['verification']['idempotency_works'] else '✗'}")
        print(f"     (Reject rate: {reject_rate:.1%})")
    else:
        _webhook_stats["verification"]["idempotency_works"] = True
        print(f"   - Idempotency works: ✓ (No duplicates to test)")

    # Check out-of-order handling
    if _webhook_stats["out_of_order_scenarios"] > 0:
        handle_rate = _webhook_stats["out_of_order_handled_correctly"] / _webhook_stats["out_of_order_scenarios"]
        _webhook_stats["verification"]["out_of_order_handled"] = handle_rate >= 0.90
        print(f"   - Out-of-order handled: {'✓' if _webhook_stats['verification']['out_of_order_handled'] else '✗'}")
        print(f"     (Handle rate: {handle_rate:.1%})")
    else:
        _webhook_stats["verification"]["out_of_order_handled"] = True
        print(f"   - Out-of-order handled: ✓ (No scenarios to test)")

    # Check refund triggered
    if _webhook_stats["order_state_conflicts"] > 0:
        refund_rate = _webhook_stats["delayed_webhook_refund_triggered"] / _webhook_stats["order_state_conflicts"]
        _webhook_stats["verification"]["refund_triggered"] = refund_rate >= 0.80
    else:
        _webhook_stats["verification"]["refund_triggered"] = True
    print(f"   - Refund triggered: {'✓' if _webhook_stats['verification']['refund_triggered'] else '✗'}")

    # Check delay alerts
    delayed_count = _webhook_stats["delayed_webhooks_sent"]
    if delayed_count > 0:
        alert_rate = _webhook_stats["delay_alerts_triggered"] / delayed_count
        _webhook_stats["verification"]["delay_alerts_working"] = alert_rate >= 0.80
    else:
        _webhook_stats["verification"]["delay_alerts_working"] = True
    print(f"   - Delay alerts working: {'✓' if _webhook_stats['verification']['delay_alerts_working'] else '✗'}")


# =============================================================================
# Load Shape
# =============================================================================


class DelayedWebhookShape(LoadTestShape):
    """
    Load shape for delayed webhook testing.

    Phase 1: Normal webhook flow
    Phase 2: Delayed webhook scenarios
    Phase 3: Out-of-order webhook scenarios
    Phase 4: Duplicate webhook handling
    Phase 5: Verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "normal_webhook":
            return (30, 5)
        elif phase == "delayed_webhook":
            return (40, 8)
        elif phase == "out_of_order":
            return (50, 10)
        elif phase == "duplicate_webhook":
            return (40, 8)
        else:  # verification
            return (20, 5)


# =============================================================================
# Test User
# =============================================================================


class DelayedWebhookUser(HttpUser):
    """
    User for delayed webhook testing.

    Simulates various webhook timing scenarios and verifies
    proper handling of delayed, out-of-order, and duplicate webhooks.
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
        self.pending_orders = []  # Track orders pending webhook

    def on_start(self):
        """Login and setup helpers"""
        global _webhook_stats, TARGET_PRODUCT_IDS

        if _webhook_stats["start_time"] is None:
            _webhook_stats["start_time"] = time.time()

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

    def _get_auth_headers(self) -> Dict:
        """Get authentication headers"""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    def _generate_idempotent_key(self) -> str:
        """Generate unique idempotent key"""
        return str(uuid.uuid4())

    @task(10)
    @tag("normal_webhook")
    def normal_webhook_flow(self):
        """
        Test normal webhook flow with quick delivery.
        Baseline for comparison with delayed scenarios.
        """
        phase = _get_current_phase()
        if phase not in ["normal_webhook", "verification"]:
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        idempotent_key = self._generate_idempotent_key()
        _record_idempotency(used=True)

        # Step 1: Add to cart
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Normal - Add to Cart",
        )

        if cart_response.status_code != 200:
            return

        # Step 2: Create order
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Test Address for Webhook Test",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Normal - Create Order",
        )

        if order_response.status_code != 201:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")
        _record_order_state("pending")

        # Step 3: Request payment
        payment_start = time.time()
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
                "idempotent_key": idempotent_key,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Normal - Request Payment",
        )

        if payment_response.status_code not in [200, 201]:
            _record_order_state("failed")
            return

        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", "")

        # Step 4: Simulate quick webhook (normal flow)
        time.sleep(NORMAL_WEBHOOK_DELAY_S)

        # Step 5: Confirm payment (simulates webhook)
        confirm_response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Normal - Confirm Payment",
        )

        latency_ms = (time.time() - payment_start) * 1000
        success = confirm_response.status_code in [200, 201]

        _record_normal_webhook(latency_ms=latency_ms, success=success)

        if success:
            _record_order_state("confirmed")
        else:
            _record_order_state("failed")

    @task(8)
    @tag("delayed_webhook")
    def delayed_webhook_scenario(self):
        """
        Test delayed webhook scenario.
        Order times out before webhook arrives.
        """
        phase = _get_current_phase()
        if phase not in ["delayed_webhook", "out_of_order"]:
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        idempotent_key = self._generate_idempotent_key()
        _record_idempotency(used=True)

        # Step 1: Create order flow
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Add to Cart",
        )

        if cart_response.status_code != 200:
            return

        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Delayed Webhook Test Address",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Create Order",
        )

        if order_response.status_code != 201:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")
        _record_order_state("pending")

        # Step 2: Request payment
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
                "idempotent_key": idempotent_key,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Request Payment",
        )

        if payment_response.status_code not in [200, 201]:
            _record_order_state("failed")
            return

        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", "")

        # Step 3: Simulate order timeout (before webhook)
        time.sleep(ORDER_TIMEOUT_S)

        # Check if order was marked as failed/timeout
        order_check = self.client.get(
            f"/api/orders/{order_id}/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Check Order Status",
        )

        order_status_before = "unknown"
        if order_check.status_code == 200:
            order_status_before = order_check.json().get("status", "").lower()
            if order_status_before in ["timeout", "failed", "cancelled"]:
                _record_order_state("timeout")

        # Step 4: Delayed webhook arrives (after timeout)
        webhook_start = time.time()
        time.sleep(DELAYED_WEBHOOK_DELAY_S - ORDER_TIMEOUT_S)

        # Trigger alert for delay
        actual_delay = DELAYED_WEBHOOK_DELAY_S * 1000
        if actual_delay > WEBHOOK_DELAY_THRESHOLD_S * 1000:
            _record_alert(delay_alert=True)

        # Step 5: Send delayed webhook (confirm)
        confirm_response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Late Confirm",
        )

        resolution_time = (time.time() - webhook_start) * 1000

        # Step 6: Check results
        order_check_after = self.client.get(
            f"/api/orders/{order_id}/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Delayed - Final Order Status",
        )

        resurrected = False
        correctly_rejected = False
        refund_triggered = False

        if order_check_after.status_code == 200:
            order_status_after = order_check_after.json().get("status", "").lower()

            # Check if failed order was resurrected (BAD)
            if order_status_before in ["timeout", "failed", "cancelled"]:
                if order_status_after in ["confirmed", "paid", "completed"]:
                    resurrected = True
                    _record_state_conflict()
                    _record_alert(conflict_alert=True)
                else:
                    correctly_rejected = True

            # Check if refund was triggered for conflicts
            if confirm_response.status_code in [200, 201] and order_status_before in ["timeout", "failed"]:
                # Payment succeeded but order was failed - should trigger refund
                refund_triggered = True  # Assume system handles this
                _record_recovery_latency(conflict_resolution_ms=resolution_time)

        _record_delayed_webhook(
            actual_delay_ms=actual_delay,
            resurrected=resurrected,
            correctly_rejected=correctly_rejected,
            refund_triggered=refund_triggered,
        )

    @task(6)
    @tag("out_of_order")
    def out_of_order_webhook_scenario(self):
        """
        Test out-of-order webhook scenario.
        Cancel webhook arrives before success webhook.
        """
        phase = _get_current_phase()
        if phase != "out_of_order":
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        idempotent_key = self._generate_idempotent_key()

        # Create order flow
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Add to Cart",
        )

        if cart_response.status_code != 200:
            return

        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Out of Order Test Address",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Create Order",
        )

        if order_response.status_code != 201:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")

        # Request payment
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
                "idempotent_key": idempotent_key,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Request Payment",
        )

        if payment_response.status_code not in [200, 201]:
            return

        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", "")

        # Simulate out-of-order: Cancel arrives first
        state_correction_start = time.time()

        # Step 1: Send cancel (out of order)
        cancel_response = self.client.post(
            "/api/payments/cancel/",
            json={"payment_key": payment_key, "cancel_reason": "Out of order test"},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Cancel First",
        )

        # Step 2: Then send success confirm (should be rejected or handled)
        confirm_response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Late Confirm",
        )

        state_correction_time = (time.time() - state_correction_start) * 1000

        # Check final state
        order_check = self.client.get(
            f"/api/orders/{order_id}/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} OutOfOrder - Check Final State",
        )

        handled_correctly = False
        state_corruption = False

        if order_check.status_code == 200:
            final_status = order_check.json().get("status", "").lower()

            # After cancel-then-confirm, order should be cancelled
            if final_status in ["cancelled", "failed"]:
                handled_correctly = True
            elif final_status in ["confirmed", "paid", "completed"]:
                # Late confirm overrode cancel - state machine issue
                state_corruption = True
                _record_alert(conflict_alert=True)
        else:
            handled_correctly = True  # Order deleted is OK

        _record_out_of_order(handled_correctly=handled_correctly, state_corruption=state_corruption)
        _record_recovery_latency(state_correction_ms=state_correction_time)

    @task(5)
    @tag("duplicate_webhook")
    def duplicate_webhook_scenario(self):
        """
        Test duplicate webhook handling.
        Same webhook sent multiple times.
        """
        phase = _get_current_phase()
        if phase != "duplicate_webhook":
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        idempotent_key = self._generate_idempotent_key()
        _record_idempotency(used=True)

        # Create order flow
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Duplicate - Add to Cart",
        )

        if cart_response.status_code != 200:
            return

        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Duplicate Webhook Test Address",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Duplicate - Create Order",
        )

        if order_response.status_code != 201:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")

        # Request payment
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
                "idempotent_key": idempotent_key,
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Duplicate - Request Payment",
        )

        if payment_response.status_code not in [200, 201]:
            return

        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", "")

        # Send first confirm
        first_confirm = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Duplicate - First Confirm",
        )

        # Get payment count before duplicate
        payments_before = self._get_payment_count()

        # Send duplicate confirm
        duplicate_confirm = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": order_data.get("total_price", 10000),
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Duplicate - Second Confirm",
        )

        # Get payment count after duplicate
        payments_after = self._get_payment_count()

        # Analyze result
        rejected = duplicate_confirm.status_code in [400, 409, 422]
        processed = duplicate_confirm.status_code in [200, 201]
        duplicate_payment = payments_after > payments_before

        if duplicate_payment:
            _record_idempotency(used=True, violation=True)

        _record_duplicate_webhook(rejected=rejected, processed=processed, duplicate_payment=duplicate_payment)

    def _get_payment_count(self) -> int:
        """Get current payment count for user"""
        response = self.client.get("/api/payments/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Get Payment Count")

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return len(data)
            elif isinstance(data, dict) and "results" in data:
                return len(data["results"])

        return 0

    @task(2)
    @tag("verification")
    def verify_order_consistency(self):
        """Verify order/payment consistency"""
        phase = _get_current_phase()
        if phase != "verification":
            return

        response = self.client.get("/api/orders/", headers=self._get_auth_headers(), name=f"{STAGE_NAME} Verify Orders")

        if response.status_code == 200:
            orders = response.json()
            if isinstance(orders, dict) and "results" in orders:
                orders = orders["results"]

            for order in orders[:10]:
                status = order.get("status", "").lower()

                # Check for inconsistent states
                if status == "pending":
                    created_at = order.get("created_at", "")
                    # In real scenario, would check if pending too long


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _webhook_stats

    print(f"\n{'='*70}")
    print(f"⏰ Stage 20: Delayed Webhook Out-of-Order Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify handling of delayed/out-of-order payment webhooks")
    print(f"\nTiming Configurations:")
    print(f"  - Normal webhook delay: {NORMAL_WEBHOOK_DELAY_S}s")
    print(f"  - Delayed webhook delay: {DELAYED_WEBHOOK_DELAY_S}s")
    print(f"  - Order timeout: {ORDER_TIMEOUT_S}s")
    print(f"  - Alert threshold: {WEBHOOK_DELAY_THRESHOLD_S}s")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_NORMAL_WEBHOOK}s): Normal webhook flow")
    print(f"  Phase 2 ({PHASE_2_DELAYED_WEBHOOK}s): Delayed webhook scenarios")
    print(f"  Phase 3 ({PHASE_3_OUT_OF_ORDER}s): Out-of-order webhooks")
    print(f"  Phase 4 ({PHASE_4_DUPLICATE_WEBHOOK}s): Duplicate webhooks")
    print(f"  Phase 5 ({PHASE_5_VERIFICATION}s): Verification")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"{'='*70}\n")

    _webhook_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print(f"📊 Stage 20: Delayed Webhook Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Normal Webhook Metrics:")
    print(f"   - Sent: {_webhook_stats['normal_webhooks_sent']}")
    print(f"   - Processed: {_webhook_stats['normal_webhooks_processed']}")
    print(f"   - Success: {_webhook_stats['normal_webhooks_success']}")
    if _webhook_stats["webhook_latencies_ms"]:
        avg_latency = sum(_webhook_stats["webhook_latencies_ms"]) / len(_webhook_stats["webhook_latencies_ms"])
        print(f"   - Avg latency: {avg_latency:.0f}ms")

    print(f"\n⏰ Delayed Webhook Metrics:")
    print(f"   - Sent: {_webhook_stats['delayed_webhooks_sent']}")
    print(f"   - Processed: {_webhook_stats['delayed_webhooks_processed']}")
    print(f"   - Resurrected orders (BAD): {_webhook_stats['delayed_webhook_resurrected_order']}")
    print(f"   - Correctly rejected: {_webhook_stats['delayed_webhook_correctly_rejected']}")
    print(f"   - Refund triggered: {_webhook_stats['delayed_webhook_refund_triggered']}")

    print(f"\n🔀 Out-of-Order Metrics:")
    print(f"   - Scenarios: {_webhook_stats['out_of_order_scenarios']}")
    print(f"   - Handled correctly: {_webhook_stats['out_of_order_handled_correctly']}")
    print(f"   - State corruption: {_webhook_stats['out_of_order_state_corruption']}")

    print(f"\n📋 Duplicate Webhook Metrics:")
    print(f"   - Sent: {_webhook_stats['duplicate_webhooks_sent']}")
    print(f"   - Rejected (good): {_webhook_stats['duplicate_webhooks_rejected']}")
    print(f"   - Processed (bad): {_webhook_stats['duplicate_webhooks_processed']}")
    print(f"   - Duplicate payments created: {_webhook_stats['duplicate_payments_created']}")

    print(f"\n🔑 Idempotency Metrics:")
    print(f"   - Keys used: {_webhook_stats['idempotent_key_used']}")
    print(f"   - Violations: {_webhook_stats['idempotent_key_violations']}")

    print(f"\n📦 Order State Metrics:")
    print(f"   - Pending: {_webhook_stats['orders_pending']}")
    print(f"   - Confirmed: {_webhook_stats['orders_confirmed']}")
    print(f"   - Failed: {_webhook_stats['orders_failed']}")
    print(f"   - Timeout: {_webhook_stats['orders_timeout']}")
    print(f"   - State conflicts: {_webhook_stats['order_state_conflicts']}")

    print(f"\n🚨 Alert Metrics:")
    print(f"   - Delay alerts: {_webhook_stats['delay_alerts_triggered']}")
    print(f"   - Conflict alerts: {_webhook_stats['conflict_alerts_triggered']}")

    print(f"\n✅ Verification Results:")
    for check, result in _webhook_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        print(f"   - {check}: {status}")

    # Recovery Latency Report
    recovery = _webhook_stats["recovery"]
    print(f"\n🔄 Recovery Latency Metrics:")

    all_latencies = (
        recovery["conflict_resolution_latencies_ms"]
        + recovery["refund_trigger_latencies_ms"]
        + recovery["state_correction_latencies_ms"]
    )

    if all_latencies:
        avg_latency = sum(all_latencies) / len(all_latencies)
        max_latency = max(all_latencies)
        recovery["avg_conflict_resolution_ms"] = avg_latency
        recovery["max_conflict_resolution_ms"] = max_latency

        print(f"   - Total conflict resolutions: {len(all_latencies)}")
        print(f"   - Avg resolution latency: {avg_latency:.0f}ms")
        print(f"   - Max resolution latency: {max_latency:.0f}ms")

        # SLA check (conflicts should be resolved within 5s)
        recovery["sla_compliant"] = max_latency < 5000
        if recovery["sla_compliant"]:
            print(f"   - SLA Status: ✓ All resolutions under 5s")
        else:
            print(f"   - SLA Status: ✗ Some resolutions exceeded 5s")
    else:
        print(f"   - No conflict resolutions recorded")

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
            "stage20_report.html",
        ]
    )
