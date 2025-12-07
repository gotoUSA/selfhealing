"""
장바구니 헬퍼 - 장바구니 조작

모든 Stage 시나리오에서 재사용
"""

import random
from typing import List, Optional, Dict, Any

from load_tests.config import ENDPOINTS


class CartHelper:
    """장바구니 조작 헬퍼"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name

    def get_cart_items(self) -> Optional[List[Dict[str, Any]]]:
        """장바구니 아이템 조회"""
        request_name = f"{self.stage_name} GET /api/cart/items/".strip()

        response = self.client.get(
            ENDPOINTS["cart_items"],
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def add_item(self, product_id: int, quantity: int = 1) -> bool:
        """장바구니에 상품 추가"""
        request_name = f"{self.stage_name} POST /api/cart/add_item/".strip()

        response = self.client.post(
            ENDPOINTS["cart_add_item"],
            json={
                "product_id": product_id,
                "quantity": quantity,
            },
            name=request_name,
        )

        return response.status_code in [200, 201]

    def add_random_items(
        self,
        product_ids: List[int],
        min_items: int = 1,
        max_items: int = 3,
        min_quantity: int = 1,
        max_quantity: int = 2,
    ) -> int:
        """
        랜덤 상품들을 장바구니에 추가

        Args:
            product_ids: 추가할 상품 ID 풀
            min_items: 최소 상품 종류 수
            max_items: 최대 상품 종류 수
            min_quantity: 최소 수량
            max_quantity: 최대 수량

        Returns:
            추가된 상품 종류 수
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
        """장바구니 아이템 수량 변경"""
        request_name = f"{self.stage_name} PATCH /api/cart/items/{{id}}/".strip()

        response = self.client.patch(
            ENDPOINTS["cart_item_detail"].format(id=item_id),
            json={"quantity": quantity},
            name=request_name,
        )

        return response.status_code == 200

    def remove_item(self, item_id: int) -> bool:
        """장바구니 아이템 삭제"""
        request_name = f"{self.stage_name} DELETE /api/cart/items/{{id}}/".strip()

        response = self.client.delete(
            ENDPOINTS["cart_item_detail"].format(id=item_id),
            name=request_name,
        )

        return response.status_code in [200, 204]

    def clear_cart(self) -> bool:
        """장바구니 비우기"""
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
                # 빈 장바구니 clear는 정상 케이스로 처리
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
        """장바구니 요약 조회"""
        request_name = f"{self.stage_name} GET /api/cart/summary/".strip()

        response = self.client.get(
            ENDPOINTS["cart_summary"],
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def has_items(self) -> bool:
        """장바구니에 상품이 있는지 확인"""
        items = self.get_cart_items()
        return bool(items)

    def prepare_cart_for_order(
        self,
        product_ids: List[int],
        min_items: int = 1,
        max_items: int = 3,
    ) -> bool:
        """
        주문을 위한 장바구니 준비 (비우고 → 추가)

        Returns:
            장바구니 준비 성공 여부
        """
        # 기존 장바구니 비우기 (실패해도 계속 진행)
        self.clear_cart()

        # 상품 추가
        added = self.add_random_items(
            product_ids,
            min_items=min_items,
            max_items=max_items,
        )

        return added > 0
