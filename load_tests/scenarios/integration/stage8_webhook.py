"""
Stage 8: Webhook Reliability Test

목적: PG Webhook 비정상 상황 처리 검증
- Webhook 중복 도착 (3회 동일 요청)
- 순서 역전 (실패 → 성공 순서로 도착)
- 지연 도착 (결제 후 30초 뒤 Webhook)
- Worker 재시작 후 replay

Note: 이 테스트는 실제 Webhook 엔드포인트 호출을 시뮬레이션합니다.
      실제 PG Webhook을 받는 것이 아니라, Webhook 핸들러를 직접 테스트합니다.

실행:
    locust -f load_tests/scenarios/stage8_webhook.py --host=http://localhost:8000 --users=30 --spawn-rate=10 --run-time=3m --headless
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import time
import random
import json
import hashlib
import hmac
from locust import HttpUser, task, between, tag, events

from dotenv import load_dotenv

load_dotenv()

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector


STAGE_NAME = "[Stage8]"

# Webhook Secret (환경 변수에서 로드)
TOSS_WEBHOOK_SECRET = os.environ.get("TOSS_WEBHOOK_SECRET", "test_webhook_secret")

# Webhook 테스트 통계
_webhook_stats = {
    "webhooks_sent": 0,
    "duplicate_handled": 0,
    "duplicate_failed": 0,
    "order_reversal_tested": 0,
    "order_reversal_verified": 0,  # DB 상태 검증 성공
    "order_reversal_failed": 0,  # DB 상태 검증 실패
    "delayed_webhook_tested": 0,
    "delayed_webhook_verified": 0,  # DB 상태 검증 성공
    "delayed_webhook_failed": 0,  # DB 상태 검증 실패
    "response_validation_errors": 0,  # 응답 코드 검증 실패
    # L1: Worker Restart Replay 테스트
    "replay_tested": 0,
    "replay_idempotent_success": 0,  # 멱등성 유지 성공
    "replay_idempotent_failed": 0,  # 멱등성 위반 (중복 처리됨)
    "replay_state_verified": 0,  # DB 상태 검증 성공
    "replay_state_failed": 0,  # DB 상태 검증 실패
}

# Webhook 엔드포인트 (프로젝트에 맞게 수정 필요)
WEBHOOK_ENDPOINT = "/api/webhooks/toss/"


def generate_webhook_signature(payload: dict) -> str:
    """Toss Webhook 서명 생성"""
    message = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(TOSS_WEBHOOK_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature


class WebhookUser(HttpUser):
    """
    Webhook Reliability Test 사용자

    PG Webhook 처리 신뢰성 테스트
    """

    wait_time = between(1, 2)

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

    def _create_completed_payment(self):
        """완료된 결제 생성 (Webhook 테스트용)"""
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return None

        # 장바구니 준비
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(random.choice(product_ids), 1)

        if not self.cart_helper.has_items():
            return None

        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return None

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return None

        # 결제 요청 (Payment 객체 생성) - 필수 단계!
        status_code, payment_request_data = self.payment_helper.request_payment(order_id)
        if status_code not in [200, 201]:
            return None

        # 결제 요청 응답에서 금액 확인 (포인트 차감 후 금액)
        payment_amount = payment_request_data.get("amount", final_amount)

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("webhook")

        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(payment_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/",
        )

        if response.status_code not in [200, 201, 202]:
            return None

        payment_data = response.json()

        return {
            "payment_key": payment_key,
            "order_id": order_id,
            "amount": final_amount,
            "payment_id": payment_data.get("payment_id", payment_data.get("id")),
        }

    def _build_webhook_payload(self, payment_info, event_type="PAYMENT.DONE"):
        """Toss Webhook 페이로드 생성 (올바른 형식)"""
        # Toss 웹훅은 eventType과 data 필드를 사용
        return {
            "eventType": event_type,
            "data": {
                "paymentKey": payment_info["payment_key"],
                "orderId": str(payment_info["order_id"]),
                "status": "DONE" if event_type == "PAYMENT.DONE" else "ABORTED",
                "amount": int(payment_info["amount"]),
                "method": "카드",
                "requestedAt": "2025-12-07T12:00:00+09:00",
                "approvedAt": "2025-12-07T12:00:01+09:00",
            },
        }

    def _send_webhook(self, payload, request_name):
        """Webhook 전송 (서명 포함)"""
        signature = generate_webhook_signature(payload)
        headers = {"X-Toss-Webhook-Signature": signature}

        return self.client.post(
            WEBHOOK_ENDPOINT,
            json=payload,
            headers=headers,
            name=request_name,
            catch_response=True,
        )

    def _verify_payment_status(self, payment_id, expected_status):
        """
        결제 상태 DB 검증 (API를 통해)

        Args:
            payment_id: 결제 ID
            expected_status: 기대하는 결제 상태 ("done", "failed", "canceled" 등)

        Returns:
            bool: 상태가 기대값과 일치하면 True
        """
        try:
            response = self.client.get(
                f"/api/payments/{payment_id}/status/",
                name=f"{STAGE_NAME} GET /api/payments/status/ [VERIFY]",
            )
            if response.status_code == 200:
                data = response.json()
                actual_status = data.get("status", "").lower()
                # "done", "paid", "completed" 등 성공 상태 처리
                if expected_status == "done":
                    return actual_status in ["done", "paid", "completed", "success"]
                return actual_status == expected_status.lower()
            return False
        except Exception:
            return False

    def _validate_webhook_response(self, response, scenario, expected_codes):
        """
        Webhook 응답 코드 검증

        Args:
            response: HTTP 응답
            scenario: 시나리오 이름 (로깅용)
            expected_codes: 기대하는 HTTP 상태 코드 리스트

        Returns:
            bool: 응답이 기대값과 일치하면 True
        """
        global _webhook_stats

        if response.status_code in expected_codes:
            return True
        else:
            _webhook_stats["response_validation_errors"] += 1
            return False

    @task(3)
    @tag("webhook", "duplicate")
    def test_duplicate_webhook(self):
        """
        Webhook 중복 도착 테스트

        같은 Webhook을 3번 전송해도 처리가 한 번만 되어야 함

        기대 응답:
        - 첫 번째: 200 (정상 처리)
        - 두 번째, 세 번째: 200 (멱등성) 또는 409 (중복 거부)
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        # Webhook 페이로드 (올바른 이벤트 타입 사용)
        payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")

        # 기대 응답 코드 (업계 표준)
        # - 200: 정상 처리 (멱등성 보장 시)
        # - 409: 중복 거부
        # - 400: 이미 처리됨
        expected_codes = [200, 201, 400, 409]

        # 동일 Webhook 3회 전송
        success_count = 0
        for i in range(3):
            _webhook_stats["webhooks_sent"] += 1

            with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [DUP-{i+1}]") as response:
                is_valid = self._validate_webhook_response(response, f"duplicate-{i+1}", expected_codes)

                if is_valid:
                    response.success()
                    success_count += 1
                else:
                    response.failure(f"Webhook failed: {response.status_code} (expected: {expected_codes})")

            time.sleep(0.1)

        # 모든 요청이 예상대로 처리되어야 함
        if success_count == 3:
            _webhook_stats["duplicate_handled"] += 1
        else:
            _webhook_stats["duplicate_failed"] += 1

    @task(2)
    @tag("webhook", "order_reversal")
    def test_webhook_order_reversal(self):
        """
        Webhook 순서 역전 테스트

        실패 → 성공 순서로 Webhook이 도착하는 경우
        (네트워크 지연으로 인해 발생 가능)

        핵심 검증:
        - 최종 DB 상태가 "성공"이어야 함 (시간순 기준 성공이 먼저 발생했으므로)

        기대 응답:
        - FAIL Webhook: 200 또는 409 (이미 성공 처리됨)
        - SUCCESS Webhook: 200 또는 409
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        _webhook_stats["order_reversal_tested"] += 1
        payment_id = payment_info.get("payment_id")

        # 1. 먼저 실패 Webhook 전송 (실제로는 나중에 온 것)
        fail_payload = self._build_webhook_payload(payment_info, "PAYMENT.FAILED")
        expected_fail_codes = [200, 201, 400, 409]  # 이미 성공 처리된 경우 거부 가능

        with self._send_webhook(fail_payload, f"{STAGE_NAME} POST Webhook [FAIL-FIRST]") as response:
            _webhook_stats["webhooks_sent"] += 1
            is_valid = self._validate_webhook_response(response, "reversal-fail", expected_fail_codes)
            if is_valid:
                response.success()
            else:
                response.failure(f"Unexpected response: {response.status_code}")

        time.sleep(0.2)

        # 2. 그 다음 성공 Webhook 전송 (실제로는 먼저 온 것)
        success_payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        expected_success_codes = [200, 201, 400, 409]

        with self._send_webhook(success_payload, f"{STAGE_NAME} POST Webhook [SUCCESS-SECOND]") as response:
            _webhook_stats["webhooks_sent"] += 1
            is_valid = self._validate_webhook_response(response, "reversal-success", expected_success_codes)
            if is_valid:
                response.success()
            else:
                response.failure(f"Order reversal issue: {response.status_code}")

        # 3. DB 상태 검증 (핵심!) - 최종 상태가 "성공"이어야 함
        time.sleep(0.3)  # 비동기 처리 대기
        if payment_id:
            is_correct_state = self._verify_payment_status(payment_id, "done")
            if is_correct_state:
                _webhook_stats["order_reversal_verified"] += 1
            else:
                _webhook_stats["order_reversal_failed"] += 1

    @task(1)
    @tag("webhook", "delayed")
    def test_delayed_webhook(self):
        """
        지연 Webhook 테스트

        결제 완료 후 상당한 시간이 지난 후 Webhook 도착

        핵심 검증:
        - 이미 성공 처리된 결제에 대해 중복 Webhook이 와도 문제없이 처리
        - DB 상태는 여전히 "성공" 유지

        기대 응답:
        - 200: 정상 (멱등성)
        - 409: 이미 처리됨
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 결제 완료
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        _webhook_stats["delayed_webhook_tested"] += 1
        payment_id = payment_info.get("payment_id")

        # 지연 시뮬레이션 (실제로는 수 초만 대기)
        time.sleep(random.uniform(2, 5))

        # 지연된 Webhook 전송
        payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        expected_codes = [200, 201, 400, 409]  # 이미 처리된 경우도 정상

        with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [DELAYED]") as response:
            _webhook_stats["webhooks_sent"] += 1
            is_valid = self._validate_webhook_response(response, "delayed", expected_codes)
            if is_valid:
                response.success()
            else:
                response.failure(f"Delayed webhook issue: {response.status_code}")

        # DB 상태 검증 - 여전히 "성공" 상태여야 함
        if payment_id:
            is_correct_state = self._verify_payment_status(payment_id, "done")
            if is_correct_state:
                _webhook_stats["delayed_webhook_verified"] += 1
            else:
                _webhook_stats["delayed_webhook_failed"] += 1

    @task(1)
    @tag("webhook", "replay", "L1")
    def test_worker_restart_replay(self):
        """
        L1: Worker Restart 후 Replay 테스트

        시나리오:
        1. 결제 승인 완료 (Payment 객체 상태: done)
        2. Worker 재시작 시뮬레이션 (처리 지연)
        3. PG가 응답을 못 받았다고 판단하여 Webhook 재전송
        4. 시스템이 멱등성을 유지하는지 검증

        핵심 검증:
        - 이미 처리된 결제에 대해 Webhook이 다시 와도 중복 처리 없음
        - DB 상태는 "성공" 유지
        - 포인트/재고 등 부작용 없음 (멱등성)

        기대 응답:
        - 200: 멱등하게 처리 (이미 처리됨을 인지하고 성공 응답)
        - 409: 이미 처리됨 명시적 거부
        """
        global _webhook_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 1. 결제 완료 (정상 플로우)
        payment_info = self._create_completed_payment()
        if not payment_info:
            return

        _webhook_stats["replay_tested"] += 1
        payment_id = payment_info.get("payment_id")

        # 2. 결제 완료 직후 상태 확인 (기준점)
        initial_status_check = self._verify_payment_status(payment_id, "done")
        if not initial_status_check:
            # 이미 결제가 제대로 처리되지 않았으면 테스트 의미 없음
            return

        # 3. Worker 재시작 시뮬레이션 (PG가 응답 못 받았다고 판단하는 시간)
        # 실제로는 PG가 3-5초 후 재전송함
        time.sleep(random.uniform(1, 3))

        # 4. PG가 Webhook 재전송 (Replay)
        # 같은 payment_key, order_id로 다시 전송
        replay_payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        expected_codes = [200, 201, 409]  # 200: 멱등 처리, 409: 이미 처리됨

        with self._send_webhook(replay_payload, f"{STAGE_NAME} POST Webhook [REPLAY]") as response:
            _webhook_stats["webhooks_sent"] += 1

            if response.status_code in expected_codes:
                response.success()
                _webhook_stats["replay_idempotent_success"] += 1
            else:
                response.failure(f"Replay failed: {response.status_code}")
                _webhook_stats["replay_idempotent_failed"] += 1

        # 5. 두 번째 Replay (더 공격적인 시나리오)
        time.sleep(0.5)

        with self._send_webhook(replay_payload, f"{STAGE_NAME} POST Webhook [REPLAY-2]") as response:
            _webhook_stats["webhooks_sent"] += 1

            if response.status_code in expected_codes:
                response.success()
            else:
                response.failure(f"Replay-2 failed: {response.status_code}")

        # 6. DB 상태 최종 검증 - 여전히 "done" 상태여야 함
        time.sleep(0.3)
        if payment_id:
            is_correct_state = self._verify_payment_status(payment_id, "done")
            if is_correct_state:
                _webhook_stats["replay_state_verified"] += 1
            else:
                _webhook_stats["replay_state_failed"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Webhook 테스트 결과"""
    global _webhook_stats

    print("\n" + "=" * 70)
    print("[BELL] STAGE 8: WEBHOOK RELIABILITY TEST RESULTS")
    print("=" * 70)

    # 기본 통계
    print("\n[STATS] Webhook Transmission Stats:")
    print(f"   Total Webhooks Sent: {_webhook_stats['webhooks_sent']}")

    # Duplicate 테스트 결과
    print("\n[DUP] Duplicate Webhook Test:")
    print(f"   Handled Correctly: {_webhook_stats['duplicate_handled']}")
    print(f"   Failed: {_webhook_stats['duplicate_failed']}")

    # Order Reversal 테스트 결과
    print("\n[REV] Order Reversal Test:")
    print(f"   Tested: {_webhook_stats['order_reversal_tested']}")
    print(f"   DB State Verified: {_webhook_stats['order_reversal_verified']}")
    print(f"   DB State Failed: {_webhook_stats['order_reversal_failed']}")

    # Delayed Webhook 테스트 결과
    print("\n[DELAY] Delayed Webhook Test:")
    print(f"   Tested: {_webhook_stats['delayed_webhook_tested']}")
    print(f"   DB State Verified: {_webhook_stats['delayed_webhook_verified']}")
    print(f"   DB State Failed: {_webhook_stats['delayed_webhook_failed']}")

    # L1: Worker Restart Replay 테스트 결과
    print("\n[L1-REPLAY] Worker Restart Replay Test:")
    print(f"   Tested: {_webhook_stats['replay_tested']}")
    print(f"   Idempotent Success: {_webhook_stats['replay_idempotent_success']}")
    print(f"   Idempotent Failed: {_webhook_stats['replay_idempotent_failed']}")
    print(f"   State Verified: {_webhook_stats['replay_state_verified']}")
    print(f"   State Failed: {_webhook_stats['replay_state_failed']}")

    # 응답 검증 에러
    print("\n[VALID] Response Validation:")
    print(f"   Validation Errors: {_webhook_stats['response_validation_errors']}")

    # Metrics Summary
    collector = get_metrics_collector()
    summary = collector.get_summary()

    print("\n" + "-" * 70)
    print("[METRICS] Overall Metrics:")
    print(f"   Total Requests: {summary['total_requests']}")
    print(f"   Error Rate: {summary['overall_error_rate']}%")
    print("-" * 70)

    # 최종 판정 (엄격한 기준)
    # 모든 조건이 충족되어야 PASS
    all_tests_executed = (
        (_webhook_stats["duplicate_handled"] > 0 or _webhook_stats["duplicate_failed"] > 0)
        and (_webhook_stats["order_reversal_tested"] > 0)
        and (_webhook_stats["delayed_webhook_tested"] > 0)
        and (_webhook_stats["replay_tested"] > 0)  # L1 테스트 실행 확인
    )

    no_duplicate_failures = _webhook_stats["duplicate_failed"] == 0
    no_reversal_failures = _webhook_stats["order_reversal_failed"] == 0
    no_delayed_failures = _webhook_stats["delayed_webhook_failed"] == 0
    no_validation_errors = _webhook_stats["response_validation_errors"] == 0
    no_replay_failures = (
        _webhook_stats["replay_idempotent_failed"] == 0 and _webhook_stats["replay_state_failed"] == 0
    )  # L1 조건
    low_error_rate = float(summary["overall_error_rate"]) < 5.0  # 5% 미만

    # 엄격한 PASS 조건
    is_passed = (
        all_tests_executed
        and no_duplicate_failures
        and no_reversal_failures
        and no_delayed_failures
        and no_validation_errors
        and no_replay_failures  # L1 조건 추가
        and low_error_rate
    )

    print("\n" + "=" * 70)
    if is_passed:
        print("[PASS] WEBHOOK RELIABILITY TEST PASSED")
        print("   [OK] All duplicate webhooks handled correctly")
        print("   [OK] Order reversal scenarios verified")
        print("   [OK] Delayed webhooks processed properly")
        print("   [OK] L1 Worker restart replay handled (idempotent)")
        print("   [OK] DB state consistency maintained")
    else:
        print("[FAIL] WEBHOOK RELIABILITY TEST FAILED")
        if not all_tests_executed:
            print("   [X] Not all test scenarios were executed")
        if not no_duplicate_failures:
            print(f"   [X] {_webhook_stats['duplicate_failed']} duplicate handling failures")
        if not no_reversal_failures:
            print(f"   [X] {_webhook_stats['order_reversal_failed']} order reversal DB state failures")
        if not no_delayed_failures:
            print(f"   [X] {_webhook_stats['delayed_webhook_failed']} delayed webhook DB state failures")
        if not no_replay_failures:
            print(
                f"   [X] L1 Replay: {_webhook_stats['replay_idempotent_failed']} idempotent failures, {_webhook_stats['replay_state_failed']} state failures"
            )
        if not no_validation_errors:
            print(f"   [X] {_webhook_stats['response_validation_errors']} response validation errors")
        if not low_error_rate:
            print(f"   [X] Error rate {summary['overall_error_rate']}% exceeds threshold (5%)")
        print("\n   [TIP] Review webhook idempotency and state management logic")
    print("=" * 70)
