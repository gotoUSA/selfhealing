"""
Stage 9: Soak Test (Long Run)

목적: 장시간 부하에서 리소스 누수 탐지
- Users: 100
- Duration: 30분 ~ 2시간
- 메모리 사용량 추이
- DB 커넥션 풀 상태
- Redis 메모리
- 응답시간 증가 추이

실행:
    locust -f load_tests/scenarios/stage9_soak.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=30m --headless
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


STAGE_NAME = "[Stage9]"

# Soak 테스트 통계
_soak_stats = {
    "start_time": None,
    "intervals": [],  # 5분 간격 스냅샷
    "total_requests": 0,
    "total_errors": 0,
}

# 5분 간격 통계 수집
_interval_stats = {
    "requests": 0,
    "errors": 0,
    "response_times": [],
    "last_snapshot": None,
}


def _maybe_snapshot_interval():
    """5분마다 간격 통계 저장"""
    global _interval_stats, _soak_stats

    now = time.time()

    if _interval_stats["last_snapshot"] is None:
        _interval_stats["last_snapshot"] = now
        return

    # 5분 경과 확인
    if now - _interval_stats["last_snapshot"] >= 300:  # 5분
        # 스냅샷 저장
        avg_response = (
            sum(_interval_stats["response_times"]) / len(_interval_stats["response_times"])
            if _interval_stats["response_times"]
            else 0
        )

        _soak_stats["intervals"].append(
            {
                "timestamp": now,
                "elapsed_minutes": (now - _soak_stats["start_time"]) / 60,
                "requests": _interval_stats["requests"],
                "errors": _interval_stats["errors"],
                "avg_response_time": avg_response,
                "error_rate": (
                    _interval_stats["errors"] / _interval_stats["requests"] * 100 if _interval_stats["requests"] > 0 else 0
                ),
            }
        )

        # 초기화
        _interval_stats["requests"] = 0
        _interval_stats["errors"] = 0
        _interval_stats["response_times"] = []
        _interval_stats["last_snapshot"] = now


class SoakUser(HttpUser):
    """
    Soak Test 사용자

    장시간 지속적인 부하로 시스템 안정성 검증
    """

    wait_time = between(1, 3)

    def on_start(self):
        """테스트 시작 시 초기화"""
        global _soak_stats

        setup_event_hooks(STAGE_NAME)

        if _soak_stats["start_time"] is None:
            _soak_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    def _record_request(self, success: bool, response_time: float):
        """요청 통계 기록"""
        global _interval_stats, _soak_stats

        _soak_stats["total_requests"] += 1
        _interval_stats["requests"] += 1
        _interval_stats["response_times"].append(response_time)

        if not success:
            _soak_stats["total_errors"] += 1
            _interval_stats["errors"] += 1

        _maybe_snapshot_interval()

    @task(5)
    @tag("soak", "browse")
    def soak_browse(self):
        """지속적 상품 조회"""
        start = time.time()

        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /api/products/",
            catch_response=True,
        ) as response:
            elapsed = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            self._record_request(success, elapsed)

    @task(3)
    @tag("soak", "cart")
    def soak_cart(self):
        """지속적 장바구니 조작"""
        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        start = time.time()

        with self.client.post(
            "/api/cart/add_item/",
            json={
                "product_id": random.choice(product_ids),
                "quantity": 1,
            },
            name=f"{STAGE_NAME} POST /api/cart/add_item/",
            catch_response=True,
        ) as response:
            elapsed = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            self._record_request(success, elapsed)

    @task(1)
    @tag("soak", "payment")
    def soak_payment(self):
        """지속적 결제"""
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

        # 결제
        payment_key = self.payment_helper.generate_payment_key("soak")
        start = time.time()

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/",
            catch_response=True,
        ) as response:
            elapsed = (time.time() - start) * 1000
            success = response.status_code in [200, 201, 400]

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            self._record_request(success, elapsed)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Soak 테스트 결과"""
    global _soak_stats

    # 마지막 간격 저장
    _maybe_snapshot_interval()

    print("\n" + "=" * 70)
    print("🏃 STAGE 9: SOAK TEST RESULTS")
    print("=" * 70)

    if _soak_stats["start_time"]:
        elapsed_minutes = (time.time() - _soak_stats["start_time"]) / 60
        print(f"Total Duration: {elapsed_minutes:.1f} minutes")

    print(f"Total Requests: {_soak_stats['total_requests']}")
    print(f"Total Errors: {_soak_stats['total_errors']}")

    if _soak_stats["total_requests"] > 0:
        overall_error_rate = _soak_stats["total_errors"] / _soak_stats["total_requests"] * 100
        print(f"Overall Error Rate: {overall_error_rate:.2f}%")

    # 간격별 추이 분석
    if _soak_stats["intervals"]:
        print("\n📈 Interval Analysis (5-minute windows):")
        print("-" * 70)
        print(f"{'Elapsed':<12} {'Requests':<10} {'Errors':<8} {'Err%':<8} {'Avg RT':<10}")
        print("-" * 70)

        for interval in _soak_stats["intervals"]:
            print(
                f"{interval['elapsed_minutes']:.1f} min"
                f"{interval['requests']:>10}"
                f"{interval['errors']:>8}"
                f"{interval['error_rate']:>7.2f}%"
                f"{interval['avg_response_time']:>9.1f}ms"
            )

        # 성능 저하 분석
        if len(_soak_stats["intervals"]) >= 2:
            first_rt = _soak_stats["intervals"][0]["avg_response_time"]
            last_rt = _soak_stats["intervals"][-1]["avg_response_time"]
            rt_increase = ((last_rt - first_rt) / first_rt * 100) if first_rt > 0 else 0

            first_err = _soak_stats["intervals"][0]["error_rate"]
            last_err = _soak_stats["intervals"][-1]["error_rate"]

            print("\n🔍 Performance Trend:")
            print(f"   Response Time: {first_rt:.1f}ms → {last_rt:.1f}ms ({rt_increase:+.1f}%)")
            print(f"   Error Rate: {first_err:.2f}% → {last_err:.2f}%")

            if rt_increase > 20:
                print("\n⚠️  WARNING: Response time increased >20%")
                print("   Possible memory leak or resource exhaustion")

            if last_err > first_err + 1:
                print("\n⚠️  WARNING: Error rate increased over time")
                print("   Check connection pool, worker health")

    # 최종 판정
    error_rate = _soak_stats["total_errors"] / _soak_stats["total_requests"] * 100 if _soak_stats["total_requests"] > 0 else 0

    if error_rate < 1 and (not _soak_stats["intervals"] or all(i["error_rate"] < 2 for i in _soak_stats["intervals"])):
        print("\n✅ SOAK TEST PASSED")
        print("   System stable under sustained load")
    else:
        print("\n⚠️  SOAK TEST NEEDS REVIEW")
        print("   Check server logs for memory/connection issues")

    print("=" * 70)
    print("\n📋 Recommended Checks:")
    print("   - docker stats (memory usage)")
    print("   - pg_stat_activity (DB connections)")
    print("   - redis-cli info memory")
    print("   - Server logs for OOM or connection errors")
