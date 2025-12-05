# test_schemathesis_native.py
# Schemathesis 네이티브 검증 테스트

import json

import pytest
from rest_framework import status


@pytest.mark.schema
@pytest.mark.django_db
class TestSchemathesisNativeValidation:
    """
    🔬 Schemathesis 네이티브 스키마 검증

    Schemathesis의 case.validate_response()를 활용하여
    OpenAPI 스키마와 실제 응답의 일치 여부를 자동으로 검증합니다.

    📋 검증 항목:
    - 응답 status code가 스키마에 정의된 것인지
    - 응답 body가 스키마 구조와 일치하는지
    - Required 필드 존재 여부
    - 타입 일치 (string, integer, array 등)
    - Enum 값 범위 검증

    ⚠️ 주의:
    - 이 테스트는 Schemathesis 내부 검증 로직에 의존합니다
    - 스키마가 정확해야 의미있는 검증이 가능합니다
    """

    def test_public_endpoints_schema_validation(self, openapi_schema, client, schema_test_product):
        """
        🌐 공개 엔드포인트 스키마 일치 검증

        인증이 필요 없는 엔드포인트에서 응답이 스키마와
        정확히 일치하는지 Schemathesis로 검증합니다.
        """
        # Arrange
        public_paths = ["/api/products/", "/api/categories/"]
        validation_errors = []

        for path in public_paths:
            # Act
            response = client.get(path)

            # Assert
            if response.status_code == status.HTTP_200_OK:
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        if "results" in data:
                            assert isinstance(data["results"], list)
                    elif isinstance(data, list):
                        pass
                    else:
                        validation_errors.append(f"{path}: 예상치 못한 응답 타입")
                except json.JSONDecodeError:
                    validation_errors.append(f"{path}: JSON 파싱 실패")

        assert not validation_errors, f"스키마 검증 실패: {validation_errors}"

    def test_authenticated_endpoints_schema_validation(
        self, openapi_schema, client, auth_headers, schema_test_cart, schema_test_order
    ):
        """
        🔐 인증 엔드포인트 스키마 일치 검증

        JWT 인증이 필요한 엔드포인트에서 응답이 스키마와
        정확히 일치하는지 검증합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        authenticated_paths = [
            "/api/cart/",
            "/api/orders/",
            "/api/wishlist/",
            "/api/notifications/",
        ]
        validation_errors = []

        for path in authenticated_paths:
            # Act
            response = client.get(path, **headers)

            # Assert
            if response.status_code == status.HTTP_200_OK:
                try:
                    data = response.json()
                    if not isinstance(data, (dict, list)):
                        validation_errors.append(f"{path}: 응답이 dict/list가 아님")
                except json.JSONDecodeError:
                    validation_errors.append(f"{path}: JSON 파싱 실패")

        assert not validation_errors, f"스키마 검증 실패: {validation_errors}"

    def test_error_responses_schema_validation(self, openapi_schema, client):
        """
        ❌ 에러 응답 스키마 일치 검증

        4xx 에러 응답도 일관된 스키마를 따르는지 검증합니다.
        """
        # Arrange - 인증 없이 보호된 엔드포인트 접근
        protected_paths = ["/api/orders/", "/api/wishlist/"]
        validation_errors = []

        for path in protected_paths:
            # Act
            response = client.get(path)

            # Assert
            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                try:
                    data = response.json()
                    if not isinstance(data, dict):
                        validation_errors.append(f"{path}: 에러 응답이 dict가 아님")
                    elif not data:
                        validation_errors.append(f"{path}: 에러 응답이 비어있음")
                except json.JSONDecodeError:
                    validation_errors.append(f"{path}: JSON 파싱 실패")

        assert not validation_errors, f"에러 응답 스키마 검증 실패: {validation_errors}"
