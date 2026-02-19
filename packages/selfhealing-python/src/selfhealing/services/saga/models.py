"""
Saga Core Models.

Saga Orchestrator의 코어 자료구조를 제공합니다.
- SagaStatus: Saga 인스턴스 상태 enum
- SagaStepStatus: 개별 Step 실행 상태 enum
- StepResult: Step 실행 결과 dataclass
- SagaContext: Step 간 데이터 전달 컨텍스트 dataclass
- SagaStepInstance: Step 실행 인스턴스 상태 추적 dataclass
- SagaDefinition: Saga 정의 dataclass
- SagaInstance: Saga 실행 인스턴스 dataclass
- ALLOWED_TRANSITIONS: 허용된 상태 전환 딕셔너리
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.saga.step import SagaStep


# =============================================================================
# SagaStatus — Saga 인스턴스 상태 enum
# =============================================================================


class SagaStatus(str, Enum):
    """Saga 인스턴스의 상태."""

    PENDING = "pending"
    """생성됨, 실행 대기 중."""

    RUNNING = "running"
    """Forward step들 순차 실행 중."""

    COMPLETED = "completed"
    """모든 Forward step 성공. 최종 완료."""

    COMPENSATING = "compensating"
    """Forward step 실패로 역순 compensate 실행 중."""

    COMPENSATED = "compensated"
    """모든 compensate 성공. 원상 복구 완료."""

    COMPENSATION_FAILED = "compensation_failed"
    """compensate 중 실패. DLQ에 저장됨. 수동 개입 필요."""

    SUSPENDED = "suspended"
    """외부 서비스 장애(CircuitBreaker OPEN)로 일시 중지."""

    TIMED_OUT = "timed_out"
    """전체 Saga 타임아웃 초과."""


ALLOWED_TRANSITIONS: dict[SagaStatus, set[SagaStatus]] = {
    SagaStatus.PENDING: {SagaStatus.RUNNING},
    SagaStatus.RUNNING: {
        SagaStatus.COMPLETED,
        SagaStatus.COMPENSATING,
        SagaStatus.SUSPENDED,
        SagaStatus.TIMED_OUT,
    },
    SagaStatus.COMPENSATING: {
        SagaStatus.COMPENSATED,
        SagaStatus.COMPENSATION_FAILED,
    },
    SagaStatus.SUSPENDED: {
        SagaStatus.RUNNING,
        SagaStatus.COMPENSATING,
    },
    SagaStatus.COMPLETED: set(),
    SagaStatus.COMPENSATED: set(),
    SagaStatus.COMPENSATION_FAILED: set(),
    SagaStatus.TIMED_OUT: set(),
}
"""허용된 상태 전환 매핑. 터미널 상태(COMPLETED, COMPENSATED, COMPENSATION_FAILED, TIMED_OUT)는 빈 set."""


# =============================================================================
# SagaStepStatus — 개별 Step 실행 상태 enum
# =============================================================================


class SagaStepStatus(str, Enum):
    """개별 Step의 실행 상태."""

    NOT_STARTED = "not_started"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXECUTE_FAILED = "execute_failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATE_FAILED = "compensate_failed"


# =============================================================================
# StepResult — Step 실행 결과
# =============================================================================


@dataclass
class StepResult:
    """SagaStep의 execute() 또는 compensate() 실행 결과."""

    success: bool
    """성공 여부."""

    data: dict[str, Any] = field(default_factory=dict)
    """Step이 생성한 데이터 (execute 시 context에 merge됨)."""

    error: str | None = None
    """실패 시 에러 메시지."""

    error_code: str | None = None
    """실패 시 에러 코드 (DLQ 분류용)."""

    retryable: bool = False
    """재시도 가능 여부. True면 Orchestrator가 재시도를 시도할 수 있음."""

    partial_execution: bool = False
    """외부 부작용(side-effect)이 발생한 후 실패한 경우 True.

    partial_execution=True면 EXECUTE_FAILED 상태여도 보상 대상에 포함된다.
    """

    @classmethod
    def succeeded(cls, data: dict[str, Any] | None = None) -> StepResult:
        """성공 결과 팩토리 메서드."""
        return cls(success=True, data=data or {})

    @classmethod
    def failed(cls, error: str, error_code: str = "", retryable: bool = False) -> StepResult:
        """실패 결과 팩토리 메서드."""
        return cls(success=False, error=error, error_code=error_code, retryable=retryable)

    @classmethod
    def failed_with_side_effect(
        cls,
        error: str,
        data: dict[str, Any] | None = None,
        error_code: str = "",
        retryable: bool = False,
    ) -> StepResult:
        """외부 부작용이 발생한 후 실패한 경우의 팩토리 메서드.

        Orchestrator가 이 Step도 compensate 대상에 포함한다.
        data에 부분 실행 결과를 함께 전달하면 compensate가 정확한 정리를 수행할 수 있다.
        """
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            retryable=retryable,
            data=data or {},
            partial_execution=True,
        )


# =============================================================================
# SagaContext — Step 간 데이터 전달 컨텍스트
# =============================================================================


@dataclass
class SagaContext:
    """Saga 실행 중 Step 간 데이터 전달 컨텍스트.

    각 Step의 execute()가 반환한 StepResult.data가 자동으로 merge된다.
    compensate() 시에도 이전 execute()에서 생성한 데이터에 접근 가능.
    """

    saga_instance_id: str
    """소속 Saga 인스턴스 ID."""

    initial_data: dict[str, Any] = field(default_factory=dict)
    """Saga 시작 시 전달된 초기 데이터."""

    step_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    """각 Step이 반환한 데이터. key = step_name."""

    failed_step_name: str | None = None
    """Forward 실패를 유발한 Step 이름. COMPENSATING 전환 시 Orchestrator가 설정."""

    abort_reason: str | None = None
    """실패 원인 메시지."""

    abort_error_code: str | None = None
    """실패 에러 코드 (StepResult.error_code에서 복사)."""

    # -----------------------------------------------------------------
    # 조회 메서드
    # -----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """초기 데이터 + 모든 Step 결과에서 key 검색 (편의 메서드).

        검색 순서: step_results (최신 먼저) → initial_data

        Warning:
            동일한 key가 여러 Step에서 사용되면 의도치 않은 값이 반환될 수 있다.
            키 충돌 가능성이 있는 경우 get_step_data() 또는 get_from_step()을 사용할 것.
        """
        for step_data in reversed(list(self.step_results.values())):
            if key in step_data:
                return step_data[key]
        return self.initial_data.get(key, default)

    def get_step_data(self, step_name: str) -> dict[str, Any]:
        """특정 Step의 결과 데이터만 반환 (네임스페이스 격리 조회)."""
        return self.step_results.get(step_name, {})

    def get_from_step(self, step_name: str, key: str, default: Any = None) -> Any:
        """특정 Step의 결과에서 특정 key만 조회.

        get()과 달리 명시적으로 Step을 지정하므로 키 충돌 위험이 없다.
        """
        return self.step_results.get(step_name, {}).get(key, default)

    # -----------------------------------------------------------------
    # 상태 변이 메서드
    # -----------------------------------------------------------------

    def merge_step_result(self, step_name: str, data: dict[str, Any]) -> None:
        """Step 실행 결과를 컨텍스트에 merge."""
        self.step_results[step_name] = data

    def set_compensation_context(
        self,
        failed_step_name: str,
        abort_reason: str,
        abort_error_code: str | None = None,
    ) -> None:
        """COMPENSATING 전환 시 보상 맥락 설정.

        Orchestrator가 COMPENSATING 전환 시점에 호출한다.
        compensate() 함수 내에서 ctx.abort_reason으로 실패 원인을 참조 가능.
        """
        self.failed_step_name = failed_step_name
        self.abort_reason = abort_reason
        self.abort_error_code = abort_error_code

    # -----------------------------------------------------------------
    # 직렬화
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "saga_instance_id": self.saga_instance_id,
            "initial_data": self.initial_data,
            "step_results": self.step_results,
            "failed_step_name": self.failed_step_name,
            "abort_reason": self.abort_reason,
            "abort_error_code": self.abort_error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaContext:
        return cls(
            saga_instance_id=data["saga_instance_id"],
            initial_data=data.get("initial_data", {}),
            step_results=data.get("step_results", {}),
            failed_step_name=data.get("failed_step_name"),
            abort_reason=data.get("abort_reason"),
            abort_error_code=data.get("abort_error_code"),
        )


# =============================================================================
# SagaStepInstance — Step 실행 인스턴스 상태 추적
# =============================================================================


@dataclass
class SagaStepInstance:
    """Saga Step의 실행 인스턴스 (상태 추적용).

    RecoveryStep과 동일한 패턴:
    - order: 실행 순서
    - status: 현재 상태
    - started_at/completed_at: 시간 추적
    - error_message: 실패 원인
    """

    step_name: str
    """Step 이름 (SagaStep.name과 매핑)."""

    order: int
    """실행 순서 (0부터 시작)."""

    status: SagaStepStatus = SagaStepStatus.NOT_STARTED
    """현재 상태."""

    execute_started_at: str | None = None
    """Forward 실행 시작 시각 (ISO 8601)."""

    execute_completed_at: str | None = None
    """Forward 실행 완료 시각."""

    compensate_started_at: str | None = None
    """Compensate 시작 시각."""

    compensate_completed_at: str | None = None
    """Compensate 완료 시각."""

    error_message: str | None = None
    """실패 시 에러 메시지."""

    error_code: str | None = None
    """실패 시 에러 코드."""

    retry_count: int = 0
    """재시도 횟수."""

    next_retry_at: str | None = None
    """다음 재시도 예정 시각 (ISO 8601)."""

    partial_execution: bool = False
    """execute 중 외부 부작용(side-effect)이 발생했는지 여부.

    StepResult.partial_execution=True일 때 Orchestrator가 이 필드를 설정.
    EXECUTE_FAILED 상태여도 이 값이 True면 보상 대상에 포함된다.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "order": self.order,
            "status": self.status.value,
            "execute_started_at": self.execute_started_at,
            "execute_completed_at": self.execute_completed_at,
            "compensate_started_at": self.compensate_started_at,
            "compensate_completed_at": self.compensate_completed_at,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "partial_execution": self.partial_execution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaStepInstance:
        return cls(
            step_name=data["step_name"],
            order=data["order"],
            status=SagaStepStatus(data.get("status", "not_started")),
            execute_started_at=data.get("execute_started_at"),
            execute_completed_at=data.get("execute_completed_at"),
            compensate_started_at=data.get("compensate_started_at"),
            compensate_completed_at=data.get("compensate_completed_at"),
            error_message=data.get("error_message"),
            error_code=data.get("error_code"),
            retry_count=data.get("retry_count", 0),
            next_retry_at=data.get("next_retry_at"),
            partial_execution=data.get("partial_execution", False),
        )


# =============================================================================
# SagaDefinition — Saga 정의
# =============================================================================


@dataclass
class SagaDefinition:
    """Saga 정의. 이름과 Step 목록으로 구성.

    어댑터 레이어에서 생성하여 SagaRegistry에 등록.
    """

    name: str
    """Saga 이름 (고유 식별자)."""

    version: int = 1
    """정의 스키마 버전.

    배포 도중 Step 구성이 변경되면 version을 증가시킨다.
    SagaInstance 생성 시 이 값이 definition_version으로 스냅샷된다.
    """

    steps: list[SagaStep] = field(default_factory=list)
    """실행할 Step 목록 (순서대로 execute, 역순으로 compensate)."""

    timeout_seconds: int = 600
    """전체 Saga 타임아웃 (초). 기본 10분."""

    max_retries_per_step: int = 2
    """Step 실패 시 최대 재시도 횟수. StepResult.retryable=True일 때만 적용."""

    retry_backoff_strategy: str = "exponential"
    """재시도 백오프 전략.

    기존 core/backoff.py의 BackoffStrategy 구현 중 선택:
    - "exponential": ExponentialBackoff (기본 20% Jitter 포함)
    - "linear": LinearBackoff
    - "constant": ConstantBackoff
    - "decorrelated": DecorrelatedJitterBackoff (AWS-style)
    """

    description: str = ""
    """Saga 설명 (감사 로그용)."""

    def validate(self) -> tuple[bool, str]:
        """Saga 정의 유효성 검증.

        Returns:
            (valid, error_message) 튜플
        """
        if not self.name:
            return False, "Saga name is required"
        if not self.steps:
            return False, "At least one step is required"

        names = [s.name for s in self.steps]
        duplicates = [n for n in names if names.count(n) > 1]
        if duplicates:
            return False, f"Duplicate step names: {set(duplicates)}"

        return True, ""


# =============================================================================
# SagaInstance — Saga 실행 인스턴스
# =============================================================================


@dataclass
class SagaInstance:
    """Saga 실행 인스턴스. 상태 추적 + 직렬화.

    RecoverySession과 동일한 패턴:
    - id: 고유 ID
    - status: 전체 상태
    - steps: Step 인스턴스 목록
    - current_step_index: 현재 진행 위치
    - to_dict()/from_dict(): 직렬화
    """

    id: str
    """고유 인스턴스 ID. 형식: "saga-{uuid}"."""

    saga_name: str
    """SagaDefinition.name 참조."""

    definition_version: int = 1
    """생성 시점의 SagaDefinition.version 스냅샷.

    Orchestrator가 인스턴스를 픽업할 때 현재 SagaDefinition.version과 비교.
    불일치 시 SUSPENDED 전환하여 버전 불일치 데이터 오염을 방지.
    """

    version: int = 0
    """OCC(Optimistic Concurrency Control)용 버전 카운터. 저장 시마다 +1 증가."""

    status: SagaStatus = SagaStatus.PENDING
    """현재 상태."""

    step_instances: list[SagaStepInstance] = field(default_factory=list)
    """Step 실행 인스턴스 목록."""

    current_step_index: int = 0
    """현재 Forward 진행 중인 Step 인덱스 (0부터)."""

    compensate_step_index: int = -1
    """현재 Compensate 진행 중인 Step 인덱스.

    -1이면 compensate 미시작.
    COMPENSATING 상태에서 마지막 성공 Step부터 역순으로 감소.
    """

    context: SagaContext | None = None
    """실행 컨텍스트 (Step 간 데이터 공유)."""

    started_at: str | None = None
    """Saga 시작 시각 (ISO 8601)."""

    completed_at: str | None = None
    """Saga 완료 시각."""

    initiated_by: str = "system"
    """시작 주체. "system" 또는 사용자 ID."""

    error_message: str | None = None
    """최종 에러 메시지 (실패 시)."""

    correlation_id: str | None = None
    """EventBus correlation_id (추적용)."""

    metadata: dict[str, Any] | None = None
    """추가 메타데이터."""

    def get_current_step(self) -> SagaStepInstance | None:
        """현재 Forward 진행 중인 Step 반환."""
        if 0 <= self.current_step_index < len(self.step_instances):
            return self.step_instances[self.current_step_index]
        return None

    def get_compensate_step(self) -> SagaStepInstance | None:
        """현재 Compensate 대상 Step 반환."""
        if 0 <= self.compensate_step_index < len(self.step_instances):
            return self.step_instances[self.compensate_step_index]
        return None

    def get_executed_steps(self) -> list[SagaStepInstance]:
        """Forward 실행이 성공한 Step 목록."""
        return [s for s in self.step_instances if s.status == SagaStepStatus.EXECUTED]

    def get_compensation_targets(self) -> list[SagaStepInstance]:
        """역순 compensate 대상 Step 목록.

        EXECUTED 상태이거나, EXECUTE_FAILED이면서 partial_execution=True인 Step을 포함한다.
        """
        return [
            s
            for s in self.step_instances
            if s.status == SagaStepStatus.EXECUTED or (s.status == SagaStepStatus.EXECUTE_FAILED and s.partial_execution)
        ]

    def get_progress(self) -> dict[str, Any]:
        """진행 상황 요약."""
        total = len(self.step_instances)
        executed = sum(1 for s in self.step_instances if s.status == SagaStepStatus.EXECUTED)
        compensated = sum(1 for s in self.step_instances if s.status == SagaStepStatus.COMPENSATED)

        return {
            "saga_name": self.saga_name,
            "status": self.status.value,
            "total_steps": total,
            "executed_steps": executed,
            "compensated_steps": compensated,
            "progress_percent": (executed / total * 100) if total > 0 else 0,
            "current_step_index": self.current_step_index,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "saga_name": self.saga_name,
            "definition_version": self.definition_version,
            "version": self.version,
            "status": self.status.value,
            "step_instances": [s.to_dict() for s in self.step_instances],
            "current_step_index": self.current_step_index,
            "compensate_step_index": self.compensate_step_index,
            "context": self.context.to_dict() if self.context else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaInstance:
        step_instances = [SagaStepInstance.from_dict(s) for s in data.get("step_instances", [])]
        context = SagaContext.from_dict(data["context"]) if data.get("context") else None
        return cls(
            id=data["id"],
            saga_name=data["saga_name"],
            definition_version=data.get("definition_version", 1),
            version=data.get("version", 0),
            status=SagaStatus(data.get("status", "pending")),
            step_instances=step_instances,
            current_step_index=data.get("current_step_index", 0),
            compensate_step_index=data.get("compensate_step_index", -1),
            context=context,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            initiated_by=data.get("initiated_by", "system"),
            error_message=data.get("error_message"),
            correlation_id=data.get("correlation_id"),
            metadata=data.get("metadata"),
        )
