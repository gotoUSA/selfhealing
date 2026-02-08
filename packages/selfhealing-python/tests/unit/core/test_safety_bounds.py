"""
Tests for SafetyBounds.
core/safety_bounds.py의 파라미터 안전 한계 관리에 대한 단위 테스트.
is_within_bounds, clamp_to_bounds, update_bounds, check_all 등을 검증합니다.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.core.safety_bounds import SafetyBounds, ParameterBound


# =============================================================================
# ParameterBound Tests
# =============================================================================


class TestParameterBound:
    """ParameterBound 데이터클래스 테스트."""

    def test_valid_bound(self):
        """Valid bound validation
        유효한 한계 설정이 올바르게 검증되는지 확인.
        """
        bound = ParameterBound(min_value=0, max_value=100, max_change_per_cycle=0.3)
        assert bound.validate() is True

    def test_invalid_bound_min_greater_than_max(self):
        """Invalid bound: min > max
        min_value가 max_value보다 큰 경우 검증 실패 확인.
        """
        bound = ParameterBound(min_value=100, max_value=50, max_change_per_cycle=0.3)
        assert bound.validate() is False

    def test_invalid_bound_negative_change(self):
        """Invalid bound: negative max_change_per_cycle
        max_change_per_cycle이 0 이하일 때 검증 실패 확인.
        """
        bound = ParameterBound(min_value=0, max_value=100, max_change_per_cycle=-0.1)
        assert bound.validate() is False

    def test_invalid_bound_change_exceeds_one(self):
        """Invalid bound: max_change > 1
        max_change_per_cycle이 1을 초과할 때 검증 실패 확인.
        """
        bound = ParameterBound(min_value=0, max_value=100, max_change_per_cycle=1.5)
        assert bound.validate() is False

    def test_boundary_exact_one(self):
        """Boundary exact 1.0 for change
        max_change_per_cycle이 정확히 1.0일 때도 유효한지 확인.
        1.0은 100% 변경을 의미하며, > 1.0이 아니므로 유효합니다.
        """
        bound = ParameterBound(min_value=0, max_value=100, max_change_per_cycle=1.0)
        assert bound.validate() is True  # 1.0은 > 1.0이 아니므로 유효


# =============================================================================
# SafetyBounds Initialization Tests
# =============================================================================


class TestSafetyBoundsInit:
    """SafetyBounds 초기화 테스트."""

    @patch("selfhealing.core.safety_bounds.get_safety_bounds_settings")
    def test_default_parameters_created(self, mock_settings):
        """Default parameters created
        기본 8개 파라미터의 한계가 생성되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.timeout_ms_min = 100
        mock_s.timeout_ms_max = 10000
        mock_s.timeout_ms_max_change = 0.3
        mock_s.retry_count_min = 1
        mock_s.retry_count_max = 10
        mock_s.retry_count_max_change = 0.5
        mock_s.circuit_breaker_threshold_min = 0.1
        mock_s.circuit_breaker_threshold_max = 0.8
        mock_s.circuit_breaker_threshold_max_change = 0.2
        mock_s.jitter_range_min = 0.01
        mock_s.jitter_range_max = 1.0
        mock_s.jitter_range_max_change = 0.5
        mock_s.rate_limit_rps_min = 10
        mock_s.rate_limit_rps_max = 10000
        mock_s.rate_limit_rps_max_change = 0.3
        mock_s.backoff_base_ms_min = 100
        mock_s.backoff_base_ms_max = 10000
        mock_s.backoff_base_ms_max_change = 0.3
        mock_s.backoff_max_ms_min = 1000
        mock_s.backoff_max_ms_max = 600000
        mock_s.backoff_max_ms_max_change = 0.3
        mock_s.connection_pool_size_min = 1
        mock_s.connection_pool_size_max = 200
        mock_s.connection_pool_size_max_change = 0.3
        mock_settings.return_value = mock_s

        bounds = SafetyBounds()
        all_bounds = bounds.get_all_bounds()
        assert len(all_bounds) == 8
        assert "timeout_ms" in all_bounds
        assert "retry_count" in all_bounds
        assert "circuit_breaker_threshold" in all_bounds

    @patch("selfhealing.core.safety_bounds.get_safety_bounds_settings")
    def test_custom_bounds_applied(self, mock_settings):
        """Custom bounds applied
        커스텀 한계가 올바르게 적용되는지 확인.
        """
        mock_s = MagicMock()
        mock_s.timeout_ms_min = 100
        mock_s.timeout_ms_max = 10000
        mock_s.timeout_ms_max_change = 0.3
        mock_s.retry_count_min = 1
        mock_s.retry_count_max = 10
        mock_s.retry_count_max_change = 0.5
        mock_s.circuit_breaker_threshold_min = 0.1
        mock_s.circuit_breaker_threshold_max = 0.8
        mock_s.circuit_breaker_threshold_max_change = 0.2
        mock_s.jitter_range_min = 0.01
        mock_s.jitter_range_max = 1.0
        mock_s.jitter_range_max_change = 0.5
        mock_s.rate_limit_rps_min = 10
        mock_s.rate_limit_rps_max = 10000
        mock_s.rate_limit_rps_max_change = 0.3
        mock_s.backoff_base_ms_min = 100
        mock_s.backoff_base_ms_max = 10000
        mock_s.backoff_base_ms_max_change = 0.3
        mock_s.backoff_max_ms_min = 1000
        mock_s.backoff_max_ms_max = 600000
        mock_s.backoff_max_ms_max_change = 0.3
        mock_s.connection_pool_size_min = 1
        mock_s.connection_pool_size_max = 200
        mock_s.connection_pool_size_max_change = 0.3
        mock_settings.return_value = mock_s

        bounds = SafetyBounds(
            custom_bounds={
                "custom_param": {
                    "min_value": 0,
                    "max_value": 50,
                    "max_change_per_cycle": 0.2,
                }
            }
        )
        b = bounds.get_bounds("custom_param")
        assert b is not None
        assert b["max_value"] == 50


# =============================================================================
# is_within_bounds Tests
# =============================================================================


class TestIsWithinBounds:
    """is_within_bounds 메서드 테스트."""

    @pytest.fixture(autouse=True)
    def setup_bounds(self):
        """테스트용 SafetyBounds 인스턴스 생성 (직접 bounds 설정)."""
        with patch("selfhealing.core.safety_bounds.get_safety_bounds_settings"):
            self.bounds = SafetyBounds.__new__(SafetyBounds)
            from threading import RLock

            self.bounds._lock = RLock()
            self.bounds.strict_mode = True
            self.bounds.bounds = {
                "timeout_ms": ParameterBound(min_value=100, max_value=10000, max_change_per_cycle=0.3),
                "retry_count": ParameterBound(min_value=1, max_value=10, max_change_per_cycle=0.5),
            }

    def test_value_within_range(self):
        """Value within range
        범위 내의 값이 True를 반환하는지 확인.
        """
        assert self.bounds.is_within_bounds("timeout_ms", 500) is True

    def test_value_below_minimum(self):
        """Value below minimum
        최솟값 미만의 값이 False를 반환하는지 확인.
        """
        assert self.bounds.is_within_bounds("timeout_ms", 50) is False

    def test_value_above_maximum(self):
        """Value above maximum
        최댓값 초과의 값이 False를 반환하는지 확인.
        """
        assert self.bounds.is_within_bounds("timeout_ms", 20000) is False

    def test_value_at_boundary(self):
        """Value at exact boundary
        정확히 경계값에서도 True를 반환하는지 확인.
        """
        assert self.bounds.is_within_bounds("timeout_ms", 100) is True
        assert self.bounds.is_within_bounds("timeout_ms", 10000) is True

    def test_change_ratio_within_limit(self):
        """Change ratio within limit
        변경 비율이 허용 범위 내일 때 True를 반환하는지 확인.
        """
        # current=1000, new=1200, change_ratio=0.2 < 0.3
        assert self.bounds.is_within_bounds("timeout_ms", 1200, current_value=1000) is True

    def test_change_ratio_exceeds_limit(self):
        """Change ratio exceeds limit
        변경 비율이 허용 범위를 초과할 때 False를 반환하는지 확인.
        """
        # current=1000, new=2000, change_ratio=1.0 > 0.3
        assert self.bounds.is_within_bounds("timeout_ms", 2000, current_value=1000) is False

    def test_unknown_parameter_strict_mode(self):
        """Unknown parameter in strict mode
        strict_mode에서 알 수 없는 파라미터가 False를 반환하는지 확인.
        """
        assert self.bounds.is_within_bounds("unknown_param", 100) is False

    def test_unknown_parameter_non_strict_mode(self):
        """Unknown parameter in non-strict mode
        non-strict 모드에서 알 수 없는 파라미터가 True를 반환하는지 확인.
        """
        self.bounds.strict_mode = False
        assert self.bounds.is_within_bounds("unknown_param", 100) is True


# =============================================================================
# clamp_to_bounds Tests
# =============================================================================


class TestClampToBounds:
    """clamp_to_bounds 메서드 테스트."""

    @pytest.fixture(autouse=True)
    def setup_bounds(self):
        """테스트용 SafetyBounds 인스턴스 생성."""
        with patch("selfhealing.core.safety_bounds.get_safety_bounds_settings"):
            self.bounds = SafetyBounds.__new__(SafetyBounds)
            from threading import RLock

            self.bounds._lock = RLock()
            self.bounds.strict_mode = True
            self.bounds.bounds = {
                "timeout_ms": ParameterBound(min_value=100, max_value=10000, max_change_per_cycle=0.3),
            }

    def test_clamp_below_minimum(self):
        """Clamp below minimum
        최솟값 미만의 값이 최솟값으로 조정되는지 확인.
        """
        result = self.bounds.clamp_to_bounds("timeout_ms", 50)
        assert result == 100

    def test_clamp_above_maximum(self):
        """Clamp above maximum
        최댓값 초과의 값이 최댓값으로 조정되는지 확인.
        """
        result = self.bounds.clamp_to_bounds("timeout_ms", 20000)
        assert result == 10000

    def test_clamp_within_range(self):
        """Clamp within range
        범위 내의 값은 변경되지 않는지 확인.
        """
        result = self.bounds.clamp_to_bounds("timeout_ms", 5000)
        assert result == 5000

    def test_clamp_with_change_limit(self):
        """Clamp with change limit
        변경폭이 제한되는지 확인. current_value 기반으로 max_change 적용.
        """
        # current=1000, value=2000 → change=1000, max_change=1000*0.3=300
        # clamped = 1000 + 300 = 1300
        result = self.bounds.clamp_to_bounds("timeout_ms", 2000, current_value=1000)
        assert result == 1300

    def test_clamp_decrease_with_change_limit(self):
        """Clamp decrease with change limit
        감소 방향의 변경폭이 제한되는지 확인.
        """
        # current=1000, value=200 → decrease, max_change=1000*0.3=300
        # clamped = max(100, 1000 - 300) = 700
        result = self.bounds.clamp_to_bounds("timeout_ms", 200, current_value=1000)
        assert result == 700

    def test_clamp_unknown_parameter(self):
        """Clamp unknown parameter
        알 수 없는 파라미터의 값은 변경되지 않는지 확인.
        """
        result = self.bounds.clamp_to_bounds("unknown_param", 999)
        assert result == 999


# =============================================================================
# Update/Remove Bounds Tests
# =============================================================================


class TestUpdateBounds:
    """update_bounds, remove_bounds 메서드 테스트."""

    @pytest.fixture(autouse=True)
    def setup_bounds(self):
        """테스트용 SafetyBounds 인스턴스 생성."""
        with patch("selfhealing.core.safety_bounds.get_safety_bounds_settings"):
            self.bounds = SafetyBounds.__new__(SafetyBounds)
            from threading import RLock

            self.bounds._lock = RLock()
            self.bounds.strict_mode = True
            self.bounds.bounds = {}

    def test_update_bounds(self):
        """Update bounds
        한계 업데이트가 올바르게 동작하는지 확인.
        """
        result = self.bounds.update_bounds(
            "new_param",
            {
                "min_value": 10,
                "max_value": 100,
                "max_change_per_cycle": 0.3,
            },
        )
        assert result is True
        b = self.bounds.get_bounds("new_param")
        assert b["min_value"] == 10
        assert b["max_value"] == 100

    def test_update_bounds_invalid(self):
        """Update bounds with invalid config
        유효하지 않은 설정으로 업데이트 시 False를 반환하는지 확인.
        """
        result = self.bounds.update_bounds(
            "bad_param",
            {
                "min_value": 100,
                "max_value": 10,  # min > max → invalid
                "max_change_per_cycle": 0.3,
            },
        )
        assert result is False

    def test_remove_bounds(self):
        """Remove bounds
        한계 제거가 올바르게 동작하는지 확인.
        """
        self.bounds.update_bounds(
            "to_remove",
            {
                "min_value": 0,
                "max_value": 100,
                "max_change_per_cycle": 0.3,
            },
        )
        result = self.bounds.remove_bounds("to_remove")
        assert result is True
        assert self.bounds.get_bounds("to_remove") is None

    def test_remove_nonexistent_bounds(self):
        """Remove nonexistent bounds
        존재하지 않는 한계 제거 시 False를 반환하는지 확인.
        """
        result = self.bounds.remove_bounds("nonexistent")
        assert result is False


# =============================================================================
# check_all Tests
# =============================================================================


class TestCheckAll:
    """check_all 메서드 테스트."""

    @pytest.fixture(autouse=True)
    def setup_bounds(self):
        """테스트용 SafetyBounds 인스턴스 생성."""
        with patch("selfhealing.core.safety_bounds.get_safety_bounds_settings"):
            self.bounds = SafetyBounds.__new__(SafetyBounds)
            from threading import RLock

            self.bounds._lock = RLock()
            self.bounds.strict_mode = True
            self.bounds.bounds = {
                "timeout_ms": ParameterBound(min_value=100, max_value=10000, max_change_per_cycle=0.3),
                "retry_count": ParameterBound(min_value=1, max_value=10, max_change_per_cycle=0.5),
            }

    def test_check_all_valid(self):
        """Check all valid values
        모든 값이 유효할 때 결과가 올바른지 확인.
        """
        result = self.bounds.check_all({"timeout_ms": 500, "retry_count": 3})
        assert result["timeout_ms"] is True
        assert result["retry_count"] is True

    def test_check_all_mixed(self):
        """Check all mixed values
        일부 유효, 일부 무효일 때 결과가 올바른지 확인.
        """
        result = self.bounds.check_all({"timeout_ms": 50, "retry_count": 3})
        assert result["timeout_ms"] is False
        assert result["retry_count"] is True
