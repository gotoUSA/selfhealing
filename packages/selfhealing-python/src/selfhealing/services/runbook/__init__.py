"""
Automated Runbook Executor — 자동화된 런북 실행 오케스트레이션 패키지.

기존 복구 컴포넌트(RecoveryCoordinator, AutoRollbackGuard, ActionExecutor 등)를
선언적 런북 정의에 따라 통합 호출하는 파사드/오케스트레이터 계층입니다.

Modules:
    models             — 패턴 매칭 데이터 모델 (조건, 결과)
    metrics_provider   — 범용 메트릭 제공자 Protocol
    duration_tracker   — min_duration_seconds Redis/인메모리 추적
    pattern_matcher    — 현재 증상 → 런북 트리거 조건 매칭
    runbook_registry   — 런북/Step/ActionPrimitive 등록·조회·관리
    execution_models   — RunbookExecutionContext, RunbookStepResult 등 실행 모델
    exceptions         — RunbookExecutionError 계층
    resolvers          — ParamResolver Protocol, DotPathResolver (변수 치환)
    contracts          — CompensationContract (보상 No-op 안전성 검증)
    executor           — 단계별 실행, 중간 검증, 보상
    approval_gate      — 위험도별 자동/수동 승인
    playback_recorder  — 실행 과정 재생 가능 기록
    service            — 통합 서비스 (파이프라인 오케스트레이션)

Reference:
    docs/self_healing/middleware_system/272_RUNBOOK_ARCHITECTURE_OVERVIEW.md
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md
"""

from selfhealing.services.runbook.approval_gate import (
    RunbookApprovalGate,
    get_runbook_approval_gate,
    reset_runbook_approval_gate,
)
from selfhealing.services.runbook.contracts import CompensationContract
from selfhealing.services.runbook.duration_tracker import DurationTracker
from selfhealing.services.runbook.exceptions import (
    ApprovalAlreadyDecidedError,
    RunbookApprovalDuplicateError,
    RunbookApprovalError,
    RunbookCompensationError,
    RunbookExecutionError,
    RunbookLockConflictError,
    RunbookStaleContextError,
    RunbookStepTimeoutError,
    RunbookVersionMismatchError,
)
from selfhealing.services.runbook.execution_models import (
    ApprovalDecision,
    ApprovalDecisionType,
    CompensationSummary,
    RecordingSummary,
    RunbookApprovalRequest,
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.executor import RunbookExecutor
from selfhealing.services.runbook.recorder import RunbookPlaybackRecorder
from selfhealing.services.runbook.metrics_provider import RunbookMetricsProvider
from selfhealing.services.runbook.models import (
    ConditionOperator,
    EventCondition,
    LabelFilter,
    MatchResult,
    MatchSelectionResult,
    MetricCondition,
    PatternCondition,
)
from selfhealing.services.runbook.pattern_matcher import PatternMatcher
from selfhealing.services.runbook.resolvers import DotPathResolver, ParamResolver, resolve_params
from selfhealing.services.runbook.runbook_registry import (
    BUILTIN_CATEGORIES,
    ActionHandler,
    ActionPrimitiveRegistry,
    RiskLevel,
    Runbook,
    RunbookRegistry,
    RunbookStep,
    RunbookStepContext,
    StepCondition,
)

__all__ = [
    # Data Models (Pattern Matching)
    "ConditionOperator",
    "LabelFilter",
    "MetricCondition",
    "EventCondition",
    "PatternCondition",
    "MatchResult",
    "MatchSelectionResult",
    # Metrics Provider Protocol
    "RunbookMetricsProvider",
    # Duration Tracker
    "DurationTracker",
    # Pattern Matcher
    "PatternMatcher",
    # Runbook Registry
    "RiskLevel",
    "StepCondition",
    "RunbookStep",
    "RunbookStepContext",
    "ActionHandler",
    "Runbook",
    "ActionPrimitiveRegistry",
    "BUILTIN_CATEGORIES",
    "RunbookRegistry",
    # Execution Models
    "RunbookExecutionStatus",
    "RunbookStepResult",
    "CompensationSummary",
    "RunbookExecutionContext",
    "RecordingSummary",
    # Approval Gate Models
    "ApprovalDecisionType",
    "ApprovalDecision",
    "RunbookApprovalRequest",
    # Approval Gate
    "RunbookApprovalGate",
    "get_runbook_approval_gate",
    "reset_runbook_approval_gate",
    # Exceptions
    "RunbookExecutionError",
    "RunbookLockConflictError",
    "RunbookStepTimeoutError",
    "RunbookStaleContextError",
    "RunbookVersionMismatchError",
    "RunbookCompensationError",
    "RunbookApprovalError",
    "ApprovalAlreadyDecidedError",
    "RunbookApprovalDuplicateError",
    # Resolvers
    "ParamResolver",
    "DotPathResolver",
    "resolve_params",
    # Contracts
    "CompensationContract",
    # Executor
    "RunbookExecutor",
    # Recorder
    "RunbookPlaybackRecorder",
]
