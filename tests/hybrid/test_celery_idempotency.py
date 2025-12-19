"""Celery 태스크 멱등성 테스트

같은 태스크가 2번 실행되어도 DB 변화는 1회만 발생해야 함
- Payment 생성의 멱등성 (idempotency_key 기반)
- 결제 취소의 멱등성 (이미 취소된 결제 재취소 방지)
"""

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from decimal import Decimal

import pytest
from django.core.cache import cache

from shopping.models.payment import Payment
from shopping.models.point import PointHistory
from shopping.services.payment_service import PaymentService
from shopping.tasks.point_tasks import add_points_after_payment
from shopping.tests.factories import (
    CategoryFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)


# 테스트용 캐시 설정 (DummyCache 대신 LocMemCache 사용)
TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-celery-idempotency",
    }
}


@pytest.fixture(autouse=True)
def celery_eager_mode(settings):
    """Celery를 동기 모드로 설정"""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def use_locmem_cache(settings):
    """테스트용 LocMemCache 사용"""
    settings.CACHES = TEST_CACHES
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db(transaction=True)
class TestPaymentIdempotency:
    """결제 생성 멱등성 테스트 (idempotency_key 기반)"""

    def test_same_idempotency_key_returns_existing_payment(self, use_locmem_cache):
        """동일한 idempotency_key로 2회 요청 시 같은 Payment 반환"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        order = OrderFactory.pending(user=user)
        idempotency_key = "test_key_12345"

        # Act - 첫 번째 요청
        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # 두 번째 요청 (동일 키)
        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert
        assert payment1.id == payment2.id
        assert Payment.objects.filter(order=order).count() == 1

    def test_different_idempotency_keys_create_different_payments(self, use_locmem_cache):
        """다른 idempotency_key로 요청 시 새 Payment 생성"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        order1 = OrderFactory.pending(user=user)
        order2 = OrderFactory.pending(user=user)

        # Act
        payment1 = PaymentService.create_payment(
            order=order1,
            payment_method="card",
            idempotency_key="key_001",
        )
        payment2 = PaymentService.create_payment(
            order=order2,
            payment_method="card",
            idempotency_key="key_002",
        )

        # Assert
        assert payment1.id != payment2.id

    def test_idempotency_key_cached(self, use_locmem_cache):
        """idempotency_key가 캐시에 저장됨"""
        # Arrange
        user = UserFactory(is_email_verified=True)
        order = OrderFactory.pending(user=user)
        idempotency_key = "cached_key_test"

        # Act
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert
        cache_key = f"payment:idempotency:{idempotency_key}"
        cached_payment_id = cache.get(cache_key)
        assert cached_payment_id == payment.id


@pytest.mark.django_db
class TestPointTaskIdempotency:
    """포인트 적립 태스크 멱등성 테스트"""

    def test_full_point_payment_skips_earning(self):
        """포인트 전액 결제 시 적립 스킵"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=15000)
        category = CategoryFactory()
        product = ProductFactory(category=category, price=Decimal("10000"))

        # 포인트 전액 결제 (final_amount = 0)
        order = OrderFactory.paid(
            user=user,
            total_amount=Decimal("10000"),
            shipping_fee=Decimal("3000"),
            used_points=13000,
            final_amount=Decimal("0"),
        )
        OrderItemFactory(order=order, product=product, price=product.price)
        PaymentFactory.done(order=order)

        initial_points = user.points

        # Act
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert
        user.refresh_from_db()
        assert result["status"] == "skipped"
        assert "포인트 전액 결제" in result["message"]
        assert user.points == initial_points

    def test_point_earning_creates_history_with_order(self):
        """포인트 적립 시 주문과 연결된 이력 생성"""
        # Arrange
        user = UserFactory(is_email_verified=True, points=0, membership_level="bronze")
        category = CategoryFactory()
        product = ProductFactory(category=category, price=Decimal("10000"))

        order = OrderFactory.paid(user=user, total_amount=Decimal("10000"))
        OrderItemFactory(order=order, product=product, price=product.price)
        PaymentFactory.done(order=order)

        # Act
        result = add_points_after_payment(user_id=user.id, order_id=order.id)

        # Assert
        assert result["status"] == "success"

        history = PointHistory.objects.filter(user=user, type="earn", order=order).first()
        assert history is not None
        assert history.points == 100  # 1% of 10000
