"""
SLA Slack Block Kit 포맷터 단위 테스트.

대상: selfhealing/services/unified_notification.py::format_sla_slack_blocks()
- Slack Block Kit 메시지 구조 검증
- 우선순위별 이모지 매핑
- RTT 변화율/Region 조건부 표시
- URL 빌더 연동 및 Actionable 버튼 생성
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tests.unit.throttle.conftest import (
    ALL_URL_ENVS,
    NO_URL_ENVS,
    SVC_PAYMENT,
    TEST_ADMIN_BASE_URL,
    TEST_DASHBOARD_URL,
    TEST_RUNBOOK_URL,
    WARNING_RTT_MS,
    WARNING_THRESHOLD_MS,
    WARNING_CURRENT_LIMIT,
)


def _make_payload(**metadata_overrides):
    """테스트용 NotificationPayload 생성."""
    from selfhealing.services.unified_notification import (
        NotificationCategory,
        NotificationPayload,
        NotificationPriority,
    )

    base_metadata = {
        "service_name": SVC_PAYMENT,
        "rtt_ms": WARNING_RTT_MS,
        "threshold_ms": WARNING_THRESHOLD_MS,
        "current_limit": WARNING_CURRENT_LIMIT,
        "event_type": "sla_warning",
    }
    base_metadata.update(metadata_overrides)

    return NotificationPayload(
        title="SLA Warning: Response Time Exceeded",
        message="Test message",
        priority=NotificationPriority.HIGH,
        category=NotificationCategory.SLA,
        source="adaptive_throttle",
        metadata=base_metadata,
    )


class TestFormatSlaSlackBlocksStructure:
    """Slack Block Kit 기본 구조 테스트."""

    def test_returns_dict_with_blocks_key(self):
        """반환값에 'blocks' 키가 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        assert "blocks" in result
        assert isinstance(result["blocks"], list)

    def test_contains_header_block(self):
        """헤더 블록이 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        header_blocks = [b for b in result["blocks"] if b.get("type") == "header"]
        assert len(header_blocks) == 1
        assert payload.title in header_blocks[0]["text"]["text"]

    def test_contains_section_with_fields(self):
        """필드가 포함된 섹션 블록이 있어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        section_blocks = [b for b in result["blocks"] if b.get("type") == "section"]
        fields_section = [s for s in section_blocks if "fields" in s]
        assert len(fields_section) >= 1

    def test_contains_details_section(self):
        """Details 메시지 섹션이 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        section_blocks = [b for b in result["blocks"] if b.get("type") == "section"]
        details_section = [s for s in section_blocks if s.get("text", {}).get("text", "").startswith("*Details:*")]
        assert len(details_section) == 1


class TestFormatSlaSlackBlocksFields:
    """필수 필드 포함 테스트."""

    def test_contains_rtt_field(self):
        """RTT 필드가 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(rtt_ms=250.5)

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        rtt_fields = [f for f in fields if "*RTT:*" in f["text"]]
        assert len(rtt_fields) == 1
        assert "250.5ms" in rtt_fields[0]["text"]

    def test_contains_threshold_field(self):
        """Threshold 필드가 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(threshold_ms=200)

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        threshold_fields = [f for f in fields if "*Threshold:*" in f["text"]]
        assert len(threshold_fields) == 1
        assert "200ms" in threshold_fields[0]["text"]

    def test_contains_current_limit_field(self):
        """Current Limit 필드가 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(current_limit=80)

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        limit_fields = [f for f in fields if "*Current Limit:*" in f["text"]]
        assert len(limit_fields) == 1
        assert "80" in limit_fields[0]["text"]

    def test_contains_service_field(self):
        """Service 필드가 포함되어야 함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(service_name="payment")

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        service_fields = [f for f in fields if "*Service:*" in f["text"]]
        assert len(service_fields) == 1
        assert "payment" in service_fields[0]["text"]

    def _get_fields(self, result: dict) -> list:
        """블록에서 fields 추출."""
        for block in result["blocks"]:
            if block.get("type") == "section" and "fields" in block:
                return block["fields"]
        return []


class TestFormatSlaSlackBlocksOptionalFields:
    """선택적 필드 테스트."""

    def test_rtt_change_percent_included_when_present(self):
        """rtt_change_percent가 있으면 필드에 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(rtt_change_percent=25.5)

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        change_fields = [f for f in fields if "*RTT Change:*" in f["text"]]
        assert len(change_fields) == 1
        assert "+25.5%" in change_fields[0]["text"]

    def test_rtt_change_percent_negative(self):
        """음수 변화율도 올바르게 표시."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(rtt_change_percent=-15.3)

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        change_fields = [f for f in fields if "*RTT Change:*" in f["text"]]
        assert len(change_fields) == 1
        assert "-15.3%" in change_fields[0]["text"]

    def test_rtt_change_percent_excluded_when_none(self):
        """rtt_change_percent가 None이면 필드 미포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()  # rtt_change_percent 미설정

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        change_fields = [f for f in fields if "*RTT Change:*" in f["text"]]
        assert len(change_fields) == 0

    def test_region_included_when_present(self):
        """region이 있으면 필드에 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload(region="ap-northeast-2")

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        region_fields = [f for f in fields if "*Region:*" in f["text"]]
        assert len(region_fields) == 1
        assert "ap-northeast-2" in region_fields[0]["text"]

    def test_region_excluded_when_none(self):
        """region이 None이면 필드 미포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()  # region 미설정

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        region_fields = [f for f in fields if "*Region:*" in f["text"]]
        assert len(region_fields) == 0

    def _get_fields(self, result: dict) -> list:
        """블록에서 fields 추출."""
        for block in result["blocks"]:
            if block.get("type") == "section" and "fields" in block:
                return block["fields"]
        return []


class TestFormatSlaSlackBlocksPriorityEmoji:
    """우선순위별 이모지 테스트."""

    def test_critical_priority_emoji(self):
        """CRITICAL 우선순위: 🔴 (빨간 원)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.CRITICAL)

        header = result["blocks"][0]
        assert "\U0001f534" in header["text"]["text"]  # 🔴

    def test_high_priority_emoji(self):
        """HIGH 우선순위: 🟠 (주황 원)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        header = result["blocks"][0]
        assert "\U0001f7e0" in header["text"]["text"]  # 🟠

    def test_medium_priority_emoji(self):
        """MEDIUM 우선순위: 🟡 (노란 원)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.MEDIUM)

        header = result["blocks"][0]
        assert "\U0001f7e1" in header["text"]["text"]  # 🟡

    def test_low_priority_emoji(self):
        """LOW 우선순위: ⚪ (흰 원, 기본값)."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = _make_payload()

        result = format_sla_slack_blocks(payload, NotificationPriority.LOW)

        header = result["blocks"][0]
        assert "\u26aa" in header["text"]["text"]  # ⚪


class TestFormatSlaSlackBlocksActionableButtons:
    """Actionable 버튼 테스트."""

    def test_no_actions_block_when_no_urls(self):
        """URL이 없으면 actions 블록 미포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, NO_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload()

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(actions_blocks) == 0

    def test_all_buttons_when_all_urls_set(self):
        """모든 URL이 설정되면 3개 버튼 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, ALL_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload()

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_blocks = [b for b in result["blocks"] if b.get("type") == "actions"]
            assert len(actions_blocks) == 1
            assert len(actions_blocks[0]["elements"]) == 3

    def test_dashboard_button_url(self):
        """Dashboard 버튼 URL이 올바르게 생성."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, ALL_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload(service_name="payment")

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_block = [b for b in result["blocks"] if b.get("type") == "actions"][0]
            dashboard_btn = [e for e in actions_block["elements"] if e.get("action_id") == "view_throttle_dashboard"][0]

            assert TEST_DASHBOARD_URL in dashboard_btn["url"]
            assert "payment" in dashboard_btn["url"]

    def test_admin_button_has_primary_style(self):
        """Admin 버튼은 primary 스타일."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, ALL_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload()

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_block = [b for b in result["blocks"] if b.get("type") == "actions"][0]
            admin_btn = [e for e in actions_block["elements"] if e.get("action_id") == "view_throttle_admin"][0]

            assert admin_btn.get("style") == "primary"

    def test_runbook_button_url_with_anchor(self):
        """Runbook 버튼 URL에 앵커 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, ALL_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload(event_type="sla_critical")

            result = format_sla_slack_blocks(payload, NotificationPriority.CRITICAL)

            actions_block = [b for b in result["blocks"] if b.get("type") == "actions"][0]
            runbook_btn = [e for e in actions_block["elements"] if e.get("action_id") == "view_sla_runbook"][0]

            assert "#sla-critical" in runbook_btn["url"]

    def test_only_dashboard_button_when_only_dashboard_url_set(self):
        """Dashboard URL만 설정되면 Dashboard 버튼만 포함."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        partial_env = {
            "THROTTLE_SLA_DASHBOARD_URL": TEST_DASHBOARD_URL,
            "THROTTLE_SLA_ADMIN_BASE_URL": "",
            "THROTTLE_SLA_RUNBOOK_URL": "",
        }
        with patch.dict(os.environ, partial_env, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload()

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_block = [b for b in result["blocks"] if b.get("type") == "actions"][0]
            assert len(actions_block["elements"]) == 1
            assert actions_block["elements"][0]["action_id"] == "view_throttle_dashboard"


class TestFormatSlaSlackBlocksDefaultValues:
    """기본값 처리 테스트."""

    def test_default_service_name(self):
        """service_name 미설정 시 'default' 사용."""
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()
        payload = NotificationPayload(
            title="Test",
            message="Test",
            priority=NotificationPriority.HIGH,
            category=NotificationCategory.SLA,
            metadata={},  # service_name 없음
        )

        result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

        fields = self._get_fields(result)
        service_fields = [f for f in fields if "*Service:*" in f["text"]]
        assert "default" in service_fields[0]["text"]

    def test_default_event_type_for_url_builder(self):
        """event_type 미설정 시 'sla_warning' 기본값."""
        from selfhealing.services.unified_notification import (
            NotificationPriority,
            format_sla_slack_blocks,
        )
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        with patch.dict(os.environ, ALL_URL_ENVS, clear=False):
            reset_throttle_sla_alert_url_builder()
            payload = _make_payload()
            # event_type 제거
            payload.metadata.pop("event_type", None)

            result = format_sla_slack_blocks(payload, NotificationPriority.HIGH)

            actions_block = [b for b in result["blocks"] if b.get("type") == "actions"][0]
            runbook_btn = [e for e in actions_block["elements"] if e.get("action_id") == "view_sla_runbook"][0]

            # 기본값 sla_warning → #sla-warning
            assert "#sla-warning" in runbook_btn["url"]

    def _get_fields(self, result: dict) -> list:
        """블록에서 fields 추출."""
        for block in result["blocks"]:
            if block.get("type") == "section" and "fields" in block:
                return block["fields"]
        return []
