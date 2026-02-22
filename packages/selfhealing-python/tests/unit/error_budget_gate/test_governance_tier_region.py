"""
거버넌스 체크 티어/리전 전파 테스트.

is_error_budget_blocking(), check_all_governance()에
tier_id/region 파라미터가 올바르게 전파되는지 검증.
"""

from unittest.mock import patch

from selfhealing.services.error_budget_gate.config import (
    GateCheckResult,
    GateStatus,
)

# =============================================================================
# 동작 검증: is_error_budget_blocking() tier/region 전파
# =============================================================================


class TestIsErrorBudgetBlockingTierRegionBehavior:
    """is_error_budget_blocking()의 tier_id/region 전파 동작."""

    def test_tier_id_passed_to_check_automation_allowed(self):
        """tier_id가 check_automation_allowed에 전달됨."""
        mock_result = GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
            error_budget_percent=80.0,
            threshold_percent=10.0,
        )

        with patch(
            "selfhealing.services.error_budget_gate.check_automation_allowed",
            return_value=mock_result,
        ) as mock_check:
            with patch(
                "selfhealing.services.governance.checks._governance_cache",
            ) as mock_cache:
                mock_cache.get.return_value = None
                from selfhealing.services.governance.checks import (
                    is_error_budget_blocking,
                )

                is_error_budget_blocking(tier_id="critical", region="seoul")

        mock_check.assert_called_once_with(tier_id="critical", region="seoul")

    def test_returns_not_blocked_when_allowed(self):
        """허용 시 (False, budget, threshold) 반환."""
        mock_result = GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
            error_budget_percent=80.0,
            threshold_percent=10.0,
        )

        with patch(
            "selfhealing.services.error_budget_gate.check_automation_allowed",
            return_value=mock_result,
        ):
            with patch(
                "selfhealing.services.governance.checks._governance_cache",
            ) as mock_cache:
                mock_cache.get.return_value = None
                from selfhealing.services.governance.checks import (
                    is_error_budget_blocking,
                )

                is_blocked, budget, threshold = is_error_budget_blocking(tier_id="standard")

        assert is_blocked is False
        assert budget == 80.0

    def test_returns_blocked_when_not_allowed(self):
        """차단 시 (True, budget, threshold) 반환."""
        mock_result = GateCheckResult(
            allowed=False,
            status=GateStatus.BLOCKED,
            error_budget_percent=5.0,
            threshold_percent=10.0,
        )

        with patch(
            "selfhealing.services.error_budget_gate.check_automation_allowed",
            return_value=mock_result,
        ):
            with patch(
                "selfhealing.services.governance.checks._governance_cache",
            ) as mock_cache:
                mock_cache.get.return_value = None
                from selfhealing.services.governance.checks import (
                    is_error_budget_blocking,
                )

                is_blocked, budget, threshold = is_error_budget_blocking(tier_id="critical")

        assert is_blocked is True


# =============================================================================
# 동작 검증: check_all_governance() tier/region 전파
# =============================================================================


class TestCheckAllGovernanceTierRegionBehavior:
    """check_all_governance()의 tier_id/region 전파 동작."""

    def test_tier_id_and_region_propagated_to_error_budget_check(self):
        """tier_id와 region이 is_error_budget_blocking에 전파됨."""
        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ):
            with patch(
                "selfhealing.services.governance.checks.is_emergency_blocking",
                return_value=(False, "NONE"),
            ):
                with patch(
                    "selfhealing.services.governance.checks.is_error_budget_blocking",
                    return_value=(False, 80.0, 10.0),
                ) as mock_budget:
                    from selfhealing.services.governance.checks import (
                        check_all_governance,
                    )

                    check_all_governance(
                        tier_id="critical",
                        region="seoul",
                    )

        mock_budget.assert_called_once_with(tier_id="critical", region="seoul")

    def test_error_budget_blocked_returns_blocked_result(self):
        """Error Budget 차단 시 blocked 결과 반환."""
        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            return_value=True,
        ):
            with patch(
                "selfhealing.services.governance.checks.is_emergency_blocking",
                return_value=(False, "NONE"),
            ):
                with patch(
                    "selfhealing.services.governance.checks.is_error_budget_blocking",
                    return_value=(True, 5.0, 15.0),
                ):
                    with patch(
                        "selfhealing.services.governance.checks._log_governance_blocked",
                    ):
                        from selfhealing.services.governance.checks import (
                            check_all_governance,
                        )

                        result = check_all_governance(
                            tier_id="critical",
                            region="seoul",
                        )

        assert result.allowed is False
        assert result.block_reason == "error_budget"
