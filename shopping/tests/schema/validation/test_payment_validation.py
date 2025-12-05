# test_payment_validation.py
# 결제 필드 검증 테스트 - 필수 필드 존재 및 타입 검증

"""
💳 결제 필드 검증 테스트
=======================

가이드라인 01_SUCCESS_RESPONSE_SCHEMA.md에 따라
결제 API 응답의 필수 필드 존재 및 타입을 검증합니다.

✅ 검증 항목:
- 필수 필드 존재 확인
- 필드 타입 일치 확인
- 선택적 필드 타입 확인 (존재 시)

📁 관련 가이드:
- guides/01_SUCCESS_RESPONSE_SCHEMA.md
- guides/02_ERROR_RESPONSE_SCHEMA.md

🏷️ API Tier: Tier 1 (Critical)
- 결제는 외부 PG 연동 포함, 필수 테스트 대상
"""

import pytest
from decimal import Decimal
from rest_framework import status

from ..conftest import assert_payment_schema, assert_list_response


# ==========================================
# 📦 필수 필드 정의
# ==========================================

PAYMENT_REQUIRED_FIELDS = ["id", "order", "amount", "status", "created_at"]

# ==========================================
# 📦 필드 타입 정의 (실제 API 응답 기준 - 2025-12-05 검증됨)
# ==========================================

PAYMENT_FIELD_TYPES = {
    # 필수 필드
    "id": int,
    "order": int,
    "amount": str,  # Decimal → str 직렬화
    "status": str,
    "created_at": str,
    # 선택적 필드
    "updated_at": str,
    "order_number": str,
    "payment_key": (str, type(None)),
    "order_id": int,  # order.id와 동일
    "method": str,
    "card_company": str,
    "card_number": str,
    "installment_plan_months": int,
    "status_display": str,
    "is_canceled": bool,
    "canceled_amount": str,  # Decimal → str 직렬화
    "cancel_reason": str,
    "approved_at": (str, type(None)),
    "canceled_at": (str, type(None)),
    "receipt_url": str,
    "used_points": int,
    "earned_points": int,
}


@pytest.mark.schema
@pytest.mark.django_db
class TestPaymentFieldValidation:
    """
    💳 결제 필드 검증 테스트

    결제 API 응답의 필수 필드 존재 및 타입을 검증합니다.

    ✅ 검증 항목:
    - 필수 필드: id, order, amount, status, created_at
    - 타입 검증: int, str, Decimal 등
    """

    def test_payment_list_required_fields(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 목록 API 필수 필드 존재 확인

        📋 가이드라인 01 참조
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조
        data = response.json()
        assert "results" in data, "페이지네이션 응답에 'results' 필드 필요"
        assert "count" in data, "페이지네이션 응답에 'count' 필드 필요"

        # 결제 목록이 비어있지 않으면 필드 검증
        if data["results"]:
            for i, payment in enumerate(data["results"][:5]):
                for field in PAYMENT_REQUIRED_FIELDS:
                    assert field in payment, f"결제[{i}]: 필수 필드 '{field}' 누락"

    def test_payment_detail_required_fields(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 상세 API 필수 필드 존재 확인

        📋 가이드라인 01 참조
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 필수 필드 존재
        data = response.json()
        for field in PAYMENT_REQUIRED_FIELDS:
            assert field in data, f"필수 필드 '{field}' 누락"

    def test_payment_detail_field_types(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 상세 API 필드 타입 검증

        각 필드가 올바른 타입인지 확인합니다.
        (2025-12-05 실제 API 응답 기준으로 검증됨)
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 필드 타입 검증 (실제 응답 기준)
        assert isinstance(data["id"], int), f"id 타입 불일치: {type(data['id'])}"
        assert isinstance(data["order"], int), f"order 타입 불일치: {type(data['order'])}"
        assert isinstance(data["status"], str), f"status 타입 불일치: {type(data['status'])}"
        assert isinstance(data["created_at"], str), f"created_at 타입 불일치: {type(data['created_at'])}"
        # amount는 Decimal이 str로 직렬화됨
        assert isinstance(data["amount"], str), f"amount 타입 불일치: {type(data['amount'])}"

    def test_payment_list_items_field_types(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 목록 각 아이템 필드 타입 검증

        목록의 각 결제가 올바른 필드 타입을 갖추는지 검증합니다.
        (2025-12-05 실제 API 응답 기준으로 검증됨)
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 결제 목록이 비어있지 않으면 타입 검증
        if data["results"]:
            for i, payment in enumerate(data["results"][:5]):
                assert isinstance(payment["id"], int), f"결제[{i}]: id 타입 불일치"
                assert isinstance(payment["order"], int), f"결제[{i}]: order 타입 불일치"
                assert isinstance(payment["status"], str), f"결제[{i}]: status 타입 불일치"
                assert isinstance(payment["amount"], str), f"결제[{i}]: amount 타입 불일치 (str 예상)"

    def test_payment_schema_helper_validation(self, client, auth_headers, schema_test_payment):
        """
        💳 conftest.py의 assert_payment_schema 헬퍼 활용 테스트

        헬퍼 함수를 사용한 결제 스키마 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 헬퍼 함수로 검증
        assert_payment_schema(data, context=f"/api/payments/{payment_id}/")


@pytest.mark.schema
@pytest.mark.django_db
class TestPaymentOptionalFields:
    """
    💳 결제 선택적 필드 검증 테스트

    선택적 필드가 존재할 때 올바른 타입인지 검증합니다.
    """

    def test_payment_optional_fields_types(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 상세 API 선택적 필드 타입 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 선택적 필드 타입 검증 (존재할 경우에만)
        optional_type_checks = {
            "order_number": str,
            "method": str,
            "card_company": str,
            "card_number": str,
            "installment_plan_months": int,
            "status_display": str,
            "is_canceled": bool,
            "receipt_url": str,
        }

        for field, expected_type in optional_type_checks.items():
            if field in data and data[field] is not None:
                assert isinstance(
                    data[field], expected_type
                ), f"선택적 필드 '{field}' 타입 불일치: 기대={expected_type}, 실제={type(data[field])}"

    def test_payment_nullable_fields(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 상세 API nullable 필드 검증

        null이 허용되는 필드 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # nullable 필드는 str 또는 None이어야 함
        nullable_fields = ["payment_key", "approved_at", "canceled_at"]

        for field in nullable_fields:
            if field in data:
                assert data[field] is None or isinstance(
                    data[field], str
                ), f"nullable 필드 '{field}'는 str 또는 None이어야 함: {type(data[field])}"


@pytest.mark.schema
@pytest.mark.django_db
class TestPaymentListPagination:
    """
    💳 결제 목록 페이지네이션 검증 테스트
    """

    def test_payment_list_pagination_structure(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 목록 API 페이지네이션 응답 구조 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 페이지네이션 필드 검증
        assert "count" in data, "페이지네이션: 'count' 필드 필요"
        assert "page" in data, "페이지네이션: 'page' 필드 필요"
        assert "page_size" in data, "페이지네이션: 'page_size' 필드 필요"
        assert "results" in data, "페이지네이션: 'results' 필드 필요"

        # 타입 검증
        assert isinstance(data["count"], int), "count는 int이어야 함"
        assert isinstance(data["page"], int), "page는 int이어야 함"
        assert isinstance(data["page_size"], int), "page_size는 int이어야 함"
        assert isinstance(data["results"], list), "results는 list이어야 함"

    def test_payment_list_pagination_params(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 목록 API 페이지네이션 파라미터 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/?page=1&page_size=5", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert data["page"] == 1, "page 값이 요청과 일치해야 함"
        assert data["page_size"] == 5, "page_size 값이 요청과 일치해야 함"


@pytest.mark.schema
@pytest.mark.django_db
class TestPaymentStatusFilter:
    """
    💳 결제 상태 필터 검증 테스트
    """

    def test_payment_list_status_filter(self, client, auth_headers, schema_test_payment):
        """
        💳 결제 목록 API 상태 필터 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - done 상태 필터
        response = client.get("/api/payments/?status=done", **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # 모든 결과가 done 상태인지 검증
        for payment in data["results"]:
            assert payment["status"] == "done", f"필터링된 결제의 상태가 'done'이어야 함: {payment['status']}"

    def test_payment_list_invalid_status_filter(self, client, auth_headers):
        """
        💳 결제 목록 API 잘못된 상태 필터 시 에러 검증
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 잘못된 상태값
        response = client.get("/api/payments/?status=invalid_status", **headers)

        # Assert - 400 에러 반환
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "error" in data, "에러 응답에 'error' 필드 필요"
