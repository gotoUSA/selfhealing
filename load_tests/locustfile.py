"""
Locust 로드 테스트 - Weight 기반 시나리오

실행 방법:
    locust -f shopping/tests/performance/locustfile.py --host=http://localhost:8000

웹 UI:
    http://localhost:8089

시나리오 변경:
    아래 WebsiteUser의 tasks 딕셔너리에서 weight만 변경하면 됩니다.
    5가지 프리셋이 주석으로 제공되어 있습니다.
"""

from locust import HttpUser, task, TaskSet, between, LoadTestShape
import random
import time


class BrowsingUser(TaskSet):
    """
    브라우징만 하는 사용자 (60~80%)
    - 상품 목록/상세 조회만
    - 구매 의도 없음
    """

    @task(10)
    def browse_product_list(self):
        """상품 목록 조회 (가장 빈번)"""
        page = random.randint(1, 5)
        self.client.get(f"/api/products/?page={page}")

    @task(5)
    def view_product_detail(self):
        """상품 상세 조회"""
        if hasattr(self.user, "product_ids") and self.user.product_ids:
            product_id = random.choice(self.user.product_ids)
            self.client.get(f"/api/products/{product_id}/")

    @task(2)
    def search_products(self):
        """상품 검색"""
        keywords = ["성능테스트", "상품", "0", "1"]
        keyword = random.choice(keywords)
        self.client.get(f"/api/products/?search={keyword}")

    @task(1)
    def view_categories(self):
        """카테고리 조회"""
        self.client.get("/api/categories/")


class CartUser(TaskSet):
    """
    장바구니까지 담는 사용자 (15~25%)
    - 상품 보고 장바구니 추가
    - 구매는 안 함
    """

    @task(3)
    def browse_and_add_to_cart(self):
        """상품 보고 장바구니에 담기"""
        if hasattr(self.user, "product_ids") and self.user.product_ids:
            product_id = random.choice(self.user.product_ids)

            # 상세 조회
            self.client.get(f"/api/products/{product_id}/")

            # 50% 확률로 장바구니 추가
            if random.random() < 0.5:
                self.client.post("/api/cart-items/", json={"product_id": product_id, "quantity": random.randint(1, 2)})

    @task(2)
    def view_cart(self):
        """장바구니 확인"""
        self.client.get("/api/cart-items/")

    @task(1)
    def modify_cart(self):
        """장바구니 수정/삭제 (마음 바뀜)"""
        response = self.client.get("/api/cart-items/")
        if response.status_code == 200:
            items = response.json()
            if items and len(items) > 0:
                item_id = items[0].get("id")
                if item_id:
                    # 50% 삭제, 50% 수량 변경
                    if random.random() < 0.5:
                        self.client.delete(f"/api/cart-items/{item_id}/")
                    else:
                        # PUT 메서드 사용 (API가 PATCH를 지원하지 않음)
                        self.client.put(f"/api/cart-items/{item_id}/", json={"quantity": random.randint(1, 3)})


class OrderUser(TaskSet):
    """
    주문 생성까지 가는 사용자 (5~10%)
    - 장바구니 추가 -> 주문 생성
    - 결제는 안 함 (결제 전 이탈)
    """

    @task
    def add_to_cart_and_create_order(self):
        """장바구니 추가 -> 주문 생성"""
        # 주문 생성은 로그인 필수
        if not hasattr(self.user, "is_logged_in") or not self.user.is_logged_in:
            self.user.login()

        if not hasattr(self.user, "product_ids") or not self.user.product_ids:
            return

        # 1. 장바구니에 상품 추가 (1~2개)
        num_items = random.randint(1, 2)
        added_items = 0

        for _ in range(num_items):
            product_id = random.choice(self.user.product_ids)
            response = self.client.post("/api/cart-items/", json={"product_id": product_id, "quantity": random.randint(1, 2)})
            if response.status_code == 201:
                added_items += 1

        # 아이템 추가 실패하면 중단
        if added_items == 0:
            return

        # 2. 주문 생성 전 장바구니 확인 (레이스 컨디션 방지)
        cart_response = self.client.get("/api/cart-items/")
        if cart_response.status_code != 200 or not cart_response.json():
            return  # 장바구니 비어있으면 주문 생성 스킵

        # 3. 주문 생성
        self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "테스트",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101호",
            },
        )

        # 결제는 안 함 (여기서 이탈)


class PaymentUser(TaskSet):
    """
    결제까지 완료하는 사용자 (2~5%)
    - 전체 구매 플로우
    - 실제 구매 전환
    """

    @task
    def complete_purchase_flow(self):
        """완전한 구매 플로우"""
        # 결제는 로그인 필수
        if not hasattr(self.user, "is_logged_in") or not self.user.is_logged_in:
            self.user.login()

        if not hasattr(self.user, "product_ids") or not self.user.product_ids:
            return

        # 1. 상품 상세 조회
        product_id = random.choice(self.user.product_ids)
        self.client.get(f"/api/products/{product_id}/")

        # 2. 장바구니 추가
        response = self.client.post("/api/cart-items/", json={"product_id": product_id, "quantity": random.randint(1, 2)})

        if response.status_code != 201:
            return  # 실패하면 포기

        # 10% 확률로 여기서 포기
        if random.random() < 0.1:
            return

        # 3. 장바구니 확인 (레이스 컨디션 방지 - 결과도 검증)
        cart_response = self.client.get("/api/cart-items/")
        if cart_response.status_code != 200 or not cart_response.json():
            return  # 장바구니 비어있으면 주문 생성 스킵

        # 4. 주문 생성
        response = self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "테스트",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101호",
            },
        )

        if response.status_code not in [201, 202]:
            return  # 실패하면 포기

        order_id = response.json().get("order_id")
        final_amount = response.json().get("final_amount")
        if not final_amount:
            return

        # 5% 확률로 결제 전 포기
        if random.random() < 0.05:
            return

        # 5. 결제 승인
        payment_key = f"test_key_{int(time.time() * 1000)}_{random.randint(1, 100000)}"

        self.client.post(
            "/api/payments/confirm/", json={"payment_key": payment_key, "order_id": order_id, "amount": int(final_amount)}
        )


# ==================== 🔥 시나리오 프리셋 ====================
# 아래 5가지 중 하나를 선택하거나, 직접 커스터마이징하세요.

# 📌 프리셋 1: Light Traffic (브라우징 중심)
# - 용도: DB read, 캐시, 페이지네이션 성능 측정
# - 적정 유저: 100 → 300 → 500 → 700 → 1000
LIGHT_TRAFFIC = {
    BrowsingUser: 80,
    CartUser: 15,
    OrderUser: 5,
    PaymentUser: 0,
}

# 📌 프리셋 2: Medium Traffic (장바구니 진입)
# - 용도: Cart DB I/O + 재고 조회 부하
# - 적정 유저: 30 → 100 → 200 → 300
MEDIUM_TRAFFIC = {
    BrowsingUser: 70,
    CartUser: 20,
    OrderUser: 10,
    PaymentUser: 0,
}

# 📌 프리셋 3: High Intent (주문 생성 포함)
# - 용도: 주문 생성 로직 + 재고 차감 검증
# - 적정 유저: 50 → 100 → 200 → 300
HIGH_INTENT_TRAFFIC = {
    BrowsingUser: 60,
    CartUser: 25,
    OrderUser: 12,
    PaymentUser: 3,
}

# 📌 프리셋 4: Realistic Traffic (현실적 혼합)
# - 용도: 실제 프로덕션과 유사한 트래픽
# - 적정 유저: 100 → 300 → 500 → 700 → 900
REALISTIC_TRAFFIC = {
    BrowsingUser: 65,
    CartUser: 25,
    OrderUser: 8,
    PaymentUser: 2,
}

# 📌 프리셋 5: Stress Test (극단 시나리오)
# - 용도: 결제 API + 비동기 워커 최대 부하
# - 적정 유저: 10 → 20 → 50 → 100 (주의: 매우 높은 부하!)
STRESS_TEST = {
    BrowsingUser: 0,
    CartUser: 0,
    OrderUser: 0,
    PaymentUser: 100,
}

# ==================== 실제 사용할 시나리오 선택 ====================
# 👇 여기서 원하는 프리셋을 선택하세요
CURRENT_SCENARIO = REALISTIC_TRAFFIC  # ✅ 기본값: 현실적 트래픽


class WebsiteUser(HttpUser):
    """웹사이트 사용자 - Weight 기반 혼합"""

    # 선택한 시나리오 적용
    tasks = CURRENT_SCENARIO

    # 더 현실적인 대기 시간 (기존 1~5초 → 3~15초)
    wait_time = between(3, 15)

    def on_start(self):
        """초기화 - 모든 사용자 타입이 공유"""
        self.product_ids = []
        self.is_logged_in = False  # 로그인 상태 추적

        # 상품 ID 조회 (첫 3페이지만 - 전체 조회는 과도)
        for page in range(1, 4):
            response = self.client.get(f"/api/products/?page={page}")
            if response.status_code == 200:
                results = response.json().get("results", [])
                self.product_ids.extend([p["id"] for p in results])

        # 30%만 미리 로그인 (BrowsingUser, CartUser용)
        if random.random() < 0.3:
            self.login()

    def login(self):
        """로그인"""
        # 이미 로그인되어 있으면 스킵
        if self.is_logged_in:
            return

        user_id = random.randint(0, 999)
        response = self.client.post(
            "/api/auth/login/", json={"username": f"load_test_user_{user_id}", "password": "testpass123"}
        )
        if response.status_code == 200:
            token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {token}"})
            self.is_logged_in = True


# ==================== 🔥 점진적 부하 증가 (Optional) ====================
# 사용법: locust -f locustfile.py --host=http://localhost:8000
#         (LoadTestShape 활성화하려면 아래 주석 해제)

# class RealisticLoadShape(LoadTestShape):
#     """
#     점진적 부하 증가 패턴
#     - 1분 워밍업
#     - 3분 증가
#     - 5분 피크
#     - 2분 감소
#     """
#
#     stages = [
#         {"duration": 60, "users": 10, "spawn_rate": 2},    # 워밍업
#         {"duration": 180, "users": 50, "spawn_rate": 5},   # 증가
#         {"duration": 300, "users": 100, "spawn_rate": 5},  # 피크
#         {"duration": 420, "users": 50, "spawn_rate": 5},   # 감소
#         {"duration": 480, "users": 10, "spawn_rate": 2},   # 진정
#     ]
#
#     def tick(self):
#         run_time = self.get_run_time()
#
#         for stage in self.stages:
#             if run_time < stage["duration"]:
#                 return (stage["users"], stage["spawn_rate"])
#
#         return None  # 테스트 종료
