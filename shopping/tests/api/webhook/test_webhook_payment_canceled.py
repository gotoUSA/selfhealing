"""
결제 취소 웹훅 테스트 (PAYMENT.CANCELED)

토스페이먼츠 PAYMENT.CANCELED 이벤트 처리 및 중복 요청 방지 테스트
"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.payment import PaymentLog


@pytest.mark.django_db
class TestPaymentCanceledWebhook:
    """결제 취소 웹훅 처리"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_payment_canceled_from_paid_order(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """paid 상태 주문의 정상적인 결제 취소"""
        # Arrange
        mock_verify_webhook()

        # Order와 Payment를 paid 상태로 설정
        self.order.status = "paid"
        self.order.save()

        self.payment.status = "done"
        self.payment.payment_key = "test_payment_key_123"
        self.payment.save()

        # 재고 차감 시뮬레이션 (결제 완료 시 차감됨)
        initial_stock = self.product.stock
        self.product.stock = initial_stock - 1
        self.product.sold_count = 1
        self.product.save()

        # 포인트 적립 시뮬레이션 (배송비 제외된 total_amount 기준)
        initial_points = self.user.points
        earned_points = int(self.order.total_amount * Decimal("0.01"))
        self.user.points = initial_points + earned_points
        self.user.save()

        # order.earned_points도 설정 (회수 시 이 값 사용)
        self.order.earned_points = earned_points
        self.order.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            payment_key="test_payment_key_123",
            cancel_reason="사용자 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

        # Assert - Payment 상태 확인
        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"
        assert self.payment.is_canceled is True
        assert self.payment.cancel_reason == "사용자 요청"
        assert self.payment.canceled_at is not None

        # Assert - Order 상태 확인
        self.order.refresh_from_db()
        assert self.order.status == "canceled"

        # Assert - 재고 복구 확인
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock
        assert self.product.sold_count == 0

        # Assert - 포인트 회수 확인
        self.user.refresh_from_db()
        assert self.user.points == initial_points

        # Assert - PaymentLog 생성 확인
        log = PaymentLog.objects.filter(payment=self.payment, log_type="webhook").first()
        assert log is not None
        assert "취소" in log.message

    def test_payment_canceled_from_preparing_order(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """preparing 상태에서 취소 - 재고 복구 및 포인트 회수"""
        # Arrange
        mock_verify_webhook()

        # Order를 preparing 상태로 설정
        self.order.status = "preparing"
        self.order.save()

        self.payment.status = "done"
        self.payment.payment_key = "test_payment_key_456"
        self.payment.save()

        # 재고 차감 시뮬레이션
        initial_stock = self.product.stock
        self.product.stock = initial_stock - 1
        self.product.sold_count = 1
        self.product.save()

        # 포인트 적립 시뮬레이션 (배송비 제외된 total_amount 기준)
        initial_points = self.user.points
        earned_points = int(self.order.total_amount * Decimal("0.01"))
        self.user.points = initial_points + earned_points
        self.user.save()

        # order.earned_points도 설정 (회수 시 이 값 사용)
        self.order.earned_points = earned_points
        self.order.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="상품 품절",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Order 상태 확인
        self.order.refresh_from_db()
        assert self.order.status == "canceled"

        # Assert - 재고 복구 확인 (preparing도 복구 대상)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock
        assert self.product.sold_count == 0

        # Assert - 포인트 회수 확인
        self.user.refresh_from_db()
        assert self.user.points == initial_points

    def test_payment_canceled_with_multiple_items(
        self,
        mock_verify_webhook,
        webhook_data_builder,
        webhook_signature,
        order_with_multiple_items,
        multiple_products,
    ):
        """여러 상품이 포함된 주문의 취소"""
        # Arrange
        from shopping.models.payment import Payment

        mock_verify_webhook()

        # Order를 paid 상태로 설정
        order_with_multiple_items.status = "paid"
        order_with_multiple_items.save()

        payment = Payment.objects.create(
            order=order_with_multiple_items,
            amount=order_with_multiple_items.total_amount,
            status="done",
            toss_order_id=str(order_with_multiple_items.id),
            payment_key="test_key_multi",
        )

        # 재고 차감 시뮬레이션
        initial_stocks = {}
        for product in multiple_products:
            initial_stocks[product.id] = product.stock
            product.stock -= 1
            product.sold_count = 1
            product.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order_with_multiple_items.id),
            payment_key="test_key_multi",
            cancel_reason="사용자 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 모든 상품의 재고 복구 확인
        for product in multiple_products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id]
            assert product.sold_count == 0

    # ==========================================
    # 2단계: 경계값/중복 케이스 (Boundary)
    # ==========================================

    def test_payment_canceled_duplicate_request(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """중복 웹훅 요청 - 재고 중복 복구 방지"""
        # Arrange
        mock_verify_webhook()

        # Payment를 이미 취소 상태로 설정
        self.payment.status = "canceled"
        self.payment.is_canceled = True
        self.payment.payment_key = "already_canceled"
        self.payment.save()

        # Order도 이미 취소 상태
        self.order.status = "canceled"
        self.order.save()

        initial_stock = self.product.stock
        initial_sold_count = self.product.sold_count

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            payment_key="duplicate_key",
            cancel_reason="중복 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고가 중복 복구되지 않았는지 확인
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock
        assert self.product.sold_count == initial_sold_count

    def test_payment_canceled_order_already_canceled(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """주문이 이미 canceled 상태인 경우"""
        # Arrange
        mock_verify_webhook()

        # Order를 이미 canceled 상태로 설정
        self.order.status = "canceled"
        self.order.save()

        # Payment는 아직 done 상태 (Order만 먼저 취소된 경우)
        self.payment.status = "done"
        self.payment.payment_key = "test_key"
        self.payment.save()

        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="주문 취소",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 업데이트되어야 함
        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"
        assert self.payment.is_canceled is True

        # Assert - 재고는 중복 복구되지 않아야 함
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_payment_canceled_from_pending_order(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """pending 상태에서 취소 - 재고 차감 전이므로 복구 불필요"""
        # Arrange
        mock_verify_webhook()

        # Order는 pending 상태 (결제 대기)
        self.order.status = "pending"
        self.order.save()

        self.payment.status = "ready"
        self.payment.save()

        initial_stock = self.product.stock
        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="결제 취소",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Order 상태는 변경됨
        self.order.refresh_from_db()
        assert self.order.status == "canceled"

        # Assert - 재고는 변경 없음 (pending에서는 재고 복구 안 함)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

        # Assert - 포인트는 회수 안 함 (적립된 적 없음)
        self.user.refresh_from_db()
        assert self.user.points == initial_points

    def test_payment_canceled_from_shipped_order(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """shipped 상태에서 취소 - 재고 복구 및 포인트 회수 안 함"""
        # Arrange
        mock_verify_webhook()

        # Order를 shipped 상태로 설정
        self.order.status = "shipped"
        self.order.save()

        self.payment.status = "done"
        self.payment.payment_key = "test_key_shipped"
        self.payment.save()

        # 재고 차감 시뮬레이션
        initial_stock = self.product.stock
        self.product.stock = initial_stock - 1
        self.product.sold_count = 1
        self.product.save()

        # 포인트 적립 시뮬레이션 (배송비 제외된 total_amount 기준)
        initial_points = self.user.points
        earned_points = int(self.order.total_amount * Decimal("0.01"))
        self.user.points = initial_points + earned_points
        self.user.save()

        # order.earned_points도 설정 (회수 시 이 값 사용)
        self.order.earned_points = earned_points
        self.order.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="환불 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Order 상태는 변경됨
        self.order.refresh_from_db()
        assert self.order.status == "canceled"

        # Assert - 재고는 복구 안 됨 (shipped는 복구 대상 아님)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock - 1
        assert self.product.sold_count == 1

        # Assert - 포인트도 회수 안 됨
        self.user.refresh_from_db()
        assert self.user.points == initial_points + earned_points

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_payment_canceled_user_none(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """user가 None인 경우 포인트 회수 스킵"""
        # Arrange
        mock_verify_webhook()

        # Order를 paid 상태로 설정
        self.order.status = "paid"
        self.order.user = None
        self.order.save()

        self.payment.status = "done"
        self.payment.payment_key = "test_key_no_user"
        self.payment.save()

        # 재고 차감 시뮬레이션
        initial_stock = self.product.stock
        self.product.stock = initial_stock - 1
        self.product.sold_count = 1
        self.product.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="사용자 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 에러 없이 처리되어야 함
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment 상태는 업데이트됨
        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"

        # Assert - 재고는 복구됨
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_payment_canceled_payment_not_found(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """Payment가 존재하지 않는 경우"""
        # Arrange
        mock_verify_webhook()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id="nonexistent_order_123",
            cancel_reason="존재하지 않는 주문",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 웹훅은 성공 응답 (로그만 남기고 처리)
        assert response.status_code == status.HTTP_200_OK

    def test_payment_canceled_insufficient_points_to_deduct(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature
    ):
        """포인트 회수 시 사용자 포인트 부족 - 웹훅은 성공 처리"""
        # Arrange
        mock_verify_webhook()

        # Order를 paid 상태로 설정
        self.order.status = "paid"
        self.order.save()

        self.payment.status = "done"
        self.payment.payment_key = "test_key_no_points"
        self.payment.save()

        # 재고 차감 시뮬레이션
        initial_stock = self.product.stock
        self.product.stock = initial_stock - 1
        self.product.sold_count = 1
        self.product.save()

        # 포인트 부족 시뮬레이션 (배송비 제외된 total_amount 기준으로 적립된 포인트)
        earned_points = int(self.order.total_amount * Decimal("0.01"))
        self.user.points = earned_points - 50  # 부족한 상태
        self.user.save()

        # order.earned_points도 설정 (회수 시 이 값 사용)
        self.order.earned_points = earned_points
        self.order.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(self.order.id),
            cancel_reason="사용자 요청",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 웹훅은 성공 처리 (포인트 회수 실패해도 OK)
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment와 Order 상태는 정상 업데이트
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "canceled"
        assert self.order.status == "canceled"

        # Assert - 재고는 정상 복구
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock
