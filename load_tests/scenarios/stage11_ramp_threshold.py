"""
Stage 11: Ramp-up Threshold Discovery

Purpose: Discover system threshold and Self-Healing trigger points
- Start with low load and gradually increase
- Identify when retry starts occurring
- Identify when circuit breaker transitions
- Identify when DLQ starts growing
- Find the breaking point of the system

Load Shape:
  - LinearRamp: 10 → 300 users over 20 minutes

Metrics to Collect:
  - retry_count (per minute)
  - circuit_breaker_state
  - dlq_count
  - avg_response_time (per minute)
  - error_rate (per minute)

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage11_ramp_threshold.py --host=http://localhost:8000

    # CLI mode (20 minute ramp)
    locust -f load_tests/scenarios/stage11_ramp_threshold.py \\
        --host=http://localhost:8000 \\
        --users=300 --spawn-rate=0.25 --run-time=20m \\
        --headless --html=stage11_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 11)
"""

import os
import sys
import time
import random
import json
from datetime import datetime
from typing import Optional

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage11-RampThreshold]"


# =============================================================================
# Threshold Discovery Statistics
# =============================================================================

_threshold_stats = {
    "start_time": None,
    "current_users": 0,
    "snapshots": [],  # Per-minute snapshots
    "milestones": {
        "first_retry": None,  # User count when first retry occurred
        "first_cb_open": None,  # User count when CB first opened
        "first_dlq": None,  # User count when first DLQ item created
        "first_error_spike": None,  # User count when error rate > 5%
        "breaking_point": None,  # User count when system degraded
    },
    "current_minute": {
        "requests": 0,
        "errors": 0,
        "retries": 0,
        "response_times": [],
    },
}


def _get_current_minute_key() -> int:
    """Get current minute as integer for grouping"""
    if _threshold_stats["start_time"] is None:
        return 0
    elapsed = time.time() - _threshold_stats["start_time"]
    return int(elapsed // 60)


def _snapshot_current_minute():
    """Save current minute statistics and reset counters"""
    stats = _threshold_stats["current_minute"]

    if stats["requests"] == 0:
        return

    avg_response = sum(stats["response_times"]) / len(stats["response_times"]) if stats["response_times"] else 0
    error_rate = stats["errors"] / stats["requests"] * 100 if stats["requests"] > 0 else 0

    snapshot = {
        "minute": _get_current_minute_key(),
        "users": _threshold_stats["current_users"],
        "requests": stats["requests"],
        "errors": stats["errors"],
        "retries": stats["retries"],
        "error_rate": round(error_rate, 2),
        "avg_response_time_ms": round(avg_response, 2),
        "timestamp": datetime.now().isoformat(),
    }

    _threshold_stats["snapshots"].append(snapshot)

    # Check for milestones
    _check_milestones(snapshot)

    # Reset counters
    _threshold_stats["current_minute"] = {
        "requests": 0,
        "errors": 0,
        "retries": 0,
        "response_times": [],
    }


def _check_milestones(snapshot: dict):
    """Check and record milestone events"""
    milestones = _threshold_stats["milestones"]
    users = snapshot["users"]

    # First retry milestone
    if milestones["first_retry"] is None and snapshot["retries"] > 0:
        milestones["first_retry"] = {
            "user_count": users,
            "minute": snapshot["minute"],
            "retry_count": snapshot["retries"],
        }
        print(f"\n🎯 MILESTONE: First retry detected at {users} users (minute {snapshot['minute']})")

    # First error spike milestone (error rate > 5%)
    if milestones["first_error_spike"] is None and snapshot["error_rate"] > 5:
        milestones["first_error_spike"] = {
            "user_count": users,
            "minute": snapshot["minute"],
            "error_rate": snapshot["error_rate"],
        }
        print(f"\n⚠️ MILESTONE: Error spike ({snapshot['error_rate']}%) at {users} users")

    # Breaking point (error rate > 20% or avg response > 5000ms)
    if milestones["breaking_point"] is None:
        if snapshot["error_rate"] > 20 or snapshot["avg_response_time_ms"] > 5000:
            milestones["breaking_point"] = {
                "user_count": users,
                "minute": snapshot["minute"],
                "error_rate": snapshot["error_rate"],
                "avg_response_time_ms": snapshot["avg_response_time_ms"],
            }
            print(f"\n🔴 MILESTONE: Breaking point reached at {users} users")


def _record_request(success: bool, response_time_ms: float, is_retry: bool = False):
    """Record request statistics"""
    stats = _threshold_stats["current_minute"]
    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)

    if not success:
        stats["errors"] += 1

    if is_retry:
        stats["retries"] += 1


# =============================================================================
# Custom Load Shape - Linear Ramp
# =============================================================================


class RampUpShape(LoadTestShape):
    """
    Linear ramp-up load shape for threshold discovery.

    Gradually increases users from 10 to 300 over 20 minutes.
    This allows us to observe when the system starts showing stress.
    
    Set LOCUST_TEST_DURATION env var to override duration (in seconds).
    """

    # Configuration - can be overridden by env var
    MIN_USERS = 10
    MAX_USERS = int(os.environ.get("LOCUST_MAX_USERS", "50"))
    RAMP_DURATION = int(os.environ.get("LOCUST_TEST_DURATION", "15"))  # Default 15s for quick tests

    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time"""
        run_time = self.get_run_time()

        if run_time > self.RAMP_DURATION:
            # Test finished - return None to stop
            return None

        # Linear interpolation
        progress = run_time / self.RAMP_DURATION
        current_users = int(self.MIN_USERS + (self.MAX_USERS - self.MIN_USERS) * progress)

        # Spawn rate - faster at start, slower as we approach limit
        spawn_rate = max(1, (self.MAX_USERS - self.MIN_USERS) / self.RAMP_DURATION * 10)

        # Update global stats
        _threshold_stats["current_users"] = current_users

        return (current_users, spawn_rate)


# =============================================================================
# Test User
# =============================================================================


class ThresholdDiscoveryUser(HttpUser):
    """
    User for threshold discovery testing.

    Performs typical e-commerce operations while monitoring
    for self-healing trigger conditions.
    """

    wait_time = between(1, 3)

    def on_start(self):
        """Initialize user session"""
        global _threshold_stats

        setup_event_hooks(STAGE_NAME)

        if _threshold_stats["start_time"] is None:
            _threshold_stats["start_time"] = time.time()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        # Track last minute for snapshot triggering
        self._last_minute = _get_current_minute_key()

    def _check_snapshot_needed(self):
        """Check if we need to take a snapshot"""
        current_minute = _get_current_minute_key()
        if current_minute > self._last_minute:
            _snapshot_current_minute()
            self._last_minute = current_minute

    # =========================================================================
    # Standard E-Commerce Operations
    # =========================================================================

    @task(10)
    @tag("threshold", "browse")
    def browse_products(self):
        """Browse products - most common operation"""
        start = time.time()

        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /products/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    @task(5)
    @tag("threshold", "cart")
    def add_to_cart(self):
        """Add item to cart"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        start = time.time()

        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /cart/add_item/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    @task(3)
    @tag("threshold", "payment")
    def create_order_and_pay(self):
        """Create order and request payment - most intensive operation"""
        # Add item to cart first
        product = self.product_helper.get_random_product()
        if not product:
            return

        # Add to cart
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add (payment flow)",
        )

        # Create order
        start = time.time()

        with self.client.post(
            "/api/orders/",
            json={"shipping_address": "Test Address for Load Test"},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /orders/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                order_data = response.json()
                order_id = order_data.get("id")

                # Request payment
                if order_id:
                    self._request_payment(order_id)

                response.success()
            else:
                response.failure(f"Order creation failed: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    def _request_payment(self, order_id: int):
        """Request payment for order"""
        start = time.time()

        with self.client.post(
            "/api/payments/request/",
            json={"order_id": order_id},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /payments/request/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                # Payment failures might trigger retries
                response.failure(f"Payment request failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    @task(2)
    @tag("threshold", "self-healing")
    def check_self_healing_status(self):
        """Check self-healing system status to monitor CB state"""
        start = time.time()

        with self.client.get(
            "/api/self-healing/status/",
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} GET /self-healing/status/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                data = response.json()

                # Check for circuit breaker state changes
                # services is a list of dicts with service_name key
                services = data.get("services", [])
                for service_info in services:
                    service_name = service_info.get("service_name", "unknown")
                    if service_info.get("circuit_state") == "open" or service_info.get("state") == "open":
                        if _threshold_stats["milestones"]["first_cb_open"] is None:
                            _threshold_stats["milestones"]["first_cb_open"] = {
                                "user_count": _threshold_stats["current_users"],
                                "minute": _get_current_minute_key(),
                                "service": service_name,
                            }
                            print(f"\n🔌 MILESTONE: Circuit Breaker OPEN for {service_name}")

                response.success()
            else:
                response.failure(f"Status check failed: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate threshold discovery report on test completion"""
    # Take final snapshot
    _snapshot_current_minute()

    print("\n" + "=" * 70)
    print("📊 THRESHOLD DISCOVERY REPORT")
    print("=" * 70)

    # Milestones
    print("\n🎯 Milestones Detected:")
    milestones = _threshold_stats["milestones"]

    for name, data in milestones.items():
        if data:
            print(f"  - {name}: {json.dumps(data, indent=4)}")
        else:
            print(f"  - {name}: Not reached")

    # Summary statistics
    print("\n📈 Load Progression Summary:")
    print(f"  - Total snapshots: {len(_threshold_stats['snapshots'])}")

    if _threshold_stats["snapshots"]:
        first = _threshold_stats["snapshots"][0]
        last = _threshold_stats["snapshots"][-1]

        print(f"  - First snapshot: {first['users']} users, {first['error_rate']}% error rate")
        print(f"  - Last snapshot: {last['users']} users, {last['error_rate']}% error rate")

        # Find peak response time
        peak_snapshot = max(_threshold_stats["snapshots"], key=lambda x: x["avg_response_time_ms"])
        print(f"  - Peak response time: {peak_snapshot['avg_response_time_ms']}ms at {peak_snapshot['users']} users")

    # Save report to file
    report_path = os.path.join(_load_tests_dir, "reports", "stage11_threshold_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "test_name": "Stage 11: Ramp-up Threshold Discovery",
                "timestamp": datetime.now().isoformat(),
                "milestones": milestones,
                "snapshots": _threshold_stats["snapshots"],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\n💾 Full report saved to: {report_path}")
    print("=" * 70)
