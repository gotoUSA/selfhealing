"""
Stage 3: Latency & Timeout Injection Test

목적: PG 지연/timeout 시뮬레이션
- 정상 응답 지연 (500ms ~ 3000ms)
- Timeout 발생 패턴 기록
- 클라이언트 재시도 동작 검증

실행:
    CHAOS_ENABLED=true locust -f load_tests/scenarios/stage3_latency.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=3m --headless
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
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.chaos import FaultInjector, FaultType


STAGE_NAME = "[Stage3]"

# 타임아웃/지연 통계
_latency_stats = {
    "injected_delays": 0,
    "timeout_count": 0,
    "recovery_success": 0,
    "recovery_failure": 0,
}


class LatencyUser(HttpUser):
    """
    Latency Injection Test 사용자
    
    PG 지연 상황에서 시스템 동작 검증
    """
    
    wait_time = between(1, 2)
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)
        
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        # 카오스 주입기 초기화
        self.fault_injector = FaultInjector()
        self.fault_injector.enable()
        self.fault_injector.activate_fault(FaultType.LATENCY)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    @task(3)
    @tag("latency", "payment")
    def payment_with_latency(self):
        """
        지연이 있는 결제 플로우
        
        클라이언트 측에서 인위적 지연 후 결제 요청
        (실제 PG 지연 시뮬레이션)
        """
        global _latency_stats
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return
        
        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return
        
        # 지연 주입 (10% 확률)
        latency_injected = self.fault_injector.maybe_inject_latency(
            min_ms=500,
            max_ms=2000,
        )
        
        if latency_injected:
            _latency_stats["injected_delays"] += 1
        
        # 결제 요청
        payment_key = self.payment_helper.generate_payment_key("latency")
        start_time = time.time()
        
        try:
            with self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [LATENCY]",
                catch_response=True,
                timeout=10,  # 10초 타임아웃
            ) as response:
                elapsed = (time.time() - start_time) * 1000
                
                if response.status_code in [200, 201, 400]:
                    response.success()
                    if latency_injected:
                        _latency_stats["recovery_success"] += 1
                else:
                    response.failure(f"Payment failed: {response.status_code}")
                    if latency_injected:
                        _latency_stats["recovery_failure"] += 1
                        
        except Exception as e:
            _latency_stats["timeout_count"] += 1
            if latency_injected:
                _latency_stats["recovery_failure"] += 1

    @task(1)
    @tag("latency", "timeout")
    def simulate_timeout_scenario(self):
        """
        타임아웃 시나리오 시뮬레이션
        
        긴 지연 후 재시도 동작 검증
        """
        global _latency_stats
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return
        
        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return
        
        payment_key = self.payment_helper.generate_payment_key("timeout")
        
        # 첫 번째 시도 (짧은 타임아웃으로 실패 유도)
        first_success = False
        try:
            response = self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [TIMEOUT-1st]",
                timeout=0.5,  # 매우 짧은 타임아웃
            )
            first_success = response.status_code in [200, 201, 400]
        except:
            _latency_stats["timeout_count"] += 1
        
        # 재시도 (정상 타임아웃)
        if not first_success:
            time.sleep(0.5)  # 잠시 대기 후 재시도
            
            with self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [TIMEOUT-RETRY]",
                catch_response=True,
                timeout=10,
            ) as response:
                if response.status_code in [200, 201, 400, 409]:
                    response.success()
                    _latency_stats["recovery_success"] += 1
                else:
                    response.failure(f"Retry failed: {response.status_code}")
                    _latency_stats["recovery_failure"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 지연 테스트 결과"""
    global _latency_stats
    
    print("\n" + "=" * 60)
    print("⏱️  STAGE 3: LATENCY INJECTION TEST RESULTS")
    print("=" * 60)
    
    print(f"Injected Delays: {_latency_stats['injected_delays']}")
    print(f"Timeout Count: {_latency_stats['timeout_count']}")
    print(f"Recovery Success: {_latency_stats['recovery_success']}")
    print(f"Recovery Failure: {_latency_stats['recovery_failure']}")
    
    total_injected = _latency_stats['injected_delays'] + _latency_stats['timeout_count']
    if total_injected > 0:
        recovery_rate = _latency_stats['recovery_success'] / total_injected * 100
        print(f"\nRecovery Rate: {recovery_rate:.1f}%")
        
        if recovery_rate >= 90:
            print("✅ LATENCY TEST PASSED - System handles delays well")
        else:
            print("⚠️  LATENCY TEST WARNING - Recovery rate below 90%")
    else:
        print("ℹ️  No latency injected (increase CHAOS_PROBABILITY)")
    
    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
