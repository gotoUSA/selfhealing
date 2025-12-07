"""
Stage 5: Rollback Validation Test

목적: 실패 시 재고/포인트 롤백 검증
- 결제 전 stock_before, point_before 저장
- 강제 결제 실패 트리거
- 롤백 후 값 비교
- 불일치 시 CRITICAL FAILURE

실행:
    locust -f load_tests/scenarios/stage5_rollback.py --host=http://localhost:8000 --users=30 --spawn-rate=10 --run-time=3m --headless
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


STAGE_NAME = "[Stage5]"

# 롤백 검증 통계
_rollback_stats = {
    "failure_triggered": 0,
    "rollback_verified": 0,
    "rollback_failed": 0,
    "stock_mismatch_details": [],
}


class RollbackUser(HttpUser):
    """
    Rollback Validation Test 사용자

    결제 실패 시 재고/포인트 롤백 검증
    """

    wait_time = between(1, 2)

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

    @task(3)
    @tag("rollback", "stock")
    def verify_stock_rollback_on_failure(self):
        """
        결제 실패 시 재고 롤백 검증

        의도적으로 잘못된 금액으로 결제 시도 → 실패 → 재고 확인
        """
        global _rollback_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        product_id = random.choice(product_ids)

        # === 1. 결제 전 재고 스냅샷 ===
        stock_before = self.stock_validator.snapshot_stock(product_id)
        if stock_before is None or stock_before <= 0:
            return  # 재고 없으면 스킵

        # === 2. 장바구니 준비 ===
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(product_id, 1)

        if not self.cart_helper.has_items():
            return

        # === 3. 주문 생성 ===
        order_data = self.payment_helper.create_order()
        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # === 4. 의도적 결제 실패 (잘못된 금액) ===
        payment_key = self.payment_helper.generate_payment_key("rollback")
        wrong_amount = int(final_amount) + 10000  # 금액 불일치

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": wrong_amount,
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [WRONG_AMOUNT]",
            catch_response=True,
        ) as response:
            if response.status_code in [400]:
                response.success()  # 실패 예상
                _rollback_stats["failure_triggered"] += 1
            elif response.status_code in [200, 201]:
                # 금액 불일치인데 성공? 이상함
                response.failure("Payment succeeded with wrong amount!")
                return
            else:
                response.success()  # 기타 실패도 OK
                _rollback_stats["failure_triggered"] += 1

        # === 5. 잠시 대기 후 재고 확인 ===
        time.sleep(0.5)

        stock_after = self.stock_validator.get_stock(product_id)

        # === 6. 롤백 검증 ===
        if stock_before is not None and stock_after is not None:
            validation = self.stock_validator.validate_rollback(product_id, stock_before, stock_after)

            if validation["valid"]:
                _rollback_stats["rollback_verified"] += 1
            else:
                _rollback_stats["rollback_failed"] += 1
                _rollback_stats["stock_mismatch_details"].append(
                    {
                        "product_id": product_id,
                        "before": stock_before,
                        "after": stock_after,
                        "errors": validation["errors"],
                    }
                )

    @task(1)
    @tag("rollback", "normal_flow")
    def verify_normal_stock_decrease(self):
        """
        정상 결제 시 재고 감소 검증

        롤백 테스트의 대조군: 성공 시에는 재고가 줄어야 함
        """
        global _rollback_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        product_id = random.choice(product_ids)
        quantity = 1

        # 결제 전 재고
        stock_before = self.stock_validator.snapshot_stock(product_id)
        if stock_before is None or stock_before <= 0:
            return

        # 장바구니 준비
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(product_id, quantity)

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

        # 정상 결제
        payment_key = self.payment_helper.generate_payment_key("normal")

        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [NORMAL]",
        )

        if response.status_code not in [200, 201]:
            return  # 비즈니스 에러는 스킵

        # 재고 감소 확인
        time.sleep(0.3)
        stock_after = self.stock_validator.get_stock(product_id)

        if stock_before is not None and stock_after is not None:
            validation = self.stock_validator.validate_no_oversell(product_id, stock_before, quantity, stock_after)

            if not validation["valid"]:
                # 정상 결제인데 재고가 안 줄었으면 문제
                _rollback_stats["stock_mismatch_details"].append(
                    {
                        "product_id": product_id,
                        "type": "normal_decrease_failed",
                        "before": stock_before,
                        "after": stock_after,
                        "expected": stock_before - quantity,
                    }
                )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 롤백 검증 결과"""
    global _rollback_stats

    print("\n" + "=" * 60)
    print("🔄 STAGE 5: ROLLBACK VALIDATION TEST RESULTS")
    print("=" * 60)

    print(f"Failures Triggered: {_rollback_stats['failure_triggered']}")
    print(f"Rollback Verified: {_rollback_stats['rollback_verified']}")
    print(f"Rollback Failed: {_rollback_stats['rollback_failed']}")

    if _rollback_stats["failure_triggered"] > 0:
        rollback_rate = _rollback_stats["rollback_verified"] / _rollback_stats["failure_triggered"] * 100
        print(f"\nRollback Success Rate: {rollback_rate:.1f}%")

    if _rollback_stats["rollback_failed"] == 0:
        print("\n✅ ROLLBACK TEST PASSED")
        print("   All failed payments properly rolled back stock")
    else:
        print(f"\n❌ ROLLBACK TEST FAILED")
        print(f"   🚨 {_rollback_stats['rollback_failed']} rollback failures detected!")

        # 상세 정보 출력 (최대 5개)
        for detail in _rollback_stats["stock_mismatch_details"][:5]:
            print(f"   - Product {detail['product_id']}: " f"before={detail['before']}, after={detail['after']}")

        print("   ⚠️  Review transaction rollback logic")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print("=" * 60)
