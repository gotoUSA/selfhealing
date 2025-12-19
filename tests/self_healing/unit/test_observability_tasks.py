"""
Tests for Self-Healing Observability Celery Tasks

Tests for metric collection and SLA breach checking tasks.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
"""

from unittest.mock import MagicMock, patch

import pytest

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db


@pytest.mark.django_db
class TestCollectSelfHealingMetricsTask:
    """Tests for the collect_self_healing_metrics Celery task."""

    @patch("selfhealing.services.collect_all_metrics")
    def test_collect_metrics_success(self, mock_collect):
        """
        Purpose:
            Verify metric collection task executes successfully.
        """
        from shopping.tasks.self_healing_tasks import collect_self_healing_metrics

        mock_collect.return_value = {
            "dlq_pending_by_domain": {"payment": 5},
            "dlq_by_status": {"pending": 10},
            "circuit_breaker_states": {},
            "retry_success_rates": {"payment": 95.0},
            "collected_at": "2025-12-08T10:00:00",
        }

        result = collect_self_healing_metrics()

        assert result["success"] is True
        assert "dlq_pending_by_domain" in result
        mock_collect.assert_called_once()

    @patch("selfhealing.services.collect_all_metrics")
    def test_collect_metrics_handles_exception(self, mock_collect):
        """
        Purpose:
            Verify task handles exceptions gracefully.
        """
        from shopping.tasks.self_healing_tasks import collect_self_healing_metrics

        mock_collect.side_effect = Exception("Database connection error")

        result = collect_self_healing_metrics()

        assert result["success"] is False
        assert "error" in result


@pytest.mark.django_db
class TestCheckAndReportSLABreachesTask:
    """Tests for the check_and_report_sla_breaches Celery task."""

    @patch("selfhealing.services.record_sla_breach")
    @patch("selfhealing.services.get_dlq_service")
    def test_no_sla_breaches(self, mock_get_service, mock_record):
        """
        Purpose:
            Verify task returns correctly when no SLA breaches exist.
        """
        from shopping.tasks.self_healing_tasks import check_and_report_sla_breaches

        mock_service = MagicMock()
        mock_service.get_sla_breached_entries.return_value = []
        mock_get_service.return_value = mock_service

        result = check_and_report_sla_breaches()

        assert result["success"] is True
        assert result["total_breaches"] == 0
        mock_record.assert_not_called()

    @patch("selfhealing.services.record_sla_breach")
    @patch("selfhealing.services.get_dlq_service")
    def test_sla_breaches_detected(self, mock_get_service, mock_record):
        """
        Purpose:
            Verify SLA breaches are counted and recorded correctly.
        """
        from shopping.tasks.self_healing_tasks import check_and_report_sla_breaches

        # Create mock breached entries
        mock_entry1 = MagicMock()
        mock_entry1.domain = "payment"

        mock_entry2 = MagicMock()
        mock_entry2.domain = "payment"

        mock_entry3 = MagicMock()
        mock_entry3.domain = "point"

        mock_service = MagicMock()
        mock_service.get_sla_breached_entries.return_value = [
            mock_entry1,
            mock_entry2,
            mock_entry3,
        ]
        mock_get_service.return_value = mock_service

        result = check_and_report_sla_breaches()

        assert result["success"] is True
        assert result["total_breaches"] == 3
        assert result["breaches_by_domain"]["payment"] == 2
        assert result["breaches_by_domain"]["point"] == 1
        assert mock_record.call_count == 3

    @patch("selfhealing.services.get_dlq_service")
    def test_task_handles_exception(self, mock_get_service):
        """
        Purpose:
            Verify task handles exceptions gracefully.
        """
        from shopping.tasks.self_healing_tasks import check_and_report_sla_breaches

        mock_get_service.side_effect = Exception("Service unavailable")

        result = check_and_report_sla_breaches()

        assert result["success"] is False
        assert "error" in result


class TestSelfHealingTaskExports:
    """Tests for task module exports."""

    def test_task_exports_in_init(self):
        """
        Purpose:
            Verify new tasks are properly exported from tasks module.
        """
        from shopping.tasks import (
            check_and_report_sla_breaches,
            collect_self_healing_metrics,
        )

        assert callable(collect_self_healing_metrics)
        assert callable(check_and_report_sla_breaches)

    def test_task_names_configured(self):
        """
        Purpose:
            Verify tasks have proper Celery task names.
        """
        from shopping.tasks.self_healing_tasks import (
            check_and_report_sla_breaches,
            collect_self_healing_metrics,
        )

        assert collect_self_healing_metrics.name == "shopping.tasks.self_healing_tasks.collect_self_healing_metrics"
        assert check_and_report_sla_breaches.name == "shopping.tasks.self_healing_tasks.check_and_report_sla_breaches"

    def test_task_queues_configured(self):
        """
        Purpose:
            Verify tasks are assigned to correct queues.
        """
        from shopping.tasks.self_healing_tasks import (
            check_and_report_sla_breaches,
            collect_self_healing_metrics,
        )

        # Both tasks should be on monitoring queue
        assert collect_self_healing_metrics.queue == "monitoring"
        assert check_and_report_sla_breaches.queue == "monitoring"
