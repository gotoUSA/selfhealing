"""
Phase 3: RollbackValueSource, ResolvedRollbackValue, RollbackValueResolver 단위 테스트.

롤백 값 해결 기능 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

from unittest.mock import MagicMock

from selfhealing.services.canary.rollback_resolver import (
    ResolvedRollbackValue,
    RollbackValueResolver,
    RollbackValueSource,
)

# =============================================================================
# Test: RollbackValueSource
# =============================================================================


class TestRollbackValueSource:
    """RollbackValueSource enum 테스트."""

    def test_source_values(self):
        """출처 값이 올바르게 정의되어 있는지 확인."""
        assert RollbackValueSource.PREVIOUS_VALUES.value == "previous_values"
        assert RollbackValueSource.CONFIG_HISTORY.value == "config_history"
        assert RollbackValueSource.DEFAULT_CONFIG.value == "default_config"
        assert RollbackValueSource.UNKNOWN.value == "unknown"


# =============================================================================
# Test: ResolvedRollbackValue
# =============================================================================


class TestResolvedRollbackValue:
    """ResolvedRollbackValue 테스트."""

    def test_create_tier1_result(self):
        """Tier 1 (previous_values) 결과 생성."""
        result = ResolvedRollbackValue(
            values={"threshold": 5},
            source=RollbackValueSource.PREVIOUS_VALUES,
            is_fallback=False,
        )

        assert result.values == {"threshold": 5}
        assert result.source == RollbackValueSource.PREVIOUS_VALUES
        assert result.is_fallback is False
        assert result.fallback_reason is None
        assert result.warning is None

    def test_create_tier3_result_with_warning(self):
        """Tier 3 (default_config) 결과 생성."""
        result = ResolvedRollbackValue(
            values={"threshold": 3},
            source=RollbackValueSource.DEFAULT_CONFIG,
            is_fallback=True,
            fallback_reason="previous_values unavailable",
            warning="Using default config",
        )

        assert result.is_fallback is True
        assert result.warning is not None

    def test_to_dict(self):
        """딕셔너리 변환."""
        result = ResolvedRollbackValue(
            values={"key": "value"},
            source=RollbackValueSource.CONFIG_HISTORY,
            is_fallback=True,
            fallback_reason="test reason",
        )

        data = result.to_dict()

        assert data["source"] == "config_history"
        assert data["is_fallback"] is True
        assert data["fallback_reason"] == "test reason"


# =============================================================================
# Test: RollbackValueResolver
# =============================================================================


class TestRollbackValueResolver:
    """RollbackValueResolver 테스트."""

    def test_tier1_previous_values(self):
        """Tier 1: previous_values 사용."""
        resolver = RollbackValueResolver()

        resolved = resolver.resolve(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            previous_values={"threshold": 5},
        )

        assert resolved.source == RollbackValueSource.PREVIOUS_VALUES
        assert resolved.values == {"threshold": 5}
        assert resolved.is_fallback is False

    def test_tier2_config_history(self):
        """Tier 2: ConfigHistory 사용."""
        mock_history = MagicMock()
        mock_version = MagicMock()
        mock_version.values = {"threshold": 4}
        mock_history.get_version_before.return_value = mock_version

        resolver = RollbackValueResolver(config_history_service=mock_history)

        resolved = resolver.resolve(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            previous_values=None,  # Tier 1 없음
            created_at="2026-01-22T10:00:00Z",
        )

        assert resolved.source == RollbackValueSource.CONFIG_HISTORY
        assert resolved.values == {"threshold": 4}
        assert resolved.is_fallback is True
        assert "previous_values was empty" in resolved.fallback_reason

    def test_tier3_default_config(self):
        """Tier 3: DefaultConfig 사용."""
        resolver = RollbackValueResolver(config_history_service=None)

        resolved = resolver.resolve(
            rollout_id="rollout-1",
            config_type="circuit_breaker",
            previous_values=None,
        )

        assert resolved.source == RollbackValueSource.DEFAULT_CONFIG
        assert "failure_threshold" in resolved.values
        assert resolved.is_fallback is True
        assert resolved.warning is not None

    def test_all_tiers_exhausted(self):
        """모든 tier 실패 시 UNKNOWN."""
        resolver = RollbackValueResolver(
            config_history_service=None,
            default_configs={},  # 빈 기본값
        )

        resolved = resolver.resolve(
            rollout_id="rollout-1",
            config_type="unknown_config",
            previous_values=None,
        )

        assert resolved.source == RollbackValueSource.UNKNOWN
        assert resolved.values == {}
        assert "CRITICAL" in resolved.warning

    def test_default_configs_contain_expected_types(self):
        """기본 설정에 예상 유형이 있는지 확인."""
        assert "circuit_breaker" in RollbackValueResolver.DEFAULT_CONFIGS
        assert "dlq" in RollbackValueResolver.DEFAULT_CONFIGS
        assert "rate_limiter" in RollbackValueResolver.DEFAULT_CONFIGS
        assert "retry" in RollbackValueResolver.DEFAULT_CONFIGS
