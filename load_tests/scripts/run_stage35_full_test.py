"""
Stage 35 Full Scale Test Runner
Cache Stampede Prevention Test Suite
"""
import sys
import os
import time
import json
from datetime import datetime

# Ensure proper path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios.stage35_cache_stampede import (
    run_hot_key_stampede_test,
    run_multi_key_batch_test,
    run_early_refresh_test,
    reset_cache,
    reset_stats,
    get_stats,
    get_cache,
    generate_stage35_report,
    TARGET_RESPONSE_TIME_MS,
    TARGET_RESPONSE_TIME_MS_SIMULATION,
    TARGET_RESPONSE_TIME_MS_REDIS,
)


# =============================================================================
# 시뮬레이션 vs Redis 환경 검증 기준 정의
# =============================================================================
"""
업계 표준 Cache Stampede 검증 기준 (피드백 반영):

[시뮬레이션(in-memory) 환경에서 검증할 것 - 불변 조건]
1. 키당 DB query 1개 (중복 제거)
2. Stampede occurrence = 0
3. Early refresh는 "만료 전(pre-expiry)"에만 발생
4. Early refresh 트리거 시 성공률 100% (테스트 종료 전 완료 대기)

[Redis 연동 후에만 의미 있는 것]
1. p95/p99 응답시간 목표 (p95 < 50~100ms)
2. 멀티 워커/멀티 인스턴스에서 키당 1 query 유지
3. 락 타임아웃/스핀/갱신 실패 시 fallback 동작
4. 분산 락(Redlock) 정합성
"""

VERIFICATION_CRITERIA = {
    "simulation": {
        "db_query_per_key_max": 1,
        "stampede_occurrence": 0,
        "early_refresh_success_rate": 1.0,  # 100%
        "description": "In-memory 환경: 로직 정확성 검증 (응답시간은 의미 없음)"
    },
    "redis": {
        "p95_response_time_ms": 100,
        "p99_response_time_ms": 200,
        "db_query_per_key_max": 1,
        "stampede_occurrence": 0,
        "description": "Redis 환경: 분산 락 + 실제 latency 검증"
    }
}


def wait_for_background_refreshes(cache, timeout_s: float = 2.0) -> bool:
    """
    백그라운드 refresh 완료 대기.
    테스트 종료 전 pending refresh가 모두 완료되도록 함.
    """
    import time
    start = time.time()
    while time.time() - start < timeout_s:
        with cache._lock:
            if len(cache._refreshing_keys) == 0:
                return True
        time.sleep(0.05)
    return False


def run_full_stage35_test():
    """Run comprehensive Stage 35 test suite"""
    
    print("=" * 70)
    print("[Stage35] FULL SCALE Cache Stampede Prevention Test")
    print("=" * 70)
    print(f"Start Time: {datetime.now().isoformat()}")
    print()
    
    results = {
        "stage": "35",
        "name": "Cache Stampede Prevention",
        "start_time": datetime.now().isoformat(),
        "tests": {},
        "verification": {}
    }
    
    # =========================================================================
    # SC-35-1: Hot Key Expiry Stampede (1000 concurrent requests)
    # =========================================================================
    print("SC-35-1: Hot Key Expiry Stampede (1000 concurrent requests)")
    print("-" * 50)
    reset_cache()
    reset_stats()
    
    result1 = run_hot_key_stampede_test(1000)
    results["tests"]["hot_key_stampede"] = result1
    
    # Calculate metrics
    if result1["response_times"]:
        avg_rt = sum(result1["response_times"]) / len(result1["response_times"]) * 1000
        sorted_times = sorted(result1["response_times"])
        p95_idx = int(len(sorted_times) * 0.95)
        p95_rt = sorted_times[p95_idx] * 1000 if p95_idx < len(sorted_times) else avg_rt
    else:
        avg_rt = 0
        p95_rt = 0
    
    print(f"  [METRIC] Total Requests: {result1['total_requests']}")
    print(f"  [METRIC] Cache Hits: {result1['cache_hits']}")
    print(f"  [METRIC] Cache Misses: {result1['cache_misses']}")
    print(f"  [METRIC] DB Queries: {result1['db_queries']}")
    print(f"  [METRIC] Avg Response Time: {avg_rt:.2f}ms")
    print(f"  [METRIC] P95 Response Time: {p95_rt:.2f}ms")
    print()
    
    # Verification checks
    check_db_dedup = result1["db_queries"] <= 1
    check_stampede = result1["stampede_prevented"]
    check_response_time = avg_rt < TARGET_RESPONSE_TIME_MS
    
    print(f"  [CHECK] DB Query Deduplication (1 query per key): {'✅ PASS' if check_db_dedup else '❌ FAIL'}")
    print(f"  [CHECK] Stampede Prevented: {'✅ PASS' if check_stampede else '❌ FAIL'}")
    print(f"  [CHECK] Response Time < {TARGET_RESPONSE_TIME_MS}ms: {'✅ PASS' if check_response_time else '❌ FAIL'}")
    print()
    
    results["verification"]["sc_35_1"] = {
        "db_query_dedup": check_db_dedup,
        "stampede_prevented": check_stampede,
        "response_time_ok": check_response_time,
        "all_passed": check_db_dedup and check_stampede and check_response_time
    }
    
    # =========================================================================
    # SC-35-3: Multi-Key Batch Expiry (100 keys × 100 requests)
    # =========================================================================
    print("SC-35-3: Multi-Key Batch Expiry (100 keys × 100 requests)")
    print("-" * 50)
    reset_cache()
    
    result2 = run_multi_key_batch_test(100, 100)
    results["tests"]["multi_key_batch"] = result2
    
    print(f"  [METRIC] Total Requests: {result2['total_requests']}")
    print(f"  [METRIC] Total DB Queries: {result2['total_db_queries']}")
    print(f"  [METRIC] Expected Max DB Queries: {result2['num_keys']}")
    print(f"  [METRIC] Avg Response Time: {result2['avg_response_time_ms']:.2f}ms")
    print()
    
    # Verification checks
    check_batch_dedup = result2["total_db_queries"] <= result2["num_keys"]
    check_batch_stampede = result2["stampede_prevented"]
    check_batch_rt = result2["avg_response_time_ms"] < TARGET_RESPONSE_TIME_MS
    
    print(f"  [CHECK] DB Query Deduplication (≤100 queries): {'✅ PASS' if check_batch_dedup else '❌ FAIL'}")
    print(f"  [CHECK] Batch Stampede Prevented: {'✅ PASS' if check_batch_stampede else '❌ FAIL'}")
    print(f"  [CHECK] Response Time < {TARGET_RESPONSE_TIME_MS}ms: {'✅ PASS' if check_batch_rt else '❌ FAIL'}")
    print()
    
    results["verification"]["sc_35_3"] = {
        "db_query_dedup": check_batch_dedup,
        "stampede_prevented": check_batch_stampede,
        "response_time_ok": check_batch_rt,
        "all_passed": check_batch_dedup and check_batch_stampede and check_batch_rt
    }
    
    # =========================================================================
    # SC-35-2: Probabilistic Early Expiration
    # =========================================================================
    print("SC-35-2: Probabilistic Early Expiration (200 iterations)")
    print("-" * 50)
    reset_cache()
    
    result3 = run_early_refresh_test(200)
    
    # 백그라운드 refresh 완료 대기 (최대 2초)
    cache = get_cache()
    bg_complete = wait_for_background_refreshes(cache, timeout_s=2.0)
    
    # 통계 다시 조회 (백그라운드 완료 후)
    stats = get_stats()
    actual_triggers = stats.early_refresh_triggers
    actual_success = stats.early_refresh_success
    
    results["tests"]["early_refresh"] = {
        "iterations": result3["iterations"],
        "early_refresh_triggers": actual_triggers,
        "early_refresh_success": actual_success,
        "trigger_rate": result3["trigger_rate"],
        "background_complete": bg_complete,
    }
    
    print(f"  [METRIC] Iterations: {result3['iterations']}")
    print(f"  [METRIC] Early Refresh Triggers: {actual_triggers}")
    print(f"  [METRIC] Early Refresh Success: {actual_success}")
    print(f"  [METRIC] Trigger Rate: {result3['trigger_rate']*100:.1f}%")
    print(f"  [METRIC] Background Complete: {bg_complete}")
    print()
    
    # Verification checks
    # Early refresh 트리거 시 100% 성공해야 함 (시뮬레이션 환경에서)
    if actual_triggers > 0:
        success_rate = actual_success / actual_triggers
        check_early_success = success_rate >= VERIFICATION_CRITERIA["simulation"]["early_refresh_success_rate"]
    else:
        success_rate = 1.0
        check_early_success = True  # 트리거 없으면 통과
    
    check_early_trigger = actual_triggers >= 1  # 최소 1회는 트리거되어야 함
    
    print(f"  [CHECK] Early Refresh Triggering (≥1): {'✅ PASS' if check_early_trigger else '❌ FAIL'}")
    print(f"  [CHECK] Early Refresh Success Rate ({success_rate*100:.0f}% = 100%): {'✅ PASS' if check_early_success else '❌ FAIL'}")
    if not check_early_success:
        print(f"         ⚠️  {actual_triggers}회 트리거 중 {actual_success}회 성공 ({success_rate*100:.1f}%)")
    print()
    
    results["verification"]["sc_35_2"] = {
        "early_trigger_working": check_early_trigger,
        "early_refresh_success_rate": success_rate,
        "success_rate_target": VERIFICATION_CRITERIA["simulation"]["early_refresh_success_rate"],
        "all_passed": check_early_trigger and check_early_success
    }
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("=" * 70)
    print("[Stage35] TEST SUMMARY - Simulation Environment")
    print("=" * 70)
    print()
    print("검증 기준 (시뮬레이션 환경 - 불변 조건):")
    print(f"  • 키당 DB query: ≤ {VERIFICATION_CRITERIA['simulation']['db_query_per_key_max']}")
    print(f"  • Stampede occurrence: = {VERIFICATION_CRITERIA['simulation']['stampede_occurrence']}")
    print(f"  • Early refresh 성공률: = {VERIFICATION_CRITERIA['simulation']['early_refresh_success_rate']*100:.0f}%")
    print()
    print("⚠️  주의: 응답시간은 시뮬레이션에서 의미 없음 (Redis 연동 후 검증 필요)")
    print()
    
    all_tests_passed = (
        results["verification"]["sc_35_1"]["all_passed"] and
        results["verification"]["sc_35_3"]["all_passed"] and
        results["verification"]["sc_35_2"]["all_passed"]
    )
    
    results["end_time"] = datetime.now().isoformat()
    results["all_passed"] = all_tests_passed
    
    print()
    print("Scenario Results:")
    print(f"  SC-35-1 (Hot Key Stampede):      {'✅ PASS' if results['verification']['sc_35_1']['all_passed'] else '❌ FAIL'}")
    print(f"  SC-35-2 (Early Expiration):      {'✅ PASS' if results['verification']['sc_35_2']['all_passed'] else '❌ FAIL'}")
    print(f"  SC-35-3 (Multi-Key Batch):       {'✅ PASS' if results['verification']['sc_35_3']['all_passed'] else '❌ FAIL'}")
    print()
    print(f"Overall Result: {'✅ ALL TESTS PASSED' if all_tests_passed else '❌ SOME TESTS FAILED'}")
    print()
    print(f"End Time: {results['end_time']}")
    print("=" * 70)
    
    return results


if __name__ == "__main__":
    results = run_full_stage35_test()
    
    # Save results to JSON
    output_path = os.path.join(os.path.dirname(__file__), "reports", "stage35_results.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")
