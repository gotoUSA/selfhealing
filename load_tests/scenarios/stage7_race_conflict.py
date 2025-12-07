"""
Stage 7: Race Condition Test

목적: 동일 order_id에 대한 동시 접근 검증
- 같은 order_id로 여러 사용자/세션 동시 결제 시도
- 409/400 응답 기대
- 단 하나의 결제만 성공해야 함

실행:
    locust -f load_tests/scenarios/stage7_race_conflict.py --host=http://localhost:8000 --users=50 --spawn-rate=50 --run-time=2m --headless
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import time
import random
import threading
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector


STAGE_NAME = "[Stage7]"

# Race condition 통계
_race_stats = {
    "shared_orders": {},  # order_id -> [success_count, fail_count]
    "total_race_attempts": 0,
    "double_success": 0,  # 같은 주문에 2번 이상 성공 (심각한 문제!)
}
_race_lock = threading.Lock()


class RaceConditionUser(HttpUser):
    """
    Race Condition Test 사용자
    
    동일 리소스에 대한 동시 접근 테스트
    """
    
    wait_time = between(0.1, 0.5)  # 매우 빠른 요청
    
    # 공유 주문 풀 (여러 사용자가 같은 주문에 접근)
    _shared_order_pool = []
    _pool_lock = threading.Lock()
    _pool_initialized = False
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)
        
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # 공유 주문 풀 초기화 (최초 1회)
        self._maybe_init_shared_orders()

    def _maybe_init_shared_orders(self):
        """공유 주문 풀 초기화"""
        if RaceConditionUser._pool_initialized:
            return
        
        with RaceConditionUser._pool_lock:
            if RaceConditionUser._pool_initialized:
                return
            
            # 미리 몇 개의 주문 생성
            for _ in range(5):
                order_info = self._create_order_for_race()
                if order_info:
                    RaceConditionUser._shared_order_pool.append(order_info)
            
            RaceConditionUser._pool_initialized = True

    def _create_order_for_race(self):
        """Race 테스트용 주문 생성"""
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return None
        
        # 장바구니 준비
        self.cart_helper.clear_cart()
        product_id = random.choice(product_ids)
        self.cart_helper.add_item(product_id, 1)
        
        if not self.cart_helper.has_items():
            return None
        
        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return None
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return None
        
        return {
            "order_id": order_id,
            "final_amount": final_amount,
            "attempted": False,
        }

    @task(5)
    @tag("race", "same_order")
    def race_on_same_order(self):
        """
        같은 주문에 대한 동시 결제 시도
        
        여러 사용자가 같은 order_id로 결제 시도
        → 단 하나만 성공해야 함
        """
        global _race_stats
        
        if not self.login_helper.ensure_logged_in():
            return
        
        # 공유 주문 풀에서 주문 선택
        if not RaceConditionUser._shared_order_pool:
            return
        
        with RaceConditionUser._pool_lock:
            if not RaceConditionUser._shared_order_pool:
                return
            order_info = random.choice(RaceConditionUser._shared_order_pool)
        
        order_id = order_info["order_id"]
        final_amount = order_info["final_amount"]
        
        # 결제 시도
        payment_key = self.payment_helper.generate_payment_key("race")
        
        with _race_lock:
            _race_stats["total_race_attempts"] += 1
            if order_id not in _race_stats["shared_orders"]:
                _race_stats["shared_orders"][order_id] = {"success": 0, "fail": 0}
        
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RACE]",
            catch_response=True,
        ) as response:
            with _race_lock:
                if response.status_code in [200, 201]:
                    _race_stats["shared_orders"][order_id]["success"] += 1
                    
                    # 같은 주문에 2번 이상 성공하면 심각한 문제!
                    if _race_stats["shared_orders"][order_id]["success"] > 1:
                        _race_stats["double_success"] += 1
                        response.failure(
                            f"🚨 CRITICAL: Multiple payments on order {order_id}!"
                        )
                    else:
                        response.success()
                        
                elif response.status_code in [400, 409]:
                    # 이미 결제됨 - 정상
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()
                else:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.failure(f"Unexpected: {response.status_code}")

    @task(2)
    @tag("race", "new_order")
    def create_and_race(self):
        """
        새 주문 생성 후 즉시 경쟁
        
        주문 생성 직후 여러 결제 시도 (새 주문을 공유 풀에 추가)
        """
        if not self.login_helper.ensure_logged_in():
            return
        
        order_info = self._create_order_for_race()
        if not order_info:
            return
        
        # 공유 풀에 추가
        with RaceConditionUser._pool_lock:
            RaceConditionUser._shared_order_pool.append(order_info)
            # 풀 크기 제한 (오래된 주문 제거)
            if len(RaceConditionUser._shared_order_pool) > 20:
                RaceConditionUser._shared_order_pool.pop(0)
        
        # 즉시 결제 시도
        order_id = order_info["order_id"]
        final_amount = order_info["final_amount"]
        payment_key = self.payment_helper.generate_payment_key("race_new")
        
        with _race_lock:
            _race_stats["total_race_attempts"] += 1
            if order_id not in _race_stats["shared_orders"]:
                _race_stats["shared_orders"][order_id] = {"success": 0, "fail": 0}
        
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RACE-NEW]",
            catch_response=True,
        ) as response:
            with _race_lock:
                if response.status_code in [200, 201]:
                    _race_stats["shared_orders"][order_id]["success"] += 1
                    response.success()
                elif response.status_code in [400, 409]:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()
                else:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Race Condition 결과"""
    global _race_stats
    
    print("\n" + "=" * 60)
    print("⚡ STAGE 7: RACE CONDITION TEST RESULTS")
    print("=" * 60)
    
    print(f"Total Race Attempts: {_race_stats['total_race_attempts']}")
    print(f"Unique Orders Tested: {len(_race_stats['shared_orders'])}")
    print(f"Double Success (CRITICAL): {_race_stats['double_success']}")
    
    # 주문별 결과 분석
    orders_with_multiple_success = 0
    for order_id, stats in _race_stats["shared_orders"].items():
        if stats["success"] > 1:
            orders_with_multiple_success += 1
    
    print(f"\nOrders with Multiple Payments: {orders_with_multiple_success}")
    
    if _race_stats['double_success'] == 0 and orders_with_multiple_success == 0:
        print("\n✅ RACE CONDITION TEST PASSED")
        print("   No duplicate payments on same order")
        print("   Distributed lock working correctly")
    else:
        print(f"\n❌ RACE CONDITION TEST FAILED")
        print(f"   🚨 CRITICAL: {_race_stats['double_success']} double payments detected!")
        print("   ⚠️  Review distributed lock implementation")
        print("   📋 Action: Check SELECT FOR UPDATE / Redis Lock")
    
    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
