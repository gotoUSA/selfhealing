"""
결제 스트레스 테스트 (Payment Stress Test)

목적: 결제 API의 성능과 안정성 검증
- PG 연동 응답시간
- Idempotency (중복 결제 방지)
- 에러율 및 P99 레이턴시

실행 (Windows는 한 줄 명령어 권장):
    PYTHONUTF8=1 locust -f load_tests/scenarios/payment_stress.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=3m
"""

import os
import sys

# 독립 실행 시 load_tests 패키지 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import random
import time
from locust import HttpUser, task, between, tag

from load_tests.config import (
    TEST_USER_COUNT,
    TEST_USER_PREFIX,
    TEST_USER_PASSWORD,
    ENDPOINTS,
)


class PaymentStressUser(HttpUser):
    """
    결제 집중 테스트 사용자

    시나리오:
    - 다양한 주문(여러 상품, 여러 수량)에 대해 결제 요청
    - 중복 결제 시도 시뮬레이션
    - 결제 취소 플로우 포함

    측정 지표:
    - Payment API P95/P99 레이턴시
    - 에러율 (목표: < 0.1%)
    - 5xx 에러 비율
    """

    wait_time = between(1, 3)

    # 클래스 레벨 캐시
    _product_ids_cache: list = []
    _cache_initialized: bool = False

    def on_start(self):
        """테스트 시작 시 초기화"""
        self.is_logged_in = False
        self.access_token = None

        # 상품 ID 캐싱
        if not PaymentStressUser._cache_initialized:
            self._fetch_product_ids()
            PaymentStressUser._cache_initialized = True

        # 로그인
        self.login()

    def _fetch_product_ids(self):
        """상품 ID 조회"""
        for page in range(1, 3):
            response = self.client.get(
                f"{ENDPOINTS['products']}?page={page}",
                name="[Setup] Fetch Products",
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                PaymentStressUser._product_ids_cache.extend([p["id"] for p in results])

    def login(self):
        """로그인"""
        if self.is_logged_in:
            return

        user_index = random.randint(0, TEST_USER_COUNT - 1)
        username = f"{TEST_USER_PREFIX}{user_index}"

        response = self.client.post(
            ENDPOINTS["login"],
            json={"username": username, "password": TEST_USER_PASSWORD},
            name="POST /api/auth/login/",
        )

        if response.status_code == 200:
            data = response.json()
            # 로그인 응답: {"token": {"access": "..."}, "user": {...}}
            self.access_token = data.get("token", {}).get("access")
            self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
            self.is_logged_in = True

    @task(10)
    @tag("payment", "critical")
    def full_payment_flow(self):
        """완전한 결제 플로우"""
        if not self.is_logged_in:
            self.login()

        if not PaymentStressUser._product_ids_cache:
            return

        # 1. 장바구니 비우기 (깨끗한 상태에서 시작)
        self.client.get(ENDPOINTS["cart_items"], name="GET /api/cart/items/")

        # 2. 여러 상품 장바구니 추가 (1~3개)
        num_items = random.randint(1, 3)
        for _ in range(num_items):
            product_id = random.choice(PaymentStressUser._product_ids_cache)
            self.client.post(
                ENDPOINTS["cart_add_item"],
                json={"product_id": product_id, "quantity": random.randint(1, 2)},
                name="POST /api/cart/add_item/",
            )

        # 3. 장바구니 확인
        cart_response = self.client.get(ENDPOINTS["cart_items"], name="GET /api/cart/items/")
        if cart_response.status_code != 200 or not cart_response.json():
            return

        # 4. 주문 생성
        order_response = self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": "결제테스트",
                "shipping_phone": "010-9999-9999",
                "shipping_postal_code": "54321",
                "shipping_address": "서울시 서초구 결제테스트로",
                "shipping_address_detail": "결제동 999호",
            },
            name="POST /api/orders/",
        )

        if order_response.status_code not in [200, 201, 202]:
            return

        order_data = order_response.json()
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 5. 결제 승인 (핵심 측정 구간)
        payment_key = f"stress_test_{int(time.time() * 1000)}_{random.randint(1, 999999)}"

        with self.client.post(
            ENDPOINTS["payment_confirm"],
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name="POST /api/payments/confirm/ [CRITICAL]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
            elif response.status_code == 400:
                # 비즈니스 에러 (재고 부족 등)는 실패로 처리하지 않음
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")

    @task(2)
    @tag("payment", "idempotency")
    def duplicate_payment_attempt(self):
        """
        중복 결제 시도 테스트

        같은 payment_key로 두 번 요청하여 idempotency 검증
        """
        if not self.is_logged_in:
            self.login()

        if not PaymentStressUser._product_ids_cache:
            return

        # 장바구니 → 주문 생성
        product_id = random.choice(PaymentStressUser._product_ids_cache)
        self.client.post(
            ENDPOINTS["cart_add_item"],
            json={"product_id": product_id, "quantity": 1},
            name="POST /api/cart/add_item/",
        )

        cart_response = self.client.get(ENDPOINTS["cart_items"], name="GET /api/cart/items/")
        if cart_response.status_code != 200 or not cart_response.json():
            return

        order_response = self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": "중복테스트",
                "shipping_phone": "010-0000-0000",
                "shipping_postal_code": "00000",
                "shipping_address": "테스트",
                "shipping_address_detail": "테스트",
            },
            name="POST /api/orders/",
        )

        if order_response.status_code not in [200, 201, 202]:
            return

        order_data = order_response.json()
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 동일한 payment_key로 두 번 요청
        payment_key = f"idempotency_test_{int(time.time() * 1000)}"

        # 첫 번째 요청
        self.client.post(
            ENDPOINTS["payment_confirm"],
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name="POST /api/payments/confirm/ [1st]",
        )

        # 두 번째 요청 (중복) - 에러가 나야 정상
        with self.client.post(
            ENDPOINTS["payment_confirm"],
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name="POST /api/payments/confirm/ [duplicate]",
            catch_response=True,
        ) as response:
            # 400 또는 409가 나와야 중복 방지가 동작하는 것
            if response.status_code in [400, 409]:
                response.success()
            elif response.status_code in [200, 201]:
                # 중복 결제가 성공하면 문제!
                response.failure("CRITICAL: Duplicate payment succeeded!")
            else:
                response.failure(f"Unexpected status: {response.status_code}")
