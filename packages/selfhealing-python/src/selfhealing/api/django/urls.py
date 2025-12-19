"""
Self-Healing API URL Configuration.

Include these URLs in your Django project's urls.py:

    from selfhealing.api.django import urls as selfhealing_urls

    urlpatterns = [
        # ... your other urls
        path('api/self-healing/', include(selfhealing_urls)),
    ]
"""

from django.conf import settings
from django.urls import path

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
    DLQCleanupStatsView,
    DLQArchiveView,
    DLQPurgeView,
    LivenessView,
    ReadinessView,
    ConnectionPoolHealthView,
    simple_health_ping,
)

# Pool Circuit Breaker API
from selfhealing.api.django.pool_circuit_breaker import (
    circuit_breaker_status,
    circuit_breaker_reset,
)

# DLQ Detail Views
from selfhealing.api.django.views.dlq import (
    DLQReplayView,
    DLQCleanupStatsView,
    DLQArchiveView,
    DLQPurgeView,
    DLQListView,
    DLQDetailView,
    DLQRetryView,
    DLQResolveView,
)

# Dashboard Views
from selfhealing.api.django.views.dashboard import (
    DashboardSummaryView,
)

# System Control Views (Kill Switch)
from selfhealing.api.django.views.system_control import (
    SystemStatusView,
    SystemEnableView,
    SystemDisableView,
    DryRunEnableView,
    DryRunDisableView,
)

app_name = "selfhealing"

urlpatterns = [
    # Control API
    path("control/", ControlActionView.as_view(), name="control-action"),
    # Status endpoints
    path("status/", ControlStatusView.as_view(), name="status"),
    path("status/<str:service_name>/", ServiceStatusView.as_view(), name="service-status"),
    # Audit logs
    path("audit/", ControlAuditView.as_view(), name="audit"),
    # Quick actions
    path("allow/<str:service_name>/", QuickAllowView.as_view(), name="quick-allow"),
    path("block/<str:service_name>/", QuickBlockView.as_view(), name="quick-block"),
    path("reset/<str:service_name>/", QuickResetView.as_view(), name="quick-reset"),
    # Health & Metrics
    path("health/", SelfHealingHealthView.as_view(), name="health"),
    path("health/live/", LivenessView.as_view(), name="health-liveness"),
    path("health/ready/", ReadinessView.as_view(), name="health-readiness"),
    path("health/pool/", ConnectionPoolHealthView.as_view(), name="health-pool"),
    path("health/ping/", simple_health_ping, name="health-ping"),
    path("metrics/", SelfHealingMetricsView.as_view(), name="metrics"),
    # DLQ
    path("dlq/replay/", DLQReplayView.as_view(), name="dlq-replay"),
    path("dlq/cleanup/stats/", DLQCleanupStatsView.as_view(), name="dlq-cleanup-stats"),
    path("dlq/cleanup/archive/", DLQArchiveView.as_view(), name="dlq-cleanup-archive"),
    path("dlq/cleanup/purge/", DLQPurgeView.as_view(), name="dlq-cleanup-purge"),
    path("dlq/list/", DLQListView.as_view(), name="dlq-list"),
    path("dlq/<int:pk>/", DLQDetailView.as_view(), name="dlq-detail"),
    path("dlq/<int:pk>/retry/", DLQRetryView.as_view(), name="dlq-retry"),
    path("dlq/<int:pk>/resolve/", DLQResolveView.as_view(), name="dlq-resolve"),
    # Dashboard
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    # Pool Circuit Breaker API
    path("circuit-breaker/pool/status/", circuit_breaker_status, name="pool-cb-status"),
    path("circuit-breaker/pool/reset/", circuit_breaker_reset, name="pool-cb-reset"),
    # System Control (Global Kill Switch)
    path("system/status/", SystemStatusView.as_view(), name="system-status"),
    path("system/enable/", SystemEnableView.as_view(), name="system-enable"),
    path("system/disable/", SystemDisableView.as_view(), name="system-disable"),
    # Dry Run Mode
    path("system/dry-run/enable/", DryRunEnableView.as_view(), name="dry-run-enable"),
    path("system/dry-run/disable/", DryRunDisableView.as_view(), name="dry-run-disable"),
]

# Stress Test Endpoints - DEBUG 모드에서만 활성화 (프로덕션 제외)
if getattr(settings, "DEBUG", False) or getattr(settings, "ENABLE_STRESS_TESTS", False):
    from selfhealing.api.django.stress_views import (
        slow_query_5s,
        slow_query_10s,
        connection_leak_simulation,
        pool_status,
        heavy_concurrent_query,
    )

    urlpatterns += [
        path("stress/slow-5s/", slow_query_5s, name="stress-slow-5s"),
        path("stress/slow-10s/", slow_query_10s, name="stress-slow-10s"),
        path("stress/leak/", connection_leak_simulation, name="stress-leak"),
        path("stress/pool-status/", pool_status, name="stress-pool-status"),
        path("stress/heavy-query/", heavy_concurrent_query, name="stress-heavy-query"),
    ]
