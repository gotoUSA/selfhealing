"""
CircuitMeshSettings 단위 테스트.

테스트 대상: settings/circuit_mesh.py
검증 기법: 계약 검증 (기본값), 경계값 분석 (Field constraints), 싱글톤 라이프사이클
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfhealing.settings.circuit_mesh import (
    CircuitMeshSettings,
    get_circuit_mesh_settings,
    reset_circuit_mesh_settings,
)

# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestCircuitMeshSettingsContract:
    """CircuitMeshSettings 설계 계약값 검증."""

    def test_enabled_default_is_false(self):
        """enabled 기본값: False."""
        settings = CircuitMeshSettings()
        assert settings.enabled is False

    def test_threshold_multiplier_default(self):
        """threshold_multiplier 기본값: 2.0."""
        settings = CircuitMeshSettings()
        assert settings.threshold_multiplier == 2.0

    def test_recovery_timeout_multiplier_default(self):
        """recovery_timeout_multiplier 기본값: 3.0."""
        settings = CircuitMeshSettings()
        assert settings.recovery_timeout_multiplier == 3.0

    def test_override_ttl_seconds_default(self):
        """override_ttl_seconds 기본값: 600."""
        settings = CircuitMeshSettings()
        assert settings.override_ttl_seconds == 600

    def test_propagation_max_depth_default(self):
        """propagation_max_depth 기본값: 1."""
        settings = CircuitMeshSettings()
        assert settings.propagation_max_depth == 1

    def test_propagation_damping_factor_default(self):
        """propagation_damping_factor 기본값: 0.5."""
        settings = CircuitMeshSettings()
        assert settings.propagation_damping_factor == 0.5

    def test_coordinated_recovery_enabled_default(self):
        """coordinated_recovery_enabled 기본값: True."""
        settings = CircuitMeshSettings()
        assert settings.coordinated_recovery_enabled is True

    def test_recovery_step_delay_seconds_default(self):
        """recovery_step_delay_seconds 기본값: 30."""
        settings = CircuitMeshSettings()
        assert settings.recovery_step_delay_seconds == 30

    def test_fast_recovery_timeout_seconds_default(self):
        """fast_recovery_timeout_seconds 기본값: 5."""
        settings = CircuitMeshSettings()
        assert settings.fast_recovery_timeout_seconds == 5

    def test_max_renewals_default(self):
        """max_renewals 기본값: 3."""
        settings = CircuitMeshSettings()
        assert settings.max_renewals == 3

    def test_renewal_check_threshold_seconds_default(self):
        """renewal_check_threshold_seconds 기본값: 60."""
        settings = CircuitMeshSettings()
        assert settings.renewal_check_threshold_seconds == 60

    def test_snapshot_interval_seconds_default(self):
        """snapshot_interval_seconds 기본값: 60."""
        settings = CircuitMeshSettings()
        assert settings.snapshot_interval_seconds == 60

    def test_max_concurrent_overrides_default(self):
        """max_concurrent_overrides 기본값: 20."""
        settings = CircuitMeshSettings()
        assert settings.max_concurrent_overrides == 20

    def test_env_prefix_is_selfhealing_circuit_mesh(self):
        """환경변수 접두사: SELFHEALING_CIRCUIT_MESH_."""
        assert (
            CircuitMeshSettings.model_config["env_prefix"]
            == "SELFHEALING_CIRCUIT_MESH_"
        )


# =============================================================================
# 경계값 분석 (Boundary)
# =============================================================================


class TestCircuitMeshSettingsBoundaryContract:
    """CircuitMeshSettings 필드 경계값 계약 검증."""

    def test_threshold_multiplier_minimum_boundary(self):
        """threshold_multiplier 최소 경계: ge=1.0."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(threshold_multiplier=0.99)
        settings = CircuitMeshSettings(threshold_multiplier=1.0)
        assert settings.threshold_multiplier == 1.0

    def test_threshold_multiplier_maximum_boundary(self):
        """threshold_multiplier 최대 경계: le=10.0."""
        settings = CircuitMeshSettings(threshold_multiplier=10.0)
        assert settings.threshold_multiplier == 10.0
        with pytest.raises(ValidationError):
            CircuitMeshSettings(threshold_multiplier=10.01)

    def test_recovery_timeout_multiplier_minimum_boundary(self):
        """recovery_timeout_multiplier 최소 경계: ge=1.0."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(recovery_timeout_multiplier=0.99)
        settings = CircuitMeshSettings(recovery_timeout_multiplier=1.0)
        assert settings.recovery_timeout_multiplier == 1.0

    def test_recovery_timeout_multiplier_maximum_boundary(self):
        """recovery_timeout_multiplier 최대 경계: le=10.0."""
        settings = CircuitMeshSettings(recovery_timeout_multiplier=10.0)
        assert settings.recovery_timeout_multiplier == 10.0
        with pytest.raises(ValidationError):
            CircuitMeshSettings(recovery_timeout_multiplier=10.01)

    def test_override_ttl_seconds_minimum_boundary(self):
        """override_ttl_seconds 최소 경계: ge=60."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(override_ttl_seconds=59)
        settings = CircuitMeshSettings(override_ttl_seconds=60)
        assert settings.override_ttl_seconds == 60

    def test_override_ttl_seconds_maximum_boundary(self):
        """override_ttl_seconds 최대 경계: le=3600."""
        settings = CircuitMeshSettings(override_ttl_seconds=3600)
        assert settings.override_ttl_seconds == 3600
        with pytest.raises(ValidationError):
            CircuitMeshSettings(override_ttl_seconds=3601)

    def test_propagation_max_depth_minimum_boundary(self):
        """propagation_max_depth 최소 경계: ge=1."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(propagation_max_depth=0)
        settings = CircuitMeshSettings(propagation_max_depth=1)
        assert settings.propagation_max_depth == 1

    def test_propagation_max_depth_maximum_boundary(self):
        """propagation_max_depth 최대 경계: le=5."""
        settings = CircuitMeshSettings(propagation_max_depth=5)
        assert settings.propagation_max_depth == 5
        with pytest.raises(ValidationError):
            CircuitMeshSettings(propagation_max_depth=6)

    def test_propagation_damping_factor_minimum_boundary(self):
        """propagation_damping_factor 최소 경계: ge=0.0."""
        settings = CircuitMeshSettings(propagation_damping_factor=0.0)
        assert settings.propagation_damping_factor == 0.0

    def test_propagation_damping_factor_maximum_boundary(self):
        """propagation_damping_factor 최대 경계: le=1.0."""
        settings = CircuitMeshSettings(propagation_damping_factor=1.0)
        assert settings.propagation_damping_factor == 1.0
        with pytest.raises(ValidationError):
            CircuitMeshSettings(propagation_damping_factor=1.01)

    def test_fast_recovery_timeout_seconds_minimum_boundary(self):
        """fast_recovery_timeout_seconds 최소 경계: ge=1."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(fast_recovery_timeout_seconds=0)
        settings = CircuitMeshSettings(fast_recovery_timeout_seconds=1)
        assert settings.fast_recovery_timeout_seconds == 1

    def test_fast_recovery_timeout_seconds_maximum_boundary(self):
        """fast_recovery_timeout_seconds 최대 경계: le=60."""
        settings = CircuitMeshSettings(fast_recovery_timeout_seconds=60)
        assert settings.fast_recovery_timeout_seconds == 60
        with pytest.raises(ValidationError):
            CircuitMeshSettings(fast_recovery_timeout_seconds=61)

    def test_max_renewals_minimum_boundary(self):
        """max_renewals 최소 경계: ge=0."""
        settings = CircuitMeshSettings(max_renewals=0)
        assert settings.max_renewals == 0

    def test_max_renewals_maximum_boundary(self):
        """max_renewals 최대 경계: le=10."""
        settings = CircuitMeshSettings(max_renewals=10)
        assert settings.max_renewals == 10
        with pytest.raises(ValidationError):
            CircuitMeshSettings(max_renewals=11)

    def test_max_concurrent_overrides_minimum_boundary(self):
        """max_concurrent_overrides 최소 경계: ge=1."""
        with pytest.raises(ValidationError):
            CircuitMeshSettings(max_concurrent_overrides=0)
        settings = CircuitMeshSettings(max_concurrent_overrides=1)
        assert settings.max_concurrent_overrides == 1

    def test_max_concurrent_overrides_maximum_boundary(self):
        """max_concurrent_overrides 최대 경계: le=100."""
        settings = CircuitMeshSettings(max_concurrent_overrides=100)
        assert settings.max_concurrent_overrides == 100
        with pytest.raises(ValidationError):
            CircuitMeshSettings(max_concurrent_overrides=101)


# =============================================================================
# 동작 검증 (Behavior)
# =============================================================================


class TestCircuitMeshSettingsSingletonBehavior:
    """CircuitMeshSettings 싱글톤 캐싱/리셋 동작 검증."""

    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_circuit_mesh_settings()

    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_circuit_mesh_settings()

    def test_get_returns_same_instance(self):
        """get_circuit_mesh_settings()는 동일 인스턴스를 반환."""
        first = get_circuit_mesh_settings()
        second = get_circuit_mesh_settings()
        assert first is second

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성된다."""
        first = get_circuit_mesh_settings()
        reset_circuit_mesh_settings()
        second = get_circuit_mesh_settings()
        assert first is not second
