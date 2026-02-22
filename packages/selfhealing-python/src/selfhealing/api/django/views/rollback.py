"""
Rollback DNA API Views

Rollback DNA 서비스의 REST API 엔드포인트
"""

import structlog

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsOperator, IsSelfHealingAdmin, IsViewer

logger = structlog.get_logger()


def get_rollback_service():
    """Rollback 서비스 인스턴스 가져오기"""
    try:
        from selfhealing.services.rollback.service import RollbackService

        return RollbackService()
    except ImportError:
        return None


class RollbackPolicyView(APIView):
    """
    롤백 정책 관리 API

    GET  /api/self-healing/rollback/policy/<stage_name>/ - 정책 조회 (Viewer)
    POST /api/self-healing/rollback/policy/<stage_name>/ - 정책 설정 (Admin)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request, stage_name: str = None):
        """정책 조회"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if stage_name:
            policy = service.get_policy(stage_name)
            if policy:
                return Response(policy.to_dict())
            return Response(
                {"error": "Policy not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
            return Response(
                {"message": "Specify stage_name"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def post(self, request, stage_name: str):
        """정책 설정"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data if request.data else {}
        from selfhealing.services.rollback.models import RollbackStrategy

        strategy = RollbackStrategy(data.get("strategy", "automatic"))

        policy = service.set_policy(
            stage_name=stage_name,
            strategy=strategy,
            timeout_seconds=data.get("timeout_seconds", 120),
            max_retries=data.get("max_retries", 3),
            require_approval=data.get("require_approval", False),
        )
        return Response(policy.to_dict(), status=status.HTTP_201_CREATED)


class RollbackRequestView(APIView):
    """
    롤백 요청 API

    GET  /api/self-healing/rollback/request/             - 대기 중인 요청 조회 (Viewer)
    GET  /api/self-healing/rollback/request/<request_id>/ - 롤백 결과 조회 (Viewer)
    POST /api/self-healing/rollback/request/             - 롤백 요청 생성 (Operator)
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsOperator()]

    def get(self, request, request_id: str = None):
        """롤백 결과 조회"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if request_id:
            result = service.get_result(request_id)
            if result:
                return Response(result.to_dict())
            return Response(
                {"error": "Request not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
            # 대기 중인 요청 조회
            stage_name = request.query_params.get("stage_name")
            pending = service.get_pending_requests(stage_name)
            return Response({"pending_requests": [r.to_dict() for r in pending]})

    def post(self, request):
        """롤백 요청 생성"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data
        rollback_request = service.request_rollback(
            stage_name=data.get("stage_name"),
            reason=data.get("reason"),
            triggered_by=data.get("triggered_by", "user"),
            source_version=data.get("source_version", ""),
            target_version=data.get("target_version", ""),
            metadata=data.get("metadata", {}),
        )
        return Response(rollback_request.to_dict(), status=status.HTTP_201_CREATED)


class RollbackExecuteView(APIView):
    """
    롤백 실행 API

    POST /api/self-healing/rollback/execute/<request_id>/ - 롤백 실행 (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request, request_id: str):
        """롤백 실행"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data if request.data else {}
        components = data.get("components")

        result = service.execute_rollback(request_id, components)
        return Response(result.to_dict())


class RollbackCancelView(APIView):
    """
    롤백 취소 API

    POST /api/self-healing/rollback/cancel/<request_id>/ - 롤백 취소 (Admin)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request, request_id: str):
        """롤백 취소"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if service.cancel_rollback(request_id):
            return Response({"message": "Rollback cancelled"})
        return Response(
            {"error": "Cannot cancel rollback"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class RollbackHistoryView(APIView):
    """
    롤백 이력 API

    GET /api/self-healing/rollback/history/ - 이력 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        """이력 조회"""
        service = get_rollback_service()
        if not service:
            return Response(
                {"error": "Rollback service not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        stage_name = request.query_params.get("stage_name")
        limit = int(request.query_params.get("limit", "100"))

        history = service.get_history(stage_name=stage_name, limit=limit)
        return Response({"history": history})
