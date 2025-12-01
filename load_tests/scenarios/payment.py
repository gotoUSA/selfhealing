"""결제 시나리오 - 대규모 동시 결제 테스트

목적: 1000명이 동시에 결제할 때 시스템 안정성 검증
"""
from locust import HttpUser, task, between
import random


class PaymentUser(HttpUser):
    """결제만 집중 테스트하는 사용자"""
    wait_time = between(0.1, 0.5)  # 빠른 요청 (부하 테스트)

    def on_start(self):
        """테스트 시작 전 로그인 및 주문 생성"""
        # 1. 로그인
        response = self.client.post("/api/auth/login/", json={
            "username": f"perf_user_{random.randint(1, 1000)}",
            "password": "testpass123"
        })
        if response.status_code == 200:
            self.token = response.json().get("access")
            self.client.headers.update({
                "Authorization": f"Bearer {self.token}"
            })

        # 2. 주문 생성 (미리 준비)
        # 실제로는 주문을 미리 생성해두고 시작하는 것이 좋음

    @task
    def concurrent_payment(self):
        """동시 결제 승인 요청"""
        self.client.post("/api/payments/confirm/", json={
            "payment_key": f"load_key_{random.randint(1, 100000)}",
            "order_id": f"ORDER_{random.randint(1, 10000)}",
            "amount": 13000
        }, name="/api/payments/confirm/ [Concurrent]")
