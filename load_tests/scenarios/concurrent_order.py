"""
재고 경쟁 테스트 (Concurrent Order Test)

목적: 동시 주문 시 재고 정합성 검증
- 같은 상품에 대한 동시 주문
- Overselling 방지 확인
- 재고 차감 정확성

실행:
    locust -f load_tests/scenarios/concurrent_order.py \
        --host=http://localhost:8000 \
        --users=100 \
        --spawn-rate=50 \
        --run-time=2m

주의:
    - 이 테스트는 특정 상품(재고 제한)에 집중합니다
    - pytest 동시성 테스트에서 correctness는 이미 검증됨
    - Locust에서는 실제 부하 상황에서의 동작 확인이 목적
"""

import os
import sys

# 독립 실행 시 load_tests 패키지 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import random
import time
from locust import HttpUser, task, between, tag, events

from load_tests.config import (
    TEST_USER_COUNT,
    TEST_USER_PREFIX,
    TEST_USER_PASSWORD,
    ENDPOINTS,
)


# 테스트 결과 집계
class OrderStats:
    """주문 결과 통계"""
    successful_orders = 0
    failed_orders = 0
    stock_errors = 0


stats = OrderStats()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 통계 출력"""
    print("\n" + "=" * 50)
    print("📊 재고 경쟁 테스트 결과")
    print("=" * 50)
    print(f"✅ 성공한 주문: {stats.successful_orders}")
    print(f"❌ 실패한 주문: {stats.failed_orders}")
    print(f"📦 재고 부족 에러: {stats.stock_errors}")
    print("=" * 50)
    
    # Overselling 체크 안내
    print("\n⚠️  Overselling 확인 방법:")
    print("   1. DB에서 해당 상품의 현재 재고 확인")
    print("   2. 성공 주문 수 × 주문 수량 = 차감된 재고")
    print("   3. 초기 재고 - 차감 재고 = 현재 재고 (일치해야 함)")
    print("=" * 50 + "\n")


class ConcurrentOrderUser(HttpUser):
    """
    재고 경쟁 테스트 사용자
    
    시나리오:
    - 동일한 상품(들)에 대해 동시 주문
    - 재고가 한정된 상황에서 경쟁
    
    측정 지표:
    - 성공/실패 주문 수
    - 재고 부족 에러 수
    - Overselling 여부 (테스트 후 DB 확인)
    """
    
    wait_time = between(0.5, 1.5)  # 빠른 연속 요청
    
    # 타겟 상품 ID (테스트 시작 시 설정)
    _target_product_ids: list = []
    _initialized: bool = False
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        self.is_logged_in = False
        self.access_token = None
        
        # 타겟 상품 설정 (첫 사용자만)
        if not ConcurrentOrderUser._initialized:
            self._setup_target_products()
            ConcurrentOrderUser._initialized = True
        
        self.login()
    
    def _setup_target_products(self):
        """
        테스트할 상품 설정
        
        실제로는 재고가 제한된 특정 상품을 지정해야 함.
        여기서는 첫 5개 상품을 타겟으로 사용.
        """
        response = self.client.get(
            f"{ENDPOINTS['products']}?page=1&page_size=5",
            name="[Setup] Get Target Products",
        )
        
        if response.status_code == 200:
            results = response.json().get("results", [])
            ConcurrentOrderUser._target_product_ids = [p["id"] for p in results]
            
            print(f"\n🎯 타겟 상품 ID: {ConcurrentOrderUser._target_product_ids}")
            print("   (이 상품들에 대해 동시 주문 경쟁 테스트)")
    
    def login(self):
        """로그인"""
        if self.is_logged_in:
            return
        
        user_index = random.randint(0, TEST_USER_COUNT - 1)
        username = f"{TEST_USER_PREFIX}{user_index}"
        
        response = self.client.post(
            ENDPOINTS["login"],
            json={"username": username, "password": TEST_USER_PASSWORD},
            name="POST /api/auth/login/",
        )
        
        if response.status_code == 200:
            self.access_token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
            self.is_logged_in = True
    
    @task
    @tag("order", "concurrent", "stock")
    def race_for_order(self):
        """
        재고 경쟁 주문
        
        같은 상품에 대해 빠르게 주문을 생성하여
        재고 경쟁 상황을 시뮬레이션
        """
        if not self.is_logged_in:
            self.login()
        
        if not ConcurrentOrderUser._target_product_ids:
            return
        
        # 타겟 상품 중 하나 선택
        target_product_id = random.choice(ConcurrentOrderUser._target_product_ids)
        
        # 1. 장바구니에 추가
        cart_response = self.client.post(
            ENDPOINTS["cart_items"],
            json={
                "product_id": target_product_id,
                "quantity": 1,  # 재고 테스트를 위해 1개씩
            },
            name="POST /api/cart-items/ [race]",
        )
        
        if cart_response.status_code not in [200, 201]:
            return
        
        # 2. 장바구니 확인
        check_response = self.client.get(
            ENDPOINTS["cart_items"],
            name="GET /api/cart-items/ [race]",
        )
        
        if check_response.status_code != 200 or not check_response.json():
            return
        
        # 3. 주문 생성 (핵심 경쟁 구간)
        with self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": "경쟁테스트",
                "shipping_phone": "010-1111-1111",
                "shipping_postal_code": "11111",
                "shipping_address": "경쟁 테스트 주소",
                "shipping_address_detail": "경쟁동",
            },
            name="POST /api/orders/ [race]",
            catch_response=True
        ) as response:
            if response.status_code in [200, 201, 202]:
                stats.successful_orders += 1
                response.success()
                
                # 4. 결제까지 진행 (선택적)
                order_data = response.json()
                order_id = order_data.get("order_id")
                final_amount = order_data.get("final_amount")
                
                if order_id and final_amount:
                    payment_key = f"race_{int(time.time() * 1000)}_{random.randint(1, 999999)}"
                    
                    self.client.post(
                        ENDPOINTS["payment_confirm"],
                        json={
                            "payment_key": payment_key,
                            "order_id": order_id,
                            "amount": int(final_amount),
                        },
                        name="POST /api/payments/confirm/ [race]",
                    )
            
            elif response.status_code == 400:
                # 재고 부족 등 비즈니스 에러
                error_msg = response.text.lower()
                if "stock" in error_msg or "재고" in error_msg:
                    stats.stock_errors += 1
                else:
                    stats.failed_orders += 1
                response.success()  # 예상된 실패이므로 success 처리
            
            else:
                stats.failed_orders += 1
                response.failure(f"Order failed: {response.status_code}")
