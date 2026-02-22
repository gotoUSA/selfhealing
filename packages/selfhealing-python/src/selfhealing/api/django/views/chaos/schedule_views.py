"""
Chaos Engineering Schedule Views.

API views for scheduled experiment management.
"""

import structlog
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.serializers.chaos import (
    ApprovalActionSerializer,
    ScheduledExperimentSerializer,
)

logger = structlog.get_logger()


class ScheduleListView(APIView):
    """
    API for listing and creating scheduled experiments.

    GET: List schedules (Viewer)
    POST: Create new schedule (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """List scheduled experiments."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        scheduler = get_chaos_scheduler()

        # Query params
        enabled_only = (
            request.query_params.get("enabled_only", "false").lower() == "true"
        )
        pending_only = (
            request.query_params.get("pending_approval_only", "false").lower() == "true"
        )
        target_service = request.query_params.get("target_service")

        schedules = scheduler.list_schedules(
            enabled_only=enabled_only,
            pending_approval_only=pending_only,
            target_service=target_service,
        )

        return Response(
            {
                "status": "success",
                "data": [s.to_dict() for s in schedules],
                "count": len(schedules),
            }
        )

    def post(self, request: Request) -> Response:
        """Create a new scheduled experiment."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        serializer = ScheduledExperimentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scheduler = get_chaos_scheduler()

        # Extract experiment_config if present
        data = serializer.validated_data
        experiment_config = data.pop("experiment_config", {})

        schedule = scheduler.create_schedule(
            created_by=str(request.user),
            experiment_config=experiment_config,
            **data,
        )

        logger.info(
            "chaos_api.schedule_created",
            schedule=schedule.id,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "data": schedule.to_dict(),
            },
            status=status.HTTP_201_CREATED,
        )


class ScheduleDetailView(APIView):
    """
    API for individual schedule operations.

    GET: Retrieve schedule (Viewer)
    PATCH: Update schedule (Admin)
    DELETE: Delete schedule (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request: Request, schedule_id: str) -> Response:
        """Get schedule details."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        scheduler = get_chaos_scheduler()
        schedule = scheduler.get_schedule(schedule_id)

        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "data": schedule.to_dict(),
            }
        )

    def patch(self, request: Request, schedule_id: str) -> Response:
        """Update schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        scheduler = get_chaos_scheduler()
        schedule = scheduler.update_schedule(schedule_id, **request.data)

        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "chaos_api.schedule_updated",
            schedule_id=schedule_id,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "data": schedule.to_dict(),
            }
        )

    def delete(self, request: Request, schedule_id: str) -> Response:
        """Delete schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        scheduler = get_chaos_scheduler()
        deleted = scheduler.delete_schedule(schedule_id)

        if not deleted:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "chaos_api.schedule_deleted",
            schedule_id=schedule_id,
            request=request.user,
        )

        return Response(
            {"status": "success", "message": "Schedule deleted"},
            status=status.HTTP_204_NO_CONTENT,
        )


class ScheduleApprovalView(APIView):
    """
    API for approving/denying scheduled experiments.

    POST: Approve or deny a schedule (Admin only)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, schedule_id: str) -> Response:
        """Approve or deny a schedule."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        serializer = ApprovalActionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scheduler = get_chaos_scheduler()
        action = serializer.validated_data["action"]
        reason = serializer.validated_data.get("reason", "")

        if action == "approve":
            schedule = scheduler.approve_schedule(
                schedule_id, approved_by=str(request.user)
            )
        else:
            schedule = scheduler.deny_schedule(
                schedule_id, denied_by=str(request.user), reason=reason
            )

        if not schedule:
            return Response(
                {"status": "error", "message": "Schedule not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            "chaos_api.schedule",
            action=action,
            schedule_id=schedule_id,
            request=request.user,
        )

        return Response(
            {
                "status": "success",
                "data": schedule.to_dict(),
            }
        )


class ScheduleExecuteView(APIView):
    """
    API for executing a schedule immediately.

    POST: Execute schedule now (Admin only - dangerous operation)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, schedule_id: str) -> Response:
        """Execute schedule immediately."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        scheduler = get_chaos_scheduler()
        force = request.data.get("force", False)

        result = scheduler.execute_now(schedule_id, force=force)

        logger.info(
            "chaos_api.schedule_executed",
            schedule_id=schedule_id,
            request=request.user,
            result=result.status,
        )

        return Response(
            {
                "status": "success",
                "data": result.to_dict(),
            }
        )


class PendingApprovalsView(APIView):
    """
    API for pending approvals.

    GET: List pending approvals (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        """List pending approvals."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler

        manager = get_blast_radius_manager()
        scheduler = get_chaos_scheduler()

        # Get pending from both sources
        blast_radius_pending = manager.get_pending_approvals()
        schedule_pending = scheduler.list_schedules(pending_approval_only=True)

        return Response(
            {
                "status": "success",
                "data": {
                    "blast_radius_approvals": [
                        a.to_dict() for a in blast_radius_pending
                    ],
                    "schedule_approvals": [s.to_dict() for s in schedule_pending],
                },
                "total_pending": len(blast_radius_pending) + len(schedule_pending),
            }
        )
