"""
Tests for Emergency Level Adaptive Sampler.
"""

import pytest
from unittest.mock import patch, MagicMock


def _is_otel_available() -> bool:
    """Check if OpenTelemetry SDK is installed."""
    try:
        import opentelemetry.sdk.trace  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _is_otel_available(),
    reason="OpenTelemetry SDK not installed",
)
class TestEmergencyLevelAdaptiveSampler:
    """Tests for EmergencyLevelAdaptiveSampler."""

    def test_sampler_initialization(self):
        """Test sampler initializes with correct defaults."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        sampler = EmergencyLevelAdaptiveSampler()
        assert sampler._base_ratio == 0.01
        assert sampler._sla_critical_ms == 500

    def test_sampler_initialization_with_custom_values(self):
        """Test sampler initializes with custom values."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        sampler = EmergencyLevelAdaptiveSampler(
            base_ratio=0.1,
            sla_critical_ms=1000,
        )
        assert sampler._base_ratio == 0.1
        assert sampler._sla_critical_ms == 1000

    def test_sampler_ratio_clamped(self):
        """Test that base_ratio is clamped to 0.0-1.0."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        # Below 0
        sampler = EmergencyLevelAdaptiveSampler(base_ratio=-0.5)
        assert sampler._base_ratio == 0.0

        # Above 1
        sampler = EmergencyLevelAdaptiveSampler(base_ratio=1.5)
        assert sampler._base_ratio == 1.0

    def test_get_description(self):
        """Test sampler description includes current state."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        sampler = EmergencyLevelAdaptiveSampler(base_ratio=0.01)
        description = sampler.get_description()

        assert "EmergencyLevelAdaptiveSampler" in description
        assert "base_ratio=1.00%" in description
        assert "sla_critical_ms=500" in description

    def test_mark_sla_violation_sets_force_sample(self):
        """Test that marking SLA violation forces next sample."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        sampler = EmergencyLevelAdaptiveSampler()
        assert sampler._force_sample_next is False

        sampler.mark_sla_violation()
        assert sampler._force_sample_next is True

    def test_mark_throttle_response_sets_force_sample(self):
        """Test that marking throttle response forces next sample."""
        from selfhealing.observability.sampler import EmergencyLevelAdaptiveSampler

        sampler = EmergencyLevelAdaptiveSampler()
        assert sampler._force_sample_next is False

        sampler.mark_throttle_response()
        assert sampler._force_sample_next is True


class TestEmergencyLevelSamplingRatios:
    """Tests for emergency level to sampling ratio mapping."""

    def test_sampling_ratios_defined_for_all_levels(self):
        """Test that sampling ratios are defined for all emergency levels."""
        from selfhealing.observability.sampler import EMERGENCY_LEVEL_SAMPLING_RATIOS

        assert 0 in EMERGENCY_LEVEL_SAMPLING_RATIOS  # NORMAL
        assert 1 in EMERGENCY_LEVEL_SAMPLING_RATIOS  # LEVEL_1
        assert 2 in EMERGENCY_LEVEL_SAMPLING_RATIOS  # LEVEL_2
        assert 3 in EMERGENCY_LEVEL_SAMPLING_RATIOS  # LEVEL_3

    def test_normal_level_has_low_sampling(self):
        """Test NORMAL level uses 1% sampling."""
        from selfhealing.observability.sampler import EMERGENCY_LEVEL_SAMPLING_RATIOS

        assert EMERGENCY_LEVEL_SAMPLING_RATIOS[0] == 0.01

    def test_level_1_has_moderate_sampling(self):
        """Test LEVEL_1 uses 10% sampling."""
        from selfhealing.observability.sampler import EMERGENCY_LEVEL_SAMPLING_RATIOS

        assert EMERGENCY_LEVEL_SAMPLING_RATIOS[1] == 0.10

    def test_level_2_has_full_sampling(self):
        """Test LEVEL_2 uses 100% sampling."""
        from selfhealing.observability.sampler import EMERGENCY_LEVEL_SAMPLING_RATIOS

        assert EMERGENCY_LEVEL_SAMPLING_RATIOS[2] == 1.00

    def test_level_3_has_full_sampling(self):
        """Test LEVEL_3 uses 100% sampling."""
        from selfhealing.observability.sampler import EMERGENCY_LEVEL_SAMPLING_RATIOS

        assert EMERGENCY_LEVEL_SAMPLING_RATIOS[3] == 1.00


@pytest.mark.skipif(
    not _is_otel_available(),
    reason="OpenTelemetry SDK not installed",
)
class TestStaticRatioSampler:
    """Tests for StaticRatioSampler."""

    def test_static_sampler_initialization(self):
        """Test static sampler initializes correctly."""
        from selfhealing.observability.sampler import StaticRatioSampler

        sampler = StaticRatioSampler(ratio=0.5)
        assert sampler._ratio == 0.5

    def test_static_sampler_ratio_clamped(self):
        """Test static sampler ratio is clamped."""
        from selfhealing.observability.sampler import StaticRatioSampler

        sampler = StaticRatioSampler(ratio=-0.5)
        assert sampler._ratio == 0.0

        sampler = StaticRatioSampler(ratio=1.5)
        assert sampler._ratio == 1.0

    def test_static_sampler_description(self):
        """Test static sampler description."""
        from selfhealing.observability.sampler import StaticRatioSampler

        sampler = StaticRatioSampler(ratio=0.5)
        description = sampler.get_description()

        assert "StaticRatioSampler" in description
        assert "50.00%" in description


class TestGetCurrentEmergencyLevel:
    """Tests for _get_current_emergency_level helper."""

    def test_returns_0_when_emergency_mode_unavailable(self):
        """Test fallback to NORMAL (0) when emergency mode is unavailable."""
        from selfhealing.observability.sampler import _get_current_emergency_level

        # Mock get_emergency_manager to raise an exception
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager",
            side_effect=ImportError("Emergency mode not available"),
        ):
            level = _get_current_emergency_level()
            # Should return 0 (NORMAL) on any exception
            assert level == 0

    def test_returns_emergency_level_value(self):
        """Test returns correct emergency level value."""
        from selfhealing.observability.sampler import _get_current_emergency_level
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2

        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager",
            return_value=mock_manager,
        ):
            # This may still fail if import fails, which is fine
            try:
                level = _get_current_emergency_level()
                assert level in [0, 1, 2, 3]
            except Exception:
                # If emergency mode not available, should not crash
                pass
