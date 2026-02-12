"""
ErrorBudgetCalculator 리전 파라미터 전달 테스트.

calculate_budget_status()에 region 전달 시
콜백에 전파되고 결과 ErrorBudgetStatus.region에 반영되는지 검증.
"""

import pytest
from datetime import datetime, timedelta

from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
from selfhealing.services.error_budget.models import ErrorBudgetStatus


# =============================================================================
# 동작 검증: region 파라미터 전달
# =============================================================================


class TestCalculatorRegionPassthroughBehavior:
    """Calculator가 region을 콜백과 결과에 전달하는지 검증."""

    def test_region_passed_to_failed_operation_stats_callback(self):
        """region이 _get_failed_operation_stats 콜백에 전달됨."""
        captured_kwargs = {}

        def mock_stats(**kwargs):
            captured_kwargs.update(kwargs)
            return {"total_errors": 0}

        calc = ErrorBudgetCalculator(
            get_failed_operation_stats=mock_stats,
        )
        calc.calculate_budget_status(region="seoul")

        assert captured_kwargs.get("region") == "seoul"

    def test_region_none_passed_to_callback(self):
        """region=None도 콜백에 전달됨."""
        captured_kwargs = {}

        def mock_stats(**kwargs):
            captured_kwargs.update(kwargs)
            return {"total_errors": 0}

        calc = ErrorBudgetCalculator(
            get_failed_operation_stats=mock_stats,
        )
        calc.calculate_budget_status(region=None)

        assert "region" in captured_kwargs
        assert captured_kwargs["region"] is None

    def test_result_has_region_field(self):
        """결과 ErrorBudgetStatus에 region 필드 반영."""
        calc = ErrorBudgetCalculator()
        status = calc.calculate_budget_status(region="tokyo")

        assert status.region == "tokyo"

    def test_result_region_none_by_default(self):
        """region 미지정 시 결과의 region은 None."""
        calc = ErrorBudgetCalculator()
        status = calc.calculate_budget_status()

        assert status.region is None

    def test_callback_fallback_when_region_not_supported(self):
        """콜백이 region 미지원 시 TypeError fallback 동작."""
        call_count = {"count": 0}

        def legacy_callback(start_time, end_time, exclude_synthetic=True):
            """region 파라미터가 없는 레거시 콜백."""
            call_count["count"] += 1
            return {"total_errors": 5}

        calc = ErrorBudgetCalculator(
            get_failed_operation_stats=legacy_callback,
        )
        status = calc.calculate_budget_status(region="seoul")

        # TypeError fallback으로 레거시 콜백 호출 성공
        assert call_count["count"] >= 1
        # 결과의 region은 그대로 반영
        assert status.region == "seoul"
