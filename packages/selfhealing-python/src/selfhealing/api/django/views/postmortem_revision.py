"""
Postmortem Revision API Views.

Postmortem 리비전(버전) 관리를 위한 REST API 엔드포인트.

Endpoints:
- GET  /postmortem/{id}/revisions/         - 리비전 목록 조회
- POST /postmortem/{id}/revisions/         - 새 리비전 생성
- GET  /postmortem/{id}/revisions/{num}/   - 특정 리비전 조회
- GET  /postmortem/{id}/revisions/compare/ - 리비전 비교
- POST /postmortem/{id}/seal/              - Postmortem 봉인

RBAC Permissions:
- 리비전 조회: IsViewer
- 리비전 생성: IsOperator
- 봉인/봉인해제: IsSelfHealingAdmin
"""

from __future__ import annotations

import structlog
from typing import Any

from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import (
    IsOperator,
    IsSelfHealingAdmin,
    IsViewer,
)
from selfhealing.services.postmortem.revision import (
    PostmortemRevisionManager,
    RevisionChangeType,
    get_postmortem_revision_manager,
)

logger = structlog.get_logger()


def _write_revision_audit_log(
    event_type: str,
    incident_id: str,
    actor_id: str,
    details: dict[str, Any],
) -> None:
    """
    리비전 관련 감사 로그 기록.

    Args:
        event_type: 이벤트 유형 (REVISION_CREATED, POSTMORTEM_SEALED 등)
        incident_id: Postmortem ID
        actor_id: 수행자 ID
        details: 상세 정보
    """
    try:
        from selfhealing.services.audit.base import _write_to_wal

        _write_to_wal(
            event_type=event_type,
            source="API.PostmortemRevision",
            details=details,
            success=True,
            domain="selfhealing",
            target_id=incident_id,
        )
    except Exception as e:
        logger.warning(
            "postmortem_revision.failed_write_audit_log",
            error=e,
        )


class PostmortemRevisionListView(APIView):
    """
    Postmortem 리비전 목록 조회 및 생성 API.

    GET  /postmortem/{incident_id}/revisions/
    POST /postmortem/{incident_id}/revisions/
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]

    def get_permissions(self):
        """HTTP 메서드에 따른 권한 클래스 반환."""
        if self.request.method == "POST":
            return [IsOperator()]
        return [IsViewer()]

    def get(self, request: Request, incident_id: str) -> Response:
        """
        리비전 목록 조회.

        Returns:
            - revisions: 리비전 목록
            - total_count: 전체 리비전 수
            - is_sealed: 봉인 상태
            - latest_revision: 최신 리비전 번호
        """
        manager = get_postmortem_revision_manager()
        summary = manager.get_revision_summary(incident_id)

        return Response(
            {
                "status": "success",
                "revisions": summary["revisions"],
                "total_count": summary["total_count"],
                "is_sealed": summary["is_sealed"],
                "latest_revision": summary["latest_revision"],
                "timestamp": timezone.now().isoformat(),
            }
        )

    def post(self, request: Request, incident_id: str) -> Response:
        """
        새 리비전 생성.

        Request Body:
            - data: 수정된 Postmortem 데이터
            - change_reason: 변경 사유
            - change_type: 변경 유형 (optional, default: analysis_update)
        """
        data = request.data.get("data")
        change_reason = request.data.get("change_reason")
        change_type_str = request.data.get("change_type", "analysis_update")

        if not data:
            return Response(
                {"status": "error", "error": "missing_data", "message": "data field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not change_reason:
            return Response(
                {"status": "error", "error": "missing_change_reason", "message": "change_reason field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            change_type = RevisionChangeType(change_type_str)
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "error": "invalid_change_type",
                    "message": f"Invalid change_type: {change_type_str}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor_id = str(request.user) if request.user.is_authenticated else "anonymous"

        manager = get_postmortem_revision_manager()

        try:
            revision = manager.create_revision(
                incident_id=incident_id,
                new_data=data,
                changed_by=actor_id,
                change_reason=change_reason,
                change_type=change_type,
            )
        except ValueError as e:
            return Response(
                {"status": "error", "error": "revision_creation_failed", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _write_revision_audit_log(
            event_type="POSTMORTEM_REVISION_CREATED",
            incident_id=incident_id,
            actor_id=actor_id,
            details={
                "revision_id": revision.revision_id,
                "revision_number": revision.revision_number,
                "change_type": revision.change_type.value,
                "change_reason": change_reason,
                "actor_id": actor_id,
            },
        )

        return Response(
            {
                "status": "success",
                "revision": revision.to_dict(),
                "timestamp": timezone.now().isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class PostmortemRevisionDetailView(APIView):
    """
    특정 리비전 상세 조회 API.

    GET /postmortem/{incident_id}/revisions/{revision_number}/
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsViewer]

    def get(self, request: Request, incident_id: str, revision_number: int) -> Response:
        """특정 리비전 조회."""
        manager = get_postmortem_revision_manager()
        revision = manager.get_revision(incident_id, revision_number)

        if revision is None:
            return Response(
                {
                    "status": "error",
                    "error": "revision_not_found",
                    "message": f"Revision {revision_number} not found for {incident_id}",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "revision": revision.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class PostmortemRevisionCompareView(APIView):
    """
    두 리비전 비교 API.

    GET /postmortem/{incident_id}/revisions/compare/?a=1&b=2
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsViewer]

    def get(self, request: Request, incident_id: str) -> Response:
        """
        두 리비전 비교.

        Query Parameters:
            - a: 비교 리비전 A (이전 버전)
            - b: 비교 리비전 B (새 버전)
        """
        revision_a_str = request.query_params.get("a")
        revision_b_str = request.query_params.get("b")

        if not revision_a_str or not revision_b_str:
            return Response(
                {
                    "status": "error",
                    "error": "missing_parameters",
                    "message": "Both 'a' and 'b' query parameters are required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            revision_a = int(revision_a_str)
            revision_b = int(revision_b_str)
        except ValueError:
            return Response(
                {
                    "status": "error",
                    "error": "invalid_parameters",
                    "message": "Parameters 'a' and 'b' must be integers",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        manager = get_postmortem_revision_manager()

        try:
            diff = manager.compare_revisions(incident_id, revision_a, revision_b)
        except ValueError as e:
            return Response(
                {"status": "error", "error": "comparison_failed", "message": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "comparison": {
                    "revision_a": revision_a,
                    "revision_b": revision_b,
                    "added": diff.added,
                    "removed": diff.removed,
                    "modified": diff.modified,
                    "unchanged": diff.unchanged,
                    "has_changes": diff.has_changes,
                },
                "timestamp": timezone.now().isoformat(),
            }
        )


class PostmortemSealView(APIView):
    """
    Postmortem 봉인/봉인해제 API.

    POST   /postmortem/{incident_id}/seal/   - 봉인
    DELETE /postmortem/{incident_id}/seal/   - 봉인 해제 (Admin 전용)
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request, incident_id: str) -> Response:
        """
        Postmortem 봉인.

        Request Body:
            - seal_reason: 봉인 사유 (optional)
        """
        seal_reason = request.data.get("seal_reason", "Analysis completed")
        actor_id = str(request.user) if request.user.is_authenticated else "anonymous"

        manager = get_postmortem_revision_manager()

        try:
            seal_revision = manager.seal_postmortem(
                incident_id=incident_id,
                sealed_by=actor_id,
                seal_reason=seal_reason,
            )
        except ValueError as e:
            return Response(
                {"status": "error", "error": "seal_failed", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _write_revision_audit_log(
            event_type="POSTMORTEM_SEALED",
            incident_id=incident_id,
            actor_id=actor_id,
            details={
                "revision_id": seal_revision.revision_id,
                "revision_number": seal_revision.revision_number,
                "seal_reason": seal_reason,
                "actor_id": actor_id,
            },
        )

        return Response(
            {
                "status": "success",
                "message": f"Postmortem '{incident_id}' sealed successfully",
                "revision": seal_revision.to_dict(),
                "timestamp": timezone.now().isoformat(),
            }
        )

    def delete(self, request: Request, incident_id: str) -> Response:
        """
        Postmortem 봉인 해제 (Admin 전용).

        Request Body:
            - unseal_reason: 봉인 해제 사유
            - approval_chain: 승인 체인 (optional)
        """
        unseal_reason = request.data.get("unseal_reason")
        approval_chain = request.data.get("approval_chain", [])

        if not unseal_reason:
            return Response(
                {
                    "status": "error",
                    "error": "missing_unseal_reason",
                    "message": "unseal_reason is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        actor_id = str(request.user) if request.user.is_authenticated else "anonymous"

        manager = get_postmortem_revision_manager()

        try:
            manager.unseal_postmortem(
                incident_id=incident_id,
                unsealed_by=actor_id,
                unseal_reason=unseal_reason,
                approval_chain=approval_chain,
            )
        except ValueError as e:
            return Response(
                {"status": "error", "error": "unseal_failed", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _write_revision_audit_log(
            event_type="POSTMORTEM_UNSEALED",
            incident_id=incident_id,
            actor_id=actor_id,
            details={
                "unseal_reason": unseal_reason,
                "approval_chain": approval_chain,
                "actor_id": actor_id,
            },
        )

        return Response(
            {
                "status": "success",
                "message": f"Postmortem '{incident_id}' unsealed successfully",
                "timestamp": timezone.now().isoformat(),
            }
        )


__all__ = [
    "PostmortemRevisionListView",
    "PostmortemRevisionDetailView",
    "PostmortemRevisionCompareView",
    "PostmortemSealView",
]
