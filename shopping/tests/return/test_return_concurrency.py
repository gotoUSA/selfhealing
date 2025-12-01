"""
환불/교환 동시성 테스트

테스트 범위:
- 동일 Return에 대한 동시 환불 처리 요청
- 상태 변경 동시성 (approve, reject, confirm_receive)
- select_for_update 락 동작 검증
- 포인트 처리 동시성 정합성

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

from shopping.models.point import PointHistory
from shopping.models.return_request import Return
from shopping.models.user import User
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


class TestCompleteRefundConcurrency(TransactionTestCase):
    """
    환불 완료 동시성 테스트

    TransactionTestCase 사용 이유:
    - 실제 DB 트랜잭션 테스트 필요
    - 동시성 시나리오에서 select_for_update 검증
    """

    def setUp(self):
        """테스트 설정"""
        # Django DB connection 닫기 (각 스레드가 새 연결 사용하도록)
        connection.close()

    def _call_complete_refund(self, return_id: int) -> dict:
        """
        환불 완료 요청 헬퍼

        각 스레드에서 독립적인 DB 연결 사용
        """
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)

            with patch("shopping.utils.toss_payment.TossPaymentClient"):
                result = ReturnService.complete_refund(return_obj)

            return {
                "success": True,
                "status": result.status,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "status": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "status": None,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def test_concurrent_complete_refund_same_return(self):
        """
        동일 Return에 대한 2회 동시 환불 요청

        시나리오:
        - Return 1건이 received 상태
        - 2개의 스레드가 동시에 complete_refund 호출
        - 예상: 1개만 성공, 1개는 상태 오류로 실패
        - 검증: 데이터 정합성 (중복 처리 없음)
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
        num_requests = 2
        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._call_complete_refund, return_id) for _ in range(num_requests)]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        # 정확히 1개만 성공해야 함
        assert success_count == 1, f"성공 횟수 오류: expected=1, actual={success_count}"
        assert failure_count == 1, f"실패 횟수 오류: expected=1, actual={failure_count}"

        # 실패 이유 확인 (상태 오류)
        failed_results = [r for r in results if not r["success"]]
        assert any("반품 도착 상태에서만" in r["error"] for r in failed_results), f"예상치 못한 실패 이유: {failed_results}"

        # Return 상태 확인
        return_obj.refresh_from_db()
        assert return_obj.status == "completed"

        # 포인트 정합성 (1번만 차감)
        user.refresh_from_db()
        assert user.points == initial_points - 100

        # cancel_deduct 이력 1개만
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).count()
        assert cancel_count == 1

    def test_concurrent_complete_refund_different_returns(self):
        """
        서로 다른 Return에 대한 동시 환불 요청

        시나리오:
        - 동일 사용자의 2개 Return이 각각 received 상태
        - 2개의 스레드가 각각 다른 Return에 complete_refund 호출
        - 예상: 둘 다 성공
        - 검증: 각각의 포인트 처리 정확성
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
        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._call_complete_refund, return_obj1.id),
                executor.submit(self._call_complete_refund, return_obj2.id),
            ]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert - 둘 다 성공
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 2, f"둘 다 성공해야 함: results={results}"

        # 포인트 정합성 (총 300P 차감)
        user.refresh_from_db()
        assert user.points == initial_points - 100 - 200

        # 각각의 cancel_deduct 이력
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct").count()
        assert cancel_count == 2


class TestApproveReturnConcurrency(TransactionTestCase):
    """
    승인/거부 동시성 테스트
    """

    def setUp(self):
        connection.close()

    def _call_approve_return(self, return_id: int) -> dict:
        """승인 요청 헬퍼"""
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)
            result = ReturnService.approve_return(return_obj)

            return {
                "success": True,
                "status": result.status,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "status": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "status": None,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def _call_reject_return(self, return_id: int, reason: str) -> dict:
        """거부 요청 헬퍼"""
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)
            result = ReturnService.reject_return(return_obj, reason=reason)

            return {
                "success": True,
                "status": result.status,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "status": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "status": None,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def test_concurrent_approve_same_return(self):
        """
        동일 Return에 대한 2회 동시 승인 요청

        예상: 1개만 성공, 1개는 상태 오류로 실패
        """
        # Arrange
        return_obj = ReturnFactory.requested()
        return_id = return_obj.id
        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._call_approve_return, return_id) for _ in range(2)]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"정확히 1개만 성공해야 함: {results}"

        return_obj.refresh_from_db()
        assert return_obj.status == "approved"

    def test_concurrent_approve_and_reject_same_return(self):
        """
        동일 Return에 대해 승인과 거부가 동시에 요청됨

        예상: 둘 중 하나만 성공
        """
        # Arrange
        return_obj = ReturnFactory.requested()
        return_id = return_obj.id
        results = []

        # Act - 승인과 거부 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            approve_future = executor.submit(self._call_approve_return, return_id)
            reject_future = executor.submit(self._call_reject_return, return_id, "테스트 거부")

            results.append(approve_future.result())
            results.append(reject_future.result())

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"정확히 1개만 성공해야 함: {results}"

        return_obj.refresh_from_db()
        assert return_obj.status in ["approved", "rejected"]


class TestConfirmReceiveConcurrency(TransactionTestCase):
    """
    수령 확인 동시성 테스트
    """

    def setUp(self):
        connection.close()

    def _call_confirm_receive(self, return_id: int) -> dict:
        """수령 확인 요청 헬퍼"""
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)
            result = ReturnService.confirm_receive_return(return_obj)

            return {
                "success": True,
                "status": result.status,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "status": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "status": None,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def test_concurrent_confirm_receive_same_return(self):
        """
        동일 Return에 대한 2회 동시 수령 확인 요청

        예상: 1개만 성공
        """
        # Arrange
        return_obj = ReturnFactory.shipping()
        return_id = return_obj.id
        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._call_confirm_receive, return_id) for _ in range(2)]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 1, f"정확히 1개만 성공해야 함: {results}"

        return_obj.refresh_from_db()
        assert return_obj.status == "received"


class TestPointRecoveryConcurrency(TransactionTestCase):
    """
    포인트 회수 동시성 정합성 테스트
    """

    def setUp(self):
        connection.close()

    def _call_complete_refund(self, return_id: int) -> dict:
        """환불 완료 요청"""
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)

            with patch("shopping.utils.toss_payment.TossPaymentClient"):
                result = ReturnService.complete_refund(return_obj)

            return {
                "success": True,
                "return_id": return_obj.id,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "return_id": return_id,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "return_id": return_id,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def test_concurrent_5_refunds_same_user_point_integrity(self):
        """
        동일 사용자의 5개 Return 동시 환불 - 포인트 정합성

        시나리오:
        - 사용자 보유: 10,000P
        - 5개 주문, 각각 100P 적립
        - 5개 환불 동시 처리
        - 검증: 최종 포인트 = 10,000 - 500 = 9,500P
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
        results = []

        # Act - 5개 동시 요청
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self._call_complete_refund, r.id) for r in returns]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert
        success_count = sum(1 for r in results if r["success"])
        assert success_count == 5, f"모두 성공해야 함: {results}"

        # 포인트 정합성
        user.refresh_from_db()
        expected_points = initial_points - (100 * 5)
        assert user.points == expected_points, f"포인트 정합성 오류: expected={expected_points}, actual={user.points}"

        # cancel_deduct 이력 개수
        cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct").count()
        assert cancel_count == 5, f"cancel_deduct 이력 개수 오류: {cancel_count}"


class TestCompleteExchangeConcurrency(TransactionTestCase):
    """
    교환 완료 동시성 테스트

    TransactionTestCase 사용 이유:
    - 실제 DB 트랜잭션 테스트 필요
    - 동시성 시나리오에서 select_for_update 검증
    - 재고 정합성 검증
    """

    def setUp(self):
        """테스트 설정"""
        connection.close()

    def _call_complete_exchange(self, return_id: int) -> dict:
        """
        교환 완료 요청 헬퍼

        각 스레드에서 독립적인 DB 연결 사용
        """
        from django.db import connection

        connection.close()

        try:
            return_obj = Return.objects.get(id=return_id)
            result = ReturnService.complete_exchange(
                return_obj,
                exchange_tracking_number="123456789012",
                exchange_shipping_company="CJ대한통운",
            )

            return {
                "success": True,
                "status": result.status,
                "error": None,
            }
        except ValueError as e:
            return {
                "success": False,
                "status": None,
                "error": str(e),
            }
        except Exception as e:
            return {
                "success": False,
                "status": None,
                "error": f"{type(e).__name__}: {str(e)}",
            }

    def test_concurrent_complete_exchange_same_return(self):
        """
        동일 Return에 대한 2회 동시 교환 완료 요청

        시나리오:
        - Return 1건이 received 상태
        - 2개의 스레드가 동시에 complete_exchange 호출
        - 예상: 1개만 성공, 1개는 상태 오류로 실패
        - 검증: 재고 정합성 (중복 차감 없음)
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
        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._call_complete_exchange, return_id),
                executor.submit(self._call_complete_exchange, return_id),
            ]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert - 1개만 성공
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        assert success_count == 1, f"1개만 성공해야 함: results={results}"
        assert failure_count == 1, f"1개는 실패해야 함: results={results}"

        # 재고 정합성 (1번만 조정)
        exchange_product.refresh_from_db()
        original_product.refresh_from_db()

        assert exchange_product.stock == initial_exchange_stock - 1, (
            f"교환 재고 1번만 차감: expected={initial_exchange_stock - 1}, actual={exchange_product.stock}"
        )
        assert original_product.stock == initial_original_stock + 1, (
            f"반품 재고 1번만 증가: expected={initial_original_stock + 1}, actual={original_product.stock}"
        )

    def test_concurrent_exchange_last_stock_only_one_succeeds(self):
        """
        교환 상품 재고 1개, 2개 교환 동시 요청

        시나리오:
        - 교환 상품 재고 = 1
        - 2건의 교환이 동시에 complete_exchange 호출
        - 예상: 1건만 성공, 1건은 재고 부족으로 실패
        - 검증: select_for_update로 재고 정합성 보장
        """
        # Arrange
        exchange_product = ProductFactory(stock=1)  # 재고 1개

        # 교환 신청 2건 생성
        returns = []
        for _ in range(2):
            original_product = ProductFactory(stock=10)
            order = OrderFactory.delivered()
            order_item = OrderItemFactory(order=order, product=original_product, quantity=1)

            return_obj = ReturnFactory.received(type="exchange", order=order, exchange_product=exchange_product)
            ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
            returns.append(return_obj)

        results = []

        # Act - 동시 요청
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._call_complete_exchange, r.id) for r in returns]

            for future in as_completed(futures):
                results.append(future.result())

        # Assert - 1개만 성공
        success_count = sum(1 for r in results if r["success"])
        failure_count = sum(1 for r in results if not r["success"])

        assert success_count == 1, f"1개만 성공해야 함: results={results}"
        assert failure_count == 1, f"1개는 재고 부족으로 실패해야 함: results={results}"

        # 재고 정합성 (0이 되어야 함, 음수 안됨)
        exchange_product.refresh_from_db()
        assert exchange_product.stock == 0, f"재고는 0이어야 함: actual={exchange_product.stock}"

        # 실패 이유 확인
        failure_errors = [r["error"] for r in results if not r["success"]]
        assert any("재고가 부족" in e for e in failure_errors), f"재고 부족 에러여야 함: {failure_errors}"
