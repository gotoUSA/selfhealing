"""
ErrorBudgetService 리전 지원 테스트.

get_budget_status()의 region 파라미터 전달,
ClusterIdentity 자동 해석, get_all_region_statuses() 동작 검증.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.services.error_budget.service import ErrorBudgetService
from selfhealing.services.error_budget.models import ErrorBudgetStatus


# =============================================================================
# 동작 검증: get_budget_status() region 전달
# =============================================================================


class TestErrorBudgetServiceRegionBehavior:
    """ErrorBudgetService.get_budget_status() 리전 동작 검증."""

    def test_explicit_region_passed_to_calculator(self):
        """명시적 region이 calculator.calculate_budget_status에 전달됨."""
        service = ErrorBudgetService()

        with patch.object(
            service.calculator,
            "calculate_budget_status",
            wraps=service.calculator.calculate_budget_status,
        ) as mock_calc:
            service.get_budget_status(region="seoul")

        mock_calc.assert_called_once()
        _, kwargs = mock_calc.call_args
        assert kwargs.get("region") == "seoul"

    def test_none_region_resolves_to_cluster_identity(self):
        """region=None이면 ClusterIdentity.region으로 자동 해석."""
        mock_identity = MagicMock()
        mock_identity.region = "tokyo"

        service = ErrorBudgetService()

        with patch(
            "selfhealing.core.cluster_identity.get_cluster_identity",
            return_value=mock_identity,
        ):
            with patch.object(
                service.calculator,
                "calculate_budget_status",
                wraps=service.calculator.calculate_budget_status,
            ) as mock_calc:
                service.get_budget_status(region=None)

        _, kwargs = mock_calc.call_args
        assert kwargs.get("region") == "tokyo"

    def test_cluster_identity_failure_falls_back_to_none(self):
        """ClusterIdentity 조회 실패 시 region=None으로 fallback."""
        service = ErrorBudgetService()

        with patch(
            "selfhealing.core.cluster_identity.get_cluster_identity",
            side_effect=ImportError("no module"),
        ):
            with patch.object(
                service.calculator,
                "calculate_budget_status",
                wraps=service.calculator.calculate_budget_status,
            ) as mock_calc:
                service.get_budget_status(region=None)

        _, kwargs = mock_calc.call_args
        assert kwargs.get("region") is None


# =============================================================================
# 동작 검증: get_all_region_statuses()
# =============================================================================


class TestGetAllRegionStatusesBehavior:
    """get_all_region_statuses() 동작 검증."""

    def test_returns_dict_per_region(self):
        """regions 목록 각각에 대해 ErrorBudgetStatus 반환."""
        service = ErrorBudgetService()
        results = service.get_all_region_statuses(regions=["seoul", "tokyo"])

        assert "seoul" in results
        assert "tokyo" in results
        assert isinstance(results["seoul"], ErrorBudgetStatus)
        assert isinstance(results["tokyo"], ErrorBudgetStatus)

    def test_each_status_has_correct_region(self):
        """각 결과의 region 필드가 해당 리전과 일치."""
        service = ErrorBudgetService()
        results = service.get_all_region_statuses(regions=["seoul", "tokyo"])

        assert results["seoul"].region == "seoul"
        assert results["tokyo"].region == "tokyo"

    def test_empty_regions_returns_empty_dict(self):
        """빈 regions 목록이면 빈 딕셔너리 반환."""
        service = ErrorBudgetService()
        results = service.get_all_region_statuses(regions=[])
        assert results == {}

    def test_none_regions_uses_cluster_identity(self):
        """regions=None이면 ClusterIdentity.region 사용."""
        mock_identity = MagicMock()
        mock_identity.region = "seoul"

        service = ErrorBudgetService()

        with patch(
            "selfhealing.core.cluster_identity.get_cluster_identity",
            return_value=mock_identity,
        ):
            results = service.get_all_region_statuses(regions=None)

        assert "seoul" in results
