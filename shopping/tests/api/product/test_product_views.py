"""
ProductViewSet 테스트

테스트 범위:
- 상품 목록 필터링 (카테고리, 가격, 재고, 검색)
- 상품 CRUD (생성, 수정, 삭제)
- 리뷰 기능 (목록, 작성)
- 커스텀 액션 (인기상품, 평점높은상품, 재고부족)
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.product import Product, ProductReview
from shopping.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductReviewFactory,
    UserFactory,
)


# ==========================================
# 상품 목록 필터링 테스트
# ==========================================


@pytest.mark.django_db
class TestProductListFiltering:
    """상품 목록 필터링 기능 테스트"""

    def test_filter_by_category(self, api_client):
        """카테고리 ID로 상품 필터링"""
        # Arrange
        category1 = CategoryFactory(name="전자제품")
        category2 = CategoryFactory(name="의류")
        ProductFactory(category=category1, name="노트북")
        ProductFactory(category=category1, name="스마트폰")
        ProductFactory(category=category2, name="티셔츠")

        url = reverse("product-list")

        # Act
        response = api_client.get(url, {"category": category1.id})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        product_names = [p["name"] for p in response.data["results"]]
        assert "노트북" in product_names
        assert "스마트폰" in product_names
        assert "티셔츠" not in product_names

    def test_filter_by_price_range(self, api_client):
        """최소/최대 가격으로 상품 필터링"""
        # Arrange
        ProductFactory(name="저가상품", price=Decimal("5000"))
        ProductFactory(name="중가상품", price=Decimal("15000"))
        ProductFactory(name="고가상품", price=Decimal("50000"))

        url = reverse("product-list")

        # Act
        response = api_client.get(url, {"min_price": 10000, "max_price": 20000})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["name"] == "중가상품"

    def test_filter_by_stock_status(self, api_client):
        """재고 있음/없음으로 상품 필터링"""
        # Arrange
        ProductFactory(name="재고있음", stock=10)
        ProductFactory(name="품절", stock=0)

        url = reverse("product-list")

        # Act - 재고 있는 상품만
        response = api_client.get(url, {"in_stock": "true"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["name"] == "재고있음"

    def test_filter_by_search_keyword(self, api_client):
        """검색어로 상품명/설명 필터링"""
        # Arrange
        ProductFactory(name="애플 맥북 프로", description="고성능 노트북")
        ProductFactory(name="삼성 갤럭시", description="스마트폰")

        url = reverse("product-list")

        # Act
        response = api_client.get(url, {"search": "맥북"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert "맥북" in response.data["results"][0]["name"]


# ==========================================
# 상품 CRUD 테스트
# ==========================================


@pytest.mark.django_db
class TestProductCRUD:
    """상품 생성/수정/삭제 테스트"""

    def test_seller_can_create_product(self, api_client):
        """판매자 상품 생성 가능"""
        # Arrange
        seller = UserFactory.seller()
        category = CategoryFactory()
        api_client.force_authenticate(user=seller)

        product_data = {
            "name": "신규 상품",
            "category": category.id,
            "description": "상품 설명입니다",
            "price": 25000,
            "stock": 50,
            "sku": "NEW-SKU-001",
        }
        url = reverse("product-list")

        # Act
        response = api_client.post(url, product_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "신규 상품"
        assert Product.objects.filter(name="신규 상품", seller=seller).exists()

    def test_non_seller_cannot_create_product(self, api_client):
        """일반 사용자 상품 생성 불가"""
        # Arrange
        user = UserFactory()  # is_seller=False
        category = CategoryFactory()
        api_client.force_authenticate(user=user)

        product_data = {
            "name": "신규 상품",
            "category": category.id,
            "description": "상품 설명",
            "price": 25000,
            "stock": 50,
            "sku": "NEW-SKU-002",
        }
        url = reverse("product-list")

        # Act
        response = api_client.post(url, product_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_can_update_product(self, api_client):
        """판매자 본인 상품 수정 가능"""
        # Arrange
        seller = UserFactory.seller()
        product = ProductFactory(seller=seller, name="원래 이름", price=Decimal("10000"))
        api_client.force_authenticate(user=seller)

        url = reverse("product-detail", kwargs={"pk": product.id})

        # Act
        response = api_client.patch(url, {"name": "수정된 이름"}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        product.refresh_from_db()
        assert product.name == "수정된 이름"

    def test_non_owner_cannot_update_product(self, api_client):
        """다른 판매자의 상품 수정 불가"""
        # Arrange
        owner = UserFactory.seller()
        other_seller = UserFactory.seller()
        product = ProductFactory(seller=owner)
        api_client.force_authenticate(user=other_seller)

        url = reverse("product-detail", kwargs={"pk": product.id})

        # Act
        response = api_client.patch(url, {"name": "수정 시도"}, format="json")

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_can_delete_product(self, api_client):
        """판매자 본인 상품 삭제 가능"""
        # Arrange
        seller = UserFactory.seller()
        product = ProductFactory(seller=seller)
        product_id = product.id
        api_client.force_authenticate(user=seller)

        url = reverse("product-detail", kwargs={"pk": product_id})

        # Act
        response = api_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Product.objects.filter(id=product_id).exists()


# ==========================================
# 상품 리뷰 테스트
# ==========================================


@pytest.mark.django_db
class TestProductReview:
    """상품 리뷰 기능 테스트"""

    def test_list_product_reviews(self, api_client):
        """상품의 리뷰 목록 조회"""
        # Arrange
        product = ProductFactory()
        ProductReviewFactory(product=product, rating=5, comment="좋아요")
        ProductReviewFactory(product=product, rating=4, comment="괜찮아요")

        url = reverse("product-reviews", kwargs={"pk": product.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_authenticated_user_can_add_review(self, api_client):
        """인증된 사용자 리뷰 작성 가능"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        review_data = {"rating": 5, "comment": "훌륭한 상품입니다!"}
        url = reverse("product-reviews", kwargs={"pk": product.id})

        # Act
        response = api_client.post(url, review_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert ProductReview.objects.filter(product=product, user=user).exists()

    def test_duplicate_review_returns_400(self, api_client):
        """같은 상품에 중복 리뷰 작성 불가"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        ProductReviewFactory(product=product, user=user)
        api_client.force_authenticate(user=user)

        review_data = {"rating": 3, "comment": "두 번째 리뷰"}
        url = reverse("product-reviews", kwargs={"pk": product.id})

        # Act
        response = api_client.post(url, review_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미" in response.data.get("error", "")

    def test_unauthenticated_user_cannot_add_review(self, api_client):
        """비로그인 사용자 리뷰 작성 불가"""
        # Arrange
        product = ProductFactory()
        review_data = {"rating": 5, "comment": "좋아요"}
        url = reverse("product-reviews", kwargs={"pk": product.id})

        # Act
        response = api_client.post(url, review_data, format="json")

        # Assert - DRF 기본 설정에서 비인증은 401 반환
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ==========================================
# 커스텀 액션 테스트
# ==========================================


@pytest.mark.django_db
class TestProductCustomActions:
    """상품 커스텀 액션 테스트"""

    def test_list_popular_products(self, api_client):
        """리뷰가 많은 인기 상품 목록 조회"""
        # Arrange
        popular_product = ProductFactory(name="인기상품")
        normal_product = ProductFactory(name="일반상품")

        # 인기 상품에 리뷰 3개 추가
        for _ in range(3):
            ProductReviewFactory(product=popular_product)

        url = reverse("product-popular")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        # 리뷰 있는 상품만 나옴
        product_names = [p["name"] for p in response.data]
        assert "인기상품" in product_names

    def test_list_best_rating_products(self, api_client):
        """평균 평점이 높은 상품 목록 조회 (리뷰 3개 이상)"""
        # Arrange
        high_rated = ProductFactory(name="고평점상품")
        low_rated = ProductFactory(name="저평점상품")

        # 고평점 상품: 리뷰 3개, 평균 5점
        for _ in range(3):
            ProductReviewFactory(product=high_rated, rating=5)

        # 저평점 상품: 리뷰 3개, 평균 2점
        for _ in range(3):
            ProductReviewFactory(product=low_rated, rating=2)

        url = reverse("product-best-rating")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) >= 1
        # 첫 번째가 고평점 상품이어야 함
        assert response.data[0]["name"] == "고평점상품"

    def test_seller_can_view_low_stock_products(self, api_client):
        """판매자 본인의 재고 부족 상품 조회"""
        # Arrange
        seller = UserFactory.seller()
        ProductFactory(seller=seller, name="재고부족", stock=5)
        ProductFactory(seller=seller, name="재고충분", stock=100)
        api_client.force_authenticate(user=seller)

        url = reverse("product-low-stock")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        product_names = [p["name"] for p in response.data]
        assert "재고부족" in product_names
        assert "재고충분" not in product_names

    def test_non_seller_cannot_view_low_stock(self, api_client):
        """일반 사용자 재고 부족 상품 조회 불가"""
        # Arrange
        user = UserFactory()  # is_seller=False
        api_client.force_authenticate(user=user)

        url = reverse("product-low-stock")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
