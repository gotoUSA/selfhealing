"""
Redis 기반 Throttle Limit 분산 환경 Race Condition 테스트.

다수의 워커가 동시에 limit을 업데이트할 때 Lua 스크립트가
Race Condition을 방지하는지 검증합니다.

테스트 시나리오:
1. 동시 limit 업데이트 시 원자성 보장
2. Compare-And-Swap (CAS) 조건부 업데이트
3. Cold Start 시 마지막 안전 limit 복구
4. 다수 워커 동시 RTT 샘플 기록

Run:
    docker-compose -f docker-compose.test.yml run test-throttle-race-condition
"""

import os
import threading
import concurrent.futures
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from django.conf import settings


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def redis_client():
    """테스트용 Redis 클라이언트."""
    import redis

    client = redis.Redis(
        host=getattr(settings, "REDIS_HOST", "redis"),
        port=int(getattr(settings, "REDIS_PORT", 6379)),
        db=15,  # 테스트 전용 DB
        decode_responses=False,
    )

    # 테스트 전 정리
    client.flushdb()

    yield client

    # 테스트 후 정리
    client.flushdb()
    client.close()


@pytest.fixture
def limit_manager(redis_client):
    """RedisThrottleLimitManager 인스턴스."""
    from selfhealing.services.throttle.redis_lua import RedisThrottleLimitManager

    return RedisThrottleLimitManager(
        redis_client=redis_client,
        key_prefix="test:",
    )


# =============================================================================
# Test: 동시 Limit 업데이트 원자성
# =============================================================================


@pytest.mark.django_db
class TestConcurrentLimitUpdateAtomicity:
    """동시 limit 업데이트 시 원자성 보장 테스트."""

    def test_concurrent_atomic_updates_no_race_condition(self, limit_manager, redis_client):
        """다수 스레드가 동시에 limit을 업데이트해도 데이터 무결성 유지."""
        service_name = "race_test_service"
        num_workers = 20
        updates_per_worker = 50

        # 초기값 설정
        limit_manager.update_limit_atomic(
            service_name=service_name,
            new_limit=100,
            min_limit=10,
            max_limit=1000,
        )

        update_results = []
        lock = threading.Lock()

        def worker(worker_id: int):
            """각 워커가 limit을 증가/감소."""
            local_results = []
            for i in range(updates_per_worker):
                # 번갈아가며 증가/감소
                if i % 2 == 0:
                    new_limit = 100 + (worker_id * 10) + i
                else:
                    new_limit = 100 - (worker_id % 5)

                prev, actual = limit_manager.update_limit_atomic(
                    service_name=service_name,
                    new_limit=new_limit,
                    min_limit=10,
                    max_limit=1000,
                )
                local_results.append((prev, actual))

            with lock:
                update_results.extend(local_results)

        # 동시 실행
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker, i) for i in range(num_workers)]
            concurrent.futures.wait(futures)

        # 결과 검증
        total_updates = num_workers * updates_per_worker
        assert len(update_results) == total_updates

        # 모든 업데이트가 bounds 내에 있어야 함
        for prev, actual in update_results:
            assert 10 <= actual <= 1000, f"Limit out of bounds: {actual}"

        # 최종값 확인
        final_limit = limit_manager.get_current_limit(service_name)
        assert final_limit is not None
        assert 10 <= final_limit <= 1000

    def test_concurrent_updates_preserve_order(self, limit_manager):
        """연속 업데이트의 순서가 보존되는지 확인 (단일 키)."""
        service_name = "order_test"
        num_updates = 100

        results = []

        def sequential_update(value: int):
            prev, actual = limit_manager.update_limit_atomic(
                service_name=service_name,
                new_limit=value,
                min_limit=1,
                max_limit=1000,
            )
            return (prev, actual)

        # 순차 업데이트
        for i in range(1, num_updates + 1):
            prev, actual = sequential_update(i)
            results.append(actual)

        # 마지막 값이 정확해야 함
        final = limit_manager.get_current_limit(service_name)
        assert final == num_updates


# =============================================================================
# Test: Compare-And-Swap (CAS) 조건부 업데이트
# =============================================================================


@pytest.mark.django_db
class TestCompareAndSwapUpdate:
    """CAS 패턴 조건부 업데이트 테스트."""

    def test_cas_succeeds_when_expected_matches(self, limit_manager):
        """expected_current와 실제 값이 일치하면 CAS 성공."""
        service_name = "cas_success_test"

        # 초기값 설정
        limit_manager.update_limit_atomic(service_name, 100, 10, 500)

        # CAS 업데이트 (expected=100)
        success, actual, message = limit_manager.update_limit_cas(
            service_name=service_name,
            expected_current=100,
            new_limit=150,
            min_limit=10,
            max_limit=500,
        )

        assert success is True
        assert actual == 150
        assert message == "OK"

    def test_cas_fails_when_expected_mismatch(self, limit_manager):
        """expected_current와 실제 값이 다르면 CAS 실패."""
        service_name = "cas_fail_test"

        # 초기값 설정
        limit_manager.update_limit_atomic(service_name, 100, 10, 500)

        # CAS 업데이트 (잘못된 expected)
        success, actual, message = limit_manager.update_limit_cas(
            service_name=service_name,
            expected_current=50,  # 실제는 100
            new_limit=200,
            min_limit=10,
            max_limit=500,
        )

        assert success is False
        assert actual == 100  # 변경되지 않음
        assert message == "MISMATCH"

    def test_cas_prevents_concurrent_race_condition(self, limit_manager):
        """CAS가 동시 업데이트 Race Condition을 방지하는지 확인."""
        service_name = "cas_race_test"
        limit_manager.update_limit_atomic(service_name, 100, 10, 500)

        success_count = {"count": 0}
        fail_count = {"count": 0}
        lock = threading.Lock()

        def cas_worker():
            """모든 워커가 동일한 expected로 CAS 시도."""
            success, actual, message = limit_manager.update_limit_cas(
                service_name=service_name,
                expected_current=100,  # 초기값
                new_limit=200,
                min_limit=10,
                max_limit=500,
            )
            with lock:
                if success:
                    success_count["count"] += 1
                else:
                    fail_count["count"] += 1

        # 10개 워커 동시 CAS 시도
        num_workers = 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(cas_worker) for _ in range(num_workers)]
            concurrent.futures.wait(futures)

        # 정확히 1개만 성공해야 함
        assert success_count["count"] == 1
        assert fail_count["count"] == num_workers - 1


# =============================================================================
# Test: Cold Start 시 마지막 안전 Limit 복구
# =============================================================================


@pytest.mark.django_db
class TestColdStartSafeLimitRecovery:
    """Cold Start 시 마지막 안전 limit 복구 테스트."""

    def test_load_safe_limit_from_redis(self, limit_manager, redis_client):
        """Redis에 저장된 마지막 안전 limit을 로드하는지 확인."""
        service_name = "cold_start_test"

        # 안전 limit 저장
        limit_manager.save_safe_limit(service_name, 80)

        # 현재 limit 없이 safe limit 로드
        limit, source = limit_manager.load_safe_limit(
            service_name=service_name,
            default_limit=100,
        )

        assert limit == 80
        assert source == "SAFE"

    def test_load_current_limit_if_exists(self, limit_manager):
        """현재 limit이 있으면 safe limit보다 우선 사용."""
        service_name = "current_priority_test"

        # 현재 limit 설정
        limit_manager.update_limit_atomic(service_name, 120, 10, 500)

        # safe limit도 저장
        limit_manager.save_safe_limit(service_name, 80)

        # 로드 시 현재 limit 우선
        limit, source = limit_manager.load_safe_limit(
            service_name=service_name,
            default_limit=100,
        )

        assert limit == 120
        assert source == "CURRENT"

    def test_fallback_to_default_if_no_saved_limit(self, limit_manager):
        """저장된 limit이 없으면 기본값 사용."""
        service_name = "no_saved_limit_test"

        limit, source = limit_manager.load_safe_limit(
            service_name=service_name,
            default_limit=50,
        )

        assert limit == 50
        assert source == "DEFAULT"

    def test_safe_limit_saved_periodically(self, limit_manager, redis_client):
        """안전 limit이 주기적으로 저장되는지 확인."""
        service_name = "periodic_save_test"

        # 여러 번 업데이트하면서 save_as_safe=True
        for i in range(5):
            limit_manager.update_limit_atomic(
                service_name=service_name,
                new_limit=100 + i * 10,
                min_limit=10,
                max_limit=500,
                save_as_safe=True,
            )

        # 마지막 안전 limit 확인
        key = f"test:throttle:last_safe_limit:{service_name}"
        saved_limit = redis_client.hget(key, "limit")

        assert saved_limit is not None
        assert int(saved_limit) == 140  # 100 + 4*10


# =============================================================================
# Test: 다수 워커 동시 RTT 샘플 기록
# =============================================================================


@pytest.mark.django_db
class TestConcurrentRTTSampleRecording:
    """다수 워커 동시 RTT 샘플 기록 테스트."""

    def test_concurrent_rtt_samples_no_data_loss(self, limit_manager):
        """다수 스레드가 동시에 RTT를 기록해도 데이터 손실 없음."""
        service_name = "rtt_concurrent_test"
        num_workers = 10
        samples_per_worker = 20

        def record_samples(worker_id: int):
            for i in range(samples_per_worker):
                rtt_ms = 100.0 + worker_id * 10 + i
                limit_manager.add_rtt_sample(
                    service_name=service_name,
                    rtt_ms=rtt_ms,
                    window_seconds=60.0,
                    max_samples=500,
                )

        # 동시 기록
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(record_samples, i) for i in range(num_workers)]
            concurrent.futures.wait(futures)

        # 모든 샘플이 기록되었는지 확인
        samples = limit_manager.get_rtt_samples(service_name, window_seconds=60.0)

        expected_samples = num_workers * samples_per_worker
        assert len(samples) == expected_samples

    def test_rtt_samples_respect_window_limit(self, limit_manager):
        """RTT 샘플이 윈도우 크기를 초과하지 않는지 확인."""
        service_name = "rtt_window_test"

        # 많은 샘플 기록 (max_samples=50 제한)
        for i in range(100):
            limit_manager.add_rtt_sample(
                service_name=service_name,
                rtt_ms=100.0 + i,
                window_seconds=60.0,
                max_samples=50,
            )

        samples = limit_manager.get_rtt_samples(service_name)

        # max_samples 이하여야 함
        assert len(samples) <= 50


# =============================================================================
# Test: Safe-Open 폴백 Redis 장애 시뮬레이션
# =============================================================================


@pytest.mark.django_db
class TestSafeOpenFallbackRedisFailure:
    """Redis 장애 시 Safe-Open 폴백 테스트."""

    def test_uses_local_cache_when_redis_down(self):
        """Redis 장애 시 로컬 캐시의 마지막 안전 limit 사용."""
        from selfhealing.services.throttle.safe_open_fallback import (
            SafeOpenFallbackManager,
            SafeOpenConfig,
        )

        config = SafeOpenConfig(
            enabled=True,
            default_fallback_limit=30,
        )
        manager = SafeOpenFallbackManager(config, redis_client=None)

        # 로컬 캐시에 안전 limit 저장
        state = manager.get_or_create_service_state("fallback_test", initial_limit=100)
        state.last_known_safe_limit = 80

        # Redis 없이 limit 조회
        limit, source = manager.get_safe_limit("fallback_test", default_limit=100)

        assert limit == 80
        assert source == "local_cache"

    def test_uses_default_when_no_cache(self):
        """로컬 캐시도 없으면 기본 폴백 limit 사용."""
        from selfhealing.services.throttle.safe_open_fallback import (
            SafeOpenFallbackManager,
            SafeOpenConfig,
        )

        config = SafeOpenConfig(
            enabled=True,
            default_fallback_limit=25,
        )
        manager = SafeOpenFallbackManager(config, redis_client=None)

        # 명시적으로 캐시 없는 상태 생성 (initial_limit=0이면 캐시 미사용으로 간주)
        state = manager.get_or_create_service_state("no_cache_test", initial_limit=0)
        state.last_known_safe_limit = 0  # 캐시 없음 시뮬레이션

        limit, source = manager.get_safe_limit("no_cache_test", default_limit=100)

        # last_known_safe_limit=0이면 default_fallback_limit(25) 반환
        assert limit == 25
        assert source == "default"

    def test_redis_health_check_updates_state(self, redis_client):
        """Redis 헬스체크가 연결 상태를 올바르게 업데이트."""
        from selfhealing.services.throttle.safe_open_fallback import (
            SafeOpenFallbackManager,
            SafeOpenConfig,
            RedisConnectionState,
        )

        config = SafeOpenConfig(health_check_interval_seconds=0.1)
        manager = SafeOpenFallbackManager(config, redis_client=redis_client)

        # 헬스체크 성공
        assert manager.check_redis_health() is True
        assert manager.get_redis_state() == RedisConnectionState.CONNECTED

        # Redis 연결 끊김 시뮬레이션
        manager.force_disconnect()
        assert manager.get_redis_state() == RedisConnectionState.DISCONNECTED

        # 복구 시뮬레이션
        manager.force_connect()
        assert manager.get_redis_state() == RedisConnectionState.CONNECTED


# =============================================================================
# Test: 분산 환경 다중 서비스 동시 처리
# =============================================================================


@pytest.mark.django_db
class TestDistributedMultiServiceHandling:
    """분산 환경에서 다중 서비스 동시 처리 테스트."""

    def test_multiple_services_isolated_updates(self, limit_manager):
        """다수 서비스의 limit이 서로 독립적으로 관리되는지 확인."""
        services = [f"service_{i}" for i in range(5)]

        # 각 서비스별 초기값 설정
        for i, service in enumerate(services):
            limit_manager.update_limit_atomic(
                service_name=service,
                new_limit=100 + i * 50,
                min_limit=10,
                max_limit=1000,
            )

        # 동시에 업데이트
        def update_service(service_name: str, new_limit: int):
            limit_manager.update_limit_atomic(service_name, new_limit, 10, 1000)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_service, service, 200 + i * 100) for i, service in enumerate(services)]
            concurrent.futures.wait(futures)

        # 각 서비스별 limit 확인
        for i, service in enumerate(services):
            limit = limit_manager.get_current_limit(service)
            expected = 200 + i * 100
            assert limit == expected, f"{service}: expected {expected}, got {limit}"

    def test_high_contention_single_service(self, limit_manager):
        """단일 서비스에 고도의 경쟁 상황에서도 정합성 유지."""
        service_name = "high_contention_test"
        num_workers = 50
        updates_per_worker = 100

        # 초기값
        limit_manager.update_limit_atomic(service_name, 500, 10, 1000)

        final_values = []
        lock = threading.Lock()

        def contention_worker():
            for _ in range(updates_per_worker):
                # 랜덤하게 증가/감소
                import random

                new_limit = random.randint(50, 950)
                prev, actual = limit_manager.update_limit_atomic(service_name, new_limit, 10, 1000)

            # 마지막 업데이트 결과 저장
            final = limit_manager.get_current_limit(service_name)
            with lock:
                final_values.append(final)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(contention_worker) for _ in range(num_workers)]
            concurrent.futures.wait(futures)

        # 모든 워커가 동일한 최종값을 관찰해야 함
        # (실제로는 마지막 업데이트 시점에 따라 다를 수 있지만, bounds 내여야 함)
        for value in final_values:
            assert 10 <= value <= 1000

        final_limit = limit_manager.get_current_limit(service_name)
        assert 10 <= final_limit <= 1000
