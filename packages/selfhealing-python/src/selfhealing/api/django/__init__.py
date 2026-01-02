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
    
    # In your Django project's settings.py:
    MIDDLEWARE = [
        "selfhealing.audit.trace.trace_id_middleware",                  # [1] Trace ID
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",     # [2] Health Bridge
        "selfhealing.api.django.tiering.TieringMiddleware",             # [3] Tiering
        "selfhealing.api.django.middleware.SelfHealingMiddleware",      # [4] Self-Healing
        # ... Django Core Middlewares ...
        "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",  # [8] Pool CB
        # ... other middlewares ...
        "selfhealing.api.django.audit_middleware.AuditMiddleware",      # [11] Audit (맨 마지막!)
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

from selfhealing.api.django.middleware import (
    HealthBridgeMiddleware,
    SelfHealingMiddleware,
)

from selfhealing.api.django.pool_circuit_breaker import (
    PoolCircuitBreakerMiddleware,
    PoolCircuitBreaker,
    pool_circuit_breaker,
    circuit_breaker_status,
    circuit_breaker_reset,
)

from selfhealing.api.django.tiering import (
    TieringMiddleware,
    TierRegistry,
    get_tier_registry,
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
    # Middleware - Gateway Pipeline
    "HealthBridgeMiddleware",
    "SelfHealingMiddleware",
    "AuditMiddleware",
    "is_audit_middleware_enabled",
    "PoolCircuitBreakerMiddleware",
    "PoolCircuitBreaker",
    "pool_circuit_breaker",
    "circuit_breaker_status",
    "circuit_breaker_reset",
    "TieringMiddleware",
    "TierRegistry",
    "get_tier_registry",
]
