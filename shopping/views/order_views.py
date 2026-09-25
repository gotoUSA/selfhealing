from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, mixins, permissions, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer, ValidationError

from ..models.order import Order
from ..permissions import IsOrderOwnerOrAdmin
from ..serializers.order_serializers import OrderCreateSerializer, OrderDetailSerializer, OrderListSerializer
from ..services.order_service import OrderService, OrderServiceError
from ..services.product_query_service import prefetch_product_cards
from ..throttles import OrderCancelRateThrottle, OrderCreateRateThrottle

logger = logging.getLogger(__name__)


# ===== Swagger 문서화용 응답 Serializers =====


class OrderCreateResponseSerializer(drf_serializers.Serializer):
    """주문 생성 성공 응답"""

    order_id = drf_serializers.IntegerField(help_text="생성된 주문 ID")
    order_number = drf_serializers.CharField(help_text="주문 번호")
    status = drf_serializers.CharField(help_text="주문 상태 (pending)")
    task_id = drf_serializers.CharField(
        allow_null=True, help_text="비동기 작업 ID (발행에 실패하면 null — 주문은 접수됐고 자동으로 다시 발행된다)"
    )
    message = drf_serializers.CharField()
    status_url = drf_serializers.CharField(help_text="주문 상태 확인 URL")


# 같은 장바구니로 다시 주문했을 때 돌려주는 최근 주문의 안내 문구 — pending 은 아직 재고도 확보되지 않은 상태라
# "완료"라고 하면 안 된다
EXISTING_ORDER_MESSAGES = {
    "pending": "주문이 처리 중입니다. 잠시 후 주문 내역에서 확인해주세요.",
    "confirmed": "이미 접수된 주문이 있습니다. 주문 내역에서 결제를 진행해주세요.",
}


def _existing_order_response(order: Order) -> Response:
    """새 주문 대신 진행 중인 최근 주문을 돌려준다 (멱등 응답)"""
    return Response(
        {
            "order_id": order.id,
            "order_number": order.order_number,
            "status": order.status,
            "message": EXISTING_ORDER_MESSAGES.get(order.status, "이미 접수된 주문이 있습니다."),
            "status_url": f"/api/orders/{order.id}/",
            "idempotent": True,
        },
        status=status.HTTP_200_OK,
    )


class OrderCancelResponseSerializer(drf_serializers.Serializer):
    """주문 취소 성공 응답"""

    message = drf_serializers.CharField()
    refund_amount = drf_serializers.IntegerField(help_text="토스로 환불한 금액 (결제 전 주문은 0)")


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
)
class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    주문 관리 ViewSet

    엔드포인트:
    - GET    /api/orders/           - 주문 목록 조회
    - POST   /api/orders/           - 주문 생성
    - GET    /api/orders/{id}/      - 주문 상세 조회
    - POST   /api/orders/{id}/cancel/ - 주문 취소

    권한:
    - 인증된 사용자만 접근 가능
    - 본인 주문 또는 관리자만 조회 가능

    수정·삭제 API는 두지 않는다. 주문의 상태와 금액은 생성·취소·결제 흐름으로만 바뀐다.
    (예전엔 ModelViewSet 이라 PUT/PATCH/DELETE 가 열려 있었고, 스키마 설명은 "관리자만"이었지만
    권한 클래스는 주문 주인도 통과시켰다 — 고객이 final_amount 를 0 으로 바꾼 뒤 포인트 전액 결제로
    결제 없이 paid 를 만들 수 있었다)
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
        - annotate(item_count): 아이템 개수 미리 계산 (목록은 항목을 그리지 않으므로 항목을 읽지 않는다)
        - 상세: 주문 항목의 상품을 상품 카드 모양으로 한 번에 (항목마다 카테고리·판매자·이미지를 따로 읽지 않게)

        보안:
        - 관리자: 전체 주문 조회
        - 일반 사용자: 본인 주문만 조회
        """
        queryset = Order.objects.select_related("user").annotate(item_count=Count("order_items"))
        if self.action != "list":
            queryset = queryset.prefetch_related(prefetch_product_cards("order_items__product", self.request.user.id))

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

        # 본문 파싱은 try 밖에서 — 깨진 JSON 은 DRF 의 ParseError(400)로 답해야지, 아래 except Exception 이 500 으로 바꾸면 안 된다
        data = request.data

        try:
            serializer = self.get_serializer(data=data)
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
                    return _existing_order_response(recent_order)

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
                    return _existing_order_response(recent_order)

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
- 결제 전 주문: 재고와 사용한 포인트를 되돌리고 결제를 닫는다. 결제 승인이 진행 중이면 거절한다.
- 결제 완료 주문: 결제 취소와 같다 — 토스로 환불하고 재고·판매량·포인트를 되돌린다.""",
        tags=["Orders"],
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: int | None = None) -> Response:
        """주문 취소"""
        order = self.get_object()

        try:
            result = OrderService.cancel_by_customer(order, request.user)
            logger.info(
                f"주문 취소 성공: order_id={order.id}, order_number={order.order_number}, user_id={request.user.id}, "
                f"refund_amount={result['refund_amount']}"
            )
            return Response({"message": "주문이 취소되었습니다.", "refund_amount": result["refund_amount"]})
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
