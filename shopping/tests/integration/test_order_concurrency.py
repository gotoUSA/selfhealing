"""
주문 동시성 테스트

Purpose:
    주문 생성/취소 과정에서 동시성 문제(재고 음수, 포인트 중복 차감 등)를 방지하는지 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 재고 음수 방지
        - 포인트 음수 방지
        - F() 객체를 통한 원자적 업데이트
    B. Parameterized Scenarios:
        - 다양한 사용자 수(10/20/50)에 대한 스케일 테스트
    C. Edge Cases:
        - 재고 경계값, 포인트 경계값

Concurrency Control:
    - select_for_update on Product.stock
    - F() atomic decrement with gte=quantity condition
    - Transaction isolation level: READ COMMITTED

APIClient Usage:
    ⚠️ 동시성 테스트에서는 반드시 각 스레드/함수 내부에서 독립적인 APIClient()를 생성해야 합니다.
    공유된 api_client fixture를 사용하면 상태 오염(state pollution)으로 인해
    테스트 결과가 비결정적(non-deterministic)이 됩니다.

    올바른 패턴:
        def concurrent_request():
            client = APIClient()  # 스레드별 독립 인스턴스
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            return client.post(...)

    잘못된 패턴:
        def concurrent_request(shared_client):  # 공유된 클라이언트
            return shared_client.post(...)  # Race condition 발생!
"""

import threading
import time
from decimal import Decimal
from typing import Any

from django.db import connection
from django.db.models import F
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models import Product
from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order
from shopping.models.user import User
from shopping.tests.factories import ProductFactory, TestConstants, UserFactory


# =============================================================================
# 헬퍼 함수 및 유틸리티
# =============================================================================


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


def login_and_get_token(username: str, password: str = TestConstants.DEFAULT_PASSWORD) -> tuple:
    """
    로그인하여 JWT 토큰 발급

    Returns:
        (client, token, error) 튜플
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
    token = data.get("access") or data.get("token", {}).get("access")
    return client, token, None


def wait_for_order_completion(order_id: int, max_wait: float = 5, interval: float = 0.1) -> Order | None:
    """
    주문 완료까지 폴링 대기 (time.sleep 하드코딩 대신 사용)

    Args:
        order_id: 주문 ID
        max_wait: 최대 대기 시간 (초)
        interval: 폴링 간격 (초)

    Returns:
        완료된 Order 객체 또는 None
    """
    start = time.time()
    while time.time() - start < max_wait:
        try:
            order = Order.objects.get(id=order_id)
            if order.status in ["confirmed", "failed", "cancelled"]:
                return order
        except Order.DoesNotExist:
            pass
        time.sleep(interval)
    return Order.objects.filter(id=order_id).first()


def verify_async_order_result(response, expected_success: bool = True) -> tuple[bool, Order | None, str]:
    """
    비동기 주문 생성 결과 검증

    Returns:
        (success, order, message) 튜플
    """
    if response.status_code == status.HTTP_202_ACCEPTED:
        data = response.json()
        order_id = data.get("order_id")
        if not order_id:
            return False, None, "No order_id in 202 response"
        order = wait_for_order_completion(order_id)
        if not order:
            return False, None, f"Order {order_id} not found"
        if expected_success:
            success = order.status == "confirmed"
        else:
            success = order.status in ["failed", "cancelled"]
        return success, order, f"Order {order.id} status: {order.status}"
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        return False, None, f"400 Bad Request: {response.data}"
    return False, None, f"Unexpected status: {response.status_code}"


def create_concurrent_users_with_carts(
    count: int,
    product: Product,
    quantity: int = 1,
    points: int = 0,
    username_prefix: str = "user",
) -> list[User]:
    """
    동시성 테스트용 사용자 및 장바구니 일괄 생성

    Args:
        count: 생성할 사용자 수
        product: 장바구니에 담을 상품
        quantity: 상품 수량
        points: 초기 포인트
        username_prefix: 사용자명 접두사

    Returns:
        생성된 User 리스트
    """
    users = []
    cart_items = []

    for i in range(count):
        user = UserFactory(
            username=f"{username_prefix}_{i}",
            email=f"{username_prefix}_{i}@test.com",
            phone_number=f"010-{i:04d}-{i:04d}",
            points=points,
            is_email_verified=True,
        )
        users.append(user)
        cart, _ = Cart.get_or_create_active_cart(user)
        cart_items.append(CartItem(cart=cart, product=product, quantity=quantity))

    CartItem.objects.bulk_create(cart_items)
    return users


def run_concurrent_orders(
    users: list[User],
    shipping_data: dict,
    use_points: int = 0,
) -> list[dict[str, Any]]:
    """
    동시 주문 실행 헬퍼

    Args:
        users: 주문할 사용자 리스트
        shipping_data: 배송 정보
        use_points: 사용할 포인트

    Returns:
        각 스레드의 결과 리스트
    """
    results = []
    lock = threading.Lock()

    def create_order(user_obj: User):
        try:
            client, token, error = login_and_get_token(user_obj.username)
            if error:
                with lock:
                    results.append({"user": user_obj.username, "error": error})
                return

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            order_data = {**shipping_data}
            if use_points > 0:
                order_data["use_points"] = use_points

            response = client.post("/api/orders/", order_data, format="json")
            success, order, msg = verify_async_order_result(response, expected_success=True)

            with lock:
                results.append(
                    {
                        "user": user_obj.username,
                        "status": response.status_code,
                        "success": success,
                        "message": msg,
                    }
                )
        except Exception as e:
            with lock:
                results.append({"user": user_obj.username, "error": str(e)})
        finally:
            close_db_connection()

    threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.order_race
class TestOrderStockInvariant:
    """
    핵심 불변 조건: 재고 음수 방지

    Purpose:
        동시 주문 시 재고가 음수가 되지 않도록 보장
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        select_for_update + F() atomic decrement
    """

    def test_stock_never_goes_negative_with_concurrent_orders(self, product, shipping_data):
        """
        Purpose:
            재고 1개에 5명 동시 주문 시 정확히 1명만 성공하고 재고는 0이 됨
        Scenario:
            5 concurrent purchase requests on stock=1
        Expected:
            1 success, 4 failures, stock=0 (never negative)
        """
        # Arrange
        product.stock = 1
        product.save()

        users = create_concurrent_users_with_carts(
            count=5,
            product=product,
            quantity=1,
            username_prefix="stock_inv",
        )

        # Act
        results = run_concurrent_orders(users, shipping_data)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == 1, f"정확히 1명만 성공해야 함. 실제: {success_count}"
        assert product.stock == 0, f"재고는 0이어야 함 (음수 아님). 실제: {product.stock}"
        assert product.stock >= 0, "재고는 절대 음수가 되어서는 안 됨"

    def test_f_object_prevents_negative_stock(self, category):
        """
        Purpose:
            F() 객체를 사용한 원자적 재고 차감이 음수를 방지하는지 검증
        Scenario:
            6 threads each trying to decrement stock by 2 (total 12) on stock=10
        Expected:
            5 successes (10/2=5), 1 failure, final stock=0
        Concurrency Control:
            F() atomic update with stock__gte condition
        """
        # Arrange
        product = ProductFactory(
            name="F() 테스트 상품",
            slug="f-test-product",
            category=category,
            price=Decimal("5000"),
            stock=10,
            sku="F-TEST-001",
        )

        results = []
        lock = threading.Lock()

        def decrease_stock_with_f():
            try:
                updated = Product.objects.filter(
                    id=product.id,
                    stock__gte=2,
                ).update(stock=F("stock") - 2)
                with lock:
                    results.append({"success": updated > 0})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=decrease_stock_with_f) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == 5, f"5개 스레드 성공해야 함. 실제: {success_count}"
        assert product.stock == 0, f"최종 재고는 0이어야 함. 실제: {product.stock}"
        assert product.stock >= 0, "재고는 절대 음수가 되어서는 안 됨"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestOrderPointInvariant:
    """
    핵심 불변 조건: 포인트 무결성

    Purpose:
        동시 주문 시 포인트가 음수가 되거나 중복 차감되지 않도록 보장
    Type:
        Core invariant test (must never fail)
    """

    def test_points_never_go_negative_with_concurrent_orders(self, product, shipping_data):
        """
        Purpose:
            보유 포인트보다 많은 포인트 사용 시도 시 모두 실패해야 함
        Scenario:
            3 users with 500P each try to use 1000P simultaneously
        Expected:
            All 3 fail (insufficient points), points unchanged
        """
        # Arrange
        product.stock = 50
        product.price = Decimal("10000")
        product.save()

        users = create_concurrent_users_with_carts(
            count=3,
            product=product,
            quantity=1,
            points=500,  # 500P 보유
            username_prefix="point_inv",
        )

        # Act - 1000P 사용 시도 (보유 500P)
        results = run_concurrent_orders(users, shipping_data, use_points=1000)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        assert success_count == 0, f"포인트 부족으로 모두 실패해야 함. 성공: {success_count}"

        # 포인트 변화 없음 확인
        for u in users:
            u.refresh_from_db()
            assert u.points == 500, f"{u.username}의 포인트가 변하지 않아야 함"

    def test_point_deduction_is_atomic_and_consistent(self, product, shipping_data):
        """
        Purpose:
            포인트 전액 사용 시 원자적으로 차감되는지 검증
        Scenario:
            2 users with 1000P each use all points simultaneously
        Expected:
            Both succeed with exactly 0P remaining (no race condition)
        """
        # Arrange
        product.stock = 50
        product.price = Decimal("10000")
        product.save()

        users = create_concurrent_users_with_carts(
            count=2,
            product=product,
            quantity=1,
            points=1000,
            username_prefix="point_atomic",
        )

        # Act - 전액 1000P 사용
        results = run_concurrent_orders(users, shipping_data, use_points=1000)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 2, f"2명 모두 성공해야 함. 성공: {success_count}"

        for u in users:
            u.refresh_from_db()
            assert u.points == 0, f"{u.username}의 포인트가 정확히 0이어야 함"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.order_race
class TestOrderCancelInvariant:
    """
    핵심 불변 조건: 주문 취소 중복 방지

    Purpose:
        동일 주문에 대한 동시 취소 요청 시 1번만 처리되도록 보장
    Type:
        Core invariant test (must never fail)
    """

    def test_concurrent_cancel_only_one_succeeds(self, product, shipping_data):
        """
        Purpose:
            동일 주문에 5번 동시 취소 시도 시 1번만 성공
        Scenario:
            5 concurrent cancel requests on same order
        Expected:
            1 success, 4 failures (already cancelled)
        """
        # Arrange
        user = UserFactory(
            username="cancel_test_user",
            email="cancel_test@test.com",
            phone_number="010-7000-0001",
            is_email_verified=True,
        )
        cart, _ = Cart.get_or_create_active_cart(user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        client, token, error = login_and_get_token(user.username)
        if error:
            pytest.skip(f"로그인 실패: {error}")

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.post("/api/orders/", shipping_data, format="json")

        if response.status_code != status.HTTP_202_ACCEPTED:
            pytest.skip(f"주문 생성 실패: {response.status_code}")

        order_id = response.json().get("order_id")
        order = wait_for_order_completion(order_id)
        if not order or order.status != "confirmed":
            pytest.skip(f"주문이 confirmed 상태가 아님: {order.status if order else 'None'}")

        results = []
        lock = threading.Lock()

        def cancel_order():
            try:
                cancel_client = APIClient()
                cancel_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                resp = cancel_client.post(f"/api/orders/{order_id}/cancel/")
                with lock:
                    results.append(
                        {
                            "status": resp.status_code,
                            "success": resp.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=cancel_order) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 1, f"1번만 취소 성공해야 함. 성공: {success_count}"


# =============================================================================
# B. 파라미터화된 스케일 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.order_race
@pytest.mark.slow
class TestOrderConcurrencyScale:
    """
    스케일 검증 테스트

    Purpose:
        다양한 규모의 동시 요청에서 시스템 안정성 검증
    Note:
        50명 이상은 DB 커넥션 풀 한계로 Locust 사용 권장
    """

    @pytest.mark.parametrize(
        "user_count,expected_stock_remaining",
        [
            (10, 10),  # 20 - 10 = 10
            (20, 20),  # 40 - 20 = 20
            (50, 50),  # 100 - 50 = 50
        ],
    )
    def test_concurrent_order_scale(
        self,
        product,
        shipping_data,
        user_count: int,
        expected_stock_remaining: int,
    ):
        """
        Purpose:
            다양한 사용자 수에서 동시 주문 처리 검증
        Scenario:
            {user_count} users ordering 1 item each, stock = user_count * 2
        Expected:
            All succeed, stock = expected_stock_remaining
        """
        # Arrange
        product.stock = user_count * 2
        product.save()

        users = create_concurrent_users_with_carts(
            count=user_count,
            product=product,
            quantity=1,
            username_prefix=f"scale_{user_count}",
        )

        # Act
        start_time = time.time()
        results = run_concurrent_orders(users, shipping_data)
        elapsed_time = time.time() - start_time

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == user_count, f"{user_count}명 모두 성공해야 함. 성공: {success_count}"
        assert product.stock == expected_stock_remaining, f"재고가 {expected_stock_remaining}개 남아야 함"
        assert elapsed_time < 120, f"2분 내 완료되어야 함. 실제: {elapsed_time:.2f}초"

    @pytest.mark.parametrize(
        "stock,order_qty,user_count,expected_success",
        [
            (5, 2, 3, 2),  # 5재고, 2개씩 3명 -> 2명만 성공
            (10, 3, 5, 3),  # 10재고, 3개씩 5명 -> 3명만 성공
            (1, 1, 10, 1),  # 1재고, 1개씩 10명 -> 1명만 성공
        ],
    )
    def test_stock_boundary_with_various_scenarios(
        self,
        product,
        shipping_data,
        stock: int,
        order_qty: int,
        user_count: int,
        expected_success: int,
    ):
        """
        Purpose:
            다양한 재고/주문 조합에서 경계값 테스트
        Scenario:
            stock={stock}, {user_count} users ordering {order_qty} each
        Expected:
            {expected_success} succeed, stock approaches 0
        """
        # Arrange
        product.stock = stock
        product.save()

        users = create_concurrent_users_with_carts(
            count=user_count,
            product=product,
            quantity=order_qty,
            username_prefix=f"boundary_{stock}_{order_qty}",
        )

        # Act
        results = run_concurrent_orders(users, shipping_data)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == expected_success, f"{expected_success}명 성공해야 함. 실제: {success_count}"
        assert product.stock >= 0, "재고는 음수가 되어서는 안 됨"
        assert product.stock == stock - (expected_success * order_qty), "재고 계산 정확성 검증"


# =============================================================================
# C. 통합 시나리오 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.order_race
class TestOrderConcurrencyIntegration:
    """
    통합 동시성 시나리오

    Purpose:
        재고 + 포인트가 동시에 경합하는 복합 시나리오 검증
    """

    def test_stock_and_point_race_condition(self, product, shipping_data):
        """
        Purpose:
            재고와 포인트가 동시에 경합할 때 둘 다 정합성 유지
        Scenario:
            3 users with 5000P each, stock=2, all use 1000P
        Expected:
            2 succeed (stock limit), 1 fails
            Successful users: points = 4000P
            Failed user: points = 5000P (unchanged)
        """
        # Arrange
        product.stock = 2
        product.price = Decimal("10000")
        product.save()

        users = create_concurrent_users_with_carts(
            count=3,
            product=product,
            quantity=1,
            points=5000,
            username_prefix="race",
        )

        # Act
        results = run_concurrent_orders(users, shipping_data, use_points=1000)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == 2, f"재고 2개이므로 2명만 성공. 실제: {success_count}"
        assert product.stock == 0, "재고가 모두 차감되어야 함"

        # 포인트 정합성 검증
        success_users = [r["user"] for r in results if r.get("success", False)]
        for u in users:
            u.refresh_from_db()
            if u.username in success_users:
                assert u.points == 4000, f"성공한 {u.username}의 포인트가 4000P여야 함"
            else:
                assert u.points == 5000, f"실패한 {u.username}의 포인트가 유지되어야 함"

    def test_concurrent_cart_to_order_idempotency(self, product, shipping_data):
        """
        Purpose:
            동일 장바구니로 동시 주문 시 중복 생성 방지
        Scenario:
            Same user, 3 concurrent order requests from same cart
        Expected:
            At least 1 success, no duplicate orders
        """
        # Arrange
        product.stock = 50
        product.save()

        user = UserFactory(
            username="cart_idemp_user",
            email="cart_idemp@test.com",
            phone_number="010-7000-0002",
            is_email_verified=True,
        )
        cart, _ = Cart.get_or_create_active_cart(user)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        client, token, error = login_and_get_token(user.username)
        if error:
            pytest.skip(f"로그인 실패: {error}")

        results = []
        lock = threading.Lock()

        def create_order():
            try:
                order_client = APIClient()
                order_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                resp = order_client.post("/api/orders/", shipping_data, format="json")
                success, order, msg = verify_async_order_result(resp)
                with lock:
                    results.append({"success": success, "message": msg})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=create_order) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count >= 1, f"최소 1개는 성공해야 함. 성공: {success_count}"
        assert Order.objects.filter(user=user).count() == 1  # 중복 주문 없어야 함


# =============================================================================
# D. 락 순서 불변 조건 (Lock Ordering Invariant)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.order_race
@pytest.mark.slow
class TestOrderLockOrderInvariant:
    """
    핵심 불변 조건: 여러 상품을 잠글 때 순서가 고정되어 순환 대기가 생기지 않는다

    Purpose:
        담은 순서가 서로 반대인 두 장바구니가 동시에 주문될 때,
        process_order_heavy_tasks가 상품 ID 순으로 잠그므로 데드락이 나지 않는지 검증
    Type:
        Core invariant test — order_tasks.py의 order_by("product_id")를 지우면 빨개진다
    Concurrency Control:
        cart items를 product_id로 정렬한 뒤 select_for_update (전역 락 순서)
    Assertion choice:
        데드락은 "데이터가 틀리는" 축이 아니라 "서로 기다리는" 축이라 단언 대상이 다르다.
        - 소요 시간 단언: 느린 CI에서 거짓 실패하므로 쓰지 않는다
        - 로그 단언: Celery task logger가 caplog으로 전파되지 않아 잡히지 않는다
        - 대신 상태로 단언한다. 테스트 설정은 eager + eager_propagates라 retry()가
          재실행이 아니라 Retry를 올리므로, 데드락이 나면 주문이 pending으로 남고
          재고가 덜 빠진다 — 그 두 가지를 본다.
    """

    ROUNDS = 10
    INITIAL_STOCK = 100

    @staticmethod
    def _refill_carts(users, first_order, second_order):
        """두 사용자의 장바구니를 서로 반대 순서로 다시 채운다.

        CartItem.Meta.ordering = ["-added_at"] 이므로 담은 순서의 역순으로 잠근다.
        """
        for user, products in ((users[0], first_order), (users[1], second_order)):
            cart, _ = Cart.get_or_create_active_cart(user)
            cart.items.all().delete()
            for product in products:
                CartItem.objects.create(cart=cart, product=product, quantity=1)
                time.sleep(0.01)  # added_at 이 같은 값이 되지 않도록

    def test_opposite_cart_order_does_not_deadlock(self, category, shipping_data):
        """
        Purpose:
            상품 두 개를 서로 반대 순서로 담은 두 주문이 동시에 들어와도
            데드락 없이 둘 다 확정되고 재고가 정확한지 검증
        Scenario:
            user A: P1 → P2 담음 (잠그는 순서 P2, P1)
            user B: P2 → P1 담음 (잠그는 순서 P1, P2)
            두 사용자가 threading.Barrier 로 같은 순간에 주문, 10회 반복
        Expected:
            모든 요청 202, 주문 20건 전부 confirmed, 두 상품 재고 정확히 20 감소
        Note:
            1회만 돌리면 두 스레드의 락 획득 창이 어긋나 운으로 통과할 수 있어 반복한다
        """
        # Arrange
        product_a = ProductFactory(
            name="락 순서 테스트 상품 A",
            slug="lock-order-a",
            category=category,
            price=Decimal("10000"),
            stock=self.INITIAL_STOCK,
            sku="LOCK-ORDER-A",
        )
        product_b = ProductFactory(
            name="락 순서 테스트 상품 B",
            slug="lock-order-b",
            category=category,
            price=Decimal("10000"),
            stock=self.INITIAL_STOCK,
            sku="LOCK-ORDER-B",
        )
        assert product_a.id < product_b.id, "상품 ID 순서를 전제로 하는 테스트"

        users = [
            UserFactory(
                username=f"lock_order_{i}",
                email=f"lock_order_{i}@test.com",
                phone_number=f"010-7777-{i:04d}",
                points=0,
                is_email_verified=True,
            )
            for i in range(2)
        ]
        tokens = []
        for user in users:
            _, token, error = login_and_get_token(user.username)
            assert error is None, error
            tokens.append(token)

        # 담은 순서가 실제로 반대인지 확인 — CartItem.Meta.ordering 이 바뀌면
        # 이 테스트가 조용히 아무것도 검증하지 않게 되므로 전제를 단언한다
        self._refill_carts(users, (product_a, product_b), (product_b, product_a))
        lock_orders = [
            list(Cart.get_or_create_active_cart(user)[0].items.values_list("product_id", flat=True)) for user in users
        ]
        assert lock_orders[0] == lock_orders[1][::-1], f"두 장바구니의 잠금 순서가 반대여야 함: {lock_orders}"

        # Act
        rounds = []
        for round_index in range(self.ROUNDS):
            if round_index > 0:
                self._refill_carts(users, (product_a, product_b), (product_b, product_a))

            barrier = threading.Barrier(2)
            responses: dict[int, Any] = {}
            lock = threading.Lock()

            def place_order(index: int):
                try:
                    barrier.wait(timeout=30)
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens[index]}")
                    response = client.post("/api/orders/", shipping_data, format="json")
                    with lock:
                        responses[index] = response.status_code
                except Exception as e:
                    with lock:
                        responses[index] = f"error: {e}"
                finally:
                    close_db_connection()

            threads = [threading.Thread(target=place_order, args=(i,)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            rounds.append({"round": round_index, "codes": [responses.get(0), responses.get(1)]})

        # Assert
        product_a.refresh_from_db()
        product_b.refresh_from_db()
        expected_sold = self.ROUNDS * 2

        bad_rounds = [r for r in rounds if r["codes"] != [status.HTTP_202_ACCEPTED] * 2]
        assert not bad_rounds, f"모든 주문이 접수돼야 함(데드락 희생자는 500). 실패 라운드: {bad_rounds}"

        confirmed = Order.objects.filter(status="confirmed").count()
        pending = Order.objects.filter(status="pending").count()
        assert confirmed == expected_sold, f"주문 {expected_sold}건이 확정돼야 함. confirmed={confirmed}, pending={pending}"

        assert product_a.stock == self.INITIAL_STOCK - expected_sold, (
            f"상품 A 재고가 {expected_sold}개 빠져야 함. 실제: {product_a.stock}"
        )
        assert product_b.stock == self.INITIAL_STOCK - expected_sold, (
            f"상품 B 재고가 {expected_sold}개 빠져야 함. 실제: {product_b.stock}"
        )
