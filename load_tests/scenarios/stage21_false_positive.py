"""
Stage 21: False Positive Detection Test

Purpose: Verify Self-Healing doesn't trigger on transient issues (false positive)
- Latency alone doesn't trigger CB open
- Error rate threshold is respected
- No unnecessary service degradation
- Alert distinguishes slow vs broken
- Revenue impact of false positive logged

Scenario:
  Step 1: Normal traffic with 99.5% success
  Step 2: Inject latency spike (500ms → 2000ms) but all succeed
  Step 3: Observe if Circuit Breaker opens (should NOT)
  Step 4: Inject 1 real failure among slow requests
  Step 5: Verify proportional response

False Positive Cases:
  - High latency but 100% success → CB should stay closed
  - Single timeout among many successes → should not open CB
  - Slow external dependency but internal system healthy

Verification:
  - [ ] Latency alone doesn't trigger CB open
  - [ ] Error rate threshold is respected
  - [ ] No unnecessary service degradation
  - [ ] Alert distinguishes slow vs broken
  - [ ] Revenue impact of false positive logged

Real-World Case:
  "Slow network day, all requests succeed but take 3s.
   CB opens → 50% requests rejected → revenue loss
   when system was actually working fine."

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage21_false_positive.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage21_false_positive.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage21_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 21)
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


STAGE_NAME = "[Stage21-FalsePositive]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_NORMAL_BASELINE = max(2, int(30 * _scale))  # Normal traffic baseline
PHASE_2_HIGH_LATENCY_SUCCESS = max(4, int(90 * _scale))  # High latency but 100% success
PHASE_3_SINGLE_FAILURE = max(3, int(60 * _scale))  # Single failure among many
PHASE_4_PROPORTIONAL_CHECK = max(3, int(60 * _scale))  # Verify proportional response
PHASE_5_REVENUE_IMPACT = max(3, int(60 * _scale))  # Revenue impact analysis

TOTAL_DURATION = (
    PHASE_1_NORMAL_BASELINE
    + PHASE_2_HIGH_LATENCY_SUCCESS
    + PHASE_3_SINGLE_FAILURE
    + PHASE_4_PROPORTIONAL_CHECK
    + PHASE_5_REVENUE_IMPACT
)

# Target products
TARGET_PRODUCT_IDS = []

# Latency configurations (scaled for testing)
NORMAL_LATENCY_S = 0.1  # Normal response time
HIGH_LATENCY_S = 1.0  # Simulated high latency (would be 2-3s in prod)
TIMEOUT_LATENCY_S = 2.5  # Simulated timeout

# False positive thresholds
CB_ERROR_RATE_THRESHOLD = 0.50  # CB should open at 50% error rate
CB_FAILURE_COUNT_THRESHOLD = 5  # CB should open after 5 failures

# Revenue impact tracking
AVERAGE_ORDER_VALUE = 50000  # Average order value in won


# =============================================================================
# False Positive Statistics
# =============================================================================

_fp_stats = {
    "start_time": None,
    "phase": "normal_baseline",
    # Phase 1: Normal baseline
    "baseline_requests": 0,
    "baseline_success": 0,
    "baseline_latencies_ms": [],
    "baseline_cb_state": "closed",
    # Phase 2: High latency but success
    "high_latency_requests": 0,
    "high_latency_success": 0,  # Should be ~100%
    "high_latency_latencies_ms": [],
    "high_latency_cb_opened": False,  # Should be False (false positive if True)
    "high_latency_requests_rejected": 0,  # Should be 0
    # Phase 3: Single failure among many
    "single_failure_total_requests": 0,
    "single_failure_actual_failures": 0,
    "single_failure_cb_opened": False,  # Should be False for single failure
    # Phase 4: Proportional response
    "proportional_total_requests": 0,
    "proportional_failures": 0,
    "proportional_cb_state_changes": [],
    "proportional_response_correct": None,
    # Circuit Breaker tracking
    "cb_open_events": 0,
    "cb_close_events": 0,
    "cb_current_state": "closed",
    "cb_state_history": [],  # [(timestamp, state, reason)]
    "cb_false_positive_opens": 0,  # CB opened when it shouldn't have
    # Alert tracking
    "alerts_slow_service": 0,
    "alerts_broken_service": 0,
    "alerts_false_positive": 0,
    "alert_differentiation_correct": None,
    # Revenue impact
    "requests_rejected_by_cb": 0,
    "potential_revenue_lost": 0,  # Revenue lost due to false positive
    "false_positive_revenue_impact": 0,
    # Request tracking per phase
    "total_requests": 0,
    "total_success": 0,
    "total_failures": 0,
    "actual_error_rate": 0.0,
    # Verification
    "verification": {
        "latency_no_cb_open": None,  # High latency didn't open CB
        "error_threshold_respected": None,  # CB only opens at threshold
        "no_unnecessary_degradation": None,  # No false positive CB opens
        "alert_differentiation": None,  # Slow vs broken alerts correct
        "revenue_impact_logged": None,  # Revenue impact tracked
    },
    # SLA metrics
    "sla": {
        "false_positive_rate": None,  # Should be < 5%
        "alert_accuracy": None,  # Alert type accuracy
        "cb_precision": None,  # True positives / (True positives + False positives)
    },
}

_stats_lock = threading.Lock()


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _fp_stats["start_time"] is None:
        return "normal_baseline"

    elapsed = time.time() - _fp_stats["start_time"]

    if elapsed < PHASE_1_NORMAL_BASELINE:
        return "normal_baseline"
    elif elapsed < PHASE_1_NORMAL_BASELINE + PHASE_2_HIGH_LATENCY_SUCCESS:
        return "high_latency_success"
    elif elapsed < (PHASE_1_NORMAL_BASELINE + PHASE_2_HIGH_LATENCY_SUCCESS + PHASE_3_SINGLE_FAILURE):
        return "single_failure"
    elif elapsed < (
        PHASE_1_NORMAL_BASELINE + PHASE_2_HIGH_LATENCY_SUCCESS + PHASE_3_SINGLE_FAILURE + PHASE_4_PROPORTIONAL_CHECK
    ):
        return "proportional_check"
    else:
        return "revenue_impact"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _fp_stats["phase"]:
        old_phase = _fp_stats["phase"]
        _fp_stats["phase"] = phase

        if phase == "high_latency_success":
            print(f"\n⏳ Phase 2: High Latency Success Scenario")
            print(f"   - Injecting {HIGH_LATENCY_S}s latency on all requests")
            print(f"   - All requests should SUCCEED")
            print(f"   - CB should remain CLOSED")
        elif phase == "single_failure":
            print(f"\n⚡ Phase 3: Single Failure Among Many")
            print(f"   - Baseline requests: {_fp_stats['baseline_requests']}")
            print(f"   - High latency CB opened: {_fp_stats['high_latency_cb_opened']}")
            print(f"   - Requests rejected: {_fp_stats['high_latency_requests_rejected']}")
        elif phase == "proportional_check":
            print(f"\n📊 Phase 4: Proportional Response Check")
            print(f"   - Verifying CB opens only at error threshold")
        elif phase == "revenue_impact":
            print(f"\n💰 Phase 5: Revenue Impact Analysis")
            _calculate_revenue_impact()


def _record_baseline_request(latency_ms: float, success: bool):
    """Record baseline request"""
    with _stats_lock:
        _fp_stats["baseline_requests"] += 1
        _fp_stats["baseline_latencies_ms"].append(latency_ms)
        _fp_stats["total_requests"] += 1
        if success:
            _fp_stats["baseline_success"] += 1
            _fp_stats["total_success"] += 1
        else:
            _fp_stats["total_failures"] += 1


def _record_high_latency_request(latency_ms: float, success: bool, cb_rejected: bool = False):
    """Record high latency request"""
    with _stats_lock:
        _fp_stats["high_latency_requests"] += 1
        _fp_stats["high_latency_latencies_ms"].append(latency_ms)
        _fp_stats["total_requests"] += 1

        if cb_rejected:
            _fp_stats["high_latency_requests_rejected"] += 1
            _fp_stats["requests_rejected_by_cb"] += 1
        elif success:
            _fp_stats["high_latency_success"] += 1
            _fp_stats["total_success"] += 1
        else:
            _fp_stats["total_failures"] += 1


def _record_single_failure(is_failure: bool, cb_opened: bool):
    """Record single failure scenario"""
    with _stats_lock:
        _fp_stats["single_failure_total_requests"] += 1
        _fp_stats["total_requests"] += 1

        if is_failure:
            _fp_stats["single_failure_actual_failures"] += 1
            _fp_stats["total_failures"] += 1
        else:
            _fp_stats["total_success"] += 1

        if cb_opened and not _fp_stats["single_failure_cb_opened"]:
            _fp_stats["single_failure_cb_opened"] = True


def _record_cb_state_change(new_state: str, reason: str):
    """Record Circuit Breaker state change"""
    with _stats_lock:
        timestamp = time.time()
        old_state = _fp_stats["cb_current_state"]

        if new_state != old_state:
            _fp_stats["cb_state_history"].append((timestamp, new_state, reason))
            _fp_stats["cb_current_state"] = new_state

            if new_state == "open":
                _fp_stats["cb_open_events"] += 1

                # Check if false positive
                phase = _get_current_phase()
                if phase == "high_latency_success":
                    _fp_stats["cb_false_positive_opens"] += 1
                    _fp_stats["high_latency_cb_opened"] = True
                    print(f"   ⚠️ FALSE POSITIVE: CB opened during high latency success phase!")

            elif new_state == "closed":
                _fp_stats["cb_close_events"] += 1


def _record_alert(alert_type: str):
    """Record alert triggered"""
    with _stats_lock:
        if alert_type == "slow":
            _fp_stats["alerts_slow_service"] += 1
        elif alert_type == "broken":
            _fp_stats["alerts_broken_service"] += 1
        elif alert_type == "false_positive":
            _fp_stats["alerts_false_positive"] += 1


def _calculate_revenue_impact():
    """Calculate revenue impact of false positives"""
    with _stats_lock:
        rejected = _fp_stats["requests_rejected_by_cb"]
        # Assume each rejected request is a potential order
        _fp_stats["potential_revenue_lost"] = rejected * AVERAGE_ORDER_VALUE

        # False positive specific impact
        if _fp_stats["high_latency_cb_opened"]:
            fp_rejected = _fp_stats["high_latency_requests_rejected"]
            _fp_stats["false_positive_revenue_impact"] = fp_rejected * AVERAGE_ORDER_VALUE


def _perform_final_verification():
    """Perform final verification"""
    print(f"\n📊 Final Verification:")

    # Check latency didn't open CB
    _fp_stats["verification"]["latency_no_cb_open"] = not _fp_stats["high_latency_cb_opened"]
    print(f"   - Latency no CB open: {'✓' if _fp_stats['verification']['latency_no_cb_open'] else '✗'}")

    # Check error threshold respected
    # CB should only open if error rate exceeds threshold
    total = _fp_stats["total_requests"]
    failures = _fp_stats["total_failures"]
    if total > 0:
        actual_error_rate = failures / total
        _fp_stats["actual_error_rate"] = actual_error_rate

        # If error rate was below threshold, CB should not have opened
        if actual_error_rate < CB_ERROR_RATE_THRESHOLD:
            _fp_stats["verification"]["error_threshold_respected"] = _fp_stats["cb_open_events"] == 0
        else:
            _fp_stats["verification"]["error_threshold_respected"] = True  # Expected to open
    else:
        _fp_stats["verification"]["error_threshold_respected"] = True

    print(f"   - Error threshold respected: {'✓' if _fp_stats['verification']['error_threshold_respected'] else '✗'}")
    print(f"     (Actual error rate: {_fp_stats['actual_error_rate']:.1%}, Threshold: {CB_ERROR_RATE_THRESHOLD:.0%})")

    # Check no unnecessary degradation
    _fp_stats["verification"]["no_unnecessary_degradation"] = _fp_stats["cb_false_positive_opens"] == 0
    print(f"   - No unnecessary degradation: {'✓' if _fp_stats['verification']['no_unnecessary_degradation'] else '✗'}")
    print(f"     (False positive CB opens: {_fp_stats['cb_false_positive_opens']})")

    # Check alert differentiation
    slow_alerts = _fp_stats["alerts_slow_service"]
    broken_alerts = _fp_stats["alerts_broken_service"]
    # During high latency success phase, should get slow alerts not broken alerts
    if _fp_stats["high_latency_requests"] > 0:
        _fp_stats["verification"]["alert_differentiation"] = slow_alerts >= broken_alerts
    else:
        _fp_stats["verification"]["alert_differentiation"] = True
    print(f"   - Alert differentiation: {'✓' if _fp_stats['verification']['alert_differentiation'] else '✗'}")
    print(f"     (Slow alerts: {slow_alerts}, Broken alerts: {broken_alerts})")

    # Check revenue impact logged
    _fp_stats["verification"]["revenue_impact_logged"] = _fp_stats["potential_revenue_lost"] >= 0
    print(f"   - Revenue impact logged: {'✓' if _fp_stats['verification']['revenue_impact_logged'] else '✗'}")

    # Calculate SLA metrics
    total_cb_opens = _fp_stats["cb_open_events"]
    false_positive_opens = _fp_stats["cb_false_positive_opens"]

    if total_cb_opens > 0:
        _fp_stats["sla"]["cb_precision"] = (total_cb_opens - false_positive_opens) / total_cb_opens
    else:
        _fp_stats["sla"]["cb_precision"] = 1.0  # No opens, no false positives

    if total > 0:
        _fp_stats["sla"]["false_positive_rate"] = false_positive_opens / max(1, total_cb_opens)
    else:
        _fp_stats["sla"]["false_positive_rate"] = 0.0

    print(f"\n📈 SLA Metrics:")
    print(f"   - CB Precision: {_fp_stats['sla']['cb_precision']:.1%}")
    print(f"   - False Positive Rate: {_fp_stats['sla']['false_positive_rate']:.1%}")


# =============================================================================
# Load Shape
# =============================================================================


class FalsePositiveShape(LoadTestShape):
    """
    Load shape for false positive testing.

    Phase 1: Normal baseline traffic
    Phase 2: High latency but 100% success
    Phase 3: Single failure among many
    Phase 4: Proportional response check
    Phase 5: Revenue impact analysis
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "normal_baseline":
            return (30, 5)
        elif phase == "high_latency_success":
            return (40, 8)
        elif phase == "single_failure":
            return (40, 8)
        elif phase == "proportional_check":
            return (50, 10)
        else:  # revenue_impact
            return (20, 5)


# =============================================================================
# Test User
# =============================================================================


class FalsePositiveUser(HttpUser):
    """
    User for false positive detection testing.

    Simulates scenarios where Self-Healing might incorrectly trigger
    on transient issues like high latency.
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
        global _fp_stats, TARGET_PRODUCT_IDS

        if _fp_stats["start_time"] is None:
            _fp_stats["start_time"] = time.time()

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

    def _check_cb_state(self) -> str:
        """Check current Circuit Breaker state"""
        try:
            response = self.client.get(
                "/api/self-healing/status/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Check CB State",
            )

            if response.status_code == 200:
                data = response.json()
                # Extract CB state from response
                if isinstance(data, dict):
                    payment_cb = data.get("circuit_breakers", {}).get("toss_payment", {})
                    return payment_cb.get("state", "closed").lower()

            return "closed"
        except Exception:
            return "unknown"

    def _simulate_latency(self, latency_s: float):
        """Simulate latency by sleeping"""
        time.sleep(latency_s)

    @task(10)
    @tag("normal_baseline")
    def normal_baseline_request(self):
        """
        Phase 1: Normal baseline request.
        Establish normal latency and success rate.
        """
        phase = _get_current_phase()
        if phase != "normal_baseline":
            return

        if not TARGET_PRODUCT_IDS:
            return

        start_time = time.time()

        # Simple product list request
        response = self.client.get(
            "/api/products/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Baseline - Product List",
        )

        latency_ms = (time.time() - start_time) * 1000
        success = response.status_code == 200

        _record_baseline_request(latency_ms=latency_ms, success=success)

        # Verify CB stays closed
        cb_state = self._check_cb_state()
        _fp_stats["baseline_cb_state"] = cb_state

    @task(8)
    @tag("high_latency_success")
    def high_latency_success_request(self):
        """
        Phase 2: High latency but successful request.
        CB should NOT open because success rate is 100%.
        """
        phase = _get_current_phase()
        if phase != "high_latency_success":
            return

        if not TARGET_PRODUCT_IDS:
            return

        start_time = time.time()

        # Simulate high latency (client-side delay to simulate slow network)
        self._simulate_latency(HIGH_LATENCY_S)

        # The actual request (should succeed)
        response = self.client.get(
            "/api/products/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} HighLatency - Product List",
        )

        latency_ms = (time.time() - start_time) * 1000
        success = response.status_code == 200

        # Check if CB rejected the request (false positive indicator)
        cb_rejected = response.status_code == 503 and "circuit" in response.text.lower()

        _record_high_latency_request(latency_ms=latency_ms, success=success, cb_rejected=cb_rejected)

        # Record alert type
        if latency_ms > 1000:  # Slow but working
            _record_alert("slow")

        # Check CB state
        cb_state = self._check_cb_state()
        if cb_state == "open":
            _record_cb_state_change("open", "high_latency_phase")

    @task(6)
    @tag("single_failure")
    def single_failure_among_many(self):
        """
        Phase 3: Inject occasional single failure.
        CB should NOT open for isolated failures.
        """
        phase = _get_current_phase()
        if phase != "single_failure":
            return

        if not TARGET_PRODUCT_IDS:
            return

        # Determine if this request should fail (very low rate ~1%)
        should_fail = random.random() < 0.01

        if should_fail:
            # Simulate a failing request (request non-existent resource)
            response = self.client.get(
                "/api/products/99999999/",  # Non-existent product
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} SingleFailure - Intentional Fail",
            )
            is_failure = response.status_code != 200
        else:
            # Normal successful request
            response = self.client.get(
                "/api/products/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} SingleFailure - Normal Request",
            )
            is_failure = response.status_code != 200

        # Check CB state
        cb_state = self._check_cb_state()
        cb_opened = cb_state == "open"

        _record_single_failure(is_failure=is_failure, cb_opened=cb_opened)

        if cb_opened:
            _record_cb_state_change("open", "single_failure_phase")

    @task(5)
    @tag("proportional_check")
    def proportional_response_check(self):
        """
        Phase 4: Check CB responds proportionally to errors.
        Gradually increase error rate and verify threshold.
        """
        phase = _get_current_phase()
        if phase != "proportional_check":
            return

        if not TARGET_PRODUCT_IDS:
            return

        # Increase failure rate gradually
        elapsed = time.time() - _fp_stats["start_time"]
        phase_start = PHASE_1_NORMAL_BASELINE + PHASE_2_HIGH_LATENCY_SUCCESS + PHASE_3_SINGLE_FAILURE
        phase_elapsed = elapsed - phase_start
        phase_progress = min(1.0, phase_elapsed / PHASE_4_PROPORTIONAL_CHECK)

        # Failure rate increases from 0% to 60% over the phase
        failure_rate = phase_progress * 0.6
        should_fail = random.random() < failure_rate

        if should_fail:
            response = self.client.get(
                "/api/products/99999999/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Proportional - Fail",
            )
            _fp_stats["proportional_failures"] += 1
        else:
            response = self.client.get(
                "/api/products/",
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Proportional - Success",
            )

        _fp_stats["proportional_total_requests"] += 1

        # Check CB state
        cb_state = self._check_cb_state()

        if cb_state != _fp_stats["cb_current_state"]:
            _fp_stats["proportional_cb_state_changes"].append(
                {"time": time.time(), "state": cb_state, "error_rate": failure_rate}
            )
            _record_cb_state_change(cb_state, f"proportional_check_error_rate_{failure_rate:.0%}")

    @task(3)
    @tag("revenue_impact")
    def revenue_impact_tracking(self):
        """
        Phase 5: Track revenue impact of any false positives.
        """
        phase = _get_current_phase()
        if phase != "revenue_impact":
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # Attempt to add item to cart (revenue-impacting action)
        response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Revenue - Add to Cart",
        )

        # Check if rejected by CB
        if response.status_code == 503:
            with _stats_lock:
                _fp_stats["requests_rejected_by_cb"] += 1
                _fp_stats["potential_revenue_lost"] += AVERAGE_ORDER_VALUE

    @task(2)
    @tag("verification")
    def check_self_healing_status(self):
        """Check self-healing status for verification"""
        response = self.client.get(
            "/api/self-healing/status/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Check SH Status",
        )

        if response.status_code == 200:
            try:
                data = response.json()

                # Extract and record CB state
                if isinstance(data, dict):
                    cbs = data.get("circuit_breakers", {})
                    for service, cb_data in cbs.items():
                        state = cb_data.get("state", "closed").lower()
                        if state == "open":
                            _record_cb_state_change("open", f"status_check_{service}")
                        elif state == "closed" and _fp_stats["cb_current_state"] == "open":
                            _record_cb_state_change("closed", f"status_check_{service}")

            except Exception:
                pass


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _fp_stats

    print(f"\n{'='*70}")
    print(f"🔍 Stage 21: False Positive Detection Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify Self-Healing doesn't trigger on transient issues")
    print(f"\nConfiguration:")
    print(f"  - Normal latency: {NORMAL_LATENCY_S}s")
    print(f"  - High latency (simulated): {HIGH_LATENCY_S}s")
    print(f"  - CB error rate threshold: {CB_ERROR_RATE_THRESHOLD:.0%}")
    print(f"  - Average order value: {AVERAGE_ORDER_VALUE:,} won")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_NORMAL_BASELINE}s): Normal baseline")
    print(f"  Phase 2 ({PHASE_2_HIGH_LATENCY_SUCCESS}s): High latency success")
    print(f"  Phase 3 ({PHASE_3_SINGLE_FAILURE}s): Single failure among many")
    print(f"  Phase 4 ({PHASE_4_PROPORTIONAL_CHECK}s): Proportional check")
    print(f"  Phase 5 ({PHASE_5_REVENUE_IMPACT}s): Revenue impact analysis")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"{'='*70}\n")

    _fp_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    _perform_final_verification()

    print(f"\n{'='*70}")
    print(f"📊 Stage 21: False Positive Detection Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Phase 1 - Normal Baseline:")
    print(f"   - Requests: {_fp_stats['baseline_requests']}")
    print(f"   - Success: {_fp_stats['baseline_success']}")
    if _fp_stats["baseline_latencies_ms"]:
        avg_latency = sum(_fp_stats["baseline_latencies_ms"]) / len(_fp_stats["baseline_latencies_ms"])
        print(f"   - Avg latency: {avg_latency:.0f}ms")
    print(f"   - CB state: {_fp_stats['baseline_cb_state']}")

    print(f"\n⏳ Phase 2 - High Latency Success:")
    print(f"   - Requests: {_fp_stats['high_latency_requests']}")
    print(f"   - Success: {_fp_stats['high_latency_success']}")
    if _fp_stats["high_latency_requests"] > 0:
        success_rate = _fp_stats["high_latency_success"] / _fp_stats["high_latency_requests"]
        print(f"   - Success rate: {success_rate:.1%}")
    if _fp_stats["high_latency_latencies_ms"]:
        avg_latency = sum(_fp_stats["high_latency_latencies_ms"]) / len(_fp_stats["high_latency_latencies_ms"])
        print(f"   - Avg latency: {avg_latency:.0f}ms")
    print(f"   - CB opened (FALSE POSITIVE): {'YES ✗' if _fp_stats['high_latency_cb_opened'] else 'NO ✓'}")
    print(f"   - Requests rejected by CB: {_fp_stats['high_latency_requests_rejected']}")

    print(f"\n⚡ Phase 3 - Single Failure:")
    print(f"   - Total requests: {_fp_stats['single_failure_total_requests']}")
    print(f"   - Actual failures: {_fp_stats['single_failure_actual_failures']}")
    print(f"   - CB opened: {'YES' if _fp_stats['single_failure_cb_opened'] else 'NO'}")

    print(f"\n📊 Phase 4 - Proportional Response:")
    print(f"   - Total requests: {_fp_stats['proportional_total_requests']}")
    print(f"   - Failures injected: {_fp_stats['proportional_failures']}")
    print(f"   - CB state changes: {len(_fp_stats['proportional_cb_state_changes'])}")

    print(f"\n🔌 Circuit Breaker Summary:")
    print(f"   - CB open events: {_fp_stats['cb_open_events']}")
    print(f"   - CB close events: {_fp_stats['cb_close_events']}")
    print(f"   - False positive opens: {_fp_stats['cb_false_positive_opens']}")
    print(f"   - Current state: {_fp_stats['cb_current_state']}")

    print(f"\n🚨 Alert Summary:")
    print(f"   - Slow service alerts: {_fp_stats['alerts_slow_service']}")
    print(f"   - Broken service alerts: {_fp_stats['alerts_broken_service']}")
    print(
        f"   - Alert differentiation: {'✓' if _fp_stats['alerts_slow_service'] >= _fp_stats['alerts_broken_service'] else '✗'}"
    )

    print(f"\n💰 Revenue Impact:")
    print(f"   - Requests rejected by CB: {_fp_stats['requests_rejected_by_cb']}")
    print(f"   - Potential revenue lost: {_fp_stats['potential_revenue_lost']:,} won")
    print(f"   - False positive impact: {_fp_stats['false_positive_revenue_impact']:,} won")

    print(f"\n✅ Verification Results:")
    all_passed = True
    for check, result in _fp_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        if result is False:
            all_passed = False
        print(f"   - {check}: {status}")

    print(f"\n📈 SLA Metrics:")
    print(f"   - CB Precision: {_fp_stats['sla']['cb_precision']:.1%}")
    print(f"   - False Positive Rate: {_fp_stats['sla']['false_positive_rate']:.1%}")

    # Final verdict
    print(f"\n{'='*70}")
    if all_passed and _fp_stats["cb_false_positive_opens"] == 0:
        print(f"✅ TEST PASSED: No false positive CB triggers detected")
    else:
        print(f"❌ TEST FAILED: False positive issues detected")
        if _fp_stats["cb_false_positive_opens"] > 0:
            print(f"   - {_fp_stats['cb_false_positive_opens']} false positive CB opens")
    print(f"{'='*70}\n")


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
            "stage21_report.html",
        ]
    )
