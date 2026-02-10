"""
SafetyBounds SLA 파라미터 한계값 단위 테스트.

throttle_sla_warning_ms, throttle_sla_critical_ms가
SafetyBoundsSettings와 SafetyBounds에 올바르게 등록되었는지 검증한다.
"""

from __future__ import annotations

import pytest

from selfhealing.core.safety_bounds import ParameterBound, SafetyBounds
from selfhealing.settings.safety_bounds import (
    SafetyBoundsSettings,
    reset_safety_bounds_settings,
)


@pytest.fixture(autouse=True)
def _reset_settings():
    """테스트 격리를 위해 설정 싱글톤 초기화."""
    reset_safety_bounds_settings()
    yield
    reset_safety_bounds_settings()


class TestSafetyBoundsSettingsSlaFields:
    """SafetyBoundsSettings의 SLA 필드 존재 및 기본값 테스트."""

    def test_throttle_sla_warning_ms_fields_exist(self):
        """throttle_sla_warning_ms 관련 3개 필드가 존재해야 한다."""
        settings = SafetyBoundsSettings()
        assert hasattr(settings, "throttle_sla_warning_ms_min")
        assert hasattr(settings, "throttle_sla_warning_ms_max")
        assert hasattr(settings, "throttle_sla_warning_ms_max_change")

    def test_throttle_sla_critical_ms_fields_exist(self):
        """throttle_sla_critical_ms 관련 3개 필드가 존재해야 한다."""
        settings = SafetyBoundsSettings()
        assert hasattr(settings, "throttle_sla_critical_ms_min")
        assert hasattr(settings, "throttle_sla_critical_ms_max")
        assert hasattr(settings, "throttle_sla_critical_ms_max_change")

    def test_sla_warning_default_values(self):
        """SLA Warning 기본값은 문서 정의와 일치해야 한다."""
        field_min = SafetyBoundsSettings.model_fields["throttle_sla_warning_ms_min"]
        field_max = SafetyBoundsSettings.model_fields["throttle_sla_warning_ms_max"]
        field_change = SafetyBoundsSettings.model_fields["throttle_sla_warning_ms_max_change"]

        assert field_min.default == 50
        assert field_max.default == 2000
        assert field_change.default == 0.3

    def test_sla_critical_default_values(self):
        """SLA Critical 기본값은 문서 정의와 일치해야 한다."""
        field_min = SafetyBoundsSettings.model_fields["throttle_sla_critical_ms_min"]
        field_max = SafetyBoundsSettings.model_fields["throttle_sla_critical_ms_max"]
        field_change = SafetyBoundsSettings.model_fields["throttle_sla_critical_ms_max_change"]

        assert field_min.default == 100
        assert field_max.default == 5000
        assert field_change.default == 0.3

    def test_get_bounds_sla_warning(self):
        """get_bounds("throttle_sla_warning_ms")가 올바른 값을 반환해야 한다."""
        settings = SafetyBoundsSettings()
        bound_config = settings.get_bounds("throttle_sla_warning_ms")

        assert bound_config is not None
        assert bound_config.min_value == settings.throttle_sla_warning_ms_min
        assert bound_config.max_value == settings.throttle_sla_warning_ms_max
        assert bound_config.max_change_per_cycle == settings.throttle_sla_warning_ms_max_change

    def test_get_bounds_sla_critical(self):
        """get_bounds("throttle_sla_critical_ms")가 올바른 값을 반환해야 한다."""
        settings = SafetyBoundsSettings()
        bound_config = settings.get_bounds("throttle_sla_critical_ms")

        assert bound_config is not None
        assert bound_config.min_value == settings.throttle_sla_critical_ms_min
        assert bound_config.max_value == settings.throttle_sla_critical_ms_max
        assert bound_config.max_change_per_cycle == settings.throttle_sla_critical_ms_max_change


class TestSafetyBoundsSlaParameters:
    """SafetyBounds의 SLA 파라미터 등록 테스트."""

    def test_default_bounds_include_sla_warning(self):
        """_get_default_bounds()에 throttle_sla_warning_ms가 포함되어야 한다."""
        bounds = SafetyBounds._get_default_bounds()
        assert "throttle_sla_warning_ms" in bounds
        assert isinstance(bounds["throttle_sla_warning_ms"], ParameterBound)

    def test_default_bounds_include_sla_critical(self):
        """_get_default_bounds()에 throttle_sla_critical_ms가 포함되어야 한다."""
        bounds = SafetyBounds._get_default_bounds()
        assert "throttle_sla_critical_ms" in bounds
        assert isinstance(bounds["throttle_sla_critical_ms"], ParameterBound)

    def test_is_within_bounds_sla_warning_valid(self):
        """유효한 SLA Warning 값은 범위 내로 판정되어야 한다."""
        safety = SafetyBounds()
        settings = SafetyBoundsSettings()

        # 기본값 범위(50~2000) 내의 값
        assert safety.is_within_bounds("throttle_sla_warning_ms", 250.0) is True

    def test_is_within_bounds_sla_warning_below_min(self):
        """SLA Warning 최소값 미만은 범위 외로 판정되어야 한다."""
        safety = SafetyBounds()
        settings = SafetyBoundsSettings()

        # min_value(50) 미만
        assert safety.is_within_bounds("throttle_sla_warning_ms", 5.0) is False

    def test_is_within_bounds_sla_warning_above_max(self):
        """SLA Warning 최대값 초과는 범위 외로 판정되어야 한다."""
        safety = SafetyBounds()
        settings = SafetyBoundsSettings()

        # max_value(2000) 초과
        assert safety.is_within_bounds("throttle_sla_warning_ms", 3000.0) is False

    def test_is_within_bounds_sla_critical_valid(self):
        """유효한 SLA Critical 값은 범위 내로 판정되어야 한다."""
        safety = SafetyBounds()
        assert safety.is_within_bounds("throttle_sla_critical_ms", 1000.0) is True

    def test_is_within_bounds_sla_critical_below_min(self):
        """SLA Critical 최소값 미만은 범위 외로 판정되어야 한다."""
        safety = SafetyBounds()
        settings = SafetyBoundsSettings()

        # min_value(100) 미만
        assert safety.is_within_bounds("throttle_sla_critical_ms", 50.0) is False

    def test_is_within_bounds_sla_critical_above_max(self):
        """SLA Critical 최대값 초과는 범위 외로 판정되어야 한다."""
        safety = SafetyBounds()
        settings = SafetyBoundsSettings()

        # max_value(5000) 초과
        assert safety.is_within_bounds("throttle_sla_critical_ms", 6000.0) is False

    def test_sla_warning_bound_values_from_settings(self):
        """SafetyBounds의 SLA Warning bound 값이 Settings와 일치해야 한다."""
        settings = SafetyBoundsSettings()
        safety = SafetyBounds()

        bound = safety.bounds["throttle_sla_warning_ms"]
        assert bound.min_value == settings.throttle_sla_warning_ms_min
        assert bound.max_value == settings.throttle_sla_warning_ms_max
        assert bound.max_change_per_cycle == settings.throttle_sla_warning_ms_max_change

    def test_sla_critical_bound_values_from_settings(self):
        """SafetyBounds의 SLA Critical bound 값이 Settings와 일치해야 한다."""
        settings = SafetyBoundsSettings()
        safety = SafetyBounds()

        bound = safety.bounds["throttle_sla_critical_ms"]
        assert bound.min_value == settings.throttle_sla_critical_ms_min
        assert bound.max_value == settings.throttle_sla_critical_ms_max
        assert bound.max_change_per_cycle == settings.throttle_sla_critical_ms_max_change
