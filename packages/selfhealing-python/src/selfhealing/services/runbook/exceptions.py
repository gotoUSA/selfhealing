"""
Runbook Executor 예외 계층.

RunbookExecutionError를 최상위로, 구체적인 실패 시나리오별 서브클래스를 정의한다.

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from selfhealing.core.exceptions import RunbookError

if TYPE_CHECKING:
    from selfhealing.services.runbook.execution_models import ApprovalDecisionType


class RunbookExecutionError(RunbookError):
    """Runbook 실행 중 일반 오류."""


class RunbookNotFoundError(RunbookExecutionError):
    """Runbook ID가 레지스트리에 없음."""


class RunbookLockConflictError(RunbookExecutionError):
    """Lock 획득 실패 — 같은 namespace에서 다른 복구/런북이 진행 중."""


class RunbookStepTimeoutError(RunbookExecutionError):
    """단일 Step 타임아웃 초과.

    타임아웃 발생 시 해당 Step은 In-doubt(partial_execution=True)로 마킹되어
    역순 보상 대상에 포함된다.
    """


class RunbookStaleContextError(RunbookExecutionError):
    """resume 시 컨텍스트가 stale 임계값을 초과.

    force=True로 재시도하면 stale 체크를 건너뛴다.
    """


class RunbookVersionMismatchError(RunbookExecutionError):
    """resume 시 Runbook 정의 버전이 실행 시점과 불일치.

    새 실행을 생성하거나 기존 Runbook 버전으로 롤백해야 한다.
    """


class RunbookCompensationError(RunbookExecutionError):
    """보상 중 오류.

    실제로 raise하지 않고 Fail-Open으로 처리된다.
    로그/DLQ 기록 목적으로 정의한다.
    """


# =============================================================================
# 승인 게이트 예외
# =============================================================================


class RunbookApprovalError(RunbookExecutionError):
    """승인 게이트 일반 오류.

    승인 요청을 찾지 못하거나, 강제 실행 시 justification이 비어있는 등
    승인 프로세스의 일반적인 오류 상황.
    """


class ApprovalAlreadyDecidedError(RunbookApprovalError):
    """승인 요청이 이미 다른 결정으로 확정된 경우.

    API 레이어에서 HTTP 409 Conflict로 매핑된다.
    타이머 만료(TIMER_APPROVED)와 수동 승인/거부가 동시에 도달하여
    CAS가 선착순으로 하나만 성공한 상황에서, 패배 측이 받는 예외.
    """

    def __init__(self, execution_id: str, current_status: ApprovalDecisionType):
        self.execution_id = execution_id
        self.current_status = current_status
        super().__init__(
            f"Approval already decided: {execution_id} "
            f"is in '{current_status.value}' state"
        )


class RunbookApprovalDuplicateError(RunbookApprovalError):
    """동일 runbook_id + namespace에 이미 대기 중인 승인 요청이 존재.

    장애 스톰 중 동일 런북이 반복 트리거될 때 승인 요청/알림 폭주를 방지한다.
    PendingRecoveryApprovalManager.create_request()의 중복 방지 패턴.
    """
