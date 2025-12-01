"""
Locust Order Concurrency Test - 500~1000명 동시 주문

목적:
    pytest의 DB 커넥션 한계를 넘어서는 대규모 주문 동시성 테스트
    500-1000명이 동시에 주문을 생성하여 시스템 확장성 검증

실행 방법:
    # 500명 동시 주문
    locust -f shopping/tests/performance/scenarios/order.py \\
        --host=http://localhost:8000 \\
        --users 500 \\
        --spawn-rate 50 \\
        --run-time 5m \\
        --headless

    # 1000명 동시 주문
    locust -f shopping/tests/performance/scenarios/order.py \\
        --host=http://localhost:8000 \\
        --users 1000 \\
        --spawn-rate 100 \\
        --run-time 10m \\
        --headless

웹 UI (대화형):
    locust -f shopping/tests/performance/scenarios/order.py \\
        --host=http://localhost:8000
    # http://localhost:8089 접속하여 수동 설정
"""
from locust import HttpUser, task, between, events
import random
import time
import logging

# 통계 수집
order_stats = {
    "total_attempts": 0,
    "successful_orders": 0,
    "failed_orders": 0,
    "cart_failures": 0,
}


class OrderConcurrencyUser(HttpUser):
    """
    주문 생성 전용 사용자

    플로우:
        1. 로그인
        2. 장바구니에 상품 추가
        3. 주문 생성
        4. 완료 (결제는 skip)
    """

    wait_time = between(1, 3)

    def on_start(self):
        """초기 설정 - 로그인 및 상품 ID 수집"""
        self.product_ids = []
        self.is_logged_in = False
        self.user_id = random.randint(0, 999)  # 1000명 범위 (0-999)

        # 상품 ID 조회 (첫 페이지만)
        response = self.client.get("/api/products/?page=1")
        if response.status_code == 200:
            results = response.json().get("results", [])
            self.product_ids = [p["id"] for p in results if p.get("stock", 0) > 0]

        # 로그인
        self.login()

    def login(self):
        """로그인 수행"""
        response = self.client.post(
            "/api/auth/login/",
            json={
                "username": f"load_test_user_{self.user_id}",
                "password": "testpass123",
            },
        )

        if response.status_code == 200:
            token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {token}"})
            self.is_logged_in = True
        else:
            logging.error(f"Login failed for user {self.user_id}: {response.status_code}")

    @task
    def create_order(self):
        """
        주문 생성 플로우

        Scenario:
            - 장바구니에 1-2개 상품 추가
            - 주문 생성
            - 성공/실패 통계 수집
        """
        global order_stats

        if not self.is_logged_in:
            self.login()

        if not self.product_ids:
            logging.warning(f"User {self.user_id}: No products available")
            return

        # 1. 장바구니에 상품 추가
        num_items = random.randint(1, 2)
        added_items = 0

        for _ in range(num_items):
            product_id = random.choice(self.product_ids)
            response = self.client.post(
                "/api/cart-items/",
                json={"product_id": product_id, "quantity": random.randint(1, 2)},
                name="/api/cart-items/ [Add to Cart]",
            )

            if response.status_code == 201:
                added_items += 1
            else:
                order_stats["cart_failures"] += 1

        # 장바구니 추가 실패 시 중단
        if added_items == 0:
            logging.warning(f"User {self.user_id}: Failed to add items to cart")
            return

        # 2. 주문 생성
        order_stats["total_attempts"] += 1

        response = self.client.post(
            "/api/orders/",
            json={
                "shipping_name": f"테스트유저{self.user_id}",
                "shipping_phone": f"010-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}",
                "shipping_postal_code": f"{random.randint(10000, 99999)}",
                "shipping_address": "서울시 강남구 테헤란로",
                "shipping_address_detail": f"{random.randint(101, 999)}호",
            },
            name="/api/orders/ [Create Order]",
        )

        if response.status_code in [201, 202]:
            order_stats["successful_orders"] += 1
            order_data = response.json()
            order_id = order_data.get("order_id")
            logging.info(f"User {self.user_id}: Order created - ID: {order_id}")
        else:
            order_stats["failed_orders"] += 1
            logging.error(f"User {self.user_id}: Order failed - Status: {response.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 통계 출력"""
    print("\n" + "=" * 60)
    print("📊 Order Concurrency Test Results")
    print("=" * 60)
    print(f"총 주문 시도:     {order_stats['total_attempts']}")
    print(f"성공한 주문:      {order_stats['successful_orders']}")
    print(f"실패한 주문:      {order_stats['failed_orders']}")
    print(f"장바구니 실패:    {order_stats['cart_failures']}")

    if order_stats["total_attempts"] > 0:
        success_rate = (order_stats["successful_orders"] / order_stats["total_attempts"]) * 100
        print(f"성공률:           {success_rate:.2f}%")
    print("=" * 60 + "\n")


# ==================== 점진적 부하 증가 (Optional) ====================
# 사용 시 주석 해제

# from locust import LoadTestShape
#
# class OrderLoadShape(LoadTestShape):
#     """
#     주문 부하 점진 증가
#
#     - 1분: 100명
#     - 3분: 300명
#     - 5분: 500명
#     - 7분: 700명
#     - 10분: 1000명 (피크)
#     """
#
#     stages = [
#         {"duration": 60, "users": 100, "spawn_rate": 20},
#         {"duration": 180, "users": 300, "spawn_rate": 50},
#         {"duration": 300, "users": 500, "spawn_rate": 50},
#         {"duration": 420, "users": 700, "spawn_rate": 50},
#         {"duration": 600, "users": 1000, "spawn_rate": 100},
#     ]
#
#     def tick(self):
#         run_time = self.get_run_time()
#
#         for stage in self.stages:
#             if run_time < stage["duration"]:
#                 return (stage["users"], stage["spawn_rate"])
#
#         return None
