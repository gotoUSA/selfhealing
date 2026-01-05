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
]
