from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from ..models.order import Order
from ..permissions import IsOrderOwnerOrAdmin
from ..serializers.order_serializers import OrderCreateSerializer, OrderDetailSerializer, OrderListSerializer
from ..services.order_service import OrderService, OrderServiceError
from ..throttles import OrderCancelRateThrottle, OrderCreateRateThrottle

logger = logging.getLogger(__name__)


class OrderPagination(PageNumberPagination):
    """주문 목록 페이지네이션"""

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class OrderViewSet(viewsets.ModelViewSet):
    """
    주문 관리 ViewSet

    보안:
    - IsAuthenticated: 인증된 사용자만 접근 가능
    - IsOrderOwnerOrAdmin: 객체 레벨 권한 (본인 주문 또는 관리자)
    - get_queryset: 쿼리 레벨 필터링 (이중 보안)

    성능 최적화:
    - select_related("user"): user 정보 조회 최적화
    - prefetch_related("order_items__product"): 주문 아이템 조회 최적화
    - annotate(item_count): 주문 아이템 개수를 쿼리에서 계산 (N+1 방지)
    """

    permission_classes = [permissions.IsAuthenticated, IsOrderOwnerOrAdmin]

    # 페이지네이션 설정
    pagination_class = OrderPagination

    # 필터링 및 정렬 설정
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status"]  # status 필터 지원
    ordering_fields = ["created_at"]  # created_at 정렬만 지원
    ordering = ["-created_at"]  # 기본 정렬: 최신순

    def get_throttles(self):
        """액션별로 다른 throttle 적용"""
        if self.action == "create":
            return [OrderCreateRateThrottle()]
        elif self.action == "cancel":
            return [OrderCancelRateThrottle()]
        return super().get_throttles()

    def get_queryset(self) -> Any:
        """
        주문 조회 - 관리자는 전체, 일반 사용자는 본인 것만

        성능 최적화:
        - select_related("user"): user_username 필드를 위한 JOIN (N+1 방지)
        - prefetch_related("order_items__product"): 주문 아이템 및 상품 조회 최적화
        - annotate(item_count): 주문 아이템 개수를 쿼리에서 미리 계산 (N+1 방지)

        보안:
        - 일반 사용자는 본인 주문만 필터링 (쿼리 레벨 보안)
        - IsOrderOwnerOrAdmin과 함께 이중 보안 제공
        """
        # 기본 queryset: 성능 최적화 적용
        queryset = (
            Order.objects.select_related("user")
            .prefetch_related("order_items__product")
            .annotate(item_count=Count("order_items"))
        )

        # 권한에 따라 필터링
        if self.request.user.is_staff or self.request.user.is_superuser:
            # 관리자는 모든 주문 조회 가능
            return queryset
        else:
            # 일반 사용자는 본인 주문만 조회 가능
            return queryset.filter(user=self.request.user)

    def get_serializer_class(self) -> type[BaseSerializer]:
        if self.action == "list":
            return OrderListSerializer
        elif self.action == "create":
            return OrderCreateSerializer
        return OrderDetailSerializer

    # create 메서드 오버라이드
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        주문 생성

        이메일 인증이 완료된 사용자만 주문을 생성할 수 있습니다.
        """
        # 이메일 인증 체크
        if not request.user.is_email_verified:
            logger.warning(f"미인증 사용자 주문 생성 시도: user_id={request.user.id}, " f"email={request.user.email}")
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


        # Validate and create order using hybrid method
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Use hybrid method for async processing
        order, task_id = serializer.create_hybrid(serializer.validated_data)

        logger.info(
            f"주문 하이브리드 생성: order_id={order.id}, order_number={order.order_number}, "
            f"task_id={task_id}, user_id={request.user.id}"
        )

        # Return HTTP 202 Accepted with task tracking info
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


    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: int | None = None) -> Response:
        """
        주문 취소

        OrderService를 통해 주문 취소 로직을 처리합니다.
        """
        order = self.get_object()

        try:
            OrderService.cancel_order(order)
            logger.info(
                f"주문 취소 성공: order_id={order.id}, order_number={order.order_number}, " f"user_id={request.user.id}"
            )
            return Response({"message": "주문이 취소되었습니다."})
        except OrderServiceError as e:
            logger.warning(f"주문 취소 실패: order_id={order.id}, user_id={request.user.id}, " f"error={str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """주문 목록 조회 - 페이지네이션 구조 확인"""
        queryset = self.filter_queryset(self.get_queryset())

        # 페이지네이션이 설정되어 있으면
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        # 페이지네이션이 없으면
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
