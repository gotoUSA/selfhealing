# test_path_parameters.py
# Path Parameter Edge Cases 테스트

import pytest
from rest_framework import status

from ..conftest import (
    assert_error_response,
    assert_order_schema,
    assert_product_schema,
)


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPathParameterEdgeCases:
    """
    🔗 Path Parameter Edge Case 테스트

    경로 파라미터({id})에 다양한 형식의 값을 주입하여
    API가 적절히 처리하는지 검증합니다.

    📋 테스트 시나리오:
    - 유효한 ID → 200
    - 존재하지 않는 ID → 404
    - 음수 ID → 400 or 404
    - 문자열 ID → 400 or 404
    - float ID → 400 or 404
    - 인증 없이 보호된 리소스 접근 → 401

    ✅ 핵심 검증:
    - 5xx 에러 발생하지 않음
    - 적절한 4xx 에러 코드 반환
    - 에러 응답 구조 일관성
    """

    def test_product_detail_valid_id(self, client, schema_test_product):
        """✅ 유효한 상품 ID → 200 + 스키마 검증"""
        product_id = schema_test_product.id

        response = client.get(f"/api/products/{product_id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_product_schema(data, context=f"/api/products/{product_id}/")
        assert data["id"] == product_id

    def test_product_detail_nonexistent_id(self, client):
        """❌ 존재하지 않는 상품 ID → 404 + 에러 응답 검증"""
        invalid_id = 99999999

        response = client.get(f"/api/products/{invalid_id}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert_error_response(data, context=f"/api/products/{invalid_id}/")

    @pytest.mark.parametrize(
        "invalid_id,description",
        [
            ("-1", "음수 ID"),
            ("0", "0 ID"),
            ("abc", "문자열 ID"),
            ("1.5", "float ID"),
            ("null", "null 문자열"),
            ("1; DROP TABLE--", "SQL Injection"),
        ],
        ids=["negative", "zero", "string", "float", "null_str", "sql_injection"],
    )
    def test_product_detail_invalid_id_format(self, client, invalid_id, description):
        """
        ❌ 잘못된 ID 형식 → 400 or 404

        다양한 형태의 유효하지 않은 ID를 주입하여
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 핵심: 5xx 에러 발생하지 않아야 함
        """
        response = client.get(f"/api/products/{invalid_id}/")

        # 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 또는 404
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

    def test_order_detail_unauthorized(self, client, schema_test_order):
        """🔒 인증 없이 주문 상세 접근 → 401"""
        order_id = schema_test_order.id

        response = client.get(f"/api/orders/{order_id}/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()
        assert_error_response(data, context=f"/api/orders/{order_id}/ (unauthorized)")

    def test_order_detail_valid_id(self, client, auth_headers, schema_test_order):
        """✅ 유효한 주문 ID (인증됨) → 200"""
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        order_id = schema_test_order.id

        response = client.get(f"/api/orders/{order_id}/", **headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert_order_schema(data, context=f"/api/orders/{order_id}/")

    def test_category_detail_valid_id(self, client, schema_test_category):
        """✅ 유효한 카테고리 ID → 200"""
        category_id = schema_test_category.id

        response = client.get(f"/api/categories/{category_id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "id" in data
        assert data["id"] == category_id

    def test_notification_nonexistent_id(self, client, auth_headers):
        """❌ 존재하지 않는 알림 ID → 404"""
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        response = client.get("/api/notifications/99999999/", **headers)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_payment_nonexistent_id(self, client, auth_headers):
        """❌ 존재하지 않는 결제 ID → 404"""
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        response = client.get("/api/payments/99999999/", **headers)

        assert response.status_code == status.HTTP_404_NOT_FOUND
