"""
Self-Healing Control API Views

REST API endpoints for the Self-Healing Control API.
Based on: docs/self_healing/5_CONTROL_API/

Endpoints:
- POST /api/self-healing/control/ - Execute control action
- GET  /api/self-healing/status/ - Get all service states
- GET  /api/self-healing/status/{service_name}/ - Get specific service state
- GET  /api/self-healing/audit/ - Get audit logs
"""

import logging
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from shopping.serializers.self_healing_serializers import (
    ControlRequestSerializer,
    ControlResponseSerializer,
    ControlErrorResponseSerializer,
    ControlStatusResponseSerializer,
    ServiceStateSerializer,
    AuditLogListResponseSerializer,
    ControlAPIActions,
    ControlAPIEnvironments,
)
from selfhealing.services import (
    ControlAPIService,
    ControlRequest,
    get_control_api_service,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Control API Views
# =============================================================================


@extend_schema(tags=["Self-Healing"])
class ControlActionView(APIView):
    """
    Execute Self-Healing Control Actions.

    This endpoint provides a unified, auditable, reversible, and governed
    control surface to manage reliability behaviors.

    **Actions:**
    - `allow`: Enable service operations (CB → CLOSED)
    - `block`: Disable service operations (CB → OPEN)
    - `override`: Temporarily bypass rules (requires TTL in ops)
    - `reset`: Revert to default configuration
    - `inject_failure`: Simulate failures (test/chaos only)

    **Environments:**
    - `test`: CI/CD validation (minimal restrictions)
    - `chaos`: Resilience testing (TTL recommended)
    - `ops`: Production control (strict governance)

    **Governance:**
    - `inject_failure` is FORBIDDEN in ops
    - `override` in ops requires TTL (max 60 min)
    - All actions are audited
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Execute Control Action",
        description="""
Execute a self-healing control action on a service.

**Actions:**
| Action | Purpose | Ops Allowed |
|--------|---------|-------------|
| `allow` | Enable service operations | ✅ |
| `block` | Block service operations | ✅ |
| `override` | Temporarily bypass rules | ✅ (TTL required) |
| `reset` | Revert to defaults | ✅ |
| `inject_failure` | Simulate failures | ❌ Forbidden |

**Example Request:**
```json
{
  "service_name": "payment",
  "action": "allow",
  "environment": "ops",
  "reason": "PG recovered after maintenance"
}
```
        """,
        request=ControlRequestSerializer,
        responses={
            200: ControlResponseSerializer,
            400: ControlErrorResponseSerializer,
            403: ControlErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                "Allow Payment Service",
                value={
                    "service_name": "payment",
                    "action": "allow",
                    "environment": "ops",
                    "reason": "PG recovered after maintenance",
                },
            ),
            OpenApiExample(
                "Block with TTL",
                value={
                    "service_name": "payment",
                    "action": "block",
                    "environment": "ops",
                    "reason": "External API maintenance window",
                    "ttl_minutes": 60,
                },
            ),
            OpenApiExample(
                "Override for SLA Breach",
                value={
                    "service_name": "payment",
                    "action": "override",
                    "environment": "ops",
                    "reason": "SLA breach mitigation - latency exceeded 2s",
                    "ttl_minutes": 45,
                },
            ),
            OpenApiExample(
                "Inject Failure (Chaos)",
                value={
                    "service_name": "payment",
                    "action": "inject_failure",
                    "environment": "chaos",
                    "reason": "Resilience testing - payment timeout",
                    "ttl_minutes": 10,
                    "metadata": {"failure_rate": 0.5, "failure_type": "timeout"},
                },
            ),
        ],
    )
    def post(self, request):
        """Execute a control action."""
        serializer = ControlRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "status": "rejected",
                    "error_code": "VALIDATION_ERROR",
                    "error_message": serializer.errors,
                    "action_requested": request.data.get("action", "unknown"),
                    "environment": request.data.get("environment", "unknown"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build internal request
        control_request = ControlRequest(
            service_name=serializer.validated_data["service_name"],
            action=serializer.validated_data["action"],
            reason=serializer.validated_data["reason"],
            environment=serializer.validated_data["environment"],
            ttl_minutes=serializer.validated_data.get("ttl_minutes"),
            request_id=str(serializer.validated_data.get("request_id", "")),
            metadata=serializer.validated_data.get("metadata", {}),
            actor=request.user.username if request.user else "anonymous",
            actor_role="admin" if request.user and request.user.is_staff else "user",
        )

        # Execute
        service = get_control_api_service()
        response = service.execute(control_request)

        # Return response
        if response.status == "rejected":
            return Response(response.to_dict(), status=status.HTTP_403_FORBIDDEN)
        elif response.status == "error":
            return Response(response.to_dict(), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            return Response(response.to_dict(), status=status.HTTP_200_OK)


@extend_schema(tags=["Self-Healing"])
class ControlStatusView(APIView):
    """
    Get Self-Healing Service Status.

    Returns the current state of all services or a specific service.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get All Service States",
        description="""
Get the current status of all services in the self-healing system.

**Response includes:**
- Service name and current state (allow/block/half_open)
- Failure and success counts
- Manual control status and reason
- TTL expiration time if applicable
        """,
        parameters=[
            OpenApiParameter(
                name="environment",
                description="Environment context",
                required=False,
                type=str,
                enum=["test", "chaos", "ops"],
                default="ops",
            )
        ],
        responses={200: ControlStatusResponseSerializer},
    )
    def get(self, request):
        """Get status of all services."""
        environment = request.query_params.get("environment", "ops")

        service = get_control_api_service()
        status_data = service.get_status(environment=environment)

        return Response(status_data)


@extend_schema(tags=["Self-Healing"])
class ServiceStatusView(APIView):
    """
    Get Specific Service Status.

    Returns the current state of a specific service.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Service State",
        description="Get the current status of a specific service.",
        responses={200: ServiceStateSerializer},
    )
    def get(self, request, service_name: str):
        """Get status of a specific service."""
        service = get_control_api_service()
        status_data = service.get_service_status(service_name)

        return Response(status_data)


@extend_schema(tags=["Self-Healing"])
class ControlAuditView(APIView):
    """
    Get Self-Healing Audit Logs.

    Returns audit logs for control API actions.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Get Audit Logs",
        description="""
Get audit logs for self-healing control actions.

**Filters:**
- `service_name`: Filter by service
- `action`: Filter by action type
- `environment`: Filter by environment
- `page`: Page number (default: 1)
- `page_size`: Items per page (default: 50)
        """,
        parameters=[
            OpenApiParameter(name="service_name", type=str, required=False),
            OpenApiParameter(name="action", type=str, required=False),
            OpenApiParameter(name="environment", type=str, required=False),
            OpenApiParameter(name="page", type=int, required=False, default=1),
            OpenApiParameter(name="page_size", type=int, required=False, default=50),
        ],
        responses={200: AuditLogListResponseSerializer},
    )
    def get(self, request):
        """Get audit logs."""
        # TODO: Implement actual audit log retrieval from database
        # For now, return empty response
        return Response(
            {
                "logs": [],
                "total_count": 0,
                "page": int(request.query_params.get("page", 1)),
                "page_size": int(request.query_params.get("page_size", 50)),
            }
        )


# =============================================================================
# Quick Action Views
# =============================================================================


@extend_schema(tags=["Self-Healing"])
class QuickAllowView(APIView):
    """
    Quick Allow Action.

    Shortcut endpoint to quickly enable a service.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Quick Allow Service",
        description="Quickly enable service operations (shortcut for allow action).",
        request=None,
        responses={200: ControlResponseSerializer},
    )
    def post(self, request, service_name: str):
        """Quick allow a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.ALLOW,
            reason=request.data.get("reason", "Quick allow via API"),
            environment=request.data.get("environment", "ops"),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


@extend_schema(tags=["Self-Healing"])
class QuickBlockView(APIView):
    """
    Quick Block Action.

    Shortcut endpoint to quickly block a service.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Quick Block Service",
        description="Quickly block service operations (shortcut for block action).",
        request=None,
        responses={200: ControlResponseSerializer},
    )
    def post(self, request, service_name: str):
        """Quick block a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.BLOCK,
            reason=request.data.get("reason", "Quick block via API"),
            environment=request.data.get("environment", "ops"),
            ttl_minutes=request.data.get("ttl_minutes", 90),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


@extend_schema(tags=["Self-Healing"])
class QuickResetView(APIView):
    """
    Quick Reset Action.

    Shortcut endpoint to quickly reset a service to defaults.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Quick Reset Service",
        description="Quickly reset service to default configuration.",
        request=None,
        responses={200: ControlResponseSerializer},
    )
    def post(self, request, service_name: str):
        """Quick reset a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.RESET,
            reason=request.data.get("reason", "Quick reset via API"),
            environment=request.data.get("environment", "ops"),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


# =============================================================================
# Health Check View
# =============================================================================


@extend_schema(tags=["Self-Healing"])
class SelfHealingHealthView(APIView):
    """
    Self-Healing System Health Check.

    Returns the health status of the self-healing system itself.
    """

    permission_classes = []  # Public endpoint

    @extend_schema(
        summary="Self-Healing Health Check",
        description="Check the health of the self-healing system.",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "circuit_breaker_enabled": {"type": "boolean"},
                    "services_count": {"type": "integer"},
                    "timestamp": {"type": "string"},
                },
            }
        },
    )
    def get(self, request):
        """Get self-healing system health."""
        from django.utils import timezone

        service = get_control_api_service()

        try:
            status_data = service.get_status()
            services_count = len(status_data.get("services", []))
            cb_enabled = service.circuit_breaker.is_enabled
            health_status = "healthy"
        except Exception as e:
            logger.error(f"[SelfHealing] Health check failed: {e}")
            services_count = 0
            cb_enabled = False
            health_status = "degraded"

        return Response(
            {
                "status": health_status,
                "circuit_breaker_enabled": cb_enabled,
                "services_count": services_count,
                "timestamp": timezone.now().isoformat(),
            }
        )


# =============================================================================
# Metrics View (Trend Analysis)
# =============================================================================


@extend_schema(tags=["Self-Healing"])
class SelfHealingMetricsView(APIView):
    """
    Self-Healing Metrics for Trend Analysis.

    Unlike status (point-in-time snapshot), metrics provide trend data
    for dashboards, AI agents, and monitoring integration.

    **Consumers:**
    - Admin UI: Dashboard visualization with trend charts
    - AI Agent: Automated decision making based on trends
    - Celery: Periodic health assessments
    - Prometheus/Grafana: Metrics scraping and alerting
    - External Monitoring: Third-party integration

    **Use Cases:**
    - Identify degradation trends before outages
    - Track recovery effectiveness over time
    - Monitor automation success rates
    - Feed ML models for predictive healing
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Self-Healing Metrics",
        description="""
Retrieve comprehensive self-healing metrics for trend analysis.

**Metrics Included:**
| Category | Metrics |
|----------|---------|
| Aggregate | total_services, healthy_services, degraded_services |
| Trends | last_5m_failure_rate, last_5m_request_count |
| Recovery | avg_time_to_recovery |
| Automation | auto_allowed_count_24h, auto_blocked_count_24h |
| DLQ | total_dlq_pending, dlq_by_service |
| Per-Service | failure_rate_5m, retry_success_rate, circuit_state |

**Consumer Roles:**
- **Admin UI**: Dashboard visualization
- **AI Agent**: Automated decision support
- **Celery Tasks**: Scheduled health checks
- **External Systems**: Monitoring integration
""",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "total_services": {"type": "integer"},
                    "healthy_services": {"type": "integer"},
                    "degraded_services": {"type": "integer"},
                    "last_5m_failure_rate": {"type": "number"},
                    "last_5m_request_count": {"type": "integer"},
                    "avg_time_to_recovery": {"type": "number", "nullable": True},
                    "auto_allowed_count_24h": {"type": "integer"},
                    "auto_blocked_count_24h": {"type": "integer"},
                    "total_dlq_pending": {"type": "integer"},
                    "dlq_by_service": {"type": "object"},
                    "services": {"type": "array"},
                    "timestamp": {"type": "string"},
                    "collection_duration_ms": {"type": "integer"},
                },
            }
        },
    )
    def get(self, request):
        """
        Get comprehensive self-healing metrics.

        Returns trend data for monitoring and analysis.
        """
        service = get_control_api_service()

        try:
            metrics = service.get_metrics()
            return Response(metrics, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"[SelfHealing] Metrics collection failed: {e}")
            return Response(
                {"error": "Failed to collect metrics", "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# =============================================================================
# DLQ Replay View
# =============================================================================


@extend_schema(tags=["Self-Healing"])
class DLQReplayView(APIView):
    """
    DLQ Replay API.

    Trigger replay of failed operations from the Dead Letter Queue.
    This provides REST API access to the same functionality available in Django Admin.

    **Use Cases:**
    - Automated recovery after service restoration
    - CI/CD pipeline integration for testing
    - External monitoring system triggers
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        summary="Replay DLQ Items",
        description="""
Trigger replay of failed operations from the Dead Letter Queue.

**Parameters:**
- `domain`: Filter by domain (payment, point, inventory, etc.)
- `service_name`: Filter by service name
- `batch_size`: Maximum items to replay (default: 50, max: 200)
- `status`: DLQ item status to replay (default: pending)

**Response:**
- Total items processed
- Success/failure counts
- Individual replay results
""",
        request={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain to replay (payment, point, inventory)"},
                "service_name": {"type": "string", "description": "Service name filter"},
                "batch_size": {"type": "integer", "default": 50, "maximum": 200},
                "status": {"type": "string", "default": "pending"},
            },
        },
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "total": {"type": "integer"},
                    "success_count": {"type": "integer"},
                    "failed_count": {"type": "integer"},
                    "skipped_count": {"type": "integer"},
                },
            },
            400: {"type": "object", "properties": {"error": {"type": "string"}}},
        },
    )
    def post(self, request):
        """Trigger DLQ replay."""
        domain = request.data.get("domain")
        service_name = request.data.get("service_name")
        batch_size = min(int(request.data.get("batch_size", 50)), 200)
        item_status = request.data.get("status", "pending")

        try:
            from selfhealing.services import ReplayService

            replay_service = ReplayService()

            # Build filter
            filters = {"status": item_status}
            if domain:
                filters["domain"] = domain
            if service_name:
                filters["service_name"] = service_name

            # Execute batch replay
            result = replay_service.replay_batch(
                filters=filters,
                batch_size=batch_size,
                triggered_by=request.user.username if request.user else "api",
            )

            logger.info(
                f"[DLQ] Replay triggered via API: domain={domain}, service={service_name}, "
                f"batch_size={batch_size}, success={result.success_count}, failed={result.failed_count}"
            )

            return Response(
                {
                    "status": "success",
                    "total": result.total,
                    "success_count": result.success_count,
                    "failed_count": result.failed_count,
                    "skipped_count": result.skipped_count,
                }
            )

        except Exception as e:
            logger.error(f"[DLQ] Replay failed: {e}")
            return Response({"status": "error", "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
