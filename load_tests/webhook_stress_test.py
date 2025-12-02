"""
Webhook 스트레스 테스트 - Locust 기반

- Locust 웹훅 스트레스 테스트
- 동시 웹훅 도착 시뮬레이션

실행 방법:
    # 기본 실행 (웹 UI)
    locust -f load_tests/webhook_stress_test.py --host=http://localhost:8000

    # 헤드리스 실행 (CI/CD용)
    locust -f load_tests/webhook_stress_test.py \
        --host=http://localhost:8000 \
        --headless \
        --users 50 \
        --spawn-rate 10 \
        --run-time 60s

웹 UI:
    http://localhost:8089

주의사항:
- 이 테스트는 웹훅 엔드포인트의 동시성 제어를 검증합니다.
- 테스트 전 load_tests/setup_test_data.py를 실행하여 테스트 데이터를 생성하세요.
- 테스트 환경에서 웹훅 서명 검증이 비활성화되어 있어야 합니다.
"""

import hashlib
import hmac
import json
import random
import time
import uuid
from datetime import datetime

from locust import HttpUser, between, task, TaskSet


class WebhookPayload:
    """웹훅 페이로드 생성 유틸리티"""

    @staticmethod
    def payment_done(order_id: str, payment_key: str = None, amount: int = 10000) -> dict:
        """PAYMENT.DONE 이벤트 페이로드 생성"""
        if payment_key is None:
            payment_key = f"test_key_{uuid.uuid4().hex[:16]}"

        return {
            "eventType": "PAYMENT.DONE",
            "data": {
                "paymentKey": payment_key,
                "orderId": order_id,
                "status": "DONE",
                "totalAmount": amount,
                "method": "카드",
                "approvedAt": datetime.now().isoformat(),
                "card": {
                    "company": "신한카드",
                    "number": "1234****5678",
                    "installmentPlanMonths": 0,
                },
            },
        }

    @staticmethod
    def payment_canceled(order_id: str, payment_key: str = None) -> dict:
        """PAYMENT.CANCELED 이벤트 페이로드 생성"""
        if payment_key is None:
            payment_key = f"test_key_{uuid.uuid4().hex[:16]}"

        return {
            "eventType": "PAYMENT.CANCELED",
            "data": {
                "paymentKey": payment_key,
                "orderId": order_id,
                "status": "CANCELED",
                "cancelReason": "고객 변심",
                "canceledAt": datetime.now().isoformat(),
            },
        }

    @staticmethod
    def payment_failed(order_id: str) -> dict:
        """PAYMENT.FAILED 이벤트 페이로드 생성"""
        return {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": order_id,
                "failReason": "카드 한도 초과",
            },
        }


class WebhookSignature:
    """웹훅 서명 생성 (테스트용)"""

    # 테스트용 웹훅 시크릿 (실제 환경에서는 환경변수로 관리)
    TEST_WEBHOOK_SECRET = "test_webhook_secret_key"

    @classmethod
    def generate(cls, payload: dict) -> str:
        """
        웹훅 서명 생성

        Note: 테스트 환경에서는 실제 서명 검증이 비활성화될 수 있습니다.
        """
        payload_str = json.dumps(payload, separators=(",", ":"))
        signature = hmac.new(
            cls.TEST_WEBHOOK_SECRET.encode(),
            payload_str.encode(),
            hashlib.sha256,
        ).hexdigest()
        return signature


class WebhookStressTaskSet(TaskSet):
    """웹훅 스트레스 테스트 태스크셋"""

    # 테스트에 사용할 주문 ID 풀
    order_ids = []

    def on_start(self):
        """테스트 시작 시 주문 ID 목록 로드"""
        # 실제 환경에서는 DB에서 주문 ID를 가져오거나 setup 스크립트에서 생성
        # 여기서는 시뮬레이션을 위해 랜덤 ID 생성
        self.order_ids = [str(i) for i in range(1, 1001)]

    @task(10)
    def send_payment_done_webhook(self):
        """
        PAYMENT.DONE 웹훅 전송 (높은 빈도)

        실제 토스페이먼츠가 결제 완료 시 보내는 웹훅을 시뮬레이션
        """
        order_id = random.choice(self.order_ids)
        payment_key = f"test_key_{uuid.uuid4().hex[:16]}"
        amount = random.choice([10000, 20000, 30000, 50000])

        payload = WebhookPayload.payment_done(
            order_id=order_id,
            payment_key=payment_key,
            amount=amount,
        )

        signature = WebhookSignature.generate(payload)

        with self.client.post(
            "/api/webhooks/toss/",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Toss-Webhook-Signature": signature,
            },
            catch_response=True,
            name="Webhook: PAYMENT.DONE",
        ) as response:
            if response.status_code in [200, 400, 401, 404]:
                # 성공 또는 비즈니스 로직 에러 (중복, 존재하지 않음 등)
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(5)
    def send_duplicate_payment_done_webhook(self):
        """
        중복 PAYMENT.DONE 웹훅 전송 (동일 주문에 여러 번)

        동시성 제어 검증: 동일 주문에 대해 여러 번 웹훅이 도착해도 1번만 처리
        """
        # 같은 주문 ID와 payment_key로 여러 번 전송
        order_id = random.choice(self.order_ids[:100])  # 앞쪽 ID들만 사용 (중복 확률 높임)
        payment_key = f"dup_test_key_{order_id}"

        payload = WebhookPayload.payment_done(
            order_id=order_id,
            payment_key=payment_key,
            amount=10000,
        )

        signature = WebhookSignature.generate(payload)

        # 2~3번 연속 전송
        for i in range(random.randint(2, 3)):
            with self.client.post(
                "/api/webhooks/toss/",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Toss-Webhook-Signature": signature,
                },
                catch_response=True,
                name="Webhook: Duplicate DONE",
            ) as response:
                if response.status_code in [200, 400, 401, 404]:
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")

    @task(3)
    def send_payment_canceled_webhook(self):
        """
        PAYMENT.CANCELED 웹훅 전송

        결제 취소 이벤트 시뮬레이션
        """
        order_id = random.choice(self.order_ids)
        payment_key = f"cancel_key_{uuid.uuid4().hex[:16]}"

        payload = WebhookPayload.payment_canceled(
            order_id=order_id,
            payment_key=payment_key,
        )

        signature = WebhookSignature.generate(payload)

        with self.client.post(
            "/api/webhooks/toss/",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Toss-Webhook-Signature": signature,
            },
            catch_response=True,
            name="Webhook: PAYMENT.CANCELED",
        ) as response:
            if response.status_code in [200, 400, 401, 404]:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(2)
    def send_payment_failed_webhook(self):
        """
        PAYMENT.FAILED 웹훅 전송

        결제 실패 이벤트 시뮬레이션
        """
        order_id = random.choice(self.order_ids)

        payload = WebhookPayload.payment_failed(order_id=order_id)

        signature = WebhookSignature.generate(payload)

        with self.client.post(
            "/api/webhooks/toss/",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Toss-Webhook-Signature": signature,
            },
            catch_response=True,
            name="Webhook: PAYMENT.FAILED",
        ) as response:
            if response.status_code in [200, 400, 401, 404]:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(1)
    def send_burst_webhooks(self):
        """
        버스트 웹훅 전송 (짧은 시간에 많은 요청)

        네트워크 문제로 인한 재전송 시나리오 시뮬레이션
        """
        order_id = random.choice(self.order_ids[:50])
        payment_key = f"burst_key_{order_id}"

        payload = WebhookPayload.payment_done(
            order_id=order_id,
            payment_key=payment_key,
            amount=10000,
        )

        signature = WebhookSignature.generate(payload)

        # 5개 요청을 빠르게 연속 전송
        for i in range(5):
            with self.client.post(
                "/api/webhooks/toss/",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Toss-Webhook-Signature": signature,
                },
                catch_response=True,
                name="Webhook: Burst",
            ) as response:
                if response.status_code in [200, 400, 401, 404]:
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")


class WebhookUser(HttpUser):
    """웹훅 스트레스 테스트 사용자"""

    tasks = [WebhookStressTaskSet]

    # 요청 간 대기 시간 (짧게 설정하여 높은 부하 생성)
    wait_time = between(0.1, 0.5)

    # 테스트 데이터
    host = "http://localhost:8000"


# ==================== 프리셋 ====================


class LightWebhookUser(HttpUser):
    """경량 웹훅 테스트 (개발 환경용)"""

    tasks = [WebhookStressTaskSet]
    wait_time = between(1, 3)  # 느린 속도


class HeavyWebhookUser(HttpUser):
    """고부하 웹훅 테스트 (스트레스 테스트용)"""

    tasks = [WebhookStressTaskSet]
    wait_time = between(0.05, 0.2)  # 매우 빠른 속도


# ==================== 헬퍼 함수 ====================


def setup_test_orders(api_client, num_orders: int = 100):
    """
    테스트용 주문 데이터 생성

    실행 예시:
        from load_tests.webhook_stress_test import setup_test_orders
        setup_test_orders(client, num_orders=100)
    """
    order_ids = []

    for i in range(num_orders):
        # 주문 생성 로직 (실제 구현 필요)
        # order = create_test_order(...)
        # order_ids.append(str(order.id))
        pass

    return order_ids


if __name__ == "__main__":
    import subprocess

    # Locust 실행
    subprocess.run(
        [
            "locust",
            "-f",
            __file__,
            "--host=http://localhost:8000",
        ]
    )
