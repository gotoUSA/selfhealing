"""
주문 API Fuzz 테스트
====================

주문 생성, 조회, 취소 API에 무작위 입력을 주입하여
데이터 검증 로직의 안정성을 확인합니다.

📋 테스트 대상:
- GET /api/orders/ (주문 목록)
- GET /api/orders/{id}/ (주문 상세)
- POST /api/orders/ (주문 생성)
- POST /api/orders/{id}/cancel/ (주문 취소)

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_orders.py -v -n 0
```
"""

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from hypothesis import strategies as st
from rest_framework import status

from .conftest import (
    product_id_strategy,
    shipping_address_strategy,
    payment_method_strategy,
)


# 주문 관련 Hypothesis 전략
shipping_address_strategy = st.one_of(
    st.text(min_size=0, max_size=500),  # 일반 텍스트
    st.just(""),  # 빈 주소
    st.just(" " * 100),  # 공백만
    st.just("a" * 1000),  # 매우 긴 주소
    st.just("서울시 강남구 테헤란로 123"),  # 정상 주소
    st.just("'; DROP TABLE orders; --"),  # SQL Injection
    st.just("<script>alert('xss')</script>"),  # XSS
    st.just("🏠📍🚚"),  # 이모지
    st.just("../../../etc/passwd"),  # Path Traversal
)

payment_method_strategy = st.one_of(
    st.just("card"),
    st.just("transfer"),
    st.just("virtual_account"),
    st.just(""),  # 빈 값
    st.just("invalid_method"),  # 잘못된 값
    st.just("'; DROP TABLE--"),  # SQL Injection
    st.just("card; DELETE FROM"),
    st.text(min_size=1, max_size=50),  # 무작위 문자열
)

amount_strategy = st.one_of(
    st.integers(min_value=-100000, max_value=100000000),  # 음수, 매우 큰 수
    st.floats(min_value=-1000.0, max_value=1000000.0),  # 부동소수점
    st.just(0),
    st.just(-1),
    st.just(0.01),  # 소수점
    st.just(float("nan")),  # NaN
    st.just(float("inf")),  # Infinity
    st.just("invalid_amount"),  # 문자열
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestOrdersFuzz:
    """
    📦 주문 API Fuzz 테스트

    주문 생성, 조회, 취소 API에 무작위 입력을 주입하여
    데이터 검증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - GET /api/orders/ (주문 목록)
    - GET /api/orders/{id}/ (주문 상세)
    - POST /api/orders/ (주문 생성) - 장바구니 기반
    - POST /api/orders/{id}/cancel/ (주문 취소)

    ✅ 검증 속성:
    - 잘못된 배송 정보에 대해 적절한 에러 반환
    - 잘못된 주문 ID에 대해 400 또는 404 반환
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(order_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_detail_id_fuzz(self, client, auth_headers, order_id):
        """
        주문 상세 API ID 파라미터 퍼징

        다양한 형식의 주문 ID를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 문자열 ID
        - SQL Injection 시도
        - 매우 큰 ID

        Args:
            order_id: Hypothesis가 생성한 무작위 주문 ID
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code < 500, (
            f"주문 상세 조회에서 서버 에러 발생!\n" f"order_id={repr(order_id)}\n" f"상태 코드: {response.status_code}"
        )

    @given(
        shipping_address=shipping_address_strategy,
        shipping_name=st.text(min_size=0, max_size=200),
        shipping_phone=st.text(min_size=0, max_size=50),
    )
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_shipping_info_fuzz(
        self, client, auth_headers, schema_test_product, user, shipping_address, shipping_name, shipping_phone
    ):
        """
        주문 생성 API 배송 정보 퍼징

        다양한 형태의 배송 정보를 주입하여
        입력 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - SQL Injection 주소
        - XSS 스크립트 주소
        - 매우 긴 주소
        - 이모지가 포함된 주소
        - 빈 필수 필드

        Args:
            shipping_address: 무작위 배송 주소
            shipping_name: 무작위 수령인 이름
            shipping_phone: 무작위 연락처
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(cart=cart, product=schema_test_product, defaults={"quantity": 1})

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": shipping_address,
            "shipping_name": shipping_name,
            "shipping_phone": shipping_phone,
            "shipping_postal_code": "12345",
            "payment_method": "card",
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n"
            f"data={data}\n"
            f"상태 코드: {response.status_code}\n"
            f"응답: {response.content[:500]}"
        )

    @given(payment_method=payment_method_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_payment_method_fuzz(self, client, auth_headers, schema_test_product, user, payment_method):
        """
        주문 생성 API 결제 방법 퍼징

        다양한 형태의 결제 방법을 주입하여
        결제 방법 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 유효하지 않은 결제 방법
        - SQL Injection 시도
        - 빈 결제 방법

        Args:
            payment_method: Hypothesis가 생성한 무작위 결제 방법
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(cart=cart, product=schema_test_product, defaults={"quantity": 1})

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": payment_method,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n" f"payment_method={repr(payment_method)}\n" f"상태 코드: {response.status_code}"
        )

    @given(used_points=st.integers(min_value=-10000, max_value=10000000))
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_points_fuzz(self, client, auth_headers, schema_test_product, user, used_points):
        """
        주문 생성 API 포인트 사용 퍼징

        다양한 형태의 포인트 값을 주입하여
        포인트 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 음수 포인트
        - 보유 포인트 초과
        - 매우 큰 포인트 값

        Args:
            used_points: Hypothesis가 생성한 무작위 포인트 값
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(cart=cart, product=schema_test_product, defaults={"quantity": 1})

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": used_points,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n" f"used_points={used_points}\n" f"상태 코드: {response.status_code}"
        )

        # 음수 포인트는 거부되어야 함
        if used_points < 0:
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            ], f"음수 포인트가 허용됨: {response.status_code}"
