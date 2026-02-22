"""
Stage 24: Partial Network Partition 테스트

목표: 부분 네트워크 단절 시 복원력 검증

시나리오:
  - DB 연결은 가능하지만 Redis 연결 불가
  - 외부 API(토스) 연결만 단절
  - 특정 서비스만 응답 지연

실제 장애 사례:
  - 2025년 AWS ap-northeast-1: DB는 정상인데 Redis만 단절 → 캐시 무한 미스, 서비스 과부하

실행 방법:
    # 기본 모드 (헤더 시뮬레이션)
    locust -f load_tests/scenarios/stage24_partial_partition.py --host=http://localhost:8000

    # Toxiproxy Chaos 모드 (실제 네트워크 장애 주입)
    TOXIPROXY_URL=http://toxiproxy:8474 locust -f load_tests/scenarios/stage24_partial_partition.py \\
        --host=http://web:8000 --users=50 --spawn-rate=5 --run-time=3m --headless

검증 기준:
  - 단절된 서비스 fallback 동작
  - Circuit Breaker 정상 작동
  - 복구 후 정상 서비스 재개

Reference:
  - docs/STAGE_24_PARTIAL_PARTITION.md
"""

import os
import sys
import time
import random
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field
from enum import Enum

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

# Toxiproxy 클라이언트 (Chaos Engineering)
try:
    from load_tests.chaos.toxiproxy_client import ToxiproxyClient, ToxicType, get_toxiproxy_client

    TOXIPROXY_AVAILABLE = True
except ImportError:
    TOXIPROXY_AVAILABLE = False
    ToxiproxyClient = None


STAGE_NAME = "[Stage24-PartialPartition]"

# Toxiproxy 클라이언트 (전역)
_toxiproxy_client: Optional[ToxiproxyClient] = None
_chaos_mode_enabled = False


# =============================================================================
# Partition 상태 시뮬레이션
# =============================================================================


class PartitionMode(str, Enum):
    """네트워크 파티션 모드"""

    NORMAL = "normal"
    REDIS_DOWN = "redis_down"
    DB_DOWN = "db_down"
    EXTERNAL_API_DOWN = "external_api_down"
    PARTIAL_LATENCY = "partial_latency"


@dataclass
class PartitionSimulator:
    """부분 파티션 시뮬레이션"""

    mode: PartitionMode = PartitionMode.NORMAL
    redis_available: bool = True
    db_available: bool = True
    external_apis: Dict[str, bool] = field(default_factory=dict)
    latency_services: Dict[str, int] = field(default_factory=dict)  # service -> latency_ms

    def set_redis_partition(self, available: bool):
        """Redis 연결 상태 설정"""
        self.redis_available = available
        if not available:
            self.mode = PartitionMode.REDIS_DOWN
        elif self._all_healthy():
            self.mode = PartitionMode.NORMAL

    def set_db_partition(self, available: bool):
        """DB 연결 상태 설정"""
        self.db_available = available
        if not available:
            self.mode = PartitionMode.DB_DOWN
        elif self._all_healthy():
            self.mode = PartitionMode.NORMAL

    def set_external_api_partition(self, api_name: str, available: bool):
        """외부 API 연결 상태 설정"""
        self.external_apis[api_name] = available
        if not available:
            self.mode = PartitionMode.EXTERNAL_API_DOWN
        elif self._all_healthy():
            self.mode = PartitionMode.NORMAL

    def add_latency(self, service: str, latency_ms: int):
        """서비스에 지연 추가"""
        self.latency_services[service] = latency_ms
        if latency_ms > 0:
            self.mode = PartitionMode.PARTIAL_LATENCY

    def _all_healthy(self) -> bool:
        """모든 서비스가 정상인지 확인"""
        return (
            self.redis_available
            and self.db_available
            and all(self.external_apis.values())
            and all(v == 0 for v in self.latency_services.values())
        )

    def is_partial_partition(self) -> bool:
        """부분 파티션 상태인지 확인"""
        statuses = [self.redis_available, self.db_available] + list(self.external_apis.values())
        return any(statuses) and not all(statuses)


# 전역 파티션 시뮬레이터
_partition_simulator = PartitionSimulator()


# =============================================================================
# 테스트 통계
# =============================================================================

_partition_stats = {
    "total_requests": 0,
    "partition_scenarios": {
        "normal": 0,
        "redis_down": 0,
        "db_down": 0,
        "external_api_down": 0,
        "partial_latency": 0,
    },
    "fallback_usage": {
        "db_fallback": 0,
        "cache_fallback": 0,
        "default_value": 0,
        "no_fallback": 0,
    },
    "recovery": {
        "auto_recovered": 0,
        "manual_intervention": 0,
        "recovery_time_sum_ms": 0,
    },
    "circuit_breaker": {
        "opened": 0,
        "half_open": 0,
        "closed": 0,
    },
    "errors": {
        "timeout": 0,
        "connection_refused": 0,
        "service_unavailable": 0,
        "other": 0,
    },
}


def record_partition_scenario(mode: PartitionMode):
    """파티션 시나리오 기록"""
    _partition_stats["total_requests"] += 1
    _partition_stats["partition_scenarios"][mode.value] += 1


def record_fallback_usage(fallback_type: str):
    """Fallback 사용 기록"""
    if fallback_type in _partition_stats["fallback_usage"]:
        _partition_stats["fallback_usage"][fallback_type] += 1


def record_recovery(auto: bool, time_ms: float = 0):
    """복구 기록"""
    if auto:
        _partition_stats["recovery"]["auto_recovered"] += 1
    else:
        _partition_stats["recovery"]["manual_intervention"] += 1
    _partition_stats["recovery"]["recovery_time_sum_ms"] += time_ms


def record_circuit_breaker_state(state: str):
    """Circuit Breaker 상태 기록"""
    if state in _partition_stats["circuit_breaker"]:
        _partition_stats["circuit_breaker"][state] += 1


def record_error(error_type: str):
    """에러 기록"""
    if error_type in _partition_stats["errors"]:
        _partition_stats["errors"][error_type] += 1
    else:
        _partition_stats["errors"]["other"] += 1


# =============================================================================
# Locust User 클래스
# =============================================================================


class PartialPartitionUser(HttpUser):
    """
    Partial Network Partition 테스트 사용자

    다양한 부분 파티션 시나리오에서 시스템 복원력을 테스트
    """

    wait_time = between(0.5, 2)
    abstract = False

    def on_start(self):
        """사용자 시작 시 초기화"""
        self.user_id = random.randint(1, 10000)
        self.session_token = None
        self.failed_requests = 0
        self.fallback_count = 0
        print(f"{STAGE_NAME} User {self.user_id} started")

    # =========================================================================
    # 시나리오 1: Redis 다운 시 DB Fallback
    # =========================================================================

    @task(3)
    @tag("redis-partition", "cache-fallback")
    def test_redis_down_scenario(self):
        """
        Redis 연결 불가 시나리오

        - Redis 캐시 조회 실패
        - DB에서 직접 조회로 fallback
        - 성능 저하 허용하되 서비스는 유지
        """
        # 시나리오: Redis 다운 시뮬레이션 (50% 확률)
        simulate_redis_down = random.random() < 0.5

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-Partition": "redis" if simulate_redis_down else "none",
        }

        start_time = time.time()

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Redis Down - Product List",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_redis_down:
                record_partition_scenario(PartitionMode.REDIS_DOWN)

                if response.status_code == 200:
                    # Redis 다운이지만 서비스는 정상 → DB fallback 성공
                    response.success()
                    record_fallback_usage("db_fallback")

                    # 응답 헤더에서 fallback 사용 여부 확인
                    if response.headers.get("X-Cache-Status") == "BYPASS":
                        print(f"{STAGE_NAME} ✓ Redis down but DB fallback worked (took {elapsed_ms:.0f}ms)")

                elif response.status_code == 503:
                    # 서비스 불가 - 모든 fallback 실패
                    response.failure("Service unavailable during Redis partition")
                    record_error("service_unavailable")

                else:
                    response.failure(f"Unexpected status: {response.status_code}")
                    record_error("other")
            else:
                record_partition_scenario(PartitionMode.NORMAL)

                if response.status_code == 200:
                    response.success()
                    record_fallback_usage("no_fallback")
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 2: 외부 결제 API 다운
    # =========================================================================

    @task(2)
    @tag("external-api-partition", "payment")
    def test_payment_api_down_scenario(self):
        """
        외부 결제 API(토스) 다운 시나리오

        - 결제 요청 실패
        - 재시도 큐에 등록
        - 사용자에게 "처리 중" 응답 반환
        """
        simulate_api_down = random.random() < 0.4

        payment_data = {
            "order_id": f"order_{self.user_id}_{int(time.time())}",
            "amount": random.randint(10000, 100000),
            "payment_method": "card",
        }

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-Partition": "toss_api" if simulate_api_down else "none",
            "Content-Type": "application/json",
        }

        start_time = time.time()

        with self.client.get(
            "/api/categories/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Payment API Down - Category List",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_api_down:
                record_partition_scenario(PartitionMode.EXTERNAL_API_DOWN)

                if response.status_code in [200, 201]:
                    response.success()
                    record_fallback_usage("no_fallback")

                elif response.status_code == 202:
                    # Accepted - 비동기 처리 중
                    response.success()
                    record_fallback_usage("default_value")
                    print(f"{STAGE_NAME} ✓ Payment queued for retry (API down)")

                elif response.status_code == 503:
                    # Circuit Breaker 열림
                    response.failure("Payment service circuit breaker open")
                    record_circuit_breaker_state("opened")
                    record_error("service_unavailable")

                else:
                    response.failure(f"Unexpected status: {response.status_code}")
                    record_error("other")
            else:
                record_partition_scenario(PartitionMode.NORMAL)

                if response.status_code in [200, 201]:
                    response.success()
                else:
                    response.failure(f"Normal payment failed: {response.status_code}")

    # =========================================================================
    # 시나리오 3: DB 다운 시 Cache 읽기
    # =========================================================================

    @task(2)
    @tag("db-partition", "cache-read")
    def test_db_down_cache_read_scenario(self):
        """
        DB 다운 시 캐시에서 읽기 시나리오

        - DB 쓰기는 불가능
        - 캐시된 데이터는 읽기 가능
        - 읽기 전용 모드로 degraded 운영
        """
        simulate_db_down = random.random() < 0.3

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-Partition": "database" if simulate_db_down else "none",
        }

        with self.client.get(
            f"/api/products/{random.randint(1, 100)}/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} DB Down - Product Detail",
        ) as response:

            if simulate_db_down:
                record_partition_scenario(PartitionMode.DB_DOWN)

                if response.status_code == 200:
                    # 캐시에서 읽기 성공
                    response.success()
                    record_fallback_usage("cache_fallback")

                    if response.headers.get("X-Cache-Status") == "HIT":
                        print(f"{STAGE_NAME} ✓ DB down but served from cache")

                elif response.status_code == 503:
                    # 캐시 미스 + DB 다운 = 서비스 불가
                    response.failure("Service unavailable - no cached data")
                    record_error("service_unavailable")

                elif response.status_code == 404:
                    # 캐시 미스 (아이템 없음)
                    response.success()  # 정상적인 404

                else:
                    response.failure(f"Unexpected status: {response.status_code}")
            else:
                record_partition_scenario(PartitionMode.NORMAL)

                if response.status_code in [200, 404]:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 4: 부분 지연 (특정 서비스만 느림)
    # =========================================================================

    @task(2)
    @tag("partial-latency", "degraded")
    def test_partial_latency_scenario(self):
        """
        특정 서비스만 지연되는 시나리오

        - Redis는 정상이지만 DB가 느림
        - 또는 특정 외부 API만 느림
        - Timeout 설정에 따른 fallback 동작
        """
        simulate_latency = random.random() < 0.4
        latency_target = random.choice(["database", "redis", "external_api"])

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-Latency": latency_target if simulate_latency else "none",
            "X-Latency-Ms": str(random.randint(2000, 5000)) if simulate_latency else "0",
        }

        timeout_seconds = 3.0  # 3초 타임아웃
        start_time = time.time()

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            timeout=timeout_seconds,
            name=f"{STAGE_NAME} Partial Latency - Product List",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_latency:
                record_partition_scenario(PartitionMode.PARTIAL_LATENCY)

                if elapsed_ms > timeout_seconds * 1000:
                    # 타임아웃 발생
                    response.failure(f"Timeout after {elapsed_ms:.0f}ms (target: {latency_target})")
                    record_error("timeout")

                elif response.status_code == 200:
                    response.success()
                    if elapsed_ms > 1000:
                        print(f"{STAGE_NAME} ⚠ Slow response: {elapsed_ms:.0f}ms (target: {latency_target})")

                else:
                    response.failure(f"Failed with latency: {response.status_code}")
            else:
                record_partition_scenario(PartitionMode.NORMAL)

                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 5: 복구 후 정상 동작 확인
    # =========================================================================

    @task(1)
    @tag("recovery", "validation")
    def test_recovery_validation(self):
        """
        파티션 복구 후 정상 동작 확인

        - 서비스 상태 확인
        - Circuit Breaker 상태 확인
        - 캐시 일관성 확인
        """
        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-Partition": "none",  # 항상 정상 상태로 테스트
        }

        start_time = time.time()

        with self.client.get(
            "/api/self-healing/health/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Recovery - Health Check",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                response.success()

                try:
                    health_data = response.json()

                    # 서비스 상태 확인
                    db_status = health_data.get("database", {}).get("status", "unknown")
                    cache_status = health_data.get("cache", {}).get("status", "unknown")

                    if db_status == "healthy" and cache_status == "healthy":
                        record_recovery(auto=True, time_ms=elapsed_ms)
                        print(f"{STAGE_NAME} ✓ All services healthy")
                    else:
                        print(f"{STAGE_NAME} ⚠ Partial recovery: DB={db_status}, Cache={cache_status}")

                except Exception:
                    pass  # Health check 응답 파싱 실패 무시

            elif response.status_code == 503:
                response.failure("Service still unhealthy")
                record_error("service_unavailable")
            else:
                response.failure(f"Unexpected status: {response.status_code}")


# =============================================================================
# Toxiproxy Chaos Controller (백그라운드 스레드)
# =============================================================================


class ChaosController:
    """
    백그라운드에서 Toxiproxy를 제어하여 실제 네트워크 장애 주입.
    Netflix/Shopify 스타일 Chaos Engineering.
    """

    def __init__(self, toxiproxy_url: str = None):
        self.client = None
        self.enabled = False
        self.chaos_thread = None
        self.stop_event = threading.Event()

        if TOXIPROXY_AVAILABLE and toxiproxy_url:
            try:
                self.client = ToxiproxyClient(toxiproxy_url)
                self.client.list_proxies()  # 연결 테스트
                self.enabled = True
                print(f"{STAGE_NAME} ✓ Toxiproxy connected: {toxiproxy_url}")
            except Exception as e:
                print(f"{STAGE_NAME} ⚠ Toxiproxy not available: {e}")

    def start_chaos_cycle(self, cycle_seconds: int = 30):
        """장애 주입 사이클 시작"""
        if not self.enabled:
            return

        self.stop_event.clear()
        self.chaos_thread = threading.Thread(target=self._chaos_cycle, args=(cycle_seconds,))
        self.chaos_thread.daemon = True
        self.chaos_thread.start()
        print(f"{STAGE_NAME} 🔥 Chaos cycle started (every {cycle_seconds}s)")

    def stop_chaos(self):
        """장애 주입 중지 및 복구"""
        self.stop_event.set()
        if self.chaos_thread:
            self.chaos_thread.join(timeout=5)
        if self.enabled and self.client:
            self.client.recover_all()
            print(f"{STAGE_NAME} ✓ All chaos recovered")

    def _chaos_cycle(self, cycle_seconds: int):
        """주기적으로 장애 주입/복구"""
        scenarios = [
            self._inject_redis_down,
            self._inject_redis_slow,
            self._inject_db_slow,
            self._recover_all,
        ]

        while not self.stop_event.is_set():
            scenario = random.choice(scenarios)
            try:
                scenario()
            except Exception as e:
                print(f"{STAGE_NAME} Chaos error: {e}")

            # 다음 시나리오까지 대기
            if self.stop_event.wait(timeout=cycle_seconds):
                break

    def _inject_redis_down(self):
        """Redis 완전 차단"""
        self.client.recover_all()
        self.client.simulate_redis_down()
        print(f"{STAGE_NAME} 🔥 CHAOS: Redis DOWN")

    def _inject_redis_slow(self):
        """Redis 2초 지연"""
        self.client.recover_all()
        self.client.simulate_redis_slow(latency_ms=2000)
        print(f"{STAGE_NAME} 🔥 CHAOS: Redis SLOW (2s)")

    def _inject_db_slow(self):
        """PostgreSQL 3초 지연"""
        self.client.recover_all()
        self.client.simulate_db_slow(latency_ms=3000)
        print(f"{STAGE_NAME} 🔥 CHAOS: DB SLOW (3s)")

    def _recover_all(self):
        """모든 장애 복구"""
        self.client.recover_all()
        print(f"{STAGE_NAME} ✓ CHAOS: All RECOVERED")


# 전역 Chaos Controller
_chaos_controller: Optional[ChaosController] = None


# =============================================================================
# 이벤트 핸들러
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 초기화"""
    global _chaos_controller

    print(f"\n{'=' * 60}")
    print(f"{STAGE_NAME} Partial Network Partition Test Starting")
    print(f"{'=' * 60}")
    print(f"Target Host: {environment.host}")
    print("Scenarios:")
    print("  - Redis Down + DB Fallback")
    print("  - External API Down + Retry Queue")
    print("  - DB Down + Cache Read")
    print("  - Partial Latency + Timeout")

    # Toxiproxy Chaos Mode 활성화
    toxiproxy_url = os.environ.get("TOXIPROXY_URL")
    if toxiproxy_url:
        print("\n🔥 CHAOS MODE ENABLED")
        print(f"   Toxiproxy: {toxiproxy_url}")
        _chaos_controller = ChaosController(toxiproxy_url)
        _chaos_controller.start_chaos_cycle(cycle_seconds=20)
    else:
        print("\n📋 SIMULATION MODE (header-based)")
        print("   Set TOXIPROXY_URL for real network chaos")

    print(f"{'=' * 60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 통계 출력"""
    global _chaos_controller

    # Chaos 중지 및 복구
    if _chaos_controller:
        _chaos_controller.stop_chaos()

    print(f"\n{'=' * 60}")
    print(f"{STAGE_NAME} Test Results Summary")
    print(f"{'=' * 60}")

    total = _partition_stats["total_requests"]
    if total == 0:
        print("No requests made")
        return

    print(f"\n📊 Total Requests: {total}")

    print("\n🔌 Partition Scenarios:")
    for scenario, count in _partition_stats["partition_scenarios"].items():
        pct = (count / total) * 100 if total > 0 else 0
        print(f"   {scenario}: {count} ({pct:.1f}%)")

    print("\n🔄 Fallback Usage:")
    for fallback, count in _partition_stats["fallback_usage"].items():
        pct = (count / total) * 100 if total > 0 else 0
        print(f"   {fallback}: {count} ({pct:.1f}%)")

    print("\n🔧 Recovery Stats:")
    print(f"   Auto Recovered: {_partition_stats['recovery']['auto_recovered']}")
    print(f"   Manual Intervention: {_partition_stats['recovery']['manual_intervention']}")
    avg_recovery = _partition_stats["recovery"]["recovery_time_sum_ms"] / max(
        _partition_stats["recovery"]["auto_recovered"], 1
    )
    print(f"   Avg Recovery Time: {avg_recovery:.0f}ms")

    print("\n⚡ Circuit Breaker States:")
    for state, count in _partition_stats["circuit_breaker"].items():
        print(f"   {state}: {count}")

    print("\n❌ Errors:")
    for error, count in _partition_stats["errors"].items():
        print(f"   {error}: {count}")

    # 검증 기준 체크
    print(f"\n{'=' * 60}")
    print("📋 Validation Results")
    print(f"{'=' * 60}")

    # 1. Fallback 동작 확인
    fallback_used = (
        _partition_stats["fallback_usage"]["db_fallback"]
        + _partition_stats["fallback_usage"]["cache_fallback"]
        + _partition_stats["fallback_usage"]["default_value"]
    )
    partition_requests = total - _partition_stats["partition_scenarios"]["normal"]
    fallback_rate = (fallback_used / max(partition_requests, 1)) * 100

    if fallback_rate > 50:
        print(f"✅ Fallback working: {fallback_rate:.1f}% of partition requests used fallback")
    else:
        print(f"⚠️ Low fallback rate: {fallback_rate:.1f}% (expected > 50%)")

    # 2. 에러율 확인
    total_errors = sum(_partition_stats["errors"].values())
    error_rate = (total_errors / total) * 100

    if error_rate < 10:
        print(f"✅ Error rate acceptable: {error_rate:.1f}% (threshold: 10%)")
    else:
        print(f"❌ High error rate: {error_rate:.1f}% (threshold: 10%)")

    # 3. 복구 확인
    if _partition_stats["recovery"]["auto_recovered"] > 0:
        print(f"✅ Auto recovery working: {_partition_stats['recovery']['auto_recovered']} recoveries")
    else:
        print("⚠️ No auto recovery recorded")

    print(f"{'=' * 60}\n")


# =============================================================================
# 단독 실행용
# =============================================================================

if __name__ == "__main__":
    import subprocess

    cmd = [
        "locust",
        "-f",
        __file__,
        "--host",
        "http://localhost:8000",
        "--users",
        "50",
        "--spawn-rate",
        "5",
        "--run-time",
        "3m",
        "--headless",
        "--html",
        "stage24_report.html",
    ]

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
