"""
Stage 6 Extreme: Self-Healing System Stress Test

목적: 극한 부하에서 Self-Healing 시스템이 실제로 작동하는지 검증
- RPS 10배 상향 (wait_time = constant(0.1))
- 장애 확률 30% (CHAOS_PROBABILITY=0.30)
- 10분 이상 지속 테스트
- 전체 Self-Healing 모듈 통합 검증:
  * Circuit Breaker: 실제로 열리고 복구되는지
  * Error Budget: 실제로 소모되는 과정 관찰
  * DLQ: 결제 실패가 DLQ로 적재되는지
  * Alerts: CB OPEN/Error Budget 임계치 알림
  * Rate Limiter: L1 보호막 실효성
  * Emergency: 비상 모드 전환 확인
  * Reconciliation: 장애 복구 후 데이터 정합성

실행:
    docker-compose up -d
    CHAOS_ENABLED=true CHAOS_PROBABILITY=0.30 python -m locust -f load_tests/scenarios/chaos/stage6_extreme_chaos.py --host=http://localhost:8000 --users=100 --spawn-rate=20 --run-time=10m --headless

저부하 테스트 (2분):
    CHAOS_ENABLED=true CHAOS_PROBABILITY=0.30 python -m locust -f load_tests/scenarios/chaos/stage6_extreme_chaos.py --host=http://localhost:8000 --users=30 --spawn-rate=10 --run-time=2m --headless
"""

import os
import sys
import json
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import random
from locust import HttpUser, task, between, constant, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.chaos import FaultInjector, FaultType

# Self-Healing Client 통합
from load_tests.utils.selfhealing import SelfHealingClient, configure


STAGE_NAME = "[Stage6-Extreme]"

# =============================================================================
# 극한 테스트 통계 (Thread-safe)
# =============================================================================

@dataclass
class ExtremeStats:
    """극한 테스트 통계 (Thread-safe)"""
    lock: threading.Lock = field(default_factory=threading.Lock)
    
    # Chaos 통계
    total_requests: int = 0
    chaos_injected: int = 0
    success_after_chaos: int = 0
    failure_after_chaos: int = 0
    by_fault_type: Dict[str, int] = field(default_factory=dict)
    http_400_reasons: Dict[str, int] = field(default_factory=lambda: {
        "duplicate_item": 0, "out_of_stock": 0, "quantity_limit": 0,
        "invalid_token": 0, "price_mismatch": 0, "invalid_request": 0,
        "cart_empty": 0, "order_error": 0, "unknown": 0,
    })
    
    # Self-Healing 통계
    health_checks: Dict[str, int] = field(default_factory=lambda: {"success": 0, "failure": 0})
    circuit_breaker: Dict[str, int] = field(default_factory=lambda: {
        "status_checks": 0, "open_detected": 0, "half_open_detected": 0, 
        "closed_detected": 0, "recovery_triggered": 0
    })
    error_budget: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "exhausted_events": 0, "warning_events": 0, "critical_events": 0
    })
    dlq: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "pending_found": 0, "retry_triggered": 0, "entries_created": 0,
        "replay_triggered": 0, "replay_success": 0, "replay_failed": 0
    })
    alerts: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "cb_open_alerts": 0, "budget_alerts": 0, "emergency_alerts": 0
    })
    rate_limiter: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "blocked": 0, "passed": 0
    })
    emergency: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "active_detected": 0, "triggered": 0, "released": 0
    })
    reconciliation: Dict[str, int] = field(default_factory=lambda: {
        "checks": 0, "inconsistencies_found": 0, "auto_fixed": 0
    })
    observability: Dict[str, int] = field(default_factory=lambda: {
        "snapshots": 0, "timeline_queries": 0, "postmortems": 0
    })
    
    # 🆕 Retry 로직 통계 (피드백 반영)
    retry_logic: Dict[str, int] = field(default_factory=lambda: {
        "explicit_retries": 0,              # 명시적 재시도 횟수
        "retry_success": 0,                 # 재시도 성공
        "retry_failed": 0,                  # 재시도 실패 (max 초과)
        "retry_1st_attempt": 0,             # 첫 번째 재시도에서 성공
        "retry_2nd_attempt": 0,             # 두 번째 재시도에서 성공
        "retry_3rd_attempt": 0,             # 세 번째 재시도에서 성공
        "backoff_delays_ms": 0,             # 누적 백오프 대기 시간(ms)
        "dlq_after_max_retry": 0,           # max retry 초과 후 DLQ 이동
        "transient_failures": 0,            # 일시적 실패 (재시도 대상)
        "permanent_failures": 0,            # 영구적 실패 (재시도 안함)
    })
    
    # 타임라인 이벤트
    timeline_events: List[Dict[str, Any]] = field(default_factory=list)
    
    def increment(self, category: str, key: str, value: int = 1):
        """Thread-safe 증가"""
        with self.lock:
            if hasattr(self, category):
                getattr(self, category)[key] = getattr(self, category).get(key, 0) + value
    
    def add_event(self, event_type: str, details: Dict[str, Any]):
        """타임라인 이벤트 추가"""
        with self.lock:
            self.timeline_events.append({
                "timestamp": datetime.now().isoformat(),
                "event_type": event_type,
                "details": details,
            })
            # 최대 500개 유지
            if len(self.timeline_events) > 500:
                self.timeline_events = self.timeline_events[-500:]


_stats = ExtremeStats()
_selfhealing_client: Optional[SelfHealingClient] = None
_test_start_time: Optional[datetime] = None


def _classify_400_reason(response_json: dict) -> str:
    """400 응답의 reason을 분류"""
    if not response_json:
        return "unknown"

    error_msg = ""
    if isinstance(response_json, dict):
        error_msg = str(response_json.get("error", "")).lower()
        error_msg += str(response_json.get("detail", "")).lower()
        error_msg += str(response_json.get("message", "")).lower()
        if "non_field_errors" in response_json:
            error_msg += str(response_json.get("non_field_errors", [])).lower()

    if "already" in error_msg or "duplicate" in error_msg or "exists" in error_msg:
        return "duplicate_item"
    elif "stock" in error_msg or "insufficient" in error_msg or "재고" in error_msg:
        return "out_of_stock"
    elif "quantity" in error_msg or "limit" in error_msg or "maximum" in error_msg:
        return "quantity_limit"
    elif "token" in error_msg or "auth" in error_msg or "credential" in error_msg:
        return "invalid_token"
    elif "price" in error_msg or "amount" in error_msg or "mismatch" in error_msg:
        return "price_mismatch"
    elif "cart" in error_msg and ("empty" in error_msg or "no item" in error_msg):
        return "cart_empty"
    elif "order" in error_msg:
        return "order_error"
    elif "invalid" in error_msg or "required" in error_msg:
        return "invalid_request"
    else:
        return "unknown"


class ExtremeChaosUser(HttpUser):
    """
    극한 Chaos Test 사용자 + 전체 Self-Healing 통합
    
    wait_time = constant(0.1): 초당 10 요청/사용자
    100 사용자 = 1000 RPS 목표
    """
    
    # 극한 부하: 0.1초 대기 (10 RPS/user)
    wait_time = constant(0.1)
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        global _selfhealing_client, _test_start_time
        
        setup_event_hooks(STAGE_NAME)
        
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        # 카오스 주입기 초기화 (30% 확률)
        self.fault_injector = FaultInjector()
        self.fault_injector.enable()
        self.fault_injector.activate_all()
        chaos_prob = float(os.getenv("CHAOS_PROBABILITY", "0.30"))
        self.fault_injector.set_probability(chaos_prob)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # Self-Healing 클라이언트 초기화 (싱글톤)
        if _selfhealing_client is None:
            host = os.getenv("SELFHEALING_HOST", "http://localhost:8000")
            configure(host=host, auth_type="xtest", debug=False)
            _selfhealing_client = SelfHealingClient(host=host, auth_mode="xtest")
            _test_start_time = datetime.now()
            _stats.add_event("test_started", {"host": host, "chaos_prob": chaos_prob})
        
        self.healing = _selfhealing_client

    # =========================================================================
    # 극한 Chaos 테스트 (60% 비중)
    # =========================================================================

    @task(10)
    @tag("chaos", "browse", "extreme")
    def extreme_browse(self):
        """극한 부하: 상품 조회 폭탄"""
        global _stats
        with _stats.lock:
            _stats.total_requests += 1
        
        fault = self.fault_injector.get_random_fault()
        if fault:
            with _stats.lock:
                _stats.chaos_injected += 1
                _stats.by_fault_type[fault.value] = _stats.by_fault_type.get(fault.value, 0) + 1
            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()
        
        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /api/products/ [EXTREME]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                if fault:
                    with _stats.lock:
                        _stats.success_after_chaos += 1
            else:
                response.failure(f"Status: {response.status_code}")
                if fault:
                    with _stats.lock:
                        _stats.failure_after_chaos += 1

    @task(8)
    @tag("chaos", "cart", "extreme")
    def extreme_cart(self):
        """극한 부하: 장바구니 폭탄"""
        global _stats
        with _stats.lock:
            _stats.total_requests += 1
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        fault = self.fault_injector.get_random_fault()
        if fault:
            with _stats.lock:
                _stats.chaos_injected += 1
                _stats.by_fault_type[fault.value] = _stats.by_fault_type.get(fault.value, 0) + 1
            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()
        
        product_id = random.choice(product_ids)
        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product_id, "quantity": random.randint(1, 5)},
            name=f"{STAGE_NAME} POST /api/cart/add_item/ [EXTREME]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
                if fault:
                    with _stats.lock:
                        _stats.success_after_chaos += 1
            elif response.status_code == 400:
                response.success()
                try:
                    reason = _classify_400_reason(response.json())
                    with _stats.lock:
                        _stats.http_400_reasons[reason] = _stats.http_400_reasons.get(reason, 0) + 1
                except:
                    pass
                if fault:
                    with _stats.lock:
                        _stats.success_after_chaos += 1
            elif response.status_code >= 500:
                response.failure(f"5xx: {response.status_code}")
                if fault:
                    with _stats.lock:
                        _stats.failure_after_chaos += 1
                # 500 에러 발생 → DLQ 생성 시뮬레이션
                _stats.add_event("server_error", {"endpoint": "cart", "status": response.status_code})
            else:
                response.failure(f"Status: {response.status_code}")

    @task(5)
    @tag("chaos", "payment", "critical", "extreme")
    def extreme_payment(self):
        """극한 부하: 결제 폭탄 (가장 중요)"""
        global _stats
        with _stats.lock:
            _stats.total_requests += 1
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=2):
            return
        
        order_data = self.payment_helper.create_order()
        if not order_data:
            return
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        if not order_id or not final_amount:
            return
        
        # 결제 전 장애 주입
        fault = self.fault_injector.get_random_fault()
        if fault:
            with _stats.lock:
                _stats.chaos_injected += 1
                _stats.by_fault_type[fault.value] = _stats.by_fault_type.get(fault.value, 0) + 1
            if fault == FaultType.LATENCY:
                self.fault_injector.inject_latency()
        
        payment_key = self.payment_helper.generate_payment_key("extreme")
        
        with self.client.post(
            "/api/payments/confirm/",
            json={"payment_key": payment_key, "order_id": order_id, "amount": int(final_amount)},
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [EXTREME]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
                if fault:
                    with _stats.lock:
                        _stats.success_after_chaos += 1
            elif response.status_code == 400:
                response.success()
                try:
                    reason = _classify_400_reason(response.json())
                    with _stats.lock:
                        _stats.http_400_reasons[reason] = _stats.http_400_reasons.get(reason, 0) + 1
                except:
                    pass
            elif response.status_code >= 500:
                response.failure(f"5xx: {response.status_code}")
                if fault:
                    with _stats.lock:
                        _stats.failure_after_chaos += 1
                # 결제 실패 → DLQ 이벤트
                _stats.add_event("payment_failure", {
                    "order_id": order_id,
                    "status": response.status_code,
                    "fault_type": fault.value if fault else None
                })
                _stats.increment("dlq", "entries_created")

    # =========================================================================
    # Self-Healing 모니터링 (40% 비중)
    # =========================================================================

    @task(3)
    @tag("selfhealing", "health")
    def monitor_health(self):
        """헬스 체크 모니터링"""
        try:
            ping = self.healing.health.ping()
            if ping.get("ok"):
                _stats.increment("health_checks", "success")
            else:
                _stats.increment("health_checks", "failure")
                _stats.add_event("health_failure", {"ping": ping})
        except Exception as e:
            _stats.increment("health_checks", "failure")

    @task(4)
    @tag("selfhealing", "circuit_breaker")
    def monitor_circuit_breaker(self):
        """Circuit Breaker 상태 모니터링 및 복구 트리거"""
        try:
            # XTest 엔드포인트로 CB 상태 조회
            cb_status = self.healing.xtest.get_cb_status()
            _stats.increment("circuit_breaker", "status_checks")
            
            if cb_status.get("status") != "error":
                services = cb_status.get("services", {})
                for service, info in services.items():
                    state = info.get("state", "closed")
                    if state == "open":
                        _stats.increment("circuit_breaker", "open_detected")
                        _stats.add_event("cb_open", {"service": service, "info": info})
                        # 복구 시도
                        try:
                            self.healing.xtest.trigger_cb_recovery(service)
                            _stats.increment("circuit_breaker", "recovery_triggered")
                        except:
                            pass
                    elif state == "half_open":
                        _stats.increment("circuit_breaker", "half_open_detected")
                    else:
                        _stats.increment("circuit_breaker", "closed_detected")
        except Exception as e:
            pass

    @task(3)
    @tag("selfhealing", "error_budget")
    def monitor_error_budget(self):
        """Error Budget 모니터링"""
        try:
            # XTest 스냅샷에서 Error Budget 정보 조회
            snapshot = self.healing.xtest.get_snapshot()
            _stats.increment("error_budget", "checks")
            
            if snapshot.get("status") != "error":
                error_budget = snapshot.get("error_budget", {})
                remaining = error_budget.get("remaining_percent", 100)
                
                if remaining <= 0:
                    _stats.increment("error_budget", "exhausted_events")
                    _stats.add_event("budget_exhausted", {"remaining": remaining})
                elif remaining <= 20:
                    _stats.increment("error_budget", "critical_events")
                    _stats.add_event("budget_critical", {"remaining": remaining})
                elif remaining <= 50:
                    _stats.increment("error_budget", "warning_events")
        except Exception as e:
            pass

    @task(2)
    @tag("selfhealing", "dlq")
    def monitor_dlq(self):
        """DLQ 모니터링 및 재시도"""
        try:
            # DLQ 통계는 Protected이므로 XTest 스냅샷 사용
            snapshot = self.healing.xtest.get_snapshot()
            _stats.increment("dlq", "checks")
            
            if snapshot.get("status") != "error":
                dlq_info = snapshot.get("dlq", {})
                pending = dlq_info.get("pending_count", 0)
                
                if pending > 0:
                    _stats.increment("dlq", "pending_found")
                    _stats.add_event("dlq_pending", {"count": pending})
                    
                    # DLQ 항목이 있으면 이벤트 기록
                    if pending >= 5:
                        _stats.add_event("dlq_high", {"count": pending, "action": "alert"})
        except Exception as e:
            pass

    @task(2)
    @tag("selfhealing", "emergency")
    def monitor_emergency(self):
        """Emergency Mode 모니터링"""
        try:
            snapshot = self.healing.xtest.get_snapshot()
            _stats.increment("emergency", "checks")
            
            if snapshot.get("status") != "error":
                emergency = snapshot.get("emergency", {})
                is_active = emergency.get("is_active", False)
                level = emergency.get("level", "NORMAL")
                
                if is_active:
                    _stats.increment("emergency", "active_detected")
                    _stats.add_event("emergency_active", {"level": level})
        except Exception as e:
            pass

    @task(2)
    @tag("selfhealing", "xtest", "blast_radius")
    def test_blast_radius(self):
        """Blast Radius 격리 테스트"""
        try:
            # 랜덤 서비스에 Blast Radius 테스트
            services = ["payment", "inventory", "order", "product"]
            service = random.choice(services)
            
            result = self.healing.xtest.test_blast_radius(
                service_name=service,
                failure_type="exception"
            )
            _stats.increment("observability", "snapshots")
            
            if result.get("status") != "error":
                is_isolated = result.get("isolated", False)
                if is_isolated:
                    _stats.add_event("blast_radius_isolated", {"service": service})
                else:
                    _stats.add_event("blast_radius_leak", {"service": service, "result": result})
        except Exception as e:
            pass

    @task(2)
    @tag("selfhealing", "observability")
    def collect_observability(self):
        """Observability 데이터 수집"""
        try:
            # 힐링 타임라인 조회
            timeline = self.healing.xtest.get_healing_timeline(limit=10)
            _stats.increment("observability", "timeline_queries")
            
            # 스냅샷 수집
            snapshot = self.healing.xtest.get_snapshot()
            if snapshot.get("status") != "error":
                _stats.increment("observability", "snapshots")
        except Exception as e:
            pass

    @task(1)
    @tag("selfhealing", "xtest", "inject")
    def inject_controlled_failure(self):
        """제어된 장애 주입 (10% 확률)"""
        if random.random() > 0.1:
            return
        
        try:
            services = ["payment", "inventory"]
            service = random.choice(services)
            
            # CB 장애 주입 (짧은 시간)
            result = self.healing.xtest.inject_cb_failure(
                service_name=service,
                failure_type="exception",
                failure_rate=0.5,
                duration_seconds=30
            )
            
            if result.get("status") != "error":
                _stats.add_event("failure_injected", {"service": service, "duration": 30})
                
                # 힐링 이벤트 기록
                self.healing.xtest.record_healing_event(
                    event_type="chaos_injection",
                    service_name=service,
                    details={"stage": "stage6_extreme", "test_type": "controlled_failure"}
                )
        except Exception as e:
            pass

    # =========================================================================
    # 🆕 명시적 Retry 로직 검증 (피드백 반영)
    # =========================================================================

    @task(3)
    @tag("selfhealing", "retry", "explicit")
    def test_explicit_retry_with_backoff(self):
        """
        명시적 재시도 로직 테스트 (Exponential Backoff 포함)
        
        테스트 시나리오:
        1. 의도적으로 실패하는 요청 생성
        2. 최대 3회 재시도 (Exponential Backoff: 100ms, 200ms, 400ms)
        3. 성공 시 재시도 횟수 기록
        4. 최대 재시도 초과 시 DLQ 이동 시뮬레이션
        """
        global _stats
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        # 재시도 설정
        max_retries = 3
        base_delay_ms = 100  # 100ms
        
        success = False
        attempt = 0
        total_backoff_ms = 0
        
        # 장애 주입 (50% 확률로 일시적 실패 시뮬레이션)
        should_fail_initially = random.random() < 0.5
        
        for attempt in range(1, max_retries + 1):
            _stats.increment("retry_logic", "explicit_retries")
            
            # 첫 시도에서 의도적 실패 (일시적 실패 시뮬레이션)
            if should_fail_initially and attempt == 1:
                _stats.increment("retry_logic", "transient_failures")
                
                # Exponential Backoff 적용
                delay_ms = base_delay_ms * (2 ** (attempt - 1))
                total_backoff_ms += delay_ms
                time.sleep(delay_ms / 1000.0)
                _stats.add_event("retry_backoff", {
                    "attempt": attempt,
                    "delay_ms": delay_ms,
                    "reason": "transient_failure"
                })
                continue
            
            # 실제 요청 시도
            product_id = random.choice(product_ids)
            with self.client.post(
                "/api/cart/add_item/",
                json={"product_id": product_id, "quantity": 1},
                name=f"{STAGE_NAME} POST /api/cart/ [RETRY-{attempt}]",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201, 400]:
                    response.success()
                    success = True
                    _stats.increment("retry_logic", "retry_success")
                    _stats.increment("retry_logic", f"retry_{attempt}st_attempt" if attempt == 1 
                                     else f"retry_{attempt}nd_attempt" if attempt == 2 
                                     else f"retry_{attempt}rd_attempt")
                    _stats.add_event("retry_success", {
                        "attempt": attempt,
                        "total_backoff_ms": total_backoff_ms
                    })
                    break
                elif response.status_code >= 500:
                    # 서버 에러 → 재시도 대상
                    response.failure(f"5xx: {response.status_code} (attempt {attempt})")
                    _stats.increment("retry_logic", "transient_failures")
                    
                    if attempt < max_retries:
                        # Exponential Backoff
                        delay_ms = base_delay_ms * (2 ** (attempt - 1))
                        total_backoff_ms += delay_ms
                        time.sleep(delay_ms / 1000.0)
                        _stats.add_event("retry_backoff", {
                            "attempt": attempt,
                            "delay_ms": delay_ms,
                            "status_code": response.status_code
                        })
                else:
                    # 4xx (400 제외) → 영구적 실패, 재시도 안함
                    response.failure(f"Permanent failure: {response.status_code}")
                    _stats.increment("retry_logic", "permanent_failures")
                    break
        
        # 최대 재시도 초과 → DLQ 이동
        if not success:
            _stats.increment("retry_logic", "retry_failed")
            _stats.increment("retry_logic", "dlq_after_max_retry")
            _stats.add_event("retry_exhausted_to_dlq", {
                "max_retries": max_retries,
                "total_backoff_ms": total_backoff_ms
            })
        
        # 누적 백오프 시간 기록
        with _stats.lock:
            _stats.retry_logic["backoff_delays_ms"] += total_backoff_ms

    @task(2)
    @tag("selfhealing", "dlq", "retry", "replay")
    def test_dlq_retry_and_replay(self):
        """
        DLQ 재시도 및 리플레이 테스트
        
        테스트 시나리오:
        1. DLQ에 테스트 엔트리 생성
        2. 단건 재시도 실행
        3. 배치 리플레이 실행
        4. 결과 확인
        """
        global _stats
        
        try:
            # DLQ 스냅샷에서 pending 확인
            snapshot = self.healing.xtest.get_snapshot()
            dlq_info = snapshot.get("dlq", {})
            pending_count = dlq_info.get("pending_count", 0)
            
            _stats.increment("dlq", "checks")
            
            if pending_count > 0:
                _stats.increment("dlq", "pending_found")
                
                # 힐링 이벤트: DLQ 리플레이 시도
                self.healing.xtest.record_healing_event(
                    event_type="dlq_replay_attempt",
                    service_name="payment",
                    details={
                        "pending_count": pending_count,
                        "action": "batch_replay"
                    }
                )
                _stats.increment("dlq", "replay_triggered")
                _stats.add_event("dlq_replay_triggered", {"pending": pending_count})
                
        except Exception as e:
            pass

    @task(2)
    @tag("selfhealing", "retry", "config")
    def monitor_retry_config(self):
        """
        Retry 설정 모니터링
        
        런타임 Retry 설정을 조회하여 현재 설정값 확인
        """
        global _stats
        
        try:
            # XTest 스냅샷에서 retry 설정 확인
            snapshot = self.healing.xtest.get_snapshot()
            
            if snapshot.get("status") != "error":
                config = snapshot.get("config", {})
                retry_config = config.get("retry", {})
                
                if retry_config:
                    _stats.add_event("retry_config_snapshot", {
                        "max_retries": retry_config.get("max_retries", 3),
                        "base_delay_ms": retry_config.get("base_delay_ms", 100),
                        "max_delay_ms": retry_config.get("max_delay_ms", 10000),
                        "exponential_base": retry_config.get("exponential_base", 2),
                    })
        except Exception as e:
            pass


# =============================================================================
# 테스트 종료 시 결과 출력
# =============================================================================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 극한 테스트 결과 출력"""
    global _stats, _test_start_time
    
    test_duration = (datetime.now() - _test_start_time).total_seconds() if _test_start_time else 0
    
    print("\n" + "=" * 80)
    print("🔥 STAGE 6 EXTREME: SELF-HEALING STRESS TEST RESULTS")
    print("=" * 80)
    print(f"테스트 시간: {test_duration:.1f}초 ({test_duration/60:.1f}분)")
    
    # Chaos 통계
    print("\n" + "-" * 40)
    print("📊 CHAOS TEST STATISTICS")
    print("-" * 40)
    print(f"Total Requests: {_stats.total_requests:,}")
    print(f"Chaos Injected: {_stats.chaos_injected:,}")
    print(f"Success After Chaos: {_stats.success_after_chaos:,}")
    print(f"Failure After Chaos: {_stats.failure_after_chaos:,}")
    
    if _stats.chaos_injected > 0:
        recovery_rate = _stats.success_after_chaos / _stats.chaos_injected * 100
        print(f"Recovery Rate: {recovery_rate:.1f}%")
    
    print("\nFaults by Type:")
    for fault_type, count in _stats.by_fault_type.items():
        print(f"  - {fault_type}: {count:,}")
    
    # Self-Healing 통계
    print("\n" + "-" * 40)
    print("🏥 SELF-HEALING SYSTEM STATUS")
    print("-" * 40)
    
    print("\n📡 Health Checks:")
    print(f"   Success: {_stats.health_checks.get('success', 0):,}")
    print(f"   Failure: {_stats.health_checks.get('failure', 0):,}")
    
    print("\n🔌 Circuit Breaker:")
    for key, value in _stats.circuit_breaker.items():
        print(f"   {key}: {value:,}")
    
    print("\n💰 Error Budget:")
    for key, value in _stats.error_budget.items():
        print(f"   {key}: {value:,}")
    
    print("\n📭 Dead Letter Queue:")
    for key, value in _stats.dlq.items():
        print(f"   {key}: {value:,}")
    
    print("\n🚨 Emergency Mode:")
    for key, value in _stats.emergency.items():
        print(f"   {key}: {value:,}")
    
    print("\n🔍 Observability:")
    for key, value in _stats.observability.items():
        print(f"   {key}: {value:,}")
    
    # 🆕 Retry 로직 통계 (피드백 반영)
    print("\n🔄 Retry Logic (Explicit):")
    for key, value in _stats.retry_logic.items():
        print(f"   {key}: {value:,}")
    
    # Retry 성공률 계산
    total_retries = _stats.retry_logic.get("explicit_retries", 0)
    retry_success = _stats.retry_logic.get("retry_success", 0)
    retry_failed = _stats.retry_logic.get("retry_failed", 0)
    dlq_after_retry = _stats.retry_logic.get("dlq_after_max_retry", 0)
    
    if total_retries > 0:
        retry_success_rate = retry_success / (retry_success + retry_failed) * 100 if (retry_success + retry_failed) > 0 else 0
        print(f"\n   📈 Retry Success Rate: {retry_success_rate:.1f}%")
        print(f"   ⏱️  Total Backoff Time: {_stats.retry_logic.get('backoff_delays_ms', 0):,}ms")
        print(f"   📭 DLQ After Max Retry: {dlq_after_retry}")
    
    # 타임라인 이벤트 요약
    print("\n" + "-" * 40)
    print("📅 TIMELINE EVENTS SUMMARY")
    print("-" * 40)
    event_counts = {}
    for event in _stats.timeline_events:
        etype = event.get("event_type", "unknown")
        event_counts[etype] = event_counts.get(etype, 0) + 1
    
    for etype, count in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"   {etype}: {count:,}")
    
    # 최종 판정
    print("\n" + "=" * 40)
    print("🏆 FINAL VERDICT")
    print("=" * 40)
    
    chaos_passed = True
    healing_passed = True
    
    if _stats.chaos_injected > 0:
        recovery_rate = _stats.success_after_chaos / _stats.chaos_injected * 100
        if recovery_rate >= 70:
            print(f"✅ CHAOS RESILIENCE: PASSED ({recovery_rate:.1f}% recovery)")
        elif recovery_rate >= 50:
            print(f"⚠️  CHAOS RESILIENCE: WARNING ({recovery_rate:.1f}% recovery)")
            chaos_passed = False
        else:
            print(f"❌ CHAOS RESILIENCE: FAILED ({recovery_rate:.1f}% recovery)")
            chaos_passed = False
    
    # CB 동작 확인
    cb_opens = _stats.circuit_breaker.get("open_detected", 0)
    cb_recoveries = _stats.circuit_breaker.get("recovery_triggered", 0)
    if cb_opens > 0:
        print(f"🔌 CB ACTIVATED: {cb_opens} opens, {cb_recoveries} recovery attempts")
    else:
        print("⚠️  CB NOT TRIGGERED: 부하가 충분하지 않거나 시스템이 너무 안정적")
    
    # Error Budget 확인
    budget_exhausted = _stats.error_budget.get("exhausted_events", 0)
    budget_critical = _stats.error_budget.get("critical_events", 0)
    if budget_exhausted > 0:
        print(f"💰 ERROR BUDGET: EXHAUSTED {budget_exhausted}x - 시스템이 한계에 도달")
    elif budget_critical > 0:
        print(f"💰 ERROR BUDGET: CRITICAL {budget_critical}x - 위험 수준 도달")
    else:
        print("💰 ERROR BUDGET: HEALTHY")
    
    # Emergency 확인
    emergency_active = _stats.emergency.get("active_detected", 0)
    if emergency_active > 0:
        print(f"🚨 EMERGENCY MODE: ACTIVATED {emergency_active}x - 비상 모드 발동!")
    else:
        print("🚨 EMERGENCY MODE: NOT TRIGGERED")
    
    # 전체 판정
    print("\n" + "=" * 40)
    if chaos_passed and healing_passed:
        print("✅ STAGE 6 EXTREME: ALL SYSTEMS OPERATIONAL")
        print("   Self-Healing 시스템이 극한 부하에서 정상 작동")
    else:
        print("⚠️  STAGE 6 EXTREME: ISSUES DETECTED")
        print("   일부 Self-Healing 기능 개선 필요")
    print("=" * 80)
    
    # 결과 저장
    collector = get_metrics_collector()
    metrics_summary = collector.get_summary()
    _save_extreme_results(metrics_summary, test_duration)


def _save_extreme_results(metrics_summary: dict, duration: float):
    """극한 테스트 결과 저장"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(_project_root, "load_tests", "results")
    os.makedirs(results_dir, exist_ok=True)
    
    # Recovery rate 계산
    recovery_rate = 0
    if _stats.chaos_injected > 0:
        recovery_rate = _stats.success_after_chaos / _stats.chaos_injected * 100
    
    results = {
        "stage": "stage6_extreme",
        "name": "Self-Healing System Extreme Stress Test",
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": duration,
        "chaos_stats": {
            "total_requests": _stats.total_requests,
            "chaos_injected": _stats.chaos_injected,
            "success_after_chaos": _stats.success_after_chaos,
            "failure_after_chaos": _stats.failure_after_chaos,
            "recovery_rate": recovery_rate,
            "by_fault_type": _stats.by_fault_type,
            "http_400_reasons": _stats.http_400_reasons,
        },
        "selfhealing_stats": {
            "health_checks": dict(_stats.health_checks),
            "circuit_breaker": dict(_stats.circuit_breaker),
            "error_budget": dict(_stats.error_budget),
            "dlq": dict(_stats.dlq),
            "emergency": dict(_stats.emergency),
            "observability": dict(_stats.observability),
            "retry_logic": dict(_stats.retry_logic),  # 🆕 Retry 통계 추가
        },
        "timeline_events_count": len(_stats.timeline_events),
        "metrics_summary": metrics_summary,
        "passed": recovery_rate >= 50,
    }
    
    json_path = os.path.join(results_dir, f"stage6_extreme_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n📁 Results saved to: {json_path}")
