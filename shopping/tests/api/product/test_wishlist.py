"""
찜하기 기능 테스트

Pytest + Fixture 패턴 사용
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.cart import Cart
from shopping.models.product import Category, Product
from shopping.models.user import User


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def wishlist_user(db):
    """찜하기 테스트용 사용자"""
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
        first_name="테스트",
        last_name="유저",
    )


@pytest.fixture
def other_user(db):
    """통계 테스트용 다른 사용자"""
    return User.objects.create_user(
        username="otheruser",
        email="other@example.com",
        password="otherpass123",
    )


@pytest.fixture
def wishlist_category(db):
    """찜하기 테스트용 카테고리"""
    return Category.objects.create(
        name="전자제품",
        slug="electronics",
    )


@pytest.fixture
def wishlist_products(db, wishlist_category, wishlist_user):
    """찜하기 테스트용 상품들"""
    product1 = Product.objects.create(
        name="노트북",
        price=Decimal("900000"),
        compare_price=Decimal("1000000"),
        stock=10,
        category=wishlist_category,
        seller=wishlist_user,
        sku="NOTE001",
        description="테스트 노트북",
    )

    product2 = Product.objects.create(
        name="마우스",
        price=Decimal("50000"),
        stock=5,
        category=wishlist_category,
        seller=wishlist_user,
        sku="MOUSE001",
    )

    product3 = Product.objects.create(
        name="키보드 (품절)",
        price=Decimal("80000"),
        stock=0,
        category=wishlist_category,
        seller=wishlist_user,
        sku="KEY001",
    )

    return {"product1": product1, "product2": product2, "product3": product3}


@pytest.fixture
def authenticated_wishlist_client(wishlist_user):
    """인증된 찜하기 테스트용 클라이언트"""
    from rest_framework.test import APIClient

    client = APIClient()
    response = client.post(
        reverse("auth-login"),
        {"username": "testuser", "password": "testpass123"},
    )
    token = response.json()["token"]["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


# ==========================================
# URL Fixtures
# ==========================================


@pytest.fixture
def wishlist_urls():
    """찜하기 관련 URL들"""
    return {
        "list": reverse("wishlist-list"),
        "toggle": reverse("wishlist-toggle"),
        "add": reverse("wishlist-add"),
        "remove": reverse("wishlist-remove"),
        "bulk_add": reverse("wishlist-bulk-add"),
        "clear": reverse("wishlist-clear"),
        "check": reverse("wishlist-check"),
        "stats": reverse("wishlist-stats"),
        "move_to_cart": reverse("wishlist-move-to-cart"),
    }


# ==========================================
# 인증 테스트
# ==========================================


class TestWishlistAuthentication:
    """찜하기 인증 테스트"""

    def test_wishlist_requires_authentication(self, api_client, wishlist_urls):
        """인증 되지 않은 사용자는 찜하기 기능을 사용할 수 없음"""
        # Act
        response = api_client.get(wishlist_urls["list"])

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ==========================================
# 찜하기 추가 테스트
# ==========================================


class TestWishlistAdd:
    """찜하기 추가 테스트"""

    def test_add_to_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """상품을 찜 목록에 추가"""
        # Arrange
        product1 = wishlist_products["product1"]

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["add"], {"product_id": product1.id}
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_wished"] is True
        assert "찜 목록에 추가" in response.data["message"]
        assert wishlist_user.is_in_wishlist(product1)
        assert wishlist_user.get_wishlist_count() == 1

    def test_add_duplicate_to_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """이미 찜한 상품을 다시 추가하려고 할 때"""
        # Arrange
        product1 = wishlist_products["product1"]
        wishlist_user.add_to_wishlist(product1)

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["add"], {"product_id": product1.id}
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "이미 찜한" in response.data["message"]
        assert wishlist_user.get_wishlist_count() == 1

    def test_add_invalid_product(self, authenticated_wishlist_client, wishlist_urls):
        """존재하지 않는 상품 찜하기 시도"""
        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["add"], {"product_id": 99999}
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data


# ==========================================
# 찜하기 토글 테스트
# ==========================================


class TestWishlistToggle:
    """찜하기 토글 테스트"""

    def test_toggle_wishlist(
        self, authenticated_wishlist_client, wishlist_products, wishlist_urls
    ):
        """찜하기 토글 (추가 -> 제거 -> 추가)"""
        # Arrange
        product1 = wishlist_products["product1"]

        # Act & Assert 1 - 처음 토글 (추가)
        response = authenticated_wishlist_client.post(
            wishlist_urls["toggle"], {"product_id": product1.id}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_wished"] is True
        assert "추가" in response.data["message"]

        # Act & Assert 2 - 두 번째 토글 (제거)
        response = authenticated_wishlist_client.post(
            wishlist_urls["toggle"], {"product_id": product1.id}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_wished"] is False
        assert "제거" in response.data["message"]

        # Act & Assert 3 - 세 번째 토글 (다시 추가)
        response = authenticated_wishlist_client.post(
            wishlist_urls["toggle"], {"product_id": product1.id}
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_wished"] is True


# ==========================================
# 찜 목록 조회 테스트
# ==========================================


class TestWishlistList:
    """찜 목록 조회 테스트"""

    def test_list_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록 조회"""
        # Arrange
        wishlist_user.add_to_wishlist(wishlist_products["product1"])
        wishlist_user.add_to_wishlist(wishlist_products["product2"])
        wishlist_user.add_to_wishlist(wishlist_products["product3"])

        # Act
        response = authenticated_wishlist_client.get(wishlist_urls["list"])

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

        product_data = response.data["results"][0]
        assert "id" in product_data
        assert "name" in product_data
        assert "price" in product_data
        assert "is_available" in product_data
        assert "wishlist_count" in product_data

    def test_list_wishlist_with_filters(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """필터를 적용한 찜 목록 조회"""
        # Arrange
        wishlist_user.add_to_wishlist(wishlist_products["product1"])  # 세일 중, 재고 있음
        wishlist_user.add_to_wishlist(wishlist_products["product2"])  # 세일 아님, 재고 있음
        wishlist_user.add_to_wishlist(wishlist_products["product3"])  # 품절

        # Act & Assert - 구매 가능한 상품만
        response = authenticated_wishlist_client.get(f"{wishlist_urls['list']}?is_available=true")
        assert response.data["count"] == 2

        # Act & Assert - 세일 중인 상품만
        response = authenticated_wishlist_client.get(f"{wishlist_urls['list']}?on_sale=true")
        assert response.data["count"] == 1

        # Act & Assert - 가격 오름차순 정렬
        response = authenticated_wishlist_client.get(f"{wishlist_urls['list']}?ordering=price")
        prices = [p["price"] for p in response.data["results"]]
        assert prices == sorted(prices)


# ==========================================
# 대량 추가 테스트
# ==========================================


class TestWishlistBulkAdd:
    """여러 상품 한번에 찜하기 테스트"""

    def test_bulk_add_to_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """여러 상품을 한번에 찜하기"""
        # Arrange
        product_ids = [
            wishlist_products["product1"].id,
            wishlist_products["product2"].id,
            wishlist_products["product3"].id,
        ]

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["bulk_add"], {"product_ids": product_ids}
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["added_count"] == 3
        assert response.data["skipped_count"] == 0
        assert response.data["total_wishlist_count"] == 3
        assert wishlist_user.get_wishlist_count() == 3

    def test_bulk_add_with_duplicates(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """이미 찜한 상품 포함하여 여러 상품 찜하기"""
        # Arrange
        product1 = wishlist_products["product1"]
        product2 = wishlist_products["product2"]
        wishlist_user.add_to_wishlist(product1)

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["bulk_add"], {"product_ids": [product1.id, product2.id]}
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["added_count"] == 1  # product2만 추가
        assert response.data["skipped_count"] == 1  # product1은 스킵
        assert response.data["total_wishlist_count"] == 2


# ==========================================
# 찜 상태 확인 테스트
# ==========================================


class TestWishlistCheck:
    """찜 상태 확인 테스트"""

    def test_check_wishlist_status(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """특정 상품의 찜 상태 확인"""
        # Arrange
        product1 = wishlist_products["product1"]

        # Act & Assert - 찜하기 전
        response = authenticated_wishlist_client.get(
            f"{wishlist_urls['check']}?product_id={product1.id}"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_wished"] is False

        # Arrange - 찜하기
        wishlist_user.add_to_wishlist(product1)

        # Act & Assert - 찜하기 후
        response = authenticated_wishlist_client.get(
            f"{wishlist_urls['check']}?product_id={product1.id}"
        )
        assert response.data["is_wished"] is True
        assert response.data["wishlist_count"] == 1

    def test_wishlist_count_multiple_users(
        self,
        authenticated_wishlist_client,
        wishlist_user,
        other_user,
        wishlist_products,
        wishlist_urls,
    ):
        """여러 사용자가 찜했을 때 카운트 확인"""
        # Arrange
        product1 = wishlist_products["product1"]
        wishlist_user.add_to_wishlist(product1)
        other_user.add_to_wishlist(product1)

        # Act
        response = authenticated_wishlist_client.get(
            f"{wishlist_urls['check']}?product_id={product1.id}"
        )

        # Assert
        assert response.data["wishlist_count"] == 2


# ==========================================
# 찜 목록 통계 테스트
# ==========================================


class TestWishlistStatistics:
    """찜 목록 통계 테스트"""

    def test_wishlist_statistics(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록 통계 조회"""
        # Arrange
        wishlist_user.add_to_wishlist(wishlist_products["product1"])  # 세일, 재고 있음
        wishlist_user.add_to_wishlist(wishlist_products["product2"])  # 정가, 재고 있음
        wishlist_user.add_to_wishlist(wishlist_products["product3"])  # 품절

        # Act
        response = authenticated_wishlist_client.get(wishlist_urls["stats"])

        # Assert
        assert response.status_code == status.HTTP_200_OK
        stats = response.data

        assert stats["total_count"] == 3
        assert stats["available_count"] == 2
        assert stats["out_of_stock_count"] == 1
        assert stats["on_sale_count"] == 1

        # product1: compare_price=1000000, price=900000
        # product2: price=50000 (compare_price 없음)
        # product3: price=80000 (compare_price 없음)
        assert float(stats["total_price"]) == 1130000
        assert float(stats["total_sale_price"]) == 1030000
        assert float(stats["total_discount"]) == 100000


# ==========================================
# 찜 목록 삭제 테스트
# ==========================================


class TestWishlistRemove:
    """찜 목록 삭제 테스트"""

    def test_remove_from_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록에서 제거"""
        # Arrange
        product1 = wishlist_products["product1"]
        wishlist_user.add_to_wishlist(product1)
        assert wishlist_user.get_wishlist_count() == 1

        # Act
        response = authenticated_wishlist_client.delete(
            f"{wishlist_urls['remove']}?product_id={product1.id}"
        )

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert wishlist_user.get_wishlist_count() == 0
        assert not wishlist_user.is_in_wishlist(product1)

    def test_clear_wishlist(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록 전체 삭제"""
        # Arrange
        wishlist_user.add_to_wishlist(wishlist_products["product1"])
        wishlist_user.add_to_wishlist(wishlist_products["product2"])
        wishlist_user.add_to_wishlist(wishlist_products["product3"])

        # Act & Assert - confirm 없이 시도
        response = authenticated_wishlist_client.delete(wishlist_urls["clear"])
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Act - confirm=true로 삭제
        response = authenticated_wishlist_client.delete(f"{wishlist_urls['clear']}?confirm=true")

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert wishlist_user.get_wishlist_count() == 0


# ==========================================
# 장바구니 연동 테스트
# ==========================================


class TestWishlistMoveToCart:
    """장바구니 연동 테스트"""

    def test_move_to_cart(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록에서 장바구니로 이동"""
        # Arrange
        product1 = wishlist_products["product1"]
        product2 = wishlist_products["product2"]
        product3 = wishlist_products["product3"]  # 품절

        wishlist_user.add_to_wishlist(product1)
        wishlist_user.add_to_wishlist(product2)
        wishlist_user.add_to_wishlist(product3)
        assert wishlist_user.wishlist_products.count() == 3

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["move_to_cart"],
            {
                "product_ids": [product1.id, product2.id, product3.id],
                "remove_from_wishlist": True,
            },
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["added_items"]) == 2  # 품절 제외
        assert len(response.data["out_of_stock"]) == 1  # 품절 1개

        cart = Cart.objects.get(user=wishlist_user, is_active=True)
        assert cart.items.count() == 2
        assert wishlist_user.get_wishlist_count() == 1  # 품절 상품만 남음

    def test_move_to_cart_without_removing(
        self, authenticated_wishlist_client, wishlist_user, wishlist_products, wishlist_urls
    ):
        """찜 목록에서 장바구니로 이동 (찜 유지)"""
        # Arrange
        product1 = wishlist_products["product1"]
        wishlist_user.wishlist_products.clear()
        wishlist_user.add_to_wishlist(product1)
        wishlist_user_refreshed = User.objects.get(id=wishlist_user.id)

        # Act
        response = authenticated_wishlist_client.post(
            wishlist_urls["move_to_cart"],
            {"product_ids": [product1.id], "remove_from_wishlist": False},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        cart = Cart.objects.get(user=wishlist_user, is_active=True)
        assert cart.items.count() == 1
        assert wishlist_user_refreshed.get_wishlist_count() == 1
        assert wishlist_user_refreshed.is_in_wishlist(product1)


# ==========================================
# 본인 찜 목록만 보기 테스트
# ==========================================


class TestWishlistOwnership:
    """본인 찜 목록만 보기 테스트"""

    def test_user_can_only_see_own_wishlist(
        self, api_client, wishlist_user, other_user, wishlist_products, wishlist_urls
    ):
        """사용자는 본인의 찜 목록만 볼 수 있음"""
        # Arrange
        product1 = wishlist_products["product1"]
        product2 = wishlist_products["product2"]

        # Act & Assert 1 - 첫 번째 사용자 로그인 및 찜하기
        response = api_client.post(
            reverse("auth-login"),
            {"username": "testuser", "password": "testpass123"},
        )
        token = response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        wishlist_user.add_to_wishlist(product1)
        wishlist_user.add_to_wishlist(product2)

        response = api_client.get(wishlist_urls["list"])
        assert response.data["count"] == 2

        # Act & Assert 2 - 두 번째 사용자로 로그인
        api_client.credentials()  # 인증 초기화
        response = api_client.post(
            reverse("auth-login"),
            {"username": "otheruser", "password": "otherpass123"},
        )
        token = response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = api_client.get(wishlist_urls["list"])
        assert response.data["count"] == 0  # 다른 사용자의 찜 목록은 비어있음
