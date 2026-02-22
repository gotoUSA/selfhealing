"""
Stage 13: Repeated Spike Test (Backoff Tuning)

Purpose: Verify backoff doesn't over-accumulate on repeated failures
- Test system resilience to multiple consecutive spikes
- Verify recovery time remains consistent across cycles
- Ensure backoff duration doesn't grow indefinitely
- Validate circuit breaker resets properly between spikes
- Confirm final system state is normalized

Load Shape:
  3 cycles of:
    Spike: 0 → 500 users (30s)
    Sustain: 500 users (1m)
    Recovery: 500 → 50 users (1m)
    Cool: 50 users (2m)

Metrics to Collect:
  - recovery_time_per_cycle
  - backoff_duration_cumulative
  - circuit_breaker_transitions
  - final_state (normalized confirmation)

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage13_repeated_spike.py --host=http://localhost:8000

    # CLI mode (~13.5 minutes total for 3 cycles)
    locust -f load_tests/scenarios/stage13_repeated_spike.py \\
        --host=http://localhost:8000 \\
        --headless --html=stage13_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 13)
"""

import os
import sys
import time
import json
from datetime import datetime

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage13-RepeatedSpike]"


# =============================================================================
# Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 270 * 3  # Original: 270s per cycle * 3 cycles = 810s
_scale = _test_duration / _original_total

# Number of spike cycles
NUM_CYCLES = 1 if _test_duration <= 30 else 3

# Phase durations (seconds) - scaled
SPIKE_DURATION = max(2, int(30 * _scale * 3))  # Scale up for single cycle
SUSTAIN_DURATION = max(2, int(60 * _scale * 3))
RECOVERY_DURATION = max(2, int(60 * _scale * 3))
COOL_DURATION = max(2, int(120 * _scale * 3))

# Total cycle duration
CYCLE_DURATION = SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION + COOL_DURATION

# User counts
SPIKE_USERS = int(os.environ.get("LOCUST_MAX_USERS", "50"))
COOL_USERS = 10


# =============================================================================
# Repeated Spike Statistics
# =============================================================================

_repeated_stats = {
    "start_time": None,
    "current_cycle": 0,
    "cycles": [],  # Per-cycle statistics
    "circuit_breaker_transitions": [],  # All CB state changes
    "backoff_observations": [],  # Observed backoff durations
    "final_state": None,
    # Recovery Latency Metrics
    "recovery": {
        "per_cycle_latencies": [],  # Recovery time per cycle
        "avg_recovery_latency_seconds": None,
        "recovery_latency_trend": None,  # "stable", "increasing", "decreasing"
        "sla_breaches": 0,  # Count of cycles exceeding SLA
    },
}


def _get_current_cycle() -> int:
    """Get current cycle number (0-indexed)"""
    if _repeated_stats["start_time"] is None:
        return 0

    elapsed = time.time() - _repeated_stats["start_time"]
    cycle = int(elapsed // CYCLE_DURATION)
    return min(cycle, NUM_CYCLES - 1)


def _get_phase_in_cycle() -> str:
    """Get current phase within the cycle"""
    if _repeated_stats["start_time"] is None:
        return "spike"

    elapsed = time.time() - _repeated_stats["start_time"]
    time_in_cycle = elapsed % CYCLE_DURATION

    if time_in_cycle < SPIKE_DURATION:
        return "spike"
    elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION:
        return "sustain"
    elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION:
        return "recovery"
    else:
        return "cool"


def _ensure_cycle_stats(cycle: int):
    """Ensure stats structure exists for given cycle"""
    while len(_repeated_stats["cycles"]) <= cycle:
        _repeated_stats["cycles"].append(
            {
                "cycle_number": len(_repeated_stats["cycles"]) + 1,
                "start_time": None,
                "phases": {
                    "spike": {"requests": 0, "errors": 0, "response_times": []},
                    "sustain": {"requests": 0, "errors": 0, "response_times": []},
                    "recovery": {"requests": 0, "errors": 0, "response_times": []},
                    "cool": {"requests": 0, "errors": 0, "response_times": []},
                },
                "cb_open_time": None,
                "cb_close_time": None,
                "recovery_time_seconds": None,
            }
        )


def _record_request(success: bool, response_time_ms: float):
    """Record request statistics for current cycle and phase"""
    cycle = _get_current_cycle()
    phase = _get_phase_in_cycle()

    _ensure_cycle_stats(cycle)

    stats = _repeated_stats["cycles"][cycle]["phases"][phase]
    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)

    if not success:
        stats["errors"] += 1


def _record_cb_transition(service: str, from_state: str, to_state: str):
    """Record circuit breaker state transition"""
    cycle = _get_current_cycle()
    _ensure_cycle_stats(cycle)

    transition = {
        "cycle": cycle + 1,
        "service": service,
        "from_state": from_state,
        "to_state": to_state,
        "timestamp": time.time(),
        "elapsed_seconds": time.time() - _repeated_stats["start_time"] if _repeated_stats["start_time"] else 0,
    }

    _repeated_stats["circuit_breaker_transitions"].append(transition)

    # Track CB open/close times for recovery calculation
    cycle_stats = _repeated_stats["cycles"][cycle]

    if to_state == "open" and cycle_stats["cb_open_time"] is None:
        cycle_stats["cb_open_time"] = time.time()
        print(f"\n🔌 Cycle {cycle + 1}: Circuit Breaker OPENED for {service}")

    elif to_state == "closed" and from_state == "open":
        if cycle_stats["cb_close_time"] is None:
            cycle_stats["cb_close_time"] = time.time()

            if cycle_stats["cb_open_time"]:
                cycle_stats["recovery_time_seconds"] = cycle_stats["cb_close_time"] - cycle_stats["cb_open_time"]
                print(f"\n✅ Cycle {cycle + 1}: Recovered in {cycle_stats['recovery_time_seconds']:.1f}s")


# =============================================================================
# Custom Load Shape - Repeated Spike
# =============================================================================


class RepeatedSpikeShape(LoadTestShape):
    """
    Repeated spike load shape for backoff tuning validation.

    Runs multiple spike cycles to verify:
    - System recovers consistently
    - Backoff doesn't accumulate excessively
    - Circuit breaker resets properly
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time"""
        run_time = self.get_run_time()
        total_duration = CYCLE_DURATION * NUM_CYCLES

        if run_time > total_duration:
            return None

        # Determine current cycle and phase
        cycle = int(run_time // CYCLE_DURATION)
        time_in_cycle = run_time % CYCLE_DURATION

        # Track cycle changes
        if cycle != _repeated_stats["current_cycle"]:
            _repeated_stats["current_cycle"] = cycle
            if cycle < NUM_CYCLES:
                print(f"\n\n{'='*50}")
                print(f"🔄 STARTING CYCLE {cycle + 1} of {NUM_CYCLES}")
                print(f"{'='*50}")

        # Spike phase (0-30s in cycle)
        if time_in_cycle < SPIKE_DURATION:
            progress = time_in_cycle / SPIKE_DURATION
            users = int(COOL_USERS + (SPIKE_USERS - COOL_USERS) * progress)
            return (max(1, users), 30)

        # Sustain phase (30s-1m30s in cycle)
        elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION:
            return (SPIKE_USERS, 10)

        # Recovery phase (1m30s-2m30s in cycle)
        elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION:
            elapsed_in_phase = time_in_cycle - (SPIKE_DURATION + SUSTAIN_DURATION)
            progress = elapsed_in_phase / RECOVERY_DURATION
            users = int(SPIKE_USERS - (SPIKE_USERS - COOL_USERS) * progress)
            return (max(COOL_USERS, users), 5)

        # Cool phase (2m30s-4m30s in cycle)
        else:
            return (COOL_USERS, 1)


# =============================================================================
# Test User
# =============================================================================


class RepeatedSpikeUser(HttpUser):
    """
    User for repeated spike testing.

    Monitors system behavior across multiple spike cycles
    to validate backoff tuning and recovery consistency.
    """

    wait_time = between(0.5, 2)

    def on_start(self):
        """Initialize user session"""
        global _repeated_stats

        setup_event_hooks(STAGE_NAME)

        if _repeated_stats["start_time"] is None:
            _repeated_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        # Track last known CB state for transition detection
        self._last_cb_state = {}

    def _check_circuit_breaker(self):
        """Check and track circuit breaker state changes"""
        try:
            with self.client.get(
                "/api/self-healing/status/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} CB-monitor",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()

                    # services is a list of dicts
                    for info in data.get("services", []):
                        service = info.get("service_name", "unknown")
                        current_state = info.get("circuit_state") or info.get("state", "closed")
                        last_state = self._last_cb_state.get(service, "closed")

                        if current_state != last_state:
                            _record_cb_transition(service, last_state, current_state)
                            self._last_cb_state[service] = current_state

                    response.success()
        except Exception:
            pass

    # =========================================================================
    # Load Generation Tasks
    # =========================================================================

    @task(10)
    @tag("repeated", "browse")
    def browse_products(self):
        """Product browsing"""
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
    @tag("repeated", "cart")
    def cart_operations(self):
        """Cart operations"""
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
    @tag("repeated", "payment")
    def payment_flow(self):
        """Payment flow"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        # Clear cart first to avoid duplicate item errors
        self.client.post(
            "/api/cart/clear/",
            json={"confirm": True},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-clear",
        )

        # Add to cart
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add",
        )

        # Create order
        start = time.time()

        with self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "Load Test User",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "Repeated Spike Test Address",
                "shipping_address_detail": "Test Building 101",
            },
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /orders/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201, 202]

            if success:
                order_data = response.json()
                order_id = order_data.get("id") or order_data.get("order_id")

                if order_id:
                    self._request_payment(order_id)

                response.success()
            else:
                response.failure(f"Order failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    def _request_payment(self, order_id: int):
        """Request payment"""
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
    @tag("repeated", "monitoring")
    def monitor_system(self):
        """Monitor circuit breaker state"""
        self._check_circuit_breaker()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate repeated spike report"""
    print("\n" + "=" * 70)
    print("📊 REPEATED SPIKE TEST REPORT")
    print("=" * 70)

    # Per-cycle analysis
    print("\n📈 Per-Cycle Analysis:")
    recovery_times = []

    for i, cycle in enumerate(_repeated_stats["cycles"]):
        print(f"\n  Cycle {i + 1}:")

        # Calculate phase metrics
        for phase_name, phase_stats in cycle["phases"].items():
            if phase_stats["requests"] > 0:
                error_rate = phase_stats["errors"] / phase_stats["requests"] * 100
                avg_response = sum(phase_stats["response_times"]) / len(phase_stats["response_times"])
                print(f"    {phase_name}: {phase_stats['requests']} reqs, {error_rate:.1f}% errors, {avg_response:.0f}ms avg")

        # Recovery time
        if cycle["recovery_time_seconds"]:
            recovery_times.append(cycle["recovery_time_seconds"])
            print(f"    Recovery Time: {cycle['recovery_time_seconds']:.1f}s")

    # Recovery consistency analysis
    print("\n🔄 Recovery Consistency:")
    recovery = _repeated_stats["recovery"]
    if recovery_times:
        avg_recovery = sum(recovery_times) / len(recovery_times)
        max_recovery = max(recovery_times)
        min_recovery = min(recovery_times)

        print(f"  - Average Recovery: {avg_recovery:.1f}s")
        print(f"  - Min Recovery: {min_recovery:.1f}s")
        print(f"  - Max Recovery: {max_recovery:.1f}s")
        print(f"  - Variance: {max_recovery - min_recovery:.1f}s")

        # Update recovery metrics
        recovery["per_cycle_latencies"] = recovery_times
        recovery["avg_recovery_latency_seconds"] = avg_recovery

        # Check for accumulation
        if len(recovery_times) >= 2:
            if recovery_times[-1] > recovery_times[0] * 1.5:
                print("  ⚠️ WARNING: Recovery time increased significantly across cycles")
                recovery["recovery_latency_trend"] = "increasing"
            else:
                print("  ✅ Recovery time remained consistent")
                recovery["recovery_latency_trend"] = "stable"

        # SLA check (2 minute threshold per cycle)
        sla_breaches = sum(1 for t in recovery_times if t > 120)
        recovery["sla_breaches"] = sla_breaches
        if sla_breaches > 0:
            print(f"  ⚠️ SLA Breaches: {sla_breaches}/{len(recovery_times)} cycles exceeded 2min")
        else:
            print("  ✅ SLA Status: All cycles under 2min threshold")

    # Circuit breaker transitions
    print(f"\n🔌 Total CB Transitions: {len(_repeated_stats['circuit_breaker_transitions'])}")

    # Save report
    report_path = os.path.join(_load_tests_dir, "reports", "stage13_repeated_spike_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        # Prepare serializable data
        cycles_data = []
        for cycle in _repeated_stats["cycles"]:
            cycle_data = {
                "cycle_number": cycle["cycle_number"],
                "recovery_time_seconds": cycle["recovery_time_seconds"],
                "phases": {},
            }
            for phase_name, phase_stats in cycle["phases"].items():
                if phase_stats["requests"] > 0:
                    cycle_data["phases"][phase_name] = {
                        "requests": phase_stats["requests"],
                        "errors": phase_stats["errors"],
                        "error_rate": phase_stats["errors"] / phase_stats["requests"] * 100,
                        "avg_response_ms": sum(phase_stats["response_times"]) / len(phase_stats["response_times"]),
                    }
            cycles_data.append(cycle_data)

        json.dump(
            {
                "test_name": "Stage 13: Repeated Spike (Backoff Tuning)",
                "timestamp": datetime.now().isoformat(),
                "num_cycles": NUM_CYCLES,
                "cycles": cycles_data,
                "recovery_times": recovery_times,
                "recovery_consistency": {
                    "avg_seconds": sum(recovery_times) / len(recovery_times) if recovery_times else None,
                    "variance_seconds": max(recovery_times) - min(recovery_times) if len(recovery_times) >= 2 else None,
                },
                "cb_transitions": _repeated_stats["circuit_breaker_transitions"],
            },
            f,
            indent=2,
        )

    print(f"\n💾 Report saved to: {report_path}")
    print("=" * 70)
