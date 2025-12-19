"""
L2 Crash Recovery Test: Transaction Savepoint 테스트

DB 트랜잭션 중간 장애 발생 시 데이터 무결성이 유지되는지 검증합니다.
금융권/PG 수준의 신뢰성 테스트입니다.

시나리오:
- L2-A: Payment 상태 업데이트 직전 Crash
- L2-B: 포인트 차감 후 Order 업데이트 전 Crash
- L2-C: 재고 차감 후 Payment 완료 전 Crash
"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from decimal import Decimal
from django.db import transaction

from shopping.models import Order, OrderItem, Payment, Product, User
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
    TestConstants,
)


class SimulatedCrashError(Exception):
    """테스트용 Crash 시뮬레이션 예외"""

    pass


@pytest.mark.django_db(transaction=True)
class TestL2TransactionCrash:
    """L2: DB Transaction 중간 Crash 테스트"""

    def test_l2a_payment_status_crash_before_done(self):
        """
        L2-A: Payment 상태 업데이트 직전 Crash

        Timeline:
        1. Payment 조회 ✓
        2. payment.status = 'done' 설정
        3. ❌ CRASH (save 전)
        4. 검증: 상태가 'pending' 유지
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
        )
        original_status = payment.status

        # 2. 트랜잭션 내에서 crash 시뮬레이션
        try:
            with transaction.atomic():
                payment.status = "done"
                # Crash 시뮬레이션 (save 전)
                raise SimulatedCrashError("Crash before payment.save()")
        except SimulatedCrashError:
            pass

        # 3. 검증: DB에서 다시 조회하면 원래 상태 유지
        payment.refresh_from_db()
        assert payment.status == original_status, f"Payment should remain '{original_status}', got '{payment.status}'"

        print(f"✓ L2-A PASSED: Payment status rolled back to '{payment.status}'")

    def test_l2a_payment_crash_after_partial_save(self):
        """
        L2-A 변형: Payment save 후 Order save 전 Crash

        Payment는 저장됐지만 Order 업데이트 전에 crash 발생
        -> 전체 트랜잭션이 롤백되어야 함
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)
        order = OrderFactory(user=user, status="confirmed", total_amount=Decimal("10000"))
        OrderItemFactory(order=order, product=product, quantity=1)
        payment = PaymentFactory(order=order, status="in_progress", amount=Decimal("10000"))

        original_payment_status = payment.status
        original_order_status = order.status

        try:
            with transaction.atomic():
                # Payment 상태 변경 및 저장
                payment.status = "done"
                payment.save()

                # Order 업데이트 전 crash
                order.status = "paid"
                raise SimulatedCrashError("Crash before order.save()")
        except SimulatedCrashError:
            pass

        # 검증: 둘 다 롤백됨
        payment.refresh_from_db()
        order.refresh_from_db()

        assert (
            payment.status == original_payment_status
        ), f"Payment status should be '{original_payment_status}', got '{payment.status}'"
        assert order.status == original_order_status, f"Order status should be '{original_order_status}', got '{order.status}'"

        print(f"✓ L2-A(변형) PASSED: Both Payment and Order rolled back")

    def test_l2b_points_deducted_then_crash(self):
        """
        L2-B: 포인트 차감 후 Order 업데이트 전 Crash

        Timeline:
        1. 포인트 50,000 → 40,000 차감 ✓
        2. Order 상태 업데이트 중
        3. ❌ CRASH
        4. 검증: 포인트가 50,000으로 롤백
        """
        original_points = 50000
        deduct_amount = 10000

        user = UserFactory.with_points(original_points)
        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=Decimal(str(deduct_amount)),
            shipping_name="테스트",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 테스트구",
        )

        try:
            with transaction.atomic():
                # 포인트 차감
                user.points -= deduct_amount
                user.save()

                # Order 업데이트 중 crash
                order.status = "confirmed"
                raise SimulatedCrashError("Crash during order update")
        except SimulatedCrashError:
            pass

        # 검증: 포인트 롤백
        user.refresh_from_db()
        assert user.points == original_points, f"Points should be {original_points}, got {user.points}"

        print(f"✓ L2-B PASSED: Points rolled back to {user.points}")

    def test_l2b_points_deduct_with_point_history(self):
        """
        L2-B 변형: 포인트 차감 + PointHistory 생성 후 Crash

        PointHistory까지 생성되었지만 crash 발생
        -> 포인트와 PointHistory 모두 롤백
        """
        from shopping.models import PointHistory

        original_points = 50000
        deduct_amount = 10000
        user = UserFactory.with_points(original_points)

        initial_history_count = PointHistory.objects.filter(user=user).count()

        try:
            with transaction.atomic():
                # 포인트 차감
                user.points -= deduct_amount
                user.save()

                # PointHistory 생성 (올바른 필드명 사용)
                PointHistory.objects.create(
                    user=user,
                    points=-deduct_amount,
                    balance=user.points,
                    description="테스트 차감",
                    type="use",
                )

                # Crash
                raise SimulatedCrashError("Crash after PointHistory creation")
        except SimulatedCrashError:
            pass

        # 검증
        user.refresh_from_db()
        final_history_count = PointHistory.objects.filter(user=user).count()

        assert user.points == original_points, f"Points should be {original_points}, got {user.points}"
        assert (
            final_history_count == initial_history_count
        ), f"PointHistory count should be {initial_history_count}, got {final_history_count}"

        print(f"✓ L2-B(변형) PASSED: Points and PointHistory both rolled back")

    def test_l2c_stock_decreased_then_crash(self):
        """
        L2-C: 재고 차감 후 Payment 완료 전 Crash

        Timeline:
        1. 재고 100 → 99 차감 ✓
        2. Payment 완료 처리 중
        3. ❌ CRASH
        4. 검증: 재고가 100으로 복구
        """
        original_stock = 100
        product = ProductFactory(stock=original_stock)

        try:
            with transaction.atomic():
                # 재고 차감
                product.stock -= 1
                product.save()

                # Payment 처리 중 crash
                raise SimulatedCrashError("Crash during payment processing")
        except SimulatedCrashError:
            pass

        # 검증: 재고 롤백
        product.refresh_from_db()
        assert product.stock == original_stock, f"Stock should be {original_stock}, got {product.stock}"

        print(f"✓ L2-C PASSED: Stock rolled back to {product.stock}")

    def test_l2c_stock_and_sold_count_rollback(self):
        """
        L2-C 변형: 재고 차감 + sold_count 증가 후 Crash

        stock 감소와 sold_count 증가 모두 롤백되어야 함
        """
        original_stock = 100
        original_sold_count = 5
        product = ProductFactory(stock=original_stock, sold_count=original_sold_count)

        try:
            with transaction.atomic():
                # 재고 차감 및 판매수량 증가
                product.stock -= 1
                product.sold_count += 1
                product.save()

                # Crash
                raise SimulatedCrashError("Crash after stock update")
        except SimulatedCrashError:
            pass

        # 검증
        product.refresh_from_db()
        assert product.stock == original_stock, f"Stock should be {original_stock}, got {product.stock}"
        assert (
            product.sold_count == original_sold_count
        ), f"Sold count should be {original_sold_count}, got {product.sold_count}"

        print(f"✓ L2-C(변형) PASSED: Stock and sold_count both rolled back")

    def test_nested_transaction_savepoint_rollback(self):
        """
        중첩 트랜잭션(Savepoint) 테스트

        외부 트랜잭션은 유지하고 내부 savepoint만 롤백되는지 확인
        """
        user = UserFactory.with_points(50000)
        product = ProductFactory(stock=100)

        try:
            with transaction.atomic():
                # 외부 트랜잭션: 정상 처리
                user.points -= 5000
                user.save()

                try:
                    with transaction.atomic():
                        # 내부 savepoint: crash
                        product.stock -= 1
                        product.save()
                        raise SimulatedCrashError("Inner savepoint crash")
                except SimulatedCrashError:
                    # 내부 savepoint만 롤백됨
                    pass

                # 외부 트랜잭션에서 추가 작업 (stock은 원래대로)
                product.refresh_from_db()
                assert product.stock == 100, "Stock should be 100 after inner rollback"

                # 외부 트랜잭션도 예외로 롤백
                raise SimulatedCrashError("Outer transaction crash")
        except SimulatedCrashError:
            pass

        # 모든 것이 롤백됨
        user.refresh_from_db()
        product.refresh_from_db()

        assert user.points == 50000, f"User points should be 50000, got {user.points}"
        assert product.stock == 100, f"Product stock should be 100, got {product.stock}"

        print("✓ Nested transaction PASSED: All changes rolled back")

    def test_complete_payment_flow_crash_recovery(self):
        """
        완전한 결제 플로우에서의 crash recovery 테스트

        1. 주문 생성
        2. 재고 차감
        3. 포인트 차감
        4. Payment 생성
        5. ❌ CRASH
        6. 모든 변경 롤백 확인
        """
        original_stock = 100
        original_points = 50000
        product_price = 10000
        points_to_use = 5000

        user = UserFactory.with_points(original_points)
        product = ProductFactory(stock=original_stock, price=Decimal(str(product_price)))

        try:
            with transaction.atomic():
                # 1. 재고 차감
                product.stock -= 1
                product.save()

                # 2. 포인트 차감
                user.points -= points_to_use
                user.save()

                # 3. 주문 생성 (올바른 필드명 사용: used_points)
                order = Order.objects.create(
                    user=user,
                    status="confirmed",
                    total_amount=Decimal(str(product_price)),
                    used_points=points_to_use,
                    final_amount=Decimal(str(product_price - points_to_use)),
                    shipping_name="테스트",
                    shipping_phone="010-1234-5678",
                    shipping_postal_code="12345",
                    shipping_address="서울시 테스트구",
                )

                # 4. Payment 생성
                payment = Payment.objects.create(
                    order=order,
                    amount=Decimal(str(product_price - points_to_use)),
                    status="in_progress",
                    toss_order_id=f"test_order_{order.id}",
                )

                # 5. Crash (최종 완료 전)
                raise SimulatedCrashError("Crash before final completion")
        except SimulatedCrashError:
            pass

        # 검증: 모든 변경 롤백
        user.refresh_from_db()
        product.refresh_from_db()

        assert user.points == original_points, f"User points should be {original_points}, got {user.points}"
        assert product.stock == original_stock, f"Product stock should be {original_stock}, got {product.stock}"

        # Order와 Payment는 생성되지 않았어야 함
        assert not Order.objects.filter(user=user, used_points=points_to_use).exists(), "Order should not exist after rollback"

        print("✓ Complete payment flow crash recovery PASSED")


@pytest.mark.django_db(transaction=True)
class TestL2TransactionCrashSummary:
    """L2 테스트 결과 요약"""

    def test_summary(self):
        """테스트 완료 시 결과 출력"""
        print(
            """
============================================================
L2 TRANSACTION ROLLBACK TEST RESULTS
============================================================

Transaction Rollback Tests (Method 2: Savepoint):
  L2-A Payment Crash:         Ready for execution
  L2-A (Variant):             Ready for execution
  L2-B Points Crash:          Ready for execution
  L2-B (Variant):             Ready for execution
  L2-C Stock Crash:           Ready for execution
  L2-C (Variant):             Ready for execution
  Nested Transaction:         Ready for execution
  Complete Flow Crash:        Ready for execution

============================================================
"""
        )
