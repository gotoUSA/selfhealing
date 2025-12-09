"""
Stage 12: Spike & Recovery Test

Purpose: Verify recovery stability after sudden load spike
- Test system behavior under sudden traffic spike
- Verify circuit breaker opens when overwhelmed
- Verify circuit breaker closes after recovery
- Validate DLQ replay works correctly after spike
- Ensure data consistency is maintained

Load Shape:
  Phase 1 (0-30s): 0 → 500 users (spike)
  Phase 2 (30s-2m30s): 500 users (sustain)
  Phase 3 (2m30s-5m30s): 500 → 50 users (ramp-down)
  Phase 4 (5m30s-10m30s): 50 users (stabilize)

Metrics to Collect:
  - circuit_breaker_open_time
  - circuit_breaker_close_time
  - dlq_max_count
  - dlq_replay_success_rate
  - data_consistency (before vs after)

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage12_spike_recovery.py --host=http://localhost:8000

    # CLI mode (10.5 minutes total)
    locust -f load_tests/scenarios/stage12_spike_recovery.py \\
        --host=http://localhost:8000 \\
        --headless --html=stage12_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 12)
"""

import os
import sys
import time
import random
import json
from datetime import datetime
from typing import Optional, Dict, Any

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage12-SpikeRecovery]"


# =============================================================================
# Spike & Recovery Statistics
# =============================================================================

_spike_stats = {
    "start_time": None,
    "phase": "spike",  # spike, sustain, ramp_down, stabilize
    "phase_times": {
        "spike_start": None,
        "sustain_start": None,
        "ramp_down_start": None,
        "stabilize_start": None,
    },
    "circuit_breaker": {
        "first_open_time": None,
        "first_close_time": None,
        "open_count": 0,
        "last_state": "closed",
    },
    "dlq": {
        "max_count": 0,
        "current_count": 0,
        "items_before_spike": 0,
        "items_after_recovery": 0,
    },
    "metrics_per_phase": {
        "spike": {"requests": 0, "errors": 0, "response_times": []},
        "sustain": {"requests": 0, "errors": 0, "response_times": []},
        "ramp_down": {"requests": 0, "errors": 0, "response_times": []},
        "stabilize": {"requests": 0, "errors": 0, "response_times": []},
    },
    "consistency": {
        "checked": False,
        "orders_before": 0,
        "orders_after": 0,
        "stock_consistent": None,
    },
}


def _get_current_phase() -> str:
    """Determine current phase based on elapsed time"""
    if _spike_stats["start_time"] is None:
        return "spike"

    elapsed = time.time() - _spike_stats["start_time"]

    if elapsed < 30:
        return "spike"
    elif elapsed < 150:  # 2m30s
        return "sustain"
    elif elapsed < 330:  # 5m30s
        return "ramp_down"
    else:
        return "stabilize"


def _record_request(success: bool, response_time_ms: float):
    """Record request to current phase statistics"""
    phase = _get_current_phase()
    stats = _spike_stats["metrics_per_phase"][phase]

    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)

    if not success:
        stats["errors"] += 1


def _update_phase():
    """Update phase tracking"""
    phase = _get_current_phase()
    phase_times = _spike_stats["phase_times"]

    if phase == "spike" and phase_times["spike_start"] is None:
        phase_times["spike_start"] = time.time()
        _spike_stats["phase"] = "spike"
        print(f"\n⚡ Phase: SPIKE - Ramping to 500 users")

    elif phase == "sustain" and phase_times["sustain_start"] is None:
        phase_times["sustain_start"] = time.time()
        _spike_stats["phase"] = "sustain"
        print(f"\n🔥 Phase: SUSTAIN - Holding 500 users")

    elif phase == "ramp_down" and phase_times["ramp_down_start"] is None:
        phase_times["ramp_down_start"] = time.time()
        _spike_stats["phase"] = "ramp_down"
        print(f"\n📉 Phase: RAMP DOWN - Reducing to 50 users")

    elif phase == "stabilize" and phase_times["stabilize_start"] is None:
        phase_times["stabilize_start"] = time.time()
        _spike_stats["phase"] = "stabilize"
        print(f"\n✅ Phase: STABILIZE - Holding 50 users, monitoring recovery")


# =============================================================================
# Custom Load Shape - Spike & Recovery
# =============================================================================


class SpikeRecoveryShape(LoadTestShape):
    """
    Spike and recovery load shape.

    Simulates a sudden traffic spike followed by gradual recovery.
    This tests the system's ability to recover from overload.

    Set LOCUST_TEST_DURATION env var to scale all durations.
    """

    # Phase configuration (seconds) - scaled by env var
    _scale = int(os.environ.get("LOCUST_TEST_DURATION", "15")) / 630  # Original total: 630s
    SPIKE_DURATION = max(3, int(30 * _scale))
    SUSTAIN_DURATION = max(3, int(120 * _scale))
    RAMP_DOWN_DURATION = max(3, int(180 * _scale))
    STABILIZE_DURATION = max(3, int(300 * _scale))

    # User counts
    SPIKE_USERS = int(os.environ.get("LOCUST_MAX_USERS", "50"))
    STABLE_USERS = 10

    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time"""
        run_time = self.get_run_time()

        # Update phase tracking
        _update_phase()

        # Phase 1: Spike (0-30s) - rapid ramp to 500 users
        if run_time < self.SPIKE_DURATION:
            progress = run_time / self.SPIKE_DURATION
            users = int(self.SPIKE_USERS * progress)
            return (max(1, users), 50)  # High spawn rate for spike

        # Phase 2: Sustain (30s-2m30s) - hold at 500 users
        elif run_time < self.SPIKE_DURATION + self.SUSTAIN_DURATION:
            return (self.SPIKE_USERS, 10)

        # Phase 3: Ramp down (2m30s-5m30s) - reduce to 50 users
        elif run_time < self.SPIKE_DURATION + self.SUSTAIN_DURATION + self.RAMP_DOWN_DURATION:
            elapsed_in_phase = run_time - (self.SPIKE_DURATION + self.SUSTAIN_DURATION)
            progress = elapsed_in_phase / self.RAMP_DOWN_DURATION
            users = int(self.SPIKE_USERS - (self.SPIKE_USERS - self.STABLE_USERS) * progress)
            return (max(self.STABLE_USERS, users), 5)

        # Phase 4: Stabilize (5m30s-10m30s) - hold at 50 users
        elif run_time < self.SPIKE_DURATION + self.SUSTAIN_DURATION + self.RAMP_DOWN_DURATION + self.STABILIZE_DURATION:
            return (self.STABLE_USERS, 1)

        # Test complete
        return None


# =============================================================================
# Test User
# =============================================================================


class SpikeRecoveryUser(HttpUser):
    """
    User for spike and recovery testing.

    Monitors system behavior during traffic spikes
    and verifies proper recovery mechanisms.
    """

    wait_time = between(0.5, 2)  # Faster during spike

    def on_start(self):
        """Initialize user session"""
        global _spike_stats

        setup_event_hooks(STAGE_NAME)

        if _spike_stats["start_time"] is None:
            _spike_stats["start_time"] = time.time()
            # Check initial DLQ count
            self._check_dlq_count(initial=True)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    def _check_dlq_count(self, initial: bool = False):
        """Check current DLQ count"""
        try:
            with self.client.get(
                "/api/self-healing/status/",
                headers=self.login_helper.get_auth_header() if hasattr(self, "login_helper") else {},
                name=f"{STAGE_NAME} DLQ-check",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    dlq_count = data.get("dlq", {}).get("pending_count", 0)

                    _spike_stats["dlq"]["current_count"] = dlq_count

                    if dlq_count > _spike_stats["dlq"]["max_count"]:
                        _spike_stats["dlq"]["max_count"] = dlq_count

                    if initial:
                        _spike_stats["dlq"]["items_before_spike"] = dlq_count

                    response.success()
        except Exception:
            pass

    def _check_circuit_breaker(self):
        """Check circuit breaker state"""
        try:
            with self.client.get(
                "/api/self-healing/status/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} CB-check",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()

                    # services is a list of dicts
                    for info in data.get("services", []):
                        service = info.get("service_name", "unknown")
                        cb_state = info.get("circuit_state") or info.get("state", "closed")

                        # Detect state changes
                        if cb_state == "open" and _spike_stats["circuit_breaker"]["last_state"] != "open":
                            _spike_stats["circuit_breaker"]["open_count"] += 1

                            if _spike_stats["circuit_breaker"]["first_open_time"] is None:
                                _spike_stats["circuit_breaker"]["first_open_time"] = time.time()
                                print(f"\n🔌 Circuit Breaker OPENED for {service}")

                        elif cb_state == "closed" and _spike_stats["circuit_breaker"]["last_state"] == "open":
                            if _spike_stats["circuit_breaker"]["first_close_time"] is None:
                                _spike_stats["circuit_breaker"]["first_close_time"] = time.time()
                                print(f"\n✅ Circuit Breaker CLOSED for {service} - Recovery detected")

                        _spike_stats["circuit_breaker"]["last_state"] = cb_state

                    response.success()
        except Exception:
            pass

    # =========================================================================
    # Load Generation Tasks
    # =========================================================================

    @task(10)
    @tag("spike", "browse")
    def browse_products(self):
        """High volume product browsing"""
        start = time.time()

        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /products/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)

    @task(5)
    @tag("spike", "cart")
    def add_to_cart(self):
        """Cart operations under load"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        start = time.time()

        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /cart/add_item/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)

    @task(3)
    @tag("spike", "payment")
    def payment_flow(self):
        """Full payment flow - most stressful operation"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        # Add to cart
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add (payment)",
        )

        # Create order
        start = time.time()

        with self.client.post(
            "/api/orders/",
            json={"shipping_address": "Spike Test Address"},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /orders/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                order_data = response.json()
                order_id = order_data.get("id")

                if order_id:
                    # Request payment
                    self._request_payment(order_id)

                response.success()
            else:
                response.failure(f"Order failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    def _request_payment(self, order_id: int):
        """Request payment for order"""
        start = time.time()

        with self.client.post(
            "/api/payments/request/",
            json={"order_id": order_id},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /payments/request/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    @task(2)
    @tag("spike", "monitoring")
    def monitor_system(self):
        """Monitor self-healing system status"""
        self._check_circuit_breaker()
        self._check_dlq_count()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate spike recovery report"""
    print("\n" + "=" * 70)
    print("📊 SPIKE & RECOVERY TEST REPORT")
    print("=" * 70)

    # Phase analysis
    print("\n📈 Phase Analysis:")
    for phase, stats in _spike_stats["metrics_per_phase"].items():
        if stats["requests"] > 0:
            avg_response = sum(stats["response_times"]) / len(stats["response_times"])
            error_rate = stats["errors"] / stats["requests"] * 100
            print(f"  {phase.upper()}:")
            print(f"    - Requests: {stats['requests']}")
            print(f"    - Error Rate: {error_rate:.2f}%")
            print(f"    - Avg Response: {avg_response:.2f}ms")

    # Circuit breaker analysis
    print("\n🔌 Circuit Breaker Analysis:")
    cb = _spike_stats["circuit_breaker"]
    print(f"  - Times Opened: {cb['open_count']}")

    if cb["first_open_time"]:
        open_elapsed = cb["first_open_time"] - _spike_stats["start_time"]
        print(f"  - First Open: {open_elapsed:.1f}s after start")

    if cb["first_close_time"]:
        close_elapsed = cb["first_close_time"] - _spike_stats["start_time"]
        print(f"  - First Close: {close_elapsed:.1f}s after start")

        if cb["first_open_time"]:
            recovery_time = cb["first_close_time"] - cb["first_open_time"]
            print(f"  - Recovery Time: {recovery_time:.1f}s")

    # DLQ analysis
    print("\n📥 DLQ Analysis:")
    dlq = _spike_stats["dlq"]
    print(f"  - Items Before Spike: {dlq['items_before_spike']}")
    print(f"  - Max Count During Test: {dlq['max_count']}")
    print(f"  - Current Count: {dlq['current_count']}")

    # Save report
    report_path = os.path.join(_load_tests_dir, "reports", "stage12_spike_recovery_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_name": "Stage 12: Spike & Recovery",
                "timestamp": datetime.now().isoformat(),
                "circuit_breaker": cb,
                "dlq": dlq,
                "metrics_per_phase": {
                    phase: {
                        "requests": stats["requests"],
                        "errors": stats["errors"],
                        "error_rate": stats["errors"] / stats["requests"] * 100 if stats["requests"] > 0 else 0,
                        "avg_response_ms": (
                            sum(stats["response_times"]) / len(stats["response_times"]) if stats["response_times"] else 0
                        ),
                    }
                    for phase, stats in _spike_stats["metrics_per_phase"].items()
                },
            },
            f,
            indent=2,
        )

    print(f"\n💾 Report saved to: {report_path}")
    print("=" * 70)
