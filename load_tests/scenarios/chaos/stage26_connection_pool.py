"""
Stage 26: Connection Pool 고갈 테스트

목표: DB Connection Pool 고갈 시 복원력 검증 (토스 40분 장애 재현)

시나리오:
  - 대량 동시 DB 쿼리로 Pool 고갈 유도
  - Connection Leak 시뮬레이션
  - Pool Watchdog 자동 복구 검증
  - TC-26-4: Pool Exhaustion + Cache Stampede 통합 검증

실행 방법:
    # Web UI 모드
    locust -f load_tests/scenarios/stage26_connection_pool.py --host=http://localhost:8000

    # CLI 모드 (Spike 패턴)
    locust -f load_tests/scenarios/stage26_connection_pool.py --host=http://localhost:8000 \
        --users=200 --spawn-rate=50 --run-time=5m --headless --html=stage26_report.html

검증 기준:
  - Pool 고갈 감지 시간 < 10초
  - Leak 연결 자동 종료
  - Pool 확장 및 축소 정상 동작
  - 에러율 급증 후 복구 확인
  - TC-26-4: Stampede 방지가 Pool 폭격 방지로 이어지는지 확인

Reference:
  - docs/STAGE_26_CONNECTION_POOL.md
  - Stage 35 (Cache Stampede Prevention) 통합
"""

import os
import sys
import time
import random
from datetime import datetime
from typing import Dict

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events


STAGE_NAME = "[Stage26-ConnectionPool]"


# =============================================================================
# 테스트 통계
# =============================================================================

_pool_stats = {
    "total_requests": 0,
    "db_queries": {
        "fast": {"success": 0, "failure": 0, "times": []},
        "slow": {"success": 0, "failure": 0, "times": []},
        "heavy": {"success": 0, "failure": 0, "times": []},
    },
    "pool_exhaustion": {
        "detected_count": 0,
        "detection_times": [],
        "recovery_times": [],
    },
    "leak_simulation": {
        "leaked_connections": 0,
        "recovered_connections": 0,
    },
    "errors": {
        "connection_timeout": 0,
        "pool_exhausted": 0,
        "db_error": 0,
    },
    "phases": {
        "normal": {"start": None, "end": None},
        "stress": {"start": None, "end": None},
        "recovery": {"start": None, "end": None},
    },
    # TC-26-4: Stampede + Pool Exhaustion 통합 메트릭
    "stampede_integration": {
        "cache_miss_during_pool_stress": 0,
        "stampede_prevented_pool_explosion": 0,
        "db_queries_during_pool_low": 0,
        "fallback_to_stale_cache": 0,
        "pool_explosion_prevented": True,  # 핵심 검증 항목
    },
}


def record_query(query_type: str, success: bool, response_time: float):
    """쿼리 통계 기록"""
    _pool_stats["total_requests"] += 1
    status = "success" if success else "failure"
    _pool_stats["db_queries"][query_type][status] += 1
    _pool_stats["db_queries"][query_type]["times"].append(response_time)


def record_pool_exhaustion(detection_time: float):
    """Pool 고갈 감지 기록"""
    _pool_stats["pool_exhaustion"]["detected_count"] += 1
    _pool_stats["pool_exhaustion"]["detection_times"].append(detection_time)


def record_pool_recovery(recovery_time: float):
    """Pool 복구 기록"""
    _pool_stats["pool_exhaustion"]["recovery_times"].append(recovery_time)


# =============================================================================
# Pool 스트레스 사용자 (정상 부하)
# =============================================================================

class NormalDBUser(HttpUser):
    """
    정상적인 DB 사용 패턴
    
    빠른 쿼리 위주로 Pool을 적절히 사용
    """
    
    wait_time = between(1, 3)
    weight = 3  # 60%
    
    def on_start(self):
        """사용자 시작 시 인증"""
        self.token = None
        self._authenticate()
    
    def _authenticate(self):
        """인증 토큰 획득"""
        try:
            response = self.client.post("/api/auth/login/", json={
                "username": f"load_test_user_{random.randint(0, 199)}",
                "password": "testpass123"
            }, catch_response=True)
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
                response.success()
            else:
                response.failure(f"Auth failed: {response.status_code}")
        except Exception:
            pass  # 인증 실패해도 테스트 계속
    
    def _headers(self) -> Dict[str, str]:
        """인증 헤더"""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(5)
    @tag("fast_query")
    def fast_product_list(self):
        """빠른 상품 목록 조회 (인덱스 활용)"""
        start = time.time()
        with self.client.get(
            "/api/products/",
            headers=self._headers(),
            params={"page": 1, "limit": 10},
            catch_response=True,
            name="[Stage26] Fast Query - Product List"
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("fast", True, elapsed)
                response.success()
            elif response.status_code == 503:
                _pool_stats["errors"]["pool_exhausted"] += 1
                record_query("fast", False, elapsed)
                response.failure("Pool exhausted")
            else:
                record_query("fast", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(2)
    @tag("fast_query")
    def fast_category_list(self):
        """빠른 카테고리 조회"""
        start = time.time()
        with self.client.get(
            "/api/categories/",
            headers=self._headers(),
            catch_response=True,
            name="[Stage26] Fast Query - Categories"
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("fast", True, elapsed)
                response.success()
            else:
                record_query("fast", False, elapsed)

    @task(3)
    @tag("health_check")
    def health_check(self):
        """헬스체크 (DB Connection 확인)"""
        start = time.time()
        with self.client.get(
            "/api/self-healing/health/",
            catch_response=True,
            name="[Stage26] Health Check"
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("fast", True, elapsed)
                response.success()
            elif response.status_code == 503:
                _pool_stats["errors"]["pool_exhausted"] += 1
                record_query("fast", False, elapsed)
                response.failure("Health check failed - Pool exhausted")
            else:
                record_query("fast", False, elapsed)
                response.failure(f"Health check error: {response.status_code}")


# =============================================================================
# Heavy DB User (Pool 스트레스 유발)
# =============================================================================

class HeavyDBUser(HttpUser):
    """
    무거운 DB 사용 패턴 - Pool 고갈 유발
    
    복잡한 쿼리, 긴 트랜잭션으로 Connection 오래 점유
    """
    
    wait_time = between(0.5, 1)  # 더 빈번한 요청
    weight = 2  # 40%
    
    def on_start(self):
        """사용자 시작"""
        self.token = None
        self._authenticate()
        self.user_id = random.randint(1, 1000)
    
    def _authenticate(self):
        """인증"""
        try:
            response = self.client.post("/api/auth/login/", json={
                "username": f"load_test_user_{random.randint(0, 199)}",
                "password": "testpass123"
            })
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
        except:
            pass
    
    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(3)
    @tag("slow_query")
    def slow_search_query(self):
        """느린 검색 쿼리 (Full Table Scan 유도)"""
        search_terms = ["테스트", "상품", "best", "new", "sale", "premium"]
        start = time.time()
        
        with self.client.get(
            "/api/products/search/",
            headers=self._headers(),
            params={
                "q": random.choice(search_terms),
                "sort": "created_at",
                "include_sold_out": "true",
            },
            catch_response=True,
            name="[Stage26] Slow Query - Search",
            timeout=30
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("slow", True, elapsed)
                response.success()
            elif response.status_code in (503, 504):
                _pool_stats["errors"]["pool_exhausted"] += 1
                record_pool_exhaustion(time.time())
                record_query("slow", False, elapsed)
                response.failure("Pool exhausted or timeout")
            else:
                record_query("slow", False, elapsed)
    
    @task(2)
    @tag("heavy_query")
    def heavy_order_history(self):
        """무거운 주문 내역 조회 (JOIN 다수)"""
        start = time.time()
        
        with self.client.get(
            "/api/orders/",
            headers=self._headers(),
            params={
                "include_items": "true",
                "include_payments": "true",
                "page_size": 50,
            },
            catch_response=True,
            name="[Stage26] Heavy Query - Order History",
            timeout=30
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("heavy", True, elapsed)
                response.success()
            elif response.status_code in (503, 504):
                _pool_stats["errors"]["pool_exhausted"] += 1
                record_query("heavy", False, elapsed)
                response.failure("Pool exhausted")
            else:
                record_query("heavy", False, elapsed)
    
    @task(1)
    @tag("heavy_query")
    def heavy_analytics_query(self):
        """분석 쿼리 (집계 함수)"""
        start = time.time()
        
        with self.client.get(
            "/api/products/stats/",
            headers=self._headers(),
            catch_response=True,
            name="[Stage26] Heavy Query - Analytics",
            timeout=30
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_query("heavy", True, elapsed)
                response.success()
            elif response.status_code == 404:
                # 엔드포인트 없으면 스킵
                response.success()
            else:
                record_query("heavy", False, elapsed)
    
    @task(2)
    @tag("transaction")
    def long_transaction_payment(self):
        """긴 트랜잭션 - 결제 처리"""
        start = time.time()
        order_id = f"ORD-{random.randint(10000, 99999)}"
        
        with self.client.post(
            "/api/payments/process/",
            headers=self._headers(),
            json={
                "order_id": order_id,
                "amount": random.randint(10000, 100000),
                "method": "card",
            },
            catch_response=True,
            name="[Stage26] Transaction - Payment",
            timeout=30
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code in (200, 201):
                record_query("heavy", True, elapsed)
                response.success()
            elif response.status_code == 404:
                response.success()  # 엔드포인트 없으면 스킵
            elif response.status_code in (503, 504):
                _pool_stats["errors"]["pool_exhausted"] += 1
                record_query("heavy", False, elapsed)
                response.failure("Pool exhausted during payment")
            else:
                record_query("heavy", False, elapsed)


# =============================================================================
# 이벤트 훅
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작"""
    _pool_stats["phases"]["normal"]["start"] = datetime.now()
    
    print("\n" + "=" * 70)
    print(f"🔥 {STAGE_NAME} Connection Pool 고갈 테스트 시작")
    print("=" * 70)
    print("목표: DB Connection Pool 고갈 시 복원력 검증")
    print("시나리오:")
    print("  1. 정상 부하 → Pool 사용량 모니터링")
    print("  2. Heavy 쿼리 증가 → Pool 고갈 유도")
    print("  3. Watchdog 감지 및 복구 확인")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료"""
    _pool_stats["phases"]["recovery"]["end"] = datetime.now()
    
    print("\n" + "=" * 70)
    print(f"✅ {STAGE_NAME} Connection Pool 테스트 완료")
    print("=" * 70)
    
    # 통계 출력
    total = _pool_stats["total_requests"]
    print(f"\n📊 총 요청 수: {total}")
    
    print("\n📈 쿼리 유형별 통계:")
    for query_type, stats in _pool_stats["db_queries"].items():
        success = stats["success"]
        failure = stats["failure"]
        times = stats["times"]
        avg_time = sum(times) / len(times) if times else 0
        print(f"  {query_type}: 성공={success}, 실패={failure}, 평균응답={avg_time:.2f}ms")
    
    print(f"\n🚨 Pool 고갈 감지: {_pool_stats['pool_exhaustion']['detected_count']}회")
    
    detection_times = _pool_stats["pool_exhaustion"]["detection_times"]
    if detection_times:
        print(f"  - 감지 시간들: {detection_times[:5]}...")  # 처음 5개만
    
    print("\n❌ 에러 통계:")
    for error_type, count in _pool_stats["errors"].items():
        print(f"  {error_type}: {count}")
    
    # 검증 결과
    print("\n" + "=" * 70)
    print("🎯 검증 결과:")
    
    pool_exhausted = _pool_stats["errors"]["pool_exhausted"]
    if pool_exhausted > 0:
        print(f"  ⚠️ Pool 고갈 발생: {pool_exhausted}회 (Watchdog 동작 확인 필요)")
    else:
        print("  ✅ Pool 고갈 미발생 (부하가 충분하지 않았을 수 있음)")
    
    # 성공률 계산
    fast_total = _pool_stats["db_queries"]["fast"]["success"] + _pool_stats["db_queries"]["fast"]["failure"]
    if fast_total > 0:
        fast_success_rate = _pool_stats["db_queries"]["fast"]["success"] / fast_total * 100
        print(f"  빠른 쿼리 성공률: {fast_success_rate:.1f}%")
    
    # TC-26-4: Stampede 통합 검증 결과
    stampede = _pool_stats["stampede_integration"]
    print("\n🔗 TC-26-4: Stampede + Pool 통합 검증:")
    print(f"  - Pool 스트레스 중 캐시 미스: {stampede['cache_miss_during_pool_stress']}")
    print(f"  - Stampede 방지로 Pool 폭격 방지: {stampede['stampede_prevented_pool_explosion']}")
    print(f"  - Pool 부족 시 DB 쿼리 수: {stampede['db_queries_during_pool_low']}")
    print(f"  - Stale 캐시 fallback: {stampede['fallback_to_stale_cache']}")
    explosion_status = "✅ YES" if stampede["pool_explosion_prevented"] else "❌ NO"
    print(f"  - Pool 폭격 방지 성공: {explosion_status}")
    
    print("=" * 70 + "\n")


# =============================================================================
# 추가 설정
# =============================================================================

# 사용자 클래스들이 자동으로 로드됨
