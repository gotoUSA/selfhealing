from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connection

import pytest
from rest_framework.test import APIClient

from shopping.tests.factories import (
    ProductFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.performance
class TestConcurrentLoad:
    """대규모 동시 접속 테스트"""

    # 운영 동시성과 같은 폭으로 돌린다: gunicorn 4 프로세스 x gthread 4 = 동시 16 요청.
    # Django 는 스레드마다 별도 DB 커넥션을 열기 때문에 이 숫자가 곧 커넥션 수이고,
    # PostgreSQL 기본 max_connections 가 100 이라 여기서 100 스레드를 쓰면
    # 측정 대상(결제 동시성)이 아니라 커넥션 상한이 먼저 터진다.
    CONCURRENCY = 16
    REQUEST_COUNT = 1000

    def test_1000_payments_at_production_concurrency(self, user_factory):
        """1000건의 결제를 운영과 같은 동시 요청 폭(16)으로 처리"""
        # Arrange
        # 공유 상품 생성 (재고 1000개)
        product = ProductFactory(stock=self.REQUEST_COUNT)

        # 요청 수만큼 사용자 생성
        users = [user_factory(username=f'load_test_user_{i}') for i in range(self.REQUEST_COUNT)]

        def make_payment(user):
            client = APIClient()
            client.force_authenticate(user=user)

            # 1. 주문 및 결제 정보 생성 (DB)
            # 실제로는 프론트엔드에서 주문 생성 -> 결제창 -> 결제 승인 순서지만
            # 부하 테스트에서는 결제 승인 단계의 동시성을 집중 테스트하기 위해 미리 데이터 생성
            order = OrderFactory(user=user, status='pending')
            OrderItemFactory(order=order, product=product, quantity=1)
            payment = PaymentFactory(order=order, status='ready', amount=order.final_amount)

            # 2. 결제 승인 요청
            data = {
                # PaymentConfirmSerializer.order_id 는 우리 시스템의 Order PK 다
                # (토스에 보내는 orderId 인 Payment.toss_order_id 와는 다른 값)
                'payment_key': payment.payment_key,
                'order_id': order.id,
                'amount': int(order.final_amount)
            }
            try:
                return client.post('/api/payments/confirm/', data, format='json')
            finally:
                # 스레드별 커넥션 반납 - 하지 않으면 워커 수만큼 커넥션이 계속 잡혀 있다
                connection.close()

        # Act
        # Toss API Mocking (외부 API 호출 제외)
        with patch('shopping.utils.toss_payment.TossPaymentClient.confirm_payment') as mock_confirm:
            # 토스는 결제마다 다른 paymentKey 를 돌려준다. 고정 응답을 쓰면
            # 1000건이 같은 키로 저장되면서 unique 제약에 걸려, 측정 대신 하네스가 터진다.
            def fake_confirm(payment_key, order_id, amount):
                return {
                    'status': 'DONE',
                    'paymentKey': payment_key,
                    'approvedAt': '2025-01-01T00:00:00+09:00',
                    'orderId': order_id,
                    'totalAmount': int(amount),  # 토스 응답은 JSON number 다 (Decimal 을 그대로 두면 직렬화에서 터진다)
                }

            mock_confirm.side_effect = fake_confirm

            with ThreadPoolExecutor(max_workers=self.CONCURRENCY) as executor:
                results = list(executor.map(make_payment, users))

        # Assert
        # 1. 성공률 검증 (202 Accepted or 200 OK)
        # 비동기 처리 시 202, 동기 처리 시 200
        success_count = sum(1 for r in results if r.status_code in [200, 202])

        # 실패한 요청 로깅 (디버깅용)
        failures = [r.data for r in results if r.status_code not in [200, 202]]
        if failures:
            print(f"Failures sample: {failures[:5]}")

        assert success_count >= self.REQUEST_COUNT * 0.95, (
            f"95% 이상 성공해야 함: {success_count}/{self.REQUEST_COUNT}"
        )

        # 2. 재고 검증
        # 재고는 주문 생성 경로에서 차감된다 - 이 테스트는 이미 만들어진 주문의
        # "결제 승인"만 동시에 호출하므로 재고는 그대로여야 한다.
        # (stock >= 0 은 PositiveIntegerField 라 항상 참이어서 아무것도 검증하지 못했다)
        product.refresh_from_db()
        assert product.stock == self.REQUEST_COUNT, (
            f"결제 승인 경로는 재고를 건드리지 않아야 함: {product.stock}"
        )
