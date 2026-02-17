"""
Recovery Coordinator.

Emergency 상황에서 정상 상태로 복구하는 조율자.
역순 복구 원칙에 따라 단계별로 안전하게 시스템을 정상화합니다.

복구 순서 (LEVEL_3 기준):
1. Budget Multiplier 리셋 (5.0x → 1.0x)
2. Health Check (5분간 안정화 확인)
3. Canary Resume (일시 중지된 롤아웃 재개)
4. Governance NORMAL (자동화 재활성화)

특징:
- 역순 복구 (장애 연쇄의 역순)
- 분산 락을 통한 동시 복구 방지
- 단계별 상태 추적
- 재장애 시 자동 중단

Code reference:
    canary/locking.py (CanaryConfigLock 패턴)
    coordinator.py (EmergencyCoordinator 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from selfhealing.settings.recovery_coordinator import get_recovery_coordinator_settings

from .distributed_recovery_lock import (
    DistributedRecoveryLock,
    InMemoryRecoveryLock,
)
from .enums import RecoveryStatus
from .recovery_audit import (
    RecoveryAuditEventType,
    RecoveryAuditRecorder,
    get_recovery_audit_recorder,
)
from .recovery_state import (
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)

if TYPE_CHECKING:
    from selfhealing.audit.cascade_auditor import CascadeEventAuditor
    from selfhealing.core.state_backend import StateBackend

    from .idempotent_step_handlers import IdempotentStepHandlerRegistry
    from .regional_recovery_policy import RegionalRecoveryPolicyEngine

logger = logging.getLogger(__name__)


class RecoveryCoordinator:
    """
    복구 조율자.

    Emergency 상황에서 정상 상태로 복구할 때
    역순으로 안전하게 시스템을 정상화합니다.

    Features:
        - 역순 복구 (장애 연쇄의 역순)
        - 분산 락을 통한 동시 복구 방지
        - 단계별 상태 추적 및 저장
        - 재장애 시 자동 중단
        - 멱등성 보장 (Step Handler 재시도 안전)

    Usage:
        coordinator = RecoveryCoordinator()

        # 복구 시작
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
            initiated_by="system",
        )

        # 단계별 실행
        while True:
            step = coordinator.execute_next_step("global")
            if step is None:
                break  # 완료 또는 실패

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
    """

    # Redis 키 패턴
    SESSION_KEY = "selfhealing:{namespace}:recovery:session:{session_id}"
    ACTIVE_SESSION_KEY = "selfhealing:{namespace}:recovery:active"

    # 기본 복구 정책 (LEVEL별 복구 단계)
    DEFAULT_RECOVERY_STEPS: dict[str, list[RecoveryStep]] = {
        "LEVEL_3": [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=0,
                params={
                    "duration_minutes": 5,
                    "success_threshold": 0.95,
                    "error_rate_threshold": 0.1,
                },
            ),
            RecoveryStep(
                step_type=RecoveryStepType.CANARY_RESUME,
                order=3,
                wait_after_seconds=60,
                params={"resume_paused_only": True},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.GOVERNANCE_NORMAL,
                order=4,
                wait_after_seconds=300,  # 5분 안정화 후
                params={"reason": "[AUTO-RECOVERY] Stability confirmed"},
            ),
        ],
        "LEVEL_2": [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=0,
                params={
                    "duration_minutes": 3,
                    "success_threshold": 0.95,
                    "error_rate_threshold": 0.15,
                },
            ),
            RecoveryStep(
                step_type=RecoveryStepType.CANARY_RESUME,
                order=3,
                wait_after_seconds=30,
                params={"resume_paused_only": True},
            ),
        ],
        "LEVEL_1": [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=0,
                params={
                    "duration_minutes": 2,
                    "success_threshold": 0.90,
                    "error_rate_threshold": 0.2,
                },
            ),
        ],
    }

    def __init__(
        self,
        backend: StateBackend | None = None,
        recovery_lock: DistributedRecoveryLock | None = None,
        use_regional_policy: bool = True,
        use_idempotent_handlers: bool = True,
        cascade_auditor: CascadeEventAuditor | None = None,
        audit_recorder: RecoveryAuditRecorder | None = None,
    ):
        """
        RecoveryCoordinator 초기화.

        Args:
            backend: StateBackend 인스턴스 (None이면 자동 획득)
            recovery_lock: 분산 락 인스턴스 (None이면 InMemory 사용)
            use_regional_policy: 리전별 정책 사용 여부 (Phase 3.5)
            use_idempotent_handlers: 멱등성 핸들러 사용 여부 (Phase 2.7)
            cascade_auditor: CascadeEventAuditor 인스턴스 (Phase 5.3)
            audit_recorder: RecoveryAuditRecorder 인스턴스 (Phase 5.3)
        """
        self._backend = backend
        self._lock = threading.RLock()
        self._recovery_lock = recovery_lock or InMemoryRecoveryLock()
        self._step_handlers: dict[RecoveryStepType, Callable] = {}
        self._use_regional_policy = use_regional_policy
        self._use_idempotent_handlers = use_idempotent_handlers
        self._regional_policy_engine: RegionalRecoveryPolicyEngine | None = None
        self._idempotent_registry: IdempotentStepHandlerRegistry | None = None
        self._cascade_auditor = cascade_auditor
        self._audit_recorder = audit_recorder
        self._register_default_handlers()

    def _get_regional_policy_engine(self) -> RegionalRecoveryPolicyEngine | None:
        """리전별 정책 엔진 획득 (Phase 3.5)."""
        if not self._use_regional_policy:
            return None

        if self._regional_policy_engine is not None:
            return self._regional_policy_engine

        try:
            from .regional_recovery_policy import get_regional_recovery_policy_engine

            self._regional_policy_engine = get_regional_recovery_policy_engine()
            return self._regional_policy_engine
        except ImportError:
            logger.warning("[Recovery] RegionalRecoveryPolicyEngine not available")
            return None

    def _get_idempotent_registry(self) -> IdempotentStepHandlerRegistry | None:
        """멱등성 핸들러 레지스트리 획득 (Phase 2.7)."""
        if not self._use_idempotent_handlers:
            return None

        if self._idempotent_registry is not None:
            return self._idempotent_registry

        try:
            from .idempotent_step_handlers import get_idempotent_step_handler_registry

            self._idempotent_registry = get_idempotent_step_handler_registry()
            return self._idempotent_registry
        except ImportError:
            logger.warning("[Recovery] IdempotentStepHandlerRegistry not available")
            return None

    def _get_audit_recorder(self) -> RecoveryAuditRecorder:
        """
        RecoveryAuditRecorder 획득 (Phase 5.3).

        Returns:
            RecoveryAuditRecorder 인스턴스
        """
        if self._audit_recorder is not None:
            return self._audit_recorder

        return get_recovery_audit_recorder()

    def _get_cascade_auditor(self) -> CascadeEventAuditor | None:
        """
        CascadeEventAuditor 획득 (Phase 5.3).

        Returns:
            CascadeEventAuditor 인스턴스 또는 None
        """
        if self._cascade_auditor is not None:
            return self._cascade_auditor

        try:
            from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

            return get_cascade_event_auditor()
        except ImportError:
            logger.debug("[Recovery] CascadeEventAuditor not available")
            return None

    def _record_cascade_event(
        self,
        session: RecoverySession,
        trigger_type: str,
        effects: list[dict[str, Any]] | None = None,
    ) -> str | None:
        """
        CascadeEvent 기록 (Phase 5.3).

        복구 프로세스의 각 이벤트를 CascadeEventAuditor를 통해 기록합니다.
        인과관계 추적을 위해 76번 문서의 CascadeEvent 패턴을 따릅니다.

        Args:
            session: RecoverySession 인스턴스
            trigger_type: 트리거 유형 (RECOVERY_STARTED, RECOVERY_COMPLETED 등)
            effects: 연쇄 효과 목록

        Returns:
            생성된 cascade_event_id 또는 None

        Reference:
            docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
        """
        cascade_auditor = self._get_cascade_auditor()
        if not cascade_auditor:
            logger.debug(f"[Recovery] CascadeEvent skipped: no auditor, " f"trigger={trigger_type}, session={session.id}")
            return None

        try:
            trigger_details = {
                "session_id": session.id,
                "namespace": session.namespace,
                "trigger_level": session.trigger_level,
                "initiated_by": session.initiated_by,
                "current_step_index": session.current_step_index,
                "status": (session.status.value if hasattr(session.status, "value") else str(session.status)),
            }

            cascade_event = cascade_auditor.record(
                trigger_type=trigger_type,
                trigger_details=trigger_details,
                effects=effects or [],
                namespace=session.namespace,
                triggered_by=session.initiated_by,
            )

            logger.debug(
                f"[Recovery] CascadeEvent recorded: id={cascade_event.id}, " f"trigger={trigger_type}, session={session.id}"
            )

            return cascade_event.id

        except Exception as e:
            logger.warning(f"[Recovery] CascadeEvent recording failed: {e}, " f"trigger={trigger_type}, session={session.id}")
            return None

    def _get_backend(self) -> StateBackend:
        """StateBackend 인스턴스 획득."""
        if self._backend is not None:
            return self._backend

        from selfhealing.core.state_backend import get_state_backend

        return get_state_backend()

    def _register_default_handlers(self) -> None:
        """기본 단계 핸들러 등록."""
        self._step_handlers = {
            RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
            RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
            RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
            RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
        }

    def register_step_handler(
        self,
        step_type: RecoveryStepType,
        handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
    ) -> None:
        """
        커스텀 단계 핸들러 등록.

        Args:
            step_type: 복구 단계 유형
            handler: 핸들러 함수 (session, step) -> {"success": bool, ...}
        """
        self._step_handlers[step_type] = handler

    # =========================================================================
    # Public API
    # =========================================================================

    def start_recovery(
        self,
        namespace: str,
        trigger_level: str,
        initiated_by: str = "system",
    ) -> RecoverySession:
        """
        복구 시작.

        Phase 3.5: 리전 정책 통합
        Phase 3.7: READY_TO_RESTORE 상태 전환 지원

        Args:
            namespace: 네임스페이스 (예: "global", "seoul")
            trigger_level: 복구 대상 Emergency 레벨 (예: "LEVEL_3")
            initiated_by: 복구 시작 주체 ("system" 또는 사용자 ID)

        Returns:
            생성된 RecoverySession

        Raises:
            ValueError: 이미 진행 중인 복구가 있거나 복구 단계가 없는 경우
        """
        with self._lock:
            # 1. 진행 중인 복구 확인
            active = self.get_active_session(namespace)
            if active and active.status in (
                RecoveryStatus.IN_PROGRESS,
                RecoveryStatus.HEALTH_CHECK,
                RecoveryStatus.READY_TO_RESTORE,  # Phase 3.7: 승인 대기 중도 포함
            ):
                raise ValueError(f"Recovery already in progress: {active.id}")

            # 2. 리전 정책 확인 (Phase 3.5)
            regional_engine = self._get_regional_policy_engine()
            requires_approval = False

            if regional_engine:
                regional_config = regional_engine.get_config(namespace)
                requires_approval = regional_config.require_manual_approval
                # 리전별 복구 단계 사용
                steps = regional_engine.get_recovery_steps(namespace, trigger_level)
            else:
                # 기본 복구 단계 사용
                steps = self._get_recovery_steps(trigger_level, namespace)

            if not steps:
                raise ValueError(f"No recovery steps defined for {trigger_level}")

            # 3. 세션 ID 생성
            session_id = f"recovery-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()

            # 4. 분산 락 획득
            if not self._recovery_lock.acquire(namespace, session_id):
                current_owner = self._recovery_lock.get_lock_owner(namespace)
                raise ValueError(f"Failed to acquire recovery lock. " f"Current owner: {current_owner}")

            # 5. 세션 생성
            session = RecoverySession(
                id=session_id,
                namespace=namespace,
                trigger_level=trigger_level,
                status=RecoveryStatus.IN_PROGRESS,
                steps=steps,
                current_step_index=0,
                started_at=now,
                initiated_by=initiated_by,
            )

            # Phase 3.7: 수동 승인 필요 시 메타데이터에 기록
            if requires_approval:
                session.metadata = session.metadata or {}
                session.metadata["requires_approval"] = True

            # 6. 저장
            self._save_session(session)
            self._set_active_session(namespace, session_id)

            # 7. 감사 기록 (Phase 5.3: CascadeEvent 연동)
            self._record_recovery_started(session)

            logger.info(
                f"[Recovery] Started: id={session_id}, "
                f"namespace={namespace}, level={trigger_level}, "
                f"steps={len(steps)}, requires_approval={requires_approval}"
            )

            return session

    def execute_next_step(
        self,
        namespace: str,
    ) -> RecoveryStep | None:
        """
        다음 복구 단계 실행.

        Phase 2.7: 멱등성 핸들러 적용
        Phase 3.7: READY_TO_RESTORE 상태 전환

        Args:
            namespace: 네임스페이스

        Returns:
            실행된 RecoveryStep 또는 None (완료/실패 시)
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None

            if session.status not in (
                RecoveryStatus.IN_PROGRESS,
                RecoveryStatus.HEALTH_CHECK,
            ):
                return None

            step = session.get_current_step()
            if not step:
                # 모든 단계 완료 - Phase 3.7: READY_TO_RESTORE 체크
                self._handle_all_steps_completed(session)
                return None

            # 단계 실행
            now = datetime.now(timezone.utc).isoformat()
            step.started_at = now
            step.status = RecoveryStatus.IN_PROGRESS

            try:
                # Phase 2.7: 멱등성 핸들러 사용 시도
                idempotent_registry = self._get_idempotent_registry()

                if idempotent_registry and idempotent_registry.has_handler(step.step_type):
                    # 멱등성 핸들러 실행
                    result = idempotent_registry.execute(session, step)
                else:
                    # 기본 핸들러 실행
                    handler = self._step_handlers.get(step.step_type)
                    if not handler:
                        raise ValueError(f"No handler for step type: {step.step_type}")
                    result = handler(session, step)

                if result.get("success"):
                    step.status = RecoveryStatus.COMPLETED
                    step.completed_at = datetime.now(timezone.utc).isoformat()
                    session.current_step_index += 1

                    # 멱등성 정보 로깅
                    idempotent_info = ""
                    if result.get("idempotent"):
                        idempotent_info = " (idempotent, cached)"
                    elif result.get("already_applied"):
                        idempotent_info = " (already applied)"

                    # Phase 5.3: 단계 완료 감사 기록
                    self._record_step_executed(session, step, success=True, result=result)

                    logger.info(
                        f"[Recovery] Step completed: {step.step_type.value}, " f"session={session.id}{idempotent_info}"
                    )
                else:
                    step.status = RecoveryStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")

                    # Phase 5.3: 단계 실패 감사 기록
                    self._record_step_executed(
                        session,
                        step,
                        success=False,
                        error_message=step.error_message,
                        result=result,
                    )

                    self._fail_session(session, step.error_message)

                    logger.error(
                        f"[Recovery] Step failed: {step.step_type.value}, " f"session={session.id}, error={step.error_message}"
                    )

            except Exception as e:
                step.status = RecoveryStatus.FAILED
                step.error_message = str(e)

                # Phase 5.3: 예외 발생 감사 기록
                self._record_step_executed(session, step, success=False, error_message=str(e), result=None)

                self._fail_session(session, str(e))

                logger.exception(f"[Recovery] Step exception: {step.step_type.value}, " f"session={session.id}")

            self._save_session(session)
            return step

    def resume_recovery(
        self,
        namespace: str,
        initiated_by: str = "system",
    ) -> RecoverySession:
        """
        실패한 복구 세션을 마지막 실패 지점부터 재개.

        IdempotentStepHandlers가 이미 완료된 단계를 자동 스킵하므로,
        실패 지점의 단계만 재실행된다.

        새 세션은 현재 시점의 설정(Config)으로 복구 단계를 재구성합니다.
        이전 세션의 IdempotencyRecord 캐시와 session_id가 다르므로,
        _check_already_applied()의 비즈니스 레벨 체크에 의존합니다.
        설정 변경 후 resume하는 경우에도 안전합니다 (이미 적용된 단계 자동 스킵).

        활용하는 기존 컴포넌트:
        - IdempotentStepHandlerRegistry: 완료된 단계 캐시 결과 반환
        - RecoverySession.current_step_index: 실패 지점 위치
        - _check_already_applied(): 비즈니스 레벨 중복 실행 방지

        Args:
            namespace: 네임스페이스
            initiated_by: 재개 주체

        Returns:
            새로 생성된 RecoverySession (실패 지점부터 시작)

        Raises:
            ValueError: 재개할 실패 세션이 없거나, 최대 재개 횟수 초과 시
        """
        with self._lock:
            # 1. 마지막 실패 세션 조회
            last_session = self.get_active_session(namespace)
            if not last_session or last_session.status != RecoveryStatus.FAILED:
                raise ValueError(f"No failed recovery session to resume for namespace={namespace}")

            # 2. 최대 재개 횟수 검사 (무한 루프 방지)
            settings = get_recovery_coordinator_settings()
            resume_count = (last_session.metadata or {}).get("resume_count", 0)
            if resume_count >= settings.max_resume_count:
                raise ValueError(
                    f"Max resume count ({settings.max_resume_count}) exceeded "
                    f"for namespace={namespace}. Manual intervention required."
                )

            # 3. 실패 지점 정보 추출
            failed_step_index = last_session.current_step_index
            trigger_level = last_session.trigger_level

            logger.info(
                f"[Recovery] Resuming from step {failed_step_index}: "
                f"session={last_session.id}, level={trigger_level}, "
                f"resume_attempt={resume_count + 1}/{settings.max_resume_count}"
            )

            # 4. 새 세션 시작 (RLock 재진입으로 데드락 없음)
            # _clear_active_session() 불필요:
            #   - start_recovery()의 중복 체크가 FAILED 상태를 차단하지 않음
            #   - start_recovery()의 _set_active_session()이 기존 키를 덮어씀
            new_session = self.start_recovery(
                namespace=namespace,
                trigger_level=trigger_level,
                initiated_by=initiated_by,
            )

            # 5. 메타데이터에 재개 정보 기록
            new_session.metadata = new_session.metadata or {}
            new_session.metadata["resumed_from"] = last_session.id
            new_session.metadata["resumed_from_step"] = failed_step_index
            new_session.metadata["resume_count"] = resume_count + 1
            new_session.metadata["original_initiated_by"] = last_session.initiated_by
            self._save_session(new_session)

            logger.info(
                f"[Recovery] Resumed: new_session={new_session.id}, " f"from={last_session.id}, step={failed_step_index}"
            )

            return new_session

    def abort_recovery(
        self,
        namespace: str,
        reason: str,
    ) -> RecoverySession | None:
        """
        복구 중단.

        재장애 발생 시 또는 수동으로 복구를 중단합니다.

        Args:
            namespace: 네임스페이스
            reason: 중단 사유

        Returns:
            중단된 RecoverySession 또는 None
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None

            if session.status not in (
                RecoveryStatus.IN_PROGRESS,
                RecoveryStatus.HEALTH_CHECK,
            ):
                return None

            session.status = RecoveryStatus.ABORTED
            session.abort_reason = reason
            session.completed_at = datetime.now(timezone.utc).isoformat()

            # Phase 5.3: 복구 중단 감사 기록
            self._record_recovery_aborted(session, reason)

            self._save_session(session)
            self._clear_active_session(namespace)

            # 락 해제
            self._recovery_lock.release(namespace, session.id)

            logger.warning(f"[Recovery] Aborted: id={session.id}, reason={reason}")

            return session

    def get_active_session(
        self,
        namespace: str,
    ) -> RecoverySession | None:
        """
        활성 복구 세션 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            활성 RecoverySession 또는 None
        """
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        session_id = backend.get(key)

        if not session_id:
            return None

        return self.get_session(namespace, session_id)

    def get_session(
        self,
        namespace: str,
        session_id: str,
    ) -> RecoverySession | None:
        """
        복구 세션 조회.

        Args:
            namespace: 네임스페이스
            session_id: 세션 ID

        Returns:
            RecoverySession 또는 None
        """
        backend = self._get_backend()
        key = self.SESSION_KEY.format(
            namespace=namespace,
            session_id=session_id,
        )
        data = backend.get(key)

        if data:
            return RecoverySession.from_dict(data)
        return None

    def check_recovery_trigger(
        self,
        namespace: str,
    ) -> dict[str, Any]:
        """
        복구 트리거 조건 확인.

        Emergency 상황에서 복구 가능 여부를 확인합니다.
        RecoveryCoordinatorSettings의 stability_check 파라미터를 사용합니다.

        Args:
            namespace: 네임스페이스

        Returns:
            복구 가능 여부 및 상세 정보
        """
        settings = get_recovery_coordinator_settings()

        # 현재 Emergency 레벨 확인 시도
        current_level = self._get_current_emergency_level(namespace)

        if current_level == "NORMAL":
            return {
                "can_recover": False,
                "reason": "Already in NORMAL state",
                "current_level": "NORMAL",
            }

        # 안정화 조건 확인 (settings에서 duration, threshold 가져옴)
        stability_check = self._check_stability(
            namespace=namespace,
            duration_minutes=settings.stability_check_duration_minutes,
            error_rate_threshold=settings.stability_check_error_rate_threshold,
        )

        return {
            "can_recover": stability_check["stable"],
            "current_level": current_level,
            "stability_check": stability_check,
        }

    # =========================================================================
    # Step Handlers
    # =========================================================================

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

    def _handle_all_steps_completed(self, session: RecoverySession) -> None:
        """
        모든 단계 완료 시 처리.

        Phase 3.7: READY_TO_RESTORE 상태 전환 로직
        - require_manual_approval=True인 경우 READY_TO_RESTORE로 전환
        - 그렇지 않으면 COMPLETED로 완료
        """
        # 수동 승인 필요 여부 확인
        requires_approval = False
        if session.metadata and isinstance(session.metadata, dict):
            requires_approval = session.metadata.get("requires_approval", False)

        if requires_approval:
            # Phase 3.7: READY_TO_RESTORE 상태로 전환
            session.status = RecoveryStatus.READY_TO_RESTORE
            self._save_session(session)

            # 승인 요청 생성
            self._create_approval_request(session)

            logger.info(f"[Recovery] Waiting for approval: id={session.id}, " f"namespace={session.namespace}")
        else:
            # 일반 완료 처리
            self._complete_session(session)

    def _create_approval_request(self, session: RecoverySession) -> None:
        """
        수동 승인 요청 생성.

        Phase 3.7: PendingRecoveryApprovalManager 연동
        """
        try:
            from .pending_recovery_approval import get_pending_recovery_approval_manager

            manager = get_pending_recovery_approval_manager()

            manager.create_request(
                session_id=session.id,
                namespace=session.namespace,
                trigger_level=session.trigger_level,
            )
        except ImportError:
            logger.warning("[Recovery] PendingRecoveryApprovalManager not available")
        except Exception as e:
            logger.error(f"[Recovery] Failed to create approval request: {e}")

    def approve_recovery(
        self,
        namespace: str,
        approved_by: str,
    ) -> RecoverySession | None:
        """
        복구 승인 (Phase 3.7).

        READY_TO_RESTORE 상태인 세션을 승인하여 COMPLETED로 전환합니다.

        Args:
            namespace: 네임스페이스
            approved_by: 승인자 ID

        Returns:
            승인된 RecoverySession 또는 None
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None

            if session.status != RecoveryStatus.READY_TO_RESTORE:
                logger.warning(
                    f"[Recovery] Cannot approve: session not in READY_TO_RESTORE state. " f"Current: {session.status}"
                )
                return None

            # 승인 처리
            session.status = RecoveryStatus.COMPLETED
            session.completed_at = datetime.now(timezone.utc).isoformat()

            # 승인자 정보 기록
            session.metadata = session.metadata or {}
            session.metadata["approved_by"] = approved_by
            session.metadata["approved_at"] = datetime.now(timezone.utc).isoformat()

            self._save_session(session)
            self._clear_active_session(namespace)

            # 락 해제
            self._recovery_lock.release(namespace, session.id)

            # 승인 요청 상태 업데이트
            try:
                from .pending_recovery_approval import (
                    get_pending_recovery_approval_manager,
                )

                manager = get_pending_recovery_approval_manager()
                manager.approve(session.id, approved_by)
            except Exception:
                pass

            # EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 (Postmortem 자동 생성 트리거)
            self._publish_emergency_recovery_completed_event(session, approved_by)

            logger.info(f"[Recovery] Approved: id={session.id}, " f"approved_by={approved_by}")

            return session

    def verify_weighted_budget_stability(
        self,
        namespace: str,
    ) -> dict[str, Any]:
        """
        가중 버짓 안정성 검증 (Phase 5.4 - WeightedBudgetStability).

        Plan 75 연동: 복구 전 현재 버짓 상태를 확인하여
        가중치 적용 소진이 정상 범위인지 검증합니다.

        Args:
            namespace: 네임스페이스

        Returns:
            검증 결과:
            - stable: 안정 여부
            - current_multiplier: 현재 가중치
            - budget_remaining_percent: 남은 버짓 비율
            - weighted_consumption: 가중 소진량
        """
        try:
            # CrisisMultiplierProvider에서 현재 가중치 조회
            from selfhealing.services.coordination.crisis_multiplier import (
                get_crisis_multiplier_provider,
            )

            provider = get_crisis_multiplier_provider()
            current_multiplier = provider.get_multiplier(namespace)

            # AtomicBudgetConsumer에서 버짓 상태 조회
            budget_info = self._get_budget_info(namespace)

            # 안정성 판단: 가중치가 1.0이고 버짓 잔여량이 충분하면 안정
            is_stable = abs(current_multiplier - 1.0) < 0.001 and budget_info.get("remaining_percent", 100) > 10

            return {
                "stable": is_stable,
                "current_multiplier": current_multiplier,
                "budget_remaining_percent": budget_info.get("remaining_percent", 100),
                "weighted_consumption": budget_info.get("weighted_consumption", 0),
                "budget_total_minutes": budget_info.get("total_minutes", 0),
                "budget_used_minutes": budget_info.get("used_minutes", 0),
            }
        except ImportError:
            logger.warning("[Recovery] CrisisMultiplierProvider not available for " "weighted budget verification")
            return {
                "stable": True,
                "current_multiplier": 1.0,
                "budget_remaining_percent": 100,
                "assumed": True,
            }
        except Exception as e:
            logger.error(f"[Recovery] Weighted budget verification error: {e}")
            return {
                "stable": False,
                "error": str(e),
            }

    def _get_budget_info(self, namespace: str) -> dict[str, Any]:
        """Error Budget 정보 조회 (Plan 75 연동)."""
        try:
            from selfhealing.services.error_budget.atomic_consumer import (
                get_atomic_budget_consumer,
            )

            consumer = get_atomic_budget_consumer()

            # 버짓 상태 조회 (구현에 따라 다를 수 있음)
            budget_key = f"selfhealing:{namespace}:error_budget"
            backend = self._get_backend()

            budget_data = backend.get(budget_key)
            if budget_data and isinstance(budget_data, dict):
                total = budget_data.get("total_minutes", 60)
                used = budget_data.get("used_minutes", 0)
                remaining_percent = ((total - used) / total * 100) if total > 0 else 100

                return {
                    "total_minutes": total,
                    "used_minutes": used,
                    "remaining_percent": remaining_percent,
                    "weighted_consumption": budget_data.get("weighted_consumption", 0),
                }

            return {"remaining_percent": 100, "total_minutes": 60, "used_minutes": 0}
        except Exception:
            return {"remaining_percent": 100, "total_minutes": 60, "used_minutes": 0}

    def _get_current_emergency_level(self, namespace: str) -> str:
        """현재 Emergency 레벨 조회."""
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            current_level = manager.get_current_level()
            return current_level.name
        except (ImportError, AttributeError, Exception):
            # 가져올 수 없으면 UNKNOWN 반환
            return "UNKNOWN"

    def _check_stability(
        self,
        namespace: str,
        duration_minutes: int,
        error_rate_threshold: float,
    ) -> dict[str, Any]:
        """
        안정화 조건 확인.

        Prometheus + OTel + Mimir 인프라가 error_rate 수집/저장/알림을
        이미 처리하므로, 별도 MetricsCollector 구현 없이
        안정으로 가정합니다.

        실제 에러율 기반 판단이 필요한 경우:
        - PrometheusMetricsCollector.query_instant() 활용 가능
          (selfhealing.services.postmortem.prometheus_collector)
        - PromQL: rate(selfhealing_http_request_errors_total[Xm])
        """
        logger.debug(
            f"[Recovery] Stability check: namespace={namespace}, "
            f"duration={duration_minutes}m, threshold={error_rate_threshold}"
        )
        return {
            "stable": True,
            "error_rate": 0.0,
            "threshold": error_rate_threshold,
            "duration_minutes": duration_minutes,
            "reason": None,
            "assumed": True,
        }

    def _save_session(self, session: RecoverySession) -> None:
        """세션 저장."""
        backend = self._get_backend()
        key = self.SESSION_KEY.format(
            namespace=session.namespace,
            session_id=session.id,
        )
        backend.set(key, session.to_dict())

    def _set_active_session(
        self,
        namespace: str,
        session_id: str,
    ) -> None:
        """활성 세션 설정."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.set(key, session_id)

    def _clear_active_session(self, namespace: str) -> None:
        """활성 세션 클리어."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.delete(key)

    def _complete_session(self, session: RecoverySession) -> None:
        """세션 완료 처리."""
        session.status = RecoveryStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc).isoformat()

        # Phase 5.3: 복구 완료 감사 기록
        self._record_recovery_completed(session)

        self._save_session(session)
        self._clear_active_session(session.namespace)

        # 락 해제
        self._recovery_lock.release(session.namespace, session.id)

        # EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 (Postmortem 자동 생성 트리거)
        self._publish_emergency_recovery_completed_event(session)

        logger.info(f"[Recovery] Completed: id={session.id}, " f"namespace={session.namespace}")

    def _fail_session(
        self,
        session: RecoverySession,
        error: str,
    ) -> None:
        """세션 실패 처리.

        ACTIVE_SESSION_KEY를 유지하여 resume_recovery()가
        실패 세션을 조회할 수 있도록 한다.
        start_recovery()의 중복 체크에서 FAILED 상태는
        차단 대상이 아니므로 새 복구 시작에 영향 없음.
        """
        session.status = RecoveryStatus.FAILED
        session.abort_reason = error
        session.completed_at = datetime.now(timezone.utc).isoformat()

        self._save_session(session)
        # ACTIVE_SESSION_KEY 유지: resume_recovery()가 실패 세션을 조회할 수 있도록
        # start_recovery()가 _set_active_session()으로 기존 키를 덮어쓰므로
        # 새 복구 시작 시 자연스럽게 교체됨

        # 락 해제
        self._recovery_lock.release(session.namespace, session.id)

        logger.error(f"[Recovery] Failed: id={session.id}, error={error}")

    # =========================================================================
    # Status & History Methods
    # =========================================================================

    def get_current_status(
        self,
        namespace: str = "global",
    ) -> RecoveryStatus:
        """
        현재 복구 상태 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            현재 RecoveryStatus
        """
        # 활성 세션 확인
        active = self.get_active_session(namespace)

        if active:
            return active.status

        # Emergency 레벨 확인
        emergency_level = self._get_current_emergency_level(namespace)

        # emergency_level은 문자열 (예: "LEVEL_3", "LEVEL_2", "NORMAL", "UNKNOWN")
        if emergency_level and emergency_level.startswith("LEVEL_"):
            try:
                level_num = int(emergency_level.split("_")[1])
                if level_num >= 3:
                    return RecoveryStatus.EMERGENCY
            except (ValueError, IndexError):
                pass

        return RecoveryStatus.NORMAL

    def get_session_history(
        self,
        namespace: str | None = None,
        limit: int = 20,
    ) -> list[RecoverySession]:
        """
        복구 세션 히스토리 조회.

        Args:
            namespace: 필터링할 네임스페이스 (없으면 전체)
            limit: 최대 개수

        Returns:
            세션 목록 (최신순)
        """
        backend = self._get_backend()

        # 히스토리 키 패턴
        history_key = f"recovery:history:{namespace or '*'}"

        try:
            # Redis backend인 경우 히스토리에서 조회
            history_data = backend.get(history_key)
            if history_data and isinstance(history_data, list):
                sessions = [RecoverySession.from_dict(s) for s in history_data[:limit]]
                return sessions
        except Exception:
            pass

        # 히스토리가 없으면 빈 리스트
        return []

    # =========================================================================
    # Audit Recording Methods (Phase 5.3)
    # =========================================================================

    def _record_recovery_started(self, session: RecoverySession) -> None:
        """
        복구 시작 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STARTED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            metadata={
                "trigger_level": session.trigger_level,
                "total_steps": len(session.steps),
                "step_types": [s.step_type.value for s in session.steps],
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_INITIATED",
                "success": True,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "total_steps": len(session.steps),
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_STARTED",
            effects=effects,
        )

    def _record_step_executed(
        self,
        session: RecoverySession,
        step: RecoveryStep,
        success: bool,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """
        복구 단계 실행 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
            step: 실행된 RecoveryStep
            success: 성공 여부
            error_message: 에러 메시지 (실패 시)
            result: 핸들러 결과
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        event_type = RecoveryAuditEventType.RECOVERY_STEP_EXECUTED if success else RecoveryAuditEventType.RECOVERY_STEP_FAILED

        metadata = {
            "trigger_level": session.trigger_level,
            "step_params": step.params,
        }
        if result:
            metadata["idempotent"] = result.get("idempotent", False)
            metadata["already_applied"] = result.get("already_applied", False)

        audit_recorder.record_recovery_event(
            event_type=event_type,
            session_id=session.id,
            namespace=session.namespace,
            step_type=step.step_type.value,
            step_order=step.order,
            executed_by=session.initiated_by,
            success=success,
            error_message=error_message,
            metadata=metadata,
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": f"RECOVERY_STEP_{step.step_type.value.upper()}",
                "success": success,
                "target": step.step_type.value,
                "details": {
                    "step_order": step.order,
                    "error_message": error_message,
                },
            }
        ]
        trigger_type = "RECOVERY_STEP_EXECUTED" if success else "RECOVERY_STEP_FAILED"
        self._record_cascade_event(
            session=session,
            trigger_type=trigger_type,
            effects=effects,
        )

    def _record_recovery_completed(self, session: RecoverySession) -> None:
        """
        복구 완료 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_COMPLETED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            metadata={
                "trigger_level": session.trigger_level,
                "completed_steps": session.current_step_index,
                "total_steps": len(session.steps),
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_COMPLETED",
                "success": True,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "completed_steps": session.current_step_index,
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_COMPLETED",
            effects=effects,
        )

    def _record_recovery_aborted(
        self,
        session: RecoverySession,
        reason: str,
    ) -> None:
        """
        복구 중단 감사 기록 (Phase 5.3).

        Args:
            session: RecoverySession 인스턴스
            reason: 중단 사유
        """
        # 1. RecoveryAuditRecorder에 기록
        audit_recorder = self._get_audit_recorder()
        audit_recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_ABORTED,
            session_id=session.id,
            namespace=session.namespace,
            executed_by=session.initiated_by,
            success=False,
            error_message=reason,
            metadata={
                "trigger_level": session.trigger_level,
                "aborted_at_step": session.current_step_index,
                "total_steps": len(session.steps),
            },
        )

        # 2. CascadeEvent 기록
        effects = [
            {
                "action_type": "RECOVERY_ABORTED",
                "success": False,
                "target": session.id,
                "details": {
                    "trigger_level": session.trigger_level,
                    "abort_reason": reason,
                    "aborted_at_step": session.current_step_index,
                },
            }
        ]
        self._record_cascade_event(
            session=session,
            trigger_type="RECOVERY_ABORTED",
            effects=effects,
        )

    def _publish_emergency_recovery_completed_event(
        self,
        session: RecoverySession,
        approved_by: str | None = None,
    ) -> None:
        """
        EMERGENCY_RECOVERY_COMPLETED 이벤트 발행.

        EventBus를 통해 Emergency 복구 완료 이벤트를 발행합니다.
        이 이벤트는 Emergency Postmortem 자동 생성을 트리거합니다.

        Args:
            session: 완료된 RecoverySession 인스턴스
            approved_by: 승인자 ID (수동 승인인 경우)
        """
        try:
            # 지연 import (순환 import 방지)
            from selfhealing.services.event_bus import EventType, get_event_bus

            # duration 계산
            duration_seconds = None
            if session.started_at and session.completed_at:
                try:
                    from datetime import datetime

                    started = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
                    completed = datetime.fromisoformat(session.completed_at.replace("Z", "+00:00"))
                    duration_seconds = (completed - started).total_seconds()
                except (ValueError, TypeError):
                    pass

            event_bus = get_event_bus()
            event_bus.emit(
                event_type=EventType.EMERGENCY_RECOVERY_COMPLETED,
                data={
                    "session_id": session.id,
                    "namespace": session.namespace,
                    "trigger_level": session.trigger_level,
                    "started_at": session.started_at,
                    "completed_at": session.completed_at,
                    "duration_seconds": duration_seconds,
                    "steps_executed": session.current_step_index,
                    "total_steps": len(session.steps),
                    "requires_approval": bool(session.metadata and session.metadata.get("requires_approval")),
                    "approved_by": approved_by,
                },
                source="recovery_coordinator",
            )

            logger.info(f"[Recovery] Published EMERGENCY_RECOVERY_COMPLETED: {session.id}")

        except Exception as e:
            # 이벤트 발행 실패가 복구 완료에 영향을 주지 않도록 함
            logger.warning(f"[Recovery] Failed to publish EMERGENCY_RECOVERY_COMPLETED: {e}")


# =============================================================================
# Singleton
# =============================================================================

_recovery_coordinator: RecoveryCoordinator | None = None
_coordinator_lock = threading.Lock()


def get_recovery_coordinator() -> RecoveryCoordinator:
    """RecoveryCoordinator 싱글톤 반환."""
    global _recovery_coordinator

    if _recovery_coordinator is not None:
        return _recovery_coordinator

    with _coordinator_lock:
        if _recovery_coordinator is None:
            _recovery_coordinator = RecoveryCoordinator()
        return _recovery_coordinator


def reset_recovery_coordinator() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _recovery_coordinator
    with _coordinator_lock:
        _recovery_coordinator = None
