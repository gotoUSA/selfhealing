"""
Unit Tests for Forensic Advisor - Decision Support System

Tests the ForensicAdvisorService which provides data-driven recommendations
for DLQ item resolution.

Core Principle Verification: System provides data, humans make decisions.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from django.utils import timezone


class TestForensicAdvisorPatternMatching:
    """Test forensic advisor pattern matching logic."""

    @pytest.fixture
    def mock_failed_operation(self):
        """Create a mock FailedOperation for testing."""
        operation = MagicMock()
        operation.id = 1
        operation.error_code = ""
        operation.error_message = ""
        operation.retry_count = 0
        operation.metadata = {}
        operation.next_action_hint = ""
        operation.recommended_action = ""
        return operation

    def test_transient_network_pattern_detection(self, mock_failed_operation):
        """Test detection of transient network failure pattern."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
            RecommendedAction,
        )

        advisor = ForensicAdvisorService()

        # Set up transient network failure characteristics
        mock_failed_operation.error_code = "ETIMEDOUT"
        mock_failed_operation.error_message = "Connection timeout after 30s"
        mock_failed_operation.retry_count = 1

        advisory = advisor.analyze(mock_failed_operation)

        assert advisory.matched_pattern_id == "TRANSIENT_NETWORK"
        assert advisory.recommended_action == RecommendedAction.REPLAY.value
        assert advisory.confidence >= 0.7
        assert "일시적 네트워크 장애" in advisory.advisory_message

    def test_rate_limit_pattern_detection(self, mock_failed_operation):
        """Test detection of rate limit exceeded pattern."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
            RecommendedAction,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "429"
        mock_failed_operation.error_message = "Rate limit exceeded"
        mock_failed_operation.retry_count = 2

        advisory = advisor.analyze(mock_failed_operation)

        assert advisory.matched_pattern_id == "RATE_LIMIT"
        assert advisory.recommended_action == RecommendedAction.WAIT_AND_RETRY.value
        assert "레이트 리밋" in advisory.advisory_message

    def test_auth_failure_pattern_detection(self, mock_failed_operation):
        """Test detection of authentication/authorization failure."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
            RecommendedAction,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "403"
        mock_failed_operation.error_message = "Forbidden - invalid token"
        mock_failed_operation.retry_count = 1

        advisory = advisor.analyze(mock_failed_operation)

        assert advisory.matched_pattern_id == "AUTH_FAILURE"
        assert advisory.recommended_action == RecommendedAction.SECURITY_REVIEW.value
        assert "보안 점검" in advisory.advisory_message

    def test_repeated_failure_escalation(self, mock_failed_operation):
        """Test escalation for repeated failures (retry_count >= 3)."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
            RecommendedAction,
            AdvisoryLevel,
        )

        advisor = ForensicAdvisorService()

        # High retry count triggers escalation regardless of error type
        mock_failed_operation.error_code = "500"
        mock_failed_operation.error_message = "Internal server error"
        mock_failed_operation.retry_count = 3

        advisory = advisor.analyze(mock_failed_operation)

        assert advisory.matched_pattern_id == "REPEATED_FAILURE"
        assert advisory.recommended_action == RecommendedAction.ESCALATE.value
        assert advisory.level == AdvisoryLevel.CRITICAL.value
        assert "에스컬레이션" in advisory.advisory_message

    def test_unknown_pattern_fallback(self, mock_failed_operation):
        """Test fallback for unknown patterns."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
            RecommendedAction,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "UNKNOWN_ERROR_XYZ"
        mock_failed_operation.error_message = "Some completely unknown error"
        mock_failed_operation.retry_count = 1

        advisory = advisor.analyze(mock_failed_operation)

        assert advisory.matched_pattern_id == "UNKNOWN"
        assert advisory.recommended_action == RecommendedAction.MANUAL_CHECK.value
        assert advisory.confidence < 0.5
        assert "수동 확인" in advisory.advisory_message

    def test_analyze_and_update_stores_advisory(self, mock_failed_operation):
        """Test that analyze_and_update stores advisory in metadata."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "PG_TIMEOUT"
        mock_failed_operation.error_message = "Payment gateway timeout"
        mock_failed_operation.retry_count = 1
        mock_failed_operation.metadata = {}

        advisory = advisor.analyze_and_update(mock_failed_operation)

        # Verify metadata was updated
        assert "forensic_advisory" in mock_failed_operation.metadata
        assert mock_failed_operation.next_action_hint != ""
        assert mock_failed_operation.save.called

    def test_decision_factors_traceability(self, mock_failed_operation):
        """Test that decision factors are recorded for audit trail."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "503"
        mock_failed_operation.error_message = "Service unavailable"
        mock_failed_operation.retry_count = 2

        advisory = advisor.analyze(mock_failed_operation)

        # Decision factors must be recorded
        assert len(advisory.decision_factors) > 0
        assert advisory.evidence is not None
        assert "error_code" in advisory.evidence

    def test_hint_string_generation(self, mock_failed_operation):
        """Test generation of human-readable hint string."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        mock_failed_operation.error_code = "TIMEOUT"
        mock_failed_operation.error_message = "Connection timeout"
        mock_failed_operation.retry_count = 1

        advisory = advisor.analyze(mock_failed_operation)
        hint = advisory.to_hint_string()

        assert "[" in hint  # Contains action label
        assert "]" in hint
        assert len(hint) > 20  # Not empty


class TestForensicAdvisorSingleton:
    """Test forensic advisor singleton and convenience functions."""

    def test_get_forensic_advisor_singleton(self):
        """Test singleton accessor returns same instance."""
        from selfhealing.services.forensic_advisor import (
            get_forensic_advisor,
        )
        import selfhealing.services.forensic_advisor as module

        # Reset singleton
        module._advisor_instance = None

        advisor1 = get_forensic_advisor()
        advisor2 = get_forensic_advisor()

        assert advisor1 is advisor2

    def test_convenience_function_analyze(self):
        """Test analyze_failed_operation convenience function."""
        from shopping.services.self_healing.forensic_advisor import (
            analyze_failed_operation,
        )

        operation = MagicMock()
        operation.error_code = "500"
        operation.error_message = "Server error"
        operation.retry_count = 1
        operation.metadata = {}

        advisory = analyze_failed_operation(operation)

        assert advisory is not None
        assert advisory.analyzed_at is not None


class TestForensicAdvisorNonAutoExecution:
    """Test that ForensicAdvisor NEVER auto-executes actions."""

    def test_analyze_does_not_modify_status(self):
        """Verify analyze() does not change operation status."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        operation = MagicMock()
        operation.id = 1
        operation.error_code = "ETIMEDOUT"
        operation.error_message = "Timeout"
        operation.retry_count = 1
        operation.metadata = {}
        operation.status = "pending"

        # Analyze should not change status
        advisor.analyze(operation)

        # Status should remain unchanged
        assert operation.status == "pending"

    def test_analyze_and_update_only_updates_advisory_fields(self):
        """Verify analyze_and_update only updates advisory-related fields."""
        from shopping.services.self_healing.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        operation = MagicMock()
        operation.id = 1
        operation.error_code = "503"
        operation.error_message = "Service unavailable"
        operation.retry_count = 1
        operation.metadata = {}
        operation.status = "pending"

        advisor.analyze_and_update(operation)

        # Check save was called with only allowed fields
        save_call = operation.save.call_args
        update_fields = save_call.kwargs.get("update_fields", [])

        # Should only update advisory-related fields
        allowed_fields = {"metadata", "next_action_hint", "recommended_action", "updated_at"}
        assert set(update_fields).issubset(allowed_fields)

        # Should NOT include action-executing fields
        assert "status" not in update_fields
        assert "resolved_at" not in update_fields
        assert "resolved_by" not in update_fields


@pytest.mark.django_db
class TestForensicAdvisorIntegration:
    """Integration tests with actual database.

    These tests require a running database.
    Run with: docker-compose exec web pytest tests/self_healing/unit/test_forensic_advisor.py::TestForensicAdvisorIntegration
    """

    @pytest.fixture
    def failed_operation(self):
        """Create a real FailedOperation for testing."""
        from shopping.models.failed_operation import FailedOperation

        return FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            error_code="ETIMEDOUT",
            error_message="Connection timeout to payment gateway",
            entity_type="order",
            entity_id="12345",
            retry_count=1,
            metadata={
                "retry_history": [
                    {
                        "attempt": 1,
                        "error_code": "ETIMEDOUT",
                        "error_message": "Timeout",
                        "attempted_at": timezone.now().isoformat(),
                        "backoff_seconds": 4,
                    }
                ]
            },
        )

    @pytest.mark.skip(
        reason="Integration test - run in Docker: docker-compose exec web pytest -k TestForensicAdvisorIntegration"
    )
    def test_real_operation_analysis(self, failed_operation):
        """Test analysis with real FailedOperation model."""
        from shopping.services.self_healing.forensic_advisor import (
            get_forensic_advisor,
        )

        advisor = get_forensic_advisor()
        advisory = advisor.analyze(failed_operation)

        assert advisory.matched_pattern_id == "TRANSIENT_NETWORK"
        assert advisory.recommended_action == "replay"
        assert advisory.confidence >= 0.7

    @pytest.mark.skip(
        reason="Integration test - run in Docker: docker-compose exec web pytest -k TestForensicAdvisorIntegration"
    )
    def test_real_operation_update(self, failed_operation):
        """Test analyze_and_update with real FailedOperation model."""
        from shopping.services.self_healing.forensic_advisor import (
            get_forensic_advisor,
        )

        advisor = get_forensic_advisor()
        advisory = advisor.analyze_and_update(failed_operation)

        # Reload from database
        failed_operation.refresh_from_db()

        assert failed_operation.next_action_hint != ""
        assert "forensic_advisory" in failed_operation.metadata
        assert failed_operation.recommended_action == "replay"

        # Verify status was NOT changed
        assert failed_operation.status == "pending"
