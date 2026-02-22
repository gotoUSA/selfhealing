"""
리전-티어 매핑 해석기 테스트.

RegionalRecoveryConfig.priority 기반 리전 → 티어 추론 동작 검증.
"""

from unittest.mock import MagicMock, patch

from selfhealing.services.error_budget_gate.region_tier_resolver import (
    _DEFAULT_TIER,
    _PRIORITY_TIER_RANGES,
    resolve_tier_from_region,
)

# =============================================================================
# 계약 검증: 기본값 및 매핑 범위
# =============================================================================


class TestRegionTierResolverConstantsContract:
    """리전-티어 해석기 상수 계약 검증."""

    def test_default_tier_is_standard(self):
        """기본 폴백 티어는 standard."""
        assert _DEFAULT_TIER == "standard"

    def test_priority_ranges_cover_three_tiers(self):
        """priority 범위가 critical/standard/non_essential 3개 티어 포괄."""
        tiers = {tier for _, tier in _PRIORITY_TIER_RANGES}
        assert tiers == {"critical", "standard", "non_essential"}

    def test_critical_threshold_is_70(self):
        """critical 티어 진입 priority >= 70."""
        critical_range = next((min_p, tier) for min_p, tier in _PRIORITY_TIER_RANGES if tier == "critical")
        assert critical_range[0] == 70

    def test_standard_threshold_is_30(self):
        """standard 티어 진입 priority >= 30."""
        standard_range = next((min_p, tier) for min_p, tier in _PRIORITY_TIER_RANGES if tier == "standard")
        assert standard_range[0] == 30

    def test_non_essential_threshold_is_0(self):
        """non_essential 티어 진입 priority >= 0."""
        non_essential_range = next((min_p, tier) for min_p, tier in _PRIORITY_TIER_RANGES if tier == "non_essential")
        assert non_essential_range[0] == 0


# =============================================================================
# 동작 검증: resolve_tier_from_region()
# =============================================================================


class TestResolveTierFromRegionBehavior:
    """resolve_tier_from_region() 동작 검증."""

    def _mock_configs(self, configs: dict):
        """get_default_regional_configs를 mock으로 교체."""
        return patch(
            "selfhealing.services.coordination.regional_recovery_policy" ".get_default_regional_configs",
            return_value=configs,
        )

    def test_high_priority_region_resolves_to_critical(self):
        """priority=100 리전은 critical 티어."""
        mock_config = MagicMock()
        mock_config.priority = 100

        with self._mock_configs({"seoul": mock_config}):
            tier = resolve_tier_from_region("seoul")

        assert tier == "critical"

    def test_medium_priority_region_resolves_to_standard(self):
        """priority=50 리전은 standard 티어."""
        mock_config = MagicMock()
        mock_config.priority = 50

        with self._mock_configs({"tokyo": mock_config}):
            tier = resolve_tier_from_region("tokyo")

        assert tier == "standard"

    def test_low_priority_region_resolves_to_non_essential(self):
        """priority=10 리전은 non_essential 티어."""
        mock_config = MagicMock()
        mock_config.priority = 10

        with self._mock_configs({"oregon": mock_config}):
            tier = resolve_tier_from_region("oregon")

        assert tier == "non_essential"

    def test_unknown_region_returns_default_tier(self):
        """등록되지 않은 리전은 기본 티어(standard) 반환."""
        with self._mock_configs({}):
            tier = resolve_tier_from_region("unknown_region")

        assert tier == _DEFAULT_TIER

    def test_import_error_returns_default_tier(self):
        """모듈 import 실패 시 기본 티어 반환."""
        with patch(
            "selfhealing.services.coordination.regional_recovery_policy" ".get_default_regional_configs",
            side_effect=ImportError("no module"),
        ):
            tier = resolve_tier_from_region("seoul")

        assert tier == _DEFAULT_TIER

    def test_priority_boundary_70_is_critical(self):
        """priority=70 경계값은 critical 티어."""
        mock_config = MagicMock()
        mock_config.priority = 70

        with self._mock_configs({"boundary": mock_config}):
            tier = resolve_tier_from_region("boundary")

        assert tier == "critical"

    def test_priority_boundary_30_is_standard(self):
        """priority=30 경계값은 standard 티어."""
        mock_config = MagicMock()
        mock_config.priority = 30

        with self._mock_configs({"boundary": mock_config}):
            tier = resolve_tier_from_region("boundary")

        assert tier == "standard"

    def test_priority_boundary_29_is_non_essential(self):
        """priority=29는 non_essential 티어."""
        mock_config = MagicMock()
        mock_config.priority = 29

        with self._mock_configs({"boundary": mock_config}):
            tier = resolve_tier_from_region("boundary")

        assert tier == "non_essential"
