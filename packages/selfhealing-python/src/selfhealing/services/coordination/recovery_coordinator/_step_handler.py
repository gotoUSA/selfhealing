"""
StepHandlerMixin for RecoveryCoordinator.

이 모듈은 selfhealing.services.coordination.recovery_coordinator 패키지의 내부 구현입니다.
"""

from __future__ import annotations

import concurrent.futures
import logging
from selfhealing.settings.recovery_coordinator import get_recovery_coordinator_settings
from ..enums import RecoveryStatus
from ..recovery_state import RecoverySession, RecoveryStep, RecoveryStepType
from . import LOCK_HEARTBEAT_INTERVAL_SECONDS, StepTimeoutError

logger = logging.getLogger(__name__)


class StepHandlerMixin:
    """Step 실행 핸들러 Mixin.

    복구 단계별 핸들러와 타임아웃 래퍼를 제공합니다."""

    def _handle_budget_reset(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        Budget Multiplier 리셋.

        Crisis Multiplier를 기본값(1.0)으로 초기화.
        """
        target = step.params.get("target_multiplier", 1.0)

        try:
            # CrisisMultiplierProvider가 있으면 사용
            try:
                from selfhealing.services.coordination.crisis_multiplier import (
                    get_crisis_multiplier_provider,
                )

                provider = get_crisis_multiplier_provider()
                provider.reset_multiplier(session.namespace)
            except ImportError:
                logger.warning("[Recovery] CrisisMultiplierProvider not available, " "skipping budget reset")

            return {"success": True, "multiplier": target}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_health_check(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        안정화 검증.

        지정된 시간 동안 에러율이 임계값 이하인지 확인.
        """
        settings = get_recovery_coordinator_settings()

        # step.params에서 값이 없으면 settings에서 기본값 사용
        duration_minutes = step.params.get("duration_minutes", settings.stability_check_duration_minutes)
        error_rate_threshold = step.params.get("error_rate_threshold", settings.stability_check_error_rate_threshold)

        # Health Check 상태로 전환
        session.status = RecoveryStatus.HEALTH_CHECK

        stability = self._check_stability(
            namespace=session.namespace,
            duration_minutes=duration_minutes,
            error_rate_threshold=error_rate_threshold,
        )

        if stability["stable"]:
            # Health Check 완료 후 다시 IN_PROGRESS로
            session.status = RecoveryStatus.IN_PROGRESS
            return {"success": True, "stability": stability}
        else:
            return {
                "success": False,
                "error": f"Stability check failed: {stability['reason']}",
                "stability": stability,
            }

    def _handle_canary_resume(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        Canary 롤아웃 재개.

        Emergency로 인해 일시 중지된 Canary 롤아웃 재개.
        Whitelist 기반 필터링 및 순차 재개 지원.
        """
        resume_paused_only = step.params.get("resume_paused_only", True)

        # Whitelist 기반 필터링 (기본: error_budget만 재개)
        triggered_by_whitelist = step.params.get("triggered_by_whitelist", ["error_budget"])

        # 순차 재개 설정
        staggered_enabled = step.params.get("staggered_enabled", True)
        max_batch_size = step.params.get("max_batch_size", 5)
        interval_seconds = step.params.get("interval_seconds", 60)

        try:
            # CanaryService가 있으면 사용
            try:
                from selfhealing.services.canary import get_canary_service

                service = get_canary_service()

                if resume_paused_only:
                    if staggered_enabled:
                        # 순차 재개
                        resumed = service.resume_paused_rollouts_staggered(
                            namespace=session.namespace,
                            triggered_by_whitelist=triggered_by_whitelist,
                            max_batch_size=max_batch_size,
                            interval_seconds=interval_seconds,
                        )
                    else:
                        # 일괄 재개
                        resumed = service.resume_paused_rollouts(
                            namespace=session.namespace,
                            triggered_by_whitelist=triggered_by_whitelist,
                        )
                else:
                    resumed = service.resume_all_rollouts(session.namespace)

                return {
                    "success": True,
                    "resumed_count": len(resumed) if resumed else 0,
                    "staggered": staggered_enabled,
                    "triggered_by_whitelist": triggered_by_whitelist,
                }
            except (ImportError, AttributeError):
                logger.warning("[Recovery] CanaryService not available or missing method, " "skipping canary resume")
                return {"success": True, "resumed_count": 0, "skipped": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_governance_normal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        Governance NORMAL 모드 전환.

        STRICT 모드에서 NORMAL 모드로 전환하여 자동화 재활성화.
        """
        reason = step.params.get(
            "reason",
            "[AUTO-RECOVERY] Stability confirmed",
        )

        try:
            # EmergencyModeTracker가 있으면 사용
            try:
                from selfhealing.services.governance import get_emergency_tracker

                tracker = get_emergency_tracker()
                tracker.record_normal_restoration(
                    restored_by=session.initiated_by,
                    reason=reason,
                )
            except (ImportError, AttributeError):
                logger.warning("[Recovery] EmergencyModeTracker not available, " "skipping governance normal")
                return {"success": True, "mode": "NORMAL", "skipped": True}

            return {"success": True, "mode": "NORMAL"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Step Timeout Methods
    # =========================================================================

    def _get_step_timeout(self, step: RecoveryStep) -> int:
        """
        Step의 실효 타임아웃 결정.

        우선순위:
        1. step.timeout_seconds > 0 → Step 개별 설정 사용
        2. Settings의 Step 유형별 기본값
        3. Settings의 step_execution_timeout_seconds (전역 기본값)

        Args:
            step: RecoveryStep

        Returns:
            타임아웃 (초)
        """
        # 1. Step 개별 설정
        if step.timeout_seconds > 0:
            return step.timeout_seconds

        # 2. Step 유형별 Settings
        settings = get_recovery_coordinator_settings()
        type_timeout_map = {
            RecoveryStepType.BUDGET_RESET: settings.budget_reset_timeout_seconds,
            RecoveryStepType.HEALTH_CHECK: settings.health_check_timeout_seconds,
            RecoveryStepType.CANARY_RESUME: settings.canary_resume_timeout_seconds,
            RecoveryStepType.GOVERNANCE_NORMAL: settings.governance_normal_timeout_seconds,
        }

        type_timeout = type_timeout_map.get(step.step_type)
        if type_timeout and type_timeout > 0:
            return type_timeout

        # 3. 전역 기본값
        return settings.step_execution_timeout_seconds

    def _execute_with_timeout(
        self,
        handler_fn: Callable[[], dict[str, Any]],
        timeout_seconds: int,
        step: RecoveryStep,
        session: RecoverySession,
    ) -> dict[str, Any]:
        """
        핸들러를 타임아웃 + Lock Heartbeat + Django DB 안전 래퍼와 함께 실행.

        Args:
            handler_fn: 실행할 핸들러 함수 (인자 없는 callable)
            timeout_seconds: 타임아웃 (초)
            step: RecoveryStep (에러 보고용 + _stop_event 참조)
            session: RecoverySession (Lock extend용)

        Returns:
            핸들러 결과 dict

        Raises:
            StepTimeoutError: 타임아웃 초과 시
        """

        def _wrapped_handler() -> dict[str, Any]:
            """Django DB 커넥션 안전 래퍼."""
            try:
                from django.db import close_old_connections

                close_old_connections()
            except Exception:
                pass  # Django 미설치 또는 미설정 환경
            try:
                return handler_fn()
            finally:
                try:
                    from django.db import close_old_connections

                    close_old_connections()
                except Exception:
                    pass  # Django 미설치 또는 미설정 환경

        # ThreadPoolExecutor를 매 Step마다 생성 (Bulkhead 격리)
        # 이유: 전역 풀(max_workers=1)에서 좀비가 worker를 점유하면
        # 다음 Step 제출이 영구 block (Head-of-Line Blocking).
        # 복구 세션당 최대 4 Step이므로 생성 오버헤드 무시 가능.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_wrapped_handler)
            elapsed = 0
            heartbeat = LOCK_HEARTBEAT_INTERVAL_SECONDS

            # Polling 대기 루프: heartbeat 간격으로 Lock 연장
            while elapsed < timeout_seconds:
                remaining = timeout_seconds - elapsed
                wait_time = min(heartbeat, remaining)
                try:
                    result = future.result(timeout=wait_time)
                    return result
                except concurrent.futures.TimeoutError:
                    elapsed += wait_time
                    if elapsed >= timeout_seconds:
                        break
                    # Lock TTL 연장 (하트비트)
                    self._recovery_lock.extend(
                        session.namespace,
                        session.id,
                        additional_seconds=300,  # 5분 연장
                    )
                    logger.debug(
                        f"[Recovery] Lock heartbeat: step={step.step_type.value}, " f"elapsed={elapsed}s/{timeout_seconds}s"
                    )

            # 타임아웃 초과
            logger.error(
                f"[Recovery] Step TIMEOUT: {step.step_type.value}, " f"timeout={timeout_seconds}s, session={session.id}"
            )

            # 좀비 스레드에 종료 신호 (협력적 취소)
            stop_event = getattr(step, "_stop_event", None)
            if stop_event:
                stop_event.set()

            # 타임아웃 이벤트 발행 (Fail-Open)
            try:
                from selfhealing.services.event_bus import EventType, get_event_bus

                get_event_bus().emit(
                    event_type=EventType.EMERGENCY_RECOVERY_STARTED,
                    data={
                        "event": "step_timeout",
                        "step_type": step.step_type.value,
                        "timeout_seconds": timeout_seconds,
                        "session_id": session.id,
                    },
                    source="recovery_coordinator",
                )
            except Exception:
                pass

            raise StepTimeoutError(
                step_type=step.step_type.value,
                timeout_seconds=timeout_seconds,
            )
        finally:
            # wait=False: 좀비 스레드 완료를 기다리지 않고 즉시 반환
            # 좀비 스레드는 _stop_event.set()으로 협력적 취소 신호를 받음
            executor.shutdown(wait=False, cancel_futures=True)

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _get_recovery_steps(
        self,
        trigger_level: str,
        namespace: str = "global",
    ) -> list[RecoveryStep]:
        """
        복구 단계 목록 조회 (settings 기반 동적 생성).

        RecoveryCoordinatorSettings에서 레벨별 파라미터를 가져와
        복구 단계를 동적으로 생성합니다.
        """
        settings = get_recovery_coordinator_settings()

        # 레벨별 파라미터 매핑
        level_params = {
            "LEVEL_3": {
                "health_check_wait": settings.level3_health_check_wait_after,
                "health_check_duration": settings.level3_health_check_duration_minutes,
                "health_check_success": settings.level3_health_check_success_threshold,
                "health_check_error": settings.level3_health_check_error_rate_threshold,
                "canary_wait": settings.level3_canary_resume_wait_after,
                "governance_wait": settings.level3_governance_normal_wait_after,
                "include_governance": True,
            },
            "LEVEL_2": {
                "health_check_wait": settings.level2_health_check_wait_after,
                "health_check_duration": settings.level2_health_check_duration_minutes,
                "health_check_success": settings.level2_health_check_success_threshold,
                "health_check_error": settings.level2_health_check_error_rate_threshold,
                "canary_wait": settings.level2_canary_resume_wait_after,
                "include_governance": False,
            },
            "LEVEL_1": {
                "health_check_wait": settings.level1_health_check_wait_after,
                "health_check_duration": settings.level1_health_check_duration_minutes,
                "health_check_success": settings.level1_health_check_success_threshold,
                "health_check_error": settings.level1_health_check_error_rate_threshold,
                "include_governance": False,
            },
        }

        params = level_params.get(trigger_level)
        if not params:
            # 레거시 지원: DEFAULT_RECOVERY_STEPS에서 가져오기
            steps = self.DEFAULT_RECOVERY_STEPS.get(trigger_level, [])
            return [
                RecoveryStep(
                    step_type=s.step_type,
                    order=s.order,
                    wait_after_seconds=s.wait_after_seconds,
                    params=dict(s.params),
                )
                for s in steps
            ]

        # 동적 단계 생성
        steps: list[RecoveryStep] = []
        order = 1

        # Step 1: BUDGET_RESET (항상 포함)
        steps.append(
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=order,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            )
        )
        order += 1

        # Step 2: HEALTH_CHECK (항상 포함)
        steps.append(
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=order,
                wait_after_seconds=params.get("health_check_wait", 0),
                params={
                    "duration_minutes": params["health_check_duration"],
                    "success_threshold": params["health_check_success"],
                    "error_rate_threshold": params["health_check_error"],
                },
            )
        )
        order += 1

        # Step 3: CANARY_RESUME (LEVEL_1 제외)
        if trigger_level in ["LEVEL_3", "LEVEL_2"]:
            steps.append(
                RecoveryStep(
                    step_type=RecoveryStepType.CANARY_RESUME,
                    order=order,
                    wait_after_seconds=params.get("canary_wait", 60),
                    params={"resume_paused_only": True},
                )
            )
            order += 1

        # Step 4: GOVERNANCE_NORMAL (LEVEL_3만)
        if params.get("include_governance") and trigger_level == "LEVEL_3":
            steps.append(
                RecoveryStep(
                    step_type=RecoveryStepType.GOVERNANCE_NORMAL,
                    order=order,
                    wait_after_seconds=params.get("governance_wait", 300),
                    params={"reason": "[AUTO-RECOVERY] Stability confirmed"},
                )
            )

        return steps
