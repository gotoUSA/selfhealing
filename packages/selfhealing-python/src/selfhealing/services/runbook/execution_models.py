"""
Runbook Executor 실행 데이터 모델.

RunbookExecutionContext, RunbookStepResult, RunbookExecutionStatus, CompensationSummary
를 정의한다. SagaContext/StepResult/RecoveryStatus 패턴을 결합한 Runbook 전용 구조다.

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# =============================================================================
# 실행 상태 Enum
# =============================================================================


class RunbookExecutionStatus(str, Enum):
    """Runbook 실행 상태.

    RecoveryStatus 패턴 참조. 허용 전이:
        PENDING → EXECUTING
        EXECUTING → WAITING_APPROVAL | COMPLETED | COMPENSATING
        WAITING_APPROVAL → EXECUTING | CANCELLED
        COMPENSATING → FAILED
    """

    PENDING = "pending"
    """대기 중 (승인 대기 포함)."""

    EXECUTING = "executing"
    """실행 중."""

    WAITING_APPROVAL = "waiting_approval"
    """수동 승인 대기."""

    COMPENSATING = "compensating"
    """역순 보상 중."""

    COMPLETED = "completed"
    """성공 완료."""

    FAILED = "failed"
    """실패 (보상 완료 후)."""

    CANCELLED = "cancelled"
    """취소됨."""


# =============================================================================
# Step 실행 결과
# =============================================================================


@dataclass
class RunbookStepResult:
    """단일 Step 실행 결과.

    StepResult + ActionResult 통합. 두 패턴의 필드를 결합한다.

    partial_execution:
        True면 success=False여도 보상 대상에 포함된다.
        타임아웃(In-doubt) 시 True로 마킹 — 네트워크 응답만 못 받았을 뿐
        타겟 시스템에서는 작업이 성공했을 수 있으므로 보상 필요.
    """

    step_name: str
    """Step 식별자 (Runbook.steps[n].name)."""

    action_name: str
    """실행한 Action Primitive 이름 (Runbook.steps[n].action)."""

    success: bool
    """실행 성공 여부. Shadow 모드에서 executed=False이면 True로 간주."""

    executed: bool
    """ActionResult.executed — SHADOW 모드 판별 필드."""

    result_data: dict[str, Any] = field(default_factory=dict)
    """Action 실행 반환 데이터."""

    error: str | None = None
    """에러 메시지 (실패 시)."""

    started_at: str | None = None
    """ISO 8601 시작 시각."""

    completed_at: str | None = None
    """ISO 8601 완료 시각."""

    idempotent: bool = False
    """True면 IdempotencyRecord 캐시에서 반환된 결과."""

    partial_execution: bool = False
    """외부 부작용(side-effect)이 발생했을 가능성이 있는 실패.
    타임아웃(In-doubt) 시 True로 마킹 — 보상 대상에 포함된다."""

    compensation_status: str = "not_needed"
    """보상 처리 상태: "not_needed" | "compensated" | "compensate_failed"."""

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "step_name": self.step_name,
            "action_name": self.action_name,
            "success": self.success,
            "executed": self.executed,
            "result_data": self.result_data,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "idempotent": self.idempotent,
            "partial_execution": self.partial_execution,
            "compensation_status": self.compensation_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunbookStepResult:
        """역직렬화."""
        return cls(
            step_name=data["step_name"],
            action_name=data["action_name"],
            success=data["success"],
            executed=data["executed"],
            result_data=data.get("result_data", {}),
            error=data.get("error"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            idempotent=data.get("idempotent", False),
            partial_execution=data.get("partial_execution", False),
            compensation_status=data.get("compensation_status", "not_needed"),
        )


# =============================================================================
# 보상 요약
# =============================================================================


@dataclass
class CompensationSummary:
    """역순 보상 결과 요약.

    CompensationResult 패턴 참조 (recovery_state.py).
    """

    compensated: list[str] = field(default_factory=list)
    """보상 성공한 Step 이름 목록."""

    failed: list[tuple[str, str]] = field(default_factory=list)
    """보상 실패 목록: (step_name, error_message)."""

    skipped: list[str] = field(default_factory=list)
    """보상 프리미티브 없어 건너뛴 Step 이름 목록."""

    @property
    def all_compensated(self) -> bool:
        """모든 보상 대상이 성공적으로 보상됐으면 True."""
        return len(self.failed) == 0

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "compensated": self.compensated,
            "failed": [{"step": s, "error": e} for s, e in self.failed],
            "skipped": self.skipped,
            "all_compensated": self.all_compensated,
        }


# =============================================================================
# 실행 컨텍스트
# =============================================================================


@dataclass
class RunbookExecutionContext:
    """Runbook 실행 컨텍스트.

    SagaContext 패턴 참조. Step 간 상태를 추적하고, compensate 시 이전 결과를 참조할 수 있다.

    runbook_version:
        실행 시점의 Runbook 정의 버전 스냅샷 (SagaInstance.definition_version 패턴).
        resume 시 현재 Runbook.version과 비교하여 버전 불일치를 감지한다.
    """

    execution_id: str
    """고유 실행 ID (UUID)."""

    runbook_id: str
    """실행 중인 Runbook의 ID."""

    namespace: str
    """대상 네임스페이스."""

    trigger_event: dict[str, Any]
    """실행을 트리거한 이벤트 데이터."""

    runbook_version: int = 1
    """실행 시점의 Runbook 버전 스냅샷."""

    step_results: dict[str, RunbookStepResult] = field(default_factory=dict)
    """Step Name → 실행 결과 매핑. compensate 시 참조."""

    variables: dict[str, Any] = field(default_factory=dict)
    """Step 간 공유 변수. params의 ${...} 치환 대상."""

    current_step_index: int = 0
    """현재 실행 중인 Step 인덱스."""

    status: RunbookExecutionStatus = RunbookExecutionStatus.PENDING
    """실행 상태."""

    started_at: str | None = None
    """ISO 8601 시작 시각."""

    completed_at: str | None = None
    """ISO 8601 완료 시각."""

    abort_reason: str | None = None
    """중단 사유."""

    def get(self, step_name: str) -> RunbookStepResult | None:
        """이전 Step 결과 조회. SagaContext.get() 패턴."""
        return self.step_results.get(step_name)

    def to_dict(self) -> dict[str, Any]:
        """직렬화. StateBackend 영속화 시 사용."""
        return {
            "execution_id": self.execution_id,
            "runbook_id": self.runbook_id,
            "namespace": self.namespace,
            "trigger_event": self.trigger_event,
            "runbook_version": self.runbook_version,
            "step_results": {k: v.to_dict() for k, v in self.step_results.items()},
            "variables": self.variables,
            "current_step_index": self.current_step_index,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "abort_reason": self.abort_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunbookExecutionContext:
        """역직렬화. resume_execution() 시 StateBackend에서 로드."""
        step_results = {k: RunbookStepResult.from_dict(v) for k, v in data.get("step_results", {}).items()}
        return cls(
            execution_id=data["execution_id"],
            runbook_id=data["runbook_id"],
            namespace=data["namespace"],
            trigger_event=data.get("trigger_event", {}),
            runbook_version=data.get("runbook_version", 1),
            step_results=step_results,
            variables=data.get("variables", {}),
            current_step_index=data.get("current_step_index", 0),
            status=RunbookExecutionStatus(data.get("status", "pending")),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            abort_reason=data.get("abort_reason"),
        )
