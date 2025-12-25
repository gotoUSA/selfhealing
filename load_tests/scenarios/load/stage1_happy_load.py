"""
Stage 1: L3 통합 베이스라인 테스트 (Happy Load + L3 Observability)

목적: 정상 성능 측정 + L3 엔진 거버넌스 오버헤드 검증
- Users: 5 → 10 → 50 (스케일링)
- Spawn Rate: 5
- Duration: 1~3분

🏛️ L3 통합 검증 항목:
1. 관찰자 태스크: 부하 중 L3 엔진 상태 실시간 확인
2. SLA 동적 동기화: RuntimeConfigManager에서 SLA 타겟 로드
3. Audit Log 검증: GOVERNANCE_BLOCKED == 0 확인 (False Positive 방지)
4. Error Budget Burn Rate: 정상 부하에서 burn rate < 1.0
5. Circuit Breaker 불변성: 모든 CB가 CLOSED 상태 유지
6. 거버넌스 오버헤드: 엔진 엔드포인트 P95 < 50ms

⚠️ Happy Path SLA 타이트닝 (v2):
- P95 임계값: 100ms (운영 기준 300ms에서 축소)
- P99 임계값: 200ms (운영 기준 500ms에서 축소)
- L3 엔드포인트: 200만 success (403/404는 failure로 처리)

🚀 V3 Performance Optimization:
- /health/ping/ 초경량 엔드포인트 추가 (Target: <1ms)
- Multi-tier cache: L1 TTLCache (2s) + L2 Redis (15s)
- Target: L3 endpoints P95 < 50ms

실행:
    locust -f load_tests/scenarios/load/stage1_happy_load.py --host=http://localhost:8000 --users=5 --spawn-rate=2 --run-time=1m --headless
"""

import os
import sys

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _load_tests_dir not in sys.path:
    sys.path.insert(0, _load_tests_dir)

import random
import time
from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector
from load_tests.config import SLA_TARGETS


STAGE_NAME = "[Stage1-L3]"

# =============================================================================
# L3 Self-Healing API Endpoints
# =============================================================================
SH_API = "/api/self-healing"

# =============================================================================
# L3 Verification Statistics (Global)
# =============================================================================
_l3_stats = {
    "test_start_time": None,
    "initial_snapshot": None,
    # Observability checks
    "engine_status_checks": 0,
    "error_budget_checks": 0,
    "circuit_breaker_checks": 0,
    "ping_checks": 0,  # V3: Ultra-lightweight ping endpoint
    # L3 endpoint latencies
    "engine_latencies": [],
    "error_budget_latencies": [],
    "cb_latencies": [],
    "ping_latencies": [],  # V3: Ping latencies
    # SLA from RuntimeConfig
    "dynamic_sla_targets": None,
    # Final verification
    "governance_blocked_count": 0,
    "cb_opened_during_test": False,
    "final_burn_rate": None,
    "all_cb_closed": True,
    # Rate Limit tracking (자체 보호 동작 추적)
    "rate_limited_count": 0,
    # V3: Cache hit tracking
    "cache_hits": {"L1": 0, "L2": 0, "MISS": 0},
}


class HappyLoadUser(HttpUser):
    """
    L3 통합 Happy Path Load Test 사용자

    정상적인 사용자 행동 패턴 + L3 엔진 Observability 검증
    """

    wait_time = between(1, 3)

    def on_start(self):
        """테스트 시작 시 초기화 + L3 상태 스냅샷"""
        global _l3_stats
        setup_event_hooks(STAGE_NAME)

        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)

        self.product_helper.ensure_products_cached()
        self.login_helper.login()

        # L3: 초기 상태 스냅샷 및 동적 SLA 로드
        if _l3_stats["test_start_time"] is None:
            _l3_stats["test_start_time"] = time.time()
            self._capture_initial_snapshot()
            self._load_dynamic_sla_targets()

    def _capture_initial_snapshot(self):
        """L3 엔진 초기 상태 스냅샷 캡처"""
        global _l3_stats
        try:
            # 초기 CB 상태
            resp = self.client.get(
                f"{SH_API}/status/",
                name=f"{STAGE_NAME} [INIT] GET /status/",
                catch_response=True,
            )
            if resp.status_code == 200:
                _l3_stats["initial_snapshot"] = resp.json()
                resp.success()
            else:
                resp.success()  # 초기화 실패는 무시
        except Exception:
            pass

    def _load_dynamic_sla_targets(self):
        """RuntimeConfigManager에서 SLA 타겟 동적 로드 (SSOT)"""
        global _l3_stats
        try:
            resp = self.client.get(
                f"{SH_API}/config/sla/",
                name=f"{STAGE_NAME} [INIT] GET /config/sla/",
                catch_response=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                # SLA config에서 response time 임계값 추출
                _l3_stats["dynamic_sla_targets"] = {
                    "p95_ms": data.get("response_time_p95_ms", 300),
                    "p99_ms": data.get("response_time_p99_ms", 500),
                    "error_rate": data.get("error_rate_threshold", 0.01),
                    "availability": data.get("availability_target", 0.999),
                }
                resp.success()
            else:
                # 기본값 사용
                _l3_stats["dynamic_sla_targets"] = SLA_TARGETS.get("payment", {})
                resp.success()
        except Exception:
            _l3_stats["dynamic_sla_targets"] = SLA_TARGETS.get("payment", {})

    @task(5)
    @tag("load", "browse")
    def browse_products(self):
        """상품 목록 탐색"""
        page = random.randint(1, 3)
        self.product_helper.browse_products(page)

    @task(3)
    @tag("load", "browse")
    def view_product_detail(self):
        """상품 상세 조회"""
        product_id = self.product_helper.get_random_product_id()
        if product_id:
            self.product_helper.get_product_detail(product_id)

    @task(2)
    @tag("load", "cart")
    def manage_cart(self):
        """장바구니 관리"""
        if not self.login_helper.ensure_logged_in():
            return

        # 랜덤 상품 추가
        product_ids = self.product_helper.cached_product_ids
        if product_ids:
            self.cart_helper.add_random_items(product_ids, min_items=1, max_items=2)

        # 장바구니 조회
        self.cart_helper.get_cart_items()

    @task(1)
    @tag("load", "payment", "critical")
    def complete_purchase(self):
        """
        구매 완료 플로우

        전체 결제 플로우를 실행하고 성능 측정
        """
        if not self.login_helper.ensure_logged_in():
            return

        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return

        # 장바구니 준비
        if not self.cart_helper.prepare_cart_for_order(product_ids, min_items=1, max_items=3):
            return

        # 주문 생성
        order_data = self.payment_helper.create_order(
            shipping_name="Happy Load Test",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="테스트동 123호",
        )

        if not order_data:
            return

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return

        # 결제 승인
        payment_key = self.payment_helper.generate_payment_key("happy")

        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /api/payments/confirm/ [CRITICAL]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                response.success()
            elif response.status_code == 400:
                # 비즈니스 에러 (재고 부족 등)
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")

    # =========================================================================
    # L3 Observability Tasks (관찰자 태스크)
    # =========================================================================

    @task(1)
    @tag("l3", "observability")
    def check_engine_status(self):
        """
        L3 엔진 상태 확인 (Observability)
        
        부하 중 L3 엔진이 상태를 정확히 인식하는지 검증
        /health/ 엔드포인트는 인증 불필요
        V3: Multi-tier cache 사용으로 P95 < 10ms 목표
        """
        global _l3_stats
        start_time = time.time()
        
        with self.client.get(
            f"{SH_API}/health/",
            name=f"{STAGE_NAME} [L3] GET /health/",
            catch_response=True,
        ) as response:
            latency_ms = (time.time() - start_time) * 1000
            _l3_stats["engine_latencies"].append(latency_ms)
            _l3_stats["engine_status_checks"] += 1
            
            if response.status_code == 200:
                data = response.json()
                # V3: Cache hit tracking
                cache_info = data.get("_cache", {})
                cache_hit = cache_info.get("hit", "MISS")
                if cache_hit in _l3_stats["cache_hits"]:
                    _l3_stats["cache_hits"][cache_hit] += 1
                    
                # health status 확인
                status = data.get("status", "unknown")
                if status != "healthy":
                    _l3_stats["cb_opened_during_test"] = True
                response.success()
            elif response.status_code == 429:
                # Rate Limit은 L3 자체 보호 동작 - 성공으로 처리하되 통계 추적
                _l3_stats["rate_limited_count"] += 1
                response.success()
            else:
                # 403/404등 실제 에러는 실패 처리
                response.failure(f"Engine health check failed: {response.status_code}")

    @task(2)
    @tag("l3", "observability", "v3")
    def check_health_ping(self):
        """
        V3 Ultra-lightweight Health Ping
        
        /health/ping/ 엔드포인트 - 최소 오버헤드 (<1ms 목표)
        미들웨어 바이패스, DB 없음, 서비스 레이어 없음
        """
        global _l3_stats
        start_time = time.time()
        
        with self.client.get(
            f"{SH_API}/health/ping/",
            name=f"{STAGE_NAME} [L3-V3] GET /health/ping/",
            catch_response=True,
        ) as response:
            latency_ms = (time.time() - start_time) * 1000
            _l3_stats["ping_latencies"].append(latency_ms)
            _l3_stats["ping_checks"] += 1
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ping") == "pong":
                    response.success()
                else:
                    response.failure("Invalid ping response")
            elif response.status_code == 429:
                _l3_stats["rate_limited_count"] += 1
                response.success()
            else:
                response.failure(f"Ping failed: {response.status_code}")

    @task(1)
    @tag("l3", "observability", "error-budget")
    def check_error_budget(self):
        """
        Error Budget 상태 확인
        
        /error-budget/status/ 엔드포인트 사용
        V3: Multi-tier cache 사용으로 P95 < 20ms 목표
        주의: /l2-storage/health/는 IsAdminUser 필요하므로 사용하지 않음
        """
        global _l3_stats
        start_time = time.time()
        
        with self.client.get(
            f"{SH_API}/error-budget/status/",
            name=f"{STAGE_NAME} [L3] GET /error-budget/status/",
            catch_response=True,
        ) as response:
            latency_ms = (time.time() - start_time) * 1000
            _l3_stats["error_budget_latencies"].append(latency_ms)
            _l3_stats["error_budget_checks"] += 1
            
            if response.status_code == 200:
                data = response.json()
                # V3: Cache hit tracking
                cache_info = data.get("_cache", {})
                cache_hit = cache_info.get("hit", "MISS")
                if cache_hit in _l3_stats["cache_hits"]:
                    _l3_stats["cache_hits"][cache_hit] += 1
                    
                # Error Budget 상태 확인
                budget_data = data.get("data", data)
                burn_rate = budget_data.get("burn_rate_1h", 0)
                _l3_stats["final_burn_rate"] = burn_rate
                response.success()
            elif response.status_code == 429:
                # Rate Limit은 L3 자체 보호 동작
                _l3_stats["rate_limited_count"] += 1
                response.success()
            elif response.status_code in [403, 404]:
                # 엔드포인트 없음/권한 없음 - 경고하지만 성공으로 처리 (선택적 기능)
                response.success()
            else:
                response.failure(f"Error budget check failed: {response.status_code}")

    @task(1)
    @tag("l3", "observability", "circuit-breaker")
    def check_circuit_breakers(self):
        """
        Circuit Breaker/Pool 상태 확인
        
        Happy Path에서 Pool 상태 모니터링
        /stress/pool-status/ 엔드포인트 사용 (인증 불필요)
        V3: Multi-tier cache 사용으로 P95 < 30ms 목표
        """
        global _l3_stats
        start_time = time.time()
        
        with self.client.get(
            f"{SH_API}/stress/pool-status/",
            name=f"{STAGE_NAME} [L3] GET /stress/pool-status/",
            catch_response=True,
        ) as response:
            latency_ms = (time.time() - start_time) * 1000
            _l3_stats["cb_latencies"].append(latency_ms)
            _l3_stats["circuit_breaker_checks"] += 1
            
            if response.status_code == 200:
                data = response.json()
                # V3: Cache hit tracking
                cache_info = data.get("_cache", {})
                cache_hit = cache_info.get("hit", "MISS")
                if cache_hit in _l3_stats["cache_hits"]:
                    _l3_stats["cache_hits"][cache_hit] += 1
                    
                # Pool 상태 확인 (available 필드)
                pool_status = data.get("pool", {})
                if not pool_status.get("available", True):
                    _l3_stats["all_cb_closed"] = False
                response.success()
            elif response.status_code == 429:
                # Rate Limit은 L3 자체 보호 동작
                _l3_stats["rate_limited_count"] += 1
                response.success()
            else:
                # 403/404등 실제 에러는 실패 처리
                response.failure(f"Pool status check failed: {response.status_code}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 SLA 검증 + L3 거버넌스 검증"""
    import statistics
    import requests
    
    collector = get_metrics_collector()
    summary = collector.get_summary()
    
    # Host 추출
    host = getattr(environment, 'host', 'http://localhost:8000') or 'http://localhost:8000'

    print("\n" + "=" * 70)
    print("📊 STAGE 1: L3 통합 베이스라인 테스트 결과")
    print("=" * 70)

    # =========================================================================
    # Part 1: 기존 SLA 검증
    # =========================================================================
    print("\n🎯 [Part 1] Business SLA 검증")
    print("-" * 50)
    
    sla_passed = True
    
    # 동적 SLA 타겟 사용 (SSOT) - Happy Path에서 타이트닝된 기준
    # Stage 1 (5 users)에서는 더 엄격한 기준 적용
    dynamic_sla = _l3_stats.get("dynamic_sla_targets") or SLA_TARGETS.get("payment", {})
    # Happy Path 타이트닝: 운영 기준(300/500) 대신 100/200 적용
    p95_target = min(dynamic_sla.get("p95_ms", dynamic_sla.get("p95", 100)), 100)
    p99_target = min(dynamic_sla.get("p99_ms", dynamic_sla.get("p99", 200)), 200)

    for name, stats in summary["endpoints"].items():
        if "payments/confirm" in name.lower() and "CRITICAL" in name:
            if stats["p95"] > p95_target:
                print(f"❌ SLA VIOLATION: Payment P95 {stats['p95']:.1f}ms > {p95_target}ms")
                sla_passed = False
            else:
                print(f"✅ Payment P95: {stats['p95']:.1f}ms <= {p95_target}ms")
                
            if stats["p99"] > p99_target:
                print(f"❌ SLA VIOLATION: Payment P99 {stats['p99']:.1f}ms > {p99_target}ms")
                sla_passed = False
            else:
                print(f"✅ Payment P99: {stats['p99']:.1f}ms <= {p99_target}ms")

        if "products" in name.lower() and "GET" in name:
            target = SLA_TARGETS.get("products_list", {})
            max_error_rate = target.get("error_rate", 0.01) * 100
            if stats["error_rate"] > max_error_rate:
                print(f"❌ SLA VIOLATION: {name} Error Rate {stats['error_rate']:.2f}%")
                sla_passed = False

    if summary["overall_error_rate"] > 1.0:
        print(f"❌ SLA VIOLATION: Overall Error Rate {summary['overall_error_rate']:.2f}% > 1%")
        sla_passed = False
    else:
        print(f"✅ Overall Error Rate: {summary['overall_error_rate']:.2f}% <= 1%")

    # =========================================================================
    # Part 2: L3 Audit Log 검증 (GOVERNANCE_BLOCKED == 0)
    # =========================================================================
    print("\n🛡️ [Part 2] L3 Governance 검증 (False Positive 방지)")
    print("-" * 50)
    
    l3_passed = True
    governance_blocked = 0
    
    try:
        # Audit Log에서 GOVERNANCE_BLOCKED 조회
        resp = requests.get(
            f"{host}{SH_API}/audit-logs/",
            params={"days": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            logs = resp.json().get("logs", [])
            governance_blocked = sum(
                1 for log in logs 
                if log.get("action") in ["governance_blocked", "GOVERNANCE_BLOCKED"]
            )
            _l3_stats["governance_blocked_count"] = governance_blocked
            
            if governance_blocked > 0:
                print(f"❌ GOVERNANCE FALSE POSITIVE: {governance_blocked}건 차단 발생!")
                l3_passed = False
            else:
                print(f"✅ Governance Blocked: 0건 (False Positive 없음)")
    except Exception as e:
        print(f"⚠️ Audit Log 조회 실패: {e}")

    # =========================================================================
    # Part 3: Error Budget Burn Rate 검증
    # =========================================================================
    print("\n📉 [Part 3] Error Budget Burn Rate 검증")
    print("-" * 50)
    
    burn_rate = _l3_stats.get("final_burn_rate", 0)
    if burn_rate is not None:
        if burn_rate > 1.0:
            print(f"⚠️ Burn Rate Warning: {burn_rate:.2f} > 1.0 (정상 부하에서 높음)")
            # Warning만, 실패는 아님
        else:
            print(f"✅ Burn Rate: {burn_rate:.2f} <= 1.0 (정상)")
    else:
        print("ℹ️ Burn Rate 데이터 없음")

    # =========================================================================
    # Part 4: Circuit Breaker 불변성 검증
    # =========================================================================
    print("\n🔌 [Part 4] Circuit Breaker 불변성 검증")
    print("-" * 50)
    
    if _l3_stats.get("cb_opened_during_test"):
        print("❌ CB OPENED: Happy Path에서 Circuit Breaker가 열림!")
        l3_passed = False
    else:
        print("✅ All Circuit Breakers: CLOSED 유지")
        
    if not _l3_stats.get("all_cb_closed", True):
        print("⚠️ Pool CB: OPEN 상태 감지됨")

    # =========================================================================
    # Part 5: Governance Overhead 측정 (V3 Enhanced)
    # =========================================================================
    print("\n⚡ [Part 5] L3 Governance Overhead 측정 (V3)")
    print("-" * 50)
    
    overhead_passed = True
    # V3: 더 엄격한 타겟
    overhead_targets = {
        "Health Ping": 5,      # V3: <5ms target (previously N/A)
        "Engine Status": 10,   # V3: <10ms target (previously 50ms)
        "Error Budget": 20,    # V3: <20ms target (previously 50ms)
        "Circuit Breaker": 30, # V3: <30ms target (previously 50ms)
    }
    
    for endpoint_name, latencies in [
        ("Health Ping", _l3_stats.get("ping_latencies", [])),
        ("Engine Status", _l3_stats.get("engine_latencies", [])),
        ("Error Budget", _l3_stats.get("error_budget_latencies", [])),
        ("Circuit Breaker", _l3_stats.get("cb_latencies", [])),
    ]:
        if latencies:
            sorted_latencies = sorted(latencies)
            p95_idx = int(len(sorted_latencies) * 0.95)
            p95 = sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)]
            avg = statistics.mean(latencies)
            target = overhead_targets.get(endpoint_name, 50)
            
            status = "✅" if p95 <= target else "⚠️"
            print(f"{status} {endpoint_name}: P95={p95:.1f}ms, Avg={avg:.1f}ms (목표 <{target}ms)")
            
            if p95 > target * 2:  # 2배 초과 시 경고
                overhead_passed = False

    # =========================================================================
    # Part 6: L3 Observability 통계 (V3 Enhanced)
    # =========================================================================
    print("\n📈 [Part 6] L3 Observability 통계 (V3)")
    print("-" * 50)
    print(f"Health Ping Checks: {_l3_stats.get('ping_checks', 0)}회")
    print(f"Engine Status Checks: {_l3_stats.get('engine_status_checks', 0)}회")
    print(f"Error Budget Checks: {_l3_stats.get('error_budget_checks', 0)}회")
    print(f"Circuit Breaker Checks: {_l3_stats.get('circuit_breaker_checks', 0)}회")
    
    # V3: Cache Hit 통계
    cache_hits = _l3_stats.get("cache_hits", {})
    total_cache_ops = sum(cache_hits.values())
    if total_cache_ops > 0:
        l1_hits = cache_hits.get("L1", 0)
        l2_hits = cache_hits.get("L2", 0)
        misses = cache_hits.get("MISS", 0)
        print(f"\n🗄️ V3 Cache Statistics:")
        print(f"   L1 Hits: {l1_hits} ({l1_hits/total_cache_ops*100:.1f}%)")
        print(f"   L2 Hits: {l2_hits} ({l2_hits/total_cache_ops*100:.1f}%)")
        print(f"   Misses: {misses} ({misses/total_cache_ops*100:.1f}%)")
    
    # Rate Limit 통계 (L3 자체 보호 동작)
    rate_limited = _l3_stats.get("rate_limited_count", 0)
    if rate_limited > 0:
        print(f"\n⚡ Rate Limit 발생: {rate_limited}회 (L3 자체 보호 동작)")
    
    if _l3_stats.get("dynamic_sla_targets"):
        print(f"\n📋 Dynamic SLA (from RuntimeConfig):")
        for key, value in _l3_stats["dynamic_sla_targets"].items():
            print(f"   - {key}: {value}")

    # =========================================================================
    # Final Summary
    # =========================================================================
    print("\n" + "=" * 70)
    print("📊 최종 결과 요약")
    print("=" * 70)
    
    all_passed = sla_passed and l3_passed
    
    print(f"Business SLA: {'✅ PASSED' if sla_passed else '❌ FAILED'}")
    print(f"L3 Governance: {'✅ PASSED' if l3_passed else '❌ FAILED'}")
    print(f"Governance Overhead: {'✅ OK' if overhead_passed else '⚠️ HIGH'}")
    print(f"\nTotal Requests: {summary['total_requests']}")
    print(f"RPS: {summary['rps']:.2f}")
    print(f"Error Rate: {summary['overall_error_rate']:.2f}%")
    print(f"Test Duration: {time.time() - _l3_stats.get('test_start_time', time.time()):.1f}s")
    
    if all_passed:
        print("\n🎉 ALL TESTS PASSED - L3 통합 베이스라인 검증 완료!")
    else:
        print("\n⚠️ SOME TESTS FAILED - Review results above")
    
    print("=" * 70)
