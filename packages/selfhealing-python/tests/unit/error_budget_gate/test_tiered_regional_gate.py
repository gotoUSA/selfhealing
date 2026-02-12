"""
ErrorBudgetGate 티어/리전별 판정 동작 테스트.

Gate.check()에 tier_id/region 전달 시 차등 임계치 적용과
캐시 키 분리, 리전 Fallback 동작 검증.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.services.error_budget_gate.config import (
    ErrorBudgetGateConfig,
    GateCheckResult,
    GateStatus,
)
from selfhealing.services.error_budget_gate.gate import ErrorBudgetGate


# =============================================================================
# 동작 검증: 티어별 차등 임계치 적용
# =============================================================================


class TestTierAwareGateEvaluationBehavior:
    """Gate._evaluate()에 tier_id 전달 시 차등 임계치 적용 동작."""

    def _make_gate(self, tier_thresholds_enabled: bool = True) -> ErrorBudgetGate:
        """티어 임계치가 활성화된 Gate 생성."""
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
            threshold_hysteresis_buffer_percent=2.0,
            tier_thresholds_enabled=tier_thresholds_enabled,
        )
        return ErrorBudgetGate(config=config)

    def test_critical_tier_blocks_at_higher_threshold(self):
        """critical 티어는 15% 미만에서 차단 (글로벌 10%보다 높음)."""
        gate = self._make_gate()
        gate._current_status = GateStatus.OPEN

        with patch.object(gate, "_get_error_budget_percent", return_value=12.0):
            result = gate.check(force_refresh=True, tier_id="critical")

        # critical 티어 critical_threshold=15.0, 12% < 15% → BLOCKED
        assert result.status == GateStatus.BLOCKED
        assert result.allowed is False
        assert result.tier_id == "critical"

    def test_non_essential_tier_allows_at_lower_threshold(self):
        """non_essential 티어는 5% 미만에서만 차단."""
        gate = self._make_gate()
        gate._current_status = GateStatus.OPEN

        with patch.object(gate, "_get_error_budget_percent", return_value=7.0):
            result = gate.check(force_refresh=True, tier_id="non_essential")

        # non_essential critical_threshold=5.0, 7% > 5% → WARNING 또는 OPEN
        assert result.allowed is True

    def test_standard_tier_matches_global_thresholds(self):
        """standard 티어는 글로벌과 동일한 임계치."""
        gate = self._make_gate()
        config = gate.get_config()

        critical, warning = config.get_thresholds_for_tier("standard")
        assert critical == config.critical_threshold_percent
        assert warning == config.warning_threshold_percent

    def test_tier_disabled_uses_global_threshold(self):
        """tier_thresholds_enabled=False면 글로벌 임계치 사용."""
        gate = self._make_gate(tier_thresholds_enabled=False)
        gate._current_status = GateStatus.OPEN

        # 12%는 글로벌 critical=10% 이상 → WARNING
        with patch.object(gate, "_get_error_budget_percent", return_value=12.0):
            result = gate.check(force_refresh=True, tier_id="critical")

        assert result.allowed is True

    def test_none_tier_defaults_to_standard(self):
        """tier_id=None이면 'standard'로 처리."""
        gate = self._make_gate()
        gate._current_status = GateStatus.OPEN

        with patch.object(gate, "_get_error_budget_percent", return_value=15.0):
            result_none = gate.check(force_refresh=True, tier_id=None)

        gate._current_status = GateStatus.OPEN
        gate.clear_cache()
        with patch.object(gate, "_get_error_budget_percent", return_value=15.0):
            result_standard = gate.check(force_refresh=True, tier_id="standard")

        assert result_none.status == result_standard.status


# =============================================================================
# 동작 검증: 리전 전달 시 Gate 동작
# =============================================================================


class TestRegionAwareGateCheckBehavior:
    """Gate.check()에 region 전달 시 동작."""

    def test_region_passed_to_get_error_budget_percent(self):
        """region이 _get_error_budget_percent()에 전달됨."""
        config = ErrorBudgetGateConfig(enabled=True)
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, "_get_error_budget_percent", return_value=50.0) as mock:
            gate.check(force_refresh=True, region="seoul")

        mock.assert_called_once_with(region="seoul")

    def test_region_included_in_result(self):
        """결과에 region 필드가 포함됨."""
        config = ErrorBudgetGateConfig(enabled=True)
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, "_get_error_budget_percent", return_value=50.0):
            result = gate.check(force_refresh=True, region="tokyo")

        assert result.region == "tokyo"

    def test_disabled_gate_preserves_region(self):
        """게이트 비활성화 시에도 region이 결과에 포함됨."""
        config = ErrorBudgetGateConfig(enabled=False)
        gate = ErrorBudgetGate(config=config)

        result = gate.check(region="seoul")
        assert result.region == "seoul"

    def test_disabled_gate_preserves_tier_id(self):
        """게이트 비활성화 시에도 tier_id가 결과에 포함됨."""
        config = ErrorBudgetGateConfig(enabled=False)
        gate = ErrorBudgetGate(config=config)

        result = gate.check(tier_id="critical")
        assert result.tier_id == "critical"


# =============================================================================
# 동작 검증: 캐시 키 분리
# =============================================================================


class TestTierRegionCacheKeyBehavior:
    """티어/리전별 캐시 키 분리 동작."""

    def test_different_tiers_use_different_cache_keys(self):
        """다른 tier_id는 서로 다른 캐시 키 사용."""
        key1 = ErrorBudgetGate._build_cache_key(None, "critical")
        key2 = ErrorBudgetGate._build_cache_key(None, "standard")
        assert key1 != key2

    def test_different_regions_use_different_cache_keys(self):
        """다른 region은 서로 다른 캐시 키 사용."""
        key1 = ErrorBudgetGate._build_cache_key("seoul", None)
        key2 = ErrorBudgetGate._build_cache_key("tokyo", None)
        assert key1 != key2

    def test_none_tier_and_region_produces_global_key(self):
        """tier_id=None, region=None은 글로벌 키 생성."""
        key = ErrorBudgetGate._build_cache_key(None, None)
        assert "__global__" in key

    def test_cache_key_includes_both_region_and_tier(self):
        """캐시 키에 리전과 티어 모두 포함."""
        key = ErrorBudgetGate._build_cache_key("seoul", "critical")
        assert "seoul" in key
        assert "critical" in key

    def test_cached_result_returned_for_same_tier_region(self):
        """동일 tier/region 조합은 캐시된 결과 반환."""
        config = ErrorBudgetGateConfig(enabled=True, cache_ttl_seconds=300)
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, "_get_error_budget_percent", return_value=50.0) as mock:
            result1 = gate.check(force_refresh=True, tier_id="critical", region="seoul")
            result2 = gate.check(tier_id="critical", region="seoul")

        # 두 번째 호출은 캐시 사용 → _get_error_budget_percent 1번만 호출
        assert mock.call_count == 1
        assert result1.status == result2.status

    def test_different_tier_not_cached_together(self):
        """다른 tier_id 조합은 별도 캐시."""
        config = ErrorBudgetGateConfig(enabled=True, cache_ttl_seconds=300)
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, "_get_error_budget_percent", return_value=50.0) as mock:
            gate.check(force_refresh=True, tier_id="critical")
            gate.check(force_refresh=True, tier_id="standard")

        assert mock.call_count == 2


# =============================================================================
# 동작 검증: Fail-Open 시 tier/region 전파
# =============================================================================


class TestFailOpenPreservesTierRegionBehavior:
    """Fail-Open 시에도 tier_id/region이 결과에 포함됨."""

    def test_fail_open_preserves_tier_id(self):
        """예산 조회 실패(Fail-Open) 시 tier_id 유지."""
        config = ErrorBudgetGateConfig(enabled=True, fail_open=True)
        gate = ErrorBudgetGate(config=config)

        with patch.object(gate, "_get_error_budget_percent", return_value=None):
            result = gate.check(force_refresh=True, tier_id="critical", region="seoul")

        assert result.tier_id == "critical"
        assert result.region == "seoul"
        assert result.fail_open_triggered is True


# =============================================================================
# 동작 검증: 히스테리시스 + 티어 결합
# =============================================================================


class TestHysteresisWithTierBehavior:
    """히스테리시스 상태 전이가 티어별 임계치를 사용하는지 검증."""

    def test_blocked_recovery_uses_tier_threshold(self):
        """BLOCKED → WARNING 복구 시 티어별 critical 임계치 + buffer 사용."""
        config = ErrorBudgetGateConfig(
            enabled=True,
            tier_thresholds_enabled=True,
            threshold_hysteresis_buffer_percent=2.0,
        )
        gate = ErrorBudgetGate(config=config)
        gate._current_status = GateStatus.BLOCKED

        # critical 티어: critical_threshold=15.0, recovery=15+2=17%
        # 16%는 recovery(17%)보다 낮으므로 BLOCKED 유지
        with patch.object(gate, "_get_error_budget_percent", return_value=16.0):
            result = gate.check(force_refresh=True, tier_id="critical")

        assert result.status == GateStatus.BLOCKED

    def test_blocked_recovery_succeeds_above_tier_recovery(self):
        """BLOCKED → WARNING: 티어별 recovery 임계치(17%) 이상이면 복구."""
        config = ErrorBudgetGateConfig(
            enabled=True,
            tier_thresholds_enabled=True,
            threshold_hysteresis_buffer_percent=2.0,
        )
        gate = ErrorBudgetGate(config=config)
        gate._current_status = GateStatus.BLOCKED

        # critical 티어: critical_threshold=15.0, recovery=15+2=17%
        with patch.object(gate, "_get_error_budget_percent", return_value=17.0):
            result = gate.check(force_refresh=True, tier_id="critical")

        assert result.status == GateStatus.WARNING
