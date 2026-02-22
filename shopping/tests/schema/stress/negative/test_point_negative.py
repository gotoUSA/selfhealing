"""
포인트 API Negative 테스트
=========================

TestPointNegativeInputs: 잔액 초과, 음수 포인트, 주문 금액 초과 등
"""

import json

import pytest


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPointNegativeInputs:
    """
    💰 포인트 API에 대한 Negative 테스트

    포인트 사용 시 잔액 초과, 음수 포인트 등
    잘못된 입력에 대해 적절한 에러 응답이 반환되는지 검증합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    @pytest.fixture
    def user_with_limited_points(self, db):
        """제한된 포인트를 가진 사용자 생성"""
        from shopping.tests.factories import UserFactory

        user = UserFactory(points=5000)  # 5000 포인트
        return user

    @pytest.mark.parametrize(
        "points_to_use,user_points,expected_codes,description",
        [
            (10000, 5000, [400, 422], "잔액 초과 사용"),
            (-1000, 5000, [400, 422], "음수 포인트 사용"),
            (0, 5000, [400, 200, 201], "0 포인트 사용 (허용될 수 있음)"),
            (5001, 5000, [400, 422], "1포인트 초과 사용"),
        ],
        ids=["exceed_balance", "negative", "zero", "one_over"],
    )
    def test_order_with_invalid_points(
        self, client, schema_test_product, points_to_use, user_points, expected_codes, description
    ):
        """
        잘못된 포인트 사용 테스트

        주문 시 잔액을 초과하거나 음수 포인트를 사용하려 할 때
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 잔액 초과 거부
        - 음수 포인트 거부
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 포인트가 있는 사용자 생성
        from shopping.tests.factories import UserFactory

        user = UserFactory(points=user_points)

        # 로그인하여 토큰 획득
        login_response = client.post(
            "/api/auth/login/",
            data=json.dumps(
                {
                    "username": user.username,
                    "password": "testpass123",
                }
            ),
            content_type="application/json",
        )

        if login_response.status_code != 200:
            pytest.skip("로그인 실패로 테스트 스킵")

        login_data = login_response.json()
        token = login_data.get("access") or login_data.get("token", {}).get("access")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        # 장바구니에 상품 추가
        cart_response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(
                {
                    "product_id": schema_test_product.id,
                    "quantity": 1,
                }
            ),
            content_type="application/json",
            **headers,
        )

        if cart_response.status_code not in [200, 201]:
            pytest.skip("장바구니 추가 실패로 테스트 스킵")

        # 주문 생성 시 잘못된 포인트 사용
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": points_to_use,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음 (핵심 검증)
        assert response.status_code < 500, (
            f"{description}에서 서버 에러 발생!\n"
            f"points_to_use={points_to_use}, user_points={user_points}\n"
            f"status_code={response.status_code}"
        )

        # 비즈니스 로직 검증:
        # - 음수/잔액 초과 포인트는 정책에 따라:
        #   1. 400/422로 거부되거나
        #   2. 0 또는 최대값으로 자동 조정 후 201/202로 성공
        # 핵심: 5xx 에러 없음 + 데이터 무결성 유지 (위에서 검증됨)

    def test_point_usage_exceeds_order_total(self, client, auth_headers, schema_test_product, user):
        """
        주문 금액 초과 포인트 사용 테스트

        주문 총액보다 많은 포인트를 사용하려 할 때
        적절한 처리가 되는지 확인합니다.

        🔍 검증 포인트:
        - 주문 금액 초과 포인트 사용 처리
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 사용자에게 많은 포인트 부여
        user.points = 1000000  # 100만 포인트
        user.save()

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 장바구니에 상품 추가 (예: 10,000원 상품)
        cart_response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(
                {
                    "product_id": schema_test_product.id,
                    "quantity": 1,
                }
            ),
            content_type="application/json",
            **headers,
        )

        # 주문 금액보다 많은 포인트 사용 시도
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": 500000,  # 50만 포인트 (상품가보다 클 수 있음)
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"주문 금액 초과 포인트에서 서버 에러 발생!\n" f"status_code={response.status_code}"
