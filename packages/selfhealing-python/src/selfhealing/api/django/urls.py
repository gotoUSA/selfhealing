"""
Self-Healing API URL Configuration.

Include these URLs in your Django project's urls.py:

    from selfhealing.api.django import urls as selfhealing_urls

    urlpatterns = [
        # ... your other urls
        path('api/self-healing/', include(selfhealing_urls)),
    ]
"""

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
    path("metrics/", SelfHealingMetricsView.as_view(), name="metrics"),
    
    # DLQ
    path("dlq/replay/", DLQReplayView.as_view(), name="dlq-replay"),
]
