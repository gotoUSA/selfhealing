"""결제 포인트 처리 테스트"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    CompletedPaymentFactory,
    OrderFactory,
    OrderItemFactory,
    PointHistoryFactory,
    TossResponseBuilder,
)


@pytest.mark.django_db
class TestPaymentPointsEarnNormalCase:
    """결제 완료 시 포인트 적립 - 정상"""

    def test_point_history_metadata_on_earn(
        self,
        authenticated_client,
        user,
        order,
        payment,
        mocker,
    ):
        """포인트 이력 메타데이터 검증 - 적립"""
        # Arrange
        user.membership_level = "gold"
        user.save()

        toss_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=order.id,
            amount=int(payment.amount),
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": int(payment.amount),
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        point_history = PointHistory.objects.filter(
            user=user,
            type="earn",
            order=order,
        ).first()

        assert point_history is not None
        assert "earn_rate" in point_history.metadata
        assert "membership_level" in point_history.metadata
        assert point_history.metadata["membership_level"] == "gold"
        assert point_history.metadata["earn_rate"] == "3%"
        assert point_history.expires_at is not None

    def test_point_history_description_accuracy(
        self,
        authenticated_client,
        user,
        order,
        payment,
        mocker,
    ):
        """포인트 이력 description 정확성 검증"""
        # Arrange
        toss_response = TossResponseBuilder.success_response(
            payment_key=payment.payment_key,
            order_id=order.id,
            amount=int(payment.amount),
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": int(payment.amount),
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED

        point_history = PointHistory.objects.filter(
            user=user,
            type="earn",
            order=order,
        ).first()

        assert point_history is not None
        assert f"주문 #{order.order_number} 구매 적립" in point_history.description


@pytest.mark.django_db
class TestPaymentPointsCancelNormalCase:
    """결제 취소 시 포인트 처리 - 정상"""

    def test_fifo_points_deduction_on_cancel(
        self,
        authenticated_client,
        user,
        product,
        mocker,
    ):
        """FIFO 방식 포인트 회수 검증"""
        # Arrange - 3개의 포인트 적립 이력 생성 (만료일이 다름)
        now = timezone.now()

        # 가장 먼저 만료될 포인트 (30일 후)
        history1 = PointHistoryFactory(
            user=user,
            points=1000,
            balance=6000,
            type="earn",
            description="첫번째 적립",
            expires_at=now + timedelta(days=30),
        )

        # 두번째로 만료될 포인트 (60일 후)
        history2 = PointHistoryFactory(
            user=user,
            points=2000,
            balance=8000,
            type="earn",
            description="두번째 적립",
            expires_at=now + timedelta(days=60),
        )

        # 가장 나중에 만료될 포인트 (90일 후)
        history3 = PointHistoryFactory(
            user=user,
            points=3000,
            balance=11000,
            type="earn",
            description="세번째 적립",
            expires_at=now + timedelta(days=90),
        )

        user.points = 11000
        user.save()

        # 결제 완료된 주문 생성 (2500P 적립)
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            final_amount=product.price,
            earned_points=2500,
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        payment = CompletedPaymentFactory(
            order=order,
            amount=order.total_amount,
        )

        # 적립 이력 생성
        PointHistoryFactory(
            user=user,
            points=2500,
            balance=13500,
            type="earn",
            order=order,
            description="주문 적립",
            expires_at=now + timedelta(days=365),
        )

        user.points = 13500
        user.save()

        toss_cancel_response = TossResponseBuilder.cancel_response(
            payment_key=payment.payment_key,
            cancel_reason="FIFO 테스트",
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "FIFO 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # FIFO 순서 검증: 가장 먼저 만료되는 순서대로 회수
        history1.refresh_from_db()
        history2.refresh_from_db()
        history3.refresh_from_db()

        # 첫번째 이력 (1000P) - 전액 회수
        assert history1.metadata.get("used_amount", 0) == 1000

        # 두번째 이력 (2000P) - 1500P 회수 (2500 - 1000)
        assert history2.metadata.get("used_amount", 0) == 1500

        # 세번째 이력 (3000P) - 회수 안됨
        assert history3.metadata.get("used_amount", 0) == 0

    def test_point_history_metadata_on_cancel(
        self,
        authenticated_client,
        user,
        product,
        mocker,
    ):
        """포인트 이력 메타데이터 검증 - 취소 환불/회수"""
        # Arrange - 포인트 사용 주문
        user.points = 10000
        user.save()

        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            used_points=3000,
            final_amount=product.price - Decimal("3000"),
            earned_points=100,
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        # 포인트 사용 이력
        PointHistoryFactory(
            user=user,
            points=-3000,
            balance=7000,
            type="use",
            order=order,
            description="포인트 사용",
        )

        # 포인트 적립 이력
        PointHistoryFactory(
            user=user,
            points=100,
            balance=7100,
            type="earn",
            order=order,
            description="주문 적립",
        )

        user.points = 7100
        user.save()

        payment = CompletedPaymentFactory(
            order=order,
            amount=order.final_amount,
        )

        toss_cancel_response = TossResponseBuilder.cancel_response(
            payment_key=payment.payment_key,
            cancel_reason="메타데이터 테스트",
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "메타데이터 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # 환불 이력 (type="cancel_refund")
        refund_history = PointHistory.objects.filter(
            user=user,
            type="cancel_refund",
            order=order,
        ).first()
        assert refund_history is not None
        assert refund_history.points == 3000

        # 회수 이력 (type="cancel_deduct")
        deduct_history = PointHistory.objects.filter(
            user=user,
            type="cancel_deduct",
            order=order,
        ).first()
        assert deduct_history is not None
        assert deduct_history.points == -100


@pytest.mark.django_db
class TestPaymentPointsBoundary:
    """경계값 테스트"""

    def test_partial_expired_points_deduction(self, user_factory):
        """부분 만료된 포인트 회수 처리"""
        # Arrange
        user = user_factory(
            username="expiry_test_user",
            email="expiry@test.com",
            phone_number="010-1111-2222",
        )

        now = timezone.now()
        point_service = PointService()

        # 만료된 포인트 (500P) - 30일 전에 만료
        expired_history = PointHistoryFactory(
            user=user,
            points=500,
            balance=500,
            type="earn",
            description="만료된 적립",
            expires_at=now - timedelta(days=30),
        )

        # 유효한 포인트 (2000P) - 30일 후 만료
        valid_history = PointHistoryFactory(
            user=user,
            points=2000,
            balance=2500,
            type="earn",
            description="유효한 적립",
            expires_at=now + timedelta(days=30),
        )

        user.points = 2500
        user.save()

        # Act - 1500P 회수 시도
        result = point_service.use_points_fifo(
            user=user,
            amount=1500,
            type="cancel_deduct",
            description="부분 만료 테스트",
        )

        # Assert
        assert result["success"] is True

        # 만료된 포인트는 건너뛰고 유효한 포인트에서만 회수
        expired_history.refresh_from_db()
        valid_history.refresh_from_db()

        assert expired_history.metadata.get("used_amount", 0) == 0
        assert valid_history.metadata.get("used_amount", 0) == 1500

        user.refresh_from_db()
        assert user.points == 1000


@pytest.mark.django_db
class TestPaymentPointsException:
    """예외 케이스"""

    def test_insufficient_points_for_deduction(self, user_factory):
        """포인트 부족으로 회수 불가"""
        # Arrange
        user = user_factory(
            username="insufficient_user",
            email="insufficient@test.com",
            phone_number="010-2222-3333",
        )

        now = timezone.now()
        point_service = PointService()

        # 유효한 포인트 1000P만 존재
        PointHistoryFactory(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="유효한 적립",
            expires_at=now + timedelta(days=30),
        )

        user.points = 1000
        user.save()

        # Act - 2000P 회수 시도 (부족)
        result = point_service.use_points_fifo(
            user=user,
            amount=2000,
            type="cancel_deduct",
            description="부족 테스트",
        )

        # Assert
        assert result["success"] is False
        assert "부족" in result["message"]

        # 포인트 변동 없음
        user.refresh_from_db()
        assert user.points == 1000

    def test_insufficient_unexpired_points_with_expired_balance(self, user_factory):
        """만료된 포인트가 잔액에 포함된 경우 회수 실패"""
        # Arrange
        user = user_factory(
            username="expired_balance_user",
            email="expired_balance@test.com",
            phone_number="010-9999-8888",
        )

        now = timezone.now()
        point_service = PointService()

        # 만료된 포인트 1500P (배치 미실행)
        PointHistoryFactory(
            user=user,
            points=1500,
            balance=1500,
            type="earn",
            description="만료된 적립",
            expires_at=now - timedelta(days=10),
        )

        # 유효한 포인트 500P만 존재
        PointHistoryFactory(
            user=user,
            points=500,
            balance=2000,
            type="earn",
            description="유효한 적립",
            expires_at=now + timedelta(days=30),
        )

        user.points = 2000  # 1500(만료) + 500(유효)
        user.save()

        # Act - 1000P 회수 시도 (잔액은 2000이지만 유효 포인트는 500만)
        result = point_service.use_points_fifo(
            user=user,
            amount=1000,
            type="cancel_deduct",
            description="만료 포함 잔액 테스트",
        )

        # Assert - 유효한 포인트 부족으로 실패
        assert result["success"] is False
        assert "유효한 포인트가 부족" in result["message"]
        assert "필요: 1000" in result["message"]
        assert "사용 가능: 500" in result["message"]

        # 포인트 변동 없음 (롤백되어야 함)
        user.refresh_from_db()
        assert user.points == 2000

    def test_expired_points_cannot_be_deducted(self, user_factory):
        """만료된 포인트 회수 불가 - 만료 처리 후"""
        # Arrange
        user = user_factory(
            username="expired_user",
            email="expired@test.com",
            phone_number="010-3333-4444",
        )

        now = timezone.now()
        point_service = PointService()

        # 만료된 포인트만 존재 (30일 전에 만료)
        expired_history = PointHistoryFactory(
            user=user,
            points=3000,
            balance=3000,
            type="earn",
            description="만료된 적립",
            expires_at=now - timedelta(days=30),
        )

        user.points = 3000
        user.save()

        # 만료 처리 실행
        point_service.expire_points()

        # Act - 1000P 회수 시도 (만료 처리 후 실제 포인트 0)
        user.refresh_from_db()
        result = point_service.use_points_fifo(
            user=user,
            amount=1000,
            type="cancel_deduct",
            description="만료 테스트",
        )

        # Assert
        assert result["success"] is False
        assert "부족" in result["message"]

        # 포인트 0 (만료 처리됨)
        user.refresh_from_db()
        assert user.points == 0

        # 만료 이력 확인
        expired_history.refresh_from_db()
        assert expired_history.metadata.get("expired") is True

    def test_cancel_with_insufficient_earned_points(
        self,
        authenticated_client,
        user,
        product,
        mocker,
    ):
        """적립 포인트 회수 시 포인트 부족 - API 통합 테스트"""
        # Arrange
        user.points = 100
        user.save()

        # 결제 완료 주문 (2000P 적립됨)
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            final_amount=product.price,
            earned_points=2000,
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        # 적립 이력
        PointHistoryFactory(
            user=user,
            points=2000,
            balance=2100,
            type="earn",
            order=order,
            description="주문 적립",
        )

        # 사용자가 적립된 포인트를 거의 다 사용 (100P만 남음)
        # (별도 주문에서 2000P 사용했다고 가정)

        payment = CompletedPaymentFactory(
            order=order,
            amount=order.total_amount,
        )

        toss_cancel_response = TossResponseBuilder.cancel_response(
            payment_key=payment.payment_key,
            cancel_reason="부족 테스트",
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "부족 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert - 포인트 부족으로 취소 실패 (400 에러)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "포인트가 부족" in str(response.data)

        # 주문/결제 상태 변경 없음
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == "paid"
        assert payment.status == "done"

    def test_fifo_with_mixed_availability(self, user_factory):
        """FIFO 회수 시 일부 사용된 포인트 처리"""
        # Arrange
        user = user_factory(
            username="mixed_user",
            email="mixed@test.com",
            phone_number="010-5555-6666",
        )

        now = timezone.now()
        point_service = PointService()

        # 첫 번째 적립 (1000P, 500P 이미 사용됨)
        history1 = PointHistoryFactory(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="첫 적립",
            expires_at=now + timedelta(days=30),
        )
        history1.metadata["used_amount"] = 500
        history1.save(update_fields=["metadata"])

        # 두 번째 적립 (2000P, 사용 안 됨)
        history2 = PointHistoryFactory(
            user=user,
            points=2000,
            balance=3000,
            type="earn",
            description="두번째 적립",
            expires_at=now + timedelta(days=60),
        )

        user.points = 2500  # 1000 - 500 + 2000
        user.save()

        # Act - 1000P 회수
        result = point_service.use_points_fifo(
            user=user,
            amount=1000,
            type="cancel_deduct",
            description="혼합 테스트",
        )

        # Assert
        assert result["success"] is True

        # 첫 번째 이력의 남은 500P 전액 + 두 번째 이력에서 500P
        history1.refresh_from_db()
        history2.refresh_from_db()

        assert history1.metadata.get("used_amount", 0) == 1000  # 500 + 500
        assert history2.metadata.get("used_amount", 0) == 500

    def test_points_deduction_fifo_order_validation(self, user_factory):
        """FIFO 회수 순서 엄격 검증"""
        # Arrange
        user = user_factory(
            username="fifo_strict_user",
            email="fifo@test.com",
            phone_number="010-4444-5555",
        )

        now = timezone.now()
        point_service = PointService()

        # 5개의 적립 이력 (만료일 역순으로 생성)
        histories = []
        for i in range(5):
            history = PointHistoryFactory(
                user=user,
                points=1000,
                balance=(i + 1) * 1000,
                type="earn",
                description=f"{i+1}번째 적립",
                expires_at=now + timedelta(days=(i + 1) * 10),
            )
            histories.append(history)

        user.points = 5000
        user.save()

        # Act - 3500P 회수
        result = point_service.use_points_fifo(
            user=user,
            amount=3500,
            type="cancel_deduct",
            description="엄격 순서 검증",
        )

        # Assert
        assert result["success"] is True

        # FIFO 순서 검증
        for i, history in enumerate(histories):
            history.refresh_from_db()
            if i < 3:
                # 처음 3개 이력 (0, 1, 2) - 전액 회수
                assert history.metadata.get("used_amount", 0) == 1000
            elif i == 3:
                # 4번째 이력 - 500P 회수
                assert history.metadata.get("used_amount", 0) == 500
            else:
                # 5번째 이력 - 회수 안됨
                assert history.metadata.get("used_amount", 0) == 0
