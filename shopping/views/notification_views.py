"""알림 ViewSet

HTTP 요청/응답 처리를 담당합니다.
비즈니스 로직은 NotificationService에 위임합니다.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from ..serializers.notification_serializers import (
    NotificationListSerializer,
    NotificationMarkReadSerializer,
    NotificationSerializer,
)
from ..services.notification_service import NotificationService, NotificationServiceError


# ===== Swagger 문서화용 응답 Serializers =====


class UnreadNotificationResponseSerializer(drf_serializers.Serializer):
    """읽지 않은 알림 응답"""

    count = drf_serializers.IntegerField()
    notifications = NotificationListSerializer(many=True)


class MarkReadResponseSerializer(drf_serializers.Serializer):
    """알림 읽음 처리 응답"""

    message = drf_serializers.CharField()
    count = drf_serializers.IntegerField()


class ClearResponseSerializer(drf_serializers.Serializer):
    """알림 삭제 응답"""

    message = drf_serializers.CharField()
    count = drf_serializers.IntegerField()


class ErrorResponseSerializer(drf_serializers.Serializer):
    """에러 응답"""

    error = drf_serializers.CharField()



@extend_schema_view(
    list=extend_schema(
        summary="내 알림 목록을 조회한다.",
        description="""처리 내용:
- 현재 사용자의 알림 목록을 반환한다.""",
        tags=["Notifications"],
    ),
    retrieve=extend_schema(
        summary="알림 상세 정보를 조회한다.",
        description="""처리 내용:
- 알림의 상세 정보를 반환한다.
- 조회 시 자동으로 읽음 처리한다.""",
        tags=["Notifications"],
    ),
)
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    알림 ViewSet

    엔드포인트:
    - GET    /api/notifications/           - 알림 목록 조회
    - GET    /api/notifications/{id}/      - 알림 상세 조회 (자동 읽음 처리)
    - GET    /api/notifications/unread/    - 읽지 않은 알림 조회
    - POST   /api/notifications/mark_read/ - 알림 읽음 처리
    - DELETE /api/notifications/clear/     - 읽은 알림 전체 삭제

    권한: 인증 필요 (본인 알림만 관리 가능)
    """


    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> Any:
        """현재 사용자의 알림만 조회"""
        return NotificationService.get_queryset(self.request.user)

    def get_serializer_class(self) -> type[NotificationListSerializer] | type[NotificationSerializer]:
        """
        액션에 따라 적절한 Serializer 선택

        - list, unread: 경량 ListSerializer (message 제외)
        - retrieve: 전체 정보 포함 Serializer
        """
        serializer_map = {
            "list": NotificationListSerializer,
            "unread": NotificationListSerializer,
        }
        return serializer_map.get(self.action, NotificationSerializer)

    # ===== 알림 상세 조회 =====

    def retrieve(self, request: Request, pk: int | None = None) -> Response:
        """
        알림 상세 조회

        조회 시 자동으로 읽음 처리됩니다.
        """
        try:
            notification = NotificationService.get_by_id(request.user, int(pk))

            # 읽지 않은 알림이면 읽음 처리
            NotificationService.mark_single_as_read(notification)

            serializer = self.get_serializer(notification)
            return Response(serializer.data)

        except NotificationServiceError as e:
            return self._handle_service_error(e)

    # ===== 읽지 않은 알림 조회 =====


    @extend_schema(
        responses={200: UnreadNotificationResponseSerializer},
        summary="읽지 않은 알림을 조회한다.",
        description="""처리 내용:
- 읽지 않은 알림 개수와 최근 5개를 반환한다.
- 프론트엔드 알림 아이콘 표시에 활용한다.""",
        tags=["Notifications"],
    )
    @action(detail=False, methods=["get"])
    def unread(self, request: Request) -> Response:
        """읽지 않은 알림 조회"""
        result = NotificationService.get_unread(request.user)

        serializer = NotificationListSerializer(result.notifications, many=True)

        return Response(
            {
                "count": result.count,
                "notifications": serializer.data,
            }
        )


    # ===== 알림 읽음 처리 =====

    @extend_schema(
        request=NotificationMarkReadSerializer,
        responses={
            200: MarkReadResponseSerializer,
            400: ErrorResponseSerializer,
        },

        summary="알림을 읽음 처리한다.",
        description="""처리 내용:
- 지정된 알림들을 읽음 처리한다.
- notification_ids가 빈 배열이면 전체 읽음 처리한다.""",
        tags=["Notifications"],
    )
    @action(detail=False, methods=["post"])
    def mark_read(self, request: Request) -> Response:
        """알림 읽음 처리"""
        serializer = NotificationMarkReadSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Serializer에서 notification_ids 추출
        notification_ids = serializer.validated_data.get("notification_ids", [])


        # 빈 배열이면 전체 읽음 처리
        result = NotificationService.mark_as_read(
            user=request.user,
            notification_ids=notification_ids if notification_ids else None,
        )

        return Response(
            {
                "message": result.message,
                "count": result.count,
            }
        )

    # ===== 읽은 알림 삭제 =====

    @extend_schema(
        responses={
            200: ClearResponseSerializer,
        },

        summary="읽은 알림을 전체 삭제한다.",
        description="""처리 내용:
- 읽음 처리된 알림을 전체 삭제한다.""",
        tags=["Notifications"],
    )
    @action(detail=False, methods=["delete"])
    def clear(self, request: Request) -> Response:
        """읽은 알림 전체 삭제"""
        result = NotificationService.clear_read(user=request.user)

        return Response(
            {
                "message": result.message,
                "count": result.count,
            }
        )

    # ===== Private Helper Methods =====

    def _handle_service_error(self, error: NotificationServiceError) -> Response:
        """서비스 에러를 HTTP 응답으로 변환"""
        status_map = {
            "NOTIFICATION_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        }


        http_status = status_map.get(error.code, status.HTTP_400_BAD_REQUEST)

        response_data = {"error": error.message}
        if error.details:
            response_data.update(error.details)

        return Response(response_data, status=http_status)
