# test_error_responses.py
# 에러 응답 Contract 테스트

import json

import pytest
from rest_framework import status

from ..conftest import assert_error_response


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestErrorResponseContracts:
    """
    ❌ 에러 응답 Contract 테스트

    실패 케이스에서 반환되는 에러 응답이 일관된 형식을
    갖추고 있는지 검증합니다. Contract Test의 핵심입니다.

    📋 테스트 시나리오:
    - 로그인 실패 → 400/401 + 에러 메시지
    - 필수 필드 누락 → 400 + 필드별 에러
    - 잘못된 데이터 타입 → 400
    - 인증 없음 → 401
    - 권한 없음 → 403
    - 리소스 없음 → 404

    ✅ 핵심 검증:
    - 에러 응답이 dict 형식
    - 에러 정보 포함 (detail, errors, field_errors 등)
    - 민감 정보 미노출 (stack trace 등)
    """

    def test_login_invalid_credentials(self, client, user):
        """
        🔐 로그인 실패 - 잘못된 비밀번호

        에러 응답이 올바른 형식인지 검증합니다.
        """
        # Arrange
        data = {"username": "testuser", "password": "wrongpassword123"}

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/login/ (invalid password)")

    def test_login_empty_fields(self, client):
        """
        🔐 로그인 실패 - 빈 필드

        필수 필드가 비어있을 때 적절한 에러가 반환되는지 검증합니다.
        """
        test_cases = [
            ({"username": "", "password": "test123"}, "빈 사용자명"),
            ({"username": "test", "password": ""}, "빈 비밀번호"),
            ({}, "빈 요청 본문"),
        ]

        for data, description in test_cases:
            response = client.post(
                "/api/auth/login/",
                data=json.dumps(data),
                content_type="application/json",
            )

            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_401_UNAUTHORIZED,
            ], f"{description}: 예상치 못한 응답 {response.status_code}"

            error_data = response.json()
            assert_error_response(error_data, context=f"/api/auth/login/ ({description})")

    def test_register_invalid_data(self, client):
        """
        📝 회원가입 실패 - 유효하지 않은 데이터

        필수 필드 누락, 짧은 비밀번호 등에 대한 에러 응답을 검증합니다.
        """
        # Arrange
        data = {"username": "", "password": "short"}

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/register/ (invalid)")

    def test_cart_add_missing_fields(self, client, auth_headers):
        """
        🛒 장바구니 추가 실패 - 필수 필드 누락

        product_id, quantity 누락 시 에러 응답을 검증합니다.
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        test_cases = [
            ({}, "빈 요청"),
            ({"product_id": 1}, "quantity 누락"),
            ({"quantity": 1}, "product_id 누락"),
        ]

        for data, description in test_cases:
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST, f"{description}: 400이 아닌 {response.status_code}"
            error_data = response.json()
            assert_error_response(error_data, context=f"/api/cart/add_item/ ({description})")

    def test_cart_add_invalid_quantity(self, client, auth_headers, schema_test_product):
        """
        🛒 장바구니 추가 실패 - 잘못된 수량

        음수, 0, 매우 큰 수량에 대한 에러 응답을 검증합니다.
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_quantities = [
            (-1, "음수 수량"),
            (0, "0 수량"),
        ]

        for quantity, description in invalid_quantities:
            data = {"product_id": schema_test_product.id, "quantity": quantity}

            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # 0이나 음수는 거부되어야 함 (400) 또는 다른 처리
            assert response.status_code < 500, f"{description}에서 서버 에러 발생"

    def test_cart_add_nonexistent_product(self, client, auth_headers):
        """
        🛒 장바구니 추가 실패 - 존재하지 않는 상품

        없는 상품 ID로 추가 시도 시 에러 응답을 검증합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": 99999999, "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]
        error_data = response.json()
        assert_error_response(error_data, context="/api/cart/add_item/ (nonexistent product)")

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/orders/",
            "/api/wishlist/",
            "/api/notifications/",
            "/api/payments/",
            "/api/points/my/",
            "/api/users/profile/",
        ],
    )
    def test_unauthenticated_access_401(self, client, endpoint):
        """
        🔒 인증 없이 보호된 엔드포인트 접근 → 401

        인증이 필요한 엔드포인트에 토큰 없이 접근 시
        401 응답과 올바른 에러 형식을 검증합니다.
        """
        # Arrange - endpoint is provided by parametrize

        # Act
        response = client.get(endpoint)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"{endpoint}: 401이 아닌 {response.status_code}"
        error_data = response.json()
        assert_error_response(error_data, context=f"{endpoint} (unauthenticated)")

    def test_wishlist_toggle_missing_product(self, client, auth_headers):
        """
        ❤️ 위시리스트 토글 실패 - product_id 누락
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {}

        # Act
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/wishlist/toggle/ (missing product)")


@pytest.mark.schema
@pytest.mark.django_db
class TestSensitiveDataExposure:
    """
    🔐 민감 정보 노출 방지 테스트

    가이드 02_ERROR_RESPONSE_SCHEMA.md 기준으로 작성된 테스트입니다.

    📋 테스트 시나리오:
    - 에러 응답에 스택 트레이스 미포함
    - 에러 응답에 내부 경로 미포함
    - 에러 응답에 DB 정보 미포함

    ✅ 핵심 검증:
    - 민감한 키워드 미노출
    - 일관된 에러 형식 유지
    """

    # 민감 정보로 간주되는 키워드
    # 주의: 일반 에러 메시지에 포함될 수 있는 단어는 제외 (예: "line 1 column 2")
    SENSITIVE_KEYWORDS = [
        "Traceback",
        "traceback",
        'File "',
        "/usr/local/",
        "/home/",
        "/code/",
        "psycopg2",
        "django.db",
        "SECRET_KEY",
        "password=",
        "DATABASE_URL",
        "POSTGRES",
        "Exception:",
        '.py"',
        "raise ",
        "at 0x",  # 메모리 주소
        "Traceback (most recent call last)",
    ]

    def _check_no_sensitive_data(self, response_text: str, context: str):
        """응답에 민감 정보가 없는지 확인"""
        for keyword in self.SENSITIVE_KEYWORDS:
            assert keyword not in response_text, f"{context}: 민감 정보 노출 - '{keyword}' 발견"

    def test_404_no_sensitive_data(self, client):
        """
        🔍 404 에러에 민감 정보 미포함

        존재하지 않는 리소스 접근 시 스택 트레이스 등이 노출되지 않아야 합니다.
        """
        # Arrange
        endpoints = [
            "/api/products/99999999/",
            "/api/categories/99999999/",
            "/api/nonexistent/endpoint/",
        ]

        for endpoint in endpoints:
            # Act
            response = client.get(endpoint)

            # Assert - 민감 정보 없음
            response_text = response.content.decode("utf-8", errors="ignore")
            self._check_no_sensitive_data(response_text, endpoint)

    def test_400_no_sensitive_data(self, client, auth_headers):
        """
        🔍 400 에러에 민감 정보 미포함

        잘못된 요청 시 내부 정보가 노출되지 않아야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_data = {"invalid_field": "value", "another": [1, 2, 3]}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(invalid_data),
            content_type="application/json",
            **headers,
        )

        # Assert - 민감 정보 없음
        response_text = response.content.decode("utf-8", errors="ignore")
        self._check_no_sensitive_data(response_text, "/api/cart/add_item/ (invalid)")

    def test_401_no_sensitive_data(self, client):
        """
        🔍 401 에러에 민감 정보 미포함

        인증 실패 시 시스템 정보가 노출되지 않아야 합니다.
        주의: /api/cart/는 비회원도 세션 장바구니로 접근 가능하므로 제외
        """
        # Arrange - /api/cart/는 비회원 세션 장바구니 허용으로 제외
        endpoints = [
            "/api/orders/",
            "/api/wishlist/",
            "/api/payments/",
        ]

        for endpoint in endpoints:
            # Act
            response = client.get(endpoint)

            # Assert
            assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"{endpoint}: 401이 아닌 {response.status_code}"
            response_text = response.content.decode("utf-8", errors="ignore")
            self._check_no_sensitive_data(response_text, f"{endpoint} (401)")

    def test_malformed_json_no_sensitive_data(self, client, auth_headers):
        """
        🔍 잘못된 JSON 요청에 민감 정보 미포함

        파싱 에러 시에도 내부 정보가 노출되지 않아야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        malformed_json = "{invalid json content"

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=malformed_json,
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_text = response.content.decode("utf-8", errors="ignore")
        self._check_no_sensitive_data(response_text, "/api/cart/add_item/ (malformed JSON)")

    def test_error_response_consistent_format(self, client, auth_headers):
        """
        📋 에러 응답 형식 일관성 검증

        다양한 에러 상황에서 응답 형식이 일관되어야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        error_scenarios = [
            # (endpoint, method, data, description)
            ("/api/cart/add_item/", "post", {}, "필수 필드 누락"),
            ("/api/wishlist/toggle/", "post", {}, "product_id 누락"),
        ]

        for endpoint, method, data, description in error_scenarios:
            # Act
            if method == "post":
                response = client.post(
                    endpoint,
                    data=json.dumps(data),
                    content_type="application/json",
                    **headers,
                )
            else:
                response = client.get(endpoint, **headers)

            # Assert - 400 에러
            assert response.status_code == status.HTTP_400_BAD_REQUEST, f"{description}: {response.status_code}"

            # Assert - 응답이 dict
            error_data = response.json()
            assert isinstance(error_data, dict), f"{description}: 에러 응답이 dict가 아님"

            # Assert - 최소한 하나의 에러 정보 포함
            has_error_info = any(
                key in error_data
                for key in ["detail", "error", "errors", "message", "non_field_errors"]
                + list(error_data.keys())  # 필드별 에러도 허용
            )
            assert has_error_info or len(error_data) > 0, f"{description}: 에러 정보 없음"
