"""ReturnService 단위 테스트"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.models.return_request import Return, ReturnItem
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


@pytest.mark.django_db
class TestGenerateReturnNumber:
    """교환/환불 번호 생성 테스트"""

    def test_generate_return_number_format(self):
        """번호 형식 검증 (RET + YYYYMMDD + 001)"""
        # Act
        return_number = ReturnService.generate_return_number()

        # Assert
        today = timezone.now().strftime("%Y%m%d")
        assert return_number.startswith(f"RET{today}")
        assert len(return_number) == 14  # RET(3) + YYYYMMDD(8) + 001(3)

    def test_generate_return_number_sequential(self):
        """순차 증가 검증"""
        # Arrange - DB에 Return 객체를 생성해야 generate_return_number가 올바르게 작동
        today = timezone.now().strftime("%Y%m%d")
        number1 = ReturnService.generate_return_number()
        ReturnFactory(return_number=number1)  # DB에 저장

        # Act
        number2 = ReturnService.generate_return_number()

        # Assert
        assert int(number2[-3:]) == int(number1[-3:]) + 1

    def test_generate_return_number_with_existing_returns(self):
        """기존 번호가 있을 때 다음 번호 생성"""
        # Arrange
        today = timezone.now().strftime("%Y%m%d")
        existing_number = f"RET{today}005"
        ReturnFactory(return_number=existing_number)

        # Act
        new_number = ReturnService.generate_return_number()

        # Assert
        assert new_number == f"RET{today}006"


@pytest.mark.django_db
class TestCalculateRefundAmount:
    """환불 금액 계산 테스트"""

    def test_calculate_refund_amount_single_item(self):
        """단일 상품 환불 금액 계산"""
        # Arrange
        return_obj = ReturnFactory.with_items()
        return_items = return_obj.return_items.all()

        # Act
        amount = ReturnService.calculate_refund_amount(return_items)

        # Assert
        expected = return_items[0].product_price * return_items[0].quantity
        assert amount == expected

    def test_calculate_refund_amount_multiple_items(self):
        """여러 상품 환불 금액 계산"""
        # Arrange
        order = OrderFactory.delivered()
        order_item1 = OrderItemFactory(order=order, price=Decimal("10000"), quantity=2)
        order_item2 = OrderItemFactory(order=order, price=Decimal("20000"), quantity=1)

        return_obj = ReturnFactory(order=order)
        ReturnItemFactory(return_request=return_obj, order_item=order_item1, quantity=2)
        ReturnItemFactory(return_request=return_obj, order_item=order_item2, quantity=1)

        return_items = return_obj.return_items.all()

        # Act
        amount = ReturnService.calculate_refund_amount(return_items)

        # Assert
        assert amount == Decimal("40000")  # 10000*2 + 20000*1

    def test_calculate_refund_amount_empty_list(self):
        """빈 리스트 환불 금액 (0원)"""
        # Act
        amount = ReturnService.calculate_refund_amount([])

        # Assert
        assert amount == Decimal("0")


@pytest.mark.django_db
class TestCreateReturn:
    """교환/환불 신청 생성 테스트"""

    def test_create_refund_success(self):
        """환불 신청 생성 성공"""
        # Arrange
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order, price=Decimal("10000"), quantity=2)
        user = order.user

        return_items_data = [
            {
                "order_item": order_item,
                "quantity": 2,
                "product_name": order_item.product_name,
                "product_price": order_item.price,
            }
        ]

        # Act
        return_obj = ReturnService.create_return(
            order=order,
            user=user,
            type="refund",
            reason="change_of_mind",
            reason_detail="단순 변심",
            return_items_data=return_items_data,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )

        # Assert
        assert return_obj is not None
        assert return_obj.order == order
        assert return_obj.user == user
        assert return_obj.type == "refund"
        assert return_obj.status == "requested"
        assert return_obj.return_number is not None
        assert return_obj.refund_amount == Decimal("20000")  # 10000 * 2
        assert return_obj.return_items.count() == 1

    def test_create_exchange_success(self):
        """교환 신청 생성 성공"""
        # Arrange
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order)
        exchange_product = ProductFactory()
        user = order.user

        return_items_data = [
            {
                "order_item": order_item,
                "quantity": 1,
                "product_name": order_item.product_name,
                "product_price": order_item.price,
            }
        ]

        # Act
        return_obj = ReturnService.create_return(
            order=order,
            user=user,
            type="exchange",
            reason="defective",
            reason_detail="상품 불량",
            return_items_data=return_items_data,
            exchange_product=exchange_product,
        )

        # Assert
        assert return_obj.type == "exchange"
        assert return_obj.exchange_product == exchange_product
        assert return_obj.refund_amount == Decimal("0")  # 교환은 환불 금액 없음

    def test_create_return_with_multiple_items(self):
        """여러 상품 반품 신청"""
        # Arrange
        order = OrderFactory.delivered()
        order_item1 = OrderItemFactory(order=order, price=Decimal("10000"))
        order_item2 = OrderItemFactory(order=order, price=Decimal("20000"))
        user = order.user

        return_items_data = [
            {
                "order_item": order_item1,
                "quantity": 1,
                "product_name": order_item1.product_name,
                "product_price": order_item1.price,
            },
            {
                "order_item": order_item2,
                "quantity": 1,
                "product_name": order_item2.product_name,
                "product_price": order_item2.price,
            },
        ]

        # Act
        return_obj = ReturnService.create_return(
            order=order,
            user=user,
            type="refund",
            reason="change_of_mind",
            reason_detail="단순 변심",
            return_items_data=return_items_data,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )

        # Assert
        assert return_obj.return_items.count() == 2
        assert return_obj.refund_amount == Decimal("30000")

    def test_create_return_generates_unique_number(self):
        """교환/환불 번호 자동 생성 및 고유성"""
        # Arrange
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order)
        user = order.user

        return_items_data = [
            {
                "order_item": order_item,
                "quantity": 1,
                "product_name": order_item.product_name,
                "product_price": order_item.price,
            }
        ]

        # Act
        return1 = ReturnService.create_return(
            order=order,
            user=user,
            type="refund",
            reason="change_of_mind",
            reason_detail="테스트",
            return_items_data=return_items_data,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )

        return2 = ReturnService.create_return(
            order=order,
            user=user,
            type="refund",
            reason="change_of_mind",
            reason_detail="테스트",
            return_items_data=return_items_data,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )

        # Assert
        assert return1.return_number != return2.return_number

    def test_create_return_logging(self, caplog):
        """교환/환불 신청 생성 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.return_service")

        # Arrange
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order)
        user = order.user

        return_items_data = [
            {
                "order_item": order_item,
                "quantity": 1,
                "product_name": order_item.product_name,
                "product_price": order_item.price,
            }
        ]

        # Act
        return_obj = ReturnService.create_return(
            order=order,
            user=user,
            type="refund",
            reason="change_of_mind",
            reason_detail="테스트",
            return_items_data=return_items_data,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("교환/환불 신청 생성" in msg for msg in log_messages)
        assert any(f"return_number={return_obj.return_number}" in msg for msg in log_messages)


@pytest.mark.django_db
class TestApproveReturn:
    """교환/환불 승인 테스트"""

    def test_approve_return_success(self):
        """승인 성공"""
        # Arrange
        return_obj = ReturnFactory.requested()

        # Act
        result = ReturnService.approve_return(return_obj)

        # Assert
        assert result.status == "approved"
        assert result.approved_at is not None

    def test_approve_return_with_memo(self):
        """관리자 메모 포함 승인"""
        # Arrange
        return_obj = ReturnFactory.requested()
        admin_memo = "승인 처리합니다"

        # Act
        result = ReturnService.approve_return(return_obj, admin_memo=admin_memo)

        # Assert
        assert result.admin_memo == admin_memo

    def test_approve_return_notification_sent(self):
        """승인 시 알림 발송 확인"""
        # Arrange
        return_obj = ReturnFactory.requested()

        # Act
        ReturnService.approve_return(return_obj)

        # Assert
        from shopping.models import Notification

        assert Notification.objects.filter(user=return_obj.user, notification_type="return").exists()

    def test_approve_return_invalid_status(self):
        """잘못된 상태에서 승인 시도 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.approved()  # 이미 승인됨

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.approve_return(return_obj)

        assert "신청 상태에서만 승인할 수 있습니다" in str(exc_info.value)

    def test_approve_return_logging(self, caplog):
        """승인 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.return_service")

        # Arrange
        return_obj = ReturnFactory.requested()

        # Act
        ReturnService.approve_return(return_obj)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("교환/환불 승인" in msg for msg in log_messages)


@pytest.mark.django_db
class TestRejectReturn:
    """교환/환불 거부 테스트"""

    def test_reject_return_success(self):
        """거부 성공"""
        # Arrange
        return_obj = ReturnFactory.requested()
        reason = "상품 하자가 아님"

        # Act
        result = ReturnService.reject_return(return_obj, reason=reason)

        # Assert
        assert result.status == "rejected"
        assert result.rejected_reason == reason

    def test_reject_return_notification_sent(self):
        """거부 시 알림 발송 확인"""
        # Arrange
        return_obj = ReturnFactory.requested()

        # Act
        ReturnService.reject_return(return_obj, reason="테스트")

        # Assert
        from shopping.models import Notification

        assert Notification.objects.filter(user=return_obj.user, notification_type="return").exists()

    def test_reject_return_invalid_status(self):
        """잘못된 상태에서 거부 시도 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.approved()

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.reject_return(return_obj, reason="테스트")

        assert "신청 상태에서만 거부할 수 있습니다" in str(exc_info.value)


@pytest.mark.django_db
class TestConfirmReceiveReturn:
    """반품 도착 확인 테스트"""

    def test_confirm_receive_success(self):
        """수령 확인 성공"""
        # Arrange
        return_obj = ReturnFactory.shipping()

        # Act
        result = ReturnService.confirm_receive_return(return_obj)

        # Assert
        assert result.status == "received"

    def test_confirm_receive_notification_sent(self):
        """수령 확인 시 알림 발송 확인"""
        # Arrange
        return_obj = ReturnFactory.shipping()

        # Act
        ReturnService.confirm_receive_return(return_obj)

        # Assert
        from shopping.models import Notification

        assert Notification.objects.filter(user=return_obj.user, notification_type="return").exists()

    def test_confirm_receive_invalid_status(self):
        """잘못된 상태에서 수령 확인 시도 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.requested()

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.confirm_receive_return(return_obj)

        assert "배송 중 상태에서만 수령 확인할 수 있습니다" in str(exc_info.value)


@pytest.mark.django_db
class TestCompleteRefund:
    """환불 완료 테스트"""

    def test_complete_refund_success(self):
        """환불 완료 성공"""
        # Arrange
        return_obj = ReturnFactory.received(type="refund")
        order = return_obj.order
        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient") as mock_toss:
            mock_instance = mock_toss.return_value
            mock_instance.cancel_payment.return_value = {
                "paymentKey": "test_key_123",
                "status": "CANCELED",
                "canceledAmount": int(return_obj.refund_amount),
            }

            # Act
            result = ReturnService.complete_refund(return_obj)

            # Assert
            assert result.status == "completed"
            assert result.completed_at is not None

    def test_complete_refund_stock_restored(self):
        """환불 완료 시 재고 복구 확인"""
        # Arrange
        product = ProductFactory(stock=10)
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order, product=product, quantity=2)

        return_obj = ReturnFactory.received(type="refund", order=order)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=2)
        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        initial_stock = product.stock

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient") as mock_toss:
            mock_instance = mock_toss.return_value
            mock_instance.cancel_payment.return_value = {
                "paymentKey": "test_key",
                "status": "CANCELED",
            }

            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            product.refresh_from_db()
            assert product.stock == initial_stock + 2

    def test_complete_refund_with_account(self):
        """계좌 환불 (복호화 테스트)"""
        # Arrange - 실제 환불 시나리오처럼 OrderItem과 ReturnItem 생성
        product = ProductFactory(stock=10)
        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order, product=product, price=Decimal("50000"), quantity=1)

        return_obj = ReturnFactory.received(
            type="refund",
            order=order,
            refund_account_bank="신한은행",
            refund_account_number="110-123-456789",
            refund_account_holder="홍길동",
        )
        # ReturnItem 생성 시 refund_amount가 자동 계산됨
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)

        # refund_amount 업데이트 (실제 create_return에서 하는 것처럼)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient") as mock_toss:
            mock_instance = mock_toss.return_value

            # Act
            ReturnService.complete_refund(return_obj)

            # Assert - 복호화된 계좌번호가 API에 전달되었는지 확인
            call_args = mock_instance.cancel_payment.call_args

            # cancel_payment가 호출되었는지 확인
            assert call_args is not None, "cancel_payment가 호출되지 않았습니다"

            refund_account = call_args.kwargs.get("refund_account")

            # 계좌 정보가 올바르게 전달되었는지 확인
            assert refund_account is not None, "refund_account가 전달되지 않았습니다"
            assert refund_account["bank"] == "신한은행"
            assert refund_account["holderName"] == "홍길동"
            # accountNumber는 복호화된 값이어야 함
            assert "accountNumber" in refund_account

    def test_complete_refund_notification_sent(self):
        """환불 완료 시 알림 발송 확인"""
        # Arrange
        return_obj = ReturnFactory.received(type="refund")
        PaymentFactory(order=return_obj.order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            from shopping.models import Notification

            assert Notification.objects.filter(user=return_obj.user, notification_type="return").exists()

    def test_complete_refund_zero_amount(self):
        """환불 금액 0원 (배송비만 차감)"""
        # Arrange
        return_obj = ReturnFactory.received(type="refund", refund_amount=Decimal("0"), return_shipping_fee=Decimal("3000"))
        PaymentFactory(order=return_obj.order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient") as mock_toss:
            mock_instance = mock_toss.return_value

            # Act
            ReturnService.complete_refund(return_obj)

            # Assert - 토스 API 호출되지 않아야 함 (환불 금액이 0원 이하)
            assert not mock_instance.cancel_payment.called

    def test_complete_refund_wrong_type(self):
        """교환 타입에서 환불 완료 호출 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.received(type="exchange")

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.complete_refund(return_obj)

        assert "환불 타입에서만 사용 가능합니다" in str(exc_info.value)

    def test_complete_refund_invalid_status(self):
        """잘못된 상태에서 환불 완료 시도 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.requested(type="refund")

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.complete_refund(return_obj)

        assert "반품 도착 상태에서만 환불 처리할 수 있습니다" in str(exc_info.value)

    def test_complete_refund_logging(self, caplog):
        """환불 완료 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.return_service")

        # Arrange
        return_obj = ReturnFactory.received(type="refund")
        PaymentFactory(order=return_obj.order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            log_messages = [record.message for record in caplog.records]
            assert any("환불 완료" in msg for msg in log_messages)

    def test_complete_refund_refunds_used_points(self):
        """환불 완료 시 사용한 포인트 환불 확인"""
        # Arrange
        product = ProductFactory(stock=10)
        user = UserFactory(points=3000)
        initial_points = user.points

        order = OrderFactory.delivered(user=user, used_points=2000)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            user.refresh_from_db()
            assert user.points == initial_points + 2000

            # 포인트 환불 이력 확인
            from shopping.models.point import PointHistory

            refund_history = PointHistory.objects.filter(
                user=user,
                type="cancel_refund",
                order=order,
            ).first()
            assert refund_history is not None
            assert refund_history.points == 2000

    def test_complete_refund_deducts_earned_points(self):
        """환불 완료 시 적립된 포인트 회수 확인"""
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 100
        user = UserFactory(points=5000 + earned_points)
        initial_points = user.points

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 적립 이력 생성 (FIFO 회수 대상)
        PointHistoryFactory(
            user=user,
            points=earned_points,
            balance=user.points,
            type="earn",
            order=order,
            description="결제 완료 적립",
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            user.refresh_from_db()
            assert user.points == initial_points - earned_points

            # 포인트 회수 이력 확인
            from shopping.models.point import PointHistory

            deduct_history = PointHistory.objects.filter(
                user=user,
                type="cancel_deduct",
                order=order,
            ).first()
            assert deduct_history is not None
            assert deduct_history.points == -earned_points

    def test_complete_refund_with_both_used_and_earned_points(self):
        """환불 시 사용 포인트 환불 + 적립 포인트 회수 동시 처리"""
        # Arrange
        product = ProductFactory(stock=10)
        used_points = 1000
        earned_points = 100
        user = UserFactory(points=2000 + earned_points)
        initial_points = user.points

        order = OrderFactory.delivered(user=user, used_points=used_points, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 적립 이력 생성
        PointHistoryFactory(
            user=user,
            points=earned_points,
            balance=user.points,
            type="earn",
            order=order,
            description="결제 완료 적립",
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert
            user.refresh_from_db()
            # 환불(+1000) - 회수(-100) = +900
            assert user.points == initial_points + used_points - earned_points

            from shopping.models.point import PointHistory

            # 환불 이력 확인
            assert PointHistory.objects.filter(user=user, type="cancel_refund", order=order).exists()

            # 회수 이력 확인
            assert PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).exists()

    def test_complete_refund_fails_with_insufficient_points_to_deduct(self):
        """적립 포인트 회수할 잔액 부족 시 환불 실패"""
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 500
        user = UserFactory(points=100)  # 회수해야 할 500P보다 적음
        initial_points = user.points
        initial_stock = product.stock

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 적립 이력 생성
        PointHistoryFactory(
            user=user,
            points=earned_points,
            balance=600,
            type="earn",
            order=order,
            description="결제 완료 적립",
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act & Assert
            with pytest.raises(ValueError) as exc_info:
                ReturnService.complete_refund(return_obj)

            assert "포인트가 부족" in str(exc_info.value)

            # 롤백 확인: 상태, 포인트 모두 원래대로
            return_obj.refresh_from_db()
            assert return_obj.status == "received"

            user.refresh_from_db()
            assert user.points == initial_points

            product.refresh_from_db()
            assert product.stock == initial_stock


@pytest.mark.django_db
class TestCompleteExchange:
    """교환 완료 테스트"""

    def test_complete_exchange_success(self):
        """교환 완료 성공"""
        # Arrange
        exchange_product = ProductFactory(stock=10)
        return_obj = ReturnFactory.received(type="exchange", exchange_product=exchange_product)

        # Act
        result = ReturnService.complete_exchange(
            return_obj,
            exchange_tracking_number="987654321098",
            exchange_shipping_company="CJ대한통운",
        )

        # Assert
        assert result.status == "completed"
        assert result.completed_at is not None
        assert result.exchange_tracking_number == "987654321098"
        assert result.exchange_shipping_company == "CJ대한통운"

    def test_complete_exchange_stock_adjusted(self):
        """교환 완료 시 재고 조정 확인 (반품 +1, 교환 -1)"""
        # Arrange
        original_product = ProductFactory(stock=10)
        exchange_product = ProductFactory(stock=5)

        order = OrderFactory.delivered()
        order_item = OrderItemFactory(order=order, product=original_product, quantity=1)

        return_obj = ReturnFactory.received(type="exchange", order=order, exchange_product=exchange_product)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)

        initial_original_stock = original_product.stock
        initial_exchange_stock = exchange_product.stock

        # Act
        ReturnService.complete_exchange(
            return_obj,
            exchange_tracking_number="987654321098",
            exchange_shipping_company="CJ대한통운",
        )

        # Assert
        original_product.refresh_from_db()
        exchange_product.refresh_from_db()

        assert original_product.stock == initial_original_stock + 1  # 반품 재고 증가
        assert exchange_product.stock == initial_exchange_stock - 1  # 교환 재고 감소

    def test_complete_exchange_notification_sent(self):
        """교환 완료 시 알림 발송 확인"""
        # Arrange
        return_obj = ReturnFactory.received(type="exchange")

        # Act
        ReturnService.complete_exchange(
            return_obj,
            exchange_tracking_number="987654321098",
            exchange_shipping_company="CJ대한통운",
        )

        # Assert
        from shopping.models import Notification

        assert Notification.objects.filter(user=return_obj.user, notification_type="return").exists()

    def test_complete_exchange_wrong_type(self):
        """환불 타입에서 교환 완료 호출 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.received(type="refund")

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.complete_exchange(
                return_obj,
                exchange_tracking_number="987654321098",
                exchange_shipping_company="CJ대한통운",
            )

        assert "교환 타입에서만 사용 가능합니다" in str(exc_info.value)

    def test_complete_exchange_invalid_status(self):
        """잘못된 상태에서 교환 완료 시도 (ValueError)"""
        # Arrange
        return_obj = ReturnFactory.requested(type="exchange")

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            ReturnService.complete_exchange(
                return_obj,
                exchange_tracking_number="987654321098",
                exchange_shipping_company="CJ대한통운",
            )

        assert "반품 도착 상태에서만 교환 처리할 수 있습니다" in str(exc_info.value)


@pytest.mark.django_db
class TestCompleteRefundDuplicatePrevention:
    """
    중복 cancel_deduct 방지 테스트

    동일 주문에 대해 이미 cancel_deduct가 처리된 경우,
    중복으로 포인트를 회수하지 않아야 함
    """

    def test_complete_refund_skips_duplicate_cancel_deduct(self):
        """이미 cancel_deduct가 있으면 포인트 회수 스킵"""
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 100
        user = UserFactory(points=5000)  # cancel_deduct로 회수 후 잔액
        initial_points = user.points

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 이미 cancel_deduct 이력이 있는 상황 (이전에 처리됨)
        PointHistoryFactory(
            user=user,
            points=-earned_points,
            balance=user.points,
            type="cancel_deduct",
            order=order,
            description="주문 취소로 인한 적립 포인트 차감",
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        # Mock 토스 API
        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert - 포인트가 중복으로 차감되지 않음
            user.refresh_from_db()
            assert user.points == initial_points

            # cancel_deduct 이력이 1개만 있어야 함 (기존 것)
            cancel_deduct_count = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).count()
            assert cancel_deduct_count == 1

    def test_complete_refund_skips_duplicate_cancel_deduct_logging(self, caplog):
        """중복 cancel_deduct 스킵 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.return_service")

        # Arrange
        product = ProductFactory(stock=10)
        user = UserFactory(points=5000)
        order = OrderFactory.delivered(user=user, earned_points=100)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 이미 cancel_deduct 이력 존재
        PointHistoryFactory(
            user=user,
            points=-100,
            balance=user.points,
            type="cancel_deduct",
            order=order,
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = Decimal("10000")
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            ReturnService.complete_refund(return_obj)

            # Assert - 스킵 로그 확인
            log_messages = [record.message for record in caplog.records]
            assert any("이미 적립 포인트 회수 완료됨" in msg for msg in log_messages)


@pytest.mark.django_db
class TestCompleteRefundUsablePointsValidation:
    """
    원장(PointHistory) 기반 포인트 검증 테스트

    User.points (캐시)가 아닌 get_usable_points() 원장 기준으로 검증
    """

    def test_complete_refund_uses_usable_points_validation(self):
        """원장 기준 사용 가능 포인트로 검증"""
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 100
        user = UserFactory(points=5100)  # 캐시값

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 원장에 유효한 포인트 이력 생성 (FIFO 대상)
        PointHistoryFactory.earn(
            user=user,
            points=5000,
            balance=5000,
            order=None,
            expires_at=timezone.now() + timedelta(days=365),
        )
        PointHistoryFactory.earn(
            user=user,
            points=earned_points,
            balance=5100,
            order=order,
            expires_at=timezone.now() + timedelta(days=365),
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act
            result = ReturnService.complete_refund(return_obj)

            # Assert
            assert result.status == "completed"
            user.refresh_from_db()
            assert user.points == 5100 - earned_points  # 회수됨

    def test_complete_refund_fails_when_usable_points_insufficient(self):
        """
        원장 기준 사용 가능 포인트 부족 시 실패

        User.points는 충분하지만, 실제 원장에 유효한 포인트가 부족한 경우
        """
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 500
        user = UserFactory(points=1000)  # 캐시값은 충분

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 원장에는 만료된 포인트만 있음 (usable = 0)
        PointHistoryFactory.earn_expired(
            user=user,
            points=1000,
            balance=1000,
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act & Assert
            with pytest.raises(ValueError) as exc_info:
                ReturnService.complete_refund(return_obj)

            assert "유효한 포인트가 부족합니다" in str(exc_info.value)
            assert f"필요: {earned_points}P" in str(exc_info.value)

    def test_complete_refund_validates_usable_not_cached_points(self):
        """
        캐시(User.points)와 원장(usable_points) 불일치 시 원장 기준 검증

        시나리오:
        - User.points = 200 (관리자가 직접 수정)
        - 원장에는 10P만 유효 (이미 cancel_deduct로 회수됨)
        - earned_points = 110
        - 결과: 실패 (10P < 110P)
        """
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 110
        user = UserFactory(points=200)  # 캐시값은 충분해 보임

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # 원장: 110P 적립 후 100P 사용됨 → 남은 10P
        earn_history = PointHistoryFactory.earn(
            user=user,
            points=110,
            balance=110,
            order=order,
            expires_at=timezone.now() + timedelta(days=365),
        )
        earn_history.metadata = {"used_amount": 100}
        earn_history.save()

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act & Assert
            with pytest.raises(ValueError) as exc_info:
                ReturnService.complete_refund(return_obj)

            # 원장 기준 10P만 사용 가능하므로 실패
            assert "유효한 포인트가 부족합니다" in str(exc_info.value)
            assert "사용 가능: 10P" in str(exc_info.value)


@pytest.mark.django_db
class TestCompleteRefundIdempotency:
    """
    환불 완료 멱등성 테스트

    동일한 Return에 대해 complete_refund를 여러 번 호출해도
    안전하게 처리되어야 함
    """

    def test_complete_refund_idempotent_second_call_fails(self):
        """
        이미 완료된 Return에 대해 다시 호출 시 실패

        첫 번째 호출: 성공 (status → completed)
        두 번째 호출: ValueError (status != received)
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
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act - 첫 번째 호출 (성공)
            result = ReturnService.complete_refund(return_obj)
            assert result.status == "completed"

            # Act - 두 번째 호출 (실패 예상)
            return_obj.refresh_from_db()
            with pytest.raises(ValueError) as exc_info:
                ReturnService.complete_refund(return_obj)

            assert "반품 도착 상태에서만 환불 처리할 수 있습니다" in str(exc_info.value)

    def test_complete_refund_points_not_double_deducted(self):
        """
        멱등성: 포인트가 중복 차감되지 않음

        시나리오:
        1. 첫 번째 complete_refund 호출 → 포인트 차감
        2. (어떤 이유로) return 상태가 다시 received로 변경됨
        3. 두 번째 complete_refund 호출 → 포인트 중복 차감 안 됨
        """
        # Arrange
        product = ProductFactory(stock=10)
        earned_points = 100
        user = UserFactory(points=5000)
        initial_points = user.points

        order = OrderFactory.delivered(user=user, earned_points=earned_points)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        PointHistoryFactory.earn(
            user=user,
            points=earned_points,
            balance=initial_points,
            order=order,
            expires_at=timezone.now() + timedelta(days=365),
        )

        return_obj = ReturnFactory.received(type="refund", order=order, user=user)
        ReturnItemFactory(return_request=return_obj, order_item=order_item, quantity=1)
        return_obj.refund_amount = ReturnService.calculate_refund_amount(return_obj.return_items.all())
        return_obj.save()

        PaymentFactory(order=order, status="done", payment_key="test_key_123")

        with patch("shopping.utils.toss_payment.TossPaymentClient"):
            # Act - 첫 번째 호출
            ReturnService.complete_refund(return_obj)

            user.refresh_from_db()
            points_after_first = user.points
            assert points_after_first == initial_points - earned_points

            # 강제로 상태 변경 (비정상 시나리오 시뮬레이션)
            return_obj.status = "received"
            return_obj.save()

            # Act - 두 번째 호출
            ReturnService.complete_refund(return_obj)

            # Assert - 포인트가 중복 차감되지 않음
            user.refresh_from_db()
            assert user.points == points_after_first

            # cancel_deduct 이력은 1개만
            cancel_count = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).count()
            assert cancel_count == 1
