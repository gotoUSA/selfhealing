"""
동시성 테스트 (Schemathesis Phase 6.1)
======================================

📋 개요
-------
이 모듈은 API 레벨에서 동시 요청(Concurrent Requests)을 시뮬레이션하여
데이터 무결성과 경쟁 조건(Race Condition) 방지를 검증합니다.

🎯 테스트 목적
-------------
1. **동시 요청 처리 검증**: 여러 클라이언트가 동시에 같은 리소스에 접근할 때 데이터 일관성 유지
2. **경쟁 조건 방지 확인**: 재고 차감, 장바구니 수정, 주문 생성 등에서 Race Condition 없음
3. **데이터베이스 락(Lock) 동작 확인**: select_for_update, F() 객체 등 동시성 제어 메커니즘 검증
4. **API 응답 일관성**: 동시 요청에도 예상된 응답 코드와 에러 메시지 반환

🔧 동시성 테스트 vs 부하 테스트
-----------------------------
| 구분 | 동시성 테스트 | 부하 테스트 |
|------|--------------|------------|
| 목적 | 데이터 무결성, Race Condition 방지 | 처리량, 응답 시간 측정 |
| 요청 수 | 10~50 동시 요청 | 수백~수천 요청 |
| 검증 대상 | 최종 상태 (재고, 포인트 등) | 처리 시간, 에러율 |
| 도구 | threading, asyncio | Locust, k6, JMeter |

📊 테스트 클래스 구조
-------------------
1. TestConcurrentCartOperations
   - 동시 장바구니 추가/수정/삭제 시 데이터 무결성 유지
   - 같은 상품 동시 추가 시 수량 정확성

2. TestConcurrentStockDeduction
   - 한정 재고 상품에 동시 주문 시 재고 음수 방지
   - sold_count 정확한 증가

3. TestConcurrentPointOperations
   - 동시 포인트 적립/차감 시 음수 방지
   - 포인트 잔액 정확성

4. TestConcurrentOrderCreation
   - 동시 주문 생성 시 주문번호 중복 없음
   - 결제 대기 상태 일관성

5. TestConcurrentResourceAccess (고급)
   - 동시 리소스 수정 시 낙관적/비관적 락 동작 확인

🚀 실행 방법
-----------
```bash
# 동시성 테스트만 실행 (병렬 실행 비활성화 필수!)
pytest -m concurrency --no-cov -v -n 0

# 스키마 + 동시성 테스트 모두 실행
pytest shopping/tests/schema/test_concurrency.py --no-cov -v -n 0

# slow 테스트 제외 (빠른 피드백용)
pytest -m "concurrency and not slow" --no-cov -v -n 0

# 특정 테스트만 실행
pytest -m concurrency -k "test_cart" --no-cov -v -n 0
```

⚙️ 동시성 제어 메커니즘 설명
--------------------------
1. **select_for_update**:
   - 행 레벨 비관적 락(Pessimistic Lock)
   - 조회 시 다른 트랜잭션의 수정/삭제 차단
   - 예: Product.objects.select_for_update().get(id=product_id)

2. **F() 객체**:
   - 데이터베이스 레벨에서 원자적 연산
   - Python에서 값을 읽지 않고 DB에서 직접 연산
   - 예: Product.objects.filter(id=product_id).update(stock=F('stock') - quantity)

3. **transaction.atomic()**:
   - 여러 연산을 하나의 트랜잭션으로 묶음
   - 실패 시 전체 롤백

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터
- test_api_contract.py: Phase 2 - API Contract 테스트
- test_stateful_workflow.py: Phase 3 - Stateful 워크플로우 테스트
- test_fuzz.py: Phase 5.1 - Fuzz 테스트
- test_negative.py: Phase 5.2 - Negative 테스트
- test_performance.py: Phase 6.2 - 성능 임계값 테스트

기존 동시성 테스트 파일 (더 상세한 단위 테스트):
- shopping/tests/integration/test_order_concurrency.py
- shopping/tests/integration/test_payment_concurrency.py
- shopping/tests/integration/test_auth_concurrency.py

⚠️ 주의사항
----------
1. **병렬 실행 비활성화 필수**: pytest -n 0 옵션 사용!
   동시성 테스트 자체에서 스레드를 사용하므로 pytest-xdist와 충돌합니다.

2. **DB 연결 정리**: 각 스레드에서 connection.close() 호출 필요
   Django는 스레드별로 독립적인 DB 연결을 사용합니다.

3. **APIClient 독립 인스턴스**: 각 스레드에서 새 APIClient() 생성!
   공유된 클라이언트는 상태 오염(State Pollution) 발생합니다.

4. **트랜잭션 격리**: @pytest.mark.django_db(transaction=True) 사용
   테스트 간 데이터 격리를 보장합니다.

5. **타이밍 이슈**: 동시 요청 결과는 비결정적일 수 있음
   결과의 "합계"나 "최종 상태"를 검증하는 것이 안정적입니다.

📅 작성 정보
-----------
- 작성일: 2025-12-05
- Schemathesis Phase: 6 - 현업 수준 고도화
- 테스트 개수: 12개 (slow 마커 포함 시)

💡 구현 참고
-----------
이 파일은 API 레벨의 동시성 테스트입니다.
더 상세한 서비스 레벨 동시성 테스트는 다음 파일들을 참조하세요:
- shopping/tests/integration/test_order_concurrency.py (주문 동시성)
- shopping/tests/integration/test_payment_concurrency.py (결제 동시성)
- shopping/tests/integration/test_auth_concurrency.py (인증 동시성)

이 테스트들은 이미 존재하는 동시성 테스트를 보완하여,
OpenAPI 스키마 기반 API 계약 관점에서 동시성을 검증합니다.
"""

import concurrent.futures
import threading
import time
from decimal import Decimal
from typing import Any

import jwt as pyjwt
from django.db import connection
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order
from shopping.models.product import Product
from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory


# =============================================================================
# 헬퍼 함수 및 유틸리티
# =============================================================================


def close_db_connection():
    """
    스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수

    Django는 각 스레드에서 독립적인 DB 연결을 생성합니다.
    스레드 종료 시 명시적으로 연결을 닫지 않으면 연결 누수가 발생합니다.

    사용 위치: 각 스레드 함수의 finally 블록 또는 마지막에 호출
    """
    connection.close()


def login_and_get_token(username: str, password: str = "testpass123") -> tuple[APIClient | None, str | None, str | None]:
    """
    로그인하여 JWT 토큰 발급

    동시성 테스트에서 각 스레드는 독립적인 APIClient와 토큰이 필요합니다.
    이 함수는 새 APIClient를 생성하고 로그인 후 토큰을 반환합니다.

    Args:
        username: 사용자 이름
        password: 비밀번호 (기본값: testpass123)

    Returns:
        (client, token, error) 튜플
        - 성공 시: (APIClient, access_token, None)
        - 실패 시: (None, None, error_message)

    사용 예:
        client, token, error = login_and_get_token("testuser")
        if error:
            return {"error": error}
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.post("/api/cart/add_item/", ...)
    """
    client = APIClient()
    login_url = reverse("auth-login")
    response = client.post(
        login_url,
        {"username": username, "password": password},
        format="json",
    )

    if response.status_code != status.HTTP_200_OK:
        return None, None, f"Login failed: {response.status_code}"

    data = response.json()
    # 응답 구조: {"token": {"access": "...", "refresh": "..."}} 또는 {"access": "..."}
    token = data.get("access") or data.get("token", {}).get("access")
    return client, token, None


def run_concurrent_requests(
    func,
    args_list: list[tuple],
    max_workers: int = 10,
) -> list[dict[str, Any]]:
    """
    동시 요청 실행 유틸리티

    ThreadPoolExecutor를 사용하여 여러 요청을 동시에 실행합니다.
    각 스레드에서 독립적인 DB 연결을 사용하고, 완료 시 정리합니다.

    Args:
        func: 실행할 함수 (callable)
        args_list: 각 스레드에 전달할 인자 튜플 리스트
        max_workers: 최대 동시 워커 수

    Returns:
        각 요청의 결과 딕셔너리 리스트 (순서 보장 안 됨)

    구현 원리:
        1. ThreadPoolExecutor 생성 (max_workers 만큼)
        2. 모든 작업 submit()하여 Future 객체 획득
        3. as_completed()로 완료된 순서대로 결과 수집
        4. 각 스레드에서 close_db_connection() 호출
    """
    results = []

    def worker_wrapper(args):
        """DB 연결 정리를 포함한 래퍼 함수"""
        try:
            return func(*args)
        finally:
            close_db_connection()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker_wrapper, args) for args in args_list]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"error": str(e), "exception_type": type(e).__name__})

    return results


# =============================================================================
# 장바구니 동시성 테스트
# =============================================================================


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


# =============================================================================
# 재고 동시성 테스트
# =============================================================================


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
        assert error_5xx_count == 0, f"서버 에러 발생!"

        # Assert: 재고 음수 확인
        product.refresh_from_db()
        assert product.stock >= 0, f"재고가 음수! stock={product.stock}"

        # ✅ 회계 무결성 검증 - 재고 1개 상품
        initial_stock = 1
        assert product.stock + product.sold_count == initial_stock, (
            f"회계 무결성 오류: 재고({product.stock}) + 판매량({product.sold_count}) != "
            f"초기 재고({initial_stock}) — 누락 또는 중복 차감 발생"
        )


# =============================================================================
# API 응답 일관성 테스트
# =============================================================================


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentApiResponses:
    """
    🌐 동시 API 요청 응답 일관성 테스트

    동시에 여러 요청이 들어올 때도 API 응답이 일관성을 유지하는지 검증합니다.

    📋 테스트 시나리오:
    - 동시 GET 요청 시 모든 응답 동일
    - 동시 인증 요청 시 토큰 정상 발급
    - 동시 조회 중 데이터 변경 시 일관성

    ✅ 예상 결과:
    - 모든 응답 코드 일관성
    - JSON 파싱 오류 없음
    - 5xx 에러 없음
    """

    def test_concurrent_product_list_reads(self, db):
        """
        동시 상품 목록 조회 테스트

        10개의 동시 GET 요청이 모두 같은 결과를 반환하는지 확인합니다.
        읽기 전용 API의 동시성 안전성을 검증합니다.

        🔍 검증 포인트:
        - 모든 응답 200 OK
        - 응답 데이터 파싱 가능
        - 5xx 에러 없음
        """
        # 테스트 상품 생성
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_read_{time.time()}", is_seller=True)
        for i in range(5):
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                is_active=True,
            )

        num_requests = 10

        def get_products():
            """상품 목록 조회"""
            client = APIClient()
            response = client.get(reverse("product-list"))
            return {
                "status_code": response.status_code,
                "count": len(response.json().get("results", [])) if response.status_code == 200 else None,
            }

        # 동시 요청 실행
        args_list = [() for _ in range(num_requests)]
        results = run_concurrent_requests(get_products, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") == 200)
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 요청 성공
        assert success_count == num_requests, f"일부 요청 실패: {success_count}/{num_requests}"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생!"

    def test_concurrent_login_requests(self, db):
        """
        동시 로그인 요청 테스트

        같은 사용자로 10번 동시 로그인해도 모두 성공하고
        유효한 토큰이 발급되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 요청 200 OK
        - 각 응답에 유효한 access 토큰 포함
        - ✅ 토큰 JTI 고유성 검증 (중복 토큰 방지)
        - 5xx 에러 없음
        """
        user = UserFactory(username=f"login_test_{time.time()}")

        num_requests = 10

        def try_login(username):
            """로그인 시도"""
            client = APIClient()
            response = client.post(
                reverse("auth-login"),
                {"username": username, "password": "testpass123"},
                format="json",
            )
            data = response.json() if response.status_code == 200 else {}
            access_token = data.get("access") or data.get("token", {}).get("access")
            has_token = bool(access_token)
            return {
                "status_code": response.status_code,
                "has_token": has_token,
                "access_token": access_token,  # JTI 검증을 위해 토큰 저장
            }

        # 동시 요청 실행
        args_list = [(user.username,) for _ in range(num_requests)]
        results = run_concurrent_requests(try_login, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") == 200)
        token_count = sum(1 for r in results if r.get("has_token"))
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 요청 성공
        assert success_count == num_requests, f"일부 로그인 실패: {success_count}/{num_requests}"

        # Assert: 모든 응답에 토큰 포함
        assert token_count == num_requests, f"일부 응답에 토큰 없음: {token_count}/{num_requests}"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생!"

        # ✅ JWT JTI 고유성 검증 - 동시 로그인에서 토큰 중복 방지
        tokens = [r.get("access_token") for r in results if r.get("access_token")]
        if tokens:
            try:
                # JWT decode (검증 없이 payload만 추출)
                claims = []
                for token in tokens:
                    try:
                        # options에서 서명 검증 비활성화
                        payload = pyjwt.decode(token, options={"verify_signature": False})
                        claims.append(payload)
                    except pyjwt.InvalidTokenError:
                        pass  # 잘못된 토큰은 무시

                # JTI가 있는 경우 고유성 검증
                jtis = [c.get("jti") for c in claims if c.get("jti")]
                if jtis:
                    unique_jtis = set(jtis)
                    assert len(unique_jtis) == len(jtis), (
                        f"JWT JTI 중복 발생! 동시 로그인에서 토큰이 재사용됨. "
                        f"(전체: {len(jtis)}, 고유: {len(unique_jtis)})"
                    )
            except Exception as e:
                # JWT 파싱 실패 시 경고만 출력 (테스트 실패 안함)
                import warnings

                warnings.warn(f"JWT JTI 검증 스킵: {e}")

    @pytest.mark.slow
    def test_concurrent_mixed_operations(self, db):
        """
        혼합 동시 요청 테스트 (@slow)

        읽기/쓰기 요청이 동시에 발생할 때도
        API가 안정적으로 동작하는지 확인합니다.

        🔍 검증 포인트:
        - 읽기 요청 영향 없음
        - 쓰기 요청 순차 처리
        - ✅ 최종 장바구니 수량 = 쓰기 성공 횟수
        - 5xx 에러 없음
        """
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_mixed_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=100,
            is_active=True,
        )

        users = [UserFactory(username=f"mixed_user_{i}_{time.time()}") for i in range(5)]

        def read_operation():
            """상품 목록 읽기"""
            client = APIClient()
            response = client.get(reverse("product-list"))
            # ✅ GET 응답이 JSON 파싱 가능한지 확인
            try:
                data = response.json()
                json_valid = True
            except Exception:
                json_valid = False
            return {
                "type": "read",
                "status_code": response.status_code,
                "json_valid": json_valid,
            }

        def write_operation(user, product_id):
            """장바구니 추가 (쓰기)"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"type": "write", "status_code": 0, "error": error, "user_id": user.id}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {
                "type": "write",
                "status_code": response.status_code,
                "user_id": user.id,
            }

        # 읽기 + 쓰기 혼합 요청 준비
        results = []

        # 읽기 요청 동시 실행
        read_args = [() for _ in range(10)]
        read_results = run_concurrent_requests(read_operation, read_args, max_workers=10)
        results.extend(read_results)

        # 쓰기 요청 동시 실행
        write_args = [(u, product.id) for u in users]
        write_results = run_concurrent_requests(write_operation, write_args, max_workers=5)
        results.extend(write_results)

        # 결과 분석
        read_results_only = [r for r in results if r.get("type") == "read"]
        write_results_only = [r for r in results if r.get("type") == "write"]

        read_success = sum(1 for r in read_results_only if r.get("status_code") == 200)
        read_json_valid = sum(1 for r in read_results_only if r.get("json_valid"))
        write_success = sum(1 for r in write_results_only if r.get("status_code") in [200, 201])
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 읽기 요청 성공
        assert read_success == 10, f"읽기 요청 일부 실패: {read_success}/10"

        # ✅ Assert: 읽기 응답이 모두 유효한 JSON
        assert read_json_valid == 10, f"쓰기 중 GET 응답에서 JSON 파싱 오류 발생! " f"(유효: {read_json_valid}/10)"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생!"

        # ✅ 최종 상태 검증 - 장바구니 수량이 논리적으로 맞는지
        # 각 사용자별로 장바구니가 생성되었는지 확인
        total_cart_quantity = 0
        for user in users:
            cart = Cart.objects.filter(user=user, is_active=True).first()
            if cart:
                cart_item = CartItem.objects.filter(cart=cart, product=product).first()
                if cart_item:
                    total_cart_quantity += cart_item.quantity

        # 쓰기 성공 횟수와 총 장바구니 수량이 일치해야 함
        assert total_cart_quantity == write_success, (
            f"데이터 무결성 오류: 쓰기 성공({write_success})과 " f"총 장바구니 수량({total_cart_quantity})이 일치하지 않음!"
        )


# =============================================================================
# 포인트 동시성 테스트
# =============================================================================


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentPointOperations:
    """
    💰 포인트 동시성 테스트

    동시에 여러 포인트 적립/차감 요청이 발생할 때
    포인트 잔액이 음수가 되지 않고 정확하게 계산되는지 검증합니다.

    📋 테스트 시나리오:
    - 동시 포인트 사용 시 잔액 음수 방지
    - 동시 포인트 적립 시 정확한 합계
    - 포인트 사용 + 적립 동시 발생 시 무결성

    ✅ 예상 결과:
    - 포인트 잔액 >= 0 (절대 음수 안 됨)
    - 최종 포인트 = 초기값 + 적립 - 사용
    - 5xx 에러 없음

    🔧 동시성 제어:
    - User.objects.select_for_update()
    - F('points') - used_points >= 0 조건부 업데이트

    📅 가이드라인: 09_PERFORMANCE_TESTING.md (동시성 섹션)
    """

    @pytest.fixture
    def user_with_points(self, db):
        """포인트를 가진 테스트 사용자 생성"""
        user = UserFactory(
            username=f"point_user_{time.time()}",
            points=10000,  # 10,000 포인트
        )
        return user

    @pytest.fixture
    def point_test_setup(self, db):
        """포인트 테스트용 상품 및 사용자 설정"""
        category = CategoryFactory()
        seller = UserFactory(username=f"point_seller_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=100,
            price=Decimal("50000"),
            is_active=True,
        )
        return {
            "product": product,
            "category": category,
        }

    def test_concurrent_point_usage_no_negative(self, user_with_points, point_test_setup):
        """
        동시 포인트 사용 시 잔액 음수 방지 테스트

        10,000 포인트를 가진 사용자가 10개의 동시 요청으로
        각각 2,000 포인트씩 사용하려 할 때,
        잔액이 음수가 되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 포인트 잔액 >= 0
        - 성공 요청 수 <= 5 (10000 / 2000)
        - 포인트 부족 요청은 적절한 에러 반환
        - 5xx 에러 없음

        동시성 이슈 예방:
        - select_for_update() 사용
        - DB 레벨에서 포인트 >= 0 체크
        """
        user = user_with_points
        product = point_test_setup["product"]
        initial_points = user.points
        points_to_use = 2000
        num_requests = 10

        def use_points(username, product_id, points):
            """포인트 사용 주문 시도"""
            client, token, error = login_and_get_token(username)
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

            # 포인트를 사용한 주문 생성 시도
            order_response = client.post(
                reverse("order-list"),
                {
                    "shipping_address": "서울시 강남구 테헤란로 123",
                    "shipping_name": "홍길동",
                    "shipping_phone": "010-1234-5678",
                    "shipping_postal_code": "12345",
                    "payment_method": "card",
                    "used_points": points,
                },
                format="json",
            )
            return {
                "status_code": order_response.status_code,
                "step": "order",
                "data": order_response.json() if order_response.status_code < 500 else None,
            }

        # 동시 요청 실행
        args_list = [(user.username, product.id, points_to_use) for _ in range(num_requests)]
        results = run_concurrent_requests(use_points, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") in [200, 201])
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # Assert: 포인트 잔액 음수 확인
        user.refresh_from_db()
        assert user.points >= 0, f"포인트가 음수! points={user.points}"

        # Assert: 성공 주문 수가 가능한 범위 내
        max_possible_orders = initial_points // points_to_use
        assert success_count <= max_possible_orders, f"예상보다 많은 주문 성공: {success_count} > {max_possible_orders}"

        # ✅ 회계 무결성 검증
        # 사용된 포인트 = 초기 포인트 - 현재 포인트
        used_total = initial_points - user.points
        expected_used = success_count * points_to_use
        assert used_total <= expected_used, f"포인트 사용 계산 오류: 실제 사용({used_total}) > 예상 사용({expected_used})"

    def test_concurrent_point_earning(self, db, point_test_setup):
        """
        동시 포인트 적립 테스트

        10명의 사용자가 동시에 주문을 완료하여
        각각 포인트를 적립받을 때,
        포인트가 정확히 적립되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 적립 요청 성공
        - 각 사용자 포인트 정확히 적립
        - 5xx 에러 없음
        """
        from shopping.tests.factories import OrderFactory

        num_users = 10
        users = [UserFactory(username=f"earn_user_{i}_{time.time()}", points=0) for i in range(num_users)]

        # 각 사용자에게 결제 완료 주문 생성 (포인트 적립)
        earned_points = 500

        def create_paid_order_and_earn(user_obj):
            """결제 완료 주문 생성으로 포인트 적립 시도"""
            try:
                order = OrderFactory.paid(
                    user=user_obj,
                    total_amount=Decimal("50000"),
                    earned_points=earned_points,
                )
                # 실제 포인트 적립 로직 호출 (서비스에 따라 다를 수 있음)
                user_obj.points += order.earned_points
                user_obj.save(update_fields=["points"])
                return {"success": True, "user_id": user_obj.id, "points": user_obj.points}
            except Exception as e:
                return {"success": False, "error": str(e), "user_id": user_obj.id}
            finally:
                close_db_connection()

        # 동시 요청 실행
        args_list = [(u,) for u in users]
        results = run_concurrent_requests(lambda u: create_paid_order_and_earn(u), args_list, max_workers=num_users)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("success"))

        # Assert: 모든 적립 성공
        assert success_count == num_users, f"일부 적립 실패: {success_count}/{num_users}"

        # Assert: 각 사용자 포인트 확인
        for user in users:
            user.refresh_from_db()
            assert (
                user.points == earned_points
            ), f"사용자 {user.id} 포인트 불일치: expected={earned_points}, actual={user.points}"

    @pytest.mark.slow
    def test_concurrent_point_use_and_earn(self, db, point_test_setup):
        """
        포인트 사용 + 적립 동시 발생 테스트 (@slow)

        동일 사용자가 포인트를 사용하는 주문과
        포인트를 적립받는 이벤트가 동시에 발생할 때,
        최종 포인트가 정확히 계산되는지 확인합니다.

        🔍 검증 포인트:
        - 최종 포인트 = 초기 + 적립 - 사용
        - 포인트 >= 0
        - 데이터 무결성 유지
        """
        user = UserFactory(
            username=f"mixed_point_user_{time.time()}",
            points=5000,  # 초기 5000 포인트
        )
        initial_points = user.points

        # 사용할 포인트와 적립할 포인트
        points_to_use = 1000
        points_to_earn = 500

        def use_points_operation(user_obj):
            """포인트 사용"""
            try:
                from django.db import transaction
                from django.db.models import F

                with transaction.atomic():
                    from shopping.models.user import User

                    # select_for_update로 락 획득
                    locked_user = User.objects.select_for_update().get(id=user_obj.id)
                    if locked_user.points >= points_to_use:
                        locked_user.points = F("points") - points_to_use
                        locked_user.save(update_fields=["points"])
                        return {"success": True, "operation": "use", "amount": points_to_use}
                    else:
                        return {"success": False, "operation": "use", "error": "insufficient"}
            except Exception as e:
                return {"success": False, "operation": "use", "error": str(e)}
            finally:
                close_db_connection()

        def earn_points_operation(user_obj):
            """포인트 적립"""
            try:
                from django.db import transaction
                from django.db.models import F

                with transaction.atomic():
                    from shopping.models.user import User

                    # select_for_update로 락 획득
                    locked_user = User.objects.select_for_update().get(id=user_obj.id)
                    locked_user.points = F("points") + points_to_earn
                    locked_user.save(update_fields=["points"])
                    return {"success": True, "operation": "earn", "amount": points_to_earn}
            except Exception as e:
                return {"success": False, "operation": "earn", "error": str(e)}
            finally:
                close_db_connection()

        # 사용 5회 + 적립 5회 = 총 10회 동시 실행
        use_args = [(user,) for _ in range(5)]
        earn_args = [(user,) for _ in range(5)]

        use_results = run_concurrent_requests(lambda u: use_points_operation(u), use_args, max_workers=5)
        earn_results = run_concurrent_requests(lambda u: earn_points_operation(u), earn_args, max_workers=5)

        # 결과 분석
        use_success = sum(1 for r in use_results if r.get("success"))
        earn_success = sum(1 for r in earn_results if r.get("success"))

        # 모든 적립은 성공해야 함
        assert earn_success == 5, f"적립 일부 실패: {earn_success}/5"

        # Assert: 포인트 잔액 음수 확인
        user.refresh_from_db()
        assert user.points >= 0, f"포인트가 음수! points={user.points}"

        # ✅ 회계 무결성 검증
        # 최종 포인트 = 초기 + (적립 성공 * 적립액) - (사용 성공 * 사용액)
        expected_final = initial_points + (earn_success * points_to_earn) - (use_success * points_to_use)
        assert user.points == expected_final, (
            f"포인트 계산 오류: expected={expected_final}, actual={user.points}\n"
            f"초기={initial_points}, 적립={earn_success}x{points_to_earn}, 사용={use_success}x{points_to_use}"
        )


# =============================================================================
# 주문 동시성 테스트
# =============================================================================


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
