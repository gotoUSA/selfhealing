"""
Stock Validator

Payment pre/post stock consistency validation
"""

from typing import Optional, Dict, Any

from load_tests.config import ENDPOINTS


class StockValidator:
    """Stock consistency validation"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: Stage name for metrics prefix
        """
        self.client = client
        self.stage_name = stage_name
        self._stock_snapshots: Dict[int, int] = {}

    def get_stock(self, product_id: int) -> Optional[int]:
        """Get current stock"""
        request_name = f"{self.stage_name} [Validator] GET Product Stock".strip()

        response = self.client.get(
            ENDPOINTS["product_detail"].format(id=product_id),
            name=request_name,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("stock", 0)
        return None

    def snapshot_stock(self, product_id: int) -> Optional[int]:
        """Save stock snapshot (for comparison)"""
        stock = self.get_stock(product_id)
        if stock is not None:
            self._stock_snapshots[product_id] = stock
        return stock

    def get_snapshot(self, product_id: int) -> Optional[int]:
        """Get saved snapshot"""
        return self._stock_snapshots.get(product_id)

    def clear_snapshots(self):
        """Clear snapshots"""
        self._stock_snapshots.clear()

    def validate_no_oversell(
        self,
        product_id: int,
        before: int,
        sold: int,
        after: int,
    ) -> Dict[str, Any]:
        """
        Oversell validation

        Args:
            product_id: Product ID
            before: Stock before sale
            sold: Quantity sold
            after: Stock after sale

        Returns:
            Validation result dictionary
        """
        expected = before - sold

        result = {
            "product_id": product_id,
            "before": before,
            "sold": sold,
            "after": after,
            "expected": expected,
            "valid": True,
            "errors": [],
        }

        if after < 0:
            result["valid"] = False
            result["errors"].append(f"Negative stock: {after}")

        if after != expected:
            result["valid"] = False
            result["errors"].append(f"Stock mismatch: expected={expected}, actual={after}")

        return result

    def validate_rollback(
        self,
        product_id: int,
        before: int,
        after: int,
    ) -> Dict[str, Any]:
        """
        Stock recovery validation after rollback

        Args:
            product_id: Product ID
            before: Stock before rollback (original stock)
            after: Stock after rollback

        Returns:
            Validation result dictionary
        """
        result = {
            "product_id": product_id,
            "before": before,
            "after": after,
            "valid": before == after,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Rollback failed: before={before}, after={after}")

        return result

    def validate_stock_change(
        self,
        product_id: int,
        expected_change: int,
    ) -> Dict[str, Any]:
        """
        Stock change validation against snapshot

        Args:
            product_id: Product ID
            expected_change: Expected change amount (negative: decrease, positive: increase)

        Returns:
            Validation result dictionary
        """
        before = self._stock_snapshots.get(product_id)
        if before is None:
            return {
                "product_id": product_id,
                "valid": False,
                "errors": ["No snapshot found"],
            }

        after = self.get_stock(product_id)
        if after is None:
            return {
                "product_id": product_id,
                "valid": False,
                "errors": ["Failed to get current stock"],
            }

        expected_after = before + expected_change

        result = {
            "product_id": product_id,
            "before": before,
            "after": after,
            "expected_change": expected_change,
            "expected_after": expected_after,
            "actual_change": after - before,
            "valid": after == expected_after,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Stock change mismatch: expected={expected_change}, " f"actual={result['actual_change']}")

        return result
