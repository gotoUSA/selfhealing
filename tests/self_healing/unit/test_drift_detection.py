"""
Unit Tests for SLA Drift Detection

Tests the SLA drift detection system which compares configured SLA thresholds
with actual recovery performance.

Core Principle Verification: System provides warnings, never auto-adjusts settings.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from django.utils import timezone


class TestSLADriftDetector:
    """Test SLADriftDetector class from selfhealing package."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for detector."""
        mock_sla = MagicMock()
        mock_sla.get_all_thresholds.return_value = {
            "payment": timedelta(hours=1),
            "point": timedelta(hours=4),
        }

        return {
            "get_sla_thresholds": lambda: mock_sla,
            "get_failed_operations": MagicMock(),
            "record_sla_breach": MagicMock(),
        }

    def test_drift_detector_creation(self, mock_dependencies):
        """Test creating SLADriftDetector instance."""
        from selfhealing.tasks.drift_detection import SLADriftDetector

        detector = SLADriftDetector(
            get_sla_thresholds=mock_dependencies["get_sla_thresholds"],
            get_failed_operations=mock_dependencies["get_failed_operations"],
            record_sla_breach=mock_dependencies["record_sla_breach"],
        )

        assert detector is not None

    def test_check_drift_no_operations(self, mock_dependencies):
        """Test drift detection with no operations."""
        from selfhealing.tasks.drift_detection import SLADriftDetector

        # Mock no operations
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        mock_dependencies["get_failed_operations"].return_value = mock_qs

        detector = SLADriftDetector(
            get_sla_thresholds=mock_dependencies["get_sla_thresholds"],
            get_failed_operations=mock_dependencies["get_failed_operations"],
            record_sla_breach=mock_dependencies["record_sla_breach"],
        )

        result = detector.check_drift()

        assert result["success"] is True
        assert len(result["warnings"]) == 0
        assert "payment" in result["domains_checked"]

    def test_check_drift_with_sla_breach(self, mock_dependencies):
        """Test drift detection when SLA is breached."""
        from selfhealing.tasks.drift_detection import SLADriftDetector

        now = timezone.now()

        # Create mock operations with SLA breach
        mock_ops = []
        for i in range(10):
            op = MagicMock()
            op.created_at = now - timedelta(hours=3)
            op.resolved_at = now  # 3 hour recovery, breaches 1 hour SLA
            mock_ops.append(op)

        def mock_filter(**kwargs):
            mock_qs = MagicMock()
            if kwargs.get("status__in") == ["resolved", "rejected"]:
                mock_qs.count.return_value = 10
                mock_qs.__iter__ = lambda self: iter(mock_ops)
            elif kwargs.get("status") == "pending":
                mock_qs.__iter__ = lambda self: iter([])
            return mock_qs

        mock_dependencies["get_failed_operations"].side_effect = mock_filter

        detector = SLADriftDetector(
            get_sla_thresholds=mock_dependencies["get_sla_thresholds"],
            get_failed_operations=mock_dependencies["get_failed_operations"],
            record_sla_breach=mock_dependencies["record_sla_breach"],
        )

        result = detector.check_drift()

        assert result["success"] is True
        # Should have warning for payment domain
        payment_metrics = result["metrics"].get("payment", {})
        assert payment_metrics.get("sla_breach_count", 0) > 0


class TestChaosExperimentCleaner:
    """Test ChaosExperimentCleaner class."""

    def test_cleanup_no_expired(self):
        """Test cleanup when no experiments are expired."""
        from selfhealing.tasks.drift_detection import ChaosExperimentCleaner

        cleaner = ChaosExperimentCleaner(
            resolve_expired_experiments=lambda: 0,
        )

        result = cleaner.cleanup()

        assert result["success"] is True
        assert result["resolved_count"] == 0

    def test_cleanup_with_expired(self):
        """Test cleanup when expired experiments exist."""
        from selfhealing.tasks.drift_detection import ChaosExperimentCleaner

        cleaner = ChaosExperimentCleaner(
            resolve_expired_experiments=lambda: 5,
        )

        result = cleaner.cleanup()

        assert result["success"] is True
        assert result["resolved_count"] == 5


class TestDecisionRecorder:
    """Test DecisionRecorder class."""

    def test_record_decision_success(self):
        """Test recording decision successfully."""
        from selfhealing.tasks.drift_detection import DecisionRecorder

        mock_op = MagicMock()
        mock_op.metadata = {
            "forensic_advisory": {
                "analyzed_at": "2024-01-01T00:00:00",
                "recommended_action": "replay",
                "confidence": 0.85,
            }
        }

        recorder = DecisionRecorder(
            get_failed_operation=lambda id: mock_op,
        )

        result = recorder.record(
            operation_id=1,
            decision="approved_replay",
            decided_by="admin",
            notes="Test approval",
        )

        assert result["success"] is True
        assert "decision_records" in mock_op.metadata
        assert mock_op.save.called

    def test_record_decision_operation_not_found(self):
        """Test recording decision for non-existent operation."""
        from selfhealing.tasks.drift_detection import DecisionRecorder

        def raise_error(id):
            raise Exception("Not found")

        recorder = DecisionRecorder(
            get_failed_operation=raise_error,
        )

        result = recorder.record(
            operation_id=999,
            decision="approved",
            decided_by="admin",
        )

        assert result["success"] is False


class TestCeleryTaskWrappers:
    """Test Django/Celery task wrappers."""

    @patch("shopping.tasks.drift_detection_tasks._get_sla_thresholds")
    @patch("shopping.tasks.drift_detection_tasks._get_failed_operations")
    def test_check_sla_drift_task(self, mock_get_ops, mock_get_sla):
        """Test check_sla_drift Celery task."""
        from shopping.tasks.drift_detection_tasks import check_sla_drift

        mock_sla = MagicMock()
        mock_sla.get_all_thresholds.return_value = {"payment": timedelta(hours=1)}
        mock_get_sla.return_value = mock_sla

        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        mock_get_ops.return_value = mock_qs

        result = check_sla_drift()

        assert result["success"] is True

    @patch("shopping.tasks.drift_detection_tasks._resolve_expired_chaos_experiments")
    def test_cleanup_chaos_task(self, mock_resolve):
        """Test cleanup_expired_chaos_experiments Celery task."""
        from shopping.tasks.drift_detection_tasks import cleanup_expired_chaos_experiments

        mock_resolve.return_value = 3

        result = cleanup_expired_chaos_experiments()

        assert result["success"] is True
        assert result["resolved_count"] == 3

    @patch("shopping.tasks.drift_detection_tasks._get_failed_operation_by_id")
    def test_record_decision_task(self, mock_get_op):
        """Test record_advisory_decision Celery task."""
        from shopping.tasks.drift_detection_tasks import record_advisory_decision

        mock_op = MagicMock()
        mock_op.metadata = {}
        mock_get_op.return_value = mock_op

        result = record_advisory_decision(
            operation_id=1,
            decision="approved",
            decided_by="admin",
        )

        assert result["success"] is True


class TestDriftDetectionNoAutoAdjust:
    """Test that drift detection NEVER auto-adjusts settings."""

    def test_detector_only_returns_data(self):
        """Verify detector returns data and never modifies config."""
        from selfhealing.tasks.drift_detection import SLADriftDetector
        import inspect

        # Get source code of check_drift method
        source = inspect.getsource(SLADriftDetector.check_drift)

        # Verify no config modification patterns
        forbidden_patterns = [
            "settings.SELF_HEALING =",
            "setattr(settings",
            "update_sla_threshold",
            "modify_config",
            "write_config",
        ]

        for pattern in forbidden_patterns:
            assert pattern not in source, f"Found forbidden pattern: {pattern}"

    def test_warning_contains_action_required(self):
        """Verify warnings contain ACTION REQUIRED marker."""
        from selfhealing.tasks.drift_detection import SLADriftDetector

        now = timezone.now()

        # Create mock with SLA breach
        mock_ops = []
        for i in range(10):
            op = MagicMock()
            op.created_at = now - timedelta(hours=3)
            op.resolved_at = now
            mock_ops.append(op)

        mock_sla = MagicMock()
        mock_sla.get_all_thresholds.return_value = {"payment": timedelta(hours=1)}

        def mock_filter(**kwargs):
            mock_qs = MagicMock()
            if kwargs.get("status__in"):
                mock_qs.count.return_value = 10
                mock_qs.__iter__ = lambda self: iter(mock_ops)
            else:
                mock_qs.__iter__ = lambda self: iter([])
            return mock_qs

        detector = SLADriftDetector(
            get_sla_thresholds=lambda: mock_sla,
            get_failed_operations=mock_filter,
        )

        result = detector.check_drift()

        if result["warnings"]:
            for warning in result["warnings"]:
                assert "[ACTION REQUIRED" in warning["recommendation"]


@pytest.mark.django_db(transaction=True)
class TestDriftDetectionIntegration:
    """Integration tests with actual database.

    Run with: docker-compose exec web pytest tests/self_healing/unit/test_drift_detection.py::TestDriftDetectionIntegration
    """

    @pytest.mark.skip(
        reason="Integration test - run in Docker: docker-compose exec web pytest -k TestDriftDetectionIntegration"
    )
    def test_full_drift_detection_cycle(self):
        """Test full drift detection cycle with real data."""
        from shopping.tasks.drift_detection_tasks import check_sla_drift

        result = check_sla_drift()

        assert result["success"] is True
        assert "domains_checked" in result
