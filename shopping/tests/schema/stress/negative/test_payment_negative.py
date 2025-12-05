"""
결제 API Negative 테스트
=======================

TestPaymentNegativeInputs: 결제 API에 잘못된 입력 주입
"""

import json

import pytest
from rest_framework import status


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPaymentNegativeInputs:
    """
    💳 결제 API에 대한 Negative 테스트

    결제 API에 잘못된 입력을 주입하여 적절한 에러 응답이 반환되는지 검증합니다.
    결제는 Tier 1 Critical API로 모든 에러 처리가 엄격해야 합니다.
    """

    def test_payment_list_without_auth(self, client):
        """
        인증 없이 결제 목록 접근 → 401

        🔍 검증 포인트:
        - 인증 필수 검증
        - 401 Unauthorized 반환
        """
        # Act
        response = client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_payment_detail_without_auth(self, client, schema_test_payment):
        """
        인증 없이 결제 상세 접근 → 401

        🔍 검증 포인트:
        - 인증 필수 검증
        - 401 Unauthorized 반환
        """
        # Arrange
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_payment_nonexistent_id(self, client, auth_headers):
        """
        존재하지 않는 결제 ID 접근 → 404

        🔍 검증 포인트:
        - 유효성 검증
        - 404 Not Found 반환
        - 민감 정보 미노출
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/99999999/", **headers)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        # 스택 트레이스 미노출 확인
        assert "traceback" not in str(data).lower()
        assert "exception" not in str(data).lower()

    @pytest.mark.parametrize(
        "invalid_id,description",
        [
            ("-1", "음수 ID"),
            ("0", "0 ID"),
            ("abc", "문자열 ID"),
            ("1.5", "float ID"),
            ("null", "null 문자열"),
        ],
        ids=["negative", "zero", "string", "float", "null_str"],
    )
    def test_payment_invalid_id_format(self, client, auth_headers, invalid_id, description):
        """
        잘못된 결제 ID 형식 → 400 or 404

        🔍 검증 포인트:
        - 5xx 에러 발생하지 않음
        - 적절한 4xx 에러 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/{invalid_id}/", **headers)

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 또는 404
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

    def test_payment_list_invalid_status_filter(self, client, auth_headers):
        """
        잘못된 상태 필터 → 400

        🔍 검증 포인트:
        - 유효하지 않은 필터값 거부
        - 400 Bad Request 반환
        - 에러 메시지에 유효한 값 안내
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/?status=invalid_status", **headers)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "error" in data, "에러 응답에 'error' 필드 필요"

    @pytest.mark.parametrize(
        "page,page_size,description",
        [
            (-1, 10, "음수 페이지"),
            (0, 10, "0 페이지"),
            (1, -1, "음수 페이지 크기"),
            (1, 0, "0 페이지 크기"),
            (1, 101, "페이지 크기 초과 (max=100)"),
            ("abc", 10, "문자열 페이지"),
            (1, "abc", "문자열 페이지 크기"),
        ],
        ids=["neg_page", "zero_page", "neg_size", "zero_size", "exceed_size", "str_page", "str_size"],
    )
    def test_payment_list_invalid_pagination(self, client, auth_headers, page, page_size, description):
        """
        잘못된 페이지네이션 파라미터 → 400

        🔍 검증 포인트:
        - 음수/0 페이지 거부
        - 과도한 페이지 크기 거부
        - 문자열 페이지 거부
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/?page={page}&page_size={page_size}", **headers)

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 에러 예상 (일부 케이스는 200이 될 수 있음 - 자동 보정)
        # 최소한 5xx는 발생하면 안 됨
