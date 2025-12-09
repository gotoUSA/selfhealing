"""
Cart Helper - Cart Operations

Reusable across all Stage scenarios
"""

import random
from typing import List, Optional, Dict, Any

from load_tests.config import ENDPOINTS


class CartHelper:
    """Cart operations helper"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: Stage name for metrics prefix
        """
        self.client = client
        self.stage_name = stage_name

    def get_cart_items(self) -> Optional[List[Dict[str, Any]]]:
        """Get cart items"""
        request_name = f"{self.stage_name} GET /api/cart/items/".strip()

        response = self.client.get(
            ENDPOINTS["cart_items"],
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def add_item(self, product_id: int, quantity: int = 1) -> bool:
        """Add product to cart"""
        request_name = f"{self.stage_name} POST /api/cart/add_item/".strip()

        with self.client.post(
            ENDPOINTS["cart_add_item"],
            json={
                "product_id": product_id,
                "quantity": quantity,
            },
            name=request_name,
            catch_response=True,
        ) as response:
            # 400 errors (out of stock, duplicate product, etc.) are treated as normal business logic
            if response.status_code in [200, 201, 400]:
                response.success()
                return response.status_code in [200, 201]
            else:
                response.failure(f"Unexpected status: {response.status_code}")
                return False

    def add_random_items(
        self,
        product_ids: List[int],
        min_items: int = 1,
        max_items: int = 3,
        min_quantity: int = 1,
        max_quantity: int = 2,
    ) -> int:
        """
        Add random products to cart

        Args:
            product_ids: Pool of product IDs to add
            min_items: Minimum number of product types
            max_items: Maximum number of product types
            min_quantity: Minimum quantity
            max_quantity: Maximum quantity

        Returns:
            Number of product types added
        """
        if not product_ids:
            return 0

        num_items = random.randint(min_items, min(max_items, len(product_ids)))
        added_count = 0

        for _ in range(num_items):
            product_id = random.choice(product_ids)
            quantity = random.randint(min_quantity, max_quantity)
            if self.add_item(product_id, quantity):
                added_count += 1

        return added_count

    def update_item(self, item_id: int, quantity: int) -> bool:
        """Update cart item quantity"""
        request_name = f"{self.stage_name} PATCH /api/cart/items/{{id}}/".strip()

        response = self.client.patch(
            ENDPOINTS["cart_item_detail"].format(id=item_id),
            json={"quantity": quantity},
            name=request_name,
        )

        return response.status_code == 200

    def remove_item(self, item_id: int) -> bool:
        """Remove cart item"""
        request_name = f"{self.stage_name} DELETE /api/cart/items/{{id}}/".strip()

        response = self.client.delete(
            ENDPOINTS["cart_item_detail"].format(id=item_id),
            name=request_name,
        )

        return response.status_code in [200, 204]

    def clear_cart(self) -> bool:
        """Clear cart"""
        request_name = f"{self.stage_name} POST /api/cart/clear/".strip()

        with self.client.post(
            ENDPOINTS["cart_clear"],
            json={"confirm": True},
            name=request_name,
            catch_response=True,
        ) as response:
            if response.status_code in [200, 204]:
                response.success()
                return True
            elif response.status_code == 400:
                # Clearing empty cart is treated as normal case
                try:
                    data = response.json()
                    if data.get("code") == "CART_EMPTY":
                        response.success()
                        return True
                except Exception:
                    pass
                response.failure(f"Cart clear failed: {response.text[:100]}")
                return False
            else:
                response.failure(f"Unexpected status: {response.status_code}")
                return False

    def get_cart_summary(self) -> Optional[Dict[str, Any]]:
        """Get cart summary"""
        request_name = f"{self.stage_name} GET /api/cart/summary/".strip()

        response = self.client.get(
            ENDPOINTS["cart_summary"],
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def has_items(self) -> bool:
        """Check if cart has items"""
        items = self.get_cart_items()
        return bool(items)

    def prepare_cart_for_order(
        self,
        product_ids: List[int],
        min_items: int = 1,
        max_items: int = 3,
    ) -> bool:
        """
        Prepare cart for order (clear -> add)

        Returns:
            Whether cart preparation succeeded
        """
        # Clear existing cart (continue even if fails)
        self.clear_cart()

        # Add products
        added = self.add_random_items(
            product_ids,
            min_items=min_items,
            max_items=max_items,
        )

        return added > 0
