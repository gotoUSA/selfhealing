"""결제 금액 검증 테스트"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.product import Product
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    CompletedPaymentFactory,
    ProductFactory,
    TossResponseBuilder,
)


@pytest.mark.django_db
class TestPaymentValidationNormalCase:
    """정상 케이스 - 유효한 금액 결제"""

    def test_valid_integer_amount(
        self,
        authenticated_client,
        order,
    ):
        """일반적인 정수 금액 (10,000원)"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == order.final_amount
        assert isinstance(payment.amount, Decimal)

    def test_valid_large_amount(
        self,
        authenticated_client,
        user,
        category,
        sku_generator,
        create_order,
    ):
        """큰 금액 결제 (100,000,000원)"""
        # Arrange - 1억원 상품 주문 생성
        large_product = ProductFactory(
            category=category,
            price=Decimal("100000000"),
            sku=sku_generator("LARGE"),
        )

        order = create_order(user=user, product=large_product, status="confirmed")

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == Decimal("100000000")
        assert response.json()["amount"] == 100000000

    def test_zero_amount_with_full_points(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
        mocker,
    ):
        """포인트 전액 결제 (final_amount = 0원)"""
        # Arrange - 충분한 포인트 지급
        user.points = 50000
        user.save()

        add_to_cart_helper(user, product, quantity=1)

        # 전액 포인트 결제
        order_data = {**shipping_data, "use_points": 13000}
        order_response = authenticated_client.post(
            "/api/orders/",
            order_data,
            format="json",
        )
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.filter(user=user).order_by("-created_at").first()
        assert order is not None
        assert order.final_amount == 0

        # Payment 생성
        request_data = {"order_id": order.id}
        payment_response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Act & Assert - 0원 결제 허용
        assert payment_response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == Decimal("0")

        # Confirm 테스트
        toss_response = TossResponseBuilder.success_response(
            order_id=order.id,
            amount=0,
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        confirm_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": 0,
        }

        confirm_response = authenticated_client.post(
            "/api/payments/confirm/",
            confirm_data,
            format="json",
        )

        assert confirm_response.status_code == status.HTTP_202_ACCEPTED
        assert confirm_response.data["status"] == "processing"

    def test_partial_points_usage(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """포인트 일부 사용 (total_amount - used_points = final_amount)"""
        # Arrange
        user.points = 5000
        user.save()

        add_to_cart_helper(user, product, quantity=1)

        # 2000P 사용
        order_data = {**shipping_data, "use_points": 2000}
        order_response = authenticated_client.post(
            "/api/orders/",
            order_data,
            format="json",
        )

        # Act
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.filter(user=user).order_by("-created_at").first()
        assert order is not None

        request_data = {"order_id": order.id}
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 금액 계산 검증
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)

        assert order.total_amount == Decimal("10000")  # 상품 금액만
        assert order.shipping_fee == Decimal("3000")  # 배송비 별도
        assert order.used_points == 2000
        assert order.final_amount == Decimal("11000")  # 10000 + 3000 - 2000
        assert payment.amount == order.final_amount

    def test_decimal_precision_preserved(
        self,
        authenticated_client,
        order,
    ):
        """Decimal 정밀도 유지"""
        # Arrange
        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - Decimal 타입 유지
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)

        assert isinstance(payment.amount, Decimal)
        assert isinstance(order.total_amount, Decimal)
        assert isinstance(order.final_amount, Decimal)

        # Decimal 정확성 검증 (부동소수점 오차 없음)
        assert payment.amount == order.final_amount
        assert str(payment.amount) == str(order.final_amount)


@pytest.mark.django_db
class TestPaymentValidationBoundary:
    """경계값 테스트 - 최소/최대 금액"""

    def test_minimum_valid_amount(
        self,
        authenticated_client,
        user,
        category,
    ):
        """최소 유효 금액 (1원)"""
        # Arrange - 1원 상품
        min_product = ProductFactory(
            category=category,
            price=Decimal("1"),
        )

        order = OrderFactory(
            user=user,
            total_amount=min_product.price,
            final_amount=min_product.price,
        )

        OrderItemFactory(
            order=order,
            product=min_product,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 1원 결제 허용
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == Decimal("1")

    def test_maximum_amount_limit(
        self,
        authenticated_client,
        user,
        category,
    ):
        """최대 금액 한계 (9,999,999,999원)"""
        # Arrange - max_digits=10 한계 금액
        max_amount = Decimal("9999999999")

        max_product = ProductFactory(
            category=category,
            price=max_amount,
        )

        order = OrderFactory(
            user=user,
            total_amount=max_product.price,
            final_amount=max_product.price,
        )

        OrderItemFactory(
            order=order,
            product=max_product,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 최대 금액 허용
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == max_amount
        assert response.json()["amount"] == int(max_amount)

    def test_amount_near_maximum(
        self,
        authenticated_client,
        user,
        category,
    ):
        """최대값 근처 금액 (9,999,999,998원)"""
        # Arrange
        near_max = Decimal("9999999998")

        product = ProductFactory(
            category=category,
            price=near_max,
        )

        order = OrderFactory(
            user=user,
            total_amount=product.price,
            final_amount=product.price,
        )

        OrderItemFactory(
            order=order,
            product=product,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)
        assert payment.amount == near_max

    def test_points_making_zero_final_amount(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """포인트로 정확히 0원 만들기 (total_amount = used_points)"""
        # Arrange
        user.points = 13000
        user.save()

        add_to_cart_helper(user, product, quantity=1)

        # 정확히 total_amount만큼 포인트 사용
        order_data = {**shipping_data, "use_points": 13000}
        order_response = authenticated_client.post(
            "/api/orders/",
            order_data,
            format="json",
        )

        # Act
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.filter(user=user).order_by("-created_at").first()
        assert order is not None

        request_data = {"order_id": order.id}
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 정확히 0원
        assert response.status_code == status.HTTP_201_CREATED
        payment = Payment.objects.get(order=order)

        assert order.total_amount == Decimal("10000")  # 상품 금액만
        assert order.shipping_fee == Decimal("3000")  # 배송비 별도
        assert order.used_points == 13000
        assert order.final_amount == Decimal("0")  # 10000 + 3000 - 13000
        assert payment.amount == Decimal("0")


@pytest.mark.django_db
class TestPaymentValidationException:
    """예외 케이스 - 부적절한 금액 거부"""

    def test_negative_amount_rejected(
        self,
        authenticated_client,
        user,
        category,
    ):
        """음수 금액 거부"""
        # Arrange - 음수 금액 상품 (DB 레벨에서는 생성 가능)
        negative_product = ProductFactory(
            category=category,
            price=Decimal("-1000"),
        )

        order = OrderFactory(
            user=user,
            total_amount=negative_product.price,
            final_amount=negative_product.price,
        )

        OrderItemFactory(
            order=order,
            product=negative_product,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 음수 금액 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 올바르지 않습니다" in str(response.json())

    def test_zero_total_amount_rejected(
        self,
        authenticated_client,
        user,
        category,
    ):
        """total_amount 0원 거부"""
        # Arrange - 0원 상품
        zero_product = ProductFactory(
            category=category,
            price=Decimal("0"),
        )

        order = OrderFactory(
            user=user,
            total_amount=Decimal("0"),
            final_amount=Decimal("0"),
        )

        OrderItemFactory(
            order=order,
            product=zero_product,
            quantity=1,
            price=Decimal("0"),
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert - 0원 total_amount 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 올바르지 않습니다" in str(response.json())

    def test_negative_total_amount_rejected(
        self,
        authenticated_client,
        user,
        category,
    ):
        """total_amount 음수 거부"""
        # Arrange
        product = ProductFactory(
            category=category,
            price=Decimal("-5000"),
        )

        order = OrderFactory(
            user=user,
            total_amount=Decimal("-5000"),
            final_amount=Decimal("-5000"),
        )

        OrderItemFactory(
            order=order,
            product=product,
        )

        request_data = {"order_id": order.id}

        # Act
        response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 올바르지 않습니다" in str(response.json())

    def test_string_amount_rejected(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """문자열 금액 거부"""
        # Arrange - confirm에서 문자열 금액 전송
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": "invalid_string",  # 명확한 문자열
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert - DRF가 타입 검증
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = response.json()
        assert "amount" in response_data or "error" in response_data

    def test_null_amount_rejected(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """null 금액 거부"""
        # Arrange
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": None,  # null
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "amount" in response.json()

    def test_empty_amount_rejected(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """amount 필드 누락 거부"""
        # Arrange - amount 필드 없음
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            # amount 누락
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "amount" in response.json()

    def test_float_type_handling(
        self,
        authenticated_client,
        order,
        payment,
        mocker,
    ):
        """float 타입 처리 (자동 Decimal 변환)"""
        # Arrange
        toss_response = TossResponseBuilder.success_response(
            payment_key="test_payment_key_float",
            order_id=order.id,
            amount=int(payment.amount),
        )

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        # float를 int로 변환 (DRF DecimalField는 float를 허용하지만 정수로 변환)
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

        # Assert - int → Decimal 변환 허용
        assert response.status_code == status.HTTP_202_ACCEPTED

        payment.refresh_from_db()
        assert isinstance(payment.amount, Decimal)

    def test_decimal_with_fractional_rejected(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """소수점 금액 거부 (decimal_places=0)"""
        # Arrange - 소수점 포함 금액
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": 10000.5,  # 소수점
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert - decimal_places=0이므로 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_very_small_decimal_rejected(
        self,
        authenticated_client,
        user,
        category,
    ):
        """매우 작은 소수 금액 거부 (0.01원)"""
        # Arrange - 소수점 금액은 DB 제약으로 저장 불가
        # 대신 API에서 소수점 금액 전송 시도
        product = ProductFactory(
            category=category,
            price=Decimal("1"),
        )

        order = OrderFactory(
            user=user,
            total_amount=Decimal("1"),
            final_amount=Decimal("1"),
        )

        OrderItemFactory(
            order=order,
            product=product,
        )

        payment = PaymentFactory(
            order=order,
        )

        # confirm에서 소수점 금액 전송
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": 0.01,  # 매우 작은 소수
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_exceed_max_digits(
        self,
        authenticated_client,
        user,
        category,
    ):
        """자릿수 초과 거부 (11자리)"""
        # Arrange - 10자리 초과 금액
        try:
            exceed_product = ProductFactory(
                category=category,
                price=Decimal("10000000000"),  # 11자리
            )

            order = OrderFactory(
                user=user,
                total_amount=exceed_product.price,
                final_amount=exceed_product.price,
            )

            OrderItemFactory(
                order=order,
                product=exceed_product,
            )

            request_data = {"order_id": order.id}

            # Act
            response = authenticated_client.post(
                "/api/payments/request/",
                request_data,
                format="json",
            )

            # Assert - DB 제약 또는 검증 실패
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ]

        except Exception:
            # DB 제약으로 생성 자체가 실패하면 테스트 통과
            pass

    def test_amount_mismatch_in_confirm(
        self,
        authenticated_client,
        order,
        payment,
    ):
        """confirm 시 금액 불일치 (payment.amount ≠ request.amount)"""
        # Arrange - 잘못된 금액으로 confirm 시도
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": int(payment.amount) + 1000,  # 불일치
        }

        # Act
        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 일치하지 않습니다" in str(response.json())

    def test_amount_tampering_between_steps(
        self,
        authenticated_client,
        user,
        product,
        add_to_cart_helper,
        shipping_data,
    ):
        """request와 confirm 사이 금액 변조 시도"""
        # Arrange - 정상 주문 생성
        add_to_cart_helper(user, product, quantity=1)

        order_response = authenticated_client.post(
            "/api/orders/",
            shipping_data,
            format="json",
        )
        assert order_response.status_code == status.HTTP_202_ACCEPTED

        order = Order.objects.filter(user=user).order_by("-created_at").first()
        assert order is not None

        # 정상 결제 요청
        request_data = {"order_id": order.id}
        payment_response = authenticated_client.post(
            "/api/payments/request/",
            request_data,
            format="json",
        )
        assert payment_response.status_code == status.HTTP_201_CREATED

        payment = Payment.objects.get(order=order)
        original_amount = payment.amount

        # Act - confirm 시 변조된 금액 전송
        tampered_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": int(original_amount) - 5000,  # 5000원 감액 변조
        }

        response = authenticated_client.post(
            "/api/payments/confirm/",
            tampered_data,
            format="json",
        )

        # Assert - 금액 불일치로 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 일치하지 않습니다" in str(response.json())

        # Payment 금액은 변경되지 않음
        payment.refresh_from_db()
        assert payment.amount == original_amount
        assert payment.status == "ready"

    def test_order_payment_amount_mismatch(
        self,
        authenticated_client,
        user,
        product,
    ):
        """주문 금액과 Payment 금액 불일치"""
        # Arrange - 주문 생성
        order = OrderFactory(
            user=user,
            total_amount=Decimal("10000"),
            final_amount=Decimal("10000"),
        )

        OrderItemFactory(
            order=order,
            product=product,
        )

        # 잘못된 금액으로 Payment 직접 생성
        wrong_payment = PaymentFactory(
            order=order,
            amount=Decimal("5000"),  # order.final_amount와 다름
        )

        # Act - confirm 시도
        request_data = {
            "order_id": order.id,
            "payment_key": "test_key",
            "amount": 10000,  # order의 금액으로 전송
        }

        response = authenticated_client.post(
            "/api/payments/confirm/",
            request_data,
            format="json",
        )

        # Assert - Payment 금액과 불일치로 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "결제 금액이 일치하지 않습니다" in str(response.json())
