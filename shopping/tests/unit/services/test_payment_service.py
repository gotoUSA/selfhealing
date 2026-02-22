"""PaymentService 단위 테스트

Factory Builder 패턴을 사용한 테스트:
- Fixture 사용 최소화
- Factory Builder로 테스트 데이터 생성
- 성능 최적화 (불필요한 DB 쿼리 제거)
- 보안 고려 (테스트 격리, 민감 데이터 처리)
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from shopping.models.payment import Payment, PaymentLog
from shopping.services.order_service import OrderService
from shopping.services.payment_service import PaymentCancelError, PaymentConfirmError, PaymentService
from shopping.tests.factories import (
    CartFactory,
    CartItemFactory,
    OrderFactory,
    PaymentFactory,
    ProductFactory,
    ShippingDataBuilder,
    TossResponseBuilder,
    UserFactory,
)
from shopping.utils.toss_payment import TossPaymentError


@pytest.mark.django_db
class TestPaymentServiceCreatePayment:
    """결제 정보 생성 테스트"""

    def test_create_payment_success(self):
        """결제 정보 생성 성공"""
        # Arrange - Factory로 주문 생성
        order = OrderFactory.pending()

        # Act
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
        )

        # Assert
        assert payment is not None
        assert payment.order == order
        assert payment.amount == order.final_amount
        assert payment.method == "card"
        assert payment.status == "ready"
        assert payment.toss_order_id == str(order.id)  # Toss에 전송하는 orderId와 일치

        # PaymentLog 확인
        log = PaymentLog.objects.filter(payment=payment, log_type="request").first()
        assert log is not None
        assert log.message == "결제 요청 생성"

    def test_create_payment_with_existing_payment(self):
        """기존 결제 정보가 있을 때 새로 생성 (재시도)"""
        # Arrange - 주문과 기존 결제 생성
        order = OrderFactory.pending()
        existing_payment = PaymentService.create_payment(order, "card")
        existing_payment_id = existing_payment.id

        # Act - 동일 주문에 대해 재생성
        new_payment = PaymentService.create_payment(order, "card")

        # Assert
        assert Payment.objects.filter(order=order).count() == 1
        assert new_payment.id != existing_payment_id  # 새로운 객체
        assert not Payment.objects.filter(id=existing_payment_id).exists()  # 기존 것은 삭제됨

    def test_create_payment_logging(self, caplog):
        """결제 정보 생성 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.payment_service")

        # Arrange
        order = OrderFactory.pending()

        # Act
        payment = PaymentService.create_payment(order, "card")

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("결제 정보 생성 시작" in msg for msg in log_messages)
        assert any("결제 정보 생성 완료" in msg for msg in log_messages)
        assert any(f"payment_id={payment.id}" in msg for msg in log_messages)


@pytest.mark.django_db
class TestPaymentServiceConfirmPayment:
    """결제 승인 테스트"""

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_confirm_payment_success(self, mock_toss_client):
        """결제 승인 성공"""
        # Arrange - 주문과 결제 생성 (Factory 사용)
        user = UserFactory.with_membership("silver")  # 2% 적립률
        product = ProductFactory(price=Decimal("50000"), stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        # 주문 생성
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **ShippingDataBuilder.default(),
        )

        # 재고 및 포인트 초기값 저장
        product.refresh_from_db()
        stock_after_order = product.stock
        initial_sold_count = product.sold_count
        initial_points = user.points

        payment = PaymentService.create_payment(order, "card")

        # TossPaymentClient 모킹
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            method="카드",
        )

        # Act
        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # Assert
        payment.refresh_from_db()
        assert payment.status == "done"
        assert payment.payment_key == "test_payment_key_123"

        # 주문 상태 확인
        order.refresh_from_db()
        assert order.status == "paid"

        # sold_count 증가 확인 (재고는 주문 생성 시 이미 차감)
        product.refresh_from_db()
        assert product.stock == stock_after_order  # 재고는 변하지 않음
        assert product.sold_count == initial_sold_count + 1

        # 포인트 적립 확인 (silver: 2% 적립, 배송비 제외)
        user.refresh_from_db()
        # total_amount는 이미 순수 상품금액 (배송비 미포함)
        expected_points = initial_points + int(order.total_amount * Decimal("0.02"))
        assert user.points == expected_points
        assert result["points_earned"] > 0

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_confirm_payment_with_points_only(self, mock_toss_client):
        """포인트로만 결제한 경우 (final_amount = 0)"""
        # Arrange - 포인트 충분한 사용자
        user = UserFactory.with_high_points()  # 50,000 포인트
        product = ProductFactory(price=Decimal("50000"))
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        # 포인트로 전액 결제
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=50000,  # 주문 금액과 정확히 일치
            **ShippingDataBuilder.default(),
        )

        payment = PaymentService.create_payment(order, "card")

        # TossPaymentClient 모킹
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=0,  # 포인트로만 결제
            method="카드",
        )

        # Act
        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=0,
            user=user,
        )

        # Assert - 포인트 적립 없음
        assert result["points_earned"] == 0

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_confirm_payment_logging(self, mock_toss_client, caplog):
        """결제 승인 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.payment_service")

        # Arrange
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        # Act
        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("결제 승인 시작" in msg for msg in log_messages)
        assert any("토스페이먼츠 결제 승인 요청" in msg for msg in log_messages)
        assert any("토스페이먼츠 결제 승인 성공" in msg for msg in log_messages)
        assert any("판매량 증가" in msg for msg in log_messages)
        assert any("결제 승인 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestPaymentServiceConfirmPaymentValidation:
    """결제 승인 검증 테스트 (엣지 케이스)"""

    def test_confirm_payment_sync_already_paid(self):
        """이미 완료된 결제에 대해 승인 시도 (line 115)"""
        # Arrange - 이미 결제 완료된 Payment
        order = OrderFactory.paid()
        user = order.user
        payment = PaymentFactory.done(order=order)  # 이미 done 상태

        # Act & Assert
        with pytest.raises(PaymentConfirmError) as exc_info:
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.order_number,
                amount=int(order.final_amount),
                user=user,
            )

        assert "이미 완료된 결제입니다" in str(exc_info.value)

    @pytest.mark.parametrize(
        "status,factory_method",
        [
            ("expired", "create_expired"),
            ("canceled", "canceled"),
            ("aborted", "aborted"),
        ],
        ids=["expired", "canceled", "aborted"],
    )
    def test_confirm_payment_sync_invalid_status(self, status, factory_method):
        """유효하지 않은 결제 상태에 대해 승인 시도 (line 119)

        테스트 대상 상태: expired, canceled, aborted
        모두 동일한 에러 메시지 검증
        """
        # Arrange - 유효하지 않은 상태의 Payment
        order = OrderFactory.pending()
        user = order.user

        # Factory 메서드 호출 (expired는 직접 생성, 나머지는 팩토리 메서드)
        if status == "expired":
            payment = PaymentFactory(order=order, status="expired")
        else:
            payment = getattr(PaymentFactory, factory_method)(order=order)

        # Act & Assert
        with pytest.raises(PaymentConfirmError) as exc_info:
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.order_number,
                amount=int(order.final_amount),
                user=user,
            )

        assert "유효하지 않은 결제 상태입니다" in str(exc_info.value)


@pytest.mark.django_db
class TestPaymentServiceConfirmPaymentAsync:
    """비동기 결제 승인 테스트"""

    def test_confirm_payment_async_already_paid(self):
        """이미 완료된 결제에 대해 비동기 승인 시도 (line 277)"""
        # Arrange - 이미 결제 완료된 Payment
        order = OrderFactory.paid()
        user = order.user
        payment = PaymentFactory.done(order=order)

        # Act & Assert
        with pytest.raises(PaymentConfirmError) as exc_info:
            PaymentService.confirm_payment_async(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.final_amount),
                user=user,
            )

        assert "이미 완료된 결제입니다" in str(exc_info.value)

    def test_confirm_payment_async_already_in_progress(self):
        """이미 처리 중인 결제에 대해 비동기 승인 시도 (line 285)"""
        # Arrange - 이미 처리 중인 Payment
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentFactory(order=order, status="in_progress")

        # Act & Assert
        with pytest.raises(PaymentConfirmError) as exc_info:
            PaymentService.confirm_payment_async(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.final_amount),
                user=user,
            )

        assert "이미 처리 중인 결제입니다" in str(exc_info.value)

    def test_confirm_payment_async_invalid_status(self):
        """유효하지 않은 상태에서 비동기 승인 시도"""
        # Arrange - 만료된 Payment
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentFactory(order=order, status="expired")

        # Act & Assert
        with pytest.raises(PaymentConfirmError) as exc_info:
            PaymentService.confirm_payment_async(
                payment=payment,
                payment_key="test_payment_key",
                order_id=order.id,
                amount=int(order.final_amount),
                user=user,
            )

        assert "유효하지 않은 결제 상태입니다" in str(exc_info.value)

    @patch("shopping.tasks.payment_tasks.call_toss_confirm_api")
    @patch("shopping.tasks.payment_tasks.finalize_payment_confirm")
    def test_confirm_payment_async_eager_mode(self, mock_finalize, mock_toss_api):
        """CELERY_TASK_ALWAYS_EAGER=True 모드에서 비동기 결제 승인 (line 305-308)"""
        # Arrange
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        # Mock 설정
        mock_toss_api.return_value = {
            "paymentKey": "test_key",
            "orderId": str(order.id),
            "status": "DONE",
            "totalAmount": int(order.final_amount),
        }
        mock_finalize.return_value = {"success": True}

        # Act - CELERY_TASK_ALWAYS_EAGER가 True이면 동기 실행
        result = PaymentService.confirm_payment_async(
            payment=payment,
            payment_key="test_payment_key",
            order_id=order.id,
            amount=int(order.final_amount),
            user=user,
        )

        # Assert
        assert result["status"] == "processing"
        assert result["payment_id"] == payment.id
        # Eager 모드에서는 task_id가 "sync-execution"
        assert result["task_id"] == "sync-execution"
        mock_toss_api.assert_called_once()
        mock_finalize.assert_called_once()


@pytest.mark.django_db
class TestPaymentServiceCancelPayment:
    """결제 취소 테스트"""

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_success(self, mock_toss_client):
        """결제 취소 성공"""
        # Arrange - 결제 완료된 주문 생성
        user = UserFactory.with_membership("silver")
        product = ProductFactory(price=Decimal("50000"), stock=100)
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **ShippingDataBuilder.default(),
        )

        # 재고 초기값 저장
        product.refresh_from_db()
        stock_after_order = product.stock
        initial_sold_count = product.sold_count

        payment = PaymentService.create_payment(order, "card")

        # 먼저 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 결제 승인 후 sold_count 확인
        product.refresh_from_db()
        sold_count_after_confirm = product.sold_count
        order.refresh_from_db()
        earned_points = order.earned_points

        # 취소 API 모킹
        mock_instance.cancel_payment.return_value = TossResponseBuilder.cancel_response(
            payment_key="test_payment_key_123",
            cancel_reason="단순 변심",
        )

        # Act
        result = PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="단순 변심",
        )

        # Assert
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert payment.is_canceled

        # 주문 상태 확인
        order.refresh_from_db()
        assert order.status == "canceled"

        # 재고 복구 및 sold_count 차감 확인
        product.refresh_from_db()
        assert product.stock == stock_after_order + 1  # 주문 시 차감된 재고가 복구됨
        assert product.sold_count == sold_count_after_confirm - 1  # 승인 시 증가한 sold_count 차감

        # 적립 포인트 차감 확인
        assert result["points_deducted"] == earned_points

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_with_used_points(self, mock_toss_client):
        """포인트 사용한 결제 취소 (포인트 환불)"""
        # Arrange
        user = UserFactory.with_points(10000)
        product = ProductFactory(price=Decimal("50000"))
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        use_points = 5000
        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=use_points,
            **ShippingDataBuilder.default(),
        )

        payment = PaymentService.create_payment(order, "card")

        # 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 결제 승인 후 포인트 확인
        user.refresh_from_db()
        points_after_confirm = user.points
        order.refresh_from_db()
        earned_points = order.earned_points

        # 취소 API 모킹
        mock_instance.cancel_payment.return_value = TossResponseBuilder.cancel_response(
            payment_key="test_payment_key_123",
            cancel_reason="단순 변심",
        )

        # Act
        result = PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="단순 변심",
        )

        # Assert - 사용한 포인트 환불 확인
        assert result["points_refunded"] == use_points
        assert result["points_deducted"] == earned_points

        user.refresh_from_db()
        # points_after_confirm + 환불포인트 - 적립포인트차감
        expected_points = points_after_confirm + use_points - earned_points
        assert user.points == expected_points

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_already_canceled(self, mock_toss_client):
        """이미 취소된 결제 재취소 시도"""
        # Arrange - 결제 완료 후 취소된 상태
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        # 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 첫 번째 취소
        mock_instance.cancel_payment.return_value = TossResponseBuilder.cancel_response(
            payment_key="test_payment_key_123",
            cancel_reason="단순 변심",
        )

        PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="단순 변심",
        )

        # Act & Assert - 두 번째 취소 시도
        with pytest.raises(PaymentCancelError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        assert "이미 취소된 결제입니다" in str(exc_info.value)

    def test_cancel_payment_invalid_status(self):
        """취소 불가능한 상태의 결제 취소 시도"""
        # Arrange - ready 상태의 결제 (아직 승인되지 않음)
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        # Act & Assert
        with pytest.raises(PaymentCancelError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        assert "취소할 수 없는 결제 상태입니다" in str(exc_info.value)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_toss_error(self, mock_toss_client):
        """토스페이먼츠 API 에러 시 처리"""
        # Arrange - 결제 완료 상태
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        # 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 취소 시 에러 발생
        mock_instance.cancel_payment.side_effect = TossPaymentError(
            code="CANCEL_FAILED",
            message="취소 실패",
        )

        # Act & Assert
        with pytest.raises(PaymentCancelError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        assert "결제 취소 실패" in str(exc_info.value)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_insufficient_points(self, mock_toss_client):
        """포인트 부족으로 취소 불가 (line 464)"""
        # Arrange - 적립 포인트가 있는 결제 완료
        user = UserFactory.with_membership("silver")
        product = ProductFactory(price=Decimal("50000"))
        cart = CartFactory(user=user)
        CartItemFactory(cart=cart, product=product, quantity=1)

        order = OrderService.create_order_from_cart(
            user=user,
            cart=cart,
            use_points=0,
            **ShippingDataBuilder.default(),
        )

        payment = PaymentService.create_payment(order, "card")

        # 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 적립된 포인트 확인 후 사용자의 포인트를 0으로 만듦 (포인트 소진 시뮬레이션)
        order.refresh_from_db()
        earned_points = order.earned_points
        assert earned_points > 0

        # 포인트를 직접 0으로 설정 (다른 곳에서 사용했다고 가정)
        user.points = 0
        user.save()

        # 취소 API 모킹
        mock_instance.cancel_payment.return_value = TossResponseBuilder.cancel_response(
            payment_key="test_payment_key_123",
            cancel_reason="단순 변심",
        )

        # Act & Assert - 포인트 부족으로 취소 실패
        with pytest.raises(PaymentCancelError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        assert "포인트가 부족하여 결제를 취소할 수 없습니다" in str(exc_info.value)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_generic_exception(self, mock_toss_client):
        """취소 중 예상치 못한 오류 발생 (line 550-551)"""
        # Arrange - 결제 완료 상태
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        # 결제 승인
        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 취소 시 일반 예외 발생
        mock_instance.cancel_payment.side_effect = RuntimeError("예상치 못한 오류")

        # Act & Assert
        with pytest.raises(RuntimeError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        assert "예상치 못한 오류" in str(exc_info.value)

    def test_cancel_payment_not_found(self):
        """존재하지 않는 결제 취소 시도"""
        # Arrange
        user = UserFactory()

        # Act & Assert
        with pytest.raises(PaymentCancelError) as exc_info:
            PaymentService.cancel_payment(
                payment_id=999999,  # 존재하지 않는 ID
                user=user,
                cancel_reason="단순 변심",
            )

        assert "결제 정보를 찾을 수 없습니다" in str(exc_info.value)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_logging(self, mock_toss_client, caplog):
        """결제 취소 시 로깅 확인"""
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.payment_service")

        # Arrange - 결제 완료 상태
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        mock_instance.cancel_payment.return_value = TossResponseBuilder.cancel_response(
            payment_key="test_payment_key_123",
            cancel_reason="단순 변심",
        )

        # Act
        PaymentService.cancel_payment(
            payment_id=payment.id,
            user=user,
            cancel_reason="단순 변심",
        )

        # Assert - 로그 메시지 확인
        log_messages = [record.message for record in caplog.records]
        assert any("결제 취소 시작" in msg for msg in log_messages)
        assert any("결제 취소 검증 완료" in msg for msg in log_messages)
        assert any("토스페이먼츠 결제 취소 요청" in msg for msg in log_messages)
        assert any("토스페이먼츠 결제 취소 성공" in msg for msg in log_messages)
        assert any("재고 복구" in msg for msg in log_messages)
        assert any("결제 취소 완료" in msg for msg in log_messages)

    @patch("shopping.services.payment_service.TossPaymentClient")
    def test_cancel_payment_toss_error_logging(self, mock_toss_client, caplog):
        """토스 API 에러 시 로깅 확인 (line 527-528)"""
        import logging

        caplog.set_level(logging.ERROR, logger="shopping.services.payment_service")

        # Arrange - 결제 완료 상태
        order = OrderFactory.pending()
        user = order.user
        payment = PaymentService.create_payment(order, "card")

        mock_instance = mock_toss_client.return_value
        mock_instance.confirm_payment.return_value = TossResponseBuilder.success_response(
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
        )

        PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key_123",
            order_id=order.order_number,
            amount=int(order.final_amount),
            user=user,
        )

        # 취소 시 토스 에러 발생
        mock_instance.cancel_payment.side_effect = TossPaymentError(
            code="CANCEL_FAILED",
            message="취소 실패",
        )

        # Act
        with pytest.raises(PaymentCancelError):
            PaymentService.cancel_payment(
                payment_id=payment.id,
                user=user,
                cancel_reason="단순 변심",
            )

        # Assert - 에러 로그 확인
        log_messages = [record.message for record in caplog.records]
        assert any("토스페이먼츠 결제 취소 실패" in msg for msg in log_messages)
        assert any("CANCEL_FAILED" in msg for msg in log_messages)
