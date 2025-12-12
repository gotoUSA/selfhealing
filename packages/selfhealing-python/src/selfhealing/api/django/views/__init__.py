"""
Self-Healing API Views Package.

This package provides REST API endpoints for the Self-Healing system.

Modules:
- circuit_breaker: Control API views for Circuit Breaker management
- dlq: DLQ (Dead Letter Queue) management views
- health: Health check and metrics views
"""

# Circuit Breaker Control Views
from selfhealing.api.django.views.circuit_breaker import (
    ControlRequest,
    ControlResponse,
    ControlAPIService,
    get_control_api_service,
    ControlActionView,
    ControlStatusView,
    ServiceStatusView,
    ControlAuditView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
)

# DLQ Views
from selfhealing.api.django.views.dlq import (
    DLQReplayView,
)

# Health & Metrics Views
from selfhealing.api.django.views.health import (
    SelfHealingHealthView,
    LivenessView,
    ReadinessView,
    ConnectionPoolHealthView,
    simple_health_ping,
    SelfHealingMetricsView,
)

__all__ = [
    # Data classes
    "ControlRequest",
    "ControlResponse",
    # Service
    "ControlAPIService",
    "get_control_api_service",
    # Control Views
    "ControlActionView",
    "ControlStatusView",
    "ServiceStatusView",
    "ControlAuditView",
    # Quick Action Views
    "QuickAllowView",
    "QuickBlockView",
    "QuickResetView",
    # DLQ Views
    "DLQReplayView",
    # Health Views
    "SelfHealingHealthView",
    "LivenessView",
    "ReadinessView",
    "ConnectionPoolHealthView",
    "simple_health_ping",
    "SelfHealingMetricsView",
]
