"""
Idempotency Key 테스트

Purpose:
    Idempotency Key를 통한 중복 결제 요청 방지 검증

Test Categories:
    A. Basic Tests (기본 동작):
        - Idempotency Key로 결제 생성
        - 동일 키로 중복 요청 시 기존 결제 반환
        - 다른 키로 요청 시 새 결제 생성
    B. Concurrency Tests (동시성):
        - 동일 키로 동시 요청 시 최종 1개만 남음
        - 다른 키로 동시 요청 시 각각 생성
    C. API Integration Tests (API 통합):
        - /api/payments/request/ API에서 지원 확인

Idempotency Key 개념:
    - 클라이언트가 생성하는 고유 식별자
    - 동일한 키로 요청 시 기존 결제 반환 (멱등성 보장)
    - 네트워크 재시도, 중복 클릭 등으로 인한 중복 결제 방지

Concurrency Control:
    - DB Unique Constraint
    - select_for_update
    - 캐시 기반 멱등성 (프로덕션: Redis)
"""

import threading
import uuid
from decimal import Decimal

from django.core.cache import cache
from django.db import connection

import pytest
from rest_framework import status

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.services.payment_service import PaymentService
from shopping.tests.factories import (
    ProductFactory,
    UserFactory,
)


# =============================================================================
# 테스트 설정
# =============================================================================


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-idempotency-key",
    }
}


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


@pytest.fixture(autouse=True)
def use_locmem_cache(settings):
    """테스트에서 실제 캐시(LocMemCache) 사용"""
    settings.CACHES = TEST_CACHES
    cache.clear()
    yield
    cache.clear()


# =============================================================================
# A. 기본 동작 테스트 (Basic Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
class TestIdempotencyKeyBasic:
    """
    Idempotency Key 기본 동작 테스트

    Purpose:
        멱등성 보장 기본 동작 검증
    """

    def test_create_payment_with_idempotency_key_stores_key(self, category):
        """
        Purpose:
            idempotency_key로 결제 생성 시 해당 키가 저장됨
        Scenario:
            idempotency_key 포함하여 결제 생성
        Expected:
            Payment.idempotency_key에 저장됨
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        idempotency_key = str(uuid.uuid4())

        # Act
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert
        assert payment.idempotency_key == idempotency_key
        assert payment.order == order
        assert payment.amount == order.final_amount

    def test_duplicate_idempotency_key_returns_existing_payment(self, category):
        """
        Purpose:
            동일 idempotency_key로 요청 시 기존 결제 반환 (멱등성)
        Scenario:
            동일 키로 2번 결제 생성 요청
        Expected:
            동일한 Payment 반환, DB에 1개만 존재
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        idempotency_key = str(uuid.uuid4())

        # Act
        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert
        assert payment1.id == payment2.id, "동일한 Payment 반환"
        assert Payment.objects.filter(idempotency_key=idempotency_key).count() == 1

    def test_different_idempotency_keys_create_different_payments(self, category):
        """
        Purpose:
            서로 다른 idempotency_key는 서로 다른 결제 생성
        Scenario:
            다른 키로 2개 결제 생성 요청
        Expected:
            각각 별도의 Payment 생성
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order1 = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        order2 = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="102동",
        )

        key1 = str(uuid.uuid4())
        key2 = str(uuid.uuid4())

        # Act
        payment1 = PaymentService.create_payment(order=order1, payment_method="card", idempotency_key=key1)
        payment2 = PaymentService.create_payment(order=order2, payment_method="card", idempotency_key=key2)

        # Assert
        assert payment1.id != payment2.id
        assert payment1.idempotency_key != payment2.idempotency_key

    def test_create_payment_without_idempotency_key_works(self, category):
        """
        Purpose:
            idempotency_key 없이도 결제 생성 가능 (기존 동작 유지)
        Scenario:
            idempotency_key 없이 결제 생성
        Expected:
            Payment 생성, idempotency_key = None
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        # Act
        payment = PaymentService.create_payment(order=order, payment_method="card")

        # Assert
        assert payment.idempotency_key is None
        assert payment.order == order


# =============================================================================
# B. 동시성 테스트 (Concurrency Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
class TestIdempotencyKeyConcurrency:
    """
    Idempotency Key 동시성 테스트

    Purpose:
        동시 요청 시 멱등성 보장 검증
    Note:
        테스트 환경 LocMemCache는 스레드 간 공유 안 됨
        프로덕션 Redis는 중앙 집중형으로 동시 요청에서도 작동
    """

    def test_concurrent_same_key_final_1_payment(self, category):
        """
        Purpose:
            동일 idempotency_key로 5개 동시 요청 시 최종 1개만 남음
        Scenario:
            5개 스레드가 동일 키로 동시 결제 생성
        Expected:
            모두 성공, 최종 DB에 1개만 존재
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        idempotency_key = str(uuid.uuid4())
        results = []
        lock = threading.Lock()

        def create_payment_thread(thread_id):
            try:
                payment = PaymentService.create_payment(
                    order=Order.objects.get(pk=order.pk),
                    payment_method="card",
                    idempotency_key=idempotency_key,
                )
                with lock:
                    results.append({"thread_id": thread_id, "success": True, "payment_id": payment.id})
            except Exception as e:
                with lock:
                    results.append({"thread_id": thread_id, "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=create_payment_thread, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        successful_results = [r for r in results if r["success"]]
        assert len(successful_results) == 5, f"모든 스레드 성공. 실제: {len(successful_results)}"

        # 최종 DB에 1개만 존재
        final_payment_count = Payment.objects.filter(order=order).count()
        assert final_payment_count == 1, f"최종 1개. 실제: {final_payment_count}"

        # 최종 Payment가 idempotency_key 보유
        final_payment = Payment.objects.get(order=order)
        assert final_payment.idempotency_key == idempotency_key

    def test_concurrent_different_keys_all_created(self, category):
        """
        Purpose:
            서로 다른 idempotency_key로 5개 동시 요청 시 각각 생성
        Scenario:
            5개 스레드가 각각 다른 키로 동시 결제 생성
        Expected:
            5개 모두 성공, 5개 서로 다른 Payment 생성
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        products = [ProductFactory(category=category, stock=10, price=Decimal("10000")) for _ in range(5)]

        orders = []
        for i, product in enumerate(products):
            order = Order.objects.create(
                user=user,
                status="confirmed",
                total_amount=product.price,
                final_amount=product.price,
                shipping_name="홍길동",
                shipping_phone="010-1234-5678",
                shipping_postal_code="12345",
                shipping_address="서울시 강남구",
                shipping_address_detail=f"{100 + i}동",
            )
            orders.append(order)

        results = []
        lock = threading.Lock()

        def create_payment_thread(order_pk, idempotency_key, thread_id):
            try:
                payment = PaymentService.create_payment(
                    order=Order.objects.get(pk=order_pk),
                    payment_method="card",
                    idempotency_key=idempotency_key,
                )
                with lock:
                    results.append({
                        "thread_id": thread_id,
                        "success": True,
                        "payment_id": payment.id,
                        "idempotency_key": idempotency_key,
                    })
            except Exception as e:
                with lock:
                    results.append({"thread_id": thread_id, "success": False, "error": str(e)})
            finally:
                close_db_connection()

        # Act
        keys = [str(uuid.uuid4()) for _ in range(5)]
        threads = [
            threading.Thread(target=create_payment_thread, args=(orders[i].pk, keys[i], i))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        successful_results = [r for r in results if r["success"]]
        assert len(successful_results) == 5, f"5개 모두 성공. 실제: {len(successful_results)}"

        # 각각 다른 Payment ID
        payment_ids = set(r["payment_id"] for r in successful_results)
        assert len(payment_ids) == 5, "5개 서로 다른 Payment"


# =============================================================================
# C. API 통합 테스트 (API Integration Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
class TestIdempotencyKeyAPIIntegration:
    """
    Idempotency Key API 통합 테스트

    Purpose:
        /api/payments/request/ API에서 idempotency_key 지원 검증
    """

    def test_payment_request_api_with_idempotency_key(self, category, api_client):
        """
        Purpose:
            API에서 idempotency_key 지원 - 중복 요청 시 동일 결제 반환
        Scenario:
            동일 idempotency_key로 API 2번 호출
        Expected:
            동일한 payment_id 반환, DB에 1개만
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        idempotency_key = str(uuid.uuid4())
        api_client.force_authenticate(user=user)

        # Act 1 - 첫 번째 요청
        response1 = api_client.post(
            "/api/payments/request/",
            {"order_id": order.id, "payment_method": "card", "idempotency_key": idempotency_key},
            format="json",
        )

        # Assert 1
        assert response1.status_code == status.HTTP_201_CREATED
        payment_id1 = response1.data["payment_id"]

        # Act 2 - 동일 키로 두 번째 요청
        response2 = api_client.post(
            "/api/payments/request/",
            {"order_id": order.id, "payment_method": "card", "idempotency_key": idempotency_key},
            format="json",
        )

        # Assert 2
        assert response2.status_code == status.HTTP_201_CREATED
        payment_id2 = response2.data["payment_id"]

        assert payment_id1 == payment_id2, "동일 idempotency_key는 동일 Payment 반환"
        assert Payment.objects.filter(idempotency_key=idempotency_key).count() == 1

    def test_payment_request_api_without_idempotency_key(self, category, api_client):
        """
        Purpose:
            API에서 idempotency_key 없이도 동작 (기존 동작 유지)
        Scenario:
            idempotency_key 없이 API 호출
        Expected:
            결제 생성 성공, idempotency_key = None
        """
        # Arrange
        user = UserFactory(is_email_verified=True)
        product = ProductFactory(category=category, stock=10, price=Decimal("10000"))

        order = Order.objects.create(
            user=user,
            status="confirmed",
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101동",
        )

        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

        api_client.force_authenticate(user=user)

        # Act
        response = api_client.post(
            "/api/payments/request/",
            {"order_id": order.id, "payment_method": "card"},
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert "payment_id" in response.data

        payment = Payment.objects.get(pk=response.data["payment_id"])
        assert payment.idempotency_key is None
