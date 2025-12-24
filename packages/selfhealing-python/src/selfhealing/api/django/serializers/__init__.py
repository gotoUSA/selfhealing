"""
Self-Healing API Serializers Package.

This package provides DRF serializers for the Self-Healing system.
"""

# Re-export from parent serializers.py for backwards compatibility
from selfhealing.api.django.serializers_legacy import (
    # Constants
    ControlAPIActions,
    ControlAPIEnvironments,
    RiskLevels,
    # Request Serializers
    ControlRequestSerializer,
    DLQReplayRequestSerializer,
    # Response Serializers
    EvidenceSerializer,
    ControlResponseSerializer,
    ControlErrorResponseSerializer,
    # Status & List Serializers
    ServiceStateSerializer,
    ControlStatusResponseSerializer,
    AuditLogSerializer,
    AuditLogListResponseSerializer,
    # Metrics Serializers
    ServiceMetricsSerializer,
    MetricsResponseSerializer,
    HealthCheckResponseSerializer,
    DLQReplayResponseSerializer,
)

# Config Serializers
from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    SLAConfigSerializer,
    RateLimitConfigSerializer,
    SecurityConfigSerializer,
    IdempotencyConfigSerializer,
    NotificationConfigSerializer,
    ForensicConfigSerializer,
    LoggingConfigSerializer,
    MetricsConfigSerializer,
)

# Metric Sync Serializers (Phase 1: Poll 제거 + Manual API)
from selfhealing.api.django.serializers.metric_sync import (
    MetricSyncRequestSerializer,
    MetricSyncResponseSerializer,
    DriftReportResponseSerializer,
)

__all__ = [
    # Constants
    "ControlAPIActions",
    "ControlAPIEnvironments",
    "RiskLevels",
    # Request Serializers
    "ControlRequestSerializer",
    "DLQReplayRequestSerializer",
    # Response Serializers
    "EvidenceSerializer",
    "ControlResponseSerializer",
    "ControlErrorResponseSerializer",
    # Status & List Serializers
    "ServiceStateSerializer",
    "ControlStatusResponseSerializer",
    "AuditLogSerializer",
    "AuditLogListResponseSerializer",
    # Metrics Serializers
    "ServiceMetricsSerializer",
    "MetricsResponseSerializer",
    "HealthCheckResponseSerializer",
    "DLQReplayResponseSerializer",
    # Config Serializers
    "CircuitBreakerConfigSerializer",
    "DLQConfigSerializer",
    "RetryConfigSerializer",
    "SLAConfigSerializer",
    "RateLimitConfigSerializer",
    "SecurityConfigSerializer",
    "IdempotencyConfigSerializer",
    "NotificationConfigSerializer",
    "ForensicConfigSerializer",
    "LoggingConfigSerializer",
    "MetricsConfigSerializer",
    # Metric Sync Serializers (Phase 1)
    "MetricSyncRequestSerializer",
    "MetricSyncResponseSerializer",
    "DriftReportResponseSerializer",
]
