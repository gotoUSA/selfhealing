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


class ErrorBudgetGateHealthView(APIView):
    """
    Error Budget Gate Health Check.

    GET /api/self-healing/health/gate/

    Returns comprehensive health status of the Error Budget Gate including:
    - Gate status (open, blocked, fail_open, etc.)
    - Circuit breaker state
    - Rate limiter status
    - Alert status
    """

    permission_classes = []  # Public endpoint for monitoring

    def get(self, request):
        """Get Error Budget Gate health status."""
        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate

            gate = get_error_budget_gate()
            health = gate.get_health_status()

            # HTTP 상태 코드 결정
            if health.get("healthy"):
                return Response(health)
            else:
                return Response(health, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Health check failed: {e}")
            return Response(
                {
                    "healthy": False,
                    "status": "error",
                    "error": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetGateConfigView(APIView):
    """
    Error Budget Gate Configuration Management.

    GET /api/self-healing/config/gate/
    PUT /api/self-healing/config/gate/

    Allows runtime configuration of the Error Budget Gate.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get current gate configuration."""
        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate

            gate = get_error_budget_gate()
            config = gate.get_config()

            return Response({
                "status": "success",
                "config": config.to_dict(),
            })

        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Config retrieval failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request):
        """Update gate configuration."""
        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate

            gate = get_error_budget_gate()
            
            # 허용된 설정 필드
            allowed_fields = {
                "enabled",
                "critical_threshold_percent",
                "warning_threshold_percent",
                "fail_open",
                "cache_ttl_seconds",
                "fail_open_rate_limit_enabled",
                "fail_open_rate_limit_per_minute",
                "fail_open_rate_limit_window_seconds",
                "circuit_breaker_enabled",
                "circuit_breaker_failure_threshold",
                "circuit_breaker_recovery_timeout",
                "alert_on_fail_open",
                "alert_cooldown_seconds",
            }

            # 유효한 필드만 추출
            updates = {
                k: v for k, v in request.data.items()
                if k in allowed_fields
            }

            if not updates:
                return Response(
                    {"status": "error", "error": "No valid configuration fields provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            config = gate.update_config(**updates)

            return Response({
                "status": "success",
                "message": f"Updated {len(updates)} configuration field(s)",
                "updated_fields": list(updates.keys()),
                "config": config.to_dict(),
            })

        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Config update failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetGateResetView(APIView):
    """
    Error Budget Gate Component Reset.

    POST /api/self-healing/gate/reset/

    Reset specific components of the gate (for emergency recovery).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Reset gate components."""
        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate

            gate = get_error_budget_gate()
            
            component = request.data.get("component", "all")
            reset_actions = []

            if component in ("all", "cache"):
                gate.clear_cache()
                reset_actions.append("cache")

            if component in ("all", "rate_limiter"):
                gate.reset_rate_limiter()
                reset_actions.append("rate_limiter")

            if component in ("all", "circuit_breaker"):
                gate.reset_circuit_breaker()
                reset_actions.append("circuit_breaker")

            if component in ("all", "alerts"):
                gate.reset_alert_cooldowns()
                reset_actions.append("alerts")

            if not reset_actions:
                return Response(
                    {
                        "status": "error",
                        "error": f"Unknown component: {component}",
                        "valid_components": ["all", "cache", "rate_limiter", "circuit_breaker", "alerts"],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response({
                "status": "success",
                "message": f"Reset completed for: {', '.join(reset_actions)}",
                "reset_components": reset_actions,
            })

        except Exception as e:
            logger.error(f"[ErrorBudgetGate] Reset failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
