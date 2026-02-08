"""
Tests for FallbackStrategy.
core/fallback_strategy.py의 SimpleFallback, PartitionAwareFallback,
CacheFirstFallback에 대한 단위 테스트.
주요 폴백 경로, 에러 전파, 기본값 사용 등을 검증합니다.
"""

import pytest
from unittest.mock import MagicMock

from selfhealing.core.fallback_strategy import (
    FallbackMode,
    FallbackResult,
    SimpleFallback,
    PartitionAwareFallback,
    CacheFirstFallback,
)
from selfhealing.core.connection_health import PartitionState


# =============================================================================
# FallbackResult Tests
# =============================================================================


class TestFallbackResult:
    """FallbackResult 데이터클래스 테스트."""

    def test_success_without_fallback(self):
        """Success without fallback
        폴백 없이 성공한 경우 success=True인지 확인.
        """
        result = FallbackResult(value="data", used_fallback=False)
        assert result.success is True
        assert result.used_fallback is False

    def test_success_with_fallback(self):
        """Success with fallback
        폴백을 사용하여 성공한 경우 success=True인지 확인.
        """
        result = FallbackResult(
            value="cached_data",
            used_fallback=True,
            fallback_mode=FallbackMode.USE_CACHE,
        )
        assert result.success is True

    def test_failure_with_fail_fast(self):
        """Failure with fail fast
        모든 폴백이 실패하고 FAIL_FAST일 때 success=False인지 확인.
        """
        result = FallbackResult(
            value=None,
            used_fallback=True,
            fallback_mode=FallbackMode.FAIL_FAST,
        )
        assert result.success is False


# =============================================================================
# SimpleFallback Tests
# =============================================================================


class TestSimpleFallback:
    """SimpleFallback 전략 테스트."""

    def test_primary_success(self):
        """Primary success
        주 함수가 성공하면 폴백을 사용하지 않는지 확인.
        """
        fallback = SimpleFallback()
        result = fallback.execute(primary_fn=lambda: "primary_data")
        assert result.value == "primary_data"
        assert result.used_fallback is False

    def test_fallback_fn_used_on_primary_failure(self):
        """Fallback fn used on primary failure
        주 함수가 실패하면 폴백 함수가 사용되는지 확인.
        """

        def failing_primary():
            raise ConnectionError("Primary down")

        fallback = SimpleFallback()
        result = fallback.execute(
            primary_fn=failing_primary,
            fallback_fn=lambda: "fallback_data",
        )
        assert result.value == "fallback_data"
        assert result.used_fallback is True
        assert result.fallback_mode == FallbackMode.RETRY_ALTERNATIVE

    def test_default_value_used_when_all_fail(self):
        """Default value used when all fail
        주 함수와 폴백 함수 모두 실패하면 기본값이 사용되는지 확인.
        """
        fallback = SimpleFallback()
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
            fallback_fn=lambda: (_ for _ in ()).throw(Exception("fail too")),
            default_value="default",
        )
        assert result.value == "default"
        assert result.fallback_mode == FallbackMode.USE_DEFAULT

    def test_all_fail_returns_none(self):
        """All fail returns None
        모든 경로가 실패하고 기본값도 없으면 None이 반환되는지 확인.
        """
        fallback = SimpleFallback()
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
        )
        assert result.value is None
        assert result.fallback_mode == FallbackMode.FAIL_FAST
        assert result.original_error is not None

    def test_fallback_fn_failure_uses_default(self):
        """Fallback fn failure uses default
        폴백 함수도 실패하면 기본값이 사용되는지 확인.
        """
        fallback = SimpleFallback()
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(ValueError("p")),
            fallback_fn=lambda: (_ for _ in ()).throw(ValueError("f")),
            default_value=42,
        )
        assert result.value == 42


# =============================================================================
# PartitionAwareFallback Tests
# =============================================================================


class TestPartitionAwareFallback:
    """PartitionAwareFallback 전략 테스트."""

    def test_primary_success(self):
        """Primary success
        주 함수가 성공하면 폴백을 사용하지 않는지 확인.
        """
        state = PartitionState(db_available=True, cache_available=True)
        fallback = PartitionAwareFallback(partition_state=state)
        result = fallback.execute(primary_fn=lambda: "data")
        assert result.value == "data"
        assert result.used_fallback is False

    def test_db_fallback_when_cache_unavailable(self):
        """DB fallback when cache unavailable
        캐시 사용 불가 + DB 가용 시 DB 폴백이 사용되는지 확인.
        """
        state = PartitionState(db_available=True, cache_available=False)
        fallback = PartitionAwareFallback(
            partition_state=state,
            db_fallback=lambda: "db_data",
        )
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
        )
        assert result.value == "db_data"
        assert result.fallback_mode == FallbackMode.DEGRADE_GRACEFULLY

    def test_cache_fallback_when_db_unavailable(self):
        """Cache fallback when DB unavailable
        DB 사용 불가 + 캐시 가용 시 캐시 폴백이 사용되는지 확인.
        """
        state = PartitionState(db_available=False, cache_available=True)
        fallback = PartitionAwareFallback(
            partition_state=state,
            cache_fallback=lambda: "cached_data",
        )
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
        )
        assert result.value == "cached_data"
        assert result.fallback_mode == FallbackMode.USE_CACHE

    def test_explicit_fallback_fn_priority(self):
        """Explicit fallback fn priority
        명시적 fallback_fn이 파티션 기반 폴백보다 우선하는지 확인.
        """
        state = PartitionState(db_available=True, cache_available=False)
        fallback = PartitionAwareFallback(
            partition_state=state,
            db_fallback=lambda: "db_data",
        )
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
            fallback_fn=lambda: "explicit_fallback",
        )
        assert result.value == "explicit_fallback"
        assert result.fallback_mode == FallbackMode.RETRY_ALTERNATIVE

    def test_default_value_as_last_resort(self):
        """Default value as last resort
        모든 폴백이 실패하면 기본값이 사용되는지 확인.
        """
        state = PartitionState(db_available=False, cache_available=False)
        fallback = PartitionAwareFallback(partition_state=state)
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
            default_value="last_resort",
        )
        assert result.value == "last_resort"
        assert result.fallback_mode == FallbackMode.USE_DEFAULT

    def test_all_fail_returns_none(self):
        """All fail returns None
        모든 폴백이 실패하고 기본값도 없으면 FAIL_FAST를 반환하는지 확인.
        """
        state = PartitionState(db_available=False, cache_available=False)
        fallback = PartitionAwareFallback(partition_state=state)
        result = fallback.execute(
            primary_fn=lambda: (_ for _ in ()).throw(Exception("fail")),
        )
        assert result.value is None
        assert result.fallback_mode == FallbackMode.FAIL_FAST

    def test_update_partition_state(self):
        """Update partition state
        파티션 상태 업데이트 후 올바르게 반영되는지 확인.
        """
        state = PartitionState(db_available=False, cache_available=False)
        fallback = PartitionAwareFallback(partition_state=state)

        new_state = PartitionState(db_available=True, cache_available=True)
        fallback.update_partition_state(new_state)

        assert fallback._partition_state.db_available is True


# =============================================================================
# CacheFirstFallback Tests
# =============================================================================


class TestCacheFirstFallback:
    """CacheFirstFallback 전략 테스트."""

    def test_cache_hit(self):
        """Cache hit
        캐시에 데이터가 있으면 즉시 반환하는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: "cached",
            db_fn=lambda: "db_data",
        )
        result = fallback.execute()
        assert result.value == "cached"
        assert result.used_fallback is False

    def test_cache_miss_falls_back_to_db(self):
        """Cache miss falls back to DB
        캐시 미스 시 DB에서 가져오는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: None,  # cache miss
            db_fn=lambda: "db_data",
        )
        result = fallback.execute()
        assert result.value == "db_data"
        assert result.used_fallback is True

    def test_cache_error_falls_back_to_db(self):
        """Cache error falls back to DB
        캐시 에러 시 DB에서 가져오는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: (_ for _ in ()).throw(ConnectionError("redis down")),
            db_fn=lambda: "db_data",
        )
        result = fallback.execute()
        assert result.value == "db_data"
        assert result.used_fallback is True

    def test_cache_update_on_db_read(self):
        """Cache update on DB read
        DB에서 읽은 후 캐시가 업데이트되는지 확인.
        """
        cache_updated = []
        fallback = CacheFirstFallback(
            cache_fn=lambda: None,
            db_fn=lambda: "db_data",
            update_cache_fn=lambda v: cache_updated.append(v),
        )
        result = fallback.execute()
        assert result.value == "db_data"
        assert cache_updated == ["db_data"]

    def test_cache_update_failure_ignored(self):
        """Cache update failure ignored
        캐시 업데이트 실패가 전체 동작에 영향을 주지 않는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: None,
            db_fn=lambda: "db_data",
            update_cache_fn=lambda v: (_ for _ in ()).throw(Exception("update fail")),
        )
        result = fallback.execute()
        assert result.value == "db_data"  # 업데이트 실패에도 정상 반환

    def test_both_fail_uses_default(self):
        """Both fail uses default
        캐시와 DB 모두 실패하면 기본값이 사용되는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: (_ for _ in ()).throw(Exception("c")),
            db_fn=lambda: (_ for _ in ()).throw(Exception("d")),
        )
        result = fallback.execute(default_value="default")
        assert result.value == "default"
        assert result.fallback_mode == FallbackMode.USE_DEFAULT

    def test_both_fail_no_default_returns_none(self):
        """Both fail no default returns None
        모든 경로 실패 + 기본값 없으면 None을 반환하는지 확인.
        """
        fallback = CacheFirstFallback(
            cache_fn=lambda: (_ for _ in ()).throw(Exception("c")),
            db_fn=lambda: (_ for _ in ()).throw(Exception("d")),
        )
        result = fallback.execute()
        assert result.value is None
        assert result.fallback_mode == FallbackMode.FAIL_FAST


# =============================================================================
# FallbackMode Enum Tests
# =============================================================================


class TestFallbackMode:
    """FallbackMode enum 테스트."""

    def test_all_modes_exist(self):
        """All modes exist
        모든 폴백 모드가 존재하는지 확인.
        """
        assert FallbackMode.FAIL_FAST.value == "fail_fast"
        assert FallbackMode.USE_CACHE.value == "use_cache"
        assert FallbackMode.USE_DEFAULT.value == "use_default"
        assert FallbackMode.DEGRADE_GRACEFULLY.value == "degrade"
        assert FallbackMode.RETRY_ALTERNATIVE.value == "retry_alt"
        assert FallbackMode.HEDGE.value == "hedge"
