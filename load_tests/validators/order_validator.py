"""
주문 검증기 - Order Validator

주문 상태 전이 검증
"""

from typing import Optional, Dict, Any, List

from load_tests.config import ENDPOINTS


# 유효한 주문 상태 전이
VALID_STATE_TRANSITIONS = {
    "pending": ["confirmed", "cancelled"],
    "confirmed": ["paid", "cancelled"],
    "paid": ["shipped", "refund_requested", "cancelled"],
    "shipped": ["delivered", "refund_requested"],
    "delivered": ["refund_requested"],
    "refund_requested": ["refunded", "refund_rejected"],
    "refunded": [],
    "refund_rejected": [],
    "cancelled": [],
}


class OrderValidator:
    """주문 상태 검증"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name
        self._order_snapshots: Dict[str, Dict[str, Any]] = {}

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """주문 상세 조회"""
        request_name = f"{self.stage_name} [Validator] GET Order".strip()

        response = self.client.get(
            ENDPOINTS["order_detail"].format(id=order_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def get_order_status(self, order_id: str) -> Optional[str]:
        """주문 상태 조회"""
        order = self.get_order(order_id)
        if order:
            return order.get("status")
        return None

    def snapshot_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """주문 스냅샷 저장"""
        order = self.get_order(order_id)
        if order:
            self._order_snapshots[order_id] = order.copy()
        return order

    def get_snapshot(self, order_id: str) -> Optional[Dict[str, Any]]:
        """저장된 스냅샷 조회"""
        return self._order_snapshots.get(order_id)

    def clear_snapshots(self):
        """스냅샷 초기화"""
        self._order_snapshots.clear()

    def validate_state_transition(
        self,
        order_id: str,
        from_status: str,
        to_status: str,
    ) -> Dict[str, Any]:
        """
        주문 상태 전이 유효성 검증

        Args:
            order_id: 주문 ID
            from_status: 이전 상태
            to_status: 현재 상태

        Returns:
            검증 결과 딕셔너리
        """
        valid_next_states = VALID_STATE_TRANSITIONS.get(from_status, [])
        is_valid = to_status in valid_next_states

        result = {
            "order_id": order_id,
            "from_status": from_status,
            "to_status": to_status,
            "valid_next_states": valid_next_states,
            "valid": is_valid,
            "errors": [],
        }

        if not is_valid:
            result["errors"].append(
                f"Invalid state transition: {from_status} -> {to_status}. " f"Valid transitions: {valid_next_states}"
            )

        return result

    def validate_order_items(
        self,
        order_id: str,
        expected_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        주문 아이템 검증

        Args:
            order_id: 주문 ID
            expected_items: 예상 아이템 목록 [{"product_id": X, "quantity": Y}, ...]

        Returns:
            검증 결과 딕셔너리
        """
        order = self.get_order(order_id)
        if not order:
            return {
                "order_id": order_id,
                "valid": False,
                "errors": ["Order not found"],
            }

        actual_items = order.get("items", order.get("order_items", []))

        result = {
            "order_id": order_id,
            "expected_items": expected_items,
            "actual_items": actual_items,
            "valid": True,
            "errors": [],
        }

        # 아이템 수 검증
        if len(actual_items) != len(expected_items):
            result["valid"] = False
            result["errors"].append(f"Item count mismatch: expected={len(expected_items)}, " f"actual={len(actual_items)}")

        # 각 아이템 검증
        for expected in expected_items:
            expected_pid = expected.get("product_id")
            expected_qty = expected.get("quantity")

            found = False
            for actual in actual_items:
                actual_pid = actual.get("product_id", actual.get("product", {}).get("id"))
                actual_qty = actual.get("quantity")

                if actual_pid == expected_pid:
                    found = True
                    if actual_qty != expected_qty:
                        result["valid"] = False
                        result["errors"].append(
                            f"Quantity mismatch for product {expected_pid}: " f"expected={expected_qty}, actual={actual_qty}"
                        )
                    break

            if not found:
                result["valid"] = False
                result["errors"].append(f"Product {expected_pid} not found in order")

        return result

    def validate_order_amount(
        self,
        order_id: str,
        expected_amount: int,
    ) -> Dict[str, Any]:
        """
        주문 금액 검증

        Args:
            order_id: 주문 ID
            expected_amount: 예상 금액

        Returns:
            검증 결과 딕셔너리
        """
        order = self.get_order(order_id)
        if not order:
            return {
                "order_id": order_id,
                "valid": False,
                "errors": ["Order not found"],
            }

        actual_amount = order.get("final_amount", order.get("total_amount", 0))

        result = {
            "order_id": order_id,
            "expected_amount": expected_amount,
            "actual_amount": actual_amount,
            "valid": actual_amount == expected_amount,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Amount mismatch: expected={expected_amount}, actual={actual_amount}")

        return result

    def validate_current_status(
        self,
        order_id: str,
        expected_status: str,
    ) -> Dict[str, Any]:
        """
        현재 주문 상태 검증

        Args:
            order_id: 주문 ID
            expected_status: 예상 상태

        Returns:
            검증 결과 딕셔너리
        """
        current_status = self.get_order_status(order_id)

        if current_status is None:
            return {
                "order_id": order_id,
                "valid": False,
                "errors": ["Order not found or status unavailable"],
            }

        result = {
            "order_id": order_id,
            "expected_status": expected_status,
            "actual_status": current_status,
            "valid": current_status == expected_status,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Status mismatch: expected={expected_status}, actual={current_status}")

        return result
