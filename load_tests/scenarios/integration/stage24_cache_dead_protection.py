"""
Stage 24 Extension: Cache Dead Protection Test (GAP-07)

목표: Redis 완전 장애 시 DB 과부하 방지 검증

시나리오:
  - Redis 완전 장애 시뮬레이션
  - DB로 요청 폭주 방지 확인
  - Rate Limit to DB 동작 확인

Invariants:
  - db_query_rate < threshold when cache dead
  - graceful_degradation_active

실행 방법:
    # Standalone 모드 (시뮬레이션)
    python load_tests/scenarios/stage24_cache_dead_protection.py

    # Locust 모드
    locust -f load_tests/scenarios/stage24_cache_dead_protection.py --host=http://localhost:8000

Reference:
  - docs/GAP_RESOLUTION_PLAN.md (GAP-07)
"""

import os
import sys
import time
import random
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


STAGE_NAME = "[Stage24-CacheDeadProtection]"


# =============================================================================
# 설정
# =============================================================================


@dataclass
class CacheDeadProtectionConfig:
    """Cache Dead Protection 설정"""
    
    # 요청 설정
    requests_per_second: int = 100
    num_products: int = 1000
    
    # DB 보호 설정
    db_rate_limit_per_second: int = 50  # DB 최대 쿼리율
    db_rate_limit_window_ms: int = 1000  # Rate limit 윈도우
    
    # Circuit Breaker 설정
    cache_cb_failure_threshold: int = 5
    cache_cb_timeout_seconds: int = 30
    
    # 장애 시나리오
    cache_failure_start_seconds: int = 5
    cache_failure_duration_seconds: int = 8


CONFIG = CacheDeadProtectionConfig()


# =============================================================================
# Rate Limiter
# =============================================================================


class TokenBucketRateLimiter:
    """토큰 버킷 Rate Limiter"""
    
    def __init__(self, rate: int, window_ms: int = 1000):
        self.rate = rate  # 초당 허용량
        self.window_ms = window_ms
        self.tokens = rate
        self.last_refill = time.time()
        self.lock = threading.Lock()
        
        # 통계
        self.allowed = 0
        self.rejected = 0
        
    def allow(self) -> bool:
        """요청 허용 여부"""
        with self.lock:
            self._refill()
            
            if self.tokens > 0:
                self.tokens -= 1
                self.allowed += 1
                return True
            else:
                self.rejected += 1
                return False
    
    def _refill(self):
        """토큰 리필"""
        now = time.time()
        elapsed = now - self.last_refill
        
        if elapsed >= self.window_ms / 1000:
            self.tokens = self.rate
            self.last_refill = now
    
    def get_stats(self) -> Dict[str, int]:
        """통계"""
        with self.lock:
            return {
                "allowed": self.allowed,
                "rejected": self.rejected,
                "current_tokens": self.tokens,
            }


# =============================================================================
# Cache with Circuit Breaker
# =============================================================================


class CacheCircuitBreakerState(str, Enum):
    """Cache Circuit Breaker 상태"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CacheWithProtection:
    """보호 기능이 있는 캐시"""
    
    def __init__(self, config: CacheDeadProtectionConfig = None):
        self.config = config or CONFIG
        self.cache: Dict[str, Any] = {}
        self.is_available = True
        
        # Circuit Breaker
        self.cb_state = CacheCircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        
        # DB Rate Limiter
        self.db_rate_limiter = TokenBucketRateLimiter(
            rate=self.config.db_rate_limit_per_second,
            window_ms=self.config.db_rate_limit_window_ms,
        )
        
        # 통계
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_errors = 0
        self.db_queries_allowed = 0
        self.db_queries_rejected = 0
        self.degraded_responses = 0
        
        self.lock = threading.Lock()
        
    def get(self, key: str) -> tuple[Optional[Any], str]:
        """
        캐시에서 조회
        Returns: (value, source) where source is 'cache', 'db', 'degraded', 'rate_limited'
        """
        with self.lock:
            # Circuit Breaker 상태 확인
            if not self._cb_allow_request():
                # 캐시 사용 불가 - DB로 fallback (with rate limit)
                return self._fallback_to_db(key)
            
            if not self.is_available:
                self.failure_count += 1
                self.last_failure_time = datetime.now(timezone.utc)
                self.cache_errors += 1
                
                if self.failure_count >= self.config.cache_cb_failure_threshold:
                    self._cb_open()
                
                return self._fallback_to_db(key)
            
            # 캐시 조회
            if key in self.cache:
                self.cache_hits += 1
                return (self.cache[key], "cache")
            else:
                self.cache_misses += 1
                return self._fallback_to_db(key)
    
    def _fallback_to_db(self, key: str) -> tuple[Optional[Any], str]:
        """DB fallback (with rate limiting)"""
        if self.db_rate_limiter.allow():
            self.db_queries_allowed += 1
            # 실제로는 DB 조회
            return ({"id": key, "from": "db", "degraded": False}, "db")
        else:
            self.db_queries_rejected += 1
            self.degraded_responses += 1
            # Rate limit 걸림 - 저하된 응답 반환
            return ({"id": key, "from": "degraded", "degraded": True}, "degraded")
    
    def _cb_allow_request(self) -> bool:
        """Circuit Breaker가 요청을 허용하는지"""
        if self.cb_state == CacheCircuitBreakerState.CLOSED:
            return True
        elif self.cb_state == CacheCircuitBreakerState.OPEN:
            if self.last_failure_time:
                elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
                if elapsed >= self.config.cache_cb_timeout_seconds:
                    self.cb_state = CacheCircuitBreakerState.HALF_OPEN
                    return True
            return False
        else:  # HALF_OPEN
            return True
    
    def _cb_open(self):
        """Circuit Breaker 열기"""
        if self.cb_state != CacheCircuitBreakerState.OPEN:
            print(f"{STAGE_NAME} Cache Circuit Breaker OPEN")
            self.cb_state = CacheCircuitBreakerState.OPEN
    
    def _cb_close(self):
        """Circuit Breaker 닫기"""
        if self.cb_state != CacheCircuitBreakerState.CLOSED:
            print(f"{STAGE_NAME} Cache Circuit Breaker CLOSED")
            self.cb_state = CacheCircuitBreakerState.CLOSED
            self.failure_count = 0
    
    def set(self, key: str, value: Any):
        """캐시에 저장"""
        with self.lock:
            if self.is_available:
                self.cache[key] = value
                
                # HALF_OPEN 상태에서 성공하면 CLOSED로
                if self.cb_state == CacheCircuitBreakerState.HALF_OPEN:
                    self._cb_close()
    
    def set_availability(self, available: bool):
        """캐시 가용성 설정 (장애 주입용)"""
        with self.lock:
            was_available = self.is_available
            self.is_available = available
            
            if was_available and not available:
                print(f"{STAGE_NAME} ⚠️  CACHE FAILURE INJECTED!")
            elif not was_available and available:
                print(f"{STAGE_NAME} ✅ CACHE RECOVERED!")
    
    def get_stats(self) -> Dict[str, Any]:
        """통계"""
        with self.lock:
            rate_limiter_stats = self.db_rate_limiter.get_stats()
            
            total_requests = self.cache_hits + self.cache_misses + self.cache_errors
            
            return {
                "is_available": self.is_available,
                "cb_state": self.cb_state.value,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_errors": self.cache_errors,
                "db_queries_allowed": self.db_queries_allowed,
                "db_queries_rejected": self.db_queries_rejected,
                "degraded_responses": self.degraded_responses,
                "total_requests": total_requests,
                "rate_limiter": rate_limiter_stats,
            }


# =============================================================================
# 시뮬레이션
# =============================================================================


class CacheDeadProtectionSimulator:
    """Cache Dead Protection 시뮬레이션"""
    
    def __init__(self, config: CacheDeadProtectionConfig = None):
        self.config = config or CONFIG
        self.cache = CacheWithProtection(config)
        self.running = False
        self.results: Dict[str, Any] = {}
        
        # 시간대별 통계
        self.time_series: List[Dict] = []
        
    def run_simulation(self, duration_seconds: int = 40):
        """시뮬레이션 실행"""
        print(f"\n{STAGE_NAME} Starting Cache Dead Protection Simulation")
        print(f"  - Duration: {duration_seconds}s")
        print(f"  - Request Rate: {self.config.requests_per_second}/s")
        print(f"  - DB Rate Limit: {self.config.db_rate_limit_per_second}/s")
        print(f"  - Cache Failure: {self.config.cache_failure_start_seconds}s ~ "
              f"{self.config.cache_failure_start_seconds + self.config.cache_failure_duration_seconds}s")
        print("-" * 60)
        
        self.running = True
        start_time = time.time()
        
        # 스레드 시작
        threads = []
        
        # 요청 스레드
        request_thread = threading.Thread(
            target=self._request_loop,
            args=(duration_seconds,)
        )
        request_thread.start()
        threads.append(request_thread)
        
        # 장애 주입 스레드
        failure_thread = threading.Thread(
            target=self._failure_injection_loop,
            args=(duration_seconds,)
        )
        failure_thread.start()
        threads.append(failure_thread)
        
        # 모니터링 스레드
        monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(duration_seconds,)
        )
        monitor_thread.start()
        threads.append(monitor_thread)
        
        # 대기
        for t in threads:
            t.join()
        
        self.running = False
        
        # 결과 분석
        self._analyze_results()
    
    def _request_loop(self, duration_seconds: int):
        """요청 루프"""
        start_time = time.time()
        
        while (time.time() - start_time) < duration_seconds:
            # 랜덤 제품 조회
            product_id = f"product_{random.randint(0, self.config.num_products - 1)}"
            
            value, source = self.cache.get(product_id)
            
            # Rate limit
            time.sleep(1 / self.config.requests_per_second)
    
    def _failure_injection_loop(self, duration_seconds: int):
        """장애 주입 루프"""
        start_time = time.time()
        
        while (time.time() - start_time) < duration_seconds:
            elapsed = time.time() - start_time
            
            # 장애 시작
            if elapsed >= self.config.cache_failure_start_seconds:
                if self.cache.is_available:
                    self.cache.set_availability(False)
            
            # 장애 종료
            failure_end = (
                self.config.cache_failure_start_seconds +
                self.config.cache_failure_duration_seconds
            )
            if elapsed >= failure_end:
                if not self.cache.is_available:
                    self.cache.set_availability(True)
            
            time.sleep(0.1)
    
    def _monitor_loop(self, duration_seconds: int):
        """모니터링 루프"""
        start_time = time.time()
        last_snapshot = start_time
        
        while (time.time() - start_time) < duration_seconds:
            time.sleep(1)  # 1초마다
            
            now = time.time()
            elapsed = now - start_time
            
            stats = self.cache.get_stats()
            
            # 시간대별 통계 저장
            self.time_series.append({
                "time": elapsed,
                "is_cache_available": stats['is_available'],
                "cb_state": stats['cb_state'],
                "db_queries_allowed": stats['db_queries_allowed'],
                "db_queries_rejected": stats['db_queries_rejected'],
                "degraded_responses": stats['degraded_responses'],
            })
            
            # 5초마다 출력
            if now - last_snapshot >= 5:
                print(f"\n  📊 Stats @ {elapsed:.0f}s:")
                print(f"     Cache: {'✅ UP' if stats['is_available'] else '❌ DOWN'} "
                      f"(CB: {stats['cb_state']})")
                print(f"     DB Queries: {stats['db_queries_allowed']} allowed, "
                      f"{stats['db_queries_rejected']} rejected")
                print(f"     Degraded: {stats['degraded_responses']}")
                last_snapshot = now
    
    def _analyze_results(self):
        """결과 분석"""
        stats = self.cache.get_stats()
        
        print("\n" + "=" * 60)
        print("📊 CACHE DEAD PROTECTION TEST RESULTS")
        print("=" * 60)
        
        print("\n📈 Request Statistics:")
        print(f"  Total Requests: {stats['total_requests']}")
        print(f"  Cache Hits: {stats['cache_hits']}")
        print(f"  Cache Misses: {stats['cache_misses']}")
        print(f"  Cache Errors: {stats['cache_errors']}")
        
        print("\n🛡️  Protection Statistics:")
        print(f"  DB Queries Allowed: {stats['db_queries_allowed']}")
        print(f"  DB Queries Rejected: {stats['db_queries_rejected']}")
        print(f"  Degraded Responses: {stats['degraded_responses']}")
        
        print("\n🔄 Rate Limiter:")
        print(f"  Allowed: {stats['rate_limiter']['allowed']}")
        print(f"  Rejected: {stats['rate_limiter']['rejected']}")
        
        # 장애 기간 동안 DB 쿼리율 분석
        failure_period_stats = [
            s for s in self.time_series
            if not s['is_cache_available']
        ]
        
        if failure_period_stats:
            # 장애 기간 동안 초당 DB 쿼리 수 계산
            failure_duration = len(failure_period_stats)
            if failure_duration > 0:
                db_queries_during_failure = sum(
                    s['db_queries_allowed'] for s in failure_period_stats
                )
                avg_db_rate = stats['db_queries_allowed'] / max(1, len(self.time_series))
                
                print("\n⏱️  Failure Period Analysis:")
                print(f"  Failure Duration: {failure_duration}s")
                print(f"  Avg DB Query Rate: {avg_db_rate:.1f}/s")
        
        # Invariant 검증
        print("\n" + "-" * 60)
        print("✅ INVARIANT VERIFICATION")
        print("-" * 60)
        
        # 1. db_query_rate < threshold when cache dead
        # 장애 기간 동안 DB 쿼리율이 제한 내에 있는지
        db_rate_ok = True
        if failure_period_stats:
            for i in range(1, len(failure_period_stats)):
                current = failure_period_stats[i]
                prev = failure_period_stats[i-1]
                rate = current['db_queries_allowed'] - prev['db_queries_allowed']
                if rate > self.config.db_rate_limit_per_second * 1.2:  # 20% 여유
                    db_rate_ok = False
                    break
        
        print(f"  db_query_rate < {self.config.db_rate_limit_per_second}/s: "
              f"{'✅ PASS' if db_rate_ok else '❌ FAIL'}")
        
        # 2. graceful_degradation_active
        # 장애 시 degraded 응답이 발생했는지
        degradation_active = stats['degraded_responses'] > 0 if stats['cache_errors'] > 0 else True
        print(f"  graceful_degradation_active: {'✅ PASS' if degradation_active else '❌ FAIL'}")
        print(f"    └─ Degraded responses: {stats['degraded_responses']}")
        
        # 3. Circuit Breaker 동작
        cb_worked = any(s['cb_state'] == 'open' for s in self.time_series)
        print(f"  circuit_breaker_triggered: {'✅ PASS' if cb_worked else '⚠️  WARN'}")
        
        # 4. Rate Limiter 동작
        rate_limiter_worked = stats['rate_limiter']['rejected'] > 0
        print(f"  rate_limiter_active: {'✅ PASS' if rate_limiter_worked else '⚠️  WARN'}")
        
        # 최종 결과
        all_passed = db_rate_ok and degradation_active
        
        print("\n" + "=" * 60)
        if all_passed:
            print("🎉 CACHE DEAD PROTECTION TEST: ✅ ALL PASSED")
        else:
            print("⚠️  CACHE DEAD PROTECTION TEST: SOME CHECKS FAILED")
        print("=" * 60)
        
        self.results = {
            "passed": all_passed,
            "stats": stats,
            "invariants": {
                "db_rate_limited": db_rate_ok,
                "graceful_degradation": degradation_active,
                "circuit_breaker_worked": cb_worked,
                "rate_limiter_worked": rate_limiter_worked,
            }
        }


# =============================================================================
# Locust User (HTTP 테스트용)
# =============================================================================

try:
    from locust import HttpUser, task, between, tag, events
    
    class CacheDeadProtectionUser(HttpUser):
        """Cache Dead Protection 테스트 User"""
        
        wait_time = between(0.05, 0.15)
        
        def on_start(self):
            """테스트 시작"""
            self.product_ids = [f"product_{i}" for i in range(1000)]
            self.degraded_count = 0
        
        @task(10)
        @tag("cache", "read")
        def read_product(self):
            """제품 조회"""
            product_id = random.choice(self.product_ids)
            
            with self.client.get(
                f"/api/products/{product_id}/",
                name=f"{STAGE_NAME} GET /api/products/[id]/",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    
                    # Degraded 응답 확인
                    if data.get('degraded'):
                        self.degraded_count += 1
                        events.request.fire(
                            request_type="DEGRADED",
                            name=f"{STAGE_NAME} degraded_response",
                            response_time=response.elapsed.total_seconds() * 1000,
                            response_length=len(response.content),
                        )
                    
                    response.success()
                elif response.status_code == 503:
                    # Rate limited
                    retry_after = response.headers.get("Retry-After", "5")
                    response.success()  # 예상된 동작
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(1)
        @tag("cache", "health")
        def check_cache_health(self):
            """캐시 상태 확인"""
            with self.client.get(
                "/api/cache/health/",
                name=f"{STAGE_NAME} GET /api/cache/health/",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                elif response.status_code == 503:
                    # Cache down
                    response.success()  # 예상된 동작
                else:
                    response.failure(f"Status: {response.status_code}")

except ImportError:
    pass


# =============================================================================
# Main
# =============================================================================


if __name__ == "__main__":
    print("=" * 60)
    print("GAP-07: Cache Dead Protection Test")
    print("=" * 60)
    
    simulator = CacheDeadProtectionSimulator()
    simulator.run_simulation(duration_seconds=20)
    
    # 종료 코드
    sys.exit(0 if simulator.results.get("passed", False) else 1)
