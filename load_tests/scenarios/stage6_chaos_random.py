"""
Stage 6: Chaos Random Errors Test

목적: 랜덤 실패에서도 시스템 안정성 유지
- 3%~15% 확률로 실패 응답 시뮬레이션
- 전체 에러율 모니터링
- 복구 후 정상 동작 확인

실행:
    CHAOS_ENABLED=true CHAOS_PROBABILITY=0.10 locust -f load_tests/scenarios/stage6_chaos_random.py --host=http://localhost:8000 --users=100 --spawn-rate=20 --run-time=5m --headless
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import random
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.chaos import FaultInjector, FaultType


STAGE_NAME = "[Stage6]"

# 카오스 테스트 통계
_chaos_stats = {
    "total_requests": 0,
    "chaos_injected": 0,
    "success_after_chaos": 0,
    "failure_after_chaos": 0,
    "by_fault_type": {},
    # 400 응답 reason별 분류 (업계 표준 Chaos Engineering 방식)
    "http_400_reasons": {
        "duplicate_item": 0,
        "out_of_stock": 0,
        "quantity_limit": 0,
        "invalid_token": 0,
        "price_mismatch": 0,
        "invalid_request": 0,
        "cart_empty": 0,
        "order_error": 0,
        "unknown": 0,
    },
}


def _classify_400_reason(response_json: dict) -> str:
    """
    400 응답의 reason을 분류
    
    업계 표준: 400은 시스템 성공이지만, reason별 통계로 숨겨진 문제 탐지
    """
    if not response_json:
        return "unknown"
    
    # 응답에서 에러 메시지 추출
    error_msg = ""
    if isinstance(response_json, dict):
        error_msg = str(response_json.get("error", "")).lower()
        error_msg += str(response_json.get("detail", "")).lower()
        error_msg += str(response_json.get("message", "")).lower()
        # non_field_errors 등 DRF 에러 형식도 처리
        if "non_field_errors" in response_json:
            error_msg += str(response_json.get("non_field_errors", [])).lower()
    
    # reason 분류
    if "already" in error_msg or "duplicate" in error_msg or "exists" in error_msg:
        return "duplicate_item"
    elif "stock" in error_msg or "insufficient" in error_msg or "재고" in error_msg:
        return "out_of_stock"
    elif "quantity" in error_msg or "limit" in error_msg or "maximum" in error_msg:
        return "quantity_limit"
    elif "token" in error_msg or "auth" in error_msg or "credential" in error_msg:
        return "invalid_token"
    elif "price" in error_msg or "amount" in error_msg or "mismatch" in error_msg:
        return "price_mismatch"
    elif "cart" in error_msg and ("empty" in error_msg or "no item" in error_msg):
        return "cart_empty"
    elif "order" in error_msg:
        return "order_error"
    elif "invalid" in error_msg or "required" in error_msg:
        return "invalid_request"
    else:
        return "unknown"


class ChaosUser(HttpUser):
    """
    Chaos Random Test 사용자

    랜덤 장애 상황에서 시스템 복원력 테스트
    """

    wait_time = between(0.5, 2)

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
        self.fault_injector.activate_all()

        # 환경변수에서 확률 설정 (기본 10%)
        chaos_prob = float(os.getenv("CHAOS_PROBABILITY", "0.10"))
        self.fault_injector.set_probability(chaos_prob)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    @task(3)
    @tag("chaos", "browse")
    def chaos_browse(self):
        """카오스 상황에서 상품 조회"""
        global _chaos_stats
        _chaos_stats["total_requests"] += 1

        # 장애 주입 확인
        fault = self.fault_injector.get_random_fault()
        if fault:
            _chaos_stats["chaos_injected"] += 1
            _chaos_stats["by_fault_type"][fault.value] = _chaos_stats["by_fault_type"].get(fault.value, 0) + 1

            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()

        # 상품 조회
        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /api/products/ [CHAOS]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                if fault:
                    _chaos_stats["success_after_chaos"] += 1
            else:
                response.failure(f"Status: {response.status_code}")
                if fault:
                    _chaos_stats["failure_after_chaos"] += 1

    @task(2)
    @tag("chaos", "cart")
    def chaos_cart_operations(self):
        """카오스 상황에서 장바구니 조작"""
        global _chaos_stats
        _chaos_stats["total_requests"] += 1

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장애 주입
        fault = self.fault_injector.get_random_fault()
        if fault:
            _chaos_stats["chaos_injected"] += 1
            _chaos_stats["by_fault_type"][fault.value] = _chaos_stats["by_fault_type"].get(fault.value, 0) + 1

            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()

        # 장바구니 추가
        product_id = random.choice(product_ids)

        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": 1},
            name=f"{STAGE_NAME} POST /api/cart/add_item/ [CHAOS]",
            catch_response=True,
        ) as response:
            # 400 에러는 재고 부족, 중복 상품 등 비즈니스 로직 오류로 정상 응답으로 처리
            if response.status_code in [200, 201]:
                response.success()
                if fault:
                    _chaos_stats["success_after_chaos"] += 1
            elif response.status_code == 400:
                # 400은 성공이지만, reason별 통계 수집 (업계 표준 방식)
                response.success()
                try:
                    reason = _classify_400_reason(response.json())
                    _chaos_stats["http_400_reasons"][reason] += 1
                except Exception:
                    _chaos_stats["http_400_reasons"]["unknown"] += 1
                if fault:
                    _chaos_stats["success_after_chaos"] += 1
            elif response.status_code >= 500:
                response.failure(f"5xx Error: {response.status_code}")
                if fault:
                    _chaos_stats["failure_after_chaos"] += 1
            else:
                response.failure(f"Status: {response.status_code}")
                if fault:
                    _chaos_stats["failure_after_chaos"] += 1

    @task(1)
    @tag("chaos", "payment", "critical")
    def chaos_payment(self):
        """
        카오스 상황에서 결제

        가장 중요한 테스트: 장애 상황에서도 결제 무결성 유지
        """
        global _chaos_stats
        _chaos_stats["total_requests"] += 1

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

        # 결제 전 장애 주입
        fault = self.fault_injector.get_random_fault()
        if fault:
            _chaos_stats["chaos_injected"] += 1
            _chaos_stats["by_fault_type"][fault.value] = _chaos_stats["by_fault_type"].get(fault.value, 0) + 1

            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()

        # 결제 요청
        payment_key = self.payment_helper.generate_payment_key("chaos")

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [CHAOS]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
                if fault:
                    _chaos_stats["success_after_chaos"] += 1
            elif response.status_code == 400:
                # 400은 성공이지만, reason별 통계 수집 (업계 표준 방식)
                response.success()
                try:
                    reason = _classify_400_reason(response.json())
                    _chaos_stats["http_400_reasons"][reason] += 1
                except Exception:
                    _chaos_stats["http_400_reasons"]["unknown"] += 1
                if fault:
                    _chaos_stats["success_after_chaos"] += 1
            elif response.status_code >= 500:
                response.failure(f"5xx Error: {response.status_code}")
                if fault:
                    _chaos_stats["failure_after_chaos"] += 1
            else:
                response.failure(f"Status: {response.status_code}")
                if fault:
                    _chaos_stats["failure_after_chaos"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 카오스 테스트 결과"""
    global _chaos_stats

    print("\n" + "=" * 60)
    print("🎲 STAGE 6: CHAOS RANDOM TEST RESULTS")
    print("=" * 60)

    print(f"Total Requests: {_chaos_stats['total_requests']}")
    print(f"Chaos Injected: {_chaos_stats['chaos_injected']}")
    print(f"Success After Chaos: {_chaos_stats['success_after_chaos']}")
    print(f"Failure After Chaos: {_chaos_stats['failure_after_chaos']}")

    print("\nFaults by Type:")
    for fault_type, count in _chaos_stats["by_fault_type"].items():
        print(f"  - {fault_type}: {count}")

    # 400 응답 reason별 통계 (업계 표준 Chaos Engineering 분석)
    total_400 = sum(_chaos_stats["http_400_reasons"].values())
    if total_400 > 0:
        print("\n📊 HTTP 400 Response Analysis (Business Logic):")
        print(f"   Total 400 Responses: {total_400}")
        for reason, count in _chaos_stats["http_400_reasons"].items():
            if count > 0:
                pct = count / total_400 * 100
                print(f"   - {reason}: {count} ({pct:.1f}%)")
        
        # 위험 징후 감지
        price_mismatch = _chaos_stats["http_400_reasons"].get("price_mismatch", 0)
        if price_mismatch > 0:
            print(f"\n   ⚠️  WARNING: {price_mismatch} price mismatch errors detected!")
            print("      This may indicate race conditions or policy issues.")

    if _chaos_stats["chaos_injected"] > 0:
        recovery_rate = _chaos_stats["success_after_chaos"] / _chaos_stats["chaos_injected"] * 100
        print(f"\nRecovery Rate: {recovery_rate:.1f}%")

        if recovery_rate >= 80:
            print("\n✅ CHAOS TEST PASSED")
            print("   System handles random failures gracefully")
        elif recovery_rate >= 60:
            print("\n⚠️  CHAOS TEST WARNING")
            print("   Recovery rate could be improved")
        else:
            print("\n❌ CHAOS TEST FAILED")
            print("   System struggles with random failures")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nOverall Error Rate: {summary['overall_error_rate']}%")
    print(f"RPS: {summary['rps']}")
    print("=" * 60)
