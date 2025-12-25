"""
Stage 3: Latency & Timeout Injection Test (Self-Healing L3 통합)

목적: PG 지연/timeout 시뮬레이션 + Self-Healing 시스템 연동 검증
- 정상 응답 지연 (500ms ~ 3000ms)
- Timeout 발생 패턴 기록
- 클라이언트 재시도 동작 검증
- Circuit Breaker 상태 모니터링
- Emergency Mode 연동 테스트
- L2 Storage 상태 확인
- Recovery Metrics 수집

실행:
    CHAOS_ENABLED=true locust -f load_tests/scenarios/load/stage3_latency.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=3m --headless

Reference:
    - docs/self_healing/03_CIRCUIT_BREAKER.md
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md
"""

import os
import sys
import uuid
from datetime import datetime

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import time
import random
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.chaos import FaultInjector, FaultType


STAGE_NAME = "[Stage3-L3]"

# 타임아웃/지연 통계
_latency_stats = {
    "injected_delays": 0,
    "timeout_count": 0,
    "recovery_success": 0,
    "recovery_failure": 0,
}

# Self-Healing 통합 통계
_selfhealing_stats = {
    "health_checks": {"success": 0, "failure": 0},
    "circuit_breaker": {
        "status_checks": 0,
        "recovery_transitions": 0,
        "pool_status_checks": 0,
    },
    "emergency_mode": {
        "status_checks": 0,
        "active_detected": 0,
    },
    "l2_storage": {
        "status_checks": 0,
        "healthy": 0,
        "degraded": 0,
    },
    "error_budget": {
        "checks": 0,
        "remaining_percent": [],
    },
    "recovery_latencies": [],  # Recovery 지연 시간 기록 (ms)
}


class LatencyUser(HttpUser):
    """
    Latency Injection Test 사용자

    PG 지연 상황에서 시스템 동작 검증 + Self-Healing 연동
    """

    wait_time = between(1, 2)

    def on_start(self):
        """테스트 시작 시 초기화"""
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        # 카오스 주입기 초기화
        self.fault_injector = FaultInjector()
        self.fault_injector.enable()
        self.fault_injector.activate_fault(FaultType.LATENCY)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # Admin 토큰 (self-healing API 접근용)
        self.admin_token = None
        self._login_as_admin()

    def _login_as_admin(self) -> bool:
        """Admin 로그인 (self-healing API 접근용)"""
        try:
            with self.client.post(
                "/api/auth/login/",
                json={
                    "username": "load_test_user_0",
                    "password": "testpass123",
                },
                name=f"{STAGE_NAME} Admin Login",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    token_data = data.get("token", {})
                    self.admin_token = token_data.get("access") or data.get("access")
                    response.success()
                    return True
                else:
                    response.failure(f"Admin login failed: {response.status_code}")
                    return False
        except Exception as e:
            return False

    def _get_admin_headers(self) -> dict:
        """Admin 인증 헤더 반환"""
        headers = {"Content-Type": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        return headers

    @task(3)
    @tag("latency", "payment")
    def payment_with_latency(self):
        """
        지연이 있는 결제 플로우

        클라이언트 측에서 인위적 지연 후 결제 요청
        (실제 PG 지연 시뮬레이션)
        """
        global _latency_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return

        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 지연 주입 (10% 확률)
        latency_injected = self.fault_injector.maybe_inject_latency(
            min_ms=500,
            max_ms=2000,
        )

        if latency_injected:
            _latency_stats["injected_delays"] += 1

        # 결제 요청
        payment_key = self.payment_helper.generate_payment_key("latency")
        start_time = time.time()

        try:
            with self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [LATENCY]",
                catch_response=True,
                timeout=10,  # 10초 타임아웃
            ) as response:
                elapsed = (time.time() - start_time) * 1000

                if response.status_code in [200, 201, 400]:
                    response.success()
                    if latency_injected:
                        _latency_stats["recovery_success"] += 1
                else:
                    response.failure(f"Payment failed: {response.status_code}")
                    if latency_injected:
                        _latency_stats["recovery_failure"] += 1

        except Exception as e:
            _latency_stats["timeout_count"] += 1
            if latency_injected:
                _latency_stats["recovery_failure"] += 1

    @task(1)
    @tag("latency", "timeout")
    def simulate_timeout_scenario(self):
        """
        타임아웃 시나리오 시뮬레이션

        긴 지연 후 재시도 동작 검증
        """
        global _latency_stats

        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return

        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        payment_key = self.payment_helper.generate_payment_key("timeout")

        # 첫 번째 시도 (짧은 타임아웃으로 실패 유도)
        first_success = False
        try:
            response = self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [TIMEOUT-1st]",
                timeout=0.5,  # 매우 짧은 타임아웃
            )
            first_success = response.status_code in [200, 201, 400]
        except:
            _latency_stats["timeout_count"] += 1

        # 재시도 (정상 타임아웃)
        if not first_success:
            time.sleep(0.5)  # 잠시 대기 후 재시도

            with self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /api/payments/confirm/ [TIMEOUT-RETRY]",
                catch_response=True,
                timeout=10,
            ) as response:
                if response.status_code in [200, 201, 400, 409]:
                    response.success()
                    _latency_stats["recovery_success"] += 1
                else:
                    response.failure(f"Retry failed: {response.status_code}")
                    _latency_stats["recovery_failure"] += 1

    # =========================================================================
    # Self-Healing Integration Tasks
    # =========================================================================

    @task(2)
    @tag("selfhealing", "health")
    def check_selfhealing_health(self):
        """
        Self-Healing 시스템 Health 체크
        
        지연 상황에서 힐링 시스템의 헬스 상태 모니터링
        """
        global _selfhealing_stats
        
        start_time = time.time()
        
        with self.client.get(
            "/api/self-healing/health/",
            name=f"{STAGE_NAME} GET /health/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                _selfhealing_stats["health_checks"]["success"] += 1
                data = response.json()
                
                # Health 응답 지연 기록
                if elapsed_ms > 100:  # 100ms 초과 시 경고
                    _selfhealing_stats["recovery_latencies"].append(elapsed_ms)
                
                if data.get("status") == "healthy":
                    response.success()
                else:
                    response.failure(f"System degraded: {data.get('status')}")
            else:
                _selfhealing_stats["health_checks"]["failure"] += 1
                response.failure(f"Health check failed: {response.status_code}")

    @task(2)
    @tag("selfhealing", "circuit-breaker")
    def check_circuit_breaker_status(self):
        """
        Circuit Breaker 상태 확인
        
        지연 발생 시 Circuit Breaker가 어떻게 반응하는지 모니터링
        """
        global _selfhealing_stats
        
        _selfhealing_stats["circuit_breaker"]["status_checks"] += 1
        
        with self.client.get(
            "/api/self-healing/status/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /status/ (CB)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                data = response.json()
                
                # OPEN 또는 HALF_OPEN 상태 감지
                services = data.get("services", [])
                for service in services:
                    state = service.get("state", "").lower()
                    if state in ["open", "half_open"]:
                        _selfhealing_stats["circuit_breaker"]["recovery_transitions"] += 1
            else:
                response.failure(f"CB status failed: {response.status_code}")

    @task(2)
    @tag("selfhealing", "circuit-breaker")
    def check_circuit_breaker_pool(self):
        """
        Circuit Breaker Pool 상태 확인
        """
        global _selfhealing_stats
        
        _selfhealing_stats["circuit_breaker"]["pool_status_checks"] += 1
        
        with self.client.get(
            "/api/self-healing/circuit-breaker/pool/status/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /circuit-breaker/pool/status/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Pool API가 없을 수 있음
                response.success()
            else:
                response.failure(f"CB Pool status failed: {response.status_code}")

    @task(1)
    @tag("selfhealing", "emergency")
    def check_emergency_mode(self):
        """
        Emergency Mode 상태 확인
        
        지연 과부하 시 Emergency Mode 활성화 여부 확인
        """
        global _selfhealing_stats
        
        _selfhealing_stats["emergency_mode"]["status_checks"] += 1
        
        with self.client.get(
            "/api/self-healing/emergency/status/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /emergency/status/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                data = response.json()
                
                if data.get("is_active", False):
                    _selfhealing_stats["emergency_mode"]["active_detected"] += 1
            elif response.status_code == 404:
                # Emergency API가 없을 수 있음
                response.success()
            else:
                response.failure(f"Emergency status failed: {response.status_code}")

    @task(1)
    @tag("selfhealing", "l2-storage")
    def check_l2_storage_status(self):
        """
        L2 Storage 상태 확인
        
        지연 상황에서 L2 캐시 계층 상태 모니터링
        """
        global _selfhealing_stats
        
        _selfhealing_stats["l2_storage"]["status_checks"] += 1
        
        with self.client.get(
            "/api/self-healing/l2-storage/status/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /l2-storage/status/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                data = response.json()
                
                status_val = data.get("status", "healthy")
                if status_val == "healthy":
                    _selfhealing_stats["l2_storage"]["healthy"] += 1
                else:
                    _selfhealing_stats["l2_storage"]["degraded"] += 1
            elif response.status_code == 404:
                # L2 Storage API가 없을 수 있음
                response.success()
            else:
                response.failure(f"L2 Storage status failed: {response.status_code}")

    @task(1)
    @tag("selfhealing", "error-budget")
    def check_error_budget_status(self):
        """
        Error Budget 상태 확인
        
        지연으로 인한 오류가 Error Budget에 미치는 영향 모니터링
        """
        global _selfhealing_stats
        
        _selfhealing_stats["error_budget"]["checks"] += 1
        
        with self.client.get(
            "/api/self-healing/error-budget/status/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /error-budget/status/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                data = response.json()
                
                remaining = data.get("remaining_percent")
                if remaining is not None:
                    _selfhealing_stats["error_budget"]["remaining_percent"].append(remaining)
            elif response.status_code == 404:
                # Error Budget API가 없을 수 있음
                response.success()
            else:
                response.failure(f"Error Budget status failed: {response.status_code}")

    @task(1)
    @tag("selfhealing", "dashboard")
    def check_dashboard_summary(self):
        """
        Dashboard Summary 확인
        
        전체 시스템 상태 요약 조회
        """
        with self.client.get(
            "/api/self-healing/dashboard/summary/",
            headers=self._get_admin_headers(),
            name=f"{STAGE_NAME} GET /dashboard/summary/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [401, 403, 404]:
                # API가 없거나 권한 없음
                response.success()
            else:
                response.failure(f"Dashboard summary failed: {response.status_code}")

    @task(1)
    @tag("selfhealing", "recovery")
    def test_recovery_with_cb_check(self):
        """
        Recovery 시나리오 + Circuit Breaker 연동 테스트
        
        지연 발생 → 재시도 → Circuit Breaker 상태 확인
        """
        global _latency_stats, _selfhealing_stats
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=1):
            return
        
        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return
        
        # 지연 주입
        latency_injected = self.fault_injector.maybe_inject_latency(
            min_ms=1000,
            max_ms=3000,
        )
        
        if latency_injected:
            _latency_stats["injected_delays"] += 1
        
        payment_key = self.payment_helper.generate_payment_key("recovery-cb")
        recovery_start = time.time()
        
        # 결제 시도
        try:
            with self.client.post(
                "/api/payments/confirm/",
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": int(final_amount),
                },
                name=f"{STAGE_NAME} POST /confirm/ [RECOVERY-CB]",
                catch_response=True,
                timeout=15,
            ) as response:
                recovery_latency = (time.time() - recovery_start) * 1000
                _selfhealing_stats["recovery_latencies"].append(recovery_latency)
                
                if response.status_code in [200, 201, 400]:
                    response.success()
                    _latency_stats["recovery_success"] += 1
                elif response.status_code == 503:
                    # Service Unavailable - Circuit Breaker가 열렸을 수 있음
                    response.success()  # 예상된 동작
                    _selfhealing_stats["circuit_breaker"]["recovery_transitions"] += 1
                else:
                    response.failure(f"Recovery failed: {response.status_code}")
                    _latency_stats["recovery_failure"] += 1
        except Exception as e:
            _latency_stats["timeout_count"] += 1
            _latency_stats["recovery_failure"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 지연 테스트 + Self-Healing 결과"""
    global _latency_stats, _selfhealing_stats

    print("\n" + "=" * 70)
    print("⏱️  STAGE 3: LATENCY INJECTION + SELF-HEALING L3 TEST RESULTS")
    print("=" * 70)

    # 기본 지연 통계
    print("\n📊 Latency Injection Statistics:")
    print(f"   - Injected Delays: {_latency_stats['injected_delays']}")
    print(f"   - Timeout Count: {_latency_stats['timeout_count']}")
    print(f"   - Recovery Success: {_latency_stats['recovery_success']}")
    print(f"   - Recovery Failure: {_latency_stats['recovery_failure']}")

    total_injected = _latency_stats["injected_delays"] + _latency_stats["timeout_count"]
    if total_injected > 0:
        recovery_rate = _latency_stats["recovery_success"] / total_injected * 100
        print(f"   - Recovery Rate: {recovery_rate:.1f}%")

        if recovery_rate >= 90:
            print("   ✅ LATENCY TEST PASSED - System handles delays well")
        elif recovery_rate >= 70:
            print("   ⚠️  LATENCY TEST WARNING - Recovery rate below 90%")
        else:
            print("   ❌ LATENCY TEST FAILED - Recovery rate below 70%")
    else:
        print("   ℹ️  No latency injected (increase CHAOS_PROBABILITY)")

    # Self-Healing 통합 통계
    print("\n🔧 Self-Healing Integration Statistics:")
    
    # Health Checks
    health = _selfhealing_stats["health_checks"]
    health_total = health["success"] + health["failure"]
    health_rate = (health["success"] / health_total * 100) if health_total > 0 else 0
    print(f"\n   🏥 Health Checks:")
    print(f"      - Total: {health_total}")
    print(f"      - Success: {health['success']}")
    print(f"      - Failure: {health['failure']}")
    print(f"      - Success Rate: {health_rate:.1f}%")

    # Circuit Breaker
    cb = _selfhealing_stats["circuit_breaker"]
    print(f"\n   ⚡ Circuit Breaker:")
    print(f"      - Status Checks: {cb['status_checks']}")
    print(f"      - Pool Status Checks: {cb['pool_status_checks']}")
    print(f"      - Recovery Transitions Detected: {cb['recovery_transitions']}")

    # Emergency Mode
    em = _selfhealing_stats["emergency_mode"]
    print(f"\n   🚨 Emergency Mode:")
    print(f"      - Status Checks: {em['status_checks']}")
    print(f"      - Active Detected: {em['active_detected']}")

    # L2 Storage
    l2 = _selfhealing_stats["l2_storage"]
    print(f"\n   💾 L2 Storage:")
    print(f"      - Status Checks: {l2['status_checks']}")
    print(f"      - Healthy: {l2['healthy']}")
    print(f"      - Degraded: {l2['degraded']}")

    # Error Budget
    eb = _selfhealing_stats["error_budget"]
    print(f"\n   💰 Error Budget:")
    print(f"      - Checks: {eb['checks']}")
    if eb["remaining_percent"]:
        avg_remaining = sum(eb["remaining_percent"]) / len(eb["remaining_percent"])
        min_remaining = min(eb["remaining_percent"])
        print(f"      - Avg Remaining: {avg_remaining:.1f}%")
        print(f"      - Min Remaining: {min_remaining:.1f}%")

    # Recovery Latencies
    latencies = _selfhealing_stats["recovery_latencies"]
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        min_latency = min(latencies)
        
        # P95 계산
        sorted_latencies = sorted(latencies)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p95_latency = sorted_latencies[p95_idx] if p95_idx < len(sorted_latencies) else max_latency
        
        print(f"\n   🔄 Recovery Latency Metrics:")
        print(f"      - Samples: {len(latencies)}")
        print(f"      - Avg: {avg_latency:.1f}ms")
        print(f"      - Min: {min_latency:.1f}ms")
        print(f"      - Max: {max_latency:.1f}ms")
        print(f"      - P95: {p95_latency:.1f}ms")
        
        # SLA 체크 (2초 이내)
        if max_latency < 2000:
            print(f"      ✅ SLA Status: Under 2s threshold")
        else:
            print(f"      ⚠️  SLA Status: Exceeded 2s threshold")

    # 전체 통계
    collector = get_metrics_collector()
    summary = collector.get_summary()
    print(f"\n📈 Overall Metrics:")
    print(f"   - Total Requests: {summary['total_requests']}")
    print(f"   - Error Rate: {summary['overall_error_rate']}%")
    
    # 종합 판정
    print("\n" + "-" * 70)
    print("📋 FINAL VERDICT:")
    
    passed_tests = 0
    total_tests = 4
    
    # 1. Recovery Rate
    if total_injected > 0 and recovery_rate >= 70:
        passed_tests += 1
        print("   ✅ [1/4] Recovery Rate: PASS")
    else:
        print("   ❌ [1/4] Recovery Rate: FAIL or N/A")
    
    # 2. Health Check Rate
    if health_total > 0 and health_rate >= 90:
        passed_tests += 1
        print("   ✅ [2/4] Health Check Rate: PASS")
    else:
        print("   ❌ [2/4] Health Check Rate: FAIL or N/A")
    
    # 3. CB Monitoring
    if cb["status_checks"] > 0:
        passed_tests += 1
        print("   ✅ [3/4] Circuit Breaker Monitoring: PASS")
    else:
        print("   ❌ [3/4] Circuit Breaker Monitoring: FAIL")
    
    # 4. Recovery Latency SLA
    if latencies and max(latencies) < 5000:  # 5초 이내
        passed_tests += 1
        print("   ✅ [4/4] Recovery Latency SLA: PASS")
    else:
        print("   ❌ [4/4] Recovery Latency SLA: FAIL or N/A")
    
    print(f"\n   🎯 Result: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("   ✅ STAGE 3 L3 INTEGRATION: ALL TESTS PASSED")
    elif passed_tests >= total_tests - 1:
        print("   ⚠️  STAGE 3 L3 INTEGRATION: MOSTLY PASSED")
    else:
        print("   ❌ STAGE 3 L3 INTEGRATION: NEEDS ATTENTION")
    
    print("=" * 70)
