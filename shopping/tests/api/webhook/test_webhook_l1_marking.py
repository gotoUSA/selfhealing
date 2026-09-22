"""
웹훅 1층(Redis TTL) 마킹 시점 테스트 — 커밋 뒤에만, 원자적으로

API 테스트 설정은 DummyCache 라 1층이 꺼져 있다. 여기서만 LocMemCache 로 켜고,
on_commit 콜백이 실제로 돌도록 transaction=True 로 돈다.

- 정상 처리 → 커밋 뒤 키가 생긴다 (재전송은 1층에서 걸러진다)
- 핸들러가 예외로 롤백 → 키가 없다 → 토스 재전송이 정상 처리된다 (구멍 2 닫힘)
- 마킹은 cache.add 라 겹쳐도 한 번 (구멍 1 닫힘)
"""

import pytest
from django.core.cache import cache
from rest_framework import status

from shopping.services.toss_webhook_service import TossWebhookService

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-webhook-l1-marking",
    }
}


@pytest.fixture(autouse=True)
def use_locmem_cache(settings):
    settings.CACHES = TEST_CACHES
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db(transaction=True)
class TestWebhookL1MarkingTiming:
    """1층 키는 커밋된 처리의 흔적이지, 시도의 흔적이 아니다"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url
        self.key = TossWebhookService._get_webhook_cache_key(str(order.id), "DONE")

    def test_key_is_set_only_after_commit(self, mock_get_payment, webhook_data_builder):
        """정상 처리 → 응답 뒤 키 존재 → 같은 웹훅 재전송은 1층에서 걸러져 DB 를 건드리지 않는다"""
        mock_get_payment()
        body = webhook_data_builder(
            order_id=str(self.order.id), amount=int(self.payment.amount)
        )
        assert cache.get(self.key) is None

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert cache.get(self.key) == "1"
        self.payment.refresh_from_db()
        assert self.payment.status == "done"

        # 재전송: 1층 hit → 핸들러가 DB 를 읽기 전에 return
        lookup = mock_get_payment()
        response = self.client.post(self.webhook_url, body, format="json")
        assert response.status_code == status.HTTP_200_OK
        lookup.assert_called_once()  # 진위 확인은 하지만
        self.product.refresh_from_db()
        assert self.product.sold_count == 1  # 처리는 한 번

    def test_rollback_leaves_no_key_so_retry_succeeds(
        self, mocker, mock_get_payment, webhook_data_builder
    ):
        """핸들러가 예외로 롤백 → 500 + 키 없음 → 토스 재전송(같은 본문)이 정상 처리된다"""
        mock_get_payment()
        body = webhook_data_builder(
            order_id=str(self.order.id), amount=int(self.payment.amount)
        )

        # 처리 도중(마킹 예약 뒤, 커밋 전) 터지는 예외
        boom = mocker.patch(
            "shopping.services.toss_webhook_service.PaymentLog.objects.create",
            side_effect=RuntimeError("db hiccup"),
        )
        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert cache.get(self.key) is None  # 롤백 → 흔적 없음
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        assert self.payment.status == "ready"
        assert self.order.status == "confirmed"

        # 토스 1차 재전송 (+1분 이내여도) → 1층 통과 → 정상 처리
        boom.stop()
        mocker.stopall()
        mock_get_payment()
        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert cache.get(self.key) == "1"
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        assert self.payment.status == "done"
        assert self.order.status == "paid"
        assert self.product.sold_count == 1

    def test_early_returns_do_not_mark(self, mock_get_payment, webhook_data_builder):
        """이미 완료된 결제로 온 웹훅(2층에서 return)은 1층 키를 만들지 않는다 — 키는 처리의 흔적"""
        self.payment.status = "done"
        self.payment.save()
        mock_get_payment()
        body = webhook_data_builder(
            order_id=str(self.order.id), amount=int(self.payment.amount)
        )

        response = self.client.post(self.webhook_url, body, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert cache.get(self.key) is None


@pytest.mark.django_db
class TestWebhookL1MarkIsAtomic:
    """마킹은 SETNX — 겹쳐 불려도 한 번"""

    def test_mark_uses_add_not_set(self, mocker):
        add = mocker.patch("shopping.services.toss_webhook_service.cache.add")
        set_ = mocker.patch("shopping.services.toss_webhook_service.cache.set")

        TossWebhookService.mark_webhook_processed("ORDER_X", "DONE")

        add.assert_called_once_with("webhook:toss:ORDER_X:DONE", "1", timeout=60)
        set_.assert_not_called()

    def test_second_mark_does_not_reset_ttl(self):
        """이미 있는 키를 add 로 다시 마킹해도 값·TTL 이 덮이지 않는다"""
        key = TossWebhookService._get_webhook_cache_key("ORDER_Y", "DONE")
        cache.set(key, "first", timeout=5)

        TossWebhookService.mark_webhook_processed("ORDER_Y", "DONE")

        assert cache.get(key) == "first"
