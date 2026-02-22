"""
Audit Resilience API Endpoints.

Provides REST API for managing audit system resilience:
- Circuit breaker status and control
- Metrics endpoint (Prometheus format)
- Degraded mode status and control
- Health check endpoint
"""

import structlog

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = structlog.get_logger()


class AuditHealthView(View):
    """
    Health check endpoint for audit system.

    GET /api/v1/audit/health
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """Get audit system health status."""
        try:
            from selfhealing.audit import (
                get_audit_logger,
                get_degraded_mode_manager,
            )
            from selfhealing.audit.resilience import CircuitBreakerRegistry

            logger_instance = get_audit_logger()
            degraded_manager = get_degraded_mode_manager()
            circuit_registry = CircuitBreakerRegistry.get_instance()

            # Get backend health
            backend_health = logger_instance.get_backend_health()

            # Get degraded mode status
            degraded_status = degraded_manager.get_status()

            # Get circuit breaker summary
            circuit_stats = circuit_registry.get_all_stats()
            open_circuits = circuit_registry.get_open_circuits()

            # Determine overall status
            if degraded_status["degraded"]:
                status = "degraded"
            elif open_circuits:
                status = "warning"
            else:
                status = "healthy"

            return JsonResponse(
                {
                    "status": status,
                    "backend": backend_health,
                    "degraded_mode": degraded_status,
                    "circuit_breakers": {
                        "open_count": len(open_circuits),
                        "open_backends": open_circuits,
                        "total_count": len(circuit_stats),
                    },
                }
            )

        except Exception as e:
            logger.error(
                "audit_health_view.error",
                error=e,
            )
            return JsonResponse(
                {
                    "status": "error",
                    "error": str(e),
                },
                status=500,
            )


class CircuitBreakerStatusView(View):
    """
    Circuit breaker status and control.

    GET /api/v1/audit/circuit-breakers
    POST /api/v1/audit/circuit-breakers/{name}/reset
    POST /api/v1/audit/circuit-breakers/{name}/force-open
    POST /api/v1/audit/circuit-breakers/reset-all
    """

    def get(self, request: HttpRequest, name: str = None) -> JsonResponse:
        """Get circuit breaker status."""
        try:
            from selfhealing.audit.resilience import CircuitBreakerRegistry

            registry = CircuitBreakerRegistry.get_instance()

            if name:
                cb = registry.get(name)
                if not cb:
                    return JsonResponse(
                        {"error": f"Circuit breaker '{name}' not found"}, status=404
                    )
                return JsonResponse(cb.get_stats())
            else:
                return JsonResponse(
                    {
                        "circuit_breakers": registry.get_all_stats(),
                        "open_circuits": registry.get_open_circuits(),
                    }
                )

        except Exception as e:
            logger.error(
                "circuit_breaker_status_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class CircuitBreakerResetView(View):
    """Reset a circuit breaker."""

    def post(self, request: HttpRequest, name: str) -> JsonResponse:
        """Reset a specific circuit breaker."""
        try:
            from selfhealing.audit.resilience import CircuitBreakerRegistry

            registry = CircuitBreakerRegistry.get_instance()
            cb = registry.get(name)

            if not cb:
                return JsonResponse(
                    {"error": f"Circuit breaker '{name}' not found"}, status=404
                )

            cb.reset()

            return JsonResponse(
                {
                    "message": f"Circuit breaker '{name}' reset successfully",
                    "state": cb.get_stats(),
                }
            )

        except Exception as e:
            logger.error(
                "circuit_breaker_reset_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class CircuitBreakerForceOpenView(View):
    """Force open a circuit breaker."""

    def post(self, request: HttpRequest, name: str) -> JsonResponse:
        """Force open a specific circuit breaker."""
        try:
            from selfhealing.audit.resilience import CircuitBreakerRegistry

            registry = CircuitBreakerRegistry.get_instance()
            cb = registry.get(name)

            if not cb:
                return JsonResponse(
                    {"error": f"Circuit breaker '{name}' not found"}, status=404
                )

            cb.force_open()

            return JsonResponse(
                {
                    "message": f"Circuit breaker '{name}' forced open",
                    "state": cb.get_stats(),
                }
            )

        except Exception as e:
            logger.error(
                "circuit_breaker_force_open_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class CircuitBreakerResetAllView(View):
    """Reset all circuit breakers."""

    def post(self, request: HttpRequest) -> JsonResponse:
        """Reset all circuit breakers."""
        try:
            from selfhealing.audit.resilience import CircuitBreakerRegistry

            registry = CircuitBreakerRegistry.get_instance()
            registry.reset_all()

            return JsonResponse(
                {
                    "message": "All circuit breakers reset",
                    "circuit_breakers": registry.get_all_stats(),
                }
            )

        except Exception as e:
            logger.error(
                "circuit_breaker_reset_all_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class AuditMetricsView(View):
    """
    Prometheus-compatible metrics endpoint.

    GET /api/v1/audit/metrics
    GET /api/v1/audit/metrics?format=json
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """Get audit metrics."""
        try:
            from selfhealing.audit import get_audit_metrics

            metrics = get_audit_metrics()
            output_format = request.GET.get("format", "prometheus")

            if output_format == "json":
                return JsonResponse(metrics.get_metrics())
            else:
                # Prometheus text format
                content = metrics.get_prometheus_format()
                return HttpResponse(
                    content, content_type="text/plain; version=0.0.4; charset=utf-8"
                )

        except Exception as e:
            logger.error(
                "audit_metrics_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class DegradedModeStatusView(View):
    """
    Degraded mode status and control.

    GET /api/v1/audit/degraded-mode
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """Get degraded mode status."""
        try:
            from selfhealing.audit import get_degraded_mode_manager

            manager = get_degraded_mode_manager()
            return JsonResponse(manager.get_status())

        except Exception as e:
            logger.error(
                "degraded_mode_status_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class DegradedModeForceView(View):
    """Force degraded mode on/off."""

    def post(self, request: HttpRequest, action: str) -> JsonResponse:
        """Force degraded mode on or off."""
        try:
            import json

            from selfhealing.audit import get_degraded_mode_manager

            manager = get_degraded_mode_manager()

            if action == "enter":
                # Get reason from request body
                try:
                    body = json.loads(request.body) if request.body else {}
                    reason = body.get("reason", "Manual override via API")
                except json.JSONDecodeError:
                    reason = "Manual override via API"

                manager.force_degraded(reason)
                return JsonResponse(
                    {
                        "message": "Forced into degraded mode",
                        "status": manager.get_status(),
                    }
                )

            elif action == "exit":
                manager.force_normal()
                return JsonResponse(
                    {
                        "message": "Forced exit from degraded mode",
                        "status": manager.get_status(),
                    }
                )

            else:
                return JsonResponse(
                    {"error": f"Unknown action: {action}. Use 'enter' or 'exit'"},
                    status=400,
                )

        except Exception as e:
            logger.error(
                "degraded_mode_force_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


@method_decorator(csrf_exempt, name="dispatch")
class MetricsResetView(View):
    """Reset metrics (for testing)."""

    def post(self, request: HttpRequest) -> JsonResponse:
        """Reset all metrics."""
        try:
            from selfhealing.audit import get_audit_metrics

            metrics = get_audit_metrics()
            metrics.reset()

            return JsonResponse(
                {
                    "message": "Metrics reset successfully",
                }
            )

        except Exception as e:
            logger.error(
                "metrics_reset_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


# URL patterns helper
def get_audit_resilience_urls():
    """
    Get URL patterns for audit resilience API.

    Usage in urls.py:
        from selfhealing.audit.api import get_audit_resilience_urls

        urlpatterns = [
            ...
            path('api/v1/audit/', include(get_audit_resilience_urls())),
        ]
    """
    from django.urls import path

    return [
        path("health", AuditHealthView.as_view(), name="audit-health"),
        path("metrics", AuditMetricsView.as_view(), name="audit-metrics"),
        path("metrics/reset", MetricsResetView.as_view(), name="audit-metrics-reset"),
        path(
            "circuit-breakers",
            CircuitBreakerStatusView.as_view(),
            name="circuit-breakers-list",
        ),
        path(
            "circuit-breakers/<str:name>",
            CircuitBreakerStatusView.as_view(),
            name="circuit-breaker-detail",
        ),
        path(
            "circuit-breakers/<str:name>/reset",
            CircuitBreakerResetView.as_view(),
            name="circuit-breaker-reset",
        ),
        path(
            "circuit-breakers/<str:name>/force-open",
            CircuitBreakerForceOpenView.as_view(),
            name="circuit-breaker-force-open",
        ),
        path(
            "circuit-breakers/reset-all",
            CircuitBreakerResetAllView.as_view(),
            name="circuit-breakers-reset-all",
        ),
        path(
            "degraded-mode",
            DegradedModeStatusView.as_view(),
            name="degraded-mode-status",
        ),
        path(
            "degraded-mode/<str:action>",
            DegradedModeForceView.as_view(),
            name="degraded-mode-action",
        ),
    ]
