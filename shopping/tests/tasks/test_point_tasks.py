"""
포인트 태스크 테스트

리팩토링 노트:
- 내부 서비스 mock 제거 → 실제 서비스 실행
- 외부 의존성(send_mail)만 mock 유지
- 실제 DB 상태 변화로 검증
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.tasks.point_tasks import (
    add_points_after_payment,
    expire_points_task,
    send_email_notification,
    send_expiry_notification_task,
)
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    PointHistoryFactory,
    ProductFactory,
    UserFactory,
)


@pytest.mark.django_db(transaction=True)
class TestExpirePointsTask:
    """포인트 만료 태스크 테스트 - 실제 서비스 실행"""

    def test_expire_points_success(self):
        """만료된 포인트가 실제로 만료 처리됨"""
        # Arrange
        user = UserFactory.with_points(1000)
        # 어제 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        result = expire_points_task()

        # Assert - 실제 DB 상태 검증
        assert result["status"] == "success"
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True

    def test_no_expired_points(self):
        """만료된 포인트가 없을 때"""
        # Arrange
        user = UserFactory.with_points(1000)
        # 아직 만료되지 않은 포인트
        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act
        result = expire_points_task()

        # Assert
        assert result["status"] == "success"


@pytest.mark.django_db(transaction=True)
class TestSendExpiryNotificationTask:
    """만료 알림 태스크 테스트 - 외부 이메일만 mock"""

    def test_sends_notification_successfully(self, mocker):
        """알림 발송이 성공적으로 완료됨 - DB 상태 검증"""
        # Arrange - 외부 이메일 발송만 mock
        mocker.patch("shopping.tasks.send_email_notification")

        user = UserFactory(email="test@example.com")
        point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        result = send_expiry_notification_task()

        # Assert - 실제 DB 상태 검증
        assert result["status"] == "success"
        point.refresh_from_db()
        assert point.metadata.get("expiry_notified") is True


@pytest.mark.django_db(transaction=True)
class TestSendEmailNotification:
    """이메일 발송 태스크 테스트 - 외부 의존성만 mock"""

    def test_sends_email_successfully(self, mocker):
        """이메일이 성공적으로 발송됨"""
        # Arrange - 외부 이메일 발송만 mock
        mock_send_mail = mocker.patch(
            "shopping.tasks.point_tasks.send_mail",
            return_value=1,
        )

        # Act
        result = send_email_notification(
            email="test@example.com",
            subject="테스트 제목",
            message="테스트 메시지",
            html_message="<p>테스트</p>",
        )

        # Assert
        assert result is True
        mock_send_mail.assert_called_once()

    def test_retry_on_smtp_error(self, mocker):
        """SMTP 에러 발생 시 태스크가 재시도됨"""
        # Arrange
        mocker.patch(
            "shopping.tasks.point_tasks.send_mail",
            side_effect=Exception("SMTP connection failed"),
        )

        # Act & Assert
        with pytest.raises(Exception):
            send_email_notification.apply(
                args=("test@example.com", "제목", "메시지"),
                throw=True,
            )


@pytest.mark.django_db(transaction=True)
class TestAddPointsAfterPayment:
    """결제 후 포인트 적립 테스트 - 실제 서비스 실행"""

    def test_adds_points_for_paid_order(self):
        """결제 완료된 주문에 포인트가 실제로 적립됨"""
        # Arrange
        user = UserFactory.with_membership(level="silver")  # 2%
        initial_points = user.points
        product = ProductFactory()
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=Decimal("50000"),
            final_amount=Decimal("50000"),
        )
        OrderItemFactory(order=order, product=product)

        # Act - 실제 서비스 실행
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert - 실제 DB 상태 검증
        assert result["status"] == "success"
        assert result["points_added"] == 1000  # 50000 * 2% (silver)

        user.refresh_from_db()
        assert user.points == initial_points + 1000

        # PointHistory 생성 확인
        history = PointHistory.objects.filter(
            user=user, type="earn", description__contains="결제"
        ).first()
        assert history is not None
        assert history.points == 1000

    def test_creates_payment_log_when_payment_exists(self):
        """Payment가 존재할 때 PaymentLog가 실제로 생성됨"""
        # Arrange
        user = UserFactory.with_membership(level="bronze")  # 1%
        product = ProductFactory()
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=Decimal("10000"),
            final_amount=Decimal("10000"),
        )
        OrderItemFactory(order=order, product=product)
        PaymentFactory.done(order=order)

        # Act - 실제 서비스 실행
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert - 실제 DB 상태 검증
        assert result["status"] == "success"
        order.refresh_from_db()
        assert order.earned_points == 100  # 10000 * 1% (bronze)

        # PaymentLog 실제 생성 확인
        from shopping.models.payment import PaymentLog

        log = PaymentLog.objects.filter(payment=order.payment).first()
        assert log is not None
        assert "포인트" in log.message


@pytest.mark.django_db(transaction=True)
class TestAddPointsAfterPaymentBoundary:
    """결제 후 포인트 적립 경계 케이스"""

    def test_skips_when_full_point_payment(self):
        """포인트 전액 결제 시 적립을 스킵함"""
        # Arrange
        user = UserFactory.with_points(50000)
        product = ProductFactory()
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=Decimal("10000"),
            used_points=10000,
            final_amount=Decimal("0"),  # 포인트 전액 결제
        )
        OrderItemFactory(order=order, product=product)

        # Act
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert
        assert result["status"] == "skipped"
        assert "포인트 전액 결제" in result["message"]

    def test_skips_when_zero_points_to_earn(self):
        """적립 포인트가 0 이하일 때 스킵함"""
        # Arrange
        user = UserFactory.with_membership(level="bronze")  # 1%
        product = ProductFactory()
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=Decimal("50"),
            final_amount=Decimal("50"),  # 50 * 1% = 0.5 -> int 0
        )
        OrderItemFactory(order=order, product=product)

        # Act
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert
        assert result["status"] == "skipped"
        assert "적립할 포인트 없음" in result["message"]


@pytest.mark.django_db(transaction=True)
class TestAddPointsAfterPaymentException:
    """결제 후 포인트 적립 예외 케이스"""

    def test_returns_failed_when_user_not_found(self):
        """존재하지 않는 사용자 ID로 호출 시 실패 반환"""
        # Arrange
        product = ProductFactory()
        user = UserFactory()
        order = OrderFactory(user=user)
        OrderItemFactory(order=order, product=product)
        non_existent_user_id = 99999

        # Act
        result = add_points_after_payment(
            user_id=non_existent_user_id,
            order_id=order.id,
        )

        # Assert
        assert result["status"] == "failed"
        assert result["user_id"] == non_existent_user_id

    def test_returns_failed_when_order_not_found(self):
        """존재하지 않는 주문 ID로 호출 시 실패 반환"""
        # Arrange
        user = UserFactory()
        non_existent_order_id = 99999

        # Act
        result = add_points_after_payment(
            user_id=user.id,
            order_id=non_existent_order_id,
        )

        # Assert
        assert result["status"] == "failed"
        assert result["order_id"] == non_existent_order_id
