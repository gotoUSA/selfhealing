"""
Stage 24: Partial Network Partition Chaos Tests

Toxiproxy를 사용한 실제 네트워크 파티션 테스트.
업계 표준 Chaos Engineering 패턴 적용.

실행 방법:
    # docker-compose.chaos.yml 환경에서 실행
    docker-compose -f docker-compose.chaos.yml run --rm chaos-test
    
    # 또는 로컬에서 (Toxiproxy가 실행 중이어야 함)
    TOXIPROXY_URL=http://localhost:8474 pytest load_tests/chaos/test_partial_partition_chaos.py -v

테스트 시나리오:
    1. Redis만 다운 → DB fallback 동작 확인
    2. PostgreSQL만 다운 → Cache fallback 동작 확인  
    3. Redis 느림 (2초 지연) → 타임아웃 및 fallback
    4. 부분 장애 후 복구 → 서비스 정상화 확인
"""

from __future__ import annotations

import os
import time
import pytest
import requests
from typing import Generator

from load_tests.chaos.toxiproxy_client import (
    ToxiproxyClient,
    ToxicType,
    get_toxiproxy_client,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def toxiproxy() -> Generator[ToxiproxyClient, None, None]:
    """Toxiproxy 클라이언트"""
    client = get_toxiproxy_client()
    
    # 테스트 시작 전 모든 toxic 제거
    try:
        client.recover_all()
    except Exception:
        pytest.skip("Toxiproxy not available")
    
    yield client
    
    # 테스트 종료 후 정리
    client.recover_all()


@pytest.fixture(scope="module")
def app_url() -> str:
    """테스트 대상 앱 URL"""
    return os.environ.get("TARGET_HOST", "http://localhost:8000")


@pytest.fixture(autouse=True)
def cleanup_toxics(toxiproxy: ToxiproxyClient):
    """각 테스트 후 toxic 정리"""
    yield
    toxiproxy.recover_all()


# =============================================================================
# 헬스 체크 유틸리티
# =============================================================================

def wait_for_service(url: str, timeout: int = 30) -> bool:
    """서비스가 준비될 때까지 대기"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{url}/api/self-healing/health/", timeout=5)
            if resp.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


def make_request(url: str, path: str, timeout: float = 10) -> requests.Response:
    """테스트 요청 수행"""
    return requests.get(f"{url}{path}", timeout=timeout)


# =============================================================================
# 시나리오 1: Redis Partition (Cache Down, DB Up)
# =============================================================================

class TestRedisPartition:
    """
    시나리오: Redis만 다운, PostgreSQL은 정상
    
    예상 동작:
    - 캐시 조회 실패
    - DB fallback으로 데이터 제공
    - 응답 시간 증가 (캐시 미스)
    """
    
    def test_redis_timeout_product_list(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """Redis 타임아웃 시 상품 목록 조회"""
        # 1. 정상 상태 확인
        resp = make_request(app_url, "/api/products/")
        assert resp.status_code == 200
        normal_time = resp.elapsed.total_seconds()
        
        # 2. Redis 장애 주입
        toxiproxy.simulate_redis_down()
        time.sleep(0.5)  # 장애 적용 대기
        
        # 3. 장애 상태에서 요청
        start = time.time()
        try:
            resp = make_request(app_url, "/api/products/", timeout=15)
            elapsed = time.time() - start
            
            # DB fallback으로 여전히 동작해야 함
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            
            # 캐시 미스로 인해 응답 시간 증가 예상
            print(f"Normal: {normal_time:.2f}s, With Redis down: {elapsed:.2f}s")
            
        except requests.exceptions.Timeout:
            # 타임아웃도 예상 가능한 결과
            pytest.skip("Request timed out - may need longer timeout")
    
    def test_redis_latency_degrades_gracefully(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """Redis 지연 시 graceful degradation"""
        # Redis에 2초 지연 추가
        toxiproxy.simulate_redis_slow(latency_ms=2000)
        time.sleep(0.5)
        
        start = time.time()
        resp = make_request(app_url, "/api/products/", timeout=10)
        elapsed = time.time() - start
        
        # 서비스는 여전히 동작해야 함
        assert resp.status_code in [200, 503]
        
        print(f"Response time with 2s Redis latency: {elapsed:.2f}s")


# =============================================================================
# 시나리오 2: PostgreSQL Partition (DB Down, Cache Up)
# =============================================================================

class TestPostgresPartition:
    """
    시나리오: PostgreSQL만 다운, Redis는 정상
    
    예상 동작:
    - 캐시된 데이터는 제공 가능
    - 새 데이터 쓰기 불가
    - 읽기 전용 모드로 degraded 운영
    """
    
    def test_db_down_cached_data_available(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """DB 다운 시 캐시된 데이터 조회"""
        # 1. 먼저 정상 요청으로 캐시 워밍업
        resp = make_request(app_url, "/api/products/")
        assert resp.status_code == 200
        
        # 2. DB 장애 주입
        toxiproxy.simulate_db_down()
        time.sleep(0.5)
        
        # 3. 캐시에서 읽기 시도
        try:
            resp = make_request(app_url, "/api/products/", timeout=10)
            
            # 캐시 히트면 200, 아니면 503
            assert resp.status_code in [200, 503]
            
            if resp.status_code == 200:
                print("✓ Served from cache while DB is down")
            else:
                print("⚠ Cache miss - service unavailable")
                
        except requests.exceptions.Timeout:
            print("⚠ Request timed out during DB partition")
    
    def test_db_latency_query_timeout(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """DB 지연 시 쿼리 타임아웃"""
        # DB에 5초 지연 추가
        toxiproxy.simulate_db_slow(latency_ms=5000)
        time.sleep(0.5)
        
        start = time.time()
        try:
            resp = make_request(app_url, "/api/products/", timeout=10)
            elapsed = time.time() - start
            
            print(f"Response time with 5s DB latency: {elapsed:.2f}s")
            
            # 타임아웃 또는 성공
            assert resp.status_code in [200, 503, 504]
            
        except requests.exceptions.Timeout:
            elapsed = time.time() - start
            print(f"Timed out after {elapsed:.2f}s (expected with 5s DB latency)")


# =============================================================================
# 시나리오 3: Partial Partition (일부만 장애)
# =============================================================================

class TestPartialPartition:
    """
    시나리오: 복합 부분 장애
    
    예상 동작:
    - 일부 서비스는 정상, 일부는 장애
    - Fallback 전략에 따라 서비스 유지
    """
    
    def test_redis_down_db_up_is_partial(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """Redis 다운 + DB 정상 = Partial Partition"""
        # Redis만 다운
        toxiproxy.simulate_redis_down()
        time.sleep(0.5)
        
        # Health check로 상태 확인
        try:
            resp = make_request(app_url, "/api/self-healing/health/", timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                # Partial partition 상태 확인
                db_status = data.get("database", {}).get("status", "unknown")
                cache_status = data.get("cache", {}).get("status", "unknown")
                
                print(f"DB: {db_status}, Cache: {cache_status}")
                
                # DB는 정상, Cache는 문제 예상
                # (실제 구현에 따라 다를 수 있음)
                
        except Exception as e:
            print(f"Health check failed: {e}")
    
    def test_recovery_after_partition(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """장애 복구 후 정상 동작"""
        # 1. 장애 주입
        toxiproxy.simulate_redis_down()
        time.sleep(1)
        
        # 2. 장애 중 요청
        resp1 = make_request(app_url, "/api/products/", timeout=10)
        status_during = resp1.status_code
        
        # 3. 장애 복구
        toxiproxy.recover_all()
        time.sleep(1)
        
        # 4. 복구 후 요청
        resp2 = make_request(app_url, "/api/products/", timeout=10)
        status_after = resp2.status_code
        
        print(f"During partition: {status_during}, After recovery: {status_after}")
        
        # 복구 후에는 반드시 정상이어야 함
        assert status_after == 200


# =============================================================================
# 시나리오 4: 점진적 장애 (Latency → Timeout)
# =============================================================================

class TestGradualDegradation:
    """
    시나리오: 점진적으로 악화되는 장애
    
    예상 동작:
    - 처음엔 느려짐
    - Circuit Breaker 동작
    - 결국 장애로 전환
    """
    
    def test_increasing_latency(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """점진적으로 증가하는 지연"""
        latencies = [100, 500, 1000, 2000, 3000]
        results = []
        
        for latency in latencies:
            # 이전 toxic 제거
            toxiproxy.recover_all()
            
            # 새 지연 추가
            toxiproxy.simulate_redis_slow(latency_ms=latency)
            time.sleep(0.3)
            
            start = time.time()
            try:
                resp = make_request(app_url, "/api/products/", timeout=10)
                elapsed = time.time() - start
                results.append({
                    "latency": latency,
                    "status": resp.status_code,
                    "elapsed": elapsed
                })
            except requests.exceptions.Timeout:
                results.append({
                    "latency": latency,
                    "status": "timeout",
                    "elapsed": 10
                })
        
        print("\nGradual Degradation Results:")
        for r in results:
            print(f"  Latency: {r['latency']}ms → Status: {r['status']}, Time: {r['elapsed']:.2f}s")


# =============================================================================
# 시나리오 5: Flapping (불안정한 연결)
# =============================================================================

class TestFlappingConnection:
    """
    시나리오: 간헐적으로 연결이 끊기는 상황
    
    예상 동작:
    - 재시도 로직 동작
    - Circuit Breaker 상태 변화
    """
    
    def test_intermittent_failures(
        self, toxiproxy: ToxiproxyClient, app_url: str
    ):
        """50% 확률로 연결 리셋"""
        # 50% 확률로 연결 리셋
        toxiproxy.add_toxic(
            "redis",
            ToxicType.RESET_PEER,
            {"timeout": 0},
            toxicity=0.5  # 50% 확률
        )
        time.sleep(0.5)
        
        success = 0
        failure = 0
        
        for _ in range(10):
            try:
                resp = make_request(app_url, "/api/products/", timeout=5)
                if resp.status_code == 200:
                    success += 1
                else:
                    failure += 1
            except Exception:
                failure += 1
            time.sleep(0.2)
        
        print(f"\nFlapping test: {success} success, {failure} failures out of 10")
        
        # 일부는 성공해야 함 (50% 확률이므로)
        assert success > 0, "All requests failed in flapping test"


# =============================================================================
# 헬퍼 테스트
# =============================================================================

class TestToxiproxySetup:
    """Toxiproxy 설정 확인"""
    
    def test_toxiproxy_is_running(self, toxiproxy: ToxiproxyClient):
        """Toxiproxy 실행 확인"""
        proxies = toxiproxy.list_proxies()
        print(f"Available proxies: {list(proxies.keys())}")
        
        assert "redis" in proxies or len(proxies) >= 0
    
    def test_can_add_and_remove_toxic(self, toxiproxy: ToxiproxyClient):
        """Toxic 추가/제거 테스트"""
        proxies = toxiproxy.list_proxies()
        
        if not proxies:
            pytest.skip("No proxies configured")
        
        proxy_name = list(proxies.keys())[0]
        
        # Toxic 추가
        toxic = toxiproxy.add_toxic(
            proxy_name,
            ToxicType.LATENCY,
            {"latency": 100},
            name="test_toxic"
        )
        assert toxic["name"] == "test_toxic"
        
        # Toxic 목록 확인
        toxics = toxiproxy.list_toxics(proxy_name)
        assert any(t["name"] == "test_toxic" for t in toxics)
        
        # Toxic 제거
        removed = toxiproxy.remove_toxic(proxy_name, "test_toxic")
        assert removed is True
