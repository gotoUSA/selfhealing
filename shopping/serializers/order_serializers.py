from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import F, QuerySet

from rest_framework import serializers

from ..models.cart import Cart
from ..models.order import Order, OrderItem
from ..models.point import PointHistory
from ..models.product import Product
from ..services.order_service import OrderService, OrderServiceError
from .product_serializers import ProductListSerializer


class TotalShippingFeeMixin:
    """배송비 계산 공통 Mixin - 코드 중복 제거"""

    def get_total_shipping_fee(self, obj: Order) -> str:
        """전체 배송비 반환 (기본 배송비 + 추가 배송비)"""
        return str(obj.get_total_shipping_fee())


class OrderItemSerializer(serializers.ModelSerializer):
    """주문 상품 조회용 Serializer"""

    product_info = ProductListSerializer(source="product", read_only=True)
    subtotal = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_info",
            "product_name",  # 주문 당시 상품명
            "quantity",
            "price",  # 주문 당시 가격
            "subtotal",
        ]
        read_only_fields = ["product_name", "price"]

    def get_subtotal(self, obj: OrderItem) -> str:
        return str(obj.get_subtotal())


class OrderListSerializer(TotalShippingFeeMixin, serializers.ModelSerializer):
    """
    주문 목록 조회용 Serializer

    성능 최적화:
    - item_count: queryset에서 annotate로 계산됨 (N+1 쿼리 방지)
    - user_username: queryset에서 select_related("user")로 최적화
    """

    user_username = serializers.CharField(source="user.username", read_only=True)
    item_count = serializers.IntegerField(read_only=True)  # annotate에서 제공
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    total_shipping_fee = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "user_username",
            "status",
            "status_display",
            "total_amount",
            "shipping_fee",
            "total_shipping_fee",
            "used_points",
            "final_amount",
            "item_count",
            "created_at",
        ]


class OrderDetailSerializer(TotalShippingFeeMixin, serializers.ModelSerializer):
    """
    주문 상세 조회용 Serializer

    보안 고려사항:
    - user FK는 read_only이며, ViewSet의 get_queryset에서 권한 필터링됨
    - 일반 사용자는 본인 주문만 조회 가능 (ViewSet 레벨 제어)
    - 민감 정보(배송지, 연락처)는 본인/관리자만 접근 가능
    """

    order_items = OrderItemSerializer(many=True, read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    total_shipping_fee = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "user",
            "user_username",
            "status",
            "status_display",
            "order_items",
            "total_amount",
            "shipping_fee",
            "additional_shipping_fee",
            "is_free_shipping",
            "total_shipping_fee",
            "used_points",
            "final_amount",
            "earned_points",
            "shipping_name",
            "shipping_phone",
            "shipping_postal_code",
            "shipping_address",
            "shipping_address_detail",
            "order_memo",
            "payment_method",
            "can_cancel",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user"]  # user FK는 읽기 전용


class OrderCreateSerializer(serializers.ModelSerializer):
    """
    주문 생성용 Serializer (장바구니 -> 주문 변환)

    역할: 입력 데이터 형식 검증 및 OrderService 호출
    - 입력 필드 형식 검증 (타입, 필수 여부 등)
    - 장바구니 존재 및 항목 검증 (데이터 검증)
    - 비즈니스 로직(포인트 규칙, 배송비 계산)은 OrderService에서 처리
    """

    use_points = serializers.IntegerField(
        default=0,
        min_value=0,
        required=False,
        help_text="사용할 포인트 (최소 100포인트)",
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "shipping_name",
            "shipping_phone",
            "shipping_postal_code",
            "shipping_address",
            "shipping_address_detail",
            "order_memo",
            "use_points",
        ]
        read_only_fields = ["id"]

    def _validate_cart_items(self, cart: Cart) -> QuerySet:
        """
        장바구니 항목 검증 (별도 메서드)

        검증 항목:
        - 비활성 상품 (is_active=False)
        - 품절 상품 (stock=0)

        Note: 재고 부족 검증은 OrderService에서 select_for_update로 락을 걸고 처리

        Args:
            cart: 검증할 장바구니 객체

        Returns:
            QuerySet: 검증된 장바구니 항목들

        Raises:
            ValidationError: 검증 실패 시 (모든 에러를 리스트로 반환)
        """
        cart_items = cart.items.select_related("product")

        if not cart_items.exists():
            raise serializers.ValidationError("장바구니가 비어있습니다.")

        # 검증 에러 목록
        errors = []

        for item in cart_items:
            product = item.product

            # 1. 비활성 상품 체크
            if not product.is_active:
                errors.append(f"'{product.name}'은(는) 현재 판매하지 않는 상품입니다.")
                continue

            # 2. 품절 체크
            if product.stock == 0:
                errors.append(f"'{product.name}'은(는) 품절되었습니다.")
                continue

            # 3. 재고 부족 체크 (동기 검증 - 주문 수락 전 확인)
            if product.stock < item.quantity:
                errors.append(
                    f"'{product.name}'의 재고가 부족합니다. "
                    f"(요청: {item.quantity}개, 재고: {product.stock}개)"
                )

        # 에러가 있으면 모두 반환
        if errors:
            raise serializers.ValidationError({"cart_items": errors})

        return cart_items

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """
        입력 데이터 검증 (형식 및 데이터 존재 여부만)

        비즈니스 로직 검증(포인트 규칙, 배송비 계산 등)은 OrderService에서 처리
        """
        user = self.context["request"].user

        # 이메일 인증 확인
        if not user.is_email_verified:
            raise serializers.ValidationError("이메일 인증이 필요합니다. 먼저 이메일을 인증해주세요.")

        # 장바구니 확인
        cart = Cart.objects.filter(user=user, is_active=True).first()
        if not cart or not cart.items.exists():
            raise serializers.ValidationError("장바구니가 비어있습니다.")

        # 장바구니 항목 검증 (비활성 상품, 품절, 재고 부족)
        self._validate_cart_items(cart)

        # 검증된 장바구니를 인스턴스 변수에 저장
        self.cart = cart
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Order:
        """
        주문 생성 (OrderService 위임)

        비즈니스 로직은 OrderService에서 처리
        """
        user = self.context["request"].user
        cart = self.cart
        use_points = validated_data.pop("use_points", 0)

        try:
            # OrderService를 통한 주문 생성
            order = OrderService.create_order_from_cart(
                user=user,
                cart=cart,
                shipping_name=validated_data["shipping_name"],
                shipping_phone=validated_data["shipping_phone"],
                shipping_postal_code=validated_data["shipping_postal_code"],
                shipping_address=validated_data["shipping_address"],
                shipping_address_detail=validated_data.get("shipping_address_detail", ""),
                order_memo=validated_data.get("order_memo", ""),
                use_points=use_points,
            )
            return order
        except OrderServiceError as e:
            raise serializers.ValidationError(str(e))

    def create_hybrid(self, validated_data: dict[str, Any]) -> tuple[Order, str]:
        """
        주문 생성 (하이브리드 방식 - 비동기 처리)

        Order 레코드만 생성하고 무거운 작업은 비동기로 처리
        """
        user = self.context["request"].user
        cart = self.cart
        use_points = validated_data.pop("use_points", 0)

        try:
            # OrderService의 하이브리드 메서드 호출
            order, task_id = OrderService.create_order_hybrid(
                user=user,
                cart=cart,
                shipping_name=validated_data["shipping_name"],
                shipping_phone=validated_data["shipping_phone"],
                shipping_postal_code=validated_data["shipping_postal_code"],
                shipping_address=validated_data["shipping_address"],
                shipping_address_detail=validated_data.get("shipping_address_detail", ""),
                order_memo=validated_data.get("order_memo", ""),
                use_points=use_points,
            )
            return order, task_id
        except OrderServiceError as e:
            raise serializers.ValidationError(str(e))


    def to_representation(self, instance: Order) -> dict[str, Any]:
        """
        생성 응답시 OrderDetailSerializer 사용
        배송비 등 모든 정보를 응답에 포함
        """
        return OrderDetailSerializer(instance, context=self.context).data
