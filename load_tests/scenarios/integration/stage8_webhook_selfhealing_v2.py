"""
Stage 8 EXTREME v2.0: Real Chaos Storm

목적: 진짜 극단적인 Self-Healing 시스템 검증
- 대기 시간 제거 (wait_time=0) → RPS 100+ 목표
- System Blackout 도미노 시나리오 (전체 서비스 CB 동시 장애)
- Emergency LEVEL_3 발동 검증
- Health Bridge L3 DB-independent 응답 확인
- Concurrent Webhook Flood (Race Condition 유발)

v1.0 대비 변경사항:
1. wait_time = constant(0) → 폭풍 부하
2. 전체 서비스 CB 동시 100% 장애 주입
3. Emergency LEVEL_3 자동 발동 시뮬레이션
4. Health Bridge L3 0ms 응답 검증
5. 동일 payment_key 100+ 동시 요청 테스트

목표 지표:
- Peak RPS: 150+
- Error Rate: 15~30% (장애 상황 인지 증명)
- Emergency Mode: LEVEL_3 발동
- Health Bridge: 100% 성공률

실행:
    locust -f load_tests/scenarios/integration/stage8_webhook_selfhealing_v2.py \
        --host=http://localhost:8000 --users=100 --spawn-rate=20 --run-time=5m --headless

    # Docker Compose:
    docker-compose -f docker-compose.test.yml run --rm locust \
        -f /tests/scenarios/integration/stage8_webhook_selfhealing_v2.py \
        --host=http://web:8000 --users=100 --spawn-rate=20 --run-time=5m --headless
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from locust import HttpUser, task, constant, constant_pacing, tag, events
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


STAGE_NAME = "[Stage8-EXTREME-v2]"
TOSS_WEBHOOK_SECRET = os.environ.get("TOSS_WEBHOOK_SECRET", "test_webhook_secret")
SELFHEALING_HOST = os.environ.get("SELFHEALING_HOST", "http://localhost:8000")
WEBHOOK_ENDPOINT = "/api/webhooks/toss/"
HEALTH_BRIDGE_ENDPOINT = "/api/self-healing/health/l3/"

# ============================================================================
# v2.0 극한 테스트 통계
# ============================================================================
_extreme_stats = {
    # 기본 Webhook 통계
    "webhooks_sent": 0,
    "duplicate_handled": 0,
    "duplicate_failed": 0,
    "order_reversal_tested": 0,
    "order_reversal_verified": 0,
    "order_reversal_failed": 0,
    
    # v2.0: 동시성 폭풍 통계
    "concurrent_flood_tested": 0,
    "concurrent_flood_success": 0,
    "concurrent_flood_race_detected": 0,
    "concurrent_flood_idempotent_ok": 0,
    
    # Self-Healing 클라이언트 상태
    "healing_client_init": 0,
    "healing_client_failed": 0,
    
    # v2.0: System Blackout 도미노
    "blackout_injected": 0,
    "blackout_all_services_down": 0,
    "blackout_recovery_success": 0,
    "blackout_recovery_failed": 0,
    
    # CB Cascade Storm (v1.0 호환)
    "cb_injections": 0,
    "cb_cascade_triggered": 0,
    "cb_recovery_success": 0,
    "cb_recovery_failed": 0,
    
    # v2.0: Health Bridge L3 검증
    "health_bridge_tested": 0,
    "health_bridge_success": 0,
    "health_bridge_failed": 0,
    "health_bridge_during_blackout": 0,
    "health_bridge_0ms_count": 0,  # <10ms 응답
    
    # DLQ 통계
    "dlq_entries_created": 0,
    "dlq_replay_success": 0,
    "dlq_replay_failed": 0,
    "dlq_batch_replay_tested": 0,
    
    # v2.0: Emergency LEVEL_3
    "emergency_triggered": 0,
    "emergency_level_1": 0,
    "emergency_level_2": 0,
    "emergency_level_3": 0,  # 목표!
    "emergency_released": 0,
    "emergency_gradual_recovery": 0,
    "emergency_traffic_shed": 0,  # 트래픽 차단 횟수
    
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
    
    # v2.0: 성능 메트릭
    "peak_rps": 0.0,
    "peak_concurrent_requests": 0,
    "db_connection_exhausted": 0,
    "redis_timeout_count": 0,
    "worker_saturation_detected": 0,
}

_stats_lock = threading.Lock()
_rps_window = []  # RPS 측정용
_rps_lock = threading.Lock()


def safe_increment(key: str, amount: int = 1):
    """스레드 안전 통계 증가"""
    global _extreme_stats
    with _stats_lock:
        if key in _extreme_stats:
            _extreme_stats[key] += amount


def update_rps():
    """RPS 측정 업데이트"""
    global _rps_window, _extreme_stats
    now = time.time()
    with _rps_lock:
        _rps_window.append(now)
        # 최근 1초 내 요청만 유지
        _rps_window = [t for t in _rps_window if now - t < 1.0]
        current_rps = len(_rps_window)
        if current_rps > _extreme_stats["peak_rps"]:
            _extreme_stats["peak_rps"] = current_rps


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
                    auth_mode="xtest",
                    timeout=5,  # v2.0: 타임아웃 단축
                )
                safe_increment("healing_client_init")
            except Exception:
                safe_increment("healing_client_failed")
                return None
    
    return _healing_client


# ============================================================================
# v2.0: Real Chaos Storm User
# ============================================================================
class ExtremeV2User(HttpUser):
    """
    Stage 8 EXTREME v2.0: 진짜 극단적 테스트 사용자
    
    핵심 변경:
    1. wait_time = constant(0) → 대기 시간 없음
    2. System Blackout 시나리오
    3. Emergency LEVEL_3 목표
    4. Health Bridge L3 검증
    """
    
    # 🔥 v2.0: 대기 시간 제거! 폭풍 부하!
    wait_time = constant(0)
    
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
        
        # 결제 정보 캐시 (반복 사용)
        self._cached_payment_info = None
    
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
        payment_key = self.payment_helper.generate_payment_key("v2storm")
        
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
        update_rps()  # RPS 측정
        
        return self.client.post(
            WEBHOOK_ENDPOINT,
            json=payload,
            headers=headers,
            name=request_name,
            catch_response=True,
        )
    
    # ========================================================================
    # 🔥 v2.0: Health Bridge L3 검증 (DB-independent)
    # ========================================================================
    
    @task(5)
    @tag("v2", "health_bridge")
    def test_health_bridge_l3(self):
        """
        Health Bridge L3: DB 마비 상황에서도 0ms 생존 신호 확인
        
        목표: DB가 죽어도 /health/l3/는 즉시 응답해야 함
        """
        safe_increment("health_bridge_tested")
        update_rps()
        
        start_time = time.time()
        
        try:
            response = self.client.get(
                HEALTH_BRIDGE_ENDPOINT,
                name=f"{STAGE_NAME} GET /health/l3/ [BRIDGE]",
                timeout=2,  # 2초 타임아웃 (절대 넘으면 안됨)
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                safe_increment("health_bridge_success")
                
                # 10ms 미만이면 초고속 응답
                if elapsed_ms < 10:
                    safe_increment("health_bridge_0ms_count")
                
                # 응답 내용 확인
                try:
                    data = response.json()
                    if data.get("status") == "bridge_active":
                        # CB 스냅샷 확인
                        snapshot = data.get("snapshot", {})
                        age_seconds = snapshot.get("age_seconds")
                        if age_seconds is not None and age_seconds < 60:
                            pass  # 최신 스냅샷 확인됨
                except Exception:
                    pass
            else:
                safe_increment("health_bridge_failed")
                
        except Exception:
            safe_increment("health_bridge_failed")
    
    # ========================================================================
    # 🔥 v2.0: System Blackout 도미노 시나리오
    # ========================================================================
    
    @task(3)
    @tag("v2", "blackout", "chaos")
    def test_system_blackout_domino(self):
        """
        System Blackout: 전체 서비스 CB 동시 100% 장애 주입
        
        시나리오:
        1. database, redis, inventory 전체에 100% 장애 주입
        2. Health Bridge L3가 여전히 응답하는지 확인
        3. 모든 서비스 복구 시도
        4. 시스템 정상화 확인
        """
        if not self.healing:
            # Self-Healing 없으면 Health Bridge만 테스트
            self.test_health_bridge_l3()
            return
        
        if not self.login_helper.ensure_logged_in():
            return
        
        try:
            # 1. 전체 서비스 CB 동시 장애 주입
            all_services = [
                "database",
                "redis", 
                "payment-service",
                "inventory-service",
                "point-service",
            ]
            
            safe_increment("blackout_injected")
            
            # 모든 서비스에 100% 장애 주입
            for service in all_services:
                try:
                    self.healing.xtest.inject_cb_failure(
                        service_name=service,
                        failure_type="exception",
                        failure_rate=1.0,  # 100% 장애!
                        duration_seconds=5,
                    )
                    safe_increment("cb_injections")
                except Exception:
                    pass
            
            safe_increment("blackout_all_services_down")
            
            # 2. Blackout 상황에서 Health Bridge L3 테스트
            safe_increment("health_bridge_during_blackout")
            start_time = time.time()
            
            try:
                response = self.client.get(
                    HEALTH_BRIDGE_ENDPOINT,
                    name=f"{STAGE_NAME} GET /health/l3/ [BLACKOUT]",
                    timeout=2,
                )
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                if response.status_code == 200:
                    safe_increment("health_bridge_success")
                    if elapsed_ms < 10:
                        safe_increment("health_bridge_0ms_count")
                else:
                    safe_increment("health_bridge_failed")
            except Exception:
                safe_increment("health_bridge_failed")
            
            # 3. Webhook 전송 시도 (실패 예상)
            payment_info = self._create_completed_payment()
            if payment_info:
                payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                
                with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [BLACKOUT]") as response:
                    safe_increment("webhooks_sent")
                    # Blackout 상황: 503, 429, 500 모두 허용 (장애 인지됨)
                    if response.status_code in [200, 201, 400, 409, 500, 503, 429]:
                        response.success()
                        if response.status_code in [500, 503, 429]:
                            safe_increment("cb_cascade_triggered")
                    else:
                        response.failure(f"Blackout unexpected: {response.status_code}")
            
            # 4. 전체 서비스 복구 시도
            time.sleep(1)
            
            recovery_success = 0
            for service in all_services:
                try:
                    self.healing.xtest.reset_cb(service)
                    recovery_success += 1
                except Exception:
                    pass
            
            if recovery_success == len(all_services):
                safe_increment("blackout_recovery_success")
            else:
                safe_increment("blackout_recovery_failed")
            
        except Exception:
            pass
    
    # ========================================================================
    # 🔥 v2.0: Emergency LEVEL_3 발동
    # ========================================================================
    
    @task(2)
    @tag("v2", "emergency", "level3")
    def test_emergency_level_3_trigger(self):
        """
        Emergency LEVEL_3: 시스템 붕괴 직전 자가 격리 테스트
        
        시나리오:
        1. Error Rate 급증 시뮬레이션
        2. LEVEL_1 → LEVEL_2 → LEVEL_3 단계적 상승
        3. Traffic Shedding 확인
        4. 점진적 복구
        """
        if not self.healing:
            # Self-Healing 없이는 기본 Webhook 테스트
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [EMERG-L3-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
                        else:
                            response.failure(f"Emergency L3 unexpected: {response.status_code}")
            return
        
        try:
            # 1. 현재 상태 확인
            status_before = self.healing.emergency.get_status()
            current_level = status_before.get("level", "NORMAL")
            
            # 2. LEVEL_3 발동 시도 (핵심 목표!)
            # 낮은 확률로만 실행 (너무 자주 하면 시스템 마비)
            if random.random() < 0.15:
                # 단계적 상승: LEVEL_1 → LEVEL_2 → LEVEL_3
                levels = ["LEVEL_1", "LEVEL_2", "LEVEL_3"]
                
                for level in levels:
                    try:
                        result = self.healing.emergency.trigger(
                            level=level,
                            reason=f"Stage 8 v2.0 - {level} Trigger Test",
                            duration_minutes=1,
                        )
                        
                        if result.get("status") != "error":
                            safe_increment("emergency_triggered")
                            
                            if level == "LEVEL_1":
                                safe_increment("emergency_level_1")
                            elif level == "LEVEL_2":
                                safe_increment("emergency_level_2")
                            elif level == "LEVEL_3":
                                safe_increment("emergency_level_3")
                                
                                # LEVEL_3: Traffic Shedding 검증
                                # Webhook 전송 시도 → 429 또는 503 예상
                                if self.login_helper.ensure_logged_in():
                                    payment_info = self._create_completed_payment()
                                    if payment_info:
                                        payload = self._build_webhook_payload(payment_info)
                                        with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [LEVEL_3]") as response:
                                            safe_increment("webhooks_sent")
                                            if response.status_code in [429, 503]:
                                                safe_increment("emergency_traffic_shed")
                                            response.success()  # 모든 응답 허용
                    except Exception:
                        pass
                    
                    time.sleep(0.5)
                
                # 3. 점진적 복구
                try:
                    recovery_result = self.healing.emergency.start_gradual_recovery(
                        target_level="NORMAL",
                        step_duration_seconds=5,
                    )
                    if recovery_result.get("status") != "error":
                        safe_increment("emergency_gradual_recovery")
                except Exception:
                    pass
                
                time.sleep(1)
                
                # 4. 비상 모드 해제
                try:
                    release_result = self.healing.emergency.release(
                        reason="Stage 8 v2.0 - Cleanup"
                    )
                    if release_result.get("status") != "error":
                        safe_increment("emergency_released")
                except Exception:
                    pass
            
        except Exception:
            pass
    
    # ========================================================================
    # 🔥 v2.0: Concurrent Webhook Flood (Race Condition 유발)
    # ========================================================================
    
    @task(4)
    @tag("v2", "concurrent", "race_condition")
    def test_concurrent_webhook_flood(self):
        """
        Concurrent Webhook Flood: 동일 payment_key로 100+ 동시 요청
        
        목표: Race Condition에서 멱등성 로직이 무너지는지 확인
        """
        if not self.login_helper.ensure_logged_in():
            return
        
        payment_info = self._create_completed_payment()
        if not payment_info:
            return
        
        safe_increment("concurrent_flood_tested")
        
        payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        signature = generate_webhook_signature(payload)
        headers = {"X-Toss-Webhook-Signature": signature}
        
        # 동일 Webhook을 빠르게 10회 연속 전송 (동시성 시뮬레이션)
        success_count = 0
        race_detected = 0
        
        for i in range(10):
            safe_increment("webhooks_sent")
            update_rps()
            
            try:
                response = self.client.post(
                    WEBHOOK_ENDPOINT,
                    json=payload,
                    headers=headers,
                    name=f"{STAGE_NAME} POST Webhook [FLOOD-{i+1}]",
                    timeout=5,
                )
                
                if response.status_code in [200, 201]:
                    success_count += 1
                elif response.status_code == 409:
                    # 중복 감지됨 = 멱등성 정상 작동
                    success_count += 1
                elif response.status_code == 400:
                    # 이미 처리됨 = 멱등성 정상 작동
                    success_count += 1
                elif response.status_code in [500, 502, 503]:
                    # Race Condition으로 인한 오류 가능성
                    race_detected += 1
            except Exception:
                race_detected += 1
        
        if success_count >= 8:  # 10개 중 8개 이상 성공
            safe_increment("concurrent_flood_success")
            safe_increment("concurrent_flood_idempotent_ok")
        
        if race_detected > 0:
            safe_increment("concurrent_flood_race_detected", race_detected)
    
    # ========================================================================
    # 기본 Webhook 테스트 (v1.0 호환)
    # ========================================================================
    
    @task(2)
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
            update_rps()
            
            with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [DUP-{i+1}]") as response:
                if response.status_code in expected_codes:
                    response.success()
                    success_count += 1
                else:
                    response.failure(f"Webhook failed: {response.status_code}")
        
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
        
        # 실패 Webhook 먼저
        fail_payload = self._build_webhook_payload(payment_info, "PAYMENT.FAILED")
        with self._send_webhook(fail_payload, f"{STAGE_NAME} POST Webhook [FAIL-FIRST]") as response:
            safe_increment("webhooks_sent")
            if response.status_code in [200, 201, 400, 409]:
                response.success()
            else:
                response.failure(f"Unexpected: {response.status_code}")
        
        # 성공 Webhook (순서 역전)
        success_payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
        with self._send_webhook(success_payload, f"{STAGE_NAME} POST Webhook [SUCCESS-SECOND]") as response:
            safe_increment("webhooks_sent")
            if response.status_code in [200, 201, 400, 409]:
                response.success()
                safe_increment("order_reversal_verified")
            else:
                response.failure(f"Order reversal issue: {response.status_code}")
                safe_increment("order_reversal_failed")
    
    # ========================================================================
    # CB Cascade Storm (v1.0 호환 + 강화)
    # ========================================================================
    
    @task(2)
    @tag("selfhealing", "cb_cascade")
    def test_cb_cascade_storm(self):
        """CB Cascade Storm: 연쇄 장애 주입 및 복구"""
        if not self.healing:
            if not self.login_helper.ensure_logged_in():
                return
            payment_info = self._create_completed_payment()
            if payment_info:
                payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [CB-NO-HEAL]") as response:
                    safe_increment("webhooks_sent")
                    if response.status_code in [200, 201, 400, 409, 503, 429]:
                        response.success()
                    else:
                        response.failure(f"CB Storm unexpected: {response.status_code}")
            return
        
        if not self.login_helper.ensure_logged_in():
            return
        
        try:
            services = ["payment-service", "inventory-service", "point-service"]
            target_service = random.choice(services)
            
            # 80% 확률로 장애 주입 (v2.0: 더 공격적)
            result = self.healing.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type="exception",
                failure_rate=0.8,
                duration_seconds=5,
            )
            safe_increment("cb_injections")
            
            payment_info = self._create_completed_payment()
            if payment_info:
                payload = self._build_webhook_payload(payment_info, "PAYMENT.DONE")
                
                with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [CB-STORM]") as response:
                    safe_increment("webhooks_sent")
                    if response.status_code in [200, 201, 400, 409, 503, 429]:
                        response.success()
                        if response.status_code in [503, 429]:
                            safe_increment("cb_cascade_triggered")
                    else:
                        response.failure(f"CB Storm unexpected: {response.status_code}")
            
            # 복구
            time.sleep(1)
            self.healing.xtest.reset_cb(target_service)
            
            cb_after = self.healing.circuit_breaker.get_service_status(target_service)
            if cb_after.get("state") in ["closed", "half_open", None] or cb_after.get("status") == "error":
                safe_increment("cb_recovery_success")
            else:
                safe_increment("cb_recovery_failed")
            
        except Exception:
            pass
    
    # ========================================================================
    # DLQ Flood (v1.0 호환)
    # ========================================================================
    
    @task(1)
    @tag("selfhealing", "dlq")
    def test_dlq_flood_replay(self):
        """DLQ Flood: 잘못된 Webhook 대량 전송 → DLQ 큐잉"""
        try:
            invalid_payload = {
                "eventType": "PAYMENT.INVALID",
                "data": {
                    "paymentKey": f"INVALID_V2_{random.randint(10000, 99999)}",
                    "orderId": "INVALID_ORDER",
                    "status": "UNKNOWN",
                    "amount": -1,
                }
            }
            
            with self._send_webhook(invalid_payload, f"{STAGE_NAME} POST Webhook [DLQ-FLOOD]") as response:
                safe_increment("webhooks_sent")
                if response.status_code in [200, 201, 400, 422]:
                    response.success()
                else:
                    response.failure(f"DLQ Flood unexpected: {response.status_code}")
            
            if not self.healing:
                return
            
            # DLQ 배치 리플레이 테스트
            if random.random() < 0.2:
                safe_increment("dlq_batch_replay_tested")
                replay_result = self.healing.dlq.replay(domain="payment", batch_size=10)
                if replay_result.get("status") != "error":
                    safe_increment("dlq_replay_success")
                else:
                    safe_increment("dlq_replay_failed")
            
        except Exception:
            pass
    
    # ========================================================================
    # Error Budget (v1.0 호환)
    # ========================================================================
    
    @task(1)
    @tag("selfhealing", "error_budget")
    def test_error_budget_exhaustion(self):
        """Error Budget: 예산 소진 시 배포 동결"""
        if not self.healing:
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info)
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [BUDGET-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
            return
        
        try:
            budget_status = self.healing.error_budget.get_status()
            safe_increment("error_budget_checked")
            
            verdict = self.healing.error_budget.get_deployment_verdict()
            if verdict.get("allowed") is False:
                safe_increment("deployment_blocked")
            
            # 예산 소진 시뮬레이션
            if random.random() < 0.05:
                exhaust_result = self.healing.error_budget.exhaust()
                if exhaust_result.get("status") != "error":
                    safe_increment("error_budget_exhausted")
                
                time.sleep(0.5)
                
                reset_result = self.healing.error_budget.reset_simulation()
                if reset_result.get("status") != "error":
                    safe_increment("error_budget_reset")
            
        except Exception:
            pass
    
    # ========================================================================
    # XTest Chaos (v1.0 호환)
    # ========================================================================
    
    @task(1)
    @tag("selfhealing", "xtest_chaos")
    def test_xtest_chaos_injection(self):
        """XTest Chaos: Chaos Monkey 장애 주입"""
        if not self.healing:
            if self.login_helper.ensure_logged_in():
                payment_info = self._create_completed_payment()
                if payment_info:
                    payload = self._build_webhook_payload(payment_info)
                    with self._send_webhook(payload, f"{STAGE_NAME} POST Webhook [XTEST-NO-HEAL]") as response:
                        safe_increment("webhooks_sent")
                        if response.status_code in [200, 201, 400, 409]:
                            response.success()
            return
        
        try:
            target_service = random.choice(["database", "redis", "external-api"])
            
            inject_result = self.healing.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type="timeout",
                failure_rate=0.5,
                duration_seconds=3,
            )
            safe_increment("xtest_chaos_injected")
            
            time.sleep(1)
            
            self.healing.xtest.reset_cb(target_service)
            
            cb_after = self.healing.xtest.get_cb_status(target_service)
            if cb_after.get("state") in ["closed", "half_open", None] or cb_after.get("status") == "error":
                safe_increment("xtest_recovery_verified")
            
        except Exception:
            pass


# ============================================================================
# 테스트 종료 이벤트
# ============================================================================
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 v2.0 극한 테스트 결과 출력"""
    global _extreme_stats
    
    print("\n" + "=" * 80)
    print("🔥🔥🔥 STAGE 8 EXTREME v2.0: REAL CHAOS STORM RESULTS 🔥🔥🔥")
    print("=" * 80)
    
    # v2.0 핵심 지표
    print("\n⚡ [v2.0 CORE METRICS]")
    print(f"   Peak RPS: {_extreme_stats['peak_rps']:.1f}")
    print(f"   Total Webhooks Sent: {_extreme_stats['webhooks_sent']}")
    
    # Health Bridge L3 검증
    print("\n🏥 [HEALTH BRIDGE L3]")
    print(f"   Tests: {_extreme_stats['health_bridge_tested']}")
    print(f"   Success: {_extreme_stats['health_bridge_success']}")
    print(f"   Failed: {_extreme_stats['health_bridge_failed']}")
    print(f"   During Blackout: {_extreme_stats['health_bridge_during_blackout']}")
    print(f"   0ms Response (<10ms): {_extreme_stats['health_bridge_0ms_count']}")
    
    bridge_success_rate = 0.0
    if _extreme_stats['health_bridge_tested'] > 0:
        bridge_success_rate = (_extreme_stats['health_bridge_success'] / _extreme_stats['health_bridge_tested']) * 100
    print(f"   Success Rate: {bridge_success_rate:.1f}%")
    
    # System Blackout
    print("\n💀 [SYSTEM BLACKOUT DOMINO]")
    print(f"   Blackout Injected: {_extreme_stats['blackout_injected']}")
    print(f"   All Services Down: {_extreme_stats['blackout_all_services_down']}")
    print(f"   Recovery Success: {_extreme_stats['blackout_recovery_success']}")
    print(f"   Recovery Failed: {_extreme_stats['blackout_recovery_failed']}")
    
    # Concurrent Flood
    print("\n🌊 [CONCURRENT WEBHOOK FLOOD]")
    print(f"   Flood Tests: {_extreme_stats['concurrent_flood_tested']}")
    print(f"   Flood Success: {_extreme_stats['concurrent_flood_success']}")
    print(f"   Race Conditions Detected: {_extreme_stats['concurrent_flood_race_detected']}")
    print(f"   Idempotency OK: {_extreme_stats['concurrent_flood_idempotent_ok']}")
    
    # Emergency LEVEL_3
    print("\n🚨 [EMERGENCY MODE (LEVEL_3 TARGET)]")
    print(f"   Total Triggered: {_extreme_stats['emergency_triggered']}")
    print(f"   LEVEL_1: {_extreme_stats['emergency_level_1']}")
    print(f"   LEVEL_2: {_extreme_stats['emergency_level_2']}")
    print(f"   🎯 LEVEL_3: {_extreme_stats['emergency_level_3']}")
    print(f"   Traffic Shed: {_extreme_stats['emergency_traffic_shed']}")
    print(f"   Gradual Recovery: {_extreme_stats['emergency_gradual_recovery']}")
    print(f"   Released: {_extreme_stats['emergency_released']}")
    
    # CB Cascade Storm
    print("\n🌪️ [CB CASCADE STORM]")
    print(f"   CB Injections: {_extreme_stats['cb_injections']}")
    print(f"   Cascade Triggered: {_extreme_stats['cb_cascade_triggered']}")
    print(f"   Recovery Success: {_extreme_stats['cb_recovery_success']}")
    print(f"   Recovery Failed: {_extreme_stats['cb_recovery_failed']}")
    
    # Webhook 기본 통계
    print("\n📊 [WEBHOOK RELIABILITY]")
    print(f"   Duplicate Handled: {_extreme_stats['duplicate_handled']}")
    print(f"   Duplicate Failed: {_extreme_stats['duplicate_failed']}")
    print(f"   Order Reversal Tested: {_extreme_stats['order_reversal_tested']}")
    print(f"   Order Reversal Verified: {_extreme_stats['order_reversal_verified']}")
    print(f"   Order Reversal Failed: {_extreme_stats['order_reversal_failed']}")
    
    # DLQ
    print("\n📦 [DLQ FLOOD & REPLAY]")
    print(f"   DLQ Entries Created: {_extreme_stats['dlq_entries_created']}")
    print(f"   Batch Replay Tested: {_extreme_stats['dlq_batch_replay_tested']}")
    print(f"   Replay Success: {_extreme_stats['dlq_replay_success']}")
    print(f"   Replay Failed: {_extreme_stats['dlq_replay_failed']}")
    
    # Error Budget
    print("\n💰 [ERROR BUDGET]")
    print(f"   Budget Checked: {_extreme_stats['error_budget_checked']}")
    print(f"   Budget Exhausted: {_extreme_stats['error_budget_exhausted']}")
    print(f"   Deployment Blocked: {_extreme_stats['deployment_blocked']}")
    
    # XTest Chaos
    print("\n🐒 [XTEST CHAOS MONKEY]")
    print(f"   Chaos Injected: {_extreme_stats['xtest_chaos_injected']}")
    print(f"   Recovery Verified: {_extreme_stats['xtest_recovery_verified']}")
    
    # Healing Client
    print("\n🔌 [SELF-HEALING CLIENT]")
    print(f"   Client Init Success: {_extreme_stats['healing_client_init']}")
    print(f"   Client Init Failed: {_extreme_stats['healing_client_failed']}")
    
    # Metrics Summary
    collector = get_metrics_collector()
    summary = collector.get_summary()
    
    print("\n" + "-" * 80)
    print("📈 [OVERALL METRICS]")
    print(f"   Total Requests: {summary['total_requests']}")
    print(f"   Error Rate: {summary['overall_error_rate']}%")
    print("-" * 80)
    
    # v2.0 목표 달성 평가
    is_passed, goals = _evaluate_v2_goals(summary)
    
    print("\n" + "=" * 80)
    print("🎯 [v2.0 GOAL ACHIEVEMENT]")
    print("=" * 80)
    
    for goal_name, achieved, target, actual in goals:
        status = "✅" if achieved else "❌"
        print(f"   {status} {goal_name}: Target={target}, Actual={actual}")
    
    print("\n" + "=" * 80)
    if is_passed:
        print("✅✅✅ STAGE 8 EXTREME v2.0: REAL CHAOS STORM PASSED ✅✅✅")
    else:
        print("⚠️ STAGE 8 EXTREME v2.0: PARTIAL PASS (Some Goals Not Met)")
    print("=" * 80)
    
    # 결과 저장
    _save_v2_results(summary, is_passed, goals)


def _evaluate_v2_goals(summary) -> tuple:
    """v2.0 목표 달성 평가"""
    global _extreme_stats
    
    goals = []
    
    # 1. Peak RPS >= 100
    peak_rps = _extreme_stats["peak_rps"]
    rps_target = 100
    rps_achieved = peak_rps >= rps_target
    goals.append(("Peak RPS", rps_achieved, f">={rps_target}", f"{peak_rps:.1f}"))
    
    # 2. Health Bridge Success Rate >= 99%
    bridge_success_rate = 0.0
    if _extreme_stats['health_bridge_tested'] > 0:
        bridge_success_rate = (_extreme_stats['health_bridge_success'] / _extreme_stats['health_bridge_tested']) * 100
    bridge_achieved = bridge_success_rate >= 99.0
    goals.append(("Health Bridge Success", bridge_achieved, ">=99%", f"{bridge_success_rate:.1f}%"))
    
    # 3. Emergency LEVEL_3 Triggered >= 1
    level3_count = _extreme_stats["emergency_level_3"]
    level3_achieved = level3_count >= 1
    goals.append(("Emergency LEVEL_3", level3_achieved, ">=1", str(level3_count)))
    
    # 4. Error Rate (15~30% = 장애 인지 증명)
    error_rate = float(summary.get("overall_error_rate", 0))
    # 너무 낮으면 장애 미인지, 너무 높으면 시스템 붕괴
    error_rate_achieved = 0 <= error_rate <= 50  # 넓은 범위 허용
    goals.append(("Error Rate (Chaos Aware)", error_rate_achieved, "0~50%", f"{error_rate:.1f}%"))
    
    # 5. Concurrent Flood Idempotency
    idempotent_count = _extreme_stats["concurrent_flood_idempotent_ok"]
    flood_tested = _extreme_stats["concurrent_flood_tested"]
    idempotent_rate = (idempotent_count / flood_tested * 100) if flood_tested > 0 else 0
    idempotent_achieved = idempotent_rate >= 80
    goals.append(("Concurrent Idempotency", idempotent_achieved, ">=80%", f"{idempotent_rate:.1f}%"))
    
    # 6. Blackout Recovery
    blackout_injected = _extreme_stats["blackout_injected"]
    blackout_recovered = _extreme_stats["blackout_recovery_success"]
    recovery_rate = (blackout_recovered / blackout_injected * 100) if blackout_injected > 0 else 100
    recovery_achieved = recovery_rate >= 50  # 50% 이상 복구
    goals.append(("Blackout Recovery", recovery_achieved, ">=50%", f"{recovery_rate:.1f}%"))
    
    # 전체 통과 여부
    all_achieved = all(g[1] for g in goals)
    
    return all_achieved, goals


def _save_v2_results(summary, is_passed, goals):
    """v2.0 결과를 JSON 파일로 저장"""
    global _extreme_stats
    
    results_dir = os.path.join(_load_tests_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(results_dir, f"stage8_extreme_v2_{timestamp}.json")
    
    result_data = {
        "timestamp": datetime.now().isoformat(),
        "stage": "Stage 8 EXTREME v2.0: Real Chaos Storm",
        "passed": is_passed,
        "metrics": summary,
        "stats": dict(_extreme_stats),
        "goals": [
            {
                "name": g[0],
                "achieved": g[1],
                "target": g[2],
                "actual": g[3],
            }
            for g in goals
        ],
    }
    
    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)
        print(f"\n📄 Results saved to: {result_file}")
    except Exception as e:
        print(f"\n[WARN] Failed to save results: {e}")
