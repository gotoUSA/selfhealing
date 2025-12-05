"""
결제 API Fuzz 테스트
====================

결제 조회 API에 무작위 입력을 주입하여
결제 시스템의 안정성을 검증합니다.

📋 테스트 대상:
- GET /api/payments/ (결제 목록)
- GET /api/payments/{id}/ (결제 상세)

⚠️ 주의:
- 실제 결제 생성/승인은 Toss API 의존으로 Fuzz 대상 제외
- 조회 및 검증 로직만 테스트

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_payments.py -v -n 0
```
"""

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from hypothesis import strategies as st
from rest_framework import status

from .conftest import (
    product_id_strategy,
    pagination_strategy,
    amount_strategy,
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPaymentsFuzz:
    """
    💳 결제 API Fuzz 테스트

    결제 조회 API에 무작위 입력을 주입하여
    결제 시스템의 안정성을 검증합니다.

    📋 테스트 대상:
    - GET /api/payments/ (결제 목록)
    - GET /api/payments/{id}/ (결제 상세)

    ⚠️ 주의:
    - 실제 결제 생성/승인은 Toss API 의존으로 Fuzz 대상 제외
    - 조회 및 검증 로직만 테스트

    ✅ 검증 속성:
    - 잘못된 결제 ID에 대해 400 또는 404 반환
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(payment_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_detail_id_fuzz(self, client, auth_headers, payment_id):
        """
        결제 상세 API ID 파라미터 퍼징

        다양한 형식의 결제 ID를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 문자열 ID
        - SQL Injection 시도
        - 특수문자 ID

        Args:
            payment_id: Hypothesis가 생성한 무작위 결제 ID
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code < 500, (
            f"결제 상세 조회에서 서버 에러 발생!\n" f"payment_id={repr(payment_id)}\n" f"상태 코드: {response.status_code}"
        )
        # 유효하지 않은 ID는 400 또는 404여야 함
        if not (isinstance(payment_id, int) and payment_id > 0):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,
                status.HTTP_401_UNAUTHORIZED,
            ], f"유효하지 않은 ID에 대해 예상치 못한 응답: {response.status_code}"

    @given(
        status_filter=st.one_of(
            st.just("ready"),
            st.just("pending"),
            st.just("done"),
            st.just("canceled"),
            st.just(""),
            st.just("invalid_status"),
            st.just("'; DROP TABLE--"),
            st.text(min_size=1, max_size=50),
        ),
        page=pagination_strategy,
    )
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_list_filters_fuzz(self, client, auth_headers, status_filter, page):
        """
        결제 목록 API 필터 파라미터 퍼징

        다양한 형태의 필터 파라미터를 주입하여
        필터링 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 유효하지 않은 상태 필터
        - SQL Injection 시도
        - 잘못된 페이지 번호

        Args:
            status_filter: 무작위 상태 필터
            page: 무작위 페이지 번호
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        params = {}
        if status_filter:
            params["status"] = status_filter
        if page:
            params["page"] = page

        # Act
        response = client.get("/api/payments/", params, **headers)

        # Assert
        assert response.status_code < 500, (
            f"결제 목록 조회에서 서버 에러 발생!\n" f"params={params}\n" f"상태 코드: {response.status_code}"
        )

    @given(amount=amount_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_amount_validation_fuzz(self, client, auth_headers, schema_test_order, amount):
        """
        결제 금액 검증 퍼징

        결제 요청 시 다양한 형태의 금액을 주입하여
        금액 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 음수 금액
        - 0 금액
        - 매우 큰 금액
        - 소수점 금액
        - NaN, Infinity

        Args:
            amount: Hypothesis가 생성한 무작위 금액
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "order_id": schema_test_order.id,
            "amount": amount,
            "payment_method": "card",
        }

        # Act - 결제 준비 API 호출 (실제 결제는 아님)
        response = client.post(
            "/api/payments/prepare/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"결제 준비에서 서버 에러 발생!\n" f"amount={repr(amount)}\n" f"상태 코드: {response.status_code}"
        )

        # 음수/0 금액은 거부되어야 함
        if isinstance(amount, (int, float)) and amount <= 0:
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,  # 주문이 없을 수 있음
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            ], f"유효하지 않은 금액이 허용됨: {response.status_code}"
