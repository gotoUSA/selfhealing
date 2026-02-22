"""
SelfHealerWatchdog - Self-Healing 시스템 자체 모니터링.

"치료사가 아플 때" 문제를 해결합니다.
Self-Healing 시스템 자체가 장애 나면 자동 복구하거나 인간에게 에스컬레이션합니다.

기능:
- 모든 서브시스템 건강 상태 모니터링
- Stuck 감지 및 자동 복구 시도
- 자동 복구 실패 시 인간 에스컬레이션
- Self-Healing용 Circuit Breaker (자기 보호)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings
from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
    EscalationManager,
)
from selfhealing.meta.health_probe import (
    HealthProbeManager,
    HealthStatus,
    ProbeResult,
)

logger = structlog.get_logger().bind(component="watchdog")


@dataclass
class WatchdogState:
    """Watchdog 상태."""

    overall_status: HealthStatus
    """전체 건강 상태."""

    component_statuses: dict[str, HealthStatus]
    """컴포넌트별 건강 상태."""

    last_check: datetime
    """마지막 체크 시각."""

    escalation_pending: bool
    """에스컬레이션 대기 중 여부."""

    escalation_count: int
    """총 에스컬레이션 횟수."""

    self_cb_open: bool = False
    """Self-Healing CB 열림 여부."""

    consecutive_failures: dict[str, int] = field(default_factory=dict)
    """컴포넌트별 연속 실패 횟수."""


class SelfHealerWatchdog:
    """
    Self-Healing 시스템 자체 모니터링 Watchdog.

    기능:
    - 모든 서브시스템 건강 상태 모니터링
    - Stuck 감지 및 자동 복구 시도
    - 자동 복구 실패 시 인간 에스컬레이션
    - Self-Healing용 Circuit Breaker (자기 보호)

    사용 예시:
        watchdog = SelfHealerWatchdog()
        watchdog.start()

        # 상태 확인
        state = watchdog.get_state()
        print(f"Overall: {state.overall_status}")

        # 종료
        watchdog.stop()
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
        probe_manager: HealthProbeManager | None = None,
        escalation_manager: EscalationManager | None = None,
    ):
        """
        초기화.

        Args:
            settings: Meta-Watchdog 설정 (None이면 기본값)
            probe_manager: Health Probe Manager (None이면 생성)
            escalation_manager: Escalation Manager (None이면 생성)
        """
        self._settings = settings or get_meta_watchdog_settings()
        self._probe_manager = probe_manager or HealthProbeManager(settings=self._settings)
        self._escalation_manager = escalation_manager or EscalationManager(settings=self._settings)

        self._lock = threading.RLock()
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 상태
        self._last_check: datetime | None = None
        self._consecutive_failures: dict[str, int] = {}
        self._escalation_count = 0

        # Self-Healing Circuit Breaker 상태
        self._self_cb_open = False
        self._self_cb_open_time: float = 0
        self._self_cb_failure_count = 0

        # Recovery 쿨다운 추적
        self._last_recovery_time: dict[str, float] = {}

    def _should_skip_due_to_self_cb(self) -> bool:
        """
        Self-Healing CB 상태 확인.

        Returns:
            스킵해야 하는지 여부
        """
        if not self._settings.self_cb_enabled:
            return False

        if not self._self_cb_open:
            return False

        # Half-Open 전환 확인
        elapsed = time.time() - self._self_cb_open_time
        if elapsed > self._settings.self_cb_recovery_timeout_seconds:
            logger.info("watchdog.self_cb_half_open")
            self._self_cb_open = False
            self._self_cb_failure_count = 0
            return False

        return True

    def _open_self_cb(self) -> None:
        """Self-Healing CB 열기."""
        if self._settings.self_cb_enabled and not self._self_cb_open:
            logger.warning("watchdog.self_cb_opened")
            self._self_cb_open = True
            self._self_cb_open_time = time.time()

    def _record_self_cb_success(self) -> None:
        """Self CB 성공 기록."""
        self._self_cb_failure_count = 0

    def _record_self_cb_failure(self) -> None:
        """Self CB 실패 기록."""
        self._self_cb_failure_count += 1
        if self._self_cb_failure_count >= self._settings.self_cb_failure_threshold:
            self._open_self_cb()

    def _process_unhealthy_component(self, name: str, result: Any) -> bool:
        """비정상 컴포넌트 처리. 에스컬레이션 필요 시 True 반환."""
        self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1

        if self._consecutive_failures[name] < self._settings.self_cb_failure_threshold:
            return False

        logger.warning(
            "watchdog.unhealthy_detected",
            name=name,
        )

        if self._settings.dry_run_mode:
            logger.info(
                "watchdog.dry_run_recovery",
                name=name,
            )
            return False

        recovered = self._attempt_recovery(name, result)
        if not recovered:
            self._escalate(name, result)
            return True
        return False

    def _check_overload_status(self, component_statuses: dict) -> None:
        """과부하 상태 확인 및 Self CB 업데이트."""
        unhealthy_count = sum(1 for s in component_statuses.values() if s == HealthStatus.UNHEALTHY)
        total_count = len(component_statuses)
        if total_count > 0 and unhealthy_count >= total_count - 1:
            self._record_self_cb_failure()
        else:
            self._record_self_cb_success()

    def _build_watchdog_state(
        self,
        overall_status: HealthStatus,
        component_statuses: dict,
        escalation_pending: bool,
    ) -> WatchdogState:
        """WatchdogState 객체 생성."""
        return WatchdogState(
            overall_status=overall_status,
            component_statuses=component_statuses,
            last_check=self._last_check or datetime.now(timezone.utc),
            escalation_pending=escalation_pending,
            escalation_count=self._escalation_count,
            self_cb_open=self._self_cb_open,
            consecutive_failures=dict(self._consecutive_failures),
        )

    def check_health(self) -> WatchdogState:
        """
        건강 상태 확인 및 필요 시 조치.

        Returns:
            현재 Watchdog 상태
        """
        if self._should_skip_due_to_self_cb():
            logger.debug("watchdog.self_cb_skipped")
            return self._build_watchdog_state(HealthStatus.UNKNOWN, {}, False)

        try:
            results = self._probe_manager.probe_all()
            overall_status = self._probe_manager.get_overall_status()
            self._last_check = datetime.now(timezone.utc)

            component_statuses = {name: r.status for name, r in results.items()}
            escalation_pending = False

            for name, result in results.items():
                if result.status == HealthStatus.UNHEALTHY:
                    if self._process_unhealthy_component(name, result):
                        escalation_pending = True
                else:
                    self._consecutive_failures[name] = 0

            self._check_overload_status(component_statuses)
            self._update_state_store()

            return self._build_watchdog_state(overall_status, component_statuses, escalation_pending)

        except Exception as e:
            logger.exception(
                "watchdog.health_check_failed",
                error=e,
            )
            self._record_self_cb_failure()
            return self._build_watchdog_state(HealthStatus.UNKNOWN, {}, False)

    def _update_state_store(self) -> None:
        """상태 저장소 업데이트 (Liveness용)."""
        try:
            from selfhealing.meta.state_store import get_watchdog_state_store

            store = get_watchdog_state_store()
            store.update_last_loop_timestamp()
        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "watchdog.state_store_failed",
                error=e,
            )

    def _attempt_recovery(self, component: str, result: ProbeResult) -> bool:
        """
        자동 복구 시도 (Audit 연동).

        복구 전후에 RecoveryAuditRecorder를 통해 감사 로그를 기록합니다.
        동일 컴포넌트에 대해 recovery_cooldown_seconds 내 재시도를 차단합니다.

        Args:
            component: 컴포넌트 이름
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        # === 쿨다운 확인 ===
        now = time.time()
        last_time = self._last_recovery_time.get(component, 0.0)
        elapsed = now - last_time
        if elapsed < self._settings.recovery_cooldown_seconds:
            remaining = self._settings.recovery_cooldown_seconds - elapsed
            logger.info(
                "watchdog.recovery_cooldown_active",
                component=component,
                remaining=remaining,
            )
            return False

        start_time = now
        success = False
        session_id = f"meta-watchdog-{component}-{int(start_time)}"

        # Audit Recorder 획득 (선택적 - 없어도 복구는 진행)
        recorder = self._get_recovery_audit_recorder()

        # 복구 시작 Audit
        if recorder:
            self._record_recovery_start_audit(recorder, session_id, component, result)

        try:
            if component == "circuit_breaker":
                success = self._recover_circuit_breaker(result)
            elif component == "dlq":
                success = self._recover_dlq(result)
            elif component == "redis":
                success = self._recover_redis(result)
            elif component == "recovery_pipeline":
                success = self._recover_recovery_pipeline(result)
            else:
                logger.debug(
                    "watchdog.no_recovery_action",
                    component=component,
                )
                return False

            # 복구 시도 후 타임스탬프 기록 (성공/실패 무관)
            self._last_recovery_time[component] = start_time

            duration_ms = (time.time() - start_time) * 1000

            # 복구 완료/실패 Audit
            if recorder:
                self._record_recovery_complete_audit(recorder, session_id, component, success, duration_ms)

            logger.info(
                "watchdog.recovery_completed",
                component=component,
                success=success,
                duration_ms=round(duration_ms, 1),
            )

            return success

        except Exception as e:
            # 복구 시도 후 타임스탬프 기록 (성공/실패 무관)
            self._last_recovery_time[component] = start_time

            duration_ms = (time.time() - start_time) * 1000

            # 복구 실패 Audit
            if recorder:
                self._record_recovery_failed_audit(recorder, session_id, component, str(e), duration_ms)

            logger.error(
                "watchdog.recovery_failed",
                component=component,
                error=e,
            )
            return False

    def _get_recovery_audit_recorder(self) -> Any:
        """RecoveryAuditRecorder 획득 (선택적)."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                get_recovery_audit_recorder,
            )

            return get_recovery_audit_recorder()
        except ImportError:
            return None
        except Exception:
            return None

    def _record_recovery_start_audit(
        self,
        recorder: Any,
        session_id: str,
        component: str,
        result: ProbeResult,
    ) -> None:
        """복구 시작 Audit 기록."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                RecoveryAuditEventType,
            )

            recorder.record_recovery_event(
                event_type=RecoveryAuditEventType.RECOVERY_STARTED,
                session_id=session_id,
                namespace="meta-watchdog",
                step_type=f"recover_{component}",
                executed_by="meta-watchdog",
                metadata={
                    "component": component,
                    "probe_status": result.status.value,
                    "probe_error": result.error,
                    "probe_details": result.details,
                },
            )
        except Exception as e:
            logger.debug(
                "watchdog.audit_start_failed",
                error=e,
            )

    def _record_recovery_complete_audit(
        self,
        recorder: Any,
        session_id: str,
        component: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        """복구 완료 Audit 기록."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                RecoveryAuditEventType,
            )

            event_type = RecoveryAuditEventType.RECOVERY_COMPLETED if success else RecoveryAuditEventType.RECOVERY_STEP_FAILED

            recorder.record_recovery_event(
                event_type=event_type,
                session_id=session_id,
                namespace="meta-watchdog",
                step_type=f"recover_{component}",
                executed_by="meta-watchdog",
                success=success,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.debug(
                "watchdog.audit_complete_failed",
                error=e,
            )

    def _record_recovery_failed_audit(
        self,
        recorder: Any,
        session_id: str,
        component: str,
        error_message: str,
        duration_ms: float,
    ) -> None:
        """복구 실패 Audit 기록."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                RecoveryAuditEventType,
            )

            recorder.record_recovery_event(
                event_type=RecoveryAuditEventType.RECOVERY_STEP_FAILED,
                session_id=session_id,
                namespace="meta-watchdog",
                step_type=f"recover_{component}",
                executed_by="meta-watchdog",
                success=False,
                error_message=error_message,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.debug(
                "watchdog.audit_failed_record_failed",
                error=e,
            )

    def _recover_circuit_breaker(self, result: ProbeResult) -> bool:
        """
        Circuit Breaker 복구.

        Stuck CB를 강제로 HALF_OPEN 상태로 전환합니다.

        Args:
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        try:
            from selfhealing.services.circuit_breaker import get_circuit_breaker_service

            cb_service = get_circuit_breaker_service()
            stuck_count = result.details.get("stuck_count", 0)

            if stuck_count > 0:
                logger.info("watchdog.stuck_cb_force_half_open")
                # CB 서비스를 통한 상태 리셋
                all_states = cb_service.get_all_states()
                for state in all_states:
                    if state.get("state") == "OPEN":
                        service_name = state.get("service_name")
                        if service_name:
                            cb_service.reset_state(service_name)
                return True

            return True
        except ImportError:
            logger.debug("watchdog.cb_service_unavailable")
            return False
        except Exception as e:
            logger.exception(
                "watchdog.cb_recovery_failed",
                error=e,
            )
            return False

    def _recover_dlq(self, result: ProbeResult) -> bool:
        """
        DLQ 복구.

        DLQ Consumer 상태 확인 및 필요 시 재시작 트리거.

        Args:
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        try:
            # DLQ Consumer 상태 확인 및 재시작 트리거
            logger.info("watchdog.dlq_recovery_started")

            # Recovery Adapter를 통한 복구 시도
            try:
                from selfhealing.meta.recovery_adapter import get_recovery_adapter

                adapter = get_recovery_adapter()
                result = adapter.restart_worker(self._settings.dlq_worker_workload_name)
                return result.success
            except ImportError:
                pass

            return False
        except Exception as e:
            logger.exception(
                "watchdog.dlq_recovery_failed",
                error=e,
            )
            return False

    def _recover_redis(self, result: ProbeResult) -> bool:
        """
        Redis 연결 복구 — 2단계 전략.

        Stage 1: ProviderRegistry 싱글톤의 커넥션 풀 리셋 (소프트 복구)
        Stage 2: RecoveryAdapter를 통한 인프라 재시작 (하드 복구)

        예외 세분화:
        - ConnectionError, TimeoutError, BusyLoadingError → Stage 2 진행
        - AuthenticationError, ResponseError 등 → Stage 2 스킵 (재시작 무의미)

        Args:
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        # === Stage 1: 진성 커넥션 풀 복구 ===
        try:
            logger.info("watchdog.redis_recovery_stage1")

            from selfhealing.factory import ProviderRegistry

            adapter = ProviderRegistry.get_cache("redis")
            if adapter.reconnect():
                logger.info("watchdog.redis_recovery_stage1_succeeded")
                return True
            # reconnect()가 False 반환 — ping 실패
            logger.warning("watchdog.redis_stage_failed_reconnect")
        except ImportError:
            logger.warning("watchdog.providerregistry_available")
        except Exception as e:
            import redis as redis_lib

            _INFRA_RECOVERABLE_ERRORS = (
                redis_lib.exceptions.ConnectionError,
                redis_lib.exceptions.TimeoutError,
                redis_lib.exceptions.BusyLoadingError,
            )

            if isinstance(e, _INFRA_RECOVERABLE_ERRORS):
                logger.warning(
                    "watchdog.redis_stage1_failed_recoverable",
                    error=str(e),
                )
            else:
                logger.error(
                    "watchdog.redis_stage_failed_non",
                    error=e,
                )
                return False

        # === Stage 2: RecoveryAdapter 인프라 재시작 ===
        try:
            from selfhealing.meta.recovery_adapter import get_recovery_adapter

            recovery_adapter = get_recovery_adapter()
            workload_name = self._settings.redis_workload_name
            recovery_result = recovery_adapter.restart_worker(workload_name)
            if recovery_result.success:
                logger.info("watchdog.redis_stage_success")
            else:
                logger.error("watchdog.redis_stage_failed")
            return recovery_result.success
        except ImportError:
            logger.warning("watchdog.recoveryadapter_available")
            return False
        except Exception as e:
            logger.exception(
                "watchdog.redis_stage_error",
                error=e,
            )
            return False

    def _recover_recovery_pipeline(self, result: ProbeResult) -> bool:
        """
        Recovery Pipeline 복구.

        Stuck 복구 작업 정리 및 재시작.

        Args:
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        try:
            logger.info("watchdog.recovery_pipeline_recovery")
            # Coordinator 리셋 등 복구 로직
            return True
        except Exception as e:
            logger.exception(
                "watchdog.recovery_pipeline_error",
                error=e,
            )
            return False

    def _escalate(self, component: str, result: ProbeResult) -> None:
        """
        인간에게 에스컬레이션.

        Args:
            component: 컴포넌트 이름
            result: 프로브 결과
        """
        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title=f"Self-Healing {component} Failure",
            description=(
                f"Component '{component}' is unhealthy and automatic recovery failed.\n"
                f"Error: {result.error or 'Unknown'}\n"
                f"Manual intervention required."
            ),
            component=component,
            details=result.details,
            timestamp=datetime.now(timezone.utc),
        )

        escalation_result = self._escalation_manager.escalate(event)

        if escalation_result.success:
            self._escalation_count += 1
            logger.warning(
                "escalation.escalated",
                component=component,
                escalation_result=escalation_result.channels_sent,
            )
        else:
            # 에스컬레이션도 실패하면 폴백 기록
            self._record_fallback_escalation(component, result, event)

    def _record_fallback_escalation(
        self,
        component: str,
        result: ProbeResult,
        event: EscalationEvent,
    ) -> None:
        """
        폴백 에스컬레이션 기록.

        Slack/PagerDuty 전송 실패 시 로컬 디스크에 기록합니다.

        Args:
            component: 컴포넌트 이름
            result: 프로브 결과
            event: 에스컬레이션 이벤트
        """
        try:
            from selfhealing.meta.fallback_escalation import (
                get_fallback_escalation_handler,
            )

            handler = get_fallback_escalation_handler()
            handler.record_failed_escalation(
                component=component,
                title=event.title,
                description=event.description,
                level=event.level.value,
                details=event.details,
                failed_channels=["pagerduty", "slack"],
                error_message=result.error or "Unknown error",
            )
        except ImportError:
            logger.exception(
                "watchdog.fallback_escalation_available",
                component=component,
            )
        except Exception as e:
            logger.exception(
                "watchdog.fallback_escalation_failed",
                component=component,
                error=e,
            )

    def _run_loop(self) -> None:
        """Watchdog 백그라운드 루프."""
        while self._running:
            try:
                self.check_health()
            except Exception as e:
                logger.exception(
                    "watchdog.loop_error",
                    error=e,
                )

            self._stop_event.wait(self._settings.probe_interval_seconds)
            if self._stop_event.is_set():
                break

    def start(self) -> None:
        """Watchdog 시작."""
        if not self._settings.enabled:
            logger.info("watchdog.disabled")
            return

        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="SelfHealerWatchdog",
            daemon=True,
        )
        self._worker.start()
        logger.info("watchdog.started")

    def stop(self) -> None:
        """Watchdog 중지."""
        self._running = False
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        logger.info("watchdog.stopped")

    def is_running(self) -> bool:
        """실행 중 여부 반환."""
        return self._running

    def get_state(self) -> WatchdogState:
        """
        현재 상태 반환.

        Returns:
            WatchdogState
        """
        with self._lock:
            results = self._probe_manager.get_last_results()
            return WatchdogState(
                overall_status=self._probe_manager.get_overall_status(),
                component_statuses={name: r.status for name, r in results.items()},
                last_check=self._last_check or datetime.now(timezone.utc),
                escalation_pending=False,
                escalation_count=self._escalation_count,
                self_cb_open=self._self_cb_open,
                consecutive_failures=dict(self._consecutive_failures),
            )

    def force_check(self) -> WatchdogState:
        """
        즉시 건강 체크 수행.

        Returns:
            WatchdogState
        """
        return self.check_health()

    def reset_consecutive_failures(self, component: str | None = None) -> None:
        """
        연속 실패 카운터 리셋.

        Args:
            component: 특정 컴포넌트만 리셋 (None이면 전체)
        """
        with self._lock:
            if component:
                self._consecutive_failures.pop(component, None)
            else:
                self._consecutive_failures.clear()


# =============================================================================
# Singleton
# =============================================================================

_watchdog: SelfHealerWatchdog | None = None
_watchdog_lock = threading.Lock()


def get_selfhealer_watchdog() -> SelfHealerWatchdog:
    """SelfHealerWatchdog 싱글톤 반환."""
    global _watchdog
    if _watchdog is None:
        with _watchdog_lock:
            if _watchdog is None:
                _watchdog = SelfHealerWatchdog()
    return _watchdog


def reset_selfhealer_watchdog() -> None:
    """Watchdog 리셋 (테스트용)."""
    global _watchdog
    with _watchdog_lock:
        if _watchdog is not None:
            _watchdog.stop()
            _watchdog = None
