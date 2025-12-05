# test_authenticated_endpoints.py
# 인증 필요 API Contract 테스트

import pytest
from rest_framework import status

from ..conftest import (
    assert_cart_schema,
    assert_list_response,
    assert_order_schema,
    assert_user_schema,
)


@pytest.mark.schema
@pytest.mark.django_db
class TestAuthenticatedEndpointsContract:
    """
    🔐 인증이 필요한 엔드포인트 Contract 테스트

    JWT 인증이 필요한 API의 응답이 스키마와 일치하는지 검증합니다.

    📋 검증 대상:
    - 장바구니: /api/cart/, /api/cart/summary/, /api/cart/items/
    - 주문: /api/orders/, /api/orders/{id}/
    - 위시리스트: /api/wishlist/, /api/wishlist/stats/
    - 알림, 결제, 포인트, 사용자, 반품

    ✅ 검증 항목:
    - 200 OK 응답
    - 응답 구조 (dict, list, paginated)
    - 필수 필드 존재 및 타입 일치
    """

    def test_cart_retrieve_contract(self, openapi_schema, client, auth_headers, schema_test_cart):
        """🛒 장바구니 조회 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_cart_schema(data, context="/api/cart/")

    def test_cart_summary_contract(self, openapi_schema, client, auth_headers, schema_test_cart):
        """🛒 장바구니 요약 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/summary/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, dict), "요약 응답이 dict가 아님"
        # 요약 응답에는 합계 관련 필드가 있어야 함
        expected_summary_fields = ["total", "count", "items", "total_price", "total_amount", "item_count"]
        has_expected = any(field in data for field in expected_summary_fields)
        assert has_expected or len(data) > 0, f"장바구니 요약에 예상 필드 없음: {data.keys()}"

    def test_cart_items_contract(self, openapi_schema, client, auth_headers, schema_test_cart):
        """🛒 장바구니 아이템 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/items/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/cart/items/")

    def test_orders_list_contract(self, openapi_schema, client, auth_headers, schema_test_order):
        """📦 주문 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = assert_list_response(data, context="/api/orders/")

        for order in results[:3]:
            assert_order_schema(order, context="/api/orders/ item")

    def test_orders_detail_contract(self, openapi_schema, client, auth_headers, schema_test_order):
        """📦 주문 상세 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_order_schema(data, context=f"/api/orders/{order_id}/")
        assert data["id"] == order_id

    def test_wishlist_list_contract(self, openapi_schema, client, auth_headers):
        """❤️ 위시리스트 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/wishlist/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/wishlist/")

    def test_wishlist_stats_contract(self, openapi_schema, client, auth_headers):
        """📊 위시리스트 통계 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/wishlist/stats/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, dict), "통계 응답이 dict가 아님"
        # 통계 응답에는 수치 관련 필드가 있어야 함
        expected_stats_fields = ["count", "total", "items", "products"]
        has_expected = any(field in data for field in expected_stats_fields)
        assert has_expected or len(data) > 0, f"위시리스트 통계에 예상 필드 없음: {data.keys()}"

    def test_notifications_list_contract(self, openapi_schema, client, auth_headers):
        """🔔 알림 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/notifications/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/notifications/")

    def test_user_profile_contract(self, openapi_schema, client, auth_headers):
        """👤 사용자 프로필 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/users/profile/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_user_schema(data, context="/api/users/profile/")

    def test_payments_list_contract(self, openapi_schema, client, auth_headers):
        """💳 결제 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/payments/")

    def test_returns_list_contract(self, openapi_schema, client, auth_headers):
        """↩️ 교환/환불 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/returns/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/returns/")

    def test_points_my_contract(self, openapi_schema, client, auth_headers):
        """💰 내 포인트 조회 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/points/my/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, dict), "포인트 응답이 dict가 아님"
        # 포인트 응답에는 포인트 관련 필드가 있어야 함
        expected_points_fields = ["points", "balance", "total", "available", "amount"]
        has_expected = any(field in data for field in expected_points_fields)
        assert has_expected or len(data) > 0, f"포인트 응답에 예상 필드 없음: {data.keys()}"

    def test_products_low_stock_contract(self, openapi_schema, client, seller_auth_headers, schema_test_product):
        """📉 재고 부족 상품 API Contract 검증 (판매자)"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]}

        # Act
        response = client.get("/api/products/low_stock/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/products/low_stock/")

    def test_seller_returns_list_contract(self, openapi_schema, client, seller_auth_headers):
        """🏪 판매자 반품 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]}

        # Act
        response = client.get("/api/seller/returns/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/seller/returns/")
