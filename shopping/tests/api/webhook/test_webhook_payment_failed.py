"""
결제 실패 웹훅 테스트 (PAYMENT.FAILED)

토스페이먼츠 PAYMENT.FAILED 이벤트 처리 및 중복 요청 방지 테스트
"""

import uuid

import pytest
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.tests.factories import OrderFactory, OrderItemFactory, PaymentFactory


@pytest.mark.django_db
class TestPaymentFailedWebhook:
    """결제 실패 웹훅 처리"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        # UUID로 완전히 고유한 order_number 생성 (병렬 테스트 완전 격리)
        unique_suffix = uuid.uuid4().hex[:8]
        from django.utils import timezone

        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_payment_failed_success(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """정상적인 결제 실패 처리"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="카드 한도 초과",
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
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == "카드 한도 초과"

        # Assert - Order 상태가 payment_failed로 변경됨 (롤백 태스크 실행)
        self.order.refresh_from_db()
        assert self.order.status == "payment_failed"

        # Assert - PaymentLog 생성 확인
        log = PaymentLog.objects.filter(payment=self.payment, log_type="webhook").first()
        assert log is not None
        assert "실패" in log.message
        assert "카드 한도 초과" in log.message

    # ==========================================
    # 2단계: 경계값/중복 케이스 (Boundary)
    # ==========================================

    def test_payment_failed_duplicate_request(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """중복 웹훅 요청 - 이미 실패 처리된 결제"""
        # Arrange
        mock_verify_webhook()

        # Payment를 이미 실패 상태로 설정
        self.payment.status = "aborted"
        self.payment.fail_reason = "이미 실패 처리됨"
        self.payment.save()

        initial_log_count = PaymentLog.objects.filter(payment=self.payment).count()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="중복 요청",
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

        # Assert - fail_reason이 변경되지 않음 (중복 처리 스킵)
        self.payment.refresh_from_db()
        assert self.payment.fail_reason == "이미 실패 처리됨"

        # Assert - 중복 로그 생성 안 됨
        assert PaymentLog.objects.filter(payment=self.payment).count() == initial_log_count

    def test_payment_failed_from_in_progress_status(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """in_progress 상태에서 결제 실패"""
        # Arrange
        mock_verify_webhook()

        # Payment를 in_progress 상태로 설정
        self.payment.status = "in_progress"
        self.payment.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="결제 진행 중 오류",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == "결제 진행 중 오류"

    def test_payment_failed_from_waiting_for_deposit(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """가상계좌 입금 대기 중 결제 실패"""
        # Arrange
        mock_verify_webhook()

        # Payment를 waiting_for_deposit 상태로 설정
        self.payment.status = "waiting_for_deposit"
        self.payment.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="가상계좌 발급 실패",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == "가상계좌 발급 실패"

    def test_payment_failed_triggers_rollback(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """결제 실패 시 Order 상태가 payment_failed로 변경되고 롤백 실행됨"""
        # Arrange
        mock_verify_webhook()

        # Order를 pending 상태로 설정
        self.order.status = "pending"
        self.order.save()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="결제 실패",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - Payment는 실패 처리됨
        assert response.status_code == status.HTTP_200_OK
        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"

        # Assert - Order 상태가 payment_failed로 변경됨 (롤백 태스크 실행)
        self.order.refresh_from_db()
        assert self.order.status == "payment_failed"

    def test_payment_failed_restores_stock(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """결제 실패 시 재고가 복구됨 (롤백 태스크 실행)"""
        # Arrange
        mock_verify_webhook()

        initial_stock = self.product.stock
        initial_sold_count = self.product.sold_count

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="결제 실패",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고가 복구됨 (롤백 태스크에서 stock 증가)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock + 1  # 롤백으로 재고 복구
        assert self.product.sold_count == initial_sold_count  # sold_count는 결제 전이므로 변경 없음

    def test_payment_failed_no_point_change(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """결제 실패 시 포인트는 변경되지 않음"""
        # Arrange
        mock_verify_webhook()

        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="결제 실패",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Assert - 포인트는 변경되지 않음 (적립 전 실패)
        self.user.refresh_from_db()
        assert self.user.points == initial_points

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_payment_failed_payment_not_found(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """Payment가 존재하지 않는 경우"""
        # Arrange
        mock_verify_webhook()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id="nonexistent_order_999",
            fail_reason="존재하지 않는 주문",
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

    def test_payment_failed_empty_fail_reason(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """빈 실패 사유로 처리"""
        # Arrange
        mock_verify_webhook()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason="",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 빈 사유도 정상 처리
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == ""

    def test_payment_failed_very_long_reason(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """매우 긴 실패 사유 처리"""
        # Arrange
        mock_verify_webhook()

        long_reason = "결제 실패: " + "매우 긴 오류 메시지입니다. " * 50  # 약 1000자

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason=long_reason,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 긴 사유도 정상 처리 (TextField는 제한 없음)
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == long_reason
        assert len(self.payment.fail_reason) > 500

    def test_payment_failed_special_characters_in_reason(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """특수문자가 포함된 실패 사유 처리"""
        # Arrange
        mock_verify_webhook()

        # XSS/SQL Injection 방지 확인
        special_reason = "<script>alert('xss')</script> 카드 오류 '; DROP TABLE payment; --"

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason=special_reason,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 특수문자도 안전하게 저장됨
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == special_reason

        # Assert - PaymentLog도 안전하게 저장됨
        log = PaymentLog.objects.filter(payment=self.payment).first()
        assert log is not None
        assert special_reason in log.message

    def test_payment_failed_unicode_characters_in_reason(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """유니코드 문자가 포함된 실패 사유 처리"""
        # Arrange
        mock_verify_webhook()

        unicode_reason = "결제 실패 🚫 カード エラー 💳 支付失败"

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason=unicode_reason,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == unicode_reason


@pytest.mark.django_db
class TestPaymentFailedVariousReasons:
    """다양한 실패 사유 테스트 (parametrize 격리)"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

        # UUID로 완전히 고유한 order_number 생성 (병렬 테스트 완전 격리)
        unique_suffix = uuid.uuid4().hex[:8]
        from django.utils import timezone

        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        self.order = OrderFactory(user=user, status="pending", order_number=order_number)
        OrderItemFactory(order=self.order, product=product)
        self.payment = PaymentFactory(order=self.order, status="ready")

    @pytest.mark.parametrize(
        "fail_reason",
        [
            "카드 한도 초과",
            "카드 인증 실패",
            "잔액 부족",
            "유효하지 않은 카드",
            "카드 정보 불일치",
            "거래 거절",
            "결제 시스템 오류",
        ],
    )
    def test_payment_failed_with_various_reasons(
        self, mock_verify_webhook, webhook_data_builder, webhook_signature, fail_reason
    ):
        """다양한 실패 사유 처리"""
        # Arrange
        mock_verify_webhook()
        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(self.order.id),
            fail_reason=fail_reason,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == fail_reason
