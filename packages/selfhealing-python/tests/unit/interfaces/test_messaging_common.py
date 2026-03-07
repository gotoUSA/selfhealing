"""
Common Messaging Types 단위 테스트.

테스트 대상: interfaces/messaging_common.py
- MessageSeverity: 통합 심각도 열거형
- MessageChannel: 공유 채널 열거형

테스트 대상: interfaces/notification.py
- NotificationSeverity/NotificationChannel: 하위 호환 별칭

테스트 대상: interfaces/alert_adapter.py
- AlertSeverity: 하위 호환 (별도 Enum 유지)
"""

from __future__ import annotations

from selfhealing.interfaces.alert_adapter import AlertSeverity
from selfhealing.interfaces.messaging_common import MessageChannel, MessageSeverity
from selfhealing.interfaces.notification import (
    NotificationChannel,
    NotificationSeverity,
)

# =============================================================================
# MessageSeverity — 계약 검증
# =============================================================================


class TestMessageSeverityContract:
    """MessageSeverity 열거형 멤버 계약 검증."""

    def test_has_five_members(self):
        """MessageSeverity는 5개 멤버를 가진다."""
        assert len(MessageSeverity) == 5

    def test_critical_value(self):
        """CRITICAL 값: 'critical'."""
        assert MessageSeverity.CRITICAL.value == "critical"

    def test_high_value(self):
        """HIGH 값: 'high'."""
        assert MessageSeverity.HIGH.value == "high"

    def test_medium_value(self):
        """MEDIUM 값: 'medium'."""
        assert MessageSeverity.MEDIUM.value == "medium"

    def test_low_value(self):
        """LOW 값: 'low'."""
        assert MessageSeverity.LOW.value == "low"

    def test_info_value(self):
        """INFO 값: 'info'."""
        assert MessageSeverity.INFO.value == "info"

    def test_is_str_enum(self):
        """MessageSeverity는 str을 상속한다."""
        assert isinstance(MessageSeverity.CRITICAL, str)


# =============================================================================
# MessageChannel — 계약 검증
# =============================================================================


class TestMessageChannelContract:
    """MessageChannel 열거형 멤버 계약 검증."""

    def test_has_eight_members(self):
        """MessageChannel은 8개 멤버를 가진다."""
        assert len(MessageChannel) == 8

    def test_slack_value(self):
        """SLACK 값: 'slack'."""
        assert MessageChannel.SLACK.value == "slack"

    def test_teams_value(self):
        """TEAMS 값: 'teams'."""
        assert MessageChannel.TEAMS.value == "teams"

    def test_pagerduty_value(self):
        """PAGERDUTY 값: 'pagerduty'."""
        assert MessageChannel.PAGERDUTY.value == "pagerduty"

    def test_email_value(self):
        """EMAIL 값: 'email'."""
        assert MessageChannel.EMAIL.value == "email"

    def test_webhook_value(self):
        """WEBHOOK 값: 'webhook'."""
        assert MessageChannel.WEBHOOK.value == "webhook"

    def test_sms_value(self):
        """SMS 값: 'sms'."""
        assert MessageChannel.SMS.value == "sms"

    def test_stdout_value(self):
        """STDOUT 값: 'stdout'."""
        assert MessageChannel.STDOUT.value == "stdout"

    def test_file_value(self):
        """FILE 값: 'file'."""
        assert MessageChannel.FILE.value == "file"

    def test_is_str_enum(self):
        """MessageChannel은 str을 상속한다."""
        assert isinstance(MessageChannel.SLACK, str)


# =============================================================================
# Backward-compatible Aliases — 동작 검증
# =============================================================================


class TestNotificationAliasesBehavior:
    """NotificationSeverity/NotificationChannel 하위 호환 별칭 검증."""

    def test_notification_severity_is_message_severity(self):
        """NotificationSeverity는 MessageSeverity와 동일하다."""
        assert NotificationSeverity is MessageSeverity

    def test_notification_channel_is_message_channel(self):
        """NotificationChannel은 MessageChannel과 동일하다."""
        assert NotificationChannel is MessageChannel

    def test_notification_severity_members_accessible(self):
        """NotificationSeverity로 모든 멤버에 접근 가능하다."""
        assert NotificationSeverity.CRITICAL.value == "critical"
        assert NotificationSeverity.HIGH.value == "high"
        assert NotificationSeverity.MEDIUM.value == "medium"
        assert NotificationSeverity.LOW.value == "low"
        assert NotificationSeverity.INFO.value == "info"


class TestAlertSeverityBackwardCompatBehavior:
    """AlertSeverity 하위 호환 검증."""

    def test_alert_severity_has_three_members(self):
        """AlertSeverity는 3개 멤버(CRITICAL, WARNING, INFO)를 가진다."""
        assert len(AlertSeverity) == 3

    def test_alert_severity_values_overlap_with_message_severity(self):
        """AlertSeverity의 값은 MessageSeverity와 겹친다."""
        assert AlertSeverity.CRITICAL.value == MessageSeverity.CRITICAL.value
        assert AlertSeverity.INFO.value == MessageSeverity.INFO.value

    def test_alert_severity_is_separate_enum(self):
        """AlertSeverity는 MessageSeverity와 별도 Enum이다."""
        assert AlertSeverity is not MessageSeverity
