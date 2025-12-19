"""
Self-Healing Health & Metrics Views.

REST API endpoints for health checks and metrics.

Endpoints:
- GET  /api/self-healing/health/ - Health check
- GET  /api/self-healing/health/live/ - Kubernetes liveness probe
- GET  /api/self-healing/health/ready/ - Kubernetes readiness probe
- GET  /api/self-healing/health/pool/ - Connection pool health
- GET  /api/self-healing/health/ping/ - Simple ping
- GET  /api/self-healing/metrics/ - Get metrics
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def _get_circuit_breaker_model():
    """Lazy import CircuitBreakerState model."""
    from selfhealing.adapters.django.models import CircuitBreakerState

    return CircuitBreakerState


class SelfHealingHealthView(APIView):
    """
    Self-Healing System Health Check.

    GET /api/self-healing/health/
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Get self-healing system health."""
        try:
            from django.db import connection

            # DB 연결 확인
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()

            CircuitBreakerState = _get_circuit_breaker_model()
            services_count = CircuitBreakerState.objects.count()
            health_status = "healthy"
            db_status = "healthy"
        except Exception as e:
            logger.error(f"[SelfHealing] Health check failed: {e}")
            services_count = 0
            health_status = "degraded"
            db_status = "unhealthy"

        return Response(
            {
                "status": health_status,
                "checks": {
                    "database": db_status,
                    "circuit_breaker": "enabled",
                },
                "services_count": services_count,
                "timestamp": timezone.now().isoformat(),
            }
        )


class LivenessView(APIView):
    """
    Kubernetes-style Liveness Probe.

    GET /api/self-healing/health/live/

    Returns 200 if the application is running.
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Return liveness status."""
        return Response({"status": "alive"})


class ReadinessView(APIView):
    """
    Kubernetes-style Readiness Probe.

    GET /api/self-healing/health/ready/

    Returns 200 if the application is ready to serve traffic.
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Check if application is ready to serve traffic."""
        from django.db import connections

        ready = True
        checks = {}

        # Database readiness
        for alias in connections:
            try:
                conn = connections[alias]
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                checks[f"database_{alias}"] = "ready"
            except Exception as e:
                logger.error(f"Database {alias} readiness check failed: {e}")
                checks[f"database_{alias}"] = "not_ready"
                ready = False

        response_data = {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        }

        if not ready:
            return Response(response_data, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(response_data)


class ConnectionPoolHealthView(APIView):
    """
    Connection Pool Health Status.

    GET /api/self-healing/health/pool/

    Returns connection pool statistics if available.
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Get connection pool health status."""
        from django.db import connections

        pool_data = {"status": "healthy", "pool_info": {}}

        try:
            conn = connections["default"]

            pool_data["pool_info"] = {
                "alias": "default",
                "vendor": conn.vendor,
                "is_usable": conn.is_usable(),
            }

            if not conn.is_usable():
                pool_data["status"] = "degraded"
                return Response(pool_data, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        except Exception as e:
            logger.error(f"Connection pool health check failed: {e}")
            pool_data["status"] = "error"
            pool_data["error"] = str(e)
            return Response(pool_data, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(pool_data)


def simple_health_ping(request):
    """
    Minimal health check with lowest overhead.

    GET /api/self-healing/health/ping/

    Just returns 'pong' - useful for load balancer checks.
    """
    from django.http import JsonResponse

    return JsonResponse({"ping": "pong"})


class SelfHealingMetricsView(APIView):
    """
    Self-Healing Metrics for Trend Analysis.

    GET /api/self-healing/metrics/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive self-healing metrics."""
        from selfhealing.api.django.views.circuit_breaker import get_control_api_service

        service = get_control_api_service()

        try:
            metrics = service.get_metrics()
            return Response(metrics, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"[SelfHealing] Metrics collection failed: {e}")
            return Response(
                {"error": "Failed to collect metrics", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
