"""결제 동시성 테스트"""

import random
import threading
import time
from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.product import Product
from shopping.models.user import User
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
)


def login_and_get_token(username, password="testpass123"):
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


@pytest.mark.django_db(transaction=True)
class TestPaymentConcurrencyHappyPath:
    """정상 케이스 - 충분한 재고와 포인트가 있는 상황"""

    def test_concurrent_payment_confirm_multiple_users(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """여러 사용자가 서로 다른 주문을 동시 결제 승인"""
        # Arrange
        product.stock = 100
        product.save()

        users = []
        orders = []
        payments = []

        for i in range(5):
            # 사용자 생성
            user = user_factory(username=f"payment_user{i}")
            users.append(user)

            # 주문 생성
            order = create_order(user=user, product=product, status="pending")
            orders.append(order)

            # Payment 생성
            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)

                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "user": user_obj.username,
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_202_ACCEPTED,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"user": user_obj.username, "error": str(e)})

        # Act - 5명이 동시 결제 승인
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        if success_count != 5:
            print(f"\n결과: {results}")

        assert success_count == 5, f"5명 모두 성공해야 함. 성공: {success_count}"

        # 재고 확인 (테스트에서는 Order 생성 시 재고 차감 없이 직접 생성)
        product.refresh_from_db()
        assert product.stock == 100, f"재고는 변경되지 않음 (Order 직접 생성). 실제: {product.stock}"

        # sold_count 증가 확인 (결제 confirm 시 증가)
        assert product.sold_count == 5, f"sold_count는 5 증가해야 함. 실제: {product.sold_count}"

    def test_concurrent_payment_with_stock_deduction(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """충분한 재고에서 동시 결제 승인 (재고 차감 검증)"""
        # Arrange
        initial_stock = 50
        product.stock = initial_stock
        product.save()

        users = []
        payments = []
        quantity_per_order = 2

        for i in range(3):
            user = user_factory(username=f"stock_user{i}")
            users.append(user)

            order = create_order(user=user, product=product, quantity=quantity_per_order, status="pending")

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}"

        # sold_count 증가 (3명 x 2개 = 6개)
        product.refresh_from_db()
        assert product.sold_count == 6, f"sold_count 6 증가. 실제: {product.sold_count}"

    def test_concurrent_payment_point_earn(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """포인트 적립 동시성 (여러 결제가 동시에 완료되어 포인트 적립)"""
        # Arrange
        users = []
        payments = []

        for i in range(3):
            user = user_factory(username=f"point_earn_user{i}", points=0)
            users.append(user)

            order = create_order(user=user, product=product, status="pending")

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3

        # 포인트 적립 확인 (10000원의 1% = 100P)
        for user in users:
            user.refresh_from_db()
            assert user.points == 100, f"{user.username}의 포인트 100P 적립. 실제: {user.points}"

    def test_concurrent_payment_request_retry(self, product, user_factory, create_order):
        """동일 주문에 대한 결제 요청 재시도 (기존 Payment 삭제/재생성)"""
        # Arrange
        user = user_factory(username="retry_user")

        order = create_order(user=user, product=product, status="confirmed")

        # 기존 Payment 생성
        old_payment = PaymentFactory(order=order)
        old_payment_id = old_payment.id

        results = []
        lock = threading.Lock()

        def request_payment():
            """결제 요청"""
            try:
                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {"order_id": order.id}
                response = client.post("/api/payments/request/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "success": response.status_code == status.HTTP_201_CREATED,
                            "payment_id": response.json().get("payment_id") if response.status_code == status.HTTP_201_CREATED else None,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 동일 주문에 대해 3번 동시 결제 요청
        threads = [threading.Thread(target=request_payment) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        # 최소 1개는 성공해야 함
        assert success_count >= 1, f"최소 1개 성공. 성공: {success_count}"

        # 최종적으로 Payment는 1개만 존재해야 함 (마지막 요청이 이전 것을 삭제)
        assert (
            Payment.objects.filter(order=order).count() == 1
        ), f"Payment는 1개만 존재해야 함. 실제: {Payment.objects.filter(order=order).count()}"

        # 새로 생성된 Payment ID 확인 (성공한 요청 중 하나)
        successful_payment_ids = [r["payment_id"] for r in results if r.get("success") and r.get("payment_id")]
        if successful_payment_ids:
            # 최소 하나의 새로운 Payment가 생성되었음
            final_payment = Payment.objects.get(order=order)
            assert (
                final_payment.id in successful_payment_ids
            ), f"최종 Payment ID가 성공한 요청 중 하나여야 함. final={final_payment.id}, successful={successful_payment_ids}"

    def test_concurrent_payment_with_points_usage(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """여러 사용자가 포인트 사용하며 동시 결제"""
        # Arrange
        product.price = Decimal("10000")
        product.save()

        users = []
        payments = []

        for i in range(3):
            user = user_factory(username=f"point_use_user{i}", points=5000)
            users.append(user)

            # 포인트 차감 (FIFO 방식)
            point_service = PointService()
            result = point_service.use_points_fifo(user=user, amount=1000)
            assert result["success"]

            order = create_order(user=user, product=product, status="pending", used_points=1000)

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3

        # 포인트 확인 (5000 - 1000(사용) + 100(적립, 상품금액 10000원의 1%))
        # 적립은 total_amount(순수 상품금액) 기준, 포인트 사용과 무관
        for user in users:
            user.refresh_from_db()
            expected_points = 4100
            assert user.points == expected_points, f"포인트 {expected_points}. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
class TestPaymentConcurrencyBoundary:
    """경계값 테스트 - 재고나 포인트가 딱 맞는 경계 상황"""

    def test_concurrent_payment_exact_stock_boundary(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """재고 딱 맞는 상황에서 동시 결제 (10개 재고, 10명 동시 결제)"""
        # Arrange
        product.stock = 10
        product.save()

        users = []
        payments = []

        for i in range(10):
            user = user_factory(username=f"exact_stock{i}")
            users.append(user)

            order = create_order(user=user, product=product, status="pending")

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 10, f"10명 모두 성공. 성공: {success_count}"

    def test_concurrent_duplicate_payment_request(self, product, user_factory, create_order):
        """동일 주문 중복 결제 요청 (동시 호출)"""
        # Arrange
        user = user_factory(
            username="dup_req_user",
            email="dupreq@test.com",
            phone_number="010-7000-0001",
        )

        order = create_order(user=user, product=product, status="confirmed")

        results = []
        lock = threading.Lock()

        def request_payment():
            """결제 요청"""
            try:
                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {"order_id": order.id}
                response = client.post("/api/payments/request/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_201_CREATED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 5번 동시 요청
        threads = [threading.Thread(target=request_payment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 성공 (마지막 것이 기존 것을 덮어씀)
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count >= 1, f"최소 1개 성공. 성공: {success_count}"

        # Payment는 1개만 존재
        assert Payment.objects.filter(order=order).count() == 1

    def test_concurrent_full_point_payment(self, product, user_factory, create_order, toss_response_builder, mocker):
        """포인트 전액 사용 동시 결제"""
        # Arrange
        users = []
        payments = []

        for i in range(2):
            user = user_factory(
                username=f"full_point{i}",
                email=f"fullpoint{i}@test.com",
                phone_number=f"010-8000-000{i}",
                points=10000,
            )
            users.append(user)

            # 포인트 전액 차감 (FIFO 방식)
            point_service = PointService()
            result = point_service.use_points_fifo(user=user, amount=int(product.price))
            assert result["success"]

            order = create_order(user=user, product=product, status="pending", used_points=int(product.price))

            payment = PaymentFactory(order=order, amount=Decimal("0"))
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(amount=0),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {
                    "order_id": payment_obj.order.id,
                    "payment_key": f"test_key_{payment_obj.id}",
                    "amount": 0,
                }
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 2

        # 포인트 차감 확인 (적립 없음)
        for user in users:
            user.refresh_from_db()
            assert user.points == 0, f"포인트 0P. 실제: {user.points}"

    def test_concurrent_stock_boundary_partial_success(
        self, product, user_factory, create_order, build_confirm_request, mocker
    ):
        """재고 경계값 (5개 재고, 3명이 2개씩 주문 → 2명만 성공)"""
        # Arrange
        product.stock = 5
        product.save()

        users = []
        payments = []

        for i in range(3):
            user = user_factory(
                username=f"partial_user{i}",
                email=f"partial{i}@test.com",
                phone_number=f"010-9000-000{i}",
            )
            users.append(user)

            order = create_order(user=user, product=product, quantity=2, status="pending")

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - 일부만 성공하도록 (thread-safe)
        call_count = [0]
        count_lock = threading.Lock()

        def mock_confirm(*args, **kwargs):
            with count_lock:
                call_count[0] += 1
                current_count = call_count[0]

            if current_count <= 2:
                # Factory가 생성한 고유 payment_key 사용
                payment_key = kwargs.get("payment_key", f"fallback_key_{current_count}")
                order_id = kwargs.get("order_id", f"ORDER_{current_count}")
                return TossResponseBuilder.success_response(
                    payment_key=payment_key,
                    order_id=order_id,
                )
            else:
                from shopping.utils.toss_payment import TossPaymentError

                raise TossPaymentError("SOLD_OUT", "재고 부족")

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=mock_confirm,
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error, "payment_id": payment_obj.id})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "accepted": response.status_code == status.HTTP_202_ACCEPTED,
                            "status": response.status_code,
                            "payment_id": payment_obj.id,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e), "payment_id": payment_obj.id})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 비동기 태스크 완료 대기
        time.sleep(0.5)

        # Assert - HTTP 응답 대신 최종 결제 상태 검증
        # 2명 성공 (payment.status == 'done'), 1명 실패 (payment.status == 'aborted')
        success_count = 0
        failed_count = 0

        for payment in payments:
            payment.refresh_from_db()
            if payment.status == "done":
                success_count += 1
            elif payment.status == "aborted":
                failed_count += 1

        assert success_count == 2, f"2명 성공. 성공: {success_count}, 실패: {failed_count}"
        assert failed_count == 1, f"1명 실패. 성공: {success_count}, 실패: {failed_count}"


@pytest.mark.django_db(transaction=True)
class TestPaymentConcurrencyException:
    """예외 케이스 - 재고 부족, 중복 처리 등"""

    def test_concurrent_insufficient_stock(self, product, user_factory, create_order, build_confirm_request, mocker):
        """재고 부족 시 동시 결제 (1개 재고, 5명 시도 → 1명만 성공)"""
        # Arrange
        product.stock = 1
        product.save()

        users = []
        payments = []

        for i in range(5):
            user = user_factory(
                username=f"insuf_user{i}",
                email=f"insuf{i}@test.com",
                phone_number=f"010-1100-000{i}",
            )
            users.append(user)

            order = create_order(user=user, product=product, status="pending")

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Mock - 첫 번째만 성공 (thread-safe)
        call_count = [0]
        count_lock = threading.Lock()

        def mock_confirm(*args, **kwargs):
            with count_lock:
                call_count[0] += 1
                current_count = call_count[0]

            if current_count == 1:
                # Factory가 생성한 고유 payment_key 사용
                payment_key = kwargs.get("payment_key", f"fallback_key_{current_count}")
                order_id = kwargs.get("order_id", f"ORDER_{current_count}")
                return TossResponseBuilder.success_response(
                    payment_key=payment_key,
                    order_id=order_id,
                )
            else:
                from shopping.utils.toss_payment import TossPaymentError

                raise TossPaymentError("SOLD_OUT", "재고 부족")

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=mock_confirm,
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 비동기 태스크 완료 대기
        time.sleep(0.5)

        # Assert - HTTP 응답 대신 최종 결제 상태 검증
        # 1명 성공 (payment.status == 'done'), 4명 실패 (payment.status == 'aborted')
        success_count = 0
        failed_count = 0

        for payment in payments:
            payment.refresh_from_db()
            if payment.status == "done":
                success_count += 1
            elif payment.status == "aborted":
                failed_count += 1

        assert success_count == 1, f"1명만 성공. 성공: {success_count}, 실패: {failed_count}"
        assert failed_count == 4, f"4명 실패. 성공: {success_count}, 실패: {failed_count}"

    def test_concurrent_duplicate_payment_confirm(self, product, user_factory, create_order, mocker):
        """동일 결제 중복 승인 시도 (1개만 성공, 나머지 실패)"""
        # Arrange
        user = user_factory(
            username="dup_confirm_user",
            email="dupconfirm@test.com",
            phone_number="010-1200-0001",
        )

        order = create_order(user=user, product=product, status="pending")

        payment = PaymentFactory(order=order)

        # 중복 승인 테스트 - 모든 호출이 같은 응답을 받아야 함
        toss_response = TossResponseBuilder.success_response(payment_key="test_duplicate_key")
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        results = []
        lock = threading.Lock()

        def confirm_payment():
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(payment.amount),
                }
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "success": response.status_code == status.HTTP_202_ACCEPTED,
                            "status": response.status_code,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 동일 결제를 5번 동시 승인 시도
        threads = [threading.Thread(target=confirm_payment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 1번만 성공
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 1, f"1번만 성공. 성공: {success_count}"

        # Payment는 done 상태
        payment.refresh_from_db()
        assert payment.status == "done"

    def test_concurrent_duplicate_payment_cancel(self, product, user_factory, create_order, mocker):
        """동일 결제를 여러 번 동시 취소 시도 (1개만 성공)"""
        # Arrange
        user = user_factory(
            username="dup_cancel_user",
            email="dupcancel@test.com",
            phone_number="010-1300-0001",
        )

        order = create_order(user=user, product=product, status="paid", payment_method="card")

        from django.utils import timezone

        payment = PaymentFactory(
            order=order,
            status="done",
            payment_key="test_cancel_key",
            approved_at=timezone.now(),
        )

        # 중복 취소 테스트 - 모든 호출이 같은 응답을 받아야 함
        toss_response = TossResponseBuilder.cancel_response(payment_key="test_cancel_key")
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_response,
        )

        results = []
        lock = threading.Lock()

        def cancel_payment():
            """결제 취소"""
            try:
                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {
                    "payment_id": payment.id,
                    "cancel_reason": "동시 취소 테스트",
                }
                response = client.post("/api/payments/cancel/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "success": response.status_code == status.HTTP_200_OK,
                            "status": response.status_code,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 동일 결제를 5번 동시 취소 시도
        threads = [threading.Thread(target=cancel_payment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 1번만 성공
        success_count = sum(1 for r in results if r.get("success", False))
        failed_count = sum(1 for r in results if r.get("status") == status.HTTP_400_BAD_REQUEST)

        assert success_count == 1, f"1번만 성공. 성공: {success_count}"
        assert failed_count >= 1, f"나머지는 실패. 실패: {failed_count}"

        # Payment는 canceled 상태
        payment.refresh_from_db()
        assert payment.status == "canceled"

    def test_concurrent_webhook_and_confirm(self, product, user_factory, create_order, mocker):
        """웹훅과 confirm API 동시 호출 (race condition)"""
        # Arrange
        user = user_factory(
            username="webhook_user",
            email="webhook@test.com",
            phone_number="010-1400-0001",
        )

        order = create_order(user=user, product=product, status="pending")

        payment = PaymentFactory(order=order)

        toss_response = TossResponseBuilder.success_response(
            payment_key="test_webhook_key", order_id=order.id, amount=int(payment.amount)
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        # 웹훅 서명 검증 우회
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.verify_webhook",
            return_value=True,
        )

        results = []
        lock = threading.Lock()

        def call_confirm_api():
            """confirm API 호출 (race condition 시뮬레이션)"""
            try:
                # 랜덤 delay로 다양한 호출 순서 테스트
                time.sleep(random.uniform(0.001, 0.01))

                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"type": "confirm", "error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = {
                    "order_id": order.id,
                    "payment_key": "test_webhook_key",
                    "amount": int(payment.amount),
                }
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append(
                        {
                            "type": "confirm",
                            "success": response.status_code == status.HTTP_202_ACCEPTED,
                            "status": response.status_code,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"type": "confirm", "error": str(e)})

        def call_webhook():
            """webhook 호출 (race condition 시뮬레이션)"""
            try:
                # 랜덤 delay로 다양한 호출 순서 테스트
                time.sleep(random.uniform(0.001, 0.01))

                client = APIClient()
                webhook_data = {
                    "eventType": "PAYMENT.DONE",
                    "data": toss_response,
                }
                response = client.post("/api/webhooks/toss/", webhook_data, format="json")

                with lock:
                    results.append(
                        {
                            "type": "webhook",
                            "success": response.status_code == status.HTTP_200_OK,
                            "status": response.status_code,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"type": "webhook", "error": str(e)})

        # Act - confirm과 webhook 동시 호출
        threads = [
            threading.Thread(target=call_confirm_api),
            threading.Thread(target=call_webhook),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 둘 다 성공 (중복 처리 방지 로직이 있어야 함)
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count >= 1, f"최소 1개 성공. 성공: {success_count}"

        # Payment는 done 상태 (한 번만 처리됨)
        payment.refresh_from_db()
        assert payment.status == "done"
        assert payment.is_paid is True

        # Order는 paid 상태
        order.refresh_from_db()
        assert order.status == "paid"

    def test_concurrent_same_user_multiple_payments(
        self, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """동일 사용자가 여러 주문 동시 결제 시도"""
        # Arrange
        user = user_factory(
            username="multi_pay_user",
            email="multipay@test.com",
            phone_number="010-1500-0001",
        )

        orders = []
        payments = []

        for i in range(3):
            order = create_order(user=user, product=product, status="pending")
            orders.append(order)

            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Toss API Mock - side_effect로 매번 새로운 paymentKey 생성
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        results = []
        lock = threading.Lock()

        def confirm_payment(payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user.username)
                if error:
                    with lock:
                        results.append({"error": error})
                    return

                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")

                with lock:
                    results.append({"success": response.status_code == status.HTTP_202_ACCEPTED})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 동일 사용자가 3개 주문 동시 결제
        threads = [threading.Thread(target=confirm_payment, args=(p,)) for p in payments]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모두 성공
        success_count = sum(1 for r in results if r.get("success", False))
        error_count = sum(1 for r in results if "error" in r)
        assert success_count == 3, f"3개 모두 성공해야 함. 성공: {success_count}, 에러: {error_count}"

        # Payment 상태 검증
        done_payments = 0
        for payment in payments:
            payment.refresh_from_db()
            if payment.status == "done":
                done_payments += 1

        assert done_payments == 3, f"3개 결제 완료. 실제: {done_payments}"

        # Product 통계 검증
        product.refresh_from_db()
        assert product.sold_count == 3, f"sold_count 3 증가. 실제: {product.sold_count}"


@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestPaymentConcurrencyScaleValidation:
    """스케일 검증 - 중규모 동시성

    Note:
        50명 이상 스케일은 DB 커넥션 풀 한계로 인해 Locust로 테스트합니다.
        (shopping/tests/performance/concurrent_payment_locust.py 참조)
        pytest는 로직 검증 목적으로 10-20명 규모를 사용합니다.
    """

    @pytest.mark.parametrize("user_count", [10, 20])
    def test_concurrent_payment_confirm_scale(
        self, user_count, product, user_factory, create_order, toss_response_builder, build_confirm_request, mocker
    ):
        """중규모 동시 결제 승인 - 스케일 검증

        Args:
            user_count: 동시 결제할 사용자 수 (10 or 20)

        시나리오:
            - 재고: user_count * 2개 (충분한 재고)
            - 사용자: user_count명이 각 1개씩 동시 결제
            - 예상: user_count개 결제 성공, 재고 user_count개 남음
        """
        # Arrange
        product.stock = user_count * 2
        product.sold_count = 0
        product.save()
        users = []
        orders = []
        payments = []
        for i in range(user_count):
            user = user_factory(
                username=f"scale_user{i}",
                email=f"scale{i}@test.com",
                phone_number=f"010-{2000 + (i // 10000):04d}-{i % 10000:04d}",
            )
            users.append(user)
            order = create_order(user=user, product=product, status="pending")
            orders.append(order)
            payment = PaymentFactory(order=order)
            payments.append(payment)
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )
        results = []
        lock = threading.Lock()

        def confirm_payment(user_obj, payment_obj):
            """결제 승인"""
            try:
                client, token, error = login_and_get_token(user_obj.username)
                if error:
                    with lock:
                        results.append({"user": user_obj.username, "error": error})
                    return
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                request_data = build_confirm_request(payment_obj)
                response = client.post("/api/payments/confirm/", request_data, format="json")
                with lock:
                    results.append(
                        {
                            "user": user_obj.username,
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_202_ACCEPTED,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"user": user_obj.username, "error": str(e)})

        # Act
        threads = [threading.Thread(target=confirm_payment, args=(users[i], payments[i])) for i in range(user_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(3)
        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        error_count = sum(1 for r in results if "error" in r)
        if success_count != user_count:
            print(f"\n성공: {success_count}, 에러: {error_count}")
        assert success_count == user_count, f"{user_count}명 모두 성공. 성공: {success_count}, 에러: {error_count}"
        done_payments = sum(1 for p in payments if (p.refresh_from_db() or p.status == "done"))
        assert done_payments == user_count
        product.refresh_from_db()
        assert product.sold_count == user_count
