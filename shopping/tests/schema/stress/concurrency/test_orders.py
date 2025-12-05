"""주문 동시 생성 테스트"""

import time
from decimal import Decimal

import pytest
from django.urls import reverse

from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory

from .helpers import login_and_get_token, run_concurrent_requests


# 여기에 test_concurrency.py의 1245~1346줄을 복사하세요
# (TestConcurrentOrderCreation 클래스)


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentOrderCreation:
    """
    📦 주문 동시 생성 테스트

    동시에 여러 주문이 생성될 때 주문번호 중복이 없고
    데이터 무결성이 유지되는지 검증합니다.

    📋 테스트 시나리오:
    - 동시 주문 생성 시 주문번호 고유성
    - 동시 주문 시 재고 정확한 차감

    ✅ 예상 결과:
    - 주문번호 중복 없음
    - 재고 음수 방지
    - 5xx 에러 없음

    📅 가이드라인: 09_PERFORMANCE_TESTING.md (동시성 섹션)
    """

    @pytest.fixture
    def order_test_setup(self, db):
        """주문 테스트용 설정"""
        category = CategoryFactory()
        seller = UserFactory(username=f"order_seller_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=50,
            price=Decimal("30000"),
            is_active=True,
        )
        return {"product": product, "category": category}

    def test_concurrent_order_unique_order_numbers(self, order_test_setup):
        """
        동시 주문 시 주문번호 고유성 테스트

        10명의 사용자가 동시에 주문을 생성할 때,
        모든 주문번호가 고유한지 확인합니다.

        🔍 검증 포인트:
        - 모든 주문번호 고유
        - 중복 주문번호 없음
        - 5xx 에러 없음
        """
        product = order_test_setup["product"]
        num_users = 10

        users = [UserFactory(username=f"order_user_{i}_{time.time()}") for i in range(num_users)]

        def create_order(user, product_id):
            """주문 생성"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

            # 장바구니에 상품 추가
            cart_response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            if cart_response.status_code not in [200, 201]:
                return {"status_code": cart_response.status_code, "step": "cart"}

            # 주문 생성
            order_response = client.post(
                reverse("order-list"),
                {
                    "shipping_address": "서울시 강남구 테헤란로 123",
                    "shipping_name": "홍길동",
                    "shipping_phone": "010-1234-5678",
                    "shipping_postal_code": "12345",
                    "payment_method": "card",
                },
                format="json",
            )
            data = order_response.json() if order_response.status_code < 500 else {}
            return {
                "status_code": order_response.status_code,
                "order_number": data.get("order_number"),
            }

        # 동시 요청 실행
        args_list = [(u, product.id) for u in users]
        results = run_concurrent_requests(create_order, args_list, max_workers=num_users)

        # 결과 분석
        order_numbers = [r.get("order_number") for r in results if r.get("order_number")]
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # Assert: 주문번호 고유성
        unique_order_numbers = set(order_numbers)
        assert len(unique_order_numbers) == len(order_numbers), (
            f"주문번호 중복 발생! " f"(전체: {len(order_numbers)}, 고유: {len(unique_order_numbers)})"
        )
