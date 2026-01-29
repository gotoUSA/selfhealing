"""
Safe-Open 폴백 테스트.

Phase 7: Safe-Open 폴백 기능 테스트
- Redis 정상 시 분산 limit 동기화
- Redis 다운 시 로컬 캐시 사용
- Cold Start 시 안전 limit 복구
"""

import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock

from selfhealing.services.throttle.safe_open_fallback import (
    RedisConnectionState,
    SafeOpenConfig,
    SafeOpenFallbackManager,
    ServiceSafeLimitState,
    get_safe_open_fallback_manager,
    reset_safe_open_fallback_manager,
)


class TestRedisConnectionState:
    """Redis 연결 상태 enum 테스트."""

    def test_states(self):
        """모든 연결 상태 확인."""
        assert RedisConnectionState.CONNECTED.value == "connected"
        assert RedisConnectionState.DISCONNECTED.value == "disconnected"
        assert RedisConnectionState.RECOVERING.value == "recovering"


class TestSafeOpenConfig:
    """설정 테스트."""

    def test_default_config(self):
        """기본 설정값 확인."""
        config = SafeOpenConfig()

        assert config.enabled is True
        assert config.health_check_interval_seconds == 5.0
        assert config.failure_threshold == 3
        assert config.safe_limit_save_interval_seconds == 60.0
        assert config.safe_limit_max_age_seconds == 3600
        assert config.default_fallback_limit == 50

    def test_custom_config(self):
        """커스텀 설정값 확인."""
        config = SafeOpenConfig(
            enabled=False,
            failure_threshold=5,
            default_fallback_limit=100,
        )

        assert config.enabled is False
        assert config.failure_threshold == 5
        assert config.default_fallback_limit == 100


class TestServiceSafeLimitState:
    """서비스 상태 테스트."""

    def test_create_state(self):
        """상태 생성 확인."""
        state = ServiceSafeLimitState(
            service_name="payment_api",
            last_known_safe_limit=100,
        )

        assert state.service_name == "payment_api"
        assert state.last_known_safe_limit == 100
        assert state.redis_state == RedisConnectionState.CONNECTED
        assert state.consecutive_failures == 0


class TestSafeOpenFallbackManager:
    """Safe-Open 폴백 관리자 테스트."""

    def setup_method(self):
        """각 테스트 전 리셋."""
        reset_safe_open_fallback_manager()

    def teardown_method(self):
        """각 테스트 후 리셋."""
        reset_safe_open_fallback_manager()

    def test_disabled_returns_default(self):
        """비활성화 시 기본값 반환."""
        config = SafeOpenConfig(enabled=False)
        manager = SafeOpenFallbackManager(config=config)

        limit, source = manager.get_safe_limit("payment_api", default_limit=100)

        assert limit == 100
        assert source == "default"

    def test_no_redis_returns_local_cache_or_default(self):
        """Redis 없을 때 로컬 캐시 또는 기본값 반환."""
        manager = SafeOpenFallbackManager(redis_client=None)

        # Redis 없이 호출하면 get_or_create_service_state가 default_limit으로 초기화
        limit, source = manager.get_safe_limit("payment_api", default_limit=100)

        # 로컬 캐시에 저장된 값(default_limit) 반환
        assert limit == 100
        assert source == "local_cache"

    def test_redis_connected_returns_redis_value(self):
        """Redis 연결 시 Redis 값 반환."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        manager = SafeOpenFallbackManager(redis_client=mock_redis)

        # Limit manager 모킹
        mock_limit_manager = MagicMock()
        mock_limit_manager.load_safe_limit.return_value = (75, "CURRENT")
        manager._limit_manager = mock_limit_manager

        limit, source = manager.get_safe_limit("payment_api", default_limit=100)

        assert limit == 75
        assert source == "redis_current"

    def test_redis_disconnected_returns_local_cache(self):
        """Redis 끊김 시 로컬 캐시 반환."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Connection refused")

        manager = SafeOpenFallbackManager(redis_client=mock_redis)

        # 먼저 로컬 캐시에 값 설정
        state = manager.get_or_create_service_state("payment_api", 80)
        state.last_known_safe_limit = 80

        limit, source = manager.get_safe_limit("payment_api", default_limit=100)

        assert limit == 80
        assert source == "local_cache"

    def test_update_safe_limit_updates_local_cache(self):
        """limit 업데이트 시 로컬 캐시 갱신."""
        manager = SafeOpenFallbackManager(redis_client=None)

        manager.update_safe_limit("payment_api", 90)

        state = manager._service_states.get("payment_api")
        assert state is not None
        assert state.last_known_safe_limit == 90

    def test_force_disconnect(self):
        """강제 연결 끊김 시뮬레이션."""
        manager = SafeOpenFallbackManager()

        manager.force_disconnect()

        assert manager.get_redis_state() == RedisConnectionState.DISCONNECTED

    def test_force_connect(self):
        """강제 연결 복구 시뮬레이션."""
        manager = SafeOpenFallbackManager()
        manager.force_disconnect()

        manager.force_connect()

        assert manager.get_redis_state() == RedisConnectionState.CONNECTED

    def test_get_service_state(self):
        """서비스 상태 조회."""
        manager = SafeOpenFallbackManager()

        manager.update_safe_limit("payment_api", 100)

        state = manager.get_service_state("payment_api")

        assert state is not None
        assert state["service_name"] == "payment_api"
        assert state["last_known_safe_limit"] == 100
        assert state["redis_state"] == "connected"

    def test_get_nonexistent_service_state(self):
        """존재하지 않는 서비스 조회."""
        manager = SafeOpenFallbackManager()

        state = manager.get_service_state("nonexistent")

        assert state is None

    def test_consecutive_failures_trigger_disconnect(self):
        """연속 실패 시 연결 끊김 판단."""
        config = SafeOpenConfig(failure_threshold=3)
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = Exception("Connection error")

        manager = SafeOpenFallbackManager(config=config, redis_client=mock_redis)

        # 먼저 서비스 상태 생성
        manager.get_or_create_service_state("payment_api", 100)

        # 실패 기록
        for _ in range(3):
            manager._record_failure("payment_api")

        state = manager._service_states.get("payment_api")
        assert state.redis_state == RedisConnectionState.DISCONNECTED

    def test_health_check_interval_respected(self):
        """헬스체크 간격 준수."""
        config = SafeOpenConfig(health_check_interval_seconds=10.0)
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        manager = SafeOpenFallbackManager(config=config, redis_client=mock_redis)

        # 첫 번째 체크
        result1 = manager.check_redis_health()
        assert result1 is True

        # 바로 다시 체크 (캐시된 결과 반환)
        result2 = manager.check_redis_health()
        assert result2 is True

        # ping은 한 번만 호출됨
        assert mock_redis.ping.call_count == 1

    def test_reset_clears_all_state(self):
        """reset 시 모든 상태 초기화."""
        manager = SafeOpenFallbackManager()

        manager.update_safe_limit("service1", 100)
        manager.update_safe_limit("service2", 200)

        manager.reset()

        assert manager.get_all_service_states() == []

    def test_singleton_instance(self):
        """싱글톤 인스턴스 테스트."""
        instance1 = get_safe_open_fallback_manager()
        instance2 = get_safe_open_fallback_manager()

        assert instance1 is instance2

        reset_safe_open_fallback_manager()
        instance3 = get_safe_open_fallback_manager()

        assert instance1 is not instance3

    def test_save_with_redis_success(self):
        """Redis 저장 성공."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        manager = SafeOpenFallbackManager(redis_client=mock_redis)

        # Limit manager 모킹
        mock_limit_manager = MagicMock()
        mock_limit_manager.save_safe_limit.return_value = True
        manager._limit_manager = mock_limit_manager

        result = manager.update_safe_limit("payment_api", 100, force_save=True)

        assert result is True
