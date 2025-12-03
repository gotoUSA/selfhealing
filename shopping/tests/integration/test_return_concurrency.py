"""
환불/교환 동시성 테스트

Purpose:
    동일 Return에 대한 동시 환불/교환 처리 요청 시 데이터 정합성 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 동일 Return 중복 처리 방지
        - 포인트 정합성 (1회만 회수)
        - 재고 정합성 (1회만 복원)
    B. State Transition Tests (상태 전이):
        - approve/reject 동시 요청
        - 수령 확인 동시 요청
    C. Scale Tests:
        - 동일 사용자 다중 환불 동시 처리

Concurrency Control:
    - select_for_update - Return 행 단위 락
    - 상태 체크 + 트랜잭션

주의: 이 테스트는 기본적인 동시성 동작만 검증합니다.
실제 대규모 동시성 테스트는 Locust를 사용하세요.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

import pytest

from shopping.models.point import PointHistory
from shopping.models.return_request import Return
from shopping.services.return_service import ReturnService
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    PointHistoryFactory,
    ProductFactory,
    ReturnFactory,
    ReturnItemFactory,
    UserFactory,
)


# =============================================================================
# 헬퍼 함수
# =============================================================================


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestRefundDuplicatePreventionInvariant:
    """
    핵심 불변 조건: 동일 Return 중복 환불 방지

    Purpose:
        동일 Return에 대한 동시 환불 요청 시 1번만 처리
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        select_for_update + 상태 체크
    """

    def test_concurrent_complete_refund_same_return_only_one_succeeds(self):
        """
        Purpose:
            동일 Return에 대해 2회 동시 환불 요청 시 1회만 성공
        Scenario:
            Return 1건 received 상태, 2개 스레드 동시 complete_refund 호출
        Expected:
            1개 성공, 1개 상태 오류로 실패, 중복 처리 없음
        """
        # Arrange
        product = ProductFactory(stock=10)
        user = UserFactory(points=5000)
        order = OrderFactory.delivered(user=user, earned_points=100)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        PointHistoryFactory.earn(
            user=user,
            points=100,
            balance=5000,
            order=order,
            expires_at=timezone.now() + timedelta(days=365),
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = Decimal("10000")
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        initial_points = user.points
        return_id = return_obj.id

        def call_complete_refund(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                with patch("shopping.utils.toss_payment.TossPaymentClient"):
                    result = ReturnService.complete_refund(ret)
                return {"success": True, "status": result.status}
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"{type(e).__name__}: {str(e)}"}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call_complete_refund, return_id) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        assert success_count == 1, f"정확히 1개만 성공. 실제: {success_count}"
        assert failure_count == 1, f"정확히 1개 실패. 실제: {failure_count}"

        # 실패 이유 확인
        failed_results = [r for r in results if not r["success"]]
        assert any("반품 도착 상태에서만" in r["error"] for r in failed_results), f"상태 오류여야 함: {failed_results}"

        # 상태 확인
        return_obj.refresh_from_db()
        assert return_obj.status == "completed"

        # 포인트 정합성 (1번만 차감)
        user.refresh_from_db()
        assert user.points == initial_points - 100

        # cancel_deduct 이력 1개만
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).count()
        assert cancel_count == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestRefundPointInvariant:
    """
    핵심 불변 조건: 환불 시 포인트 정합성

    Purpose:
        동시 환불 처리 시 포인트가 정확히 1회만 회수됨
    Type:
        Core invariant test (must never fail)
    """

    def test_concurrent_5_refunds_same_user_point_integrity(self):
        """
        Purpose:
            동일 사용자 5개 Return 동시 환불 시 포인트 정합성 유지
        Scenario:
            사용자 10000P, 5개 주문 각 100P 적립, 5개 환불 동시 처리
        Expected:
            최종 포인트 = 10000 - 500 = 9500P
        """
        # Arrange
        user = UserFactory(points=10000)
        returns = []

        for i in range(5):
            product = ProductFactory(stock=10)
            order = OrderFactory.delivered(user=user, earned_points=100)
            order_item = OrderItemFactory(order=order, product=product, quantity=1)

            PointHistoryFactory.earn(
                user=user,
                points=100,
                balance=10000,
                order=order,
                expires_at=timezone.now() + timedelta(days=365),
            )

            return_obj = ReturnFactory.received(type="refund", order=order, user=user)
            ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
            return_obj.refund_amount = Decimal("10000")
            return_obj.save()

            PaymentFactory(order=order, status="done", payment_key=f"test_key_{i}")
            returns.append(return_obj)

        initial_points = user.points

        def call_complete_refund(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                with patch("shopping.utils.toss_payment.TossPaymentClient"):
                    ReturnService.complete_refund(ret)
                return {"success": True, "return_id": ret_id}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # Act
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(call_complete_refund, r.id) for r in returns]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 5, f"모두 성공. 실제: {success_count}"

        # 포인트 정합성
        user.refresh_from_db()
        expected_points = initial_points - (100 * 5)
        assert user.points == expected_points, f"포인트 정합성. 예상: {expected_points}, 실제: {user.points}"

        # cancel_deduct 이력 5개
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct").count()
        assert cancel_count == 5


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestExchangeStockInvariant:
    """
    핵심 불변 조건: 교환 시 재고 정합성

    Purpose:
        동시 교환 처리 시 재고가 정확히 1회만 조정됨
    Type:
        Core invariant test (must never fail)
    """

    def test_concurrent_complete_exchange_same_return_stock_integrity(self):
        """
        Purpose:
            동일 Return에 대해 2회 동시 교환 요청 시 재고 1회만 조정
        Scenario:
            Return 1건 received 상태, 2개 스레드 동시 complete_exchange 호출
        Expected:
            1개 성공, 교환 상품 재고 1 감소, 반품 상품 재고 1 증가
        """
        # Arrange
        original_product = ProductFactory(stock=10)
        exchange_product = ProductFactory(stock=5)

        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order, product=original_product, quantity=1)

        return_obj = ReturnFactory.received(type="exchange", order=order, exchange_product=exchange_product)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)

        return_id = return_obj.id
        initial_exchange_stock = exchange_product.stock
        initial_original_stock = original_product.stock

        def call_complete_exchange(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                result = ReturnService.complete_exchange(
                    ret,
                    exchange_tracking_number="123456789012",
                    exchange_shipping_company="CJ대한통운",
                )
                return {"success": True, "status": result.status}
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"{type(e).__name__}: {str(e)}"}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call_complete_exchange, return_id) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        assert success_count == 1, f"1개만 성공. 실제: {success_count}"
        assert failure_count == 1, f"1개 실패. 실제: {failure_count}"

        # 재고 정합성
        exchange_product.refresh_from_db()
        original_product.refresh_from_db()

        assert exchange_product.stock == initial_exchange_stock - 1, "교환 재고 1 감소"
        assert original_product.stock == initial_original_stock + 1, "반품 재고 1 증가"

    def test_exchange_last_stock_only_one_succeeds(self):
        """
        Purpose:
            교환 상품 재고 1개일 때 2개 교환 동시 요청 시 1개만 성공
        Scenario:
            교환 상품 재고 = 1, 2건 교환 동시 요청
        Expected:
            1건 성공, 1건 재고 부족 실패, 재고 = 0 (음수 안됨)
        """
        # Arrange
        exchange_product = ProductFactory(stock=1)

        returns = []
        for _ in range(2):
            original_product = ProductFactory(stock=10)
            order = OrderFactory.delivered()
            order_item = OrderItemFactory(order=order, product=original_product, quantity=1)

            return_obj = ReturnFactory.received(type="exchange", order=order, exchange_product=exchange_product)
            ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
            returns.append(return_obj)

        def call_complete_exchange(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                ReturnService.complete_exchange(
                    ret,
                    exchange_tracking_number="123456789012",
                    exchange_shipping_company="CJ대한통운",
                )
                return {"success": True}
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"{type(e).__name__}: {str(e)}"}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call_complete_exchange, r.id) for r in returns]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        assert success_count == 1, f"1개만 성공. 실제: {success_count}"
        assert failure_count == 1, f"1개 재고 부족. 실제: {failure_count}"

        # 재고 = 0 (음수 안됨)
        exchange_product.refresh_from_db()
        assert exchange_product.stock == 0, f"재고 = 0. 실제: {exchange_product.stock}"

        # 실패 이유 확인
        failure_errors = [r["error"] for r in results if not r["success"]]
        assert any("재고가 부족" in e for e in failure_errors), f"재고 부족 에러: {failure_errors}"


# =============================================================================
# B. 상태 전이 테스트 (State Transition Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestApproveRejectConcurrency:
    """
    상태 전이 동시성: 승인/거부 동시 요청

    Purpose:
        동일 Return에 대해 승인과 거부가 동시에 요청될 때 하나만 성공
    """

    def test_concurrent_approve_same_return_only_one_succeeds(self):
        """
        Purpose:
            동일 Return 2회 동시 승인 요청 시 1회만 성공
        Scenario:
            Return 1건 requested 상태, 2개 스레드 동시 approve 호출
        Expected:
            1개 성공, 1개 상태 오류
        """
        # Arrange
        return_obj = ReturnFactory.requested()
        return_id = return_obj.id

        def call_approve(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                result = ReturnService.approve_return(ret)
                return {"success": True, "status": result.status}
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"{type(e).__name__}: {str(e)}"}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call_approve, return_id) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"1개만 성공. 실제: {success_count}"

        return_obj.refresh_from_db()
        assert return_obj.status == "approved"

    def test_concurrent_approve_and_reject_mutually_exclusive(self):
        """
        Purpose:
            동일 Return에 승인과 거부가 동시 요청 시 하나만 성공
        Scenario:
            Return 1건 requested 상태, 승인/거부 동시 요청
        Expected:
            둘 중 하나만 성공, 상태는 approved 또는 rejected
        """
        # Arrange
        return_obj = ReturnFactory.requested()
        return_id = return_obj.id
        results = []

        def call_approve():
            close_db_connection()
            try:
                ret = Return.objects.get(id=return_id)
                result = ReturnService.approve_return(ret)
                results.append({"action": "approve", "success": True, "status": result.status})
            except Exception as e:
                results.append({"action": "approve", "success": False, "error": str(e)})

        def call_reject():
            close_db_connection()
            try:
                ret = Return.objects.get(id=return_id)
                result = ReturnService.reject_return(ret, reason="테스트 거부")
                results.append({"action": "reject", "success": True, "status": result.status})
            except Exception as e:
                results.append({"action": "reject", "success": False, "error": str(e)})

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(call_approve)
            executor.submit(call_reject)
            executor.shutdown(wait=True)

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"1개만 성공. 실제: {success_count}"

        return_obj.refresh_from_db()
        assert return_obj.status in ["approved", "rejected"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestConfirmReceiveConcurrency:
    """
    상태 전이 동시성: 수령 확인 동시 요청

    Purpose:
        동일 Return에 대해 수령 확인이 동시에 요청될 때 하나만 성공
    """

    def test_concurrent_confirm_receive_same_return_only_one_succeeds(self):
        """
        Purpose:
            동일 Return 2회 동시 수령 확인 요청 시 1회만 성공
        Scenario:
            Return 1건 shipping 상태, 2개 스레드 동시 confirm_receive 호출
        Expected:
            1개 성공, 1개 상태 오류
        """
        # Arrange
        return_obj = ReturnFactory.shipping()
        return_id = return_obj.id

        def call_confirm_receive(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                result = ReturnService.confirm_receive_return(ret)
                return {"success": True, "status": result.status}
            except ValueError as e:
                return {"success": False, "error": str(e)}
            except Exception as e:
                return {"success": False, "error": f"{type(e).__name__}: {str(e)}"}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call_confirm_receive, return_id) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"1개만 성공. 실제: {success_count}"

        return_obj.refresh_from_db()
        assert return_obj.status == "received"


# =============================================================================
# C. 서로 다른 Return 동시 처리 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.return_race
class TestDifferentReturnsConcurrency:
    """
    서로 다른 Return 동시 처리

    Purpose:
        서로 다른 Return에 대한 동시 요청은 모두 성공
    """

    def test_concurrent_complete_refund_different_returns_all_succeed(self):
        """
        Purpose:
            서로 다른 Return 2개 동시 환불 시 모두 성공
        Scenario:
            동일 사용자 2개 Return, 각각 received 상태, 2개 스레드 동시 환불
        Expected:
            둘 다 성공, 각각의 포인트 처리 정확
        """
        # Arrange
        user = UserFactory(points=10000)

        # Return 1
        product1 = ProductFactory(stock=10)
        order1 = OrderFactory.delivered(user=user, earned_points=100)
        order_item1 = OrderItemFactory(order=order1, product=product1, quantity=1)
        PointHistoryFactory.earn(
            user=user,
            points=100,
            balance=10000,
            order=order1,
            expires_at=timezone.now() + timedelta(days=365),
        )
        return_obj1 = ReturnFactory.received(type="refund", order=order1, user=user)
        ReturnItemFactory(return_request=return_obj1, order_item=order_item1, quantity=1)
        return_obj1.refund_amount = Decimal("10000")
        return_obj1.save()
        PaymentFactory(order=order1, status="done", payment_key="test_key_1")

        # Return 2
        product2 = ProductFactory(stock=10)
        order2 = OrderFactory.delivered(user=user, earned_points=200)
        order_item2 = OrderItemFactory(order=order2, product=product2, quantity=1)
        PointHistoryFactory.earn(
            user=user,
            points=200,
            balance=10000,
            order=order2,
            expires_at=timezone.now() + timedelta(days=365),
        )
        return_obj2 = ReturnFactory.received(type="refund", order=order2, user=user)
        ReturnItemFactory(return_request=return_obj2, order_item=order_item2, quantity=1)
        return_obj2.refund_amount = Decimal("10000")
        return_obj2.save()
        PaymentFactory(order=order2, status="done", payment_key="test_key_2")

        initial_points = user.points

        def call_complete_refund(ret_id: int) -> dict:
            close_db_connection()
            try:
                ret = Return.objects.get(id=ret_id)
                with patch("shopping.utils.toss_payment.TossPaymentClient"):
                    ReturnService.complete_refund(ret)
                return {"success": True, "return_id": ret_id}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # Act
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(call_complete_refund, return_obj1.id),
                executor.submit(call_complete_refund, return_obj2.id),
            ]
            results = [f.result() for f in as_completed(futures)]

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 2, f"둘 다 성공. 실제: {success_count}"

        # 포인트 정합성 (300P 차감)
        user.refresh_from_db()
        assert user.points == initial_points - 100 - 200

        # cancel_deduct 이력 2개
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct").count()
        assert cancel_count == 2
