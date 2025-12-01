"""
포인트 시스템 대규모 동시성 부하 테스트

⚠️  사전 준비 (필수):
    1. Celery Worker 실행:
       celery -A myproject worker --loglevel=info --pool=solo  (Windows)
       celery -A myproject worker --loglevel=info              (Linux/Mac)

    2. Redis 실행:
       redis-server  (또는 Docker: docker run -p 6379:6379 redis)

    3. Django 개발 서버 실행:
       python manage.py runserver

    4. 테스트 데이터 생성:
       python manage.py create_load_test_users --count 1000 --points 50000

실행 방법:
    # 기본 실행
    locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000

    # 1000명 동시 사용자, 초당 50명씩 증가
    locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000 --users 1000 --spawn-rate 50

    # Headless 모드 (30초 실행)
    locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000 --users 1000 --spawn-rate 50 --run-time 30s --headless

목표:
    - 1000명 이상 동시 포인트 사용
    - 비동기 주문 처리 (Celery) 동시성 검증
    - DB 커넥션 풀 충분성 검증
    - 응답 시간 및 처리량(TPS) 측정
    - select_for_update 및 F() 객체 동시성 제어 검증

주의사항:
    - ⚠️  Celery worker가 실행 중이어야 주문이 실제로 처리됩니다!
    - 주문은 비동기로 처리되므로 재고 차감에 약간의 지연이 있을 수 있습니다
    - DB 커넥션 풀 설정 확인 (DATABASES['default']['CONN_MAX_AGE'])
    - 프로덕션 환경에서는 실행 금지
"""

from locust import HttpUser, TaskSet, task, between
import random


class PointBehavior(TaskSet):
    """포인트 관련 사용자 행동 시나리오"""

    def on_start(self):
        """태스크 시작 시 로그인 및 초기화"""
        # PointConcurrentUser는 0-499 범위 사용 (충돌 방지)
        user_number = hash(id(self)) % 500  # 0-499 범위
        username = f"load_test_user_{user_number}"
        password = "testpass123"

        response = self.client.post("/api/auth/login/", json={"username": username, "password": password}, name="Login")

        # load_test_user 실패 시 testuser_N 시도 (fallback)
        if response.status_code != 200 and user_number < 20:
            username = f"testuser_{user_number}"
            response = self.client.post("/api/auth/login/", json={"username": username, "password": password}, name="Login")

        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access")
            self.user_id = data.get("user", {}).get("id")

            # 사용 가능한 상품 ID 목록 가져오기
            products_response = self.client.get("/api/products/?page_size=100", name="Get Products")
            if products_response.status_code == 200:
                products_data = products_response.json()
                # 페이지네이션 처리
                if isinstance(products_data, dict) and "results" in products_data:
                    products = products_data["results"]
                else:
                    products = products_data

                # 재고가 충분한 상품 우선 (100개 이상)
                self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) >= 100]
                if not self.available_product_ids:
                    # 재고 10개 이상
                    self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) >= 10]
                if not self.available_product_ids:
                    # 재고 1개 이상
                    self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) > 0]
                if not self.available_product_ids:
                    # 마지막: 모든 상품 (재고 없어도)
                    self.available_product_ids = [p["id"] for p in products] if products else [1]
            else:
                self.available_product_ids = [1]  # fallback
        else:
            # 로그인 실패 시 테스트 중단
            print(f"로그인 실패: {username}, 상태 코드: {response.status_code}, 응답: {response.text[:200]}")
            self.token = None
            self.available_product_ids = []
            self.interrupt()

    @task(5)
    def view_my_points(self):
        """내 포인트 조회 (가장 빈번한 작업)"""
        if not self.token:
            return

        self.client.get("/api/points/my/", headers={"Authorization": f"Bearer {self.token}"}, name="View My Points")

    @task(3)
    def view_point_history(self):
        """포인트 이력 조회"""
        if not self.token:
            return

        self.client.get("/api/points/history/", headers={"Authorization": f"Bearer {self.token}"}, name="View Point History")

    @task(2)
    def check_expiring_points(self):
        """만료 예정 포인트 조회"""
        if not self.token:
            return

        self.client.get(
            "/api/points/expiring/", headers={"Authorization": f"Bearer {self.token}"}, name="Check Expiring Points"
        )

    @task(1)
    def create_order_with_points(self):
        """주문 생성 시 포인트 사용 (실제 포인트 차감 테스트)"""
        if not self.token or not self.available_product_ids:
            return

        # 1. 장바구니에 상품 추가 (랜덤 상품 선택)
        # 주문 완료 시 장바구니가 자동으로 비워지므로 별도로 비울 필요 없음
        product_id = random.choice(self.available_product_ids)
        cart_response = self.client.post(
            "/api/cart-items/",
            json={"product_id": product_id, "quantity": 1},
            headers={"Authorization": f"Bearer {self.token}"},
            name="Add to Cart",
        )

        if cart_response.status_code not in [200, 201]:
            print(
                f"[DEBUG] 장바구니 추가 실패: 상품ID={product_id}, 상태={cart_response.status_code}, 응답={cart_response.text[:300]}"
            )
            return

        # 장바구니 추가 성공 확인
        try:
            cart_item_data = cart_response.json()
            print(f"[DEBUG] 장바구니 추가 성공: 상품ID={product_id}, 응답={cart_item_data}")
        except Exception as e:
            print(f"[DEBUG] 장바구니 추가 응답 파싱 실패: {e}")

        # 2. 포인트 사용하여 주문 생성 (포인트 부족 방지)
        points_to_use = random.choice([0, 100, 500, 1000])  # 0 포함

        with self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "부하테스트",
                "shipping_phone": f"010-{random.randint(1000, 9999):04d}-{random.randint(1000, 9999):04d}",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101동",
                "use_points": points_to_use,
            },
            headers={"Authorization": f"Bearer {self.token}"},
            name="Create Order with Points",
            catch_response=True,
        ) as order_response:
            if order_response.status_code != 202:
                # 주문 실패 상세 로그
                try:
                    error_detail = order_response.json()
                    print(f"[ERROR] 주문 실패: 상태={order_response.status_code}, 상품ID={product_id}, 에러={error_detail}")
                except:
                    print(
                        f"[ERROR] 주문 실패: 상태={order_response.status_code}, 상품ID={product_id}, 응답={order_response.text[:500]}"
                    )
                order_response.failure(f"Order failed: {order_response.status_code}")
            else:
                order_data = order_response.json()
                order_id = order_data.get("order_id")
                order_response.success()

                # 3. 주문 취소 (포인트 반환 테스트)
                if order_id and random.random() < 0.3:  # 30% 확률로 취소
                    self.client.post(
                        f"/api/orders/{order_id}/cancel/",
                        headers={"Authorization": f"Bearer {self.token}"},
                        name="Cancel Order (Return Points)",
                    )

    @task(1)
    def check_point_availability(self):
        """포인트 사용 가능 여부 확인"""
        if not self.token:
            return

        self.client.post(
            "/api/points/check/",
            json={"order_amount": 50000, "use_points": 5000},
            headers={"Authorization": f"Bearer {self.token}"},
            name="Check Point Availability",
        )


class PointConcurrentUser(HttpUser):
    """포인트 시스템 부하 테스트 사용자"""

    tasks = [PointBehavior]

    # 사용자 행동 간 대기 시간 (1~3초 랜덤)
    wait_time = between(1, 3)

    # 테스트 대상 호스트
    host = "http://localhost:8000"


class PointHighLoadUser(HttpUser):
    """고부하 포인트 사용 시나리오 (포인트 사용만 집중)"""

    wait_time = between(0.5, 1.5)
    host = "http://localhost:8000"

    def on_start(self):
        """로그인 및 초기화"""
        # PointHighLoadUser는 500-999 범위 사용 (충돌 방지)
        user_number = 500 + (hash(id(self)) % 500)  # 500-999 범위
        username = f"load_test_user_{user_number}"
        password = "testpass123"

        response = self.client.post("/api/auth/login/", json={"username": username, "password": password}, name="Login")

        if response.status_code == 200:
            self.token = response.json().get("access")

            # 사용 가능한 상품 ID 목록 가져오기
            products_response = self.client.get("/api/products/?page_size=100", name="Get Products")
            if products_response.status_code == 200:
                products_data = products_response.json()
                # 페이지네이션 처리
                if isinstance(products_data, dict) and "results" in products_data:
                    products = products_data["results"]
                else:
                    products = products_data

                # 재고가 충분한 상품 우선 (100개 이상)
                self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) >= 100]
                if not self.available_product_ids:
                    # 재고 10개 이상
                    self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) >= 10]
                if not self.available_product_ids:
                    # 재고 1개 이상
                    self.available_product_ids = [p["id"] for p in products if p.get("stock_quantity", 0) > 0]
                if not self.available_product_ids:
                    # 마지막: 모든 상품 (재고 없어도)
                    self.available_product_ids = [p["id"] for p in products] if products else [1]
            else:
                self.available_product_ids = [1]  # fallback
        else:
            self.token = None
            self.available_product_ids = []
            self.environment.runner.quit()  # 로그인 실패 시 테스트 중단

    @task
    def rapid_order_with_points(self):
        """고부하 포인트 사용 - 빠른 주문 생성"""
        if not self.token or not self.available_product_ids:
            return

        # 1. 장바구니에 상품 추가 (주문 완료 시 자동으로 비워짐)
        product_id = random.choice(self.available_product_ids)
        cart_response = self.client.post(
            "/api/cart-items/",
            json={"product_id": product_id, "quantity": 1},
            headers={"Authorization": f"Bearer {self.token}"},
            name="Quick Add to Cart",
        )

        if cart_response.status_code not in [200, 201]:
            return

        # 2. 포인트 사용하여 즉시 주문
        points_to_use = random.choice([0, 100, 500])  # 0 포함하여 실패율 감소

        with self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "고부하테스트",
                "shipping_phone": f"010-{random.randint(1000, 9999):04d}-{random.randint(1000, 9999):04d}",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101동",
                "use_points": points_to_use,
            },
            headers={"Authorization": f"Bearer {self.token}"},
            name="Rapid Order with Points",
            catch_response=True,
        ) as order_response:
            if order_response.status_code == 202:
                order_response.success()
            else:
                order_response.failure(f"Order failed: {order_response.status_code}")


# 실행 예시:
# 1. 일반 부하 테스트 (혼합 시나리오)
#    locust -f locustfiles/point_concurrent_load_test.py --users 1000 --spawn-rate 50 PointConcurrentUser
#
# 2. 고부하 테스트 (포인트 사용만)
#    locust -f locustfiles/point_concurrent_load_test.py --users 1000 --spawn-rate 100 PointHighLoadUser
#
# 3. 웹 UI로 실행
#    locust -f locustfiles/point_concurrent_load_test.py
#    브라우저에서 http://localhost:8089 접속
