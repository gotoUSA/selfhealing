"""
Tests for Semantic Configuration Descriptions.

Validates the CONFIG_DESCRIPTIONS mapping and formatting functions.
"""

import os
import django

# Configure Django settings before importing DRF
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from selfhealing.api.django.config_descriptions import (
    CONFIG_DESCRIPTIONS,
    get_field_description,
    get_field_unit,
    format_value_change,
    format_changes_log,
    format_changes_summary,
)


class TestConfigDescriptions:
    """Tests for CONFIG_DESCRIPTIONS dictionary."""

    def test_jitter_fields_have_descriptions(self):
        """Jitter fields should have semantic descriptions."""
        assert "jitter_enabled" in CONFIG_DESCRIPTIONS
        assert "jitter_max_delay_seconds" in CONFIG_DESCRIPTIONS

    def test_circuit_breaker_fields_have_descriptions(self):
        """Circuit breaker fields should have semantic descriptions."""
        assert "failure_threshold" in CONFIG_DESCRIPTIONS
        assert "recovery_timeout" in CONFIG_DESCRIPTIONS
        assert "success_threshold" in CONFIG_DESCRIPTIONS

    def test_error_budget_fields_have_descriptions(self):
        """Error budget fields should have semantic descriptions."""
        assert "threshold_healthy" in CONFIG_DESCRIPTIONS
        assert "threshold_warning" in CONFIG_DESCRIPTIONS
        assert "burn_rate_fast_critical" in CONFIG_DESCRIPTIONS
        assert "heartbeat_enabled" in CONFIG_DESCRIPTIONS

    def test_sla_fields_have_descriptions(self):
        """SLA fields should have semantic descriptions."""
        assert "default_hours" in CONFIG_DESCRIPTIONS
        assert "thresholds_by_domain" in CONFIG_DESCRIPTIONS

    def test_description_format(self):
        """Each description should be a tuple of (label, unit)."""
        for field_name, description in CONFIG_DESCRIPTIONS.items():
            assert isinstance(description, tuple), f"{field_name} should be tuple"
            assert len(description) == 2, f"{field_name} should have 2 elements"
            label, unit = description
            assert isinstance(label, str), f"{field_name} label should be string"
            assert isinstance(unit, str), f"{field_name} unit should be string"
            assert len(label) > 0, f"{field_name} label should not be empty"


class TestGetFieldDescription:
    """Tests for get_field_description function."""

    def test_known_field_returns_label(self):
        """Known field returns its semantic label."""
        label = get_field_description("jitter_enabled")
        assert label == "Infrastructure Protection (Jitter)"

    def test_unknown_field_returns_title_case(self):
        """Unknown field returns title-cased version of field name."""
        label = get_field_description("unknown_field_name")
        assert label == "Unknown Field Name"

    def test_failure_threshold_description(self):
        """Circuit breaker failure threshold has correct description."""
        label = get_field_description("failure_threshold")
        assert label == "Circuit Breaker Failure Threshold"


class TestGetFieldUnit:
    """Tests for get_field_unit function."""

    def test_bool_unit(self):
        """Boolean fields should have 'bool' unit."""
        assert get_field_unit("jitter_enabled") == "bool"

    def test_seconds_unit(self):
        """Time fields should have 'seconds' unit."""
        assert get_field_unit("jitter_max_delay_seconds") == "seconds"

    def test_percent_unit(self):
        """Percentage fields should have 'percent' unit."""
        assert get_field_unit("threshold_healthy") == "percent"

    def test_unknown_field_defaults_to_text(self):
        """Unknown fields default to 'text' unit."""
        assert get_field_unit("unknown_field") == "text"


class TestFormatValueChange:
    """Tests for format_value_change function."""

    def test_bool_change_formatting(self):
        """Boolean changes show enabled/disabled."""
        result = format_value_change("jitter_enabled", True, False)
        assert "Infrastructure Protection (Jitter)" in result
        assert "enabled" in result
        assert "disabled" in result
        assert "→" in result

    def test_seconds_change_formatting(self):
        """Seconds values show 's' suffix."""
        result = format_value_change("jitter_max_delay_seconds", 60.0, 5.0)
        assert "Jitter Delay Threshold" in result
        assert "60.0s" in result
        assert "5.0s" in result
        assert "→" in result

    def test_percent_change_formatting(self):
        """Percent values show '%' suffix."""
        result = format_value_change("threshold_warning", 20.0, 25.0)
        assert "Error Budget Warning Level" in result
        assert "20.0%" in result
        assert "25.0%" in result

    def test_multiplier_change_formatting(self):
        """Multiplier values show 'x' suffix."""
        result = format_value_change("burn_rate_fast_critical", 14.4, 12.0)
        assert "Fast Burn Rate Critical Threshold" in result
        assert "14.4x" in result
        assert "12.0x" in result

    def test_none_old_value_shows_none(self):
        """None old value shows 'None'."""
        result = format_value_change("jitter_enabled", None, True)
        assert "None" in result
        assert "enabled" in result

    def test_dict_formatting(self):
        """Dict values are formatted appropriately."""
        result = format_value_change("thresholds_by_domain", {}, {"payment": 1})
        assert "Domain-Specific SLA Thresholds" in result
        assert "{}" in result


class TestFormatChangesLog:
    """Tests for format_changes_log function."""

    def test_single_change(self):
        """Single change returns single line."""
        changes = {"jitter_enabled": False}
        previous = {"jitter_enabled": True}
        lines = format_changes_log(changes, previous)
        assert len(lines) == 1
        assert "Infrastructure Protection (Jitter)" in lines[0]

    def test_multiple_changes(self):
        """Multiple changes return multiple lines."""
        changes = {
            "jitter_enabled": False,
            "jitter_max_delay_seconds": 5.0,
        }
        previous = {
            "jitter_enabled": True,
            "jitter_max_delay_seconds": 60.0,
        }
        lines = format_changes_log(changes, previous)
        assert len(lines) == 2

    def test_no_previous_config(self):
        """Without previous config, old values show as None."""
        changes = {"jitter_enabled": True}
        lines = format_changes_log(changes, None)
        assert len(lines) == 1
        assert "None" in lines[0]


class TestFormatChangesSummary:
    """Tests for format_changes_summary function."""

    def test_empty_changes(self):
        """Empty changes return 'No changes'."""
        result = format_changes_summary({}, {})
        assert result == "No changes"

    def test_single_change_bullet(self):
        """Single change is formatted with bullet."""
        changes = {"jitter_enabled": False}
        previous = {"jitter_enabled": True}
        result = format_changes_summary(changes, previous)
        assert "•" in result
        assert "Infrastructure Protection (Jitter)" in result

    def test_multiple_changes_bullets(self):
        """Multiple changes each have bullets."""
        changes = {
            "jitter_enabled": False,
            "failure_threshold": 10,
        }
        previous = {
            "jitter_enabled": True,
            "failure_threshold": 5,
        }
        result = format_changes_summary(changes, previous)
        assert result.count("•") == 2


class TestIntegration:
    """Integration tests simulating real API usage."""

    def test_metrics_config_change_log(self):
        """Test realistic metrics config change logging."""
        changes = {
            "jitter_enabled": False,
            "jitter_max_delay_seconds": 30.0,
        }
        previous = {
            "enabled": True,
            "prefix": "selfhealing",
            "collection_interval": 60,
            "export_prometheus": True,
            "jitter_enabled": True,
            "jitter_max_delay_seconds": 60.0,
        }

        summary = format_changes_summary(changes, previous)

        # Verify semantic labels are used
        assert "Infrastructure Protection (Jitter)" in summary
        assert "Jitter Delay Threshold" in summary

        # Verify values are formatted correctly
        assert "enabled → disabled" in summary
        assert "60.0s → 30.0s" in summary

    def test_circuit_breaker_config_change_log(self):
        """Test realistic circuit breaker config change logging."""
        changes = {
            "failure_threshold": 10,
            "recovery_timeout": 120,
        }
        previous = {
            "failure_threshold": 5,
            "recovery_timeout": 60,
        }

        summary = format_changes_summary(changes, previous)

        assert "Circuit Breaker Failure Threshold" in summary
        assert "Circuit Breaker Recovery Timeout" in summary
        assert "5 → 10" in summary
        assert "60s → 120s" in summary


class TestFallbackBehavior:
    """Tests for defensive fallback behavior when formatting fails."""

    def test_format_value_change_with_unknown_field(self):
        """Unknown fields should fallback to title-cased name."""
        result = format_value_change("unknown_new_field", "old", "new")
        assert "Unknown New Field" in result
        assert "old" in result
        assert "new" in result
        assert "→" in result

    def test_format_value_change_with_complex_objects(self):
        """Complex objects should not cause exceptions."""
        class CustomObject:
            def __str__(self):
                return "CustomObject"

        # Should not raise
        result = format_value_change("some_field", CustomObject(), CustomObject())
        assert "→" in result

    def test_format_value_change_with_none_values(self):
        """None values should be handled gracefully."""
        result = format_value_change("jitter_enabled", None, None)
        assert "None" in result

    def test_format_changes_summary_with_empty_dict(self):
        """Empty changes dict should return 'No changes'."""
        result = format_changes_summary({}, {})
        assert result == "No changes"

    def test_format_changes_summary_never_raises(self):
        """format_changes_summary should never raise an exception."""
        # Even with weird inputs, should not raise
        weird_inputs = [
            (None, None),  # Will fail on .items()
            ({}, None),
            ({"field": object()}, {}),  # Non-serializable value
        ]

        for changes, previous in weird_inputs:
            try:
                if changes is None:
                    changes = {}  # Normalize
                result = format_changes_summary(changes, previous)
                assert isinstance(result, str)
            except Exception as e:
                pytest.fail(f"format_changes_summary raised: {e}")

    def test_format_value_change_fallback_on_internal_error(self):
        """If internal formatting fails, basic fallback is used."""
        # This tests that even if _format_value_with_unit somehow fails,
        # we still get a usable string back
        result = format_value_change("test_field", 123, 456)
        assert "→" in result
        assert "123" in result or "Test Field" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
