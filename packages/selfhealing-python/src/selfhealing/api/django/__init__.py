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
    
    # In your Django project's settings.py (AuditMiddleware는 맨 마지막!):
    MIDDLEWARE = [
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단
        # ... 다른 미들웨어들 ...
        "selfhealing.api.django.audit_middleware.AuditMiddleware",  # 맨 마지막!
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

from selfhealing.api.django.audit_middleware import (
    AuditMiddleware,
    is_audit_middleware_enabled,
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
    # Middleware (AuditMiddleware - Gateway Pipeline)
    "AuditMiddleware",
    "is_audit_middleware_enabled",
]
