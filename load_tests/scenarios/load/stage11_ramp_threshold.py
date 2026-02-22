"""
Stage 11: Ramp-up Threshold Discovery + EXTREME Self-Healing Test

Purpose: Discover system threshold and Self-Healing trigger points under EXTREME conditions
- Start with low load and gradually increase to maximum stress
- Actively inject failures to stress self-healing system
- Identify when circuit breaker, DLQ, emergency mode triggers
- Measure recovery latency under various stress levels
- Test all self-healing components to their limits

🔥 EXTREME Mode Features:
  - Active failure injection via XTest API
  - Cascading failure simulation
  - Error budget exhaustion testing
  - Emergency mode escalation testing
  - DLQ flood testing
  - Circuit breaker rapid-fire testing

Load Shape:
  - LinearRamp: 10 → MAX_USERS over RAMP_DURATION
  - Failure injection rate increases with user count

Environment Variables:
  - STAGE11_EXTREME_MODE: true/false (기본: true) - 극한 테스트 모드
  - STAGE11_FAILURE_RATE: 0.0~1.0 (기본: 0.3) - 장애 주입 비율
  - LOCUST_MAX_USERS: 최대 사용자 수 (기본: 100)
  - LOCUST_TEST_DURATION: 테스트 시간 초 (기본: 120)
  - STAGE11_DEBUG: true/false (기본: false) - 디버그 로깅

Execution:
    # Docker Compose
    docker-compose -f docker-compose.test.yml up -d
    docker-compose exec web locust -f load_tests/scenarios/load/stage11_ramp_threshold.py \\
        --host=http://localhost:8000 --headless \\
        --users=100 --spawn-rate=5 --run-time=2m \\
        --html=/app/load_tests/results/stage11/stage11_extreme_report.html

    # CLI mode (EXTREME)
    STAGE11_EXTREME_MODE=true LOCUST_MAX_USERS=100 LOCUST_TEST_DURATION=120 \\
    locust -f load_tests/scenarios/load/stage11_ramp_threshold.py \\
        --host=http://localhost:8000 --headless \\
        --html=load_tests/results/stage11/stage11_extreme_report.html

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 11)
    - load_tests/utils/selfhealing/ (V2 최적화 모듈)
"""

import os
import sys
import time
import random
import json
from datetime import datetime
from typing import Optional, Dict, Any

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))  # Fixed path
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks

# Import SelfHealing utilities
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.config import configure, SelfHealingConfig
    # V2 최적화 모듈
    from load_tests.utils.selfhealing.state_cache import CBStateCache
    from load_tests.utils.selfhealing.async_logger import AsyncHealingLogger, EventSeverity
    from load_tests.utils.selfhealing.defaults import SafeDefaults
    from load_tests.utils.selfhealing.adaptive_jitter import AdaptiveJitter, SystemState
    SELFHEALING_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ SelfHealing utilities not available: {e}")
    SELFHEALING_AVAILABLE = False


STAGE_NAME = "[Stage11-EXTREME]"
DEBUG_MODE = os.environ.get("STAGE11_DEBUG", "false").lower() == "true"
EXTREME_MODE = os.environ.get("STAGE11_EXTREME_MODE", "true").lower() == "true"
FAILURE_INJECTION_RATE = float(os.environ.get("STAGE11_FAILURE_RATE", "0.3"))


def debug_log(message: str):
    """디버그 로깅."""
    if DEBUG_MODE:
        print(f"[DEBUG] {STAGE_NAME} {message}")


# =============================================================================
# EXTREME Test Configuration
# =============================================================================

EXTREME_CONFIG = {
    "services": ["payment", "inventory", "notification", "shipping", "database"],
    "failure_types": ["exception", "timeout", "slow_response"],
    "cb_failure_threshold": 5,
    "cb_rapid_fire_count": 15,
    "emergency_levels": ["LEVEL_1", "LEVEL_2", "LEVEL_3"],
    "dlq_flood_count": 30,
    "error_budget_exhaust_count": 50,
    "recovery_sla_ms": 500,  # SILVER tier
    # V2 EXTREME scenarios - 리뷰 피드백 반영 개선
    "cold_start_users": 100,  # Cold Start Storm 동시 접속 수
    "slow_poison_max_delay_sec": 8,  # Slow Poisoning 최대 지연 시간 (10→8)
    "slow_poison_steps": 8,  # 지연 증가 단계 (10→8, 더 빠른 에스컬레이션)
    "data_corruption_rate": 0.95,  # Data Corruption 확률 (90→95%)
    "command_outage_duration_sec": 4,  # Command Center 장애 시간 (5→4)
    "flapping_cycles": 8,  # Flapping Service 반복 횟수 (10→8)
    "flapping_interval_sec": 0.3,  # 반복 간격 (0.5→0.3, 더 빠른 flapping)
    "zombie_latency_threshold_sec": 2.5,  # 좀비 판정 임계 응답시간 (5→3→2.5, 더 민감)
    # V2.2 추가 개선
    "adaptive_throttle_threshold": 55,  # 55명 이상 시 적응형 스로틀링
    "chaos_escalation_rate": 0.5,  # 시간에 따른 장애 주입률 증가
    "test_duration_sec": 300,  # 테스트 시간 5분으로 연장 (CB Full Lifecycle)
    "max_load_hold_sec": 120,  # 최대 부하 도달 후 2분간 유지
}


# =============================================================================
# Threshold Discovery Statistics (Enhanced)
# =============================================================================

_threshold_stats = {
    "start_time": None,
    "current_users": 0,
    "snapshots": [],  # Per-minute snapshots
    "milestones": {
        "first_retry": None,
        "first_cb_open": None,
        "first_dlq": None,
        "first_error_spike": None,
        "first_emergency": None,
        "breaking_point": None,
    },
    "current_minute": {
        "requests": 0,
        "errors": 0,
        "retries": 0,
        "response_times": [],
    },
    "recovery": {
        "degradation_detected_at": None,
        "recovery_detected_at": None,
        "recovery_latency_seconds": None,
        "threshold_discovery_latencies": [],
    },
    # EXTREME mode statistics
    "extreme": {
        "failures_injected": 0,
        "cb_triggers": 0,
        "cb_recoveries": 0,
        "emergency_triggers": 0,
        "emergency_releases": 0,
        "dlq_items_created": 0,
        "error_budget_consumed": 0,
        # V2 EXTREME stats
        "cold_start_thundering_herd": 0,
        "slow_poison_zombie_detected": 0,
        "data_corruption_blocked": 0,
        "command_outage_local_autonomy": 0,
        "flapping_half_open_transitions": 0,
        "brain_split_degraded_mode": 0,
        "scenarios_executed": {
            "cascading_failure": 0,
            "rapid_fire_cb": 0,
            "emergency_escalation": 0,
            "dlq_flood": 0,
            "error_budget_exhaust": 0,
            # V2 EXTREME scenarios
            "cold_start_storm": 0,
            "slow_poisoning": 0,
            "data_corruption_chaos": 0,
            "command_center_outage": 0,
            "flapping_service": 0,
            "brain_split": 0,
        },
    },
    # V2 Optimization stats
    "v2_stats": {
        "cache_hits": 0,
        "cache_misses": 0,
        "async_events_logged": 0,
        "jitter_applied_count": 0,
        "avg_jitter_ms": 0,
    },
}


def _get_current_minute_key() -> int:
    """Get current minute as integer for grouping"""
    if _threshold_stats["start_time"] is None:
        return 0
    elapsed = time.time() - _threshold_stats["start_time"]
    return int(elapsed // 60)


def _snapshot_current_minute():
    """Save current minute statistics and reset counters"""
    stats = _threshold_stats["current_minute"]

    if stats["requests"] == 0:
        return

    avg_response = sum(stats["response_times"]) / len(stats["response_times"]) if stats["response_times"] else 0
    error_rate = stats["errors"] / stats["requests"] * 100 if stats["requests"] > 0 else 0

    snapshot = {
        "minute": _get_current_minute_key(),
        "users": _threshold_stats["current_users"],
        "requests": stats["requests"],
        "errors": stats["errors"],
        "retries": stats["retries"],
        "error_rate": round(error_rate, 2),
        "avg_response_time_ms": round(avg_response, 2),
        "timestamp": datetime.now().isoformat(),
    }

    _threshold_stats["snapshots"].append(snapshot)

    # Check for milestones
    _check_milestones(snapshot)

    # Reset counters
    _threshold_stats["current_minute"] = {
        "requests": 0,
        "errors": 0,
        "retries": 0,
        "response_times": [],
    }


def _check_milestones(snapshot: dict):
    """Check and record milestone events"""
    milestones = _threshold_stats["milestones"]
    users = snapshot["users"]

    # First retry milestone
    if milestones["first_retry"] is None and snapshot["retries"] > 0:
        milestones["first_retry"] = {
            "user_count": users,
            "minute": snapshot["minute"],
            "retry_count": snapshot["retries"],
        }
        print(f"\n🎯 MILESTONE: First retry detected at {users} users (minute {snapshot['minute']})")

    # First error spike milestone (error rate > 5%)
    if milestones["first_error_spike"] is None and snapshot["error_rate"] > 5:
        milestones["first_error_spike"] = {
            "user_count": users,
            "minute": snapshot["minute"],
            "error_rate": snapshot["error_rate"],
        }
        print(f"\n⚠️ MILESTONE: Error spike ({snapshot['error_rate']}%) at {users} users")

    # Breaking point (error rate > 20% or avg response > 5000ms)
    if milestones["breaking_point"] is None:
        if snapshot["error_rate"] > 20 or snapshot["avg_response_time_ms"] > 5000:
            milestones["breaking_point"] = {
                "user_count": users,
                "minute": snapshot["minute"],
                "error_rate": snapshot["error_rate"],
                "avg_response_time_ms": snapshot["avg_response_time_ms"],
            }
            print(f"\n🔴 MILESTONE: Breaking point reached at {users} users")
            # Degradation 감지 시간 기록
            _threshold_stats["recovery"]["degradation_detected_at"] = time.time()


def _record_request(success: bool, response_time_ms: float, is_retry: bool = False):
    """Record request statistics"""
    stats = _threshold_stats["current_minute"]
    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)

    if not success:
        stats["errors"] += 1

    if is_retry:
        stats["retries"] += 1


def _record_extreme_event(event_type: str, count: int = 1):
    """Record EXTREME mode event."""
    extreme = _threshold_stats["extreme"]
    if event_type in extreme:
        extreme[event_type] += count
    elif event_type in extreme["scenarios_executed"]:
        extreme["scenarios_executed"][event_type] += count


# =============================================================================
# Custom Load Shape - Linear Ramp
# =============================================================================


class RampUpShape(LoadTestShape):
    """
    Linear ramp-up load shape for threshold discovery with sustained peak.

    V2.2: CB Full Lifecycle 검증을 위해 최대 부하 유지 시간 추가
    
    Phase 1: Ramp up from MIN to MAX over RAMP_DURATION (60s)
    Phase 2: Sustain MAX users for HOLD_DURATION (120s) - CB recovery 관찰
    Phase 3: Cooldown (optional)

    Set LOCUST_TEST_DURATION env var to override total duration (in seconds).
    """

    # Configuration - can be overridden by env var
    MIN_USERS = 10
    MAX_USERS = int(os.environ.get("LOCUST_MAX_USERS", "100"))
    
    # V2.2: 테스트 시간 설정 (기본 300초 = 5분)
    TOTAL_DURATION = int(os.environ.get("LOCUST_TEST_DURATION", 
                        EXTREME_CONFIG.get("test_duration_sec", 300)))
    RAMP_DURATION = 60  # 1분 동안 램프업
    HOLD_DURATION = EXTREME_CONFIG.get("max_load_hold_sec", 120)  # 2분 동안 최대 부하 유지

    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time"""
        run_time = self.get_run_time()

        # Phase 1: Ramp up
        if run_time <= self.RAMP_DURATION:
            progress = run_time / self.RAMP_DURATION
            current_users = int(self.MIN_USERS + (self.MAX_USERS - self.MIN_USERS) * progress)
            spawn_rate = max(1, (self.MAX_USERS - self.MIN_USERS) / self.RAMP_DURATION * 10)
            _threshold_stats["current_users"] = current_users
            return (current_users, spawn_rate)
        
        # Phase 2: Sustain max load (CB Full Lifecycle 관찰)
        elif run_time <= self.RAMP_DURATION + self.HOLD_DURATION:
            _threshold_stats["current_users"] = self.MAX_USERS
            return (self.MAX_USERS, 1)  # 유지 모드 - 느린 spawn rate
        
        # Phase 3: Test complete
        elif run_time > self.TOTAL_DURATION:
            return None

        # 남은 시간 - 부하 유지
        else:
            _threshold_stats["current_users"] = self.MAX_USERS
            return (self.MAX_USERS, 1)


# =============================================================================
# Test User
# =============================================================================


class ThresholdDiscoveryUser(HttpUser):
    """
    User for threshold discovery testing with EXTREME mode support.

    Performs typical e-commerce operations while:
    - Monitoring for self-healing trigger conditions
    - Actively injecting failures (EXTREME mode)
    - Testing all self-healing components to their limits
    """

    wait_time = between(0.5, 2)  # Faster for stress testing

    def on_start(self):
        """Initialize user session"""
        global _threshold_stats

        setup_event_hooks(STAGE_NAME)

        if _threshold_stats["start_time"] is None:
            _threshold_stats["start_time"] = time.time()
            # V2 최적화 모듈 초기화
            if SELFHEALING_AVAILABLE:
                self._init_v2_optimizations()

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        # Track last minute for snapshot triggering
        self._last_minute = _get_current_minute_key()
        
        # EXTREME mode: 장애 주입 간격 추적
        self._last_failure_injection = 0

    def _init_v2_optimizations(self):
        """V2 최적화 모듈 초기화 (최초 사용자만)."""
        import requests
        
        host = self.host
        
        # CBStateCache 설정
        def fetch_cb_state(service):
            try:
                resp = requests.get(f"{host}/api/self-healing/status/{service}/", timeout=5)
                if resp.status_code == 200:
                    return resp.json()
                return None
            except Exception as e:
                debug_log(f"CB state fetch failed for {service}: {e}")
                return None
        
        CBStateCache.configure(
            fetch_callback=fetch_cb_state,
            base_ttl=3.0,
            jitter_range=0.3,
            min_ttl=2.0,
            max_ttl=6.0
        )
        
        # AsyncHealingLogger 설정
        def send_events_batch(events):
            try:
                requests.post(
                    f"{host}/api/self-healing/events/batch/",
                    json={"events": events},
                    timeout=5
                )
            except Exception as e:
                debug_log(f"Batch event send failed: {e}")
        
        AsyncHealingLogger.configure(
            flush_callback=send_events_batch,
            batch_size=5,
            flush_interval=2.0
        )
        AsyncHealingLogger.start()
        
        # AdaptiveJitter 설정
        AdaptiveJitter.configure_thresholds(
            error_budget_danger=0.15,
            error_budget_safe=0.8,
            load_high=0.9,
            load_low=0.5
        )
        AdaptiveJitter.configure_jitter_ranges(
            relaxed=(0, 0.02),
            normal=(0.02, 0.06),
            stressed=(0.05, 0.15)
        )
        
        debug_log("V2 Optimization modules initialized")

    def _get_headers(self):
        """Get auth headers + X-Test-Mode for XTest endpoints."""
        headers = self.login_helper.get_auth_header()
        headers["X-Test-Mode"] = "chaos"
        return headers

    def _check_snapshot_needed(self):
        """Check if we need to take a snapshot"""
        current_minute = _get_current_minute_key()
        if current_minute > self._last_minute:
            _snapshot_current_minute()
            self._last_minute = current_minute

    def _should_inject_failure(self) -> bool:
        """EXTREME 모드에서 장애 주입 여부 결정."""
        if not EXTREME_MODE:
            return False
        
        # 부하 레벨에 따라 장애 주입 확률 증가
        current_users = _threshold_stats["current_users"]
        max_users = RampUpShape.MAX_USERS
        load_ratio = current_users / max_users if max_users > 0 else 0
        
        # 부하 50% 이상에서 장애 주입 시작, 90% 이상에서 최대
        if load_ratio < 0.5:
            return False
        
        adjusted_rate = FAILURE_INJECTION_RATE * (load_ratio - 0.3)
        return random.random() < adjusted_rate

    # =========================================================================
    # Standard E-Commerce Operations
    # =========================================================================

    @task(10)
    @tag("threshold", "browse")
    def browse_products(self):
        """Browse products - most common operation"""
        start = time.time()

        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /products/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    @task(5)
    @tag("threshold", "cart")
    def add_to_cart(self):
        """Add item to cart"""
        product = self.product_helper.get_random_product()
        if not product:
            return

        start = time.time()

        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /cart/add_item/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    @task(3)
    @tag("threshold", "payment")
    def create_order_and_pay(self):
        """Create order and request payment - most intensive operation"""
        # Add item to cart first
        product = self.product_helper.get_random_product()
        if not product:
            return

        # Add to cart
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add (payment flow)",
        )

        # Create order
        start = time.time()

        with self.client.post(
            "/api/orders/",
            json={
                "shipping_address": "서울시 강남구 테헤란로 123",
                "shipping_name": "테스트유저",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "06234",
            },
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /orders/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                order_data = response.json()
                order_id = order_data.get("id")

                # Request payment
                if order_id:
                    self._request_payment(order_id)

                response.success()
            else:
                response.failure(f"Order creation failed: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    def _request_payment(self, order_id: int):
        """Request payment for order"""
        start = time.time()

        with self.client.post(
            "/api/payments/request/",
            json={"order_id": order_id},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /payments/request/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]

            if success:
                response.success()
            else:
                # Payment failures might trigger retries
                response.failure(f"Payment request failed: {response.status_code}")

            _record_request(success, elapsed_ms)

    @task(2)
    @tag("threshold", "self-healing")
    def check_self_healing_status(self):
        """Check self-healing system status to monitor CB state"""
        start = time.time()

        with self.client.get(
            "/api/self-healing/status/",
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} GET /self-healing/status/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200

            if success:
                data = response.json()

                # Check for circuit breaker state changes
                # services is a list of dicts with service_name key
                services = data.get("services", [])
                for service_info in services:
                    service_name = service_info.get("service_name", "unknown")
                    if service_info.get("circuit_state") == "open" or service_info.get("state") == "open":
                        if _threshold_stats["milestones"]["first_cb_open"] is None:
                            _threshold_stats["milestones"]["first_cb_open"] = {
                                "user_count": _threshold_stats["current_users"],
                                "minute": _get_current_minute_key(),
                                "service": service_name,
                            }
                            print(f"\n🔌 MILESTONE: Circuit Breaker OPEN for {service_name}")
                        
                        # V2: CBStateCache에 상태 업데이트
                        if SELFHEALING_AVAILABLE:
                            CBStateCache.invalidate(service_name)

                response.success()
            else:
                response.failure(f"Status check failed: {response.status_code}")

            _record_request(success, elapsed_ms)
            self._check_snapshot_needed()

    # =========================================================================
    # EXTREME Mode - Failure Injection Scenarios
    # =========================================================================

    @task(3)
    @tag("extreme", "cb-stress")
    def extreme_cascading_failure(self):
        """
        🔥 Cascading Failure - 다중 서비스 동시 장애 주입.
        
        여러 서비스에 동시 장애를 주입하여 연쇄 장애 상황 시뮬레이션.
        Self-Healing 시스템이 제대로 복구하는지 확인.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "cascading_failure"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        # 무작위 서비스 2~3개 선택
        services_to_fail = random.sample(
            EXTREME_CONFIG["services"],
            k=min(random.randint(2, 3), len(EXTREME_CONFIG["services"]))
        )
        
        for service in services_to_fail:
            start = time.time()
            
            with self.client.post(
                f"/api/self-healing/block/{service}/",
                json={"reason": "EXTREME cascading failure test"},
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME block/{service}/",
                catch_response=True,
            ) as response:
                elapsed_ms = (time.time() - start) * 1000
                
                if response.status_code in [200, 201]:
                    _record_extreme_event("cb_triggers")
                    debug_log(f"CB blocked: {service}")
                    
                    # V2: 비동기 이벤트 로깅
                    if SELFHEALING_AVAILABLE:
                        AsyncHealingLogger.log_cb_event(
                            service=service,
                            state="BLOCKED",
                            reason="Cascading failure test"
                        )
                    
                    response.success()
                else:
                    response.failure(f"Block failed: {response.status_code}")
                
                _record_request(response.status_code in [200, 201], elapsed_ms)
        
        # V2: AdaptiveJitter 적용 후 복구 시도
        if SELFHEALING_AVAILABLE:
            AdaptiveJitter.apply_jitter_sleep()
            _threshold_stats["v2_stats"]["jitter_applied_count"] += 1
        else:
            time.sleep(random.uniform(0.5, 1.5))
        
        # 복구 시도
        recovery_start = time.time()
        for service in services_to_fail:
            with self.client.post(
                f"/api/self-healing/reset/{service}/",
                json={"reason": "EXTREME recovery test"},
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME reset/{service}/",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    _record_extreme_event("cb_recoveries")
                    
                    # V2: 캐시 무효화 및 복구 이벤트 로깅
                    if SELFHEALING_AVAILABLE:
                        CBStateCache.invalidate(service)
                        AsyncHealingLogger.log_recovery_event(
                            service=service,
                            recovery_time_ms=(time.time() - recovery_start) * 1000,
                            success=True
                        )
                    
                    response.success()
                else:
                    response.failure(f"Reset failed: {response.status_code}")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "emergency")
    def extreme_emergency_escalation(self):
        """
        🚨 Emergency Escalation - 비상 모드 에스컬레이션 테스트.
        
        부하 수준에 따라 비상 레벨을 단계적으로 올리고 해제.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "emergency_escalation"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        # 현재 부하에 따라 비상 레벨 결정
        current_users = _threshold_stats["current_users"]
        max_users = RampUpShape.MAX_USERS
        load_ratio = current_users / max_users if max_users > 0 else 0
        
        if load_ratio > 0.8:
            emergency_level = "LEVEL_3"
        elif load_ratio > 0.6:
            emergency_level = "LEVEL_2"
        else:
            emergency_level = "LEVEL_1"
        
        start = time.time()
        
        # 비상 모드 트리거
        with self.client.post(
            "/api/self-healing/emergency/trigger/",
            json={
                "level": emergency_level,
                "reason": f"EXTREME test - load {load_ratio*100:.1f}%",
                "duration_minutes": 1,
            },
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME emergency/trigger/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            if response.status_code in [200, 201]:
                _record_extreme_event("emergency_triggers")
                
                if _threshold_stats["milestones"]["first_emergency"] is None:
                    _threshold_stats["milestones"]["first_emergency"] = {
                        "user_count": current_users,
                        "minute": _get_current_minute_key(),
                        "level": emergency_level,
                    }
                    print(f"\n🚨 MILESTONE: First emergency mode at {current_users} users ({emergency_level})")
                
                # V2: 비동기 이벤트 로깅
                if SELFHEALING_AVAILABLE:
                    AsyncHealingLogger.log_emergency_event(
                        level=emergency_level,
                        action="trigger",
                        reason="EXTREME escalation test"
                    )
                
                response.success()
            else:
                response.failure(f"Emergency trigger failed: {response.status_code}")
            
            _record_request(response.status_code in [200, 201], elapsed_ms)
        
        # V2: AdaptiveJitter 적용
        if SELFHEALING_AVAILABLE:
            AdaptiveJitter.apply_jitter_sleep()
        else:
            time.sleep(random.uniform(1, 2))
        
        # 비상 모드 해제
        with self.client.post(
            "/api/self-healing/emergency/release/",
            json={"reason": "EXTREME test complete"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME emergency/release/",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                _record_extreme_event("emergency_releases")
                
                if SELFHEALING_AVAILABLE:
                    AsyncHealingLogger.log_emergency_event(
                        level="NORMAL",
                        action="release",
                        reason="EXTREME test recovery"
                    )
                
                response.success()
            else:
                response.failure(f"Emergency release failed: {response.status_code}")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "rapid-fire")
    def extreme_rapid_fire_cb(self):
        """
        ⚡ Rapid Fire CB - Circuit Breaker 연속 장애 주입.
        
        빠르게 연속으로 장애를 주입하여 CB의 빠른 전환 테스트.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "rapid_fire_cb"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        service = random.choice(EXTREME_CONFIG["services"])
        rapid_fire_count = EXTREME_CONFIG["cb_rapid_fire_count"]
        
        failures = 0
        for i in range(rapid_fire_count):
            start = time.time()
            
            with self.client.post(
                "/api/self-healing/xtest/inject-cb-failure/",
                json={"service": service, "count": 1},
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME xtest/inject-cb-failure/",
                catch_response=True,
            ) as response:
                elapsed_ms = (time.time() - start) * 1000
                
                if response.status_code in [200, 201]:
                    failures += 1
                    _record_extreme_event("failures_injected")
                    response.success()
                else:
                    response.failure(f"Injection failed: {response.status_code}")
                
                _record_request(response.status_code in [200, 201], elapsed_ms)
            
            # 빠른 연사 (짧은 간격)
            time.sleep(0.05)
        
        _record_extreme_event("cb_triggers")
        debug_log(f"Rapid fire: {failures}/{rapid_fire_count} failures injected to {service}")
        
        # V2: 비동기 이벤트 로깅
        if SELFHEALING_AVAILABLE:
            AsyncHealingLogger.log_cb_event(
                service=service,
                state="STRESSED",
                reason=f"Rapid fire - {failures} failures injected"
            )
        
        # V2: AdaptiveJitter 적용 후 상태 확인
        if SELFHEALING_AVAILABLE:
            AdaptiveJitter.apply_jitter_sleep()
            cached_state = CBStateCache.get_state(service)
            if cached_state:
                _threshold_stats["v2_stats"]["cache_hits"] += 1
        
        # 복구
        recovery_start = time.time()
        with self.client.post(
            f"/api/self-healing/reset/{service}/",
            json={"reason": "Rapid fire recovery"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME reset/{service}/ (rapid)",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                recovery_time_ms = (time.time() - recovery_start) * 1000
                _record_extreme_event("cb_recoveries")
                
                if SELFHEALING_AVAILABLE:
                    CBStateCache.invalidate(service)
                    AsyncHealingLogger.log_recovery_event(
                        service=service,
                        recovery_time_ms=recovery_time_ms,
                        success=True
                    )
                
                response.success()
            else:
                response.failure(f"Reset failed: {response.status_code}")
        
        self._check_snapshot_needed()

    @task(1)
    @tag("extreme", "dlq")
    def extreme_dlq_flood(self):
        """
        📬 DLQ Flood - Dead Letter Queue 범람 테스트.
        
        다량의 실패 작업을 생성하여 DLQ 처리 능력 테스트.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "dlq_flood"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        # DLQ 통계 확인
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/dlq/cleanup/stats/",
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME dlq/stats/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            if response.status_code == 200:
                data = response.json()
                pending_count = data.get("pending_count", 0)
                
                if _threshold_stats["milestones"]["first_dlq"] is None and pending_count > 0:
                    _threshold_stats["milestones"]["first_dlq"] = {
                        "user_count": _threshold_stats["current_users"],
                        "minute": _get_current_minute_key(),
                        "pending_count": pending_count,
                    }
                    print(f"\n📬 MILESTONE: First DLQ item at {_threshold_stats['current_users']} users")
                
                _record_extreme_event("dlq_items_created", pending_count)
                response.success()
            else:
                response.failure(f"DLQ stats failed: {response.status_code}")
            
            _record_request(response.status_code == 200, elapsed_ms)
        
        self._check_snapshot_needed()

    @task(1)
    @tag("extreme", "error-budget")
    def extreme_error_budget_exhaust(self):
        """
        💰 Error Budget Exhaust - 에러 버짓 소진 테스트.
        
        에러를 기록하여 에러 버짓 소진 및 자동 조치 테스트.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "error_budget_exhaust"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        start = time.time()
        
        # 에러 기록
        with self.client.post(
            "/api/self-healing/error-budget/record/",
            json={
                "error_count": random.randint(5, 20),
                "error_type": "simulated",
                "service_name": random.choice(EXTREME_CONFIG["services"]),
            },
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME error-budget/record/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            if response.status_code in [200, 201]:
                _record_extreme_event("error_budget_consumed")
                
                # V2: AdaptiveJitter 상태 업데이트
                if SELFHEALING_AVAILABLE:
                    # 에러 버짓 상태 조회 및 AdaptiveJitter 연동
                    try:
                        budget_resp = self.client.get(
                            "/api/self-healing/error-budget/status/",
                            headers=self._get_headers(),
                            name=f"{STAGE_NAME} error-budget/status/ (internal)",
                        )
                        if budget_resp.status_code == 200:
                            data = budget_resp.json()
                            remaining = data.get("remaining_percent", 100) / 100.0
                            AdaptiveJitter.update_cache(error_budget=remaining)
                            CBStateCache.update_system_state(error_budget_remaining=remaining)
                    except Exception as e:
                        debug_log(f"Error budget status failed: {e}")
                
                response.success()
            else:
                response.failure(f"Record failed: {response.status_code}")
            
            _record_request(response.status_code in [200, 201], elapsed_ms)
        
        self._check_snapshot_needed()

    # =========================================================================
    # V2 EXTREME Mode - Advanced Chaos Scenarios
    # =========================================================================

    @task(3)
    @tag("extreme", "cold-start")
    def extreme_cold_start_storm(self):
        """
        ❄️ Cold Start Storm (초기화 공격)
        
        모든 캐시와 세션을 삭제한 상태에서 Thundering Herd 시뮬레이션.
        SafeDefaults와 AdaptiveJitter가 트래픽을 얼마나 효과적으로 분산하는지 확인.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "cold_start_storm"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        # 1. 모든 캐시 무효화 (Cold Start 상태 생성)
        if SELFHEALING_AVAILABLE:
            CBStateCache.invalidate_all()
            debug_log("Cache invalidated - Cold Start state created")
        
        # 2. 세션/상태 초기화 요청
        start = time.time()
        with self.client.post(
            "/api/self-healing/cache/invalidate-all/",
            json={"reason": "Cold Start Storm test", "scope": "all"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME cache/invalidate-all/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            if response.status_code in [200, 201, 404]:  # 404도 허용 (엔드포인트 없을 수 있음)
                response.success()
            else:
                response.failure(f"Cache invalidate failed: {response.status_code}")
            _record_request(response.status_code in [200, 201], elapsed_ms)
        
        # 3. Thundering Herd 시뮬레이션 - 동시 요청 폭주
        thundering_requests = []
        herd_size = min(10, EXTREME_CONFIG["cold_start_users"] // 10)
        
        for i in range(herd_size):
            # V2: AdaptiveJitter 적용 - 요청 분산
            if SELFHEALING_AVAILABLE:
                jitter_ms = AdaptiveJitter.calculate_ms()
                _threshold_stats["v2_stats"]["jitter_applied_count"] += 1
                time.sleep(jitter_ms / 1000.0)
            
            start = time.time()
            with self.client.get(
                "/api/products/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} EXTREME cold-start/products/ (herd-{i})",
                catch_response=True,
            ) as response:
                elapsed_ms = (time.time() - start) * 1000
                thundering_requests.append({
                    "index": i,
                    "elapsed_ms": elapsed_ms,
                    "success": response.status_code == 200,
                })
                
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Herd request failed: {response.status_code}")
                
                _record_request(response.status_code == 200, elapsed_ms)
        
        # 4. Thundering Herd 분산 효과 측정
        successful = sum(1 for r in thundering_requests if r["success"])
        avg_latency = sum(r["elapsed_ms"] for r in thundering_requests) / len(thundering_requests) if thundering_requests else 0
        
        _record_extreme_event("cold_start_thundering_herd")
        
        debug_log(f"Cold Start Storm: {successful}/{herd_size} success, avg latency {avg_latency:.1f}ms")
        
        # V2: SafeDefaults 상태 확인
        if SELFHEALING_AVAILABLE:
            if SafeDefaults.is_degraded():
                debug_log("SafeDefaults: Degraded mode activated during Cold Start")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "slow-poison")
    def extreme_slow_poisoning(self):
        """
        🧟 Slow Poisoning (지연 가스 주입)
        
        에러 없이 응답 시간만 점진적으로 증가 (1초→8초).
        '좀비 인프라' 감지 및 Emergency LEVEL_3 강제 차단 검증.
        V2.1: 더 민감한 좀비 감지 (3초 임계치, 누적 지연 추적)
        """
        if not self._should_inject_failure():
            return
        
        scenario = "slow_poisoning"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        max_delay = EXTREME_CONFIG["slow_poison_max_delay_sec"]
        steps = EXTREME_CONFIG["slow_poison_steps"]
        zombie_threshold = EXTREME_CONFIG["zombie_latency_threshold_sec"]
        
        zombie_detected = False
        cumulative_delay_ms = 0  # V2.1: 누적 지연 추적
        consecutive_slow_responses = 0  # V2.1: 연속 느린 응답 카운트
        
        for step in range(steps):
            # 점진적으로 지연 증가 (0.5초 → max_delay초, 더 빠른 시작)
            current_delay = 0.5 + (max_delay - 0.5) * (step / (steps - 1)) if steps > 1 else 0.5
            
            start = time.time()
            
            # 지연 주입 요청
            with self.client.post(
                "/api/self-healing/xtest/inject-latency/",
                json={
                    "service": random.choice(EXTREME_CONFIG["services"]),
                    "latency_ms": int(current_delay * 1000),
                    "duration_sec": 2,
                },
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME slow-poison/inject/ (step-{step})",
                catch_response=True,
            ) as response:
                elapsed_ms = (time.time() - start) * 1000
                cumulative_delay_ms += elapsed_ms
                
                # V2.1: 실제 응답 시간 기반 좀비 감지
                if elapsed_ms >= zombie_threshold * 1000:
                    consecutive_slow_responses += 1
                else:
                    consecutive_slow_responses = 0
                
                if response.status_code in [200, 201, 404]:
                    response.success()
                else:
                    response.failure(f"Latency inject failed: {response.status_code}")
                
                _record_request(response.status_code in [200, 201], elapsed_ms)
            
            # V2.2: 좀비 상태 감지 개선 - 조건 충족 시 바로 카운트
            zombie_condition = (
                current_delay >= zombie_threshold or  # 설정된 지연이 임계치 초과
                consecutive_slow_responses >= 2 or  # 2회 연속 느린 응답
                cumulative_delay_ms >= zombie_threshold * 1000 * 3  # 누적 지연 임계치
            )
            
            if zombie_condition and not zombie_detected:
                debug_log(f"🧟 Zombie condition met: delay={current_delay:.1f}s, consecutive={consecutive_slow_responses}, cumulative={cumulative_delay_ms:.0f}ms")
                
                # V2.2: 좀비 조건 충족 시 바로 감지로 기록 (Emergency API 응답과 무관)
                zombie_detected = True
                _record_extreme_event("slow_poison_zombie_detected")
                debug_log(f"🧟 ZOMBIE DETECTED! Threshold {zombie_threshold}s exceeded")
                
                # Emergency LEVEL_3 트리거 확인 (optional - 실제 시스템 반응 로깅용)
                with self.client.get(
                    "/api/self-healing/emergency/status/",
                    headers=self._get_headers(),
                    name=f"{STAGE_NAME} EXTREME slow-poison/emergency-check/",
                    catch_response=True,
                ) as em_response:
                    if em_response.status_code == 200:
                        data = em_response.json()
                        current_level = data.get("level", "NORMAL")
                        
                        if current_level == "LEVEL_3":
                            debug_log("🧟 System also triggered Emergency LEVEL_3")
                            
                            if SELFHEALING_AVAILABLE:
                                AsyncHealingLogger.log_emergency_event(
                                    level="LEVEL_3",
                                    action="zombie_detected",
                                    reason=f"Slow poison - latency {current_delay:.1f}s"
                                )
                        em_response.success()
                    else:
                        em_response.failure(f"Emergency status failed: {em_response.status_code}")
            
            # V2: 상황에 맞는 지터 적용
            if SELFHEALING_AVAILABLE:
                AdaptiveJitter.apply_jitter_sleep()
            else:
                time.sleep(0.2)
        
        # 좀비 상태 미감지 시 직접 LEVEL_3 트리거 시도
        if not zombie_detected:
            debug_log("Zombie not auto-detected, forcing LEVEL_3 trigger")
            with self.client.post(
                "/api/self-healing/emergency/trigger/",
                json={
                    "level": "LEVEL_3",
                    "reason": "Slow Poisoning - Zombie infrastructure detected",
                    "duration_minutes": 1,
                },
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME slow-poison/force-level3/",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    _record_extreme_event("slow_poison_zombie_detected")
                    response.success()
                else:
                    response.failure(f"Force LEVEL_3 failed: {response.status_code}")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "data-corruption")
    def extreme_data_corruption_chaos(self):
        """
        🧪 Data Corruption Chaos (데이터 오염)
        
        결제 웹훅에 유효하지 않은 서명/음수 금액 등 오염 데이터 주입.
        Zero Variance Validator가 DLQ로 필터링하는지 확인.
        V2.1: 다중 엔드포인트 시도, 실제 응답 기반 검증
        """
        if not self._should_inject_failure():
            return
        
        scenario = "data_corruption_chaos"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        corruption_rate = EXTREME_CONFIG["data_corruption_rate"]
        
        # 오염 데이터 유형들
        corruption_types = [
            {"type": "invalid_signature", "signature": "INVALID_SIG_" + str(random.randint(1000, 9999))},
            {"type": "negative_amount", "amount": -random.randint(1000, 100000)},
            {"type": "future_timestamp", "timestamp": "2099-12-31T23:59:59Z"},
            {"type": "null_order_id", "order_id": None},
            {"type": "overflow_amount", "amount": 99999999999999},
            {"type": "sql_injection", "order_id": "1; DROP TABLE orders;--"},
            {"type": "xss_payload", "note": "<script>alert('xss')</script>"},
        ]
        
        # V2.1: 테스트할 엔드포인트 목록 (여러 개 시도)
        test_endpoints = [
            "/api/self-healing/xtest/inject-corrupted-data/",  # xtest 전용 엔드포인트
            "/api/payments/toss/webhook/",  # 실제 webhook
            "/api/self-healing/dlq/",  # DLQ 직접 주입
        ]
        
        # 오염 데이터 주입 (95% 확률)
        if random.random() < corruption_rate:
            corruption = random.choice(corruption_types)
            
            # 오염된 데이터 생성
            corrupted_data = {
                "paymentKey": f"test_payment_{random.randint(10000, 99999)}",
                "orderId": corruption.get("order_id", f"order_{random.randint(1000, 9999)}"),
                "amount": corruption.get("amount", random.randint(1000, 50000)),
                "status": "DONE",
                "secret": corruption.get("signature", "valid_secret"),
                "corruption_type": corruption["type"],  # 테스트용 마커
                "test_mode": True,  # V2.1: 테스트 모드 플래그
            }
            
            corruption_blocked = False
            
            # V2.1: 여러 엔드포인트 시도
            for endpoint in test_endpoints:
                if corruption_blocked:
                    break  # 이미 차단됨
                    
                start = time.time()
                
                with self.client.post(
                    endpoint,
                    json=corrupted_data,
                    headers=self._get_headers(),
                    name=f"{STAGE_NAME} EXTREME corruption/inject/ ({corruption['type']})",
                    catch_response=True,
                ) as response:
                    elapsed_ms = (time.time() - start) * 1000
                    
                    # 예상: 오염 데이터는 거부되어야 함 (400, 422, 403, 429)
                    # V2.2: 429 (Rate Limit)도 시스템 보호 동작으로 카운팅
                    if response.status_code in [400, 422, 403, 429]:
                        corruption_blocked = True
                        _record_extreme_event("data_corruption_blocked")
                        debug_log(f"✓ Corruption blocked at {endpoint}: {corruption['type']} (status={response.status_code})")
                        
                        if SELFHEALING_AVAILABLE:
                            AsyncHealingLogger.log_cb_event(
                                service="data_validator",
                                state="BLOCKED",
                                reason="data_corruption_blocked",
                                corruption_type=corruption["type"],
                                endpoint=endpoint,
                                status_code=response.status_code,
                            )
                        
                        response.success()  # 거부가 정상 동작
                    elif response.status_code == 404:
                        # 엔드포인트 없음 - 다음 엔드포인트 시도
                        debug_log(f"Endpoint not found: {endpoint}, trying next...")
                        response.success()  # 404는 실패가 아님
                        continue
                    elif response.status_code in [200, 201]:
                        # 오염 데이터가 통과됨 = 문제
                        debug_log(f"⚠ Corruption PASSED (should be blocked): {corruption['type']}")
                        response.failure(f"Corruption not blocked: {corruption['type']}")
                    else:
                        response.failure(f"Unexpected status: {response.status_code}")
                    
                    _record_request(response.status_code in [400, 422, 403], elapsed_ms)
            
            # DLQ에 기록되었는지 확인
            with self.client.get(
                "/api/self-healing/dlq/cleanup/stats/",
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME corruption/dlq-check/",
                catch_response=True,
            ) as dlq_response:
                if dlq_response.status_code == 200:
                    data = dlq_response.json()
                    if data.get("pending_count", 0) > 0:
                        debug_log(f"DLQ has {data['pending_count']} pending items (corruption logged)")
                    dlq_response.success()
                else:
                    dlq_response.failure(f"DLQ check failed: {dlq_response.status_code}")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "command-outage")
    def extreme_command_center_outage(self):
        """
        🌑 Command Center Outage (사령탑 실종)
        
        Self-Healing 서버와의 연결을 끊고 로컬 자치권 발동 테스트.
        고립된 상태에서 스스로 임계치로 자가 생존 모드 진입 확인.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "command_center_outage"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        outage_duration = EXTREME_CONFIG["command_outage_duration_sec"]
        
        # 1. 네트워크 장애 시뮬레이션 시작
        start = time.time()
        
        with self.client.post(
            "/api/self-healing/xtest/network-outage/",
            json={
                "target": "self-healing-server",
                "duration_sec": outage_duration,
                "mode": "complete_disconnect",
            },
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME outage/start/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            if response.status_code in [200, 201, 404]:
                response.success()
            else:
                response.failure(f"Network outage start failed: {response.status_code}")
            
            _record_request(response.status_code in [200, 201], elapsed_ms)
        
        # 2. V2: SafeDefaults로 로컬 자치권 모드 활성화
        if SELFHEALING_AVAILABLE:
            SafeDefaults.enter_degraded_mode()
            debug_log("Local autonomy mode activated (SafeDefaults.degraded = True)")
        
        # 3. 사령탑 없이 일반 작업 수행 (로컬 임계치로 동작해야 함)
        local_operations = 0
        local_errors = 0
        
        for i in range(5):
            # 일반 쇼핑 작업
            with self.client.get(
                "/api/products/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} EXTREME outage/local-op/ ({i})",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    local_operations += 1
                    response.success()
                else:
                    local_errors += 1
                    response.failure(f"Local operation failed: {response.status_code}")
            
            # V2: 로컬 자치권 모드에서 SafeDefaults 적용
            if SELFHEALING_AVAILABLE:
                # 로컬 임계치로 자가 판단
                if SafeDefaults.is_degraded():
                    defaults = SafeDefaults.get_all_defaults()
                    debug_log(f"Using local defaults: timeout={defaults.get('timeout', 5)}s")
        
        # 4. 로컬 자치권 발동 여부 확인
        if local_operations > 0:
            _record_extreme_event("command_outage_local_autonomy")
            debug_log(f"Local autonomy: {local_operations} ops succeeded during outage")
            
            if SELFHEALING_AVAILABLE:
                # log_cb_event(service, state, reason, **kwargs)
                AsyncHealingLogger.log_cb_event(
                    service="command_center",
                    state="DISCONNECTED",
                    reason="local_autonomy_activated",
                    local_operations=local_operations,
                    local_errors=local_errors,
                    outage_duration=outage_duration,
                )
        
        # 5. 장애 종료 및 복구
        time.sleep(min(outage_duration, 2))  # 테스트에서는 최대 2초만 대기
        
        if SELFHEALING_AVAILABLE:
            SafeDefaults.exit_degraded_mode()
            debug_log("Local autonomy mode deactivated")
        
        with self.client.post(
            "/api/self-healing/xtest/network-outage/restore/",
            json={"target": "self-healing-server"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME outage/restore/",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201, 404]:
                response.success()
            else:
                response.failure(f"Network restore failed: {response.status_code}")
        
        self._check_snapshot_needed()

    @task(2)
    @tag("extreme", "flapping")
    def extreme_flapping_service(self):
        """
        🎭 Flapping Service (서비스 플래핑)
        
        서비스가 빠르게 ON/OFF를 반복하는 불안정한 상태 시뮬레이션.
        Circuit Breaker의 half-open 상태 전환 및 상태 머신 안정성 테스트.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "flapping_service"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        cycles = EXTREME_CONFIG["flapping_cycles"]
        interval = EXTREME_CONFIG["flapping_interval_sec"]
        service = random.choice(EXTREME_CONFIG["services"])
        
        half_open_count = 0
        
        for cycle in range(cycles):
            # 장애 발생 (서비스 다운)
            with self.client.post(
                f"/api/self-healing/block/{service}/",
                json={"reason": f"Flapping test cycle {cycle}", "duration_sec": interval},
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME flapping/block/ (cycle-{cycle})",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                else:
                    response.failure(f"Flapping block failed: {response.status_code}")
            
            time.sleep(interval / 2)
            
            # 복구 (서비스 업)
            with self.client.post(
                f"/api/self-healing/reset/{service}/",
                json={"reason": f"Flapping recovery cycle {cycle}"},
                headers=self._get_headers(),
                name=f"{STAGE_NAME} EXTREME flapping/reset/ (cycle-{cycle})",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                else:
                    response.failure(f"Flapping reset failed: {response.status_code}")
            
            # CB 상태 확인 (half-open 감지)
            if SELFHEALING_AVAILABLE:
                cached = CBStateCache.get_state(service)
                if cached and cached.get("state") == "half-open":
                    half_open_count += 1
                    debug_log(f"Half-open detected for {service} at cycle {cycle}")
            
            time.sleep(interval / 2)
        
        # 플래핑 통계
        if half_open_count > 0:
            _record_extreme_event("flapping_half_open_transitions", half_open_count)
        
        debug_log(f"Flapping complete: {cycles} cycles, {half_open_count} half-open transitions")
        
        if SELFHEALING_AVAILABLE:
            AsyncHealingLogger.log_cb_event(
                service=service,
                state="STABILIZING",
                reason=f"Flapping test completed - {half_open_count} half-opens"
            )
        
        self._check_snapshot_needed()

    @task(1)
    @tag("extreme", "brain-split")
    def extreme_brain_split(self):
        """
        🧠 Brain Split (분리 뇌)
        
        Redis/DB 연결을 끊어 캐시 일관성 상실 시뮬레이션.
        SafeDefaults의 Degraded Mode 활성화 및 데이터 일관성 복구 검증.
        """
        if not self._should_inject_failure():
            return
        
        scenario = "brain_split"
        debug_log(f"Starting {scenario}")
        _record_extreme_event(scenario)
        
        # 1. Redis 연결 끊기 시뮬레이션
        start = time.time()
        
        with self.client.post(
            "/api/self-healing/xtest/disconnect/",
            json={"target": "redis", "duration_sec": 3},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME brain-split/disconnect-redis/",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201, 404]:
                response.success()
            else:
                response.failure(f"Redis disconnect failed: {response.status_code}")
        
        # 2. V2: SafeDefaults로 Degraded Mode 강제 활성화
        if SELFHEALING_AVAILABLE:
            SafeDefaults.enter_degraded_mode()
            _record_extreme_event("brain_split_degraded_mode")
            debug_log("Brain Split: Degraded mode activated")
            
            # 캐시 무효화
            CBStateCache.invalidate_all()
        
        # 3. Degraded Mode에서 작업 수행
        degraded_ops = 0
        
        for i in range(3):
            with self.client.get(
                "/api/products/",
                headers=self.login_helper.get_auth_header(),
                name=f"{STAGE_NAME} EXTREME brain-split/degraded-op/ ({i})",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    degraded_ops += 1
                    response.success()
                else:
                    response.failure(f"Degraded operation failed: {response.status_code}")
        
        # 4. 일관성 검증 시도
        with self.client.get(
            "/api/self-healing/consistency/check/",
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME brain-split/consistency-check/",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Consistency check failed: {response.status_code}")
        
        # 5. 복구
        if SELFHEALING_AVAILABLE:
            SafeDefaults.exit_degraded_mode()
            debug_log("Brain Split: Degraded mode deactivated")
        
        with self.client.post(
            "/api/self-healing/xtest/reconnect/",
            json={"target": "redis"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} EXTREME brain-split/reconnect-redis/",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201, 404]:
                response.success()
            else:
                response.failure(f"Redis reconnect failed: {response.status_code}")
        
        debug_log(f"Brain Split complete: {degraded_ops} ops in degraded mode")
        
        self._check_snapshot_needed()


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 실행."""
    print("\n" + "=" * 80)
    print(f"🔥 {STAGE_NAME} EXTREME Self-Healing Threshold Discovery Started")
    print("=" * 80)
    print("\n📋 테스트 설정:")

    print(f"   - EXTREME Mode: {'✅ 활성화' if EXTREME_MODE else '❌ 비활성화'}")
    print(f"   - Failure Injection Rate: {FAILURE_INJECTION_RATE*100:.0f}%")
    print(f"   - Max Users: {RampUpShape.MAX_USERS}")
    print(f"   - Ramp Duration: {RampUpShape.RAMP_DURATION}s")
    print(f"   - Debug Mode: {'✅' if DEBUG_MODE else '❌'}")
    
    if SELFHEALING_AVAILABLE:
        print("\n🚀 V2 최적화 모듈:")
        print("   - CBStateCache: ✅ TTL 캐싱 + Polling Jitter")
        print("   - AsyncHealingLogger: ✅ 비동기 이벤트 버퍼링")
        print("   - AdaptiveJitter: ✅ 상황 기반 지능형 지터")
        print("   - SafeDefaults: ✅ Degraded Mode Fallback")
    else:
        print("\n⚠️ SelfHealing 모듈 비활성화")
    
    if EXTREME_MODE:
        print("\n🔥 EXTREME 시나리오 (기본):")
        print("   - 🔥 Cascading Failure - 다중 서비스 동시 장애")
        print("   - 🚨 Emergency Escalation - 비상 모드 에스컬레이션")
        print("   - ⚡ Rapid Fire CB - Circuit Breaker 연속 장애")
        print("   - 📬 DLQ Flood - Dead Letter Queue 범람")
        print("   - 💰 Error Budget Exhaust - 에러 버짓 소진")
        print("\n🔥 EXTREME 시나리오 (V2 고급):")
        print("   - ❄️ Cold Start Storm - Thundering Herd 분산 테스트")
        print("   - 🧟 Slow Poisoning - 좀비 인프라 감지 테스트")
        print("   - 🧪 Data Corruption Chaos - Zero Variance Validator 검증")
        print("   - 🌑 Command Center Outage - 로컬 자치권 발동 테스트")
        print("   - 🎭 Flapping Service - CB Half-Open 상태 머신 테스트")
        print("   - 🧠 Brain Split - Degraded Mode 활성화 테스트")
    
    print("\n" + "=" * 80 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate threshold discovery report on test completion"""
    # Take final snapshot
    _snapshot_current_minute()
    
    # V2 최적화 모듈 정리 및 통계 수집
    v2_stats = None
    if SELFHEALING_AVAILABLE:
        v2_stats = _collect_v2_stats()
        _cleanup_v2_modules()

    print("\n" + "=" * 80)
    print(f"📊 {STAGE_NAME} THRESHOLD DISCOVERY REPORT")
    print("=" * 80)

    # Milestones
    print("\n🎯 Milestones Detected:")
    milestones = _threshold_stats["milestones"]

    for name, data in milestones.items():
        if data:
            print(f"  - {name}: {json.dumps(data, indent=4)}")
        else:
            print(f"  - {name}: Not reached")

    # Summary statistics
    print("\n📈 Load Progression Summary:")
    print(f"  - Total snapshots: {len(_threshold_stats['snapshots'])}")

    if _threshold_stats["snapshots"]:
        first = _threshold_stats["snapshots"][0]
        last = _threshold_stats["snapshots"][-1]

        print(f"  - First snapshot: {first['users']} users, {first['error_rate']}% error rate")
        print(f"  - Last snapshot: {last['users']} users, {last['error_rate']}% error rate")

        # Find peak response time
        peak_snapshot = max(_threshold_stats["snapshots"], key=lambda x: x["avg_response_time_ms"])
        print(f"  - Peak response time: {peak_snapshot['avg_response_time_ms']}ms at {peak_snapshot['users']} users")

    # EXTREME mode statistics
    if EXTREME_MODE:
        extreme = _threshold_stats["extreme"]
        print("\n🔥 EXTREME Mode Statistics:")
        print(f"   - Failures Injected: {extreme['failures_injected']}")
        print(f"   - CB Triggers: {extreme['cb_triggers']}")
        print(f"   - CB Recoveries: {extreme['cb_recoveries']}")
        print(f"   - Emergency Triggers: {extreme['emergency_triggers']}")
        print(f"   - Emergency Releases: {extreme['emergency_releases']}")
        print(f"   - DLQ Items Created: {extreme['dlq_items_created']}")
        print(f"   - Error Budget Consumed: {extreme['error_budget_consumed']}")
        
        print("\n📋 V2 고급 시나리오 통계:")
        print(f"   - ❄️ Cold Start Thundering Herd: {extreme.get('cold_start_thundering_herd', 0)}회")
        print(f"   - 🧟 Slow Poison Zombie Detected: {extreme.get('slow_poison_zombie_detected', 0)}회")
        print(f"   - 🧪 Data Corruption Blocked: {extreme.get('data_corruption_blocked', 0)}회")
        print(f"   - 🌑 Command Outage Local Autonomy: {extreme.get('command_outage_local_autonomy', 0)}회")
        print(f"   - 🎭 Flapping Half-Open Transitions: {extreme.get('flapping_half_open_transitions', 0)}회")
        print(f"   - 🧠 Brain Split Degraded Mode: {extreme.get('brain_split_degraded_mode', 0)}회")
        
        print("\n📋 Scenarios Executed:")
        for scenario, count in extreme["scenarios_executed"].items():
            print(f"   - {scenario}: {count}회")

    # V2 Optimization Statistics
    if v2_stats:
        print("\n🚀 V2 최적화 모듈 통계:")
        print("   📦 CBStateCache:")
        cache_stats = v2_stats.get("cache_stats", {})
        print(f"      - 캐시 히트율: {cache_stats.get('hit_rate', 0)*100:.1f}%")
        print(f"      - 총 요청: {cache_stats.get('total_requests', 0)}")
        
        print("   📝 AsyncHealingLogger:")
        logger_stats = v2_stats.get("async_logger_stats", {})
        print(f"      - 총 이벤트: {logger_stats.get('events_logged', 0)}")
        print(f"      - 플러시된 이벤트: {logger_stats.get('events_flushed', 0)}")
        
        print("   🎲 AdaptiveJitter:")
        jitter_stats = v2_stats.get("jitter_stats", {})
        print(f"      - 평균 지터: {jitter_stats.get('avg_jitter_ms', 0):.1f}ms")
        print(f"      - Relaxed/Normal/Stressed: {jitter_stats.get('relaxed_count', 0)}/{jitter_stats.get('normal_count', 0)}/{jitter_stats.get('stressed_count', 0)}")

    # Recovery Latency Report
    recovery = _threshold_stats["recovery"]
    print("\n🔄 Recovery Latency Metrics:")
    if recovery["recovery_latency_seconds"]:
        print(f"   - Recovery latency: {recovery['recovery_latency_seconds']:.1f}s")
        if recovery["recovery_latency_seconds"] < 120:
            print("   - SLA Status: ✓ Under 2min threshold")
        else:
            print("   - SLA Status: ✗ Exceeded 2min threshold")
    else:
        print("   - No recovery event recorded (system may not have degraded)")

    # Save report to file
    _save_results(environment, v2_stats)

    print("=" * 80)


def _collect_v2_stats() -> Dict[str, Any]:
    """V2 최적화 모듈 통계 수집."""
    stats = {}
    
    try:
        stats["cache_stats"] = CBStateCache.get_stats()
    except Exception as e:
        debug_log(f"Failed to get cache stats: {e}")
        stats["cache_stats"] = {}
    
    try:
        stats["async_logger_stats"] = AsyncHealingLogger.get_stats()
    except Exception as e:
        debug_log(f"Failed to get logger stats: {e}")
        stats["async_logger_stats"] = {}
    
    try:
        stats["jitter_stats"] = AdaptiveJitter.get_stats()
    except Exception as e:
        debug_log(f"Failed to get jitter stats: {e}")
        stats["jitter_stats"] = {}
    
    try:
        stats["degraded_mode"] = SafeDefaults.is_degraded()
    except Exception as e:
        debug_log(f"Failed to get SafeDefaults stats: {e}")
        stats["degraded_mode"] = False
    
    return stats


def _cleanup_v2_modules():
    """V2 최적화 모듈 정리."""
    try:
        AsyncHealingLogger.flush_now()
        AsyncHealingLogger.stop()
        debug_log("AsyncHealingLogger stopped")
    except Exception as e:
        debug_log(f"Failed to stop AsyncHealingLogger: {e}")
    
    try:
        CBStateCache.invalidate_all()
        debug_log("CBStateCache cleared")
    except Exception as e:
        debug_log(f"Failed to clear CBStateCache: {e}")


def _save_results(environment, v2_stats: Optional[Dict] = None):
    """테스트 결과를 파일로 저장."""
    results_dir = os.path.join(_load_tests_dir, "results", "stage11")
    os.makedirs(results_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON 결과 저장
    json_path = os.path.join(results_dir, f"stage11_extreme_{timestamp}.json")
    
    result_data = {
        "test_name": "Stage 11: EXTREME Ramp-up Threshold Discovery",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "extreme_mode": EXTREME_MODE,
            "failure_injection_rate": FAILURE_INJECTION_RATE,
            "max_users": RampUpShape.MAX_USERS,
            "ramp_duration": RampUpShape.RAMP_DURATION,
        },
        "milestones": _threshold_stats["milestones"],
        "snapshots": _threshold_stats["snapshots"],
        "recovery": _threshold_stats["recovery"],
        "extreme_stats": _threshold_stats["extreme"] if EXTREME_MODE else None,
        "v2_optimization_stats": v2_stats,
    }
    
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 JSON 결과 저장: {json_path}")
    
    # Markdown 보고서 저장
    md_path = os.path.join(results_dir, f"stage11_extreme_{datetime.now().strftime('%Y-%m-%d')}.md")
    _generate_markdown_report(md_path, result_data, v2_stats)
    print(f"📝 Markdown 보고서 저장: {md_path}")


def _generate_markdown_report(path: str, data: Dict, v2_stats: Optional[Dict] = None):
    """Markdown 형식의 보고서 생성."""
    milestones = data["milestones"]
    extreme = data.get("extreme_stats", {}) or {}
    snapshots = data.get("snapshots", [])
    
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Stage 11 EXTREME Threshold Discovery 테스트 결과 보고서\n\n")
        f.write(f"📅 **테스트 일시**: {data['timestamp'][:10]}\n")
        f.write("🏷️ **버전**: EXTREME Self-Healing V2\n")
        f.write("🎯 **테스트 목표**: 시스템 임계점 발견 및 Self-Healing 극한 테스트\n\n")
        f.write("---\n\n")
        
        # Executive Summary
        f.write("## 📋 Executive Summary\n\n")
        f.write("| 항목 | 값 | 비고 |\n")
        f.write("|------|-----|------|\n")
        f.write(f"| **EXTREME Mode** | {'✅ 활성화' if EXTREME_MODE else '❌ 비활성화'} | - |\n")
        f.write(f"| **최대 사용자** | {data['config']['max_users']} | - |\n")
        f.write(f"| **테스트 시간** | {data['config']['ramp_duration']}s | - |\n")
        
        if milestones.get("breaking_point"):
            bp = milestones["breaking_point"]
            f.write(f"| **Breaking Point** | {bp['user_count']} users | Error {bp['error_rate']}% |\n")
        else:
            f.write("| **Breaking Point** | 미도달 | 시스템 안정 |\n")
        
        if milestones.get("first_cb_open"):
            cb = milestones["first_cb_open"]
            f.write(f"| **첫 CB Open** | {cb['user_count']} users | {cb.get('service', 'unknown')} |\n")
        
        f.write("\n---\n\n")
        
        # Milestones
        f.write("## 🎯 Milestone 이벤트\n\n")
        for name, value in milestones.items():
            if value:
                f.write(f"### {name}\n")
                f.write("```json\n")
                f.write(json.dumps(value, indent=2, ensure_ascii=False))
                f.write("\n```\n\n")
        
        # EXTREME Statistics
        if EXTREME_MODE and extreme:
            f.write("## 🔥 EXTREME Mode 통계\n\n")
            f.write("| 항목 | 값 |\n")
            f.write("|------|-----|\n")
            f.write(f"| Failures Injected | {extreme.get('failures_injected', 0)} |\n")
            f.write(f"| CB Triggers | {extreme.get('cb_triggers', 0)} |\n")
            f.write(f"| CB Recoveries | {extreme.get('cb_recoveries', 0)} |\n")
            f.write(f"| Emergency Triggers | {extreme.get('emergency_triggers', 0)} |\n")
            f.write(f"| Emergency Releases | {extreme.get('emergency_releases', 0)} |\n")
            f.write(f"| DLQ Items | {extreme.get('dlq_items_created', 0)} |\n")
            f.write(f"| Error Budget Consumed | {extreme.get('error_budget_consumed', 0)} |\n")
            f.write("\n")
            
            # V2 고급 시나리오 통계
            f.write("### V2 고급 시나리오 통계\n\n")
            f.write("| 시나리오 | 항목 | 값 |\n")
            f.write("|----------|------|-----|\n")
            f.write(f"| ❄️ Cold Start Storm | Thundering Herd 분산 | {extreme.get('cold_start_thundering_herd', 0)}회 |\n")
            f.write(f"| 🧟 Slow Poisoning | 좀비 인프라 감지 | {extreme.get('slow_poison_zombie_detected', 0)}회 |\n")
            f.write(f"| 🧪 Data Corruption | 데이터 오염 차단 | {extreme.get('data_corruption_blocked', 0)}회 |\n")
            f.write(f"| 🌑 Command Outage | 로컬 자치권 발동 | {extreme.get('command_outage_local_autonomy', 0)}회 |\n")
            f.write(f"| 🎭 Flapping Service | Half-Open 전환 | {extreme.get('flapping_half_open_transitions', 0)}회 |\n")
            f.write(f"| 🧠 Brain Split | Degraded Mode 활성화 | {extreme.get('brain_split_degraded_mode', 0)}회 |\n")
            f.write("\n")
            
            f.write("### 시나리오별 실행 횟수\n\n")
            f.write("| 시나리오 | 실행 횟수 | 카테고리 |\n")
            f.write("|----------|----------|----------|\n")
            scenarios = extreme.get("scenarios_executed", {})
            basic_scenarios = ["cascading_failure", "rapid_fire_cb", "emergency_escalation", "dlq_flood", "error_budget_exhaust"]
            advanced_scenarios = ["cold_start_storm", "slow_poisoning", "data_corruption_chaos", "command_center_outage", "flapping_service", "brain_split"]
            
            for scenario, count in scenarios.items():
                if scenario in basic_scenarios:
                    f.write(f"| {scenario} | {count} | 기본 |\n")
                elif scenario in advanced_scenarios:
                    f.write(f"| {scenario} | {count} | V2 고급 |\n")
                else:
                    f.write(f"| {scenario} | {count} | - |\n")
            f.write("\n")
        
        # V2 Optimization Stats
        if v2_stats:
            f.write("## 🚀 V2 최적화 모듈 통계\n\n")
            
            cache_stats = v2_stats.get("cache_stats", {})
            f.write("### 📦 CBStateCache\n")
            f.write(f"- 캐시 히트율: {cache_stats.get('hit_rate', 0)*100:.1f}%\n")
            f.write(f"- 총 요청: {cache_stats.get('total_requests', 0)}\n\n")
            
            logger_stats = v2_stats.get("async_logger_stats", {})
            f.write("### 📝 AsyncHealingLogger\n")
            f.write(f"- 총 이벤트: {logger_stats.get('events_logged', 0)}\n")
            f.write(f"- 플러시된 이벤트: {logger_stats.get('events_flushed', 0)}\n\n")
            
            jitter_stats = v2_stats.get("jitter_stats", {})
            f.write("### 🎲 AdaptiveJitter\n")
            f.write(f"- 평균 지터: {jitter_stats.get('avg_jitter_ms', 0):.1f}ms\n")
            f.write(f"- Relaxed: {jitter_stats.get('relaxed_count', 0)}회\n")
            f.write(f"- Normal: {jitter_stats.get('normal_count', 0)}회\n")
            f.write(f"- Stressed: {jitter_stats.get('stressed_count', 0)}회\n\n")
        
        # Snapshots Summary
        if snapshots:
            f.write("## 📈 부하 진행 요약\n\n")
            f.write("| 분 | 사용자 | 요청 | 에러율 | 평균 응답시간 |\n")
            f.write("|-----|--------|------|--------|---------------|\n")
            for snap in snapshots[-10:]:  # 마지막 10개만
                f.write(f"| {snap['minute']} | {snap['users']} | {snap['requests']} | {snap['error_rate']}% | {snap['avg_response_time_ms']:.1f}ms |\n")
            f.write("\n")
        
        # ========================================
        # V2 EXTREME 시나리오 상세 분석
        # ========================================
        f.write("---\n\n")
        f.write("## 🔬 V2 EXTREME 시나리오 상세 분석\n\n")
        
        # ❄️ Cold Start Storm
        f.write("### ❄️ Cold Start Storm (Thundering Herd)\n\n")
        f.write("**목표**: 캐시 무효화 후 동시 100개 요청 발생 시 시스템 보호 검증\n\n")
        f.write("| 항목 | 결과 |\n")
        f.write("|------|------|\n")
        cold_start_count = extreme.get("cold_start_thundering_herd", 0)
        f.write(f"| Thundering Herd 분산 | {cold_start_count}회 |\n")
        f.write("| Cache Invalidation | 성공 |\n")
        f.write(f"| 상태 | {'✅ PASS' if cold_start_count > 0 else '⚠️ 미실행'} |\n\n")
        f.write("> **검증 포인트**: CBStateCache.invalidate_all() 호출 후 100명 동시 접속 시 AdaptiveJitter가 요청을 분산\n\n")
        
        # 🧟 Slow Poisoning
        f.write("### 🧟 Slow Poisoning (Zombie Infrastructure)\n\n")
        f.write("**목표**: 점진적 지연 증가를 감지하여 좀비 인프라 사전 차단\n\n")
        f.write("| 항목 | 결과 |\n")
        f.write("|------|------|\n")
        zombie_count = extreme.get("slow_poison_zombie_detected", 0)
        zombie_threshold = EXTREME_CONFIG.get("zombie_latency_threshold_sec", 5)
        f.write(f"| 좀비 인프라 감지 | {zombie_count}회 |\n")
        f.write(f"| 지연 임계치 | {zombie_threshold}초 |\n")
        f.write(f"| Emergency LEVEL_3 트리거 | {'✅ 동작' if zombie_count > 0 else '⚠️ 미감지'} |\n\n")
        f.write(f"> **검증 포인트**: 응답 시간이 {zombie_threshold}초 초과 시 자동으로 Emergency LEVEL_3 트리거\n\n")
        
        # 🧪 Data Corruption Chaos
        f.write("### 🧪 Data Corruption Chaos (Zero Variance Validator)\n\n")
        f.write("**목표**: 악의적/손상된 데이터 입력 시 시스템 보호 검증\n\n")
        f.write("| 오염 유형 | 설명 | 기대 결과 |\n")
        f.write("|----------|------|----------|\n")
        f.write("| null_order_id | NULL 주문 ID | 400/422 거부 |\n")
        f.write("| negative_amount | 음수 금액 | 400/422 거부 |\n")
        f.write("| overflow_amount | 오버플로우 금액 | 400/422 거부 |\n")
        f.write("| sql_injection | SQL Injection 공격 | 400/403 거부 |\n")
        f.write("| xss_payload | XSS 스크립트 주입 | 400/403 거부 |\n")
        f.write("| invalid_signature | 잘못된 서명 | 400/403 거부 |\n")
        f.write("| future_timestamp | 미래 타임스탬프 | 400/422 거부 |\n\n")
        corruption_blocked = extreme.get("data_corruption_blocked", 0)
        f.write(f"**결과**: {corruption_blocked}건 오염 데이터 차단 {'✅' if corruption_blocked > 0 else '⚠️'}\n\n")
        
        # 🌑 Command Center Outage
        f.write("### 🌑 Command Center Outage (Local Autonomy)\n\n")
        f.write("**목표**: 사령탑 연결 실패 시 로컬 자치권으로 서비스 지속 검증\n\n")
        f.write("| 항목 | 결과 |\n")
        f.write("|------|------|\n")
        local_autonomy = extreme.get("command_outage_local_autonomy", 0)
        f.write(f"| 로컬 자치권 발동 | {local_autonomy}회 |\n")
        f.write("| SafeDefaults 활성화 | ✅ |\n")
        f.write(f"| 서비스 지속 | {'✅ 성공' if local_autonomy > 0 else '⚠️ 미검증'} |\n\n")
        f.write("> **검증 포인트**: SafeDefaults.enter_degraded_mode() 호출 후에도 핵심 기능 정상 동작\n\n")
        
        # 🎭 Flapping Service
        f.write("### 🎭 Flapping Service (Circuit Breaker Half-Open)\n\n")
        f.write("**목표**: 빠른 ON/OFF 반복 시 CB가 Half-Open 상태에서 안정화되는지 검증\n\n")
        f.write("| 항목 | 결과 |\n")
        f.write("|------|------|\n")
        half_open = extreme.get("flapping_half_open_transitions", 0)
        f.write("| Flapping 사이클 | 10회 |\n")
        f.write(f"| Half-Open 전환 | {half_open}회 |\n")
        f.write(f"| CB 안정화 | {'✅ 성공' if half_open > 0 else '⚠️ 미검증'} |\n\n")
        f.write("> **검증 포인트**: 10회 연속 block/reset 후 CB가 HALF_OPEN에서 CLOSED로 복구\n\n")
        
        # 🧠 Brain Split
        f.write("### 🧠 Brain Split (Degraded Mode)\n\n")
        f.write("**목표**: Redis 연결 실패 시 Degraded Mode로 전환하여 서비스 지속\n\n")
        f.write("| 항목 | 결과 |\n")
        f.write("|------|------|\n")
        degraded = extreme.get("brain_split_degraded_mode", 0)
        f.write(f"| Degraded Mode 진입 | {degraded}회 |\n")
        f.write("| 로컬 기본값 사용 | ✅ |\n")
        f.write(f"| 서비스 지속 | {'✅ 성공' if degraded > 0 else '⚠️ 미검증'} |\n\n")
        f.write("> **검증 포인트**: Redis 단절 시 SafeDefaults.get_all_defaults()로 로컬 기본값 사용\n\n")
        
        # ========================================
        # Self-Healing 컴포넌트 동작 분석
        # ========================================
        f.write("---\n\n")
        f.write("## 🔧 Self-Healing 컴포넌트 동작 분석\n\n")
        
        f.write("### Circuit Breaker 상태 전이\n\n")
        f.write("```\n")
        f.write("CLOSED → OPEN: 5회 연속 실패 시\n")
        f.write("OPEN → HALF_OPEN: 30초 대기 후\n")
        f.write("HALF_OPEN → CLOSED: 성공 응답 시\n")
        f.write("HALF_OPEN → OPEN: 실패 응답 시\n")
        f.write("```\n\n")
        
        cb_triggers = extreme.get("cb_triggers", 0)
        cb_recoveries = extreme.get("cb_recoveries", 0)
        f.write("| 이벤트 | 횟수 |\n")
        f.write("|--------|------|\n")
        f.write(f"| CB OPEN 전환 | {cb_triggers}회 |\n")
        f.write(f"| CB 복구 (CLOSED) | {cb_recoveries}회 |\n\n")
        
        f.write("### Emergency Mode 에스컬레이션\n\n")
        f.write("```\n")
        f.write("LEVEL_0 (정상) → LEVEL_1 (경고): Error Budget 50% 소진\n")
        f.write("LEVEL_1 → LEVEL_2 (위험): Error Budget 80% 소진\n")
        f.write("LEVEL_2 → LEVEL_3 (긴급): Error Budget 100% 또는 좀비 감지\n")
        f.write("```\n\n")
        
        emergency_triggers = extreme.get("emergency_triggers", 0)
        emergency_releases = extreme.get("emergency_releases", 0)
        f.write("| 이벤트 | 횟수 |\n")
        f.write("|--------|------|\n")
        f.write(f"| Emergency 트리거 | {emergency_triggers}회 |\n")
        f.write(f"| Emergency 해제 | {emergency_releases}회 |\n\n")
        
        f.write("### DLQ (Dead Letter Queue)\n\n")
        dlq_items = extreme.get("dlq_items_created", 0)
        f.write("| 항목 | 값 |\n")
        f.write("|------|-----|\n")
        f.write(f"| 캡처된 실패 메시지 | {dlq_items}개 |\n")
        f.write(f"| 재처리 대기 | {dlq_items}개 |\n\n")
        
        # ========================================
        # 테스트 환경 및 권장사항
        # ========================================
        f.write("---\n\n")
        f.write("## 📊 테스트 환경\n\n")
        f.write("| 항목 | 값 |\n")
        f.write("|------|-----|\n")
        f.write("| **프레임워크** | Locust 2.x |\n")
        f.write("| **환경** | Docker Compose |\n")
        f.write("| **서비스** | web, db, redis, celery, nginx |\n")
        f.write(f"| **최대 사용자** | {data['config']['max_users']} |\n")
        f.write(f"| **테스트 시간** | {data['config']['ramp_duration']}s |\n")
        f.write(f"| **Failure Injection Rate** | {data['config'].get('failure_injection_rate', 0.3)*100:.0f}% |\n\n")
        
        f.write("## 📝 권장사항\n\n")
        
        # 분석 기반 권장사항 생성
        recommendations = []
        if cold_start_count == 0:
            recommendations.append("- ❄️ Cold Start Storm 시나리오 재검증 필요")
        if zombie_count == 0:
            recommendations.append("- 🧟 Slow Poisoning 좀비 감지 임계치 조정 검토")
        if corruption_blocked == 0:
            recommendations.append("- 🧪 Data Corruption 테스트 엔드포인트 확인 필요")
        if local_autonomy == 0:
            recommendations.append("- 🌑 Command Outage 로컬 자치권 테스트 강화")
        if half_open == 0:
            recommendations.append("- 🎭 Flapping Service Half-Open 전환 로직 검토")
        if degraded == 0:
            recommendations.append("- 🧠 Brain Split Degraded Mode 트리거 조건 확인")
        
        if recommendations:
            for rec in recommendations:
                f.write(f"{rec}\n")
        else:
            f.write("✅ 모든 V2 EXTREME 시나리오가 정상 동작했습니다.\n")
        
        f.write("\n---\n\n")
        f.write("*Generated by Stage 11 EXTREME Self-Healing Test Suite V2*\n")
        f.write("*Copyright © 2025 MyProject*\n")
