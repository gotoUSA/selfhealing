"""
ErrorBudgetCalculator exclude_synthetic 테스트.

exclude_synthetic 파라미터와 exclude_chaos deprecated 처리를 검증합니다.
"""

import warnings
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
from selfhealing.slo import SLOConfig


class TestExcludeSyntheticParameter:
    """exclude_synthetic 파라미터 테스트."""

    def test_exclude_synthetic_default_is_true(self):
        """exclude_synthetic 기본값은 True."""
        called_params = {}

        def mock_stats(**kwargs):
            called_params.update(kwargs)
            return {"total_errors": 10}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=mock_stats)

        calculator.calculate_budget_status()

        # exclude_synthetic=True가 전달되어야 함
        assert called_params.get("exclude_synthetic") is True

    def test_exclude_synthetic_false_passes_to_stats(self):
        """exclude_synthetic=False가 stats 함수에 전달됨."""
        called_params = {}

        def mock_stats(**kwargs):
            called_params.update(kwargs)
            return {"total_errors": 10}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=mock_stats)

        calculator.calculate_budget_status(exclude_synthetic=False)

        assert called_params.get("exclude_synthetic") is False


class TestExcludeChaosDeprecation:
    """exclude_chaos deprecated 경고 테스트."""

    def test_exclude_chaos_emits_deprecation_warning(self):
        """exclude_chaos 사용 시 DeprecationWarning 발생."""

        def mock_stats(**kwargs):
            return {"total_errors": 5}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=mock_stats)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            calculator.calculate_budget_status(exclude_chaos=True)

            # DeprecationWarning이 발생해야 함
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "exclude_chaos is deprecated" in str(w[0].message)
            assert "exclude_synthetic" in str(w[0].message)

    def test_exclude_chaos_value_used_as_exclude_synthetic(self):
        """exclude_chaos 값이 exclude_synthetic으로 사용됨."""
        called_params = {}

        def mock_stats(**kwargs):
            called_params.update(kwargs)
            return {"total_errors": 3}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=mock_stats)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            calculator.calculate_budget_status(exclude_chaos=False)

        # exclude_chaos=False가 exclude_synthetic=False로 해석됨
        # 하위 호환성을 위해 exclude_chaos로 시도
        assert called_params.get("exclude_synthetic") is False or called_params.get("exclude_chaos") is False


class TestBackwardCompatibility:
    """하위 호환성 테스트."""

    def test_old_stats_function_without_exclude_params(self):
        """파라미터가 없는 이전 버전 stats 함수와 호환."""

        def old_style_stats(start_time, end_time):
            return {"total_errors": 7}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=old_style_stats)

        # TypeError 없이 실행되어야 함
        result = calculator.calculate_budget_status()

        assert result is not None
        assert result.error_count_window == 7

    def test_stats_function_with_only_exclude_chaos(self):
        """exclude_chaos만 있는 stats 함수와 호환."""

        def chaos_only_stats(start_time, end_time, exclude_chaos=True):
            return {"total_errors": 5 if exclude_chaos else 10}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=chaos_only_stats)

        # TypeError 없이 실행되어야 함
        result = calculator.calculate_budget_status()

        assert result is not None


class TestErrorBudgetCalculation:
    """에러 버짓 계산 테스트."""

    def test_error_budget_exclude_synthetic(self):
        """
        문서 137 섹션 5.1 명시 테스트: 합성 에러 버짓 제외.

        exclude_synthetic=True 시 합성 요청의 에러가 에러 버짓 계산에서
        제외되는지 검증합니다.
        """
        called_params = {}

        def mock_stats(start_time, end_time, exclude_synthetic=True):
            called_params["exclude_synthetic"] = exclude_synthetic
            return {"total_errors": 5}

        calculator = ErrorBudgetCalculator(get_failed_operation_stats=mock_stats)

        # exclude_synthetic=True (기본값)로 호출
        calculator.calculate_budget_status(exclude_synthetic=True)

        # stats 함수가 exclude_synthetic=True로 호출됨
        assert called_params.get("exclude_synthetic") is True

    def test_synthetic_errors_excluded_from_budget(self):
        """합성 에러가 버짓 계산에서 제외됨."""

        # exclude_synthetic=True일 때 에러 5개
        # exclude_synthetic=False일 때 에러 15개 (합성 10개 포함)
        def mock_stats(start_time, end_time, exclude_synthetic=True):
            if exclude_synthetic:
                return {"total_errors": 5}  # 실제 에러만
            else:
                return {"total_errors": 15}  # 합성 포함

        def mock_request_stats(start_time, end_time):
            return {"total_requests": 10000}

        calculator = ErrorBudgetCalculator(
            get_failed_operation_stats=mock_stats,
            get_request_stats=mock_request_stats,
        )

        # 합성 제외 (기본값)
        result_excluded = calculator.calculate_budget_status(exclude_synthetic=True)

        # 합성 포함
        result_included = calculator.calculate_budget_status(exclude_synthetic=False)

        # 합성 제외 시 에러가 적음
        assert result_excluded.error_count_window == 5
        assert result_included.error_count_window == 15

        # 따라서 합성 제외 시 버짓 소진량이 더 적음
        assert result_excluded.budget_remaining_percent > result_included.budget_remaining_percent
