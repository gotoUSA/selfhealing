"""Redis 연결 실패 시 폴백 테스트"""

import pytest
from django.core.cache import cache
from redis.exceptions import ConnectionError as RedisConnectionError

from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentService
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)


@pytest.mark.django_db
class TestRedisFallback:
    """Redis 폴백"""

    def test_redis_connection_error_does_not_block_payment(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        payment = PaymentFactory(order=order, status="ready")

        mocker.patch(
            "shopping.utils.toss_payment.TossPaymentClient.confirm_payment",
            return_value=TossResponseBuilder.success_response(),
        )
        mocker.patch.object(cache, "get", side_effect=RedisConnectionError("Connection refused"))
        mocker.patch.object(cache, "set", side_effect=RedisConnectionError("Connection refused"))

        # Act
        result = PaymentService.confirm_payment_sync(
            payment=payment,
            payment_key="test_payment_key",
            order_id=order.id,
            amount=int(order.total_amount),
            user=user,
        )

        # Assert
        assert result["payment"].status == "done"

    def test_cache_miss_deletes_existing_and_creates_new_payment(self):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)

        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key="unique-key-12345",
        )
        payment1_id = payment1.id
        cache.clear()

        # Act
        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key="unique-key-12345",
        )

        # Assert - 캐시 미스 시 기존 Payment 삭제 후 새로 생성
        assert payment2.status == "ready"
        assert payment2.id != payment1_id
        assert not Payment.objects.filter(id=payment1_id).exists()

    def test_idempotency_key_in_cache_returns_same_payment(self, mocker):
        # Arrange
        user = UserFactory.with_points(5000)
        product = ProductFactory(stock=10)
        order = OrderFactory(user=user, status="confirmed", total_amount=product.price)
        OrderItemFactory(order=order, product=product)
        idempotency_key = "recovery-test-key-789"

        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )
        payment1_id = payment1.id

        # 캐시에서 payment_id를 반환하도록 모킹
        mocker.patch.object(
            cache,
            "get",
            return_value=payment1_id,
        )

        # Act
        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert
        assert payment2.id == payment1_id
