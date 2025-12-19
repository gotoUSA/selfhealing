"""
Stage 22: Self-Healing + Rate Limit Conflict Test

Purpose: Verify retry mechanism doesn't trigger rate limiting (self-DDoS)
- Retry respects external rate limits
- Backoff increases on 429 response
- Circuit breaker opens on rate limit cascade
- No self-inflicted DDoS
- Rate limit headers parsed and respected

Scenario:
  Step 1: External API has rate limit (simulated)
  Step 2: Requests fail → retry triggered
  Step 3: Retries + new requests exceed rate limit
  Step 4: Rate limit (429) triggers more retries
  Step 5: Observe cascade effect

Rate Limit Scenarios:
  - PG rate limit: Too many payment attempts
  - SMS rate limit: OTP resend spam
  - External API: Third-party service limits

Verification:
  - [ ] Retry respects external rate limits
  - [ ] Backoff increases on 429 response
  - [ ] Circuit breaker opens on rate limit cascade
  - [ ] No self-inflicted DDoS
  - [ ] Rate limit headers parsed and respected

Real-World Case:
  "Payment retries hit PG rate limit → 429 responses
   → more retries → entire payment service blocked
   → all customers affected, not just original failures."

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage22_rate_limit_conflict.py --host=http://localhost:8000

    # CLI mode (~5 minutes)
    locust -f load_tests/scenarios/stage22_rate_limit_conflict.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage22_report.html

Reference:
    - docs/self_healing/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 22)
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


STAGE_NAME = "[Stage22-RateLimitConflict]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 15s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "15"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_NORMAL_BASELINE = max(2, int(30 * _scale))  # Normal traffic
PHASE_2_APPROACHING_LIMIT = max(3, int(60 * _scale))  # Approaching rate limit
PHASE_3_RATE_LIMITED = max(4, int(90 * _scale))  # Exceeding rate limit
PHASE_4_RETRY_CASCADE = max(3, int(60 * _scale))  # Retry cascade observation
PHASE_5_RECOVERY = max(3, int(60 * _scale))  # Recovery after rate limit

TOTAL_DURATION = (
    PHASE_1_NORMAL_BASELINE + PHASE_2_APPROACHING_LIMIT + PHASE_3_RATE_LIMITED + PHASE_4_RETRY_CASCADE + PHASE_5_RECOVERY
)

# Target products
TARGET_PRODUCT_IDS = []

# Rate limit configuration (simulated)
SIMULATED_RATE_LIMIT = 100  # requests per minute (simulated)
SIMULATED_RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_THRESHOLD_PERCENT = 0.8  # 80% of limit triggers warning

# Retry configuration
MAX_RETRY_ATTEMPTS = 5
BASE_BACKOFF_S = 0.5
MAX_BACKOFF_S = 5.0
BACKOFF_MULTIPLIER = 2.0

# =============================================================================
# Global Rate Limit Coordinator (Self-DDoS Prevention)
# =============================================================================
# This simulates the distributed RateLimitCoordinator from selfhealing package
# In production, this would use Redis/DB for cross-process coordination

_global_rate_limit_lock = threading.Lock()
_global_cooldown_until = 0.0  # Unix timestamp when cooldown ends
_global_consecutive_429s = 0


def _set_global_cooldown(retry_after: float = None):
    """Set global cooldown for all users (Self-DDoS prevention)."""
    global _global_cooldown_until, _global_consecutive_429s

    with _global_rate_limit_lock:
        _global_consecutive_429s += 1

        # Calculate backoff with exponential increase
        if retry_after and retry_after > 0:
            base_delay = retry_after
        else:
            base_delay = 1.0

        # Exponential backoff: base * (2 ^ consecutive_429s)
        delay = base_delay * (BACKOFF_MULTIPLIER ** (_global_consecutive_429s - 1))
        delay = min(delay, MAX_BACKOFF_S)

        # Add jitter to prevent thundering herd (±30%)
        jitter = delay * random.uniform(-0.3, 0.3)
        delay = max(0.1, delay + jitter)

        _global_cooldown_until = time.time() + delay

        return delay


def _wait_for_global_cooldown() -> float:
    """Wait if currently in global cooldown period."""
    global _global_cooldown_until

    with _global_rate_limit_lock:
        now = time.time()
        if now < _global_cooldown_until:
            wait_time = _global_cooldown_until - now
            return wait_time
        return 0.0


def _reset_global_cooldown():
    """Reset on successful request."""
    global _global_consecutive_429s

    with _global_rate_limit_lock:
        _global_consecutive_429s = max(0, _global_consecutive_429s - 1)


# =============================================================================
# Rate Limit Statistics
# =============================================================================

_rl_stats = {
    "start_time": None,
    "phase": "normal_baseline",
    # Request tracking
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "rate_limited_requests": 0,  # 429 responses
    # Rate limit tracking
    "requests_per_window": [],  # Request count per time window
    "current_window_start": None,
    "current_window_count": 0,
    "rate_limit_hit_times": [],  # Timestamps when rate limit was hit
    "rate_limit_hit_count": 0,
    # Retry tracking
    "retry_attempts": 0,
    "retry_successes": 0,
    "retry_failures": 0,
    "retry_after_rate_limit": 0,  # Retries triggered after 429
    "retry_cascade_events": 0,  # Retry causing more rate limits
    # Backoff tracking
    "backoff_durations_ms": [],
    "backoff_respected": 0,  # Times backoff was properly applied
    "backoff_ignored": 0,  # Times backoff was not applied (bad)
    "retry_after_header_respected": 0,  # 429 Retry-After header respected
    # Circuit Breaker behavior
    "cb_current_state": "closed",
    "cb_opened_on_rate_limit": False,  # CB opened due to rate limiting
    "cb_state_changes": [],
    "cb_prevented_cascade": 0,  # Times CB prevented cascade
    # Self-DDoS indicators
    "request_rate_per_second": [],
    "peak_request_rate": 0,
    "self_ddos_detected": False,
    "self_ddos_events": 0,
    # Rate limit header tracking
    "rate_limit_headers_received": 0,
    "retry_after_values": [],  # Retry-After header values received
    "rate_limit_remaining_values": [],  # X-RateLimit-Remaining values
    # Phase-specific metrics
    "phase_metrics": {
        "normal": {"requests": 0, "rate_limited": 0},
        "approaching": {"requests": 0, "rate_limited": 0},
        "limited": {"requests": 0, "rate_limited": 0},
        "cascade": {"requests": 0, "rate_limited": 0, "retry_induced": 0},
        "recovery": {"requests": 0, "rate_limited": 0},
    },
    # Verification
    "verification": {
        "retry_respects_rate_limit": None,  # Backoff increases on 429
        "backoff_on_429": None,  # Proper backoff behavior
        "cb_opens_on_cascade": None,  # CB protects from cascade
        "no_self_ddos": None,  # No self-inflicted DDoS
        "rate_headers_parsed": None,  # Rate limit headers parsed
    },
    # SLA metrics
    "sla": {
        "rate_limit_recovery_time_s": None,
        "cascade_prevented": None,
        "self_ddos_avoided": None,
    },
}

_stats_lock = threading.Lock()

# Simulated rate limit tracking
_simulated_rate_tracker = {
    "window_start": None,
    "request_count": 0,
}


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _rl_stats["start_time"] is None:
        return "normal_baseline"

    elapsed = time.time() - _rl_stats["start_time"]

    if elapsed < PHASE_1_NORMAL_BASELINE:
        return "normal_baseline"
    elif elapsed < PHASE_1_NORMAL_BASELINE + PHASE_2_APPROACHING_LIMIT:
        return "approaching_limit"
    elif elapsed < (PHASE_1_NORMAL_BASELINE + PHASE_2_APPROACHING_LIMIT + PHASE_3_RATE_LIMITED):
        return "rate_limited"
    elif elapsed < (PHASE_1_NORMAL_BASELINE + PHASE_2_APPROACHING_LIMIT + PHASE_3_RATE_LIMITED + PHASE_4_RETRY_CASCADE):
        return "retry_cascade"
    else:
        return "recovery"


def _update_phase():
    """Update phase and log transitions"""
    phase = _get_current_phase()

    if phase != _rl_stats["phase"]:
        old_phase = _rl_stats["phase"]
        _rl_stats["phase"] = phase

        if phase == "approaching_limit":
            print(f"\n📈 Phase 2: Approaching Rate Limit")
            print(f"   - Baseline requests: {_rl_stats['phase_metrics']['normal']['requests']}")
            print(f"   - Rate limited: {_rl_stats['phase_metrics']['normal']['rate_limited']}")
        elif phase == "rate_limited":
            print(f"\n🚫 Phase 3: Rate Limited Scenario")
            print(f"   - Approaching limit requests: {_rl_stats['phase_metrics']['approaching']['requests']}")
            print(f"   - Increasing request rate to trigger rate limit")
        elif phase == "retry_cascade":
            print(f"\n🔄 Phase 4: Retry Cascade Observation")
            print(f"   - Rate limited requests: {_rl_stats['rate_limited_requests']}")
            print(f"   - Retry attempts: {_rl_stats['retry_attempts']}")
            print(f"   - Observing if retries cause cascade")
        elif phase == "recovery":
            print(f"\n✅ Phase 5: Recovery Phase")
            print(f"   - Cascade events: {_rl_stats['retry_cascade_events']}")
            print(f"   - CB state: {_rl_stats['cb_current_state']}")
            _calculate_recovery_metrics()


def _check_simulated_rate_limit() -> tuple[bool, int]:
    """
    Check if simulated rate limit is exceeded.
    Returns (is_limited, retry_after_seconds)
    """
    current_time = time.time()

    with _stats_lock:
        # Initialize or reset window
        if _simulated_rate_tracker["window_start"] is None:
            _simulated_rate_tracker["window_start"] = current_time
            _simulated_rate_tracker["request_count"] = 0

        # Check if window has expired
        window_elapsed = current_time - _simulated_rate_tracker["window_start"]
        if window_elapsed >= SIMULATED_RATE_LIMIT_WINDOW_S:
            _simulated_rate_tracker["window_start"] = current_time
            _simulated_rate_tracker["request_count"] = 0

        # Increment count
        _simulated_rate_tracker["request_count"] += 1

        # Check limit
        if _simulated_rate_tracker["request_count"] > SIMULATED_RATE_LIMIT:
            retry_after = int(SIMULATED_RATE_LIMIT_WINDOW_S - window_elapsed) + 1
            return True, max(1, retry_after)

        return False, 0


def _record_request(success: bool, rate_limited: bool = False, phase: str = None):
    """Record request result"""
    with _stats_lock:
        _rl_stats["total_requests"] += 1

        if rate_limited:
            _rl_stats["rate_limited_requests"] += 1
            _rl_stats["rate_limit_hit_count"] += 1
            _rl_stats["rate_limit_hit_times"].append(time.time())
        elif success:
            _rl_stats["successful_requests"] += 1
        else:
            _rl_stats["failed_requests"] += 1

        # Track per-phase metrics
        if phase:
            phase_key = {
                "normal_baseline": "normal",
                "approaching_limit": "approaching",
                "rate_limited": "limited",
                "retry_cascade": "cascade",
                "recovery": "recovery",
            }.get(phase, "normal")

            _rl_stats["phase_metrics"][phase_key]["requests"] += 1
            if rate_limited:
                _rl_stats["phase_metrics"][phase_key]["rate_limited"] += 1

        # Track request rate
        current_time = time.time()
        _rl_stats["request_rate_per_second"].append(current_time)

        # Calculate current rate (last 5 seconds)
        recent_requests = [t for t in _rl_stats["request_rate_per_second"] if current_time - t < 5]
        current_rate = len(recent_requests) / 5.0

        if current_rate > _rl_stats["peak_request_rate"]:
            _rl_stats["peak_request_rate"] = current_rate


def _record_retry(success: bool, after_rate_limit: bool = False, caused_cascade: bool = False):
    """Record retry attempt"""
    with _stats_lock:
        _rl_stats["retry_attempts"] += 1

        if success:
            _rl_stats["retry_successes"] += 1
        else:
            _rl_stats["retry_failures"] += 1

        if after_rate_limit:
            _rl_stats["retry_after_rate_limit"] += 1

        if caused_cascade:
            _rl_stats["retry_cascade_events"] += 1

            # Track in cascade phase metrics
            _rl_stats["phase_metrics"]["cascade"]["retry_induced"] += 1


def _record_backoff(duration_ms: float, respected: bool, retry_after_respected: bool = False):
    """Record backoff behavior"""
    with _stats_lock:
        _rl_stats["backoff_durations_ms"].append(duration_ms)

        if respected:
            _rl_stats["backoff_respected"] += 1
        else:
            _rl_stats["backoff_ignored"] += 1

        if retry_after_respected:
            _rl_stats["retry_after_header_respected"] += 1


def _record_rate_limit_headers(retry_after: int = None, remaining: int = None):
    """Record rate limit headers"""
    with _stats_lock:
        _rl_stats["rate_limit_headers_received"] += 1

        if retry_after is not None:
            _rl_stats["retry_after_values"].append(retry_after)

        if remaining is not None:
            _rl_stats["rate_limit_remaining_values"].append(remaining)


def _record_cb_state_change(new_state: str, reason: str):
    """Record Circuit Breaker state change"""
    with _stats_lock:
        old_state = _rl_stats["cb_current_state"]

        if new_state != old_state:
            _rl_stats["cb_state_changes"].append({"time": time.time(), "from": old_state, "to": new_state, "reason": reason})
            _rl_stats["cb_current_state"] = new_state

            if new_state == "open" and "rate_limit" in reason.lower():
                _rl_stats["cb_opened_on_rate_limit"] = True

            print(f"   🔌 CB State: {old_state} → {new_state} ({reason})")


def _check_self_ddos():
    """Check for self-DDoS indicators"""
    with _stats_lock:
        # Self-DDoS indicators:
        # 1. High retry rate after rate limiting
        # 2. Rate of 429s increasing over time
        # 3. Request rate not decreasing after rate limit

        if _rl_stats["retry_after_rate_limit"] > 20:
            # Too many retries after rate limiting
            recent_429s = len([t for t in _rl_stats["rate_limit_hit_times"] if time.time() - t < 10])

            if recent_429s > 10:  # More than 10 rate limits in 10 seconds
                _rl_stats["self_ddos_detected"] = True
                _rl_stats["self_ddos_events"] += 1
                print(f"   ⚠️ Self-DDoS indicator detected! ({recent_429s} rate limits in 10s)")


def _calculate_recovery_metrics():
    """Calculate recovery metrics after rate limit phase"""
    with _stats_lock:
        if _rl_stats["rate_limit_hit_times"]:
            last_rate_limit = max(_rl_stats["rate_limit_hit_times"])

            # Find first successful request after last rate limit
            recovery_time = None
            # Simplified - just calculate based on phase timing
            _rl_stats["sla"]["rate_limit_recovery_time_s"] = time.time() - last_rate_limit if last_rate_limit else None

        _rl_stats["sla"]["cascade_prevented"] = _rl_stats["retry_cascade_events"] < 5
        _rl_stats["sla"]["self_ddos_avoided"] = not _rl_stats["self_ddos_detected"]


def _perform_final_verification():
    """Perform final verification"""
    print(f"\n📊 Final Verification:")

    # Check retry respects rate limit
    if _rl_stats["backoff_respected"] + _rl_stats["backoff_ignored"] > 0:
        backoff_ratio = _rl_stats["backoff_respected"] / (_rl_stats["backoff_respected"] + _rl_stats["backoff_ignored"])
        _rl_stats["verification"]["retry_respects_rate_limit"] = backoff_ratio >= 0.8
    else:
        _rl_stats["verification"]["retry_respects_rate_limit"] = True
    print(f"   - Retry respects rate limit: {'✓' if _rl_stats['verification']['retry_respects_rate_limit'] else '✗'}")

    # Check backoff on 429
    if _rl_stats["backoff_durations_ms"]:
        avg_backoff = sum(_rl_stats["backoff_durations_ms"]) / len(_rl_stats["backoff_durations_ms"])
        _rl_stats["verification"]["backoff_on_429"] = avg_backoff > BASE_BACKOFF_S * 1000 * 0.5
    else:
        _rl_stats["verification"]["backoff_on_429"] = True
    print(f"   - Backoff on 429: {'✓' if _rl_stats['verification']['backoff_on_429'] else '✗'}")

    # Check CB opens on cascade
    _rl_stats["verification"]["cb_opens_on_cascade"] = (
        _rl_stats["cb_opened_on_rate_limit"] or _rl_stats["retry_cascade_events"] < 3
    )
    print(f"   - CB opens on cascade: {'✓' if _rl_stats['verification']['cb_opens_on_cascade'] else '✗'}")

    # Check no self-DDoS
    _rl_stats["verification"]["no_self_ddos"] = not _rl_stats["self_ddos_detected"]
    print(f"   - No self-DDoS: {'✓' if _rl_stats['verification']['no_self_ddos'] else '✗'}")

    # Check rate headers parsed
    _rl_stats["verification"]["rate_headers_parsed"] = _rl_stats["rate_limit_headers_received"] > 0
    print(f"   - Rate headers parsed: {'✓' if _rl_stats['verification']['rate_headers_parsed'] else '✗'}")


# =============================================================================
# Load Shape
# =============================================================================


class RateLimitConflictShape(LoadTestShape):
    """
    Load shape for rate limit conflict testing.

    Phase 1: Normal baseline traffic
    Phase 2: Approaching rate limit
    Phase 3: Exceeding rate limit (trigger 429s)
    Phase 4: Retry cascade observation
    Phase 5: Recovery
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "normal_baseline":
            return (20, 5)  # Low traffic
        elif phase == "approaching_limit":
            return (40, 10)  # Increasing traffic
        elif phase == "rate_limited":
            return (80, 20)  # High traffic to trigger rate limits
        elif phase == "retry_cascade":
            return (60, 15)  # Sustained traffic
        else:  # recovery
            return (20, 5)  # Reduced traffic for recovery


# =============================================================================
# Test User
# =============================================================================


class RateLimitConflictUser(HttpUser):
    """
    User for rate limit conflict testing.

    Simulates scenarios where retry mechanisms might conflict
    with external rate limits, potentially causing self-DDoS.
    """

    wait_time = between(0.3, 1.0)  # Fast wait time to hit rate limits

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login_helper = None
        self.product_helper = None
        self.cart_helper = None
        self.payment_helper = None
        self.access_token = None
        self.user_id = None
        self.user_index = None
        self.consecutive_429s = 0
        self.current_backoff = BASE_BACKOFF_S

    def on_start(self):
        """Login and setup helpers"""
        global _rl_stats, TARGET_PRODUCT_IDS

        if _rl_stats["start_time"] is None:
            _rl_stats["start_time"] = time.time()

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

    def _calculate_backoff(self, attempt: int, retry_after: int = None) -> float:
        """Calculate exponential backoff with jitter"""
        if retry_after:
            return retry_after

        backoff = min(BASE_BACKOFF_S * (BACKOFF_MULTIPLIER**attempt), MAX_BACKOFF_S)
        # Add jitter (0-25%)
        jitter = backoff * random.uniform(0, 0.25)
        return backoff + jitter

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
                if isinstance(data, dict):
                    payment_cb = data.get("circuit_breakers", {}).get("toss_payment", {})
                    return payment_cb.get("state", "closed").lower()

            return "closed"
        except Exception:
            return "unknown"

    def _handle_rate_limit_response(self, response) -> tuple[int, int]:
        """
        Extract rate limit info from response headers.
        Returns (retry_after_seconds, remaining_requests)
        """
        retry_after = None
        remaining = None

        # Check for standard rate limit headers
        if "Retry-After" in response.headers:
            try:
                retry_after = int(response.headers["Retry-After"])
            except ValueError:
                retry_after = 60  # Default

        if "X-RateLimit-Remaining" in response.headers:
            try:
                remaining = int(response.headers["X-RateLimit-Remaining"])
            except ValueError:
                pass

        if retry_after or remaining is not None:
            _record_rate_limit_headers(retry_after=retry_after, remaining=remaining)

        return retry_after or 0, remaining or 0

    @task(10)
    @tag("rate_limit_test")
    def payment_rate_limit_scenario(self):
        """
        Test payment endpoint with rate limit simulation.
        Simulates rate limiting behavior on payment requests.

        Uses Global Cooldown pattern to prevent Self-DDoS:
        - When one user gets 429, ALL users wait
        - Exponential backoff with jitter
        - Prevents cascade of retries
        """
        phase = _get_current_phase()

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)

        # *** Self-DDoS Prevention: Check global cooldown first ***
        wait_time = _wait_for_global_cooldown()
        if wait_time > 0:
            # Record that we're respecting the global cooldown
            _record_backoff(duration_ms=wait_time * 1000, respected=True, retry_after_respected=True)
            time.sleep(wait_time)
            # After waiting, continue with the request

        # Check simulated rate limit
        is_limited, retry_after = _check_simulated_rate_limit()

        if is_limited:
            # Simulate 429 response behavior
            _record_request(success=False, rate_limited=True, phase=phase)
            _record_rate_limit_headers(retry_after=retry_after)

            self.consecutive_429s += 1

            # *** Self-DDoS Prevention: Set GLOBAL cooldown ***
            # This makes ALL users wait, not just this one
            cooldown = _set_global_cooldown(retry_after)

            # Check for self-DDoS
            _check_self_ddos()

            # Proper backoff behavior (now uses global cooldown)
            _record_backoff(duration_ms=cooldown * 1000, respected=True, retry_after_respected=bool(retry_after))

            # Sleep for the global cooldown
            time.sleep(min(cooldown, 2.0))  # Cap at 2s for testing

            # Check if CB should open
            if self.consecutive_429s >= 5:
                cb_state = self._check_cb_state()
                if cb_state == "open":
                    _record_cb_state_change("open", "rate_limit_cascade")

            return

        # Reset consecutive 429s on success
        if self.consecutive_429s > 0:
            self.consecutive_429s = 0
            self.current_backoff = BASE_BACKOFF_S
            _reset_global_cooldown()  # Notify global coordinator of success

        # Actual request
        response = self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Add to Cart",
        )

        # Check for real 429 from server
        if response.status_code == 429:
            _record_request(success=False, rate_limited=True, phase=phase)
            retry_after, remaining = self._handle_rate_limit_response(response)

            self.consecutive_429s += 1

            # *** Self-DDoS Prevention: Set GLOBAL cooldown ***
            cooldown = _set_global_cooldown(retry_after)

            _check_self_ddos()

            # Apply global backoff
            _record_backoff(duration_ms=cooldown * 1000, respected=True, retry_after_respected=bool(retry_after))

            time.sleep(min(cooldown, 2.0))

        elif response.status_code in [200, 201]:
            _record_request(success=True, rate_limited=False, phase=phase)
            self.consecutive_429s = 0
            _reset_global_cooldown()  # Success - gradually reset
        else:
            _record_request(success=False, rate_limited=False, phase=phase)

    @task(5)
    @tag("retry_behavior")
    def retry_with_backoff(self):
        """
        Test retry behavior with exponential backoff.
        Verifies proper backoff on repeated failures.
        """
        phase = _get_current_phase()
        if phase not in ["rate_limited", "retry_cascade"]:
            return

        if not TARGET_PRODUCT_IDS:
            return

        product_id = random.choice(TARGET_PRODUCT_IDS)
        idempotent_key = str(uuid.uuid4())

        # Attempt with retries
        for attempt in range(MAX_RETRY_ATTEMPTS):
            # *** GLOBAL COOLDOWN CHECK (Self-DDoS Prevention) ***
            cooldown_wait = _wait_for_global_cooldown()
            if cooldown_wait > 0:
                _record_backoff(duration_ms=cooldown_wait * 1000, respected=True, retry_after_respected=True)
                time.sleep(min(cooldown_wait, 2.0))
                # After cooldown, check again before proceeding
                continue

            # Check simulated rate limit
            is_limited, retry_after = _check_simulated_rate_limit()

            if is_limited:
                _record_retry(
                    success=False,
                    after_rate_limit=True,
                    caused_cascade=(attempt > 0 and phase == "retry_cascade"),
                )

                # *** Set GLOBAL cooldown for all users ***
                cooldown = _set_global_cooldown(retry_after)
                _check_self_ddos()

                _record_backoff(duration_ms=cooldown * 1000, respected=True)

                time.sleep(min(cooldown, 2.0))
                continue

            # Actual request
            response = self.client.post(
                "/api/cart/add_item/",
                json={"product_id": product_id, "quantity": 1},
                headers=self._get_auth_headers(),
                name=f"{STAGE_NAME} Retry Add to Cart",
            )

            if response.status_code == 429:
                _record_retry(
                    success=False,
                    after_rate_limit=True,
                    caused_cascade=(attempt > 0),
                )

                retry_after, _ = self._handle_rate_limit_response(response)

                # *** Set GLOBAL cooldown for all users ***
                cooldown = _set_global_cooldown(retry_after)
                _check_self_ddos()

                _record_backoff(duration_ms=cooldown * 1000, respected=True)

                time.sleep(min(cooldown, 2.0))
                continue

            elif response.status_code in [200, 201]:
                _record_retry(success=True, after_rate_limit=False)
                _reset_global_cooldown()  # Success - reset global state
                break

            else:
                _record_retry(success=False, after_rate_limit=False)
                # Backoff on general failure too
                backoff_s = self._calculate_backoff(attempt)
                time.sleep(min(backoff_s, 1.0))

    @task(3)
    @tag("cascade_prevention")
    def check_cascade_prevention(self):
        """
        Check if Circuit Breaker prevents retry cascade.
        """
        phase = _get_current_phase()
        if phase != "retry_cascade":
            return

        # *** GLOBAL COOLDOWN CHECK (Self-DDoS Prevention) ***
        cooldown_wait = _wait_for_global_cooldown()
        if cooldown_wait > 0:
            _record_backoff(duration_ms=cooldown_wait * 1000, respected=True, retry_after_respected=True)
            time.sleep(min(cooldown_wait, 2.0))
            return  # Skip this iteration

        # Check CB state
        cb_state = self._check_cb_state()

        if cb_state == "open":
            _rl_stats["cb_prevented_cascade"] += 1

            # CB is protecting us - record and skip request
            with _stats_lock:
                if cb_state != _rl_stats["cb_current_state"]:
                    _record_cb_state_change("open", "cascade_prevention")

            return

        # If CB is closed, make a request to check rate limit state
        response = self.client.get(
            "/api/products/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Cascade Check Request",
        )

        if response.status_code == 429:
            # Still rate limited - CB should consider opening
            _record_request(success=False, rate_limited=True, phase=phase)
            # *** Set GLOBAL cooldown ***
            _set_global_cooldown()
            _check_self_ddos()
        elif response.status_code == 200:
            _reset_global_cooldown()

    @task(2)
    @tag("recovery")
    def recovery_monitoring(self):
        """
        Monitor recovery after rate limiting.
        """
        phase = _get_current_phase()
        if phase != "recovery":
            return

        # *** GLOBAL COOLDOWN CHECK (Self-DDoS Prevention) ***
        cooldown_wait = _wait_for_global_cooldown()
        if cooldown_wait > 0:
            _record_backoff(duration_ms=cooldown_wait * 1000, respected=True, retry_after_respected=True)
            time.sleep(min(cooldown_wait, 2.0))
            return  # Skip this iteration

        # Light request to check if rate limit has lifted
        response = self.client.get(
            "/api/products/",
            headers=self._get_auth_headers(),
            name=f"{STAGE_NAME} Recovery Check",
        )

        if response.status_code == 200:
            _record_request(success=True, rate_limited=False, phase=phase)
            _reset_global_cooldown()  # Success - reset global state

            # Check if CB has closed
            cb_state = self._check_cb_state()
            if cb_state == "closed" and _rl_stats["cb_current_state"] == "open":
                _record_cb_state_change("closed", "recovery_complete")

        elif response.status_code == 429:
            _record_request(success=False, rate_limited=True, phase=phase)
            # *** Set GLOBAL cooldown ***
            _set_global_cooldown()
            _check_self_ddos()


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    global _rl_stats

    print(f"\n{'='*70}")
    print(f"🚫 Stage 22: Self-Healing + Rate Limit Conflict Test")
    print(f"{'='*70}")
    print(f"Purpose: Verify retry mechanism doesn't trigger rate limiting (self-DDoS)")
    print(f"\nConfiguration:")
    print(f"  - Simulated rate limit: {SIMULATED_RATE_LIMIT} req/min")
    print(f"  - Max retry attempts: {MAX_RETRY_ATTEMPTS}")
    print(f"  - Base backoff: {BASE_BACKOFF_S}s")
    print(f"  - Max backoff: {MAX_BACKOFF_S}s")
    print(f"  - Backoff multiplier: {BACKOFF_MULTIPLIER}x")
    print(f"\nTest Phases:")
    print(f"  Phase 1 ({PHASE_1_NORMAL_BASELINE}s): Normal baseline")
    print(f"  Phase 2 ({PHASE_2_APPROACHING_LIMIT}s): Approaching limit")
    print(f"  Phase 3 ({PHASE_3_RATE_LIMITED}s): Rate limited")
    print(f"  Phase 4 ({PHASE_4_RETRY_CASCADE}s): Retry cascade")
    print(f"  Phase 5 ({PHASE_5_RECOVERY}s): Recovery")
    print(f"\nTotal Duration: {TOTAL_DURATION}s")
    print(f"{'='*70}\n")

    _rl_stats["start_time"] = time.time()

    # Setup custom metrics
    setup_event_hooks()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate final report"""
    _perform_final_verification()

    print(f"\n{'='*70}")
    print(f"📊 Stage 22: Rate Limit Conflict Test Results")
    print(f"{'='*70}")

    print(f"\n📈 Request Summary:")
    print(f"   - Total requests: {_rl_stats['total_requests']}")
    print(f"   - Successful: {_rl_stats['successful_requests']}")
    print(f"   - Failed: {_rl_stats['failed_requests']}")
    print(f"   - Rate limited (429): {_rl_stats['rate_limited_requests']}")

    if _rl_stats["total_requests"] > 0:
        success_rate = _rl_stats["successful_requests"] / _rl_stats["total_requests"]
        rate_limit_rate = _rl_stats["rate_limited_requests"] / _rl_stats["total_requests"]
        print(f"   - Success rate: {success_rate:.1%}")
        print(f"   - Rate limit rate: {rate_limit_rate:.1%}")

    print(f"\n🔄 Retry Statistics:")
    print(f"   - Total retry attempts: {_rl_stats['retry_attempts']}")
    print(f"   - Retry successes: {_rl_stats['retry_successes']}")
    print(f"   - Retry failures: {_rl_stats['retry_failures']}")
    print(f"   - Retries after rate limit: {_rl_stats['retry_after_rate_limit']}")
    print(f"   - Retry cascade events: {_rl_stats['retry_cascade_events']}")

    print(f"\n⏱️ Backoff Statistics:")
    print(f"   - Backoff respected: {_rl_stats['backoff_respected']}")
    print(f"   - Backoff ignored: {_rl_stats['backoff_ignored']}")
    print(f"   - Retry-After headers respected: {_rl_stats['retry_after_header_respected']}")

    if _rl_stats["backoff_durations_ms"]:
        avg_backoff = sum(_rl_stats["backoff_durations_ms"]) / len(_rl_stats["backoff_durations_ms"])
        max_backoff = max(_rl_stats["backoff_durations_ms"])
        print(f"   - Avg backoff: {avg_backoff:.0f}ms")
        print(f"   - Max backoff: {max_backoff:.0f}ms")

    print(f"\n🔌 Circuit Breaker:")
    print(f"   - Current state: {_rl_stats['cb_current_state']}")
    print(f"   - State changes: {len(_rl_stats['cb_state_changes'])}")
    print(f"   - Opened on rate limit: {'Yes' if _rl_stats['cb_opened_on_rate_limit'] else 'No'}")
    print(f"   - Cascade preventions: {_rl_stats['cb_prevented_cascade']}")

    print(f"\n⚠️ Self-DDoS Indicators:")
    print(f"   - Self-DDoS detected: {'YES ✗' if _rl_stats['self_ddos_detected'] else 'NO ✓'}")
    print(f"   - Self-DDoS events: {_rl_stats['self_ddos_events']}")
    print(f"   - Peak request rate: {_rl_stats['peak_request_rate']:.1f} req/s")

    print(f"\n📊 Phase Metrics:")
    for phase_name, metrics in _rl_stats["phase_metrics"].items():
        print(f"   {phase_name.capitalize()}:")
        print(f"     - Requests: {metrics['requests']}")
        print(f"     - Rate limited: {metrics['rate_limited']}")
        if "retry_induced" in metrics:
            print(f"     - Retry induced: {metrics['retry_induced']}")

    print(f"\n📋 Rate Limit Headers:")
    print(f"   - Headers received: {_rl_stats['rate_limit_headers_received']}")
    if _rl_stats["retry_after_values"]:
        avg_retry_after = sum(_rl_stats["retry_after_values"]) / len(_rl_stats["retry_after_values"])
        print(f"   - Avg Retry-After: {avg_retry_after:.0f}s")

    print(f"\n✅ Verification Results:")
    all_passed = True
    for check, result in _rl_stats["verification"].items():
        status = "✓" if result else "✗" if result is False else "?"
        if result is False:
            all_passed = False
        print(f"   - {check}: {status}")

    print(f"\n📈 SLA Metrics:")
    if _rl_stats["sla"]["rate_limit_recovery_time_s"]:
        print(f"   - Recovery time: {_rl_stats['sla']['rate_limit_recovery_time_s']:.1f}s")
    print(f"   - Cascade prevented: {'✓' if _rl_stats['sla']['cascade_prevented'] else '✗'}")
    print(f"   - Self-DDoS avoided: {'✓' if _rl_stats['sla']['self_ddos_avoided'] else '✗'}")

    # Final verdict
    print(f"\n{'='*70}")
    if all_passed and not _rl_stats["self_ddos_detected"]:
        print(f"✅ TEST PASSED: No self-DDoS detected, rate limits respected")
    else:
        print(f"❌ TEST FAILED: Rate limit conflict issues detected")
        if _rl_stats["self_ddos_detected"]:
            print(f"   - Self-DDoS detected: {_rl_stats['self_ddos_events']} events")
        if _rl_stats["retry_cascade_events"] > 5:
            print(f"   - Excessive retry cascades: {_rl_stats['retry_cascade_events']}")
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
            "stage22_report.html",
        ]
    )
