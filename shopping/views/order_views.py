from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, permissions, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer, ValidationError

from ..models.order import Order
from ..permissions import IsOrderOwnerOrAdmin
from ..serializers.order_serializers import OrderCreateSerializer, OrderDetailSerializer, OrderListSerializer
from ..services.order_service import OrderService, OrderServiceError
from ..throttles import OrderCancelRateThrottle, OrderCreateRateThrottle

logger = logging.getLogger(__name__)


# ===== Swagger 문서화용 응답 Serializers =====


class OrderCreateResponseSerializer(drf_serializers.Serializer):
    """주문 생성 성공 응답"""

    order_id = drf_serializers.IntegerField(help_text="생성된 주문 ID")
    order_number = drf_serializers.CharField(help_text="주문 번호")
    status = drf_serializers.CharField(help_text="주문 상태 (pending)")
    task_id = drf_serializers.CharField(help_text="비동기 작업 ID")
    message = drf_serializers.CharField()
    status_url = drf_serializers.CharField(help_text="주문 상태 확인 URL")


class OrderCancelResponseSerializer(drf_serializers.Serializer):
    """주문 취소 성공 응답"""

    message = drf_serializers.CharField()


class OrderErrorResponseSerializer(drf_serializers.Serializer):
    """주문 에러 응답"""

    error = drf_serializers.CharField()
    message = drf_serializers.CharField(required=False)
    detail = drf_serializers.CharField(required=False)
    verification_required = drf_serializers.BooleanField(required=False)
    verification_url = drf_serializers.CharField(required=False)


class OrderPagination(PageNumberPagination):
    """주문 목록 페이지네이션"""

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


@extend_schema_view(
    list=extend_schema(
        summary="주문 목록을 조회한다.",
        description="""처리 내용:
- 내 주문 목록을 페이지네이션하여 반환한다.
- 상태별 필터링을 적용한다.
- 최신순으로 정렬한다.""",
        tags=["Orders"],
    ),
    retrieve=extend_schema(
        summary="주문 상세 정보를 조회한다.",
        description="""처리 내용:
- 주문 상세 정보를 반환한다.
- 본인 주문 또는 관리자만 조회 가능하다.""",
        tags=["Orders"],
    ),
    update=extend_schema(
        summary="주문 정보를 전체 수정한다.",
        description="""처리 내용:
- 주문 정보를 전체 수정한다.
- 관리자만 사용 가능하다.""",
        tags=["Orders"],
    ),
    partial_update=extend_schema(
        summary="주문 정보를 부분 수정한다.",
        description="""처리 내용:
- 주문 정보를 부분 수정한다.
- 관리자만 사용 가능하다.""",
        tags=["Orders"],
    ),
    destroy=extend_schema(
        summary="주문을 삭제한다.",
        description="""처리 내용:
- 주문을 삭제한다.
- 관리자만 사용 가능하다.""",
        tags=["Orders"],
    ),
)
class OrderViewSet(viewsets.ModelViewSet):
    """
    주문 관리 ViewSet

    엔드포인트:
    - GET    /api/orders/           - 주문 목록 조회
    - POST   /api/orders/           - 주문 생성
    - GET    /api/orders/{id}/      - 주문 상세 조회
    - POST   /api/orders/{id}/cancel/ - 주문 취소

    권한:
    - 인증된 사용자만 접근 가능
    - 본인 주문 또는 관리자만 조회/수정 가능
    """

    permission_classes = [permissions.IsAuthenticated, IsOrderOwnerOrAdmin]
    pagination_class = OrderPagination

    # 필터링 및 정렬 설정
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_throttles(self):
        """액션별로 다른 throttle 적용"""
        if self.action == "create":
            return [OrderCreateRateThrottle()]
        elif self.action == "cancel":
            return [OrderCancelRateThrottle()]
        return super().get_throttles()

    def get_queryset(self) -> Any:
        """
        주문 조회 쿼리셋

        성능 최적화:
        - select_related("user"): N+1 방지
        - prefetch_related("order_items__product"): 주문 아이템 최적화
        - annotate(item_count): 아이템 개수 미리 계산

        보안:
        - 관리자: 전체 주문 조회
        - 일반 사용자: 본인 주문만 조회
        """
        queryset = (
            Order.objects.select_related("user")
            .prefetch_related("order_items__product")
            .annotate(item_count=Count("order_items"))
        )

        if self.request.user.is_staff or self.request.user.is_superuser:
            return queryset
        else:
            return queryset.filter(user=self.request.user)

    def get_serializer_class(self) -> type[BaseSerializer]:
        if self.action == "list":
            return OrderListSerializer
        elif self.action == "create":
            return OrderCreateSerializer
        return OrderDetailSerializer

    @extend_schema(
        request=OrderCreateSerializer,
        responses={
            202: OrderCreateResponseSerializer,
            400: OrderErrorResponseSerializer,
            403: OrderErrorResponseSerializer,
        },
        summary="새 주문을 생성한다.",
        description="""처리 내용:
- 주문 정보를 검증하고 생성한다.
- 이메일 인증이 완료된 사용자만 주문 가능하다.
- 비동기로 처리되며 202 Accepted를 반환한다.""",
        tags=["Orders"],
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """주문 생성 (이메일 인증 필요, Idempotent)"""
        # 이메일 인증 체크
        if not request.user.is_email_verified:
            logger.warning(f"미인증 사용자 주문 생성 시도: user_id={request.user.id}, email={request.user.email}")
            return Response(
                {
                    "error": "이메일 인증이 필요합니다.",
                    "message": "주문을 생성하려면 먼저 이메일 인증을 완료해주세요.",
                    "detail": "이메일 인증 후 모든 기능을 사용하실 수 있습니다.",
                    "verification_required": True,
                    "verification_url": "/api/email-verification/send/",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # 비동기 처리를 위해 하이브리드 방식 사용
            order, task_id = serializer.create_hybrid(serializer.validated_data)

            logger.info(
                f"주문 하이브리드 생성: order_id={order.id}, order_number={order.order_number}, "
                f"task_id={task_id}, user_id={request.user.id}"
            )

            return Response(
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "status": "pending",
                    "task_id": task_id,
                    "message": "주문 처리 중입니다. 잠시 후 주문 내역에서 확인해주세요.",
                    "status_url": f"/api/orders/{order.id}/",
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValidationError as e:
            # Idempotency: 장바구니가 비어있으면 최근 주문 반환 시도
            error_msg = str(e.detail) if hasattr(e, "detail") else str(e)
            if "장바구니가 비어있습니다" in error_msg:
                recent_order = (
                    Order.objects.filter(user=request.user, status__in=["pending", "confirmed", "processing"])
                    .order_by("-created_at")
                    .first()
                )

                if recent_order:
                    logger.info(
                        f"Idempotent 주문 반환: order_id={recent_order.id}, "
                        f"user_id={request.user.id} (장바구니 비어있음 - 이미 주문됨)"
                    )
                    return Response(
                        {
                            "order_id": recent_order.id,
                            "order_number": recent_order.order_number,
                            "status": recent_order.status,
                            "message": "이미 주문이 완료되었습니다.",
                            "status_url": f"/api/orders/{recent_order.id}/",
                            "idempotent": True,
                        },
                        status=status.HTTP_200_OK,
                    )

            logger.error(f"주문 생성 실패 (ValidationError): user_id={request.user.id}, error={str(e)}")
            return Response(
                e.detail if hasattr(e, "detail") else {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except OrderServiceError as e:
            # Idempotency: 장바구니가 비어있거나 이미 처리 중이면 최근 주문 반환
            error_msg = str(e)
            if "장바구니가 비어있습니다" in error_msg or "이미 주문이 진행 중" in error_msg:
                recent_order = (
                    Order.objects.filter(user=request.user, status__in=["pending", "confirmed", "processing"])
                    .order_by("-created_at")
                    .first()
                )

                if recent_order:
                    logger.info(
                        f"Idempotent 주문 반환: order_id={recent_order.id}, " f"user_id={request.user.id} ({error_msg})"
                    )
                    return Response(
                        {
                            "order_id": recent_order.id,
                            "order_number": recent_order.order_number,
                            "status": recent_order.status,
                            "message": "이미 주문이 완료되었습니다.",
                            "status_url": f"/api/orders/{recent_order.id}/",
                            "idempotent": True,
                        },
                        status=status.HTTP_200_OK,
                    )

            logger.error(f"주문 생성 실패 (OrderServiceError): user_id={request.user.id}, error={str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"주문 생성 실패 (Unexpected): user_id={request.user.id}, error={str(e)}", exc_info=True)
            return Response({"error": "주문 생성 중 오류가 발생했습니다."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        request=None,
        responses={
            200: OrderCancelResponseSerializer,
            400: OrderErrorResponseSerializer,
        },
        summary="주문을 취소한다.",
        description="""처리 내용:
- 배송 전 상태의 주문을 취소한다.
- 사용한 포인트를 환불한다.
- 재고를 복구한다.""",
        tags=["Orders"],
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: int | None = None) -> Response:
        """주문 취소"""
        order = self.get_object()

        try:
            OrderService.cancel_order(order)
            logger.info(f"주문 취소 성공: order_id={order.id}, order_number={order.order_number}, user_id={request.user.id}")
            return Response({"message": "주문이 취소되었습니다."})
        except OrderServiceError as e:
            logger.warning(f"주문 취소 실패: order_id={order.id}, user_id={request.user.id}, error={str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """주문 목록 조회"""
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
