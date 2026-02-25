"""
Runbook Executor 실행 데이터 모델.

RunbookExecutionContext, RunbookStepResult, RunbookExecutionStatus, CompensationSummary
를 정의한다. SagaContext/StepResult/RecoveryStatus 패턴을 결합한 Runbook 전용 구조다.

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §3
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.services.governance.checks import BlockReason, GovernanceCheckResult
    from selfhealing.services.runbook.runbook_registry import RiskLevel


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
        """직렬화. result_data는 deep copy하여 외부 수정이 원본에 영향을 미치지 않게 한다."""
        return {
            "step_name": self.step_name,
            "action_name": self.action_name,
            "success": self.success,
            "executed": self.executed,
            "result_data": copy.deepcopy(self.result_data),
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


# =============================================================================
# 승인 결정 유형
# =============================================================================


class ApprovalDecisionType(str, Enum):
    """승인 결정 유형.

    RiskLevel에 따라 ApprovalGate가 반환하는 결정 값.
    """

    AUTO_APPROVED = "auto_approved"
    """LOW: 거버넌스 통과 후 즉시 자동 승인."""

    TIMER_APPROVED = "timer_approved"
    """MEDIUM: 타이머 만료로 자동 승인 (거부 없이 expires_at 도달)."""

    MANUALLY_APPROVED = "manually_approved"
    """HIGH: 운영자가 수동 승인."""

    REJECTED = "rejected"
    """운영자가 수동 거부."""

    BLOCKED = "blocked"
    """거버넌스 차단 또는 CRITICAL 위험도로 자동 실행 차단."""

    WAITING = "waiting"
    """승인 대기 중 (MEDIUM/HIGH)."""


# =============================================================================
# 승인 결정 결과
# =============================================================================


@dataclass
class ApprovalDecision:
    """승인 결정 결과.

    ApprovalGate.evaluate_approval()이 반환하는 최종 결정.
    거버넌스 차단, 자동 승인, 대기 중, 수동 승인/거부 등의 상태를 포함한다.
    """

    decision_type: ApprovalDecisionType
    """결정 유형."""

    risk_level: RiskLevel
    """해당 런북의 위험도."""

    approved_by: str | None = None
    """수동 승인자 ID. 자동 승인 시 'system:auto' 또는 'system:timer'."""

    block_reason: BlockReason | None = None
    """거버넌스 차단 사유 (차단 시에만 설정)."""

    block_message: str = ""
    """차단 메시지."""

    decided_at: str | None = None
    """ISO 8601 결정 시각."""

    governance_result: GovernanceCheckResult | None = None
    """거버넌스 체크 결과 (차단 시 상세 정보 포함)."""

    @property
    def is_approved(self) -> bool:
        """승인된 상태인지 여부."""
        return self.decision_type in (
            ApprovalDecisionType.AUTO_APPROVED,
            ApprovalDecisionType.TIMER_APPROVED,
            ApprovalDecisionType.MANUALLY_APPROVED,
        )

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        result: dict[str, Any] = {
            "decision_type": self.decision_type.value,
            "risk_level": self.risk_level.value if self.risk_level else None,
            "approved_by": self.approved_by,
            "block_reason": self.block_reason.value if self.block_reason else None,
            "block_message": self.block_message,
            "decided_at": self.decided_at,
            "is_approved": self.is_approved,
        }
        return result


# =============================================================================
# 승인 요청 레코드
# =============================================================================


@dataclass
class RunbookApprovalRequest:
    """승인 요청 레코드.

    PendingRecoveryApprovalManager.create_request() 패턴을 따른다.
    StateBackend(Redis/InMemory)에 영속화되며, CAS 기반 원자적 상태 전환으로
    타이머 만료와 수동 승인/거부의 동시성을 관리한다.
    """

    request_id: str
    """승인 요청 고유 ID."""

    execution_id: str
    """RunbookExecutionContext.execution_id와 1:1 매핑."""

    runbook_id: str
    """대상 Runbook ID."""

    namespace: str
    """대상 네임스페이스."""

    risk_level: RiskLevel
    """런북의 위험도."""

    status: ApprovalDecisionType = ApprovalDecisionType.WAITING
    """현재 승인 상태."""

    created_at: str | None = None
    """ISO 8601 생성 시각."""

    expires_at: str | None = None
    """ISO 8601 만료 시각 (MEDIUM 타이머 전용)."""

    decided_at: str | None = None
    """ISO 8601 결정 시각."""

    decided_by: str | None = None
    """결정자 ID."""

    runbook_summary: dict[str, Any] = field(default_factory=dict)
    """런북 요약 정보 (이름, step 수, 위험도 등)."""

    reminder_count: int = 0
    """리마인더 발송 횟수."""

    last_reminder_at: str | None = None
    """마지막 리마인더 시각."""

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {
            "request_id": self.request_id,
            "execution_id": self.execution_id,
            "runbook_id": self.runbook_id,
            "namespace": self.namespace,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, Enum) else self.risk_level,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "runbook_summary": self.runbook_summary,
            "reminder_count": self.reminder_count,
            "last_reminder_at": self.last_reminder_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunbookApprovalRequest:
        """역직렬화."""
        from selfhealing.services.runbook.runbook_registry import RiskLevel

        return cls(
            request_id=data["request_id"],
            execution_id=data["execution_id"],
            runbook_id=data["runbook_id"],
            namespace=data["namespace"],
            risk_level=RiskLevel(data["risk_level"]),
            status=ApprovalDecisionType(data.get("status", "waiting")),
            created_at=data.get("created_at"),
            expires_at=data.get("expires_at"),
            decided_at=data.get("decided_at"),
            decided_by=data.get("decided_by"),
            runbook_summary=data.get("runbook_summary", {}),
            reminder_count=data.get("reminder_count", 0),
            last_reminder_at=data.get("last_reminder_at"),
        )
