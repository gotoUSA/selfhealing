"""
구매자 사용자 (Buyer User)

실제 서비스의 5~10%를 차지하는 사용자 유형.
전체 구매 플로우를 완료하는 핵심 전환 사용자.
"""

import random
import time
from locust import task, tag

from .base import BaseUser
from load_tests.config import ENDPOINTS


class BuyerUser(BaseUser):
    """
    결제까지 완료하는 사용자
    
    행동 패턴:
    - 상품 조회 → 장바구니 추가 → 주문 생성 → 결제 완료
    - 일부는 중간에 이탈 (현실적 시뮬레이션)
    
    로그인: 필수
    """
    
    def on_start(self):
        """시작 시 로그인"""
        super().on_start()
        self.login()
    
    @task(3)
    @tag("read", "products")
    def browse_products(self):
        """상품 조회 (구매 전 탐색)"""
        self.client.get(
            f"{ENDPOINTS['products']}?page={random.randint(1, 3)}",
            name="GET /api/products/",
        )
        
        product_id = self.get_random_product_id()
        if product_id:
            self.client.get(
                ENDPOINTS["product_detail"].format(id=product_id),
                name="GET /api/products/{id}/",
            )
    
    @task(2)
    @tag("write", "order", "payment")
    def complete_purchase(self):
        """
        완전한 구매 플로우
        
        1. 상품 상세 조회
        2. 장바구니 추가
        3. 장바구니 확인
        4. 주문 생성
        5. 결제 승인
        """
        if not self.ensure_logged_in():
            return
        
        product_id = self.get_random_product_id()
        if not product_id:
            return
        
        # 1. 상품 상세 조회
        self.client.get(
            ENDPOINTS["product_detail"].format(id=product_id),
            name="GET /api/products/{id}/",
        )
        
        # 2. 장바구니 추가
        with self.client.post(
            ENDPOINTS["cart_items"],
            json={
                "product_id": product_id,
                "quantity": random.randint(1, 2),
            },
            name="POST /api/cart-items/",
            catch_response=True
        ) as response:
            if response.status_code not in [200, 201]:
                response.failure(f"Add to cart failed: {response.status_code}")
                return
            response.success()
        
        # 10% 확률로 여기서 이탈
        if random.random() < 0.1:
            return
        
        # 3. 장바구니 확인
        with self.client.get(
            ENDPOINTS["cart_items"],
            name="GET /api/cart-items/",
            catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"Get cart failed: {response.status_code}")
                return
            
            cart_items = response.json()
            response.success()
            
            if not cart_items:
                return
        
        # 4. 주문 생성
        with self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": "테스트 사용자",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구 테스트로 123",
                "shipping_address_detail": "테스트동 101호",
            },
            name="POST /api/orders/",
            catch_response=True
        ) as response:
            if response.status_code not in [200, 201, 202]:
                response.failure(f"Create order failed: {response.status_code}")
                return
            
            order_data = response.json()
            response.success()
            
            order_id = order_data.get("order_id")
            final_amount = order_data.get("final_amount")
            
            if not order_id or not final_amount:
                return
        
        # 5% 확률로 결제 전 이탈
        if random.random() < 0.05:
            return
        
        # 5. 결제 승인
        payment_key = f"test_key_{int(time.time() * 1000)}_{random.randint(1, 100000)}"
        
        self.client.post(
            ENDPOINTS["payment_confirm"],
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name="POST /api/payments/confirm/",
        )
    
    @task(1)
    @tag("read", "orders")
    def view_orders(self):
        """주문 내역 조회"""
        if not self.ensure_logged_in():
            return
        
        self.client.get(
            ENDPOINTS["orders"],
            name="GET /api/orders/",
        )
