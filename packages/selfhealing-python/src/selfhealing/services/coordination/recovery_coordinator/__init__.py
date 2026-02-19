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

import concurrent.futures
import json
import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from selfhealing.settings.recovery_coordinator import get_recovery_coordinator_settings

from ..distributed_recovery_lock import (
    DistributedRecoveryLock,
    InMemoryRecoveryLock,
)
from ..enums import CompensationStatus, RecoveryStatus
from ..recovery_audit import (
    RecoveryAuditEventType,
    RecoveryAuditRecorder,
    get_recovery_audit_recorder,
)
from ..recovery_state import (
    CompensationResult,
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


# =============================================================================
# Step-Level Timeout 관련 예외 및 상수
# =============================================================================

LOCK_HEARTBEAT_INTERVAL_SECONDS = 60
"""Lock 하트비트 간격 (초). _execute_with_timeout() Polling 루프 주기."""


class StepTimeoutError(Exception):
    """Step 실행 시간 초과.

    _execute_with_timeout()에서 핸들러가 timeout_seconds 내에
    완료되지 않을 때 발생한다.
    """

    def __init__(self, step_type: str, timeout_seconds: int):
        self.step_type = step_type
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Step '{step_type}' timed out after {timeout_seconds}s")


class SessionVersionConflictError(Exception):
    """세션 저장 시 버전 충돌.

    좀비 스레드의 뒤늦은 저장을 차단하기 위한 OCC 예외.
    canary/versioning.py의 VersionConflictError와 패턴 통일.
    """

    def __init__(self, session_id: str, expected: int, actual: int):
        self.session_id = session_id
        self.expected_version = expected
        self.actual_version = actual
        super().__init__(f"Session version conflict: {session_id}, " f"expected v{expected}, actual v{actual}")


# Redis Lua 스크립트: version 기반 CAS (Compare-And-Set)
SESSION_CAS_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    redis.call("SET", KEYS[1], ARGV[1])
    return 1
end
local data = cjson.decode(current)
local expected_version = tonumber(ARGV[2])
if data["version"] == nil or data["version"] == expected_version then
    redis.call("SET", KEYS[1], ARGV[1])
    return 1
else
    return 0
end
"""



from ._step_handler import StepHandlerMixin
from ._session_persistence import SessionPersistenceMixin
from ._audit_recording import AuditRecordingMixin
from ._approval import ApprovalMixin


class RecoveryCoordinator(
    StepHandlerMixin,
    SessionPersistenceMixin,
    AuditRecordingMixin,
    ApprovalMixin,
):
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
        self._compensate_handlers: dict[RecoveryStepType, Callable] = {}
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
        # 보상 핸들러: 현재는 빈 dict (필요 시 등록)
        self._compensate_handlers = {}
    def register_step_handler(
        self,
        step_type: RecoveryStepType,
        handler: Callable[[RecoverySession, RecoveryStep], dict[str, Any]],
        compensate: Callable[[RecoverySession, RecoveryStep], dict[str, Any]] | None = None,
    ) -> None:
        """
        커스텀 단계 핸들러 등록.

        Args:
            step_type: 복구 단계 유형
            handler: Forward 핸들러 함수 (session, step) -> {"success": bool, ...}
            compensate: 보상 핸들러 함수 (선택). Step 실패 시 이전 성공 Step 역순 보상에 사용.
                    None이면 해당 Step은 보상 대상에서 제외.

        Note:
            이 시스템은 At-least-once 보상을 지향합니다.
            compensate 핸들러는 반드시 멱등성(Idempotency)을 보장해야 합니다.
            동일한 Step에 대해 compensate가 여러 번 호출되어도 동일한 결과를 보장해야 합니다.
            (서버 재시작, 네트워크 재시도 등으로 인해 중복 호출될 수 있음)

        Warning:
            1. handler 내부에서 RecoveryCoordinator의 public API를 직접 호출하면
               데드락이 발생할 수 있습니다. (self._lock이 RLock이지만
               ThreadPoolExecutor의 별도 스레드에서 실행되므로)
            2. 핸들러가 호출하는 모든 외부 서비스에는 반드시 라이브러리 레벨
               Timeout을 설정해야 합니다. (예: requests.get(url, timeout=30))
            3. 장기 실행 핸들러는 getattr(step, '_stop_event', None)으로
               취소 신호를 주기적으로 확인해야 합니다.
        """
        self._step_handlers[step_type] = handler
        if compensate is not None:
            self._compensate_handlers[step_type] = compensate

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
                RecoveryStatus.COMPENSATING,  # 보상 중에도 새 복구 차단
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

        개별 Step에 타임아웃을 적용하고, 협력적 취소(stop_event)를 주입하며,
        Lock 하트비트를 유지하면서 핸들러를 실행한다.

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
                self._handle_all_steps_completed(session)
                return None

            # 단계 실행 준비
            now = datetime.now(timezone.utc).isoformat()
            step.started_at = now
            step.status = RecoveryStatus.IN_PROGRESS

            # 협력적 취소 Event 주입 (좀비 스레드 종료 신호용)
            stop_event = threading.Event()
            step._stop_event = stop_event  # type: ignore[attr-defined]

            # Step 타임아웃 결정 (Step 개별 → 유형별 Settings → 전역 기본값)
            timeout = self._get_step_timeout(step)

            try:
                # 핸들러 선택
                idempotent_registry = self._get_idempotent_registry()

                if idempotent_registry and idempotent_registry.has_handler(step.step_type):
                    handler_fn = lambda: idempotent_registry.execute(session, step)
                else:
                    handler = self._step_handlers.get(step.step_type)
                    if not handler:
                        raise ValueError(f"No handler for step type: {step.step_type}")
                    handler_fn = lambda: handler(session, step)

                # 타임아웃 + Lock 하트비트 + Django DB 안전 래퍼 적용 실행
                result = self._execute_with_timeout(handler_fn, timeout, step, session)

                if result.get("success"):
                    step.status = RecoveryStatus.COMPLETED
                    step.completed_at = datetime.now(timezone.utc).isoformat()
                    step.result_data = result
                    step.compensation_status = CompensationStatus.PENDING
                    session.current_step_index += 1

                    # 멱등성 정보 로깅
                    idempotent_info = ""
                    if result.get("idempotent"):
                        idempotent_info = " (idempotent, cached)"
                    elif result.get("already_applied"):
                        idempotent_info = " (already applied)"

                    self._record_step_executed(session, step, success=True, result=result)

                    logger.info(
                        f"[Recovery] Step completed: {step.step_type.value}, " f"session={session.id}{idempotent_info}"
                    )
                else:
                    step.status = RecoveryStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")

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

            except StepTimeoutError:
                # 타임아웃 시 stop_event 설정 → 좀비 스레드에 종료 신호
                stop_event.set()

                step.status = RecoveryStatus.FAILED
                step.error_message = str(StepTimeoutError(step.step_type.value, timeout))

                self._record_step_executed(
                    session,
                    step,
                    success=False,
                    error_message=step.error_message,
                    result=None,
                )

                self._fail_session(session, step.error_message)

                logger.error(f"[Recovery] Step timeout: {step.step_type.value}, " f"session={session.id}, timeout={timeout}s")

            except Exception as e:
                step.status = RecoveryStatus.FAILED
                step.error_message = str(e)

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
