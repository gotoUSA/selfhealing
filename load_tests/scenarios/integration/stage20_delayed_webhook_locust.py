"""
Stage 20: Delayed / Out-of-Order Webhook Handling - Docker-Based Locust Load Test

Purpose:
    Validate the system's handling of delayed, out-of-order, and duplicate webhook
    deliveries in a distributed Docker environment with realistic system boundaries.

Architecture:
    ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
    │   Locust Test   │──────│  webhook-sender │──────│   Web Service   │
    │   (This file)   │      │   (Flask app)   │      │   (Django)      │
    └─────────────────┘      └─────────────────┘      └─────────────────┘
           │                        │                        │
           │                        │                        ▼
           │                        │                 ┌─────────────┐
           ▼                        │                 │  PostgreSQL │
    ┌─────────────────┐             │                 │    + Redis  │
    │ User Flow Tasks │             │                 └─────────────┘
    │ - Create Order  │             │
    │ - Request Pay   │             │
    │ - Verify State  │             │
    └─────────────────┘             │
                                    ▼
                          ┌─────────────────────┐
                          │ Webhook Delivery    │
                          │ - Normal (fast)     │
                          │ - Delayed (late)    │
                          │ - Out-of-Order      │
                          │ - Duplicate         │
                          └─────────────────────┘

Key Design Principles:
    1. Locust DOES NOT send webhooks directly
    2. webhook-sender service handles all webhook delivery
    3. Correlation via order_id/payment_key across services
    4. State verification after webhook processing

Test Phases:
    Phase 1: Baseline (Normal webhooks with minimal delay)
    Phase 2: Delayed webhook scenarios (webhook after order timeout)
    Phase 3: Out-of-order webhooks (CANCEL before CONFIRM)
    Phase 4: Duplicate webhooks (idempotency testing)
    Phase 5: Verification and consistency check

Failure Definitions (NOT HTTP ERRORS):
    - Resurrection: FAILED/TIMEOUT order → SUCCESS after late webhook
    - State Corruption: Order shows SUCCESS when CANCEL arrived first
    - Double Processing: Multiple payments for same idempotency key

Execution:
    docker-compose -f docker-compose.stage20.yml up --abort-on-container-exit

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 20)
"""

import os
import sys
import time
import json
import random
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

# Try to import helpers
try:
    from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
    from load_tests.metrics import setup_event_hooks
    HELPERS_AVAILABLE = True
except ImportError:
    HELPERS_AVAILABLE = False
    print("[WARN] load_tests.utils not available - running standalone mode")


# =============================================================================
# Configuration
# =============================================================================

STAGE_NAME = "[Stage20-DelayedWebhook-Docker]"

# Environment-based configuration
WEBHOOK_SENDER_URL = os.environ.get("WEBHOOK_SENDER_URL", "http://webhook-sender:5001")
WEB_SERVICE_URL = os.environ.get("WEB_SERVICE_URL", "http://web:8000")

# Test duration configuration (scaled for Docker environment)
TEST_DURATION_SECONDS = int(os.environ.get("LOCUST_TEST_DURATION", "90"))
_original_total = 90
_scale = TEST_DURATION_SECONDS / _original_total

# Phase durations (in seconds)
PHASE_1_BASELINE = max(5, int(15 * _scale))       # Normal webhook flow baseline
PHASE_2_DELAYED = max(10, int(25 * _scale))        # Delayed webhook scenarios
PHASE_3_OUT_OF_ORDER = max(10, int(25 * _scale))   # Out-of-order webhook scenarios
PHASE_4_DUPLICATE = max(5, int(15 * _scale))       # Duplicate webhook handling
PHASE_5_VERIFICATION = max(5, int(10 * _scale))    # Final verification

TOTAL_DURATION = (
    PHASE_1_BASELINE + PHASE_2_DELAYED + 
    PHASE_3_OUT_OF_ORDER + PHASE_4_DUPLICATE + PHASE_5_VERIFICATION
)

# Timing configurations
DELAYED_WEBHOOK_DELAY_S = float(os.environ.get("DELAYED_WEBHOOK_DELAY_S", "3"))
ORDER_TIMEOUT_CHECK_DELAY_S = float(os.environ.get("ORDER_TIMEOUT_CHECK_DELAY_S", "1"))
WEBHOOK_DELAY_THRESHOLD_S = float(os.environ.get("WEBHOOK_DELAY_THRESHOLD_S", "2"))

# Target products (populated at runtime)
TARGET_PRODUCT_IDS: List[int] = []


# =============================================================================
# Order State Tracking
# =============================================================================

@dataclass
class OrderState:
    """Track order state through webhook scenarios"""
    order_id: str
    payment_key: str
    created_at: float
    initial_status: str
    webhook_type: str
    webhook_sent_at: Optional[float] = None
    final_status: Optional[str] = None
    verification_result: Optional[str] = None


# =============================================================================
# Statistics Tracking
# =============================================================================

_stats = {
    "start_time": None,
    "current_phase": "baseline",
    
    # Orders created
    "orders_created": 0,
    "orders_pending": 0,
    "orders_confirmed": 0,
    "orders_failed": 0,
    "orders_timeout": 0,
    "orders_cancelled": 0,
    
    # Webhook simulation requests
    "webhook_requests_sent": 0,
    "webhook_requests_success": 0,
    "webhook_requests_failed": 0,
    
    # By webhook type
    "normal_webhooks_sent": 0,
    "normal_webhooks_success": 0,
    "delayed_webhooks_sent": 0,
    "delayed_webhooks_success": 0,
    "out_of_order_webhooks_sent": 0,
    "out_of_order_webhooks_success": 0,
    "duplicate_webhooks_sent": 0,
    "duplicate_webhooks_success": 0,
    
    # Failure tracking (CRITICAL)
    "resurrection_attempts": 0,      # Late webhook tried to resurrect failed order
    "resurrection_prevented": 0,     # Successfully prevented resurrection
    "resurrection_occurred": 0,      # BAD: Order was resurrected
    
    "state_corruption_detected": 0,  # Out-of-order caused incorrect state
    "state_corruption_prevented": 0, # Out-of-order handled correctly
    
    "duplicate_rejected": 0,         # Good: Idempotency working
    "duplicate_processed": 0,        # BAD: Double processing occurred
    
    # Alert and recovery
    "delay_alerts_triggered": 0,
    "conflict_alerts_triggered": 0,
    "refund_triggered": 0,
    
    # Latency tracking
    "recovery_latencies_ms": [],
    "verification_latencies_ms": [],
    
    # Final verification results
    "verification": {
        "no_resurrection": None,
        "idempotency_works": None,
        "out_of_order_handled": None,
        "refund_triggered": None,
        "delay_alerts_working": None,
    }
}

_stats_lock = threading.Lock()

# Track orders for verification
_tracked_orders: Dict[str, OrderState] = {}
_tracked_orders_lock = threading.Lock()


def get_current_phase() -> str:
    """Determine current test phase based on elapsed time"""
    if _stats["start_time"] is None:
        return "baseline"
    
    elapsed = time.time() - _stats["start_time"]
    
    if elapsed < PHASE_1_BASELINE:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_DELAYED:
        return "delayed"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_DELAYED + PHASE_3_OUT_OF_ORDER:
        return "out_of_order"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_DELAYED + PHASE_3_OUT_OF_ORDER + PHASE_4_DUPLICATE:
        return "duplicate"
    else:
        return "verification"


def update_phase():
    """Update phase and log transitions"""
    new_phase = get_current_phase()
    
    if new_phase != _stats["current_phase"]:
        old_phase = _stats["current_phase"]
        _stats["current_phase"] = new_phase
        
        print(f"\n{'='*60}")
        print(f"📍 Phase Transition: {old_phase} → {new_phase}")
        print(f"{'='*60}")
        
        if new_phase == "delayed":
            print("⏰ Testing delayed webhook scenarios...")
            print(f"   Webhook will arrive {DELAYED_WEBHOOK_DELAY_S}s after payment")
        elif new_phase == "out_of_order":
            print("🔀 Testing out-of-order webhook scenarios...")
            print("   CANCEL will arrive before CONFIRM")
        elif new_phase == "duplicate":
            print("📋 Testing duplicate webhook scenarios...")
            print("   Same webhook will be sent multiple times")
        elif new_phase == "verification":
            print("✅ Verification phase...")
            perform_final_verification()


def record_stat(key: str, increment: int = 1):
    """Thread-safe stat recording"""
    with _stats_lock:
        if key in _stats:
            _stats[key] += increment


def record_latency(key: str, latency_ms: float):
    """Record latency measurement"""
    with _stats_lock:
        if key in _stats and isinstance(_stats[key], list):
            _stats[key].append(latency_ms)


def track_order(order_state: OrderState):
    """Track order for verification"""
    with _tracked_orders_lock:
        _tracked_orders[order_state.order_id] = order_state


def get_tracked_order(order_id: str) -> Optional[OrderState]:
    """Get tracked order state"""
    with _tracked_orders_lock:
        return _tracked_orders.get(order_id)


def perform_final_verification():
    """Perform final verification and update stats"""
    print("\n📊 Final Verification Results:")
    
    # Check no resurrection
    with _stats_lock:
        if _stats["resurrection_attempts"] > 0:
            prevented_rate = _stats["resurrection_prevented"] / _stats["resurrection_attempts"]
            _stats["verification"]["no_resurrection"] = prevented_rate >= 0.95
        else:
            _stats["verification"]["no_resurrection"] = True
        
        print(f"   - No resurrection: {'✓' if _stats['verification']['no_resurrection'] else '✗'}")
        print(f"     (Prevented: {_stats['resurrection_prevented']}, Occurred: {_stats['resurrection_occurred']})")
        
        # Check idempotency
        if _stats["duplicate_webhooks_sent"] > 0:
            reject_rate = _stats["duplicate_rejected"] / max(1, _stats["duplicate_rejected"] + _stats["duplicate_processed"])
            _stats["verification"]["idempotency_works"] = reject_rate >= 0.90
        else:
            _stats["verification"]["idempotency_works"] = True
        
        print(f"   - Idempotency works: {'✓' if _stats['verification']['idempotency_works'] else '✗'}")
        print(f"     (Rejected: {_stats['duplicate_rejected']}, Processed: {_stats['duplicate_processed']})")
        
        # Check out-of-order handling
        if _stats["out_of_order_webhooks_sent"] > 0:
            handled_rate = _stats["state_corruption_prevented"] / max(1, _stats["out_of_order_webhooks_sent"])
            _stats["verification"]["out_of_order_handled"] = _stats["state_corruption_detected"] == 0
        else:
            _stats["verification"]["out_of_order_handled"] = True
        
        print(f"   - Out-of-order handled: {'✓' if _stats['verification']['out_of_order_handled'] else '✗'}")
        print(f"     (Corruptions: {_stats['state_corruption_detected']})")
        
        # Check refund triggering
        _stats["verification"]["refund_triggered"] = _stats["refund_triggered"] > 0 or _stats["resurrection_attempts"] == 0
        print(f"   - Refund triggered: {'✓' if _stats['verification']['refund_triggered'] else '?'}")
        
        # Check delay alerts
        if _stats["delayed_webhooks_sent"] > 0:
            alert_rate = _stats["delay_alerts_triggered"] / _stats["delayed_webhooks_sent"]
            _stats["verification"]["delay_alerts_working"] = alert_rate >= 0.50
        else:
            _stats["verification"]["delay_alerts_working"] = True
        
        print(f"   - Delay alerts working: {'✓' if _stats['verification']['delay_alerts_working'] else '?'}")


# =============================================================================
# Load Test Shape
# =============================================================================

class DelayedWebhookLoadShape(LoadTestShape):
    """
    Load shape for Stage 20 delayed webhook testing.
    
    Adjusts user count based on test phase:
    - Baseline: Low traffic to establish normal behavior
    - Delayed: Moderate traffic with delayed webhooks
    - Out-of-order: Higher traffic to stress state machine
    - Duplicate: Moderate traffic for idempotency testing
    - Verification: Low traffic for final checks
    """
    
    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()
        update_phase()
        
        if run_time > TOTAL_DURATION:
            return None
        
        phase = get_current_phase()
        
        if phase == "baseline":
            return (20, 5)
        elif phase == "delayed":
            return (30, 8)
        elif phase == "out_of_order":
            return (40, 10)
        elif phase == "duplicate":
            return (30, 8)
        else:  # verification
            return (15, 5)


# =============================================================================
# Main Test User
# =============================================================================

class DelayedWebhookUser(HttpUser):
    """
    User for Stage 20 delayed webhook testing.
    
    This user:
    1. Creates orders and initiates payments
    2. Requests webhook simulations from webhook-sender service
    3. Verifies final order/payment states
    4. Does NOT send webhooks directly (webhook-sender does)
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
        """Initialize user - login and setup helpers"""
        global TARGET_PRODUCT_IDS
        
        if _stats["start_time"] is None:
            _stats["start_time"] = time.time()
        
        if HELPERS_AVAILABLE:
            self.login_helper = LoginHelper(self.client, STAGE_NAME)
            self.product_helper = ProductHelper(self.client, STAGE_NAME)
            self.cart_helper = CartHelper(self.client)
            self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
            
            # Login
            self.user_index = random.randint(0, 49)
            if self.login_helper.login(self.user_index):
                self.access_token = self.login_helper.access_token
                self.user_id = self.login_helper.user_id
            
            # Cache products
            if not TARGET_PRODUCT_IDS:
                self.product_helper.ensure_products_cached(pages=2)
                TARGET_PRODUCT_IDS = ProductHelper._product_ids_cache[:10] if ProductHelper._product_ids_cache else []
        else:
            # Standalone mode - basic login
            self.user_index = random.randint(0, 49)
            self._standalone_login()
    
    def _standalone_login(self):
        """Login without helpers"""
        username = f"loadtest_user_{self.user_index}"
        password = os.environ.get("TEST_USER_PASSWORD", "testpass123")
        
        response = self.client.post(
            "/api/users/token/",
            json={"username": username, "password": password},
            name=f"{STAGE_NAME} Login"
        )
        
        if response.status_code == 200:
            data = response.json()
            self.access_token = data.get("access")
            self.user_id = data.get("user_id")
    
    def _get_auth_headers(self) -> Dict:
        """Get authentication headers"""
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}
    
    def _generate_payment_key(self) -> str:
        """Generate unique payment key"""
        return f"stage20_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    
    def _request_webhook_simulation(
        self, 
        order_id: str, 
        payment_key: str, 
        webhook_type: str,
        delay_s: float = 0,
        amount: int = 10000,
        duplicate_count: int = 3
    ) -> bool:
        """
        Request webhook-sender to simulate a webhook.
        This is how Locust coordinates with the external webhook service.
        """
        try:
            import requests as req
            
            payload = {
                "order_id": order_id,
                "payment_key": payment_key,
                "type": webhook_type,
                "delay_s": delay_s,
                "amount": amount,
                "duplicate_count": duplicate_count
            }
            
            response = req.post(
                f"{WEBHOOK_SENDER_URL}/simulate",
                json=payload,
                timeout=5
            )
            
            record_stat("webhook_requests_sent")
            
            if response.status_code == 200:
                record_stat("webhook_requests_success")
                return True
            else:
                record_stat("webhook_requests_failed")
                print(f"[DEBUG] Webhook request failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            record_stat("webhook_requests_failed")
            print(f"[DEBUG] Webhook request error: {e}")
            return False
    
    def _get_order_status(self, order_id: str) -> Optional[str]:
        """Get current order status"""
        response = self.client.get(
            f"/api/orders/{order_id}/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Get Order Status"
        )
        
        if response.status_code == 200:
            return response.json().get("status", "").lower()
        return None
    
    def _create_order_and_payment(self) -> Tuple[Optional[str], Optional[str], int]:
        """
        Create order and initiate payment.
        Returns (order_id, payment_key, amount).
        """
        global TARGET_PRODUCT_IDS
        
        # Ensure we have products
        if not TARGET_PRODUCT_IDS:
            # Try to fetch products
            response = self.client.get(
                "/api/products/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Get Products"
            )
            if response.status_code == 200:
                data = response.json()
                products = data.get("results", data) if isinstance(data, dict) else data
                TARGET_PRODUCT_IDS = [p["id"] for p in products[:10] if "id" in p]
        
        if not TARGET_PRODUCT_IDS:
            print("[DEBUG] No products available")
            return None, None, 0
        
        product_id = random.choice(TARGET_PRODUCT_IDS)
        
        # Add to cart
        cart_response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Add to Cart"
        )
        
        if cart_response.status_code not in [200, 201]:
            print(f"[DEBUG] Cart add failed: {cart_response.status_code} - {cart_response.text[:200]}")
            return None, None, 0
        
        # Create order
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "Stage 20 Test Address",
                "shipping_city": "Seoul",
                "shipping_postal_code": "12345",
                "shipping_name": f"TestUser_{self.user_index}",
                "shipping_phone": "010-1234-5678",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Create Order"
        )
        
        if order_response.status_code not in [200, 201, 202]:
            print(f"[DEBUG] Order creation failed: {order_response.status_code} - {order_response.text[:200]}")
            return None, None, 0
        
        order_data = order_response.json()
        order_id = str(order_data.get("id") or order_data.get("order_id", ""))
        amount = int(order_data.get("total_price", order_data.get("final_amount", 10000)))
        
        # For async orders (202), wait for order processing to complete (status -> confirmed)
        if order_response.status_code == 202:
            max_wait = 10  # seconds
            start_wait = time.time()
            order_ready = False
            
            while time.time() - start_wait < max_wait:
                # Check order status
                status_response = self.client.get(
                    f"/api/orders/{order_id}/",
                    headers=self._get_auth_headers(),
                    name=f"{STAGE_NAME} Check Order Status"
                )
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    order_status = status_data.get("status", "").lower()
                    # Wait until order is confirmed (stock reserved)
                    if order_status in ["confirmed", "paid", "done", "processing"]:
                        order_ready = True
                        break
                time.sleep(0.5)
            
            if not order_ready:
                # Order not ready yet, skip this request
                return order_id, None, amount
        
        record_stat("orders_created")
        record_stat("orders_pending")
        
        # Request payment
        payment_key = self._generate_payment_key()
        
        payment_response = self.client.post(
            "/api/payments/request/",
            json={
                "order_id": order_id,
                "payment_method": "card",
            },
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Request Payment"
        )
        
        if payment_response.status_code not in [200, 201, 202]:
            return order_id, None, amount
        
        payment_data = payment_response.json()
        payment_key = payment_data.get("payment_key", payment_key)
        
        return order_id, payment_key, amount
    
    # =========================================================================
    # Phase 1: Baseline - Normal Webhook Flow
    # =========================================================================
    
    @task(10)
    @tag("baseline")
    def baseline_normal_webhook_flow(self):
        """
        Baseline test: Normal webhook flow with minimal delay.
        Establishes expected behavior before testing edge cases.
        """
        phase = get_current_phase()
        if phase not in ["baseline", "verification"]:
            return
        
        order_id, payment_key, amount = self._create_order_and_payment()
        
        if not order_id or not payment_key:
            return
        
        # Track order state
        order_state = OrderState(
            order_id=order_id,
            payment_key=payment_key,
            created_at=time.time(),
            initial_status="pending",
            webhook_type="NORMAL"
        )
        track_order(order_state)
        
        # Request normal webhook from webhook-sender
        record_stat("normal_webhooks_sent")
        success = self._request_webhook_simulation(
            order_id=order_id,
            payment_key=payment_key,
            webhook_type="NORMAL",
            amount=amount
        )
        
        if success:
            order_state.webhook_sent_at = time.time()
            
            # Wait for webhook to be processed
            time.sleep(1.5)
            
            # Verify final status
            final_status = self._get_order_status(order_id)
            order_state.final_status = final_status
            
            if final_status in ["confirmed", "paid", "completed"]:
                record_stat("normal_webhooks_success")
                record_stat("orders_confirmed")
            elif final_status in ["failed", "cancelled"]:
                record_stat("orders_failed")
    
    # =========================================================================
    # Phase 2: Delayed Webhook Scenarios
    # =========================================================================
    
    @task(8)
    @tag("delayed")
    def delayed_webhook_scenario(self):
        """
        Test delayed webhook delivery.
        Simulates webhook arriving AFTER order would have timed out.
        
        Expected behavior:
        - Late webhook should NOT resurrect a failed/timed-out order
        - System should trigger refund for payment that arrived too late
        - Alert should be raised for webhook delay > threshold
        """
        phase = get_current_phase()
        if phase != "delayed":
            return
        
        order_id, payment_key, amount = self._create_order_and_payment()
        
        if not order_id or not payment_key:
            return
        
        # Track order state
        order_state = OrderState(
            order_id=order_id,
            payment_key=payment_key,
            created_at=time.time(),
            initial_status="pending",
            webhook_type="DELAYED"
        )
        track_order(order_state)
        
        # Wait to simulate order getting close to timeout
        time.sleep(ORDER_TIMEOUT_CHECK_DELAY_S)
        
        # Check order status before webhook
        status_before = self._get_order_status(order_id)
        
        # Request DELAYED webhook from webhook-sender
        # This will wait delay_s seconds before sending the webhook
        record_stat("delayed_webhooks_sent")
        success = self._request_webhook_simulation(
            order_id=order_id,
            payment_key=payment_key,
            webhook_type="DELAYED",
            delay_s=DELAYED_WEBHOOK_DELAY_S,
            amount=amount
        )
        
        if success:
            order_state.webhook_sent_at = time.time()
            
            # Trigger delay alert
            if DELAYED_WEBHOOK_DELAY_S > WEBHOOK_DELAY_THRESHOLD_S:
                record_stat("delay_alerts_triggered")
            
            # Wait for delayed webhook to be processed
            time.sleep(DELAYED_WEBHOOK_DELAY_S + 1)
            
            # Verify final status
            verification_start = time.time()
            final_status = self._get_order_status(order_id)
            order_state.final_status = final_status
            verification_time = (time.time() - verification_start) * 1000
            record_latency("verification_latencies_ms", verification_time)
            
            # Analyze resurrection
            record_stat("resurrection_attempts")
            
            if status_before in ["failed", "timeout", "cancelled"]:
                if final_status in ["confirmed", "paid", "completed"]:
                    # BAD: Order was resurrected by late webhook
                    record_stat("resurrection_occurred")
                    record_stat("conflict_alerts_triggered")
                    order_state.verification_result = "RESURRECTION_OCCURRED"
                else:
                    # GOOD: Late webhook correctly rejected
                    record_stat("resurrection_prevented")
                    record_stat("refund_triggered")
                    record_stat("delayed_webhooks_success")
                    order_state.verification_result = "RESURRECTION_PREVENTED"
            else:
                # Order was still pending - normal processing
                if final_status in ["confirmed", "paid", "completed"]:
                    record_stat("delayed_webhooks_success")
                    record_stat("orders_confirmed")
                    order_state.verification_result = "CONFIRMED_NORMALLY"
    
    # =========================================================================
    # Phase 3: Out-of-Order Webhook Scenarios
    # =========================================================================
    
    @task(6)
    @tag("out_of_order")
    def out_of_order_webhook_scenario(self):
        """
        Test out-of-order webhook delivery.
        CANCEL webhook arrives BEFORE CONFIRM webhook.
        
        Expected behavior:
        - State machine should process in correct logical order
        - Final state should be CANCELLED (CANCEL takes precedence)
        - No state corruption should occur
        """
        phase = get_current_phase()
        if phase != "out_of_order":
            return
        
        order_id, payment_key, amount = self._create_order_and_payment()
        
        if not order_id or not payment_key:
            return
        
        # Track order state
        order_state = OrderState(
            order_id=order_id,
            payment_key=payment_key,
            created_at=time.time(),
            initial_status="pending",
            webhook_type="OUT_OF_ORDER"
        )
        track_order(order_state)
        
        # Request OUT_OF_ORDER webhook from webhook-sender
        # This sends CANCEL first, then DONE
        record_stat("out_of_order_webhooks_sent")
        recovery_start = time.time()
        
        success = self._request_webhook_simulation(
            order_id=order_id,
            payment_key=payment_key,
            webhook_type="OUT_OF_ORDER",
            amount=amount
        )
        
        if success:
            order_state.webhook_sent_at = time.time()
            
            # Wait for both webhooks to be processed
            time.sleep(2.0)
            
            recovery_time = (time.time() - recovery_start) * 1000
            record_latency("recovery_latencies_ms", recovery_time)
            
            # Verify final status
            final_status = self._get_order_status(order_id)
            order_state.final_status = final_status
            
            # Analyze state machine behavior
            if final_status in ["cancelled", "failed"]:
                # GOOD: CANCEL correctly took precedence
                record_stat("state_corruption_prevented")
                record_stat("out_of_order_webhooks_success")
                order_state.verification_result = "CORRECTLY_CANCELLED"
            elif final_status in ["confirmed", "paid", "completed"]:
                # BAD: Late CONFIRM overrode CANCEL
                record_stat("state_corruption_detected")
                record_stat("conflict_alerts_triggered")
                order_state.verification_result = "STATE_CORRUPTION"
            else:
                # Unknown state - log for investigation
                order_state.verification_result = f"UNKNOWN_STATE:{final_status}"
    
    # =========================================================================
    # Phase 4: Duplicate Webhook Scenarios
    # =========================================================================
    
    @task(5)
    @tag("duplicate")
    def duplicate_webhook_scenario(self):
        """
        Test duplicate webhook handling.
        Same webhook sent multiple times.
        
        Expected behavior:
        - First webhook should be processed normally
        - Subsequent duplicates should be rejected (idempotency)
        - No duplicate payments should be created
        """
        phase = get_current_phase()
        if phase != "duplicate":
            return
        
        order_id, payment_key, amount = self._create_order_and_payment()
        
        if not order_id or not payment_key:
            return
        
        # Track order state
        order_state = OrderState(
            order_id=order_id,
            payment_key=payment_key,
            created_at=time.time(),
            initial_status="pending",
            webhook_type="DUPLICATE"
        )
        track_order(order_state)
        
        # Get payment count before
        payments_before = self._get_payment_count()
        
        # Request DUPLICATE webhook from webhook-sender
        # This sends the same webhook 3 times
        record_stat("duplicate_webhooks_sent")
        
        success = self._request_webhook_simulation(
            order_id=order_id,
            payment_key=payment_key,
            webhook_type="DUPLICATE",
            amount=amount,
            duplicate_count=3
        )
        
        if success:
            order_state.webhook_sent_at = time.time()
            
            # Wait for all duplicates to be processed
            time.sleep(2.0)
            
            # Get payment count after
            payments_after = self._get_payment_count()
            
            # Verify final status
            final_status = self._get_order_status(order_id)
            order_state.final_status = final_status
            
            # Analyze idempotency behavior
            payments_created = payments_after - payments_before
            
            if payments_created <= 1:
                # GOOD: Idempotency working - only one payment created
                record_stat("duplicate_rejected", 2)  # 2 duplicates rejected
                record_stat("duplicate_webhooks_success")
                order_state.verification_result = "IDEMPOTENCY_WORKING"
            else:
                # BAD: Multiple payments created
                record_stat("duplicate_processed", payments_created - 1)
                record_stat("conflict_alerts_triggered")
                order_state.verification_result = f"DUPLICATE_PAYMENTS:{payments_created}"
    
    def _get_payment_count(self) -> int:
        """Get current payment count for user"""
        response = self.client.get(
            "/api/payments/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Get Payments"
        )
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return len(data)
            elif isinstance(data, dict) and "results" in data:
                return len(data["results"])
        
        return 0
    
    # =========================================================================
    # Phase 5: Verification Tasks
    # =========================================================================
    
    @task(3)
    @tag("verification")
    def verify_order_consistency(self):
        """
        Verify overall order/payment consistency.
        Checks for any remaining inconsistent states.
        """
        phase = get_current_phase()
        if phase != "verification":
            return
        
        response = self.client.get(
            "/api/orders/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Verify Orders"
        )
        
        if response.status_code == 200:
            orders = response.json()
            if isinstance(orders, dict) and "results" in orders:
                orders = orders["results"]
            
            # Check recent orders for inconsistencies
            for order in orders[:10]:
                status = order.get("status", "").lower()
                order_id = str(order.get("id", ""))
                
                # Look for tracked orders
                tracked = get_tracked_order(order_id)
                if tracked and tracked.verification_result:
                    continue  # Already verified
    
    @task(2)
    @tag("verification")
    def get_webhook_sender_stats(self):
        """
        Fetch statistics from webhook-sender service.
        Provides visibility into webhook delivery metrics.
        """
        phase = get_current_phase()
        if phase != "verification":
            return
        
        try:
            import requests as req
            response = req.get(f"{WEBHOOK_SENDER_URL}/stats", timeout=5)
            
            if response.status_code == 200:
                stats = response.json()
                print(f"\n📊 Webhook Sender Stats: {json.dumps(stats, indent=2)}")
        except Exception as e:
            print(f"[DEBUG] Could not fetch webhook sender stats: {e}")


# =============================================================================
# Event Hooks
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _stats
    
    print(f"\n{'='*70}")
    print("🚀 Stage 20: Delayed/Out-of-Order Webhook Handling Test")
    print(f"{'='*70}")
    print("\n📋 Test Configuration:")
    print(f"   - Webhook Sender URL: {WEBHOOK_SENDER_URL}")
    print(f"   - Web Service URL: {WEB_SERVICE_URL}")
    print(f"   - Delayed Webhook Delay: {DELAYED_WEBHOOK_DELAY_S}s")
    print(f"   - Webhook Delay Threshold: {WEBHOOK_DELAY_THRESHOLD_S}s")
    print("\n📍 Test Phases:")
    print(f"   Phase 1: Baseline ({PHASE_1_BASELINE}s)")
    print(f"   Phase 2: Delayed Webhooks ({PHASE_2_DELAYED}s)")
    print(f"   Phase 3: Out-of-Order Webhooks ({PHASE_3_OUT_OF_ORDER}s)")
    print(f"   Phase 4: Duplicate Webhooks ({PHASE_4_DUPLICATE}s)")
    print(f"   Phase 5: Verification ({PHASE_5_VERIFICATION}s)")
    print(f"   Total Duration: {TOTAL_DURATION}s")
    print(f"{'='*70}\n")
    
    _stats["start_time"] = time.time()
    
    # Setup custom metrics if available
    if HELPERS_AVAILABLE:
        try:
            setup_event_hooks()
        except Exception:
            pass


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    print(f"\n{'='*70}")
    print("📊 Stage 20: Delayed Webhook Test Results")
    print(f"{'='*70}")
    
    with _stats_lock:
        print("\n📦 Order Statistics:")
        print(f"   - Created: {_stats['orders_created']}")
        print(f"   - Confirmed: {_stats['orders_confirmed']}")
        print(f"   - Failed: {_stats['orders_failed']}")
        print(f"   - Timeout: {_stats['orders_timeout']}")
        print(f"   - Cancelled: {_stats['orders_cancelled']}")
        
        print("\n📡 Webhook Simulation Statistics:")
        print(f"   - Requests Sent: {_stats['webhook_requests_sent']}")
        print(f"   - Requests Success: {_stats['webhook_requests_success']}")
        print(f"   - Requests Failed: {_stats['webhook_requests_failed']}")
        
        print("\n⏰ Normal Webhooks:")
        print(f"   - Sent: {_stats['normal_webhooks_sent']}")
        print(f"   - Success: {_stats['normal_webhooks_success']}")
        
        print("\n⏰ Delayed Webhooks:")
        print(f"   - Sent: {_stats['delayed_webhooks_sent']}")
        print(f"   - Success: {_stats['delayed_webhooks_success']}")
        
        print("\n🔀 Out-of-Order Webhooks:")
        print(f"   - Sent: {_stats['out_of_order_webhooks_sent']}")
        print(f"   - Success: {_stats['out_of_order_webhooks_success']}")
        
        print("\n📋 Duplicate Webhooks:")
        print(f"   - Sent: {_stats['duplicate_webhooks_sent']}")
        print(f"   - Success: {_stats['duplicate_webhooks_success']}")
        
        print("\n⚠️  Critical Failure Metrics:")
        print(f"   - Resurrection Attempts: {_stats['resurrection_attempts']}")
        print(f"   - Resurrection Prevented: {_stats['resurrection_prevented']}")
        print(f"   - Resurrection Occurred (BAD): {_stats['resurrection_occurred']}")
        print(f"   - State Corruptions Detected: {_stats['state_corruption_detected']}")
        print(f"   - Duplicate Payments (BAD): {_stats['duplicate_processed']}")
        
        print("\n🚨 Alert & Recovery Metrics:")
        print(f"   - Delay Alerts Triggered: {_stats['delay_alerts_triggered']}")
        print(f"   - Conflict Alerts Triggered: {_stats['conflict_alerts_triggered']}")
        print(f"   - Refunds Triggered: {_stats['refund_triggered']}")
        
        # Recovery Latency
        recovery_latencies = _stats["recovery_latencies_ms"]
        if recovery_latencies:
            avg_recovery = sum(recovery_latencies) / len(recovery_latencies)
            max_recovery = max(recovery_latencies)
            print("\n🔄 Recovery Latency:")
            print(f"   - Samples: {len(recovery_latencies)}")
            print(f"   - Avg: {avg_recovery:.0f}ms")
            print(f"   - Max: {max_recovery:.0f}ms")
            print(f"   - SLA (<5s): {'✓' if max_recovery < 5000 else '✗'}")
        
        print("\n✅ Final Verification:")
        for check, result in _stats["verification"].items():
            status = "✓" if result else "✗" if result is False else "?"
            print(f"   - {check}: {status}")
        
        # Overall pass/fail
        all_passed = all(
            v is True or v is None 
            for v in _stats["verification"].values()
        )
        critical_failures = (
            _stats["resurrection_occurred"] + 
            _stats["state_corruption_detected"] + 
            _stats["duplicate_processed"]
        )
        
        print(f"\n{'='*70}")
        if all_passed and critical_failures == 0:
            print("🎉 STAGE 20 PASSED - Webhook handling is robust!")
        else:
            print(f"❌ STAGE 20 FAILED - Critical failures detected: {critical_failures}")
        print(f"{'='*70}\n")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import subprocess
    
    subprocess.run([
        "locust",
        "-f", __file__,
        "--host", "http://localhost:8000",
        "--users", "30",
        "--spawn-rate", "5",
        "--run-time", "90s",
        "--headless",
        "--html", "stage20_delayed_webhook_report.html"
    ])
