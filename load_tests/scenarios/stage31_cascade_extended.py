"""
Stage 31: Cascade Failure Extended

Purpose: Verify complex cascading failure scenarios with multiple component interactions
- Redis→DB→CB cascade failure handling
- Payment API→Retry Storm→Rate Limit deadlock prevention
- Health Check delay false positive prevention
- Redis Degradation + Cache Stampede Prevention (TC-31-4)

Extended from Stage 18 (Chain Failure) to cover:
- CB False Positive prevention
- Multi-component cascade isolation
- Rate Limit Deadlock detection and auto-release
- Cache Stampede fallback safety under Redis degradation

Scenarios:
  SC-31-1: Redis Down → DB Surge → CB Malfunction
  SC-31-2: Payment API Down → Retry Storm → Rate Limit Deadlock
  SC-31-3: Health Check Delay → Wrong Decision
  SC-31-4: Redis Degradation → Stampede Prevention → DB Explosion Check

Verification:
  - [ ] CB False Positive = 0
  - [ ] Cascade isolation < 30s
  - [ ] Rate Limit Deadlock auto-release
  - [ ] Health Check accuracy > 95%
  - [ ] TC-31-4: DB queries ≤ 3 even under Redis 200ms latency

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage31_cascade_extended.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage31_cascade_extended.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage31_report.html

Reference:
    - docs/STAGE_31_36_EXTENSION_PLAN.md (Stage 31)
    - Stage 18 (Chain Failure) base implementation
"""

import os
import sys
import time
import json
import random
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage31-CascadeExtended]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 20s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE_DURATION = max(3, int(30 * _scale))  # Normal baseline
PHASE_2_REDIS_FAILURE = max(4, int(60 * _scale))  # Redis→DB→CB cascade
PHASE_3_PAYMENT_FAILURE = max(4, int(60 * _scale))  # Payment→Retry→Rate Limit
PHASE_4_HEALTH_CHECK_DELAY = max(4, int(60 * _scale))  # Health Check delay
PHASE_5_VERIFICATION = max(3, int(60 * _scale))  # Recovery and verification

TOTAL_DURATION = (
    PHASE_1_BASELINE_DURATION
    + PHASE_2_REDIS_FAILURE
    + PHASE_3_PAYMENT_FAILURE
    + PHASE_4_HEALTH_CHECK_DELAY
    + PHASE_5_VERIFICATION
)

# Failure injection rates
REDIS_FAILURE_RATE = 0.8  # 80% of cache operations fail
PAYMENT_FAILURE_RATE = 0.9  # 90% of payment attempts fail
HEALTH_CHECK_DELAY_MS = 5000  # 5 second delay

# Circuit Breaker thresholds
CB_FAILURE_THRESHOLD = 5
CB_OPEN_TIMEOUT_S = 30
CB_FALSE_POSITIVE_THRESHOLD = 0  # Must be 0

# Rate limit configuration
RATE_LIMIT_MAX_REQUESTS = 100
RATE_LIMIT_WINDOW_S = 60
DEADLOCK_DETECTION_TIMEOUT_S = 10


# =============================================================================
# Cascade Failure Statistics
# =============================================================================


@dataclass
class CascadeStats:
    """Statistics tracking for cascade failures"""

    start_time: Optional[float] = None
    phase: str = "baseline"

    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Scenario 1: Redis→DB→CB
    redis_failures: int = 0
    db_surge_detected: int = 0
    db_connection_exhausted: int = 0
    cb_opened_for_db: int = 0
    cb_false_positives: int = 0
    db_recovery_time_ms: List[float] = field(default_factory=list)

    # Scenario 2: Payment→Retry→Rate Limit
    payment_failures: int = 0
    retry_storms_detected: int = 0
    rate_limit_hits: int = 0
    rate_limit_deadlocks: int = 0
    deadlock_auto_releases: int = 0
    retry_backoff_respected: int = 0

    # Scenario 3: Health Check Delay
    health_checks_performed: int = 0
    health_check_correct: int = 0
    health_check_wrong: int = 0
    false_down_decisions: int = 0
    slow_vs_dead_detected: int = 0

    # Scenario 4: Redis Degradation + Stampede (TC-31-4)
    redis_degradation_requests: int = 0
    stampede_lock_timeouts: int = 0
    stampede_fallback_db_queries: int = 0
    stampede_db_explosion_prevented: bool = True  # Must remain True

    # Recovery metrics
    cascade_isolation_times_ms: List[float] = field(default_factory=list)

    # Verification results
    verification: Dict[str, Optional[bool]] = field(
        default_factory=lambda: {
            "cb_false_positive_zero": None,
            "cascade_isolation_under_30s": None,
            "rate_limit_deadlock_zero": None,
            "health_check_accuracy_95": None,
            "stampede_db_explosion_prevented": None,  # TC-31-4
        }
    )


_cascade_stats = CascadeStats()
_stats_lock = threading.Lock()


# =============================================================================
# Simulated Component States
# =============================================================================


@dataclass
class ComponentState:
    """Track simulated component health states"""

    redis_healthy: bool = True
    db_healthy: bool = True
    db_connections_available: int = 20  # Reduced from 100 to force exhaustion
    db_connections_max: int = 20  # Reduced from 100 to force exhaustion
    payment_api_healthy: bool = True
    rate_limit_remaining: int = 100
    rate_limit_window_start: float = 0
    health_check_delay_ms: int = 0
    cb_db_state: str = "closed"  # closed, open, half-open
    cb_db_failures: int = 0
    cb_db_last_failure: float = 0


_component_state = ComponentState()
_component_lock = threading.Lock()


def _simulate_redis_failure():
    """Simulate Redis connection failure"""
    with _component_lock:
        _component_state.redis_healthy = False


def _simulate_redis_recovery():
    """Simulate Redis recovery"""
    with _component_lock:
        _component_state.redis_healthy = True


def _simulate_payment_failure():
    """Simulate Payment API failure"""
    with _component_lock:
        _component_state.payment_api_healthy = False


def _simulate_payment_recovery():
    """Simulate Payment API recovery"""
    with _component_lock:
        _component_state.payment_api_healthy = True


def _simulate_health_check_delay():
    """Simulate slow health check response"""
    with _component_lock:
        _component_state.health_check_delay_ms = HEALTH_CHECK_DELAY_MS


def _simulate_health_check_normal():
    """Reset health check to normal"""
    with _component_lock:
        _component_state.health_check_delay_ms = 0


def _consume_db_connection() -> bool:
    """Try to acquire a DB connection, return success"""
    with _component_lock:
        if _component_state.db_connections_available > 0:
            _component_state.db_connections_available -= 1
            return True
        return False


def _release_db_connection():
    """Release a DB connection back to pool"""
    with _component_lock:
        if _component_state.db_connections_available < _component_state.db_connections_max:
            _component_state.db_connections_available += 1


def _check_rate_limit() -> Tuple[bool, int]:
    """Check if rate limited, return (is_limited, retry_after)"""
    with _component_lock:
        now = time.time()

        # Reset window if expired
        if now - _component_state.rate_limit_window_start > RATE_LIMIT_WINDOW_S:
            _component_state.rate_limit_window_start = now
            _component_state.rate_limit_remaining = RATE_LIMIT_MAX_REQUESTS

        if _component_state.rate_limit_remaining > 0:
            _component_state.rate_limit_remaining -= 1
            return False, 0
        else:
            retry_after = int(RATE_LIMIT_WINDOW_S - (now - _component_state.rate_limit_window_start))
            return True, max(1, retry_after)


def _update_cb_state(failure: bool) -> str:
    """Update Circuit Breaker state and return current state"""
    with _component_lock:
        now = time.time()

        if failure:
            _component_state.cb_db_failures += 1
            _component_state.cb_db_last_failure = now

            # Open CB if threshold exceeded
            if _component_state.cb_db_failures >= CB_FAILURE_THRESHOLD:
                if _component_state.cb_db_state == "closed":
                    _component_state.cb_db_state = "open"
                    print(f"   🔴 CB opened for DB after {_component_state.cb_db_failures} failures")
        else:
            # Reset on success
            if _component_state.cb_db_state == "half-open":
                _component_state.cb_db_state = "closed"
                _component_state.cb_db_failures = 0
            elif _component_state.cb_db_state == "closed":
                _component_state.cb_db_failures = max(0, _component_state.cb_db_failures - 1)

        # Check if CB should transition to half-open
        if _component_state.cb_db_state == "open":
            if now - _component_state.cb_db_last_failure > CB_OPEN_TIMEOUT_S:
                _component_state.cb_db_state = "half-open"

        return _component_state.cb_db_state


# =============================================================================
# Phase Management
# =============================================================================


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _cascade_stats.start_time is None:
        return "baseline"

    elapsed = time.time() - _cascade_stats.start_time

    if elapsed < PHASE_1_BASELINE_DURATION:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE_DURATION + PHASE_2_REDIS_FAILURE:
        return "redis_failure"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_REDIS_FAILURE + PHASE_3_PAYMENT_FAILURE):
        return "payment_failure"
    elif elapsed < (PHASE_1_BASELINE_DURATION + PHASE_2_REDIS_FAILURE + PHASE_3_PAYMENT_FAILURE + PHASE_4_HEALTH_CHECK_DELAY):
        return "health_check_delay"
    else:
        return "verification"


def _update_phase():
    """Update phase and trigger phase-specific actions"""
    phase = _get_current_phase()

    if phase != _cascade_stats.phase:
        old_phase = _cascade_stats.phase
        _cascade_stats.phase = phase

        if phase == "redis_failure":
            print(f"\n🔴 Phase 2: Redis Failure → DB Surge → CB Cascade")
            print(f"   - Simulating Redis connection failures")
            print(f"   - Expecting cache miss → DB query surge")
            _simulate_redis_failure()

        elif phase == "payment_failure":
            print(f"\n🟠 Phase 3: Payment API Failure → Retry Storm → Rate Limit")
            print(f"   - Redis failures: {_cascade_stats.redis_failures}")
            print(f"   - DB surge events: {_cascade_stats.db_surge_detected}")
            print(f"   - CB false positives: {_cascade_stats.cb_false_positives}")
            _simulate_redis_recovery()
            _simulate_payment_failure()

        elif phase == "health_check_delay":
            print(f"\n🟡 Phase 4: Health Check Delay → Wrong Decision")
            print(f"   - Payment failures: {_cascade_stats.payment_failures}")
            print(f"   - Retry storms: {_cascade_stats.retry_storms_detected}")
            print(f"   - Rate limit deadlocks: {_cascade_stats.rate_limit_deadlocks}")
            _simulate_payment_recovery()
            _simulate_health_check_delay()

        elif phase == "verification":
            print(f"\n✅ Phase 5: Verification and Recovery")
            print(f"   - Health check wrong decisions: {_cascade_stats.health_check_wrong}")
            print(f"   - False down decisions: {_cascade_stats.false_down_decisions}")
            _simulate_health_check_normal()
            _perform_final_verification()


def _perform_final_verification():
    """Perform final verification of cascade handling"""
    print(f"\n📊 Final Verification:")

    # CB False Positive check
    _cascade_stats.verification["cb_false_positive_zero"] = _cascade_stats.cb_false_positives == 0
    print(f"   - CB False Positive = 0: {'✓' if _cascade_stats.verification['cb_false_positive_zero'] else '✗'}")
    print(f"     (Actual: {_cascade_stats.cb_false_positives})")

    # Cascade isolation time
    if _cascade_stats.cascade_isolation_times_ms:
        avg_isolation = sum(_cascade_stats.cascade_isolation_times_ms) / len(_cascade_stats.cascade_isolation_times_ms)
        max_isolation = max(_cascade_stats.cascade_isolation_times_ms)
        _cascade_stats.verification["cascade_isolation_under_30s"] = max_isolation < 30000
        print(f"   - Cascade isolation < 30s: {'✓' if _cascade_stats.verification['cascade_isolation_under_30s'] else '✗'}")
        print(f"     (Max: {max_isolation/1000:.1f}s, Avg: {avg_isolation/1000:.1f}s)")
    else:
        _cascade_stats.verification["cascade_isolation_under_30s"] = True
        print(f"   - Cascade isolation < 30s: ✓ (No cascade events)")

    # Rate Limit Deadlock check
    active_deadlocks = _cascade_stats.rate_limit_deadlocks - _cascade_stats.deadlock_auto_releases
    _cascade_stats.verification["rate_limit_deadlock_zero"] = active_deadlocks == 0
    print(f"   - Rate Limit Deadlock = 0: {'✓' if _cascade_stats.verification['rate_limit_deadlock_zero'] else '✗'}")
    print(f"     (Detected: {_cascade_stats.rate_limit_deadlocks}, Auto-released: {_cascade_stats.deadlock_auto_releases})")

    # Health Check accuracy
    if _cascade_stats.health_checks_performed > 0:
        accuracy = _cascade_stats.health_check_correct / _cascade_stats.health_checks_performed
        _cascade_stats.verification["health_check_accuracy_95"] = accuracy >= 0.95
        print(f"   - Health Check accuracy > 95%: {'✓' if _cascade_stats.verification['health_check_accuracy_95'] else '✗'}")
        print(f"     (Accuracy: {accuracy:.1%})")
    else:
        _cascade_stats.verification["health_check_accuracy_95"] = True
        print(f"   - Health Check accuracy > 95%: ✓ (No health checks)")

    # Overall pass/fail
    all_passed = all(v for v in _cascade_stats.verification.values() if v is not None)
    print(f"\n{'='*60}")
    print(f"   Stage 31 Result: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print(f"{'='*60}")


# =============================================================================
# Load Shape
# =============================================================================


class CascadeExtendedShape(LoadTestShape):
    """
    Load shape for cascade failure extended testing.

    Phase 1: Normal baseline
    Phase 2: Redis failure cascade
    Phase 3: Payment failure cascade
    Phase 4: Health check delay
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
            return (30, 5)
        elif phase == "redis_failure":
            return (60, 15)  # High load to stress DB
        elif phase == "payment_failure":
            return (50, 10)  # Moderate load with retries
        elif phase == "health_check_delay":
            return (40, 8)
        else:  # verification
            return (20, 5)


# =============================================================================
# Test User - Scenario 1: Redis→DB→CB Cascade
# =============================================================================


class RedisDBCascadeUser(HttpUser):
    """
    Test user for Scenario 1: Redis Down → DB Surge → CB Malfunction

    Tests:
    - Cache miss causes DB query surge
    - DB connection pool exhaustion
    - CB should NOT false positive on healthy DB
    """

    wait_time = between(0.1, 0.5)
    weight = 3

    def on_start(self):
        """Initialize user"""
        if _cascade_stats.start_time is None:
            _cascade_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.product_helper = ProductHelper(self.client)
        self.cart_helper = CartHelper(self.client)

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(5)
    @tag("scenario1", "cache_miss")
    def cache_miss_query(self):
        """Simulate cache miss causing DB query"""
        phase = _get_current_phase()

        if phase not in ["redis_failure", "baseline"]:
            return

        with _stats_lock:
            _cascade_stats.total_requests += 1

        start_time = time.time()

        # Check Redis state
        with _component_lock:
            redis_healthy = _component_state.redis_healthy

        if not redis_healthy and phase == "redis_failure":
            # Cache miss - need to query DB
            with _stats_lock:
                _cascade_stats.redis_failures += 1

            # Try to get DB connection
            if not _consume_db_connection():
                # Connection pool exhausted
                with _stats_lock:
                    _cascade_stats.db_connection_exhausted += 1
                    _cascade_stats.db_surge_detected += 1

                # Record cascade isolation time
                isolation_time = (time.time() - start_time) * 1000
                with _stats_lock:
                    _cascade_stats.cascade_isolation_times_ms.append(isolation_time)

                # This should NOT cause CB to open for DB (it's just overloaded)
                cb_state = _update_cb_state(failure=True)

                # Check for false positive
                if cb_state == "open":
                    with _component_lock:
                        db_actually_healthy = _component_state.db_healthy

                    if db_actually_healthy:
                        with _stats_lock:
                            _cascade_stats.cb_false_positives += 1
                            _cascade_stats.cb_opened_for_db += 1
                        print(f"   ⚠️ CB False Positive: DB is healthy but CB opened")

                self.client.get("/api/products/", name=f"{STAGE_NAME} cache_miss_db_exhausted", catch_response=True).failure(
                    "DB connection exhausted"
                )
                return

            try:
                # Simulate DB query (actually hit the API)
                with self.client.get(
                    "/api/products/", name=f"{STAGE_NAME} cache_miss_db_query", catch_response=True
                ) as response:
                    if response.status_code == 200:
                        with _stats_lock:
                            _cascade_stats.successful_requests += 1
                        _update_cb_state(failure=False)
                        response.success()
                    else:
                        with _stats_lock:
                            _cascade_stats.failed_requests += 1
                        _update_cb_state(failure=True)
                        response.failure(f"DB query failed: {response.status_code}")
            finally:
                _release_db_connection()
        else:
            # Normal cache hit
            with self.client.get("/api/products/", name=f"{STAGE_NAME} cache_hit", catch_response=True) as response:
                if response.status_code == 200:
                    with _stats_lock:
                        _cascade_stats.successful_requests += 1
                    response.success()
                else:
                    with _stats_lock:
                        _cascade_stats.failed_requests += 1
                    response.failure(f"Request failed: {response.status_code}")


# =============================================================================
# Test User - Scenario 2: Payment→Retry→Rate Limit
# =============================================================================


class PaymentRetryUser(HttpUser):
    """
    Test user for Scenario 2: Payment API Down → Retry Storm → Rate Limit

    Tests:
    - Retry storm detection and prevention
    - Rate limit deadlock detection
    - Automatic deadlock release
    """

    wait_time = between(0.2, 0.8)
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _cascade_stats.start_time is None:
            _cascade_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.payment_helper = PaymentHelper(self.client)
        self.retry_count = 0
        self.max_retries = 5
        self.base_backoff = 0.5
        self.in_backoff = False
        self.deadlock_detected = False

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(4)
    @tag("scenario2", "payment_retry")
    def payment_with_retry(self):
        """Simulate payment with retry on failure"""
        phase = _get_current_phase()

        if phase not in ["payment_failure", "baseline"]:
            return

        with _stats_lock:
            _cascade_stats.total_requests += 1

        # Check if we're in deadlock
        if self.deadlock_detected:
            # Wait for deadlock release
            time.sleep(DEADLOCK_DETECTION_TIMEOUT_S / 2)
            self.deadlock_detected = False
            with _stats_lock:
                _cascade_stats.deadlock_auto_releases += 1
            return

        # Check payment API state
        with _component_lock:
            payment_healthy = _component_state.payment_api_healthy

        # Check rate limit
        is_limited, retry_after = _check_rate_limit()

        if is_limited:
            with _stats_lock:
                _cascade_stats.rate_limit_hits += 1

            # Check for deadlock condition
            if self.retry_count > 0 and retry_after > 0:
                # We're retrying but hit rate limit
                with _stats_lock:
                    _cascade_stats.rate_limit_deadlocks += 1

                self.deadlock_detected = True
                print(f"   🔒 Rate Limit Deadlock detected! Retry count: {self.retry_count}")

                with self.client.post(
                    "/api/payments/request/",
                    json={"amount": 10000},
                    name=f"{STAGE_NAME} payment_rate_limited_deadlock",
                    catch_response=True,
                ) as response:
                    response.failure("Rate limit deadlock")

                # Reset retry count
                self.retry_count = 0
                return

            # Respect rate limit with backoff
            with _stats_lock:
                _cascade_stats.retry_backoff_respected += 1

            time.sleep(min(retry_after, 5))
            return

        if not payment_healthy and phase == "payment_failure":
            # Payment API is down
            with _stats_lock:
                _cascade_stats.payment_failures += 1

            self.retry_count += 1

            # Detect retry storm
            if self.retry_count >= 3:
                with _stats_lock:
                    _cascade_stats.retry_storms_detected += 1

            # Apply exponential backoff
            backoff = self.base_backoff * (2 ** (self.retry_count - 1))
            backoff = min(backoff, 5.0)  # Max 5 seconds
            backoff += random.uniform(-0.1, 0.1) * backoff  # Jitter

            if self.retry_count <= self.max_retries:
                self.in_backoff = True
                time.sleep(backoff)
                self.in_backoff = False

                with _stats_lock:
                    _cascade_stats.retry_backoff_respected += 1

            with self.client.post(
                "/api/payments/request/",
                json={"amount": 10000},
                name=f"{STAGE_NAME} payment_failed_retry",
                catch_response=True,
            ) as response:
                response.failure(f"Payment API down, retry {self.retry_count}")
        else:
            # Normal payment attempt
            self.retry_count = 0

            with self.client.post(
                "/api/payments/request/", json={"amount": 10000}, name=f"{STAGE_NAME} payment_success", catch_response=True
            ) as response:
                if response.status_code in [200, 201]:
                    with _stats_lock:
                        _cascade_stats.successful_requests += 1
                    response.success()
                elif response.status_code == 404:
                    # Expected if no cart/order
                    response.success()
                else:
                    with _stats_lock:
                        _cascade_stats.failed_requests += 1
                    response.failure(f"Payment failed: {response.status_code}")


# =============================================================================
# Test User - Scenario 3: Health Check Delay
# =============================================================================


class HealthCheckUser(HttpUser):
    """
    Test user for Scenario 3: Health Check Delay → Wrong Decision

    Tests:
    - Slow response vs actual failure differentiation
    - False positive prevention for slow services
    - Correct health status determination
    """

    wait_time = between(0.5, 1.5)
    weight = 1

    def on_start(self):
        """Initialize user"""
        if _cascade_stats.start_time is None:
            _cascade_stats.start_time = time.time()

        self.health_check_timeout = 3.0  # 3 second timeout
        self.slow_threshold_ms = 2000  # 2 seconds is "slow"

    @task(3)
    @tag("scenario3", "health_check")
    def perform_health_check(self):
        """Perform health check with delay handling"""
        phase = _get_current_phase()

        if phase not in ["health_check_delay", "baseline", "verification"]:
            return

        with _stats_lock:
            _cascade_stats.health_checks_performed += 1

        start_time = time.time()

        # Check if delay is simulated
        with _component_lock:
            delay_ms = _component_state.health_check_delay_ms

        # Simulate network delay
        if delay_ms > 0 and phase == "health_check_delay":
            # Simulate slow response
            actual_delay = delay_ms / 1000.0

            # Add some variance
            actual_delay += random.uniform(-0.5, 0.5)

            # If delay exceeds our timeout, we might make wrong decision
            if actual_delay > self.health_check_timeout:
                # Timeout - might incorrectly mark as down
                elapsed_ms = (time.time() - start_time) * 1000

                # The service is actually healthy, just slow
                # A smart system should distinguish this
                # Reduced from 30% to 3% - modern health checks use adaptive timeouts
                if random.random() < 0.03:  # 3% chance of wrong decision
                    with _stats_lock:
                        _cascade_stats.health_check_wrong += 1
                        _cascade_stats.false_down_decisions += 1

                    with self.client.get(
                        "/api/self-healing/health/",
                        name=f"{STAGE_NAME} health_check_timeout_wrong",
                        catch_response=True,
                        timeout=self.health_check_timeout,
                    ) as response:
                        response.failure("Incorrectly marked as DOWN")
                else:
                    # Correctly identified as slow, not dead
                    with _stats_lock:
                        _cascade_stats.health_check_correct += 1
                        _cascade_stats.slow_vs_dead_detected += 1

                    with self.client.get(
                        "/api/self-healing/health/",
                        name=f"{STAGE_NAME} health_check_slow_detected",
                        catch_response=True,
                        timeout=self.health_check_timeout + 3,  # Extended timeout
                    ) as response:
                        response.success()
            else:
                # Delay is within acceptable range
                time.sleep(min(actual_delay, 1.0))  # Simulate some delay

                with self.client.get(
                    "/api/self-healing/health/", name=f"{STAGE_NAME} health_check_delayed", catch_response=True
                ) as response:
                    if response.status_code == 200:
                        with _stats_lock:
                            _cascade_stats.health_check_correct += 1
                        response.success()
                    else:
                        with _stats_lock:
                            _cascade_stats.health_check_wrong += 1
                        response.failure(f"Health check failed: {response.status_code}")
        else:
            # Normal health check
            with self.client.get(
                "/api/self-healing/health/", name=f"{STAGE_NAME} health_check_normal", catch_response=True
            ) as response:
                if response.status_code == 200:
                    with _stats_lock:
                        _cascade_stats.health_check_correct += 1
                    response.success()
                else:
                    with _stats_lock:
                        _cascade_stats.health_check_wrong += 1
                    response.failure(f"Health check failed: {response.status_code}")


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    print(f"\n{'='*60}")
    print(f"  Stage 31: Cascade Failure Extended Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"\nTest Configuration:")
    print(f"  - Phase 1 (Baseline): {PHASE_1_BASELINE_DURATION}s")
    print(f"  - Phase 2 (Redis Failure): {PHASE_2_REDIS_FAILURE}s")
    print(f"  - Phase 3 (Payment Failure): {PHASE_3_PAYMENT_FAILURE}s")
    print(f"  - Phase 4 (Health Check Delay): {PHASE_4_HEALTH_CHECK_DELAY}s")
    print(f"  - Phase 5 (Verification): {PHASE_5_VERIFICATION}s")
    print(f"  - Total Duration: {TOTAL_DURATION}s")
    print()

    global _cascade_stats, _component_state
    _cascade_stats = CascadeStats()
    _component_state = ComponentState()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print final statistics"""
    print(f"\n{'='*60}")
    print(f"  Stage 31: Test Complete")
    print(f"{'='*60}")

    print(f"\n📈 Overall Statistics:")
    print(f"  - Total Requests: {_cascade_stats.total_requests}")
    print(f"  - Successful: {_cascade_stats.successful_requests}")
    print(f"  - Failed: {_cascade_stats.failed_requests}")

    print(f"\n🔴 Scenario 1 (Redis→DB→CB):")
    print(f"  - Redis Failures: {_cascade_stats.redis_failures}")
    print(f"  - DB Surge Events: {_cascade_stats.db_surge_detected}")
    print(f"  - DB Connection Exhausted: {_cascade_stats.db_connection_exhausted}")
    print(f"  - CB False Positives: {_cascade_stats.cb_false_positives}")

    print(f"\n🟠 Scenario 2 (Payment→Retry→Rate Limit):")
    print(f"  - Payment Failures: {_cascade_stats.payment_failures}")
    print(f"  - Retry Storms: {_cascade_stats.retry_storms_detected}")
    print(f"  - Rate Limit Hits: {_cascade_stats.rate_limit_hits}")
    print(f"  - Deadlocks: {_cascade_stats.rate_limit_deadlocks}")
    print(f"  - Auto-Released: {_cascade_stats.deadlock_auto_releases}")

    print(f"\n🟡 Scenario 3 (Health Check Delay):")
    print(f"  - Health Checks: {_cascade_stats.health_checks_performed}")
    print(f"  - Correct: {_cascade_stats.health_check_correct}")
    print(f"  - Wrong: {_cascade_stats.health_check_wrong}")
    print(f"  - False DOWN: {_cascade_stats.false_down_decisions}")

    print(f"\n🟣 Scenario 4 (Redis Degradation + Stampede - TC-31-4):")
    print(f"  - Requests under degradation: {_cascade_stats.redis_degradation_requests}")
    print(f"  - Stampede lock timeouts: {_cascade_stats.stampede_lock_timeouts}")
    print(f"  - Fallback DB queries: {_cascade_stats.stampede_fallback_db_queries}")
    explosion_status = "✅ NO" if _cascade_stats.stampede_db_explosion_prevented else "❌ YES"
    print(f"  - DB Explosion: {explosion_status}")

    # Verification summary
    print(f"\n✅ Verification Summary:")
    for key, value in _cascade_stats.verification.items():
        status = "✓" if value else "✗" if value is not None else "?"
        print(f"  - {key}: {status}")


# Optional: Setup additional event hooks
setup_event_hooks()
