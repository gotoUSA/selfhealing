"""
대규모 부하 테스트 (1000명+ 동시 사용자)

실행 방법:
    # 기본 실행 (1000명, 점진적 증가)
    locust -f load_tests/large_scale_load_test.py --host=http://localhost:8000

    # 헤드리스 모드 (1000명 피크)
    locust -f load_tests/large_scale_load_test.py --host=http://localhost:8000 \
        --headless -u 1000 -r 50 --run-time 10m

    # 분산 모드 (Master)
    locust -f load_tests/large_scale_load_test.py --host=http://localhost:8000 --master

    # 분산 모드 (Worker)
    locust -f load_tests/large_scale_load_test.py --host=http://localhost:8000 --worker

웹 UI:
    http://localhost:8089

목적:
    - 1000명+ 동시 사용자 시나리오 테스트
    - 동시성 제어 모니터링 (락 경합, 데드락 감지)
    - 시스템 한계점 파악
"""

import random
import time
from locust import HttpUser, task, TaskSet, between, LoadTestShape, events
from locust.runners import MasterRunner, WorkerRunner


# ==================== 성능 메트릭 수집 ====================
class PerformanceMetrics:
    """성능 메트릭 수집기"""

    def __init__(self):
        self.total_orders = 0
        self.successful_orders = 0
        self.failed_orders = 0
        self.stock_errors = 0
        self.lock_timeout_errors = 0
        self.concurrent_conflicts = 0

    def reset(self):
        self.total_orders = 0
        self.successful_orders = 0
        self.failed_orders = 0
        self.stock_errors = 0
        self.lock_timeout_errors = 0
        self.concurrent_conflicts = 0

    def report(self):
        """메트릭 리포트 출력"""
        if self.total_orders > 0:
            success_rate = (self.successful_orders / self.total_orders) * 100
        else:
            success_rate = 0

        print("\n" + "=" * 60)
        print("📊 대규모 부하 테스트 결과 요약")
        print("=" * 60)
        print(f"  총 주문 시도:       {self.total_orders:,}")
        print(f"  성공한 주문:        {self.successful_orders:,}")
        print(f"  실패한 주문:        {self.failed_orders:,}")
        print(f"  성공률:             {success_rate:.2f}%")
        print("-" * 60)
        print(f"  재고 부족 에러:     {self.stock_errors:,}")
        print(f"  락 타임아웃:        {self.lock_timeout_errors:,}")
        print(f"  동시성 충돌:        {self.concurrent_conflicts:,}")
        print("=" * 60 + "\n")


metrics = PerformanceMetrics()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 메트릭 출력"""
    if not isinstance(environment.runner, WorkerRunner):
        metrics.report()


# ==================== 브라우징 전용 사용자 ====================
class BrowsingOnlyUser(TaskSet):
    """
    브라우징만 하는 사용자 (비로그인)
    - DB read 부하 테스트
    - 캐시 효율성 검증
    """

    @task(10)
    def browse_product_list(self):
        """상품 목록 조회 (페이지네이션)"""
        page = random.randint(1, 10)
        with self.client.get(
            f"/api/products/?page={page}",
            catch_response=True,
            name="/api/products/?page=[N]"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

    @task(5)
    def view_product_detail(self):
        """상품 상세 조회"""
        if hasattr(self.user, "product_ids") and self.user.product_ids:
            product_id = random.choice(self.user.product_ids)
            self.client.get(f"/api/products/{product_id}/")

    @task(2)
    def search_products(self):
        """상품 검색"""
        keywords = ["테스트", "상품", "성능", "로드"]
        keyword = random.choice(keywords)
        self.client.get(f"/api/products/?search={keyword}")

    @task(1)
    def view_categories(self):
        """카테고리 조회"""
        self.client.get("/api/categories/")


# ==================== 장바구니 사용자 ====================
class CartIntenseUser(TaskSet):
    """
    장바구니 집중 사용자
    - Cart DB I/O 부하 테스트
    - 동시 장바구니 수정 테스트
    """

    def on_start(self):
        """TaskSet 시작 시 로그인"""
        if not getattr(self.user, "is_logged_in", False):
            self.user.login()

    @task(5)
    def add_to_cart(self):
        """장바구니에 상품 추가"""
        if hasattr(self.user, "product_ids") and self.user.product_ids:
            product_id = random.choice(self.user.product_ids)
            self.client.post(
                "/api/cart-items/",
                json={"product_id": product_id, "quantity": random.randint(1, 3)}
            )

    @task(3)
    def view_cart(self):
        """장바구니 확인"""
        self.client.get("/api/cart-items/")

    @task(2)
    def update_cart_item(self):
        """장바구니 수량 변경"""
        response = self.client.get("/api/cart-items/")
        if response.status_code == 200:
            items = response.json()
            if items and len(items) > 0:
                item_id = items[0].get("id")
                if item_id:
                    self.client.put(
                        f"/api/cart-items/{item_id}/",
                        json={"quantity": random.randint(1, 5)}
                    )

    @task(1)
    def delete_cart_item(self):
        """장바구니 아이템 삭제"""
        response = self.client.get("/api/cart-items/")
        if response.status_code == 200:
            items = response.json()
            if items and len(items) > 0:
                item_id = items[0].get("id")
                if item_id:
                    self.client.delete(f"/api/cart-items/{item_id}/")


# ==================== 주문 집중 사용자 ====================
class OrderIntenseUser(TaskSet):
    """
    주문 집중 사용자
    - 주문 생성 + 재고 차감 동시성 테스트
    - 락 경합 시나리오
    """

    def on_start(self):
        """TaskSet 시작 시 로그인"""
        if not getattr(self.user, "is_logged_in", False):
            self.user.login()

    @task
    def create_order_flow(self):
        """주문 생성 플로우"""
        if not hasattr(self.user, "product_ids") or not self.user.product_ids:
            return

        metrics.total_orders += 1

        # 1. 장바구니 비우기 (기존 아이템 제거)
        cart_response = self.client.get("/api/cart-items/")
        if cart_response.status_code == 200:
            items = cart_response.json()
            for item in items:
                self.client.delete(f"/api/cart-items/{item['id']}/")

        # 2. 상품 추가 (동시성 테스트를 위해 동일 상품 선택 확률 높임)
        # 상위 5개 상품 중에서 선택 (재고 경합 유발)
        hot_products = self.user.product_ids[:5] if len(self.user.product_ids) >= 5 else self.user.product_ids
        product_id = random.choice(hot_products)

        add_response = self.client.post(
            "/api/cart-items/",
            json={"product_id": product_id, "quantity": random.randint(1, 2)}
        )

        if add_response.status_code != 201:
            metrics.failed_orders += 1
            return

        # 3. 주문 생성
        with self.client.post(
            "/api/orders/",
            json={
                "shipping_name": f"테스트유저_{random.randint(1, 10000)}",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구 테헤란로",
                "shipping_address_detail": f"{random.randint(1, 100)}호",
            },
            catch_response=True,
            name="/api/orders/ [CREATE]"
        ) as response:
            if response.status_code in [201, 202]:
                metrics.successful_orders += 1
                response.success()
            elif response.status_code == 400:
                # 재고 부족 등
                response_text = response.text.lower()
                if "재고" in response_text or "stock" in response_text:
                    metrics.stock_errors += 1
                elif "lock" in response_text or "timeout" in response_text:
                    metrics.lock_timeout_errors += 1
                else:
                    metrics.concurrent_conflicts += 1
                metrics.failed_orders += 1
                response.failure(f"Order failed: {response.text[:100]}")
            else:
                metrics.failed_orders += 1
                response.failure(f"Status: {response.status_code}")


# ==================== 결제 완료 사용자 ====================
class PaymentCompleteUser(TaskSet):
    """
    결제 완료까지 진행하는 사용자
    - 전체 구매 플로우 테스트
    - 결제 동시성 테스트
    """

    def on_start(self):
        """TaskSet 시작 시 로그인"""
        if not getattr(self.user, "is_logged_in", False):
            self.user.login()

    @task
    def complete_purchase(self):
        """완전한 구매 플로우"""
        if not hasattr(self.user, "product_ids") or not self.user.product_ids:
            return

        # 1. 장바구니 준비
        cart_response = self.client.get("/api/cart-items/")
        if cart_response.status_code == 200:
            items = cart_response.json()
            for item in items:
                self.client.delete(f"/api/cart-items/{item['id']}/")

        # 2. 상품 추가
        product_id = random.choice(self.user.product_ids)
        add_response = self.client.post(
            "/api/cart-items/",
            json={"product_id": product_id, "quantity": 1}
        )

        if add_response.status_code != 201:
            return

        # 3. 주문 생성
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "테스트",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101호",
            }
        )

        if order_response.status_code not in [201, 202]:
            return

        order_data = order_response.json()
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 4. 결제 승인
        payment_key = f"test_payment_{int(time.time() * 1000)}_{random.randint(1, 999999)}"

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount)
            },
            catch_response=True,
            name="/api/payments/confirm/ [PAYMENT]"
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")


# ==================== 메인 사용자 클래스 ====================
class LargeScaleUser(HttpUser):
    """
    대규모 부하 테스트용 사용자

    시나리오 비율:
    - 브라우징: 60% (비로그인 트래픽)
    - 장바구니: 20% (로그인 필요)
    - 주문 생성: 15% (동시성 테스트 핵심)
    - 결제 완료: 5% (전체 플로우)
    """

    tasks = {
        BrowsingOnlyUser: 60,
        CartIntenseUser: 20,
        OrderIntenseUser: 15,
        PaymentCompleteUser: 5,
    }

    # 대기 시간 (1~5초)
    wait_time = between(1, 5)

    def on_start(self):
        """초기화"""
        self.product_ids = []
        self.is_logged_in = False

        # 상품 ID 수집
        for page in range(1, 6):
            response = self.client.get(f"/api/products/?page={page}")
            if response.status_code == 200:
                results = response.json().get("results", [])
                self.product_ids.extend([p["id"] for p in results])

    def login(self):
        """로그인"""
        if self.is_logged_in:
            return True

        user_id = random.randint(0, 999)
        response = self.client.post(
            "/api/auth/login/",
            json={
                "username": f"load_test_user_{user_id}",
                "password": "testpass123"
            }
        )

        if response.status_code == 200:
            token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {token}"})
            self.is_logged_in = True
            return True

        return False


# ==================== 점진적 부하 증가 패턴 ====================
class LargeScaleLoadShape(LoadTestShape):
    """
    대규모 점진적 부하 증가 패턴

    단계:
    1. 워밍업: 0-2분, 100명까지
    2. 증가 1: 2-5분, 300명까지
    3. 증가 2: 5-8분, 600명까지
    4. 피크: 8-15분, 1000명 유지
    5. 스파이크: 15-17분, 1200명 (과부하 테스트)
    6. 피크 유지: 17-25분, 1000명
    7. 감소: 25-30분, 500명까지
    8. 종료: 30분 후 테스트 종료
    """

    stages = [
        # (duration_seconds, users, spawn_rate)
        {"duration": 120, "users": 100, "spawn_rate": 10},      # 워밍업
        {"duration": 300, "users": 300, "spawn_rate": 15},      # 증가 1
        {"duration": 480, "users": 600, "spawn_rate": 20},      # 증가 2
        {"duration": 900, "users": 1000, "spawn_rate": 25},     # 피크
        {"duration": 1020, "users": 1200, "spawn_rate": 50},    # 스파이크
        {"duration": 1500, "users": 1000, "spawn_rate": 25},    # 피크 유지
        {"duration": 1800, "users": 500, "spawn_rate": 20},     # 감소
    ]

    def tick(self):
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])

        return None  # 테스트 종료


# ==================== 대안: 간단한 점진적 증가 ====================
class SimpleStepLoadShape(LoadTestShape):
    """
    간단한 단계별 부하 증가 패턴

    5분마다 200명씩 증가:
    - 0-5분: 200명
    - 5-10분: 400명
    - 10-15분: 600명
    - 15-20분: 800명
    - 20-25분: 1000명
    - 25-30분: 유지
    """

    step_duration = 300  # 5분
    step_users = 200     # 단계당 증가 사용자
    max_users = 1000     # 최대 사용자
    spawn_rate = 25      # 초당 생성

    def tick(self):
        run_time = self.get_run_time()

        # 30분 후 종료
        if run_time > 1800:
            return None

        current_step = int(run_time / self.step_duration)
        target_users = min((current_step + 1) * self.step_users, self.max_users)

        return (target_users, self.spawn_rate)
