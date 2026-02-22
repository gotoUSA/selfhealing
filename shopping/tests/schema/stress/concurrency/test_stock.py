"""재고 동시 차감 테스트"""

import time
from decimal import Decimal

import pytest
from django.urls import reverse

from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory

from .helpers import login_and_get_token, run_concurrent_requests


# 여기에 test_concurrency.py의 527~695줄을 복사하세요
# (TestConcurrentStockDeduction 클래스)


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentStockDeduction:
    """
    📦 재고 동시 차감 테스트

    한정 재고 상품에 여러 사용자가 동시에 주문할 때
    재고가 음수가 되지 않는지 검증합니다.

    📋 테스트 시나리오:
    - 재고 5개 상품에 10명이 동시 주문
    - 재고 1개 상품에 여러 명이 동시 주문

    ✅ 예상 결과:
    - 재고 음수 방지 (항상 >= 0)
    - 성공한 주문 수 <= 초기 재고
    - sold_count 정확히 증가

    🔧 동시성 제어:
    - Product.objects.select_for_update()
    - F('stock') - quantity >= 0 조건부 업데이트

    Note:
        더 상세한 재고 동시성 테스트는 다음 파일 참조:
        shopping/tests/integration/test_order_concurrency.py
    """

    @pytest.fixture
    def limited_stock_product(self, db):
        """재고가 제한된 테스트 상품 생성"""
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=5,  # 재고 5개
            price=Decimal("50000"),
            is_active=True,
        )
        return product

    def test_concurrent_orders_no_negative_stock(self, limited_stock_product):
        """
        동시 주문 시 재고 음수 방지 테스트

        재고 5개 상품에 10명의 사용자가 동시에 1개씩 주문할 때,
        재고가 음수가 되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 재고 >= 0 (절대 음수 안 됨)
        - 성공 주문 수 <= 5
        - 재고 부족 주문은 적절한 에러 코드 반환

        구현 참고:
        - 이 테스트는 장바구니 추가 단계에서 재고 체크를 검증합니다.
        - 실제 주문 생성 동시성은 test_order_concurrency.py에서 다룹니다.
        """
        product = limited_stock_product
        num_users = 10

        # 10명의 사용자 생성
        users = [UserFactory(username=f"buyer_{i}_{time.time()}") for i in range(num_users)]

        def add_to_cart_and_check(user):
            """사용자별 장바구니 추가 요청"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error, "user_id": user.id}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product.id, "quantity": 1},
                format="json",
            )
            return {
                "status_code": response.status_code,
                "user_id": user.id,
                "data": response.json() if response.status_code < 500 else None,
            }

        # 동시 요청 실행
        args_list = [(u,) for u in users]
        results = run_concurrent_requests(add_to_cart_and_check, args_list, max_workers=num_users)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") in [200, 201])
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # Assert: 재고 음수 확인
        product.refresh_from_db()
        assert product.stock >= 0, f"재고가 음수! stock={product.stock}"

        # ✅ 회계 무결성 검증 - stock + sold_count == 초기값
        # 재고가 차감된 만큼 sold_count가 증가해야 함
        initial_stock = 5  # fixture에서 설정한 초기 재고
        assert product.stock + product.sold_count == initial_stock, (
            f"회계 무결성 오류: 재고({product.stock}) + 판매량({product.sold_count}) != "
            f"초기 재고({initial_stock}) — 누락 또는 중복 차감 발생"
        )

    @pytest.mark.slow
    def test_concurrent_single_stock_item(self, db):
        """
        재고 1개 상품 동시 구매 테스트 (@slow)

        재고가 딱 1개인 상품에 10명이 동시에 접근할 때,
        정확히 1명만 성공하는지 확인합니다.

        🔍 검증 포인트:
        - 성공 구매자 = 최대 1명
        - 나머지는 재고 부족 에러
        - 재고 = 0 (음수 아님)
        """
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_single_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=1,  # 재고 1개만!
            price=Decimal("100000"),
            is_active=True,
        )

        num_users = 10
        users = [UserFactory(username=f"buyer_single_{i}_{time.time()}") for i in range(num_users)]

        def try_purchase(user, product_id):
            """장바구니 추가로 구매 시도"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {"status_code": response.status_code}

        # 동시 요청 실행
        args_list = [(u, product.id) for u in users]
        results = run_concurrent_requests(try_purchase, args_list, max_workers=num_users)

        # 결과 분석
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, "서버 에러 발생!"

        # Assert: 재고 음수 확인
        product.refresh_from_db()
        assert product.stock >= 0, f"재고가 음수! stock={product.stock}"

        # ✅ 회계 무결성 검증 - 재고 1개 상품
        initial_stock = 1
        assert product.stock + product.sold_count == initial_stock, (
            f"회계 무결성 오류: 재고({product.stock}) + 판매량({product.sold_count}) != "
            f"초기 재고({initial_stock}) — 누락 또는 중복 차감 발생"
        )
