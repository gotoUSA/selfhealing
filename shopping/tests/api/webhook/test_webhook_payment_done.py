"""
결제 승인 웹훅 테스트 (PAYMENT.DONE)

토스페이먼츠 PAYMENT.DONE 이벤트 처리 및 중복 요청 방지 테스트
"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.cart import Cart
from shopping.models.payment import PaymentLog


@pytest.mark.django_db
class TestPaymentDoneWebhook:
    """결제 승인 웹훅 처리"""

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

    def test_payment_done_success(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """정상적인 결제 승인 처리"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
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
        assert self.payment.status == "done"
        assert self.payment.is_paid is True
        assert self.payment.payment_key == "test_payment_key_123"
        assert self.payment.method == "카드"

        # Assert - Order 상태 확인
        self.order.refresh_from_db()
        assert self.order.status == "paid"
        assert self.order.payment_method == "카드"

        # Assert - 재고 차감 확인 (order fixture: -1, webhook: -1 = total -2)
        self.product.refresh_from_db()
        assert self.product.stock == 8
        assert self.product.sold_count == 1

        # Assert - 포인트 적립 확인 (배송비 제외된 order.total_amount 기준, 1%)
        self.user.refresh_from_db()
        expected_points = int(self.order.total_amount * Decimal("0.01"))
        assert self.user.points == 5000 + expected_points

        # Assert - 장바구니 비활성화 확인
        active_carts = Cart.objects.filter(user=self.user, is_active=True)
        assert active_carts.count() == 0

        # Assert - PaymentLog 생성 확인
        log = PaymentLog.objects.filter(payment=self.payment, log_type="webhook").first()
        assert log is not None
        assert "결제 완료" in log.message

    def test_payment_done_with_multiple_items(
        self,
        mock_verify_webhook,
        webhook_data_builder,
        webhook_signature,
        order_with_multiple_items,
        multiple_products,
    ):
        """여러 상품이 포함된 주문의 결제 승인"""
        # Arrange
        from shopping.models.payment import Payment

        mock_verify_webhook()

        payment = Payment.objects.create(
            order=order_with_multiple_items,
            amount=order_with_multiple_items.total_amount,
            status="pending",
            toss_order_id=str(order_with_multiple_items.id),
        )

        initial_stocks = {p.id: p.stock for p in multiple_products}

        webhook_data = webhook_data_builder(
            order_id=str(order_with_multiple_items.id),
            payment_key="test_key_multi",
            amount=int(payment.amount),
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

        # Assert - 모든 상품의 재고 차감 확인
        for product in multiple_products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id] - 1
            assert product.sold_count == 1

    # ==========================================
    # 2단계: 경계값/중복 케이스 (Boundary)
    # ==========================================

    def test_payment_done_duplicate_request(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """중복 웹훅 요청 - 재고 중복 차감 방지"""
        # Arrange
        mock_verify_webhook()

        # Payment를 이미 완료 상태로 설정
        self.payment.status = "done"
        self.payment.payment_key = "already_processed"
        self.payment.save()

        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            payment_key="duplicate_key",
            amount=int(self.payment.amount),
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

        # Assert - 재고가 중복 차감되지 않았는지 확인
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_payment_done_order_already_paid(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """주문이 이미 paid 상태인 경우"""
        # Arrange
        mock_verify_webhook()

        # Order를 이미 paid 상태로 설정
        self.order.status = "paid"
        self.order.save()

        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
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
        assert self.payment.status == "done"

        # Assert - 재고는 중복 차감되지 않아야 함
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_payment_done_insufficient_stock(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """재고 부족 시나리오 - 로그만 남기고 계속 진행"""
        # Arrange
        mock_verify_webhook()

        # 재고를 0으로 설정
        self.product.stock = 0
        self.product.save()

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 웹훅은 성공 응답
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment와 Order 상태는 업데이트됨
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "done"
        assert self.order.status == "paid"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_payment_done_user_none(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """user가 None인 경우 포인트 적립 스킵"""
        # Arrange
        mock_verify_webhook()

        # Order의 user를 None으로 설정
        self.order.user = None
        self.order.save()

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
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
        assert self.payment.status == "done"

    def test_payment_done_payment_not_found(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """Payment가 존재하지 않는 경우"""
        # Arrange
        mock_verify_webhook()

        webhook_data = webhook_data_builder(
            order_id="nonexistent_order_123",
            amount=10000,
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
