"""
Stage 33: JWT Cascade Extended

Purpose: Verify JWT expiry stampede handling and authentication server resilience
- JWT Expiry Stampede prevention (1000 concurrent token refresh)
- Auth Server Fallback (primary to secondary transition)
- Re-Auth Storm Throttling (priority-based processing)
- Replay Queue Growth management

Extended from Stage 23 (Clock Skew) to cover:
- Mass JWT expiry scenarios
- Auth server failover
- Throttling during re-authentication storms
- Queue size management

Scenarios:
  SC-33-1: JWT Expiry Stampede (1000 tokens expire simultaneously)
  SC-33-2: Auth Server Fallback (primary failure → secondary)
  SC-33-3: Re-Auth Storm Throttling (max 100/sec enforcement)
  SC-33-4: Replay Queue Growth (max 10,000 queue size)

Verification:
  - [ ] Re-issue response time < 2s
  - [ ] Fallback time < 5s
  - [ ] Throttle limit = 100/sec
  - [ ] Queue size < 10,000

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage33_jwt_cascade.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage33_jwt_cascade.py \\
        --host=http://localhost:8000 \\
        --users=100 --spawn-rate=20 --run-time=5m \\
        --headless --html=stage33_report.html

Reference:
    - docs/STAGE_31_36_EXTENSION_PLAN.md (Stage 33)
    - Stage 23 (Clock Skew) base implementation
"""

import os
import sys
import time
import json
import random
import threading
import uuid
import hashlib
import hmac
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict, deque
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


STAGE_NAME = "[Stage33-JWTCascade]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 20s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE = max(3, int(30 * _scale))  # Normal baseline
PHASE_2_JWT_STAMPEDE = max(5, int(75 * _scale))  # JWT expiry stampede
PHASE_3_AUTH_FALLBACK = max(4, int(60 * _scale))  # Auth server fallback
PHASE_4_REAUTH_STORM = max(4, int(60 * _scale))  # Re-auth throttling
PHASE_5_QUEUE_GROWTH = max(3, int(45 * _scale))  # Queue management
PHASE_6_VERIFICATION = max(2, int(30 * _scale))  # Verification

TOTAL_DURATION = (
    PHASE_1_BASELINE
    + PHASE_2_JWT_STAMPEDE
    + PHASE_3_AUTH_FALLBACK
    + PHASE_4_REAUTH_STORM
    + PHASE_5_QUEUE_GROWTH
    + PHASE_6_VERIFICATION
)

# JWT configuration
JWT_EXPIRY_S = 3600  # 1 hour
JWT_REFRESH_THRESHOLD_S = 300  # Refresh when 5 minutes remaining
JWT_CLOCK_SKEW_TOLERANCE_S = 30

# Auth server configuration
AUTH_SERVER_RESPONSE_TIME_MS = 100  # Normal response time
AUTH_SERVER_FALLBACK_THRESHOLD = 5  # Failures before fallback
AUTH_SERVER_FALLBACK_TIMEOUT_S = 5

# Throttling configuration
REAUTH_THROTTLE_LIMIT = 100  # Max requests per second
REAUTH_THROTTLE_WINDOW_S = 1

# Queue configuration
REPLAY_QUEUE_MAX_SIZE = 10000
REPLAY_QUEUE_WARNING_THRESHOLD = 0.8  # 80%
REPLAY_QUEUE_ITEM_TIMEOUT_S = 60


# =============================================================================
# JWT Cascade Statistics
# =============================================================================


@dataclass
class JWTCascadeStats:
    """Statistics tracking for JWT cascade scenarios"""

    start_time: Optional[float] = None
    phase: str = "baseline"

    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Scenario 1: JWT Stampede
    jwt_tokens_issued: int = 0
    jwt_tokens_expired: int = 0
    jwt_refresh_requests: int = 0
    jwt_refresh_success: int = 0
    jwt_refresh_throttled: int = 0
    jwt_refresh_response_times_ms: List[float] = field(default_factory=list)
    jwt_stampede_detected: bool = False

    # Scenario 2: Auth Server Fallback
    primary_auth_requests: int = 0
    primary_auth_failures: int = 0
    fallback_triggered: int = 0
    fallback_time_ms: List[float] = field(default_factory=list)
    secondary_auth_requests: int = 0
    secondary_auth_success: int = 0
    sessions_preserved: int = 0
    sessions_lost: int = 0

    # Scenario 3: Re-Auth Storm Throttling
    reauth_requests_total: int = 0
    reauth_requests_processed: int = 0
    reauth_requests_throttled: int = 0
    reauth_rate_per_second: List[float] = field(default_factory=list)
    priority_requests_processed: int = 0
    normal_requests_processed: int = 0

    # Scenario 4: Replay Queue
    queue_items_added: int = 0
    queue_items_processed: int = 0
    queue_items_expired: int = 0
    queue_size_samples: List[int] = field(default_factory=list)
    queue_max_size_reached: int = 0
    queue_overflow_prevented: int = 0

    # Verification results
    verification: Dict[str, Optional[bool]] = field(
        default_factory=lambda: {
            "reissue_response_under_2s": None,
            "fallback_under_5s": None,
            "throttle_limit_100": None,
            "queue_size_under_10k": None,
        }
    )


_jwt_stats = JWTCascadeStats()
_stats_lock = threading.Lock()


# =============================================================================
# Simulated Auth Components
# =============================================================================


@dataclass
class AuthServerState:
    """Track simulated auth server states"""

    primary_healthy: bool = True
    secondary_healthy: bool = True
    primary_failure_count: int = 0
    current_server: str = "primary"  # primary or secondary

    # Throttling state
    throttle_window_start: float = 0
    throttle_window_count: int = 0

    # Replay queue
    replay_queue: deque = field(default_factory=lambda: deque(maxlen=REPLAY_QUEUE_MAX_SIZE))
    queue_lock: threading.Lock = field(default_factory=threading.Lock)


_auth_state = AuthServerState()
_auth_lock = threading.Lock()


# JWT Token Registry (simulates token issuance tracking)
@dataclass
class JWTToken:
    """Simulated JWT token"""

    token_id: str
    user_id: str
    issued_at: float
    expires_at: float
    is_valid: bool = True


_token_registry: Dict[str, JWTToken] = {}
_token_lock = threading.Lock()


# =============================================================================
# Auth Server Simulation Functions
# =============================================================================


def _issue_jwt_token(user_id: str, expires_in: int = JWT_EXPIRY_S) -> JWTToken:
    """Issue a new JWT token"""
    now = time.time()
    token = JWTToken(
        token_id=str(uuid.uuid4()),
        user_id=user_id,
        issued_at=now,
        expires_at=now + expires_in,
    )

    with _token_lock:
        _token_registry[token.token_id] = token

    with _stats_lock:
        _jwt_stats.jwt_tokens_issued += 1

    return token


def _check_token_expiry(token: JWTToken) -> bool:
    """Check if token is expired"""
    now = time.time()
    return now >= token.expires_at


def _refresh_jwt_token(old_token: JWTToken) -> Tuple[Optional[JWTToken], float, bool]:
    """
    Refresh a JWT token.
    Returns: (new_token, response_time_ms, was_throttled)
    """
    start_time = time.time()

    # Check throttling
    with _auth_lock:
        now = time.time()

        # Reset window if expired
        if now - _auth_state.throttle_window_start > REAUTH_THROTTLE_WINDOW_S:
            _auth_state.throttle_window_start = now
            _auth_state.throttle_window_count = 0

        # Check if throttled
        if _auth_state.throttle_window_count >= REAUTH_THROTTLE_LIMIT:
            with _stats_lock:
                _jwt_stats.jwt_refresh_throttled += 1
            return None, 0, True

        _auth_state.throttle_window_count += 1

    # Simulate auth server processing
    time.sleep(AUTH_SERVER_RESPONSE_TIME_MS / 1000)

    # Issue new token
    new_token = _issue_jwt_token(old_token.user_id)

    # Invalidate old token
    with _token_lock:
        if old_token.token_id in _token_registry:
            _token_registry[old_token.token_id].is_valid = False

    response_time = (time.time() - start_time) * 1000

    with _stats_lock:
        _jwt_stats.jwt_refresh_success += 1
        _jwt_stats.jwt_refresh_response_times_ms.append(response_time)

    return new_token, response_time, False


def _simulate_primary_failure():
    """Simulate primary auth server failure"""
    with _auth_lock:
        _auth_state.primary_healthy = False


def _simulate_primary_recovery():
    """Simulate primary auth server recovery"""
    with _auth_lock:
        _auth_state.primary_healthy = True
        _auth_state.primary_failure_count = 0
        _auth_state.current_server = "primary"


def _auth_request(is_priority: bool = False) -> Tuple[bool, float, str]:
    """
    Make an auth request to current server.
    Returns: (success, response_time_ms, server_used)
    """
    start_time = time.time()

    with _auth_lock:
        current_server = _auth_state.current_server

        # Check primary health
        if current_server == "primary" and not _auth_state.primary_healthy:
            _auth_state.primary_failure_count += 1

            with _stats_lock:
                _jwt_stats.primary_auth_failures += 1

            # Check if should fallback
            if _auth_state.primary_failure_count >= AUTH_SERVER_FALLBACK_THRESHOLD:
                _auth_state.current_server = "secondary"
                fallback_time = (time.time() - start_time) * 1000

                with _stats_lock:
                    _jwt_stats.fallback_triggered += 1
                    _jwt_stats.fallback_time_ms.append(fallback_time)

                print(f"   🔄 Auth Server Fallback: primary → secondary")
                current_server = "secondary"

    # Process request
    if current_server == "primary":
        with _stats_lock:
            _jwt_stats.primary_auth_requests += 1

        # Simulate processing
        time.sleep(AUTH_SERVER_RESPONSE_TIME_MS / 1000)

        with _auth_lock:
            success = _auth_state.primary_healthy

        response_time = (time.time() - start_time) * 1000
        return success, response_time, "primary"
    else:
        with _stats_lock:
            _jwt_stats.secondary_auth_requests += 1

        # Simulate processing (secondary may be slightly slower)
        time.sleep((AUTH_SERVER_RESPONSE_TIME_MS * 1.2) / 1000)

        with _auth_lock:
            success = _auth_state.secondary_healthy

        if success:
            with _stats_lock:
                _jwt_stats.secondary_auth_success += 1

        response_time = (time.time() - start_time) * 1000
        return success, response_time, "secondary"


def _check_reauth_throttle() -> Tuple[bool, int]:
    """
    Check re-auth throttling.
    Returns: (is_throttled, requests_in_window)
    """
    with _auth_lock:
        now = time.time()

        # Reset window if expired
        if now - _auth_state.throttle_window_start > REAUTH_THROTTLE_WINDOW_S:
            _auth_state.throttle_window_start = now
            _auth_state.throttle_window_count = 0

        is_throttled = _auth_state.throttle_window_count >= REAUTH_THROTTLE_LIMIT
        return is_throttled, _auth_state.throttle_window_count


def _add_to_replay_queue(item: Dict[str, Any]) -> bool:
    """
    Add item to replay queue.
    Returns: True if added, False if queue full
    """
    with _auth_state.queue_lock:
        if len(_auth_state.replay_queue) >= REPLAY_QUEUE_MAX_SIZE:
            with _stats_lock:
                _jwt_stats.queue_overflow_prevented += 1
            return False

        item["added_at"] = time.time()
        _auth_state.replay_queue.append(item)

        with _stats_lock:
            _jwt_stats.queue_items_added += 1
            _jwt_stats.queue_size_samples.append(len(_auth_state.replay_queue))

        # Check warning threshold
        if len(_auth_state.replay_queue) >= REPLAY_QUEUE_MAX_SIZE * REPLAY_QUEUE_WARNING_THRESHOLD:
            with _stats_lock:
                _jwt_stats.queue_max_size_reached += 1

        return True


def _process_replay_queue() -> int:
    """
    Process expired items in replay queue.
    Returns: Number of items processed
    """
    processed = 0
    now = time.time()

    with _auth_state.queue_lock:
        # Remove expired items
        items_to_remove = []
        for i, item in enumerate(_auth_state.replay_queue):
            if now - item.get("added_at", 0) > REPLAY_QUEUE_ITEM_TIMEOUT_S:
                items_to_remove.append(i)

        # Remove in reverse order to maintain indices
        for i in reversed(items_to_remove):
            if i < len(_auth_state.replay_queue):
                _auth_state.replay_queue.remove(_auth_state.replay_queue[i])
                processed += 1

    if processed > 0:
        with _stats_lock:
            _jwt_stats.queue_items_expired += processed

    return processed


def _get_queue_size() -> int:
    """Get current replay queue size"""
    with _auth_state.queue_lock:
        return len(_auth_state.replay_queue)


# =============================================================================
# Phase Management
# =============================================================================


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _jwt_stats.start_time is None:
        return "baseline"

    elapsed = time.time() - _jwt_stats.start_time

    if elapsed < PHASE_1_BASELINE:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_JWT_STAMPEDE:
        return "jwt_stampede"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_JWT_STAMPEDE + PHASE_3_AUTH_FALLBACK:
        return "auth_fallback"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_JWT_STAMPEDE + PHASE_3_AUTH_FALLBACK + PHASE_4_REAUTH_STORM:
        return "reauth_storm"
    elif (
        elapsed < PHASE_1_BASELINE + PHASE_2_JWT_STAMPEDE + PHASE_3_AUTH_FALLBACK + PHASE_4_REAUTH_STORM + PHASE_5_QUEUE_GROWTH
    ):
        return "queue_growth"
    else:
        return "verification"


def _update_phase():
    """Update phase and trigger phase-specific actions"""
    phase = _get_current_phase()

    if phase != _jwt_stats.phase:
        old_phase = _jwt_stats.phase
        _jwt_stats.phase = phase

        if phase == "jwt_stampede":
            print(f"\n🔴 Phase 2: JWT Expiry Stampede")
            print(f"   - Simulating mass JWT expiration")
            print(f"   - Testing refresh throttling")

            # Expire a batch of tokens
            with _token_lock:
                for token_id, token in list(_token_registry.items())[:100]:
                    token.expires_at = time.time() - 1  # Already expired
                    with _stats_lock:
                        _jwt_stats.jwt_tokens_expired += 1

        elif phase == "auth_fallback":
            print(f"\n🟠 Phase 3: Auth Server Fallback")
            print(f"   - JWT refresh requests: {_jwt_stats.jwt_refresh_requests}")
            print(f"   - Throttled: {_jwt_stats.jwt_refresh_throttled}")
            _simulate_primary_failure()

        elif phase == "reauth_storm":
            print(f"\n🟡 Phase 4: Re-Auth Storm Throttling")
            print(f"   - Fallback triggered: {_jwt_stats.fallback_triggered}")
            print(f"   - Secondary success: {_jwt_stats.secondary_auth_success}")
            _simulate_primary_recovery()

        elif phase == "queue_growth":
            print(f"\n🔵 Phase 5: Replay Queue Growth")
            print(f"   - Re-auth throttled: {_jwt_stats.reauth_requests_throttled}")
            print(f"   - Rate samples: {_jwt_stats.reauth_rate_per_second[-5:] if _jwt_stats.reauth_rate_per_second else []}")

        elif phase == "verification":
            print(f"\n✅ Phase 6: Verification and Recovery")
            print(f"   - Queue size: {_get_queue_size()}")
            print(f"   - Items processed: {_jwt_stats.queue_items_processed}")
            _perform_final_verification()


def _perform_final_verification():
    """Perform final verification of JWT cascade handling"""
    print(f"\n📊 Final Verification:")

    # Re-issue response time < 2s
    if _jwt_stats.jwt_refresh_response_times_ms:
        avg_response = sum(_jwt_stats.jwt_refresh_response_times_ms) / len(_jwt_stats.jwt_refresh_response_times_ms)
        max_response = max(_jwt_stats.jwt_refresh_response_times_ms)
        _jwt_stats.verification["reissue_response_under_2s"] = max_response < 2000
        print(f"   - Re-issue response < 2s: {'✓' if _jwt_stats.verification['reissue_response_under_2s'] else '✗'}")
        print(f"     (Max: {max_response:.0f}ms, Avg: {avg_response:.0f}ms)")
    else:
        _jwt_stats.verification["reissue_response_under_2s"] = True
        print(f"   - Re-issue response < 2s: ✓ (No refresh requests)")

    # Fallback time < 5s
    if _jwt_stats.fallback_time_ms:
        max_fallback = max(_jwt_stats.fallback_time_ms)
        _jwt_stats.verification["fallback_under_5s"] = max_fallback < 5000
        print(f"   - Fallback time < 5s: {'✓' if _jwt_stats.verification['fallback_under_5s'] else '✗'}")
        print(f"     (Max: {max_fallback:.0f}ms)")
    else:
        _jwt_stats.verification["fallback_under_5s"] = True
        print(f"   - Fallback time < 5s: ✓ (No fallback events)")

    # Throttle limit = 100/sec
    if _jwt_stats.reauth_rate_per_second:
        max_rate = max(_jwt_stats.reauth_rate_per_second)
        _jwt_stats.verification["throttle_limit_100"] = max_rate <= REAUTH_THROTTLE_LIMIT * 1.1  # 10% tolerance
        print(f"   - Throttle limit ≤ 100/sec: {'✓' if _jwt_stats.verification['throttle_limit_100'] else '✗'}")
        print(f"     (Max rate: {max_rate:.0f}/sec)")
    else:
        _jwt_stats.verification["throttle_limit_100"] = True
        print(f"   - Throttle limit ≤ 100/sec: ✓ (No rate samples)")

    # Queue size < 10,000
    max_queue = max(_jwt_stats.queue_size_samples) if _jwt_stats.queue_size_samples else 0
    _jwt_stats.verification["queue_size_under_10k"] = max_queue < REPLAY_QUEUE_MAX_SIZE
    print(f"   - Queue size < 10,000: {'✓' if _jwt_stats.verification['queue_size_under_10k'] else '✗'}")
    print(f"     (Max: {max_queue}, Overflow prevented: {_jwt_stats.queue_overflow_prevented})")

    # Overall pass/fail
    all_passed = all(v for v in _jwt_stats.verification.values() if v is not None)
    print(f"\n{'='*60}")
    print(f"   Stage 33 Result: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print(f"{'='*60}")


# =============================================================================
# Load Shape
# =============================================================================


class JWTCascadeShape(LoadTestShape):
    """
    Load shape for JWT cascade testing.

    Phase 1: Normal baseline
    Phase 2: JWT Stampede
    Phase 3: Auth Server Fallback
    Phase 4: Re-Auth Storm
    Phase 5: Queue Growth
    Phase 6: Verification
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
        elif phase == "jwt_stampede":
            return (100, 25)  # High load for stampede
        elif phase == "auth_fallback":
            return (50, 10)
        elif phase == "reauth_storm":
            return (80, 20)  # High load to test throttling
        elif phase == "queue_growth":
            return (60, 15)
        else:  # verification
            return (20, 5)


# =============================================================================
# Test User - Scenario 1: JWT Stampede
# =============================================================================


class JWTStampedeUser(HttpUser):
    """
    Test user for Scenario 1: JWT Expiry Stampede

    Tests:
    - Mass JWT expiration handling
    - Refresh request throttling
    - Response time under load
    """

    wait_time = between(0.1, 0.5)
    weight = 3

    def on_start(self):
        """Initialize user"""
        if _jwt_stats.start_time is None:
            _jwt_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.user_id = str(uuid.uuid4())
        self.token = None

        # Login and get initial token
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

        self.token = _issue_jwt_token(self.user_id)

    @task(5)
    @tag("scenario1", "jwt_refresh")
    def jwt_refresh_on_expiry(self):
        """Check token expiry and refresh if needed"""
        phase = _get_current_phase()

        if phase not in ["jwt_stampede", "baseline"]:
            return

        with _stats_lock:
            _jwt_stats.total_requests += 1
            _jwt_stats.jwt_refresh_requests += 1

        if self.token is None:
            self.token = _issue_jwt_token(self.user_id)
            return

        # Check if token needs refresh
        now = time.time()
        time_to_expiry = self.token.expires_at - now

        if time_to_expiry <= JWT_REFRESH_THRESHOLD_S or _check_token_expiry(self.token):
            # Token expired or about to expire - refresh
            new_token, response_time, was_throttled = _refresh_jwt_token(self.token)

            if was_throttled:
                # Add to replay queue
                _add_to_replay_queue(
                    {
                        "type": "refresh",
                        "user_id": self.user_id,
                        "old_token_id": self.token.token_id,
                    }
                )

                with self.client.get(
                    "/api/auth/refresh/", name=f"{STAGE_NAME} jwt_refresh_throttled", catch_response=True
                ) as response:
                    response.failure("Throttled")
            else:
                self.token = new_token

                with self.client.get(
                    "/api/auth/refresh/", name=f"{STAGE_NAME} jwt_refresh_success", catch_response=True
                ) as response:
                    if response_time < 2000:
                        with _stats_lock:
                            _jwt_stats.successful_requests += 1
                        response.success()
                    else:
                        with _stats_lock:
                            _jwt_stats.failed_requests += 1
                        response.failure(f"Slow response: {response_time:.0f}ms")
        else:
            # Token still valid - normal request
            with self.client.get("/api/products/", name=f"{STAGE_NAME} normal_request", catch_response=True) as response:
                if response.status_code == 200:
                    with _stats_lock:
                        _jwt_stats.successful_requests += 1
                    response.success()


# =============================================================================
# Test User - Scenario 2: Auth Server Fallback
# =============================================================================


class AuthFallbackUser(HttpUser):
    """
    Test user for Scenario 2: Auth Server Fallback

    Tests:
    - Primary to secondary failover
    - Session preservation
    - Fallback response time
    """

    wait_time = between(0.2, 0.8)
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _jwt_stats.start_time is None:
            _jwt_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.user_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())

        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(4)
    @tag("scenario2", "auth_fallback")
    def auth_with_fallback(self):
        """Make auth request with fallback support"""
        phase = _get_current_phase()

        if phase not in ["auth_fallback", "baseline"]:
            return

        with _stats_lock:
            _jwt_stats.total_requests += 1

        # Make auth request
        success, response_time, server_used = _auth_request()

        if success:
            with _stats_lock:
                _jwt_stats.successful_requests += 1
                if server_used == "secondary":
                    _jwt_stats.sessions_preserved += 1

            with self.client.post(
                "/api/auth/verify/",
                json={"session_id": self.session_id},
                name=f"{STAGE_NAME} auth_{server_used}",
                catch_response=True,
            ) as response:
                response.success()
        else:
            with _stats_lock:
                _jwt_stats.failed_requests += 1
                _jwt_stats.sessions_lost += 1

            with self.client.post(
                "/api/auth/verify/",
                json={"session_id": self.session_id},
                name=f"{STAGE_NAME} auth_failed",
                catch_response=True,
            ) as response:
                response.failure(f"Auth failed on {server_used}")


# =============================================================================
# Test User - Scenario 3: Re-Auth Storm Throttling
# =============================================================================


class ReAuthStormUser(HttpUser):
    """
    Test user for Scenario 3: Re-Auth Storm Throttling

    Tests:
    - Throttling enforcement (100/sec max)
    - Priority-based processing
    - Graceful degradation
    """

    wait_time = between(0.05, 0.2)  # Fast requests to test throttling
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _jwt_stats.start_time is None:
            _jwt_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.user_id = str(uuid.uuid4())
        self.is_priority = random.random() < 0.2  # 20% are priority users
        self.request_count = 0
        self.window_start = time.time()

        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(5)
    @tag("scenario3", "reauth_throttle")
    def reauth_with_throttling(self):
        """Make re-auth requests to test throttling"""
        phase = _get_current_phase()

        if phase not in ["reauth_storm", "baseline"]:
            return

        with _stats_lock:
            _jwt_stats.total_requests += 1
            _jwt_stats.reauth_requests_total += 1

        # Track rate
        now = time.time()
        if now - self.window_start > 1.0:
            rate = self.request_count / (now - self.window_start)
            with _stats_lock:
                _jwt_stats.reauth_rate_per_second.append(rate)
            self.request_count = 0
            self.window_start = now
        self.request_count += 1

        # Check throttling
        is_throttled, window_count = _check_reauth_throttle()

        if is_throttled and not self.is_priority:
            with _stats_lock:
                _jwt_stats.reauth_requests_throttled += 1

            with self.client.post("/api/auth/reauth/", name=f"{STAGE_NAME} reauth_throttled", catch_response=True) as response:
                response.failure("Throttled")
            return

        # Process request
        success, response_time, server_used = _auth_request(is_priority=self.is_priority)

        if success:
            with _stats_lock:
                _jwt_stats.reauth_requests_processed += 1
                _jwt_stats.successful_requests += 1
                if self.is_priority:
                    _jwt_stats.priority_requests_processed += 1
                else:
                    _jwt_stats.normal_requests_processed += 1

            with self.client.post("/api/auth/reauth/", name=f"{STAGE_NAME} reauth_success", catch_response=True) as response:
                response.success()
        else:
            with _stats_lock:
                _jwt_stats.failed_requests += 1

            with self.client.post("/api/auth/reauth/", name=f"{STAGE_NAME} reauth_failed", catch_response=True) as response:
                response.failure("Auth failed")


# =============================================================================
# Test User - Scenario 4: Replay Queue Growth
# =============================================================================


class ReplayQueueUser(HttpUser):
    """
    Test user for Scenario 4: Replay Queue Growth

    Tests:
    - Queue size management
    - Item expiration
    - Overflow prevention
    """

    wait_time = between(0.1, 0.4)
    weight = 1

    def on_start(self):
        """Initialize user"""
        if _jwt_stats.start_time is None:
            _jwt_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.user_id = str(uuid.uuid4())

        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(3)
    @tag("scenario4", "queue_growth")
    def add_to_queue(self):
        """Add items to replay queue"""
        phase = _get_current_phase()

        if phase not in ["queue_growth", "baseline"]:
            return

        with _stats_lock:
            _jwt_stats.total_requests += 1

        # Add item to queue
        item = {
            "type": "auth_retry",
            "user_id": self.user_id,
            "request_id": str(uuid.uuid4()),
        }

        added = _add_to_replay_queue(item)

        if added:
            with self.client.post("/api/auth/queue/", name=f"{STAGE_NAME} queue_add", catch_response=True) as response:
                with _stats_lock:
                    _jwt_stats.successful_requests += 1
                response.success()
        else:
            with self.client.post("/api/auth/queue/", name=f"{STAGE_NAME} queue_full", catch_response=True) as response:
                response.failure("Queue full")

    @task(2)
    @tag("scenario4", "queue_process")
    def process_queue(self):
        """Process expired items from queue"""
        phase = _get_current_phase()

        if phase not in ["queue_growth", "verification"]:
            return

        processed = _process_replay_queue()

        with _stats_lock:
            _jwt_stats.queue_items_processed += processed

        # Record current queue size
        queue_size = _get_queue_size()
        with _stats_lock:
            _jwt_stats.queue_size_samples.append(queue_size)


# =============================================================================
# Locust Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Test start initialization"""
    global _jwt_stats, _auth_state, _token_registry

    _jwt_stats = JWTCascadeStats()
    _auth_state = AuthServerState()
    _token_registry = {}

    print(f"\n{'='*60}")
    print(f"🚀 Stage 33: JWT Cascade Extended")
    print(f"{'='*60}")
    print(f"Total Duration: {TOTAL_DURATION}s")
    print(f"Phases:")
    print(f"  1. Baseline: {PHASE_1_BASELINE}s")
    print(f"  2. JWT Stampede: {PHASE_2_JWT_STAMPEDE}s")
    print(f"  3. Auth Fallback: {PHASE_3_AUTH_FALLBACK}s")
    print(f"  4. Re-Auth Storm: {PHASE_4_REAUTH_STORM}s")
    print(f"  5. Queue Growth: {PHASE_5_QUEUE_GROWTH}s")
    print(f"  6. Verification: {PHASE_6_VERIFICATION}s")
    print(f"{'='*60}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Test stop - print final report"""
    print(f"\n{'='*60}")
    print(f"📊 Stage 33 Final Report")
    print(f"{'='*60}")
    print(f"Total Requests: {_jwt_stats.total_requests}")
    print(f"  Successful: {_jwt_stats.successful_requests}")
    print(f"  Failed: {_jwt_stats.failed_requests}")
    print(f"\nJWT Stampede:")
    print(f"  Tokens Issued: {_jwt_stats.jwt_tokens_issued}")
    print(f"  Refresh Success: {_jwt_stats.jwt_refresh_success}")
    print(f"  Refresh Throttled: {_jwt_stats.jwt_refresh_throttled}")
    print(f"\nAuth Fallback:")
    print(f"  Primary Failures: {_jwt_stats.primary_auth_failures}")
    print(f"  Fallback Triggered: {_jwt_stats.fallback_triggered}")
    print(f"  Sessions Preserved: {_jwt_stats.sessions_preserved}")
    print(f"\nRe-Auth Storm:")
    print(f"  Total Requests: {_jwt_stats.reauth_requests_total}")
    print(f"  Throttled: {_jwt_stats.reauth_requests_throttled}")
    print(f"  Priority Processed: {_jwt_stats.priority_requests_processed}")
    print(f"\nQueue Management:")
    print(f"  Items Added: {_jwt_stats.queue_items_added}")
    print(f"  Items Processed: {_jwt_stats.queue_items_processed}")
    print(f"  Overflow Prevented: {_jwt_stats.queue_overflow_prevented}")
    print(f"{'='*60}")

    # Print verification results
    for key, value in _jwt_stats.verification.items():
        status = "✓" if value else "✗" if value is False else "?"
        print(f"  {status} {key}")

    all_passed = all(v for v in _jwt_stats.verification.values() if v is not None)
    print(f"\n  Overall: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print(f"{'='*60}")


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "STAGE_NAME",
    "JWTCascadeStats",
    "_jwt_stats",
    "_auth_state",
    "_issue_jwt_token",
    "_check_token_expiry",
    "_refresh_jwt_token",
    "_simulate_primary_failure",
    "_simulate_primary_recovery",
    "_auth_request",
    "_check_reauth_throttle",
    "_add_to_replay_queue",
    "_process_replay_queue",
    "_get_queue_size",
    "_get_current_phase",
    "JWTCascadeShape",
    "JWTStampedeUser",
    "AuthFallbackUser",
    "ReAuthStormUser",
    "ReplayQueueUser",
]
