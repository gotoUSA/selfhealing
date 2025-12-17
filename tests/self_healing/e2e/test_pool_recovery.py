#!/usr/bin/env python3
"""
Stage 26: Connection Pool 고갈 → 회복 테스트

Pool 3개 설정에서:
1. 동시에 5개 slow query 실행 → Pool 고갈
2. 대기 후 → Pool 회복 확인
"""
import requests
import concurrent.futures
import time
import sys

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def test_endpoint(name, endpoint, timeout=15):
    """엔드포인트 테스트"""
    try:
        start = time.time()
        r = requests.get(f"{BASE_URL}{endpoint}", timeout=timeout)
        elapsed = (time.time() - start) * 1000
        return {
            "name": name,
            "status": r.status_code,
            "success": r.status_code == 200,
            "time_ms": elapsed,
            "detail": r.json() if r.status_code == 200 else r.text[:100],
        }
    except requests.Timeout:
        return {"name": name, "status": 0, "success": False, "time_ms": 15000, "detail": "Timeout"}
    except Exception as e:
        return {"name": name, "status": -1, "success": False, "time_ms": 0, "detail": str(e)}


def run_concurrent_slow_queries(count, delay_endpoint):
    """동시에 여러 slow query 실행"""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(test_endpoint, f"slow_{i}", delay_endpoint, timeout=20) for i in range(count)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def main():
    print("=" * 70)
    print("🧪 Stage 26: Connection Pool 고갈 → 회복 테스트")
    print(f"   Target: {BASE_URL}")
    print("=" * 70)

    # Phase 1: 초기 상태 확인
    print("\n📊 Phase 1: 초기 상태 확인")
    result = test_endpoint("products", "/api/products/")
    print(f"   Products API: {result['status']} ({result['time_ms']:.0f}ms)")

    result = test_endpoint("pool_status", "/api/self-healing/stress/pool-status/")
    print(f"   Pool Status: {result['status']} ({result['time_ms']:.0f}ms)")
    if result["success"]:
        print(f"   Detail: {result['detail']}")

    # Phase 2: Pool 고갈 유도
    print("\n🔥 Phase 2: Pool 고갈 유도 (5개 slow query 동시 실행)")
    print("   Pool 크기: 3개, 요청: 5개 → 2개는 대기/실패 예상")

    start = time.time()
    results = run_concurrent_slow_queries(5, "/api/self-healing/stress/slow-5s/")
    elapsed = time.time() - start

    successes = sum(1 for r in results if r["success"])
    failures = sum(1 for r in results if not r["success"])

    print(f"\n   결과: {successes}개 성공, {failures}개 실패 (소요: {elapsed:.1f}초)")
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"   {status} {r['name']}: {r['status']} ({r['time_ms']:.0f}ms)")

    # Phase 3: 회복 확인 (즉시)
    print("\n🔍 Phase 3: 즉시 회복 확인")
    result = test_endpoint("products", "/api/products/")
    print(f"   Products API: {result['status']} ({result['time_ms']:.0f}ms)")

    result = test_endpoint("pool_status", "/api/self-healing/stress/pool-status/")
    print(f"   Pool Status: {result['status']} ({result['time_ms']:.0f}ms)")

    # Phase 4: 5초 대기 후 회복 재확인
    print("\n⏳ Phase 4: 5초 대기 후 회복 확인...")
    time.sleep(5)

    result = test_endpoint("products", "/api/products/")
    print(f"   Products API: {result['status']} ({result['time_ms']:.0f}ms)")

    if result["success"]:
        print("\n🎉 SUCCESS: Pool 회복 확인!")
        print("   - 고갈 상태에서 일부 요청 실패")
        print("   - 부하 해소 후 정상 응답 확인")
    else:
        print("\n❌ FAIL: Pool 회복 실패")
        print("   - 서버가 여전히 응답하지 않음")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
