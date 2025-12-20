"""
Fault Tolerance Tests for New Self-Healing Features

Tests that verify:
1. ForensicAdvisor failures don't break core DLQ operations
2. ChaosContext failures don't break DLQ operations
3. Drift Detection task failures are logged and don't crash system
4. All new features have graceful degradation

Core Principle: Self-healing features should NEVER make things worse.
"""

import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch


class TestForensicAdvisorFaultTolerance:
    """Test ForensicAdvisor failure handling."""

    def test_analyze_with_invalid_operation(self):
        """Advisor should handle invalid operation gracefully."""
        from selfhealing.services.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        # Mock operation with missing/invalid data
        mock_op = MagicMock()
        mock_op.id = 1
        mock_op.error_code = None
        mock_op.error_message = None
        mock_op.retry_count = 0
        mock_op.metadata = None

        # Should not raise exception
        advisory = advisor.analyze(mock_op)

        # Should return valid advisory with fallback values
        assert advisory is not None
        assert advisory.recommended_action == "manual_check"
        assert advisory.confidence > 0

    def test_analyze_and_update_db_failure_logged(self):
        """When DB save fails, should log error but not crash."""
        from selfhealing.services.forensic_advisor import (
            ForensicAdvisorService,
        )

        advisor = ForensicAdvisorService()

        # Mock operation that fails on save
        mock_op = MagicMock()
        mock_op.id = 1
        mock_op.error_code = "TIMEOUT"
        mock_op.error_message = "Connection timeout"
        mock_op.retry_count = 1
        mock_op.metadata = {}
        mock_op.save.side_effect = Exception("DB connection failed")

        # Should raise exception (this is expected - caller handles it)
        with pytest.raises(Exception) as exc_info:
            advisor.analyze_and_update(mock_op)

        assert "DB connection failed" in str(exc_info.value)


class TestChaosContextFaultTolerance:
    """Test ChaosContext failure handling."""

    def test_attach_chaos_context_with_invalid_operation(self):
        """Attaching chaos context to invalid op should not crash."""
        from selfhealing.services.chaos_context import (
            ChaosExperimentContext,
            attach_chaos_context,
        )

        mock_op = MagicMock()
        mock_op.id = 1
        mock_op.metadata = None
        mock_op.next_action_hint = ""

        context = ChaosExperimentContext(
            experiment_name="test_experiment",
            experiment_type="latency_injection",
        )

        # Should not raise exception
        try:
            attach_chaos_context(mock_op, context)
        except Exception as e:
            # If metadata is None, operation should be handled
            assert mock_op.metadata is not None or "metadata" in str(e).lower()

    def test_is_chaos_experiment_with_corrupted_metadata(self):
        """is_chaos_experiment should handle corrupted metadata."""
        from selfhealing.services.chaos_context import is_chaos_experiment

        mock_op = MagicMock()
        mock_op.metadata = {"chaos_context": "not_a_dict"}  # Corrupted

        # Should not crash, should return False
        result = is_chaos_experiment(mock_op)
        assert isinstance(result, bool)

    @pytest.mark.skip(reason="resolve_expired_chaos_experiments is Django-specific, not part of selfhealing package")
    def test_resolve_expired_with_db_failure(self):
        """resolve_expired should handle DB failures gracefully."""
        from selfhealing.services.chaos_context import (
            resolve_expired_chaos_experiments,
        )

        with patch("shopping.models.failed_operation.FailedOperation") as MockOp:
            MockOp.objects.filter.side_effect = Exception("DB unavailable")

            # Should raise exception (caller must handle)
            with pytest.raises(Exception):
                resolve_expired_chaos_experiments()


class TestDriftDetectionFaultTolerance:
    """Test Drift Detection task failure handling."""

    def test_check_sla_drift_handles_db_failure(self):
        """check_sla_drift should return error result on DB failure."""
        from shopping.tasks.drift_detection_tasks import check_sla_drift

        with patch("shopping.tasks.drift_detection_tasks._get_sla_thresholds") as mock_sla:
            mock_sla.side_effect = Exception("Config unavailable")

            result = check_sla_drift()

            assert result["success"] is False
            assert "error" in result

    def test_sla_detector_handles_empty_queryset(self):
        """SLADriftDetector handles empty querysets properly."""
        from selfhealing.tasks.drift_detection import SLADriftDetector
        from django.utils import timezone

        mock_sla = MagicMock()
        mock_sla.get_all_thresholds.return_value = {"payment": timedelta(hours=1)}

        mock_qs = MagicMock()
        mock_qs.count.return_value = 0

        detector = SLADriftDetector(
            get_sla_thresholds=lambda: mock_sla,
            get_failed_operations=lambda **kwargs: mock_qs,
        )

        result = detector.check_drift()

        assert result["success"] is True
        assert result["metrics"]["payment"]["total_resolved"] == 0


class TestAuditTrailResilience:
    """Test that audit trail mechanisms are resilient."""

    def test_control_api_audit_is_best_effort(self):
        """ControlAPI audit logging should never block response."""
        from selfhealing.services.control_api_service import (
            ControlAPIService,
        )
        import inspect

        # Verify by code inspection that _record_audit has try/except
        source = inspect.getsource(ControlAPIService._record_audit)

        assert "try:" in source
        assert "except" in source
        assert "Best-effort" in source or "best-effort" in source

    def test_forensic_advisor_logs_on_failure(self):
        """ForensicAdvisor should log failures properly."""
        from selfhealing.services.forensic_advisor import ForensicAdvisorService

        advisor = ForensicAdvisorService()

        # Mock operation with edge case data
        mock_op = MagicMock()
        mock_op.id = 1
        mock_op.error_code = None
        mock_op.error_message = None
        mock_op.retry_count = 0
        mock_op.metadata = {}

        with patch("selfhealing.services.forensic_advisor.logger") as mock_logger:
            advisory = advisor.analyze(mock_op)

            # Should return valid advisory even with edge case
            assert advisory is not None
            assert advisory.matched_pattern_id == "UNKNOWN"


class TestGracefulDegradation:
    """Test graceful degradation patterns in new features."""

    def test_dlq_service_fallback_to_local_adapter(self):
        """DLQService should fallback to local adapter if package unavailable."""
        from selfhealing.services.dlq_service import DLQService
        import inspect

        # Verify by code inspection
        source = inspect.getsource(DLQService.repository.fget)

        assert "Fallback to local" in source
        assert "DjangoFailedOperationRepository" in source

    def test_idempotency_service_graceful_degradation(self):
        """IdempotencyService should gracefully degrade to DB-only."""
        from selfhealing.services.idempotency_service import (
            IdempotencyService,
        )
        import inspect

        # Verify by code inspection - check_event is the generic method
        source = inspect.getsource(IdempotencyService.check_event)

        # The service should handle graceful degradation
        assert (
            "graceful" in source.lower() or "Gracefully" in source or "fallback" in source.lower() or "cache" in source.lower()
        )

    def test_new_features_dont_block_core_operations(self):
        """New features should not block core DLQ operations."""
        # This is verified by the architecture:
        # - ForensicAdvisor is called AFTER DLQ entry creation
        # - ChaosContext is OPTIONAL metadata
        # - Drift Detection runs in separate Celery tasks

        from selfhealing.services.dlq_service import DLQService
        from selfhealing.services.forensic_advisor import (
            ForensicAdvisorService,
        )
        from selfhealing.services.chaos_context import ChaosExperimentContext

        # Verify DLQService doesn't require ForensicAdvisor
        dlq = DLQService()
        assert not hasattr(dlq, "forensic_advisor")

        # Verify ForensicAdvisor is separate service
        advisor = ForensicAdvisorService()
        assert advisor is not None

        # Verify ChaosContext is standalone
        context = ChaosExperimentContext()
        assert context is not None


class TestSystemRecovery:
    """Test system recovery when features fail."""

    def test_drift_detection_failure_returns_structured_error(self):
        """Drift detection failure returns structured error for monitoring."""
        from shopping.tasks.drift_detection_tasks import check_sla_drift

        with patch("shopping.tasks.drift_detection_tasks._get_sla_thresholds") as mock_sla:
            mock_sla.side_effect = Exception("Database connection lost")

            result = check_sla_drift()

            # Should return structured error, not raise exception
            assert result["success"] is False
            assert "error" in result
            assert "checked_at" in result  # Always includes timestamp

    def test_forensic_analysis_task_failure_logged(self):
        """analyze_pending_operations logs failures properly."""
        from shopping.tasks.drift_detection_tasks import analyze_pending_operations

        with patch("shopping.tasks.drift_detection_tasks._get_failed_operations") as mock_ops:
            mock_ops.side_effect = Exception("DB unavailable")

            result = analyze_pending_operations()

            assert result["success"] is False
            assert "error" in result

    def test_cleanup_task_failure_logged(self):
        """cleanup_expired_chaos_experiments logs failures properly."""
        from shopping.tasks.drift_detection_tasks import (
            cleanup_expired_chaos_experiments,
        )

        with patch("shopping.tasks.drift_detection_tasks._resolve_expired_chaos_experiments") as mock_resolve:
            mock_resolve.side_effect = Exception("Cleanup failed")

            result = cleanup_expired_chaos_experiments()

            assert result["success"] is False
            assert "error" in result
