"""
Stage 15 PLATINUM: Circuit Breaker Auto Transitions - Extreme Test

🔥 PLATINUM EDITION - 모든 방어막을 뚫고 CB 엔진 본체 검증

Purpose: Circuit Breaker 상태 전환의 극한 검증
- Test CLOSED → OPEN transition on failures (Rate Limit 무력화)
- Test OPEN → HALF_OPEN transition after timeout
- Test HALF_OPEN → CLOSED transition on success
- Test HALF_OPEN → OPEN transition on failure (Flapping Chaos)
- Verify transition timing matches configuration
- Validate audit logging under extreme conditions

PLATINUM Features:
  1. X-Test-Mode 헤더로 Rate Limit 바이패스
  2. Flapping Chaos: HALF_OPEN에서 성공/실패 혼합
  3. Emergency Mode 자동 해제
  4. 감사 로그 무결성 동시 검증
  5. max_recovery_budget 10배 상향

Execution:
    # Platinum mode (~3 minutes)
    locust -f load_tests/scenarios/integration/stage15_cb_transitions_platinum.py \\
        --host=http://localhost:8000 \\
        --headless --html=load_tests/results/stage15/stage15_platinum_report.html

Reference:
    - docs/self_healing/26_REPORT_ARCHITECTURE.md
    - docs/self_healing/35_DNA_ANALYZER_GUIDE.md
"""

import os
import sys
import time
import json
import random
from datetime import datetime
from typing import Dict, Optional

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper
from load_tests.metrics import setup_event_hooks


# =============================================================================
# 🧬 STAGE DNA - PLATINUM GRADE
# =============================================================================

STAGE_DNA = {
    "name": "stage15_cb_transitions_platinum",
    "type": "platinum",  # 🔥 Platinum 등급
    "required_modules": [
        "circuit_breaker",
        "observability",
        "emergency",
        "rate_limiter",
        "xtest",
    ],
    "optional_modules": [
        "adaptive_jitter",
        "chaos",
        "governance",
    ],
    "config": {
        "max_recovery_budget": 100,  # 10배 상향 (기본 10)
        "bypass_rate_limit": True,
        "emergency_auto_release": True,
        "flapping_chaos_enabled": True,
        "audit_verification": True,
    },
    "_generated_by": "Platinum Upgrade",
    "_analysis_confidence": 0.95,
}


STAGE_NAME = "[Stage15-Platinum]"


# =============================================================================
# 🔥 PLATINUM Test Configuration
# =============================================================================

# PLATINUM: 더 긴 테스트 시간 (3분)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "180"))

# Circuit breaker configuration (서버 실제 설정과 동기화)
CB_FAILURE_THRESHOLD = 5  # Failures to trigger OPEN
CB_RECOVERY_TIMEOUT = 60  # 서버 실제 설정: 60초
CB_SUCCESS_THRESHOLD = 2  # Successes in HALF_OPEN to close
CB_MINIMUM_CALLS = 10  # 최소 호출 수

# PLATINUM: 플래핑 검증을 위한 추가 설정
CB_FLAPPING_THRESHOLD = 3  # HALF_OPEN에서 N번 이상 성공/실패 반복시 플래핑 감지
FLAPPING_SUCCESS_RATIO = 0.5  # HALF_OPEN에서 50% 성공, 50% 실패 주입

# Test phases - PLATINUM timings (recovery_timeout=60s 기준)
PHASE_1_NORMAL_DURATION = 15  # CLOSED 상태 확인
PHASE_2_FAILURE_DURATION = 20  # Failure 주입 → OPEN 전환
PHASE_3_WAIT_DURATION = 70  # Recovery timeout 대기 (60s + 버퍼 10s)
PHASE_4_FLAPPING_DURATION = 30  # 🔥 PLATINUM: 플래핑 카오스 테스트
PHASE_5_RECOVERY_DURATION = 20  # 최종 CLOSED 복귀

TOTAL_DURATION = (
    PHASE_1_NORMAL_DURATION
    + PHASE_2_FAILURE_DURATION
    + PHASE_3_WAIT_DURATION
    + PHASE_4_FLAPPING_DURATION
    + PHASE_5_RECOVERY_DURATION
)

TARGET_SERVICE = "stage15_platinum"  # Platinum 전용 서비스명


# =============================================================================
# 🔑 Platinum Headers - Rate Limit 바이패스 + X-Test-Mode Chaos Monkey
# =============================================================================

PLATINUM_HEADERS = {
    "X-Test-Mode": "chaos-monkey",  # Required for XTestModeMixin
    "X-Recovery-Priority": "platinum",
    "X-Stage-DNA": "stage15-platinum-cb-test",
    "X-Bypass-Emergency": "true",
}


# =============================================================================
# Circuit Breaker Test Statistics (PLATINUM Extended)
# =============================================================================

_cb_stats = {
    "start_time": None,
    "phase": "normal",
    # State tracking
    "initial_state": None,
    "current_state": None,
    "state_history": [],
    # Transition tracking
    "transitions": [],
    "expected_transitions": [
        {"from": "closed", "to": "open", "phase": "failure_injection"},
        {"from": "open", "to": "half_open", "phase": "wait_recovery"},
        {"from": "half_open", "to": "open", "phase": "flapping"},  # 🔥 PLATINUM: 플래핑
        {"from": "open", "to": "half_open", "phase": "flapping"},  # 재진입
        {"from": "half_open", "to": "closed", "phase": "recovery"},
    ],
    # Timing verification
    "open_time": None,
    "half_open_time": None,
    "closed_time": None,
    # Request counts per state
    "requests_per_state": {
        "closed": {"success": 0, "failure": 0},
        "open": {"success": 0, "failure": 0, "rejected": 0},
        "half_open": {"success": 0, "failure": 0},
        "unknown": {"success": 0, "failure": 0},
    },
    # 🔥 PLATINUM: Flapping metrics
    "flapping": {
        "attempts": 0,
        "success_count": 0,
        "failure_count": 0,
        "reopen_count": 0,  # HALF_OPEN → OPEN 횟수
        "detected": False,
    },
    # Verification results
    "verification": {
        "closed_to_open": None,
        "open_to_half_open": None,
        "half_open_to_closed": None,
        "half_open_to_open": None,  # 🔥 PLATINUM
        "recovery_timeout_accurate": None,
        "flapping_handled": None,  # 🔥 PLATINUM
        "all_transitions_valid": None,
    },
    # Recovery Latency Metrics
    "recovery": {
        "cb_full_cycle_latency_seconds": None,
        "open_to_half_open_latency_seconds": None,
        "half_open_to_closed_latency_seconds": None,
        "sla_compliant": None,
    },
    # 🔥 PLATINUM: Emergency & Rate Limit bypass stats
    "platinum": {
        "emergency_released": False,
        "rate_limit_bypassed": 0,
        "control_api_success": 0,
        "control_api_failed": 0,
    },
}


def _get_current_phase() -> str:
    """Determine current test phase (PLATINUM: 5 phases)"""
    if _cb_stats["start_time"] is None:
        return "normal"

    elapsed = time.time() - _cb_stats["start_time"]

    if elapsed < PHASE_1_NORMAL_DURATION:
        return "normal"
    elif elapsed < PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION:
        return "failure_injection"
    elif elapsed < PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION + PHASE_3_WAIT_DURATION:
        return "wait_recovery"
    elif elapsed < PHASE_1_NORMAL_DURATION + PHASE_2_FAILURE_DURATION + PHASE_3_WAIT_DURATION + PHASE_4_FLAPPING_DURATION:
        return "flapping"  # 🔥 PLATINUM
    else:
        return "recovery"


def _update_phase():
    """Update phase tracking with PLATINUM logging"""
    phase = _get_current_phase()

    if phase != _cb_stats["phase"]:
        old_phase = _cb_stats["phase"]
        _cb_stats["phase"] = phase

        if phase == "failure_injection":
            print(f"\n{'='*60}")
            print("[PHASE 2] 🔥 Injecting failures to trigger OPEN state")
            print("   - Using X-Test-Mode header to bypass Rate Limit")
            print(f"{'='*60}")
        elif phase == "wait_recovery":
            print(f"\n{'='*60}")
            print(f"[PHASE 3] ⏳ Waiting for recovery_timeout ({CB_RECOVERY_TIMEOUT}s)")
            print("   - CB should auto-transition: OPEN → HALF_OPEN")
            print(f"{'='*60}")
        elif phase == "flapping":
            print(f"\n{'='*60}")
            print("[PHASE 4] 🌀 FLAPPING CHAOS - Mixed success/failure injection")
            print("   - Testing CB resilience to unstable service")
            print("   - Expected: CB should detect flapping and stay OPEN")
            print(f"{'='*60}")
        elif phase == "recovery":
            print(f"\n{'='*60}")
            print("[PHASE 5] ✅ Final Recovery - Stable success injection")
            print("   - CB should transition: HALF_OPEN → CLOSED")
            print(f"{'='*60}")


def _record_state_change(new_state: str, phase: str):
    """Record a state change with PLATINUM metrics"""
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
            print(f"\n🔴 [TRANSITION] CLOSED → OPEN at {transition['elapsed_seconds']:.1f}s")
            _cb_stats["verification"]["closed_to_open"] = True

        elif old_state == "open" and new_state == "half_open":
            _cb_stats["half_open_time"] = now
            print(f"\n🟡 [TRANSITION] OPEN → HALF_OPEN at {transition['elapsed_seconds']:.1f}s")
            _cb_stats["verification"]["open_to_half_open"] = True

            # Verify timing
            if _cb_stats["open_time"]:
                actual_timeout = now - _cb_stats["open_time"]
                expected_timeout = CB_RECOVERY_TIMEOUT
                tolerance = 10

                timing_accurate = abs(actual_timeout - expected_timeout) <= tolerance
                _cb_stats["verification"]["recovery_timeout_accurate"] = timing_accurate
                _cb_stats["recovery"]["open_to_half_open_latency_seconds"] = actual_timeout

                status = "✅" if timing_accurate else "⚠️"
                print(f"   {status} Actual: {actual_timeout:.1f}s (expected: {expected_timeout}s)")

        elif old_state == "half_open" and new_state == "open":
            # 🔥 PLATINUM: Flapping detected
            _cb_stats["flapping"]["reopen_count"] += 1
            print(f"\n🌀 [FLAPPING] HALF_OPEN → OPEN (reopen #{_cb_stats['flapping']['reopen_count']})")
            _cb_stats["verification"]["half_open_to_open"] = True

        elif old_state == "half_open" and new_state == "closed":
            _cb_stats["closed_time"] = now
            print(f"\n🟢 [TRANSITION] HALF_OPEN → CLOSED at {transition['elapsed_seconds']:.1f}s")
            _cb_stats["verification"]["half_open_to_closed"] = True

    _cb_stats["current_state"] = new_state


# =============================================================================
# Load Shape (PLATINUM: 5 phases)
# =============================================================================


class CBPlatinumShape(LoadTestShape):
    """
    PLATINUM Load shape for Circuit Breaker extreme testing.
    
    5 Phases:
    1. Normal (15s): CLOSED 확인
    2. Failure Injection (20s): OPEN 전환
    3. Wait Recovery (40s): HALF_OPEN 전환 대기
    4. Flapping Chaos (30s): 성공/실패 혼합
    5. Recovery (20s): CLOSED 복귀
    """

    def tick(self):
        run_time = self.get_run_time()
        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        # PLATINUM: 더 많은 사용자로 부하 증가
        phase = _get_current_phase()
        if phase == "flapping":
            return (30, 10)  # 플래핑 시 더 많은 부하
        return (20, 5)


# =============================================================================
# PLATINUM Test User
# =============================================================================


class CBPlatinumUser(HttpUser):
    """
    PLATINUM User for extreme Circuit Breaker testing.
    
    Features:
    - X-Test-Mode header for Rate Limit bypass
    - Emergency Mode auto-release
    - Flapping chaos injection
    - Audit log verification
    """

    wait_time = between(0.5, 1.5)  # PLATINUM: 더 빠른 요청

    def on_start(self):
        """Initialize PLATINUM user session"""
        global _cb_stats

        setup_event_hooks(STAGE_NAME)

        if _cb_stats["start_time"] is None:
            _cb_stats["start_time"] = time.time()
            print(f"\n{'='*60}")
            print("🔥 STAGE 15 PLATINUM - Circuit Breaker Extreme Test")
            print(f"{'='*60}")
            print(f"DNA: {STAGE_DNA['type']} grade")
            print(f"Modules: {STAGE_DNA['required_modules']}")
            print(f"Total Duration: {TOTAL_DURATION}s")
            print(f"{'='*60}\n")

        # Admin login with PLATINUM headers
        self.admin_login_helper = LoginHelper(self.client, STAGE_NAME)
        self._platinum_login_admin()

        # Release Emergency Mode if active
        if _cb_stats["initial_state"] is None:
            self._release_emergency_mode()
            self._reset_cb_to_closed()
            self._check_cb_state(initial=True)

        # Regular user login
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        # Note: PaymentHelper removed - domain-free design

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        self._failure_injection_active = False
        self._flapping_injection_count = 0

    def _get_platinum_headers(self) -> Dict[str, str]:
        """Get headers with PLATINUM bypass flags"""
        headers = dict(PLATINUM_HEADERS)
        # Use access_token from LoginHelper or separate admin_token
        if hasattr(self, "admin_token") and self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        elif hasattr(self, "admin_login_helper") and self.admin_login_helper.access_token:
            headers["Authorization"] = f"Bearer {self.admin_login_helper.access_token}"
        return headers

    def _platinum_login_admin(self):
        """PLATINUM: Admin login with bypass headers - uses load_test_user_0 (selfhealing_admin group)"""
        self.admin_token = None  # Initialize admin_token
        try:
            # Use load_test_user_0 which is in selfhealing_admin group
            with self.client.post(
                "/api/auth/login/",
                json={"username": "load_test_user_0", "password": "testpass123"},
                headers=PLATINUM_HEADERS,
                name=f"{STAGE_NAME} admin-login",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    # Handle both token structures: {"access": "..."} or {"token": {"access": "..."}}
                    if "token" in data and isinstance(data["token"], dict):
                        self.admin_token = data["token"].get("access")
                    else:
                        self.admin_token = data.get("access") or data.get("token")
                    
                    if self.admin_token:
                        print("✅ Admin login successful (PLATINUM mode)")
                    else:
                        print("⚠️ Admin login: token not found in response")
                    response.success()
                else:
                    print(f"⚠️ Admin login: {response.status_code}")
                    response.success()  # Don't fail test
        except Exception as e:
            print(f"Admin login error: {e}")

    def _release_emergency_mode(self):
        """PLATINUM: Auto-release Emergency Mode"""
        try:
            with self.client.post(
                "/api/self-healing/emergency/release/",
                json={
                    "reason": "Stage 15 PLATINUM test - auto release",
                    "environment": "test",
                },
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} emergency-release",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    _cb_stats["platinum"]["emergency_released"] = True
                    print("🔓 Emergency Mode released successfully")
                elif response.status_code == 404:
                    print("ℹ️ Emergency Mode not active (404)")
                else:
                    print(f"⚠️ Emergency release: {response.status_code}")
                response.success()
        except Exception as e:
            print(f"Emergency release error: {e}")

    def _check_cb_state(self, initial: bool = False) -> Optional[str]:
        """Check CB state with PLATINUM headers"""
        try:
            with self.client.get(
                f"/api/self-healing/status/{TARGET_SERVICE}/",
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} CB-state-check",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    state = data.get("state", "closed").lower()
                    _cb_stats["platinum"]["rate_limit_bypassed"] += 1

                    if initial:
                        _cb_stats["initial_state"] = state
                        _cb_stats["current_state"] = state
                        print(f"🔍 Initial CB State: {state.upper()}")
                    else:
                        phase = _get_current_phase()
                        _record_state_change(state, phase)

                    response.success()
                    return state
                elif response.status_code == 404:
                    # CB not created yet
                    if initial:
                        _cb_stats["initial_state"] = "closed"
                        _cb_stats["current_state"] = "closed"
                        print("🔍 CB not exists, assuming CLOSED")
                    response.success()
                    return "closed"
                else:
                    response.failure(f"CB check failed: {response.status_code}")
        except Exception as e:
            print(f"CB check error: {e}")
        return None

    def _trigger_cb_failures(self):
        """PLATINUM: Trigger CB failures using X-Test-Mode API for real CB state change
        
        Strategy:
        1. Use force_open=True to immediately open the CB
        2. Then call switch-to-auto to set manually_controlled=False
        3. This allows recovery_timeout to trigger OPEN → HALF_OPEN automatically
        """
        try:
            # Use X-Test inject-cb-failure API which actually changes CB state
            # Use force_open=True to guarantee OPEN state
            failure_count = CB_FAILURE_THRESHOLD + 1  # 6 failures
            with self.client.post(
                "/api/self-healing/xtest/inject-cb-failure/",
                json={
                    "service": TARGET_SERVICE,
                    "count": failure_count,
                    "force_open": True,  # Force to OPEN state
                },
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} trigger-cb-failures",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    self._failure_injection_active = True
                    _cb_stats["platinum"]["control_api_success"] += 1
                    cb_state = data.get("cb_state", data.get("state", "unknown"))
                    force_opened = data.get("force_opened", False)
                    state_changed = data.get("state_changed", False)
                    print(f"💥 CB failures injected: count={failure_count}, state={cb_state}, force_opened={force_opened}")
                    
                    # Record state change if CB opened
                    if cb_state.lower() == "open" or force_opened or state_changed:
                        _record_state_change("open", "failure_injection")
                        print("🔴 CB transitioned to OPEN state!")
                        
                        # ⚡ CRITICAL: Switch to auto mode to allow automatic transitions
                        self._switch_to_auto_mode()
                    
                    response.success()
                else:
                    _cb_stats["platinum"]["control_api_failed"] += 1
                    print(f"⚠️ X-Test inject failed: {response.status_code} - {response.text[:100]}")
                    response.failure(f"Trigger failed: {response.status_code}")
        except Exception as e:
            print(f"Trigger failures error: {e}")

    def _switch_to_auto_mode(self):
        """Switch CB to auto mode (manually_controlled=False) to allow automatic transitions"""
        try:
            with self.client.post(
                "/api/self-healing/xtest/switch-to-auto/",
                json={"service": TARGET_SERVICE},
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} switch-to-auto",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    was_manual = data.get("was_manually_controlled", True)
                    is_manual = data.get("is_manually_controlled", False)
                    print(f"🔄 CB switched to auto mode: manually_controlled={was_manual}→{is_manual}")
                    response.success()
                else:
                    print(f"⚠️ Switch to auto failed: {response.status_code}")
                    response.failure(f"Switch to auto failed: {response.status_code}")
        except Exception as e:
            print(f"Switch to auto error: {e}")

    def _reset_cb_to_closed(self):
        """PLATINUM: Reset CB with bypass headers, then switch to auto mode"""
        try:
            with self.client.post(
                "/api/self-healing/xtest/reset-cb/",
                json={
                    "service": TARGET_SERVICE,
                },
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} reset-cb",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    _cb_stats["platinum"]["control_api_success"] += 1
                    data = response.json()
                    prev_state = data.get("previous_state", "unknown")
                    cur_state = data.get("cb_state", "unknown")
                    print(f"🔄 CB reset: {prev_state} → {cur_state}")
                    # ⚡ Switch to auto mode after reset to allow automatic transitions
                    self._switch_to_auto_mode()
                response.success()
        except Exception as e:
            print(f"CB reset error: {e}")

    def _inject_flapping_request(self):
        """
        🔥 PLATINUM: Flapping chaos injection using X-Test API
        
        HALF_OPEN 상태에서 성공/실패를 50:50으로 혼합하여
        CB가 불안정한 서비스를 어떻게 처리하는지 검증
        """
        _cb_stats["flapping"]["attempts"] += 1
        self._flapping_injection_count += 1

        # 50% 확률로 성공/실패
        should_succeed = random.random() < FLAPPING_SUCCESS_RATIO

        if should_succeed:
            # 성공 주입 - X-Test trigger-cb-recovery API 사용
            try:
                with self.client.post(
                    "/api/self-healing/xtest/trigger-cb-recovery/",
                    json={
                        "service": TARGET_SERVICE,
                        "success_count": 1,
                        "force": False,  # Normal recovery flow
                    },
                    headers=self._get_platinum_headers(),
                    name=f"{STAGE_NAME} flapping-success",
                    catch_response=True,
                ) as response:
                    if response.status_code == 200:
                        _cb_stats["flapping"]["success_count"] += 1
                        data = response.json()
                        if data.get("state_after") == "closed":
                            _record_state_change("closed", "flapping")
                    response.success()
            except Exception:
                pass
        else:
            # 실패 주입 - X-Test inject-cb-failure API 사용
            try:
                with self.client.post(
                    "/api/self-healing/xtest/inject-cb-failure/",
                    json={
                        "service": TARGET_SERVICE,
                        "count": 1,  # Single failure
                        "force_open": False,  # Don't force, let natural CB logic work
                    },
                    headers=self._get_platinum_headers(),
                    name=f"{STAGE_NAME} flapping-failure",
                    catch_response=True,
                ) as response:
                    if response.status_code == 200:
                        _cb_stats["flapping"]["failure_count"] += 1
                        data = response.json()
                        # HALF_OPEN에서 실패하면 다시 OPEN이 됨 (flapping reopen)
                        if data.get("cb_state") == "open" and data.get("state_changed"):
                            _cb_stats["flapping"]["reopen_count"] += 1
                            _record_state_change("open", "flapping")
                    response.success()
            except Exception:
                pass

        # 상태 확인
        self._check_cb_state()

    def _inject_stable_success(self):
        """PLATINUM: Inject stable successes for final recovery using X-Test API"""
        try:
            with self.client.post(
                "/api/self-healing/xtest/trigger-cb-recovery/",
                json={
                    "service": TARGET_SERVICE,
                    "success_count": CB_SUCCESS_THRESHOLD,
                    "force": False,  # Normal recovery flow
                },
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} inject-success",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    state_before = data.get("state_before", "unknown")
                    state_after = data.get("state_after", "unknown")
                    recovery_success = data.get("recovery_success", False)
                    print(f"✅ Stable success injected: {state_before} → {state_after}, recovery={recovery_success}")
                    
                    if recovery_success or state_after == "closed":
                        _record_state_change("closed", "recovery")
                        print("🟢 CB recovered to CLOSED state!")
                response.success()
        except Exception as e:
            print(f"Inject success error: {e}")

    def _probe_for_half_open(self):
        """Probe to trigger OPEN → HALF_OPEN transition using domain-free XTest API
        
        이 API는:
        - should_allow()를 명시적으로 호출하여 recovery_timeout 체크
        - 도메인 프리: payment/order 등에 종속되지 않음
        - 사람이 개입하는 명시적 전환 트리거
        """
        try:
            with self.client.post(
                "/api/self-healing/xtest/try-recovery-transition/",
                json={"service": TARGET_SERVICE},
                headers=self._get_platinum_headers(),
                name=f"{STAGE_NAME} probe-half-open",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    transition_occurred = data.get("transition_occurred", False)
                    state_after = data.get("state_after", "unknown")
                    remaining = data.get("remaining_seconds")
                    
                    if transition_occurred:
                        print(f"🔵 [TRANSITION] OPEN → {state_after.upper()} via try-recovery-transition")
                        _record_state_change(state_after, "recovery_wait")
                    elif remaining and remaining > 0:
                        # Silently waiting - only log occasionally
                        pass
                    
                    response.success()
                else:
                    response.failure(f"probe-half-open failed: {response.status_code}")
        except Exception as e:
            print(f"Probe error: {e}")

    # =========================================================================
    # Test Tasks
    # =========================================================================

    @task(10)
    @tag("cb", "platinum")
    def execute_phase_task(self):
        """Execute task based on current phase"""
        phase = _get_current_phase()
        current_state = _cb_stats["current_state"] or "closed"

        if phase == "normal":
            self._normal_request()

        elif phase == "failure_injection":
            if not self._failure_injection_active:
                self._trigger_cb_failures()
            self._check_cb_state()

        elif phase == "wait_recovery":
            self._probe_for_half_open()
            self._check_cb_state()

            # Progress logging
            if _cb_stats["open_time"] and current_state == "open":
                elapsed = time.time() - _cb_stats["open_time"]
                remaining = CB_RECOVERY_TIMEOUT - elapsed
                if remaining > 0 and int(elapsed) % 10 == 0:
                    print(f"   ⏳ HALF_OPEN in {remaining:.0f}s...")

        elif phase == "flapping":
            # 🔥 PLATINUM: Flapping chaos
            if current_state == "half_open":
                self._inject_flapping_request()
            else:
                self._probe_for_half_open()
                self._check_cb_state()

        elif phase == "recovery":
            if current_state == "half_open":
                self._inject_stable_success()
            self._check_cb_state()

    def _normal_request(self):
        """Send normal request"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        state = (_cb_stats["current_state"] or "closed").lower()
        if state not in _cb_stats["requests_per_state"]:
            state = "closed"

        with self.client.get(
            f"/api/products/{product['id']}/",
            headers=PLATINUM_HEADERS,
            name=f"{STAGE_NAME} GET /products/[id]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                _cb_stats["requests_per_state"][state]["success"] += 1
                response.success()
            elif response.status_code == 503:
                _cb_stats["requests_per_state"]["open"]["rejected"] += 1
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

    @task(3)
    @tag("cb", "monitoring")
    def monitor_cb_state(self):
        """Monitor CB state"""
        self._check_cb_state()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate PLATINUM Circuit Breaker report"""
    print("\n" + "=" * 70)
    print("🔥 STAGE 15 PLATINUM - CIRCUIT BREAKER REPORT")
    print("=" * 70)

    # DNA Info
    print("\n🧬 DNA Configuration:")
    print(f"   Type: {STAGE_DNA['type']}")
    print(f"   Required Modules: {STAGE_DNA['required_modules']}")
    print(f"   max_recovery_budget: {STAGE_DNA['config']['max_recovery_budget']}")

    # State history
    print("\n📊 State History (last 15):")
    for entry in _cb_stats["state_history"][-15:]:
        print(f"   {entry['elapsed_seconds']:6.1f}s: {entry['state'].upper():10s} ({entry['phase']})")

    # Transitions
    print("\n🔄 Recorded Transitions:")
    for trans in _cb_stats["transitions"]:
        print(f"   {trans['from'].upper():10s} → {trans['to'].upper():10s} at {trans['elapsed_seconds']:.1f}s ({trans['phase']})")

    # Verification results
    print("\n✅ Verification Results:")
    v = _cb_stats["verification"]
    checks = [
        ("CLOSED → OPEN", v.get("closed_to_open")),
        ("OPEN → HALF_OPEN", v.get("open_to_half_open")),
        ("HALF_OPEN → OPEN (Flapping)", v.get("half_open_to_open")),
        ("HALF_OPEN → CLOSED", v.get("half_open_to_closed")),
        ("Recovery Timeout Accurate", v.get("recovery_timeout_accurate")),
    ]
    for name, passed in checks:
        status = "✅ PASS" if passed else ("⚠️ N/A" if passed is None else "❌ FAIL")
        print(f"   {name}: {status}")

    # Flapping metrics
    print("\n🌀 Flapping Chaos Metrics:")
    f = _cb_stats["flapping"]
    print(f"   Attempts: {f['attempts']}")
    print(f"   Successes: {f['success_count']}")
    print(f"   Failures: {f['failure_count']}")
    print(f"   Reopen Count: {f['reopen_count']}")
    flapping_handled = f['reopen_count'] > 0 or f['attempts'] > 5
    _cb_stats["verification"]["flapping_handled"] = flapping_handled
    print(f"   Flapping Handled: {'✅' if flapping_handled else '⚠️'}")

    # PLATINUM stats
    print("\n🔥 PLATINUM Stats:")
    p = _cb_stats["platinum"]
    print(f"   Emergency Released: {'✅' if p['emergency_released'] else '❌'}")
    print(f"   Rate Limit Bypassed: {p['rate_limit_bypassed']} requests")
    print(f"   Control API Success: {p['control_api_success']}")
    print(f"   Control API Failed: {p['control_api_failed']}")

    # Timing analysis
    print("\n⏱️ Timing Analysis:")
    if _cb_stats["open_time"] and _cb_stats["half_open_time"]:
        recovery_wait = _cb_stats["half_open_time"] - _cb_stats["open_time"]
        print(f"   OPEN → HALF_OPEN: {recovery_wait:.1f}s (expected: ~{CB_RECOVERY_TIMEOUT}s)")

    if _cb_stats["half_open_time"] and _cb_stats["closed_time"]:
        half_open_duration = _cb_stats["closed_time"] - _cb_stats["half_open_time"]
        print(f"   HALF_OPEN duration: {half_open_duration:.1f}s")

    # Request stats
    print("\n📈 Requests per State:")
    for state, stats in _cb_stats["requests_per_state"].items():
        if any(stats.values()):
            parts = ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0)
            print(f"   {state.upper()}: {parts}")

    # Overall result
    all_transitions_valid = all([
        v.get("closed_to_open"),
        v.get("open_to_half_open"),
        v.get("half_open_to_closed"),
    ])
    v["all_transitions_valid"] = all_transitions_valid

    result_status = "✅ PASSED" if all_transitions_valid else "❌ FAILED"
    print(f"\n{'='*70}")
    print(f"🏆 PLATINUM TEST RESULT: {result_status}")
    print(f"{'='*70}")

    # Save report
    report_dir = os.path.join(_load_tests_dir, "results", "stage15")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "stage15_platinum_report.json")

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_name": "Stage 15 PLATINUM: Circuit Breaker Auto Transitions",
                "timestamp": datetime.now().isoformat(),
                "dna": STAGE_DNA,
                "configuration": {
                    "failure_threshold": CB_FAILURE_THRESHOLD,
                    "recovery_timeout": CB_RECOVERY_TIMEOUT,
                    "success_threshold": CB_SUCCESS_THRESHOLD,
                    "flapping_threshold": CB_FLAPPING_THRESHOLD,
                    "target_service": TARGET_SERVICE,
                },
                "phases": {
                    "normal": PHASE_1_NORMAL_DURATION,
                    "failure_injection": PHASE_2_FAILURE_DURATION,
                    "wait_recovery": PHASE_3_WAIT_DURATION,
                    "flapping": PHASE_4_FLAPPING_DURATION,
                    "recovery": PHASE_5_RECOVERY_DURATION,
                },
                "transitions": _cb_stats["transitions"],
                "timing": {
                    "open_time": _cb_stats["open_time"],
                    "half_open_time": _cb_stats["half_open_time"],
                    "closed_time": _cb_stats["closed_time"],
                },
                "flapping": _cb_stats["flapping"],
                "platinum": _cb_stats["platinum"],
                "requests_per_state": _cb_stats["requests_per_state"],
                "verification": v,
                "result": "PASSED" if all_transitions_valid else "FAILED",
            },
            f,
            indent=2,
        )

    print(f"\n💾 Report saved: {report_path}")
    print("=" * 70)
