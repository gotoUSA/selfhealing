"""
Stage 24: Partial Partition Integration Tests

실제 서비스 시나리오에서 partial partition 처리 검증

Scenarios:
1. Redis만 다운, DB는 정상 → 캐시 미스 → DB에서 직접 조회
2. DB만 다운, Redis는 정상 → 캐시에서 읽기
3. 외부 API만 다운 → 캐시된 응답 또는 재시도 큐잉
4. 모든 연결 정상 → 정상 동작
5. 모든 연결 다운 → graceful failure

Reference: docs/STAGE_24_PARTIAL_PARTITION.md
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone as tz

from selfhealing.core.connection_health import (
    ConnectionType,
    ConnectionStatus,
    ConnectionHealth,
    PartitionState,
    DefaultConnectionHealthMonitor,
)
from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    PartitionAwareFallback,
    SimpleFallback,
    CacheFirstFallback,
)


class TestCachePartitionScenario:
    """
    시나리오: Redis만 다운, DB는 정상
    예상: 캐시 미스 → DB에서 직접 조회
    """

    def test_redis_down_db_fallback(self):
        """Redis 다운 시 DB로 fallback"""
        monitor = DefaultConnectionHealthMonitor()

        # DB: 정상
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: True)

        # Redis: 다운
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: False)

        # DB 체크하여 HEALTHY 상태로
        monitor.check_health(ConnectionType.DATABASE, "primary")

        # 3회 체크하여 UNHEALTHY 상태로
        for _ in range(3):
            monitor.check_health(ConnectionType.CACHE, "redis")

        state = monitor.get_partition_state()

        assert state.db_available is True
        assert state.cache_available is False
        assert state.is_partial_partition is True

        # Fallback 전략 적용
        db_data = {"user_id": 123, "name": "Test User"}
        strategy = PartitionAwareFallback(state, db_fallback=lambda: db_data)

        # 캐시 조회 실패 시나리오
        def cache_lookup():
            raise ConnectionError("Redis connection refused")

        result = strategy.execute(primary_fn=cache_lookup, default_value=None)

        assert result.value == db_data
        assert result.used_fallback is True
        assert result.fallback_mode == FallbackMode.DEGRADE_GRACEFULLY

    def test_redis_down_with_metrics(self):
        """Redis 다운 시 메트릭 기록 확인"""
        monitor = DefaultConnectionHealthMonitor()

        # DB: 정상
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: True)

        # Redis: 다운
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: False)

        # Health check 수행
        monitor.check_health(ConnectionType.DATABASE, "primary")
        for _ in range(3):
            health = monitor.check_health(ConnectionType.CACHE, "redis")

        # 메트릭 확인
        assert health.consecutive_failures == 3
        assert health.status == ConnectionStatus.UNHEALTHY
        assert health.last_failure is not None

        all_states = monitor.get_all_health_states()
        assert len(all_states) == 2


class TestDBPartitionScenario:
    """
    시나리오: DB만 다운, Redis는 정상
    예상: 캐시에서 읽기
    """

    def test_db_down_cache_fallback(self):
        """DB 다운 시 캐시에서 읽기"""
        monitor = DefaultConnectionHealthMonitor()

        # DB: 다운
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: False)

        # Redis: 정상
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: True)

        # Health check 수행
        for _ in range(3):
            monitor.check_health(ConnectionType.DATABASE, "primary")
        monitor.check_health(ConnectionType.CACHE, "redis")

        state = monitor.get_partition_state()

        assert state.db_available is False
        assert state.cache_available is True
        assert state.is_partial_partition is True

        # Fallback 전략 적용
        cached_data = {"user_id": 123, "name": "Cached User"}
        strategy = PartitionAwareFallback(state, cache_fallback=lambda: cached_data)

        def db_lookup():
            raise ConnectionError("Database connection refused")

        result = strategy.execute(primary_fn=db_lookup, default_value=None)

        assert result.value == cached_data
        assert result.used_fallback is True
        assert result.fallback_mode == FallbackMode.USE_CACHE


class TestExternalAPIPartitionScenario:
    """
    시나리오: 외부 결제 API만 다운
    예상: 결제 재시도 큐에 넣고 사용자에게 "처리 중" 응답
    """

    def test_payment_api_down_detection(self):
        """결제 API 다운 감지"""
        monitor = DefaultConnectionHealthMonitor()

        # DB, Cache: 정상
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: True)
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: True)

        # 결제 API: 다운
        monitor.register_health_check(ConnectionType.EXTERNAL_API, "toss_payments", lambda: False)

        monitor.check_health(ConnectionType.DATABASE, "primary")
        monitor.check_health(ConnectionType.CACHE, "redis")
        for _ in range(3):
            monitor.check_health(ConnectionType.EXTERNAL_API, "toss_payments")

        state = monitor.get_partition_state()

        assert state.db_available is True
        assert state.cache_available is True
        assert state.external_apis.get("toss_payments") is False
        assert state.is_partial_partition is True

    def test_payment_api_down_fallback_response(self):
        """결제 API 다운 시 fallback 응답"""
        state = PartitionState(
            db_available=True,
            cache_available=True,
            external_apis={"toss_payments": False},
        )

        # 결제 요청이 실패하면 "처리 중" 응답 반환
        pending_response = {"status": "pending", "message": "Payment is being processed"}

        strategy = PartitionAwareFallback(state)

        def payment_request():
            raise ConnectionError("Toss API unavailable")

        result = strategy.execute(
            primary_fn=payment_request,
            default_value=pending_response,
        )

        assert result.value == pending_response
        assert result.used_fallback is True
        assert result.fallback_mode == FallbackMode.USE_DEFAULT

    def test_multiple_external_apis_partial_down(self):
        """여러 외부 API 중 일부만 다운"""
        monitor = DefaultConnectionHealthMonitor()

        # 여러 외부 API 등록
        monitor.register_health_check(ConnectionType.EXTERNAL_API, "toss_payments", lambda: False)
        monitor.register_health_check(ConnectionType.EXTERNAL_API, "notification_api", lambda: True)
        monitor.register_health_check(ConnectionType.EXTERNAL_API, "analytics_api", lambda: True)

        # Health check 수행
        for _ in range(3):
            monitor.check_health(ConnectionType.EXTERNAL_API, "toss_payments")
        monitor.check_health(ConnectionType.EXTERNAL_API, "notification_api")
        monitor.check_health(ConnectionType.EXTERNAL_API, "analytics_api")

        state = monitor.get_partition_state()

        assert state.external_apis["toss_payments"] is False
        assert state.external_apis["notification_api"] is True
        assert state.external_apis["analytics_api"] is True
        assert state.is_partial_partition is True


class TestRecoveryScenario:
    """
    시나리오: 연결 복구 후 정상 동작 재개
    """

    def test_redis_recovery(self):
        """Redis 복구 후 정상 동작"""
        redis_healthy = [False]  # Mutable for closure

        monitor = DefaultConnectionHealthMonitor()
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: True)
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: redis_healthy[0])

        # 초기: Redis 다운
        monitor.check_health(ConnectionType.DATABASE, "primary")
        for _ in range(3):
            monitor.check_health(ConnectionType.CACHE, "redis")

        state = monitor.get_partition_state()
        assert state.is_partial_partition is True

        # Redis 복구
        redis_healthy[0] = True
        monitor.check_health(ConnectionType.CACHE, "redis")

        state = monitor.get_partition_state()
        assert state.cache_available is True
        assert state.is_healthy is True

    def test_flapping_connection(self):
        """불안정한 연결 (flapping) 처리"""
        call_count = [0]

        def flapping_check():
            call_count[0] += 1
            # 매 3번째 호출만 실패
            return call_count[0] % 3 != 0

        monitor = DefaultConnectionHealthMonitor(failure_threshold=2)
        monitor.register_health_check(ConnectionType.CACHE, "redis", flapping_check)

        # 여러 번 체크하여 불안정한 상태 시뮬레이션
        statuses = []
        for _ in range(6):
            health = monitor.check_health(ConnectionType.CACHE, "redis")
            statuses.append(health.status)

        # 연속 실패가 없으므로 UNHEALTHY까지 가지 않아야 함
        # 성공 → 성공 → 실패 → 성공 → 성공 → 실패
        # failure_threshold=2이므로 DEGRADED까지만
        assert ConnectionStatus.UNHEALTHY not in statuses[:4]


class TestCacheFirstStrategy:
    """
    시나리오: 캐시 우선 전략 테스트
    """

    def test_cache_first_with_update(self):
        """캐시 미스 → DB 조회 → 캐시 업데이트"""
        cache_storage = {}

        def get_from_cache():
            return cache_storage.get("user:123")

        def get_from_db():
            return {"user_id": 123, "name": "DB User"}

        def update_cache(value):
            cache_storage["user:123"] = value

        strategy = CacheFirstFallback(
            cache_fn=get_from_cache,
            db_fn=get_from_db,
            update_cache_fn=update_cache,
        )

        # 첫 조회: 캐시 미스 → DB에서 가져옴 → 캐시 업데이트
        result = strategy.execute()
        assert result.value == {"user_id": 123, "name": "DB User"}
        assert result.used_fallback is True

        # 캐시에 저장됨
        assert cache_storage.get("user:123") is not None

        # 두 번째 조회: 캐시 히트
        result2 = strategy.execute()
        assert result2.value == {"user_id": 123, "name": "DB User"}
        assert result2.used_fallback is False


class TestPartitionStateTransitions:
    """
    시나리오: Partition 상태 전이 테스트
    """

    def test_healthy_to_partial_to_full_to_recovery(self):
        """정상 → 부분 파티션 → 완전 파티션 → 복구 전이"""
        db_healthy = [True]
        cache_healthy = [True]

        monitor = DefaultConnectionHealthMonitor(failure_threshold=2)
        monitor.register_health_check(ConnectionType.DATABASE, "primary", lambda: db_healthy[0])
        monitor.register_health_check(ConnectionType.CACHE, "redis", lambda: cache_healthy[0])

        # 1. 정상 상태
        monitor.check_health(ConnectionType.DATABASE, "primary")
        monitor.check_health(ConnectionType.CACHE, "redis")
        state = monitor.get_partition_state()
        assert state.is_healthy is True

        # 2. 캐시만 다운 → 부분 파티션
        cache_healthy[0] = False
        for _ in range(2):
            monitor.check_health(ConnectionType.CACHE, "redis")
        state = monitor.get_partition_state()
        assert state.is_partial_partition is True

        # 3. DB도 다운 → 완전 파티션
        db_healthy[0] = False
        for _ in range(2):
            monitor.check_health(ConnectionType.DATABASE, "primary")
        state = monitor.get_partition_state()
        assert state.is_full_partition is True

        # 4. 모두 복구 → 정상
        db_healthy[0] = True
        cache_healthy[0] = True
        monitor.check_health(ConnectionType.DATABASE, "primary")
        monitor.check_health(ConnectionType.CACHE, "redis")
        state = monitor.get_partition_state()
        assert state.is_healthy is True


class TestFallbackChaining:
    """
    시나리오: Fallback 체이닝 테스트
    """

    def test_fallback_chain_execution(self):
        """Fallback 체인 실행 순서"""
        execution_order = []

        def primary():
            execution_order.append("primary")
            raise RuntimeError("Primary failed")

        def explicit_fallback():
            execution_order.append("explicit_fallback")
            raise RuntimeError("Explicit fallback also failed")

        state = PartitionState(db_available=True, cache_available=False)

        def db_fallback():
            execution_order.append("db_fallback")
            return "from_db"

        strategy = PartitionAwareFallback(state, db_fallback=db_fallback)

        result = strategy.execute(
            primary_fn=primary,
            fallback_fn=explicit_fallback,
            default_value="default",
        )

        # 실행 순서: primary → explicit_fallback → db_fallback
        assert execution_order == ["primary", "explicit_fallback", "db_fallback"]
        assert result.value == "from_db"

    def test_update_partition_state_dynamically(self):
        """동적으로 partition 상태 업데이트"""
        initial_state = PartitionState(db_available=True, cache_available=False)
        strategy = PartitionAwareFallback(
            initial_state,
            db_fallback=lambda: "from_db",
            cache_fallback=lambda: "from_cache",
        )

        # 초기: cache 불가 → DB fallback 사용
        result = strategy.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
            default_value=None,
        )
        assert result.value == "from_db"

        # 상태 변경: DB 불가, cache 가용
        new_state = PartitionState(db_available=False, cache_available=True)
        strategy.update_partition_state(new_state)

        # 이제 cache fallback 사용
        result = strategy.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
            default_value=None,
        )
        assert result.value == "from_cache"
