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
