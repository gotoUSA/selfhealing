from __future__ import annotations

from typing import Any

from rest_framework import serializers

# 모델 import
from ..models.cart import Cart, CartItem

# 다른 Serializer import
from .product_serializers import ProductListSerializer


class CartItemSerializer(serializers.ModelSerializer):
    """
    장바구니 아이템 조회용 Serializer

    장바구니에 담긴 개별 상품 정보를 표시할 때 사용합니다.
    상품의 상세 정보와 함께 소계 금액, 재고 상태 등을 제공합니다.
    """

    # 상품 정보를 포함 (ProductListSerializer 재사용)
    product = ProductListSerializer(read_only=True)

    # 상품 기본 정보 (빠른 참조용)
    product_id = serializers.IntegerField(source="product.id", read_only=True, help_text="상품 ID")
    product_name = serializers.CharField(source="product.name", read_only=True, help_text="상품명")
    product_price = serializers.DecimalField(
        source="product.price",
        max_digits=10,
        decimal_places=0,
        read_only=True,
        help_text="상품 단가",
    )

    # 가격 스냅샷 관련 필드
    price_at_add = serializers.DecimalField(
        max_digits=10,
        decimal_places=0,
        read_only=True,
        help_text="담은 시점 가격",
    )
    is_price_changed = serializers.BooleanField(read_only=True, help_text="가격 변경 여부")
    price_difference = serializers.DecimalField(
        max_digits=10,
        decimal_places=0,
        read_only=True,
        help_text="가격 차이 (양수면 인상, 음수면 인하)",
    )
    current_price = serializers.DecimalField(
        source="product.price",
        max_digits=10,
        decimal_places=0,
        read_only=True,
        help_text="현재 가격",
    )

    # 계산 필드들
    subtotal = serializers.SerializerMethodField(help_text="소계 (단가 x 수량)")
    is_available = serializers.SerializerMethodField(help_text="구매 가능 여부 (재고 확인)")
    available_stock = serializers.IntegerField(source="product.stock", read_only=True, help_text="현재 재고 수량")

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product",  # 상품 전체 정보
            "product_id",  # 상품 ID만
            "product_name",  # 상품명만
            "product_price",  # 단가
            "quantity",  # 수량
            "subtotal",  # 소계
            "is_available",  # 구매 가능 여부
            "available_stock",  # 재고
            "price_at_add",  # 담은 시점 가격
            "is_price_changed",  # 가격 변경 여부
            "price_difference",  # 가격 차이
            "current_price",  # 현재 가격
            "added_at",  # 추가일시
            "updated_at",  # 수정일시
        ]
        read_only_fields = ["added_at", "updated_at"]

    def get_subtotal(self, obj: CartItem) -> str:
        """
        소계 계산 (모델의 property 활용)

        Returns:
            str: 소계 금액 (문자열로 변환하여 정확한 값 전달)
        """
        return str(obj.subtotal)

    def get_is_available(self, obj: CartItem) -> bool:
        """
        구매 가능 여부 확인

        상품이 활성화되어 있고 재고가 충분한지 확인합니다.

        Returns:
            bool: 구매 가능하면 True
        """
        return obj.is_available()


class CartItemCreateSerializer(serializers.ModelSerializer):
    """
    장바구니에 상품 추가용 Serializer

    POST 요청으로 장바구니에 새 상품을 추가할 때 사용합니다.
    비즈니스 로직(재고 검증, 동시성 처리 등)은 CartService에서 담당합니다.
    """

    # 상품 ID로 받기 (필수)
    product_id = serializers.IntegerField(write_only=True, help_text="추가할 상품 ID")

    # 수량 (기본값 1)
    quantity = serializers.IntegerField(
        default=1,
        min_value=1,
        max_value=999,  # 최대 구매 수량 제한
        help_text="추가할 수량 (기본값: 1)",
    )

    class Meta:
        model = CartItem
        fields = ["product_id", "quantity"]

    def to_representation(self, instance: CartItem) -> dict[str, Any]:
        """
        응답 시 CartItemSerializer 형식으로 변환

        생성 후 전체 정보를 반환합니다.
        """
        return CartItemSerializer(instance, context=self.context).data


class CartItemUpdateSerializer(serializers.ModelSerializer):
    """
    장바구니 아이템 수량 변경용 Serializer

    PUT/PATCH 요청으로 수량을 변경할 때 사용합니다.
    비즈니스 로직(재고 검증, 동시성 처리 등)은 CartService에서 담당합니다.
    수량이 0이 되면 자동으로 삭제됩니다.
    """

    quantity = serializers.IntegerField(
        min_value=0,  # 0 허용 (삭제 의미)
        max_value=999,
        help_text="변경할 수량 (0이면 삭제)",
    )

    class Meta:
        model = CartItem
        fields = ["quantity"]


class CartSerializer(serializers.ModelSerializer):
    """
    장바구니 전체 정보 조회용 Serializer

    장바구니의 모든 아이템과 총액, 총 수량 등을 포함합니다.
    주로 장바구니 페이지에서 사용됩니다.
    """

    # 장바구니 아이템들
    items = CartItemSerializer(many=True, read_only=True)

    # 사용자 정보 (간단히)
    user_id = serializers.IntegerField(source="user.id", read_only=True, help_text="사용자 ID")
    user_username = serializers.CharField(source="user.username", read_only=True, help_text="사용자명")

    # 집계 정보
    total_amount = serializers.SerializerMethodField(help_text="총 금액")
    total_quantity = serializers.SerializerMethodField(help_text="총 수량")
    item_count = serializers.SerializerMethodField(help_text="상품 종류 수")

    # 구매 가능 여부
    is_all_available = serializers.SerializerMethodField(help_text="모든 상품 구매 가능 여부")
    unavailable_items = serializers.SerializerMethodField(help_text="구매 불가능한 상품 목록")

    class Meta:
        model = Cart
        fields = [
            "id",
            "user_id",
            "user_username",
            "items",  # 장바구니 아이템들
            "total_amount",  # 총액
            "total_quantity",  # 총 수량
            "item_count",  # 상품 종류 수
            "is_active",
            "is_all_available",  # 모두 구매 가능한지
            "unavailable_items",  # 구매 불가 아이템
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "is_active"]

    def get_total_amount(self, obj: Cart) -> str:
        """
        총 금액 계산 (prefetch된 items로 Python 계산)

        DB aggregate 대신 이미 로드된 데이터를 활용하여 성능 최적화
        """
        total = sum(item.subtotal for item in obj.items.all())
        return str(total)

    def get_total_quantity(self, obj: Cart) -> int:
        """
        총 수량 계산 (prefetch된 items로 Python 계산)

        DB aggregate 대신 이미 로드된 데이터를 활용하여 성능 최적화
        """
        return sum(item.quantity for item in obj.items.all())

    def get_item_count(self, obj: Cart) -> int:
        """
        장바구니에 담긴 상품 종류 수
        """
        return obj.items.count()

    def get_is_all_available(self, obj: Cart) -> bool:
        """
        모든 상품이 구매 가능한지 확인

        get_unavailable_items 결과를 재사용하여 중복 순회 방지
        """
        return len(self.get_unavailable_items(obj)) == 0

    def get_unavailable_items(self, obj: Cart) -> list[dict[str, Any]]:
        """
        구매 불가능한 상품 목록 반환

        재고 부족이나 비활성 상품을 찾아서 반환합니다.
        """
        unavailable = []

        for item in obj.items.all():
            if not item.is_available():
                reason = ""
                if not item.product.is_active:
                    reason = "판매 중단"
                elif item.product.stock == 0:
                    reason = "품절"
                elif item.product.stock < item.quantity:
                    reason = f"재고 부족 (현재 {item.product.stock}개)"

                unavailable.append(
                    {
                        "item_id": item.id,
                        "product_id": item.product.id,
                        "product_name": item.product.name,
                        "requested_quantity": item.quantity,
                        "available_stock": item.product.stock,
                        "reason": reason,
                    }
                )
        return unavailable


class SimpleCartSerializer(serializers.ModelSerializer):
    """
    간단한 장바구니 정보용 Serializer

    헤더나 사이드바에서 장바구니 요약 정보를 보여줄 때 사용합니다.
    """

    total_amount = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "total_amount",
            "total_quantity",
            "item_count",
        ]

    def get_total_amount(self, obj: Cart) -> str:
        """
        총 금액 (prefetch된 items로 Python 계산)
        """
        total = sum(item.subtotal for item in obj.items.all())
        return str(total)

    def get_total_quantity(self, obj: Cart) -> int:
        """
        총 수량 (prefetch된 items로 Python 계산)
        """
        return sum(item.quantity for item in obj.items.all())

    def get_item_count(self, obj: Cart) -> int:
        """
        상품 종류 수 (prefetch된 데이터 활용)
        """
        return len(obj.items.all())


class CartClearSerializer(serializers.Serializer):
    """
    장바구니 비우기 확인용 Serializer

    안전을 위해 확인 메시지를 요구합니다.
    """

    confirm = serializers.BooleanField(required=True, help_text="장바구니를 비우려면 true를 전송하세요.")

    def validate_confirm(self, value: bool) -> bool:  # 특정 필드 검증
        """확인 값 검증"""
        if not value:
            raise serializers.ValidationError("장바구니 비우기를 확인해주세요.")
        return value
