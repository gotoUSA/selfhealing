"""
Circuit Breaker Stale Cache 통합 테스트.

Test Coverage:
- CanaryWithStaleCacheService: Canary + Stale Cache 결합
- build_stale_cache_key: 캐시 키 생성 헬퍼 (#234)
- record_success 캐시 자동 저장 (#234)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

from selfhealing.services.circuit_breaker.models import (
    ServiceConfig,
    RecoveryStrategy,
    CanaryRecoveryStageConfig,
)
from selfhealing.services.circuit_breaker.stale_cache_integration import (
    CanaryWithStaleCacheService,
    build_stale_cache_key,
)


# =============================================================================
# 4.2 CanaryWithStaleCacheService Tests
# =============================================================================


class TestStaleCacheStore:
    """StaleCacheStore 테스트."""

    def test_set_and_get(self):
        """캐시 저장 및 조회."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )

        store = StaleCacheStore()
        store.set("key1", {"data": "value"}, ttl_seconds=300)

        entry = store.get("key1", max_stale_age=300)

        assert entry is not None
        assert entry.value == {"data": "value"}

    def test_get_nonexistent_returns_none(self):
        """없는 키 조회 시 None 반환."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )

        store = StaleCacheStore()

        entry = store.get("nonexistent")

        assert entry is None

    def test_stale_detection(self):
        """Stale 상태 감지."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheEntry,
        )

        entry = StaleCacheEntry(
            key="key1",
            value="data",
            ttl_seconds=1,
        )
        # TTL 초과 시뮬레이션
        entry.cached_at = datetime.now(timezone.utc) - timedelta(seconds=2)

        assert entry.is_stale() is True

    def test_cache_stats(self):
        """캐시 통계."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )

        store = StaleCacheStore()
        store.set("key1", "value1")
        store.get("key1")
        store.get("key2")  # miss

        stats = store.get_stats()

        assert stats["sets"] == 1
        assert stats["hits"] >= 1 or stats["stale_hits"] >= 1
        assert stats["misses"] >= 1


class TestCanaryWithStaleCacheService:
    """CanaryWithStaleCacheService 테스트."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )

        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()

    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )

        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()

    def test_closed_state_allows_backend(self):
        """CLOSED 상태에서 백엔드 허용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="closed",
        )

        assert decision.allow_backend is True
        assert decision.use_stale is False

    def test_open_state_uses_stale_cache(self):
        """OPEN 상태에서 Stale Cache 사용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()
        service.update_cache("payment:123", {"amount": 100}, "payment-api")

        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="open",
        )

        assert decision.allow_backend is False
        assert decision.use_stale is True
        assert decision.stale_data == {"amount": 100}

    def test_open_state_rejects_without_cache(self):
        """OPEN 상태에서 캐시 없으면 거부."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:unknown",
            cb_state="open",
        )

        assert decision.allow_backend is False
        assert decision.reject is True

    def test_half_open_canary_request(self):
        """HALF_OPEN 상태에서 Canary 요청."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )

        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()

        # Canary 복구 시작 (100% 트래픽으로 설정)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=100.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        manager.start_canary_recovery("payment-api", strategy)

        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="half_open",
        )

        # 100% 트래픽이므로 항상 canary
        assert decision.allow_backend is True
        assert decision.is_canary_request is True

    def test_half_open_non_canary_uses_stale(self):
        """HALF_OPEN에서 non-canary 요청은 Stale Cache 사용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )

        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()

        # 캐시 설정
        service.update_cache("payment:123", {"amount": 100}, "payment-api")

        # 0% 트래픽 Canary (모든 요청이 non-canary)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryRecoveryStageConfig(traffic_percent=0.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        manager.start_canary_recovery("payment-api", strategy)

        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="half_open",
        )

        # 0% 트래픽이므로 모두 stale cache 사용
        assert decision.allow_backend is False
        assert decision.use_stale is True
        assert decision.stale_data == {"amount": 100}

    def test_record_success_updates_canary(self):
        """성공 기록이 Canary 매니저에 전달됨."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )

        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()

        manager.start_canary_recovery("payment-api")
        service.record_success("payment-api")

        state = manager.get_recovery_state("payment-api")
        assert state.metrics.success_count == 1

    def test_get_stats(self):
        """통합 통계 조회."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        stats = service.get_stats()

        assert "canary_allowed" in stats
        assert "stale_served" in stats
        assert "cache_stats" in stats


# =============================================================================
# 동작 검증 — build_stale_cache_key (#234)
# =============================================================================


class TestBuildStaleCacheKeyBehavior:
    """build_stale_cache_key() 동작 검증 (#234)."""

    def test_static_method_format(self):
        """정적 메서드가 'domain:identifier' 형식의 키를 반환한다."""
        result = CanaryWithStaleCacheService.build_stale_cache_key(
            "payment",
            "user123",
        )
        assert result == "payment:user123"

    def test_module_level_function_delegates(self):
        """모듈 레벨 build_stale_cache_key()는 정적 메서드와 동일한 결과를 반환한다."""
        static_result = CanaryWithStaleCacheService.build_stale_cache_key(
            "product",
            "order456",
        )
        module_result = build_stale_cache_key("product", "order456")
        assert module_result == static_result

    def test_various_domains(self):
        """다양한 domain 값이 올바르게 키에 포함된다."""
        assert build_stale_cache_key("payment", "123") == "payment:123"
        assert build_stale_cache_key("product", "abc") == "product:abc"
        assert build_stale_cache_key("user", "xyz") == "user:xyz"

    def test_empty_strings(self):
        """빈 문자열도 형식을 유지한다."""
        result = build_stale_cache_key("", "")
        assert result == ":"

    def test_special_characters_in_identifier(self):
        """식별자에 특수 문자가 포함되어도 그대로 유지한다."""
        result = build_stale_cache_key("payment", "user-123_v2")
        assert result == "payment:user-123_v2"

    def test_consistent_key_for_same_input(self):
        """동일 입력은 항상 동일 키를 생성한다 (결정론적)."""
        key1 = build_stale_cache_key("service", "id")
        key2 = build_stale_cache_key("service", "id")
        assert key1 == key2


# =============================================================================
# 동작 검증 — record_success 캐시 자동 저장 (#234)
# =============================================================================


class TestRecordSuccessAutoCacheBehavior:
    """record_success() 캐시 자동 저장 동작 검증 (#234)."""

    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )

        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()

    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )

        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()

    def test_record_success_without_cache_params(self):
        """cache_key/response_data 없이 호출 시 기존 동작 유지 (캐시 저장 안 함)."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()
        service.record_success("payment-api")

        # 캐시에 아무것도 저장되지 않음
        assert service._cache.get("payment:123") is None

    def test_record_success_with_cache_params_stores_data(self):
        """cache_key + response_data 전달 시 캐시에 자동 저장된다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()
        cache_key = build_stale_cache_key("payment", "123")
        response_data = {"amount": 500, "currency": "KRW"}

        service.record_success(
            "payment-api",
            cache_key=cache_key,
            response_data=response_data,
        )

        # 캐시에 저장되었는지 확인
        entry = service._cache.get(
            cache_key,
            max_stale_age=service._config.stale_cache_max_age_seconds,
        )
        assert entry is not None
        assert entry.value == response_data

    def test_record_success_cache_key_only_no_store(self):
        """cache_key만 전달하고 response_data가 None이면 캐시 저장하지 않는다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()
        cache_key = build_stale_cache_key("payment", "456")

        service.record_success("payment-api", cache_key=cache_key)

        entry = service._cache.get(cache_key)
        assert entry is None

    def test_record_success_response_data_only_no_store(self):
        """response_data만 전달하고 cache_key가 None이면 캐시 저장하지 않는다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        service.record_success(
            "payment-api",
            response_data={"amount": 100},
        )

        # cache_key가 없으므로 저장 불가 — 에러 없이 종료
        assert service._cache.get_stats()["sets"] == 0

    def test_record_success_auto_cache_still_records_metrics(self):
        """캐시 자동 저장 시에도 성공 메트릭은 정상 기록된다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )

        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()
        manager.start_canary_recovery("payment-api")

        service.record_success(
            "payment-api",
            cache_key=build_stale_cache_key("payment", "789"),
            response_data={"amount": 200},
        )

        # 성공 메트릭 확인
        with service._stats_lock:
            assert service._stats["backend_success"] >= 1

        # Canary 매니저에도 전달 확인
        state = manager.get_recovery_state("payment-api")
        assert state.metrics.success_count == 1

    def test_record_success_cache_failure_suppressed(self):
        """update_cache() 실패 시 예외가 suppress되어 record_success()는 성공한다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        with patch.object(
            service,
            "update_cache",
            side_effect=RuntimeError("cache write failed"),
        ):
            # 예외 발생하지 않아야 함
            service.record_success(
                "payment-api",
                cache_key="payment:error",
                response_data={"data": "test"},
            )

        # 성공 메트릭은 정상 기록됨
        with service._stats_lock:
            assert service._stats["backend_success"] >= 1

    def test_record_success_cache_failure_logs_warning(self):
        """update_cache() 실패 시 logger.warning이 호출된다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        with (
            patch.object(
                service,
                "update_cache",
                side_effect=RuntimeError("disk full"),
            ),
            patch(
                "selfhealing.services.circuit_breaker.stale_cache_integration.logger",
            ) as mock_logger,
        ):
            service.record_success(
                "payment-api",
                cache_key="payment:warn",
                response_data={"data": "test"},
            )
            mock_logger.warning.assert_called_once()
            assert "Auto cache update failed" in str(
                mock_logger.warning.call_args,
            )

    def test_backward_compatible_signature(self):
        """기존 시그니처 record_success(service_id)와 하위 호환된다."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )

        service = get_canary_stale_cache_service()

        # 기존 방식 — 위치 인자로 service_id만 전달
        service.record_success("payment-api")

        with service._stats_lock:
            assert service._stats["backend_success"] >= 1
