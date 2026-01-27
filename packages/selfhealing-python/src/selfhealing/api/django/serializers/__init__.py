"""
Self-Healing API Serializers Package.

This package provides DRF serializers for the Self-Healing system.
"""

# Config Serializers
from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    ForensicConfigSerializer,
    IdempotencyConfigSerializer,
    LoggingConfigSerializer,
    MetricsConfigSerializer,
    NotificationConfigSerializer,
    RateLimitConfigSerializer,
    RetryConfigSerializer,
    SecurityConfigSerializer,
    SLAConfigSerializer,
)

# Metric Sync Serializers
from selfhealing.api.django.serializers.metric_sync import (
    DriftReportResponseSerializer,
    MetricSyncRequestSerializer,
    MetricSyncResponseSerializer,
)

# Re-export from parent serializers.py for backwards compatibility
from selfhealing.api.django.serializers_legacy import (  # Constants; Request Serializers; Response Serializers; Status & List Serializers; Metrics Serializers
    AuditLogListResponseSerializer,
    AuditLogSerializer,
    ControlAPIActions,
    ControlAPIEnvironments,
    ControlErrorResponseSerializer,
    ControlRequestSerializer,
    ControlResponseSerializer,
    ControlStatusResponseSerializer,
    DLQReplayRequestSerializer,
    DLQReplayResponseSerializer,
    EvidenceSerializer,
    HealthCheckResponseSerializer,
    MetricsResponseSerializer,
    RiskLevels,
    ServiceMetricsSerializer,
    ServiceStateSerializer,
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
    # Metric Sync Serializers
    "MetricSyncRequestSerializer",
    "MetricSyncResponseSerializer",
    "DriftReportResponseSerializer",
]
