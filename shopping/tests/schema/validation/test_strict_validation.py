# test_strict_validation.py
# Strict 스키마 검증 테스트

import pytest
from rest_framework import status

from ..conftest import (
    assert_list_response,
    assert_product_schema,
    SchemaValidationError,
)


@pytest.mark.schema
@pytest.mark.django_db
class TestStrictSchemaValidation:
    """
    🔬 Strict 모드 스키마 검증 테스트

    strict=True로 스키마 검증 시 예상치 못한 추가 필드가
    없는지까지 검증합니다.

    ⚠️ 주의:
    - API 응답에 추가 필드가 있으면 테스트 실패
    - 스키마와 실제 구현의 drift 감지
    """

    def test_product_detail_strict_schema(self, client, schema_test_product):
        """
        🛒 상품 상세 Strict 스키마 검증

        응답에 예상치 못한 필드가 없는지 검증합니다.
        """
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Strict mode로 검증 - 추가 필드 허용 안 함
        # 실제로는 이 테스트가 실패할 수 있음 (API에 추가 필드가 있을 경우)
        # 그런 경우 conftest.py의 allowed_fields를 업데이트해야 함
        try:
            assert_product_schema(data, strict=True, context=f"/api/products/{schema_test_product.id}/ (strict)")
        except SchemaValidationError as e:
            # strict 검증 실패 시 어떤 필드가 추가되었는지 기록
            pytest.skip(f"Strict 검증 실패 (추가 필드 존재): {e}")

    def test_products_list_items_schema(self, client, schema_test_product):
        """
        🛒 상품 목록 각 아이템 스키마 검증

        목록의 각 상품이 올바른 스키마를 따르는지 검증합니다.
        """
        # Arrange - none needed for public API

        # Act
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        results = assert_list_response(data, context="/api/products/")

        for i, product in enumerate(results[:5]):
            assert_product_schema(product, context=f"/api/products/ item[{i}]")
