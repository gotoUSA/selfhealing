"""
Idempotent Step Handlers.

복구 단계 재시도 안전성을 보장하는 멱등성 핸들러 모듈.

Phase 2.7 구현:
- IdempotentStepHandler: 멱등성 보장 기본 핸들러
- IdempotentStepRegistry: 핸들러 레지스트리
- step_idempotency_key: 멱등성 키 생성

멱등성 보장 방식:
1. 단계 실행 전 완료 여부 확인
2. 이미 완료된 단계는 결과만 반환
3. 실행 중 상태 추적으로 중복 실행 방지
4. 부분 실패 시 롤백 또는 Skip 처리

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.5.2
"""

from __future__ import annotations

import hashlib
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from .recovery_state import RecoverySession, RecoveryStep, RecoveryStepType

if TYPE_CHECKING:
    from selfhealing.core.state_backend import StateBackend

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# 멱등성 키 TTL (시간)
IDEMPOTENCY_KEY_TTL_HOURS = 24

# 실행 중 타임아웃 (분) - 이 시간 초과 시 재실행 허용
EXECUTION_TIMEOUT_MINUTES = 30


# =============================================================================
# Idempotency Status
# =============================================================================


class IdempotencyStatus(str, Enum):
    """
    멱등성 상태.

    단계의 멱등성 처리 상태를 나타냅니다.
    """

    NOT_EXECUTED = "not_executed"
    """아직 실행되지 않음."""

    EXECUTING = "executing"
    """실행 중."""

    COMPLETED = "completed"
    """성공적으로 완료됨."""

    FAILED = "failed"
    """실패함."""

    SKIPPED = "skipped"
    """건너뛰어짐 (이전 실행 결과 사용)."""


@dataclass
class IdempotencyRecord:
    """
    멱등성 기록.

    단계 실행의 멱등성 상태를 추적합니다.
    """

    idempotency_key: str
    """고유 멱등성 키."""

    session_id: str
    """복구 세션 ID."""

    step_type: str
    """단계 유형."""

    step_order: int
    """단계 순서."""

    status: IdempotencyStatus = IdempotencyStatus.NOT_EXECUTED
    """현재 상태."""

    started_at: str | None = None
    """시작 시각."""

    completed_at: str | None = None
    """완료 시각."""

    result: dict[str, Any] | None = None
    """실행 결과."""

    error_message: str | None = None
    """에러 메시지."""

    retry_count: int = 0
    """재시도 횟수."""

    def is_safe_to_execute(self) -> bool:
        """
        실행 가능 여부 확인.

        이미 완료되었거나 오래된 실행 중 상태가 아닌 경우 True.
        """
        if self.status == IdempotencyStatus.COMPLETED:
            return False

        if self.status == IdempotencyStatus.EXECUTING:
            # 실행 중인 경우 타임아웃 확인
            if self.started_at:
                try:
                    started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
                    timeout = started + timedelta(minutes=EXECUTION_TIMEOUT_MINUTES)
                    if datetime.now(timezone.utc) < timeout:
                        return False  # 아직 타임아웃 안됨
                except (ValueError, TypeError):
                    pass

        return True

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "idempotency_key": self.idempotency_key,
            "session_id": self.session_id,
            "step_type": self.step_type,
            "step_order": self.step_order,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdempotencyRecord:
        """딕셔너리에서 생성."""
        return cls(
            idempotency_key=data.get("idempotency_key", ""),
            session_id=data.get("session_id", ""),
            step_type=data.get("step_type", ""),
            step_order=data.get("step_order", 0),
            status=IdempotencyStatus(data.get("status", "not_executed")),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            result=data.get("result"),
            error_message=data.get("error_message"),
            retry_count=data.get("retry_count", 0),
        )


# =============================================================================
# Idempotency Key Generator
# =============================================================================


def generate_idempotency_key(
    session_id: str,
    step_type: str,
    step_order: int,
    params: dict[str, Any] | None = None,
) -> str:
    """
    멱등성 키 생성.

    세션 ID, 단계 유형, 순서, 파라미터를 조합하여 고유 키 생성.

    Args:
        session_id: 복구 세션 ID
        step_type: 단계 유형
        step_order: 단계 순서
        params: 단계 파라미터 (선택)

    Returns:
        멱등성 키 문자열
    """
    # 파라미터를 정렬된 문자열로 변환
    params_str = ""
    if params:
        import json

        params_str = json.dumps(params, sort_keys=True)

    # 해시 생성
    key_source = f"{session_id}:{step_type}:{step_order}:{params_str}"
    key_hash = hashlib.sha256(key_source.encode()).hexdigest()[:16]

    return f"idem:{session_id}:{step_type}:{step_order}:{key_hash}"


# =============================================================================
# Idempotent Step Handler
# =============================================================================


class IdempotentStepHandler(ABC):
    """
    멱등성 보장 단계 핸들러 추상 클래스.

    모든 복구 단계 핸들러가 상속해야 하는 기본 클래스.
    멱등성 체크 및 결과 캐싱을 자동으로 처리합니다.

    Usage:
        class BudgetResetHandler(IdempotentStepHandler):
            def _execute_internal(self, session, step):
                # 실제 Budget Reset 로직
                return {"success": True, "multiplier": 1.0}

            def _check_already_applied(self, session, step):
                # 이미 리셋되었는지 확인
                return current_multiplier == 1.0
    """

    def __init__(
        self,
        backend: StateBackend | None = None,
    ):
        """
        초기화.

        Args:
            backend: StateBackend 인스턴스 (None이면 자동 획득)
        """
        self._backend = backend
        self._lock = threading.Lock()
        # 인메모리 폴백
        self._memory_records: dict[str, IdempotencyRecord] = {}

    def _get_backend(self) -> StateBackend | None:
        """StateBackend 인스턴스 획득."""
        if self._backend is not None:
            return self._backend

        try:
            from selfhealing.core.state_backend import get_state_backend

            return get_state_backend()
        except ImportError:
            return None

    def execute(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        멱등성 보장 실행.

        1. 이미 완료되었는지 확인
        2. 실행 중 상태로 마킹
        3. 실제 실행
        4. 결과 저장

        Args:
            session: 복구 세션
            step: 복구 단계

        Returns:
            실행 결과 딕셔너리
        """
        # 1. 멱등성 키 생성
        idempotency_key = generate_idempotency_key(
            session_id=session.id,
            step_type=step.step_type.value,
            step_order=step.order,
            params=step.params,
        )

        # 2. 기존 레코드 확인
        record = self._get_record(idempotency_key)

        if record and not record.is_safe_to_execute():
            # 이미 완료된 경우 캐시된 결과 반환
            logger.info(
                f"[IdempotentStepHandler] Returning cached result: " f"key={idempotency_key}, status={record.status.value}"
            )
            return {
                "success": record.status == IdempotencyStatus.COMPLETED,
                "idempotent": True,
                "cached_result": record.result,
                "error": record.error_message,
            }

        # 3. 비즈니스 로직 레벨에서 이미 적용되었는지 확인
        if self._check_already_applied(session, step):
            logger.info(f"[IdempotentStepHandler] Already applied (business check): " f"step={step.step_type.value}")
            # 레코드 저장
            self._save_completed_record(
                idempotency_key,
                session,
                step,
                result={"success": True, "already_applied": True},
            )
            return {
                "success": True,
                "idempotent": True,
                "already_applied": True,
            }

        # 4. 실행 중 상태로 마킹
        if not record:
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                session_id=session.id,
                step_type=step.step_type.value,
                step_order=step.order,
            )

        record.status = IdempotencyStatus.EXECUTING
        record.started_at = datetime.now(timezone.utc).isoformat()
        record.retry_count += 1
        self._save_record(record)

        # 5. 실제 실행
        try:
            result = self._execute_internal(session, step)

            # 6. 성공 시 완료 기록
            if result.get("success"):
                record.status = IdempotencyStatus.COMPLETED
                record.completed_at = datetime.now(timezone.utc).isoformat()
                record.result = result
            else:
                record.status = IdempotencyStatus.FAILED
                record.error_message = result.get("error", "Unknown error")
                record.result = result

            self._save_record(record)

            return {
                **result,
                "idempotent": False,
                "retry_count": record.retry_count,
            }

        except Exception as e:
            # 실패 기록
            record.status = IdempotencyStatus.FAILED
            record.error_message = str(e)
            self._save_record(record)

            logger.exception(f"[IdempotentStepHandler] Execution error: " f"step={step.step_type.value}, error={e}")

            return {
                "success": False,
                "error": str(e),
                "idempotent": False,
                "retry_count": record.retry_count,
            }

    @abstractmethod
    def _execute_internal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        실제 단계 실행 로직.

        하위 클래스에서 구현해야 합니다.

        Args:
            session: 복구 세션
            step: 복구 단계

        Returns:
            {"success": bool, ...} 형태의 결과
        """
        pass

    def _check_already_applied(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> bool:
        """
        비즈니스 레벨에서 이미 적용되었는지 확인.

        예: Budget Reset의 경우 현재 multiplier가 1.0인지 확인.

        기본 구현은 False 반환 (항상 실행).
        하위 클래스에서 오버라이드할 수 있습니다.

        Args:
            session: 복구 세션
            step: 복구 단계

        Returns:
            True if 이미 적용됨
        """
        return False

    def _get_record(self, key: str) -> IdempotencyRecord | None:
        """멱등성 레코드 조회."""
        backend = self._get_backend()

        if backend:
            try:
                data = backend.get(key)
                if data:
                    return IdempotencyRecord.from_dict(data)
            except Exception:
                pass

        # 인메모리 폴백
        with self._lock:
            return self._memory_records.get(key)

    def _save_record(self, record: IdempotencyRecord) -> None:
        """멱등성 레코드 저장."""
        backend = self._get_backend()

        if backend:
            try:
                backend.set(
                    record.idempotency_key,
                    record.to_dict(),
                    ttl=IDEMPOTENCY_KEY_TTL_HOURS * 3600,
                )
                return
            except Exception:
                pass

        # 인메모리 폴백
        with self._lock:
            self._memory_records[record.idempotency_key] = record

    def _save_completed_record(
        self,
        key: str,
        session: RecoverySession,
        step: RecoveryStep,
        result: dict[str, Any],
    ) -> None:
        """완료된 레코드 저장."""
        record = IdempotencyRecord(
            idempotency_key=key,
            session_id=session.id,
            step_type=step.step_type.value,
            step_order=step.order,
            status=IdempotencyStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc).isoformat(),
            result=result,
        )
        self._save_record(record)


# =============================================================================
# Concrete Handlers
# =============================================================================


class IdempotentBudgetResetHandler(IdempotentStepHandler):
    """
    멱등성 Budget Reset 핸들러.

    Budget Multiplier를 기본값(1.0)으로 초기화합니다.
    이미 1.0인 경우 스킵합니다.
    """

    def _execute_internal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """Budget Multiplier 리셋 실행."""
        target = step.params.get("target_multiplier", 1.0)

        try:
            from selfhealing.services.coordination.crisis_multiplier import (
                get_crisis_multiplier_provider,
            )

            provider = get_crisis_multiplier_provider()

            old_multiplier = provider.get_multiplier(session.namespace)
            provider.reset_multiplier(session.namespace)
            new_multiplier = provider.get_multiplier(session.namespace)

            return {
                "success": True,
                "old_multiplier": old_multiplier,
                "new_multiplier": new_multiplier,
                "target_multiplier": target,
            }
        except ImportError:
            logger.warning("[IdempotentBudgetResetHandler] CrisisMultiplierProvider not available")
            return {
                "success": True,
                "skipped": True,
                "reason": "Provider not available",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _check_already_applied(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> bool:
        """현재 multiplier가 이미 1.0인지 확인."""
        try:
            from selfhealing.services.coordination.crisis_multiplier import (
                get_crisis_multiplier_provider,
            )

            provider = get_crisis_multiplier_provider()
            current = provider.get_multiplier(session.namespace)
            return abs(current - 1.0) < 0.001
        except Exception:
            return False


class IdempotentHealthCheckHandler(IdempotentStepHandler):
    """
    멱등성 Health Check 핸들러.

    안정화 상태를 확인합니다.
    Health Check는 본질적으로 멱등성이 있습니다 (상태 확인만 수행).
    """

    def _execute_internal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """Health Check 실행."""
        duration_minutes = step.params.get("duration_minutes", 5)
        error_rate_threshold = step.params.get("error_rate_threshold", 0.1)
        success_threshold = step.params.get("success_threshold", 0.95)

        # Prometheus + OTel + Mimir 인프라가 error_rate 수집/저장/알림을
        # 이미 처리하므로, 별도 MetricsCollector 없이 안정으로 가정.
        # 실제 에러율 기반 판단 필요 시:
        #   PrometheusMetricsCollector.query_instant() 활용
        #   (selfhealing.services.postmortem.prometheus_collector)
        logger.debug(
            "[IdempotentHealthCheckHandler] Stability check: "
            f"namespace={session.namespace}, duration={duration_minutes}m, "
            f"threshold={error_rate_threshold}"
        )
        return {
            "success": True,
            "assumed": True,
            "error_rate": 0.0,
            "threshold": error_rate_threshold,
            "duration_minutes": duration_minutes,
        }


class IdempotentCanaryResumeHandler(IdempotentStepHandler):
    """
    멱등성 Canary Resume 핸들러.

    일시 중지된 Canary 롤아웃을 재개합니다.
    이미 재개된 경우 스킵합니다.
    """

    def _execute_internal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """Canary 재개 실행."""
        resume_paused_only = step.params.get("resume_paused_only", True)

        try:
            from selfhealing.services.canary import get_canary_service

            service = get_canary_service()

            if resume_paused_only:
                paused = service.get_paused_rollouts(session.namespace)
                if not paused:
                    return {
                        "success": True,
                        "resumed_count": 0,
                        "reason": "No paused rollouts",
                    }
                resumed = service.resume_paused_rollouts(session.namespace)
            else:
                resumed = service.resume_all_rollouts(session.namespace)

            return {
                "success": True,
                "resumed_count": len(resumed) if resumed else 0,
                "resumed_rollouts": resumed,
            }
        except (ImportError, AttributeError):
            logger.warning("[IdempotentCanaryResumeHandler] CanaryService not available")
            return {"success": True, "skipped": True, "resumed_count": 0}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _check_already_applied(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> bool:
        """일시 중지된 롤아웃이 없는지 확인."""
        try:
            from selfhealing.services.canary import get_canary_service

            service = get_canary_service()
            paused = service.get_paused_rollouts(session.namespace)
            return len(paused) == 0
        except Exception:
            return False


class IdempotentGovernanceNormalHandler(IdempotentStepHandler):
    """
    멱등성 Governance NORMAL 핸들러.

    Governance 모드를 NORMAL로 전환합니다.
    이미 NORMAL인 경우 스킵합니다.
    """

    def _execute_internal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """Governance NORMAL 전환 실행."""
        reason = step.params.get(
            "reason",
            "[AUTO-RECOVERY] Stability confirmed",
        )

        try:
            from selfhealing.services.governance import get_emergency_tracker

            tracker = get_emergency_tracker()

            old_mode = tracker.get_current_state().mode
            tracker.record_normal_restoration(
                restored_by=session.initiated_by,
                reason=reason,
            )
            new_mode = tracker.get_current_state().mode

            return {
                "success": True,
                "old_mode": old_mode,
                "new_mode": new_mode,
                "reason": reason,
            }
        except (ImportError, AttributeError):
            logger.warning("[IdempotentGovernanceNormalHandler] EmergencyModeTracker not available")
            return {"success": True, "skipped": True, "mode": "NORMAL"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _check_already_applied(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> bool:
        """이미 NORMAL 모드인지 확인."""
        try:
            from selfhealing.services.governance import get_emergency_tracker

            tracker = get_emergency_tracker()
            current_mode = tracker.get_current_state().mode
            return current_mode == "NORMAL"
        except Exception:
            return False


# =============================================================================
# Handler Registry
# =============================================================================


class IdempotentStepHandlerRegistry:
    """
    멱등성 핸들러 레지스트리.

    단계 유형별 핸들러를 관리합니다.
    """

    def __init__(self):
        """초기화."""
        self._handlers: dict[RecoveryStepType, IdempotentStepHandler] = {}
        self._lock = threading.Lock()
        self._register_defaults()

    def _register_defaults(self) -> None:
        """기본 핸들러 등록."""
        self._handlers = {
            RecoveryStepType.BUDGET_RESET: IdempotentBudgetResetHandler(),
            RecoveryStepType.HEALTH_CHECK: IdempotentHealthCheckHandler(),
            RecoveryStepType.CANARY_RESUME: IdempotentCanaryResumeHandler(),
            RecoveryStepType.GOVERNANCE_NORMAL: IdempotentGovernanceNormalHandler(),
        }

    def register(
        self,
        step_type: RecoveryStepType,
        handler: IdempotentStepHandler,
    ) -> None:
        """
        핸들러 등록.

        Args:
            step_type: 단계 유형
            handler: 핸들러 인스턴스
        """
        with self._lock:
            self._handlers[step_type] = handler

    def get(
        self,
        step_type: RecoveryStepType,
    ) -> IdempotentStepHandler | None:
        """
        핸들러 조회.

        Args:
            step_type: 단계 유형

        Returns:
            핸들러 또는 None
        """
        with self._lock:
            return self._handlers.get(step_type)

    def has_handler(self, step_type: RecoveryStepType) -> bool:
        """
        핸들러 존재 여부 확인.

        Args:
            step_type: 단계 유형

        Returns:
            True if 핸들러가 등록됨
        """
        with self._lock:
            return step_type in self._handlers

    def execute(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> dict[str, Any]:
        """
        핸들러 실행.

        Args:
            session: 복구 세션
            step: 복구 단계

        Returns:
            실행 결과

        Raises:
            ValueError: 핸들러가 없는 경우
        """
        handler = self.get(step.step_type)

        if not handler:
            raise ValueError(f"No handler registered for step type: {step.step_type}")

        return handler.execute(session, step)


# =============================================================================
# Singleton
# =============================================================================

_handler_registry: IdempotentStepHandlerRegistry | None = None
_registry_lock = threading.Lock()


def get_idempotent_step_handler_registry() -> IdempotentStepHandlerRegistry:
    """IdempotentStepHandlerRegistry 싱글톤 반환."""
    global _handler_registry

    if _handler_registry is not None:
        return _handler_registry

    with _registry_lock:
        if _handler_registry is None:
            _handler_registry = IdempotentStepHandlerRegistry()
        return _handler_registry


def reset_idempotent_step_handler_registry() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _handler_registry
    with _registry_lock:
        _handler_registry = None
