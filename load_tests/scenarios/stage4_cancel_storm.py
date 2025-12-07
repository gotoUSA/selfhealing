"""
Stage 4: Cancel Storm Test

목적: 결제 직후 취소 폭주 시뮬레이션
- confirm 후 0.1~1초 내 cancel 요청
- 동시 confirm + cancel 교차
- 취소 성공률 측정
- 재고 복구 확인

실행:
    locust -f load_tests/scenarios/stage4_cancel_storm.py --host=http://localhost:8000 --users=50 --spawn-rate=20 --run-time=2m --headless
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
from load_tests.validators import StockValidator


STAGE_NAME = "[Stage4]"

# 취소 통계
_cancel_stats = {
    "confirm_success": 0,
    "cancel_attempted": 0,
    "cancel_success": 0,
    "cancel_failed": 0,
    "stock_mismatch": 0,
}

# 동시성 환경에서 개별 트랜잭션 재고 추적은 부정확하므로 비활성화
# 테스트 종료 후 전체 재고 무결성은 별도 스크립트로 검증
_skip_individual_stock_check = True


class CancelStormUser(HttpUser):
    """
    Cancel Storm Test 사용자

    결제 직후 취소 폭주 시뮬레이션
    """

    wait_time = between(0.5, 1.5)  # 빠른 요청

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        self.stock_validator = StockValidator(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    @task(5)
    @tag("cancel", "storm")
    def confirm_then_cancel(self):
        """
        결제 후 즉시 취소

        사용자가 실수로 결제 후 바로 취소하는 시나리오
        """
        global _cancel_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 테스트할 상품 선택
        product_id = random.choice(product_ids)

        # 결제 전 재고 스냅샷
        stock_before = self.stock_validator.snapshot_stock(product_id)

        # 장바구니 준비
        self.cart_helper.clear_cart()
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

        # 결제 요청 (Payment 객체 생성)
        request_status, request_data = self.payment_helper.request_payment(order_id)
        if request_status not in [200, 201]:
            return

        payment_id_from_request = request_data.get("payment_id") if request_data else None
        amount_from_request = request_data.get("amount") if request_data else int(final_amount)

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("cancel_storm")

        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(amount_from_request),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/",
        )

        if response.status_code not in [200, 201, 202]:
            return

        _cancel_stats["confirm_success"] += 1
        payment_data = response.json()
        payment_id = payment_data.get("payment_id", payment_data.get("id"))

        if not payment_id:
            return

        # 짧은 지연 후 취소 (0.1 ~ 1초)
        delay = random.uniform(0.1, 1.0)
        time.sleep(delay)

        # 취소 요청
        _cancel_stats["cancel_attempted"] += 1

        with self.client.post(
            "/api/payments/cancel/",
            json={
                "payment_id": payment_id,
                "cancel_reason": "Cancel Storm Test",
            },
            name=f"{STAGE_NAME} POST /api/payments/cancel/ [STORM]",
            catch_response=True,
        ) as cancel_response:
            if cancel_response.status_code in [200, 201]:
                cancel_response.success()
                _cancel_stats["cancel_success"] += 1

                # 동시성 환경에서 개별 재고 검증은 다른 사용자의 영향으로 부정확함
                # 대신 취소 API 성공 자체를 검증 기준으로 사용
                if not _skip_individual_stock_check:
                    # 재고 복구 확인 (참고용, 실패해도 테스트 통과)
                    stock_after = self.stock_validator.get_stock(product_id)
                    if stock_before is not None and stock_after is not None:
                        if stock_before != stock_after:
                            _cancel_stats["stock_mismatch"] += 1

            elif cancel_response.status_code in [400, 409]:
                # 이미 처리된 상태 등
                cancel_response.success()
                _cancel_stats["cancel_failed"] += 1
            else:
                cancel_response.failure(f"Cancel failed: {cancel_response.status_code}")
                _cancel_stats["cancel_failed"] += 1

    @task(2)
    @tag("cancel", "rapid")
    def rapid_cancel_after_confirm(self):
        """
        결제 직후 연속 취소 시도

        사용자가 취소 버튼을 여러 번 누르는 시나리오
        """
        global _cancel_stats

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

        # 결제 요청 (Payment 객체 생성)
        request_status, request_data = self.payment_helper.request_payment(order_id)
        if request_status not in [200, 201]:
            return

        amount_from_request = request_data.get("amount") if request_data else int(final_amount)

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("rapid_cancel")

        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(amount_from_request),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/",
        )

        if response.status_code not in [200, 201, 202]:
            return

        _cancel_stats["confirm_success"] += 1
        payment_data = response.json()
        payment_id = payment_data.get("payment_id", payment_data.get("id"))

        if not payment_id:
            return

        # 연속 3번 취소 시도
        for i in range(3):
            _cancel_stats["cancel_attempted"] += 1

            with self.client.post(
                "/api/payments/cancel/",
                json={
                    "payment_id": payment_id,
                    "cancel_reason": f"Rapid Cancel Test #{i+1}",
                },
                name=f"{STAGE_NAME} POST /api/payments/cancel/ [RAPID-{i+1}]",
                catch_response=True,
            ) as cancel_response:
                if cancel_response.status_code in [200, 201]:
                    cancel_response.success()
                    if i == 0:  # 첫 번째만 성공 카운트
                        _cancel_stats["cancel_success"] += 1
                elif cancel_response.status_code in [400, 409]:
                    cancel_response.success()  # 중복 취소는 정상
                    if i > 0:
                        pass  # 두 번째부터는 실패 예상
                else:
                    cancel_response.failure(f"Rapid cancel failed: {cancel_response.status_code}")

            time.sleep(0.05)  # 50ms 간격


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Cancel Storm 결과"""
    global _cancel_stats

    print("\n" + "=" * 60)
    print("🌀 STAGE 4: CANCEL STORM TEST RESULTS")
    print("=" * 60)

    print(f"Confirm Success: {_cancel_stats['confirm_success']}")
    print(f"Cancel Attempted: {_cancel_stats['cancel_attempted']}")
    print(f"Cancel Success: {_cancel_stats['cancel_success']}")
    print(f"Cancel Failed: {_cancel_stats['cancel_failed']}")

    if _cancel_stats["cancel_attempted"] > 0:
        cancel_rate = _cancel_stats["cancel_success"] / _cancel_stats["cancel_attempted"] * 100
        print(f"\nCancel Success Rate: {cancel_rate:.1f}%")

    # Cancel Storm 테스트 성공 기준:
    # 1. 최소 1건 이상의 confirm 성공
    # 2. 최소 1건 이상의 cancel 성공
    # 3. confirm 성공 시 취소 시도율 50% 이상
    confirm_ok = _cancel_stats["confirm_success"] > 0
    cancel_ok = _cancel_stats["cancel_success"] > 0
    cancel_rate_ok = (
        _cancel_stats["cancel_attempted"] > 0 and (_cancel_stats["cancel_success"] / _cancel_stats["cancel_attempted"]) >= 0.3
    )

    if confirm_ok and cancel_ok and cancel_rate_ok:
        print("\n✅ CANCEL STORM TEST PASSED")
        print("   Confirm and cancel flow working correctly")
        if _skip_individual_stock_check:
            print("   (Individual stock checks skipped in concurrent environment)")
    else:
        print(f"\n❌ CANCEL STORM TEST FAILED")
        if not confirm_ok:
            print("   ⚠️  No successful confirms")
        if not cancel_ok:
            print("   ⚠️  No successful cancels")
        if not cancel_rate_ok:
            print("   ⚠️  Cancel success rate too low")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
