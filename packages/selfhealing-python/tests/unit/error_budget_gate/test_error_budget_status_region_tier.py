"""
ErrorBudgetStatus 리전/티어 필드 테스트.

ErrorBudgetStatus.region, tier_id 필드 존재 및 기본값 검증.
"""


from selfhealing.services.error_budget.models import ErrorBudgetStatus

# =============================================================================
# 계약 검증: 필드 존재
# =============================================================================


class TestErrorBudgetStatusRegionTierFieldsContract:
    """ErrorBudgetStatus에 region/tier_id 필드 존재 계약 검증."""

    def _make_status(self, **kwargs) -> ErrorBudgetStatus:
        """최소 필수 필드로 ErrorBudgetStatus 생성."""
        defaults = dict(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=4.32,
            budget_remaining_minutes=38.88,
            budget_remaining_percent=90.0,
        )
        defaults.update(kwargs)
        return ErrorBudgetStatus(**defaults)

    def test_region_field_default_none(self):
        """region 필드 기본값 None."""
        status = self._make_status()
        assert status.region is None

    def test_tier_id_field_default_none(self):
        """tier_id 필드 기본값 None."""
        status = self._make_status()
        assert status.tier_id is None


# =============================================================================
# 동작 검증: 필드 설정
# =============================================================================


class TestErrorBudgetStatusRegionTierBehavior:
    """ErrorBudgetStatus region/tier_id 동작 검증."""

    def _make_status(self, **kwargs) -> ErrorBudgetStatus:
        defaults = dict(
            slo_name="availability",
            slo_target=0.999,
            window_days=30,
            budget_total_minutes=43.2,
            budget_consumed_minutes=4.32,
            budget_remaining_minutes=38.88,
            budget_remaining_percent=90.0,
        )
        defaults.update(kwargs)
        return ErrorBudgetStatus(**defaults)

    def test_region_settable(self):
        """region 값 설정 가능."""
        status = self._make_status(region="seoul")
        assert status.region == "seoul"

    def test_tier_id_settable(self):
        """tier_id 값 설정 가능."""
        status = self._make_status(tier_id="critical")
        assert status.tier_id == "critical"

    def test_existing_properties_unaffected(self):
        """region/tier_id 추가가 기존 프로퍼티에 영향 없음."""
        status = self._make_status(
            budget_remaining_percent=90.0,
            region="seoul",
            tier_id="critical",
        )
        assert status.is_healthy is True
        assert status.is_over_budget is False
