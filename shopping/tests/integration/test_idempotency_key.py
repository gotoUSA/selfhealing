"""
Idempotency Key 테스트

- Idempotency Key 도입 검증
- 중복 결제 요청 방지 테스트

Idempotency Key 개념:
- 클라이언트가 생성하는 고유 식별자
- 동일한 키로 요청 시 기존 결제 반환 (멱등성 보장)
- 네트워크 재시도, 중복 클릭 등으로 인한 중복 결제 방지
"""

import threading
import uuid
from decimal import Decimal

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


def close_db_connection():
    """스레드별 DB 연결 정리"""
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestIdempotencyKeyBasic:
    """Idempotency Key 기본 동작 테스트"""

    def test_create_payment_with_idempotency_key(self, category):
        """
        idempotency_key로 결제 생성 시 해당 키가 저장됨
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
        동일한 idempotency_key로 결제 요청 시 기존 결제 반환 (멱등성)
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

        # Act - 첫 번째 결제 생성
        payment1 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Act - 동일한 키로 두 번째 결제 시도
        payment2 = PaymentService.create_payment(
            order=order,
            payment_method="card",
            idempotency_key=idempotency_key,
        )

        # Assert - 동일한 Payment 반환
        assert payment1.id == payment2.id
        assert Payment.objects.filter(idempotency_key=idempotency_key).count() == 1

    def test_different_idempotency_keys_create_different_payments(self, category):
        """
        서로 다른 idempotency_key는 서로 다른 결제를 생성
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
        payment1 = PaymentService.create_payment(
            order=order1,
            payment_method="card",
            idempotency_key=key1,
        )

        payment2 = PaymentService.create_payment(
            order=order2,
            payment_method="card",
            idempotency_key=key2,
        )

        # Assert
        assert payment1.id != payment2.id
        assert payment1.idempotency_key != payment2.idempotency_key

    def test_create_payment_without_idempotency_key(self, category):
        """
        idempotency_key 없이도 결제 생성 가능 (기존 동작 유지)
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
        payment = PaymentService.create_payment(
            order=order,
            payment_method="card",
        )

        # Assert
        assert payment.idempotency_key is None
        assert payment.order == order


@pytest.mark.django_db(transaction=True)
class TestIdempotencyKeyConcurrency:
    """Idempotency Key 동시성 테스트"""

    def test_concurrent_requests_with_same_idempotency_key(self, category):
        """
        동일한 idempotency_key로 동시 요청 시 1개만 생성

        시나리오:
        - 5개 스레드가 동일한 idempotency_key로 동시에 결제 생성 시도
        - 결과: 1개의 Payment만 생성됨
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
            """결제 생성 스레드"""
            try:
                payment = PaymentService.create_payment(
                    order=Order.objects.get(pk=order.pk),
                    payment_method="card",
                    idempotency_key=idempotency_key,
                )
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "payment_id": payment.id,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                        }
                    )
            finally:
                close_db_connection()

        # Act - 5개 스레드 동시 실행
        threads = [threading.Thread(target=create_payment_thread, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        # Payment는 1개만 존재해야 함
        payment_count = Payment.objects.filter(idempotency_key=idempotency_key).count()
        assert payment_count == 1, f"Payment가 1개만 있어야 함. 실제: {payment_count}"

        # 모든 스레드가 동일한 payment_id를 받아야 함
        successful_results = [r for r in results if r["success"]]
        payment_ids = set(r["payment_id"] for r in successful_results)
        assert len(payment_ids) == 1, f"모든 스레드가 동일한 Payment를 받아야 함. 실제: {payment_ids}"

    def test_concurrent_requests_with_different_idempotency_keys(self, category):
        """
        서로 다른 idempotency_key로 동시 요청 시 각각 생성
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
            """결제 생성 스레드"""
            try:
                payment = PaymentService.create_payment(
                    order=Order.objects.get(pk=order_pk),
                    payment_method="card",
                    idempotency_key=idempotency_key,
                )
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": True,
                            "payment_id": payment.id,
                            "idempotency_key": idempotency_key,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append(
                        {
                            "thread_id": thread_id,
                            "success": False,
                            "error": str(e),
                        }
                    )
            finally:
                close_db_connection()

        # Act - 5개 스레드, 각각 다른 idempotency_key로 실행
        keys = [str(uuid.uuid4()) for _ in range(5)]
        threads = [
            threading.Thread(
                target=create_payment_thread,
                args=(orders[i].pk, keys[i], i),
            )
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        successful_results = [r for r in results if r["success"]]
        assert len(successful_results) == 5, f"5개 모두 성공해야 함. 실제: {len(successful_results)}"

        # 각각 다른 Payment ID
        payment_ids = set(r["payment_id"] for r in successful_results)
        assert len(payment_ids) == 5, f"5개의 서로 다른 Payment가 생성되어야 함"


@pytest.mark.django_db(transaction=True)
class TestIdempotencyKeyAPIIntegration:
    """Idempotency Key API 통합 테스트"""

    def test_payment_request_api_with_idempotency_key(self, category, api_client):
        """
        /api/payments/request/ API에서 idempotency_key 지원 테스트
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

        # 인증
        api_client.force_authenticate(user=user)

        # Act - 첫 번째 요청
        response1 = api_client.post(
            "/api/payments/request/",
            {
                "order_id": order.id,
                "payment_method": "card",
                "idempotency_key": idempotency_key,
            },
            format="json",
        )

        # Assert - 첫 번째 요청 성공
        assert response1.status_code == status.HTTP_201_CREATED
        payment_id1 = response1.data["payment_id"]

        # Act - 동일한 키로 두 번째 요청
        response2 = api_client.post(
            "/api/payments/request/",
            {
                "order_id": order.id,
                "payment_method": "card",
                "idempotency_key": idempotency_key,
            },
            format="json",
        )

        # Assert - 두 번째 요청도 성공하고 동일한 payment_id 반환
        assert response2.status_code == status.HTTP_201_CREATED
        payment_id2 = response2.data["payment_id"]

        assert payment_id1 == payment_id2, "동일한 idempotency_key는 동일한 Payment를 반환해야 함"

        # DB에 1개만 존재
        assert Payment.objects.filter(idempotency_key=idempotency_key).count() == 1

    def test_payment_request_api_without_idempotency_key(self, category, api_client):
        """
        /api/payments/request/ API에서 idempotency_key 없이도 동작
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
            {
                "order_id": order.id,
                "payment_method": "card",
            },
            format="json",
        )

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert "payment_id" in response.data

        payment = Payment.objects.get(pk=response.data["payment_id"])
        assert payment.idempotency_key is None
