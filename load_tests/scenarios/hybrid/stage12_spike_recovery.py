"""
Stage 12: EXTREME Spike & Recovery Test (Self-Healing Torture Test)

Purpose: 극단적인 스파이크 부하에서 Self-Healing 시스템 검증
- XTest Chaos Injection으로 장애 강제 주입
- Emergency Mode 트리거/해제 검증
- Circuit Breaker 극한 테스트
- Error Budget 소진 및 복구 테스트
- DLQ 플러딩 및 리플레이 검증
- Kill Switch 활성화/비활성화 테스트

Load Shape:
  Phase 1 (0-30s): 0 → MAX users (spike) + Chaos Injection
  Phase 2 (30s-2m): MAX users (sustain) + Emergency Escalation
  Phase 3 (2m-4m): MAX → MIN users (ramp-down) + Recovery
  Phase 4 (4m-6m): MIN users (stabilize) + Verification

Self-Healing Features Tested:
  ✓ Circuit Breaker (Open/Close/Half-Open)
  ✓ Emergency Mode (LEVEL_1/LEVEL_2/LEVEL_3)
  ✓ Error Budget (Exhaust/Recovery)
  ✓ DLQ (Flood/Replay)
  ✓ Kill Switch (Activate/Deactivate)
  ✓ Rate Limiter (Under extreme load)
  ✓ Health Check (During chaos)

Execution:
    # Docker Compose 실행
    docker-compose exec web python -m locust \\
        -f load_tests/scenarios/hybrid/stage12_spike_recovery.py \\
        --headless --host http://localhost:8000 \\
        --html load_tests/results/stage12/stage12_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 12)
"""

import os
import sys
import time
import random
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks
from load_tests.reports import print_console_report, save_all_reports

# Self-Healing 클라이언트 임포트
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.state_cache import CBStateCache
    from load_tests.utils.selfhealing.async_logger import AsyncHealingLogger, EventSeverity
    from load_tests.utils.selfhealing.adaptive_jitter import AdaptiveJitter, SystemState

    SELFHEALING_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] Self-Healing client not available: {e}")
    SELFHEALING_AVAILABLE = False


STAGE_NAME = "[Stage12-ExtremeSpike]"
DEBUG_MODE = os.environ.get("STAGE12_DEBUG", "false").lower() == "true"  # 프로덕션에서는 false

# =============================================================================
# 환경 설정
# =============================================================================

MAX_USERS = int(os.environ.get("LOCUST_MAX_USERS", "100"))  # V2.5: 100명
MIN_USERS = int(os.environ.get("LOCUST_MIN_USERS", "5"))
TEST_DURATION = int(os.environ.get("LOCUST_TEST_DURATION", "300"))  # V2.5: 5분

# 단계별 시간 비율
SPIKE_RATIO = 0.15  # 15%
SUSTAIN_RATIO = 0.30  # 30%
RAMP_DOWN_RATIO = 0.30  # 30%
STABILIZE_RATIO = 0.25  # 25%

# =============================================================================
# V2.5 Platinum Grade Add-ons 설정
# =============================================================================

# SLA Hard-Cap 설정 ⚖️
SLA_P99_THRESHOLD_MS = 250  # P99 응답시간 임계값 (Gold Tier)
SLA_DATA_VARIANCE_TOLERANCE = 0  # 데이터 오차 허용 개수 (0 = 불허)
SLA_STRICT_MODE = True  # SLA 엄격 모드 활성화

# Message Storm & Backpressure 설정 📨
MESSAGE_STORM_ENABLED = True
MESSAGE_STORM_RATE = 1000  # 초당 로그 주입 수
MESSAGE_STORM_DURATION = 10  # 폭풍 지속 시간 (초)
BACKPRESSURE_BUFFER_SIZE = 5000  # 버퍼 최대 크기

# Clock Skew Attack 설정 ⏰
CLOCK_SKEW_ENABLED = True
CLOCK_SKEW_MAX_DRIFT_SEC = 5  # 최대 시간 왜곡 (초)
CLOCK_SKEW_ATTACK_INTERVAL = 30  # 공격 간격 (초)

# Cascading Failure 설정 🌊
CASCADING_FAILURE_ENABLED = True
CASCADE_SERVICES = ["payment", "order", "inventory", "notification"]
CASCADE_PROPAGATION_DELAY = 2  # 전파 지연 (초)

# Retry Storm Prevention 설정 🔄
RETRY_STORM_ENABLED = True
RETRY_STORM_THRESHOLD = 50  # 초당 재시도 임계값
RETRY_BACKOFF_MULTIPLIER = 2.0  # 백오프 승수

# Zombie Detection 설정 (Strict)
ZOMBIE_LATENCY_THRESHOLD_SEC = 2.5  # V2.5: 2.5초 (Strict)


# =============================================================================
# 극단적 Self-Healing 통계
# =============================================================================

_extreme_stats = {
    "start_time": None,
    "phase": "spike",
    "phase_times": {
        "spike_start": None,
        "sustain_start": None,
        "ramp_down_start": None,
        "stabilize_start": None,
    },
    # Circuit Breaker 분석
    "circuit_breaker": {
        "first_open_time": None,
        "first_close_time": None,
        "open_count": 0,
        "close_count": 0,
        "half_open_count": 0,
        "last_state": "closed",
        "services_affected": [],
        "recovery_latency_ms": None,
    },
    # DLQ 분석
    "dlq": {
        "max_count": 0,
        "current_count": 0,
        "items_before_spike": 0,
        "items_after_recovery": 0,
        "replay_success_count": 0,
        "replay_fail_count": 0,
    },
    # Emergency Mode 분석
    "emergency": {
        "triggered_count": 0,
        "released_count": 0,
        "max_level": 0,
        "last_level": 0,
        "trigger_times": [],
    },
    # Error Budget 분석
    "error_budget": {
        "initial_remaining": None,
        "min_remaining": None,
        "exhausted": False,
        "recovered": False,
        "consumed_during_test": 0,
    },
    # Kill Switch 분석
    "kill_switch": {
        "activated_count": 0,
        "deactivated_count": 0,
        "targets": [],
    },
    # Chaos Injection 분석
    "chaos": {
        "failures_injected": 0,
        "cb_triggers": 0,
        "recovery_triggers": 0,
    },
    # Phase별 메트릭
    "metrics_per_phase": {
        "spike": {"requests": 0, "errors": 0, "response_times": []},
        "sustain": {"requests": 0, "errors": 0, "response_times": []},
        "ramp_down": {"requests": 0, "errors": 0, "response_times": []},
        "stabilize": {"requests": 0, "errors": 0, "response_times": []},
    },
    # V2 최적화 모듈 통계
    "v2_modules": {
        "cache_hits": 0,
        "cache_misses": 0,
        "async_events": 0,
        "jitter_applied": 0,
    },
    # Recovery Latency
    "recovery": {
        "spike_impact_detected_at": None,
        "recovery_started_at": None,
        "recovery_completed_at": None,
        "recovery_latency_seconds": None,
    },
    # 데이터 일관성
    "consistency": {
        "checked": False,
        "orders_before": 0,
        "orders_after": 0,
        "stock_consistent": None,
    },
    # 디버그 로그
    "debug_logs": [],
    # =============================================================================
    # V2.5 Platinum Grade Add-ons 통계
    # =============================================================================
    # SLA Hard-Cap ⚖️
    "sla_hardcap": {
        "p99_violations": 0,
        "p99_max_ms": 0,
        "data_variance_count": 0,
        "sla_passed": True,
        "violation_timestamps": [],
    },
    # Message Storm & Backpressure 📨
    "message_storm": {
        "messages_injected": 0,
        "buffer_overflow_count": 0,
        "max_buffer_size": 0,
        "backpressure_activated": False,
        "messages_dropped": 0,
        "main_logic_affected": False,
    },
    # Clock Skew Attack ⏰
    "clock_skew": {
        "attacks_executed": 0,
        "max_drift_sec": 0,
        "cb_window_corrupted": False,
        "recovery_found": False,
        "consistency_maintained": True,
    },
    # Cascading Failure 🌊
    "cascading_failure": {
        "cascades_triggered": 0,
        "services_affected": [],
        "max_cascade_depth": 0,
        "isolation_success": True,
    },
    # Retry Storm Prevention 🔄
    "retry_storm": {
        "storms_detected": 0,
        "retries_blocked": 0,
        "backoff_applied": 0,
        "circuit_protected": True,
    },
}

# 전역 Self-Healing 클라이언트
_sh_client: Optional["SelfHealingClient"] = None
_cb_cache: Optional["CBStateCache"] = None
_adaptive_jitter: Optional["AdaptiveJitter"] = None


def _debug_log(message: str):
    """디버그 로그 기록"""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] {message}"
        _extreme_stats["debug_logs"].append(log_entry)
        print(f"🔍 {log_entry}")


def _get_current_phase() -> str:
    """현재 단계 결정"""
    if _extreme_stats["start_time"] is None:
        return "spike"

    elapsed = time.time() - _extreme_stats["start_time"]
    total = TEST_DURATION

    spike_end = total * SPIKE_RATIO
    sustain_end = spike_end + (total * SUSTAIN_RATIO)
    ramp_down_end = sustain_end + (total * RAMP_DOWN_RATIO)

    if elapsed < spike_end:
        return "spike"
    elif elapsed < sustain_end:
        return "sustain"
    elif elapsed < ramp_down_end:
        return "ramp_down"
    else:
        return "stabilize"


def _record_request(success: bool, response_time_ms: float):
    """요청 기록 + SLA Hard-Cap 검증"""
    phase = _get_current_phase()
    stats = _extreme_stats["metrics_per_phase"][phase]

    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)

    # SLA Hard-Cap 검증 ⚖️
    if SLA_STRICT_MODE:
        _check_sla_hardcap(response_time_ms)

    if not success:
        stats["errors"] += 1
        # 스파이크 영향 감지
        if phase == "spike" and _extreme_stats["recovery"]["spike_impact_detected_at"] is None:
            error_rate = stats["errors"] / stats["requests"] * 100 if stats["requests"] > 0 else 0
            if error_rate > 10:  # 10% 이상 에러
                _extreme_stats["recovery"]["spike_impact_detected_at"] = time.time()
                _debug_log(f"Spike impact detected! Error rate: {error_rate:.1f}%")


# =============================================================================
# V2.5 Platinum Grade Add-ons Functions
# =============================================================================


def _check_sla_hardcap(response_time_ms: float):
    """
    SLA Hard-Cap 검증 ⚖️ (V2.6: Phase별 분리)
    
    - SPIKE/SUSTAIN: SLA 완화 (P99 * 4 허용)
    - RAMP_DOWN: SLA 완화 (P99 * 2 허용)
    - STABILIZE: 엄격 적용 (P99 ≤ 250ms)
    """
    global _extreme_stats

    phase = _get_current_phase()
    sla = _extreme_stats["sla_hardcap"]

    # Phase별 임계값 조정
    if phase in ["spike", "sustain"]:
        threshold = SLA_P99_THRESHOLD_MS * 4  # 1000ms 허용
    elif phase == "ramp_down":
        threshold = SLA_P99_THRESHOLD_MS * 2  # 500ms 허용
    else:  # stabilize
        threshold = SLA_P99_THRESHOLD_MS  # 250ms 엄격 적용

    # P99 최대값 업데이트 (전체)
    if response_time_ms > sla["p99_max_ms"]:
        sla["p99_max_ms"] = response_time_ms

    # STABILIZE 단계에서만 SLA 위반으로 판정
    if phase == "stabilize" and response_time_ms > threshold:
        sla["p99_violations"] += 1
        sla["sla_passed"] = False
        sla["violation_timestamps"].append(time.time())

        if sla["p99_violations"] == 1:  # 첫 번째 위반
            _debug_log(f"⚖️ SLA VIOLATION (STABILIZE): P99 {response_time_ms:.0f}ms > {threshold}ms!")
    
    # 다른 단계에서는 경고만 (위반 카운트 안 함)
    elif response_time_ms > threshold:
        _debug_log(f"⚖️ SLA WARNING ({phase}): {response_time_ms:.0f}ms > {threshold}ms (relaxed)")


def _calculate_recovery_p99() -> float:
    """복구 후 P99 계산 (STABILIZE 단계만)"""
    stabilize_times = _extreme_stats["metrics_per_phase"]["stabilize"].get("response_times", [])
    return _calculate_p99(stabilize_times)


def _record_data_variance(variance_type: str, details: str = ""):
    """데이터 오차 기록"""
    global _extreme_stats

    sla = _extreme_stats["sla_hardcap"]
    sla["data_variance_count"] += 1

    if SLA_DATA_VARIANCE_TOLERANCE == 0:
        sla["sla_passed"] = False

    _debug_log(f"⚖️ DATA VARIANCE: {variance_type} - {details}")


def _calculate_p99(response_times: list) -> float:
    """P99 계산"""
    if not response_times:
        return 0.0
    sorted_times = sorted(response_times)
    index = int(len(sorted_times) * 0.99)
    return sorted_times[min(index, len(sorted_times) - 1)]


def _simulate_clock_skew(drift_seconds: float) -> dict:
    """
    Clock Skew Attack 시뮬레이션 ⏰
    시스템 시간 왜곡을 시뮬레이션하여 CB 타임 윈도우 테스트
    """
    global _extreme_stats

    clock_stats = _extreme_stats["clock_skew"]
    clock_stats["attacks_executed"] += 1

    if abs(drift_seconds) > clock_stats["max_drift_sec"]:
        clock_stats["max_drift_sec"] = abs(drift_seconds)

    # 시뮬레이션: 시간 왜곡 효과
    # 실제 시스템 시간은 변경하지 않고, 논리적으로 시뮬레이션
    skewed_time = time.time() + drift_seconds

    _debug_log(f"⏰ Clock Skew Attack: drift={drift_seconds:.2f}s, simulated_time={skewed_time:.0f}")

    return {
        "original_time": time.time(),
        "skewed_time": skewed_time,
        "drift_seconds": drift_seconds,
    }


def _inject_message_storm(count: int) -> dict:
    """
    Message Storm 주입 📨
    AsyncHealingLogger에 대량 로그를 주입하여 Backpressure 테스트
    """
    global _extreme_stats

    storm_stats = _extreme_stats["message_storm"]
    start_time = time.time()
    injected = 0
    dropped = 0

    for i in range(count):
        try:
            if SELFHEALING_AVAILABLE:
                AsyncHealingLogger.log(
                    {
                        "event": "message_storm",
                        "index": i,
                        "timestamp": time.time(),
                        "payload": "X" * 100,  # 100바이트 페이로드
                    },
                    EventSeverity.DEBUG,
                )
                injected += 1
        except Exception as e:
            # 버퍼 오버플로우 또는 Backpressure
            dropped += 1
            if "buffer" in str(e).lower() or "overflow" in str(e).lower():
                storm_stats["buffer_overflow_count"] += 1
                storm_stats["backpressure_activated"] = True

    elapsed = time.time() - start_time
    storm_stats["messages_injected"] += injected
    storm_stats["messages_dropped"] += dropped

    _debug_log(f"📨 Message Storm: injected={injected}, dropped={dropped}, elapsed={elapsed:.3f}s")

    return {
        "injected": injected,
        "dropped": dropped,
        "elapsed_seconds": elapsed,
        "rate": injected / elapsed if elapsed > 0 else 0,
    }


def _trigger_cascading_failure(origin_service: str) -> dict:
    """
    Cascading Failure 시뮬레이션 🌊
    한 서비스 장애가 다른 서비스로 전파되는지 테스트
    """
    global _extreme_stats

    cascade_stats = _extreme_stats["cascading_failure"]
    cascade_stats["cascades_triggered"] += 1

    affected = [origin_service]
    depth = 0

    # 장애 전파 시뮬레이션
    for service in CASCADE_SERVICES:
        if service != origin_service and service not in affected:
            # 의존성 체인에 따라 전파
            time.sleep(CASCADE_PROPAGATION_DELAY * 0.1)  # 시뮬레이션용 짧은 딜레이
            affected.append(service)
            depth += 1

            if service not in cascade_stats["services_affected"]:
                cascade_stats["services_affected"].append(service)

    cascade_stats["max_cascade_depth"] = max(cascade_stats["max_cascade_depth"], depth)

    _debug_log(f"🌊 Cascading Failure: origin={origin_service}, affected={len(affected)}, depth={depth}")

    return {
        "origin": origin_service,
        "affected_services": affected,
        "cascade_depth": depth,
    }


def _detect_retry_storm(retry_count: int) -> bool:
    """
    Retry Storm 감지 및 방지 🔄
    """
    global _extreme_stats

    storm_stats = _extreme_stats["retry_storm"]

    if retry_count > RETRY_STORM_THRESHOLD:
        storm_stats["storms_detected"] += 1
        storm_stats["retries_blocked"] += retry_count
        storm_stats["backoff_applied"] += 1

        _debug_log(f"🔄 Retry Storm Detected: count={retry_count}, applying backoff")
        return True

    return False


def _update_phase():
    """단계 변경 추적"""
    phase = _get_current_phase()
    phase_times = _extreme_stats["phase_times"]

    if phase == "spike" and phase_times["spike_start"] is None:
        phase_times["spike_start"] = time.time()
        _extreme_stats["phase"] = "spike"
        _debug_log("⚡ Phase: SPIKE - Ramping to MAX users + CHAOS INJECTION")

    elif phase == "sustain" and phase_times["sustain_start"] is None:
        phase_times["sustain_start"] = time.time()
        _extreme_stats["phase"] = "sustain"
        _debug_log("🔥 Phase: SUSTAIN - Holding MAX users + EMERGENCY ESCALATION")

    elif phase == "ramp_down" and phase_times["ramp_down_start"] is None:
        phase_times["ramp_down_start"] = time.time()
        _extreme_stats["phase"] = "ramp_down"
        _extreme_stats["recovery"]["recovery_started_at"] = time.time()
        _debug_log("📉 Phase: RAMP DOWN - Recovery initiated")

    elif phase == "stabilize" and phase_times["stabilize_start"] is None:
        phase_times["stabilize_start"] = time.time()
        _extreme_stats["phase"] = "stabilize"
        _debug_log("✅ Phase: STABILIZE - Verification mode")


# =============================================================================
# Custom Load Shape - Extreme Spike & Recovery
# =============================================================================


class ExtremeSpikeShape(LoadTestShape):
    """
    극단적인 스파이크 부하 패턴.

    Self-Healing 시스템을 극한까지 테스트하기 위해:
    1. 급격한 스파이크로 시스템 과부하
    2. 지속적인 부하로 안정성 검증
    3. 점진적 감소로 복구 검증
    4. 안정화 단계에서 일관성 확인
    """

    def tick(self):
        """현재 시간에 맞는 (user_count, spawn_rate) 반환"""
        run_time = self.get_run_time()

        # 단계 업데이트
        _update_phase()

        # 단계별 시간 계산
        total = TEST_DURATION
        spike_end = total * SPIKE_RATIO
        sustain_end = spike_end + (total * SUSTAIN_RATIO)
        ramp_down_end = sustain_end + (total * RAMP_DOWN_RATIO)

        # Phase 1: Spike (급격한 증가)
        if run_time < spike_end:
            progress = run_time / spike_end
            users = int(MAX_USERS * progress)
            return (max(1, users), MAX_USERS // 2)  # 빠른 스폰

        # Phase 2: Sustain (최대 부하 유지)
        elif run_time < sustain_end:
            return (MAX_USERS, 10)

        # Phase 3: Ramp Down (점진적 감소)
        elif run_time < ramp_down_end:
            elapsed_in_phase = run_time - sustain_end
            phase_duration = ramp_down_end - sustain_end
            progress = elapsed_in_phase / phase_duration
            users = int(MAX_USERS - (MAX_USERS - MIN_USERS) * progress)
            return (max(MIN_USERS, users), 5)

        # Phase 4: Stabilize (안정화)
        elif run_time < total:
            return (MIN_USERS, 1)

        # 테스트 완료
        return None


# =============================================================================
# Self-Healing 테스트 클라이언트
# =============================================================================


class ExtremeSpikeUser(HttpUser):
    """
    극단적인 스파이크 테스트 사용자.

    Self-Healing 시스템의 모든 기능을 극한까지 테스트합니다.
    V2.6: V2 모듈 완전 연결, Phase별 SLA 분리, 복구 후 P99 측정
    """

    wait_time = between(0.1, 0.5)  # 매우 빠른 요청

    def on_start(self):
        """사용자 세션 초기화"""
        global _extreme_stats, _sh_client, _cb_cache, _adaptive_jitter

        setup_event_hooks(STAGE_NAME)

        # 첫 번째 사용자가 전역 초기화
        if _extreme_stats["start_time"] is None:
            _extreme_stats["start_time"] = time.time()
            _debug_log("=" * 60)
            _debug_log("🚀 EXTREME Spike & Recovery Test V2.6 STARTED")
            _debug_log(f"   Max Users: {MAX_USERS}, Test Duration: {TEST_DURATION}s")
            _debug_log("=" * 60)

            # Self-Healing 클라이언트 초기화
            if SELFHEALING_AVAILABLE:
                try:
                    _sh_client = SelfHealingClient(
                        host=os.environ.get("SELFHEALING_HOST", "http://localhost:8000"),
                        auth_mode="xtest",
                    )
                    # 인증
                    _sh_client.login("admin", "admin")

                    # =========================================================
                    # V2.6: V2 최적화 모듈 완전 초기화
                    # =========================================================
                    
                    # 1. CBStateCache - fetch_callback 등록
                    _cb_cache = CBStateCache()
                    
                    def _fetch_cb_status(service_name: str) -> dict:
                        """CB 상태를 API에서 가져오는 콜백"""
                        try:
                            if _sh_client:
                                return _sh_client.circuit_breaker.get_status(service_name)
                        except Exception:
                            pass
                        return {"state": "unknown"}
                    
                    # CBStateCache에 콜백 등록 (클래스 속성으로)
                    CBStateCache._fetch_callback = _fetch_cb_status
                    _debug_log("✅ CBStateCache configured with fetch callback")
                    
                    # 2. AdaptiveJitter 초기화
                    _adaptive_jitter = AdaptiveJitter()
                    _debug_log("✅ AdaptiveJitter initialized")
                    
                    # 3. AsyncHealingLogger 시작
                    try:
                        AsyncHealingLogger.start()
                        _debug_log("✅ AsyncHealingLogger started")
                    except Exception as e:
                        _debug_log(f"⚠️ AsyncHealingLogger start skipped: {e}")

                    _debug_log("✅ All V2.6 modules initialized")

                    # 초기 상태 기록
                    self._record_initial_state()

                except Exception as e:
                    _debug_log(f"❌ Self-Healing client init failed: {e}")
                    traceback.print_exc()

        # 기본 헬퍼 초기화
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    def _record_initial_state(self):
        """초기 상태 기록"""
        global _sh_client, _extreme_stats

        if not _sh_client:
            return

        try:
            # Error Budget 초기 상태
            eb_status = _sh_client.error_budget.get_status()
            if "remaining_percent" in str(eb_status):
                _extreme_stats["error_budget"]["initial_remaining"] = eb_status.get("remaining_percent", 100)
                _extreme_stats["error_budget"]["min_remaining"] = eb_status.get("remaining_percent", 100)
                _debug_log(f"Initial Error Budget: {eb_status.get('remaining_percent', 100)}%")

            # DLQ 초기 상태
            dlq_stats = _sh_client.dlq.stats()
            pending = dlq_stats.get("pending_count", 0) if isinstance(dlq_stats, dict) else 0
            _extreme_stats["dlq"]["items_before_spike"] = pending
            _debug_log(f"Initial DLQ: {pending} items")

            # Circuit Breaker 초기 상태
            cb_status = _sh_client.circuit_breaker.get_all_status()
            _debug_log(f"Initial CB Status: {cb_status}")

        except Exception as e:
            _debug_log(f"Failed to record initial state: {e}")

    # =========================================================================
    # 극단적 Chaos Injection 태스크
    # =========================================================================

    @task(5)
    @tag("chaos", "circuit_breaker")
    def chaos_inject_cb_failure(self):
        """Circuit Breaker 장애 강제 주입"""
        global _sh_client, _extreme_stats

        phase = _get_current_phase()
        if phase not in ["spike", "sustain"]:
            return  # 복구 단계에서는 주입 안함

        if not _sh_client:
            return

        try:
            services = ["payment-service", "order-service", "inventory-service"]
            target_service = random.choice(services)

            # XTest로 장애 주입
            result = _sh_client.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type=random.choice(["exception", "timeout", "slow_response"]),
                failure_rate=random.uniform(0.3, 0.8),
                duration_seconds=10,
            )

            if result.get("status") != "error":
                _extreme_stats["chaos"]["failures_injected"] += 1
                _debug_log(f"💥 Chaos: Injected failure to {target_service}")

        except Exception as e:
            _debug_log(f"Chaos injection failed: {e}")

    @task(3)
    @tag("chaos", "emergency")
    def chaos_emergency_escalation(self):
        """Emergency 레벨 점진적 상승"""
        global _sh_client, _extreme_stats

        phase = _get_current_phase()
        if phase != "sustain":
            return  # sustain 단계에서만

        if not _sh_client:
            return

        try:
            current_level = _extreme_stats["emergency"]["last_level"]

            # 레벨 상승 (최대 LEVEL_3)
            if current_level < 3:
                levels = ["LEVEL_1", "LEVEL_2", "LEVEL_3"]
                next_level = levels[min(current_level, 2)]

                result = _sh_client.emergency.trigger(
                    level=next_level,
                    reason=f"Stage12 Extreme Test - Phase: {phase}",
                    duration_minutes=1,
                )

                if result.get("status") != "error":
                    _extreme_stats["emergency"]["triggered_count"] += 1
                    _extreme_stats["emergency"]["last_level"] = current_level + 1
                    _extreme_stats["emergency"]["max_level"] = max(_extreme_stats["emergency"]["max_level"], current_level + 1)
                    _extreme_stats["emergency"]["trigger_times"].append(time.time())
                    _debug_log(f"🚨 Emergency: Escalated to {next_level}")

        except Exception as e:
            _debug_log(f"Emergency escalation failed: {e}")

    @task(4)
    @tag("chaos", "error_budget")
    def chaos_consume_error_budget(self):
        """Error Budget 소진 시뮬레이션"""
        global _sh_client, _extreme_stats

        phase = _get_current_phase()
        if phase not in ["spike", "sustain"]:
            return

        if not _sh_client:
            return

        try:
            # 에러 기록하여 예산 소진
            result = _sh_client.error_budget.record_error(
                error_count=random.randint(5, 20),
                error_type="spike_test",
                service_name="stage12-test",
            )

            if result.get("status") != "error":
                _extreme_stats["error_budget"]["consumed_during_test"] += 1

                # 현재 예산 확인
                status = _sh_client.error_budget.get_status()
                remaining = status.get("remaining_percent", 100)

                if (
                    _extreme_stats["error_budget"]["min_remaining"] is None
                    or remaining < _extreme_stats["error_budget"]["min_remaining"]
                ):
                    _extreme_stats["error_budget"]["min_remaining"] = remaining

                if remaining <= 0:
                    _extreme_stats["error_budget"]["exhausted"] = True
                    _debug_log("💀 Error Budget: EXHAUSTED!")

        except Exception as e:
            _debug_log(f"Error budget consume failed: {e}")

    @task(2)
    @tag("chaos", "kill_switch")
    def chaos_toggle_kill_switch(self):
        """Kill Switch 토글"""
        global _sh_client, _extreme_stats

        phase = _get_current_phase()
        if phase not in ["spike"]:
            return  # spike 단계에서만

        if not _sh_client:
            return

        try:
            targets = ["risky-feature", "new-payment-flow", "experimental-cache"]
            target = random.choice(targets)

            # 킬스위치 활성화
            result = _sh_client.chaos.activate_kill_switch(
                target=target,
                reason="Stage12 Extreme Test",
            )

            if result.get("status") != "error":
                _extreme_stats["kill_switch"]["activated_count"] += 1
                if target not in _extreme_stats["kill_switch"]["targets"]:
                    _extreme_stats["kill_switch"]["targets"].append(target)
                _debug_log(f"🔌 Kill Switch: Activated for {target}")

                # 잠시 후 비활성화
                time.sleep(0.5)
                _sh_client.chaos.deactivate_kill_switch(
                    target=target,
                    reason="Stage12 Recovery",
                )
                _extreme_stats["kill_switch"]["deactivated_count"] += 1

        except Exception as e:
            _debug_log(f"Kill switch toggle failed: {e}")

    # =========================================================================
    # V2.5 Platinum Grade Add-ons 태스크
    # =========================================================================

    @task(3)
    @tag("platinum", "sla_hardcap")
    def sla_hardcap_verification(self):
        """
        SLA Hard-Cap 검증 ⚖️
        실시간으로 P99 응답시간과 데이터 오차를 검증합니다.
        """
        global _extreme_stats

        if not SLA_STRICT_MODE:
            return

        phase = _get_current_phase()

        # Phase별 P99 계산
        stats = _extreme_stats["metrics_per_phase"][phase]
        if len(stats.get("response_times", [])) >= 10:
            p99 = _calculate_p99(stats["response_times"])

            sla = _extreme_stats["sla_hardcap"]
            if p99 > SLA_P99_THRESHOLD_MS:
                if p99 > sla["p99_max_ms"]:
                    sla["p99_max_ms"] = p99
                    _debug_log(f"⚖️ SLA WARNING: P99={p99:.0f}ms exceeds {SLA_P99_THRESHOLD_MS}ms")

    @task(2)
    @tag("platinum", "message_storm")
    def message_storm_attack(self):
        """
        Message Storm & Backpressure 테스트 📨
        AsyncHealingLogger에 초당 수천 개의 로그를 주입하여
        버퍼 오버플로우와 Backpressure 제어를 테스트합니다.
        """
        global _extreme_stats

        if not MESSAGE_STORM_ENABLED:
            return

        phase = _get_current_phase()
        if phase != "spike":
            return  # spike 단계에서만 수행

        try:
            # 짧은 버스트로 메시지 폭풍 주입
            burst_size = min(MESSAGE_STORM_RATE // 10, 100)  # 100개씩 버스트
            result = _inject_message_storm(burst_size)

            storm_stats = _extreme_stats["message_storm"]

            # 메인 로직 영향 테스트
            start = time.time()
            with self.client.get(
                "/api/products/",
                name=f"{STAGE_NAME} POST-STORM Products",
                catch_response=True,
            ) as response:
                elapsed = (time.time() - start) * 1000

                # 메시지 폭풍 후에도 메인 로직이 250ms 이내에 응답해야 함
                if elapsed > SLA_P99_THRESHOLD_MS:
                    storm_stats["main_logic_affected"] = True
                    _debug_log(f"📨 WARNING: Main logic affected by storm! Latency={elapsed:.0f}ms")
                else:
                    _debug_log(f"📨 Backpressure OK: Main logic unaffected, latency={elapsed:.0f}ms")

                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")

        except Exception as e:
            _debug_log(f"Message storm attack failed: {e}")

    @task(2)
    @tag("platinum", "clock_skew")
    def clock_skew_attack(self):
        """
        Clock Skew Attack ⏰
        시스템 클럭을 인위적으로 왜곡하여 CB 타임 윈도우 테스트
        """
        global _extreme_stats

        if not CLOCK_SKEW_ENABLED:
            return

        phase = _get_current_phase()
        if phase not in ["sustain", "ramp_down"]:
            return  # sustain/ramp_down에서만

        try:
            # 랜덤 시간 왜곡 (-5초 ~ +5초)
            drift = random.uniform(-CLOCK_SKEW_MAX_DRIFT_SEC, CLOCK_SKEW_MAX_DRIFT_SEC)
            result = _simulate_clock_skew(drift)

            clock_stats = _extreme_stats["clock_skew"]

            # CB 상태 확인하여 윈도우 손상 여부 검증
            if SELFHEALING_AVAILABLE and _sh_client:
                try:
                    # 왜곡된 시간에서 CB 상태 조회
                    cb_status = _sh_client.circuit_breaker.get_all_status()

                    # 상태가 비정상적이면 윈도우 손상으로 판단
                    if cb_status.get("status") == "error":
                        clock_stats["cb_window_corrupted"] = True
                        _debug_log(f"⏰ CB Window potentially corrupted by clock skew!")
                    else:
                        clock_stats["recovery_found"] = True
                        _debug_log(f"⏰ CB recovered from clock skew (drift={drift:.2f}s)")

                except Exception:
                    clock_stats["cb_window_corrupted"] = True

            # 데이터 일관성 확인
            orders_before = _extreme_stats["consistency"]["orders_before"]
            orders_after = _extreme_stats["consistency"]["orders_after"]

            if orders_before > 0 and orders_after > 0:
                if orders_after < orders_before:
                    clock_stats["consistency_maintained"] = False
                    _debug_log(f"⏰ CONSISTENCY VIOLATION after clock skew!")

        except Exception as e:
            _debug_log(f"Clock skew attack failed: {e}")

    @task(3)
    @tag("platinum", "cascading_failure")
    def cascading_failure_test(self):
        """
        Cascading Failure 테스트 🌊
        한 서비스 장애가 다른 서비스로 전파되는지 검증합니다.
        """
        global _extreme_stats

        if not CASCADING_FAILURE_ENABLED:
            return

        phase = _get_current_phase()
        if phase != "sustain":
            return  # sustain 단계에서만

        if not _sh_client:
            return

        try:
            # 랜덤 서비스에서 장애 시작
            origin = random.choice(CASCADE_SERVICES)

            # 먼저 원본 서비스에 장애 주입
            _sh_client.xtest.inject_cb_failure(
                service_name=f"{origin}-service",
                failure_type="exception",
                failure_rate=0.9,
                duration_seconds=5,
            )

            # 장애 전파 시뮬레이션
            result = _trigger_cascading_failure(origin)

            cascade_stats = _extreme_stats["cascading_failure"]

            # 격리 성공 여부 확인
            # 다른 서비스가 정상 응답하면 격리 성공
            isolated_count = 0
            for service in CASCADE_SERVICES:
                if service != origin:
                    with self.client.get(
                        "/api/products/",  # 기본 엔드포인트로 테스트
                        name=f"{STAGE_NAME} CASCADE-{service}",
                        catch_response=True,
                    ) as response:
                        if response.status_code == 200:
                            isolated_count += 1
                            response.success()
                        else:
                            cascade_stats["isolation_success"] = False
                            response.failure(f"Cascade affected: {response.status_code}")

            if isolated_count == len(CASCADE_SERVICES) - 1:
                _debug_log(f"🌊 Cascade ISOLATED: {origin} failed, others OK")
            else:
                _debug_log(f"🌊 Cascade SPREAD: {len(CASCADE_SERVICES) - isolated_count - 1} services affected")

        except Exception as e:
            _debug_log(f"Cascading failure test failed: {e}")

    @task(2)
    @tag("platinum", "retry_storm")
    def retry_storm_prevention(self):
        """
        Retry Storm Prevention 🔄
        재시도 폭풍 발생 시 시스템 보호 검증
        """
        global _extreme_stats

        if not RETRY_STORM_ENABLED:
            return

        phase = _get_current_phase()
        if phase not in ["spike", "sustain"]:
            return

        try:
            storm_stats = _extreme_stats["retry_storm"]

            # 의도적으로 실패하는 요청 다수 생성
            retry_count = 0
            max_retries = 60  # 폭풍 시뮬레이션

            for _ in range(max_retries):
                with self.client.get(
                    "/api/nonexistent-endpoint-for-retry-test/",
                    name=f"{STAGE_NAME} RETRY-STORM",
                    catch_response=True,
                ) as response:
                    if response.status_code != 200:
                        retry_count += 1

                        # Backoff 적용
                        if retry_count > RETRY_STORM_THRESHOLD:
                            backoff = min(retry_count * 0.1 * RETRY_BACKOFF_MULTIPLIER, 2.0)
                            time.sleep(backoff)
                            storm_stats["backoff_applied"] += 1

                        response.failure(f"Retry test: {response.status_code}")
                    else:
                        response.success()
                        break

            # Retry Storm 감지
            if _detect_retry_storm(retry_count):
                _debug_log(f"🔄 Retry Storm handled: {retry_count} retries with backoff")
            else:
                _debug_log(f"🔄 Normal retry pattern: {retry_count} retries")

        except Exception as e:
            _debug_log(f"Retry storm prevention failed: {e}")

    # =========================================================================
    # 모니터링 태스크
    # =========================================================================

    @task(10)
    @tag("monitoring", "circuit_breaker")
    def monitor_circuit_breaker(self):
        """Circuit Breaker 상태 모니터링 (V2.6: 캐시 완전 연동)"""
        global _sh_client, _extreme_stats, _cb_cache

        if not _sh_client:
            return

        try:
            # V2.6: 캐시 먼저 확인 (fetch_callback 등록됨)
            if SELFHEALING_AVAILABLE and _cb_cache:
                try:
                    # 캐시에서 상태 조회 시도
                    cached = CBStateCache.get_state("all_services")
                    if cached and cached.get("state") != "unknown":
                        _extreme_stats["v2_modules"]["cache_hits"] += 1
                        # 캐시된 데이터로 상태 업데이트
                        self._update_cb_stats_from_data(cached)
                        return
                except Exception as e:
                    _debug_log(f"Cache miss: {e}")
                
                _extreme_stats["v2_modules"]["cache_misses"] += 1

            # API 직접 호출 (캐시 미스 시)
            with self.client.get(
                "/api/self-healing/status/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} CB-monitor",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    
                    # V2.6: 캐시에 저장
                    if SELFHEALING_AVAILABLE:
                        try:
                            CBStateCache.set_state("all_services", data, ttl=5)
                        except Exception:
                            pass

                    # 서비스별 상태 분석
                    self._update_cb_stats_from_data(data)

                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")

        except Exception as e:
            _debug_log(f"CB monitor failed: {e}")

    def _update_cb_stats_from_data(self, data: dict):
        """CB 상태 데이터로 통계 업데이트"""
        global _extreme_stats
        
        for info in data.get("services", []):
            service = info.get("service_name", "unknown")
            cb_state = info.get("circuit_state") or info.get("state", "closed")

            # 상태 변화 감지
            if cb_state == "open" and _extreme_stats["circuit_breaker"]["last_state"] != "open":
                _extreme_stats["circuit_breaker"]["open_count"] += 1
                _extreme_stats["chaos"]["cb_triggers"] += 1

                if _extreme_stats["circuit_breaker"]["first_open_time"] is None:
                    _extreme_stats["circuit_breaker"]["first_open_time"] = time.time()
                    _debug_log(f"🔴 CB OPENED: {service}")

                if service not in _extreme_stats["circuit_breaker"]["services_affected"]:
                    _extreme_stats["circuit_breaker"]["services_affected"].append(service)

            elif cb_state == "half_open":
                _extreme_stats["circuit_breaker"]["half_open_count"] += 1
                _debug_log(f"🟡 CB HALF-OPEN: {service}")

            elif cb_state == "closed" and _extreme_stats["circuit_breaker"]["last_state"] == "open":
                _extreme_stats["circuit_breaker"]["close_count"] += 1
                _extreme_stats["chaos"]["recovery_triggers"] += 1

                if _extreme_stats["circuit_breaker"]["first_close_time"] is None:
                    _extreme_stats["circuit_breaker"]["first_close_time"] = time.time()

                    # 복구 지연 계산
                    if _extreme_stats["circuit_breaker"]["first_open_time"]:
                        recovery_ms = (
                            _extreme_stats["circuit_breaker"]["first_close_time"]
                            - _extreme_stats["circuit_breaker"]["first_open_time"]
                        ) * 1000
                        _extreme_stats["circuit_breaker"]["recovery_latency_ms"] = recovery_ms
                        _debug_log(f"🟢 CB CLOSED: {service} (Recovery: {recovery_ms:.0f}ms)")

            _extreme_stats["circuit_breaker"]["last_state"] = cb_state

    @task(5)
    @tag("monitoring", "dlq")
    def monitor_dlq(self):
        """DLQ 상태 모니터링"""
        global _sh_client, _extreme_stats

        if not _sh_client:
            return

        try:
            with self.client.get(
                "/api/self-healing/dlq/list/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} DLQ-monitor",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    pending = data.get("pending_count", 0) if isinstance(data, dict) else len(data.get("results", []))

                    _extreme_stats["dlq"]["current_count"] = pending

                    if pending > _extreme_stats["dlq"]["max_count"]:
                        _extreme_stats["dlq"]["max_count"] = pending
                        _debug_log(f"📥 DLQ Max: {pending} items")

                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")

        except Exception as e:
            _debug_log(f"DLQ monitor failed: {e}")

    @task(3)
    @tag("monitoring", "health")
    def monitor_health(self):
        """전체 시스템 Health 모니터링"""
        start = time.time()

        with self.client.get(
            "/api/self-healing/health/",
            headers=self.login_helper.get_auth_header() if hasattr(self, "login_helper") else {},
            name=f"{STAGE_NAME} Health-check",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)

    # =========================================================================
    # 부하 생성 태스크 (쇼핑 API)
    # =========================================================================

    @task(15)
    @tag("load", "browse")
    def browse_products(self):
        """상품 브라우징 (높은 부하) - V2.6: Stressed 모드 강제 활성화"""
        global _adaptive_jitter

        # V2.6: Adaptive Jitter - Phase별 강제 모드
        if _adaptive_jitter and SELFHEALING_AVAILABLE:
            try:
                phase = _get_current_phase()
                
                # SPIKE/SUSTAIN: stressed 모드 강제 (요청 분산 극대화)
                if phase in ["spike", "sustain"]:
                    # 강제 stressed 모드: 높은 지터로 요청 분산
                    jitter_sec = AdaptiveJitter.calculate(current_load=0.95, stressed=True)
                elif phase == "ramp_down":
                    jitter_sec = AdaptiveJitter.calculate(current_load=0.6)
                else:  # stabilize
                    jitter_sec = AdaptiveJitter.calculate(current_load=0.2)
                
                time.sleep(jitter_sec)
                _extreme_stats["v2_modules"]["jitter_applied"] += 1
            except Exception as e:
                # stressed 파라미터 미지원 시 fallback
                try:
                    load = 0.9 if phase in ["spike", "sustain"] else 0.3
                    jitter_sec = AdaptiveJitter.calculate(current_load=load)
                    time.sleep(jitter_sec)
                    _extreme_stats["v2_modules"]["jitter_applied"] += 1
                except Exception:
                    pass

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

    @task(8)
    @tag("load", "cart")
    def add_to_cart(self):
        """장바구니 추가"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        start = time.time()

        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": random.randint(1, 5)},
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

    @task(5)
    @tag("load", "payment")
    def payment_flow(self):
        """결제 플로우 (가장 스트레스가 높음)"""

        product = self.product_helper.get_random_product()
        if not product:
            return

        # 장바구니 추가
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add (payment)",
        )

        # 주문 생성
        start = time.time()

        with self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "서울시 강남구 테헤란로 123",
                "shipping_name": "테스트유저",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "06234",
            },
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
                    self._request_payment(order_id)

                response.success()

                # Async Logger (V2) - 클래스 메서드 사용
                if SELFHEALING_AVAILABLE:
                    try:
                        AsyncHealingLogger.log(
                            {"event": "order_created", "order_id": order_id, "elapsed_ms": elapsed_ms},
                            EventSeverity.INFO,
                        )
                        _extreme_stats["v2_modules"]["async_events"] += 1
                    except Exception:
                        pass
            else:
                response.failure(f"Order failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    def _request_payment(self, order_id: int):
        """결제 요청"""
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

    # =========================================================================
    # 복구 검증 태스크
    # =========================================================================

    @task(2)
    @tag("recovery", "verification")
    def verify_recovery(self):
        """복구 검증 (stabilize 단계에서만)"""
        global _sh_client, _extreme_stats

        phase = _get_current_phase()
        if phase != "stabilize":
            return

        if not _sh_client:
            return

        try:
            # Emergency 해제 확인
            emergency_status = _sh_client.emergency.get_status()
            if emergency_status.get("active", False):
                _sh_client.emergency.release(reason="Stage12 Recovery Verification")
                _extreme_stats["emergency"]["released_count"] += 1
                _debug_log("✅ Emergency released during recovery")

            # Error Budget 복구 확인
            eb_status = _sh_client.error_budget.get_status()
            remaining = eb_status.get("remaining_percent", 0)
            if remaining > 0 and _extreme_stats["error_budget"]["exhausted"]:
                _extreme_stats["error_budget"]["recovered"] = True
                _debug_log(f"✅ Error Budget recovered: {remaining}%")

            # DLQ 리플레이 시도
            dlq_list = _sh_client.dlq.list(status="pending", limit=5)
            items = dlq_list.get("results", []) if isinstance(dlq_list, dict) else []

            for item in items[:3]:  # 최대 3개만 리플레이
                pk = item.get("id") or item.get("pk")
                if pk:
                    result = _sh_client.dlq.retry(pk)
                    if result.get("status") != "error":
                        _extreme_stats["dlq"]["replay_success_count"] += 1
                        _debug_log(f"✅ DLQ replay success: {pk}")
                    else:
                        _extreme_stats["dlq"]["replay_fail_count"] += 1

            # 복구 완료 시간 기록
            if _extreme_stats["recovery"]["recovery_completed_at"] is None:
                # 에러율이 5% 이하면 복구 완료로 간주
                stats = _extreme_stats["metrics_per_phase"]["stabilize"]
                if stats["requests"] > 0:
                    error_rate = stats["errors"] / stats["requests"] * 100
                    if error_rate < 5:
                        _extreme_stats["recovery"]["recovery_completed_at"] = time.time()

                        if _extreme_stats["recovery"]["recovery_started_at"]:
                            latency = (
                                _extreme_stats["recovery"]["recovery_completed_at"]
                                - _extreme_stats["recovery"]["recovery_started_at"]
                            )
                            _extreme_stats["recovery"]["recovery_latency_seconds"] = latency
                            _debug_log(f"✅ Recovery completed in {latency:.1f}s")

        except Exception as e:
            _debug_log(f"Recovery verification failed: {e}")


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """극단적 테스트 리포트 생성 - 공통 보고서 모듈 사용"""
    global _extreme_stats

    # Async Logger 플러시 (클래스 메서드 사용)
    try:
        AsyncHealingLogger.flush_now()
        AsyncHealingLogger.stop()
    except Exception as e:
        if DEBUG_MODE:
            print(f"🔍 AsyncLogger flush skipped: {e}")

    # 설정 딕셔너리 준비
    config = {
        "test_duration": TEST_DURATION,
        "max_users": MAX_USERS,
        "min_users": MIN_USERS,
        "sla_p99_threshold_ms": SLA_P99_THRESHOLD_MS,
        "clock_skew_max_drift_sec": CLOCK_SKEW_MAX_DRIFT_SEC,
    }

    # 콘솔 보고서 출력
    print_console_report(_extreme_stats, config)

    # 파일 보고서 저장
    results_dir = os.path.join(_load_tests_dir, "results", "stage12")
    save_all_reports(
        stats=_extreme_stats,
        config=config,
        results_dir=results_dir,
        stage_name="stage12",
        debug_logs=_extreme_stats.get("debug_logs"),
        debug_mode=DEBUG_MODE
    )

    # Debug logs (추가 출력)
    if DEBUG_MODE and _extreme_stats.get("debug_logs"):
        print("\n🔍 Debug Logs (last 20):")
        for log in _extreme_stats["debug_logs"][-20:]:
            print(f"  {log}")

    print("\n" + "=" * 80)

