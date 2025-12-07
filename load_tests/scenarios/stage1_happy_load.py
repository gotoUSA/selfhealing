"""
Stage 1: Happy Load Test

목적: 정상 성능 측정 (Baseline)
- Users: 50 → 100 → 200 (스케일링)
- Spawn Rate: 20
- Duration: 3~5분
- 전체 결제 플로우 반복
- P95/P99 레이턴시 측정
- 에러율 < 1% 목표

실행:
    locust -f load_tests/scenarios/stage1_happy_load.py --host=http://localhost:8000 --users=100 --spawn-rate=20 --run-time=3m --headless
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
from load_tests.config import SLA_TARGETS


STAGE_NAME = "[Stage1]"


class HappyLoadUser(HttpUser):
    """
    Happy Path Load Test 사용자

    정상적인 사용자 행동 패턴 시뮬레이션
    """

    wait_time = between(1, 3)

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    @task(5)
    @tag("load", "browse")
    def browse_products(self):
        """상품 목록 탐색"""
        page = random.randint(1, 3)
        self.product_helper.browse_products(page)

    @task(3)
    @tag("load", "browse")
    def view_product_detail(self):
        """상품 상세 조회"""
        product_id = self.product_helper.get_random_product_id()
        if product_id:
            self.product_helper.get_product_detail(product_id)

    @task(2)
    @tag("load", "cart")
    def manage_cart(self):
        """장바구니 관리"""
        if not self.login_helper.ensure_logged_in():
            return

        # 랜덤 상품 추가
        product_ids = self.product_helper.cached_product_ids
        if product_ids:
            self.cart_helper.add_random_items(product_ids, min_items=1, max_items=2)

        # 장바구니 조회
        self.cart_helper.get_cart_items()

    @task(1)
    @tag("load", "payment", "critical")
    def complete_purchase(self):
        """
        구매 완료 플로우

        전체 결제 플로우를 실행하고 성능 측정
        """
        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=3):
            return

        # 주문 생성
        order_data = self.payment_helper.create_order(
            shipping_name="Happy Load Test",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="테스트동 123호",
        )

        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("happy")

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [CRITICAL]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
            elif response.status_code == 400:
                # 비즈니스 에러 (재고 부족 등)
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 SLA 검증"""
    collector = get_metrics_collector()
    summary = collector.get_summary()

    print("\n" + "=" * 60)
    print("📊 STAGE 1: HAPPY LOAD TEST RESULTS")
    print("=" * 60)

    # SLA 검증
    sla_passed = True

    for name, stats in summary["endpoints"].items():
        if "payments/confirm" in name.lower():
            target = SLA_TARGETS.get("payment", {})
            if stats["p95"] > target.get("p95", 300):
                print(f"❌ SLA VIOLATION: Payment P95 {stats['p95']}ms > {target.get('p95', 300)}ms")
                sla_passed = False
            if stats["p99"] > target.get("p99", 500):
                print(f"❌ SLA VIOLATION: Payment P99 {stats['p99']}ms > {target.get('p99', 500)}ms")
                sla_passed = False

        if "products" in name.lower() and "GET" in name:
            target = SLA_TARGETS.get("products_list", {})
            if stats["error_rate"] > target.get("error_rate", 1) * 100:
                print(f"❌ SLA VIOLATION: {name} Error Rate {stats['error_rate']}%")
                sla_passed = False

    if summary["overall_error_rate"] > 1.0:
        print(f"❌ SLA VIOLATION: Overall Error Rate {summary['overall_error_rate']}% > 1%")
        sla_passed = False

    if sla_passed:
        print("✅ ALL SLA TARGETS MET")
    else:
        print("⚠️  SLA VIOLATIONS DETECTED - Review performance")

    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"RPS: {summary['rps']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
