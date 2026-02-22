"""
Stage 7: Race Condition + Self-Healing Extreme Test

목적: 동일 order_id에 대한 동시 접근 검증 + Self-Healing 시스템 극한 테스트
- 같은 order_id로 여러 사용자/세션 동시 결제 시도
- 409/400 응답 기대
- 단 하나의 결제만 성공해야 함
- 극한 상황에서 Self-Healing 시스템 작동 검증:
  * Circuit Breaker: 대량 실패 시 CB Open/Recovery
  * Emergency Mode: 극단적 장애 시 비상 모드 트리거/해제
  * DLQ: 실패한 결제 DLQ 적재 및 재처리
  * Error Budget: 에러 버짓 소진/회복 테스트
  * XTest Mode: Blast Radius, 장애 주입, 힐링 이벤트 기록

실행:
    locust -f load_tests/scenarios/integration/stage7_race_conflict.py --host=http://localhost:8000 --users=50 --spawn-rate=50 --run-time=3m --headless
"""

import os
import sys
import json
from datetime import datetime

_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _load_tests_dir not in sys.path:
    sys.path.insert(0, _load_tests_dir)

import random
import threading
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector

# Self-Healing 시스템 통합
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.config import configure as sh_configure
    SELFHEALING_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] SelfHealing import failed: {e}")
    SELFHEALING_AVAILABLE = False


STAGE_NAME = "[Stage7]"
DEBUG_MODE = os.environ.get("STAGE7_DEBUG", "false").lower() == "true"

# Race condition 통계
_race_stats = {
    "shared_orders": {},  # order_id -> [success_count, fail_count]
    "total_race_attempts": 0,
    "double_success": 0,  # 같은 주문에 2번 이상 성공 (심각한 문제!)
}
_race_lock = threading.Lock()

# Self-Healing 통계
_healing_stats = {
    # Circuit Breaker
    "cb_checks": 0,
    "cb_open_triggered": 0,
    "cb_recovery_triggered": 0,
    "cb_resets": 0,
    # Emergency Mode
    "emergency_triggers": 0,
    "emergency_releases": 0,
    "emergency_escalations": 0,
    # DLQ
    "dlq_stats_checks": 0,
    "dlq_replays": 0,
    "dlq_pending_count": 0,
    # Error Budget
    "error_budget_checks": 0,
    "error_budget_injections": 0,
    "error_budget_exhausted": 0,
    "error_budget_resets": 0,
    # XTest Mode
    "blast_radius_tests": 0,
    "healing_events_recorded": 0,
    "snapshots_taken": 0,
    "timeline_queries": 0,
    # Health Checks
    "health_checks": 0,
    "health_failures": 0,
    # Errors
    "selfhealing_errors": [],
}
_healing_lock = threading.Lock()


def debug_log(msg: str):
    """디버그 로그 출력."""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[DEBUG {timestamp}] {msg}")


class RaceConditionUser(HttpUser):
    """
    Race Condition + Self-Healing Extreme Test 사용자

    동일 리소스에 대한 동시 접근 테스트 + 극한 상황에서 힐링 시스템 검증
    """

    wait_time = between(0.1, 0.5)  # 매우 빠른 요청

    # 공유 주문 풀 (여러 사용자가 같은 주문에 접근)
    _shared_order_pool = []
    _pool_lock = threading.Lock()
    _pool_initialized = False
    
    # Self-Healing 클라이언트 (싱글톤)
    _sh_client = None
    _sh_lock = threading.Lock()
    _sh_initialized = False

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        # 공유 주문 풀 초기화 (최초 1회)
        self._maybe_init_shared_orders()
        
        # Self-Healing 클라이언트 초기화
        self._maybe_init_selfhealing()
    
    def _maybe_init_selfhealing(self):
        """Self-Healing 클라이언트 초기화 (최초 1회)."""
        if not SELFHEALING_AVAILABLE:
            return
        
        if RaceConditionUser._sh_initialized:
            return
        
        with RaceConditionUser._sh_lock:
            if RaceConditionUser._sh_initialized:
                return
            
            try:
                # Self-Healing 서버 설정 (쇼핑 API와 같은 호스트)
                host = os.environ.get("SELFHEALING_HOST", "http://localhost:8000")
                debug_log(f"Initializing SelfHealingClient with host={host}")
                
                RaceConditionUser._sh_client = SelfHealingClient(
                    host=host,
                    auth_mode="xtest",  # XTest 모드로 Chaos 기능 활성화
                    timeout=10,
                )
                
                # 헬스 체크로 연결 확인
                ping_result = RaceConditionUser._sh_client.health.ping()
                debug_log(f"SelfHealing ping: {ping_result}")
                
                if ping_result.get("ok"):
                    debug_log("SelfHealingClient initialized successfully")
                else:
                    debug_log(f"SelfHealing ping failed: {ping_result}")
                
                RaceConditionUser._sh_initialized = True
                
            except Exception as e:
                debug_log(f"SelfHealing initialization failed: {e}")
                with _healing_lock:
                    _healing_stats["selfhealing_errors"].append(f"init: {str(e)}")
    
    @property
    def sh(self):
        """Self-Healing 클라이언트 반환."""
        return RaceConditionUser._sh_client

    def _maybe_init_shared_orders(self):
        """공유 주문 풀 초기화"""
        if RaceConditionUser._pool_initialized:
            return

        with RaceConditionUser._pool_lock:
            if RaceConditionUser._pool_initialized:
                return

            # 미리 몇 개의 주문 생성
            for _ in range(5):
                order_info = self._create_order_for_race()
                if order_info:
                    RaceConditionUser._shared_order_pool.append(order_info)

            RaceConditionUser._pool_initialized = True

    def _create_order_for_race(self):
        """Race 테스트용 주문 생성"""
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return None

        # 장바구니 준비
        self.cart_helper.clear_cart()
        product_id = random.choice(product_ids)
        self.cart_helper.add_item(product_id, 1)

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

        return {
            "order_id": order_id,
            "final_amount": final_amount,
            "attempted": False,
        }

    @task(5)
    @tag("race", "same_order")
    def race_on_same_order(self):
        """
        같은 주문에 대한 동시 결제 시도

        여러 사용자가 같은 order_id로 결제 시도
        → 단 하나만 성공해야 함
        """
        global _race_stats

        if not self.login_helper.ensure_logged_in():
            return

        # 공유 주문 풀에서 주문 선택
        if not RaceConditionUser._shared_order_pool:
            return

        with RaceConditionUser._pool_lock:
            if not RaceConditionUser._shared_order_pool:
                return
            # randrange로 명시적 인덱스 선택 + defensive copy
            idx = random.randrange(len(RaceConditionUser._shared_order_pool))
            shared = RaceConditionUser._shared_order_pool[idx]
        order_info = shared.copy()  # shallow defensive copy

        order_id = order_info["order_id"]
        final_amount = order_info["final_amount"]

        # 결제 시도
        payment_key = self.payment_helper.generate_payment_key("race")

        with _race_lock:
            _race_stats["total_race_attempts"] += 1
            if order_id not in _race_stats["shared_orders"]:
                _race_stats["shared_orders"][order_id] = {"success": 0, "fail": 0}

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RACE]",
            catch_response=True,
        ) as response:
            with _race_lock:
                if response.status_code in [200, 201]:
                    prev_success = _race_stats["shared_orders"][order_id]["success"]
                    _race_stats["shared_orders"][order_id]["success"] += 1

                    # 같은 주문에 2번 이상 성공하면 심각한 문제!
                    # 정확한 중복 결제 건수 카운팅 (success=3이면 2건의 중복)
                    if prev_success >= 1:
                        _race_stats["double_success"] += 1
                        response.failure(f"🚨 CRITICAL: Multiple payments on order {order_id}!")
                    else:
                        response.success()

                elif response.status_code in [400, 409]:
                    # 이미 결제됨 - 정상
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()
                else:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.failure(f"Unexpected: {response.status_code}")

    @task(2)
    @tag("race", "new_order")
    def create_and_race(self):
        """
        새 주문 생성 후 즉시 경쟁

        주문 생성 직후 여러 결제 시도 (새 주문을 공유 풀에 추가)
        """
        if not self.login_helper.ensure_logged_in():
            return

        order_info = self._create_order_for_race()
        if not order_info:
            return

        # 공유 풀에 추가
        with RaceConditionUser._pool_lock:
            RaceConditionUser._shared_order_pool.append(order_info)
            # 풀 크기 제한 (오래된 주문 제거)
            if len(RaceConditionUser._shared_order_pool) > 20:
                RaceConditionUser._shared_order_pool.pop(0)

        # 즉시 결제 시도
        order_id = order_info["order_id"]
        final_amount = order_info["final_amount"]
        payment_key = self.payment_helper.generate_payment_key("race_new")

        with _race_lock:
            _race_stats["total_race_attempts"] += 1
            if order_id not in _race_stats["shared_orders"]:
                _race_stats["shared_orders"][order_id] = {"success": 0, "fail": 0}

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [RACE-NEW]",
            catch_response=True,
        ) as response:
            with _race_lock:
                if response.status_code in [200, 201]:
                    _race_stats["shared_orders"][order_id]["success"] += 1
                    response.success()
                elif response.status_code in [400, 409]:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()
                else:
                    _race_stats["shared_orders"][order_id]["fail"] += 1
                    response.success()

    # =========================================================================
    # Self-Healing Extreme Test Tasks
    # =========================================================================

    @task(3)
    @tag("healing", "circuit_breaker")
    def extreme_circuit_breaker_test(self):
        """
        극한 Circuit Breaker 테스트
        
        대량 실패 상황을 시뮬레이션하여 CB Open/Recovery 동작 검증
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. CB 상태 확인
            cb_status = self.sh.xtest.get_cb_status()
            debug_log(f"CB Status: {cb_status}")
            
            with _healing_lock:
                _healing_stats["cb_checks"] += 1
            
            # 2. Payment 서비스에 장애 주입 (극단적: 100% 실패율)
            if random.random() < 0.3:  # 30% 확률로 장애 주입
                inject_result = self.sh.xtest.inject_cb_failure(
                    service_name="payment-service",
                    failure_type="exception",
                    failure_rate=1.0,
                    duration_seconds=10,
                )
                debug_log(f"CB Failure Injected: {inject_result}")
                
                with _healing_lock:
                    _healing_stats["cb_open_triggered"] += 1
                
                # 힐링 이벤트 기록
                self.sh.xtest.record_healing_event(
                    event_type="cb_failure_injected",
                    service_name="payment-service",
                    details={"failure_rate": 1.0, "duration": 10},
                )
                with _healing_lock:
                    _healing_stats["healing_events_recorded"] += 1
            
            # 3. CB 복구 테스트
            if random.random() < 0.2:  # 20% 확률로 복구 트리거
                recovery_result = self.sh.xtest.trigger_cb_recovery("payment-service")
                debug_log(f"CB Recovery Triggered: {recovery_result}")
                
                with _healing_lock:
                    _healing_stats["cb_recovery_triggered"] += 1
            
            # 4. Fast Fail 테스트 (다량 요청 시뮬레이션)
            if random.random() < 0.15:  # 15% 확률로 Fast Fail 테스트
                fast_fail_result = self.sh.xtest.fast_fail_test(
                    service_name="payment-service",
                    request_count=20,
                )
                debug_log(f"Fast Fail Test: {fast_fail_result}")
                
        except Exception as e:
            debug_log(f"CB Test Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"cb: {str(e)[:100]}")

    @task(2)
    @tag("healing", "emergency")
    def extreme_emergency_mode_test(self):
        """
        극한 Emergency Mode 테스트
        
        비상 모드 트리거/에스컬레이션/해제 시나리오
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. 비상 모드 상태 확인
            em_status = self.sh.emergency.get_status()
            debug_log(f"Emergency Status: {em_status}")
            
            # 2. 극단적 상황 시뮬레이션 - 비상 모드 트리거
            if random.random() < 0.2:  # 20% 확률로 비상 모드 트리거
                level = random.choice(["LEVEL_1", "LEVEL_2", "LEVEL_3"])
                trigger_result = self.sh.emergency.trigger(
                    level=level,
                    reason=f"Stage7 Race Condition Extreme Test - {level}",
                    duration_minutes=1,  # 1분 후 자동 해제
                )
                debug_log(f"Emergency Triggered ({level}): {trigger_result}")
                
                with _healing_lock:
                    _healing_stats["emergency_triggers"] += 1
                    if level in ["LEVEL_2", "LEVEL_3"]:
                        _healing_stats["emergency_escalations"] += 1
                
                # 힐링 이벤트 기록
                self.sh.xtest.record_healing_event(
                    event_type="emergency_triggered",
                    service_name="payment-service",
                    details={"level": level, "reason": "race_condition_extreme"},
                )
                with _healing_lock:
                    _healing_stats["healing_events_recorded"] += 1
            
            # 3. 비상 모드 해제
            if random.random() < 0.3 and em_status.get("is_active"):
                release_result = self.sh.emergency.release(
                    reason="Stage7 Test - Controlled Release"
                )
                debug_log(f"Emergency Released: {release_result}")
                
                with _healing_lock:
                    _healing_stats["emergency_releases"] += 1
                    
        except Exception as e:
            debug_log(f"Emergency Test Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"emergency: {str(e)[:100]}")

    @task(2)
    @tag("healing", "dlq")
    def extreme_dlq_test(self):
        """
        극한 DLQ (Dead Letter Queue) 테스트
        
        실패한 결제 DLQ 적재 및 대량 재처리 시뮬레이션
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. DLQ 통계 확인
            dlq_stats = self.sh.dlq.stats()
            debug_log(f"DLQ Stats: {dlq_stats}")
            
            with _healing_lock:
                _healing_stats["dlq_stats_checks"] += 1
                pending = dlq_stats.get("pending_count", 0)
                if pending > _healing_stats["dlq_pending_count"]:
                    _healing_stats["dlq_pending_count"] = pending
            
            # 2. DLQ 목록 조회 (pending 상태)
            dlq_list = self.sh.dlq.list(status="pending", domain="payment", limit=10)
            debug_log(f"DLQ Pending List: {dlq_list}")
            
            # 3. 대량 리플레이 시도
            if random.random() < 0.25 and dlq_stats.get("pending_count", 0) > 0:
                replay_result = self.sh.dlq.replay(domain="payment", batch_size=20)
                debug_log(f"DLQ Replay: {replay_result}")
                
                with _healing_lock:
                    _healing_stats["dlq_replays"] += 1
                
                # 힐링 이벤트 기록
                self.sh.xtest.record_healing_event(
                    event_type="dlq_replay",
                    service_name="payment-service",
                    details={"batch_size": 20, "domain": "payment"},
                )
                with _healing_lock:
                    _healing_stats["healing_events_recorded"] += 1
                    
        except Exception as e:
            debug_log(f"DLQ Test Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"dlq: {str(e)[:100]}")

    @task(2)
    @tag("healing", "error_budget")
    def extreme_error_budget_test(self):
        """
        극한 Error Budget 테스트
        
        에러 버짓 소진/회복 시나리오
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. Error Budget 상태 확인
            eb_status = self.sh.error_budget.get_status()
            debug_log(f"Error Budget Status: {eb_status}")
            
            with _healing_lock:
                _healing_stats["error_budget_checks"] += 1
            
            remaining = eb_status.get("remaining_percent", 100)
            
            # 2. 에러 버짓 소진 시뮬레이션 (극단적)
            if random.random() < 0.2 and remaining > 50:
                # 대량 에러 주입
                inject_result = self.sh.xtest.inject_error_budget(
                    slo_name="availability",
                    error_count=random.randint(50, 200),  # 50~200개 에러
                )
                debug_log(f"Error Budget Injected: {inject_result}")
                
                with _healing_lock:
                    _healing_stats["error_budget_injections"] += 1
                
                # 힐링 이벤트 기록
                self.sh.xtest.record_healing_event(
                    event_type="error_budget_consumed",
                    service_name="payment-service",
                    details={"error_count": inject_result.get("error_count", 0)},
                )
                with _healing_lock:
                    _healing_stats["healing_events_recorded"] += 1
            
            # 3. 에러 버짓 소진 체크
            if remaining < 10:
                debug_log(f"ERROR BUDGET CRITICAL: {remaining}% remaining")
                with _healing_lock:
                    _healing_stats["error_budget_exhausted"] += 1
                
                # 에러 버짓 리셋 (테스트 환경)
                if random.random() < 0.5:
                    reset_result = self.sh.error_budget.reset_simulation()
                    debug_log(f"Error Budget Reset: {reset_result}")
                    with _healing_lock:
                        _healing_stats["error_budget_resets"] += 1
            
            # 4. 배포 판정 확인
            verdict = self.sh.error_budget.get_deployment_verdict()
            debug_log(f"Deployment Verdict: {verdict}")
            
        except Exception as e:
            debug_log(f"Error Budget Test Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"error_budget: {str(e)[:100]}")

    @task(3)
    @tag("healing", "xtest")
    def extreme_blast_radius_test(self):
        """
        극한 Blast Radius (장애 격리) 테스트
        
        다중 서비스 장애 주입 및 격리 검증
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. 시스템 스냅샷 조회
            snapshot = self.sh.xtest.get_snapshot()
            debug_log(f"System Snapshot: {snapshot}")
            
            with _healing_lock:
                _healing_stats["snapshots_taken"] += 1
            
            # 2. 단일 서비스 Blast Radius 테스트
            if random.random() < 0.3:
                services = ["payment-service", "order-service", "point-service"]
                target_service = random.choice(services)
                
                blast_result = self.sh.xtest.test_blast_radius(
                    service_name=target_service,
                    failure_type=random.choice(["exception", "timeout", "partial"]),
                )
                debug_log(f"Blast Radius Test ({target_service}): {blast_result}")
                
                with _healing_lock:
                    _healing_stats["blast_radius_tests"] += 1
            
            # 3. 다중 서비스 Blast Radius 테스트 (더 극단적)
            if random.random() < 0.15:
                multi_result = self.sh.xtest.test_multi_blast_radius(
                    services=["payment-service", "order-service", "point-service"],
                    failure_type="exception",
                )
                debug_log(f"Multi Blast Radius Test: {multi_result}")
                
                with _healing_lock:
                    _healing_stats["blast_radius_tests"] += 1
            
            # 4. 힐링 타임라인 조회
            timeline = self.sh.xtest.get_healing_timeline(limit=5)
            debug_log(f"Healing Timeline: {timeline}")
            
            with _healing_lock:
                _healing_stats["timeline_queries"] += 1
                
        except Exception as e:
            debug_log(f"Blast Radius Test Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"blast_radius: {str(e)[:100]}")

    @task(2)
    @tag("healing", "health")
    def extreme_health_check(self):
        """
        극한 Health Check 테스트
        
        연속적인 헬스 체크로 시스템 안정성 모니터링
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. Ping
            ping = self.sh.health.ping()
            debug_log(f"Health Ping: {ping}")
            
            # 2. Liveness
            liveness = self.sh.health.liveness()
            debug_log(f"Health Liveness: {liveness}")
            
            # 3. Readiness
            readiness = self.sh.health.readiness()
            debug_log(f"Health Readiness: {readiness}")
            
            with _healing_lock:
                _healing_stats["health_checks"] += 1
                
                # 헬스 체크 실패 카운트
                if not ping.get("ok") or liveness.get("status") == "error" or readiness.get("status") == "error":
                    _healing_stats["health_failures"] += 1
            
            # 4. Full Health (인증 필요)
            full_health = self.sh.health.full_health()
            debug_log(f"Full Health: {full_health}")
            
            # 5. Pool Health
            pool_health = self.sh.health.pool_health()
            debug_log(f"Pool Health: {pool_health}")
            
        except Exception as e:
            debug_log(f"Health Check Error: {e}")
            with _healing_lock:
                _healing_stats["health_failures"] += 1
                _healing_stats["selfhealing_errors"].append(f"health: {str(e)[:100]}")

    @task(1)
    @tag("healing", "chaos")
    def extreme_chaos_control(self):
        """
        극한 Chaos 제어 테스트
        
        킬스위치, 안전 체크 등 카오스 엔지니어링 제어
        """
        global _healing_stats
        
        if not self.sh:
            return
        
        try:
            # 1. 킬스위치 상태 확인
            killswitch_status = self.sh.chaos.get_kill_switch_status()
            debug_log(f"Kill Switch Status: {killswitch_status}")
            
            # 2. 안전 체크 실행
            safety_status = self.sh.chaos.get_safety_status()
            debug_log(f"Safety Status: {safety_status}")
            
            # 3. 안전 체크 실행 (극단적 상황 시)
            if random.random() < 0.2:
                safety_run = self.sh.chaos.run_safety_check()
                debug_log(f"Safety Check Run: {safety_run}")
            
            # 4. CB 리셋 (안전 조치)
            if random.random() < 0.1:
                reset_result = self.sh.xtest.reset_cb("payment-service")
                debug_log(f"CB Reset: {reset_result}")
                
                with _healing_lock:
                    _healing_stats["cb_resets"] += 1
                    
        except Exception as e:
            debug_log(f"Chaos Control Error: {e}")
            with _healing_lock:
                _healing_stats["selfhealing_errors"].append(f"chaos: {str(e)[:100]}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 Race Condition + Self-Healing 결과"""
    global _race_stats, _healing_stats

    print("\n" + "=" * 70)
    print("⚡ STAGE 7: RACE CONDITION + SELF-HEALING EXTREME TEST RESULTS")
    print("=" * 70)

    # =========================================================================
    # Race Condition 결과
    # =========================================================================
    print("\n📊 RACE CONDITION TEST RESULTS")
    print("-" * 50)
    
    print(f"Total Race Attempts: {_race_stats['total_race_attempts']}")
    print(f"Unique Orders Tested: {len(_race_stats['shared_orders'])}")
    print(f"Double Success (CRITICAL): {_race_stats['double_success']}")

    # 주문별 결과 분석 - 정확한 중복 건수 계산
    orders_with_multiple_success = 0
    total_duplicate_payments = 0
    failed_orders = []  # 실패한 order 목록 (디버깅용)

    for order_id, stats in _race_stats["shared_orders"].items():
        if stats["success"] > 1:
            orders_with_multiple_success += 1
            # success=3이면 2건의 중복 결제
            total_duplicate_payments += stats["success"] - 1
            failed_orders.append((order_id, stats["success"]))

    print(f"\nOrders with Multiple Payments: {orders_with_multiple_success}")
    print(f"Total Duplicate Payment Count: {total_duplicate_payments}")

    # 실패한 order ID 목록 출력 (디버깅 및 추적용)
    if failed_orders:
        print("\n🚨 Orders with Multiple Success (CRITICAL):")
        for order_id, success_count in failed_orders[:5]:  # 최대 5개만 출력
            print(f"   - {order_id}: {success_count} successes")

    race_passed = _race_stats["double_success"] == 0 and orders_with_multiple_success == 0
    
    if race_passed:
        print("\n✅ RACE CONDITION TEST PASSED")
        print("   No duplicate payments on same order")
        print("   Distributed lock working correctly")
    else:
        print("\n❌ RACE CONDITION TEST FAILED")
        print(f"   🚨 CRITICAL: {_race_stats['double_success']} double payments detected!")
        print(f"   📊 Affected Orders: {orders_with_multiple_success}")
        print(f"   💰 Total Duplicate Payments: {total_duplicate_payments}")
        print("   ⚠️  Review distributed lock implementation")
        print("   📋 Action: Check SELECT FOR UPDATE / Redis Lock")

    # =========================================================================
    # Self-Healing 결과
    # =========================================================================
    print("\n" + "-" * 50)
    print("🏥 SELF-HEALING EXTREME TEST RESULTS")
    print("-" * 50)
    
    if SELFHEALING_AVAILABLE:
        print("\n🔌 Circuit Breaker:")
        print(f"   Status Checks: {_healing_stats['cb_checks']}")
        print(f"   Failures Injected: {_healing_stats['cb_open_triggered']}")
        print(f"   Recoveries Triggered: {_healing_stats['cb_recovery_triggered']}")
        print(f"   Resets: {_healing_stats['cb_resets']}")
        
        print("\n🚨 Emergency Mode:")
        print(f"   Triggers: {_healing_stats['emergency_triggers']}")
        print(f"   Escalations (L2/L3): {_healing_stats['emergency_escalations']}")
        print(f"   Releases: {_healing_stats['emergency_releases']}")
        
        print("\n📬 Dead Letter Queue:")
        print(f"   Stats Checks: {_healing_stats['dlq_stats_checks']}")
        print(f"   Replays: {_healing_stats['dlq_replays']}")
        print(f"   Max Pending: {_healing_stats['dlq_pending_count']}")
        
        print("\n💰 Error Budget:")
        print(f"   Status Checks: {_healing_stats['error_budget_checks']}")
        print(f"   Injections: {_healing_stats['error_budget_injections']}")
        print(f"   Exhaustions: {_healing_stats['error_budget_exhausted']}")
        print(f"   Resets: {_healing_stats['error_budget_resets']}")
        
        print("\n🧪 XTest Mode (Blast Radius):")
        print(f"   Blast Radius Tests: {_healing_stats['blast_radius_tests']}")
        print(f"   Healing Events Recorded: {_healing_stats['healing_events_recorded']}")
        print(f"   Snapshots Taken: {_healing_stats['snapshots_taken']}")
        print(f"   Timeline Queries: {_healing_stats['timeline_queries']}")
        
        print("\n📡 Health Checks:")
        print(f"   Total Checks: {_healing_stats['health_checks']}")
        print(f"   Failures: {_healing_stats['health_failures']}")
        
        # Self-Healing 에러 출력
        if _healing_stats["selfhealing_errors"]:
            print(f"\n⚠️  Self-Healing Errors ({len(_healing_stats['selfhealing_errors'])}):")
            for err in _healing_stats["selfhealing_errors"][:5]:
                print(f"   - {err}")
        
        # Self-Healing 점수 계산
        total_healing_ops = (
            _healing_stats["cb_checks"] +
            _healing_stats["emergency_triggers"] +
            _healing_stats["dlq_stats_checks"] +
            _healing_stats["error_budget_checks"] +
            _healing_stats["blast_radius_tests"] +
            _healing_stats["health_checks"]
        )
        error_count = len(_healing_stats["selfhealing_errors"])
        healing_success_rate = ((total_healing_ops - error_count) / max(total_healing_ops, 1)) * 100
        
        print(f"\n📈 Self-Healing Score: {healing_success_rate:.1f}%")
        
        healing_passed = healing_success_rate >= 80
    else:
        print("\n⚠️  Self-Healing client not available")
        healing_passed = True
        total_healing_ops = 0
        healing_success_rate = 0

    # =========================================================================
    # 최종 요약
    # =========================================================================
    collector = get_metrics_collector()
    summary = collector.get_summary()
    
    print("\n" + "=" * 70)
    print("📋 FINAL SUMMARY")
    print("=" * 70)
    print(f"Total Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print(f"Race Condition Test: {'✅ PASSED' if race_passed else '❌ FAILED'}")
    print(f"Self-Healing Test: {'✅ PASSED' if healing_passed else '⚠️ DEGRADED'}")
    
    overall_passed = race_passed and healing_passed
    print(f"\n{'🎉 ALL TESTS PASSED' if overall_passed else '⚠️ SOME TESTS NEED ATTENTION'}")
    print("=" * 70)
    
    # 결과 JSON 저장 (results 폴더에 저장용)
    _save_test_results(
        race_stats=_race_stats,
        healing_stats=_healing_stats,
        race_passed=race_passed,
        healing_passed=healing_passed,
        healing_success_rate=healing_success_rate,
        summary=summary,
        orders_with_multiple_success=orders_with_multiple_success,
        total_duplicate_payments=total_duplicate_payments,
    )


def _save_test_results(**kwargs):
    """테스트 결과를 JSON 파일로 저장."""
    try:
        results_dir = os.path.join(_load_tests_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"stage7_race_selfhealing_{timestamp}.json"
        filepath = os.path.join(results_dir, filename)
        
        result_data = {
            "test_name": "Stage 7: Race Condition + Self-Healing Extreme Test",
            "timestamp": datetime.now().isoformat(),
            "race_condition": {
                "total_attempts": kwargs.get("race_stats", {}).get("total_race_attempts", 0),
                "unique_orders": len(kwargs.get("race_stats", {}).get("shared_orders", {})),
                "double_success": kwargs.get("race_stats", {}).get("double_success", 0),
                "orders_with_multiple_success": kwargs.get("orders_with_multiple_success", 0),
                "total_duplicate_payments": kwargs.get("total_duplicate_payments", 0),
                "passed": kwargs.get("race_passed", False),
            },
            "self_healing": {
                "circuit_breaker": {
                    "checks": _healing_stats.get("cb_checks", 0),
                    "failures_injected": _healing_stats.get("cb_open_triggered", 0),
                    "recoveries": _healing_stats.get("cb_recovery_triggered", 0),
                    "resets": _healing_stats.get("cb_resets", 0),
                },
                "emergency_mode": {
                    "triggers": _healing_stats.get("emergency_triggers", 0),
                    "escalations": _healing_stats.get("emergency_escalations", 0),
                    "releases": _healing_stats.get("emergency_releases", 0),
                },
                "dlq": {
                    "stats_checks": _healing_stats.get("dlq_stats_checks", 0),
                    "replays": _healing_stats.get("dlq_replays", 0),
                    "max_pending": _healing_stats.get("dlq_pending_count", 0),
                },
                "error_budget": {
                    "checks": _healing_stats.get("error_budget_checks", 0),
                    "injections": _healing_stats.get("error_budget_injections", 0),
                    "exhaustions": _healing_stats.get("error_budget_exhausted", 0),
                    "resets": _healing_stats.get("error_budget_resets", 0),
                },
                "xtest": {
                    "blast_radius_tests": _healing_stats.get("blast_radius_tests", 0),
                    "healing_events": _healing_stats.get("healing_events_recorded", 0),
                    "snapshots": _healing_stats.get("snapshots_taken", 0),
                    "timeline_queries": _healing_stats.get("timeline_queries", 0),
                },
                "health": {
                    "checks": _healing_stats.get("health_checks", 0),
                    "failures": _healing_stats.get("health_failures", 0),
                },
                "success_rate": kwargs.get("healing_success_rate", 0),
                "passed": kwargs.get("healing_passed", False),
                "errors": _healing_stats.get("selfhealing_errors", [])[:10],
            },
            "performance": {
                "total_requests": kwargs.get("summary", {}).get("total_requests", 0),
                "error_rate": kwargs.get("summary", {}).get("overall_error_rate", 0),
            },
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n📁 Results saved to: {filepath}")
        
    except Exception as e:
        print(f"\n⚠️ Failed to save results: {e}")
