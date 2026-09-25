from __future__ import annotations


from rest_framework import serializers

from shopping.models.product import Product


class WishlistProductSerializer(serializers.ModelSerializer):
    """찜 목록에 표시할 상품 정보 Serializer"""

    # 추가 필드들
    is_available = serializers.SerializerMethodField()
    wishlist_count = serializers.SerializerMethodField()
    discount_rate = serializers.SerializerMethodField()
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "price",
            "compare_price",
            "discount_rate",
            "primary_image",
            "stock",
            "is_available",
            "wishlist_count",
            "created_at",
        ]

    def get_is_available(self, obj: Product) -> bool:
        """구매 가능 여부"""
        return obj.stock > 0 and obj.is_active

    def get_wishlist_count(self, obj: Product) -> int:
        """이 상품을 찜한 사용자 수 (찜 목록 쿼리셋이 서브쿼리로 미리 계산)"""
        if hasattr(obj, "wishlist_cnt"):
            return obj.wishlist_cnt
        return obj.wished_by_users.count()

    def get_discount_rate(self, obj: Product) -> float:
        """할인율 계산"""
        if obj.compare_price and obj.compare_price > obj.price:
            rate = ((obj.compare_price - obj.price) / obj.compare_price) * 100
            return round(rate, 1)
        return 0

    def get_primary_image(self, obj: Product) -> str | None:
        """
        대표 이미지 URL

        prefetch 된 이미지 안에서 고른다 — images.filter(...) 는 prefetch 를 건너뛰고 상품마다 쿼리를 보낸다.
        """
        images = list(obj.images.all())
        chosen = next((image for image in images if image.is_primary), None)
        if chosen is None and images:
            # 대표 이미지가 없으면 첫 번째 이미지
            chosen = images[0]
        if chosen is None:
            return None
        return chosen.image.url if hasattr(chosen.image, "url") else None


class WishlistToggleSerializer(serializers.Serializer):
    """찜하기 토글 요청 Serializer"""

    product_id = serializers.IntegerField(required=True, help_text="찜하기/취소할 상품 ID")

    def validate_product_id(self, value: int) -> int:
        """상품 존재 여부 확인"""
        if not Product.objects.filter(id=value).exists():
            raise serializers.ValidationError("존재하지 않는 상품입니다.")
        return value


class WishlistBulkAddSerializer(serializers.Serializer):
    """여러 상품 한번에 찜하기 Serializer"""

    product_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        allow_empty=False,
        help_text="찜할 상품 ID 리스트",
    )

    def validate_product_ids(self, value: list[int]) -> list[int]:
        """상품들 존재 여부 확인"""
        if not value:
            raise serializers.ValidationError("상품 ID를 하나 이상 입력해주세요.")

        # 중복 제거
        unique_ids = list(set(value))

        # 존재하는 상품인지 확인
        existing_ids = Product.objects.filter(id__in=unique_ids).values_list("id", flat=True)

        not_found = set(unique_ids) - set(existing_ids)
        if not_found:
            raise serializers.ValidationError(f"다음 상품을 찾을 수 없습니다: {list(not_found)}")

        return unique_ids


class WishlistStatusSerializer(serializers.Serializer):
    """상품의 찜 상태 확인 Serializer"""

    product_id = serializers.IntegerField(required=True)
    is_wished = serializers.BooleanField(read_only=True)
    wishlist_count = serializers.IntegerField(read_only=True)


class WishlistStatsSerializer(serializers.Serializer):
    """사용자 찜 목록 통계 Serializer"""

    total_count = serializers.IntegerField(read_only=True)
    available_count = serializers.IntegerField(read_only=True)
    out_of_stock_count = serializers.IntegerField(read_only=True)
    on_sale_count = serializers.IntegerField(read_only=True)
    total_price = serializers.DecimalField(max_digits=10, decimal_places=0, read_only=True)
    total_sale_price = serializers.DecimalField(max_digits=10, decimal_places=0, read_only=True)
    total_discount = serializers.DecimalField(max_digits=10, decimal_places=0, read_only=True)
