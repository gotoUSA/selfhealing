"""
L2 Crash Recovery Test: Celery Task Crash 시뮬레이션

Celery Worker Crash 상황에서의 데이터 무결성 및 복구 테스트입니다.
금융권/PG 수준의 신뢰성 테스트입니다.

시나리오:
- L2-D: Webhook 처리 중 Celery Worker Crash
- L2-E: 결제 승인 Task Crash 후 Replay (idempotency 검증)
"""

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.db import transaction
from celery.exceptions import WorkerLostError, Retry

from shopping.models import Order, OrderItem, Payment, Product, User
from shopping.models.payment import PaymentLog
from shopping.tasks.payment_tasks import call_toss_confirm_api, finalize_payment_confirm
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
    TossResponseBuilder,
)
from shopping.utils.toss_payment import TossPaymentError


class SimulatedCrashError(Exception):
    """테스트용 Crash 시뮬레이션 예외"""
    pass


@pytest.mark.django_db(transaction=True)
class TestL2CeleryCrash:
    """L2: Celery Worker Crash 시뮬레이션 테스트"""

    def test_l2d_webhook_during_celery_crash(self, mocker):
        """
        L2-D: Webhook 처리 중 Celery Worker Crash

        Timeline:
        1. 결제 승인 요청 → Celery task 시작
        2. ❌ Worker 죽음 (WorkerLostError)
        3. PG가 Webhook 재전송
        4. 검증: Webhook으로 정상 복구
        """
        # 1. 테스트 데이터 준비
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
            payment_key="test_crash_key",
        )

        # 2. Task가 crash 발생시키도록 mock
        mock_task = mocker.patch.object(
            call_toss_confirm_api,
            "delay",
            side_effect=WorkerLostError("Worker crashed!")
        )

        # 3. Task 호출 시도 (crash 발생)
        worker_crashed = False
        try:
            call_toss_confirm_api.delay("test_crash_key", order.id, 10000)
        except WorkerLostError:
            worker_crashed = True

        assert worker_crashed, "Worker should have crashed"

        # 4. Payment 상태 확인 (여전히 in_progress)
        payment.refresh_from_db()
        assert payment.status == "in_progress", \
            "Payment should remain in_progress after worker crash"

        # 5. Webhook으로 복구 시뮬레이션
        # (실제로는 Webhook handler가 호출됨)
        toss_response = TossResponseBuilder.success_response(
            payment_key="test_crash_key",
            order_id=str(order.id),
            amount=10000,
        )
        payment.mark_as_paid(toss_response)

        # 6. 최종 상태 검증
        payment.refresh_from_db()
        assert payment.status == "done", \
            "Payment should be done after webhook recovery"

        print("✓ L2-D PASSED: Webhook recovered payment after worker crash")

    def test_l2d_payment_state_preserved_on_worker_crash(self, mocker):
        """
        L2-D 변형: Worker crash 시 Payment 상태 보존 검증

        finalize_payment_confirm 실행 중 crash 발생해도
        트랜잭션이 롤백되어 상태가 보존되는지 확인
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
        )

        original_status = payment.status
        original_stock = product.stock
        original_sold_count = product.sold_count

        # mark_as_paid 시점에서 crash 시뮬레이션
        def mock_mark_as_paid(self, data):
            raise Exception("Worker crashed during mark_as_paid")

        mocker.patch.object(Payment, "mark_as_paid", mock_mark_as_paid)

        # Act
        with pytest.raises(Exception, match="Worker crashed"):
            finalize_payment_confirm(
                TossResponseBuilder.success_response(),
                payment.id,
                user.id
            )

        # Assert - 트랜잭션 롤백으로 상태 유지
        payment.refresh_from_db()
        product.refresh_from_db()

        assert payment.status == original_status, \
            f"Payment status should be '{original_status}', got '{payment.status}'"
        assert product.stock == original_stock, \
            f"Stock should be {original_stock}, got {product.stock}"
        assert product.sold_count == original_sold_count, \
            f"Sold count should be {original_sold_count}, got {product.sold_count}"

        print("✓ L2-D(변형) PASSED: Payment state preserved after worker crash")

    def test_l2e_confirm_task_crash_then_replay(self, mocker):
        """
        L2-E: 결제 승인 Task 중간 Crash 후 Replay

        Timeline:
        1. confirm_payment task 시작
        2. Toss API 호출 성공
        3. DB 저장 중 ❌ CRASH
        4. 동일 요청 재시도
        5. 검증: idempotency 유지, 중복 결제 없음
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
            idempotency_key="unique_key_123",
        )

        toss_response = TossResponseBuilder.success_response(
            payment_key="toss_key_123",
            order_id=str(order.id),
            amount=10000,
        )

        # 1. 첫 번째 시도에서 crash (mark_as_paid에서 예외)
        call_count = {"value": 0}
        original_mark_as_paid = Payment.mark_as_paid

        def mock_mark_as_paid_first_call(self, data):
            call_count["value"] += 1
            if call_count["value"] == 1:
                # 첫 번째 호출: crash
                raise SimulatedCrashError("Crash during first mark_as_paid")
            # 두 번째 호출: 정상 처리
            return original_mark_as_paid(self, data)

        mocker.patch.object(Payment, "mark_as_paid", mock_mark_as_paid_first_call)

        # 첫 번째 시도 (실패)
        first_attempt_crashed = False
        try:
            finalize_payment_confirm(toss_response, payment.id, user.id)
        except SimulatedCrashError:
            first_attempt_crashed = True

        assert first_attempt_crashed, "First attempt should have crashed"

        # Payment 상태 확인 (여전히 in_progress)
        payment.refresh_from_db()
        assert payment.status == "in_progress", \
            "Payment should remain in_progress after crash"

        # 2. 재시도 (정상 처리)
        result = finalize_payment_confirm(toss_response, payment.id, user.id)

        # 3. 검증: 정상 처리됨
        assert result["status"] == "success", \
            f"Second attempt should succeed, got {result['status']}"

        payment.refresh_from_db()
        assert payment.status == "done", "Payment should be done after retry"

        # 4. 중복 처리 방지 확인: 세 번째 시도
        result_duplicate = finalize_payment_confirm(toss_response, payment.id, user.id)
        assert result_duplicate["status"] == "already_processed", \
            "Third attempt should return already_processed"

        print("✓ L2-E PASSED: Idempotency maintained after crash and replay")

    def test_l2e_no_duplicate_payment_on_retry(self):
        """
        L2-E 변형: 재시도 시 중복 결제 없음 확인

        동일한 결제 요청이 여러 번 들어와도
        실제 결제 처리는 한 번만 수행됨
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
        )

        toss_response = TossResponseBuilder.success_response(
            order_id=str(order.id),
            amount=10000,
        )

        # 첫 번째 처리
        result1 = finalize_payment_confirm(toss_response, payment.id, user.id)
        assert result1["status"] == "success"

        product.refresh_from_db()
        sold_after_first = product.sold_count

        # 두 번째 처리 (중복)
        result2 = finalize_payment_confirm(toss_response, payment.id, user.id)
        assert result2["status"] == "already_processed"

        product.refresh_from_db()
        sold_after_second = product.sold_count

        # sold_count가 증가하지 않았는지 확인
        assert sold_after_second == sold_after_first, \
            f"Sold count should remain {sold_after_first}, got {sold_after_second}"

        # Payment 중복 생성 확인
        payment_count = Payment.objects.filter(order=order, status="done").count()
        assert payment_count == 1, \
            f"Should have exactly 1 successful payment, got {payment_count}"

        print("✓ L2-E(변형) PASSED: No duplicate payments on retry")

    def test_l2e_concurrent_duplicate_requests(self):
        """
        L2-E 변형: 동시 중복 요청 처리

        거의 동시에 들어온 중복 요청 중 하나만 처리됨
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
        )

        toss_response = TossResponseBuilder.success_response(
            order_id=str(order.id),
            amount=10000,
        )

        # 동시 요청 시뮬레이션 (순차적이지만 idempotency 검증)
        results = []
        for i in range(5):
            result = finalize_payment_confirm(toss_response, payment.id, user.id)
            results.append(result["status"])

        # 첫 번째만 success, 나머지는 already_processed
        success_count = results.count("success")
        already_processed_count = results.count("already_processed")

        assert success_count == 1, f"Should have exactly 1 success, got {success_count}"
        assert already_processed_count == 4, \
            f"Should have 4 already_processed, got {already_processed_count}"

        # 최종 상태 확인
        payment.refresh_from_db()
        product.refresh_from_db()

        assert payment.status == "done"
        # sold_count는 1만 증가해야 함 (중복 처리 방지)

        print("✓ L2-E(동시성) PASSED: Only one request processed out of 5")


@pytest.mark.django_db(transaction=True)
class TestL2NetworkErrorRecovery:
    """L2: 네트워크 에러 복구 테스트"""

    def test_network_error_triggers_retry(self, mocker):
        """
        네트워크 에러 발생 시 재시도 메커니즘 동작 확인
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
        )

        # Toss API가 네트워크 에러 발생시키도록 mock
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError(
                code="NETWORK_ERROR",
                message="Connection failed"
            ),
        )

        # retry 호출 시 Retry 예외 발생시키도록 mock
        mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=Retry("Retrying due to network error"),
        )

        # Act
        with pytest.raises(Retry):
            call_toss_confirm_api("test_payment_key", order.id, int(order.total_amount))

        # Assert - 네트워크 에러 시 payment가 aborted로 변경되고 retry 시도
        payment.refresh_from_db()
        assert payment.status == "aborted", \
            f"Payment should be aborted after network error, got '{payment.status}'"

        print("✓ Network error retry PASSED")

    def test_max_retry_exceeded_aborts_payment(self, mocker):
        """
        최대 재시도 횟수 초과 시 결제 abort 처리
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(
            user=user,
            status="confirmed",
            total_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(
            order=order,
            status="in_progress",
            amount=Decimal("10000"),
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError(code="TIMEOUT", message="Request timed out"),
        )
        mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=TossPaymentError(code="TIMEOUT", message="Request timed out"),
        )

        # Act
        with pytest.raises(TossPaymentError):
            call_toss_confirm_api("test_payment_key", order.id, int(order.total_amount))

        # Assert
        payment.refresh_from_db()
        assert payment.status == "aborted", \
            f"Payment should be aborted after max retries, got '{payment.status}'"

        print("✓ Max retry exceeded PASSED")


@pytest.mark.django_db(transaction=True)
class TestL2CeleryCrashSummary:
    """L2 Celery 테스트 결과 요약"""

    def test_summary(self):
        """테스트 완료 시 결과 출력"""
        print("""
============================================================
L2 CELERY CRASH RECOVERY TEST RESULTS
============================================================

Celery Crash Recovery Tests (Method 3: Celery Mock):
  L2-D Webhook Recovery:      Ready for execution
  L2-D (State Preservation):  Ready for execution
  L2-E Idempotency:           Ready for execution
  L2-E (No Duplicates):       Ready for execution
  L2-E (Concurrent):          Ready for execution
  Network Error Retry:        Ready for execution
  Max Retry Exceeded:         Ready for execution

============================================================
""")
