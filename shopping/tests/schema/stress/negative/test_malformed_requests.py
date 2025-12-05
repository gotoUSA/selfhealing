"""
잘못된 요청 형식 테스트
======================

TestMalformedRequests: 잘못된 JSON, 잘못된 Content-Type, 빈 바디, 예상치 못한 필드
"""

import json

import pytest
from rest_framework import status


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestMalformedRequests:
    """
    📝 잘못된 요청 형식 테스트

    잘못된 JSON, 잘못된 Content-Type 등
    형식이 잘못된 요청에 대한 처리를 검증합니다.

    📋 테스트 시나리오:
    - 잘못된 JSON 형식
    - 잘못된 Content-Type
    - 빈 요청 본문
    - 예상치 못한 필드
    """

    def test_malformed_json_body(self, client, auth_headers):
        """
        잘못된 JSON 형식 요청 테스트

        파싱 불가능한 JSON을 전송하면
        400 Bad Request가 반환되어야 합니다.

        🔍 검증 포인트:
        - JSON 파싱 에러 처리
        - 명확한 에러 메시지
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        malformed_jsons = [
            "not a json at all",
            "{invalid json}",
            "{'single': 'quotes'}",
            '{"unclosed": "brace"',
            '{"trailing": "comma",}',
        ]

        for malformed in malformed_jsons:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=malformed,
                content_type="application/json",
                **headers,
            )

            # Assert
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"잘못된 JSON이 허용됨: {response.status_code}\n" f"malformed={repr(malformed)}"
            )

    def test_wrong_content_type(self, client, auth_headers, schema_test_product):
        """
        잘못된 Content-Type 테스트

        JSON 데이터를 다른 Content-Type으로 전송하면
        적절한 에러가 반환되어야 합니다.

        🔍 검증 포인트:
        - Content-Type 검증
        - 415 Unsupported Media Type 또는 400 Bad Request
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": schema_test_product.id, "quantity": 1}
        wrong_content_types = [
            "text/plain",
            "text/html",
            "application/xml",
        ]

        for content_type in wrong_content_types:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type=content_type,
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"잘못된 Content-Type에서 서버 에러 발생!\n" f"content_type={content_type}"

    def test_extra_unexpected_fields(self, client, auth_headers, schema_test_product):
        """
        예상치 못한 필드 포함 테스트

        스키마에 정의되지 않은 추가 필드가 있을 때
        무시되거나 에러가 발생하는지 확인합니다.

        🔍 검증 포인트:
        - 추가 필드 무시 또는 에러
        - 정상 동작에 영향 없음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "product_id": schema_test_product.id,
            "quantity": 1,
            "unexpected_field": "should be ignored",
            "another_field": 12345,
            "__proto__": {"admin": True},  # Prototype pollution 시도
        }

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음 (추가 필드로 인한 서버 오류 없음)
        assert response.status_code < 500, f"추가 필드로 인해 서버 에러 발생!\n" f"data={data}"
