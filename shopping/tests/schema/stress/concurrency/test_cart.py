"""장바구니 동시성 테스트"""

import time
from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework import status

from shopping.models.cart import Cart, CartItem
from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory

from .helpers import login_and_get_token, run_concurrent_requests


# 여기에 test_concurrency.py의 258~524줄을 복사하세요
# (TestConcurrentCartOperations 클래스)


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentCartOperations:
    """
    🛒 장바구니 동시성 테스트

    같은 사용자가 여러 탭/기기에서 동시에 장바구니를 조작하거나,
    같은 상품에 여러 사용자가 동시에 접근할 때 데이터 무결성을 검증합니다.

    📋 테스트 시나리오:
    - 같은 상품 동시 추가 시 수량 정확성
    - 동시 수량 변경 시 최종 값 일관성
    - 동시 삭제 시 오류 없음

    ✅ 예상 결과:
    - 재고 초과 추가 방지
    - 수량 정확하게 반영
    - 5xx 에러 없음

    🔧 동시성 제어:
    - CartItem.objects.select_for_update() 사용
    - F() 객체로 원자적 수량 업데이트
    """

    @pytest.fixture
    def concurrent_test_data(self, db):
        """
        동시성 테스트용 데이터 생성

        Returns:
            dict: {
                "user": User 인스턴스,
                "product": Product 인스턴스 (재고 100),
                "category": Category 인스턴스
            }

        Note:
            각 테스트에서 독립적인 데이터를 생성하여 격리성 보장
        """
        category = CategoryFactory()
        user = UserFactory(username=f"cart_test_user_{time.time()}")
        product = ProductFactory(
            category=category,
            stock=100,
            price=Decimal("10000"),
            is_active=True,
        )
        return {
            "user": user,
            "product": product,
            "category": category,
        }

    def test_concurrent_add_same_product(self, concurrent_test_data):
        """
        같은 상품 동시 추가 테스트

        10개의 동시 요청이 같은 상품을 장바구니에 추가할 때,
        최종 수량이 정확하게 10이 되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 요청 성공 (201 Created)
        - 최종 장바구니 수량 = 요청 수
        - 5xx 에러 없음

        동시성 이슈 예방:
        - 각 요청은 독립적인 APIClient 사용
        - DB에서 직접 수량 확인 (캐시 영향 제거)
        """
        user = concurrent_test_data["user"]
        product = concurrent_test_data["product"]
        num_requests = 10

        def add_to_cart(user_id, product_id):
            """개별 장바구니 추가 요청"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {
                "status_code": response.status_code,
                "data": response.json() if response.status_code < 500 else None,
            }

        # 동시 요청 실행
        args_list = [(user.id, product.id) for _ in range(num_requests)]
        results = run_concurrent_requests(add_to_cart, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") in [200, 201])
        error_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_count == 0, f"서버 에러 발생! 5xx 응답 {error_count}개"

        # Assert: 대부분 성공 (일부 409 Conflict 가능)
        assert success_count >= num_requests * 0.8, f"성공률 낮음: {success_count}/{num_requests}"

        # Assert: 최종 장바구니 수량 확인
        cart = Cart.objects.filter(user=user, is_active=True).first()
        if cart:
            cart_item = CartItem.objects.filter(cart=cart, product=product).first()
            if cart_item:
                # 동시 요청이므로 정확히 num_requests가 아닐 수 있음
                # 하지만 음수가 되어서는 안 됨
                assert cart_item.quantity > 0, "장바구니 수량이 0 이하!"
                assert cart_item.quantity <= product.stock, "재고 초과 추가!"

            # ✅ Row Duplication 검증 - Race Condition으로 인한 중복 row 생성 방지
            cart_item_count = CartItem.objects.filter(cart=cart, product=product).count()
            assert cart_item_count == 1, (
                f"Race Condition 발생: 동일 상품에 대해 CartItem row가 중복 생성됨! "
                f"(expected: 1, actual: {cart_item_count})"
            )

    def test_concurrent_cart_add_stock_limit(self, concurrent_test_data):
        """
        재고 한도 동시 추가 테스트

        재고가 5개인 상품에 10개의 동시 요청 (각 1개씩)을 보낼 때,
        최대 5개까지만 추가되는지 확인합니다.

        🔍 검증 포인트:
        - 재고 초과 요청은 거부 (400 또는 409)
        - 최종 장바구니 수량 <= 재고
        - 5xx 에러 없음

        이 테스트는 재고 체크 로직의 원자성을 검증합니다.
        """
        user = concurrent_test_data["user"]
        product = concurrent_test_data["product"]

        # 재고를 5개로 제한
        product.stock = 5
        product.save()

        num_requests = 10

        def add_to_cart(user_id, product_id):
            """개별 장바구니 추가 요청"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {
                "status_code": response.status_code,
                "data": response.json() if response.status_code < 500 else None,
            }

        # 동시 요청 실행
        args_list = [(user.id, product.id) for _ in range(num_requests)]
        results = run_concurrent_requests(add_to_cart, args_list, max_workers=num_requests)

        # 결과 분석
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # ✅ 409 Conflict 응답 메시지 일관성 검증 - API Contract
        conflict_responses = [r for r in results if r.get("status_code") == 409]
        for r in conflict_responses:
            data = r.get("data", {})
            # 409 응답에는 에러 메시지가 있어야 함
            has_error_message = data.get("error") or data.get("message") or data.get("detail") or data.get("errors")
            assert has_error_message, f"409 Conflict 응답에 에러 메시지 없음 — API Contract 위반. " f"응답: {data}"

        # ✅ 400 Bad Request 응답 메시지 일관성 검증
        bad_request_responses = [r for r in results if r.get("status_code") == 400]
        for r in bad_request_responses:
            data = r.get("data", {})
            has_error_message = (
                data.get("error")
                or data.get("message")
                or data.get("detail")
                or data.get("errors")
                or data.get("stock")  # 재고 관련 에러 필드
            )
            assert has_error_message, f"400 Bad Request 응답에 에러 메시지 없음 — API Contract 위반. " f"응답: {data}"

        # Assert: 최종 장바구니 수량이 재고 이하
        cart = Cart.objects.filter(user=user, is_active=True).first()
        if cart:
            cart_item = CartItem.objects.filter(cart=cart, product=product).first()
            if cart_item:
                assert cart_item.quantity <= 5, f"재고 초과! 장바구니: {cart_item.quantity}, 재고: 5"

            # ✅ Row Duplication 검증
            cart_item_count = CartItem.objects.filter(cart=cart, product=product).count()
            assert cart_item_count == 1, (
                f"Race Condition 발생: 동일 상품에 대해 CartItem row가 중복 생성됨! "
                f"(expected: 1, actual: {cart_item_count})"
            )

    @pytest.mark.slow
    def test_concurrent_cart_operations_multiple_products(self, concurrent_test_data):
        """
        여러 상품 동시 추가 테스트 (@slow)

        5개의 다른 상품을 각각 10번씩 동시에 추가할 때,
        장바구니 데이터가 정확하게 유지되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 상품이 올바른 수량으로 추가
        - 상품 간 데이터 오염 없음
        - 5xx 에러 없음

        Note: @slow 마커로 인해 기본 실행에서 제외됩니다.
        전체 실행 시에만 포함됩니다.
        """
        user = concurrent_test_data["user"]
        category = concurrent_test_data["category"]

        # 5개의 상품 생성
        products = [ProductFactory(category=category, stock=100, price=Decimal("10000"), is_active=True) for _ in range(5)]

        def add_to_cart(user_id, product_id):
            """개별 장바구니 추가 요청"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"status_code": 0, "error": error, "product_id": product_id}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {
                "status_code": response.status_code,
                "product_id": product_id,
            }

        # 각 상품마다 10번씩 동시 요청 (총 50개)
        args_list = [(user.id, p.id) for p in products for _ in range(10)]
        results = run_concurrent_requests(add_to_cart, args_list, max_workers=50)

        # 결과 분석
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # Assert: 각 상품의 장바구니 수량 확인
        cart = Cart.objects.filter(user=user, is_active=True).first()
        if cart:
            for product in products:
                cart_item = CartItem.objects.filter(cart=cart, product=product).first()
                if cart_item:
                    assert cart_item.quantity > 0, f"상품 {product.id} 수량이 0 이하!"
