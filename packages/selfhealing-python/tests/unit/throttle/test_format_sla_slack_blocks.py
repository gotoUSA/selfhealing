"""
format_sla_slack_blocks Slack Block Kit 포맷 단위 테스트.

대상: selfhealing/services/unified_notification.py format_sla_slack_blocks()
- Slack Block Kit 구조 (header/section/actions)
- RTT/Threshold/Limit/Service 필드
- RTT 변화율 / Region 동적 필드
- Actionable 버튼 (URL 빌더 연동)
- Priority별 emoji 매핑
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestFormatSlaSlackBlocksStructure:
    """기본 Slack Block Kit 구조 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def _make_payload(self, **metadata_overrides):
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        metadata = {
            "rtt_ms": 250.0,
            "threshold_ms": 200,
            "current_limit": 80,
            "service_name": "payment",
        }
        metadata.update(metadata_overrides)

        return NotificationPayload(
            title="SLA Warning: RTT Exceeded",
            message="RTT 250ms exceeded 200ms threshold",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata=metadata,
        )

    def test_returns_blocks_key(self):
        """결과에 'blocks' 키 존재."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)
        assert "blocks" in result

    def test_minimum_three_blocks(self):
        """최소 3개 블록 (header, fields, details)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)
        assert len(result["blocks"]) >= 3

    def test_header_block_type(self):
        """첫 번째 블록이 header 타입."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)
        assert result["blocks"][0]["type"] == "header"

    def test_header_contains_title(self):
        """Header에 payload.title 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload()
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        assert payload.title in result["blocks"][0]["text"]["text"]

    def test_fields_section(self):
        """두 번째 블록에 fields 존재."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)
        fields_block = result["blocks"][1]
        assert "fields" in fields_block
        assert len(fields_block["fields"]) >= 4

    def test_basic_fields_content(self):
        """RTT/Threshold/Limit/Service 기본 필드 존재."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)

        assert "RTT" in field_texts
        assert "250.0ms" in field_texts
        assert "Threshold" in field_texts
        assert "200" in field_texts
        assert "Current Limit" in field_texts
        assert "80" in field_texts
        assert "Service" in field_texts
        assert "payment" in field_texts

    def test_details_section(self):
        """세 번째 블록에 Details 메시지 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload()
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        details_block = result["blocks"][2]
        assert "Details" in details_block["text"]["text"]
        assert payload.message in details_block["text"]["text"]

    def test_default_service_name(self):
        """metadata에 service_name 없으면 'default' 사용."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = NotificationPayload(
            title="Test",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata={"rtt_ms": 100.0},
        )

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "default" in field_texts


class TestFormatSlaSlackBlocksDynamicFields:
    """RTT Change%, Region 동적 필드 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def _make_payload(self, **metadata_overrides):
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        metadata = {
            "rtt_ms": 250.0,
            "threshold_ms": 200,
            "current_limit": 80,
            "service_name": "payment",
        }
        metadata.update(metadata_overrides)

        return NotificationPayload(
            title="SLA Warning",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata=metadata,
        )

    def test_rtt_change_percent_included(self):
        """RTT Change% 필드 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload(rtt_change_percent=25.0)
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "RTT Change" in field_texts
        assert "+25.0%" in field_texts

    def test_rtt_change_percent_negative(self):
        """음수 RTT Change%."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload(rtt_change_percent=-15.5)
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "-15.5%" in field_texts

    def test_rtt_change_not_included_when_none(self):
        """rtt_change_percent 없으면 RTT Change 필드 미포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload()  # rtt_change_percent 없음
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "RTT Change" not in field_texts

    def test_region_included(self):
        """Region 필드 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload(region="ap-northeast-2")
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "Region" in field_texts
        assert "ap-northeast-2" in field_texts

    def test_region_not_included_when_none(self):
        """region 없으면 Region 필드 미포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload()  # region 없음
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        field_texts = " ".join(f["text"] for f in fields)
        assert "Region" not in field_texts

    def test_both_rtt_change_and_region(self):
        """RTT Change와 Region 동시 포함 시 필드 수 6개."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        payload = self._make_payload(rtt_change_percent=30.0, region="us-west-2")
        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)
        fields = result["blocks"][1]["fields"]
        # 기본 4 + rtt_change 1 + region 1 = 6
        assert len(fields) == 6


class TestFormatSlaSlackBlocksActionableUrls:
    """Actionable 버튼(URL) 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def _make_payload(self, **metadata_overrides):
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
        )

        metadata = {
            "rtt_ms": 250.0,
            "service_name": "payment",
            "event_type": "sla_warning",
        }
        metadata.update(metadata_overrides)

        return NotificationPayload(
            title="SLA Warning",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata=metadata,
        )

    def test_all_urls_configured(self):
        """3개 URL 설정 시 3개 버튼."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(action_blocks) == 1
            buttons = action_blocks[0]["elements"]
            assert len(buttons) == 3

            button_ids = [b["action_id"] for b in buttons]
            assert "view_throttle_dashboard" in button_ids
            assert "view_throttle_admin" in button_ids
            assert "view_sla_runbook" in button_ids

    def test_no_urls_configured(self):
        """URL 미설정 시 actions 블록 없음."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(action_blocks) == 0

    def test_dashboard_only(self):
        """대시보드 URL만 설정 시 1개 버튼."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(action_blocks) == 1
            assert len(action_blocks[0]["elements"]) == 1
            assert action_blocks[0]["elements"][0]["action_id"] == "view_throttle_dashboard"

    def test_admin_button_has_primary_style(self):
        """Admin 버튼에 style='primary' 설정."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            admin_button = action_blocks[0]["elements"][0]
            assert admin_button["style"] == "primary"

    def test_buttons_have_urls(self):
        """각 버튼에 URL 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                reset_throttle_sla_alert_url_builder,
            )

            reset_throttle_sla_alert_url_builder()

            result = format_sla_slack_blocks(self._make_payload(), NotificationPriority.HIGH)

            action_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            for button in action_blocks[0]["elements"]:
                assert "url" in button
                assert button["url"] is not None


class TestFormatSlaSlackBlocksSeverityEmoji:
    """Priority별 emoji 매핑 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def _make_payload(self, priority):
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
        )

        return NotificationPayload(
            title="Test",
            message="Test",
            priority=priority,
            category=NotificationCategory.SLA,
            metadata={"rtt_ms": 100.0, "service_name": "test"},
        )

    def test_critical_emoji(self):
        """CRITICAL → 🔴 (U+1F534)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(
            self._make_payload(NotificationPriority.CRITICAL),
            NotificationPriority.CRITICAL,
        )
        assert "\U0001f534" in result["blocks"][0]["text"]["text"]

    def test_high_emoji(self):
        """HIGH → 🟠 (U+1F7E0)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(
            self._make_payload(NotificationPriority.HIGH),
            NotificationPriority.HIGH,
        )
        assert "\U0001f7e0" in result["blocks"][0]["text"]["text"]

    def test_medium_emoji(self):
        """MEDIUM → 🟡 (U+1F7E1)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(
            self._make_payload(NotificationPriority.MEDIUM),
            NotificationPriority.MEDIUM,
        )
        assert "\U0001f7e1" in result["blocks"][0]["text"]["text"]

    def test_low_emoji_fallback(self):
        """LOW → ⚪ (U+26AA) 기본값."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(
            self._make_payload(NotificationPriority.LOW),
            NotificationPriority.LOW,
        )
        assert "\u26aa" in result["blocks"][0]["text"]["text"]

    def test_info_emoji_fallback(self):
        """INFO → ⚪ (U+26AA) 기본값."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )

        result = format_sla_slack_blocks(
            self._make_payload(NotificationPriority.INFO),
            NotificationPriority.INFO,
        )
        assert "\u26aa" in result["blocks"][0]["text"]["text"]
