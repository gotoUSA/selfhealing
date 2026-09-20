"""
장바구니(Cart) API 테스트

pytest 스타일로 작성된 장바구니 기능 테스트입니다.
Factory를 사용하여 테스트 데이터를 생성합니다.

테스트 범위:
- CartViewSet: 장바구니 CRUD, bulk_add, check_stock
- CartItemViewSet: 아이템 개별 관리
- 동시성 처리
- 비회원(익명) 장바구니

APIClient Usage (동시성 테스트):
    ⚠️ 동시성 테스트(TestConcurrentAccess 클래스)에서는 반드시 각 스레드 내부에서
    독립적인 APIClient()를 생성해야 합니다.
    공유된 api_client fixture를 사용하면 상태 오염(state pollution)으로 인해
    테스트 결과가 비결정적(non-deterministic)이 됩니다.

    올바른 패턴:
        def concurrent_request():
            client = APIClient()  # 스레드별 독립 인스턴스
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            return client.post(...)
"""

import threading
import time
from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.cart import Cart, CartItem
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    ProductFactory,
    UserFactory,
)


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def cart_urls():
    """장바구니 관련 URL 모음"""
    return {
        "detail": reverse("cart-detail"),
        "summary": reverse("cart-summary"),
        "add_item": reverse("cart-add-item"),
        "items": reverse("cart-items"),
        "clear": reverse("cart-clear"),
        "bulk_add": reverse("cart-bulk-add"),
        "check_stock": reverse("cart-check-stock"),
    }


@pytest.fixture
def other_user(db):
    """다른 사용자 (권한 테스트용)"""
    return UserFactory(username="otheruser")


@pytest.fixture
def out_of_stock_product(db, category, seller_user):
    """품절 상품"""
    return ProductFactory.out_of_stock(category=category, seller=seller_user)


@pytest.fixture
def inactive_product(db, category, seller_user):
    """판매 중단 상품"""
    return ProductFactory.inactive(category=category, seller=seller_user)


@pytest.fixture
def product2(db, category, seller_user):
    """두 번째 테스트 상품"""
    return ProductFactory(
        name="테스트 상품 2",
        category=category,
        seller=seller_user,
        price=Decimal("20000"),
        stock=5,
    )


# ==========================================
# 장바구니 조회 테스트
# ==========================================


@pytest.mark.django_db
class TestCartRetrieve:
    """장바구니 조회 테스트"""

    def test_authenticated_user_creates_cart_on_first_access(self, authenticated_client, user, cart_urls):
        """인증된 사용자 첫 접근 시 장바구니 생성"""
        # Act
        response = authenticated_client.get(cart_urls["detail"])

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "id" in data
        assert data["total_amount"] == "0"
        assert data["total_quantity"] == 0
        assert Cart.objects.filter(user=user, is_active=True).exists()

    def test_returns_same_cart_on_multiple_access(self, authenticated_client, cart_urls):
        """여러 번 조회해도 동일한 장바구니 반환"""
        # Act
        response1 = authenticated_client.get(cart_urls["detail"])
        response2 = authenticated_client.get(cart_urls["detail"])

        # Assert
        assert response1.json()["id"] == response2.json()["id"]

    def test_anonymous_user_gets_session_based_cart(self, api_client, cart_urls):
        """비회원은 세션 기반 장바구니 사용"""
        # Act
        response = api_client.get(cart_urls["detail"])

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total_amount"] == "0"

    def test_cart_response_structure(self, authenticated_client, cart_urls):
        """장바구니 응답 구조 검증"""
        # Act
        response = authenticated_client.get(cart_urls["detail"])

        # Assert
        data = response.json()
        required_fields = ["id", "items", "total_amount", "total_quantity", "item_count", "is_active", "is_all_available"]
        for field in required_fields:
            assert field in data, f"응답에 '{field}' 필드 누락"


# ==========================================
# 상품 추가 테스트
# ==========================================


@pytest.mark.django_db
class TestCartAddItem:
    """장바구니 상품 추가 테스트"""

    def test_add_product_success(self, authenticated_client, product, cart_urls):
        """상품 추가 성공"""
        # Arrange
        add_data = {"product_id": product.id, "quantity": 2}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["message"] == "장바구니에 추가되었습니다."
        assert data["item"]["product_id"] == product.id
        assert data["item"]["quantity"] == 2

    def test_add_same_product_increases_quantity(self, authenticated_client, product, cart_urls):
        """동일 상품 추가 시 수량 증가"""
        # Arrange
        add_data = {"product_id": product.id, "quantity": 2}
        authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.json()["item"]["quantity"] == 4

    def test_add_out_of_stock_product_fails(self, authenticated_client, out_of_stock_product, cart_urls):
        """품절 상품 추가 실패"""
        # Arrange
        add_data = {"product_id": out_of_stock_product.id, "quantity": 1}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "재고가 부족" in str(response.json())

    def test_add_inactive_product_fails(self, authenticated_client, inactive_product, cart_urls):
        """판매 중단 상품 추가 실패"""
        # Arrange
        add_data = {"product_id": inactive_product.id, "quantity": 1}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "판매 중단" in str(response.json())

    def test_add_exceeds_stock_fails(self, authenticated_client, product, cart_urls):
        """재고 초과 수량 추가 실패"""
        # Arrange
        add_data = {"product_id": product.id, "quantity": product.stock + 5}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "재고가 부족" in str(response.json())

    def test_add_nonexistent_product_fails(self, authenticated_client, cart_urls):
        """존재하지 않는 상품 추가 실패"""
        # Arrange
        add_data = {"product_id": 99999, "quantity": 1}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "찾을 수 없" in str(response.json())

    def test_add_with_invalid_quantity_fails(self, authenticated_client, product, cart_urls):
        """유효하지 않은 수량으로 추가 실패"""
        # Arrange - 수량 0
        add_data = {"product_id": product.id, "quantity": 0}

        # Act
        response = authenticated_client.post(cart_urls["add_item"], add_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==========================================
# 수량 변경 테스트
# ==========================================


@pytest.mark.django_db
class TestCartUpdateItem:
    """장바구니 아이템 수량 변경 테스트"""

    def test_update_quantity_success(self, authenticated_client, user, product, cart_urls):
        """수량 변경 성공"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product, quantity=2)
        update_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act
        response = authenticated_client.patch(update_url, {"quantity": 5}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["item"]["quantity"] == 5

    def test_update_quantity_to_zero_deletes_item(self, authenticated_client, user, product, cart_urls):
        """수량 0으로 변경 시 아이템 삭제"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product, quantity=2)
        update_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act
        response = authenticated_client.patch(update_url, {"quantity": 0}, format="json")

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not CartItem.objects.filter(id=item.id).exists()

    def test_update_exceeds_stock_fails(self, authenticated_client, user, product, cart_urls):
        """재고 초과 수량 변경 실패"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product, quantity=2)
        update_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act
        response = authenticated_client.patch(update_url, {"quantity": product.stock + 10}, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "재고가 부족" in str(response.json())


# ==========================================
# 아이템 삭제 테스트
# ==========================================


@pytest.mark.django_db
class TestCartDeleteItem:
    """장바구니 아이템 삭제 테스트"""

    def test_delete_item_success(self, authenticated_client, user, product):
        """아이템 삭제 성공"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product)
        delete_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act
        response = authenticated_client.delete(delete_url)

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not CartItem.objects.filter(id=item.id).exists()

    def test_delete_other_users_item_fails(self, authenticated_client, other_user, product):
        """다른 사용자 아이템 삭제 실패"""
        # Arrange
        other_cart = CartFactory(user=other_user)
        other_item = CartItemFactory(cart=other_cart, product=product)
        delete_url = reverse("cart-item-detail", kwargs={"pk": other_item.id})

        # Act
        response = authenticated_client.delete(delete_url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==========================================
# 장바구니 비우기 테스트
# ==========================================


@pytest.mark.django_db
class TestCartClear:
    """장바구니 비우기 테스트"""

    def test_clear_cart_success(self, authenticated_client, user, product, product2, cart_urls):
        """장바구니 비우기 성공"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product)
        CartItemFactory(cart=cart, product=product2)

        # Act
        response = authenticated_client.post(cart_urls["clear"], {"confirm": True}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert cart.items.count() == 0

    def test_clear_without_confirm_fails(self, authenticated_client, user, product, cart_urls):
        """confirm=False 시 비우기 실패"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product)

        # Act
        response = authenticated_client.post(cart_urls["clear"], {"confirm": False}, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert cart.items.count() == 1

    def test_clear_empty_cart_is_idempotent(self, authenticated_client, user, cart_urls):
        """이미 빈 장바구니 비우기는 멱등 — 200 OK와 안내 메시지"""
        # Arrange
        CartFactory(user=user)

        # Act
        response = authenticated_client.post(cart_urls["clear"], {"confirm": True}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "비어있습니다" in str(response.json())


# ==========================================
# 장바구니 요약 테스트
# ==========================================


@pytest.mark.django_db
class TestCartSummary:
    """장바구니 요약 정보 테스트"""

    def test_summary_calculation(self, authenticated_client, user, product, product2, cart_urls):
        """요약 정보 계산 정확성"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)  # 10000 * 2
        CartItemFactory(cart=cart, product=product2, quantity=3)  # 20000 * 3

        # Act
        response = authenticated_client.get(cart_urls["summary"])

        # Assert
        data = response.json()
        assert data["total_amount"] == "80000"  # 20000 + 60000
        assert data["total_quantity"] == 5
        assert data["item_count"] == 2


# ==========================================
# 재고 확인 테스트
# ==========================================


@pytest.mark.django_db
class TestCartCheckStock:
    """장바구니 재고 확인 테스트"""

    def test_all_stock_available(self, authenticated_client, user, product, cart_urls):
        """모든 상품 재고 충분"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)

        # Act
        response = authenticated_client.get(cart_urls["check_stock"])

        # Assert
        data = response.json()
        assert data["has_issues"] is False
        assert data["message"] == "모든 상품을 구매할 수 있습니다."

    def test_stock_shortage_detected(self, authenticated_client, user, product, cart_urls):
        """재고 부족 감지"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=5)
        product.stock = 3  # 재고 부족 상황
        product.save()

        # Act
        response = authenticated_client.get(cart_urls["check_stock"])

        # Assert
        data = response.json()
        assert data["has_issues"] is True
        assert data["issues"][0]["issue"] == "재고 부족"
        assert data["issues"][0]["requested"] == 5
        assert data["issues"][0]["available"] == 3

    def test_inactive_product_detected(self, authenticated_client, user, product, cart_urls):
        """판매 중단 상품 감지"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=2)
        product.is_active = False
        product.save()

        # Act
        response = authenticated_client.get(cart_urls["check_stock"])

        # Assert
        data = response.json()
        assert data["has_issues"] is True
        assert data["issues"][0]["issue"] == "판매 중단"


# ==========================================
# Bulk Add 테스트 (신규)
# ==========================================


@pytest.mark.django_db
class TestCartBulkAdd:
    """장바구니 일괄 추가 테스트"""

    def test_bulk_add_success(self, api_client, category, seller_user, cart_urls):
        """여러 상품 일괄 추가 성공"""
        # Arrange - User와 Cart를 직접 생성하고 force_authenticate 사용
        test_user = UserFactory(is_email_verified=True)
        CartFactory(user=test_user, is_active=True)
        api_client.force_authenticate(user=test_user)

        prod1 = ProductFactory(category=category, seller=seller_user, is_active=True)
        prod2 = ProductFactory(category=category, seller=seller_user, is_active=True)

        items_data = {
            "items": [
                {"product_id": prod1.id, "quantity": 2},
                {"product_id": prod2.id, "quantity": 1},
            ]
        }

        # Act
        response = api_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        data = response.json()
        assert response.status_code in [
            status.HTTP_201_CREATED,
            status.HTTP_207_MULTI_STATUS,
        ], f"Unexpected status {response.status_code}. Response: {data}"
        assert len(data["added_items"]) == 2, f"Expected 2, got {len(data['added_items'])}. Full response: {data}"
        assert "2개의 상품이 추가" in data["message"]

    def test_bulk_add_partial_success(self, api_client, category, seller_user, cart_urls):
        """일부 상품만 추가 성공 (207 Multi-Status)"""
        # Arrange - User와 Cart를 직접 생성하고 force_authenticate 사용
        test_user = UserFactory(is_email_verified=True)
        CartFactory(user=test_user, is_active=True)
        api_client.force_authenticate(user=test_user)

        active_prod = ProductFactory(category=category, seller=seller_user, is_active=True)
        inactive_prod = ProductFactory.inactive(category=category, seller=seller_user)

        items_data = {
            "items": [
                {"product_id": active_prod.id, "quantity": 2},
                {"product_id": inactive_prod.id, "quantity": 1},  # 판매중단
            ]
        }

        # Act
        response = api_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        data = response.json()
        assert (
            response.status_code == status.HTTP_207_MULTI_STATUS
        ), f"Expected 207, got {response.status_code}. Response: {data}"
        assert len(data["added_items"]) == 1, f"Expected 1, got {len(data['added_items'])}. Full response: {data}"
        assert data["error_count"] == 1

    def test_bulk_add_empty_items_fails(self, authenticated_client, cart_urls):
        """빈 배열 전송 시 실패"""
        # Arrange
        items_data = {"items": []}

        # Act
        response = authenticated_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "상품 정보가 없습니다" in str(response.json())

    def test_bulk_add_missing_product_id(self, api_client, category, seller_user, cart_urls):
        """product_id 누락 시 에러"""
        # Arrange - User와 Cart를 직접 생성하고 force_authenticate 사용
        test_user = UserFactory(is_email_verified=True)
        CartFactory(user=test_user, is_active=True)
        api_client.force_authenticate(user=test_user)

        prod = ProductFactory(category=category, seller=seller_user, is_active=True)

        items_data = {
            "items": [
                {"quantity": 2},  # product_id 누락
                {"product_id": prod.id, "quantity": 1},
            ]
        }

        # Act
        response = api_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        data = response.json()
        assert (
            response.status_code == status.HTTP_207_MULTI_STATUS
        ), f"Expected 207, got {response.status_code}. Response: {data}"
        assert data["error_count"] >= 1, f"Expected error_count >= 1, got {data.get('error_count')}. Response: {data}"
        assert len(data["added_items"]) >= 1, f"Expected added_items >= 1, got {len(data['added_items'])}. Response: {data}"

    def test_bulk_add_exceeds_stock(self, api_client, category, seller_user, cart_urls):
        """재고 초과 상품 포함 시 해당 상품만 실패"""
        # Arrange - User와 Cart를 직접 생성하고 force_authenticate 사용
        test_user = UserFactory(is_email_verified=True)
        CartFactory(user=test_user, is_active=True)
        api_client.force_authenticate(user=test_user)

        prod = ProductFactory(category=category, seller=seller_user, is_active=True, stock=10)
        items_data = {
            "items": [
                {"product_id": prod.id, "quantity": prod.stock + 100},
            ]
        }

        # Act
        response = api_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_207_MULTI_STATUS
        data = response.json()
        assert data["error_count"] == 1
        assert "재고 부족" in str(data["errors"])

    def test_bulk_add_invalid_quantity(self, api_client, category, seller_user, cart_urls):
        """유효하지 않은 수량"""
        # Arrange - User와 Cart를 직접 생성하고 force_authenticate 사용
        test_user = UserFactory(is_email_verified=True)
        CartFactory(user=test_user, is_active=True)
        api_client.force_authenticate(user=test_user)

        prod = ProductFactory(category=category, seller=seller_user, is_active=True)
        items_data = {
            "items": [
                {"product_id": prod.id, "quantity": 0},
                {"product_id": prod.id, "quantity": 1000},  # 최대값 초과
            ]
        }

        # Act
        response = api_client.post(cart_urls["bulk_add"], items_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_207_MULTI_STATUS
        assert response.json()["error_count"] == 2


# ==========================================
# CartItemViewSet 테스트 (신규)
# ==========================================


@pytest.mark.django_db
class TestCartItemViewSet:
    """CartItemViewSet 테스트"""

    def test_list_cart_items(self, authenticated_client, user, product, product2):
        """아이템 목록 조회"""
        # Arrange
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product)
        CartItemFactory(cart=cart, product=product2)
        list_url = reverse("cart-items")

        # Act
        response = authenticated_client.get(list_url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 2

    def test_create_cart_item(self, authenticated_client, product):
        """아이템 생성"""
        # Arrange
        create_url = reverse("cart-add-item")

        create_data = {"product_id": product.id, "quantity": 2}

        # Act
        response = authenticated_client.post(create_url, create_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["item"]["quantity"] == 2

    def test_update_cart_item(self, authenticated_client, user, product):
        """아이템 수정 (PATCH 사용)"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product, quantity=1)
        update_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act - cart-item-detail은 PATCH만 허용 (PUT은 405)
        response = authenticated_client.patch(update_url, {"quantity": 5}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["item"]["quantity"] == 5

    def test_destroy_cart_item(self, authenticated_client, user, product):
        """아이템 삭제"""
        # Arrange
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, product=product)
        delete_url = reverse("cart-item-detail", kwargs={"pk": item.id})

        # Act
        response = authenticated_client.delete(delete_url)

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT


# ==========================================
# 동시성 테스트
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestCartConcurrency:
    """장바구니 동시성 처리 테스트"""

    def test_concurrent_add_same_product(self, api_client, product):
        """동시에 같은 상품 추가 시 정확한 수량 처리"""
        # Arrange
        user = UserFactory()
        response = api_client.post(
            reverse("auth-login"),
            {"username": user.username, "password": "testpass123"},
        )
        token = response.json()["token"]["access"]

        success_count = 0
        thread_count = 5

        def add_to_cart():
            nonlocal success_count
            from rest_framework.test import APIClient

            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            resp = client.post(
                reverse("cart-add-item"),
                {"product_id": product.id, "quantity": 1},
                format="json",
            )
            if resp.status_code == status.HTTP_201_CREATED:
                success_count += 1

        # Act
        threads = [threading.Thread(target=add_to_cart) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        cart = Cart.objects.get(user=user, is_active=True)
        cart_item = CartItem.objects.get(cart=cart, product=product)
        assert cart_item.quantity == thread_count

    def test_concurrent_quantity_update(self, api_client, product):
        """동시 수량 변경 시 데이터 정합성 유지"""
        # Arrange
        user = UserFactory()
        cart = CartFactory(user=user)
        cart_item = CartItemFactory(cart=cart, product=product, quantity=5)

        response = api_client.post(
            reverse("auth-login"),
            {"username": user.username, "password": "testpass123"},
        )
        token = response.json()["token"]["access"]

        quantities = [3, 7, 2, 8, 4]
        results = []

        def update_quantity(qty):
            from rest_framework.test import APIClient

            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            update_url = reverse("cart-item-detail", kwargs={"pk": cart_item.id})
            resp = client.patch(update_url, {"quantity": qty}, format="json")
            results.append({"quantity": qty, "status": resp.status_code})

        # Act
        threads = [threading.Thread(target=update_quantity, args=(qty,)) for qty in quantities]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        cart_item.refresh_from_db()
        success_count = sum(1 for r in results if r["status"] == 200)
        assert success_count == len(quantities)  # 모든 요청 성공
        assert cart_item.quantity in quantities  # 최종값은 요청값 중 하나
        assert 0 < cart_item.quantity <= product.stock  # 데이터 정합성


# ==========================================
# 성능 테스트
# ==========================================


@pytest.mark.django_db
class TestCartPerformance:
    """장바구니 성능 테스트"""

    def test_large_cart_query_performance(self, authenticated_client, user, category, seller_user):
        """많은 상품이 담긴 장바구니 조회 성능"""
        # Arrange
        cart = CartFactory(user=user)
        products = [
            ProductFactory(category=category, seller=seller_user, price=Decimal(str(1000 * (i + 1)))) for i in range(50)
        ]
        for i, prod in enumerate(products):
            CartItemFactory(cart=cart, product=prod, quantity=i + 1)

        # Act
        start_time = time.time()
        response = authenticated_client.get(reverse("cart-detail"))
        elapsed = time.time() - start_time

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert elapsed < 1.0, f"조회 시간 {elapsed:.3f}초 (1초 초과)"
        assert len(response.json()["items"]) == 50

        # 총액 검증
        expected_total = sum(prod.price * (i + 1) for i, prod in enumerate(products))
        assert Decimal(response.json()["total_amount"]) == expected_total
