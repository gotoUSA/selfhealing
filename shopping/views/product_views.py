from __future__ import annotations

from typing import Any

from django.db.models import Avg, Count, Q
from django.utils.text import slugify

from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework import filters, permissions, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer, Serializer

# 모델 import
from shopping.models.product import Category, Product, ProductReview

# Serializer import
from shopping.serializers import (
    CategorySerializer,
    CategoryTreeSerializer,
    ProductCreateUpdateSerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ProductReviewSerializer,
)

# 권한
from shopping.permissions import IsSellerAndOwner


# ===== Swagger 문서화용 응답 Serializers =====


class ProductErrorResponseSerializer(drf_serializers.Serializer):
    """상품 에러 응답"""

    error = drf_serializers.CharField()


class ReviewAddResponseSerializer(drf_serializers.Serializer):
    """리뷰 작성 응답"""

    id = drf_serializers.IntegerField()
    rating = drf_serializers.IntegerField()
    comment = drf_serializers.CharField()
    user = drf_serializers.CharField()
    created_at = drf_serializers.DateTimeField()


class CategoryTreeItemSerializer(drf_serializers.Serializer):
    """카테고리 트리 아이템"""

    id = drf_serializers.IntegerField()
    name = drf_serializers.CharField()
    slug = drf_serializers.CharField()
    product_count = drf_serializers.IntegerField()
    children = drf_serializers.ListField()


class ProductPagination(PageNumberPagination):
    """
    상품 목록 페이지네이션 설정
    - 페이지당 12개 상품 표시 (기본값)
    -클라이언트가 page_size 파라미터로 조정 가능 (최대 100개)
    """

    page_size = 12  # 페이지당 기본 아이템수
    page_size_query_param = "page_size"  # 클라이언트가 페이지 크기를 조정할 수 있는 파라미터
    max_page_size = 100  # 최대 페이지 크기 제한


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(name="search", description="검색어 (상품명, 설명, 카테고리명)", required=False, type=str),
            OpenApiParameter(name="category", description="카테고리 ID", required=False, type=int),
            OpenApiParameter(name="min_price", description="최소 가격", required=False, type=int),
            OpenApiParameter(name="max_price", description="최대 가격", required=False, type=int),
            OpenApiParameter(name="in_stock", description="재고 여부 (true/false)", required=False, type=str),
            OpenApiParameter(name="seller", description="판매자 ID", required=False, type=int),
            OpenApiParameter(
                name="ordering", description="정렬 (price, -price, created_at, -created_at)", required=False, type=str
            ),
        ],
        summary="상품 목록을 조회한다.",
        description="""처리 내용:
- 활성화된 상품 목록을 페이지네이션하여 반환한다.
- 검색어, 카테고리, 가격 범위 등 필터링을 적용한다.
- 정렬 조건에 따라 결과를 정렬한다.""",
        tags=["Products"],
    ),
    retrieve=extend_schema(
        summary="상품 상세 정보를 조회한다.",
        description="""처리 내용:
- 상품의 상세 정보를 반환한다.
- 판매자, 카테고리, 이미지, 리뷰 정보를 포함한다.""",
        tags=["Products"],
    ),
    create=extend_schema(
        summary="새 상품을 등록한다.",
        description="""처리 내용:
- 상품 정보를 검증하고 등록한다.
- 판매자는 현재 로그인한 사용자로 자동 설정한다.
- slug는 상품명으로 자동 생성한다.""",
        tags=["Products"],
    ),
    update=extend_schema(
        summary="상품 정보를 전체 수정한다.",
        description="""처리 내용:
- 상품 정보를 전체 수정한다.
- 본인 상품만 수정 가능하다.""",
        tags=["Products"],
    ),
    partial_update=extend_schema(
        summary="상품 정보를 부분 수정한다.",
        description="""처리 내용:
- 상품 정보를 부분 수정한다.
- 본인 상품만 수정 가능하다.""",
        tags=["Products"],
    ),
    destroy=extend_schema(
        summary="상품을 삭제한다.",
        description="""처리 내용:
- 상품을 삭제한다.
- 본인 상품만 삭제 가능하다.""",
        tags=["Products"],
    ),
)
class ProductViewSet(viewsets.ModelViewSet):
    """상품 CRUD 및 검색/필터링 ViewSet"""

    queryset = Product.objects.all()
    pagination_class = ProductPagination
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsSellerAndOwner]

    # 검색 필드 설정
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "name",
        "description",
        "category__name",
    ]  # 상품명, 설명, 카테고리명으로 검색

    # 정렬 필드 설정
    ordering_fields = [
        "price",
        "created_at",
        "stock",
        "name",
    ]  # 가격, 등록일, 재고, 이름순 정렬
    ordering = ["-created_at"]  # 기본 정렬: 최신순

    def get_permissions(self) -> list:
        """
        액션별 권한 설정

        - reviews, create_review: 인증된 사용자면 리뷰 작성 가능 (판매자 아니어도 됨)
        - create, update, partial_update, destroy: 판매자만 가능
        - list, retrieve: 누구나 가능
        """
        if self.action in ["reviews", "create_review"]:
            # 리뷰는 인증된 사용자면 작성 가능
            return [permissions.IsAuthenticatedOrReadOnly()]
        return super().get_permissions()

    def get_serializer_class(self) -> type[BaseSerializer]:
        """
        액션별 Serializer 선택

        - list: ProductListSerializer (간단한 정보)
        - retrieve: ProductDetailSerializer (전체 정보)
        - create/update/partial_update: ProductCreateUpdateSerializer
        """
        if self.action == "list":
            return ProductListSerializer
        elif self.action == "retrieve":
            return ProductDetailSerializer
        elif self.action in ["create", "update", "partial_update"]:
            return ProductCreateUpdateSerializer

        # 기본값은 목록용 Serializer
        return ProductListSerializer

    def get_queryset(self) -> Any:
        """
        상품 쿼리셋 조회 및 필터링

        성능 최적화:
        - select_related: seller, category (JOIN 최적화)
        - prefetch_related: images, reviews (N+1 문제 방지)
        - annotate: avg_rating, review_cnt, wishlist_cnt, is_wished (집계)

        필터링:
        - category: 카테고리 및 하위 카테고리 포함
        - min_price, max_price: 가격 범위
        - in_stock: 재고 여부
        - seller: 판매자
        - is_active: 활성 상품만 (기본)
        """
        from django.db.models import Case, When, Value, BooleanField

        # 현재 사용자 ID (인증되지 않은 경우 None)
        user_id = self.request.user.id if self.request.user.is_authenticated else None

        queryset = (
            Product.objects.filter(is_active=True)
            .select_related("seller", "category")
            .prefetch_related("images", "reviews")
            .annotate(
                # 평균 평점과 리뷰 수를 미리 계산
                avg_rating=Avg("reviews__rating"),
                review_cnt=Count("reviews", distinct=True),
                # 찜한 사용자 수 (distinct 필수: JOIN으로 인한 중복 방지)
                wishlist_cnt=Count("wished_by_users", distinct=True),
                # 현재 사용자가 찜했는지 여부 (SQL에서 계산)
                is_wished=Case(
                    When(wished_by_users__id=user_id, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
            )
        )

        # 카테고리 필터링
        category_id = self.request.query_params.get("category", None)
        if category_id:
            try:
                # 하위 카테고리도 포함하여 필터링
                category = Category.objects.get(pk=category_id)
                # 현재 카테고리와 모든 하위 카테고리의 상품 조회
                categories = category.get_descendants(include_self=True)
                queryset = queryset.filter(category__in=categories)
            except Category.DoesNotExist:
                pass

        # 가격 범위 필터링
        min_price = self.request.query_params.get("min_price", None)
        max_price = self.request.query_params.get("max_price", None)

        if min_price:
            try:
                queryset = queryset.filter(price__gte=int(min_price))
            except ValueError:
                pass

        if max_price:
            try:
                queryset = queryset.filter(price__lte=int(max_price))
            except ValueError:
                pass

        # 재고 상태 필터링
        in_stock = self.request.query_params.get("in_stock", None)
        if in_stock is not None:
            if in_stock.lower() == "true":
                queryset = queryset.filter(stock__gt=0)
            elif in_stock.lower() == "false":
                queryset = queryset.filter(stock=0)

        # 판매자 필터링
        seller_id = self.request.query_params.get("seller", None)
        if seller_id:
            queryset = queryset.filter(seller_id=seller_id)

        return queryset

    def perform_create(self, serializer: Serializer) -> None:
        """
        상품 생성 시 추가 처리
        POST /api/products/

        자동 처리:
        - seller: 현재 로그인한 사용자로 설정
        - slug: 상품명으로 자동 생성 (한글 지원)
        - slug 중복 시 숫자 추가 (예: product-1, product-2)
        """
        # slug가 없으면 상품명으로 생성
        if not serializer.validated_data.get("slug"):
            name = serializer.validated_data.get("name", "")
            # 한굴 slug 지원을 위해 allow_unicode=True
            slug = slugify(name, allow_unicode=True)

            # 중복 방지: 같은 slug가 있으면 숫자 추가
            base_slug = slug
            counter = 1
            while Product.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1

            serializer.validated_data["slug"] = slug

        # 현재 사용자를 판매자로 설정
        serializer.save(seller=self.request.user)

    def perform_update(self, serializer: Serializer) -> None:
        """
        상품 수정 시 추가 처리
        PUT/PATCH /api/products/{id}/

        - name 변경 시 slug 자동 재생성
        - slug 중복 시 숫자 추가
        """

        # slug 변경시 중복 체크
        if "name" in serializer.validated_data and not serializer.validated_data.get("slug"):
            name = serializer.validated_data.get("name", "")
            slug = slugify(name, allow_unicode=True)

            # 현재 상품 제외하고 중복 체크
            existing = Product.objects.filter(slug=slug).exclude(pk=serializer.instance.pk)
            if existing.exists():
                base_slug = slug
                counter = 1
                while Product.objects.filter(slug=slug).exclude(pk=serializer.instance.pk).exists():
                    slug = f"{base_slug}-{counter}"
                    counter += 1

            serializer.validated_data["slug"] = slug

        serializer.save()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="ordering", description="정렬 (created_at, -created_at, rating, -rating)", required=False, type=str
            ),
        ],
        responses={200: ProductReviewSerializer(many=True)},
        summary="상품 리뷰 목록을 조회한다.",
        description="""처리 내용:
- 해당 상품의 리뷰 목록을 반환한다.
- 정렬 조건에 따라 결과를 정렬한다.
- 페이지네이션을 적용한다.""",
        tags=["Products"],
    )
    @action(detail=True, methods=["get"], permission_classes=[permissions.AllowAny])
    def reviews(self, request: Request, pk: int | None = None) -> Response:
        product = self.get_object()
        reviews = product.reviews.all().select_related("user")

        # 정렬
        ordering = request.query_params.get("ordering", "-created_at")
        if ordering in ["created_at", "-created_at", "rating", "-rating"]:
            reviews = reviews.order_by(ordering)

        # 페이지네이션 적용
        page = self.paginate_queryset(reviews)
        if page is not None:
            serializer = ProductReviewSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ProductReviewSerializer(reviews, many=True)
        return Response(serializer.data)

    @extend_schema(
        request=ProductReviewSerializer,
        responses={
            201: ReviewAddResponseSerializer,
            400: ProductErrorResponseSerializer,
        },
        summary="상품 리뷰를 작성한다.",
        description="""처리 내용:
- 상품에 리뷰를 작성한다.
- 상품당 1개 리뷰만 작성 가능하다.
- 인증된 사용자만 작성 가능하다.""",
        tags=["Products"],
    )
    @reviews.mapping.post
    def create_review(self, request: Request, pk: int | None = None) -> Response:
        # 인증 확인
        if not request.user.is_authenticated:
            return Response(
                {"error": "로그인이 필요합니다."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        product = self.get_object()

        # 이미 리뷰를 작성했는지 확인
        if ProductReview.objects.filter(product=product, user=request.user).exists():
            return Response(
                {"error": "이미 이 상품에 대한 리뷰를 작성하셨습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 리뷰 생성
        serializer = ProductReviewSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user, product=product)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        responses={200: ProductListSerializer(many=True)},
        summary="인기 상품 목록을 조회한다.",
        description="""처리 내용:
- 리뷰 개수 기준 인기 상품 목록을 반환한다.
- 최대 12개 상품을 반환한다.""",
        tags=["Products"],
    )
    @action(detail=False, methods=["get"])
    def popular(self, request: Request) -> Response:
        popular_products = (
            self.get_queryset()
            .annotate(review_count=Count("reviews"))
            .filter(review_count__gt=0)
            .order_by("-review_count")[:12]
        )

        serializer = ProductListSerializer(popular_products, many=True)
        return Response(serializer.data)

    @extend_schema(
        responses={200: ProductListSerializer(many=True)},
        summary="평점 높은 상품 목록을 조회한다.",
        description="""처리 내용:
- 평균 평점 기준 상품 목록을 반환한다.
- 리뷰 3개 이상인 상품만 포함한다.
- 최대 12개 상품을 반환한다.""",
        tags=["Products"],
    )
    @action(detail=False, methods=["get"])
    def best_rating(self, request: Request) -> Response:
        best_products = (
            self.get_queryset()
            .annotate(avg_rating=Avg("reviews__rating"), review_count=Count("reviews"))
            .filter(review_count__gte=3)  # 최소 3개 이상의 리뷰가 있는 상품만
            .order_by("-avg_rating")[:12]
        )

        serializer = ProductListSerializer(best_products, many=True)
        return Response(serializer.data)

    @extend_schema(
        responses={
            200: ProductListSerializer(many=True),
            401: ProductErrorResponseSerializer,
            403: ProductErrorResponseSerializer,
        },
        summary="재고 부족 상품 목록을 조회한다.",
        description="""처리 내용:
- 재고 10개 이하 상품 목록을 반환한다.
- 판매자 본인 상품만 조회 가능하다.
- 재고 적은 순으로 정렬한다.""",
        tags=["Products"],
    )
    @action(detail=False, methods=["get"])
    def low_stock(self, request: Request) -> Response:
        # 판매자 권한 체크
        if not request.user.is_authenticated:
            return Response({"error": "로그인이 필요합니다."}, status=status.HTTP_401_UNAUTHORIZED)

        if not request.user.is_seller:
            return Response({"error": "판매자만 조회할 수 있습니다."}, status=status.HTTP_403_FORBIDDEN)

        # 본인 상품 중 재고 부족 상품 조회
        low_stock_products = (
            Product.objects.filter(seller=request.user, is_active=True, stock__lte=10)  # 본인 상품만  # 재고 10개 이하
            .select_related("category")
            .order_by("stock", "-created_at")  # 재고 적은 순, 최신 순
        )

        serializer = ProductListSerializer(low_stock_products, many=True, context={"request": request})
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="카테고리 목록을 조회한다.",
        description="""처리 내용:
- 활성화된 카테고리 목록을 반환한다.
- 각 카테고리의 상품 개수를 포함한다.""",
        tags=["Categories"],
    ),
    retrieve=extend_schema(
        summary="카테고리 상세 정보를 조회한다.",
        description="""처리 내용:
- 카테고리의 상세 정보를 반환한다.
- 부모 카테고리 정보를 포함한다.""",
        tags=["Categories"],
    ),
)
class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """카테고리 조회 전용 ViewSet (읽기 전용)"""

    queryset = Category.objects.all()
    permission_classes = [permissions.AllowAny]  # 누구나 조회 가능

    def get_serializer_class(self) -> type[BaseSerializer]:
        """
        액션별 Serializer 선택

        - tree: CategoryTreeSerializer (계층 구조)
        - 기본: CategorySerializer (일반 정보)
        """
        if self.action == "tree":
            return CategoryTreeSerializer
        return CategorySerializer

    def get_queryset(self) -> Any:
        """
        카테고리 쿼리셋 조회

        - 활성 카테고리만 표시 (is_active=True)
        - select_related: parent (JOIN 최적화)
        - annotate: products_count (상품 개수 집계)
        """
        return (
            Category.objects.filter(is_active=True)
            .select_related("parent")
            .annotate(products_count=Count("products", filter=Q(products__is_active=True)))
        )

    @extend_schema(
        responses={200: CategoryTreeItemSerializer(many=True)},
        summary="카테고리 계층 구조를 조회한다.",
        description="""처리 내용:
- 카테고리를 계층 구조(Tree)로 반환한다.
- Redis 캐싱을 적용하여 성능을 최적화한다.
- 각 카테고리의 상품 개수를 포함한다.""",
        tags=["Categories"],
    )
    @action(detail=False, methods=["get"])
    def tree(self, request: Request) -> Response:
        from django.core.cache import cache
        from mptt.templatetags.mptt_tags import cache_tree_children

        # 캐시 키
        cache_key = "category_tree_v2"

        # 캐시에서 먼저 조회
        tree = cache.get(cache_key)

        if tree is None:
            # 캐시 미스 - DB에서 새로 생성
            # 모든 활성 카테고리를 한 번에 가져오고 상품 수 annotate
            # Note: 모델에 product_count @property가 있으므로 다른 이름 사용
            categories = Category.objects.filter(is_active=True).annotate(
                products_count=Count("products", filter=Q(products__is_active=True))
            )

            # MPTT 내장 함수로 트리 캐싱 (매우 빠름!)
            # 이 함수는 각 카테고리에 get_children() 메서드로 접근 가능한 캐시를 추가
            root_nodes = cache_tree_children(categories)

            def serialize_category(category: Category) -> dict[str, Any]:
                """카테고리를 딕셔너리로 직렬화 (재귀)"""
                return {
                    "id": category.id,
                    "name": category.name,
                    "slug": category.slug,
                    "product_count": category.products_count,  # annotate된 필드 사용
                    "children": [serialize_category(child) for child in category.get_children()],  # 이미 캐싱됨!
                }

            # 최상위 카테고리들을 직렬화
            tree = [serialize_category(cat) for cat in root_nodes]

            # Redis에 캐싱 (1시간)
            # 신호(signal)로 자동 무효화되므로 긴 TTL 사용 가능
            cache.set(cache_key, tree, 60 * 60)

        return Response(tree)

    @extend_schema(
        responses={200: ProductListSerializer(many=True)},
        summary="카테고리별 상품 목록을 조회한다.",
        description="""처리 내용:
- 해당 카테고리의 상품 목록을 반환한다.
- 하위 카테고리 상품도 포함한다.
- 페이지네이션을 적용한다.""",
        tags=["Categories"],
    )
    @action(detail=True, methods=["get"])
    def products(self, request: Request, pk: int | None = None) -> Response:
        from django.db.models import Case, When, Value, BooleanField

        category = self.get_object()

        # 현재 카테고리와 모든 하위 카테고리 가져오기
        categories = category.get_descendants(include_self=True)

        # 현재 사용자 ID
        user_id = request.user.id if request.user.is_authenticated else None

        # 해당 카테고리들의 상품 조회 (ProductViewSet과 동일한 annotate 적용)
        products = (
            Product.objects.filter(category__in=categories, is_active=True)
            .select_related("seller", "category")
            .prefetch_related("images", "reviews")
            .annotate(
                avg_rating=Avg("reviews__rating"),
                review_cnt=Count("reviews", distinct=True),
                wishlist_cnt=Count("wished_by_users", distinct=True),
                is_wished=Case(
                    When(wished_by_users__id=user_id, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
            )
            .order_by("-created_at")  # 페이지네이션 일관성을 위한 정렬
        )

        # ProductViewSet의 필터링 로직 재사용
        # 페이지네이션 적용
        paginator = ProductPagination()
        page = paginator.paginate_queryset(products, request)

        if page is not None:
            serializer = ProductListSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)

        serializer = ProductListSerializer(products, many=True, context={"request": request})
        return Response(serializer.data)
