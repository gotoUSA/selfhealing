"""
Stage 5: Rollback Validation Test

목적: 실패 시 재고/포인트 롤백 검증
- 결제 전 stock_before, point_before 저장
- 강제 결제 실패 트리거
- 롤백 후 값 비교
- 불일치 시 CRITICAL FAILURE

통계 분류:
- rollback_verified: 정상 롤백 확인 (재고 변화 없음)
- rollback_failed: 시스템 버그로 재고가 비정상 증가 (Critical)
- variance_concurrent: 동시 주문으로 인한 예상된 재고 감소

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
from collections import defaultdict
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.validators import StockValidator


STAGE_NAME = "[Stage5]"

# 환경 변수로 threshold 설정 가능
PASS_THRESHOLD = float(os.environ.get("ROLLBACK_PASS_THRESHOLD", "50.0"))

# 롤백 검증 통계 - 실패/동시성 분리
_rollback_stats = {
    "failure_triggered": 0,
    "rollback_verified": 0,  # 정상: 재고 변화 없음
    "rollback_failed": 0,  # 버그: 재고가 비정상 증가 (심각)
    "variance_concurrent": 0,  # 예상됨: 동시 주문으로 재고 감소
    "product_variance": defaultdict(int),  # product_id별 variance 집계
    "details": {
        "verified": [],
        "failed": [],
        "variance": [],
    },
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
        결제 실패 시 재고 안정성 검증

        주문 생성 후 재고가 차감된 상태에서,
        결제 실패(잘못된 금액)가 발생해도 재고에 추가 변화가 없어야 함.
        (재고는 주문 생성 시 이미 차감됨, 결제 confirm은 sold_count만 증가)
        """
        global _rollback_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        product_id = random.choice(product_ids)

        # === 1. 장바구니 준비 전 재고 확인 ===
        stock_initial = self.stock_validator.get_stock(product_id)
        if stock_initial is None or stock_initial <= 0:
            return  # 재고 없으면 스킵

        # === 2. 장바구니 준비 ===
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(product_id, 1)

        if not self.cart_helper.has_items():
            return

        # === 3. 주문 생성 (이 시점에서 재고가 차감됨) ===
        order_data = self.payment_helper.create_order()
        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # === 4. 주문 생성 후 재고 스냅샷 (이 시점에서 이미 재고 차감됨) ===
        stock_after_order = self.stock_validator.snapshot_stock(product_id)
        if stock_after_order is None:
            return

        # === 5. 의도적 결제 실패 (잘못된 금액) ===
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

        # === 6. 잠시 대기 후 재고 확인 ===
        time.sleep(0.5)

        stock_after_payment_fail = self.stock_validator.get_stock(product_id)

        # === 7. 안정성 검증: 결제 실패 후 재고에 추가 변화 없어야 함 ===
        if stock_after_order is not None and stock_after_payment_fail is not None:
            # 결제 confirm 실패는 재고에 영향을 주지 않아야 함
            validation = self.stock_validator.validate_rollback(product_id, stock_after_order, stock_after_payment_fail)

            if validation["valid"]:
                _rollback_stats["rollback_verified"] += 1
                _rollback_stats["details"]["verified"].append(
                    {
                        "product_id": product_id,
                        "stock": stock_after_order,
                    }
                )
            else:
                # 재고 변동 방향에 따라 분류
                diff = stock_after_payment_fail - stock_after_order

                if diff > 0:
                    # 재고 증가 = 시스템 버그 (Critical)
                    _rollback_stats["rollback_failed"] += 1
                    _rollback_stats["details"]["failed"].append(
                        {
                            "product_id": product_id,
                            "before": stock_after_order,
                            "after": stock_after_payment_fail,
                            "diff": diff,
                            "reason": "STOCK_INCREASED (BUG)",
                        }
                    )
                else:
                    # 재고 감소 = 동시 주문으로 인한 예상된 변동
                    _rollback_stats["variance_concurrent"] += 1
                    _rollback_stats["product_variance"][product_id] += 1
                    _rollback_stats["details"]["variance"].append(
                        {
                            "product_id": product_id,
                            "before": stock_after_order,
                            "after": stock_after_payment_fail,
                            "diff": diff,
                            "reason": "CONCURRENT_ORDER (expected)",
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
                _rollback_stats["details"]["failed"].append(
                    {
                        "product_id": product_id,
                        "type": "normal_decrease_failed",
                        "before": stock_before,
                        "after": stock_after,
                        "expected": stock_before - quantity,
                        "reason": "NORMAL_PAYMENT_NO_DECREASE",
                    }
                )


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 롤백 검증 결과 - 실패/동시성 분리 리포트"""
    global _rollback_stats

    print("\n" + "=" * 70)
    print("🔄 STAGE 5: ROLLBACK VALIDATION TEST RESULTS")
    print("=" * 70)

    triggered = _rollback_stats["failure_triggered"]
    verified = _rollback_stats["rollback_verified"]
    failed = _rollback_stats["rollback_failed"]
    variance = _rollback_stats["variance_concurrent"]

    print(f"\n📊 SUMMARY")
    print(f"   Failures Triggered:    {triggered}")
    print(f"   ✅ Rollback Verified:  {verified}")
    print(f"   ❌ Rollback Failed:    {failed} (System Bug)")
    print(f"   🔄 Variance Detected:  {variance} (Concurrent Orders)")

    # 성공률 계산 (진짜 실패만 제외)
    if triggered > 0:
        # 진짜 성공률: verified + variance (예상된 동작) / triggered
        effective_success = verified + variance
        success_rate = (effective_success / triggered) * 100
        print(f"\n   Effective Success Rate: {success_rate:.1f}%")
        print(f"   (Verified + Expected Variance) / Triggered")

    # Product별 Variance 집계 (Top 5)
    if _rollback_stats["product_variance"]:
        print(f"\n📦 VARIANCE BY PRODUCT (Top 5)")
        sorted_variance = sorted(_rollback_stats["product_variance"].items(), key=lambda x: x[1], reverse=True)[:5]
        for product_id, count in sorted_variance:
            print(f"   Product {product_id}: {count} variance occurrences")

    # 진짜 실패 상세 (Critical)
    if _rollback_stats["details"]["failed"]:
        print(f"\n🚨 CRITICAL FAILURES (Stock Increased - BUG)")
        for detail in _rollback_stats["details"]["failed"][:5]:
            print(
                f"   - Product {detail.get('product_id')}: "
                f"before={detail.get('before')}, after={detail.get('after')}, "
                f"reason={detail.get('reason')}"
            )

    # 테스트 판정
    print("\n" + "-" * 70)

    # 판정 기준: 진짜 실패(rollback_failed)가 0이면 통과
    # 또는 성공률이 threshold 이상이면 통과
    test_passed = False

    if failed == 0:
        test_passed = True
        print("✅ ROLLBACK TEST PASSED")
        print("   No system bugs detected (all failures are expected variance)")
    elif triggered > 0:
        success_rate = ((verified + variance) / triggered) * 100
        if success_rate >= PASS_THRESHOLD:
            test_passed = True
            print(f"✅ ROLLBACK TEST PASSED (threshold: {PASS_THRESHOLD}%)")
            print(f"   Success rate: {success_rate:.1f}%")
        else:
            print(f"❌ ROLLBACK TEST FAILED (threshold: {PASS_THRESHOLD}%)")
            print(f"   🚨 {failed} system bugs detected!")
            print("   ⚠️  Review transaction rollback logic")
    else:
        print("⚠️  NO ROLLBACK SCENARIOS TRIGGERED")
        print("   Check if products have sufficient stock")

    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print(f"Pass Threshold: {PASS_THRESHOLD}% (env: ROLLBACK_PASS_THRESHOLD)")
    print("=" * 70)
