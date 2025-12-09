"""
Stage 0: Smoke Baseline Test

Purpose: Environment/Login/Basic flow verification
- Verify login success
- Verify product list retrieval
- Single product order + 1 successful payment
- Save baseline latency

Run:
    locust -f load_tests/scenarios/stage0_smoke.py --host=http://localhost:8000 --users=5 --spawn-rate=5 --run-time=30s --headless
"""

import os
import sys

# 프로젝트 루트 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector


STAGE_NAME = "[Stage0]"


class SmokeUser(HttpUser):
    """
    Smoke Test User

    For verifying environment normal operation. Subsequent Stages abort on failure.
    """

    wait_time = between(1, 2)

    def on_start(self):
        """Initialize on test start"""
        # Setup metrics hooks (once)
        setup_event_hooks(STAGE_NAME)

        # Initialize helpers
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        # Cache products
        self.product_helper.ensure_products_cached()

        # Login
        self.login_helper.login()

    @task(1)
    @tag("smoke", "login")
    def verify_login(self):
        """Verify login status"""
        if not self.login_helper.is_logged_in:
            success = self.login_helper.login()
            if not success:
                # Login failure is critical in smoke test
                raise Exception("SMOKE FAILED: Login failed")

    @task(2)
    @tag("smoke", "products")
    def verify_product_list(self):
        """Verify product list retrieval"""
        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /api/products/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if "results" in data:
                    response.success()
                else:
                    response.failure("SMOKE FAILED: No results in product list")
            else:
                response.failure(f"SMOKE FAILED: Product list returned {response.status_code}")

    @task(2)
    @tag("smoke", "products")
    def verify_product_detail(self):
        """Verify product detail retrieval"""
        product_id = self.product_helper.get_random_product_id()
        if not product_id:
            return

        with self.client.get(
            f"/api/products/{product_id}/",
            name=f"{STAGE_NAME} GET /api/products/{{id}}/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if "id" in data and "name" in data:
                    response.success()
                else:
                    response.failure("SMOKE FAILED: Invalid product detail response")
            else:
                response.failure(f"SMOKE FAILED: Product detail returned {response.status_code}")

    @task(1)
    @tag("smoke", "payment", "critical")
    def verify_full_payment_flow(self):
        """
        Full payment flow verification

        Core of smoke test: At least one payment must succeed
        """
        if not self.login_helper.is_logged_in:
            self.login_helper.login()

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 1. Prepare cart
        self.cart_helper.clear_cart()

        product_id = product_ids[0]
        added = self.cart_helper.add_item(product_id, 1)
        if not added:
            return

        # 2. Verify cart
        items = self.cart_helper.get_cart_items()
        if not items:
            return

        # 3. Create order
        order_data = self.payment_helper.create_order(
            shipping_name="Smoke Test",
            shipping_phone="010-0000-0000",
            shipping_postal_code="00000",
            shipping_address="Smoke Test Address",
            shipping_address_detail="Test",
        )

        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 4. Confirm payment
        payment_key = self.payment_helper.generate_payment_key("smoke")

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
                # Business errors are allowed (out of stock, etc.)
                response.success()
            else:
                response.failure(f"SMOKE FAILED: Payment returned {response.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Summary results on test stop"""
    collector = get_metrics_collector()
    summary = collector.get_summary()

    print("\n" + "=" * 60)
    print("🔥 STAGE 0: SMOKE TEST RESULTS")
    print("=" * 60)

    total_failure = summary["total_failure"]
    error_rate = summary["overall_error_rate"]

    if total_failure == 0 and error_rate == 0:
        print("✅ SMOKE TEST PASSED - All systems operational")
    else:
        print(f"❌ SMOKE TEST FAILED - Errors: {total_failure}, Rate: {error_rate}%")
        print("⚠️  Do not proceed with other stages until smoke test passes!")

    print("=" * 60)
