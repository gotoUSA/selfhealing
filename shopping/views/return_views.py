from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from shopping.models import Return
from shopping.serializers.return_serializers import (
    ReturnApproveSerializer,
    ReturnCompleteSerializer,
    ReturnConfirmReceiveSerializer,
    ReturnCreateSerializer,
    ReturnDetailSerializer,
    ReturnListSerializer,
    ReturnRejectSerializer,
    ReturnUpdateSerializer,
)


# ===== Swagger 문서화용 응답 Serializers =====


class ReturnCreateResponseSerializer(drf_serializers.Serializer):
    """교환/환불 신청 응답"""

    message = drf_serializers.CharField()
    return_ = ReturnDetailSerializer(source="return")


class ReturnMessageResponseSerializer(drf_serializers.Serializer):
    """교환/환불 메시지 응답"""

    message = drf_serializers.CharField()


class ReturnActionResponseSerializer(drf_serializers.Serializer):
    """교환/환불 액션 응답"""

    message = drf_serializers.CharField()
    return_ = ReturnDetailSerializer(source="return")


class ReturnErrorResponseSerializer(drf_serializers.Serializer):
    """교환/환불 에러 응답"""

    message = drf_serializers.CharField()


# =============================================================================
# 고객용 Return ViewSet
# /api/returns/ - 고객이 자신의 교환/환불 신청을 관리
# =============================================================================


@extend_schema_view(
    list=extend_schema(
        responses={200: ReturnListSerializer(many=True)},
        summary="내 교환/환불 목록을 조회한다.",
        description="""
처리 내용:
- 로그인한 사용자가 신청한 교환/환불 목록을 조회한다.
- 상태(status) 및 유형(type)으로 필터링할 수 있다.
        """,
        tags=["Returns - 고객"],
    ),
    retrieve=extend_schema(
        responses={200: ReturnDetailSerializer, 404: ReturnErrorResponseSerializer},
        summary="교환/환불 상세 정보를 조회한다.",
        description="""
처리 내용:
- 내가 신청한 교환/환불의 상세 정보를 조회한다.
- 관련 주문 및 상품 정보를 함께 반환한다.
        """,
        tags=["Returns - 고객"],
    ),
    create=extend_schema(
        request=ReturnCreateSerializer,
        responses={201: ReturnCreateResponseSerializer, 400: ReturnErrorResponseSerializer},
        summary="교환/환불을 신청한다.",
        description="""
처리 내용:
- 주문 상품에 대해 교환 또는 환불을 신청한다.
- 신청 사유와 반품 상품 정보를 입력한다.
- 신청 상태로 생성되며 판매자 승인을 대기한다.
        """,
        tags=["Returns - 고객"],
    ),
    partial_update=extend_schema(
        request=ReturnUpdateSerializer,
        responses={200: ReturnDetailSerializer, 400: ReturnErrorResponseSerializer},
        summary="교환/환불 정보를 수정한다.",
        description="""
처리 내용:
- 교환/환불 정보(송장번호 등)를 수정한다.
- 신청자만 수정할 수 있다.
- 승인된 상태에서만 송장번호 입력 가능.
        """,
        tags=["Returns - 고객"],
    ),
    destroy=extend_schema(
        responses={
            200: ReturnMessageResponseSerializer,
            400: ReturnErrorResponseSerializer,
            403: ReturnErrorResponseSerializer,
        },
        summary="교환/환불 신청을 취소한다.",
        description="""
처리 내용:
- 신청(requested) 상태의 교환/환불만 취소할 수 있다.
- 신청자만 취소할 수 있다.
- 취소된 교환/환불은 복구할 수 없다.
        """,
        tags=["Returns - 고객"],
    ),
)
class ReturnViewSet(viewsets.ModelViewSet):
    """
    고객용 교환/환불 API ViewSet

    고객이 자신의 교환/환불을 관리하는 엔드포인트:
    - GET    /api/returns/           - 내 교환/환불 목록
    - POST   /api/returns/           - 교환/환불 신청
    - GET    /api/returns/{id}/      - 교환/환불 상세
    - PATCH  /api/returns/{id}/      - 송장번호 입력 등 수정
    - DELETE /api/returns/{id}/      - 신청 취소
    """

    permission_classes = [IsAuthenticated]
    queryset = Return.objects.all()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self) -> type[BaseSerializer]:
        """액션별 Serializer 선택"""
        if self.action == "create":
            return ReturnCreateSerializer
        elif self.action == "list":
            return ReturnListSerializer
        elif self.action == "retrieve":
            return ReturnDetailSerializer
        elif self.action in ["update", "partial_update"]:
            return ReturnUpdateSerializer
        return ReturnListSerializer

    def get_queryset(self) -> Any:
        """
        현재 사용자가 신청한 교환/환불만 조회
        """
        user = self.request.user

        queryset = (
            Return.objects.filter(user=user)
            .select_related("order", "exchange_product")
            .prefetch_related("return_items__order_item__product")
        )

        # 필터링
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        type_filter = self.request.query_params.get("type")
        if type_filter:
            queryset = queryset.filter(type=type_filter)

        return queryset

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """교환/환불 신청"""
        order_id = kwargs.get("order_id") or request.data.get("order_id")

        serializer = self.get_serializer(data=request.data, context={"request": request, "order_id": order_id})

        serializer.is_valid(raise_exception=True)
        return_obj = serializer.save()

        return Response(
            {
                "message": f"{return_obj.get_type_display()} 신청이 완료되었습니다.",
                "return": ReturnDetailSerializer(return_obj).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """교환/환불 신청 취소"""
        return_obj = self.get_object()

        # 권한 확인 (이미 queryset에서 본인 것만 필터링하지만 추가 확인)
        if return_obj.user != request.user:
            return Response({"message": "권한이 없습니다."}, status=status.HTTP_403_FORBIDDEN)

        # 상태 확인
        if return_obj.status != "requested":
            return Response(
                {"message": "신청 상태에서만 취소할 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return_obj.delete()

        return Response({"message": "교환/환불 신청이 취소되었습니다."}, status=status.HTTP_200_OK)


# =============================================================================
# 판매자용 Return ViewSet
# /api/seller/returns/ - 판매자가 자신의 상품에 대한 교환/환불을 처리
# =============================================================================


@extend_schema_view(
    list=extend_schema(
        responses={200: ReturnListSerializer(many=True)},
        summary="내 상품에 대한 교환/환불 목록을 조회한다.",
        description="""
처리 내용:
- 판매자 본인 상품에 대한 교환/환불 신청 목록을 조회한다.
- 상태(status) 및 유형(type)으로 필터링할 수 있다.
        """,
        tags=["Returns - 판매자"],
    ),
    retrieve=extend_schema(
        responses={200: ReturnDetailSerializer, 404: ReturnErrorResponseSerializer},
        summary="교환/환불 상세 정보를 조회한다.",
        description="""
처리 내용:
- 내 상품에 대한 교환/환불의 상세 정보를 조회한다.
- 신청자, 주문, 상품 정보를 함께 반환한다.
        """,
        tags=["Returns - 판매자"],
    ),
)
class SellerReturnViewSet(viewsets.ReadOnlyModelViewSet):
    """
    판매자용 교환/환불 API ViewSet

    판매자가 자신의 상품에 대한 교환/환불을 처리하는 엔드포인트:
    - GET    /api/seller/returns/                   - 내 상품 교환/환불 목록
    - GET    /api/seller/returns/{id}/              - 교환/환불 상세
    - POST   /api/seller/returns/{id}/approve/      - 승인
    - POST   /api/seller/returns/{id}/reject/       - 거부
    - POST   /api/seller/returns/{id}/confirm-receive/ - 반품 도착 확인
    - POST   /api/seller/returns/{id}/complete/     - 완료 처리
    """

    permission_classes = [IsAuthenticated]
    queryset = Return.objects.all()

    def get_serializer_class(self) -> type[BaseSerializer]:
        """액션별 Serializer 선택"""
        if self.action == "list":
            return ReturnListSerializer
        elif self.action == "retrieve":
            return ReturnDetailSerializer
        elif self.action == "approve":
            return ReturnApproveSerializer
        elif self.action == "reject":
            return ReturnRejectSerializer
        elif self.action == "confirm_receive":
            return ReturnConfirmReceiveSerializer
        elif self.action == "complete":
            return ReturnCompleteSerializer
        return ReturnListSerializer

    def get_queryset(self) -> Any:
        """
        판매자 본인 상품에 대한 교환/환불만 조회
        관리자(staff)는 모든 교환/환불 조회 가능
        """
        user = self.request.user

        # 관리자인 경우: 모든 Return 접근 가능
        if user.is_staff:
            queryset = (
                Return.objects.all()
                .select_related("order", "exchange_product", "user")
                .prefetch_related("return_items__order_item__product")
            )
        # 판매자인 경우: 본인 상품에 대한 Return만 접근 가능
        elif user.is_seller:
            queryset = (
                Return.objects.filter(return_items__order_item__product__seller=user)
                .distinct()
                .select_related("order", "exchange_product", "user")
                .prefetch_related("return_items__order_item__product")
            )
        # 그 외: 빈 queryset (권한 없음)
        else:
            return Return.objects.none()

        # 필터링
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        type_filter = self.request.query_params.get("type")
        if type_filter:
            queryset = queryset.filter(type=type_filter)

        return queryset

    def _check_seller_permission(self, return_obj: Return) -> tuple[bool, str]:
        """
        판매자 권한 확인 헬퍼 메서드

        해당 교환/환불의 모든 상품이 요청자의 상품인지 확인
        관리자(staff)는 모든 교환/환불 처리 가능

        Args:
            return_obj: Return 객체

        Returns:
            tuple: (bool, str) - (권한 여부, 에러 메시지)
        """
        user = self.request.user

        # 관리자(staff)인 경우 모든 권한 허용
        if user.is_staff:
            return True, ""

        # 판매자가 아닌 경우
        if not user.is_seller:
            return False, "판매자만 접근할 수 있습니다."

        # 교환/환불에 포함된 모든 상품의 판매자 확인
        return_items = return_obj.return_items.select_related("order_item__product__seller").all()

        for item in return_items:
            if item.order_item.product.seller != user:
                return False, "본인 상품에 대한 교환/환불만 처리할 수 있습니다."

        return True, ""

    @extend_schema(
        request=ReturnApproveSerializer,
        responses={
            200: ReturnActionResponseSerializer,
            403: ReturnErrorResponseSerializer,
        },
        summary="교환/환불 요청을 승인한다.",
        description="""
처리 내용:
- 판매자 또는 관리자만 승인할 수 있다.
- 승인 후 고객에게 반품 안내가 발송된다.
- 상태가 승인(approved)으로 변경된다.
        """,
        tags=["Returns - 판매자"],
    )
    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request: Request, pk: int | None = None) -> Response:
        return_obj = self.get_object()

        # 판매자 권한 확인
        has_permission, error_message = self._check_seller_permission(return_obj)
        if not has_permission:
            return Response({"message": error_message}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data, context={"return_obj": return_obj})
        serializer.is_valid(raise_exception=True)
        return_obj = serializer.save()

        return Response(
            {
                "message": "승인되었습니다.",
                "return": ReturnDetailSerializer(return_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ReturnRejectSerializer,
        responses={
            200: ReturnActionResponseSerializer,
            403: ReturnErrorResponseSerializer,
        },
        summary="교환/환불 요청을 거부한다.",
        description="""
처리 내용:
- 판매자 또는 관리자만 거부할 수 있다.
- 거부 사유를 함께 입력해야 한다.
- 상태가 거부(rejected)로 변경된다.
        """,
        tags=["Returns - 판매자"],
    )
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request: Request, pk: int | None = None) -> Response:
        return_obj = self.get_object()

        # 판매자 권한 확인
        has_permission, error_message = self._check_seller_permission(return_obj)
        if not has_permission:
            return Response({"message": error_message}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data, context={"return_obj": return_obj})
        serializer.is_valid(raise_exception=True)
        return_obj = serializer.save()

        return Response(
            {
                "message": "거부되었습니다.",
                "return": ReturnDetailSerializer(return_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ReturnConfirmReceiveSerializer,
        responses={
            200: ReturnActionResponseSerializer,
            403: ReturnErrorResponseSerializer,
        },
        summary="반품 도착을 확인한다.",
        description="""
처리 내용:
- 판매자 또는 관리자가 반품 상품의 도착을 확인한다.
- 상태가 도착완료(received)로 변경된다.
- 도착 확인 후 환불/교환 완료 처리가 가능해진다.
        """,
        tags=["Returns - 판매자"],
    )
    @action(detail=True, methods=["post"], url_path="confirm-receive")
    def confirm_receive(self, request: Request, pk: int | None = None) -> Response:
        return_obj = self.get_object()

        # 판매자 권한 확인
        has_permission, error_message = self._check_seller_permission(return_obj)
        if not has_permission:
            return Response({"message": error_message}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data={}, context={"return_obj": return_obj})
        serializer.is_valid(raise_exception=True)
        return_obj = serializer.save()

        return Response(
            {
                "message": "반품 도착이 확인되었습니다.",
                "return": ReturnDetailSerializer(return_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ReturnCompleteSerializer,
        responses={
            200: ReturnActionResponseSerializer,
            400: ReturnErrorResponseSerializer,
            403: ReturnErrorResponseSerializer,
        },
        summary="교환/환불을 완료 처리한다.",
        description="""
처리 내용:
- 판매자 또는 관리자가 교환/환불을 완료 처리한다.
- 환불의 경우 자동 환불 처리된다.
- 교환의 경우 교환 상품 송장번호를 입력한다.
        """,
        tags=["Returns - 판매자"],
    )
    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request: Request, pk: int | None = None) -> Response:
        return_obj = self.get_object()

        # 판매자 권한 확인
        has_permission, error_message = self._check_seller_permission(return_obj)
        if not has_permission:
            return Response({"message": error_message}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data, context={"return_obj": return_obj})
        serializer.is_valid(raise_exception=True)

        try:
            return_obj = serializer.save()

            message = "환불이 완료되었습니다." if return_obj.type == "refund" else "교환 상품이 발송되었습니다."

            return Response(
                {
                    "message": message,
                    "return": ReturnDetailSerializer(return_obj).data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"message": f"처리 중 오류가 발생했습니다: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
