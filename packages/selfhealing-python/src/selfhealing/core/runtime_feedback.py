"""
Runtime Feedback Loop - 실시간 메트릭 기반 자동 튜닝

Netflix Hystrix, Google Autopilot 스타일의 자율 조정 시스템

핵심 흐름:
1. 메트릭 수집 (Prometheus/Datadog/Internal)
2. 조정 필요 여부 판단 (Decision Engine)
3. 안전 한계 검증 (Safety Bounds)
4. 설정 적용 + 감사 로그 + 알림
5. 실패 시 자동 롤백 (Fallback 안전장치)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

import structlog

from selfhealing.settings.runtime_feedback import get_runtime_feedback_settings

logger = structlog.get_logger()


class FeedbackLoopState(str, Enum):
    """피드백 루프 상태"""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class AdjustmentResult:
    """조정 결과"""

    success: bool
    parameter: str
    old_value: float
    new_value: float
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None
    rollback_available: bool = True


class MetricsAdapter(Protocol):
    """메트릭 수집 어댑터 프로토콜"""

    def fetch_current_metrics(self) -> dict[str, float]:
        """현재 메트릭 수집"""
        ...


class ConfigApplier(Protocol):
    """설정 적용기 프로토콜"""

    def get_current(self, parameter: str) -> float:
        """현재 값 조회"""
        ...

    def apply(self, parameter: str, value: float) -> bool:
        """설정 적용"""
        ...

    def rollback(self, parameter: str, value: float) -> bool:
        """롤백 적용"""
        ...


class RuntimeFeedbackLoop:
    """
    실시간 피드백 루프

    자동 튜닝 + 실패 시 자동 롤백 안전장치 포함

    Safety Features:
    - 조정 전 이전 값 스냅샷 저장
    - 조정 후 헬스체크 (metrics degradation detection)
    - 문제 감지 시 자동 롤백
    - 연속 실패 시 피드백 루프 일시 정지

    설정값은 RuntimeFeedbackSettings를 통해 환경변수로 오버라이드 가능:
    - SELFHEALING_RUNTIME_MAX_CONSECUTIVE_FAILURES
    - SELFHEALING_RUNTIME_ROLLBACK_COOLDOWN
    - SELFHEALING_RUNTIME_ADJUSTMENT_WAIT
    """

    @property
    def MAX_CONSECUTIVE_FAILURES(self) -> int:
        """최대 연속 실패 횟수 - 초과 시 자동 일시 정지"""
        return get_runtime_feedback_settings().max_consecutive_failures

    @property
    def POST_ROLLBACK_COOLDOWN(self) -> int:
        """롤백 후 안정화 대기 시간 (초)"""
        return get_runtime_feedback_settings().rollback_cooldown

    @property
    def POST_ADJUSTMENT_WAIT(self) -> int:
        """조정 후 효과 확인 대기 시간 (초)"""
        return get_runtime_feedback_settings().adjustment_wait

    def __init__(
        self,
        metrics_adapter: MetricsAdapter,
        decision_engine,  # DecisionEngine
        safety_bounds,  # SafetyBounds
        audit_adapter,  # AuditLogAdapter
        alert_manager,  # GateAlertManager 또는 호환 인터페이스
        config_applier: ConfigApplier,
        enabled: bool = True,
        interval_seconds: int = 60,
        auto_rollback_enabled: bool = True,
    ):
        self.metrics_adapter = metrics_adapter
        self.decision_engine = decision_engine
        self.safety_bounds = safety_bounds
        self.audit_adapter = audit_adapter
        self.alert_manager = alert_manager
        self.config_applier = config_applier

        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.auto_rollback_enabled = auto_rollback_enabled

        # 상태 관리
        self._state = FeedbackLoopState.STOPPED
        self._lock = threading.RLock()
        self._running = False
        self._stop_event = threading.Event()  # 빠른 종료를 위한 이벤트
        self._thread: threading.Thread | None = None

        # 롤백용 스냅샷
        self._adjustment_history: list[AdjustmentResult] = []
        self._snapshot_before_adjustment: dict[str, float] = {}
        self._consecutive_failures = 0
        self._last_rollback_time: datetime | None = None

        # 메트릭 베이스라인 (조정 전 baseline)
        self._baseline_metrics: dict[str, float] | None = None

        logger.info("initialized")

    @property
    def state(self) -> FeedbackLoopState:
        """현재 상태"""
        return self._state

    def start(self) -> bool:
        """피드백 루프 시작"""
        with self._lock:
            if self._running:
                logger.warning("runtime_feedback.already_running")
                return False

            self._running = True
            self._stop_event.clear()  # 이벤트 초기화
            self._state = FeedbackLoopState.RUNNING
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("started")
            return True

    def stop(self) -> bool:
        """피드백 루프 중지"""
        with self._lock:
            self._running = False
            self._state = FeedbackLoopState.STOPPED
            self._stop_event.set()  # 대기 중인 스레드 즉시 깨우기

        if self._thread:
            self._thread.join(timeout=1)  # 1초면 충분 (Event로 즉시 깨어남)
            self._thread = None

        logger.info("stopped")
        return True

    def pause(self, reason: str = "manual") -> bool:
        """피드백 루프 일시 정지"""
        with self._lock:
            self._state = FeedbackLoopState.PAUSED
            logger.warning(
                "runtime_feedback.paused",
                reason=reason,
            )
            self._send_alert(
                "feedback_loop_paused", f"RuntimeFeedback 일시 정지됨: {reason}"
            )
            return True

    def resume(self) -> bool:
        """피드백 루프 재개"""
        with self._lock:
            if not self._running:
                logger.warning("runtime_feedback.running_cannot_resume")
                return False

            self._state = FeedbackLoopState.RUNNING
            self._consecutive_failures = 0
            logger.info("resumed")
            return True

    def _run_loop(self):
        """메인 루프"""
        while self._running:
            try:
                if self._state == FeedbackLoopState.RUNNING and self.enabled:
                    self.observe_and_adjust()
            except Exception as e:
                logger.exception(
                    "runtime_feedback.loop_error",
                    error=e,
                )
                self._handle_loop_error(e)

            # Event.wait()는 set() 시 즉시 반환 (time.sleep 대신 사용)
            if self._stop_event.wait(timeout=self.interval_seconds):
                break  # 종료 신호 수신

    def _handle_loop_error(self, error: Exception):
        """루프 에러 핸들링"""
        with self._lock:
            self._consecutive_failures += 1

            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self.pause(f"연속 {self._consecutive_failures}회 실패: {error}")
                self._state = FeedbackLoopState.ERROR

    def observe_and_adjust(self) -> dict[str, Any]:
        """
        관찰 및 조정 수행

        Returns:
            조정 결과 딕셔너리
        """
        # 롤백 쿨다운 체크
        if self._is_in_rollback_cooldown():
            return {"adjusted": False, "reason": "in_rollback_cooldown"}

        # 1. 메트릭 수집
        try:
            metrics = self.metrics_adapter.fetch_current_metrics()
        except Exception as e:
            logger.exception(
                "runtime_feedback.metrics_fetch_failed",
                error=e,
            )
            return {
                "adjusted": False,
                "reason": "metrics_fetch_failed",
                "error": str(e),
            }

        # 2. 베이스라인 저장 (첫 실행 시)
        if self._baseline_metrics is None:
            self._baseline_metrics = metrics.copy()

        # 3. 조정 결정
        decisions = self.decision_engine.analyze(metrics)

        if not decisions:
            self._consecutive_failures = 0  # 정상 사이클
            return {"adjusted": False, "reason": "no_adjustment_needed"}

        adjustments_made = []

        for decision in decisions:
            result = self._apply_single_adjustment(decision, metrics)
            if result:
                adjustments_made.append(result)

        # 4. 조정 후 헬스체크 (자동 롤백 활성화 시)
        if self.auto_rollback_enabled and adjustments_made:
            self._schedule_health_check(adjustments_made, metrics)

        return {
            "adjusted": len(adjustments_made) > 0,
            "adjustments": [self._result_to_dict(r) for r in adjustments_made],
        }

    def _apply_single_adjustment(
        self, decision, current_metrics: dict[str, float]  # AdjustmentDecision
    ) -> AdjustmentResult | None:
        """단일 조정 적용"""
        # 1. 안전 한계 검증
        if not self.safety_bounds.is_within_bounds(
            decision.parameter, decision.suggested_value
        ):
            logger.warning(
                "runtime_feedback.rejected_safety_bounds",
                decision=decision.parameter,
                decision_1=decision.suggested_value,
            )
            return None

        # 2. 현재 값 스냅샷 (롤백용)
        old_value = self.config_applier.get_current(decision.parameter)
        self._snapshot_before_adjustment[decision.parameter] = old_value

        # 3. 설정 적용
        try:
            success = self.config_applier.apply(
                decision.parameter, decision.suggested_value
            )
        except Exception as e:
            logger.exception(
                "runtime_feedback.apply_failed",
                error=e,
            )
            return AdjustmentResult(
                success=False,
                parameter=decision.parameter,
                old_value=old_value,
                new_value=decision.suggested_value,
                reason=decision.reason,
                error=str(e),
            )

        if not success:
            return None

        result = AdjustmentResult(
            success=True,
            parameter=decision.parameter,
            old_value=old_value,
            new_value=decision.suggested_value,
            reason=decision.reason,
        )

        # 4. 이력 저장
        self._adjustment_history.append(result)

        # 5. 감사 로그
        self._record_audit(result)

        # 6. 알림
        self._send_auto_tuning_alert(result)

        self._consecutive_failures = 0
        return result

    def _schedule_health_check(
        self, adjustments: list[AdjustmentResult], pre_metrics: dict[str, float]
    ):
        """
        조정 후 헬스체크 스케줄링

        별도 스레드에서 조정 효과 확인 후 문제 시 롤백
        """

        def _health_check():
            time.sleep(self.POST_ADJUSTMENT_WAIT)

            try:
                post_metrics = self.metrics_adapter.fetch_current_metrics()

                if self._detect_degradation(pre_metrics, post_metrics):
                    logger.warning("runtime_feedback.degradation_detected_initiating_rollback")
                    self._rollback_adjustments(adjustments)
            except Exception as e:
                logger.exception(
                    "runtime_feedback.health_check_failed",
                    error=e,
                )

        thread = threading.Thread(target=_health_check, daemon=True)
        thread.start()

    def _detect_degradation(
        self, pre_metrics: dict[str, float], post_metrics: dict[str, float]
    ) -> bool:
        """
        메트릭 저하 감지

        에러율 증가, 레이턴시 급증 등을 감지
        """
        # 에러율 증가 감지 (20% 이상 증가)
        pre_error = pre_metrics.get("error_rate", 0)
        post_error = post_metrics.get("error_rate", 0)

        if post_error > 0 and pre_error > 0:
            error_increase = (post_error - pre_error) / pre_error
            if error_increase > 0.2:
                logger.warning(
                    "runtime_feedback.error_rate_increased",
                    error_increase=error_increase,
                )
                return True
        elif post_error > 0.05 and pre_error == 0:
            # 0에서 5% 이상으로 급증
            return True

        # 레이턴시 급증 감지 (50% 이상 증가)
        pre_latency = pre_metrics.get("p99_latency_ms", 0)
        post_latency = post_metrics.get("p99_latency_ms", 0)

        if post_latency > 0 and pre_latency > 0:
            latency_increase = (post_latency - pre_latency) / pre_latency
            if latency_increase > 0.5:
                logger.warning(
                    "runtime_feedback.latency_increased",
                    latency_increase=latency_increase,
                )
                return True

        return False

    def _rollback_adjustments(self, adjustments: list[AdjustmentResult]):
        """
        조정 롤백 실행

        가장 최근 조정부터 역순으로 롤백
        """
        for result in reversed(adjustments):
            if not result.rollback_available:
                continue

            try:
                success = self.config_applier.rollback(
                    result.parameter, result.old_value
                )

                if success:
                    logger.info(
                        "runtime_feedback.rolled_back",
                        result=result.parameter,
                        result_1=result.new_value,
                        result_2=result.old_value,
                    )
                    self._record_rollback_audit(result)
                    self._send_rollback_alert(result)
            except Exception as e:
                logger.exception(
                    "runtime_feedback.rollback_failed",
                    error=e,
                )

        # 롤백 쿨다운 시작
        with self._lock:
            self._last_rollback_time = datetime.now(timezone.utc)
            self._consecutive_failures += 1

    def _is_in_rollback_cooldown(self) -> bool:
        """롤백 쿨다운 중인지 확인"""
        with self._lock:
            if self._last_rollback_time is None:
                return False

            elapsed = (
                datetime.now(timezone.utc) - self._last_rollback_time
            ).total_seconds()
            return elapsed < self.POST_ROLLBACK_COOLDOWN

    def _record_audit(self, result: AdjustmentResult):
        """감사 로그 기록"""
        try:
            from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry

            entry = AuditEntry(
                action=AuditAction.CONFIG_CHANGE,
                resource_type="auto_tuning",
                resource_id=result.parameter,
                details={
                    "type": "automatic_adjustment",
                    "old_value": result.old_value,
                    "new_value": result.new_value,
                    "reason": result.reason,
                },
                actor_type="system",
                actor_id="runtime_feedback_loop",
            )
            self.audit_adapter.log(entry)
        except Exception as e:
            logger.warning(
                "runtime_feedback.audit_log_failed",
                error=e,
            )

    def _record_rollback_audit(self, result: AdjustmentResult):
        """롤백 감사 로그 기록"""
        try:
            from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry

            entry = AuditEntry(
                action=AuditAction.CONFIG_CHANGE,
                resource_type="auto_tuning_rollback",
                resource_id=result.parameter,
                details={
                    "type": "automatic_rollback",
                    "rolled_back_from": result.new_value,
                    "rolled_back_to": result.old_value,
                    "original_reason": result.reason,
                    "rollback_reason": "degradation_detected",
                },
                actor_type="system",
                actor_id="runtime_feedback_loop",
            )
            self.audit_adapter.log(entry)
        except Exception as e:
            logger.warning(
                "runtime_feedback.rollback_audit_log_failed",
                error=e,
            )

    def _send_auto_tuning_alert(self, result: AdjustmentResult):
        """자율 조정 알림"""
        try:
            if hasattr(self.alert_manager, "send_auto_tuning_alert"):
                self.alert_manager.send_auto_tuning_alert(
                    parameter=result.parameter,
                    old_value=result.old_value,
                    new_value=result.new_value,
                    reason=result.reason,
                )
            else:
                self._send_alert(
                    "auto_tuning",
                    f"자동 조정: {result.parameter} {result.old_value} → {result.new_value}",
                )
        except Exception as e:
            logger.warning(
                "runtime_feedback.alert_failed",
                error=e,
            )

    def _send_rollback_alert(self, result: AdjustmentResult):
        """롤백 알림"""
        self._send_alert(
            "auto_rollback",
            f"🔄 자동 롤백: {result.parameter} {result.new_value} → {result.old_value} "
            f"(사유: 메트릭 저하 감지)",
        )

    def _send_alert(self, alert_type: str, message: str):
        """일반 알림"""
        try:
            if hasattr(self.alert_manager, "_send_notification"):
                self.alert_manager._send_notification(
                    title=f"[RuntimeFeedback] {alert_type}",
                    message=message,
                    severity="warning",
                )
            else:
                logger.info(
                    "runtime_feedback.event",
                    alert_type=alert_type,
                    message=message,
                )
        except Exception as e:
            logger.warning(
                "runtime_feedback.alert_failed",
                error=e,
            )

    def _result_to_dict(self, result: AdjustmentResult) -> dict[str, Any]:
        """결과를 딕셔너리로 변환"""
        return {
            "success": result.success,
            "parameter": result.parameter,
            "old_value": result.old_value,
            "new_value": result.new_value,
            "reason": result.reason,
            "timestamp": result.timestamp.isoformat(),
            "error": result.error,
        }

    def get_status(self) -> dict[str, Any]:
        """상태 조회"""
        with self._lock:
            return {
                "state": self._state.value,
                "enabled": self.enabled,
                "interval_seconds": self.interval_seconds,
                "auto_rollback_enabled": self.auto_rollback_enabled,
                "consecutive_failures": self._consecutive_failures,
                "in_rollback_cooldown": self._is_in_rollback_cooldown(),
                "adjustment_count": len(self._adjustment_history),
                "last_adjustments": [
                    self._result_to_dict(r) for r in self._adjustment_history[-5:]
                ],
            }

    def manual_rollback(self, parameter: str) -> bool:
        """수동 롤백"""
        with self._lock:
            if parameter not in self._snapshot_before_adjustment:
                logger.warning(
                    "runtime_feedback.no_snapshot",
                    parameter=parameter,
                )
                return False

            old_value = self._snapshot_before_adjustment[parameter]

            try:
                success = self.config_applier.rollback(parameter, old_value)
                if success:
                    logger.info(
                        "runtime_feedback.manual_rollback",
                        parameter=parameter,
                        old_value=old_value,
                    )
                return success
            except Exception as e:
                logger.exception(
                    "runtime_feedback.manual_rollback_failed",
                    error=e,
                )
                return False


__all__ = [
    "RuntimeFeedbackLoop",
    "FeedbackLoopState",
    "AdjustmentResult",
    "MetricsAdapter",
    "ConfigApplier",
]
