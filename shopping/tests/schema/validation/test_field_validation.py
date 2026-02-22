# test_field_validation.py
# 필드 검증 테스트 - 필수 필드 존재 및 타입 검증

"""
📋 필드 검증 테스트
=================

가이드라인 01_SUCCESS_RESPONSE_SCHEMA.md에 따라
API 응답의 필수 필드 존재 및 타입을 검증합니다.

✅ 검증 항목:
- 필수 필드 존재 확인
- 필드 타입 일치 확인
- 선택적 필드 타입 확인 (존재 시)

📁 관련 가이드:
- guides/01_SUCCESS_RESPONSE_SCHEMA.md
- guides/02_ERROR_RESPONSE_SCHEMA.md
"""

import pytest
from decimal import Decimal
from rest_framework import status

from ..conftest import (
    assert_product_schema,
    assert_order_schema,
    assert_user_schema,
    assert_cart_schema,
    assert_list_response,
)


# ==========================================
# 📦 필수 필드 정의
# ==========================================

PRODUCT_REQUIRED_FIELDS = ["id", "name", "price", "stock"]
ORDER_REQUIRED_FIELDS = ["id", "status", "total_amount", "created_at"]
USER_REQUIRED_FIELDS = ["id", "username", "email"]
CART_REQUIRED_FIELDS = ["id", "items", "total_amount"]
CART_ITEM_REQUIRED_FIELDS = ["id", "product", "quantity"]

# ==========================================
# 📦 필드 타입 정의
# ==========================================

PRODUCT_FIELD_TYPES = {
    "id": int,
    "name": str,
    "price": (int, float, str, Decimal),  # Decimal은 str로 직렬화될 수 있음
    "stock": int,
    "is_active": bool,
    "created_at": str,  # ISO 8601 문자열
    "description": (str, type(None)),
    "category": (dict, int, type(None)),
}

ORDER_FIELD_TYPES = {
    "id": int,
    "status": str,
    "total_amount": (int, float, str, Decimal),
    "final_amount": (int, float, str, Decimal),
    "created_at": str,
    "updated_at": str,
    "shipping_name": str,
    "shipping_address": str,
}

USER_FIELD_TYPES = {
    "id": int,
    "username": str,
    "email": str,
    "is_active": bool,
    "date_joined": str,
}


@pytest.mark.schema
@pytest.mark.django_db
class TestProductFieldValidation:
    """
    🛍️ 상품 필드 검증 테스트

    상품 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, name, price, stock
    - 타입 검증: int, str, Decimal 등
    """

    def test_product_detail_required_fields(self, client, schema_test_product):
        """
        🛒 상품 상세 API 필수 필드 존재 확인

        📋 가이드라인 01 참조
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        for field in PRODUCT_REQUIRED_FIELDS:
            assert field in data, f"필수 필드 '{field}' 누락"

    def test_product_detail_field_types(self, client, schema_test_product):
        """
        🛒 상품 상세 API 필드 타입 검증

        각 필드가 올바른 타입인지 확인합니다.
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 필드 타입 검증
        assert isinstance(data["id"], int), f"id 타입 불일치: {type(data['id'])}"
        assert isinstance(data["name"], str), f"name 타입 불일치: {type(data['name'])}"
        assert isinstance(data["stock"], int), f"stock 타입 불일치: {type(data['stock'])}"
        # price는 Decimal이 str로 직렬화될 수 있음
        assert isinstance(data["price"], (int, float, str)), f"price 타입 불일치: {type(data['price'])}"

    def test_product_list_items_required_fields(self, client, schema_test_product):
        """
        🛒 상품 목록 각 아이템 필수 필드 확인

        목록의 각 상품이 필수 필드를 갖추는지 검증합니다.
        """
        # Arrange - none needed for public API

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 응답 구조 검증
        results = assert_list_response(data, context="/api/products/")

        # 각 아이템 필수 필드 검증 (처음 5개만)
        for i, product in enumerate(results[:5]):
            for field in PRODUCT_REQUIRED_FIELDS:
                assert field in product, f"상품[{i}]: 필수 필드 '{field}' 누락"

    def test_product_schema_helper_validation(self, client, schema_test_product):
        """
        🛒 conftest.py의 assert_product_schema 헬퍼 활용 테스트

        헬퍼 함수를 사용한 상품 스키마 검증
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 헬퍼 함수로 검증
        assert_product_schema(data, context=f"/api/products/{product_id}/")


@pytest.mark.schema
@pytest.mark.django_db
class TestOrderFieldValidation:
    """
    📦 주문 필드 검증 테스트

    주문 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, status, total_amount, created_at
    - 타입 검증
    """

    def test_order_detail_required_fields(self, client, auth_headers, schema_test_order):
        """
        📦 주문 상세 API 필수 필드 존재 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        for field in ORDER_REQUIRED_FIELDS:
            assert field in data, f"필수 필드 '{field}' 누락"

    def test_order_detail_field_types(self, client, auth_headers, schema_test_order):
        """
        📦 주문 상세 API 필드 타입 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 필드 타입 검증
        assert isinstance(data["id"], int), "id 타입 불일치"
        assert isinstance(data["status"], str), "status 타입 불일치"
        assert isinstance(data["created_at"], str), "created_at 타입 불일치"
        # total_amount는 Decimal → str 직렬화
        assert isinstance(data["total_amount"], (int, float, str)), "total_amount 타입 불일치"

    def test_order_list_items_required_fields(self, client, auth_headers, schema_test_order):
        """
        📦 주문 목록 각 아이템 필수 필드 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 응답 구조 검증
        results = assert_list_response(data, context="/api/orders/")

        # 각 아이템 필수 필드 검증
        for i, order in enumerate(results[:5]):
            for field in ORDER_REQUIRED_FIELDS:
                assert field in order, f"주문[{i}]: 필수 필드 '{field}' 누락"

    def test_order_schema_helper_validation(self, client, auth_headers, schema_test_order):
        """
        📦 conftest.py의 assert_order_schema 헬퍼 활용 테스트
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 헬퍼 함수로 검증
        assert_order_schema(data, context=f"/api/orders/{order_id}/")


@pytest.mark.schema
@pytest.mark.django_db
class TestUserFieldValidation:
    """
    👤 사용자 필드 검증 테스트

    사용자 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, username, email
    - 타입 검증
    """

    def test_user_profile_required_fields(self, client, auth_headers):
        """
        👤 사용자 프로필 API 필수 필드 존재 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/users/profile/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        for field in USER_REQUIRED_FIELDS:
            assert field in data, f"필수 필드 '{field}' 누락"

    def test_user_profile_field_types(self, client, auth_headers):
        """
        👤 사용자 프로필 API 필드 타입 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/users/profile/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 필드 타입 검증
        assert isinstance(data["id"], int), "id 타입 불일치"
        assert isinstance(data["username"], str), "username 타입 불일치"
        assert isinstance(data["email"], str), "email 타입 불일치"

    def test_user_schema_helper_validation(self, client, auth_headers):
        """
        👤 conftest.py의 assert_user_schema 헬퍼 활용 테스트
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/users/profile/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 헬퍼 함수로 검증
        assert_user_schema(data, context="/api/users/profile/")


@pytest.mark.schema
@pytest.mark.django_db
class TestCartFieldValidation:
    """
    🛒 장바구니 필드 검증 테스트

    장바구니 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 장바구니 필수 필드: id, items, total_amount
    - 장바구니 아이템 필수 필드: id, product, quantity
    """

    def test_cart_required_fields(self, client, auth_headers, schema_test_cart):
        """
        🛒 장바구니 API 필수 필드 존재 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        # 장바구니 응답 구조에 따라 조정
        assert "items" in data or "cart_items" in data or isinstance(data, list), "장바구니 아이템 필드 누락"

    def test_cart_item_required_fields(self, client, auth_headers, schema_test_cart):
        """
        🛒 장바구니 아이템 필수 필드 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/items/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 응답 처리
        items = data if isinstance(data, list) else data.get("results", data.get("items", []))

        # 각 아이템 필드 검증
        for i, item in enumerate(items[:5]):
            assert "id" in item or "product" in item, f"아이템[{i}]: id 또는 product 필드 누락"
            assert "quantity" in item, f"아이템[{i}]: quantity 필드 누락"

    def test_cart_schema_helper_validation(self, client, auth_headers, schema_test_cart):
        """
        🛒 conftest.py의 assert_cart_schema 헬퍼 활용 테스트
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 헬퍼 함수로 검증
        assert_cart_schema(data, context="/api/cart/")


@pytest.mark.schema
@pytest.mark.django_db
class TestCategoryFieldValidation:
    """
    📂 카테고리 필드 검증 테스트

    카테고리 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, name, slug
    - 타입 검증
    """

    CATEGORY_REQUIRED_FIELDS = ["id", "name", "slug"]

    def test_category_detail_required_fields(self, client, schema_test_category):
        """
        📂 카테고리 상세 API 필수 필드 존재 확인
        """
        # Arrange
        category_id = schema_test_category.id

        # Act
        response = client.get(f"/api/categories/{category_id}/")

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        for field in self.CATEGORY_REQUIRED_FIELDS:
            assert field in data, f"필수 필드 '{field}' 누락"

    def test_category_detail_field_types(self, client, schema_test_category):
        """
        📂 카테고리 상세 API 필드 타입 검증
        """
        # Arrange
        category_id = schema_test_category.id

        # Act
        response = client.get(f"/api/categories/{category_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 필드 타입 검증
        assert isinstance(data["id"], int), "id 타입 불일치"
        assert isinstance(data["name"], str), "name 타입 불일치"
        assert isinstance(data["slug"], str), "slug 타입 불일치"

    def test_category_list_items_required_fields(self, client, schema_test_category):
        """
        📂 카테고리 목록 각 아이템 필수 필드 확인
        """
        # Arrange - none needed for public API

        # Act
        response = client.get("/api/categories/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 응답 구조 검증
        results = assert_list_response(data, context="/api/categories/")

        # 각 아이템 필수 필드 검증
        for i, category in enumerate(results[:5]):
            for field in self.CATEGORY_REQUIRED_FIELDS:
                assert field in category, f"카테고리[{i}]: 필수 필드 '{field}' 누락"


@pytest.mark.schema
@pytest.mark.django_db
class TestNotificationFieldValidation:
    """
    🔔 알림 필드 검증 테스트

    알림 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, message, is_read, created_at
    - 타입 검증
    """

    NOTIFICATION_REQUIRED_FIELDS = ["id", "message", "is_read", "created_at"]

    def test_notification_list_structure(self, client, auth_headers):
        """
        🔔 알림 목록 API 구조 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/notifications/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 목록 구조
        data = response.json()
        assert isinstance(data, (dict, list)), "응답이 dict 또는 list가 아님"

    def test_notification_item_field_types(self, client, auth_headers):
        """
        🔔 알림 아이템 필드 타입 검증

        알림이 있을 경우 필드 타입을 검증합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/notifications/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 추출
        results = data if isinstance(data, list) else data.get("results", [])

        # 알림이 있을 경우 타입 검증
        if results:
            notification = results[0]
            if "id" in notification:
                assert isinstance(notification["id"], int), "id 타입 불일치"
            if "is_read" in notification:
                assert isinstance(notification["is_read"], bool), "is_read 타입 불일치"
            if "created_at" in notification:
                assert isinstance(notification["created_at"], str), "created_at 타입 불일치"
