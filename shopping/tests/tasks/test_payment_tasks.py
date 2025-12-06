"""결제 비동기 태스크 테스트"""

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from shopping.models.payment import Payment
from shopping.tasks.payment_tasks import (
    call_toss_confirm_api,
    finalize_payment_confirm,
    rollback_payment_failure,
    detect_orphaned_orders,
    notify_payment_failure,
)
from shopping.tests.factories import (
    PaymentFactory,
    OrderFactory,
    ProductFactory,
    UserFactory,
    OrderItemFactory,
)


@pytest.mark.django_db(transaction=True)
class TestPaymentTasksHappyPath:
    """결제 태스크 정상 케이스"""

    @pytest.fixture(autouse=True)
    def force_eager_mode(self, settings):
        """이 클래스의 모든 테스트에서 Celery eager 모드 강제 활성화"""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        yield

    def test_call_toss_api_task_success(self, mocker):
        """Toss API 호출 태스크가 성공적으로 실행됨"""
        # Arrange
        mock_response = {
            "paymentKey": "test_key_123",
            "orderId": "ORDER_123",
            "status": "DONE",
        }
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=mock_response
        )

        # Act
        result = call_toss_confirm_api(
            payment_key="test_key_123",
            order_id="ORDER_123",
            amount=10000
        )

        # Assert
        assert result == mock_response
        assert result["status"] == "DONE"

    def test_finalize_payment_task_success(self, user_factory, product):
        """결제 최종 처리 태스크가 성공적으로 실행됨"""
        # Arrange
        from django.utils import timezone

        user = user_factory()
        order = OrderFactory(user=user, status="pending")
        payment = PaymentFactory(order=order, status="ready")

        # OrderItem 생성
        order_item = OrderItemFactory(order=order, product=product, quantity=2)

        toss_response = {
            "paymentKey": "test_key",
            "status": "DONE",
            "approvedAt": timezone.now().isoformat(),  # timezone-aware datetime
        }

        # Act
        result = finalize_payment_confirm(
            toss_response=toss_response,
            payment_id=payment.id,
            user_id=user.id
        )

        # Assert
        payment.refresh_from_db()
        assert payment.is_paid
        assert payment.order.status == "paid"
        assert result["status"] == "success"

    def test_payment_chain_integration(self, user_factory, mocker):
        """Toss API → 최종 처리 체인이 정상 작동"""
        # Arrange
        user = user_factory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)

        mock_toss_response = {"status": "DONE", "paymentKey": "key123"}
        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=mock_toss_response
        )

        # Act - eager 모드에서 직접 실행 (CI 환경 호환성)
        from shopping.tasks.payment_tasks import call_toss_confirm_api, finalize_payment_confirm

        # 첫 번째 태스크: Toss API 호출
        toss_result = call_toss_confirm_api("key123", "ORDER_123", 10000)

        # 두 번째 태스크: 결제 최종 처리 (toss_result를 첫 번째 인자로 전달)
        final_result = finalize_payment_confirm(toss_result, payment.id, user.id)

        # Assert
        payment.refresh_from_db()
        assert payment.is_paid
        assert final_result["status"] == "success"


@pytest.mark.django_db(transaction=True)
class TestPaymentTasksBoundary:
    """결제 태스크 경계 케이스"""

    def test_duplicate_payment_confirm_ignored(self, user_factory):
        """이미 처리된 결제는 무시됨"""
        # Arrange
        user = user_factory()
        order = OrderFactory(user=user, status="paid")
        payment = PaymentFactory(order=order, status="done")

        # Act
        result = finalize_payment_confirm(
            toss_response={"status": "DONE"},
            payment_id=payment.id,
            user_id=user.id
        )

        # Assert
        assert result["status"] == "already_processed"


@pytest.mark.django_db(transaction=True)
class TestPaymentTasksException:
    """결제 태스크 예외 케이스"""

    def test_toss_api_network_error_retries(self, mocker):
        """네트워크 오류 시 재시도"""
        # Arrange
        from shopping.utils.toss_payment import TossPaymentError

        mock_client = mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError("NETWORK_ERROR", "Network failed")
        )

        # Act & Assert
        with pytest.raises(Exception):  # Celery retry exception
            call_toss_confirm_api.apply(
                args=("key", "order", 10000),
                throw=True
            )

    def test_payment_not_found(self):
        """존재하지 않는 결제 ID로 최종 처리 시도"""
        # Arrange
        non_existent_id = 99999

        # Act & Assert
        with pytest.raises(Payment.DoesNotExist):
            finalize_payment_confirm(
                toss_response={"status": "DONE"},
                payment_id=non_existent_id,
                user_id=1
            )


@pytest.mark.django_db(transaction=True)
class TestRollbackPaymentFailure:
    """결제 실패 롤백 태스크 테스트"""

    @pytest.fixture(autouse=True)
    def force_eager_mode(self, settings):
        """Celery eager 모드 강제 활성화"""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        yield

    def test_rollback_restores_stock(self, user_factory, product):
        """롤백 시 재고가 복구됨"""
        # Arrange
        user = user_factory()
        initial_stock = product.stock

        # 주문 생성 및 재고 차감 시뮬레이션
        order = OrderFactory(user=user, status="confirmed")
        order_item = OrderItemFactory(order=order, product=product, quantity=2)
        product.stock = initial_stock - 2
        product.save()

        # Act
        result = rollback_payment_failure(order.id, "결제 실패 테스트")

        # Assert
        product.refresh_from_db()
        order.refresh_from_db()
        assert product.stock == initial_stock
        assert order.status == "payment_failed"
        assert result["status"] == "success"
        assert result["stock_restored"] == 2

    def test_rollback_refunds_points(self, user_factory, product):
        """롤백 시 사용한 포인트가 환불됨"""
        # Arrange
        user = user_factory()
        initial_points = user.points

        # 포인트를 사용한 주문
        order = OrderFactory(user=user, status="confirmed", used_points=1000)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)
        product.stock -= 1
        product.save()

        # 포인트 차감 시뮬레이션
        user.points = initial_points - 1000
        user.save()

        # Act
        result = rollback_payment_failure(order.id, "결제 실패 테스트")

        # Assert
        user.refresh_from_db()
        order.refresh_from_db()
        assert order.status == "payment_failed"
        assert result["points_refunded"] == 1000
        # 포인트가 복구되었는지 확인
        assert user.points == initial_points

    def test_rollback_idempotent(self, user_factory, product):
        """이미 롤백된 주문은 다시 처리하지 않음 (멱등성)"""
        # Arrange
        user = user_factory()
        order = OrderFactory(user=user, status="payment_failed")

        # Act
        result = rollback_payment_failure(order.id, "중복 롤백 시도")

        # Assert
        assert result["status"] == "already_processed"

    def test_rollback_paid_order_rejected(self, user_factory, product):
        """결제 완료된 주문은 롤백 불가"""
        # Arrange
        user = user_factory()
        order = OrderFactory(user=user, status="paid")

        # Act
        result = rollback_payment_failure(order.id, "잘못된 롤백 시도")

        # Assert
        assert result["status"] == "error"
        assert "paid" in result["message"]

    def test_rollback_order_not_found(self):
        """존재하지 않는 주문 롤백 시도"""
        # Act
        result = rollback_payment_failure(99999, "존재하지 않는 주문")

        # Assert
        assert result["status"] == "error"
        assert "order not found" in result["message"]

    def test_rollback_updates_payment_status(self, user_factory, product):
        """롤백 시 Payment 상태도 업데이트됨"""
        # Arrange
        user = user_factory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # Act
        result = rollback_payment_failure(order.id, "결제 실패")

        # Assert
        payment.refresh_from_db()
        assert payment.status == "aborted"


@pytest.mark.django_db(transaction=True)
class TestDetectOrphanedOrders:
    """Orphaned Order 감지 태스크 테스트"""

    @pytest.fixture(autouse=True)
    def force_eager_mode(self, settings):
        """Celery eager 모드 강제 활성화"""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        yield

    @pytest.fixture
    def user_factory(self):
        """사용자 팩토리"""
        def _create_user(**kwargs):
            return UserFactory(**kwargs)
        return _create_user

    def test_detect_no_orphans(self):
        """불일치 주문이 없을 때"""
        # Act
        result = detect_orphaned_orders(threshold_minutes=10)

        # Assert
        assert result["status"] == "completed"
        assert result["detected"] == 0
        assert result["triggered"] == 0

    def test_detect_orphaned_order(self, user_factory, mocker):
        """confirmed + aborted 상태의 주문을 감지함"""
        from datetime import timedelta
        from django.utils import timezone

        # Arrange: orphaned order 생성
        user = user_factory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="aborted")
        
        # updated_at을 15분 전으로 설정
        old_time = timezone.now() - timedelta(minutes=15)
        from shopping.models.order import Order
        Order.objects.filter(pk=order.pk).update(updated_at=old_time)

        # rollback 태스크 모킹 (실제 실행 방지)
        mock_rollback = mocker.patch(
            "shopping.tasks.payment_tasks.rollback_payment_failure.delay"
        )

        # Act
        result = detect_orphaned_orders(threshold_minutes=10)

        # Assert
        assert result["status"] == "completed"
        assert result["detected"] == 1
        assert result["triggered"] == 1
        mock_rollback.assert_called_once()

    def test_ignore_recent_orders(self, user_factory, mocker):
        """최근 주문은 무시함 (아직 처리 중일 수 있음)"""
        # Arrange: 방금 생성된 orphaned order (5분 전)
        from datetime import timedelta
        from django.utils import timezone
        
        user = user_factory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="aborted")
        
        # updated_at을 5분 전으로 설정 (threshold 10분보다 짧음)
        recent_time = timezone.now() - timedelta(minutes=5)
        from shopping.models.order import Order
        Order.objects.filter(pk=order.pk).update(updated_at=recent_time)

        mock_rollback = mocker.patch(
            "shopping.tasks.payment_tasks.rollback_payment_failure.delay"
        )

        # Act
        result = detect_orphaned_orders(threshold_minutes=10)

        # Assert: 최근 주문은 감지 안 됨
        assert result["detected"] == 0
        mock_rollback.assert_not_called()


@pytest.mark.django_db
class TestNotifyPaymentFailure:
    """결제 실패 알림 태스크 테스트"""

    @pytest.fixture(autouse=True)
    def force_eager_mode(self, settings):
        """Celery eager 모드 강제 활성화"""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        yield

    def test_notify_warning(self):
        """warning 레벨 알림"""
        # Act
        result = notify_payment_failure(
            order_id=123,
            failure_type="rollback_triggered",
            details="테스트 상세 정보",
            severity="warning"
        )

        # Assert
        assert result["status"] == "notified"
        assert result["order_id"] == 123
        assert result["failure_type"] == "rollback_triggered"
        assert result["severity"] == "warning"

    def test_notify_critical(self):
        """critical 레벨 알림"""
        # Act
        result = notify_payment_failure(
            order_id=456,
            failure_type="rollback_failed",
            details="롤백 실패 - 수동 처리 필요",
            severity="critical"
        )

        # Assert
        assert result["status"] == "notified"
        assert result["severity"] == "critical"

