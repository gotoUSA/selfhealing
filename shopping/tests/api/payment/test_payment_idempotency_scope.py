"""
결제 멱등성 키(Redis 1층)의 범위 — 키는 그 주문에만 통한다

API 테스트 설정은 DummyCache 라 멱등성 키가 꺼져 있다. 여기서만 LocMemCache 로 켠다.
클라이언트가 만든 키만으로 저장하면, 60초 안에 같은 키를 보낸 다른 주문·다른 사용자에게
먼저 만든 결제를 돌려준다(응답에 그 주문의 주문번호·금액·첫 상품명이 실린다).
"""

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentService

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-payment-idempotency-scope",
    }
}

KEY = "client-generated-key-0001"


@pytest.fixture(autouse=True)
def use_locmem_cache(settings):
    settings.CACHES = TEST_CACHES
    cache.clear()
    yield
    cache.clear()


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _request_payment(client, order):
    return client.post(
        "/api/payments/request/",
        {"order_id": order.id, "idempotency_key": KEY},
        format="json",
    )


def _second_order(user, product):
    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="홍길동",
        shipping_phone="010-9999-8888",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250122000002",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )
    return order


@pytest.mark.django_db
class TestIdempotencyKeyIsScopedToTheOrder:
    """같은 키라도 다른 주문이면 그 주문의 결제를 받는다"""

    def test_another_users_order_with_the_same_key_gets_its_own_payment(self, user, order, other_user, other_user_order):
        first = _request_payment(_client(user), order)
        assert first.status_code == status.HTTP_201_CREATED

        second = _request_payment(_client(other_user), other_user_order)

        assert second.status_code == status.HTTP_201_CREATED
        data = second.json()
        assert data["order_id"] == other_user_order.id
        assert data["toss_order_id"] == other_user_order.order_number
        assert data["payment_id"] != first.json()["payment_id"]
        assert Payment.objects.get(order=other_user_order).id == data["payment_id"]
        assert Payment.objects.get(order=order).id == first.json()["payment_id"]

    def test_another_order_of_the_same_user_with_the_same_key_gets_its_own_payment(self, user, order, product):
        other_order = _second_order(user, product)
        client = _client(user)
        first = _request_payment(client, order)

        second = _request_payment(client, other_order)

        assert second.status_code == status.HTTP_201_CREATED
        data = second.json()
        assert data["order_id"] == other_order.id
        assert data["toss_order_id"] == other_order.order_number
        assert data["payment_id"] != first.json()["payment_id"]
        assert Payment.objects.get(order=other_order).id == data["payment_id"]

    def test_same_order_and_key_is_answered_from_redis(self, order):
        """서비스를 바로 부르면 시리얼라이저의 기존 결제 재사용을 거치지 않아 Redis 1층이 답한다"""
        first = PaymentService.create_payment(order=order, idempotency_key=KEY)

        again = PaymentService.create_payment(order=order, idempotency_key=KEY)

        assert again.id == first.id
        assert Payment.objects.filter(order=order).count() == 1
