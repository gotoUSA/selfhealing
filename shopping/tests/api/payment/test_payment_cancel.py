from decimal import Decimal
from unittest.mock import Mock

import pytest
from rest_framework import status
from django.db.models import F
from shopping.models.order import OrderItem
from shopping.models.payment import PaymentLog
from shopping.models.point import PointHistory
from shopping.models.product import Product
from shopping.tests.factories import (
    CompletedPaymentFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    PointHistoryFactory,
    ProductFactory,
    TossResponseBuilder,
)


@pytest.mark.django_db
class TestPaymentCancelNormalCase:
    """정상 케이스"""

    def test_successful_payment_cancellation_full_flow(
        self,
        authenticated_client,
        user,
        paid_order,
        paid_payment,
        product,
        toss_cancel_response_builder,
        mocker,
    ):
        """정상 결제 취소 (전체) - 전체 플로우 검증"""
        # Arrange
        initial_stock = product.stock
        initial_sold_count = product.sold_count
        initial_points = user.points
        order_item = paid_order.order_items.first()
        order_quantity = order_item.quantity

        toss_cancel_response = TossResponseBuilder.cancel_response(
            payment_key=paid_payment.payment_key,
            cancel_reason="단순 변심",
        )

        mock_cancel = mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "단순 변심",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert - HTTP 응답
        assert response.status_code == status.HTTP_200_OK
        assert "결제가 취소되었습니다" in response.data["message"]
        assert "payment" in response.data
        assert "refund_amount" in response.data

        # Assert - Toss API 호출 확인
        mock_cancel.assert_called_once_with(
            payment_key=paid_payment.payment_key,
            cancel_reason="단순 변심",
        )

        # Assert - Payment 상태 변경
        paid_payment.refresh_from_db()
        assert paid_payment.status == "canceled"
        assert paid_payment.is_canceled is True

        # Assert - Order 상태 변경
        paid_order.refresh_from_db()
        assert paid_order.status == "canceled"

        # Assert - 재고 복구
        product.refresh_from_db()
        assert product.stock == initial_stock + order_quantity

        # Assert - sold_count 감소
        assert product.sold_count == initial_sold_count - order_quantity

    def test_payment_status_changed_to_canceled(
        self,
        authenticated_client,
        paid_payment,
        toss_cancel_response_builder,
        mocker,
    ):
        """Payment 상태 변경 (done → canceled)"""
        # Arrange
        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        assert paid_payment.status == "done"
        assert paid_payment.is_canceled is False

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "고객 변심",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        paid_payment.refresh_from_db()
        assert paid_payment.status == "canceled"
        assert paid_payment.is_canceled is True
        assert paid_payment.canceled_at is not None

    def test_order_status_changed_to_canceled(
        self,
        authenticated_client,
        paid_order,
        paid_payment,
        toss_cancel_response_builder,
        mocker,
    ):
        """Order 상태 변경 (paid → canceled)"""
        # Arrange
        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        assert paid_order.status == "paid"

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "고객 변심",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        paid_order.refresh_from_db()
        assert paid_order.status == "canceled"

    def test_stock_restored_after_cancel(
        self,
        authenticated_client,
        paid_payment,
        product,
        toss_cancel_response_builder,
        mocker,
    ):
        """재고 복구 확인"""
        # Arrange
        initial_stock = product.stock
        order = paid_payment.order
        order_item = order.order_items.first()
        order_quantity = order_item.quantity

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "재고 확인 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        product.refresh_from_db()
        assert product.stock == initial_stock + order_quantity

    def test_sold_count_decreased_after_cancel(
        self,
        authenticated_client,
        paid_payment,
        product,
        toss_cancel_response_builder,
        mocker,
    ):
        """sold_count 감소 확인"""
        # Arrange
        initial_sold_count = product.sold_count
        order = paid_payment.order
        order_item = order.order_items.first()
        order_quantity = order_item.quantity

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "sold_count 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        product.refresh_from_db()
        assert product.sold_count == initial_sold_count - order_quantity

    def test_multiple_products_stock_restored(
        self,
        authenticated_client,
        user,
        category,
        sku_generator,
        adjust_stock,
        toss_cancel_response_builder,
        mocker,
    ):
        """여러 상품 주문 취소 시 모든 재고 복구"""
        # Arrange - 여러 상품 생성
        products = [
            ProductFactory(
                name=f"테스트 상품 {i+1}",
                category=category,
                price=Decimal("10000") * (i + 1),
                stock=10,
                sold_count=0,
                sku=sku_generator("CANCEL"),
            )
            for i in range(3)
        ]

        # 주문 생성
        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=sum(p.price * 2 for p in products),
        )

        # 각 상품 2개씩 주문 (재고 차감 및 sold_count 증가 시뮬레이션)
        for product in products:
            OrderItemFactory(
                order=order,
                product=product,
                quantity=2,
            )
            adjust_stock(product, stock_delta=-2, sold_delta=2)

        # Payment 생성
        payment = CompletedPaymentFactory(
            order=order,
            amount=order.total_amount,
        )

        # 초기 재고 및 sold_count 저장
        initial_stocks = {p.id: p.stock for p in products}
        initial_sold_counts = {p.id: p.sold_count for p in products}

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        authenticated_client.force_authenticate(user=user)

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "여러 상품 취소 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # 모든 상품의 재고 및 sold_count 복구 확인
        for product in products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id] + 2
            assert product.sold_count == initial_sold_counts[product.id] - 2

    def test_used_points_refunded(
        self,
        authenticated_client,
        user,
        product,
        category,
        adjust_stock,
        toss_cancel_response_builder,
        mocker,
    ):
        """사용한 포인트 환불 확인"""
        # Arrange - 포인트 사용 주문 생성
        user.points = 3000
        user.save()
        initial_points = user.points

        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            used_points=2000,
            final_amount=product.price - Decimal("2000"),
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        # 재고 차감 시뮬레이션
        adjust_stock(product, stock_delta=-1, sold_delta=1)

        payment = CompletedPaymentFactory(
            order=order,
            amount=order.final_amount,
        )

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "포인트 환불 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["refunded_points"] == 2000

        # 포인트 환불 확인
        user.refresh_from_db()
        assert user.points == initial_points + 2000

        # 포인트 환불 이력 확인
        refund_history = PointHistory.objects.filter(
            user=user,
            type="cancel_refund",
            order=order,
        ).first()
        assert refund_history is not None
        assert refund_history.points == 2000

    def test_earned_points_deducted(
        self,
        authenticated_client,
        user,
        paid_order,
        paid_payment,
        toss_cancel_response_builder,
        mocker,
    ):
        """적립된 포인트 회수 확인"""
        # Arrange - 포인트 적립 시뮬레이션
        earned_points = int(paid_payment.amount * Decimal("0.01"))
        user.points = 5000 + earned_points
        user.save()
        initial_points = user.points

        paid_order.earned_points = earned_points
        paid_order.save()

        # 적립 이력 생성
        PointHistoryFactory(
            user=user,
            points=earned_points,
            balance=user.points,
            type="earn",
            order=paid_order,
            description="결제 완료 적립",
        )

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "포인트 회수 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["deducted_points"] == earned_points

        # 포인트 회수 확인
        user.refresh_from_db()
        assert user.points == initial_points - earned_points

        # 포인트 회수 이력 확인
        deduct_history = PointHistory.objects.filter(
            user=user,
            type="cancel_deduct",
            order=paid_order,
        ).first()
        assert deduct_history is not None
        assert deduct_history.points == -earned_points

    def test_zero_points_order_cancel(
        self,
        authenticated_client,
        user,
        product,
        adjust_stock,
        toss_cancel_response_builder,
        mocker,
    ):
        """포인트 사용/적립 없는 주문 취소 (안전성 확인)"""
        # Arrange - 포인트 관련 없는 주문
        user.points = 1000
        user.save()
        initial_points = user.points

        order = OrderFactory(
            user=user,
            status="paid",
            total_amount=product.price,
            used_points=0,
            earned_points=0,
            final_amount=product.price,
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        # 재고 차감 시뮬레이션
        adjust_stock(product, stock_delta=-1, sold_delta=1)
        payment = CompletedPaymentFactory(
            order=order,
            amount=order.total_amount,
        )

        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "포인트 0 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["refunded_points"] == 0
        assert response.data["deducted_points"] == 0

        # 포인트 변동 없음 확인
        user.refresh_from_db()
        assert user.points == initial_points

    def test_cancel_log_created(
        self,
        authenticated_client,
        paid_payment,
        toss_cancel_response_builder,
        mocker,
    ):
        """취소 로그 기록 확인"""
        # Arrange
        toss_cancel_response = toss_cancel_response_builder()

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.cancel_payment",
            return_value=toss_cancel_response,
        )

        initial_log_count = PaymentLog.objects.filter(payment=paid_payment).count()

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "로그 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # 취소 로그 생성 확인
        cancel_logs = PaymentLog.objects.filter(
            payment=paid_payment,
            log_type="cancel",
        )
        assert cancel_logs.count() > 0

        # 전체 로그 개수 증가 확인
        final_log_count = PaymentLog.objects.filter(payment=paid_payment).count()
        assert final_log_count > initial_log_count


@pytest.mark.django_db
class TestPaymentCancelException:
    """예외 케이스"""

    def test_payment_not_found(self, authenticated_client):
        """존재하지 않는 결제"""
        # Arrange
        nonexistent_payment_id = 99999

        request_data = {
            "payment_id": nonexistent_payment_id,
            "cancel_reason": "존재하지 않는 결제",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 정보를 찾을 수 없습니다" in str(response.data)

    def test_already_canceled_payment(
        self,
        authenticated_client,
        user,
        product,
        mocker,
    ):
        """이미 취소된 결제"""
        # Arrange - 이미 취소된 결제 생성
        order = OrderFactory(
            user=user,
            status="canceled",
            total_amount=product.price,
        )

        OrderItemFactory(
            order=order,
            product=product,
            quantity=1,
        )

        payment = PaymentFactory(
            order=order,
            amount=order.total_amount,
            status="canceled",
            is_canceled=True,
            cancel_reason="이미 취소됨",
        )

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "재취소 시도",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "이미 취소된 결제입니다" in str(response.data)

    def test_cannot_cancel_pending_payment(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """취소 불가능한 상태 (pending/ready)"""
        # Arrange - pending 상태 결제는 취소 불가
        assert payment.status in ["pending", "ready"]

        request_data = {
            "payment_id": payment.id,
            "cancel_reason": "pending 결제 취소 시도",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "취소할 수 없는 결제 상태입니다" in str(response.data)

    def test_other_user_payment_cancel_attempt(
        self,
        api_client,
        other_user,
        paid_payment,
    ):
        """다른 사용자의 결제 취소 시도"""
        # Arrange - 다른 사용자로 인증
        api_client.force_authenticate(user=other_user)

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "권한 없는 취소 시도",
        }

        # Act
        response = api_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 정보를 찾을 수 없습니다" in str(response.data)

    def test_toss_api_error_response(
        self,
        authenticated_client,
        paid_payment,
        mocker,
    ):
        """Toss API 에러 응답"""
        # Arrange - Toss API 에러 모킹
        from shopping.utils.toss_payment import TossPaymentError

        mock_cancel = mocker.patch("shopping.utils.toss_payment.TossPaymentClient.cancel_payment")
        mock_cancel.side_effect = TossPaymentError(
            code="INVALID_REQUEST",
            message="취소할 수 없는 결제입니다.",
            status_code=400,
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "API 에러 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data

    def test_unexpected_exception_500_error(
        self,
        authenticated_client,
        paid_payment,
        mocker,
    ):
        """기타 예외 500 에러"""
        # Arrange - 예상치 못한 예외 발생
        mocker.patch(
            "shopping.services.payment_service.PaymentService.cancel_payment",
            side_effect=RuntimeError("데이터베이스 연결 오류"),
        )

        request_data = {
            "payment_id": paid_payment.id,
            "cancel_reason": "500 에러 테스트",
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/cancel/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "결제 취소 중 오류가 발생했습니다" in str(response.data)
        assert "message" in response.data
