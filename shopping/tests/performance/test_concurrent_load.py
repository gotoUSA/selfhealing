import pytest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from rest_framework.test import APIClient

from shopping.models.product import Product
from shopping.tests.factories import (
    UserFactory,
    ProductFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.performance
class TestConcurrentLoad:
    """대규모 동시 접속 테스트"""

    def test_1000_concurrent_payments(self, user_factory):
        """1000명 동시 결제 처리"""
        # Arrange
        # 공유 상품 생성 (재고 1000개)
        product = ProductFactory(stock=1000)

        # 1000명의 사용자 생성
        users = [user_factory(username=f'load_test_user_{i}') for i in range(1000)]

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
                'payment_key': f'test_key_{user.id}',
                'order_id': order.order_number,
                'amount': int(order.final_amount)
            }
            return client.post('/api/payments/confirm/', data, format='json')

        # Act
        # Toss API Mocking (외부 API 호출 제외)
        with patch('shopping.utils.toss_payment.TossPaymentClient.confirm_payment') as mock_confirm:
            mock_confirm.return_value = {
                'status': 'DONE',
                'paymentKey': 'test_key',
                'approvedAt': '2025-01-01T00:00:00+09:00',
                'orderId': 'ORDER_ID',
                'totalAmount': 10000
            }

            # 100개의 스레드로 1000개 요청 처리
            with ThreadPoolExecutor(max_workers=100) as executor:
                results = list(executor.map(make_payment, users))

        # Assert
        # 1. 성공률 검증 (202 Accepted or 200 OK)
        # 비동기 처리 시 202, 동기 처리 시 200
        success_count = sum(1 for r in results if r.status_code in [200, 202])

        # 실패한 요청 로깅 (디버깅용)
        failures = [r.data for r in results if r.status_code not in [200, 202]]
        if failures:
            print(f"Failures sample: {failures[:5]}")

        assert success_count >= 950, f"95% 이상 성공해야 함: {success_count}/1000"

        # 2. 재고 검증
        product.refresh_from_db()
        # 동시성 이슈가 해결되었다면 재고는 0이어야 함 (1000개 판매)
        # 하지만 일부 실패가 허용되므로 0 이상이어야 함
        assert product.stock >= 0, f"재고는 음수가 될 수 없음: {product.stock}"
