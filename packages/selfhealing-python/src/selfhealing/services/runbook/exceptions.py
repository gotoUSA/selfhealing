"""
Runbook Executor 예외 계층.

RunbookExecutionError를 최상위로, 구체적인 실패 시나리오별 서브클래스를 정의한다.

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §12
"""

from __future__ import annotations


class RunbookExecutionError(Exception):
    """Runbook 실행 중 일반 오류."""


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
