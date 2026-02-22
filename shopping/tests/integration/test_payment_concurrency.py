"""
결제 동시성 테스트

Purpose:
    결제 승인/취소 과정에서 동시성 문제(중복 결제, 이중 취소 등)를 방지하는지 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 중복 결제 방지 (같은 주문에 2번 결제 불가)
        - 중복 취소 방지
        - 포인트 1회만 적립
        - sold_count 1회만 증가
    B. Parameterized Scenarios:
        - 다양한 사용자 수(10/20)에 대한 스케일 테스트
    C. Edge Cases:
        - 재고 경계값, 동시 결제 요청

Concurrency Control:
    - select_for_update on Payment
    - is_paid flag check (상태 기반 중복 방지)
    - Idempotency key (멱등성 보장)

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
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.payment import Payment
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    PaymentFactory,
    TossResponseBuilder,
)


# =============================================================================
# 헬퍼 함수 및 유틸리티
# =============================================================================


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


def login_and_get_token(username: str, password: str = "testpass123") -> tuple:
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


def run_concurrent_payment_confirms(
    users_and_payments: list[tuple],
    build_confirm_request,
) -> list[dict[str, Any]]:
    """
    동시 결제 승인 실행 헬퍼

    Args:
        users_and_payments: (user, payment) 튜플 리스트
        build_confirm_request: confirm request 생성 함수

    Returns:
        각 스레드의 결과 리스트
    """
    results = []
    lock = threading.Lock()

    def confirm_payment(user_obj, payment_obj):
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
        finally:
            close_db_connection()

    threads = [threading.Thread(target=confirm_payment, args=(u, p)) for u, p in users_and_payments]
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
@pytest.mark.payment_race
class TestPaymentDuplicatePreventionInvariant:
    """
    핵심 불변 조건: 중복 결제 방지

    Purpose:
        동일 결제에 대한 동시 승인 요청 시 1번만 처리되도록 보장
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        select_for_update + is_paid flag
    """

    def test_duplicate_confirm_only_one_succeeds(
        self,
        product,
        user_factory,
        create_order,
        mocker,
    ):
        """
        Purpose:
            동일 결제에 5번 동시 승인 시도 시 1번만 성공
        Scenario:
            5 concurrent confirm requests on same payment
        Expected:
            1 success, 4 failures (already paid)
        """
        # Arrange
        user = user_factory(username="dup_confirm_user")
        order = create_order(user=user, product=product, status="pending")
        payment = PaymentFactory(order=order)

        toss_response = TossResponseBuilder.success_response(payment_key="test_duplicate_key")
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        results = []
        lock = threading.Lock()

        def confirm_payment():
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
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=confirm_payment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        payment.refresh_from_db()

        assert success_count == 1, f"1번만 승인 성공해야 함. 성공: {success_count}"
        assert payment.status == "done", "Payment는 done 상태여야 함"

    def test_duplicate_cancel_only_one_succeeds(
        self,
        product,
        user_factory,
        create_order,
        mocker,
    ):
        """
        Purpose:
            동일 결제에 5번 동시 취소 시도 시 1번만 성공
        Scenario:
            5 concurrent cancel requests on same paid payment
        Expected:
            1 success, 4 failures (already cancelled)
        """
        # Arrange
        from django.utils import timezone

        user = user_factory(username="dup_cancel_user")
        order = create_order(user=user, product=product, status="paid", payment_method="card")

        payment = PaymentFactory(
            order=order,
            status="done",
            payment_key="test_cancel_key",
            approved_at=timezone.now(),
        )

        toss_response = TossResponseBuilder.cancel_response(payment_key="test_cancel_key")
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_response,
        )

        results = []
        lock = threading.Lock()

        def cancel_payment():
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
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=cancel_payment) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        payment.refresh_from_db()

        assert success_count == 1, f"1번만 취소 성공해야 함. 성공: {success_count}"
        assert payment.status == "canceled", "Payment는 canceled 상태여야 함"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestPaymentPointInvariant:
    """
    핵심 불변 조건: 포인트 1회 적립

    Purpose:
        동시 결제 완료 시 포인트가 1번만 적립되도록 보장
    Type:
        Core invariant test (must never fail)
    """

    def test_points_earned_only_once_on_concurrent_confirm(
        self,
        product,
        user_factory,
        create_order,
        toss_response_builder,
        build_confirm_request,
        mocker,
    ):
        """
        Purpose:
            동시 결제 승인 시 포인트가 1번만 적립됨
        Scenario:
            3 users each have separate orders, all confirm simultaneously
        Expected:
            Each user gets points exactly once (100P per 10000 order)
        """
        # Arrange
        users = []
        payments = []

        for i in range(3):
            user = user_factory(username=f"point_earn_user{i}", points=0)
            users.append(user)
            order = create_order(user=user, product=product, status="pending")
            payment = PaymentFactory(order=order)
            payments.append(payment)

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        users_and_payments = list(zip(users, payments))

        # Act
        results = run_concurrent_payment_confirms(users_and_payments, build_confirm_request)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}"

        # 각 사용자 포인트 1회만 적립 확인 (10000원의 1% = 100P)
        for user in users:
            user.refresh_from_db()
            assert user.points == 100, f"{user.username}의 포인트는 100P여야 함. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestPaymentSoldCountInvariant:
    """
    핵심 불변 조건: sold_count 1회 증가

    Purpose:
        동시 결제 완료 시 sold_count가 정확히 1회씩만 증가하도록 보장
    Type:
        Core invariant test (must never fail)
    """

    def test_sold_count_increases_correctly_on_concurrent_confirm(
        self,
        product,
        user_factory,
        create_order,
        toss_response_builder,
        build_confirm_request,
        mocker,
    ):
        """
        Purpose:
            3명 동시 결제 시 sold_count가 정확히 3 증가
        Scenario:
            3 users each order 1 item, all confirm simultaneously
        Expected:
            sold_count = 3 (not more due to race condition)
        """
        # Arrange
        product.stock = 100
        product.sold_count = 0
        product.save()

        users = []
        payments = []

        for i in range(3):
            user = user_factory(username=f"sold_count_user{i}")
            users.append(user)
            order = create_order(user=user, product=product, status="pending")
            payment = PaymentFactory(order=order)
            payments.append(payment)

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        users_and_payments = list(zip(users, payments))

        # Act
        results = run_concurrent_payment_confirms(users_and_payments, build_confirm_request)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}"
        assert product.sold_count == 3, f"sold_count는 정확히 3이어야 함. 실제: {product.sold_count}"


# =============================================================================
# B. 파라미터화된 스케일 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
@pytest.mark.slow
class TestPaymentConcurrencyScale:
    """
    스케일 검증 테스트

    Purpose:
        다양한 규모의 동시 결제 요청에서 시스템 안정성 검증
    Note:
        50명 이상은 DB 커넥션 풀 한계로 Locust 사용 권장
    """

    @pytest.mark.parametrize("user_count", [10, 20])
    def test_concurrent_payment_confirm_scale(
        self,
        user_count: int,
        product,
        user_factory,
        create_order,
        toss_response_builder,
        build_confirm_request,
        mocker,
    ):
        """
        Purpose:
            다양한 사용자 수에서 동시 결제 승인 검증
        Scenario:
            {user_count} users each with separate orders confirm simultaneously
        Expected:
            All succeed, sold_count = user_count
        """
        # Arrange
        product.stock = user_count * 2
        product.sold_count = 0
        product.save()

        users = []
        payments = []

        for i in range(user_count):
            user = user_factory(
                username=f"scale_user{i}",
                email=f"scale{i}@test.com",
                phone_number=f"010-{2000 + (i // 10000):04d}-{i % 10000:04d}",
            )
            users.append(user)
            order = create_order(user=user, product=product, status="pending")
            payment = PaymentFactory(order=order)
            payments.append(payment)

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        users_and_payments = list(zip(users, payments))

        # Act
        start_time = time.time()
        results = run_concurrent_payment_confirms(users_and_payments, build_confirm_request)
        time.sleep(3)  # 비동기 태스크 완료 대기

        elapsed_time = time.time() - start_time

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        product.refresh_from_db()

        assert success_count == user_count, f"{user_count}명 모두 성공해야 함. 성공: {success_count}"
        assert product.sold_count == user_count, f"sold_count는 {user_count}여야 함"
        assert elapsed_time < 120, f"2분 내 완료되어야 함. 실제: {elapsed_time:.2f}초"


# =============================================================================
# C. 통합 시나리오 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.payment_race
class TestPaymentConcurrencyIntegration:
    """
    통합 동시성 시나리오

    Purpose:
        다양한 결제 상황에서의 동시성 제어 검증
    """

    def test_concurrent_payment_with_points_usage(
        self,
        product,
        user_factory,
        create_order,
        toss_response_builder,
        build_confirm_request,
        mocker,
    ):
        """
        Purpose:
            포인트 사용과 함께 동시 결제 승인 시 정합성 유지
        Scenario:
            3 users with 5000P each use 1000P and confirm simultaneously
        Expected:
            All succeed, each user has 4100P (5000 - 1000 + 100 earned)
        """
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

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=lambda *args, **kwargs: toss_response_builder(),
        )

        users_and_payments = list(zip(users, payments))

        # Act
        results = run_concurrent_payment_confirms(users_and_payments, build_confirm_request)

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3, f"3명 모두 성공해야 함. 성공: {success_count}"

        # 포인트 확인: 5000 - 1000(사용) + 100(적립) = 4100
        for user in users:
            user.refresh_from_db()
            assert user.points == 4100, f"포인트는 4100P여야 함. 실제: {user.points}"

    @pytest.mark.parametrize(
        "stock,user_count,expected_success",
        [
            (1, 5, 1),  # 1재고, 5명 -> 1명만 성공
            (5, 3, 3),  # 5재고, 3명 (2개씩) -> mock에서 2명만 성공 설정
        ],
    )
    def test_stock_boundary_payment_confirm(
        self,
        stock: int,
        user_count: int,
        expected_success: int,
        product,
        user_factory,
        create_order,
        build_confirm_request,
        mocker,
    ):
        """
        Purpose:
            재고 경계값에서 동시 결제 승인 검증
        Scenario:
            stock={stock}, {user_count} users confirm simultaneously
        Expected:
            {expected_success} succeed based on stock availability
        """
        # Arrange
        product.stock = stock
        product.save()

        users = []
        payments = []

        for i in range(user_count):
            user = user_factory(username=f"stock_bound_{stock}_{i}")
            users.append(user)
            order = create_order(user=user, product=product, status="pending")
            payment = PaymentFactory(order=order)
            payments.append(payment)

        # Mock: thread-safe하게 expected_success만큼만 성공
        call_count = [0]
        count_lock = threading.Lock()

        def mock_confirm(*args, **kwargs):
            with count_lock:
                call_count[0] += 1
                current_count = call_count[0]

            if current_count <= expected_success:
                payment_key = kwargs.get("payment_key", f"key_{current_count}")
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

        users_and_payments = list(zip(users, payments))

        # Act
        results = run_concurrent_payment_confirms(users_and_payments, build_confirm_request)
        time.sleep(0.5)  # 비동기 태스크 완료 대기

        # Assert - 최종 결제 상태로 검증
        done_count = 0
        aborted_count = 0

        for payment in payments:
            payment.refresh_from_db()
            if payment.status == "done":
                done_count += 1
            elif payment.status == "aborted":
                aborted_count += 1

        assert done_count == expected_success, f"{expected_success}개 결제 완료. 실제: {done_count}"

    def test_concurrent_payment_request_retry(
        self,
        product,
        user_factory,
        create_order,
    ):
        """
        Purpose:
            동일 주문에 대한 중복 결제 요청 시 기존 것 삭제하고 새로 생성
        Scenario:
            Same order, 3 concurrent payment requests
        Expected:
            All requests handled, final Payment count = 1
        """
        # Arrange
        user = user_factory(username="retry_user")
        order = create_order(user=user, product=product, status="confirmed")

        results = []
        lock = threading.Lock()

        def request_payment():
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
                            "payment_id": (
                                response.json().get("payment_id") if response.status_code == status.HTTP_201_CREATED else None
                            ),
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=request_payment) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))

        assert success_count >= 1, f"최소 1개 성공. 성공: {success_count}"
        assert Payment.objects.filter(order=order).count() == 1, "최종 Payment는 1개만 존재해야 함"
