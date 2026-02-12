"""
Canary Rollout Module.

설정 변경의 점진적 배포 및 자동 롤백 시스템.

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Components:
    - models.py: CanaryState, CanaryStage, CanaryRollout, CanaryMetrics, PassCriteria
    - service.py: CanaryRolloutService, get_canary_rollout_service
    - locking.py: CanaryConfigLock, ConfigLockError
    - versioning.py: VersionChecker, check_version_and_rollback, VersionConflictError
    - chaos_guard.py: CanaryChaosGuard, ChaosConflictPolicy, ChaosConflictResult
    - audit.py: log_canary_action, log_canary_error
    - cross_cluster.py: CrossClusterNotifier, CrossClusterPropagationRequest, GovernancePolicySync
    - feature_flag.py: CanaryFeatureFlag, CanaryFlagConfig, CanaryConfigMiddleware

Usage:
    from selfhealing.services.canary import (
        # Data Models
        CanaryState,
        CanaryStage,
        CanaryRollout,
        CanaryMetrics,
        PassCriteria,
        # Service
        get_canary_rollout_service,
        CanaryRolloutService,
        # Locking
        CanaryConfigLock,
        ConfigLockError,
        # Versioning
        VersionConflictError,
        check_version_and_rollback,
        # Chaos Guard
        CanaryChaosGuard,
        ChaosConflictPolicy,
    )

    # Example: Create and start a rollout
    service = get_canary_rollout_service()
    rollout = service.create_rollout(
        config_type="circuit_breaker",
        new_values={"failure_threshold": 3},
        stages=[
            CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10),
        ],
        created_by="admin@example.com",
    )
    service.start_rollout(rollout.id)
"""

# Audit
from selfhealing.services.canary.audit import (
    log_canary_action,
    log_canary_error,
    log_canary_metrics_check,
)

# Chaos Guard
from selfhealing.services.canary.chaos_guard import (
    CanaryChaosGuard,
    ChaosConflictPolicy,
    ChaosConflictResult,
)

# Cross-Cluster (Step 5)
from selfhealing.services.canary.cross_cluster import (
    ClusterConfigChange,
    CrossClusterNotifier,
    CrossClusterPropagationRequest,
    GovernancePolicy,
    GovernancePolicySync,
    PropagationRequest,
    PropagationRequestStatus,
    get_cross_cluster_notifier,
    get_governance_policy_sync,
    get_propagation_request_service,
    reset_cross_cluster_services,
)

# Feature Flag (Step 6)
from selfhealing.services.canary.feature_flag import (
    CanaryConfigMiddleware,
    CanaryDecision,
    CanaryFeatureFlag,
    CanaryFlagConfig,
    CanarySelectionStrategy,
    get_canary_feature_flag,
    reset_canary_feature_flag,
)

# Safety Interlock (Step 7)
from selfhealing.services.canary.interlock import (
    CanarySafetyInterlock,
    InterlockAction,
    InterlockCheckFailure,
    InterlockResult,
    get_canary_safety_interlock,
    reset_canary_safety_interlock,
)

# Locking
from selfhealing.services.canary.locking import (
    CanaryConfigLock,
    ConfigLockError,
)

# Data Models
from selfhealing.services.canary.models import (
    CanaryMetrics,
    CanaryRollout,
    CanaryStage,
    CanaryState,
    PassCriteria,
    apply_tier_floor,
)

# Service
from selfhealing.services.canary.service import (
    CanaryRolloutService,
    get_canary_rollout_service,
    reset_canary_rollout_service,
)

# Versioning
from selfhealing.services.canary.versioning import (
    VersionChecker,
    VersionConflictError,
    check_version_and_rollback,
)

__all__ = [
    # Data Models
    "CanaryState",
    "CanaryStage",
    "CanaryRollout",
    "CanaryMetrics",
    "PassCriteria",
    "apply_tier_floor",
    # Service
    "CanaryRolloutService",
    "get_canary_rollout_service",
    "reset_canary_rollout_service",
    # Locking
    "CanaryConfigLock",
    "ConfigLockError",
    # Versioning
    "VersionChecker",
    "VersionConflictError",
    "check_version_and_rollback",
    # Chaos Guard
    "CanaryChaosGuard",
    "ChaosConflictPolicy",
    "ChaosConflictResult",
    # Audit
    "log_canary_action",
    "log_canary_error",
    "log_canary_metrics_check",
    # Cross-Cluster (Step 5)
    "ClusterConfigChange",
    "PropagationRequest",
    "PropagationRequestStatus",
    "GovernancePolicy",
    "CrossClusterNotifier",
    "CrossClusterPropagationRequest",
    "GovernancePolicySync",
    "get_cross_cluster_notifier",
    "get_propagation_request_service",
    "get_governance_policy_sync",
    "reset_cross_cluster_services",
    # Feature Flag (Step 6)
    "CanarySelectionStrategy",
    "CanaryFlagConfig",
    "CanaryDecision",
    "CanaryFeatureFlag",
    "CanaryConfigMiddleware",
    "get_canary_feature_flag",
    "reset_canary_feature_flag",
    # Safety Interlock (Step 7)
    "InterlockAction",
    "InterlockCheckFailure",
    "InterlockResult",
    "CanarySafetyInterlock",
    "get_canary_safety_interlock",
    "reset_canary_safety_interlock",
]
