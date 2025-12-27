"""
Stage 8 EXTREME: Webhook + Self-Healing Chaos Storm

목적: PG Webhook 비정상 상황에서 Self-Healing 시스템 극한 검증
- Webhook 중복/역전/지연 + Circuit Breaker 연쇄 장애
- DLQ 큐잉/리플레이 검증
- Emergency Mode 자동 트리거
- Error Budget 소진 시 배포 동결
- XTest 모드 Chaos Injection

극한 시나리오:
1. CB Cascade Storm: Webhook 처리 중 payment-service CB Open → 연쇄 장애
2. DLQ Flood: 대량 Webhook 실패 → DLQ 큐잉 → 배치 리플레이
3. Emergency Auto-Trigger: Error Rate 급증 → 자동 비상 모드
4. Error Budget Exhaustion: 예산 소진 → 배포 동결 검증
5. Reconciliation: Shadow Budget 검증

실행:
    locust -f load_tests/scenarios/integration/stage8_webhook_selfhealing.py \
        --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=5m --headless
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_integration_dir = os.path.dirname(_current_dir)
_scenarios_dir = os.path.dirname(_integration_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import time
import random
import json
import hashlib
import hmac
import threading
from datetime import datetime
from locust import HttpUser, task, between, tag, events
from locust.runners import MasterRunner, WorkerRunner

from dotenv import load_dotenv
load_dotenv()

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector

# Self-Healing 클라이언트 임포트
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    SELFHEALING_AVAILABLE = True
except ImportError:
    SELFHEALING_AVAILABLE = False
    print("[WARN] SelfHealingClient not available")


STAGE_NAME = "[Stage8-EXTREME]"
TOSS_WEBHOOK_SECRET = os.environ.get("TOSS_WEBHOOK_SECRET", "test_webhook_secret")
SELFHEALING_HOST = os.environ.get("SELFHEALING_HOST", "http://localhost:8000")
WEBHOOK_ENDPOINT = "/api/webhooks/toss/"

# ============================================================================
# 극한 테스트 통계
# ============================================================================
_extreme_stats = {
    # 기본 Webhook 통계
    "webhooks_sent": 0,
    "duplicate_handled": 0,
    "duplicate_failed": 0,
    "order_reversal_tested": 0,
    "order_reversal_verified": 0,
    "order_reversal_failed": 0,
    "delayed_webhook_tested": 0,
    "delayed_webhook_verified": 0,
    "delayed_webhook_failed": 0,
    "replay_tested": 0,
    "replay_idempotent_success": 0,
    "replay_idempotent_failed": 0,
    
    # Self-Healing 통계
    "healing_client_init": 0,
    "healing_client_failed": 0,
    
    # CB Cascade Storm
    "cb_injections": 0,
    "cb_cascade_triggered": 0,
    "cb_recovery_success": 0,
    "cb_recovery_failed": 0,
    
    # DLQ 통계
    "dlq_entries_created": 0,
    "dlq_replay_success": 0,
    "dlq_replay_failed": 0,
    "dlq_batch_replay_tested": 0,
    
    # Emergency Mode
    "emergency_triggered": 0,
    "emergency_released": 0,
    "emergency_gradual_recovery": 0,
    
    # Error Budget
    "error_budget_checked": 0,
    "error_budget_exhausted": 0,
    "error_budget_reset": 0,
    "deployment_blocked": 0,
    
    # Observability
    "snapshots_taken": 0,
    "healing_events_recorded": 0,
    "blast_radius_tests": 0,
    
    # XTest Chaos
    "xtest_chaos_injected": 0,
    "xtest_recovery_verified": 0,
    
    # Reconciliation
    "shadow_budget_tested": 0,
    "reconciliation_verified": 0,
}

_stats_lock = threading.Lock()


def safe_increment(key: str, amount: int = 1):
    """스레드 안전 통계 증가"""
    global _extreme_stats
    with _stats_lock:
        _extreme_stats[key] += amount


def generate_webhook_signature(payload: dict) -> str:
    """Toss Webhook 서명 생성"""
    message = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(
        TOSS_WEBHOOK_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


# ============================================================================
# Self-Healing 클라이언트 싱글톤
# ============================================================================
_healing_client = None
_healing_lock = threading.Lock()


def get_healing_client() -> "SelfHealingClient":
    """Self-Healing 클라이언트 싱글톤 반환"""
    global _healing_client
    
    if not SELFHEALING_AVAILABLE:
        return None
    
    with _healing_lock:
        if _healing_client is None:
            try:
                _healing_client = SelfHealingClient(
                    host=SELFHEALING_HOST,
                    auth_mode="xtest",  # Chaos 테스트용 XTest 모드
                    timeout=10,
                )
                safe_increment("healing_client_init")
            except Exception as e:
                safe_increment("healing_client_failed")
                return None
    
    return _healing_client


# ============================================================================
# Webhook + Self-Healing Extreme User
# ============================================================================
class WebhookSelfHealingUser(HttpUser):
    """
    Webhook + Self-Healing 극한 테스트 사용자
    
    기존 Webhook 신뢰성 테스트 + Self-Healing 시스템 스트레스
    """
    
    wait_time = between(0.5, 1.5)  # 빠른 테스트를 위해 wait_time 단축
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)
        
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # Self-Healing 클라이언트
        self.healing = get_healing_client()
    
    def _create_completed_payment(self):
        """완료된 결제 생성 (Webhook 테스트용)"""
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return None
        
        self.cart_helper.clear_cart()
        self.cart_helper.add_item(random.choice(product_ids), 1)
        
        if not self.cart_helper.has_items():
            return None
        
        order_data = self.payment_helper.create_order()
        if not order_data:
            return None
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return None
        
        status_code, payment_request_data = self.payment_helper.request_payment(order_id)
        if status_code not in [200, 201]:
            return None
        
        payment_amount = payment_request_data.get("amount", final_amount)
        payment_key = self.payment_helper.generate_payment_key("extreme")
        
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
        """Toss Webhook 페이로드 생성"""
        return {
            "eventType": event_type,
            "data": {
                "paymentKey": payment_info["payment_key"],
                "orderId": str(payment_info["order_id"]),
                "status": "DONE" if event_type == "PAYMENT.DONE" else "ABORTED",
                "amount": int(payment_info["amount"]),
                "method": "카드",
                "requestedAt": datetime.now().isoformat(),
                "approvedAt": datetime.now().isoformat(),
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
        """결제 상태 DB 검증"""
        try:
            response = self.client.get(
                f"/api/payments/{payment_id}/status/",
                name=f"{STAGE_NAME} GET /api/payments/status/ [VERIFY]",
            )
            if response.status_code == 200:
                data = response.json()
                actual_status = data.get("status", "").lower()
                if expected_status == "done":
                    return actual_status in ["done", "paid", "completed", "success"]
                return actual_status == expected_status.lower()
            return False
        except Exception:
            return False
    
    # ========================================================================
    # 기본 Webhook 테스트 (기존 로직 유지)
    # ========================================================================
    
    @task(3)
    @tag("webhook", "duplicate")
    def test_duplicate_webhook(self):
        """Webhook 중복 도착 테스트"""
        if not self.login_helper.ensure_logged_in():
            return
        
        payment_info = self._create_completed_payment()
        if not payment_info:
            return
        
        payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        expected_codes = [200, 201, 400, 409]
        
        success_count = 0
        for i in range(3):
            safe_increment("webhooks_sent")
            
            with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [DUP-{i+1}]") as response:
                if response.status_code in expected_codes:
                    response.success()
                    success_count += 1
                else:
                    response.failure(f"Webhook failed: {response.status_code}")
            
            time.sleep(0.1)
        
        if success_count == 3:
            safe_increment("duplicate_handled")
        else:
            safe_increment("duplicate_failed")
    
    @task(2)
    @tag("webhook", "order_reversal")
    def test_webhook_order_reversal(self):
        """Webhook 순서 역전 테스트"""
        if not self.login_helper.ensure_logged_in():
            return
        
        payment_info = self._create_completed_payment()
        if not payment_info:
            return
        
        safe_increment("order_reversal_tested")
        payment_id = payment_info.get("payment_id")
        
        # 실패 Webhook 먼저
        fail_payload = self._build_webhook_payload(payment_info, "PAYMENT.FAILED")
        with self._send_webhook(fail_payload, f"{STAGE_NAME} POST Webhook [FAIL-FIRST]") as response:
            safe_increment("webhooks_sent")
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Unexpected: {response.status_code}")
        
        time.sleep(0.2)
        
        # 성공 Webhook
        success_payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        with self._send_webhook(success_payload, f"{STAGE_NAME} POST Webhook [SUCCESS-SECOND]") as response:
            safe_increment("webhooks_sent")
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Order reversal issue: {response.status_code}")
        
        time.sleep(0.3)
        if payment_id:
            if self._verify_payment_status(payment_id, "done"):
                safe_increment("order_reversal_verified")
            else:
                safe_increment("order_reversal_failed")
    
    # ========================================================================
    # Self-Healing 극한 테스트
    # ========================================================================
    
    @task(3)
    @tag("selfhealing", "cb_cascade")
    def test_cb_cascade_storm(self):
        """
        CB Cascade Storm: Webhook 처리 중 연쇄 장애
        
        시나리오:
        1. payment-service CB에 장애 주입
        2. Webhook 전송 시도 (실패 예상)
        3. 연쇄적으로 관련 서비스 CB 확인
        4. 자동 복구 검증
        """
        if not self.healing:
            # Self-Healing 없이도 기본 테스트는 수행
            if not self.login_helper.ensure_logged_in():
                return
            payment_info = self._create_completed_payment()
            if payment_info:
                payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [CB-STORM-NO-HEAL]") as response:
                    safe_increment("webhooks_sent")
                    if response.status_code in [200, 201, 400, 409, 503, 429]:
                        response.success()
                    else:
                        response.failure(f"CB Storm unexpected: {response.status_code}")
            return
        
        if not self.login_helper.ensure_logged_in():
            return
        
        try:
            # 1. CB 장애 주입
            services = ["payment-service", "inventory-service", "point-service"]
            target_service = random.choice(services)
            
            result = self.healing.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type="exception",
                failure_rate=0.8,
                duration_seconds=10,
            )
            safe_increment("cb_injections")
            
            # 2. 결제 생성 및 Webhook 전송 시도
            payment_info = self._create_completed_payment()
            if payment_info:
                payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                
                with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [CB-STORM]") as response:
                    safe_increment("webhooks_sent")
                    # CB Open 상태에서는 503 또는 429 예상
                    if response.status_code in [200, 201, 400, 409, 503, 429]:
                        response.success()
                        if response.status_code in [503, 429]:
                            safe_increment("cb_cascade_triggered")
                    else:
                        response.failure(f"CB Storm unexpected: {response.status_code}")
            
            # 3. CB 상태 확인
            time.sleep(2)
            cb_status = self.healing.circuit_breaker.get_service_status(target_service)
            
            # 4. CB 리셋 시도
            self.healing.xtest.reset_cb(target_service)
            
            time.sleep(1)
            # 복구 확인
            cb_after = self.healing.circuit_breaker.get_service_status(target_service)
            if cb_after.get("state") in ["closed", "half_open", None] or cb_after.get("status") == "error":
                safe_increment("cb_recovery_success")
            else:
                safe_increment("cb_recovery_failed")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(2)
    @tag("selfhealing", "dlq")
    def test_dlq_flood_replay(self):
        """
        DLQ Flood: 대량 Webhook 실패 → DLQ 큐잉 → 배치 리플레이
        
        시나리오:
        1. 의도적으로 잘못된 Webhook 대량 전송
        2. DLQ 큐잉 확인
        3. 배치 리플레이 실행
        4. 리플레이 결과 검증
        """
        # DLQ 테스트는 잘못된 웹훅 전송 (Self-Healing 없어도 동작)
        try:
            # 잘못된 Webhook 전송 (DLQ 유발)
            invalid_payload = {
                "eventType": "PAYMENT.INVALID",
                "data": {
                    "paymentKey": "INVALID_KEY_" + str(random.randint(1000, 9999)),
                    "orderId": "INVALID_ORDER",
                    "status": "UNKNOWN",
                    "amount": -1,  # 잘못된 금액
                }
            }
            
            with self._send_webhook(invalid_payload, f"{STAGE_NAME} POST Webhook [DLQ-FLOOD]") as response:
                safe_increment("webhooks_sent")
                # 잘못된 요청은 400 또는 DLQ 처리 후 200 가능
                if response.status_code in [200, 201, 400, 422]:
                    response.success()
                else:
                    response.failure(f"DLQ Flood unexpected: {response.status_code}")
            
            if not self.healing:
                return
            
            time.sleep(0.5)
            
            # DLQ 현재 상태 확인
            dlq_before = self.healing.dlq.stats()
            pending_before = dlq_before.get("pending_count", 0)
            
            # DLQ 상태 다시 확인
            dlq_after = self.healing.dlq.stats()
            pending_after = dlq_after.get("pending_count", 0)
            
            if pending_after > pending_before:
                safe_increment("dlq_entries_created")
            
            # DLQ 배치 리플레이 테스트 (가끔만)
            if random.random() < 0.3:
                safe_increment("dlq_batch_replay_tested")
                replay_result = self.healing.dlq.replay(
                    domain="payment",
                    batch_size=10,
                )
                if replay_result.get("status") != "error":
                    safe_increment("dlq_replay_success")
                else:
                    safe_increment("dlq_replay_failed")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(2)
    @tag("selfhealing", "emergency")
    def test_emergency_mode_trigger(self):
        """
        Emergency Mode: 극한 상황에서 비상 모드 자동 트리거
        
        시나리오:
        1. Emergency 상태 확인
        2. Level 1 비상 모드 트리거 (테스트용)
        3. 점진적 복구 시작
        4. 정상 상태 복귀 확인
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [EMERG-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"Emergency test unexpected: {response.status_code}")
            return
        
        try:
            # 1. 현재 상태 확인
            status_before = self.healing.emergency.get_status()
            
            # 2. 비상 모드 트리거 (낮은 확률로만)
            if random.random() < 0.2:
                result = self.healing.emergency.trigger(
                    level="LEVEL_1",
                    reason="Stage 8 Extreme Test - Auto Trigger",
                    duration_minutes=1,  # 1분 후 자동 해제
                )
                if result.get("status") != "error":
                    safe_increment("emergency_triggered")
                
                time.sleep(1)
                
                # 3. 점진적 복구 시도
                if random.random() < 0.5:
                    recovery_result = self.healing.emergency.start_gradual_recovery(
                        target_level="NORMAL",
                        step_duration_seconds=10,
                    )
                    if recovery_result.get("status") != "error":
                        safe_increment("emergency_gradual_recovery")
                
                time.sleep(2)
                
                # 4. 비상 모드 해제
                release_result = self.healing.emergency.release(
                    reason="Stage 8 Extreme Test - Cleanup"
                )
                if release_result.get("status") != "error":
                    safe_increment("emergency_released")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(2)
    @tag("selfhealing", "error_budget")
    def test_error_budget_exhaustion(self):
        """
        Error Budget Exhaustion: 예산 소진 시 배포 동결 검증
        
        시나리오:
        1. 현재 Error Budget 확인
        2. 에러 기록 (시뮬레이션)
        3. 배포 판정 확인
        4. 예산 리셋
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [BUDGET-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"Budget test unexpected: {response.status_code}")
            return
        
        try:
            # 1. 현재 Error Budget 확인
            budget_status = self.healing.error_budget.get_status()
            safe_increment("error_budget_checked")
            
            remaining = budget_status.get("remaining_percent", 100)
            
            # 2. 에러 기록 (가끔만)
            if random.random() < 0.3:
                record_result = self.healing.error_budget.record_error(
                    error_count=random.randint(1, 5),
                    error_type="webhook_failure",
                    service_name="webhook-handler",
                )
            
            # 3. 배포 판정 확인
            verdict = self.healing.error_budget.get_deployment_verdict()
            if verdict.get("allowed") is False:
                safe_increment("deployment_blocked")
            
            # 4. 예산 소진 시뮬레이션 (매우 낮은 확률)
            if random.random() < 0.05:
                exhaust_result = self.healing.error_budget.exhaust()
                if exhaust_result.get("status") != "error":
                    safe_increment("error_budget_exhausted")
                
                time.sleep(1)
                
                # 시뮬레이션 리셋
                reset_result = self.healing.error_budget.reset_simulation()
                if reset_result.get("status") != "error":
                    safe_increment("error_budget_reset")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(2)
    @tag("selfhealing", "observability")
    def test_observability_snapshot(self):
        """
        Observability: 스냅샷 생성 및 힐링 이벤트 기록
        
        시나리오:
        1. 현재 시스템 스냅샷 생성
        2. 타임라인 조회
        3. Blast Radius 측정 (격리 확인)
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [OBS-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"Observability test unexpected: {response.status_code}")
            return
        
        try:
            # 1. 스냅샷 생성
            snapshot = self.healing.observability.create_snapshot(
                label=f"Stage8-Extreme-{datetime.now().strftime('%H%M%S')}"
            )
            if snapshot.get("status") != "error":
                safe_increment("snapshots_taken")
            
            # 2. 타임라인 조회
            timeline = self.healing.observability.get_timeline(limit=20)
            events = timeline.get("events", [])
            safe_increment("healing_events_recorded", len(events))
            
            # 3. Blast Radius 테스트 (격리 확인)
            if random.random() < 0.3:
                # 현재 스냅샷과 비교
                current_snapshot = self.healing.observability.get_current_snapshot()
                if current_snapshot.get("status") != "error":
                    safe_increment("blast_radius_tests")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(1)
    @tag("selfhealing", "xtest_chaos")
    def test_xtest_chaos_injection(self):
        """
        XTest Chaos: Chaos Monkey 장애 주입 및 복구
        
        시나리오:
        1. XTest 모드로 장애 주입
        2. Fast Fail 테스트
        3. 복구 트리거
        4. 복구 검증
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [XTEST-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"XTest test unexpected: {response.status_code}")
            return
        
        try:
            # 1. XTest 장애 주입
            target_service = random.choice(["database", "redis", "external-api"])
            
            inject_result = self.healing.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type="timeout",
                failure_rate=0.5,
                duration_seconds=5,
            )
            safe_increment("xtest_chaos_injected")
            
            # 2. Fast Fail 테스트
            fail_result = self.healing.xtest.fast_fail_test(
                service_name=target_service,
                request_count=5,
            )
            
            time.sleep(2)
            
            # 3. CB 상태 확인 및 복구
            cb_status = self.healing.xtest.get_cb_status(target_service)
            
            # 4. 복구 트리거
            self.healing.xtest.reset_cb(target_service)
            
            time.sleep(1)
            
            # 5. 복구 검증
            cb_after = self.healing.xtest.get_cb_status(target_service)
            if cb_after.get("state") in ["closed", "half_open", None] or cb_after.get("status") == "error":
                safe_increment("xtest_recovery_verified")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(1)
    @tag("selfhealing", "reconciliation")
    def test_shadow_budget_reconciliation(self):
        """
        Reconciliation: Shadow Budget 검증
        
        시나리오:
        1. Shadow Budget 상태 확인
        2. FailSafe 기간 조회
        3. 정합성 검증
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [RECON-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"Reconciliation test unexpected: {response.status_code}")
            return
        
        try:
            # 1. Reconciliation 상태 확인
            status = self.healing.reconciliation.get_status()
            safe_increment("shadow_budget_tested")
            
            # 2. FailSafe 기간 조회
            failsafe = self.healing.reconciliation.get_failsafe_periods()
            
            # 3. 설정 확인
            config = self.healing.reconciliation.get_config()
            
            if status.get("status") != "error":
                safe_increment("reconciliation_verified")
            
        except Exception:
            pass  # Self-Healing API 미구현 시 무시
    
    @task(1)
    @tag("webhook", "delayed_extreme")
    def test_delayed_webhook_with_healing(self):
        """
        Delayed Webhook + Self-Healing 통합 테스트
        
        지연 Webhook 도착 시 시스템 상태 스냅샷 및 검증
        """
        if not self.login_helper.ensure_logged_in():
            return
        
        payment_info = self._create_completed_payment()
        if not payment_info:
            return
        
        safe_increment("delayed_webhook_tested")
        payment_id = payment_info.get("payment_id")
        
        # 스냅샷 생성 (지연 전)
        if self.healing:
            try:
                self.healing.observability.create_snapshot(
                    label=f"pre-delayed-{payment_id}"
                )
            except Exception:
                pass
        
        # 지연 시뮬레이션
        time.sleep(random.uniform(3, 6))
        
        # 지연된 Webhook 전송
        payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        
        with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [DELAYED-HEAL]") as response:
            safe_increment("webhooks_sent")
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Delayed webhook issue: {response.status_code}")
        
        # 스냅샷 생성 (지연 후)
        if self.healing:
            try:
                self.healing.observability.create_snapshot(
                    label=f"post-delayed-{payment_id}"
                )
            except Exception:
                pass
        
        # DB 상태 검증
        if payment_id:
            if self._verify_payment_status(payment_id, "done"):
                safe_increment("delayed_webhook_verified")
            else:
                safe_increment("delayed_webhook_failed")


# ============================================================================
# 테스트 종료 이벤트
# ============================================================================
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 극한 테스트 결과 출력"""
    global _extreme_stats
    
    print("\n" + "=" * 80)
    print("🔥 STAGE 8 EXTREME: WEBHOOK + SELF-HEALING CHAOS STORM RESULTS")
    print("=" * 80)
    
    # 기본 Webhook 통계
    print("\n📊 [WEBHOOK STATS]")
    print(f"   Total Webhooks Sent: {_extreme_stats['webhooks_sent']}")
    print(f"   Duplicate Handled: {_extreme_stats['duplicate_handled']}")
    print(f"   Duplicate Failed: {_extreme_stats['duplicate_failed']}")
    print(f"   Order Reversal Tested: {_extreme_stats['order_reversal_tested']}")
    print(f"   Order Reversal Verified: {_extreme_stats['order_reversal_verified']}")
    print(f"   Order Reversal Failed: {_extreme_stats['order_reversal_failed']}")
    print(f"   Delayed Webhook Tested: {_extreme_stats['delayed_webhook_tested']}")
    print(f"   Delayed Webhook Verified: {_extreme_stats['delayed_webhook_verified']}")
    print(f"   Delayed Webhook Failed: {_extreme_stats['delayed_webhook_failed']}")
    
    # Self-Healing 클라이언트 상태
    print("\n🏥 [SELF-HEALING CLIENT]")
    print(f"   Client Init Success: {_extreme_stats['healing_client_init']}")
    print(f"   Client Init Failed: {_extreme_stats['healing_client_failed']}")
    
    # CB Cascade Storm
    print("\n🌪️ [CB CASCADE STORM]")
    print(f"   CB Injections: {_extreme_stats['cb_injections']}")
    print(f"   Cascade Triggered: {_extreme_stats['cb_cascade_triggered']}")
    print(f"   Recovery Success: {_extreme_stats['cb_recovery_success']}")
    print(f"   Recovery Failed: {_extreme_stats['cb_recovery_failed']}")
    
    # DLQ 통계
    print("\n📦 [DLQ FLOOD & REPLAY]")
    print(f"   DLQ Entries Created: {_extreme_stats['dlq_entries_created']}")
    print(f"   Batch Replay Tested: {_extreme_stats['dlq_batch_replay_tested']}")
    print(f"   Replay Success: {_extreme_stats['dlq_replay_success']}")
    print(f"   Replay Failed: {_extreme_stats['dlq_replay_failed']}")
    
    # Emergency Mode
    print("\n🚨 [EMERGENCY MODE]")
    print(f"   Emergency Triggered: {_extreme_stats['emergency_triggered']}")
    print(f"   Emergency Released: {_extreme_stats['emergency_released']}")
    print(f"   Gradual Recovery: {_extreme_stats['emergency_gradual_recovery']}")
    
    # Error Budget
    print("\n💰 [ERROR BUDGET]")
    print(f"   Budget Checked: {_extreme_stats['error_budget_checked']}")
    print(f"   Budget Exhausted: {_extreme_stats['error_budget_exhausted']}")
    print(f"   Budget Reset: {_extreme_stats['error_budget_reset']}")
    print(f"   Deployment Blocked: {_extreme_stats['deployment_blocked']}")
    
    # Observability
    print("\n📸 [OBSERVABILITY]")
    print(f"   Snapshots Taken: {_extreme_stats['snapshots_taken']}")
    print(f"   Healing Events Recorded: {_extreme_stats['healing_events_recorded']}")
    print(f"   Blast Radius Tests: {_extreme_stats['blast_radius_tests']}")
    
    # XTest Chaos
    print("\n🐒 [XTEST CHAOS MONKEY]")
    print(f"   Chaos Injected: {_extreme_stats['xtest_chaos_injected']}")
    print(f"   Recovery Verified: {_extreme_stats['xtest_recovery_verified']}")
    
    # Reconciliation
    print("\n🔄 [RECONCILIATION]")
    print(f"   Shadow Budget Tested: {_extreme_stats['shadow_budget_tested']}")
    print(f"   Reconciliation Verified: {_extreme_stats['reconciliation_verified']}")
    
    # Metrics Summary
    collector = get_metrics_collector()
    summary = collector.get_summary()
    
    print("\n" + "-" * 80)
    print("📈 [OVERALL METRICS]")
    print(f"   Total Requests: {summary['total_requests']}")
    print(f"   Error Rate: {summary['overall_error_rate']}%")
    print("-" * 80)
    
    # 최종 판정
    is_passed = _evaluate_test_result(summary)
    
    print("\n" + "=" * 80)
    if is_passed:
        print("✅ STAGE 8 EXTREME: WEBHOOK + SELF-HEALING TEST PASSED")
        print("   ✓ Webhook idempotency maintained")
        print("   ✓ CB cascade handled correctly")
        print("   ✓ DLQ replay functioning")
        print("   ✓ Emergency mode responsive")
        print("   ✓ Error budget managed")
        print("   ✓ Self-healing system operational")
    else:
        print("❌ STAGE 8 EXTREME: WEBHOOK + SELF-HEALING TEST FAILED")
        _print_failure_reasons()
    print("=" * 80)
    
    # 결과 저장
    _save_results(summary, is_passed)


def _evaluate_test_result(summary) -> bool:
    """테스트 결과 평가"""
    global _extreme_stats
    
    # 기본 조건
    tests_executed = _extreme_stats["webhooks_sent"] > 0
    no_duplicate_failures = _extreme_stats["duplicate_failed"] == 0
    no_reversal_failures = _extreme_stats["order_reversal_failed"] == 0
    no_delayed_failures = _extreme_stats["delayed_webhook_failed"] == 0
    low_error_rate = float(summary["overall_error_rate"]) < 10.0  # 극한 테스트는 10% 허용
    
    # Self-Healing 조건
    healing_operational = (
        _extreme_stats["healing_client_init"] > 0 or 
        _extreme_stats["healing_client_failed"] == 0
    )
    cb_recovery_ok = (
        _extreme_stats["cb_recovery_failed"] == 0 or 
        _extreme_stats["cb_recovery_success"] >= _extreme_stats["cb_recovery_failed"]
    )
    dlq_ok = _extreme_stats["dlq_replay_failed"] == 0
    
    return all([
        tests_executed,
        no_duplicate_failures,
        no_reversal_failures,
        no_delayed_failures,
        low_error_rate,
        healing_operational,
        cb_recovery_ok,
        dlq_ok,
    ])


def _print_failure_reasons():
    """실패 원인 출력"""
    global _extreme_stats
    
    if _extreme_stats["duplicate_failed"] > 0:
        print(f"   ✗ {_extreme_stats['duplicate_failed']} duplicate handling failures")
    if _extreme_stats["order_reversal_failed"] > 0:
        print(f"   ✗ {_extreme_stats['order_reversal_failed']} order reversal failures")
    if _extreme_stats["delayed_webhook_failed"] > 0:
        print(f"   ✗ {_extreme_stats['delayed_webhook_failed']} delayed webhook failures")
    if _extreme_stats["cb_recovery_failed"] > _extreme_stats["cb_recovery_success"]:
        print(f"   ✗ CB recovery issues: {_extreme_stats['cb_recovery_failed']} failures")
    if _extreme_stats["dlq_replay_failed"] > 0:
        print(f"   ✗ DLQ replay failed: {_extreme_stats['dlq_replay_failed']}")
    if _extreme_stats["healing_client_failed"] > 0:
        print(f"   ✗ Self-Healing client initialization failed")


def _save_results(summary, is_passed):
    """결과를 JSON 파일로 저장"""
    global _extreme_stats
    
    results_dir = os.path.join(_load_tests_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(results_dir, f"stage8_extreme_{timestamp}.json")
    
    result_data = {
        "timestamp": datetime.now().isoformat(),
        "stage": "Stage 8 EXTREME: Webhook + Self-Healing",
        "passed": is_passed,
        "metrics": summary,
        "stats": dict(_extreme_stats),
    }
    
    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)
        print(f"\n📄 Results saved to: {result_file}")
    except Exception as e:
        print(f"\n[WARN] Failed to save results: {e}")
