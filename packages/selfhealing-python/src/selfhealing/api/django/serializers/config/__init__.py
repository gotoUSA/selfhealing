"""
Configuration Serializers Package.

Provides backward-compatible re-exports of all config serializers.

Serializers for validating and serializing runtime configuration updates.
Includes apply strategy support (immediate, delayed, graceful).

Phase 6: Fail-Safe Default 강화 추가.
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

# Base
from .base import ApplyStrategyMixin

# Core configs
from .core_configs import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    RateLimitConfigSerializer,
    IdempotencyConfigSerializer,
)

# SLO configs
from .slo_configs import (
    SLAConfigSerializer,
    SLODefinitionSerializer,
    SLOConfigSerializer,
    ErrorBudgetConfigSerializer,
)

# Security configs
from .security_configs import (
    SecurityConfigSerializer,
    NotificationConfigSerializer,
)

# Advanced configs
from .advanced_configs import (
    ForensicConfigSerializer,
    MetricsConfigSerializer,
    LoggingConfigSerializer,
)

# Pending configs
from .pending_configs import (
    PendingConfigChangeSerializer,
    CancelPendingChangeSerializer,
)

# Storage configs
from .storage_configs import (
    L2StorageConfigSerializer,
    L2StorageStatusSerializer,
    ShadowLogEntrySerializer,
    ShadowLogStatsSerializer,
    ReplayAutomationConfigSerializer,
)

__all__ = [
    # Base
    "ApplyStrategyMixin",
    # Core configs
    "CircuitBreakerConfigSerializer",
    "DLQConfigSerializer",
    "RetryConfigSerializer",
    "RateLimitConfigSerializer",
    "IdempotencyConfigSerializer",
    # SLO configs
    "SLAConfigSerializer",
    "SLODefinitionSerializer",
    "SLOConfigSerializer",
    "ErrorBudgetConfigSerializer",
    # Security configs
    "SecurityConfigSerializer",
    "NotificationConfigSerializer",
    # Advanced configs
    "ForensicConfigSerializer",
    "MetricsConfigSerializer",
    "LoggingConfigSerializer",
    # Pending configs
    "PendingConfigChangeSerializer",
    "CancelPendingChangeSerializer",
    # Storage configs
    "L2StorageConfigSerializer",
    "L2StorageStatusSerializer",
    "ShadowLogEntrySerializer",
    "ShadowLogStatsSerializer",
    "ReplayAutomationConfigSerializer",
]
