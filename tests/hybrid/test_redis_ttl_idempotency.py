"""Redis TTL 기반 멱등성/웹훅 중복 방어 테스트

Toss 실시간 결제 환경에 최적화된 TTL 설정:
- Idempotency Key: 60초
- Webhook Event: 60초
"""

import pytest

# 이 파일의 모든 테스트는 DB 및 Redis 필요
pytestmark = [pytest.mark.requires_db, pytest.mark.requires_redis]

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache

from shopping.models.payment import Payment
from shopping.models.webhook_event import WebhookEvent
from shopping.services.payment_service import IDEMPOTENCY_KEY_TTL, PaymentService
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory
from shopping.services.toss_webhook_service import (
    WEBHOOK_EVENT_TTL,
    TossWebhookService,
)


# 테스트용 실제 캐시 설정 (DummyCache 대신 LocMemCache 사용)
TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-redis-ttl",
    }
}


@pytest.fixture(autouse=True)
def use_locmem_cache(settings):
    """테스트에서 실제 캐시(LocMemCache) 사용"""
    settings.CACHES = TEST_CACHES
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestIdempotencyKeyRedisTTL:
    """Idempotency Key Redis TTL 테스트 (60초)"""

    def test_idempotency_ttl_constant_is_60_seconds(self):
        """TTL 상수가 60초로 설정되어 있는지 확인"""
        assert IDEMPOTENCY_KEY_TTL == 60

    def test_create_payment_sets_redis_cache(self):
        """결제 생성 시 Redis에 멱등성 키 저장"""
        # Arrange
        order = OrderFactory.pending()
        idempotency_key = "test_idempotency_key_001"

        # Act
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert - Redis에 저장되었는지 확인
        cache_key = f"payment:idempotency:{idempotency_key}"
        cached_value = cache.get(cache_key)
        assert cached_value == payment.id

    def test_duplicate_request_within_ttl_returns_existing_payment(self):
        """TTL 내 동일 키 요청 시 기존 결제 반환"""
        # Arrange
        order = OrderFactory.pending()
        idempotency_key = "test_idempotency_key_002"

        # Act - 첫 번째 요청
        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Act - 두 번째 요청 (동일 키)
        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert - 동일한 Payment 반환
        assert payment1.id == payment2.id
        assert Payment.objects.filter(order=order).count() == 1

    def test_request_after_ttl_creates_new_payment(self):
        """TTL 만료 후 동일 키 요청 시 새 결제 생성"""
        # Arrange
        order1 = OrderFactory.pending()
        order2 = OrderFactory.pending()
        idempotency_key = "test_idempotency_key_003"

        # Act - 첫 번째 요청
        payment1 = PaymentService.create_payment(
            order=order1,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # TTL 만료 시뮬레이션 (캐시 삭제)
        cache_key = f"payment:idempotency:{idempotency_key}"
        cache.delete(cache_key)

        # Act - TTL 만료 후 새 주문에 동일 키로 요청
        payment2 = PaymentService.create_payment(
            order=order2,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert - 새로운 Payment 생성됨
        assert payment1.id != payment2.id
        assert payment2.order == order2

    def test_idempotency_key_without_key_skips_cache(self):
        """멱등성 키 없이 요청 시 캐시 사용 안 함"""
        # Arrange
        order = OrderFactory.pending()

        # Act
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=None,
        )

        # Assert - 결제 생성됨, 캐시에 저장 안 됨
        assert payment is not None
        # 키가 None이므로 캐시 키도 없음


@pytest.mark.django_db
class TestWebhookEventRedisTTL:
    """Webhook Event Redis TTL 테스트 (60초)"""

    def test_webhook_ttl_constant_is_60_seconds(self):
        """TTL 상수가 60초로 설정되어 있는지 확인"""
        assert WEBHOOK_EVENT_TTL == 60

    def test_get_webhook_cache_key_format(self):
        """웹훅 캐시 키 형식 확인"""
        cache_key = TossWebhookService._get_webhook_cache_key("ORDER_001", "PAYMENT.DONE")
        assert cache_key == "webhook:toss:ORDER_001:PAYMENT.DONE"

    def test_mark_webhook_processed_sets_cache(self):
        """웹훅 처리 완료 시 Redis에 저장"""
        # Arrange
        order_id = "ORDER_001"
        event_type = "PAYMENT.DONE"

        # Act
        TossWebhookService.mark_webhook_processed(order_id, event_type)

        # Assert
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        assert cache.get(cache_key) == "1"

    def test_is_webhook_duplicate_returns_true_within_ttl(self):
        """TTL 내 동일 웹훅 중복 체크"""
        # Arrange
        order_id = "ORDER_002"
        event_type = "PAYMENT.DONE"
        TossWebhookService.mark_webhook_processed(order_id, event_type)

        # Act & Assert
        assert TossWebhookService.is_webhook_duplicate(order_id, event_type) is True

    def test_is_webhook_duplicate_returns_false_after_ttl(self):
        """TTL 만료 후 중복 아님"""
        # Arrange
        order_id = "ORDER_003"
        event_type = "PAYMENT.DONE"
        TossWebhookService.mark_webhook_processed(order_id, event_type)

        # TTL 만료 시뮬레이션
        cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
        cache.delete(cache_key)

        # Act & Assert
        assert TossWebhookService.is_webhook_duplicate(order_id, event_type) is False

    def test_is_webhook_duplicate_returns_false_for_new_event(self):
        """처음 받는 이벤트는 중복 아님"""
        # Act & Assert
        assert TossWebhookService.is_webhook_duplicate("NEW_ORDER_001", "PAYMENT.DONE") is False

    def test_different_event_types_are_independent(self):
        """다른 이벤트 타입은 독립적으로 처리"""
        # Arrange
        order_id = "ORDER_004"
        TossWebhookService.mark_webhook_processed(order_id, "PAYMENT.DONE")

        # Act & Assert - DONE은 중복, CANCELED는 아님
        assert TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.DONE") is True
        assert TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.CANCELED") is False


@pytest.mark.django_db
class TestWebhookEventLogging:
    """WebhookEvent 모델 로깅 테스트"""

    def test_log_webhook_event_creates_record(self):
        """웹훅 이벤트 로깅 시 DB 레코드 생성"""
        # Arrange
        order_id = "ORDER_LOG_001"
        event_type = "PAYMENT.DONE"

        # Act
        TossWebhookService.log_webhook_event(order_id, event_type)

        # Assert
        event = WebhookEvent.objects.filter(order_id=order_id).first()
        assert event is not None
        assert event.event_type == event_type
        assert event.source == "toss"
        assert f"toss:{order_id}:{event_type}" in event.event_id

    def test_log_webhook_event_multiple_events_same_order(self):
        """동일 주문에 여러 이벤트 로깅 가능"""
        # Arrange
        order_id = "ORDER_LOG_002"

        # Act
        TossWebhookService.log_webhook_event(order_id, "PAYMENT.DONE")
        TossWebhookService.log_webhook_event(order_id, "PAYMENT.CANCELED")

        # Assert
        events = WebhookEvent.objects.filter(order_id=order_id)
        assert events.count() == 2
        event_types = set(e.event_type for e in events)
        assert event_types == {"PAYMENT.DONE", "PAYMENT.CANCELED"}


@pytest.mark.django_db
class TestPaymentModelIdempotencyKey:
    """Payment 모델 idempotency_key 필드 테스트"""

    def test_idempotency_key_allows_duplicate_values(self):
        """idempotency_key 중복 허용 (unique 제약 제거됨)"""
        # Arrange
        order1 = OrderFactory.pending()
        order2 = OrderFactory.pending()
        same_key = "duplicate_key_test"

        # Act - 동일한 키로 두 개의 Payment 생성
        payment1 = Payment.objects.create(
            order=order1,
            toss_order_id=str(order1.id),
            amount=order1.final_amount,
            status="ready",
            idempotency_key=same_key,
        )
        payment2 = Payment.objects.create(
            order=order2,
            toss_order_id=str(order2.id),
            amount=order2.final_amount,
            status="ready",
            idempotency_key=same_key,
        )

        # Assert - 둘 다 생성됨 (unique 제약 없음)
        assert payment1.id != payment2.id
        assert payment1.idempotency_key == payment2.idempotency_key

    def test_idempotency_key_can_be_null(self):
        """idempotency_key는 null 허용"""
        # Arrange
        order = OrderFactory.pending()

        # Act
        payment = Payment.objects.create(
            order=order,
            toss_order_id=str(order.id),
            amount=order.final_amount,
            status="ready",
            idempotency_key=None,
        )

        # Assert
        assert payment.idempotency_key is None
