"""
장바구니/주문 중간 상태 처리 통합 테스트

테스트 범위:
- 가격 변경 → 장바구니 조회 → 경고 표시 → 주문 플로우
- 품절 → 자동 정리 → 주문 플로우
- 등급 변경 → 스냅샷 적립률 사용 플로우
"""

from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.cart import Cart
from shopping.models.order import Order
from shopping.services.cart_service import CartService
from shopping.tests.factories import (
    ProductFactory,
    UserFactory,
    ShippingDataBuilder,
)


@pytest.fixture
def api_client():
    """인증된 API 클라이언트"""
    return APIClient()


@pytest.fixture
def verified_user():
    """이메일 인증 완료 사용자"""
    return UserFactory.verified()


@pytest.fixture
def authenticated_client(api_client, verified_user):
    """인증된 클라이언트"""
    api_client.force_authenticate(user=verified_user)
    return api_client


@pytest.mark.django_db
class TestCartRetrieveWithWarnings:
    """장바구니 조회 시 경고 정보 반환 테스트"""

    def test_cart_retrieve_includes_stock_warnings(self, authenticated_client, verified_user):
        """재고 문제 경고 포함"""
        # Arrange
        product = ProductFactory(stock=10)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=8)
        product.stock = 5
        product.save()

        # Act
        response = authenticated_client.get("/api/cart/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["warnings"]["has_issues"] is True
        assert len(response.data["warnings"]["stock_issues"]) == 1

    def test_cart_retrieve_includes_price_warnings(self, authenticated_client, verified_user):
        """가격 변경 경고 포함"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        # Act
        response = authenticated_client.get("/api/cart/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["warnings"]["has_issues"] is True
        assert len(response.data["warnings"]["price_changes"]) == 1

    def test_cart_retrieve_no_warnings_when_ok(self, authenticated_client, verified_user):
        """문제 없을 시 경고 없음"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        response = authenticated_client.get("/api/cart/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["warnings"]["has_issues"] is False


@pytest.mark.django_db
class TestCartCleanupEndpoint:
    """장바구니 정리 엔드포인트 테스트"""

    def test_cleanup_removes_unavailable_products(self, authenticated_client, verified_user):
        """구매 불가 상품 제거"""
        # Arrange
        product = ProductFactory(stock=10)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)
        product.stock = 0
        product.save()

        # Act
        response = authenticated_client.post("/api/cart/cleanup/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "1개 상품이 제거" in response.data["message"]

    def test_cleanup_adjusts_quantity(self, authenticated_client, verified_user):
        """재고 부족 시 수량 조정"""
        # Arrange
        product = ProductFactory(stock=10)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=8)
        product.stock = 3
        product.save()

        # Act
        response = authenticated_client.post("/api/cart/cleanup/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "수량이 조정" in response.data["message"]

    def test_cleanup_returns_no_action_when_ok(self, authenticated_client, verified_user):
        """정리할 상품 없을 시 메시지"""
        # Arrange
        product = ProductFactory(stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        response = authenticated_client.post("/api/cart/cleanup/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "정리할 상품이 없습니다" in response.data["message"]


@pytest.mark.django_db(transaction=True)
class TestUpdatePricesEndpoint:
    """가격 업데이트 엔드포인트 테스트"""

    def test_update_prices_updates_snapshots(self, authenticated_client, verified_user):
        """가격 스냅샷 업데이트"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        # Act
        response = authenticated_client.post("/api/cart/update_prices/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "1개 상품의 가격이 현재 가격으로 업데이트" in response.data["message"]

    def test_update_prices_no_action_when_no_changes(self, authenticated_client, verified_user):
        """변경 없을 시 메시지"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"))
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)

        # Act
        response = authenticated_client.post("/api/cart/update_prices/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "업데이트할 가격 변경 상품이 없습니다" in response.data["message"]


@pytest.mark.django_db
class TestOrderCreationWithPriceChange:
    """가격 변경 시 주문 생성 테스트"""

    def test_order_blocked_when_price_changed(self, authenticated_client, verified_user):
        """가격 변경 시 주문 차단"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        shipping_data = ShippingDataBuilder.default()

        # Act
        response = authenticated_client.post("/api/orders/", shipping_data)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "가격이 변경되었습니다" in str(response.data)

    def test_order_allowed_after_price_confirmation(self, authenticated_client, verified_user):
        """가격 확인 후 주문 허용"""
        # Arrange
        product = ProductFactory(price=Decimal("10000"), stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)
        product.price = Decimal("15000")
        product.save()

        # 가격 업데이트 (사용자 동의)
        CartService.update_item_prices(cart)

        shipping_data = ShippingDataBuilder.default()

        # Act
        response = authenticated_client.post("/api/orders/", shipping_data)

        # Assert - 비동기 주문 처리로 202 Accepted 반환
        assert response.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.django_db
class TestMembershipSnapshotFlow:
    """등급 스냅샷 플로우 테스트"""

    def test_order_saves_membership_at_creation(self, authenticated_client, verified_user):
        """주문 생성 시 등급 저장"""
        # Arrange
        verified_user.membership_level = "gold"
        verified_user.save()

        product = ProductFactory(price=Decimal("10000"), stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)

        shipping_data = ShippingDataBuilder.default()

        # Act
        response = authenticated_client.post("/api/orders/", shipping_data)

        # Assert - 비동기 주문 처리로 202 Accepted 반환
        assert response.status_code == status.HTTP_202_ACCEPTED
        # 비동기 처리이므로 가장 최근 생성된 주문 조회
        order = Order.objects.filter(user=verified_user).order_by("-created_at").first()
        assert order is not None
        assert order.membership_at_order == "gold"
        assert order.earn_rate_at_order == 3  # gold = 3%

    def test_membership_change_does_not_affect_existing_order(self, authenticated_client, verified_user):
        """등급 변경이 기존 주문에 영향 없음"""
        # Arrange
        verified_user.membership_level = "vip"
        verified_user.save()

        product = ProductFactory(price=Decimal("10000"), stock=100)
        cart, _ = Cart.get_or_create_active_cart(user=verified_user)
        CartService.add_item(cart, product.id, quantity=1)

        shipping_data = ShippingDataBuilder.default()

        # Act - 주문 생성 (비동기 처리로 202 Accepted 반환)
        response = authenticated_client.post("/api/orders/", shipping_data)
        assert response.status_code == status.HTTP_202_ACCEPTED

        # 비동기 처리이므로 가장 최근 생성된 주문 조회
        order = Order.objects.filter(user=verified_user).order_by("-created_at").first()
        assert order is not None
        order_id = order.id

        # 등급 강등
        verified_user.membership_level = "bronze"
        verified_user.save()

        # Assert - 주문의 스냅샷은 변경되지 않음
        order.refresh_from_db()
        assert order.membership_at_order == "vip"
        assert order.earn_rate_at_order == 5  # vip = 5%
