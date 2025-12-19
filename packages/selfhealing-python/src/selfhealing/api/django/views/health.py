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

Note:
- 비즈니스 로직은 HealthCheckService로 분리됨
- View는 Request/Response 처리만 담당
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.health_check import get_health_check_service

logger = logging.getLogger(__name__)


class SelfHealingHealthView(APIView):
    """
    Self-Healing System Health Check.

    GET /api/self-healing/health/
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Get self-healing system health."""
        service = get_health_check_service()
        health = service.get_overall_health()
        return Response(health.to_dict())


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
        service = get_health_check_service()
        readiness = service.get_readiness()
        
        response_data = readiness.to_dict()
        del response_data["is_ready"]  # Remove internal field from response

        if not readiness.is_ready:
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
        service = get_health_check_service()
        pool_health = service.get_pool_health()
        
        response_data = pool_health.to_dict()
        if response_data.get("error") is None:
            del response_data["error"]  # Remove None error field

        if pool_health.status in ("degraded", "error"):
            return Response(response_data, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response(response_data)


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
