"""
Stage 35: Multi-Worker Distributed Test

여러 프로세스/컨테이너에서 동시에 같은 키에 접근할 때
Redis 분산 락이 제대로 작동하는지 검증

실행:
    docker-compose -f docker-compose.stage35.yml up -d redis
    # 터미널 4개에서 동시 실행
    docker-compose -f docker-compose.stage35.yml run --rm stampede-test-redis
"""

import os
import sys
import time
import threading
import json
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Redis 임포트
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    print("Redis not available. Run: pip install redis")
    REDIS_AVAILABLE = False
    sys.exit(1)

STAGE_NAME = "[Stage35-MultiWorker]"

# 설정
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
WORKER_ID = os.environ.get("WORKER_ID", str(uuid.uuid4())[:8])
NUM_WORKERS = int(os.environ.get("NUM_WORKERS", "4"))
REQUESTS_PER_WORKER = int(os.environ.get("REQUESTS_PER_WORKER", "50"))

# 락 설정
LOCK_TTL_MS = 500
LOCK_POLL_INTERVAL_MS = 10
LOCK_POLL_MAX_WAIT_MS = 500

# Lua 스크립트
UNLOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


class DistributedStampedeTest:
    """분산 환경 Stampede 테스트"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.worker_id = WORKER_ID
        self._unlock_script = self.redis.register_script(UNLOCK_SCRIPT)
        
        # 통계 (Redis 기반 - 워커 간 공유)
        self.stats_key = "test:stats:stage35"
    
    def reset_stats(self):
        """통계 초기화"""
        self.redis.delete(self.stats_key)
        self.redis.hset(self.stats_key, mapping={
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "db_queries": 0,
            "lock_acquired": 0,
            "lock_failed": 0,
            "duplicate_queries": 0,
        })
    
    def incr_stat(self, field: str, amount: int = 1):
        """통계 증가 (원자적)"""
        self.redis.hincrby(self.stats_key, field, amount)
    
    def get_stats(self) -> dict:
        """통계 조회"""
        stats = self.redis.hgetall(self.stats_key)
        return {k: int(v) for k, v in stats.items()}
    
    def acquire_lock(self, key: str) -> tuple:
        """락 획득"""
        lock_key = f"lock:{key}"
        token = f"{self.worker_id}:{uuid.uuid4()}"
        
        result = self.redis.set(lock_key, token, nx=True, px=LOCK_TTL_MS)
        return result is True, token, lock_key
    
    def release_lock(self, lock_key: str, token: str) -> bool:
        """락 해제"""
        try:
            return self._unlock_script(keys=[lock_key], args=[token]) == 1
        except:
            return False
    
    def simulate_db_query(self, key: str) -> dict:
        """DB 쿼리 시뮬레이션"""
        # 쿼리 횟수 기록 (키별)
        query_count_key = f"test:query_count:{key}"
        count = self.redis.incr(query_count_key)
        
        if count > 1:
            self.incr_stat("duplicate_queries")
            print(f"  ⚠️ [{self.worker_id}] DUPLICATE QUERY for {key} (count={count})")
        
        self.incr_stat("db_queries")
        
        # DB 지연 시뮬레이션
        time.sleep(0.03)  # 30ms
        
        return {
            "key": key,
            "data": f"value_{key}",
            "queried_by": self.worker_id,
            "timestamp": time.time()
        }
    
    def get_with_stampede_prevention(self, key: str) -> tuple:
        """Stampede 방지 포함 캐시 조회"""
        cache_key = f"cache:{key}"
        start = time.time()
        
        self.incr_stat("total_requests")
        
        # 1. 캐시 조회
        cached = self.redis.get(cache_key)
        if cached:
            self.incr_stat("cache_hits")
            return json.loads(cached), True, time.time() - start
        
        self.incr_stat("cache_misses")
        
        # 2. 락 획득 시도
        acquired, token, lock_key = self.acquire_lock(key)
        
        if acquired:
            self.incr_stat("lock_acquired")
            try:
                # Double-check
                cached = self.redis.get(cache_key)
                if cached:
                    return json.loads(cached), True, time.time() - start
                
                # DB 쿼리
                value = self.simulate_db_query(key)
                
                # 캐시 저장
                self.redis.setex(cache_key, 60, json.dumps(value))
                
                return value, False, time.time() - start
            finally:
                self.release_lock(lock_key, token)
        else:
            self.incr_stat("lock_failed")
            
            # 3. 폴링 대기
            poll_start = time.time()
            max_wait = LOCK_POLL_MAX_WAIT_MS / 1000
            
            while (time.time() - poll_start) < max_wait:
                time.sleep(LOCK_POLL_INTERVAL_MS / 1000)
                cached = self.redis.get(cache_key)
                if cached:
                    return json.loads(cached), True, time.time() - start
            
            # 4. 폴링 실패 - fallback (중복 쿼리 발생 가능)
            value = self.simulate_db_query(key)
            self.redis.setex(cache_key, 60, json.dumps(value))
            return value, False, time.time() - start
    
    def run_worker_test(self, num_requests: int = 50) -> dict:
        """단일 워커 테스트 실행"""
        key = "hot_key_distributed"
        results = {
            "worker_id": self.worker_id,
            "requests": num_requests,
            "response_times": []
        }
        
        for i in range(num_requests):
            value, was_hit, elapsed = self.get_with_stampede_prevention(key)
            results["response_times"].append(elapsed * 1000)  # ms
        
        return results
    
    def run_multi_thread_test(self, num_threads: int = 4, requests_per_thread: int = 25) -> dict:
        """멀티 스레드 테스트 (단일 프로세스 내)"""
        print(f"\n{STAGE_NAME} Multi-thread test: {num_threads} threads × {requests_per_thread} requests")
        
        # 초기화
        self.reset_stats()
        self.redis.delete("cache:hot_key_distributed")
        self.redis.delete("test:query_count:hot_key_distributed")
        
        results = []
        
        def worker_task(thread_id):
            local_results = []
            for _ in range(requests_per_thread):
                value, was_hit, elapsed = self.get_with_stampede_prevention("hot_key_distributed")
                local_results.append(elapsed * 1000)
            return {"thread_id": thread_id, "times": local_results}
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_threads)]
            for future in as_completed(futures):
                results.append(future.result())
        
        # 통계 수집
        stats = self.get_stats()
        
        all_times = []
        for r in results:
            all_times.extend(r["times"])
        
        sorted_times = sorted(all_times)
        p95_idx = int(len(sorted_times) * 0.95)
        
        summary = {
            "total_requests": stats.get("total_requests", 0),
            "cache_hits": stats.get("cache_hits", 0),
            "cache_misses": stats.get("cache_misses", 0),
            "db_queries": stats.get("db_queries", 0),
            "duplicate_queries": stats.get("duplicate_queries", 0),
            "lock_acquired": stats.get("lock_acquired", 0),
            "lock_failed": stats.get("lock_failed", 0),
            "p95_response_ms": sorted_times[p95_idx] if sorted_times else 0,
            "stampede_prevented": stats.get("duplicate_queries", 0) == 0,
            "single_db_query": stats.get("db_queries", 0) == 1,
        }
        
        return summary


def run_distributed_test():
    """분산 테스트 실행"""
    print("=" * 70)
    print(f"{STAGE_NAME} Distributed Multi-Worker Stampede Test")
    print("=" * 70)
    print(f"Worker ID: {WORKER_ID}")
    print(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
    print()
    
    # Redis 연결
    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5
        )
        client.ping()
        print(f"✅ Redis 연결 성공")
    except Exception as e:
        print(f"❌ Redis 연결 실패: {e}")
        return
    
    tester = DistributedStampedeTest(client)
    
    # 테스트 1: 멀티 스레드 (4 threads × 50 requests)
    print("\n" + "=" * 50)
    print("TEST 1: Multi-Thread Stampede Test")
    print("=" * 50)
    
    result1 = tester.run_multi_thread_test(num_threads=8, requests_per_thread=50)
    
    print(f"\n결과:")
    print(f"  Total Requests: {result1['total_requests']}")
    print(f"  Cache Hits: {result1['cache_hits']}")
    print(f"  DB Queries: {result1['db_queries']} (expected: 1)")
    print(f"  Duplicate Queries: {result1['duplicate_queries']} (expected: 0)")
    print(f"  Lock Acquired: {result1['lock_acquired']}")
    print(f"  Lock Failed: {result1['lock_failed']}")
    print(f"  P95 Response: {result1['p95_response_ms']:.2f}ms")
    print(f"  Stampede Prevented: {'✅' if result1['stampede_prevented'] else '❌'}")
    print(f"  Single DB Query: {'✅' if result1['single_db_query'] else '❌'}")
    
    # 테스트 2: 더 높은 동시성 (16 threads × 100 requests)
    print("\n" + "=" * 50)
    print("TEST 2: High Concurrency Stampede Test")
    print("=" * 50)
    
    result2 = tester.run_multi_thread_test(num_threads=16, requests_per_thread=100)
    
    print(f"\n결과:")
    print(f"  Total Requests: {result2['total_requests']}")
    print(f"  Cache Hits: {result2['cache_hits']}")
    print(f"  DB Queries: {result2['db_queries']} (expected: 1)")
    print(f"  Duplicate Queries: {result2['duplicate_queries']} (expected: 0)")
    print(f"  Lock Acquired: {result2['lock_acquired']}")
    print(f"  Lock Failed: {result2['lock_failed']}")
    print(f"  P95 Response: {result2['p95_response_ms']:.2f}ms")
    print(f"  Stampede Prevented: {'✅' if result2['stampede_prevented'] else '❌'}")
    print(f"  Single DB Query: {'✅' if result2['single_db_query'] else '❌'}")
    
    # 최종 결과
    print("\n" + "=" * 70)
    print(f"{STAGE_NAME} FINAL SUMMARY")
    print("=" * 70)
    
    all_passed = (
        result1['stampede_prevented'] and result1['single_db_query'] and
        result2['stampede_prevented'] and result2['single_db_query']
    )
    
    print(f"\nTest 1 (8×50):  {'✅ PASS' if result1['stampede_prevented'] and result1['single_db_query'] else '❌ FAIL'}")
    print(f"Test 2 (16×100): {'✅ PASS' if result2['stampede_prevented'] and result2['single_db_query'] else '❌ FAIL'}")
    print(f"\n전체 결과: {'✅ ALL PASSED' if all_passed else '❌ FAILED'}")
    print("=" * 70)
    
    # 결과 저장
    results = {
        "stage": "35-distributed",
        "worker_id": WORKER_ID,
        "timestamp": datetime.now().isoformat(),
        "test1_8x50": result1,
        "test2_16x100": result2,
        "all_passed": all_passed
    }
    
    output_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"stage35_distributed_{WORKER_ID}.json")
    
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: {output_path}")
    
    return results


if __name__ == "__main__":
    run_distributed_test()
