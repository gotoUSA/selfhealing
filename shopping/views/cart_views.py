"""장바구니 뷰

장바구니 관련 API 엔드포인트를 제공합니다.
비즈니스 로직은 CartService로 위임합니다.

현업 표준 패턴:
1. 뷰는 HTTP 요청/응답 처리에만 집중
2. 비즈니스 로직은 서비스 레이어에 위임
3. Serializer는 입력 검증 및 출력 직렬화 담당
"""

from __future__ import annotations

from typing import Any


from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from shopping.models.cart import Cart, CartItem

from shopping.serializers import (
    CartClearSerializer,
    CartItemCreateSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    SimpleCartSerializer,
)
from shopping.services.cart_service import CartService, CartServiceError
from shopping.services.product_query_service import prefetch_product_cards


# ===== Swagger 문서화용 응답 Serializers =====


class CartAddItemResponseSerializer(drf_serializers.Serializer):
    """장바구니 상품 추가 응답"""

    message = drf_serializers.CharField()
    item = CartItemSerializer()


class CartUpdateItemResponseSerializer(drf_serializers.Serializer):
    """장바구니 수량 변경 응답"""

    message = drf_serializers.CharField()
    item = CartItemSerializer()


class CartMessageResponseSerializer(drf_serializers.Serializer):
    """장바구니 일반 메시지 응답"""

    message = drf_serializers.CharField()


class CartErrorResponseSerializer(drf_serializers.Serializer):
    """장바구니 에러 응답"""

    error = drf_serializers.CharField(required=False)
    message = drf_serializers.CharField(required=False)
    code = drf_serializers.CharField(required=False)

    product_id = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    quantity = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


class CartBulkAddItemSerializer(drf_serializers.Serializer):
    """장바구니 일괄 추가 개별 아이템"""

    product_id = drf_serializers.IntegerField(help_text="상품 ID")
    quantity = drf_serializers.IntegerField(default=1, help_text="수량 (기본값: 1)")


class CartBulkAddRequestSerializer(drf_serializers.Serializer):
    """장바구니 일괄 추가 요청"""

    items = drf_serializers.ListField(child=CartBulkAddItemSerializer(), help_text="추가할 상품 목록")


class CartBulkAddResponseSerializer(drf_serializers.Serializer):
    """장바구니 일괄 추가 응답"""

    message = drf_serializers.CharField()
    added_items = CartItemSerializer(many=True)
    errors = drf_serializers.ListField(required=False)
    error_count = drf_serializers.IntegerField(required=False)


class CartStockCheckResponseSerializer(drf_serializers.Serializer):
    """재고 확인 응답"""

    has_issues = drf_serializers.BooleanField()
    issues = drf_serializers.ListField(required=False)
    message = drf_serializers.CharField()


@extend_schema_view(
    retrieve=extend_schema(
        summary="장바구니 전체 정보를 조회한다.",
        description="""처리 내용:
- 현재 사용자의 장바구니 전체 정보를 반환한다.
- 아이템 목록과 총 금액을 포함한다.""",
        tags=["Cart"],
    ),
)
class CartViewSet(viewsets.GenericViewSet):
    """
    장바구니 관리 ViewSet

    회원/비회원 모두 사용 가능합니다.
    - 회원: user 기반 장바구니
    - 비회원: session_key 기반 장바구니

    엔드포인트:
    - GET    /api/cart/            - 장바구니 조회
    - GET    /api/cart/summary/    - 장바구니 요약
    - POST   /api/cart/add_item/   - 상품 추가
    - GET    /api/cart/items/      - 아이템 목록
    - PATCH  /api/cart/items/{id}/ - 수량 변경
    - DELETE /api/cart/items/{id}/ - 아이템 삭제
    - POST   /api/cart/clear/      - 장바구니 비우기
    - POST   /api/cart/bulk_add/   - 일괄 추가
    - GET    /api/cart/check_stock/ - 재고 확인
    """

    permission_classes = [permissions.AllowAny]
    queryset = Cart.objects.none()

    def get_serializer_class(self) -> type[BaseSerializer]:
        """액션에 따라 적절한 Serializer 반환"""
        action_serializer_map = {
            "retrieve": CartSerializer,
            "summary": SimpleCartSerializer,
            "add_item": CartItemCreateSerializer,
            "update_item": CartItemUpdateSerializer,
            "clear": CartClearSerializer,
        }
        return action_serializer_map.get(self.action, CartSerializer)

    def _get_cart(self, product_cards: bool = False) -> Cart:
        """
        현재 사용자/세션의 활성 장바구니를 가져오거나 생성

        서비스 레이어에 위임합니다.
        product_cards: 항목마다 상품 카드를 그리는 응답이면 True (N+1 방지)
        """
        user = self.request.user if self.request.user.is_authenticated else None
        return CartService.get_or_create_cart(user=user, request=self.request, product_cards=product_cards)

    @extend_schema(
        responses={200: CartSerializer},
        summary="장바구니 전체 정보를 조회한다.",
        description="""처리 내용:
- 현재 사용자의 장바구니 전체 정보를 반환한다.
- 아이템 목록과 총 금액을 포함한다.
- 재고/가격 변경 시 경고 정보를 포함한다.""",
        tags=["Cart"],
    )
    def retrieve(self, request: Request) -> Response:
        """장바구니 전체 정보 조회"""
        cart = self._get_cart(product_cards=True)

        # 자동으로 재고/가격 변경 확인
        from dataclasses import asdict

        stock_issues = CartService.check_stock(cart)
        price_changes = CartService.check_price_changes(cart)

        serializer = self.get_serializer(cart)
        response_data = serializer.data

        # 경고 정보 추가
        if stock_issues or price_changes:
            response_data["warnings"] = {
                "stock_issues": [asdict(issue) for issue in stock_issues],
                "price_changes": [asdict(change) for change in price_changes],
                "has_issues": True,
            }
        else:
            response_data["warnings"] = {"has_issues": False}

        return Response(response_data)

    @extend_schema(
        responses={200: SimpleCartSerializer},
        summary="장바구니 요약 정보를 조회한다.",
        description="""처리 내용:
- 헤더/사이드바용 간단한 장바구니 정보를 반환한다.
- 아이템 개수와 총 금액을 포함한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        """장바구니 요약 정보 조회"""
        cart = self._get_cart()

        serializer = self.get_serializer(cart)
        return Response(serializer.data)

    @extend_schema(
        request=CartItemCreateSerializer,
        responses={
            201: CartAddItemResponseSerializer,
            400: CartErrorResponseSerializer,
        },
        summary="장바구니에 상품을 추가한다.",
        description="""처리 내용:
- 장바구니에 상품을 추가한다.
- 이미 담긴 상품이면 수량만 증가한다.
- 회원/비회원 모두 사용 가능하다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["post"])
    def add_item(self, request: Request) -> Response:
        """장바구니에 상품 추가"""
        cart = self._get_cart()

        # Serializer로 입력 검증
        serializer = self.get_serializer(
            data=request.data,
            context={"request": request, "cart": cart},
        )
        serializer.is_valid(raise_exception=True)

        # 서비스 레이어에 비즈니스 로직 위임
        product_id = serializer.validated_data["product_id"]
        quantity = serializer.validated_data.get("quantity", 1)

        try:
            cart_item = CartService.add_item(
                cart=cart,
                product_id=product_id,
                quantity=quantity,
            )
            return Response(
                {
                    "message": "장바구니에 추가되었습니다.",
                    "item": CartItemSerializer(cart_item, context={"request": request}).data,
                },
                status=status.HTTP_201_CREATED,
            )
        except CartServiceError as e:
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        responses={200: CartItemSerializer(many=True)},
        summary="장바구니 아이템 목록을 조회한다.",
        description="""처리 내용:
- 장바구니의 아이템 목록을 반환한다.
- 최근 추가된 순서로 정렬한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["get"])
    def items(self, request: Request) -> Response:
        """장바구니 아이템 목록 조회"""
        cart = self._get_cart(product_cards=True)

        # 최근 추가 순으로 이미 읽어 둔 항목 (상품 카드 포함)
        serializer = CartItemSerializer(cart.items.all(), many=True, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        request=CartItemUpdateSerializer,
        responses={
            200: CartUpdateItemResponseSerializer,
            204: None,
            400: CartErrorResponseSerializer,
            404: CartErrorResponseSerializer,
        },
        summary="장바구니 아이템 수량을 변경한다.",
        description="""처리 내용:
- 아이템의 수량을 변경한다.
- 수량이 0이면 아이템을 삭제한다.""",
        tags=["Cart"],
    )
    @action(detail=True, methods=["patch"], url_path="items")
    def update_item(self, request: Request, pk: int | None = None) -> Response:
        """장바구니 아이템 수량 변경"""
        cart = self._get_cart()

        # 입력 검증
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quantity = serializer.validated_data.get("quantity")

        try:
            cart_item = CartService.update_item_quantity(
                cart=cart,
                item_id=pk,
                quantity=quantity,
            )

            if cart_item is None:
                # 수량이 0이면 삭제됨
                return Response(
                    {"message": "장바구니에서 삭제되었습니다."},
                    status=status.HTTP_204_NO_CONTENT,
                )

            return Response(
                {
                    "message": "수량이 변경되었습니다.",
                    "item": CartItemSerializer(cart_item, context={"request": request}).data,
                }
            )
        except CartServiceError as e:
            if e.code == "ITEM_NOT_FOUND":
                raise NotFound(e.message)
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        responses={
            204: CartMessageResponseSerializer,
            404: CartErrorResponseSerializer,
        },
        summary="장바구니 아이템을 삭제한다.",
        description="""처리 내용:
- 장바구니에서 특정 상품을 완전히 제거한다.""",
        tags=["Cart"],
    )
    @action(detail=True, methods=["delete"], url_path="items")
    def delete_item(self, request: Request, pk: int | None = None) -> Response:
        """장바구니 아이템 삭제"""
        cart = self._get_cart()

        try:
            product_name = CartService.remove_item(cart=cart, item_id=pk)
            return Response(
                {"message": f"{product_name}이(가) 장바구니에서 삭제되었습니다."},
                status=status.HTTP_204_NO_CONTENT,
            )
        except CartServiceError as e:
            if e.code == "ITEM_NOT_FOUND":
                raise NotFound(e.message)
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        request=CartClearSerializer,
        responses={
            200: CartMessageResponseSerializer,
            400: CartErrorResponseSerializer,
        },
        summary="장바구니를 비운다.",
        description="""처리 내용:
- 장바구니의 모든 아이템을 삭제한다.
- 실수 방지를 위해 confirm=true가 필수이다.
- Idempotent: 이미 비어있는 장바구니도 200 OK를 반환한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["post"])
    def clear(self, request: Request) -> Response:
        """장바구니 비우기 (Idempotent)"""
        cart = self._get_cart()

        # 확인 검증
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item_count = CartService.clear_cart(cart=cart)

        if item_count == 0:
            return Response(
                {"message": "장바구니가 이미 비어있습니다."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"message": f"{item_count}개의 상품이 장바구니에서 삭제되었습니다."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=CartBulkAddRequestSerializer,
        responses={
            201: CartBulkAddResponseSerializer,
            207: CartBulkAddResponseSerializer,
            400: CartErrorResponseSerializer,
        },
        summary="여러 상품을 한 번에 추가한다.",
        description="""처리 내용:
- 여러 상품을 한 번에 장바구니에 추가한다.
- N+1 쿼리 최적화를 적용한다.
- 일부 실패 시 207 Multi-Status를 반환한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["post"])
    def bulk_add(self, request: Request) -> Response:
        """여러 상품을 한 번에 장바구니에 추가"""
        cart = self._get_cart()
        items_data = request.data.get("items", [])

        try:
            result = CartService.bulk_add_items(cart=cart, items_data=items_data)

            # 추가된 항목을 상품 카드 모양으로 다시 읽는다 (항목마다 상품 쿼리가 늘지 않게, 순서 유지)
            user_id = request.user.id if request.user.is_authenticated else None
            loaded = CartItem.objects.prefetch_related(prefetch_product_cards("product", user_id)).in_bulk(
                [item.pk for item in result.added_items]
            )
            added_items = [loaded[item.pk] for item in result.added_items]

            response_data = {
                "message": f"{result.success_count}개의 상품이 추가되었습니다.",
                "added_items": CartItemSerializer(added_items, many=True, context={"request": request}).data,
            }

            if result.errors:
                response_data["errors"] = result.errors
                response_data["error_count"] = result.error_count
                return Response(response_data, status=status.HTTP_207_MULTI_STATUS)

            return Response(response_data, status=status.HTTP_201_CREATED)

        except CartServiceError as e:
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        responses={
            200: CartStockCheckResponseSerializer,
        },
        summary="장바구니 상품 재고를 확인한다.",
        description="""처리 내용:
- 장바구니 상품들의 재고를 확인한다.
- 판매 중단, 품절, 재고 부족 상품을 반환한다.
- 주문 직전 재고 확인 시 사용한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["get"])
    def check_stock(self, request: Request) -> Response:
        """장바구니 상품들의 재고 확인"""
        cart = self._get_cart()

        issues = CartService.check_stock(cart=cart)

        if issues:
            # StockIssue dataclass를 dict로 변환
            issues_data = [
                {
                    "item_id": issue.item_id,
                    "product_id": issue.product_id,
                    "product_name": issue.product_name,
                    "issue": CartService.get_stock_issue_message(issue.issue_type),
                    "requested": issue.requested,
                    "available": issue.available,
                }
                for issue in issues
            ]
            return Response(
                {
                    "has_issues": True,
                    "issues": issues_data,
                    "message": "일부 상품의 재고가 부족합니다.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {"has_issues": False, "message": "모든 상품을 구매할 수 있습니다."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={
            200: CartMessageResponseSerializer,
        },
        summary="장바구니에서 구매 불가능한 상품을 자동 정리한다.",
        description="""처리 내용:
- 판매 중단, 품절 상품을 자동으로 제거한다.
- 재고 부족 상품은 재고에 맞게 수량을 조정한다.
- 정리된 상품 목록을 반환한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["post"])
    def cleanup(self, request: Request) -> Response:
        """구매 불가능한 상품 자동 정리"""
        cart = self._get_cart()

        result = CartService.cleanup_unavailable_items(cart=cart)

        if result["removed_count"] == 0 and result["updated_count"] == 0:
            return Response(
                {"message": "정리할 상품이 없습니다."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "message": f"{result['removed_count']}개 상품이 제거되고, "
                f"{result['updated_count']}개 상품의 수량이 조정되었습니다.",
                "removed": result["removed"],
                "updated": result["updated"],
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={
            200: CartMessageResponseSerializer,
        },
        summary="장바구니 상품 가격을 현재 가격으로 업데이트한다.",
        description="""처리 내용:
- 가격이 변경된 상품들의 담은 시점 가격을 현재 가격으로 갱신한다.
- 사용자가 가격 변경을 확인하고 동의한 후 호출한다.""",
        tags=["Cart"],
    )
    @action(detail=False, methods=["post"])
    def update_prices(self, request: Request) -> Response:
        """장바구니 상품 가격을 현재 가격으로 업데이트"""
        cart = self._get_cart()

        updated_count = CartService.update_item_prices(cart=cart)

        if updated_count == 0:
            return Response(
                {"message": "업데이트할 가격 변경 상품이 없습니다."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"message": f"{updated_count}개 상품의 가격이 현재 가격으로 업데이트되었습니다."},
            status=status.HTTP_200_OK,
        )


# ===== CartItemViewSet 응답 Serializers =====


class CartItemListResponseSerializer(drf_serializers.Serializer):
    """장바구니 아이템 목록 응답"""

    pass  # CartItemSerializer(many=True)와 동일


class CartItemCreateResponseSerializer(drf_serializers.Serializer):
    """장바구니 아이템 생성 응답"""

    id = drf_serializers.IntegerField()
    product = drf_serializers.DictField()
    quantity = drf_serializers.IntegerField()


class CartItemUpdateResponseSerializer(drf_serializers.Serializer):
    """장바구니 아이템 수정 응답"""

    id = drf_serializers.IntegerField()
    product = drf_serializers.DictField()
    quantity = drf_serializers.IntegerField()


@extend_schema_view(
    list=extend_schema(
        responses={200: CartItemListResponseSerializer(many=True)},
        summary="장바구니 아이템 목록을 조회한다.",
        description="""처리 내용:
- 현재 사용자/세션의 장바구니 아이템 목록을 반환한다.""",
        tags=["Cart"],
    ),
    create=extend_schema(
        request=CartItemCreateSerializer,
        responses={
            201: CartItemCreateResponseSerializer,
            400: CartErrorResponseSerializer,
        },
        summary="장바구니에 아이템을 추가한다.",
        description="""처리 내용:
- 장바구니에 상품을 추가한다.
- 회원/비회원 모두 사용 가능하다.""",
        tags=["Cart"],
    ),
    update=extend_schema(
        request=CartItemUpdateSerializer,
        responses={
            200: CartItemUpdateResponseSerializer,
            204: None,
            400: CartErrorResponseSerializer,
            404: CartErrorResponseSerializer,
        },
        summary="아이템 수량을 변경한다.",
        description="""처리 내용:
- 장바구니 아이템의 수량을 변경한다.
- 수량을 0으로 설정하면 삭제한다.""",
        tags=["Cart"],
    ),
    destroy=extend_schema(
        responses={
            204: None,
            404: CartErrorResponseSerializer,
        },
        summary="아이템을 삭제한다.",
        description="""처리 내용:
- 장바구니에서 아이템을 삭제한다.""",
        tags=["Cart"],
    ),
)
class CartItemViewSet(viewsets.GenericViewSet):
    """
    장바구니 아이템 개별 관리 ViewSet

    RESTful하게 아이템을 관리하고 싶을 때 사용합니다.
    CartViewSet의 action들과 동일한 기능을 제공합니다.
    회원/비회원 모두 사용 가능합니다.
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = CartItemSerializer

    def _get_cart(self) -> Cart:
        """현재 사용자/세션의 활성 장바구니를 가져오거나 생성"""
        user = self.request.user if self.request.user.is_authenticated else None
        return CartService.get_or_create_cart(user=user, request=self.request)

    def get_queryset(self) -> Any:
        """
        현재 사용자/세션의 장바구니 아이템만 반환

        회원: user 기반 장바구니
        비회원: session_key 기반 장바구니
        """
        try:
            if self.request.user.is_authenticated:
                # 회원 장바구니
                cart = Cart.objects.get(user=self.request.user, is_active=True)
            else:
                # 비회원 장바구니
                session_key = self.request.session.session_key
                if not session_key:
                    return CartItem.objects.none()
                cart = Cart.objects.get(session_key=session_key, is_active=True)

            user_id = self.request.user.id if self.request.user.is_authenticated else None
            return cart.items.prefetch_related(prefetch_product_cards("product", user_id)).order_by("-added_at")
        except Cart.DoesNotExist:
            return CartItem.objects.none()

    def list(self, request: Request) -> Response:
        """장바구니 아이템 목록"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request: Request) -> Response:
        """장바구니에 아이템 추가"""
        cart = self._get_cart()

        serializer = CartItemCreateSerializer(
            data=request.data,
            context={"request": request, "cart": cart},
        )
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data["product_id"]
        quantity = serializer.validated_data.get("quantity", 1)

        try:
            cart_item = CartService.add_item(
                cart=cart,
                product_id=product_id,
                quantity=quantity,
            )
            return Response(
                CartItemSerializer(cart_item, context={"request": request}).data,
                status=status.HTTP_201_CREATED,
            )
        except CartServiceError as e:
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def update(self, request: Request, pk: int | None = None) -> Response:
        """아이템 수량 변경"""
        cart = self._get_cart()

        serializer = CartItemUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        quantity = serializer.validated_data.get("quantity", 1)

        try:
            cart_item = CartService.update_item_quantity(
                cart=cart,
                item_id=pk,
                quantity=quantity,
            )

            if cart_item is None:
                return Response(status=status.HTTP_204_NO_CONTENT)

            return Response(CartItemSerializer(cart_item, context={"request": request}).data)
        except CartServiceError as e:
            if e.code == "ITEM_NOT_FOUND":
                raise NotFound(e.message)
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request: Request, pk: int | None = None) -> Response:
        """아이템 삭제"""
        cart = self._get_cart()

        try:
            CartService.remove_item(cart=cart, item_id=pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except CartServiceError as e:
            if e.code == "ITEM_NOT_FOUND":
                raise NotFound(e.message)
            return Response(
                {"error": e.message, "code": e.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
