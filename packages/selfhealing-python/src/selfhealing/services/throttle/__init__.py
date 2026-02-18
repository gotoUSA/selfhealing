"""
Throttle Services for Self-Healing System.

Provides framework-agnostic throttling with:
- Base throttle logic (sliding window, token bucket)
- Netflix Gradient Adaptive Throttling
- DRF adapter for Django integration

Usage:
    from selfhealing.services.throttle import AdaptiveThrottle, ThrottleConfig

    throttle = AdaptiveThrottle(
        config=ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
        )
    )

    # Check if request is allowed
    allowed, info = throttle.check("user_123")

    # Record response time for gradient calculation
    throttle.record_response(response_time_ms=45.2)
"""

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    GradientCalculator,
    get_adaptive_throttle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.base import BaseThrottle, SlidingWindowThrottle
from selfhealing.services.throttle.gradient import (
    RTTSample,
    get_gradient_calculator,
    reset_gradient_calculators,
)
from selfhealing.services.throttle.dlq_sink import ThrottleDLQSink
from selfhealing.services.throttle.facade import AdaptiveThrottleFacade
from selfhealing.services.throttle.limit_adjuster import ThrottleLimitAdjuster
from selfhealing.services.throttle.policy import ThrottlePolicy
from selfhealing.services.throttle.cb_bridge import (
    RTTMetrics,
    RTTSeverity,
    ServiceHealthMetrics,
    ThrottleCircuitBreakerBridge,
    get_throttle_cb_bridge,
    reset_throttle_cb_bridge,
)
from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.services.throttle.dlq_integration import (
    ThrottleDeniedRequest,
    ThrottleDLQConfig,
    ThrottleDLQIntegration,
    get_throttle_dlq_integration,
    reset_throttle_dlq_integration,
)
from selfhealing.services.throttle.recovery_dampening import (
    RecoveryDampeningConfig,
    RecoveryDampeningManager,
    RecoveryPhase,
    ServiceRecoveryState,
    get_recovery_dampening_manager,
    reset_recovery_dampening_manager,
)
from selfhealing.services.throttle.redis_lua import (
    RedisThrottleLimitManager,
    ThrottleLuaScripts,
)
from selfhealing.services.throttle.registry import (
    ServiceThrottleConfig,
    ServiceThrottleState,
    ThrottleRegistry,
    get_throttle_registry,
    reset_throttle_registry,
)
from selfhealing.services.throttle.safe_open_fallback import (
    RedisConnectionState,
    SafeOpenConfig,
    SafeOpenFallbackManager,
    ServiceSafeLimitState,
    get_safe_open_fallback_manager,
    reset_safe_open_fallback_manager,
)
from selfhealing.services.throttle.time_bucketed_window import (
    BucketData,
    RTTWindowStats,
    TimeBucketedGradientCalculator,
    TimeBucketedRTTWindow,
)

__all__ = [
    "ThrottleConfig",
    "BaseThrottle",
    "SlidingWindowThrottle",
    "AdaptiveThrottle",
    "GradientCalculator",
    "RTTSample",
    "get_gradient_calculator",
    "reset_gradient_calculators",
    "get_adaptive_throttle",
    "reset_adaptive_throttle",
    # Registry
    "ThrottleRegistry",
    "ServiceThrottleConfig",
    "ServiceThrottleState",
    "get_throttle_registry",
    "reset_throttle_registry",
    # CB Bridge
    "ThrottleCircuitBreakerBridge",
    "RTTMetrics",
    "RTTSeverity",
    "ServiceHealthMetrics",
    "get_throttle_cb_bridge",
    "reset_throttle_cb_bridge",
    # DLQ Integration
    "ThrottleDeniedRequest",
    "ThrottleDLQConfig",
    "ThrottleDLQIntegration",
    "get_throttle_dlq_integration",
    "reset_throttle_dlq_integration",
    # Recovery Dampening
    "RecoveryDampeningConfig",
    "RecoveryDampeningManager",
    "RecoveryPhase",
    "ServiceRecoveryState",
    "get_recovery_dampening_manager",
    "reset_recovery_dampening_manager",
    # Redis Lua Scripts
    "ThrottleLuaScripts",
    "RedisThrottleLimitManager",
    # Safe-Open Fallback
    "SafeOpenConfig",
    "SafeOpenFallbackManager",
    "ServiceSafeLimitState",
    "RedisConnectionState",
    "get_safe_open_fallback_manager",
    "reset_safe_open_fallback_manager",
    # ThrottlePolicy (순수 Policy)
    "ThrottlePolicy",
    "ThrottleLimitAdjuster",
    "ThrottleDLQSink",
    "AdaptiveThrottleFacade",
    # Time-Bucketed Window
    "TimeBucketedRTTWindow",
    "TimeBucketedGradientCalculator",
    "RTTWindowStats",
    "BucketData",
]
