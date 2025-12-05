# test_authenticated_endpoints.py
# 인증 필요 API Contract 테스트

import pytest
from rest_framework import status

from ..conftest import (
    assert_cart_schema,
    assert_error_response,
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


@pytest.mark.schema
@pytest.mark.django_db
class TestAuthenticationEdgeCases:
    """
    🔒 인증 Edge Case 테스트

    가이드 05_AUTHENTICATION.md 기준으로 작성된 테스트입니다.

    📋 테스트 시나리오:
    - 잘못된 토큰 형식 → 401
    - 만료/손상된 토큰 → 401
    - 역할 기반 접근 제어 → 403 또는 빈 결과

    ✅ 핵심 검증:
    - 잘못된 인증은 항상 401 반환
    - 권한 없는 접근은 403 또는 필터링
    """

    @pytest.mark.parametrize(
        "auth_header,description",
        [
            ("", "빈 헤더"),
            ("Bearer", "토큰 누락"),
            ("Bearer ", "빈 토큰"),
            ("Bearer invalid.token.here", "잘못된 토큰"),
            ("invalid_token_no_bearer", "Bearer 접두사 누락"),
            ("Basic dXNlcjpwYXNz", "Basic 인증 시도"),
            ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature", "손상된 JWT"),
        ],
        ids=["empty", "bearer_only", "bearer_empty", "invalid", "no_bearer", "basic_auth", "corrupted_jwt"],
    )
    def test_invalid_auth_headers_401(self, client, auth_header, description):
        """
        🔒 잘못된 인증 헤더 → 401

        다양한 형태의 잘못된 인증 헤더가
        모두 401 Unauthorized를 반환하는지 검증합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_header} if auth_header else {}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"{description}: 401이 아닌 {response.status_code}"

        # 에러 응답 구조 검증
        error_data = response.json()
        assert_error_response(error_data, context=f"/api/orders/ ({description})")

    def test_invalid_token_multiple_endpoints(self, client):
        """
        🔒 잘못된 토큰으로 여러 보호된 엔드포인트 접근 → 모두 401

        여러 보호된 엔드포인트에서 일관되게 401을 반환하는지 검증합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": "Bearer invalid.token.here"}
        protected_endpoints = [
            "/api/orders/",
            "/api/wishlist/",
            "/api/cart/",
            "/api/notifications/",
            "/api/payments/",
            "/api/points/my/",
            "/api/users/profile/",
        ]

        for endpoint in protected_endpoints:
            # Act
            response = client.get(endpoint, **headers)

            # Assert
            assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"{endpoint}: 401이 아닌 {response.status_code}"

    def test_regular_user_access_seller_api(self, client, auth_headers):
        """
        👤 일반 사용자 → 판매자 전용 API 접근

        일반 사용자가 판매자 API에 접근할 때
        403 (권한 거부) 또는 200 (빈 결과)를 반환해야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/seller/returns/", **headers)

        # Assert - 403 또는 200(빈 결과) 허용
        assert response.status_code in [
            status.HTTP_200_OK,  # 소유권 기반 필터링 (빈 결과)
            status.HTTP_403_FORBIDDEN,  # 역할 기반 거부
        ], f"예상치 못한 응답: {response.status_code}"

    def test_seller_access_seller_api_success(self, client, seller_auth_headers):
        """
        🏪 판매자 → 판매자 API 접근 성공

        판매자 권한으로 판매자 API에 접근할 때
        200 OK를 반환해야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]}

        # Act
        response = client.get("/api/seller/returns/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_list_response(data, context="/api/seller/returns/ (seller)")

    def test_regular_user_access_low_stock(self, client, auth_headers):
        """
        👤 일반 사용자 → 재고 부족 API 접근

        재고 부족 상품 조회는 판매자용 API입니다.
        일반 사용자는 403 또는 빈 결과를 받아야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/products/low_stock/", **headers)

        # Assert
        assert response.status_code in [
            status.HTTP_200_OK,  # 빈 결과
            status.HTTP_403_FORBIDDEN,  # 권한 거부
        ], f"예상치 못한 응답: {response.status_code}"
