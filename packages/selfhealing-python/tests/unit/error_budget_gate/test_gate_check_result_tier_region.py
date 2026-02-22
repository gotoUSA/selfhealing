"""
GateCheckResult 티어/리전 필드 테스트.

GateCheckResult.tier_id, region 필드 존재 및 to_dict() 직렬화 동작 검증.
"""


from selfhealing.services.error_budget_gate.config import (
    GateCheckResult,
    GateStatus,
)

# =============================================================================
# 계약 검증: 필드 존재
# =============================================================================


class TestGateCheckResultTierRegionFieldsContract:
    """GateCheckResult에 tier_id/region 필드가 존재하는지 계약 검증."""

    def test_tier_id_field_exists_default_none(self):
        """tier_id 필드 존재, 기본값 None."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN)
        assert result.tier_id is None

    def test_region_field_exists_default_none(self):
        """region 필드 존재, 기본값 None."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN)
        assert result.region is None


# =============================================================================
# 동작 검증: 필드 설정 및 직렬화
# =============================================================================


class TestGateCheckResultTierRegionBehavior:
    """GateCheckResult tier_id/region 동작 검증."""

    def test_tier_id_settable(self):
        """tier_id 값 설정 가능."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN, tier_id="critical")
        assert result.tier_id == "critical"

    def test_region_settable(self):
        """region 값 설정 가능."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN, region="seoul")
        assert result.region == "seoul"

    def test_to_dict_includes_tier_when_set(self):
        """to_dict()에 tier_id가 None이 아닐 때만 포함."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN, tier_id="critical")
        d = result.to_dict()
        assert d["tier_id"] == "critical"

    def test_to_dict_includes_region_when_set(self):
        """to_dict()에 region이 None이 아닐 때만 포함."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN, region="seoul")
        d = result.to_dict()
        assert d["region"] == "seoul"

    def test_to_dict_excludes_tier_when_none(self):
        """to_dict()에 tier_id=None이면 키 미포함."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN)
        d = result.to_dict()
        assert "tier_id" not in d

    def test_to_dict_excludes_region_when_none(self):
        """to_dict()에 region=None이면 키 미포함."""
        result = GateCheckResult(allowed=True, status=GateStatus.OPEN)
        d = result.to_dict()
        assert "region" not in d
