"""
Chaos Notification Tests

Tests for Phase 0 (NotificationCategory.CHAOS) and Phase 1 (ChaosNotificationService).

Test Categories:
- Phase 0 (3 tests): NotificationCategory.CHAOS enum, cooldown, dedup
- Phase 1 (8 tests): ChaosActionableUrls, ChaosNotificationService

Reference: docs/self_healing/middleware_system/24_CHAOS_INTEGRATION_PLAN.md §8.1, §8.2
"""

import pytest
from unittest.mock import patch, MagicMock


# =============================================================================
# Phase 0 Tests: NotificationCategory.CHAOS (3 tests)
# =============================================================================


class TestNotificationCategory:
    """Phase 0 tests for NotificationCategory.CHAOS."""

    def test_chaos_category_exists(self):
        """Test that CHAOS enum exists in NotificationCategory."""
        from selfhealing.services.unified_notification import NotificationCategory

        # CHAOS 카테고리가 존재해야 함
        assert hasattr(NotificationCategory, "CHAOS")
        assert NotificationCategory.CHAOS.value == "chaos"

    def test_chaos_cooldown_configured(self):
        """Test that CHAOS cooldown is 300 seconds (5 minutes)."""
        from selfhealing.services.unified_notification import (
            RoutingPolicy,
            NotificationCategory,
        )

        policy = RoutingPolicy()

        # CHAOS 카테고리의 cooldown은 300초 (5분)
        cooldown = policy.get_cooldown(NotificationCategory.CHAOS)
        assert cooldown == 300

        # cooldown_seconds 딕셔너리에 직접 접근해서도 확인
        assert NotificationCategory.CHAOS in policy.cooldown_seconds
        assert policy.cooldown_seconds[NotificationCategory.CHAOS] == 300

    def test_chaos_dedup_key_independent(self):
        """Test that CHAOS notifications have independent dedup keys."""
        from selfhealing.services.unified_notification import (
            NotificationPayload,
            NotificationCategory,
            NotificationPriority,
        )

        # CHAOS 알림 생성
        chaos_payload = NotificationPayload(
            title="Chaos Experiment Started",
            message="Latency injection on payment-api",
            priority=NotificationPriority.INFO,
            category=NotificationCategory.CHAOS,
            source="chaos_experiment",
            dedup_key="chaos:exp-001:started",
        )

        # OPERATIONS 알림 생성
        ops_payload = NotificationPayload(
            title="Operation Completed",
            message="Some operation",
            priority=NotificationPriority.MEDIUM,
            category=NotificationCategory.OPERATIONS,
            source="drift_detection",
            dedup_key="ops:drift:completed",
        )

        # dedup_key가 독립적으로 설정됨
        assert chaos_payload.dedup_key == "chaos:exp-001:started"
        assert ops_payload.dedup_key == "ops:drift:completed"
        assert chaos_payload.dedup_key != ops_payload.dedup_key

        # 카테고리도 다름
        assert chaos_payload.category == NotificationCategory.CHAOS
        assert ops_payload.category == NotificationCategory.OPERATIONS


# =============================================================================
# Phase 1 Tests: ChaosActionableUrls (4 tests)
# =============================================================================


class TestChaosActionableUrls:
    """Phase 1 tests for ChaosActionableAlertUrlBuilder."""

    def test_admin_stop_url_format(self):
        """Test that admin stop URL has correct format."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableAlertUrlBuilder,
        )

        builder = ChaosActionableAlertUrlBuilder()
        urls = builder.build_experiment_alert_urls(
            experiment_id="exp-123",
            target_service="payment-api",
        )

        # admin_stop_url이 존재하고 올바른 형식
        assert urls.admin_stop_url is not None
        assert "exp-123" in urls.admin_stop_url
        assert "action=stop" in urls.admin_stop_url

    def test_emergency_stop_url_requires_confirm(self):
        """Test that emergency stop URL includes confirm parameter."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableAlertUrlBuilder,
        )

        builder = ChaosActionableAlertUrlBuilder()
        emergency_url = builder.build_emergency_stop_url(reason="emergency")

        # confirm=required 파라미터가 포함되어야 함
        assert "confirm=required" in emergency_url
        assert "action=kill_all" in emergency_url
        assert "reason=emergency" in emergency_url

    def test_urls_has_any_url_method(self):
        """Test has_any_url method works correctly."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableUrls,
        )

        # URL이 하나라도 있는 경우
        urls_with_data = ChaosActionableUrls(
            admin_stop_url="/api/chaos/stop",
        )
        assert urls_with_data.has_any_url() is True

        # URL이 없는 경우
        urls_empty = ChaosActionableUrls()
        assert urls_empty.has_any_url() is False

    def test_urls_to_dict_serialization(self):
        """Test ChaosActionableUrls to_dict serialization."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableUrls,
        )

        urls = ChaosActionableUrls(
            dashboard_url="https://grafana.example.com/chaos",
            admin_stop_url="/api/chaos/exp-1/stop",
            admin_detail_url="/api/chaos/exp-1/detail",
            runbook_url="https://docs.example.com/runbook",
        )

        result = urls.to_dict()

        assert result["dashboard_url"] == "https://grafana.example.com/chaos"
        assert result["admin_stop_url"] == "/api/chaos/exp-1/stop"
        assert result["admin_detail_url"] == "/api/chaos/exp-1/detail"
        assert result["runbook_url"] == "https://docs.example.com/runbook"


# =============================================================================
# Phase 1 Tests: ChaosNotificationService (4 tests)
# =============================================================================


class TestChaosNotification:
    """Phase 1 tests for ChaosNotificationService."""

    def test_experiment_start_notification(self):
        """Test that experiment start notification is sent correctly."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            result = send_chaos_experiment_alert(
                experiment_id="exp-001",
                experiment_type="latency_injection",
                target_service="payment-api",
                event_type="started",
                details={"duration_seconds": 60},
            )

            assert result is True
            mock_instance.notify.assert_called_once()

            # NotificationPayload 확인
            call_args = mock_instance.notify.call_args
            payload = call_args[0][0]
            assert "Started" in payload.title
            assert payload.category.value == "chaos"

    def test_experiment_end_notification(self):
        """Test that experiment end notification is sent correctly."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            result = send_chaos_experiment_alert(
                experiment_id="exp-001",
                experiment_type="latency_injection",
                target_service="payment-api",
                event_type="stopped",
                details={"success": True},
            )

            assert result is True
            mock_instance.notify.assert_called_once()

            payload = mock_instance.notify.call_args[0][0]
            assert "Completed" in payload.title

    def test_experiment_error_notification(self):
        """Test that experiment error notification is sent with HIGH priority."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert
        from selfhealing.services.unified_notification import NotificationPriority

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            result = send_chaos_experiment_alert(
                experiment_id="exp-001",
                experiment_type="failure_injection",
                target_service="payment-api",
                event_type="failed",
                details={"error": "Unexpected failure"},
            )

            assert result is True

            payload = mock_instance.notify.call_args[0][0]
            assert "Failed" in payload.title
            assert payload.priority == NotificationPriority.HIGH

    def test_notification_with_admin_url(self):
        """Test that notification includes admin URL in metadata."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        with patch(
            "selfhealing.services.unified_notification.get_unified_notification_manager"
        ) as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            send_chaos_experiment_alert(
                experiment_id="exp-001",
                experiment_type="latency_injection",
                target_service="payment-api",
                event_type="started",
                details={},
            )

            payload = mock_instance.notify.call_args[0][0]

            # metadata에 admin_url이 포함되어야 함
            assert "admin_url" in payload.metadata
            assert payload.metadata["admin_url"] is not None
            assert "exp-001" in payload.metadata["admin_url"]
