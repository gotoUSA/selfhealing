"""
Self-Healing Control API Views - Circuit Breaker Control.

REST API endpoints for Circuit Breaker control operations.

Endpoints:
- POST /api/self-healing/control/ - Execute control action
- GET  /api/self-healing/status/ - Get all service states
- GET  /api/self-healing/status/{service_name}/ - Get specific service state
- GET  /api/self-healing/audit/ - Get audit logs
- POST /api/self-healing/allow/{service_name}/ - Quick allow
- POST /api/self-healing/block/{service_name}/ - Quick block
- POST /api/self-healing/reset/{service_name}/ - Quick reset

Note:
- 비즈니스 로직은 ControlAPIService(services/control_api_service.py)로 분리됨
- View는 Request/Response 처리만 담당
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers import (
    ControlRequestSerializer,
)
from selfhealing.core.constants import ControlAPIActions

# Import from services layer
from selfhealing.services.control_api_service import (
    ControlAPIService,
    ControlRequest,
    ControlResponse,
    get_control_api_service,
)

logger = logging.getLogger(__name__)


# Re-export for backward compatibility
__all__ = [
    # Classes
    "ControlAPIService",
    "ControlRequest",
    "ControlResponse",
    # Functions
    "get_control_api_service",
    # Views
    "ControlActionView",
    "ControlStatusView",
    "ServiceStatusView",
    "ControlAuditView",
    "QuickAllowView",
    "QuickBlockView",
    "QuickResetView",
]


# =============================================================================
# Control API Views
# =============================================================================


class ControlActionView(APIView):
    """
    Execute Self-Healing Control Actions.

    POST /api/self-healing/control/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

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


class ControlStatusView(APIView):
    """
    Get Self-Healing Service Status.

    GET /api/self-healing/status/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get status of all services."""
        environment = request.query_params.get("environment", "ops")

        service = get_control_api_service()
        status_data = service.get_status(environment=environment)

        return Response(status_data)


class ServiceStatusView(APIView):
    """
    Get Specific Service Status.

    GET /api/self-healing/status/{service_name}/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, service_name: str):
        """Get status of a specific service."""
        service = get_control_api_service()
        status_data = service.get_service_status(service_name)

        return Response(status_data)


class ControlAuditView(APIView):
    """
    Get Self-Healing Audit Logs.

    GET /api/self-healing/audit/
    
    Note: Read-only endpoint - all authenticated users can view.
    Audit logs are immutable and cannot be modified via any API.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get audit logs from AuditLogger."""
        from datetime import datetime, timedelta
        from selfhealing.audit import get_audit_logger
        
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 50))
        config_type = request.query_params.get("config_type")
        user = request.query_params.get("user")
        days = int(request.query_params.get("days", 7))
        
        try:
            audit_logger = get_audit_logger()
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)
            
            # Query logs with filters
            all_logs = audit_logger.query(
                start_time=start_time,
                end_time=end_time,
                config_type=config_type,
                user=user,
                limit=page * page_size + page_size,  # Fetch enough for pagination
            )
            
            # Paginate results
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_logs = all_logs[start_idx:end_idx]
            
            return Response({
                "logs": paginated_logs,
                "total_count": len(all_logs),
                "page": page,
                "page_size": page_size,
                "filters": {
                    "config_type": config_type,
                    "user": user,
                    "days": days,
                },
            })
        except Exception as e:
            logger.warning(f"[AuditLogsView] Error retrieving audit logs: {e}")
            return Response(
                {
                    "logs": [],
                    "total_count": 0,
                    "page": page,
                    "page_size": page_size,
                    "error": "Audit log retrieval temporarily unavailable",
                }
            )


# =============================================================================
# Quick Action Views
# =============================================================================


class QuickAllowView(APIView):
    """
    Quick Allow Action.

    POST /api/self-healing/allow/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

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


class QuickBlockView(APIView):
    """
    Quick Block Action.

    POST /api/self-healing/block/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

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


class QuickResetView(APIView):
    """
    Quick Reset Action.

    POST /api/self-healing/reset/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

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
