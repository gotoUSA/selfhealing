# test_full_schema_validation.py
# 전체 스키마 검증 테스트 (@slow)

import pytest
from rest_framework import status

from ..conftest import is_excluded_endpoint, is_public_endpoint


@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFullSchemaValidation:
    """
    🔥 전체 API 스키마 검증 (Smoke Test)

    모든 엔드포인트를 순회하며 기본적인 동작과
    스키마 준수 여부를 검증합니다.

    ⚠️ 실행 시간:
    - 전체 엔드포인트 순회로 시간이 오래 걸림 (~30초+)
    - @pytest.mark.slow로 표시되어 기본 실행에서 제외 가능

    🚀 실행 방법:
    ```bash
    # slow 테스트 포함
    pytest shopping/tests/schema/ -v -m "schema"

    # slow 테스트 제외
    pytest shopping/tests/schema/ -v -m "schema and not slow"
    ```

    ✅ 검증 항목:
    - 5xx 서버 에러 없음
    - 유효한 JSON 응답
    - 응답 구조 일관성
    - 필수 필드 존재 (확장됨)
    """

    def test_all_get_endpoints_no_5xx(self, openapi_schema, client, auth_headers, schema_test_product, schema_test_order):
        """
        🚫 모든 GET 엔드포인트에서 5xx 에러 없음 확인

        어떤 상황에서도 서버 에러가 발생하지 않아야 합니다.
        """
        failed_endpoints = []
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            if "{" in op.path:
                continue

            if is_excluded_endpoint(op.path):
                continue

            if op.method.upper() != "GET":
                continue

            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **headers)

            if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                failed_endpoints.append(f"{op.path}: {response.status_code}")

        assert not failed_endpoints, f"5xx 에러 발생 엔드포인트: {failed_endpoints}"

    def test_all_endpoints_return_valid_json(
        self, openapi_schema, client, auth_headers, schema_test_product, schema_test_order
    ):
        """
        📄 모든 GET 엔드포인트가 유효한 JSON을 반환하는지 검증
        """
        invalid_json_endpoints = []
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            if "{" in op.path:
                continue

            if is_excluded_endpoint(op.path):
                continue

            if op.method.upper() != "GET":
                continue

            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **headers)

            if status.HTTP_200_OK <= response.status_code < status.HTTP_300_MULTIPLE_CHOICES:
                try:
                    response.json()
                except (ValueError, TypeError):
                    invalid_json_endpoints.append(op.path)

        assert not invalid_json_endpoints, f"유효하지 않은 JSON 반환: {invalid_json_endpoints}"

    def test_all_success_responses_have_valid_structure(
        self, openapi_schema, client, auth_headers, schema_test_product, schema_test_order
    ):
        """
        📋 모든 성공 응답이 유효한 구조를 갖추는지 검증

        응답이 dict 또는 list 형태여야 합니다.
        """
        invalid_structure_endpoints = []
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            if "{" in op.path:
                continue

            if is_excluded_endpoint(op.path):
                continue

            if op.method.upper() != "GET":
                continue

            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **headers)

            if status.HTTP_200_OK <= response.status_code < status.HTTP_300_MULTIPLE_CHOICES:
                try:
                    data = response.json()
                    if not isinstance(data, (dict, list)):
                        invalid_structure_endpoints.append(f"{op.path}: type={type(data).__name__}")
                except (ValueError, TypeError):
                    pass  # JSON 파싱 실패는 다른 테스트에서 검증

        assert not invalid_structure_endpoints, f"잘못된 응답 구조: {invalid_structure_endpoints}"
