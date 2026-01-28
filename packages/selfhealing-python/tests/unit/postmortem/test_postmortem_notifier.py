"""
Postmortem Notifier 테스트.

PostmortemNotifier 및 SlackBlockKitBuilder 테스트입니다.
"""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest


class TestPostmortemNotificationPayload:
    """PostmortemNotificationPayload 테스트."""

    def test_to_dict(self):
        """to_dict 동작 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationPayload,
        )

        payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test Postmortem",
            service_name="payment_service",
            duration_seconds=1800.0,
            generation_type="auto",
            severity="high",
            namespace="production",
        )

        result = payload.to_dict()

        assert result["incident_id"] == "PM-001"
        assert result["title"] == "Test Postmortem"
        assert result["service_name"] == "payment_service"
        assert result["duration_seconds"] == 1800.0
        assert result["generation_type"] == "auto"
        assert result["severity"] == "high"
        assert result["namespace"] == "production"


class TestPostmortemNotificationConfig:
    """PostmortemNotificationConfig 테스트."""

    def test_from_env_default_values(self):
        """기본값 로드 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            config = PostmortemNotificationConfig.from_env()

        assert config.enabled is True
        assert config.slack_webhook_url == ""
        assert config.channels == ["slack"]

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_NOTIFICATION_ENABLED": "false",
            "POSTMORTEM_SLACK_WEBHOOK": "https://hooks.slack.com/test",
            "POSTMORTEM_NOTIFICATION_CHANNELS": "slack,email,webhook",
        },
    )
    def test_from_env_custom_values(self):
        """사용자 설정값 로드 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
        )

        config = PostmortemNotificationConfig.from_env()

        assert config.enabled is False
        assert config.slack_webhook_url == "https://hooks.slack.com/test"
        assert config.channels == ["slack", "email", "webhook"]


class TestSlackBlockKitBuilder:
    """SlackBlockKitBuilder 테스트."""

    def test_build_postmortem_message_structure(self):
        """Block Kit 메시지 구조 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationPayload,
            SlackBlockKitBuilder,
        )

        payload = PostmortemNotificationPayload(
            incident_id="PM-20260128-001",
            title="[Postmortem] PM-20260128-001 자동 생성",
            service_name="payment_service",
            duration_seconds=1800.0,
            generation_type="auto",
            deep_links={
                "postmortem_url": "https://pm.internal/PM-20260128-001",
                "dashboard_url": "https://grafana.internal/d/cb",
                "runbook_url": "https://docs.internal/runbook",
            },
            severity="high",
            namespace="production",
        )

        message = SlackBlockKitBuilder.build_postmortem_message(payload)

        assert "blocks" in message
        assert "text" in message  # Fallback text

        blocks = message["blocks"]

        # Header block
        header_block = next((b for b in blocks if b["type"] == "header"), None)
        assert header_block is not None
        assert "PM-20260128-001" in header_block["text"]["text"]

        # Section block with summary
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) >= 1

        # Actions block with buttons
        actions_block = next((b for b in blocks if b["type"] == "actions"), None)
        assert actions_block is not None
        buttons = actions_block["elements"]
        assert len(buttons) >= 1

        # 상세 보기 버튼 (primary style)
        detail_button = next((b for b in buttons if "상세 보기" in b["text"]["text"]), None)
        assert detail_button is not None
        assert detail_button["style"] == "primary"
        assert detail_button["url"] == "https://pm.internal/PM-20260128-001"

        # Context block
        context_block = next((b for b in blocks if b["type"] == "context"), None)
        assert context_block is not None

    def test_build_postmortem_message_emoji_by_type(self):
        """생성 유형별 이모지 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationPayload,
            SlackBlockKitBuilder,
        )

        # Auto
        auto_payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test",
            service_name="svc",
            duration_seconds=100,
            generation_type="auto",
        )
        auto_msg = SlackBlockKitBuilder.build_postmortem_message(auto_payload)
        assert "🔄" in auto_msg["text"]

        # Group
        group_payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test",
            service_name="svc",
            duration_seconds=100,
            generation_type="group",
        )
        group_msg = SlackBlockKitBuilder.build_postmortem_message(group_payload)
        assert "📦" in group_msg["text"]

        # Emergency
        emergency_payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test",
            service_name="svc",
            duration_seconds=100,
            generation_type="emergency",
        )
        emergency_msg = SlackBlockKitBuilder.build_postmortem_message(emergency_payload)
        assert "🚨" in emergency_msg["text"]

    def test_build_postmortem_message_with_affected_services(self):
        """그룹 Postmortem의 영향받은 서비스 표시."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationPayload,
            SlackBlockKitBuilder,
        )

        payload = PostmortemNotificationPayload(
            incident_id="PM-GROUP-001",
            title="Group Postmortem",
            service_name="payment_service",
            duration_seconds=3600,
            generation_type="group",
            affected_services=[
                "payment_service",
                "order_service",
                "inventory_service",
                "shipping_service",
                "notification_service",
                "analytics_service",
            ],
        )

        message = SlackBlockKitBuilder.build_postmortem_message(payload)
        blocks_json = json.dumps(message["blocks"], ensure_ascii=False)

        # 5개까지 표시되고 "외 1개"가 있어야 함
        assert "payment_service" in blocks_json
        assert "외 1개" in blocks_json

    def test_build_postmortem_message_max_buttons(self):
        """최대 버튼 수 제한 (4개)."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationPayload,
            SlackBlockKitBuilder,
        )

        payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test",
            service_name="svc",
            duration_seconds=100,
            generation_type="auto",
            deep_links={
                "postmortem_url": "https://pm.internal/PM-001",
                "dashboard_url": "https://grafana.internal",
                "runbook_url": "https://docs.internal",
                "audit_log_url": "https://audit.internal",
                "metrics_url": "https://prometheus.internal",  # 5번째 - 제외되어야 함
            },
        )

        message = SlackBlockKitBuilder.build_postmortem_message(payload)
        actions_block = next((b for b in message["blocks"] if b["type"] == "actions"), None)

        assert actions_block is not None
        assert len(actions_block["elements"]) <= 4


class TestPostmortemNotifier:
    """PostmortemNotifier 테스트."""

    @pytest.fixture(autouse=True)
    def reset_notifier(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.notifier import reset_postmortem_notifier

        reset_postmortem_notifier()
        yield
        reset_postmortem_notifier()

    def test_notify_disabled(self):
        """알림 비활성화 시 바로 True 반환."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        config = PostmortemNotificationConfig(enabled=False)
        notifier = PostmortemNotifier(config=config)

        result = notifier.notify_postmortem_created(
            incident_id="PM-001",
            service_name="payment",
            duration_seconds=100,
        )

        assert result is True

    def test_notify_without_slack_webhook(self):
        """Slack Webhook 미설정 시 성공으로 간주."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        config = PostmortemNotificationConfig(
            enabled=True,
            slack_webhook_url="",
            channels=["slack"],
        )
        notifier = PostmortemNotifier(config=config)

        result = notifier.notify_postmortem_created(
            incident_id="PM-001",
            service_name="payment",
            duration_seconds=100,
        )

        assert result is True

    @mock.patch("urllib.request.urlopen")
    def test_notify_slack_success(self, mock_urlopen):
        """Slack Webhook 발송 성공."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        # Mock 응답 설정
        mock_response = mock.MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        config = PostmortemNotificationConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/test",
            channels=["slack"],
        )
        notifier = PostmortemNotifier(config=config)

        result = notifier.notify_postmortem_created(
            incident_id="PM-001",
            service_name="payment",
            duration_seconds=1800,
            deep_links={"postmortem_url": "https://pm.internal/PM-001"},
        )

        assert result is True
        mock_urlopen.assert_called_once()

        # 요청 데이터 검증
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        assert request.full_url == "https://hooks.slack.com/test"
        assert request.method == "POST"

    @mock.patch("urllib.request.urlopen")
    def test_notify_slack_failure(self, mock_urlopen):
        """Slack Webhook 발송 실패."""
        import urllib.error

        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        config = PostmortemNotificationConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/test",
            channels=["slack"],
        )
        notifier = PostmortemNotifier(config=config)

        result = notifier.notify_postmortem_created(
            incident_id="PM-001",
            service_name="payment",
            duration_seconds=100,
        )

        assert result is False

    @mock.patch("urllib.request.urlopen")
    def test_send_webhook(self, mock_urlopen):
        """임의 Webhook URL 발송."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotificationPayload,
            PostmortemNotifier,
        )

        mock_response = mock.MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = mock.MagicMock(return_value=mock_response)
        mock_response.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        config = PostmortemNotificationConfig(enabled=True)
        notifier = PostmortemNotifier(config=config)

        payload = PostmortemNotificationPayload(
            incident_id="PM-001",
            title="Test",
            service_name="payment",
            duration_seconds=100,
            generation_type="auto",
        )

        result = notifier.send_webhook(
            webhook_url="https://custom.webhook.internal",
            payload=payload,
        )

        assert result is True
        mock_urlopen.assert_called_once()

    def test_is_enabled(self):
        """is_enabled 메서드 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        enabled_config = PostmortemNotificationConfig(enabled=True)
        disabled_config = PostmortemNotificationConfig(enabled=False)

        enabled_notifier = PostmortemNotifier(config=enabled_config)
        disabled_notifier = PostmortemNotifier(config=disabled_config)

        assert enabled_notifier.is_enabled() is True
        assert disabled_notifier.is_enabled() is False

    def test_get_config(self):
        """get_config 메서드 검증."""
        from selfhealing.services.postmortem.notifier import (
            PostmortemNotificationConfig,
            PostmortemNotifier,
        )

        config = PostmortemNotificationConfig(
            enabled=True,
            slack_webhook_url="https://hooks.slack.com/test",
            channels=["slack", "email"],
        )
        notifier = PostmortemNotifier(config=config)

        result = notifier.get_config()

        assert result["enabled"] is True
        assert result["slack_configured"] is True
        assert result["channels"] == ["slack", "email"]


class TestPostmortemNotifierSingleton:
    """싱글톤 패턴 테스트."""

    @pytest.fixture(autouse=True)
    def reset_notifier(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.notifier import reset_postmortem_notifier

        reset_postmortem_notifier()
        yield
        reset_postmortem_notifier()

    def test_get_returns_same_instance(self):
        """get_postmortem_notifier는 동일 인스턴스 반환."""
        from selfhealing.services.postmortem.notifier import get_postmortem_notifier

        notifier1 = get_postmortem_notifier()
        notifier2 = get_postmortem_notifier()

        assert notifier1 is notifier2

    def test_reset_creates_new_instance(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.services.postmortem.notifier import (
            get_postmortem_notifier,
            reset_postmortem_notifier,
        )

        notifier1 = get_postmortem_notifier()
        reset_postmortem_notifier()
        notifier2 = get_postmortem_notifier()

        assert notifier1 is not notifier2
