"""
Emergency Coordination Layer.

긴급 상황 조율 레이어 - 독립적인 Emergency 시스템들 간의 연계를 조율합니다.

주요 컴포넌트:
- EmergencyCoordinator: 중앙 조율자
- AntiFlappingGuard: 플래핑 방지 히스테리시스 가드
- ScopedEmergencyState: 네임스페이스별 상태
- CoordinationAction: 연계 액션 정의

Phase 1 구현:
- EmergencyScope enum
- ScopedEmergencyState 모델
- EmergencyCoordinator 골격
- AntiFlappingGuard (히스테리시스 + recovery_hysteresis_factor)
- Dry-Run Mode

Phase 2 구현:
- CoordinationPolicyEngine: 정책 기반 액션 결정
- CoordinationPolicy: 연계 정책 정의
- CriticalPathFallback: Redis/Audit 장애 시 로컬 폴백
- AtomicLevelTransition: Lua 스크립트 원자적 상태 변경
- DomainAwareCrisisMultiplier: 도메인 인지형 가중치

Phase 4 구현:
- RecoveryAccountabilityConfig: 복구 책임 추적 설정
- OptimisticLocalActionExecutor: 낙관적 선조치 실행기

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from .anti_flapping import (
    EMERGENCY_LEVEL_COOLDOWN_SECONDS,
    AntiFlappingGuard,
)
from .atomic_transition import (
    ATOMIC_TRANSITION_SCRIPT,
    AtomicLevelTransition,
)
from .coordinator import (
    DryRunAuditLogger,
    EmergencyCoordinator,
)
from .crisis_multiplier import (
    MAX_CRISIS_MULTIPLIER,
    CrisisMultiplierRegistry,
    DomainAwareCrisisMultiplier,
)
from .critical_path_fallback import (
    CriticalPathFallback,
)
from .critical_worker import (
    CriticalPathDedicatedWorkerConfig,
    CriticalTaskPriority,
    WorkerQueueConfig,
    get_critical_worker_config,
    get_task_queue,
)
from .enums import (
    ActionType,
    CommandPrecedence,
    EmergencyScope,
    RecoveryStatus,
)

# Phase 2.7: Idempotent Step Handlers
from .idempotent_step_handlers import (
    IdempotencyRecord,
    IdempotencyStatus,
    IdempotentBudgetResetHandler,
    IdempotentCanaryResumeHandler,
    IdempotentGovernanceNormalHandler,
    IdempotentHealthCheckHandler,
    IdempotentStepHandler,
    IdempotentStepHandlerRegistry,
    generate_idempotency_key,
    get_idempotent_step_handler_registry,
    reset_idempotent_step_handler_registry,
)
from .models import (
    CoordinationActionResult,
    CoordinationAction,
    CoordinationResult,
    OverrideTTLConfig,
    RecoveryAccountabilityConfig,
    ScopedEmergencyState,
)
from .optimistic_action import (
    OptimisticActionResult,
    OptimisticLocalActionExecutor,
    get_optimistic_action_executor,
    reset_optimistic_action_executor,
)
from .pending_recovery_approval import (
    PendingRecoveryApprovalManager,
    RecoveryApprovalRequest,
    RecoveryApprovalStatus,
    get_pending_recovery_approval_manager,
    reset_pending_recovery_approval_manager,
)
from .policy_engine import (
    CoordinationPolicy,
    CoordinationPolicyEngine,
)

# Phase 5: Audit & Monitoring
from .recovery_audit import (
    DangerousForceRecoveryAuditEntry,
    ForceRecoveryType,
    RecoveryAuditEntry,
    RecoveryAuditEventType,
    RecoveryAuditRecorder,
    get_recovery_audit_recorder,
    record_dangerous_force_recovery,
    record_recovery_event,
)

# Phase 3: Recovery Extension
from .recovery_circuit_breaker import (
    RecoveryCircuitBreaker,
    RecoveryCircuitBreakerConfig,
    RecoveryCircuitState,
    RecoveryMetricsSnapshot,
    get_recovery_circuit_breaker,
    reset_recovery_circuit_breaker,
)

# Phase 5.5: Recovery Metrics
from .recovery_metrics import (
    PROMETHEUS_AVAILABLE,
    RecoveryMetricsRecorder,
    StepTimer,
    get_recovery_metrics_recorder,
    reset_recovery_metrics_recorder,
)

# Phase 5.6: Recovery Notifications
from .recovery_notifications import (
    recovery_aborted_notification,
    recovery_approval_required_notification,
    recovery_circuit_breaker_trip_notification,
    recovery_completed_notification,
    recovery_daily_summary_notification,
    recovery_failed_notification,
    recovery_stale_approval_reminder,
    recovery_started_notification,
    recovery_step_progress_notification,
)

# Phase 1.4: Recovery Session Archive
from .recovery_session_archive import (
    RecoverySessionArchiveData,
    RecoverySessionArchiveService,
    RecoveryStepArchiveData,
    get_recovery_session_archive_service,
    reset_recovery_session_archive_service,
)
from .recovery_shutdown import (
    RecoveryAwareShutdownConfig,
    RecoveryAwareShutdownHook,
    RecoveryShutdownStats,
    create_recovery_aware_shutdown_hook,
    run_prestop_check,
)

# Phase 5 Extension: Infrastructure Stability
from .redis_key_guard import (
    KeyPatternConfig,
    RedisKeyPriority,
    RedisKeyPriorityEviction,
    RedisMemoryInfo,
    get_redis_key_guard,
)
from .regional_recovery_policy import (
    DEFAULT_REGIONAL_CONFIGS,
    RegionalRecoveryConfig,
    RegionalRecoveryPolicyEngine,
    get_regional_recovery_policy_engine,
    reset_regional_recovery_policy_engine,
)

__all__ = [
    # Enums
    "EmergencyScope",
    "ActionType",
    "CommandPrecedence",
    "RecoveryStatus",
    # Models
    "CoordinationAction",
    "ScopedEmergencyState",
    "OverrideTTLConfig",
    "CoordinationActionResult",
    "CoordinationResult",
    # Phase 4: Recovery Accountability
    "RecoveryAccountabilityConfig",
    # Anti-Flapping
    "AntiFlappingGuard",
    "EMERGENCY_LEVEL_COOLDOWN_SECONDS",
    # Coordinator
    "EmergencyCoordinator",
    "DryRunAuditLogger",
    # Phase 2: Policy Engine
    "CoordinationPolicy",
    "CoordinationPolicyEngine",
    # Phase 2: Critical Path Fallback
    "CriticalPathFallback",
    # Phase 2: Atomic Transition
    "AtomicLevelTransition",
    "ATOMIC_TRANSITION_SCRIPT",
    # Phase 2: Crisis Multiplier
    "DomainAwareCrisisMultiplier",
    "CrisisMultiplierRegistry",
    "MAX_CRISIS_MULTIPLIER",
    # Phase 4: Optimistic Action
    "OptimisticLocalActionExecutor",
    "OptimisticActionResult",
    "get_optimistic_action_executor",
    "reset_optimistic_action_executor",
    # Phase 3: Recovery Circuit Breaker
    "RecoveryCircuitBreaker",
    "RecoveryCircuitBreakerConfig",
    "RecoveryCircuitState",
    "RecoveryMetricsSnapshot",
    "get_recovery_circuit_breaker",
    "reset_recovery_circuit_breaker",
    # Phase 3: Regional Recovery Policy
    "RegionalRecoveryConfig",
    "RegionalRecoveryPolicyEngine",
    "DEFAULT_REGIONAL_CONFIGS",
    "get_regional_recovery_policy_engine",
    "reset_regional_recovery_policy_engine",
    # Phase 3: Pending Recovery Approval
    "RecoveryApprovalStatus",
    "RecoveryApprovalRequest",
    "PendingRecoveryApprovalManager",
    "get_pending_recovery_approval_manager",
    "reset_pending_recovery_approval_manager",
    # Phase 5: Audit & Monitoring
    "ForceRecoveryType",
    "RecoveryAuditEventType",
    "DangerousForceRecoveryAuditEntry",
    "RecoveryAuditEntry",
    "RecoveryAuditRecorder",
    "get_recovery_audit_recorder",
    "record_dangerous_force_recovery",
    "record_recovery_event",
    # Phase 5 Extension: Redis Key Guard
    "RedisKeyPriority",
    "RedisMemoryInfo",
    "KeyPatternConfig",
    "RedisKeyPriorityEviction",
    "get_redis_key_guard",
    # Phase 5 Extension: Critical Worker
    "CriticalTaskPriority",
    "WorkerQueueConfig",
    "CriticalPathDedicatedWorkerConfig",
    "get_critical_worker_config",
    "get_task_queue",
    # Phase 5 Extension: Recovery Shutdown
    "RecoveryAwareShutdownConfig",
    "RecoveryShutdownStats",
    "RecoveryAwareShutdownHook",
    "create_recovery_aware_shutdown_hook",
    "run_prestop_check",
    # Phase 1.4: Recovery Session Archive
    "RecoveryStepArchiveData",
    "RecoverySessionArchiveData",
    "RecoverySessionArchiveService",
    "get_recovery_session_archive_service",
    "reset_recovery_session_archive_service",
    # Phase 2.7: Idempotent Step Handlers
    "IdempotencyStatus",
    "IdempotencyRecord",
    "IdempotentStepHandler",
    "IdempotentBudgetResetHandler",
    "IdempotentHealthCheckHandler",
    "IdempotentCanaryResumeHandler",
    "IdempotentGovernanceNormalHandler",
    "IdempotentStepHandlerRegistry",
    "get_idempotent_step_handler_registry",
    "reset_idempotent_step_handler_registry",
    "generate_idempotency_key",
    # Phase 5.5: Recovery Metrics
    "RecoveryMetricsRecorder",
    "StepTimer",
    "get_recovery_metrics_recorder",
    "reset_recovery_metrics_recorder",
    "PROMETHEUS_AVAILABLE",
    # Phase 5.6: Recovery Notifications
    "recovery_started_notification",
    "recovery_completed_notification",
    "recovery_failed_notification",
    "recovery_aborted_notification",
    "recovery_approval_required_notification",
    "recovery_stale_approval_reminder",
    "recovery_circuit_breaker_trip_notification",
    "recovery_step_progress_notification",
    "recovery_daily_summary_notification",
]
