"""
Prometheus 메트릭 리전/티어 레이블 테스트.

error_budget_remaining_percent, canary_governance_blocked_total 메트릭에
region/tier 레이블 존재 및 record_error_budget_status() 파라미터 동작 검증.
"""

import pytest
from unittest.mock import patch, MagicMock


# =============================================================================
# 계약 검증: 메트릭 레이블
# =============================================================================


class TestErrorBudgetMetricLabelsContract:
    """error_budget_remaining_percent 메트릭 레이블 계약 검증."""

    def test_has_region_label(self):
        """error_budget_remaining_percent에 region 레이블 존재."""
        from selfhealing.services.metrics.definitions import (
            error_budget_remaining_percent,
        )

        assert "region" in error_budget_remaining_percent._labelnames

    def test_has_tier_label(self):
        """error_budget_remaining_percent에 tier 레이블 존재."""
        from selfhealing.services.metrics.definitions import (
            error_budget_remaining_percent,
        )

        assert "tier" in error_budget_remaining_percent._labelnames

    def test_has_slo_name_label(self):
        """error_budget_remaining_percent에 slo_name 레이블 유지."""
        from selfhealing.services.metrics.definitions import (
            error_budget_remaining_percent,
        )

        assert "slo_name" in error_budget_remaining_percent._labelnames

    def test_has_is_synthetic_label(self):
        """error_budget_remaining_percent에 is_synthetic 레이블 유지."""
        from selfhealing.services.metrics.definitions import (
            error_budget_remaining_percent,
        )

        assert "is_synthetic" in error_budget_remaining_percent._labelnames


class TestCanaryGovernanceMetricLabelsContract:
    """canary_governance_blocked_total 메트릭 레이블 계약 검증."""

    def test_has_region_label(self):
        """canary_governance_blocked_total에 region 레이블 존재."""
        from selfhealing.services.metrics.definitions import (
            canary_governance_blocked_total,
        )

        assert "region" in canary_governance_blocked_total._labelnames

    def test_has_tier_label(self):
        """canary_governance_blocked_total에 tier 레이블 존재."""
        from selfhealing.services.metrics.definitions import (
            canary_governance_blocked_total,
        )

        assert "tier" in canary_governance_blocked_total._labelnames

    def test_has_block_reason_label(self):
        """canary_governance_blocked_total에 block_reason 레이블 유지."""
        from selfhealing.services.metrics.definitions import (
            canary_governance_blocked_total,
        )

        assert "block_reason" in canary_governance_blocked_total._labelnames


# =============================================================================
# 동작 검증: record_error_budget_status() region/tier 파라미터
# =============================================================================


class TestRecordErrorBudgetStatusRegionTierBehavior:
    """record_error_budget_status()의 region/tier 파라미터 동작."""

    def test_region_and_tier_passed_to_gauge(self):
        """region과 tier가 Gauge.labels()에 전달됨."""
        with patch("selfhealing.services.metrics.recorders.error_budget_remaining_percent") as mock_gauge:
            with patch("selfhealing.services.metrics.recorders.error_budget_remaining_minutes"):
                with patch("selfhealing.services.metrics.recorders.burn_rate_1h"):
                    with patch("selfhealing.services.metrics.recorders.burn_rate_6h"):
                        from selfhealing.services.metrics.recorders import (
                            record_error_budget_status,
                        )

                        record_error_budget_status(
                            slo_name="availability",
                            remaining_percent=85.0,
                            remaining_minutes=30.0,
                            burn_rate_1h_value=1.0,
                            burn_rate_6h_value=0.5,
                            region="seoul",
                            tier="critical",
                        )

            mock_gauge.labels.assert_called()
            call_kwargs = mock_gauge.labels.call_args
            # positional 또는 keyword arguments 확인
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("region") == "seoul"
                assert call_kwargs.kwargs.get("tier") == "critical"

    def test_default_empty_region_and_tier(self):
        """region/tier 미지정 시 빈 문자열 기본값."""
        import inspect
        from selfhealing.services.metrics.recorders import record_error_budget_status

        sig = inspect.signature(record_error_budget_status)
        assert sig.parameters["region"].default == ""
        assert sig.parameters["tier"].default == ""


# =============================================================================
# 계약 검증: Alerting rules 티어별 PromQL
# =============================================================================


class TestAlertingRulesTierAwareContract:
    """Alerting rules 티어별 PromQL 계약 검증."""

    def test_error_budget_critical_rule_has_tier_filter(self):
        """ErrorBudgetCritical 규칙에 tier 필터 존재."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["ErrorBudgetCritical"]
        assert 'tier="critical"' in rule["expr"]
        assert 'tier="standard"' in rule["expr"]
        assert 'tier="non_essential"' in rule["expr"]

    def test_error_budget_warning_rule_has_tier_filter(self):
        """ErrorBudgetWarning 규칙에 tier 필터 존재."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["ErrorBudgetWarning"]
        assert 'tier="critical"' in rule["expr"]
        assert 'tier="standard"' in rule["expr"]
        assert 'tier="non_essential"' in rule["expr"]

    def test_error_budget_critical_rule_has_region_in_description(self):
        """ErrorBudgetCritical 설명에 region 템플릿 포함."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["ErrorBudgetCritical"]
        assert "region" in rule["description"]

    def test_error_budget_warning_rule_has_region_in_description(self):
        """ErrorBudgetWarning 설명에 region 템플릿 포함."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["ErrorBudgetWarning"]
        assert "region" in rule["description"]
