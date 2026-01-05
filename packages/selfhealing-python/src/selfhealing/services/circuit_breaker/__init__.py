"""
Circuit Breaker Module

Provides circuit breaker functionality for external service protection.

Features:
- Toggle-based circuit breaker (not automatic failure counting)
- Manual force open/close by operators
- Conditional replay trigger when circuit breaker closes
- Rate limit cascade detection (auto-open CB on 429 storm)
- Self-DDoS protection (prevent retry amplification)
- Adaptive Threshold (Emergency Level 연동)
- Freeze Mode (LOCKDOWN 시 상태 동결)
- Panic Threshold (70% OPEN 감지 시 Emergency Level 3 선포)
- Distributed Tracing (CB 상태 변화 trace_id 추적)

Structure:
- config.py: Configuration and types (~125 lines)
- rate_limit_tracker.py: Rate limit tracking (~95 lines)
- protection.py: Protection mixin (~250 lines)
- manual_control.py: Manual control mixin (~350 lines)
- service.py: Main service class (~285 lines)
- convenience.py: Module-level functions (~135 lines)
- models.py: Advanced protection data models
- adaptive_threshold.py: Emergency Level 연동 임계값 조정
- freeze_mode.py: LOCKDOWN Freeze Mode
- panic_threshold.py: 시스템 전체 자폭 방지
- tracing.py: Distributed Tracing 연동

Usage:
    from selfhealing.services.circuit_breaker import (
        CircuitBreakerService,
        CircuitBreakerConfig,
        CircuitBreakerResult,
        CircuitState,
        should_allow_request,
        force_open_circuit,
        force_close_circuit,
    )
"""

# Config and types
from .config import (
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    FallbackResult,
)

# Rate limit tracking
from .rate_limit_tracker import (
    RateLimitTracker,
    get_rate_limit_tracker,
)

# Mixins (for extension)
from .protection import ProtectionMixin
from .manual_control import ManualControlMixin

# Main service
from .service import CircuitBreakerService

# Convenience functions
from .convenience import (
    get_circuit_breaker_service,
    should_allow_request,
    force_open_circuit,
    force_close_circuit,
    record_rate_limit,
    should_allow_with_protection,
    get_protection_status,
)

# Advanced Protection Models (Phase 0)
from .models import (
    ServiceConfig,
    SheddingLevel,
    LoadSheddingPolicy,
    CanaryStage,
    RecoveryStrategy,
    ThresholdMultiplier,
    AdaptiveThresholdPolicy,
    OpenStrategy,
    CircuitBreakerAdvancedConfig,
    PanicThresholdConfig,
    FreezeModeState,
)

# Adaptive Threshold (Phase 1)
from .adaptive_threshold import (
    AdaptiveThresholdManager,
    AdjustedThreshold,
    get_adaptive_threshold_manager,
    get_adjusted_cb_threshold,
    should_allow_cb_auto_open,
)

# Freeze Mode (Phase 1)
from .freeze_mode import (
    FreezeModeManager,
    FreezeReason,
    get_freeze_mode_manager,
    is_freeze_mode_active,
    should_allow_cb_state_change,
)

# Panic Threshold (Phase 1)
from .panic_threshold import (
    PanicThresholdMonitor,
    PanicThresholdResult,
    get_panic_threshold_monitor,
    check_panic_threshold,
    is_panic_threshold_triggered,
)

# Distributed Tracing (Phase 2)
from .tracing import (
    TracingConfig,
    TriggeringRequestInfo,
    TraceContextProvider,
    CircuitBreakerTracingManager,
    get_tracing_manager,
    record_failure_with_trace,
    get_triggering_request,
    log_state_change_with_trace,
)

# Service Config Manager (Phase 3)
from .service_config import (
    ServiceConfigManager,
    get_service_config_manager,
    reset_service_config_manager,
    register_service,
    get_service_config,
    get_services_by_criticality,
    get_shedding_targets,
    is_critical_service,
)

# Blast Radius Integration (Phase 3)
from .blast_radius_integration import (
    BlastRadiusLevel,
    BlastRadiusAssessment,
    ServiceDependency,
    ServiceDependencyGraph,
    BlastRadiusIntegration,
    BlastRadiusConfig,
    get_blast_radius_integration,
    reset_blast_radius_integration,
    assess_cb_open_impact,
    should_allow_cb_auto_open as should_allow_cb_auto_open_blast,
    register_service_dependency,
)

__all__ = [
    # Config and types
    "CircuitBreakerConfig",
    "CircuitBreakerResult",
    "CircuitState",
    "FallbackResult",
    # Rate limit tracking
    "RateLimitTracker",
    "get_rate_limit_tracker",
    # Mixins
    "ProtectionMixin",
    "ManualControlMixin",
    # Main service
    "CircuitBreakerService",
    # Convenience functions
    "get_circuit_breaker_service",
    "should_allow_request",
    "force_open_circuit",
    "force_close_circuit",
    "record_rate_limit",
    "should_allow_with_protection",
    "get_protection_status",
    # Advanced Protection Models (Phase 0)
    "ServiceConfig",
    "SheddingLevel",
    "LoadSheddingPolicy",
    "CanaryStage",
    "RecoveryStrategy",
    "ThresholdMultiplier",
    "AdaptiveThresholdPolicy",
    "OpenStrategy",
    "CircuitBreakerAdvancedConfig",
    "PanicThresholdConfig",
    "FreezeModeState",
    # Adaptive Threshold (Phase 1)
    "AdaptiveThresholdManager",
    "AdjustedThreshold",
    "get_adaptive_threshold_manager",
    "get_adjusted_cb_threshold",
    "should_allow_cb_auto_open",
    # Freeze Mode (Phase 1)
    "FreezeModeManager",
    "FreezeReason",
    "get_freeze_mode_manager",
    "is_freeze_mode_active",
    "should_allow_cb_state_change",
    # Panic Threshold (Phase 1)
    "PanicThresholdMonitor",
    "PanicThresholdResult",
    "get_panic_threshold_monitor",
    "check_panic_threshold",
    "is_panic_threshold_triggered",
    # Distributed Tracing (Phase 2)
    "TracingConfig",
    "TriggeringRequestInfo",
    "TraceContextProvider",
    "CircuitBreakerTracingManager",
    "get_tracing_manager",
    "record_failure_with_trace",
    "get_triggering_request",
    "log_state_change_with_trace",
    # Service Config Manager (Phase 3)
    "ServiceConfigManager",
    "get_service_config_manager",
    "reset_service_config_manager",
    "register_service",
    "get_service_config",
    "get_services_by_criticality",
    "get_shedding_targets",
    "is_critical_service",
    # Blast Radius Integration (Phase 3)
    "BlastRadiusLevel",
    "BlastRadiusAssessment",
    "ServiceDependency",
    "ServiceDependencyGraph",
    "BlastRadiusIntegration",
    "BlastRadiusConfig",
    "get_blast_radius_integration",
    "reset_blast_radius_integration",
    "assess_cb_open_impact",
    "should_allow_cb_auto_open_blast",
    "register_service_dependency",
]
