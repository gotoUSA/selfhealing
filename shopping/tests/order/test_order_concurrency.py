import threading
import time
from decimal import Decimal

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


def login_and_get_token(username, password=TestConstants.DEFAULT_PASSWORD):
    """
    로그인하여 JWT 토큰 발급

    멀티스레딩 환경에서 Django가 자동으로 스레드별 DB 연결을 관리합니다.
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

    # 토큰 추출 (다양한 응답 구조 지원)
    data = response.json()
    token = data.get("access") or data.get("token", {}).get("access")
    return client, token, None


# create_test_user 헬퍼 함수는 UserFactory로 대체되었습니다.
# 이제 UserFactory(username=..., email=..., phone_number=..., points=..., is_email_verified=...)를 사용하세요.


def wait_for_order_completion(order_id, max_wait_seconds=5, polling_interval=0.1):
    """Poll order status until completion or timeout."""
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        try:
            order = Order.objects.get(id=order_id)
            if order.status in ["confirmed", "failed", "cancelled"]:
                return order
            time.sleep(polling_interval)
        except Order.DoesNotExist:
            time.sleep(polling_interval)
    return Order.objects.filter(id=order_id).first()


def verify_async_order_result(response, expected_success=True):
    """Verify async order creation result. Returns: (success, order, message)"""
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
            msg = f"Order {order.id} status: {order.status}"
        else:
            success = order.status in ["failed", "cancelled"]
            msg = f"Order {order.id} failed as expected: {order.status}"
        return success, order, msg
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        return False, None, f"400 Bad Request: {response.data}"
    else:
        return False, None, f"Unexpected status: {response.status_code}"


@pytest.mark.django_db(transaction=True)
class TestOrderConcurrencyHappyPath:
    """충분한 재고와 포인트가 있는 상황에서의 동시 주문 테스트"""

    def test_concurrent_order_creation_sufficient_stock(self, authenticated_client, user, product, shipping_data):
        """여러 사용자가 동시 주문 생성 - 충분한 재고"""
        # Arrange
        product.stock = 100
        product.save()

        users = []
        for i in range(5):
            u = UserFactory(
                username=f"user{i}",
                email=f"user{i}@test.com",
                phone_number=f"010-0000-000{i}",
                is_email_verified=True,
            )
            users.append(u)

            # 각 사용자 장바구니에 상품 추가
            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=2)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성 함수"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
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

        # Act - 5명이 동시 주문
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 성공해야 함
        success_count = sum(1 for r in results if r.get("success", False))

        # 디버깅: 실패 시 결과 출력
        if success_count != 5:
            print(f"\n결과: {results}")

        assert success_count == 5, f"5명 모두 성공해야 함. 성공: {success_count}, 결과: {results}"

        # 재고 확인 (100 - 5명 x 2개 = 90개)
        product.refresh_from_db()
        assert product.stock == 90, f"재고가 10개 차감되어야 함 (5명 x 2개). 실제 재고: {product.stock}"

    def test_concurrent_order_with_points_sufficient_balance(self, user, product, shipping_data):
        """여러 사용자가 포인트 사용하며 동시 주문"""
        # Arrange
        product.stock = 50
        product.price = Decimal("10000")
        product.save()

        users = []
        for i in range(3):
            u = UserFactory(
                username=f"pointuser{i}",
                email=f"pointuser{i}@test.com",
                phone_number=f"010-1111-000{i}",
                points=5000,
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order_with_points(user_obj):
            """포인트 사용하여 주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                order_data = {**shipping_data, "use_points": 1000}
                response = client.post("/api/orders/", order_data, format="json")

                # 비동기 응답 처리
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

        # Act
        threads = [threading.Thread(target=create_order_with_points, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 3:
            print(f"\n결과: {results}")

        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}, 결과: {results}"

        # 재고 확인 (50 - 3명 x 1개 = 47개)
        product.refresh_from_db()
        assert product.stock == 47, f"재고가 3개 차감되어야 함. 실제: {product.stock}"
        # 각 사용자의 포인트 차감 확인
        for u in users:
            u.refresh_from_db()
            assert u.points == 4000, f"{u.username}의 포인트가 1000P 차감되어야 함"


@pytest.mark.django_db(transaction=True)
class TestOrderConcurrencyBoundary:
    """재고나 포인트가 딱 맞는 경계 상황에서의 동시성 테스트"""

    def test_concurrent_order_stock_exact_boundary(self, product, shipping_data):
        """재고 10개에 10명이 각 1개씩 동시 주문"""
        # Arrange
        product.stock = 10
        product.save()

        users = []
        for i in range(10):
            u = UserFactory(
                username=f"boundary_user{i}",
                email=f"boundary{i}@test.com",
                phone_number=f"010-2222-000{i}",
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
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

        # Act
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 성공해야 함
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 10:
            print(f"\n결과: {results}")

        assert success_count == 10, f"10명 모두 성공해야 함. 성공: {success_count}"

    def test_concurrent_order_stock_boundary_one_fails(self, product, shipping_data):
        """재고 5개에 3명이 각 2개씩 동시 주문 - 2명만 성공"""
        # Arrange
        product.stock = 5
        product.save()

        users = []
        for i in range(3):
            u = UserFactory(
                username=f"boundary_fail{i}",
                email=f"boundaryfail{i}@test.com",
                phone_number=f"010-3333-000{i}",
                points=5000,
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=2)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리 (일부는 재고 부족으로 실패 가능)
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

        # Act
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 2명만 성공 (5개 재고 - 2개 - 2개 = 1개 남음, 세 번째는 재고 부족)
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = len(results) - success_count  # 비동기 처리에서는 success 플래그로 판단

        if success_count != 2:
            print(f"\n결과: {results}")

        assert (
            success_count == 2
        ), f"2명만 성공해야 함 (재고 5개 -> 2개 주문 -> 2개 주문 -> 실패 성공: {success_count}, 실패: {failed_count})"
        assert failed_count == 1, f"1명은 재고 부족으로 실패해야 함. 실패: {failed_count}"

        # 재고 확인 (5 - 2 - 2 = 1개 남음)
        product.refresh_from_db()
        assert product.stock == 1, f"최종 재고는 1개여야 함. 실제: {product.stock}"

    def test_concurrent_point_usage_boundary(self, product, shipping_data):
        """포인트 딱 맞게 사용하는 동시 주문"""
        # Arrange
        product.stock = 50
        product.price = Decimal("10000")
        product.save()

        users = []
        for i in range(2):
            u = UserFactory(
                username=f"point_boundary{i}",
                email=f"pointbound{i}@test.com",
                phone_number=f"010-4444-000{i}",
                points=1000,
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order_exact_points(user_obj):
            """보유 포인트 전액 사용"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                order_data = {**shipping_data, "use_points": 1000}
                response = client.post("/api/orders/", order_data, format="json")

                # 비동기 응답 처리
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

        # Act
        threads = [threading.Thread(target=create_order_exact_points, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 성공
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 2:
            print(f"\n결과: {results}")

        assert success_count == 2, f"2명 모두 성공해야 함. 성공: {success_count}"

        # 포인트 확인
        for u in users:
            u.refresh_from_db()
            assert u.points == 0, f"{u.username}의 포인트가 0이 되어야 함"


@pytest.mark.django_db(transaction=True)
class TestOrderConcurrencyException:
    """
    3단계: 예외 케이스 (Exception)

    재고 부족, 포인트 부족 등 실패가 예상되는 동시성 시나리오
    """

    def test_concurrent_order_insufficient_stock(self, product, shipping_data):
        """재고 1개에 여러 명 동시 주문 - 1명만 성공"""
        # Arrange
        product.stock = 1  # 재고 1개만
        product.save()

        users = []
        for i in range(5):
            u = UserFactory(
                username=f"insuf_stock{i}",
                email=f"insufstock{i}@test.com",
                phone_number=f"010-5555-000{i}",
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리 (대부분 재고 부족으로 실패 예상)
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

        # Act
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 1명만 성공 (재고 1개, 5명이 각 1개씩 주문)
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = len(results) - success_count

        if success_count != 1:
            print(f"\n결과: {results}")

        assert success_count == 1, f"1명만 성공해야 함 (재고 1개). 성공: {success_count}, 실패: {failed_count}"
        assert failed_count == 4, f"4명은 재고 부족으로 실패해야 함. 실패: {failed_count}"

        # 재고 확인 (1 - 1 = 0개)
        product.refresh_from_db()
        assert product.stock == 0, f"최종 재고는 0개여야 함. 실제: {product.stock}"

    def test_concurrent_order_insufficient_points(self, product, shipping_data):
        """보유 포인트 부족 시 동시 주문"""
        # Arrange
        product.stock = 50
        product.price = Decimal("10000")
        product.save()

        users = []
        for i in range(3):
            u = UserFactory(
                username=f"insuf_point{i}",
                email=f"insufpoint{i}@test.com",
                phone_number=f"010-6666-000{i}",
                points=500,  # 부족한 포인트
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order_with_insufficient_points(user_obj):
            """부족한 포인트로 주문 시도"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                order_data = {**shipping_data, "use_points": 1000}
                response = client.post("/api/orders/", order_data, format="json")

                # 비동기 응답 처리 (포인트 부족으로 실패 예상)
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

        # Act
        threads = [threading.Thread(target=create_order_with_insufficient_points, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 실패
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = len(results) - success_count  # 비동기 처리에서는 success 플래그로 판단

        if success_count != 0:
            print(f"\n결과: {results}")

        assert success_count == 0, f"포인트 부족으로 모두 실패해야 함. 성공: {success_count}"
        assert failed_count == 3, f"3명 모두 실패해야 함. 실패: {failed_count}"

    def test_concurrent_order_cancel(self, product, shipping_data):
        """동일 주문을 여러 번 동시 취소 시도"""
        # Arrange - 사용자 생성 및 주문 생성
        user = UserFactory(
            username="cancel_test_user",
            email="cancel_test@test.com",
            phone_number="010-7000-0001",
            is_email_verified=True,
        )
        cart = Cart.get_or_create_active_cart(user)[0]
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        client, token, error = login_and_get_token(user.username)

        if error:
            pytest.skip(f"로그인 실패: {error}")

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.post("/api/orders/", shipping_data, format="json")

        if response.status_code != status.HTTP_202_ACCEPTED:
            pytest.skip(f"주문 생성 실패: {response.status_code}")

        response_data = response.json()
        # 응답에서 id 추출 (다양한 키 지원)
        order_id = response_data.get("id") or response_data.get("order_id") or response_data.get("pk")

        if not order_id:
            pytest.skip(f"주문 응답에 id가 없음. 응답 키: {list(response_data.keys())}")

        # 주문 완료 대기 (취소하려면 먼저 confirmed 상태가 되어야 함)
        order = wait_for_order_completion(order_id, max_wait_seconds=5)
        if not order or order.status != "confirmed":
            pytest.skip(f"주문이 confirmed 상태가 되지 않음: {order.status if order else 'None'}")

        results = []
        lock = threading.Lock()

        def cancel_order():
            """주문 취소"""
            try:
                cancel_client = APIClient()
                cancel_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = cancel_client.post(f"/api/orders/{order_id}/cancel/")

                with lock:
                    results.append(
                        {
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 5번 동시 취소 시도
        threads = [threading.Thread(target=cancel_order) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 1번만 성공, 나머지는 실패
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = sum(1 for r in results if r.get("status") == status.HTTP_400_BAD_REQUEST)

        if success_count != 1:
            print(f"\n결과: {results}")

        assert success_count == 1, f"1번만 취소 성공해야 함. 성공: {success_count}"
        assert failed_count == 4, f"4번은 이미 취소되어 실패해야 함. 실패: {failed_count}"

    def test_concurrent_cart_to_order_multiple_times(self, product, shipping_data):
        """동일 장바구니로 여러 번 동시 주문 시도"""
        # Arrange
        product.stock = 50
        product.save()

        user = UserFactory(
            username="cart_multi_order_user",
            email="cart_multi@test.com",
            phone_number="010-7000-0002",
            is_email_verified=True,
        )
        cart = Cart.get_or_create_active_cart(user)[0]
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        client, token, error = login_and_get_token(user.username)

        if error:
            pytest.skip(f"로그인 실패: {error}")

        results = []
        lock = threading.Lock()

        def create_order_from_same_cart():
            """같은 장바구니로 주문 생성"""
            try:
                order_client = APIClient()
                order_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = order_client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
                success, order, msg = verify_async_order_result(response, expected_success=True)

                with lock:
                    results.append(
                        {
                            "status": response.status_code,
                            "success": success,
                            "message": msg,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 3번 동시 주문 시도
        threads = [threading.Thread(target=create_order_from_same_cart) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 최소 1개는 성공
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count < 1:
            print(f"\n결과: {results}")

        assert success_count >= 1, f"최소 1개는 성공해야 함. 성공: {success_count}"

    def test_concurrent_same_user_multiple_orders(self, product, shipping_data):
        """동일 사용자가 여러 주문 동시 생성"""
        # Arrange
        product.stock = 100
        product.save()

        user = UserFactory(
            username="same_user_multi_order",
            email="same_user_multi@test.com",
            phone_number="010-7000-0003",
            is_email_verified=True,
        )
        # 장바구니에 상품 추가
        cart = Cart.get_or_create_active_cart(user)[0]
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        client, token, error = login_and_get_token(user.username)

        if error:
            pytest.skip(f"로그인 실패: {error}")

        results = []
        lock = threading.Lock()

        def create_order():
            """주문 생성"""
            try:
                order_client = APIClient()
                order_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = order_client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
                success, order, msg = verify_async_order_result(response, expected_success=True)

                with lock:
                    results.append(
                        {
                            "status": response.status_code,
                            "success": success,
                            "message": msg,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 5번 동시 주문
        threads = [threading.Thread(target=create_order) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count < 1:
            print(f"\n결과: {results}")

        assert success_count >= 1, f"최소 1개는 성공해야 함. 성공: {success_count}"


@pytest.mark.django_db(transaction=True)
class TestOrderConcurrencyAdvanced:
    """
    고급 시나리오

    F() 객체, select_for_update, 대량 동시 요청 등 고급 동시성 제어 테스트
    """

    def test_f_object_stock_decrease_concurrency(self, category):
        """F() 객체를 사용한 재고 차감 동시성 제어"""
        # Arrange - 재고 10개인 상품
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
            """F() 객체로 재고 차감"""
            try:
                # F() 객체로 안전한 차감 (재고 2개 이상일 때만)
                updated = Product.objects.filter(id=product.id, stock__gte=2).update(stock=F("stock") - 2)
                with lock:
                    results.append(
                        {
                            "success": updated > 0,
                            "updated_count": updated,
                            "thread": threading.current_thread().name,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e), "thread": threading.current_thread().name})

        # Act - 6개 스레드가 각 2개씩 차감 시도 (총 12개 시도, 재고 10개)
        threads = [threading.Thread(target=decrease_stock_with_f, name=f"Thread-{i}") for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        if success_count != 5:
            print(f"\n결과: {results}")

        # 5개 스레드 성공 (10 - 2 - 2 - 2 - 2 - 2 = 0)
        assert success_count == 5, f"5개 스레드 성공해야 함. 성공: {success_count}"
        assert product.stock == 0, f"최종 재고는 0개여야 함. 실제: {product.stock}"

    def test_select_for_update_order_creation(self, product, shipping_data):
        """select_for_update로 주문 생성 동시성 제어"""
        # Arrange
        product.stock = 3
        product.save()

        users = []
        for i in range(3):
            u = UserFactory(
                username=f"sfu_user{i}",
                email=f"sfu{i}@test.com",
                phone_number=f"010-7777-000{i}",
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order_with_lock(user_obj):
            """주문 생성 (내부적으로 select_for_update 사용)"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
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

        # Act
        threads = [threading.Thread(target=create_order_with_lock, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - select_for_update로 순차 처리되어 모두 성공
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 3:
            print(f"\n결과: {results}")

        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}"

        # 재고 확인 (3 - 3명 x 1개 = 0개)
        product.refresh_from_db()
        assert product.stock == 0, f"재고는 모두 차감되어야 함. 실제 재고: {product.stock}"

    def test_high_concurrency_order_creation(self, product, shipping_data):
        """대량 동시 주문 요청 (50명)"""
        # Arrange
        product.stock = 100
        product.save()

        users = []
        for i in range(50):
            u = UserFactory(
                username=f"mass_user{i}",
                email=f"mass{i}@test.com",
                phone_number=f"010-8888-{i:04d}",
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
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

        # Act - 50명 동시 주문
        start_time = time.time()
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed_time = time.time() - start_time

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 50:
            print(f"\n결과 샘플: {results[:5]}")  # 처음 5개만 출력

        assert success_count == 50, f"50명 모두 성공해야 함. 성공: {success_count}"
        assert elapsed_time < 30, f"30초 내에 완료되어야 함. 실제: {elapsed_time:.2f}초"

    def test_race_condition_stock_and_point(self, product, shipping_data):
        """재고와 포인트 동시 경합 상황"""
        # Arrange
        product.stock = 2  # 재고 2개
        product.price = Decimal("10000")
        product.save()

        users = []
        for i in range(3):
            u = UserFactory(
                username=f"race_user{i}",
                email=f"race{i}@test.com",
                phone_number=f"010-9999-000{i}",
                points=5000,
                is_email_verified=True,
            )
            users.append(u)

            cart = Cart.get_or_create_active_cart(u)[0]
            CartItem.objects.create(cart=cart, product=product, quantity=1)

        results = []
        lock = threading.Lock()

        def create_order_with_points(user_obj):
            """포인트 사용하여 주문"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                order_data = {**shipping_data, "use_points": 1000}
                response = client.post("/api/orders/", order_data, format="json")

                # 비동기 응답 처리 (일부는 재고 부족으로 실패 가능)
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

        # Act
        threads = [threading.Thread(target=create_order_with_points, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 재고 2개이므로 2명만 성공
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = len(results) - success_count

        if success_count != 2:
            print(f"\n결과: {results}")

        assert success_count == 2, f"2명만 성공해야 함 (재고 2개). 성공: {success_count}, 실패: {failed_count}"
        assert failed_count == 1, f"1명은 재고 부족으로 실패해야 함. 실패: {failed_count}"

        # 재고 확인 (2 - 2명 x 1개 = 0개)
        product.refresh_from_db()
        assert product.stock == 0, f"재고가 모두 차감되어야 함. 실재 재고: {product.stock}"

        # 성공한 사용자는 포인트 차감 확인
        success_users = [r["user"] for r in results if r.get("success", False)]
        for username in success_users:
            u = User.objects.get(username=username)
            assert u.points == 4000, f"{u.username}의 포인트가 1000P 차감되어야 함"


@pytest.mark.django_db(transaction=True)
class TestOrderConcurrencyScaleValidation:
    """
    스케일 검증

    중규모 동시 주문 요청으로 시스템 확장성 검증
    """

    @pytest.mark.slow
    @pytest.mark.parametrize("user_count", [10, 20])
    def test_concurrent_order_creation_scale(self, product, shipping_data, user_count):
        """중규모 동시 주문 생성 - 스케일 검증

        Args:
            user_count: 동시 주문할 사용자 수 (10 or 20)

        Scenario:
            - 재고: user_count * 2개 (충분한 재고)
            - 사용자: user_count명이 각 1개씩 동시 주문
            - 예상: user_count개 주문 모두 성공, 재고 user_count개 남음

        Note:
            500-1000명 스케일은 DB 커넥션 풀 한계로 인해 Locust로 테스트합니다.
            pytest는 로직 검증 목적으로 10-20명 규모를 사용합니다.
        """
        # Arrange - 충분한 재고 설정
        product.stock = user_count * 2
        product.save()

        # 사용자 생성
        users = []
        for i in range(user_count):
            user = UserFactory(
                username=f"scale_user_{user_count}_{i}",
                email=f"scale{user_count}_{i}@test.com",
                phone_number=f"010-9000-{i:04d}",
                is_email_verified=True,
            )
            users.append(user)

        # 장바구니 아이템 벌크 생성
        cart_items = []
        for user in users:
            cart, _ = Cart.get_or_create_active_cart(user)
            cart_items.append(CartItem(cart=cart, product=product, quantity=1))

        CartItem.objects.bulk_create(cart_items)

        results = []
        lock = threading.Lock()

        def create_order(user_obj):
            """주문 생성"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                response = client.post("/api/orders/", shipping_data, format="json")

                # 비동기 응답 처리
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

        # Act - 동시 주문
        start_time = time.time()
        threads = [threading.Thread(target=create_order, args=(u,)) for u in users]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        elapsed_time = time.time() - start_time

        # Assert - 모두 성공
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = len(results) - success_count

        if success_count != user_count:
            # 디버깅: 실패 시 샘플만 출력 (처음 5개)
            failed_samples = [r for r in results if not r.get("success", False)][:5]
            print(f"\n실패 샘플: {failed_samples}")

        assert success_count == user_count, f"{user_count}명 모두 성공해야 함. 성공: {success_count}, 실패: {failed_count}"

        # 재고 확인 (초기 재고 - user_count개 주문 = user_count개 남음)
        product.refresh_from_db()
        expected_stock = user_count
        assert product.stock == expected_stock, f"재고가 {expected_stock}개 남아야 함. 실제: {product.stock}"

        # 성능 확인 (적절한 시간 내 완료)
        max_time = 120  # 2분
        assert elapsed_time < max_time, f"{max_time}초 내 완료되어야 함. 실제: {elapsed_time:.2f}초"
