# test_response_structure_validation.py
# 응답 구조 검증 테스트

"""
📋 응답 구조 검증 테스트
======================

가이드라인 01_SUCCESS_RESPONSE_SCHEMA.md에 따라
API 응답의 구조 유형을 검증합니다.

✅ 검증 항목:
- 단일 객체 응답 (dict)
- 단순 목록 응답 (list)
- 페이지네이션 응답 (dict with count, results)
- HTTP 상태 코드 일관성

📁 관련 가이드:
- guides/01_SUCCESS_RESPONSE_SCHEMA.md
"""

import pytest
from rest_framework import status

from ..conftest import (
    assert_list_response,
    assert_paginated_response,
)


@pytest.mark.schema
@pytest.mark.django_db
class TestSingleObjectResponse:
    """
    📦 단일 객체 응답 검증 테스트

    상세 조회 API가 dict 형태의 단일 객체를 반환하는지 검증합니다.

    ✅ 검증 항목:
    - 응답 타입이 dict
    - id 필드 존재 및 요청 ID와 일치
    """

    def test_product_detail_returns_dict(self, client, schema_test_product):
        """
        🛒 상품 상세 API가 dict를 반환
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 단일 객체 구조 검증
        assert isinstance(data, dict), f"응답이 dict가 아님: {type(data)}"
        assert "id" in data, "id 필드 누락"
        assert data["id"] == product_id, f"ID 불일치: {data['id']} != {product_id}"

    def test_category_detail_returns_dict(self, client, schema_test_category):
        """
        📂 카테고리 상세 API가 dict를 반환
        """
        # Arrange
        category_id = schema_test_category.id

        # Act
        response = client.get(f"/api/categories/{category_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 단일 객체 구조 검증
        assert isinstance(data, dict), f"응답이 dict가 아님: {type(data)}"
        assert "id" in data, "id 필드 누락"
        assert data["id"] == category_id, "ID 불일치"

    def test_order_detail_returns_dict(self, client, auth_headers, schema_test_order):
        """
        📦 주문 상세 API가 dict를 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 단일 객체 구조 검증
        assert isinstance(data, dict), f"응답이 dict가 아님: {type(data)}"
        assert "id" in data, "id 필드 누락"
        assert data["id"] == order_id, "ID 불일치"

    def test_user_profile_returns_dict(self, client, auth_headers):
        """
        👤 사용자 프로필 API가 dict를 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/users/profile/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 단일 객체 구조 검증
        assert isinstance(data, dict), f"응답이 dict가 아님: {type(data)}"
        assert "id" in data, "id 필드 누락"


@pytest.mark.schema
@pytest.mark.django_db
class TestListResponse:
    """
    📋 목록 응답 검증 테스트

    목록 조회 API가 list 또는 paginated 형태를 반환하는지 검증합니다.

    ✅ 검증 항목:
    - 응답 타입이 list 또는 paginated dict
    - results가 list 타입
    """

    def test_product_list_returns_list_or_paginated(self, client, schema_test_product):
        """
        🛒 상품 목록 API가 list 또는 paginated 응답 반환
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 구조 검증 (헬퍼 함수 사용)
        results = assert_list_response(data, context="/api/products/")
        assert isinstance(results, list), "results가 list가 아님"

    def test_category_list_returns_list_or_paginated(self, client, schema_test_category):
        """
        📂 카테고리 목록 API가 list 또는 paginated 응답 반환
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/categories/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 구조 검증
        results = assert_list_response(data, context="/api/categories/")
        assert isinstance(results, list), "results가 list가 아님"

    def test_order_list_returns_list_or_paginated(self, client, auth_headers, schema_test_order):
        """
        📦 주문 목록 API가 list 또는 paginated 응답 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 구조 검증
        results = assert_list_response(data, context="/api/orders/")
        assert isinstance(results, list), "results가 list가 아님"

    def test_wishlist_returns_list_or_paginated(self, client, auth_headers):
        """
        ❤️ 위시리스트 API가 list 또는 paginated 응답 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/wishlist/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 구조 검증
        results = assert_list_response(data, context="/api/wishlist/")
        assert isinstance(results, list), "results가 list가 아님"

    def test_notifications_returns_list_or_paginated(self, client, auth_headers):
        """
        🔔 알림 목록 API가 list 또는 paginated 응답 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/notifications/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 목록 구조 검증
        results = assert_list_response(data, context="/api/notifications/")
        assert isinstance(results, list), "results가 list가 아님"


@pytest.mark.schema
@pytest.mark.django_db
class TestPaginatedResponse:
    """
    📄 페이지네이션 응답 검증 테스트

    페이지네이션이 적용된 API가 올바른 구조를 반환하는지 검증합니다.

    ✅ 검증 항목:
    - count 필드 존재 및 int 타입
    - results 필드 존재 및 list 타입
    - next/previous 필드 (존재 시 str 또는 null)
    """

    def test_product_list_pagination_structure(self, client, schema_test_product):
        """
        🛒 상품 목록 페이지네이션 구조 검증
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 페이지네이션 응답일 경우 구조 검증
        if isinstance(data, dict) and "results" in data:
            assert_paginated_response(data, context="/api/products/")

    def test_product_list_pagination_fields(self, client, schema_test_product):
        """
        🛒 상품 목록 페이지네이션 필드 타입 검증
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        if isinstance(data, dict) and "count" in data:
            # count 타입 검증
            assert isinstance(data["count"], int), f"count 타입 불일치: {type(data['count'])}"

            # results 타입 검증
            assert isinstance(data["results"], list), f"results 타입 불일치: {type(data['results'])}"

            # next/previous 검증 (존재 시)
            if "next" in data and data["next"] is not None:
                assert isinstance(data["next"], str), f"next 타입 불일치: {type(data['next'])}"

            if "previous" in data and data["previous"] is not None:
                assert isinstance(data["previous"], str), f"previous 타입 불일치: {type(data['previous'])}"

    def test_order_list_pagination_structure(self, client, auth_headers, schema_test_order):
        """
        📦 주문 목록 페이지네이션 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 페이지네이션 응답일 경우 구조 검증
        if isinstance(data, dict) and "results" in data:
            assert_paginated_response(data, context="/api/orders/")


@pytest.mark.schema
@pytest.mark.django_db
class TestHttpStatusCodeConsistency:
    """
    📊 HTTP 상태 코드 일관성 검증 테스트

    각 HTTP 메서드에 대해 올바른 상태 코드가 반환되는지 검증합니다.

    ✅ 검증 항목:
    - GET 조회: 200 OK
    - POST 생성: 201 Created 또는 200 OK
    - 존재하지 않음: 404 Not Found
    - 인증 필요: 401 Unauthorized
    """

    def test_get_existing_resource_200(self, client, schema_test_product):
        """
        ✅ 존재하는 리소스 GET → 200 OK
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_get_nonexistent_resource_404(self, client):
        """
        ❌ 존재하지 않는 리소스 GET → 404 Not Found
        """
        # Arrange
        invalid_id = 99999999

        # Act
        response = client.get(f"/api/products/{invalid_id}/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_protected_without_auth_401(self, client):
        """
        🔒 인증 없이 보호된 리소스 GET → 401 Unauthorized
        """
        # Arrange - 인증 헤더 없이

        # Act
        response = client.get("/api/orders/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_list_200(self, client):
        """
        ✅ 공개 목록 GET → 200 OK
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_get_authenticated_list_200(self, client, auth_headers):
        """
        ✅ 인증된 목록 GET → 200 OK
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.schema
@pytest.mark.django_db
class TestSpecialResponseStructures:
    """
    🔧 특수 응답 구조 검증 테스트

    카테고리 트리, 통계 등 특수한 구조의 응답을 검증합니다.
    """

    def test_category_tree_structure(self, client, schema_test_category):
        """
        🌲 카테고리 트리 API 구조 검증
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/categories/tree/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 트리 구조 (list of dict with children)
        assert isinstance(data, (list, dict)), "트리 응답이 list 또는 dict가 아님"

    def test_cart_summary_structure(self, client, auth_headers, schema_test_cart):
        """
        🛒 장바구니 요약 API 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/cart/summary/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 요약 응답은 dict
        assert isinstance(data, dict), "요약 응답이 dict가 아님"

    def test_wishlist_stats_structure(self, client, auth_headers):
        """
        📊 위시리스트 통계 API 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/wishlist/stats/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 통계 응답은 dict
        assert isinstance(data, dict), "통계 응답이 dict가 아님"

    def test_popular_products_structure(self, client, schema_test_product):
        """
        🔥 인기 상품 API 구조 검증
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/popular/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 인기 상품 목록
        results = assert_list_response(data, context="/api/products/popular/")
        assert isinstance(results, list), "인기 상품 결과가 list가 아님"

    def test_best_rating_products_structure(self, client, schema_test_product):
        """
        ⭐ 평점순 상품 API 구조 검증
        """
        # Arrange - none needed

        # Act
        response = client.get("/api/products/best_rating/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 평점순 상품 목록
        results = assert_list_response(data, context="/api/products/best_rating/")
        assert isinstance(results, list), "평점순 상품 결과가 list가 아님"


@pytest.mark.schema
@pytest.mark.django_db
class TestEmptyListResponse:
    """
    📭 빈 목록 응답 검증 테스트

    데이터가 없을 때도 올바른 구조가 반환되는지 검증합니다.
    """

    def test_empty_order_list_structure(self, client, auth_headers):
        """
        📭 빈 주문 목록 구조 검증

        새 사용자의 빈 주문 목록도 올바른 구조여야 함
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 빈 목록도 올바른 구조
        results = assert_list_response(data, context="/api/orders/ (may be empty)")
        assert isinstance(results, list), "빈 목록도 list 타입이어야 함"

    def test_empty_wishlist_structure(self, client, auth_headers):
        """
        📭 빈 위시리스트 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/wishlist/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 빈 목록도 올바른 구조
        results = assert_list_response(data, context="/api/wishlist/ (may be empty)")
        assert isinstance(results, list), "빈 위시리스트도 list 타입이어야 함"

    def test_empty_notifications_structure(self, client, auth_headers):
        """
        📭 빈 알림 목록 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/notifications/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 빈 목록도 올바른 구조
        results = assert_list_response(data, context="/api/notifications/ (may be empty)")
        assert isinstance(results, list), "빈 알림 목록도 list 타입이어야 함"

    def test_search_no_results_structure(self, client):
        """
        📭 검색 결과 없음 구조 검증

        검색 결과가 없어도 올바른 구조여야 함
        """
        # Arrange
        nonexistent_query = "xyznonexistent12345"

        # Act
        response = client.get(f"/api/products/?search={nonexistent_query}")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 빈 결과도 올바른 구조
        results = assert_list_response(data, context="/api/products/?search=nonexistent")
        assert isinstance(results, list), "빈 검색 결과도 list 타입이어야 함"
