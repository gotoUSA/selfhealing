"""
쇼퍼 사용자 (Shopper User)

실제 서비스의 20~25%를 차지하는 사용자 유형.
상품을 보고 장바구니에 담지만, 구매까지 이어지지 않음.
"""

import random
from locust import task, tag

from .base import BaseUser
from load_tests.config import ENDPOINTS


class ShopperUser(BaseUser):
    """
    장바구니까지 담는 사용자

    행동 패턴:
    - 상품 조회 후 장바구니 추가
    - 장바구니 확인
    - 장바구니 수정/삭제 (마음 바뀜)

    로그인: 필요
    """

    def on_start(self):
        """시작 시 로그인"""
        super().on_start()
        self.login()

    @task(5)
    @tag("read", "products")
    def browse_and_view(self):
        """상품 목록 및 상세 조회"""
        # 목록 조회
        self.client.get(
            f"{ENDPOINTS['products']}?page={random.randint(1, 3)}",
            name="GET /api/products/",
        )

        # 상세 조회
        product_id = self.get_random_product_id()
        if product_id:
            self.client.get(
                ENDPOINTS["product_detail"].format(id=product_id),
                name="GET /api/products/{id}/",
            )

    @task(4)
    @tag("write", "cart")
    def add_to_cart(self):
        """장바구니에 상품 추가"""
        if not self.ensure_logged_in():
            return

        product_id = self.get_random_product_id()
        if not product_id:
            return

        # 상세 조회 후 장바구니 추가 (실제 사용자 패턴)
        self.client.get(
            ENDPOINTS["product_detail"].format(id=product_id),
            name="GET /api/products/{id}/",
        )

        self.client.post(
            ENDPOINTS["cart_add_item"],
            json={
                "product_id": product_id,
                "quantity": random.randint(1, 3),
            },
            name="POST /api/cart/add_item/",
        )

    @task(3)
    @tag("read", "cart")
    def view_cart(self):
        """장바구니 확인"""
        if not self.ensure_logged_in():
            return

        self.client.get(
            ENDPOINTS["cart_items"],
            name="GET /api/cart/items/",
        )

    @task(2)
    @tag("write", "cart")
    def modify_cart(self):
        """장바구니 수정/삭제"""
        if not self.ensure_logged_in():
            return

        with self.client.get(ENDPOINTS["cart_items"], name="GET /api/cart/items/", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Failed to get cart: {response.status_code}")
                return

            items = response.json()
            response.success()

            if not items:
                return

            item = random.choice(items)
            item_id = item.get("id")

            if not item_id:
                return

            if random.random() < 0.5:
                self.client.delete(
                    ENDPOINTS["cart_item_detail"].format(id=item_id),
                    name="DELETE /api/cart/items/{id}/",
                )
            else:
                self.client.patch(
                    ENDPOINTS["cart_item_detail"].format(id=item_id),
                    json={"quantity": random.randint(1, 5)},
                    name="PATCH /api/cart/items/{id}/",
                )
