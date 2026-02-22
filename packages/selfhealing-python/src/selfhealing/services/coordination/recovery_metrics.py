"""
Recovery Prometheus Metrics.

복구 프로세스 모니터링을 위한 Prometheus 메트릭 모듈.

Phase 5.5 구현:
- selfhealing_recovery_sessions_total: 전체 복구 세션 수
- selfhealing_recovery_steps_total: 단계별 실행 수
- selfhealing_recovery_duration_seconds: 복구 소요 시간
- selfhealing_recovery_circuit_breaker_trips_total: 서킷 브레이커 트립 수
- selfhealing_recovery_pending_approvals: 대기 중인 승인 수

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#5.5
"""

from __future__ import annotations

import structlog
import threading
import time
from typing import Any

logger = structlog.get_logger()


# =============================================================================
# Prometheus Metrics (Fallback if prometheus_client not available)
# =============================================================================

try:
    from prometheus_client import Counter, Gauge, Histogram, Info

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    # 더미 클래스 정의
    class DummyMetric:
        def labels(self, **kwargs):
            return self

        def inc(self, amount=1):
            pass

        def dec(self, amount=1):
            pass

        def set(self, value):
            pass

        def observe(self, value):
            pass

        def info(self, value):
            pass

    def Counter(*args, **kwargs):
        return DummyMetric()

    def Gauge(*args, **kwargs):
        return DummyMetric()

    def Histogram(*args, **kwargs):
        return DummyMetric()

    def Info(*args, **kwargs):
        return DummyMetric()


# =============================================================================
# Metric Definitions
# =============================================================================

# 복구 세션 카운터
RECOVERY_SESSIONS_TOTAL = Counter(
    "selfhealing_recovery_sessions_total",
    "Total number of recovery sessions",
    ["namespace", "trigger_level", "status"],
)

# 복구 단계 카운터
RECOVERY_STEPS_TOTAL = Counter(
    "selfhealing_recovery_steps_total",
    "Total number of recovery step executions",
    ["namespace", "step_type", "status"],
)

# 복구 소요 시간 히스토그램
RECOVERY_DURATION_SECONDS = Histogram(
    "selfhealing_recovery_duration_seconds",
    "Recovery session duration in seconds",
    ["namespace", "trigger_level", "status"],
    buckets=[30, 60, 120, 300, 600, 1200, 1800, 3600],
)

# 단계별 소요 시간 히스토그램
RECOVERY_STEP_DURATION_SECONDS = Histogram(
    "selfhealing_recovery_step_duration_seconds",
    "Recovery step duration in seconds",
    ["namespace", "step_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

# 서킷 브레이커 트립 카운터
RECOVERY_CIRCUIT_BREAKER_TRIPS_TOTAL = Counter(
    "selfhealing_recovery_circuit_breaker_trips_total",
    "Total number of circuit breaker trips during recovery",
    ["namespace", "reason"],
)

# 대기 중인 승인 게이지
RECOVERY_PENDING_APPROVALS = Gauge(
    "selfhealing_recovery_pending_approvals",
    "Current number of pending recovery approvals",
    ["namespace"],
)

# 방치된 승인 게이지
RECOVERY_STALE_APPROVALS = Gauge(
    "selfhealing_recovery_stale_approvals",
    "Current number of stale recovery approvals (waiting > 30 min)",
    ["namespace"],
)

# 현재 복구 상태 게이지
RECOVERY_CURRENT_STATUS = Gauge(
    "selfhealing_recovery_current_status",
    "Current recovery status (0=normal, 1=emergency, 2=recovering, 3=ready_to_restore)",
    ["namespace"],
)

# 복구 재시도 카운터
RECOVERY_RETRIES_TOTAL = Counter(
    "selfhealing_recovery_retries_total",
    "Total number of recovery step retries",
    ["namespace", "step_type"],
)

# 멱등성 스킵 카운터
RECOVERY_IDEMPOTENT_SKIPS_TOTAL = Counter(
    "selfhealing_recovery_idempotent_skips_total",
    "Total number of idempotent step skips",
    ["namespace", "step_type"],
)


# =============================================================================
# Metric Status Mapping
# =============================================================================

STATUS_TO_VALUE = {
    "normal": 0,
    "emergency": 1,
    "recovering": 2,
    "in_progress": 2,
    "ready_to_restore": 3,
    "health_check": 2,
    "completed": 0,
    "failed": 1,
    "aborted": 1,
}


# =============================================================================
# Metrics Recorder
# =============================================================================


class RecoveryMetricsRecorder:
    """
    복구 메트릭 기록기.

    복구 프로세스의 각 단계에서 Prometheus 메트릭을 기록합니다.

    Usage:
        recorder = get_recovery_metrics_recorder()

        # 세션 시작
        recorder.record_session_started("global", "LEVEL_3")

        # 단계 완료
        recorder.record_step_completed("global", "budget_reset", duration=1.5)

        # 세션 완료
        recorder.record_session_completed("global", "LEVEL_3", "completed", duration=120)
    """

    def __init__(self):
        """초기화."""
        self._lock = threading.Lock()
        # 내부 상태 추적 (Prometheus 없을 때 대체용)
        self._internal_metrics: dict[str, Any] = {
            "sessions": {},
            "steps": {},
            "pending_approvals": {},
        }

    # =========================================================================
    # Session Metrics
    # =========================================================================

    def record_session_started(
        self,
        namespace: str,
        trigger_level: str,
    ) -> None:
        """
        세션 시작 기록.

        Args:
            namespace: 네임스페이스
            trigger_level: 트리거 레벨
        """
        RECOVERY_SESSIONS_TOTAL.labels(
            namespace=namespace,
            trigger_level=trigger_level,
            status="started",
        ).inc()

        self._set_status(namespace, "recovering")

        logger.debug(
            f"[RecoveryMetrics] Session started: "
            f"namespace={namespace}, level={trigger_level}"
        )

    def record_session_completed(
        self,
        namespace: str,
        trigger_level: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """
        세션 완료 기록.

        Args:
            namespace: 네임스페이스
            trigger_level: 트리거 레벨
            status: 최종 상태 (completed, failed, aborted)
            duration_seconds: 총 소요 시간 (초)
        """
        RECOVERY_SESSIONS_TOTAL.labels(
            namespace=namespace,
            trigger_level=trigger_level,
            status=status,
        ).inc()

        RECOVERY_DURATION_SECONDS.labels(
            namespace=namespace,
            trigger_level=trigger_level,
            status=status,
        ).observe(duration_seconds)

        final_status = "normal" if status == "completed" else "emergency"
        self._set_status(namespace, final_status)

        logger.debug(
            f"[RecoveryMetrics] Session completed: "
            f"namespace={namespace}, status={status}, duration={duration_seconds:.1f}s"
        )

    # =========================================================================
    # Step Metrics
    # =========================================================================

    def record_step_started(
        self,
        namespace: str,
        step_type: str,
    ) -> None:
        """
        단계 시작 기록.

        Args:
            namespace: 네임스페이스
            step_type: 단계 유형
        """
        RECOVERY_STEPS_TOTAL.labels(
            namespace=namespace,
            step_type=step_type,
            status="started",
        ).inc()

        logger.debug(
            f"[RecoveryMetrics] Step started: "
            f"namespace={namespace}, step={step_type}"
        )

    def record_step_completed(
        self,
        namespace: str,
        step_type: str,
        success: bool,
        duration_seconds: float,
        idempotent_skip: bool = False,
    ) -> None:
        """
        단계 완료 기록.

        Args:
            namespace: 네임스페이스
            step_type: 단계 유형
            success: 성공 여부
            duration_seconds: 소요 시간 (초)
            idempotent_skip: 멱등성 스킵 여부
        """
        status = "completed" if success else "failed"

        RECOVERY_STEPS_TOTAL.labels(
            namespace=namespace,
            step_type=step_type,
            status=status,
        ).inc()

        RECOVERY_STEP_DURATION_SECONDS.labels(
            namespace=namespace,
            step_type=step_type,
        ).observe(duration_seconds)

        if idempotent_skip:
            RECOVERY_IDEMPOTENT_SKIPS_TOTAL.labels(
                namespace=namespace,
                step_type=step_type,
            ).inc()

        logger.debug(
            f"[RecoveryMetrics] Step completed: "
            f"namespace={namespace}, step={step_type}, "
            f"success={success}, duration={duration_seconds:.2f}s"
        )

    def record_step_retry(
        self,
        namespace: str,
        step_type: str,
    ) -> None:
        """
        단계 재시도 기록.

        Args:
            namespace: 네임스페이스
            step_type: 단계 유형
        """
        RECOVERY_RETRIES_TOTAL.labels(
            namespace=namespace,
            step_type=step_type,
        ).inc()

        logger.debug(
            f"[RecoveryMetrics] Step retry: " f"namespace={namespace}, step={step_type}"
        )

    # =========================================================================
    # Circuit Breaker Metrics
    # =========================================================================

    def record_circuit_breaker_trip(
        self,
        namespace: str,
        reason: str,
    ) -> None:
        """
        서킷 브레이커 트립 기록.

        Args:
            namespace: 네임스페이스
            reason: 트립 사유
        """
        RECOVERY_CIRCUIT_BREAKER_TRIPS_TOTAL.labels(
            namespace=namespace,
            reason=reason,
        ).inc()

        self._set_status(namespace, "emergency")

        logger.warning(
            f"[RecoveryMetrics] Circuit breaker tripped: "
            f"namespace={namespace}, reason={reason}"
        )

    # =========================================================================
    # Approval Metrics
    # =========================================================================

    def update_pending_approvals(
        self,
        namespace: str,
        count: int,
        stale_count: int = 0,
    ) -> None:
        """
        대기 중인 승인 수 업데이트.

        Args:
            namespace: 네임스페이스
            count: 대기 중인 승인 수
            stale_count: 방치된 승인 수
        """
        RECOVERY_PENDING_APPROVALS.labels(
            namespace=namespace,
        ).set(count)

        RECOVERY_STALE_APPROVALS.labels(
            namespace=namespace,
        ).set(stale_count)

        if count > 0:
            self._set_status(namespace, "ready_to_restore")

    # =========================================================================
    # Status Metrics
    # =========================================================================

    def _set_status(
        self,
        namespace: str,
        status: str,
    ) -> None:
        """
        현재 상태 설정.

        Args:
            namespace: 네임스페이스
            status: 상태 문자열
        """
        value = STATUS_TO_VALUE.get(status, 0)
        RECOVERY_CURRENT_STATUS.labels(
            namespace=namespace,
        ).set(value)

    def set_current_status(
        self,
        namespace: str,
        status: str,
    ) -> None:
        """
        현재 상태 명시적 설정.

        Args:
            namespace: 네임스페이스
            status: 상태 문자열
        """
        self._set_status(namespace, status)

    # =========================================================================
    # Batch Update
    # =========================================================================

    def update_from_coordinator_state(
        self,
        namespace: str,
        status: str,
        active_session: bool,
        pending_count: int,
        stale_count: int,
    ) -> None:
        """
        Coordinator 상태에서 일괄 업데이트.

        주기적으로 호출하여 상태를 동기화합니다.

        Args:
            namespace: 네임스페이스
            status: 현재 상태
            active_session: 활성 세션 여부
            pending_count: 대기 중인 승인 수
            stale_count: 방치된 승인 수
        """
        self._set_status(namespace, status)
        self.update_pending_approvals(namespace, pending_count, stale_count)


# =============================================================================
# Context Manager for Step Timing
# =============================================================================


class StepTimer:
    """
    단계 타이밍 컨텍스트 매니저.

    Usage:
        with StepTimer(recorder, "global", "budget_reset") as timer:
            # 단계 실행
            result = execute_step()
            timer.set_success(result.get("success", False))
    """

    def __init__(
        self,
        recorder: RecoveryMetricsRecorder,
        namespace: str,
        step_type: str,
    ):
        """초기화."""
        self.recorder = recorder
        self.namespace = namespace
        self.step_type = step_type
        self.start_time: float | None = None
        self.success = True
        self.idempotent_skip = False

    def __enter__(self) -> StepTimer:
        """컨텍스트 진입."""
        self.start_time = time.time()
        self.recorder.record_step_started(self.namespace, self.step_type)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """컨텍스트 종료."""
        duration = time.time() - (self.start_time or time.time())

        if exc_type is not None:
            self.success = False

        self.recorder.record_step_completed(
            namespace=self.namespace,
            step_type=self.step_type,
            success=self.success,
            duration_seconds=duration,
            idempotent_skip=self.idempotent_skip,
        )

    def set_success(self, success: bool) -> None:
        """성공 여부 설정."""
        self.success = success

    def set_idempotent_skip(self) -> None:
        """멱등성 스킵 설정."""
        self.idempotent_skip = True


# =============================================================================
# Singleton
# =============================================================================

_metrics_recorder: RecoveryMetricsRecorder | None = None
_recorder_lock = threading.Lock()


def get_recovery_metrics_recorder() -> RecoveryMetricsRecorder:
    """RecoveryMetricsRecorder 싱글톤 반환."""
    global _metrics_recorder

    if _metrics_recorder is not None:
        return _metrics_recorder

    with _recorder_lock:
        if _metrics_recorder is None:
            _metrics_recorder = RecoveryMetricsRecorder()
        return _metrics_recorder


def reset_recovery_metrics_recorder() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _metrics_recorder
    with _recorder_lock:
        _metrics_recorder = None
