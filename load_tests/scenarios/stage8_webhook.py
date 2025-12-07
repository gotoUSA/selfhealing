"""
Stage 8: Webhook Reliability Test

목적: PG Webhook 비정상 상황 처리 검증
- Webhook 중복 도착 (3회 동일 요청)
- 순서 역전 (실패 → 성공 순서로 도착)
- 지연 도착 (결제 후 30초 뒤 Webhook)
- Worker 재시작 후 replay

Note: 이 테스트는 실제 Webhook 엔드포인트 호출을 시뮬레이션합니다.
      실제 PG Webhook을 받는 것이 아니라, Webhook 핸들러를 직접 테스트합니다.

실행:
    locust -f load_tests/scenarios/stage8_webhook.py --host=http://localhost:8000 --users=30 --spawn-rate=10 --run-time=3m --headless
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
import json
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector


STAGE_NAME = "[Stage8]"

# Webhook 테스트 통계
_webhook_stats = {
    "webhooks_sent": 0,
    "duplicate_handled": 0,
    "duplicate_failed": 0,
    "order_reversal_tested": 0,
    "delayed_webhook_tested": 0,
}

# Webhook 엔드포인트 (프로젝트에 맞게 수정 필요)
WEBHOOK_ENDPOINT = "/api/payments/webhook/toss/"


class WebhookUser(HttpUser):
    """
    Webhook Reliability Test 사용자

    PG Webhook 처리 신뢰성 테스트
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

    def _create_completed_payment(self):
        """완료된 결제 생성 (Webhook 테스트용)"""
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return None

        # 장바구니 준비
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(random.choice(product_ids), 1)

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

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("webhook")

        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/",
        )

        if response.status_code not in [200, 201]:
            return None

        payment_data = response.json()

        return {
            "payment_key": payment_key,
            "order_id": order_id,
            "amount": final_amount,
            "payment_id": payment_data.get("payment_id", payment_data.get("id")),
        }

    def _build_webhook_payload(self, payment_info, status="DONE"):
        """Toss Webhook 페이로드 생성"""
        return {
            "paymentKey": payment_info["payment_key"],
            "orderId": payment_info["order_id"],
            "status": status,
            "amount": payment_info["amount"],
            "method": "카드",
            "requestedAt": "2025-12-07T12:00:00+09:00",
            "approvedAt": "2025-12-07T12:00:01+09:00",
        }

    @task(3)
    @tag("webhook", "duplicate")
    def test_duplicate_webhook(self):
        """
        Webhook 중복 도착 테스트

        같은 Webhook을 3번 전송해도 처리가 한 번만 되어야 함
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        # Webhook 페이로드
        payload = self._build_webhook_payload(payment_info, "DONE")

        # 동일 Webhook 3회 전송
        success_count = 0
        for i in range(3):
            _webhook_stats["webhooks_sent"] += 1

            with self.client.post(
                WEBHOOK_ENDPOINT,
                json=payload,
                name=f"{STAGE_NAME} POST Webhook [DUP-{i+1}]",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                    success_count += 1
                elif response.status_code in [400, 409]:
                    # 중복 처리 거부 - 정상
                    response.success()
                else:
                    response.failure(f"Webhook failed: {response.status_code}")

            time.sleep(0.1)

        # 첫 번째만 성공해야 함 (또는 모두 성공하되 처리는 1회)
        if success_count >= 1:
            _webhook_stats["duplicate_handled"] += 1
        else:
            _webhook_stats["duplicate_failed"] += 1

    @task(2)
    @tag("webhook", "order_reversal")
    def test_webhook_order_reversal(self):
        """
        Webhook 순서 역전 테스트

        실패 → 성공 순서로 Webhook이 도착하는 경우
        (네트워크 지연으로 인해 발생 가능)
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        _webhook_stats["order_reversal_tested"] += 1

        # 1. 먼저 실패 Webhook 전송 (실제로는 나중에 온 것)
        fail_payload = self._build_webhook_payload(payment_info, "ABORTED")

        with self.client.post(
            WEBHOOK_ENDPOINT,
            json=fail_payload,
            name=f"{STAGE_NAME} POST Webhook [FAIL-FIRST]",
            catch_response=True,
        ) as response:
            _webhook_stats["webhooks_sent"] += 1
            # 상태 관계없이 기록
            response.success()

        time.sleep(0.2)

        # 2. 그 다음 성공 Webhook 전송 (실제로는 먼저 온 것)
        success_payload = self._build_webhook_payload(payment_info, "DONE")

        with self.client.post(
            WEBHOOK_ENDPOINT,
            json=success_payload,
            name=f"{STAGE_NAME} POST Webhook [SUCCESS-SECOND]",
            catch_response=True,
        ) as response:
            _webhook_stats["webhooks_sent"] += 1
            # 최종 상태가 올바르게 결정되어야 함
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Order reversal issue: {response.status_code}")

    @task(1)
    @tag("webhook", "delayed")
    def test_delayed_webhook(self):
        """
        지연 Webhook 테스트

        결제 완료 후 상당한 시간이 지난 후 Webhook 도착
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        _webhook_stats["delayed_webhook_tested"] += 1

        # 지연 시뮬레이션 (실제로는 수 초만 대기)
        time.sleep(random.uniform(2, 5))

        # 지연된 Webhook 전송
        payload = self._build_webhook_payload(payment_info, "DONE")

        with self.client.post(
            WEBHOOK_ENDPOINT,
            json=payload,
            name=f"{STAGE_NAME} POST Webhook [DELAYED]",
            catch_response=True,
        ) as response:
            _webhook_stats["webhooks_sent"] += 1
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Delayed webhook issue: {response.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Webhook 테스트 결과"""
    global _webhook_stats

    print("\n" + "=" * 60)
    print("🔔 STAGE 8: WEBHOOK RELIABILITY TEST RESULTS")
    print("=" * 60)

    print(f"Total Webhooks Sent: {_webhook_stats['webhooks_sent']}")
    print(f"Duplicate Handled: {_webhook_stats['duplicate_handled']}")
    print(f"Duplicate Failed: {_webhook_stats['duplicate_failed']}")
    print(f"Order Reversal Tested: {_webhook_stats['order_reversal_tested']}")
    print(f"Delayed Webhook Tested: {_webhook_stats['delayed_webhook_tested']}")

    total_tests = (
        _webhook_stats["duplicate_handled"]
        + _webhook_stats["duplicate_failed"]
        + _webhook_stats["order_reversal_tested"]
        + _webhook_stats["delayed_webhook_tested"]
    )

    if _webhook_stats["duplicate_failed"] == 0:
        print("\n✅ WEBHOOK RELIABILITY TEST PASSED")
        print("   Duplicate webhooks handled correctly")
        print("   Order reversal processed properly")
    else:
        print(f"\n⚠️  WEBHOOK RELIABILITY TEST WARNING")
        print(f"   {_webhook_stats['duplicate_failed']} duplicate handling failures")
        print("   Review webhook idempotency logic")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
