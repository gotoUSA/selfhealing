"""
Governance Approval Views.

4-Eyes Approval 관련 View 클래스들입니다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin

logger = logging.getLogger(__name__)


class ApprovalRequestListView(APIView):
    """
    4-Eyes Approval Request List API.

    GET  /api/self-healing/governance/approval-requests/
    POST /api/self-healing/governance/approval-requests/

    4-Eyes Principle:
    - Admin A creates a request (PENDING)
    - Admin B receives notification
    - Admin B approves/rejects within 24 hours
    - Expired requests are auto-cleaned
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        """
        Get all approval requests.

        Query Parameters:
            status: Filter by status (PENDING, APPROVED, REJECTED, EXPIRED)
            for_me: Only show requests I can approve (excludes my own)
        """
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()

            # Expire old requests first
            manager.expire_old_requests()

            status_filter = request.query_params.get("status")
            for_me = request.query_params.get("for_me", "").lower() == "true"

            actor = getattr(request.user, "username", str(request.user))

            if for_me:
                requests_list = manager.get_pending_requests_for_user(actor)
            else:
                requests_list = manager.get_approval_requests(status=status_filter)

            return Response(
                {
                    "status": "success",
                    "requests": requests_list,
                    "count": len(requests_list),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval request list failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        """
        Create a new approval request.

        Request Body:
            request_type: Type (config_change, mode_change, emergency_action)
            description: Human-readable description
            payload: Request data to be approved
            expiry_hours: Hours until expiry (default 24)
        """
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))

            request_type = request.data.get("request_type", "")
            description = request.data.get("description", "")
            payload = request.data.get("payload", {})
            expiry_hours = request.data.get("expiry_hours", 24)

            if not request_type:
                return Response(
                    {"status": "error", "error": "request_type is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not description:
                return Response(
                    {"status": "error", "error": "description is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            approval_request = manager.create_approval_request(
                request_type=request_type,
                description=description,
                requested_by=actor,
                payload=payload,
                expiry_hours=expiry_hours,
            )

            logger.info(
                f"[Governance] Approval request created: {approval_request['id']} by {actor}"
            )

            return Response(
                {
                    "status": "created",
                    "request": approval_request,
                    "message": "Approval request created. Awaiting approval from another admin.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval request creation failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApprovalRequestApproveView(APIView):
    """
    4-Eyes Approval Request Approve API.

    POST /api/self-healing/governance/approval-requests/{id}/approve/

    Approves a pending request. The approver must be different from the requester.
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, request_id: str) -> Response:
        """Approve a pending request."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))

            result = manager.approve_request(request_id, actor)

            if result is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Request not found, already processed, expired, or self-approval attempted",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"[Governance] Request {request_id} approved by {actor}")

            return Response(
                {
                    "status": "approved",
                    "request": result,
                    "approved_by": actor,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Approval failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ApprovalRequestRejectView(APIView):
    """
    4-Eyes Approval Request Reject API.

    POST /api/self-healing/governance/approval-requests/{id}/reject/
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, request_id: str) -> Response:
        """Reject a pending request."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            actor = getattr(request.user, "username", str(request.user))
            reason = request.data.get("reason", "")

            result = manager.reject_request(request_id, actor, reason)

            if result is None:
                return Response(
                    {
                        "status": "error",
                        "error": "Request not found or already processed",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            logger.info(f"[Governance] Request {request_id} rejected by {actor}")

            return Response(
                {
                    "status": "rejected",
                    "request": result,
                    "rejected_by": actor,
                    "reason": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception(f"[Governance] Rejection failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


__all__ = [
    "ApprovalRequestListView",
    "ApprovalRequestApproveView",
    "ApprovalRequestRejectView",
]
