"""
Payment Helper - Payment Flow Logic

Reusable across all Stage scenarios
"""

import time
import random
from typing import Optional, Dict, Any, Tuple

from load_tests.config import ENDPOINTS


class PaymentHelper:
    """Payment flow helper"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: Stage name for metrics prefix
        """
        self.client = client
        self.stage_name = stage_name

    def create_order(
        self,
        shipping_name: str = "Test User",
        shipping_phone: str = "010-1234-5678",
        shipping_postal_code: str = "12345",
        shipping_address: str = "Test Address, Seoul",
        shipping_address_detail: str = "Test Apt 123",
    ) -> Optional[Dict[str, Any]]:
        """
        Create order

        Returns:
            Order data (includes order_id, final_amount) or None
        """
        request_name = f"{self.stage_name} POST /api/orders/".strip()

        response = self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": shipping_name,
                "shipping_phone": shipping_phone,
                "shipping_postal_code": shipping_postal_code,
                "shipping_address": shipping_address,
                "shipping_address_detail": shipping_address_detail,
            },
            name=request_name,
        )

        if response.status_code in [200, 201, 202]:
            order_data = response.json()
            order_id = order_data.get("order_id")

            # Fetch order detail if final_amount not in create response
            if order_id and not order_data.get("final_amount"):
                order_detail = self.get_order(order_id)
                if order_detail:
                    order_data["final_amount"] = order_detail.get("final_amount")

            return order_data
        return None

    def generate_payment_key(self, prefix: str = "test") -> str:
        """Generate unique payment_key"""
        timestamp = int(time.time() * 1000)
        random_suffix = random.randint(1, 999999)
        return f"{prefix}_{timestamp}_{random_suffix}"

    def request_payment(
        self,
        order_id: int,
        payment_method: str = "card",
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        Payment request (creates Payment object)

        Must be called before opening Toss Payments window.
        Payment object is created at this stage.

        Args:
            order_id: Order ID
            payment_method: Payment method (default: card)

        Returns:
            (status_code, response_data) tuple
            On success, response_data includes payment_id, amount, etc.
        """
        request_name = f"{self.stage_name} POST /api/payments/request/".strip()

        response = self.client.post(
            ENDPOINTS["payment_request"],
            json={
                "order_id": order_id,
                "payment_method": payment_method,
            },
            name=request_name,
        )

        return response.status_code, self._safe_json(response)

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: int,
        catch_response: bool = False,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        Payment confirmation

        Args:
            payment_key: PG payment key
            order_id: Order ID
            amount: Payment amount
            catch_response: If True, use Locust catch_response

        Returns:
            (status_code, response_data) tuple
        """
        request_name = f"{self.stage_name} POST /api/payments/confirm/".strip()

        if catch_response:
            with self.client.post(
                ENDPOINTS["payment_confirm"],
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": amount,
                },
                name=request_name,
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                    return response.status_code, response.json()
                elif response.status_code == 400:
                    # Business errors are not treated as failures
                    response.success()
                    return response.status_code, self._safe_json(response)
                else:
                    response.failure(f"Payment failed: {response.status_code}")
                    return response.status_code, self._safe_json(response)
        else:
            response = self.client.post(
                ENDPOINTS["payment_confirm"],
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": amount,
                },
                name=request_name,
            )
            return response.status_code, self._safe_json(response)

    def cancel_payment(
        self,
        payment_id: int,
        cancel_reason: str = "Test cancellation",
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        Cancel payment

        Args:
            payment_id: Payment ID
            cancel_reason: Cancellation reason

        Returns:
            (status_code, response_data) tuple
        """
        request_name = f"{self.stage_name} POST /api/payments/cancel/".strip()

        response = self.client.post(
            ENDPOINTS["payment_cancel"],
            json={
                "payment_id": payment_id,
                "cancel_reason": cancel_reason,
            },
            name=request_name,
        )

        return response.status_code, self._safe_json(response)

    def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        """Get payment details"""
        request_name = f"{self.stage_name} GET /api/payments/{{id}}/".strip()

        response = self.client.get(
            ENDPOINTS["payment_detail"].format(id=payment_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order details"""
        request_name = f"{self.stage_name} GET /api/orders/{{id}}/".strip()

        response = self.client.get(
            ENDPOINTS["order_detail"].format(id=order_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def full_payment_flow(
        self,
        product_ids: list,
        cart_helper,
        shipping_info: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Execute complete payment flow

        Args:
            product_ids: List of product IDs
            cart_helper: CartHelper instance
            shipping_info: Shipping information (optional)

        Returns:
            (success, payment_result_data) tuple
        """
        # 1. Prepare cart
        if not cart_helper.prepare_cart_for_order(product_ids):
            return False, None

        # 2. Verify cart
        if not cart_helper.has_items():
            return False, None

        # 3. Create order
        shipping = shipping_info or {
            "shipping_name": "Test User",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "Test Address, Seoul",
            "shipping_address_detail": "Test Apt 123",
        }

        order_data = self.create_order(**shipping)
        if not order_data:
            return False, None

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return False, None

        # 4. Confirm payment
        payment_key = self.generate_payment_key()
        status_code, payment_data = self.confirm_payment(
            payment_key=payment_key,
            order_id=order_id,
            amount=int(final_amount),
            catch_response=True,
        )

        success = status_code in [200, 201]

        return success, {
            "order_id": order_id,
            "order_data": order_data,
            "payment_key": payment_key,
            "payment_data": payment_data,
            "status_code": status_code,
        }

    def _safe_json(self, response) -> Optional[Dict[str, Any]]:
        """Safe JSON parsing"""
        try:
            return response.json()
        except Exception:
            return None
