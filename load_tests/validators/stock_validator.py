"""
재고 검증기 - Stock Validator

결제 전후 재고 정합성 검증
"""

from typing import Optional, Dict, Any

from load_tests.config import ENDPOINTS


class StockValidator:
    """재고 정합성 검증"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name
        self._stock_snapshots: Dict[int, int] = {}

    def get_stock(self, product_id: int) -> Optional[int]:
        """현재 재고 조회"""
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
        """재고 스냅샷 저장 (비교용)"""
        stock = self.get_stock(product_id)
        if stock is not None:
            self._stock_snapshots[product_id] = stock
        return stock

    def get_snapshot(self, product_id: int) -> Optional[int]:
        """저장된 스냅샷 조회"""
        return self._stock_snapshots.get(product_id)

    def clear_snapshots(self):
        """스냅샷 초기화"""
        self._stock_snapshots.clear()

    def validate_no_oversell(
        self, 
        product_id: int, 
        before: int, 
        sold: int, 
        after: int,
    ) -> Dict[str, Any]:
        """
        과잉 판매 검증
        
        Args:
            product_id: 상품 ID
            before: 판매 전 재고
            sold: 판매 수량
            after: 판매 후 재고
            
        Returns:
            검증 결과 딕셔너리
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
            result["errors"].append(
                f"Stock mismatch: expected={expected}, actual={after}"
            )

        return result

    def validate_rollback(
        self, 
        product_id: int, 
        before: int, 
        after: int,
    ) -> Dict[str, Any]:
        """
        롤백 후 재고 복구 검증
        
        Args:
            product_id: 상품 ID
            before: 롤백 전 재고 (원래 재고)
            after: 롤백 후 재고
            
        Returns:
            검증 결과 딕셔너리
        """
        result = {
            "product_id": product_id,
            "before": before,
            "after": after,
            "valid": before == after,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(
                f"Rollback failed: before={before}, after={after}"
            )

        return result

    def validate_stock_change(
        self,
        product_id: int,
        expected_change: int,
    ) -> Dict[str, Any]:
        """
        스냅샷 대비 재고 변화 검증
        
        Args:
            product_id: 상품 ID
            expected_change: 예상 변화량 (음수: 감소, 양수: 증가)
            
        Returns:
            검증 결과 딕셔너리
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
            result["errors"].append(
                f"Stock change mismatch: expected={expected_change}, "
                f"actual={result['actual_change']}"
            )

        return result
