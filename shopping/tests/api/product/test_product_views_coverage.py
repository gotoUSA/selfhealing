"""
ProductViewSet 및 CategoryViewSet 커버리지 보완 테스트

커버되지 않은 라인:
- 104, 109, 160-161, 170-171, 176-177 (필터링 edge cases)
- 184-185, 190, 205->220, 214-215 (slug 자동 생성)
- 232->247, 239-243 (리뷰 정렬/페이지네이션)
- 266->270, 275-276 (리뷰 유효성 검사)
- 310 (low_stock 비인증)
- 368, 407-409 (카테고리 트리)
- 450->479, 531-532 (카테고리별 상품)
"""

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

import pytest
from rest_framework import status

from shopping.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductReviewFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestProductFilteringEdgeCases:
    """상품 필터링 경계 조건 테스트"""

    def test_invalid_category_id_is_ignored(self, api_client):
        """존재하지 않는 카테고리 ID는 무시되고 전체 상품 반환"""
        # Arrange
        ProductFactory(name="상품1")
        ProductFactory(name="상품2")

        # Act
        response = api_client.get(reverse("product-list"), {"category": 99999})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2

    def test_invalid_min_price_is_ignored(self, api_client):
        """잘못된 min_price 값은 무시"""
        # Arrange
        ProductFactory(price=Decimal("10000"))

        # Act
        response = api_client.get(reverse("product-list"), {"min_price": "invalid"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_invalid_max_price_is_ignored(self, api_client):
        """잘못된 max_price 값은 무시"""
        # Arrange
        ProductFactory(price=Decimal("50000"))

        # Act
        response = api_client.get(reverse("product-list"), {"max_price": "not_a_number"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_filter_out_of_stock_products_only(self, api_client):
        """in_stock=false로 품절 상품만 조회"""
        # Arrange
        ProductFactory(name="재고있음", stock=10)
        ProductFactory(name="품절", stock=0)

        # Act
        response = api_client.get(reverse("product-list"), {"in_stock": "false"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["name"] == "품절"

    def test_filter_by_seller_id(self, api_client):
        """판매자 ID로 상품 필터링"""
        # Arrange
        seller1 = UserFactory.seller()
        seller2 = UserFactory.seller()
        ProductFactory(seller=seller1, name="판매자1 상품")
        ProductFactory(seller=seller2, name="판매자2 상품")

        # Act
        response = api_client.get(reverse("product-list"), {"seller": seller1.id})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["name"] == "판매자1 상품"


@pytest.mark.django_db
class TestProductSlugGeneration:
    """상품 슬러그 자동 생성 테스트"""

    def test_slug_auto_generated_from_name_on_create(self, api_client):
        """상품 생성 시 slug 미지정이면 name에서 자동 생성"""
        # Arrange
        seller = UserFactory.seller()
        category = CategoryFactory()
        api_client.force_authenticate(user=seller)
        product_data = {
            "name": "테스트 상품",
            "category": category.id,
            "description": "설명",
            "price": 10000,
            "stock": 10,
            "sku": "AUTO-SLUG-001",
        }

        # Act
        response = api_client.post(reverse("product-list"), product_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["slug"] == "테스트-상품"

    def test_duplicate_slug_appends_counter(self, api_client):
        """동일 이름 상품 생성 시 slug에 숫자 추가"""
        # Arrange
        seller = UserFactory.seller()
        category = CategoryFactory()
        ProductFactory(name="중복상품", slug="중복상품", seller=seller, category=category)
        api_client.force_authenticate(user=seller)
        product_data = {
            "name": "중복상품",
            "category": category.id,
            "description": "설명",
            "price": 10000,
            "stock": 10,
            "sku": "DUP-SLUG-001",
        }

        # Act
        response = api_client.post(reverse("product-list"), product_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["slug"] == "중복상품-1"

    def test_slug_regenerated_on_name_update(self, api_client):
        """상품 이름 변경 시 slug 자동 재생성"""
        # Arrange
        seller = UserFactory.seller()
        product = ProductFactory(seller=seller, name="원래이름", slug="원래이름")
        api_client.force_authenticate(user=seller)

        # Act
        response = api_client.patch(
            reverse("product-detail", kwargs={"pk": product.id}),
            {"name": "새이름"},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["slug"] == "새이름"

    def test_slug_duplicate_on_update_appends_counter(self, api_client):
        """상품 수정 시 slug 중복이면 숫자 추가"""
        # Arrange
        seller = UserFactory.seller()
        ProductFactory(name="기존상품", slug="기존상품", seller=seller)
        product = ProductFactory(name="다른상품", slug="다른상품", seller=seller)
        api_client.force_authenticate(user=seller)

        # Act
        response = api_client.patch(
            reverse("product-detail", kwargs={"pk": product.id}),
            {"name": "기존상품"},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["slug"] == "기존상품-1"


@pytest.mark.django_db
class TestProductReviewOrdering:
    """상품 리뷰 정렬 테스트"""

    def test_reviews_ordered_by_rating_ascending(self, api_client):
        """리뷰를 평점 오름차순으로 정렬"""
        # Arrange
        product = ProductFactory()
        ProductReviewFactory(product=product, rating=5)
        ProductReviewFactory(product=product, rating=1)
        ProductReviewFactory(product=product, rating=3)

        # Act
        response = api_client.get(
            reverse("product-reviews", kwargs={"pk": product.id}),
            {"ordering": "rating"},
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        ratings = [r["rating"] for r in response.data["results"]]
        assert ratings == [1, 3, 5]

    def test_reviews_ordered_by_rating_descending(self, api_client):
        """리뷰를 평점 내림차순으로 정렬"""
        # Arrange
        product = ProductFactory()
        ProductReviewFactory(product=product, rating=2)
        ProductReviewFactory(product=product, rating=5)
        ProductReviewFactory(product=product, rating=3)

        # Act
        response = api_client.get(
            reverse("product-reviews", kwargs={"pk": product.id}),
            {"ordering": "-rating"},
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        ratings = [r["rating"] for r in response.data["results"]]
        assert ratings == [5, 3, 2]

    def test_reviews_ordered_by_created_at_ascending(self, api_client):
        """리뷰를 생성일 오름차순으로 정렬"""
        # Arrange
        product = ProductFactory()
        review1 = ProductReviewFactory(product=product, comment="첫번째")
        review2 = ProductReviewFactory(product=product, comment="두번째")

        # Act
        response = api_client.get(
            reverse("product-reviews", kwargs={"pk": product.id}),
            {"ordering": "created_at"},
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"][0]["id"] == review1.id
        assert response.data["results"][1]["id"] == review2.id


@pytest.mark.django_db
class TestProductReviewValidation:
    """상품 리뷰 유효성 검사 테스트"""

    def test_invalid_review_data_returns_400(self, api_client):
        """잘못된 리뷰 데이터는 400 반환"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)
        invalid_data = {"comment": "평점 누락"}  # rating 필드 누락

        # Act
        response = api_client.post(
            reverse("product-reviews", kwargs={"pk": product.id}),

            invalid_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "rating" in response.data

    def test_invalid_rating_value_returns_400(self, api_client):
        """유효하지 않은 rating 값은 400 반환"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)
        invalid_data = {"rating": 10, "comment": "범위 초과"}  # 1-5 범위 초과

        # Act
        response = api_client.post(
            reverse("product-reviews", kwargs={"pk": product.id}),

            invalid_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLowStockAccessControl:
    """재고 부족 접근 제어 테스트"""

    def test_unauthenticated_user_returns_401(self, api_client):
        """비로그인 사용자는 401 반환"""
        # Act
        response = api_client.get(reverse("product-low-stock"))

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "로그인" in response.data.get("error", "")


@pytest.mark.django_db
class TestCategoryTree:
    """카테고리 트리 조회 테스트"""

    def test_tree_returns_hierarchical_structure_cache_miss(self, api_client):
        """캐시 미스 시 DB에서 계층 구조 생성"""
        # Arrange
        cache.delete("category_tree_v2")  # 캐시 삭제
        parent = CategoryFactory(name="부모카테고리")
        CategoryFactory(name="자식카테고리", parent=parent)

        # Act
        response = api_client.get(reverse("category-tree"))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        parent_data = next((c for c in response.data if c["name"] == "부모카테고리"), None)
        assert parent_data is not None
        assert len(parent_data["children"]) == 1
        assert parent_data["children"][0]["name"] == "자식카테고리"

    def test_tree_uses_cache_when_available(self, api_client):
        """캐시에 데이터가 있으면 캐시 사용 (DB 조회 스킵)"""
        # Arrange
        cached_tree = [{"id": 999, "name": "캐시된카테고리", "slug": "cached", "product_count": 5, "children": []}]

        # Act - mock으로 캐시 히트 시뮬레이션 (병렬 테스트 환경에서 안전)
        with patch("django.core.cache.cache.get", return_value=cached_tree):
            response = api_client.get(reverse("category-tree"))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]["name"] == "캐시된카테고리"


@pytest.mark.django_db
class TestCategoryProducts:
    """카테고리별 상품 목록 테스트"""

    def test_category_products_includes_descendants(self, api_client):
        """하위 카테고리 상품도 포함하여 조회"""
        # Arrange
        parent = CategoryFactory(name="전자제품")
        child = CategoryFactory(name="노트북", parent=parent)
        ProductFactory(category=parent, name="TV")
        ProductFactory(category=child, name="맥북")

        # Act
        response = api_client.get(reverse("category-products", kwargs={"pk": parent.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        product_names = [p["name"] for p in response.data["results"]]
        assert "TV" in product_names
        assert "맥북" in product_names

    def test_category_products_with_authenticated_user(self, api_client):
        """인증된 사용자로 카테고리 상품 조회"""
        # Arrange
        user = UserFactory()
        category = CategoryFactory()
        ProductFactory(category=category, name="테스트상품")
        api_client.force_authenticate(user=user)

        # Act
        response = api_client.get(reverse("category-products", kwargs={"pk": category.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1


@pytest.mark.django_db
class TestCategoryDetail:
    """카테고리 상세 조회 테스트"""

    def test_category_detail_returns_correct_data(self, api_client):
        """카테고리 상세 정보 조회"""
        # Arrange
        category = CategoryFactory(name="테스트 카테고리")

        # Act
        response = api_client.get(reverse("category-detail", kwargs={"pk": category.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "테스트 카테고리"
