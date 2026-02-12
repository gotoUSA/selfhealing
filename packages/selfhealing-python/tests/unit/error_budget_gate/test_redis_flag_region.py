"""
BudgetExhaustedFlagManager 리전별 Redis 키 분리 테스트.

리전별 키 생성, set_exhausted/is_exhausted의 region 파라미터 동작 검증.
"""

import pytest
import time
from unittest.mock import MagicMock

from selfhealing.services.error_budget_gate.redis_flag import (
    BudgetExhaustedFlagManager,
    BUDGET_EXHAUSTED_BY_SLO_KEY,
    BUDGET_EXHAUSTED_BY_SLO_REGION_KEY,
    BUDGET_STATUS_KEY,
    BUDGET_STATUS_REGION_KEY,
)


# =============================================================================
# 계약 검증: 리전별 키 패턴
# =============================================================================


class TestRegionalRedisKeyPatternsContract:
    """리전별 Redis 키 패턴 계약 검증."""

    def test_global_slo_key_pattern(self):
        """글로벌 SLO 키 패턴에 region 없음."""
        key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        assert "availability" in key
        assert "{region}" not in key

    def test_regional_slo_key_pattern(self):
        """리전별 SLO 키 패턴에 slo_name과 region 포함."""
        key = BUDGET_EXHAUSTED_BY_SLO_REGION_KEY.format(slo_name="availability", region="seoul")
        assert "availability" in key
        assert "seoul" in key

    def test_regional_status_key_pattern(self):
        """리전별 상태 키 패턴에 slo_name과 region 포함."""
        key = BUDGET_STATUS_REGION_KEY.format(slo_name="availability", region="tokyo")
        assert "availability" in key
        assert "tokyo" in key


# =============================================================================
# 동작 검증: _build_slo_key()
# =============================================================================


class TestBuildSloKeyBehavior:
    """_build_slo_key() 동작 검증."""

    def test_no_region_returns_global_key(self):
        """region=None이면 글로벌 키 반환."""
        key = BudgetExhaustedFlagManager._build_slo_key("availability", region=None)
        expected = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        assert key == expected

    def test_region_returns_regional_key(self):
        """region 지정 시 리전별 키 반환."""
        key = BudgetExhaustedFlagManager._build_slo_key("availability", region="seoul")
        expected = BUDGET_EXHAUSTED_BY_SLO_REGION_KEY.format(slo_name="availability", region="seoul")
        assert key == expected


# =============================================================================
# 동작 검증: _build_status_key()
# =============================================================================


class TestBuildStatusKeyBehavior:
    """_build_status_key() 동작 검증."""

    def test_no_region_returns_global_status_key(self):
        """region=None이면 글로벌 상태 키 반환."""
        key = BudgetExhaustedFlagManager._build_status_key("availability", region=None)
        expected = BUDGET_STATUS_KEY.format(slo_name="availability")
        assert key == expected

    def test_region_returns_regional_status_key(self):
        """region 지정 시 리전별 상태 키 반환."""
        key = BudgetExhaustedFlagManager._build_status_key("availability", region="seoul")
        expected = BUDGET_STATUS_REGION_KEY.format(slo_name="availability", region="seoul")
        assert key == expected


# =============================================================================
# 동작 검증: set_exhausted/is_exhausted with region
# =============================================================================


class TestRegionalSetIsExhaustedBehavior:
    """리전별 set_exhausted/is_exhausted 동작 검증."""

    def test_set_regional_exhausted_uses_regional_key(self):
        """set_exhausted(region="seoul")은 리전별 Redis 키 사용."""
        mock_redis = MagicMock()
        manager = BudgetExhaustedFlagManager(redis_client=mock_redis)

        manager.set_exhausted("availability", True, region="seoul")

        expected_key = BUDGET_EXHAUSTED_BY_SLO_REGION_KEY.format(slo_name="availability", region="seoul")
        mock_redis.setex.assert_called_once()
        actual_key = mock_redis.setex.call_args[0][0]
        assert actual_key == expected_key

    def test_set_global_exhausted_uses_global_key(self):
        """set_exhausted(region=None)은 글로벌 Redis 키 사용."""
        mock_redis = MagicMock()
        manager = BudgetExhaustedFlagManager(redis_client=mock_redis)

        manager.set_exhausted("availability", True, region=None)

        expected_key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        mock_redis.setex.assert_called_once()
        actual_key = mock_redis.setex.call_args[0][0]
        assert actual_key == expected_key

    def test_is_exhausted_local_cache_isolated_by_region(self):
        """리전별 로컬 캐시가 분리됨."""
        manager = BudgetExhaustedFlagManager(redis_client=None)

        manager.set_exhausted("availability", True, region="seoul")
        manager.set_exhausted("availability", False, region="tokyo")

        assert manager.is_exhausted("availability", region="seoul") is True
        assert manager.is_exhausted("availability", region="tokyo") is False

    def test_is_exhausted_without_region_returns_global(self):
        """region 미지정 시 글로벌 캐시 반환."""
        manager = BudgetExhaustedFlagManager(redis_client=None)

        manager.set_exhausted("availability", True, region=None)
        manager.set_exhausted("availability", False, region="seoul")

        assert manager.is_exhausted("availability", region=None) is True
        assert manager.is_exhausted("availability", region="seoul") is False

    def test_fail_open_when_no_redis_and_no_cache(self):
        """Redis 없고 캐시 미적중 시 False(Fail-Open) 반환."""
        manager = BudgetExhaustedFlagManager(redis_client=None)
        assert manager.is_exhausted("availability", region="unknown") is False
