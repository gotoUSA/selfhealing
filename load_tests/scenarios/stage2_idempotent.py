"""
Stage 2: Idempotency Test

목적: 중복 결제 방지 검증
- 동일 payment_key 반복 요청
- 첫 요청: 200/201 기대
- 두 번째 요청: 400/409 기대
- 200이 오면 CRITICAL FAILURE

실행:
    locust -f load_tests/scenarios/stage2_idempotent.py --host=http://localhost:8000 --users=30 --spawn-rate=10 --run-time=2m --headless
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


STAGE_NAME = "[Stage2]"

# 중복 결제 성공 카운터 (심각한 문제)
_duplicate_payment_success_count = 0


class IdempotencyUser(HttpUser):
    """
    Idempotency Test 사용자

    중복 결제 방지 검증. 실패 시 CRITICAL.
    """

    wait_time = between(1, 2)

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    @task(1)
    @tag("idempotency", "critical")
    def test_duplicate_payment(self):
        """
        중복 결제 시도 테스트

        같은 payment_key로 두 번 요청하여 idempotency 검증
        """
        global _duplicate_payment_success_count

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        self.cart_helper.clear_cart()
        product_id = random.choice(product_ids)
        self.cart_helper.add_item(product_id, 1)

        if not self.cart_helper.has_items():
            return

        # 주문 생성
        order_data = self.payment_helper.create_order(
            shipping_name="Idempotency Test",
            shipping_phone="010-0000-0000",
            shipping_postal_code="00000",
            shipping_address="테스트",
            shipping_address_detail="테스트",
        )

        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 동일한 payment_key 생성
        payment_key = f"idempotency_{int(time.time() * 1000)}_{random.randint(1, 999999)}"

        # === 첫 번째 요청 ===
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [1st]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
                first_success = True
            elif response.status_code == 400:
                # 비즈니스 에러 (재고 부족 등) - 중복 테스트 불가
                response.success()
                return
            else:
                response.failure(f"First payment failed: {response.status_code}")
                return

        # 약간의 지연 후 중복 요청
        time.sleep(0.1)

        # === 두 번째 요청 (중복) ===
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [DUPLICATE]",
            catch_response=True,
        ) as response:
            if response.status_code in [400, 409]:
                # 중복 방지 정상 동작
                response.success()
            elif response.status_code in [200, 201]:
                # ❌ 심각한 문제: 중복 결제가 성공함
                _duplicate_payment_success_count += 1
                response.failure(
                    f"🚨 CRITICAL: Duplicate payment succeeded! " f"payment_key={payment_key}, order_id={order_id}"
                )
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(1)
    @tag("idempotency", "rapid")
    def test_rapid_duplicate(self):
        """
        빠른 연속 중복 요청 테스트

        거의 동시에 같은 요청을 보내는 경우
        """
        global _duplicate_payment_success_count

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        self.cart_helper.clear_cart()
        product_id = random.choice(product_ids)
        self.cart_helper.add_item(product_id, 1)

        if not self.cart_helper.has_items():
            return

        # 주문 생성
        order_data = self.payment_helper.create_order()

        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        payment_key = f"rapid_{int(time.time() * 1000)}_{random.randint(1, 999999)}"

        # 첫 번째 요청 (결과 무시)
        self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RAPID-1st]",
        )

        # 즉시 두 번째 요청 (지연 없음)
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RAPID-DUP]",
            catch_response=True,
        ) as response:
            if response.status_code in [400, 409]:
                response.success()
            elif response.status_code in [200, 201]:
                _duplicate_payment_success_count += 1
                response.failure("🚨 CRITICAL: Rapid duplicate succeeded!")
            else:
                response.success()  # 기타 에러는 허용


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Idempotency 검증 결과"""
    global _duplicate_payment_success_count

    print("\n" + "=" * 60)
    print("🔐 STAGE 2: IDEMPOTENCY TEST RESULTS")
    print("=" * 60)

    if _duplicate_payment_success_count == 0:
        print("✅ IDEMPOTENCY TEST PASSED")
        print("   No duplicate payments were processed")
    else:
        print(f"❌ IDEMPOTENCY TEST FAILED")
        print(f"   🚨 CRITICAL: {_duplicate_payment_success_count} duplicate payments succeeded!")
        print("   ⚠️  This is a DATA INTEGRITY issue!")
        print("   📋 Action Required: Review payment confirmation logic")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
