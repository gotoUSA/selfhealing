"""
Stage 9: Soak Test (Long Run)

Purpose: Detect resource leaks under long-running load
- Users: 100
- Duration: 30 minutes ~ 2 hours
- Memory usage trends
- DB connection pool status
- Redis memory
- Response time increase trends

Run:
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

# Soak test statistics
_soak_stats = {
    "start_time": None,
    "intervals": [],  # 5-minute interval snapshots
    "total_requests": 0,
    "total_errors": 0,
}

# 5-minute interval statistics collection
_interval_stats = {
    "requests": 0,
    "errors": 0,
    "response_times": [],
    "last_snapshot": None,
}


def _maybe_snapshot_interval():
    """Save interval statistics every 5 minutes"""
    global _interval_stats, _soak_stats

    now = time.time()

    if _interval_stats["last_snapshot"] is None:
        _interval_stats["last_snapshot"] = now
        return

    # Check if 5 minutes have passed
    if now - _interval_stats["last_snapshot"] >= 300:  # 5 minutes
        # Save snapshot
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

        # Reset
        _interval_stats["requests"] = 0
        _interval_stats["errors"] = 0
        _interval_stats["response_times"] = []
        _interval_stats["last_snapshot"] = now


class SoakUser(HttpUser):
    """
    Soak Test User

    Verify system stability under long-running sustained load
    """

    wait_time = between(1, 3)

    def on_start(self):
        """Initialize on test start"""
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
        """Record request statistics"""
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
        """Continuous product browsing"""
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
        """Continuous cart operations"""
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
        """Continuous payment"""
        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # Prepare cart
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return

        # Create order
        order_data = self.payment_helper.create_order()
        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # Payment
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
    """Soak test results on test stop"""
    global _soak_stats

    # Save last interval
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

    # Interval trend analysis
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
