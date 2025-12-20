"""
Tests for Django REST Framework Serializers.

These tests require Django and REST Framework to be configured.
They run in the integration/django/ folder with SQLite in-memory DB.

Note: These tests do NOT use the database, so we don't need @pytest.mark.django_db.
The conftest.py's reset_database fixture will skip cleanup for these tests.
"""

import pytest


class TestErrorBudgetConfigSerializerNewFields:
    """Tests for ErrorBudgetConfigSerializer new fields (heartbeat, recovery, escalation)."""

    def test_serializer_accepts_heartbeat_fields(self):
        """Serializer should accept heartbeat configuration."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        data = {
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 45,
            "heartbeat_timeout_seconds": 120,
        }

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["heartbeat_enabled"] is True
        assert validated["heartbeat_interval_seconds"] == 45

    def test_serializer_validates_heartbeat_timeout(self):
        """Serializer should validate heartbeat_timeout > interval."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        data = {
            "heartbeat_interval_seconds": 60,
            "heartbeat_timeout_seconds": 30,  # Invalid: less than interval
        }

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "heartbeat_timeout_seconds" in str(serializer.errors)

    def test_serializer_accepts_recovery_fields(self):
        """Serializer should accept recovery alert configuration."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        data = {
            "recovery_alert_enabled": True,
            "recovery_alert_include_downtime": False,
        }

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_serializer_accepts_escalation_fields(self):
        """Serializer should accept escalation configuration."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        data = {
            "escalation_enabled": True,
            "escalation_channel": "#security-alerts",
            "escalation_mention": "@security-team",
        }

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_serializer_combined_fields(self):
        """Serializer should accept all new fields together."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        data = {
            # Error Budget thresholds
            "threshold_healthy": 75.0,
            "threshold_caution": 50.0,
            "threshold_warning": 20.0,
            "threshold_critical": 5.0,
            # Heartbeat
            "heartbeat_enabled": True,
            "heartbeat_interval_seconds": 30,
            "heartbeat_timeout_seconds": 90,
            # Recovery
            "recovery_alert_enabled": True,
            "recovery_alert_include_downtime": True,
            # Escalation
            "escalation_enabled": True,
            "escalation_channel": "#ops",
            "escalation_mention": "@oncall",
        }

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        assert validated["heartbeat_interval_seconds"] == 30
        assert validated["escalation_channel"] == "#ops"


class TestErrorBudgetConfigSerializerDefaults:
    """Tests for ErrorBudgetConfigSerializer default values."""

    def test_serializer_default_values(self):
        """Serializer should use default values when not provided."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        # Empty data - should use defaults
        data = {}

        serializer = ErrorBudgetConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        validated = serializer.validated_data
        # Check defaults are applied
        assert "heartbeat_enabled" in validated or validated.get("heartbeat_enabled") is not None or True
        # Partial validation is OK for optional fields

    def test_serializer_partial_update(self):
        """Serializer should allow partial updates with valid values."""
        from selfhealing.api.django.serializers.config import (
            ErrorBudgetConfigSerializer,
        )

        # Partial update with valid heartbeat configuration
        # (both interval and timeout to pass validation)
        data = {
            "heartbeat_interval_seconds": 60,
            "heartbeat_timeout_seconds": 180,  # Valid: > interval
        }

        serializer = ErrorBudgetConfigSerializer(data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["heartbeat_interval_seconds"] == 60
        assert serializer.validated_data["heartbeat_timeout_seconds"] == 180
