"""
Chaos Context Integration Tests.

Tests requiring actual Django database.
Run with: docker-compose exec web pytest tests/self_healing/django/test_chaos_context_integration.py -v
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.django_db
class TestChaosContextIntegration:
    """Integration tests with actual database."""

    @pytest.fixture
    def failed_operation(self):
        """Create a real FailedOperation for testing."""
        from shopping.models.failed_operation import FailedOperation

        return FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_code="CHAOS_INJECTED",
            error_message="Intentional chaos experiment failure",
            entity_type="order",
            entity_id="chaos-test-001",
        )

    def test_attach_and_retrieve_chaos_context(self, failed_operation):
        """Test full attach and retrieve cycle."""
        from selfhealing.services.chaos_context import (
            attach_chaos_context,
            get_chaos_context,
            is_chaos_experiment,
            create_chaos_context,
            ChaosExperimentType,
        )

        # Create and attach context
        context = create_chaos_context(
            experiment_type=ChaosExperimentType.TIMEOUT,
            target_service="payment_gateway",
            target_domain="payment",
            initiated_by="test",
        )

        attach_chaos_context(failed_operation, context)

        # Reload from database
        failed_operation.refresh_from_db()

        # Verify
        assert is_chaos_experiment(failed_operation) is True
        assert "[CHAOS]" in failed_operation.next_action_hint

        retrieved = get_chaos_context(failed_operation)
        assert retrieved is not None
        assert retrieved.experiment_type == "timeout"
        assert retrieved.target_service == "payment_gateway"

    def test_resolve_real_chaos_experiment(self, failed_operation):
        """Test resolving real chaos experiment."""
        from selfhealing.services.chaos_context import (
            attach_chaos_context,
            resolve_chaos_experiment,
            create_chaos_context,
            ChaosExperimentType,
        )

        # Attach chaos context
        context = create_chaos_context(
            experiment_type=ChaosExperimentType.ERROR_5XX,
            target_service="order_api",
            auto_resolve=True,
        )
        attach_chaos_context(failed_operation, context)

        # Resolve
        result = resolve_chaos_experiment(
            failed_operation,
            "Experiment completed successfully",
        )

        assert result is True

        # Reload and verify
        failed_operation.refresh_from_db()
        assert failed_operation.status == "resolved"
        assert "[CHAOS Experiment]" in failed_operation.resolution_note
