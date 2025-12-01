"""위시리스트(찜) ViewSet

HTTP 요청/응답 처리를 담당합니다.
비즈니스 로직은 WishlistService에 위임합니다.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema

from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

# Serializer import
from shopping.serializers.wishlist_serializers import (
    WishlistBulkAddSerializer,
    WishlistProductSerializer,
    WishlistStatsSerializer,
    WishlistToggleSerializer,
)

# Service import
from shopping.services.wishlist_service import (
    WishlistFilter,
    WishlistService,
    WishlistServiceError,
)


# ===== Swagger 문서화용 응답 Serializers =====


class WishlistListResponseSerializer(drf_serializers.Serializer):
    """찜 목록 조회 응답"""

    count = drf_serializers.IntegerField()
    results = WishlistProductSerializer(many=True)


class WishlistToggleResponseSerializer(drf_serializers.Serializer):
    """찜하기 토글 응답"""

    is_wished = drf_serializers.BooleanField()
    message = drf_serializers.CharField()
    wishlist_count = drf_serializers.IntegerField()


class WishlistAddResponseSerializer(drf_serializers.Serializer):
    """찜 추가 응답"""

    message = drf_serializers.CharField()
    is_wished = drf_serializers.BooleanField()
    wishlist_count = drf_serializers.IntegerField(required=False)


class WishlistMessageResponseSerializer(drf_serializers.Serializer):
    """일반 메시지 응답"""

    message = drf_serializers.CharField()


class WishlistErrorResponseSerializer(drf_serializers.Serializer):
    """에러 응답"""

    error = drf_serializers.CharField()


class WishlistBulkAddResponseSerializer(drf_serializers.Serializer):
    """일괄 찜하기 응답"""

    message = drf_serializers.CharField()
    added_count = drf_serializers.IntegerField()
    skipped_count = drf_serializers.IntegerField()
    total_wishlist_count = drf_serializers.IntegerField()


class WishlistCheckResponseSerializer(drf_serializers.Serializer):
    """찜 상태 확인 응답"""

    product_id = drf_serializers.IntegerField()
    is_wished = drf_serializers.BooleanField()
    wishlist_count = drf_serializers.IntegerField()


class WishlistMoveToCartResponseSerializer(drf_serializers.Serializer):
    """장바구니로 이동 응답"""

    message = drf_serializers.CharField()
    added_items = drf_serializers.ListField(child=drf_serializers.CharField())
    already_in_cart = drf_serializers.ListField(child=drf_serializers.CharField())
    out_of_stock = drf_serializers.ListField(child=drf_serializers.CharField())


class WishlistMoveToCartRequestSerializer(drf_serializers.Serializer):
    """장바구니로 이동 요청"""

    product_ids = drf_serializers.ListField(
        child=drf_serializers.IntegerField(),
        help_text="이동할 상품 ID 목록",
    )
    remove_from_wishlist = drf_serializers.BooleanField(
        default=False,
        help_text="장바구니 추가 후 찜 목록에서 제거 여부",
    )


# ===== Swagger 문서화용 응답 Serializers =====


class WishlistListResponseSerializer(drf_serializers.Serializer):
    """찜 목록 조회 응답"""

    count = drf_serializers.IntegerField()
    results = WishlistProductSerializer(many=True)


class WishlistToggleResponseSerializer(drf_serializers.Serializer):
    """찜하기 토글 응답"""

    is_wished = drf_serializers.BooleanField()
    message = drf_serializers.CharField()
    wishlist_count = drf_serializers.IntegerField()


class WishlistAddResponseSerializer(drf_serializers.Serializer):
    """찜 추가 응답"""

    message = drf_serializers.CharField()
    is_wished = drf_serializers.BooleanField()
    wishlist_count = drf_serializers.IntegerField(required=False)


class WishlistMessageResponseSerializer(drf_serializers.Serializer):
    """일반 메시지 응답"""

    message = drf_serializers.CharField()


class WishlistErrorResponseSerializer(drf_serializers.Serializer):
    """에러 응답"""

    error = drf_serializers.CharField()


class WishlistBulkAddResponseSerializer(drf_serializers.Serializer):
    """일괄 찜하기 응답"""

    message = drf_serializers.CharField()
    added_count = drf_serializers.IntegerField()
    skipped_count = drf_serializers.IntegerField()
    total_wishlist_count = drf_serializers.IntegerField()


class WishlistCheckResponseSerializer(drf_serializers.Serializer):
    """찜 상태 확인 응답"""

    product_id = drf_serializers.IntegerField()
    is_wished = drf_serializers.BooleanField()
    wishlist_count = drf_serializers.IntegerField()


class WishlistMoveToCartResponseSerializer(drf_serializers.Serializer):
    """장바구니로 이동 응답"""

    message = drf_serializers.CharField()
    added_items = drf_serializers.ListField(child=drf_serializers.CharField())
    already_in_cart = drf_serializers.ListField(child=drf_serializers.CharField())
    out_of_stock = drf_serializers.ListField(child=drf_serializers.CharField())


class WishlistMoveToCartRequestSerializer(drf_serializers.Serializer):
    """장바구니로 이동 요청"""

    product_ids = drf_serializers.ListField(child=drf_serializers.IntegerField(), help_text="이동할 상품 ID 목록")
    remove_from_wishlist = drf_serializers.BooleanField(default=False, help_text="장바구니 추가 후 찜 목록에서 제거 여부")


class WishlistViewSet(GenericViewSet):
    """
    찜하기(위시리스트) 관리 ViewSet

    엔드포인트:
    - GET    /api/wishlist/             - 찜 목록 조회
    - POST   /api/wishlist/toggle/      - 찜하기 토글 (추가/제거)
    - POST   /api/wishlist/add/         - 찜 추가
    - DELETE /api/wishlist/remove/      - 찜 제거
    - POST   /api/wishlist/bulk_add/    - 여러 상품 일괄 추가
    - DELETE /api/wishlist/clear/       - 찜 목록 전체 삭제
    - GET    /api/wishlist/check/       - 찜 상태 확인
    - GET    /api/wishlist/stats/       - 찜 목록 통계
    - POST   /api/wishlist/move_to_cart/ - 장바구니로 이동

    권한: 인증 필요 (본인 찜 목록만 관리 가능)
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> Any:
        """현재 사용자의 찜한 상품 쿼리셋 반환"""
        return WishlistService.get_list(self.request.user)

    # ===== 찜 목록 조회 =====


    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="ordering",
                description="정렬 (기본: -created_at)",
                required=False,
                type=str,
                enum=["created_at", "-created_at", "price", "-price", "name"],
            ),
            OpenApiParameter(
                name="is_available",
                description="구매 가능 상품만 (true/false)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="on_sale",
                description="세일 중인 상품만 (true/false)",
                required=False,
                type=str,
            ),

        ],
        request=None,  # GET 요청에는 request body가 없음
        responses={200: WishlistListResponseSerializer},
        summary="찜 목록을 조회한다.",
        description="""처리 내용:
- 현재 사용자의 찜 목록을 반환한다.
- 정렬 및 필터링을 적용한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["get"])
    def list(self, request: Request) -> Response:
        """찜 목록 조회"""
        # 필터 파싱
        filters = self._parse_filters(request)

        # 서비스 호출
        queryset = WishlistService.get_list(request.user, filters)


        serializer = WishlistProductSerializer(queryset, many=True)

        return Response(
            {
                "count": queryset.count(),
                "results": serializer.data,
            }
        )

    # ===== 찜하기 토글 =====


    @extend_schema(
        request=WishlistToggleSerializer,
        responses={
            200: WishlistToggleResponseSerializer,
            400: WishlistErrorResponseSerializer,
        },
        summary="찜하기를 토글한다.",
        description="""처리 내용:
- 찜하기를 토글한다 (추가/제거).
- 하트 버튼 구현에 최적화되어 있다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["post"])
    def toggle(self, request: Request) -> Response:
        """찜하기 토글"""
        serializer = WishlistToggleSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = WishlistService.toggle(
                user=request.user,
                product_id=serializer.validated_data["product_id"],
            )

            return Response(
                {
                    "is_wished": result.is_wished,
                    "message": result.message,
                    "wishlist_count": result.wishlist_count,
                }
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 찜 추가 =====

    @extend_schema(
        request=WishlistToggleSerializer,
        responses={
            200: WishlistAddResponseSerializer,
            201: WishlistAddResponseSerializer,
            400: WishlistErrorResponseSerializer,
        },
        summary="찜 목록에 상품을 추가한다.",
        description="""처리 내용:
- 찜 목록에 상품을 추가한다.
- 이미 찜한 상품이면 에러 없이 무시한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["post"])
    def add(self, request: Request) -> Response:
        """찜 목록에 상품 추가"""
        serializer = WishlistToggleSerializer(data=request.data)

        if not serializer.is_valid():
            if "product_id" in serializer.errors:
                return Response(
                    {"error": str(serializer.errors["product_id"][0])},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            is_new, message, wishlist_count = WishlistService.add(
                user=request.user,
                product_id=serializer.validated_data["product_id"],
            )

            response_data = {
                "message": message,
                "is_wished": True,
                "wishlist_count": wishlist_count,
            }

            return Response(
                response_data,
                status=status.HTTP_201_CREATED if is_new else status.HTTP_200_OK,
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 찜 제거 =====

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="product_id",
                description="제거할 상품 ID",
                required=True,
                type=int,
            ),

        ],
        responses={
            204: WishlistMessageResponseSerializer,
            400: WishlistErrorResponseSerializer,
            404: WishlistMessageResponseSerializer,
        },
        summary="찜 목록에서 상품을 제거한다.",
        description="""처리 내용:
- 찜 목록에서 상품을 제거한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["delete"])
    def remove(self, request: Request) -> Response:
        """찜 목록에서 상품 제거"""
        product_id = request.query_params.get("product_id")

        if not product_id:
            return Response(
                {"error": "product_id가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            WishlistService.remove(
                user=request.user,
                product_id=int(product_id),
            )

            return Response(
                {"message": "찜 목록에서 제거되었습니다."},
                status=status.HTTP_204_NO_CONTENT,
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 일괄 추가 =====


    @extend_schema(
        request=WishlistBulkAddSerializer,
        responses={
            201: WishlistBulkAddResponseSerializer,
            400: WishlistErrorResponseSerializer,
        },
        summary="여러 상품을 일괄 찜한다.",
        description="""처리 내용:
- 여러 상품을 한 번에 찜 목록에 추가한다.
- 중복된 상품은 자동 제외한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["post"])
    def bulk_add(self, request: Request) -> Response:
        """여러 상품 일괄 찜하기"""
        serializer = WishlistBulkAddSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = WishlistService.bulk_add(
                user=request.user,
                product_ids=serializer.validated_data["product_ids"],
            )

            return Response(
                {
                    "message": f"{result.added_count}개 상품이 찜 목록에 추가되었습니다.",
                    "added_count": result.added_count,
                    "skipped_count": result.skipped_count,
                    "total_wishlist_count": result.total_wishlist_count,
                },
                status=status.HTTP_201_CREATED,
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 전체 삭제 =====

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="confirm",
                description="전체 삭제 확인 (true 필수)",
                required=True,
                type=str,
            ),

        ],
        responses={
            204: WishlistMessageResponseSerializer,
            400: WishlistErrorResponseSerializer,
        },
        summary="찜 목록을 전체 삭제한다.",
        description="""처리 내용:
- 찜 목록을 전체 삭제한다.
- 실수 방지를 위해 confirm=true 파라미터가 필수이다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["delete"])
    def clear(self, request: Request) -> Response:
        """찜 목록 전체 삭제"""
        confirm = request.query_params.get("confirm")

        if confirm != "true":
            return Response(
                {"error": "찜 목록 전체 삭제를 확인하려면 confirm=true를 추가하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            count = WishlistService.clear(user=request.user)

            return Response(
                {"message": f"{count}개의 상품이 찜 목록에서 삭제되었습니다."},
                status=status.HTTP_204_NO_CONTENT,
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 찜 상태 확인 =====

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="product_id",
                description="확인할 상품 ID",
                required=True,
                type=int,
            ),

        ],
        request=None,  # GET 요청에는 request body가 없음
        responses={
            200: WishlistCheckResponseSerializer,
            400: WishlistErrorResponseSerializer,
        },
        summary="찜 상태를 확인한다.",
        description="""처리 내용:
- 특정 상품의 찜 상태를 확인한다.
- 하트 버튼 초기 상태 표시에 사용한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["get"])
    def check(self, request: Request) -> Response:
        """특정 상품의 찜 상태 확인"""
        product_id = request.query_params.get("product_id")

        if not product_id:
            return Response(
                {"error": "product_id가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = WishlistService.check(
                user=request.user,
                product_id=int(product_id),
            )

            return Response(result)

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== 통계 조회 =====

    @extend_schema(
        request=None,  # GET 요청에는 request body가 없음
        responses={200: WishlistStatsSerializer},
        summary="찜 목록 통계를 조회한다.",
        description="""처리 내용:
- 찜 목록 통계를 반환한다.
- 전체/구매가능/품절 상품 수를 포함한다.
- 가격 합계 및 할인 금액을 포함한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["get"])
    def stats(self, request: Request) -> Response:
        """찜 목록 통계 조회"""
        stats = WishlistService.get_stats(user=request.user)

        serializer = WishlistStatsSerializer(
            {
                "total_count": stats.total_count,
                "available_count": stats.available_count,
                "out_of_stock_count": stats.out_of_stock_count,
                "on_sale_count": stats.on_sale_count,
                "total_price": stats.total_price,
                "total_sale_price": stats.total_sale_price,
                "total_discount": stats.total_discount,
            }
        )

        return Response(serializer.data)

    # ===== 장바구니로 이동 =====


    @extend_schema(
        request=WishlistMoveToCartRequestSerializer,
        responses={
            200: WishlistMoveToCartResponseSerializer,
            400: WishlistErrorResponseSerializer,
            404: WishlistErrorResponseSerializer,
        },
        summary="찜 목록에서 장바구니로 이동한다.",
        description="""처리 내용:
- 찜 목록에서 장바구니로 상품을 이동한다.
- 재고를 자동 확인한다.
- remove_from_wishlist=true 시 찜 목록에서 제거한다.""",
        tags=["Wishlist"],
    )
    @action(detail=False, methods=["post"])
    def move_to_cart(self, request: Request) -> Response:
        """찜 목록에서 장바구니로 이동"""
        product_ids = request.data.get("product_ids", [])

        remove_from_wishlist = request.data.get("remove_from_wishlist", False)

        # Boolean으로 명시적 변환
        if isinstance(remove_from_wishlist, str):
            remove_from_wishlist = remove_from_wishlist.lower() in ["true", "1", "yes"]
        else:
            remove_from_wishlist = bool(remove_from_wishlist)

        try:
            result = WishlistService.move_to_cart(
                user=request.user,
                product_ids=product_ids,
                remove_from_wishlist=remove_from_wishlist,
            )

            return Response(
                {
                    "message": result.message,
                    "added_items": result.added_items,
                    "already_in_cart": result.already_in_cart,
                    "out_of_stock": result.out_of_stock,
                },
                status=status.HTTP_200_OK,
            )

        except WishlistServiceError as e:
            return self._handle_service_error(e)

    # ===== Private Helper Methods =====

    def _parse_filters(self, request: Request) -> WishlistFilter:
        """요청에서 필터 옵션 파싱"""
        is_available = request.query_params.get("is_available")
        on_sale = request.query_params.get("on_sale")
        ordering = request.query_params.get("ordering", "-created_at")

        return WishlistFilter(
            is_available=True if is_available == "true" else (False if is_available == "false" else None),
            on_sale=True if on_sale == "true" else None,
            ordering=ordering,
        )

    def _handle_service_error(self, error: WishlistServiceError) -> Response:
        """서비스 에러를 HTTP 응답으로 변환"""
        status_map = {
            "PRODUCT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "NOT_IN_WISHLIST": status.HTTP_404_NOT_FOUND,
            "PRODUCTS_NOT_IN_WISHLIST": status.HTTP_404_NOT_FOUND,
            "WISHLIST_EMPTY": status.HTTP_400_BAD_REQUEST,
            "EMPTY_PRODUCT_IDS": status.HTTP_400_BAD_REQUEST,
        }

        http_status = status_map.get(error.code, status.HTTP_400_BAD_REQUEST)

        response_data = {"error": error.message}
        if error.details:
            response_data.update(error.details)

        return Response(response_data, status=http_status)
