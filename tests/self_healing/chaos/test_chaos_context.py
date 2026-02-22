"""
Unit Tests for Chaos Experiment Context

Tests the ChaosExperimentContext which distinguishes intentional
chaos experiments from actual failures in the DLQ.
"""

import pytest
from datetime import timedelta
from unittest.mock import MagicMock

from django.utils import timezone


class TestChaosExperimentContext:
    """Test ChaosExperimentContext data structure."""

    def test_create_context_with_defaults(self):
        """Test creating context with default values."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
            ChaosExperimentType,
            ChaosExperimentStatus,
        )

        context = ChaosExperimentContext()

        assert context.experiment_id.startswith("chaos-")
        assert context.experiment_type == ChaosExperimentType.LATENCY_INJECTION.value
        assert context.status == ChaosExperimentStatus.ACTIVE.value
        assert context.auto_resolve is True
        assert context.expected_recovery is True

    def test_create_context_with_custom_values(self):
        """Test creating context with custom values."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
            ChaosExperimentType,
        )

        context = ChaosExperimentContext(
            experiment_id="test-exp-001",
            experiment_name="Payment Gateway Latency Test",
            experiment_type=ChaosExperimentType.ERROR_5XX.value,
            target_service="toss_payment",
            target_domain="payment",
            injection_rate=0.01,
            injected_latency_ms=500,
            initiated_by="admin",
            initiated_from="gameday_exercise",
        )

        assert context.experiment_id == "test-exp-001"
        assert context.experiment_name == "Payment Gateway Latency Test"
        assert context.experiment_type == "error_5xx"
        assert context.target_service == "toss_payment"
        assert context.injection_rate == 0.01

    def test_to_dict_serialization(self):
        """Test serialization to dictionary."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
        )

        context = ChaosExperimentContext(
            experiment_id="test-exp-002",
            experiment_name="Test Experiment",
            target_service="test_service",
        )

        data = context.to_dict()

        assert isinstance(data, dict)
        assert data["experiment_id"] == "test-exp-002"
        assert data["experiment_name"] == "Test Experiment"
        assert "started_at" in data
        assert "expires_at" in data

    def test_from_dict_deserialization(self):
        """Test deserialization from dictionary."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
        )

        data = {
            "experiment_id": "test-exp-003",
            "experiment_name": "Restored Experiment",
            "experiment_type": "timeout",
            "target_service": "api_service",
            "status": "active",
            "auto_resolve": True,
        }

        context = ChaosExperimentContext.from_dict(data)

        assert context.experiment_id == "test-exp-003"
        assert context.experiment_name == "Restored Experiment"
        assert context.experiment_type == "timeout"
        assert context.auto_resolve is True

    def test_is_expired_not_expired(self):
        """Test is_expired returns False for active experiments."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
        )

        context = ChaosExperimentContext(
            expected_duration_seconds=3600,  # 1 hour
        )

        assert context.is_expired() is False

    def test_is_expired_when_expired(self):
        """Test is_expired returns True for expired experiments."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
        )

        # Create context with expired time
        past_time = (timezone.now() - timedelta(hours=1)).isoformat()
        context = ChaosExperimentContext(
            started_at=past_time,
            expected_duration_seconds=60,  # 1 minute (already passed)
        )

        assert context.is_expired() is True

    def test_mark_completed(self):
        """Test marking experiment as completed."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
            ChaosExperimentStatus,
        )

        context = ChaosExperimentContext()
        assert context.status == ChaosExperimentStatus.ACTIVE.value

        context.mark_completed("Test completed successfully")

        assert context.status == ChaosExperimentStatus.COMPLETED.value
        assert context.resolved_at != ""
        assert "successfully" in context.resolution_note

    def test_mark_aborted(self):
        """Test marking experiment as aborted."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
            ChaosExperimentStatus,
        )

        context = ChaosExperimentContext()
        context.mark_aborted("Emergency abort")

        assert context.status == ChaosExperimentStatus.ABORTED.value
        assert "Emergency" in context.resolution_note


class TestChaosContextHelpers:
    """Test chaos context helper functions."""

    @pytest.fixture
    def mock_operation(self):
        """Create a mock FailedOperation."""
        operation = MagicMock()
        operation.id = 1
        operation.metadata = {}
        operation.status = "pending"
        operation.Status = MagicMock()
        operation.Status.RESOLVED = "resolved"
        operation.ResolutionType = MagicMock()
        operation.ResolutionType.AUTO_REPLAY = "auto_replay"
        return operation

    def test_is_chaos_experiment_false(self, mock_operation):
        """Test is_chaos_experiment returns False for regular operations."""
        from selfhealing.services.chaos_context import (
            is_chaos_experiment,
        )

        mock_operation.metadata = {}
        assert is_chaos_experiment(mock_operation) is False

        mock_operation.metadata = {"some_key": "value"}
        assert is_chaos_experiment(mock_operation) is False

    def test_is_chaos_experiment_true(self, mock_operation):
        """Test is_chaos_experiment returns True for chaos operations."""
        from selfhealing.services.chaos_context import (
            is_chaos_experiment,
        )

        mock_operation.metadata = {
            "chaos_experiment_context": {
                "experiment_id": "test-001",
                "experiment_type": "latency_injection",
            }
        }

        assert is_chaos_experiment(mock_operation) is True

    def test_get_chaos_context_none(self, mock_operation):
        """Test get_chaos_context returns None for regular operations."""
        from selfhealing.services.chaos_context import (
            get_chaos_context,
        )

        mock_operation.metadata = {}
        assert get_chaos_context(mock_operation) is None

    def test_get_chaos_context_success(self, mock_operation):
        """Test get_chaos_context returns context for chaos operations."""
        from selfhealing.services.chaos_context import (
            get_chaos_context,
        )

        mock_operation.metadata = {
            "chaos_experiment_context": {
                "experiment_id": "test-002",
                "experiment_type": "error_5xx",
                "target_service": "payment_api",
            }
        }

        context = get_chaos_context(mock_operation)

        assert context is not None
        assert context.experiment_id == "test-002"
        assert context.experiment_type == "error_5xx"

    def test_attach_chaos_context(self, mock_operation):
        """Test attaching chaos context to operation."""
        from selfhealing.services.chaos_context import (
            attach_chaos_context,
            ChaosExperimentContext,
        )

        context = ChaosExperimentContext(
            experiment_id="test-003",
            experiment_type="timeout",
            target_service="order_service",
        )

        attach_chaos_context(mock_operation, context)

        assert "chaos_experiment_context" in mock_operation.metadata
        assert mock_operation.metadata["is_chaos_experiment"] is True
        assert "[CHAOS]" in mock_operation.next_action_hint
        assert mock_operation.save.called

    def test_resolve_chaos_experiment(self, mock_operation):
        """Test resolving a chaos experiment."""
        from selfhealing.services.chaos_context import (
            resolve_chaos_experiment,
        )

        mock_operation.metadata = {
            "chaos_experiment_context": {
                "experiment_id": "test-004",
                "experiment_type": "latency_injection",
                "status": "active",
            }
        }

        result = resolve_chaos_experiment(mock_operation, "Test complete")

        assert result is True
        assert mock_operation.status == "resolved"
        assert mock_operation.save.called

    def test_resolve_chaos_experiment_not_chaos(self, mock_operation):
        """Test resolve_chaos_experiment returns False for non-chaos ops."""
        from selfhealing.services.chaos_context import (
            resolve_chaos_experiment,
        )

        mock_operation.metadata = {}

        result = resolve_chaos_experiment(mock_operation)

        assert result is False


class TestCreateChaosContext:
    """Test chaos context factory function."""

    def test_create_chaos_context_basic(self):
        """Test basic chaos context creation."""
        from selfhealing.services.chaos_context import (
            create_chaos_context,
            ChaosExperimentType,
        )

        context = create_chaos_context(
            experiment_type=ChaosExperimentType.LATENCY_INJECTION,
            target_service="payment_api",
            target_domain="payment",
        )

        assert context.experiment_type == "latency_injection"
        assert context.target_service == "payment_api"
        assert context.target_domain == "payment"
        assert context.initiated_by == "system"

    def test_create_chaos_context_with_all_params(self):
        """Test chaos context creation with all parameters."""
        from selfhealing.services.chaos_context import (
            create_chaos_context,
            ChaosExperimentType,
        )

        context = create_chaos_context(
            experiment_type=ChaosExperimentType.ERROR_5XX,
            target_service="order_processor",
            target_domain="payment",
            duration_seconds=600,
            initiated_by="admin_user",
            initiated_from="gameday_2024",
            auto_resolve=False,
            injection_rate=0.05,
            injected_error_code="503",
            approval_ticket="TICKET-123",
        )

        assert context.experiment_type == "error_5xx"
        assert context.expected_duration_seconds == 600
        assert context.initiated_by == "admin_user"
        assert context.initiated_from == "gameday_2024"
        assert context.auto_resolve is False
        assert context.injection_rate == 0.05
        assert context.approval_ticket == "TICKET-123"

    def test_create_chaos_context_string_type(self):
        """Test chaos context creation with string experiment type."""
        from selfhealing.services.chaos_context import (
            create_chaos_context,
        )

        context = create_chaos_context(
            experiment_type="rate_limit",
            target_service="api_gateway",
        )

        assert context.experiment_type == "rate_limit"


# TestChaosContextIntegration moved to tests/self_healing/django/test_chaos_context_integration.py
