#!/usr/bin/env python3
"""
Stage 26: Circuit Breaker E2E 테스트 (간단 버전)

Docker 없이 직접 서버에서 테스트.
Pool 3개 설정 + Circuit Breaker 활성화 상태에서 실행.

사용법:
    python test_circuit_breaker_e2e.py http://localhost:8000
"""
import requests
import concurrent.futures
import time
import sys
import threading

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

# 통계
stats = {
    "total": 0,
    "success_200": 0,
    "circuit_503": 0,
    "timeout": 0,
    "other_error": 0,
    "circuit_open_detected": False,
    "recovery_detected": False,
}
stats_lock = threading.Lock()


def make_request(endpoint, timeout=30):
    """단일 요청"""
    try:
        start = time.time()
        r = requests.get(f"{BASE_URL}{endpoint}", timeout=timeout)
        elapsed = (time.time() - start) * 1000

        with stats_lock:
            stats["total"] += 1

            if r.status_code == 200:
                stats["success_200"] += 1
                return {
                    "status": 200,
                    "time_ms": elapsed,
                    "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:100],
                }

            elif r.status_code == 503:
                stats["circuit_503"] += 1
                stats["circuit_open_detected"] = True
                try:
                    body = r.json()
                    return {"status": 503, "time_ms": elapsed, "circuit_state": body.get("circuit_state", "?"), "body": body}
                except:
                    return {"status": 503, "time_ms": elapsed, "body": r.text[:100]}

            else:
                stats["other_error"] += 1
                return {"status": r.status_code, "time_ms": elapsed, "body": r.text[:100]}

    except requests.Timeout:
        with stats_lock:
            stats["total"] += 1
            stats["timeout"] += 1
        return {"status": 0, "time_ms": timeout * 1000, "body": "Timeout"}
    except Exception as e:
        with stats_lock:
            stats["total"] += 1
            stats["other_error"] += 1
        return {"status": -1, "time_ms": 0, "body": str(e)}


def concurrent_requests(endpoint, count, timeout=30):
    """동시에 여러 요청"""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(make_request, endpoint, timeout) for _ in range(count)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def print_stats():
    """현재 통계 출력"""
    print(
        f"   📊 총: {stats['total']}, 성공: {stats['success_200']}, "
        f"503(CB): {stats['circuit_503']}, 타임아웃: {stats['timeout']}, 기타에러: {stats['other_error']}"
    )


def main():
    print("=" * 70)
    print("🧪 Stage 26: Circuit Breaker E2E 테스트")
    print(f"   Target: {BASE_URL}")
    print("   Pool: 3개, Circuit Breaker: 활성화")
    print("=" * 70)

    # =========================================================================
    # Phase 1: 서버 상태 확인
    # =========================================================================
    print("\n" + "=" * 70)
    print("📍 Phase 1: 서버 상태 확인")
    print("=" * 70)

    result = make_request("/api/products/")
    if result["status"] != 200:
        print(f"   ❌ 서버 응답 없음! status={result['status']}")
        print(f"   상세: {result['body']}")
        print("\n   서버가 실행 중인지 확인하세요.")
        return
    print(f"   ✅ Products API: 200 OK ({result['time_ms']:.0f}ms)")

    result = make_request("/api/self-healing/stress/pool-status/")
    if result["status"] == 200:
        pool_info = result["body"]
        print(f"   ✅ Pool Status: {pool_info}")
    else:
        print(f"   ⚠️ Pool Status 조회 실패: {result['status']}")

    result = make_request("/api/self-healing/circuit-breaker/pool/status/")
    if result["status"] == 200:
        cb_info = result["body"]
        print(f"   ✅ Circuit Breaker: {cb_info.get('circuit_breaker', {}).get('state', '?')}")
    else:
        print(f"   ⚠️ CB Status 조회 실패: {result['status']}")

    # =========================================================================
    # Phase 2: Pool 고갈 유도
    # =========================================================================
    print("\n" + "=" * 70)
    print("🔥 Phase 2: Pool 고갈 유도")
    print("   - 10개 slow query (5초) 동시 실행")
    print("   - Pool 3개이므로 7개는 대기/503 예상")
    print("=" * 70)

    start_time = time.time()

    # 백그라운드에서 slow query 실행 (Pool 점유)
    slow_futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # 10개 slow query 시작
        for i in range(10):
            future = executor.submit(make_request, "/api/self-healing/stress/slow-5s/", 30)
            slow_futures.append(future)
            print(f"   🚀 Slow query {i+1} 시작")
            time.sleep(0.05)  # 더 빠르게 시작!

        # 0.5초 대기 (Pool이 꽉 찰 시간)
        print("\n   ⏳ 0.5초 대기 (Pool 고갈 유도)...")
        time.sleep(0.5)

        # Pool 고갈 상태에서 추가 요청 시도 - 더 많이, 더 빠르게!
        print("\n   🎯 Pool 고갈 상태에서 추가 요청 시도 (20개)...")
        additional_futures = []
        for i in range(20):
            future = executor.submit(make_request, "/api/products/", 2)  # timeout 2초
            additional_futures.append((i, future))
            time.sleep(0.1)  # 0.1초 간격으로 연속 요청

        # 추가 요청 결과 확인
        for i, future in additional_futures:
            try:
                result = future.result(timeout=5)
                status_icon = "✅" if result["status"] == 200 else "🔴" if result["status"] == 503 else "❌"

                if result["status"] == 503:
                    circuit_state = result.get("circuit_state", "?")
                    print(
                        f"   {status_icon} 추가요청 {i+1}: {result['status']} (Circuit: {circuit_state}) - {result['time_ms']:.0f}ms"
                    )
                else:
                    print(f"   {status_icon} 추가요청 {i+1}: {result['status']} - {result['time_ms']:.0f}ms")
            except Exception as e:
                print(f"   ❌ 추가요청 {i+1}: Exception - {e}")

        # slow query 완료 대기
        print("\n   ⏳ Slow query 완료 대기...")
        for i, future in enumerate(slow_futures):
            result = future.result()
            status_icon = "✅" if result["status"] == 200 else "🔴" if result["status"] == 503 else "❌"
            print(f"   {status_icon} Slow query {i+1} 완료: {result['status']} - {result['time_ms']:.0f}ms")

    elapsed = time.time() - start_time
    print(f"\n   ⏱️ Phase 2 소요 시간: {elapsed:.1f}초")
    print_stats()

    # =========================================================================
    # Phase 3: Circuit Breaker 상태 확인
    # =========================================================================
    print("\n" + "=" * 70)
    print("🔍 Phase 3: Circuit Breaker 상태 확인")
    print("=" * 70)

    result = make_request("/api/self-healing/circuit-breaker/pool/status/")
    if result["status"] == 200:
        cb_info = result["body"]
        cb = cb_info.get("circuit_breaker", {})
        print(f"   상태: {cb.get('state', '?')}")
        print(f"   실패 카운트: {cb.get('failure_count', '?')}")
        print(f"   거부된 요청: {cb_info.get('statistics', {}).get('rejected_requests', '?')}")
        print(f"   Pool 고갈 횟수: {cb_info.get('statistics', {}).get('pool_exhaustion_count', '?')}")
    else:
        print(f"   ⚠️ CB Status 조회 실패: {result['status']}")

    # =========================================================================
    # Phase 4: 복구 확인
    # =========================================================================
    print("\n" + "=" * 70)
    print("🔄 Phase 4: 복구 확인")
    print("   - 15초 대기 후 정상 요청 테스트")
    print("=" * 70)

    # Circuit Breaker recovery timeout 대기
    print("   ⏳ 15초 대기 (Circuit Breaker 복구 대기)...")
    time.sleep(15)

    recovery_success = 0
    for i in range(5):
        result = make_request("/api/products/")
        status_icon = "✅" if result["status"] == 200 else "🔴" if result["status"] == 503 else "❌"
        print(f"   {status_icon} 복구 테스트 {i+1}: {result['status']} - {result['time_ms']:.0f}ms")

        if result["status"] == 200:
            recovery_success += 1
            stats["recovery_detected"] = True

        time.sleep(0.5)

    # =========================================================================
    # 최종 결과
    # =========================================================================
    print("\n" + "=" * 70)
    print("📋 최종 결과")
    print("=" * 70)
    print_stats()

    print("\n🎯 검증 결과:")

    # 503 발생 여부
    if stats["circuit_503"] > 0:
        print(f"   ✅ Circuit Breaker 발동: {stats['circuit_503']}회 503 반환")
    else:
        print(f"   ❌ Circuit Breaker 미발동: 503 없음")

    # 복구 여부
    if stats["recovery_detected"]:
        print(f"   ✅ 자동 복구 확인: 부하 해소 후 정상 응답")
    else:
        print(f"   ❌ 복구 미확인")

    # 최종 판정
    print("\n" + "=" * 70)
    if stats["circuit_503"] > 0 and stats["recovery_detected"]:
        print("🎉 SUCCESS: Circuit Breaker 정상 작동!")
        print("   - Pool 고갈 시 503 반환 (Fail Fast)")
        print("   - 부하 해소 후 자동 복구")
    elif stats["circuit_503"] > 0:
        print("⚠️ PARTIAL: Circuit Breaker 발동했지만 복구 미확인")
        print("   - 복구 timeout 늘려서 재시도 필요")
    else:
        print("❌ FAIL: Circuit Breaker 미발동")
        print("   - Pool 설정 확인 필요")
        print("   - USE_POOL_CIRCUIT_BREAKER=TRUE 확인")
    print("=" * 70)


if __name__ == "__main__":
    main()
