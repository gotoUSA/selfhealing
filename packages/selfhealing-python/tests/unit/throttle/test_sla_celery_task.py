"""
SLA 알림 Celery 태스크 단위 테스트.

대상: selfhealing/adapters/celery/tasks/sla_notification.py
- send_sla_notification 태스크 디스패치 로직
- notification_type별 핸들러 라우팅
- 알 수 없는 notification_type 처리
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSendSlaNotificationTask:
    """send_sla_notification Celery 태스크 테스트."""

    def test_dispatch_warning(self):
        """notification_type='warning' → _send_sla_warning_sync 호출."""
        with patch("selfhealing.services.throttle.sla_notification._send_sla_warning_sync") as mock_fn:
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            event_data = {"current_rtt_ms": 250.0, "service_name": "payment"}
            result = send_sla_notification(event_data=event_data, notification_type="warning")

            mock_fn.assert_called_once_with(event_data)
            assert result["status"] == "sent"
            assert result["type"] == "warning"

    def test_dispatch_critical(self):
        """notification_type='critical' → _send_sla_critical_sync 호출."""
        with patch("selfhealing.services.throttle.sla_notification._send_sla_critical_sync") as mock_fn:
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            event_data = {"current_rtt_ms": 600.0, "service_name": "order"}
            result = send_sla_notification(event_data=event_data, notification_type="critical")

            mock_fn.assert_called_once_with(event_data)
            assert result["status"] == "sent"
            assert result["type"] == "critical"

    def test_dispatch_recovered(self):
        """notification_type='recovered' → _send_limit_recovered_sync 호출."""
        with patch("selfhealing.services.throttle.sla_notification._send_limit_recovered_sync") as mock_fn:
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            event_data = {"previous_limit": 70, "new_limit": 100, "rtt_ms": 50.0}
            result = send_sla_notification(event_data=event_data, notification_type="recovered")

            mock_fn.assert_called_once_with(event_data)
            assert result["status"] == "sent"
            assert result["type"] == "recovered"

    def test_unknown_type_no_handler_called(self):
        """알 수 없는 notification_type → 핸들러 미호출."""
        with (
            patch("selfhealing.services.throttle.sla_notification._send_sla_warning_sync") as mock_warn,
            patch("selfhealing.services.throttle.sla_notification._send_sla_critical_sync") as mock_crit,
            patch("selfhealing.services.throttle.sla_notification._send_limit_recovered_sync") as mock_recov,
        ):
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            result = send_sla_notification(event_data={"test": True}, notification_type="unknown_type")

            mock_warn.assert_not_called()
            mock_crit.assert_not_called()
            mock_recov.assert_not_called()
            assert result["status"] == "sent"
            assert result["type"] == "unknown_type"

    def test_return_dict_structure(self):
        """반환값 딕셔너리 구조 확인."""
        with patch("selfhealing.services.throttle.sla_notification._send_sla_warning_sync"):
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            result = send_sla_notification(event_data={}, notification_type="warning")

            assert isinstance(result, dict)
            assert "status" in result
            assert "type" in result

    def test_event_data_passed_through(self):
        """event_data가 핸들러에 그대로 전달."""
        with patch("selfhealing.services.throttle.sla_notification._send_sla_critical_sync") as mock_fn:
            from selfhealing.adapters.celery.tasks.sla_notification import (
                send_sla_notification,
            )

            complex_data = {
                "current_rtt_ms": 500.0,
                "threshold_ms": 400,
                "service_name": "auth",
                "nested": {"key": "value"},
            }
            send_sla_notification(event_data=complex_data, notification_type="critical")

            mock_fn.assert_called_once_with(complex_data)


class TestSendSlaNotificationTaskAttributes:
    """Celery 태스크 속성(데코레이터) 테스트."""

    def test_task_name(self):
        """태스크 이름 확인."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.name == "selfhealing.adapters.celery.tasks.send_sla_notification"

    def test_task_max_retries(self):
        """max_retries=3."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.max_retries == 3

    def test_task_default_retry_delay(self):
        """default_retry_delay=30."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.default_retry_delay == 30

    def test_task_acks_late(self):
        """acks_late=True (Graceful Shutdown)."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.acks_late is True

    def test_task_queue(self):
        """queue='selfhealing'."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.queue == "selfhealing"

    def test_task_time_limit(self):
        """time_limit=60."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.time_limit == 60

    def test_task_soft_time_limit(self):
        """soft_time_limit=55."""
        from selfhealing.adapters.celery.tasks.sla_notification import (
            send_sla_notification,
        )

        assert send_sla_notification.soft_time_limit == 55
