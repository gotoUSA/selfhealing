from __future__ import annotations

import decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Avg

from rest_framework import serializers

from ..models.product import Category, Product, ProductImage, ProductReview

User = get_user_model()


def _rounded_rating(value: Any) -> float:
    """평균 평점 - null을 0.0으로 변환하고 소수점 1자리로 반올림"""
    if value is None:
        return 0.0
    return round(float(value), 1)


class ProductListSerializer(serializers.ModelSerializer):
    """
    상품 목록 조회용 Serializer

    상품 목록 페이지에서 보여줄 최소한의 정보만 포함됩니다.
    상세 정보는 ProductDetailSerializer에서 처리합니다.
    """

    # 카테고리 이름을 보여주기 위한 필드
    # source를 사용하여 관련 모델의 필드를 가져옵니다.
    category_name = serializers.CharField(source="category.name", read_only=True, help_text="상품이 속한 카테고리 이름")

    # 판매자 정보 (선택적 필드이므로 allow_null=True)
    seller_username = serializers.CharField(
        source="seller.username",
        read_only=True,
        allow_null=True,
        help_text="판매자 아이디",
    )

    # 대표 이미지 URL을 가져오는 커스텀 필드
    # SerializerMethodField를 사용하면 메서드로 값을 계산할 수 있습니다.
    thumbnail_image = serializers.SerializerMethodField(help_text="상품 대표 이미지 URL")

    # 평균 평점·리뷰 개수·찜 수·찜 여부:
    # 여러 상품을 그리는 곳은 product_query_service.product_card_queryset 이 서브쿼리로 미리 계산한다 (N+1 방지).
    # 미리 계산되지 않은 단건 응답(장바구니 담기 등)은 아래 get_* 가 직접 조회한다 — 필드는 항상 나온다.
    average_rating = serializers.SerializerMethodField(help_text="평균 평점 (0.0 ~ 5.0)")
    review_count = serializers.SerializerMethodField(help_text="리뷰 총 개수")

    # 할인된 가격 (나중에 할인 기능 추가시 사용)
    # 지금은 원가와 동일하게 반환
    discounted_price = serializers.SerializerMethodField(help_text="할인가 (현재는 원가와 동일)")

    # 재고 상태를 텍스트로 표시 (모델 property 사용)
    stock_status = serializers.ReadOnlyField(help_text="재고 상태 (품절/부족/충분)")

    # 찜 관련 필드
    wishlist_count = serializers.SerializerMethodField(help_text="찜한 사용자 수")
    is_wished = serializers.SerializerMethodField(help_text="현재 사용자가 찜했는지 여부")

    class Meta:
        model = Product
        fields = [
            "id",  # 상품 ID (PK)
            "name",  # 상품명
            "slug",  # URL용 슬러그
            "price",  # 가격
            "discounted_price",  # 할인가
            "stock",  # 재고 수량
            "stock_status",  # 재고 상태
            "category_name",  # 카테고리 이름
            "seller_username",  # 판매자 아이디
            "thumbnail_image",  # 대표 이미지
            "average_rating",  # 평균 평점
            "review_count",  # 리뷰 개수
            "is_active",  # 판매 중 여부
            "created_at",  # 등록일
            "wishlist_count",  # 찜목록 카운팅
            "is_wished",  # 찜 여부
        ]

        # 읽기 전용 필드 지정 (API로 수정 불가)
        read_only_fields = ["created_at", "slug"]

    def get_thumbnail_image(self, obj: Product) -> str | None:
        """
        상품의 대표 이미지 URL을 반환합니다.

        Args:
            obj: Product 인스턴스

        Returns:
            str: 이미지 URL 또는 None
        """
        # 상품의 첫번째 이미지를 대표 이미지로 사용
        first_image = obj.images.first()

        if first_image and first_image.image:
            # request 객체에서 build_absolute_uri 메서드를 사용하여 전체 URL 생성
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(first_image.image.url)
            # request가 없으면 상대 경로 반환
            return first_image.image.url

        # 이미지가 없으면 None 반환 (프론드엔드에서 기본 이미지 처리)
        return None

    def get_average_rating(self, obj: Product) -> float:
        if hasattr(obj, "avg_rating"):
            return _rounded_rating(obj.avg_rating)
        return _rounded_rating(obj.reviews.aggregate(v=Avg("rating"))["v"])

    def get_review_count(self, obj: Product) -> int:
        if hasattr(obj, "review_cnt"):
            return obj.review_cnt
        return obj.reviews.count()

    def get_wishlist_count(self, obj: Product) -> int:
        if hasattr(obj, "wishlist_cnt"):
            return obj.wishlist_cnt
        return obj.wished_by_users.count()

    def get_is_wished(self, obj: Product) -> bool:
        if hasattr(obj, "is_wished"):
            return bool(obj.is_wished)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        return obj.wished_by_users.filter(pk=user.pk).exists()

    def get_discounted_price(self, obj: Product) -> str:
        """
        할인가를 반환합니다.

        TODO: 나중에 할인 모델을 추가하면 실제 할인가 계산 로직 구현
        """
        # 현재는 원가 그대로 반환
        return str(obj.price)


class ProductImageSerializer(serializers.ModelSerializer):
    """상품 이미지 Serializer"""

    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "image", "image_url", "alt_text", "order", "is_primary"]

    def get_image_url(self, obj: ProductImage) -> str | None:
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url if obj.image else None


class ProductReviewSerializer(serializers.ModelSerializer):
    """상품 리뷰"""

    user_display_name = serializers.SerializerMethodField()
    created_at_formatted = serializers.DateTimeField(source="created_at", format="%Y년 %m월 %d일", read_only=True)

    class Meta:
        model = ProductReview
        fields = [
            "id",
            "rating",
            "comment",
            "user_display_name",
            "created_at_formatted",
        ]

    def get_user_display_name(self, obj: ProductReview) -> str:
        username = obj.user.username
        if len(username) <= 2:
            return username[0] + "*"
        return username[0] + "*" * (len(username) - 2) + username[-1]


class ProductDetailSerializer(serializers.ModelSerializer):
    """상품 상세 조회용 Serializer"""

    # 카테고리 정보 - 필요한 필드만 직접 지정
    category_id = serializers.IntegerField(source="category.id", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    category_parent_name = serializers.CharField(source="category.parent.name", read_only=True, default=None)

    # 판매자 정보 - 공개 가능한 정보만 직접 지정
    seller_id = serializers.IntegerField(source="seller.id", read_only=True)
    seller_username = serializers.CharField(source="seller.username", read_only=True)
    seller_level = serializers.CharField(source="seller.membership_level", read_only=True)
    seller_product_count = serializers.SerializerMethodField()

    # 관련 데이터
    images = ProductImageSerializer(many=True, read_only=True)
    recent_reviews = serializers.SerializerMethodField()

    # 계산 필드 (평점·리뷰 수는 상세 쿼리셋의 서브쿼리 값 — 리뷰 전체를 읽지 않는다)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    stock_status = serializers.ReadOnlyField()
    is_in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            # 기본 필드
            "id",
            "name",
            "slug",
            "description",
            "price",
            "stock",
            "sold_count",
            "is_active",
            "created_at",
            "updated_at",
            # 카테고리 정보
            "category_id",
            "category_name",
            "category_slug",
            "category_parent_name",
            # 판매자 정보
            "seller_id",
            "seller_username",
            "seller_level",
            "seller_product_count",
            # 관련 데이터
            "images",
            "recent_reviews",
            # 계산 필드
            "average_rating",
            "review_count",
            "stock_status",
            "is_in_stock",
        ]
        read_only_fields = ["slug", "sold_count", "created_at", "updated_at"]

    def get_seller_product_count(self, obj: Product) -> int:
        """판매자의 상품 수를 반환"""
        if obj.seller:
            return obj.seller.products.filter(is_active=True).count()
        return 0

    def get_recent_reviews(self, obj: Product) -> list[dict[str, Any]]:
        """최근 리뷰 10개 반환 (작성자까지 쿼리 한 번 — 리뷰마다 작성자를 따로 읽지 않는다)"""
        recent_reviews = obj.reviews.select_related("user").order_by("-created_at")[:10]
        return ProductReviewSerializer(recent_reviews, many=True, context=self.context).data

    def get_average_rating(self, obj: Product) -> float:
        """평균 평점"""
        if hasattr(obj, "avg_rating"):
            return _rounded_rating(obj.avg_rating)
        return _rounded_rating(obj.reviews.aggregate(avg=Avg("rating"))["avg"])

    def get_review_count(self, obj: Product) -> int:
        """리뷰 수"""
        if hasattr(obj, "review_cnt"):
            return obj.review_cnt
        return obj.reviews.count()

    def get_is_in_stock(self, obj: Product) -> bool:
        """재고 여부"""
        return obj.stock > 0


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """
    상품 생성 및 수정용 Serializer

    - 생성(POST) 시: 필수 필드 검증 및 기본값 설정
    - 수정(PUT/PATCH) 시: 부분 업데이트 지원
    - 판매자(seller)는 ViewSet에서 자동으로 설정되므로 제외
    """

    # 카테고리는 ID로만 받고, 존재 여부 검증
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(is_active=True),
        help_text="카테고리 ID (활성화된 카테고리만 선택 가능)",
    )

    # slug는 선택적 필드 (없으면 name으로 자동 생성)
    slug = serializers.SlugField(
        max_length=200,
        required=False,
        allow_blank=True,
        help_text="URL용 슬러그 (비워두면 상품명으로 자동 생성)",
    )

    # 가격 검증 (음수 불가)
    price = serializers.DecimalField(max_digits=10, decimal_places=0, min_value=0, help_text="판매가 (0원 이상)")

    compare_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=0,
        min_value=0,
        required=False,
        allow_null=True,
        help_text="할인 전 가격 (선택사항, 판매가보다 높아야 함)",
    )

    # SKU는 고유해야 함
    sku = serializers.CharField(max_length=50, help_text="재고관리코드 (고유값)")

    # 태그는 쉼표로 구분된 문자열
    tags = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        help_text="태그 (쉼표로 구분, 예: 신상품,베스트,세일)",
    )

    class Meta:
        model = Product
        fields = [
            # 기본 정보
            "id",
            "name",
            "slug",
            "category",
            # 상품 설명
            "description",
            "short_description",
            # 가격 정보
            "price",
            "compare_price",
            # 재고 관리
            "stock",
            "is_available",
            "sku",
            # 추가 정보
            "brand",
            "tags",
            # 상태
            "is_active",
            # 읽기 전용 필드
            "view_count",
            "sold_count",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "view_count",  # 시스템에서 관리
            "sold_count",  # 시스템에서 관리
            "created_at",
            "updated_at",
        ]

        # 필수 필드 명시 (모델과 동일하게)
        required_fields = ["name", "category", "description", "price", "sku"]

    def validate_sku(self, value: str) -> str:
        """
        SKU 중복 검증

        수정 시에는 자기 자신은 제외하고 중복 체크
        """
        # 현재 인스턴스가 있는 경우 (수정 시)
        if self.instance:
            # 자기 자신을 제외하고 중복 체크
            if Product.objects.filter(sku=value).exclude(pk=self.instance.pk).exists():
                raise serializers.ValidationError(f"SKU '{value}'는 이미 사용중입니다.")
        else:
            # 생성 시 중복 체크
            if Product.objects.filter(sku=value).exists():
                raise serializers.ValidationError(f"SKU '{value}'는 이미 사용중입니다.")

        return value

    def validate_slug(self, value: str) -> str:
        """
        Slug 중복 검증 (입력된 경우만)
        """
        if value:  # slug가 입력된 경우만 검증
            # 수정 시 자기 자신 제외
            if self.instance:
                if Product.objects.filter(slug=value).exclude(pk=self.instance.pk).exists():
                    raise serializers.ValidationError(f"URL 슬러그 '{value}'는 이미 사용중입니다.")
            else:
                # 생성 시
                if Product.objects.filter(slug=value).exists():
                    raise serializers.ValidationError(f"URL 슬러그 '{value}'는 이미 사용중입니다.")

        return value

    def validate_compare_price(self, value: decimal.Decimal | None) -> decimal.Decimal | None:
        """
        할인 전 가격 검증

        입력된 경우, 판매가보다 높아야 함
        """
        if value is not None:
            # 생성 시
            if not self.instance and "price" in self.initial_data:
                price = self.initial_data.get("price")
                if price and value <= decimal.Decimal(str(price)):
                    raise serializers.ValidationError("할인 전 가격은 판매가보다 높아야 합니다.")

            # 수정 시
            elif self.instance:
                price = self.initial_data.get("price", self.instance.price)
                if value <= decimal.Decimal(str(price)):
                    raise serializers.ValidationError("할인 전 가격은 판매가보다 높아야 합니다.")

        return value

    def validate_stock(self, value: int) -> int:
        """
        재고 수량 검증
        """
        if value < 0:
            raise serializers.ValidationError("재고 수량은 0 이상이어야 합니다.")

        return value

    def validate_tags(self, value: str) -> str:
        """
        태그 형식 검증 및 정규화

        - 앞뒤 공백 제거
        - 중복 태그 제거
        - 빈 태그 제거
        """
        if value:
            # 쉼표로 분리하고 각 태그의 앞뒤 공백 제거
            tags = [tag.strip() for tag in value.split(",")]

            # 빈 태그 제거
            tags = [tag for tag in tags if tag]

            # 중복 제거 (순서 유지)
            seen = set()
            unique_tags = []
            for tag in tags:
                if tag not in seen:
                    seen.add(tag)
                    unique_tags.append(tag)

            # 다시 쉼표로 연결
            return ",".join(unique_tags)

        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """
        전체 데이터 검증

        여러 필드 간의 관계를 검증
        """
        # 재고가 0인데 판매 가능으로 설정한 경우 경고
        stock = attrs.get("stock", 0 if not self.instance else self.instance.stock)
        is_available = attrs.get("is_available", True if not self.instance else self.instance.is_available)

        if stock == 0 and is_available:
            # 경고만 하고 에러는 발생시키지 않음 (관리자가 의도적으로 설정할 수 있음)
            # 필요시 여기서 ValidationError 발생 가능
            pass

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Product:
        """
        상품 생성

        - slug 자동 생성은 모델의 save 메서드에서 처리
        - seller는 ViewSet에서 설정되므로 여기서는 처리하지 않음
        """
        return Product.objects.create(**validated_data)

    def update(self, instance: Product, validated_data: dict[str, Any]) -> Product:
        """
        상품 수정

        - 부분 업데이트 지원 (PATCH)
        - seller는 변경 불가 (read_only이므로 validated_data에 없음)
        """
        # 각 필드 업데이트
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

    def to_representation(self, instance: Product) -> dict[str, Any]:
        """
        응답 시 추가 정보 포함

        생성/수정 후 응답에는 카테고리명 등 추가 정보 포함
        """
        data = super().to_representation(instance)

        # 판매자 정보 추가
        if instance.seller:
            data["seller"] = instance.seller.id
            data["seller_username"] = instance.seller.username

        return data
