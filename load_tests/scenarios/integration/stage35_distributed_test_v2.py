"""
Stage 35: Multi-Worker Distributed Test v2

피드백 반영:
1. DB Query 카운터 = "실제 DB 쿼리 직전"만 증가 (통일된 정의)
2. 테스트별 글로벌 카운터 분리 (test1_stats, test2_stats)
3. 워커별 로컬 통계 ≠ 글로벌 통계 혼란 제거
4. 실제 DB 쿼리 발생 여부만 추적

실행:
    python stage35_distributed_test_v2.py
"""

import os
import sys
import time
import threading
import json
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Optional

# Redis 임포트
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    print("Redis not available. Run: pip install redis")
    sys.exit(1)

STAGE_NAME = "[Stage35-v2]"

# 설정
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
WORKER_ID = os.environ.get("WORKER_ID", f"w-{str(uuid.uuid4())[:6]}")

# 락 설정
DB_QUERY_TIME_MS = 30  # 예상 DB 쿼리 시간
LOCK_TTL_MS = DB_QUERY_TIME_MS * 5  # 150ms (피드백: ×3~5)
LOCK_POLL_INTERVAL_MS = 10
LOCK_POLL_MAX_WAIT_MS = 300

# Lua 스크립트: 토큰 검증 후 삭제
UNLOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


@dataclass
class TestConfig:
    """테스트 설정"""
    test_id: str
    threads: int
    requests_per_thread: int
    cache_key: str
    
    @property
    def stats_key(self) -> str:
        return f"test:{self.test_id}:stats"
    
    @property
    def db_query_count_key(self) -> str:
        return f"test:{self.test_id}:db_query_count"
    
    @property
    def lock_key(self) -> str:
        return f"lock:{self.cache_key}"


class StampedeTestV2:
    """
    개선된 Stampede 테스트
    
    핵심 원칙:
    - DB Query 카운트는 "실제 DB 쿼리 직전"에서만 증가
    - 모든 통계는 테스트별로 분리된 Redis 키 사용
    - 워커별 로컬 통계 없음 → 글로벌 통계만 사용
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.worker_id = WORKER_ID
        self._unlock_script = self.redis.register_script(UNLOCK_SCRIPT)
    
    def reset_test(self, config: TestConfig):
        """테스트 초기화 - 모든 관련 키 삭제"""
        self.redis.delete(config.cache_key)
        self.redis.delete(config.lock_key)
        self.redis.delete(config.stats_key)
        self.redis.delete(config.db_query_count_key)
        
        # 통계 초기화
        self.redis.hset(config.stats_key, mapping={
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "actual_db_queries": 0,  # 핵심: "실제" DB 쿼리
            "lock_acquired": 0,
            "lock_failed": 0,
            "lock_wait_success": 0,  # 폴링 후 캐시 히트
            "lock_wait_timeout": 0,  # 폴링 타임아웃
        })
    
    def incr_stat(self, config: TestConfig, field: str, amount: int = 1):
        """통계 원자적 증가"""
        self.redis.hincrby(config.stats_key, field, amount)
    
    def get_stats(self, config: TestConfig) -> dict:
        """통계 조회"""
        raw = self.redis.hgetall(config.stats_key)
        return {k: int(v) for k, v in raw.items()}
    
    def acquire_lock(self, config: TestConfig) -> tuple:
        """락 획득 시도"""
        token = f"{self.worker_id}:{uuid.uuid4()}"
        result = self.redis.set(config.lock_key, token, nx=True, px=LOCK_TTL_MS)
        return result is True, token
    
    def release_lock(self, config: TestConfig, token: str) -> bool:
        """락 해제 (토큰 검증)"""
        try:
            return self._unlock_script(keys=[config.lock_key], args=[token]) == 1
        except Exception:
            return False
    
    def execute_db_query(self, config: TestConfig) -> dict:
        """
        실제 DB 쿼리 실행
        
        핵심: DB 쿼리 카운트는 여기서만 증가!
        - "시도"나 "함수 진입"이 아닌 "실제 실행 직전"
        """
        # 1. 글로벌 쿼리 카운트 증가 (원자적)
        query_number = self.redis.incr(config.db_query_count_key)
        
        # 2. 통계 증가 (실제 DB 쿼리)
        self.incr_stat(config, "actual_db_queries")
        
        # 3. 중복 쿼리 감지 (query_number > 1이면 중복)
        is_duplicate = query_number > 1
        
        if is_duplicate:
            print(f"  ⚠️ [{self.worker_id}] DUPLICATE DB QUERY #{query_number}")
        
        # 4. DB 쿼리 시뮬레이션
        time.sleep(DB_QUERY_TIME_MS / 1000)
        
        return {
            "data": f"value_from_db",
            "queried_by": self.worker_id,
            "query_number": query_number,
            "is_duplicate": is_duplicate,
            "timestamp": time.time()
        }
    
    def get_with_stampede_prevention(self, config: TestConfig) -> dict:
        """
        Stampede 방지 포함 데이터 조회
        
        반환: {
            "data": ...,
            "source": "cache_hit" | "db_query" | "lock_wait_hit" | "lock_wait_fallback",
            "elapsed_ms": float
        }
        """
        start = time.time()
        self.incr_stat(config, "total_requests")
        
        # 1. 캐시 조회
        cached = self.redis.get(config.cache_key)
        if cached:
            self.incr_stat(config, "cache_hits")
            return {
                "data": json.loads(cached),
                "source": "cache_hit",
                "elapsed_ms": (time.time() - start) * 1000
            }
        
        self.incr_stat(config, "cache_misses")
        
        # 2. 락 획득 시도
        acquired, token = self.acquire_lock(config)
        
        if acquired:
            self.incr_stat(config, "lock_acquired")
            try:
                # Double-check (락 획득 사이에 다른 워커가 캐시 채웠을 수 있음)
                cached = self.redis.get(config.cache_key)
                if cached:
                    return {
                        "data": json.loads(cached),
                        "source": "cache_hit",
                        "elapsed_ms": (time.time() - start) * 1000
                    }
                
                # 실제 DB 쿼리 실행
                db_result = self.execute_db_query(config)
                
                # 캐시 저장 (TTL 60초)
                self.redis.setex(config.cache_key, 60, json.dumps(db_result))
                
                return {
                    "data": db_result,
                    "source": "db_query",
                    "elapsed_ms": (time.time() - start) * 1000
                }
            finally:
                self.release_lock(config, token)
        else:
            self.incr_stat(config, "lock_failed")
            
            # 3. 락 실패 → 폴링 대기
            poll_start = time.time()
            max_wait_s = LOCK_POLL_MAX_WAIT_MS / 1000
            
            while (time.time() - poll_start) < max_wait_s:
                time.sleep(LOCK_POLL_INTERVAL_MS / 1000)
                cached = self.redis.get(config.cache_key)
                if cached:
                    self.incr_stat(config, "lock_wait_success")
                    return {
                        "data": json.loads(cached),
                        "source": "lock_wait_hit",
                        "elapsed_ms": (time.time() - start) * 1000
                    }
            
            # 4. 폴링 타임아웃 → Fallback (중복 쿼리 가능)
            self.incr_stat(config, "lock_wait_timeout")
            print(f"  ⚠️ [{self.worker_id}] Lock wait timeout, fallback to DB query")
            
            db_result = self.execute_db_query(config)
            self.redis.setex(config.cache_key, 60, json.dumps(db_result))
            
            return {
                "data": db_result,
                "source": "lock_wait_fallback",
                "elapsed_ms": (time.time() - start) * 1000
            }
    
    def run_test(self, config: TestConfig) -> dict:
        """테스트 실행"""
        print(f"\n{'='*60}")
        print(f"TEST: {config.test_id}")
        print(f"  Threads: {config.threads}")
        print(f"  Requests/Thread: {config.requests_per_thread}")
        print(f"  Total Requests: {config.threads * config.requests_per_thread}")
        print(f"{'='*60}")
        
        # 초기화
        self.reset_test(config)
        
        response_times = []
        response_times_lock = threading.Lock()
        
        def worker_task(thread_id: int):
            local_times = []
            for _ in range(config.requests_per_thread):
                result = self.get_with_stampede_prevention(config)
                local_times.append(result["elapsed_ms"])
            
            with response_times_lock:
                response_times.extend(local_times)
            
            return thread_id
        
        # 멀티스레드 실행
        with ThreadPoolExecutor(max_workers=config.threads) as executor:
            futures = [executor.submit(worker_task, i) for i in range(config.threads)]
            for future in as_completed(futures):
                future.result()
        
        # 통계 수집
        stats = self.get_stats(config)
        
        # P95 계산
        sorted_times = sorted(response_times)
        p95_idx = int(len(sorted_times) * 0.95)
        p95 = sorted_times[p95_idx] if sorted_times else 0
        
        # 실제 DB 쿼리 카운트 확인
        actual_query_count = int(self.redis.get(config.db_query_count_key) or 0)
        
        # 결과
        result = {
            "test_id": config.test_id,
            "config": {
                "threads": config.threads,
                "requests_per_thread": config.requests_per_thread,
            },
            "stats": {
                "total_requests": stats.get("total_requests", 0),
                "cache_hits": stats.get("cache_hits", 0),
                "cache_misses": stats.get("cache_misses", 0),
                "actual_db_queries": stats.get("actual_db_queries", 0),
                "lock_acquired": stats.get("lock_acquired", 0),
                "lock_failed": stats.get("lock_failed", 0),
                "lock_wait_success": stats.get("lock_wait_success", 0),
                "lock_wait_timeout": stats.get("lock_wait_timeout", 0),
            },
            "verification": {
                "global_db_query_count": actual_query_count,
                "duplicate_queries": max(0, actual_query_count - 1),
                "stampede_prevented": actual_query_count <= 1,
            },
            "performance": {
                "p95_response_ms": round(p95, 2),
            },
        }
        
        # 출력
        print(f"\n📊 결과 ({config.test_id})")
        print(f"  ├─ Total Requests:     {result['stats']['total_requests']}")
        print(f"  ├─ Cache Hits:         {result['stats']['cache_hits']}")
        print(f"  ├─ Cache Misses:       {result['stats']['cache_misses']}")
        print(f"  ├─ Actual DB Queries:  {result['stats']['actual_db_queries']}")
        print(f"  ├─ Lock Acquired:      {result['stats']['lock_acquired']}")
        print(f"  ├─ Lock Failed:        {result['stats']['lock_failed']}")
        print(f"  │   ├─ Wait Success:   {result['stats']['lock_wait_success']}")
        print(f"  │   └─ Wait Timeout:   {result['stats']['lock_wait_timeout']}")
        print(f"  ├─ P95 Response:       {result['performance']['p95_response_ms']}ms")
        print(f"  └─ Verification:")
        print(f"      ├─ Global DB Count:  {result['verification']['global_db_query_count']}")
        print(f"      ├─ Duplicate Queries: {result['verification']['duplicate_queries']}")
        print(f"      └─ Stampede Prevented: {'✅ YES' if result['verification']['stampede_prevented'] else '❌ NO'}")
        
        return result


def run_all_tests():
    """전체 테스트 실행"""
    print("=" * 70)
    print(f"{STAGE_NAME} Cache Stampede Prevention Test v2")
    print("=" * 70)
    print(f"Worker ID: {WORKER_ID}")
    print(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
    print(f"Lock TTL: {LOCK_TTL_MS}ms (DB Time {DB_QUERY_TIME_MS}ms × 5)")
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
        print("✅ Redis 연결 성공")
    except Exception as e:
        print(f"❌ Redis 연결 실패: {e}")
        return None
    
    tester = StampedeTestV2(client)
    
    # 테스트 구성
    tests = [
        TestConfig(
            test_id="test1_8x50",
            threads=8,
            requests_per_thread=50,
            cache_key="cache:stampede_test_v2_1"
        ),
        TestConfig(
            test_id="test2_16x100",
            threads=16,
            requests_per_thread=100,
            cache_key="cache:stampede_test_v2_2"
        ),
    ]
    
    results = []
    for config in tests:
        result = tester.run_test(config)
        results.append(result)
    
    # 최종 요약
    print("\n" + "=" * 70)
    print(f"{STAGE_NAME} FINAL SUMMARY")
    print("=" * 70)
    
    all_passed = all(r["verification"]["stampede_prevented"] for r in results)
    
    print("\n┌────────────────┬──────────────────┬──────────────┬─────────────┐")
    print("│ Test           │ Actual DB Queries│ Duplicates   │ Result      │")
    print("├────────────────┼──────────────────┼──────────────┼─────────────┤")
    for r in results:
        db_q = r["verification"]["global_db_query_count"]
        dup = r["verification"]["duplicate_queries"]
        passed = r["verification"]["stampede_prevented"]
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"│ {r['test_id']:<14} │ {db_q:<16} │ {dup:<12} │ {status:<11} │")
    print("└────────────────┴──────────────────┴──────────────┴─────────────┘")
    
    print(f"\n전체 결과: {'✅ ALL PASSED' if all_passed else '❌ SOME FAILED'}")
    print("=" * 70)
    
    # 결과 저장
    output = {
        "stage": "35-v2",
        "worker_id": WORKER_ID,
        "timestamp": datetime.now().isoformat(),
        "tests": results,
        "all_passed": all_passed,
    }
    
    output_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"stage35_v2_{WORKER_ID}.json")
    
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n결과 저장: {output_path}")
    
    return output


if __name__ == "__main__":
    run_all_tests()
