from decimal import Decimal
from typing import Any
from unittest.mock import Mock, patch

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.models.point import PointHistory
from shopping.models.user import User


@pytest.mark.django_db
class TestOrderPaymentIntegration:
    """주문→결제→적립 통합 테스트"""

    def test_full_payment_flow_with_points_usage(
        self,
        authenticated_client,
        user_with_points,
        product,
        add_to_cart_helper,
        shipping_data,
        mock_payment_success,
    ):
        """포인트 일부 사용 + 결제 완료 → 포인트 적립 확인"""
        # Arrange
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 2000}
        authenticated_client.force_authenticate(user=user_with_points)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", order_data, format="json")
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        # Act - 결제 요청 (Payment 생성)
        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )
        assert payment_request_response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)

        # Act - 결제 승인 (Mock 사용 - Celery eager mode for sync execution)
        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(order.final_amount)

            # 비동기 메서드를 모킹하여 동기 버전을 직접 호출
            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            confirm_response = authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_payment_key_123",
                    "amount": int(order.final_amount),
                },
                format="json",
            )

        # Assert - 결제 성공 (비동기 처리)
        assert confirm_response.status_code == status.HTTP_202_ACCEPTED
        assert confirm_response.data["status"] == "processing"

        # Assert - Payment 상태 확인
        payment.refresh_from_db()
        assert payment.is_paid is True
        assert payment.status == "done"

        # Assert - Order 상태 확인
        order.refresh_from_db()
        assert order.status == "paid"

        # Assert - 포인트 적립 확인
        user_with_points.refresh_from_db()
        # 원래 5000 - 사용 2000 + 적립 (상품금액 10000 * 1% = 100)
        # 배송비는 적립 대상에서 제외
        assert user_with_points.points == 3100  # 5000 - 2000 + 100

        # Assert - 포인트 이력 확인
        earn_history = PointHistory.objects.filter(user=user_with_points, type="earn", order=order).first()
        assert earn_history is not None
        assert earn_history.points == 100

    def test_full_payment_flow_without_points(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
        mock_payment_success,
    ):
        """포인트 미사용 결제 플로우"""
        # Arrange
        add_to_cart_helper(user, product, quantity=1)
        authenticated_client.force_authenticate(user=user)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", shipping_data, format="json")
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)
        assert order.used_points == 0
        expected_amount = order.total_amount + order.shipping_fee

        # Act - 결제 요청
        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )
        assert payment_request_response.status_code == status.HTTP_201_CREATED

        # Act - 결제 승인 (Mock)
        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(expected_amount)

            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            confirm_response = authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(expected_amount),
                },
                format="json",
            )

        # Assert
        assert confirm_response.status_code == status.HTTP_202_ACCEPTED
        order.refresh_from_db()
        assert order.status == "paid"

        # Assert - 포인트 적립만 발생 (사용 없음)
        user.refresh_from_db()
        # 상품금액 10000원 기준 (배송비 3000 제외) → 100포인트
        assert user.points == 5100  # 5000 + 100

    def test_full_payment_flow_with_full_points(
        self,
        authenticated_client,
        user_with_high_points,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """전액 포인트 결제 (final_amount=0) - 결제 승인 스킵"""
        # Arrange
        product.price = Decimal("10000")
        product.save()
        add_to_cart_helper(user_with_high_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 13000}  # 상품 10000 + 배송비 3000
        authenticated_client.force_authenticate(user=user_with_high_points)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", order_data, format="json")

        # Assert - 주문 생성 성공
        assert order_response.status_code == status.HTTP_202_ACCEPTED
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)
        assert order.used_points == 13000
        assert order.final_amount == Decimal("0")

        # Assert - 포인트 차감 확인
        user_with_high_points.refresh_from_db()
        assert user_with_high_points.points == 37000  # 50000 - 13000

        # Act - 결제 요청 시도 (전액 포인트는 결제 불필요)
        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )

        # Assert - Payment 생성됨 (amount=0)
        assert payment_request_response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == Decimal("0")

    def test_payment_amount_matches_order_final_amount(
        self,
        authenticated_client,
        user_with_points,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """Payment 금액과 Order final_amount 일치성 검증"""
        # Arrange
        add_to_cart_helper(user_with_points, product, quantity=2)
        order_data = {**shipping_data, "use_points": 3000}
        authenticated_client.force_authenticate(user=user_with_points)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", order_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        # Act - 결제 요청
        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )
        payment = Payment.objects.get(order=order)

        # Assert - 금액 일치
        assert payment.amount == order.final_amount
        # 상품 20000 + 배송비 3000 - 포인트 3000 = 20000
        assert payment.amount == Decimal("20000")
        assert order.final_amount == Decimal("20000")

    def test_payment_with_free_shipping_amount(
        self,
        authenticated_client,
        user_with_points,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """무료배송 주문의 결제 금액 검증"""
        # Arrange - 35,000원 (무료배송) + 5,000 포인트 사용
        product.price = Decimal("35000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 5000}
        authenticated_client.force_authenticate(user=user_with_points)

        # Act
        order_response = authenticated_client.post("/api/orders/", order_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )
        payment = Payment.objects.get(order=order)

        # Assert - 배송비 0원 확인
        assert order.shipping_fee == Decimal("0")
        assert order.is_free_shipping is True
        # 35000 - 5000 = 30000
        assert payment.amount == Decimal("30000")
        assert order.final_amount == Decimal("30000")

    def test_payment_with_remote_area_amount(
        self,
        authenticated_client,
        user_with_points,
        product,
        add_to_cart_helper,
        remote_shipping_data,
    ):
        """도서산간 지역 추가 배송비 포함 결제 금액 검증"""
        # Arrange - 20,000원 + 제주 배송비 6,000원 + 2,000포인트 사용
        product.price = Decimal("20000")
        product.save()
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**remote_shipping_data, "use_points": 2000}
        authenticated_client.force_authenticate(user=user_with_points)

        # Act
        order_response = authenticated_client.post("/api/orders/", order_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        payment_request_response = authenticated_client.post(
            "/api/payments/request/",
            {"order_id": order.id},
            format="json",
        )
        payment = Payment.objects.get(order=order)

        # Assert - 도서산간 배송비 포함
        assert order.shipping_fee == Decimal("3000")
        assert order.additional_shipping_fee == Decimal("3000")
        # 20000 + 3000 + 3000 - 2000 = 24000
        assert payment.amount == Decimal("24000")
        assert order.final_amount == Decimal("24000")

    def test_points_earn_after_payment_confirm(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
        mock_payment_success,
    ):
        """결제 승인 후 포인트 적립 확인"""
        # Arrange
        initial_points = user.points
        add_to_cart_helper(user, product, quantity=1)
        authenticated_client.force_authenticate(user=user)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", shipping_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        # Act - 결제 요청 및 승인
        authenticated_client.post("/api/payments/request/", {"order_id": order.id}, format="json")

        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(order.final_amount)

            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            confirm_response = authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(order.final_amount),
                },
                format="json",
            )

        # Assert - 포인트 적립
        user.refresh_from_db()
        # 상품금액 10000원 기준 (배송비 제외) → 100포인트
        expected_earn = 100
        assert user.points == initial_points + expected_earn

        # Assert - 적립 이력
        earn_history = PointHistory.objects.filter(user=user, type="earn", order=order).first()
        assert earn_history is not None
        assert earn_history.points == expected_earn

    def test_points_earn_default_rate_when_level_unspecified(
        self,
        authenticated_client,
        product,
        add_to_cart_helper,
        shipping_data,
        user_factory,
        mock_payment_success,
    ):
        """등급 미지정 시 기본 적립률 1% 검증"""
        # Arrange - 등급 미지정 사용자 (기본값 적용)
        user = user_factory(username="test_user_default", points=10000)
        # membership_level을 명시하지 않음 → 기본값(bronze or None) 적용
        add_to_cart_helper(user, product, quantity=1)
        authenticated_client.force_authenticate(user=user)

        # Act - 주문 생성
        order_response = authenticated_client.post("/api/orders/", shipping_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        # Act - 결제 요청 및 승인
        authenticated_client.post("/api/payments/request/", {"order_id": order.id}, format="json")

        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(order.final_amount)

            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            confirm_response = authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(order.final_amount),
                },
                format="json",
            )

        # Assert - 기본 1% 적립 (배송비 제외 상품금액 기준)
        user.refresh_from_db()
        # total_amount는 이미 순수 상품금액 (배송비 미포함)
        expected_earn = int(order.total_amount * Decimal("0.01"))
        actual_earn = user.points - 10000  # 초기 포인트 차감
        assert actual_earn == expected_earn
        # product_amount = 10000 (배송비 3000 제외) → 100포인트
        assert actual_earn == 100

        # Assert - 포인트 이력 확인
        earn_history = PointHistory.objects.filter(user=user, type="earn", order=order).first()
        assert earn_history is not None
        assert earn_history.points == 100

    def test_points_earn_rate_by_membership_level(
        self,
        authenticated_client,
        product,
        add_to_cart_helper,
        shipping_data,
        user_factory,
        mock_payment_success,
    ):
        """등급별 포인트 적립률 통합 검증 (bronze 1%, silver 2%, gold 3%, vip 5%)"""
        # Arrange - 등급별 사용자 및 기대 적립률
        membership_levels = ["bronze", "silver", "gold", "vip"]
        expected_rates = {"bronze": 1, "silver": 2, "gold": 3, "vip": 5}

        for level in membership_levels:
            user = user_factory(
                username=f"user_{level}",
                points=10000,
                membership_level=level,
            )
            add_to_cart_helper(user, product, quantity=1)
            authenticated_client.force_authenticate(user=user)

            # Act - 주문 생성
            order_response = authenticated_client.post("/api/orders/", shipping_data, format="json")
            order_id = order_response.data["order_id"]
            order = Order.objects.get(id=order_id)

            # Act - 결제 요청 및 승인
            authenticated_client.post("/api/payments/request/", {"order_id": order.id}, format="json")

            with (
                patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
                patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
            ):
                mock_confirm.return_value = mock_payment_success(order.final_amount)

                from shopping.services.payment_service import PaymentService

                mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                    PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                    {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
                )[1]

                confirm_response = authenticated_client.post(
                    "/api/payments/confirm/",
                    {
                        "order_id": order.id,
                        "payment_key": "test_key",
                        "amount": int(order.final_amount),
                    },
                    format="json",
                )

            # Assert - 등급별 적립률 검증 (배송비 제외)
            user.refresh_from_db()
            expected_rate = expected_rates[level]
            # total_amount는 이미 순수 상품금액 (배송비 미포함)
            expected_earn = int(order.total_amount * Decimal(expected_rate) / Decimal("100"))
            actual_earn = user.points - 10000  # 초기 포인트 차감

            assert actual_earn == expected_earn, f"{level} 등급 적립률 검증 실패"
            # product_amount = 10000 (배송비 제외)
            # bronze: 100, silver: 200, gold: 300, vip: 500

            # Assert - 포인트 이력 확인
            earn_history = PointHistory.objects.filter(user=user, type="earn", order=order).first()
            assert earn_history is not None
            assert earn_history.points == expected_earn

    def test_payment_cancel_refunds_used_points(
        self,
        authenticated_client,
        user_with_points,
        product,
        add_to_cart_helper,
        shipping_data,
        mock_payment_success,
        mock_payment_cancel,
    ):
        """결제 취소 시 사용한 포인트 환불 확인"""
        # Arrange - 포인트 사용 주문 및 결제
        initial_points = user_with_points.points
        add_to_cart_helper(user_with_points, product, quantity=1)
        order_data = {**shipping_data, "use_points": 3000}
        authenticated_client.force_authenticate(user=user_with_points)

        order_response = authenticated_client.post("/api/orders/", order_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        authenticated_client.post("/api/payments/request/", {"order_id": order.id}, format="json")

        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(order.final_amount)

            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(order.final_amount),
                },
                format="json",
            )

        payment = Payment.objects.get(order=order)

        # Act - 결제 취소 (Mock)
        with patch("shopping.utils.toss_payment.TossPaymentClient.cancel_payment") as mock_cancel_client:
            mock_cancel_client.return_value = mock_payment_cancel

            cancel_response = authenticated_client.post(
                "/api/payments/cancel/",
                {
                    "payment_id": payment.id,
                    "cancel_reason": "단순 변심",
                },
                format="json",
            )

        # Assert - 취소 성공
        assert cancel_response.status_code == status.HTTP_200_OK

        # Assert - 사용 포인트 환불
        user_with_points.refresh_from_db()
        # 초기 5000 - 사용 3000 + 적립 100 - 적립 차감 100 + 환불 3000 = 5000
        assert user_with_points.points == initial_points

        # Assert - 환불 이력
        refund_history = PointHistory.objects.filter(user=user_with_points, type="cancel_refund", order=order).first()
        assert refund_history is not None
        assert refund_history.points == 3000

    def test_payment_cancel_deducts_earned_points(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
        mock_payment_success,
        mock_payment_cancel,
    ):
        """결제 취소 시 적립된 포인트 차감 확인"""
        # Arrange - 결제 완료
        initial_points = user.points
        add_to_cart_helper(user, product, quantity=1)
        authenticated_client.force_authenticate(user=user)

        order_response = authenticated_client.post("/api/orders/", shipping_data, format="json")
        order_id = order_response.data["order_id"]
        order = Order.objects.get(id=order_id)

        authenticated_client.post("/api/payments/request/", {"order_id": order.id}, format="json")

        with (
            patch("shopping.utils.toss_payment.TossPaymentClient.confirm_payment") as mock_confirm,
            patch("shopping.services.payment_service.PaymentService.confirm_payment_async") as mock_async,
        ):
            mock_confirm.return_value = mock_payment_success(order.final_amount)

            from shopping.services.payment_service import PaymentService

            mock_async.side_effect = lambda payment, payment_key, order_id, amount, user: (
                PaymentService.confirm_payment_sync(payment, payment_key, order_id, amount, user),
                {"status": "processing", "payment_id": payment.id, "task_id": "test", "message": "test"},
            )[1]

            authenticated_client.post(
                "/api/payments/confirm/",
                {
                    "order_id": order.id,
                    "payment_key": "test_key",
                    "amount": int(order.final_amount),
                },
                format="json",
            )

        # 적립 확인
        user.refresh_from_db()
        earned_points = user.points - initial_points
        assert earned_points == 100  # 상품금액 10000 * 1% = 100 (배송비 제외)

        payment = Payment.objects.get(order=order)

        # Act - 결제 취소
        with patch("shopping.utils.toss_payment.TossPaymentClient.cancel_payment") as mock_cancel_client:
            mock_cancel_client.return_value = mock_payment_cancel

            cancel_response = authenticated_client.post(
                "/api/payments/cancel/",
                {
                    "payment_id": payment.id,
                    "cancel_reason": "단순 변심",
                },
                format="json",
            )

        # Assert - 적립 포인트 차감
        user.refresh_from_db()
        assert user.points == initial_points  # 원래대로 돌아옴

        # Assert - 차감 이력
        deduct_history = PointHistory.objects.filter(user=user, type="cancel_deduct", order=order).first()
        assert deduct_history is not None
        assert deduct_history.points == -earned_points
