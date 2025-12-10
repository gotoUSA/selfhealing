"""
Django REST Framework API for the self-healing system.

This module provides DRF views, serializers, and URL configurations
for the self-healing control API.

Usage:
    # In your Django project's urls.py:
    from selfhealing.api.django import urls as selfhealing_urls

    urlpatterns = [
        path('api/self-healing/', include(selfhealing_urls)),
    ]
"""

from selfhealing.api.django.views import (
    ControlActionView,
    ControlStatusView,
    ServiceStatusView,
    ControlAuditView,
    QuickAllowView,
    QuickBlockView,
    QuickResetView,
    SelfHealingHealthView,
    SelfHealingMetricsView,
    DLQReplayView,
    get_control_api_service,
    ControlAPIService,
)

from selfhealing.api.django.serializers import (
    ControlRequestSerializer,
    ControlResponseSerializer,
    ControlErrorResponseSerializer,
    ControlStatusResponseSerializer,
    ServiceStateSerializer,
    AuditLogListResponseSerializer,
    MetricsResponseSerializer,
    ControlAPIActions,
    ControlAPIEnvironments,
)

__all__ = [
    # Views
    "ControlActionView",
    "ControlStatusView",
    "ServiceStatusView",
    "ControlAuditView",
    "QuickAllowView",
    "QuickBlockView",
    "QuickResetView",
    "SelfHealingHealthView",
    "SelfHealingMetricsView",
    "DLQReplayView",
    # Service
    "get_control_api_service",
    "ControlAPIService",
    # Serializers
    "ControlRequestSerializer",
    "ControlResponseSerializer",
    "ControlErrorResponseSerializer",
    "ControlStatusResponseSerializer",
    "ServiceStateSerializer",
    "AuditLogListResponseSerializer",
    "MetricsResponseSerializer",
    # Constants
    "ControlAPIActions",
    "ControlAPIEnvironments",
]
