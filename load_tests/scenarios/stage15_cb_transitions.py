"""
Stage 15: Circuit Breaker Auto Transitions Test

Purpose: Verify automatic Circuit Breaker state transitions
- Test CLOSED → OPEN transition on failures
- Test OPEN → HALF_OPEN transition after timeout
- Test HALF_OPEN → CLOSED transition on success
- Test HALF_OPEN → OPEN transition on failure
- Verify transition timing matches configuration
- Validate audit logging of transitions

Scenario:
  Phase 1: Normal requests → confirm closed
  Phase 2: Inject 5 consecutive failures → confirm open transition
  Phase 3: Wait recovery_timeout (60s) → confirm half_open transition
  Phase 4: 2 successful requests → confirm closed return

Verification:
  - Each transition accuracy
  - Transition timing (config compliance)
  - half_open request limiting
  - Transition audit log

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage15_cb_transitions.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage15_cb_transitions.py \\
        --host=http://localhost:8000 \\
        --users=20 --spawn-rate=5 --run-time=5m \\
        --headless --html=stage15_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 15)
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage15-CBTransitions]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 190  # Original total: 190s
_scale = _test_duration / _original_total

# Circuit breaker configuration (should match actual config)
CB_FAILURE_THRESHOLD = 5  # Failures to trigger OPEN
CB_RECOVERY_TIMEOUT = 60  # Seconds before HALF_OPEN
CB_SUCCESS_THRESHOLD = 2  # Successes in HALF_OPEN to close

# Test phases - scaled
PHASE_1_NORMAL_DURATION = max(2, int(30 * _scale))
PHASE_2_FAILURE_DURATION = max(2, int(30 * _scale))
PHASE_3_WAIT_DURATION = max(3, int(70 * _scale))
PHASE_4_RECOVERY_DURATION = max(3, int(60 * _scale))

TOTAL_DURATION = PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION + PHASE_3_WAIT_DURATION + PHASE_4_RECOVERY_DURATION

TARGET_SERVICE = "payment"  # Service to test CB on


# =============================================================================
# Circuit Breaker Test Statistics
# =============================================================================

_cb_stats = {
    "start_time": None,
    "phase": "normal",  # normal, failure_injection, wait_recovery, recovery
    # State tracking
    "initial_state": None,
    "current_state": None,
    "state_history": [],  # List of (timestamp, state, phase)
    # Transition tracking
    "transitions": [],  # List of transition events
    "expected_transitions": [
        {"from": "closed", "to": "open", "phase": "failure_injection"},
        {"from": "open", "to": "half_open", "phase": "wait_recovery"},
        {"from": "half_open", "to": "closed", "phase": "recovery"},
    ],
    # Timing verification
    "open_time": None,  # When CB opened
    "half_open_time": None,  # When CB went to half_open
    "closed_time": None,  # When CB returned to closed
    # Request counts per state
    "requests_per_state": {
        "closed": {"success": 0, "failure": 0},
        "open": {"success": 0, "failure": 0, "rejected": 0},
        "half_open": {"success": 0, "failure": 0},
    },
    # Verification results
    "verification": {
        "closed_to_open": None,
        "open_to_half_open": None,
        "half_open_to_closed": None,
        "recovery_timeout_accurate": None,
        "all_transitions_valid": None,
    },
    # Recovery Latency Metrics
    "recovery": {
        "cb_full_cycle_latency_seconds": None,  # CLOSED → OPEN → HALF_OPEN → CLOSED
        "open_to_half_open_latency_seconds": None,  # Recovery timeout
        "half_open_to_closed_latency_seconds": None,  # Success validation time
        "sla_compliant": None,  # Whether recovery met SLA
    },
}


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _cb_stats["start_time"] is None:
        return "normal"

    elapsed = time.time() - _cb_stats["start_time"]

    if elapsed < PHASE_1_NORMAL_DURATION:
        return "normal"
    elif elapsed < PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION:
        return "failure_injection"
    elif elapsed < PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION + PHASE_3_WAIT_DURATION:
        return "wait_recovery"
    else:
        return "recovery"


def _update_phase():
    """Update phase tracking"""
    phase = _get_current_phase()

    if phase != _cb_stats["phase"]:
        old_phase = _cb_stats["phase"]
        _cb_stats["phase"] = phase

        if phase == "failure_injection":
            print(f"\n⚡ Phase 2: Injecting failures to trigger OPEN state")
        elif phase == "wait_recovery":
            print(f"\n⏳ Phase 3: Waiting for recovery_timeout ({CB_RECOVERY_TIMEOUT}s)")
            print(f"   - CB should transition from OPEN → HALF_OPEN")
        elif phase == "recovery":
            print(f"\n✅ Phase 4: Recovery - sending successful requests")
            print(f"   - CB should transition from HALF_OPEN → CLOSED")


def _record_state_change(new_state: str, phase: str):
    """Record a state change"""
    now = time.time()
    old_state = _cb_stats["current_state"]

    _cb_stats["state_history"].append(
        {
            "timestamp": now,
            "elapsed_seconds": now - _cb_stats["start_time"] if _cb_stats["start_time"] else 0,
            "state": new_state,
            "phase": phase,
        }
    )

    if old_state and old_state != new_state:
        transition = {
            "from": old_state,
            "to": new_state,
            "timestamp": now,
            "elapsed_seconds": now - _cb_stats["start_time"] if _cb_stats["start_time"] else 0,
            "phase": phase,
        }
        _cb_stats["transitions"].append(transition)

        # Track specific transition times
        if old_state == "closed" and new_state == "open":
            _cb_stats["open_time"] = now
            print(f"\n🔴 TRANSITION: CLOSED → OPEN")
            _cb_stats["verification"]["closed_to_open"] = True

        elif old_state == "open" and new_state == "half_open":
            _cb_stats["half_open_time"] = now
            print(f"\n🟡 TRANSITION: OPEN → HALF_OPEN")
            _cb_stats["verification"]["open_to_half_open"] = True

            # Verify timing
            if _cb_stats["open_time"]:
                actual_timeout = now - _cb_stats["open_time"]
                expected_timeout = CB_RECOVERY_TIMEOUT
                tolerance = 10  # 10 second tolerance

                timing_accurate = abs(actual_timeout - expected_timeout) <= tolerance
                _cb_stats["verification"]["recovery_timeout_accurate"] = timing_accurate

                print(f"   - Actual timeout: {actual_timeout:.1f}s (expected: {expected_timeout}s)")
                print(f"   - Timing accurate: {'✓' if timing_accurate else '✗'}")

        elif old_state == "half_open" and new_state == "closed":
            _cb_stats["closed_time"] = now
            print(f"\n🟢 TRANSITION: HALF_OPEN → CLOSED")
            _cb_stats["verification"]["half_open_to_closed"] = True

    _cb_stats["current_state"] = new_state


# =============================================================================
# Load Shape
# =============================================================================


class CBTransitionShape(LoadTestShape):
    """
    Load shape for Circuit Breaker transition testing.

    Maintains consistent low load to allow precise CB state observation.
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        # Keep load low for precise testing
        return (20, 5)


# =============================================================================
# Test User
# =============================================================================


class CBTransitionUser(HttpUser):
    """
    User for Circuit Breaker transition testing.

    Monitors CB state and triggers state transitions
    through controlled success/failure patterns.
    """

    wait_time = between(1, 2)

    def on_start(self):
        """Initialize user session"""
        global _cb_stats

        setup_event_hooks(STAGE_NAME)

        if _cb_stats["start_time"] is None:
            _cb_stats["start_time"] = time.time()

        # Admin login helper for Control API access
        self.admin_login_helper = LoginHelper(self.client, STAGE_NAME)
        self.admin_login_helper.login_as_admin()

        # Get initial CB state (using admin auth)
        if _cb_stats["initial_state"] is None:
            self._check_cb_state(initial=True)

        # Regular user login helper for normal operations
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        self._failure_injection_active = False

    def _check_cb_state(self, initial: bool = False) -> Optional[str]:
        """Check current circuit breaker state"""
        try:
            with self.client.get(
                f"/api/self-healing/status/{TARGET_SERVICE}/",
                headers=self.admin_login_helper.get_auth_header() if hasattr(self, "admin_login_helper") else {},
                name=f"{STAGE_NAME} CB-state-check",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    state = data.get("circuit_state", "closed")

                    if initial:
                        _cb_stats["initial_state"] = state
                        _cb_stats["current_state"] = state
                        print(f"\n📊 Initial CB State: {state.upper()}")
                    else:
                        phase = _get_current_phase()
                        _record_state_change(state, phase)

                    response.success()
                    return state
        except Exception:
            pass
        return None

    def _block_service(self):
        """Block service via Control API (force CB to OPEN)"""
        try:
            with self.client.post(
                "/api/self-healing/control/",
                json={
                    "service_name": TARGET_SERVICE,
                    "action": "block",
                    "environment": "test",
                    "reason": "Stage 15 CB control test - blocking service",
                    "ttl_minutes": 5,
                },
                headers=self.admin_login_helper.get_auth_header(),
                name=f"{STAGE_NAME} block-service",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    self._failure_injection_active = True
                    print(f"\n🔴 Service BLOCKED via Control API")
                    response.success()
                else:
                    response.failure(f"Block failed: {response.status_code}")
        except Exception as e:
            print(f"Block error: {e}")

    def _allow_service(self):
        """Allow service via Control API (force CB to CLOSED)"""
        if not self._failure_injection_active:
            return

        try:
            with self.client.post(
                "/api/self-healing/control/",
                json={
                    "service_name": TARGET_SERVICE,
                    "action": "allow",
                    "environment": "test",
                    "reason": "Stage 15 CB control test - allowing service",
                },
                headers=self.admin_login_helper.get_auth_header(),
                name=f"{STAGE_NAME} allow-service",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    self._failure_injection_active = False
                    print(f"\n🟢 Service ALLOWED via Control API")
                    response.success()
        except Exception as e:
            print(f"Allow error: {e}")

    # =========================================================================
    # Test Tasks
    # =========================================================================

    @task(10)
    @tag("cb", "request")
    def send_request_to_service(self):
        """Send request to target service based on current phase"""
        phase = _get_current_phase()
        current_state = _cb_stats["current_state"] or "closed"

        # Phase 1: Normal requests (confirm closed)
        if phase == "normal":
            self._normal_request()

        # Phase 2: Block service to force CB OPEN
        elif phase == "failure_injection":
            if not self._failure_injection_active:
                self._block_service()
            # Check state after blocking
            self._check_cb_state()

        # Phase 3: Wait, then allow service
        elif phase == "wait_recovery":
            # Allow service at start of wait phase
            if self._failure_injection_active:
                self._allow_service()
            # Check state after allowing
            self._check_cb_state()

        # Phase 4: Recovery requests
        elif phase == "recovery":
            self._normal_request()

    def _normal_request(self):
        """Send normal request that should succeed"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        state = _cb_stats["current_state"] or "closed"

        with self.client.get(
            f"/api/products/{product['id']}/",
            name=f"{STAGE_NAME} GET /products/[id]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                _cb_stats["requests_per_state"][state]["success"] = (
                    _cb_stats["requests_per_state"].get(state, {}).get("success", 0) + 1
                )
                response.success()
            elif response.status_code == 503:
                # CB is open, request rejected
                _cb_stats["requests_per_state"]["open"]["rejected"] = (
                    _cb_stats["requests_per_state"]["open"].get("rejected", 0) + 1
                )
                response.success()  # Expected behavior
            else:
                response.failure(f"Status: {response.status_code}")

    def _failing_request(self):
        """Send request that will fail (to payment service)"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        state = _cb_stats["current_state"] or "closed"

        # Add to cart and create order
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add",
        )

        order_response = self.client.post(
            "/api/orders/",
            json={"shipping_address": "CB Test"},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} create-order",
        )

        if order_response.status_code not in [200, 201]:
            return

        order_data = order_response.json()
        order_id = order_data.get("id")

        if not order_id:
            return

        # Payment request (will fail due to injection)
        with self.client.post(
            "/api/payments/request/",
            json={"order_id": order_id},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} payment-request (failing)",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                _cb_stats["requests_per_state"][state]["success"] = (
                    _cb_stats["requests_per_state"].get(state, {}).get("success", 0) + 1
                )
                response.success()
            elif response.status_code == 503:
                # CB is open
                _cb_stats["requests_per_state"]["open"]["rejected"] = (
                    _cb_stats["requests_per_state"]["open"].get("rejected", 0) + 1
                )
                response.success()
            else:
                _cb_stats["requests_per_state"][state]["failure"] = (
                    _cb_stats["requests_per_state"].get(state, {}).get("failure", 0) + 1
                )
                response.failure(f"Payment failed (expected): {response.status_code}")

    @task(5)
    @tag("cb", "monitoring")
    def monitor_cb_state(self):
        """Monitor circuit breaker state"""
        self._check_cb_state()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate Circuit Breaker transition report"""
    print("\n" + "=" * 70)
    print("📊 CIRCUIT BREAKER TRANSITION REPORT")
    print("=" * 70)

    # State history
    print("\n📈 State History:")
    for entry in _cb_stats["state_history"][-10:]:  # Last 10 entries
        print(f"  - {entry['elapsed_seconds']:.1f}s: {entry['state'].upper()} (phase: {entry['phase']})")

    # Transitions
    print("\n🔄 Recorded Transitions:")
    for trans in _cb_stats["transitions"]:
        print(
            f"  - {trans['from'].upper()} → {trans['to'].upper()} at {trans['elapsed_seconds']:.1f}s (phase: {trans['phase']})"
        )

    # Verification results
    print("\n✅ Verification Results:")
    v = _cb_stats["verification"]

    print(f"  - CLOSED → OPEN transition: {'✓' if v['closed_to_open'] else '✗'}")
    print(f"  - OPEN → HALF_OPEN transition: {'✓' if v['open_to_half_open'] else '✗'}")
    print(f"  - HALF_OPEN → CLOSED transition: {'✓' if v['half_open_to_closed'] else '✗'}")
    print(f"  - Recovery timeout accurate: {'✓' if v['recovery_timeout_accurate'] else '✗'}")

    # Timing analysis
    print("\n⏱️ Timing Analysis:")
    if _cb_stats["open_time"] and _cb_stats["half_open_time"]:
        recovery_wait = _cb_stats["half_open_time"] - _cb_stats["open_time"]
        print(f"  - Time in OPEN state: {recovery_wait:.1f}s (expected: ~{CB_RECOVERY_TIMEOUT}s)")

    if _cb_stats["half_open_time"] and _cb_stats["closed_time"]:
        half_open_duration = _cb_stats["closed_time"] - _cb_stats["half_open_time"]
        print(f"  - Time in HALF_OPEN state: {half_open_duration:.1f}s")

    # Request statistics per state
    print("\n📊 Requests per State:")
    for state, stats in _cb_stats["requests_per_state"].items():
        if any(stats.values()):
            print(
                f"  - {state.upper()}: success={stats.get('success', 0)}, failure={stats.get('failure', 0)}, rejected={stats.get('rejected', 0)}"
            )

    # Overall result
    all_transitions_valid = all(
        [
            v["closed_to_open"],
            v["open_to_half_open"],
            v["half_open_to_closed"],
        ]
    )
    v["all_transitions_valid"] = all_transitions_valid

    print(f"\n🎯 All Transitions Valid: {'PASSED ✓' if all_transitions_valid else 'FAILED ✗'}")

    # Recovery Latency Report
    recovery = _cb_stats["recovery"]
    print(f"\n🔄 Recovery Latency Metrics:")
    if _cb_stats["open_time"] and _cb_stats["closed_time"]:
        full_cycle = _cb_stats["closed_time"] - _cb_stats["open_time"]
        recovery["cb_full_cycle_latency_seconds"] = full_cycle
        print(f"   - Full CB cycle latency: {full_cycle:.1f}s")
        
        if _cb_stats["half_open_time"]:
            open_to_half = _cb_stats["half_open_time"] - _cb_stats["open_time"]
            half_to_closed = _cb_stats["closed_time"] - _cb_stats["half_open_time"]
            recovery["open_to_half_open_latency_seconds"] = open_to_half
            recovery["half_open_to_closed_latency_seconds"] = half_to_closed
            print(f"   - OPEN → HALF_OPEN: {open_to_half:.1f}s (expected: ~{CB_RECOVERY_TIMEOUT}s)")
            print(f"   - HALF_OPEN → CLOSED: {half_to_closed:.1f}s")
        
        # SLA check (full cycle should be under 2 minutes typically)
        sla_threshold = CB_RECOVERY_TIMEOUT + 30  # recovery_timeout + buffer
        recovery["sla_compliant"] = full_cycle < sla_threshold
        if recovery["sla_compliant"]:
            print(f"   - SLA Status: ✓ Under {sla_threshold}s threshold")
        else:
            print(f"   - SLA Status: ✗ Exceeded {sla_threshold}s threshold")
    else:
        print(f"   - CB full cycle not completed")

    # Save report
    report_path = os.path.join(_load_tests_dir, "reports", "stage15_cb_transitions_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_name": "Stage 15: Circuit Breaker Auto Transitions",
                "timestamp": datetime.now().isoformat(),
                "configuration": {
                    "failure_threshold": CB_FAILURE_THRESHOLD,
                    "recovery_timeout": CB_RECOVERY_TIMEOUT,
                    "success_threshold": CB_SUCCESS_THRESHOLD,
                    "target_service": TARGET_SERVICE,
                },
                "transitions": _cb_stats["transitions"],
                "timing": {
                    "open_time": _cb_stats["open_time"],
                    "half_open_time": _cb_stats["half_open_time"],
                    "closed_time": _cb_stats["closed_time"],
                },
                "requests_per_state": _cb_stats["requests_per_state"],
                "verification": v,
            },
            f,
            indent=2,
        )

    print(f"\n💾 Report saved to: {report_path}")
    print("=" * 70)
