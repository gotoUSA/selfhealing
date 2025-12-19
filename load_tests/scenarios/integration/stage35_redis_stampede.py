"""
Stage 35: Cache Stampede Prevention - Redis 분산 락 버전

Purpose: 멀티 프로세스/멀티 인스턴스 환경에서 Cache Stampede 방지 검증
- Redis 분산 락 (토큰 기반 + Lua 스크립트)
- Single-flight 패턴 (락 실패 시 폴링 대기)
- TTL jitter로 동시 만료 방지
- Probabilistic Early Expiration (XFetch)

핵심 설계 원칙:
1. 분산 락은 토큰(UUID) 소유권으로 관리
2. 락 TTL = 예상 DB 시간 × 3~5
3. 락 실패 시 폴링 (5-20ms 간격, 최대 300ms)
4. TTL jitter ±10%
5. Early refresh는 만료 전만

테스트 환경:
- gunicorn worker 4개 이상 또는
- 컨테이너 2개 이상 동시 실행

합격 기준:
- 키당 DB 쿼리: 1회 (TTL 윈도우당)
- Stampede occurrence: 0
- 락 대기 p95: < 150ms
- 락 실패로 인한 중복 DB 쿼리: 0

Execution:
    docker-compose -f docker-compose.stage35.yml up -d
    docker-compose -f docker-compose.stage35.yml exec locust \
        locust -f /mnt/locust/scenarios/stage35_redis_stampede.py --headless
"""

import os
import sys
import time
import random
import threading
import uuid
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable
from collections import defaultdict
from dataclasses import dataclass, field
from contextlib import contextmanager

# Redis 임포트 (없으면 시뮬레이션 모드)
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

# Locust 임포트
try:
    from locust import HttpUser, task, between, tag, events, LoadTestShape
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    HttpUser = object
    task = lambda weight=1: lambda f: f
    between = lambda a, b: None
    tag = lambda *args: lambda f: f
    events = None
    LoadTestShape = object


STAGE_NAME = "[Stage35-Redis]"


# =============================================================================
# Configuration
# =============================================================================

# Redis 연결 설정
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))

# 캐시 설정
CACHE_TTL_S = 60
CACHE_TTL_JITTER_PERCENT = 0.10  # ±10% jitter

# 락 설정 (피드백 반영: DB 시간 × 3~5)
EXPECTED_DB_QUERY_TIME_MS = 50  # 예상 DB 조회 시간
LOCK_TTL_MS = EXPECTED_DB_QUERY_TIME_MS * 5  # 250ms
LOCK_POLL_INTERVAL_MS = 10  # 폴링 간격 (5-20ms 권장)
LOCK_POLL_MAX_WAIT_MS = 300  # 최대 대기 시간

# Early Refresh 설정
CACHE_EARLY_EXPIRY_DELTA_S = 10
CACHE_EARLY_EXPIRY_PROBABILITY = 0.1

# 테스트 설정
TARGET_RESPONSE_TIME_MS = 150  # Redis 환경 p95 목표
NUM_HOT_KEYS = 10
NUM_COLD_KEYS = 100

# 워커 식별
WORKER_ID = os.environ.get("WORKER_ID", str(uuid.uuid4())[:8])


# =============================================================================
# Lua Scripts for Atomic Operations
# =============================================================================

# 락 해제: 토큰 일치 시에만 삭제 (피드백 핵심 1번)
UNLOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# 락 연장: 토큰 일치 시에만 TTL 연장
EXTEND_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""


# =============================================================================
# Statistics Tracking
# =============================================================================

@dataclass
class RedisStampedeStats:
    """Redis 환경 Cache Stampede 통계"""
    
    start_time: Optional[float] = None
    worker_id: str = WORKER_ID
    
    # 요청 통계
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    
    # DB 쿼리 통계 (핵심 검증 항목)
    db_queries: int = 0
    db_queries_per_key: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    
    # 락 통계
    lock_acquired: int = 0
    lock_failed: int = 0
    lock_wait_times: List[float] = field(default_factory=list)
    lock_poll_counts: List[int] = field(default_factory=list)
    
    # Stampede 감지
    stampede_detected: int = 0
    duplicate_db_queries: int = 0  # 키당 1회 초과 시 카운트
    
    # Early Refresh
    early_refresh_triggers: int = 0
    early_refresh_success: int = 0
    
    # 응답 시간
    response_times: List[float] = field(default_factory=list)
    
    # 에러
    errors: List[Dict[str, Any]] = field(default_factory=list)
    
    def reset(self):
        self.start_time = time.time()
        self.total_requests = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_queries = 0
        self.db_queries_per_key.clear()
        self.lock_acquired = 0
        self.lock_failed = 0
        self.lock_wait_times.clear()
        self.lock_poll_counts.clear()
        self.stampede_detected = 0
        self.duplicate_db_queries = 0
        self.early_refresh_triggers = 0
        self.early_refresh_success = 0
        self.response_times.clear()
        self.errors.clear()
    
    def record_db_query(self, key: str):
        """DB 쿼리 기록 및 중복 감지"""
        self.db_queries += 1
        self.db_queries_per_key[key] += 1
        if self.db_queries_per_key[key] > 1:
            self.duplicate_db_queries += 1
            self.stampede_detected += 1
    
    def get_p95_response_time_ms(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times)-1)] * 1000
    
    def get_p95_lock_wait_ms(self) -> float:
        if not self.lock_wait_times:
            return 0.0
        sorted_times = sorted(self.lock_wait_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times)-1)] * 1000
    
    def get_avg_poll_count(self) -> float:
        if not self.lock_poll_counts:
            return 0.0
        return sum(self.lock_poll_counts) / len(self.lock_poll_counts)


_stats = RedisStampedeStats()
_stats_lock = threading.Lock()


def get_stats() -> RedisStampedeStats:
    return _stats


def reset_stats():
    with _stats_lock:
        _stats.reset()


# =============================================================================
# Redis Distributed Lock (피드백 반영)
# =============================================================================

class RedisDistributedLock:
    """
    Redis 분산 락 구현
    
    핵심 설계:
    1. 토큰(UUID) 기반 소유권 관리
    2. Lua 스크립트로 원자적 해제
    3. TTL은 DB 조회 시간의 3~5배
    """
    
    def __init__(self, redis_client: 'redis.Redis', key: str, ttl_ms: int = LOCK_TTL_MS):
        self.redis = redis_client
        self.key = f"lock:{key}"
        self.ttl_ms = ttl_ms
        self.token = str(uuid.uuid4())  # 소유권 토큰
        self._unlock_script = self.redis.register_script(UNLOCK_SCRIPT)
        self._extend_script = self.redis.register_script(EXTEND_LOCK_SCRIPT)
    
    def acquire(self) -> bool:
        """락 획득 시도"""
        result = self.redis.set(
            self.key,
            self.token,
            nx=True,  # 없을 때만 설정
            px=self.ttl_ms  # TTL (밀리초)
        )
        return result is True
    
    def release(self) -> bool:
        """락 해제 (토큰 일치 시에만)"""
        try:
            result = self._unlock_script(keys=[self.key], args=[self.token])
            return result == 1
        except Exception:
            return False
    
    def extend(self, additional_ms: int = None) -> bool:
        """락 연장 (토큰 일치 시에만)"""
        ttl = additional_ms or self.ttl_ms
        try:
            result = self._extend_script(keys=[self.key], args=[self.token, ttl])
            return result == 1
        except Exception:
            return False
    
    @contextmanager
    def hold(self):
        """컨텍스트 매니저로 락 사용"""
        acquired = self.acquire()
        try:
            yield acquired
        finally:
            if acquired:
                self.release()


# =============================================================================
# Redis Cache with Stampede Prevention
# =============================================================================

class RedisCacheWithStampedePrevention:
    """
    Redis 캐시 + Stampede 방지
    
    기능:
    - 분산 락 기반 single-flight
    - TTL jitter
    - Probabilistic early refresh
    - 폴링 대기 (락 실패 시)
    """
    
    def __init__(self, redis_client: 'redis.Redis'):
        self.redis = redis_client
        self._db_query_count = 0
    
    def _get_ttl_with_jitter(self) -> int:
        """TTL에 jitter 적용 (±10%)"""
        jitter = random.uniform(-CACHE_TTL_JITTER_PERCENT, CACHE_TTL_JITTER_PERCENT)
        return int(CACHE_TTL_S * (1 + jitter))
    
    def _should_early_refresh(self, ttl_remaining: float) -> bool:
        """Early refresh 여부 판단 (만료 전에만)"""
        if ttl_remaining <= 0:
            return False  # 만료됨 = early가 아님
        if ttl_remaining < CACHE_EARLY_EXPIRY_DELTA_S:
            probability = (CACHE_EARLY_EXPIRY_DELTA_S - ttl_remaining) / CACHE_EARLY_EXPIRY_DELTA_S
            return random.random() < probability * CACHE_EARLY_EXPIRY_PROBABILITY
        return False
    
    def get(
        self,
        key: str,
        fetch_fn: Callable[[], Any] = None,
        skip_cache: bool = False
    ) -> Tuple[Any, bool, float]:
        """
        캐시에서 값 조회 (Stampede 방지)
        
        Returns:
            Tuple of (value, was_cache_hit, response_time_seconds)
        """
        start = time.time()
        cache_key = f"cache:{key}"
        
        with _stats_lock:
            _stats.total_requests += 1
        
        if not skip_cache:
            # 1. 캐시 조회
            cached = self.redis.get(cache_key)
            if cached:
                # TTL 확인
                ttl = self.redis.ttl(cache_key)
                
                # Early refresh 체크 (만료 전에만)
                if self._should_early_refresh(ttl):
                    with _stats_lock:
                        _stats.early_refresh_triggers += 1
                    self._trigger_background_refresh(key, fetch_fn)
                
                with _stats_lock:
                    _stats.cache_hits += 1
                
                elapsed = time.time() - start
                with _stats_lock:
                    _stats.response_times.append(elapsed)
                
                return json.loads(cached), True, elapsed
        
        # 2. Cache miss - Single-flight 시작
        with _stats_lock:
            _stats.cache_misses += 1
        
        return self._single_flight_fetch(key, cache_key, fetch_fn, start)
    
    def _single_flight_fetch(
        self,
        key: str,
        cache_key: str,
        fetch_fn: Callable[[], Any],
        start: float
    ) -> Tuple[Any, bool, float]:
        """
        Single-flight 패턴으로 DB 조회
        
        - 락 획득 성공: DB 조회 후 캐시 채움
        - 락 획득 실패: 폴링으로 캐시 채워지길 대기
        """
        lock = RedisDistributedLock(self.redis, key)
        
        if lock.acquire():
            # 락 획득 성공 - DB 조회
            with _stats_lock:
                _stats.lock_acquired += 1
            
            try:
                # Double-check: 락 획득 사이에 다른 워커가 채웠을 수 있음
                cached = self.redis.get(cache_key)
                if cached:
                    elapsed = time.time() - start
                    with _stats_lock:
                        _stats.response_times.append(elapsed)
                    return json.loads(cached), True, elapsed
                
                # DB 조회 실행
                value = self._execute_db_query(key, fetch_fn)
                
                # 캐시 저장 (TTL jitter 적용)
                ttl = self._get_ttl_with_jitter()
                self.redis.setex(cache_key, ttl, json.dumps(value))
                
                elapsed = time.time() - start
                with _stats_lock:
                    _stats.response_times.append(elapsed)
                
                return value, False, elapsed
            
            finally:
                lock.release()
        
        else:
            # 락 획득 실패 - 폴링 대기 (피드백 핵심 3번)
            with _stats_lock:
                _stats.lock_failed += 1
            
            return self._poll_for_cache(key, cache_key, fetch_fn, start)
    
    def _poll_for_cache(
        self,
        key: str,
        cache_key: str,
        fetch_fn: Callable[[], Any],
        start: float
    ) -> Tuple[Any, bool, float]:
        """
        락 실패 시 폴링으로 캐시 대기
        
        - 5-20ms 간격으로 폴링
        - 최대 300ms 대기
        - 대기 실패 시 fallback (직접 조회)
        """
        poll_start = time.time()
        poll_count = 0
        max_wait = LOCK_POLL_MAX_WAIT_MS / 1000
        poll_interval = LOCK_POLL_INTERVAL_MS / 1000
        
        while (time.time() - poll_start) < max_wait:
            poll_count += 1
            time.sleep(poll_interval + random.uniform(0, 0.01))  # jitter
            
            cached = self.redis.get(cache_key)
            if cached:
                wait_time = time.time() - poll_start
                with _stats_lock:
                    _stats.lock_wait_times.append(wait_time)
                    _stats.lock_poll_counts.append(poll_count)
                
                elapsed = time.time() - start
                with _stats_lock:
                    _stats.response_times.append(elapsed)
                
                return json.loads(cached), True, elapsed
        
        # 폴링 실패 - fallback (락 없이 직접 조회)
        # 이 경우 중복 쿼리가 발생할 수 있음 (스탬피드 감지됨)
        with _stats_lock:
            _stats.lock_wait_times.append(time.time() - poll_start)
            _stats.lock_poll_counts.append(poll_count)
        
        value = self._execute_db_query(key, fetch_fn)
        
        # 캐시 저장 시도 (이미 있으면 덮어쓰기)
        ttl = self._get_ttl_with_jitter()
        self.redis.setex(cache_key, ttl, json.dumps(value))
        
        elapsed = time.time() - start
        with _stats_lock:
            _stats.response_times.append(elapsed)
        
        return value, False, elapsed
    
    def _execute_db_query(self, key: str, fetch_fn: Callable[[], Any] = None) -> Any:
        """DB 쿼리 실행 (시뮬레이션)"""
        with _stats_lock:
            _stats.record_db_query(key)
        
        # DB 지연 시뮬레이션 (10-50ms)
        time.sleep(random.uniform(0.01, 0.05))
        
        if fetch_fn:
            return fetch_fn()
        else:
            return {
                "key": key,
                "data": f"value_for_{key}",
                "timestamp": time.time(),
                "worker": WORKER_ID
            }
    
    def _trigger_background_refresh(self, key: str, fetch_fn: Callable[[], Any] = None):
        """백그라운드 early refresh"""
        def refresh_task():
            try:
                lock = RedisDistributedLock(self.redis, f"refresh:{key}")
                if lock.acquire():
                    try:
                        value = self._execute_db_query(key, fetch_fn)
                        cache_key = f"cache:{key}"
                        ttl = self._get_ttl_with_jitter()
                        self.redis.setex(cache_key, ttl, json.dumps(value))
                        
                        with _stats_lock:
                            _stats.early_refresh_success += 1
                    finally:
                        lock.release()
            except Exception as e:
                with _stats_lock:
                    _stats.errors.append({"type": "early_refresh", "error": str(e)})
        
        thread = threading.Thread(target=refresh_task, daemon=True)
        thread.start()
    
    def expire_key(self, key: str):
        """키 강제 만료"""
        self.redis.delete(f"cache:{key}")
    
    def expire_all_test_keys(self):
        """모든 테스트 키 만료"""
        for key in self.redis.scan_iter("cache:*"):
            self.redis.delete(key)
        for key in self.redis.scan_iter("lock:*"):
            self.redis.delete(key)
    
    def get_db_query_count(self) -> int:
        with _stats_lock:
            return _stats.db_queries


# =============================================================================
# Test Functions
# =============================================================================

def create_redis_client() -> Optional['redis.Redis']:
    """Redis 클라이언트 생성"""
    if not REDIS_AVAILABLE:
        print(f"{STAGE_NAME} Redis 모듈 없음 - pip install redis 필요")
        return None
    
    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=5
        )
        client.ping()
        print(f"{STAGE_NAME} Redis 연결 성공: {REDIS_HOST}:{REDIS_PORT}")
        return client
    except Exception as e:
        print(f"{STAGE_NAME} Redis 연결 실패: {e}")
        return None


def run_hot_key_stampede_test_redis(
    cache: RedisCacheWithStampedePrevention,
    num_requests: int = 100,
    num_threads: int = 50
) -> Dict[str, Any]:
    """
    Hot Key Stampede 테스트 (Redis 버전)
    
    멀티스레드로 동일 키에 동시 접근하여 스탬피드 방지 검증
    """
    print(f"\n{STAGE_NAME} Hot Key Stampede Test: {num_requests} requests, {num_threads} threads")
    
    reset_stats()
    cache.expire_all_test_keys()
    
    key = "hot_product_redis_1"
    results = {
        "total_requests": num_requests,
        "threads": num_threads,
        "response_times": [],
        "errors": []
    }
    
    def make_request():
        try:
            value, was_hit, elapsed = cache.get(key)
            results["response_times"].append(elapsed)
        except Exception as e:
            results["errors"].append(str(e))
    
    # 멀티스레드 실행
    threads = []
    for _ in range(num_requests):
        t = threading.Thread(target=make_request)
        threads.append(t)
    
    # 동시 시작
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
    
    # 결과 수집
    stats = get_stats()
    results.update({
        "cache_hits": stats.cache_hits,
        "cache_misses": stats.cache_misses,
        "db_queries": stats.db_queries,
        "duplicate_db_queries": stats.duplicate_db_queries,
        "stampede_detected": stats.stampede_detected,
        "lock_acquired": stats.lock_acquired,
        "lock_failed": stats.lock_failed,
        "p95_response_time_ms": stats.get_p95_response_time_ms(),
        "p95_lock_wait_ms": stats.get_p95_lock_wait_ms(),
        "avg_poll_count": stats.get_avg_poll_count(),
    })
    
    # 검증
    results["stampede_prevented"] = stats.duplicate_db_queries == 0
    results["single_db_query"] = stats.db_queries == 1
    
    print(f"  Total Requests: {results['total_requests']}")
    print(f"  Cache Hits: {results['cache_hits']}")
    print(f"  DB Queries: {results['db_queries']} (expected: 1)")
    print(f"  Duplicate DB Queries: {results['duplicate_db_queries']} (expected: 0)")
    print(f"  Lock Acquired: {results['lock_acquired']}")
    print(f"  Lock Failed (polled): {results['lock_failed']}")
    print(f"  P95 Response Time: {results['p95_response_time_ms']:.2f}ms")
    print(f"  P95 Lock Wait: {results['p95_lock_wait_ms']:.2f}ms")
    print(f"  Stampede Prevented: {results['stampede_prevented']}")
    
    return results


def run_multi_key_batch_test_redis(
    cache: RedisCacheWithStampedePrevention,
    num_keys: int = 20,
    requests_per_key: int = 20
) -> Dict[str, Any]:
    """
    Multi-Key Batch Expiry 테스트 (Redis 버전)
    """
    print(f"\n{STAGE_NAME} Multi-Key Batch Test: {num_keys} keys × {requests_per_key} requests")
    
    reset_stats()
    cache.expire_all_test_keys()
    
    keys = [f"batch_key_redis_{i}" for i in range(num_keys)]
    results = {
        "num_keys": num_keys,
        "requests_per_key": requests_per_key,
        "total_requests": num_keys * requests_per_key,
        "response_times": [],
        "errors": []
    }
    
    def make_request(key: str):
        try:
            value, was_hit, elapsed = cache.get(key)
            results["response_times"].append(elapsed)
        except Exception as e:
            results["errors"].append(str(e))
    
    # 모든 요청 스레드 생성
    threads = []
    for key in keys:
        for _ in range(requests_per_key):
            t = threading.Thread(target=make_request, args=(key,))
            threads.append(t)
    
    # 섞어서 랜덤 순서로 실행
    random.shuffle(threads)
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
    
    # 결과 수집
    stats = get_stats()
    results.update({
        "db_queries": stats.db_queries,
        "duplicate_db_queries": stats.duplicate_db_queries,
        "stampede_detected": stats.stampede_detected,
        "p95_response_time_ms": stats.get_p95_response_time_ms(),
        "p95_lock_wait_ms": stats.get_p95_lock_wait_ms(),
    })
    
    results["stampede_prevented"] = stats.duplicate_db_queries == 0
    results["correct_db_queries"] = stats.db_queries <= num_keys
    
    print(f"  Total Requests: {results['total_requests']}")
    print(f"  DB Queries: {results['db_queries']} (expected max: {num_keys})")
    print(f"  Duplicate DB Queries: {results['duplicate_db_queries']} (expected: 0)")
    print(f"  P95 Response Time: {results['p95_response_time_ms']:.2f}ms")
    print(f"  Stampede Prevented: {results['stampede_prevented']}")
    
    return results


def run_early_refresh_test_redis(
    cache: RedisCacheWithStampedePrevention,
    num_iterations: int = 50
) -> Dict[str, Any]:
    """
    Early Refresh 테스트 (Redis 버전)
    """
    print(f"\n{STAGE_NAME} Early Refresh Test: {num_iterations} iterations")
    
    reset_stats()
    cache.expire_all_test_keys()
    
    key = "early_refresh_test_redis"
    
    # 초기 캐시 채우기
    cache.get(key)
    
    # TTL을 early refresh 범위로 조정
    cache.redis.expire(f"cache:{key}", CACHE_EARLY_EXPIRY_DELTA_S - 2)
    
    for _ in range(num_iterations):
        cache.get(key)
        time.sleep(0.02)  # 20ms 간격
    
    # 백그라운드 완료 대기
    time.sleep(0.5)
    
    stats = get_stats()
    
    results = {
        "iterations": num_iterations,
        "early_refresh_triggers": stats.early_refresh_triggers,
        "early_refresh_success": stats.early_refresh_success,
        "trigger_rate": stats.early_refresh_triggers / num_iterations if num_iterations > 0 else 0,
    }
    
    if stats.early_refresh_triggers > 0:
        results["success_rate"] = stats.early_refresh_success / stats.early_refresh_triggers
    else:
        results["success_rate"] = 1.0
    
    print(f"  Iterations: {results['iterations']}")
    print(f"  Early Refresh Triggers: {results['early_refresh_triggers']}")
    print(f"  Early Refresh Success: {results['early_refresh_success']}")
    print(f"  Success Rate: {results['success_rate']*100:.1f}%")
    
    return results


def run_full_redis_test() -> Dict[str, Any]:
    """전체 Redis 테스트 실행"""
    print("=" * 70)
    print(f"{STAGE_NAME} Full Redis Cache Stampede Prevention Test")
    print("=" * 70)
    print(f"Worker ID: {WORKER_ID}")
    print(f"Lock TTL: {LOCK_TTL_MS}ms (DB time × 5)")
    print(f"Poll Interval: {LOCK_POLL_INTERVAL_MS}ms")
    print(f"Max Poll Wait: {LOCK_POLL_MAX_WAIT_MS}ms")
    print(f"TTL Jitter: ±{CACHE_TTL_JITTER_PERCENT*100:.0f}%")
    print()
    
    results = {
        "stage": "35-redis",
        "name": "Cache Stampede Prevention (Redis)",
        "start_time": datetime.now().isoformat(),
        "worker_id": WORKER_ID,
        "config": {
            "lock_ttl_ms": LOCK_TTL_MS,
            "poll_interval_ms": LOCK_POLL_INTERVAL_MS,
            "max_poll_wait_ms": LOCK_POLL_MAX_WAIT_MS,
            "ttl_jitter_percent": CACHE_TTL_JITTER_PERCENT,
        },
        "tests": {},
        "verification": {}
    }
    
    # Redis 연결
    redis_client = create_redis_client()
    if not redis_client:
        results["error"] = "Redis 연결 실패"
        return results
    
    cache = RedisCacheWithStampedePrevention(redis_client)
    
    # Test 1: Hot Key Stampede
    print("\n" + "=" * 50)
    print("SC-35-1: Hot Key Expiry Stampede (Redis)")
    print("=" * 50)
    results["tests"]["hot_key_stampede"] = run_hot_key_stampede_test_redis(
        cache, num_requests=100, num_threads=50
    )
    
    # Test 2: Multi-Key Batch
    print("\n" + "=" * 50)
    print("SC-35-3: Multi-Key Batch Expiry (Redis)")
    print("=" * 50)
    results["tests"]["multi_key_batch"] = run_multi_key_batch_test_redis(
        cache, num_keys=20, requests_per_key=20
    )
    
    # Test 3: Early Refresh
    print("\n" + "=" * 50)
    print("SC-35-2: Probabilistic Early Expiration (Redis)")
    print("=" * 50)
    results["tests"]["early_refresh"] = run_early_refresh_test_redis(
        cache, num_iterations=50
    )
    
    # Verification
    hot_key = results["tests"]["hot_key_stampede"]
    multi_key = results["tests"]["multi_key_batch"]
    early = results["tests"]["early_refresh"]
    
    results["verification"] = {
        "sc_35_1": {
            "single_db_query": hot_key["single_db_query"],
            "stampede_prevented": hot_key["stampede_prevented"],
            "p95_response_ok": hot_key["p95_response_time_ms"] < TARGET_RESPONSE_TIME_MS,
            "p95_lock_wait_ok": hot_key["p95_lock_wait_ms"] < TARGET_RESPONSE_TIME_MS,
            "all_passed": (
                hot_key["single_db_query"] and
                hot_key["stampede_prevented"] and
                hot_key["p95_lock_wait_ms"] < TARGET_RESPONSE_TIME_MS
            )
        },
        "sc_35_3": {
            "correct_db_queries": multi_key["correct_db_queries"],
            "stampede_prevented": multi_key["stampede_prevented"],
            "all_passed": multi_key["correct_db_queries"] and multi_key["stampede_prevented"]
        },
        "sc_35_2": {
            "triggers_detected": early["early_refresh_triggers"] > 0,
            "success_rate_ok": early["success_rate"] >= 0.9,
            "all_passed": early["early_refresh_triggers"] > 0 and early["success_rate"] >= 0.9
        }
    }
    
    results["end_time"] = datetime.now().isoformat()
    results["all_passed"] = all(
        v["all_passed"] for v in results["verification"].values()
    )
    
    # Summary
    print("\n" + "=" * 70)
    print(f"{STAGE_NAME} TEST SUMMARY - Redis Environment")
    print("=" * 70)
    print()
    print("검증 기준 (Redis 분산 락 환경):")
    print(f"  • 키당 DB query: 1회")
    print(f"  • Stampede occurrence: 0")
    print(f"  • 락 대기 p95: < {TARGET_RESPONSE_TIME_MS}ms")
    print(f"  • Early refresh 성공률: ≥ 90%")
    print()
    print("결과:")
    for scenario, verification in results["verification"].items():
        status = "✅ PASS" if verification["all_passed"] else "❌ FAIL"
        print(f"  {scenario}: {status}")
    print()
    print(f"전체 결과: {'✅ ALL PASSED' if results['all_passed'] else '❌ FAILED'}")
    print("=" * 70)
    
    # 정리
    cache.expire_all_test_keys()
    
    return results


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    results = run_full_redis_test()
    
    # 결과 저장
    output_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "stage35_redis_results.json")
    
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\nResults saved to: {output_path}")
