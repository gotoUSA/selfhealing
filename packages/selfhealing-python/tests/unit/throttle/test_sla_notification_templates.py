"""
SLA 알림 메시지 템플릿 단위 테스트.

대상: selfhealing/services/throttle/sla_notification_templates.py
- build_sla_warning_message()
- build_sla_critical_message()
- build_sla_recovered_message()
"""

from __future__ import annotations

import pytest

from tests.unit.throttle.conftest import (
    CRITICAL_CURRENT_LIMIT,
    CRITICAL_GRADIENT,
    CRITICAL_PREVIOUS_LIMIT,
    CRITICAL_REDUCTION_PERCENT,
    CRITICAL_RTT_MS,
    CRITICAL_THRESHOLD_MS,
    RECOVERED_NEW_LIMIT,
    RECOVERED_PREVIOUS_LIMIT,
    RECOVERED_RTT_MS,
    SVC_DEFAULT,
    SVC_ORDER,
    SVC_PAYMENT,
    WARNING_CURRENT_LIMIT,
    WARNING_GRADIENT,
    WARNING_PREVIOUS_LIMIT,
    WARNING_RTT_MS,
    WARNING_THRESHOLD_MS,
    make_warning_event_data,
    make_critical_event_data,
    make_recovered_event_data,
)


class TestBuildSlaWarningMessage:
    """SLA Warning 메시지 템플릿 테스트."""

    def _build(self, **overrides):
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        data = make_warning_event_data(**overrides)
        return build_sla_warning_message(**data)

    def test_basic_required_keys(self):
        """반환 딕셔너리에 title/severity/message/details/actions 포함."""
        result = self._build()
        assert set(result.keys()) == {"title", "severity", "message", "details", "actions"}

    def test_severity_is_high(self):
        """Warning severity는 'high'."""
        assert self._build()["severity"] == "high"

    def test_event_type_in_details(self):
        """details.event_type이 'sla_warning'."""
        assert self._build()["details"]["event_type"] == "sla_warning"

    def test_rtt_in_message(self):
        """RTT 값이 메시지에 포함."""
        result = self._build(rtt_ms=300.5)
        assert "300.5ms" in result["message"]

    def test_service_name_in_message(self):
        """서비스 이름이 메시지에 포함."""
        result = self._build(service_name="order-service")
        assert "order-service" in result["message"]

    def test_threshold_in_message(self):
        """임계값이 메시지에 포함."""
        result = self._build(threshold_ms=WARNING_THRESHOLD_MS)
        assert str(WARNING_THRESHOLD_MS) in result["message"]

    def test_limit_change_in_message(self):
        """Limit 변화가 메시지에 포함."""
        result = self._build(previous_limit=WARNING_PREVIOUS_LIMIT, current_limit=WARNING_CURRENT_LIMIT)
        assert str(WARNING_PREVIOUS_LIMIT) in result["message"]
        assert str(WARNING_CURRENT_LIMIT) in result["message"]

    def test_gradient_in_message(self):
        """Gradient 값이 메시지에 포함."""
        result = self._build(gradient=0.123)
        assert "0.123" in result["message"]

    def test_with_rtt_change_percent_positive(self):
        """양수 RTT 변화율이 메시지에 +기호와 함께 포함."""
        result = self._build(rtt_change_percent=25.0)
        assert "+25.0%" in result["message"]
        assert result["details"]["rtt_change_percent"] == 25.0

    def test_with_rtt_change_percent_negative(self):
        """음수 RTT 변화율이 메시지에 -기호와 함께 포함."""
        result = self._build(rtt_change_percent=-10.5)
        assert "-10.5%" in result["message"]

    def test_without_rtt_change_percent(self):
        """RTT 변화율이 None이면 변화율 텍스트 미포함."""
        result = self._build(rtt_change_percent=None)
        assert "변화율" not in result["message"]
        assert result["details"]["rtt_change_percent"] is None

    def test_with_region(self):
        """Region이 title에 대괄호로 포함."""
        result = self._build(region="ap-northeast-2")
        assert "[ap-northeast-2]" in result["title"]

    def test_without_region(self):
        """Region=None이면 대괄호 미포함."""
        result = self._build(region=None)
        assert "[" not in result["title"] or "SLA" in result["title"]

    def test_default_service_name(self):
        """service_name 기본값은 'default'."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )

        data = make_warning_event_data()
        del data["service_name"]
        result = build_sla_warning_message(**data)
        assert SVC_DEFAULT in result["message"]

    def test_details_all_fields_present(self):
        """details에 모든 필수 필드 존재."""
        result = self._build(rtt_change_percent=15.0)
        details = result["details"]
        expected_keys = {
            "rtt_ms",
            "threshold_ms",
            "current_limit",
            "previous_limit",
            "gradient",
            "rtt_change_percent",
            "event_type",
        }
        assert expected_keys == set(details.keys())

    def test_actions_list(self):
        """actions에 check_dashboard, review_throttle_config 포함."""
        result = self._build()
        assert "check_dashboard" in result["actions"]
        assert "review_throttle_config" in result["actions"]

    def test_title_contains_warning(self):
        """title에 'Warning' 키워드 포함."""
        result = self._build()
        assert "Warning" in result["title"]


class TestBuildSlaCriticalMessage:
    """SLA Critical 메시지 템플릿 테스트."""

    def _build(self, **overrides):
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )

        data = make_critical_event_data(**overrides)
        return build_sla_critical_message(**data)

    def test_severity_is_critical(self):
        """Critical severity는 'critical'."""
        assert self._build()["severity"] == "critical"

    def test_event_type_in_details(self):
        """details.event_type이 'sla_critical'."""
        assert self._build()["details"]["event_type"] == "sla_critical"

    def test_critical_keyword_in_message(self):
        """메시지에 'CRITICAL' 키워드 포함."""
        assert "CRITICAL" in self._build()["message"]

    def test_rtt_in_message(self):
        """RTT 값이 메시지에 포함."""
        result = self._build(rtt_ms=750.3)
        assert "750.3ms" in result["message"]

    def test_service_name_in_message(self):
        """서비스 이름이 메시지에 포함."""
        result = self._build(service_name="payment-gateway")
        assert "payment-gateway" in result["message"]

    def test_reduction_percent_in_message(self):
        """감축률이 메시지에 포함."""
        result = self._build(reduction_percent=45)
        assert "45" in result["message"]

    def test_reduction_percent_in_details(self):
        """details에 reduction_percent 존재."""
        result = self._build(reduction_percent=CRITICAL_REDUCTION_PERCENT)
        assert result["details"]["reduction_percent"] == CRITICAL_REDUCTION_PERCENT

    def test_with_rtt_change_percent(self):
        """RTT 변화율이 메시지에 포함."""
        result = self._build(rtt_change_percent=50.0)
        assert "+50.0%" in result["message"]

    def test_without_rtt_change_percent(self):
        """RTT 변화율이 None이면 변화율 텍스트 미포함."""
        result = self._build(rtt_change_percent=None)
        assert "변화율" not in result["message"]

    def test_with_region(self):
        """Region이 title에 포함."""
        result = self._build(region="us-west-2")
        assert "[us-west-2]" in result["title"]

    def test_without_region(self):
        """Region=None이면 대괄호 미포함."""
        result = self._build(region=None)
        assert "[]" not in result["title"]

    def test_escalate_oncall_in_actions(self):
        """Critical에는 escalate_oncall 액션 포함."""
        result = self._build()
        assert "escalate_oncall" in result["actions"]

    def test_actions_include_dashboard_and_config(self):
        """Critical에도 dashboard/config 액션 포함."""
        result = self._build()
        assert "check_dashboard" in result["actions"]
        assert "review_throttle_config" in result["actions"]

    def test_default_service_name(self):
        """service_name 기본값은 'default'."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )

        data = make_critical_event_data()
        del data["service_name"]
        result = build_sla_critical_message(**data)
        assert SVC_DEFAULT in result["message"]

    def test_immediate_action_hint(self):
        """메시지에 즉각 조치 힌트 포함."""
        result = self._build()
        assert "action" in result["message"].lower() or "required" in result["message"].lower()


class TestBuildSlaRecoveredMessage:
    """SLA Recovered 메시지 템플릿 테스트."""

    def _build(self, **overrides):
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )

        data = make_recovered_event_data(**overrides)
        return build_sla_recovered_message(**data)

    def test_severity_is_medium(self):
        """Recovered severity는 'medium'."""
        assert self._build()["severity"] == "medium"

    def test_event_type_in_details(self):
        """details.event_type이 'limit_recovered'."""
        assert self._build()["details"]["event_type"] == "limit_recovered"

    def test_limit_change_in_message(self):
        """이전/새 limit이 메시지에 포함."""
        result = self._build(previous_limit=60, new_limit=110)
        assert "60" in result["message"]
        assert "110" in result["message"]

    def test_rtt_in_message(self):
        """현재 RTT가 메시지에 포함."""
        result = self._build(rtt_ms=42.5)
        assert "42.5ms" in result["message"]

    def test_service_name_in_message(self):
        """서비스 이름이 메시지에 포함."""
        result = self._build(service_name="auth-service")
        assert "auth-service" in result["message"]

    def test_with_region(self):
        """Region이 title에 포함."""
        result = self._build(region="us-east-1")
        assert "[us-east-1]" in result["title"]

    def test_without_region(self):
        """Region=None이면 대괄호 미포함."""
        result = self._build(region=None)
        assert "[]" not in result["title"]

    def test_title_contains_recovered(self):
        """title에 'Recovered' 키워드 포함."""
        result = self._build()
        assert "Recovered" in result["title"]

    def test_verify_recovery_in_actions(self):
        """actions에 verify_recovery 포함."""
        result = self._build()
        assert "verify_recovery" in result["actions"]

    def test_details_structure(self):
        """details에 필수 키 존재."""
        result = self._build()
        expected_keys = {"previous_limit", "new_limit", "rtt_ms", "event_type"}
        assert expected_keys == set(result["details"].keys())

    def test_default_service_name(self):
        """service_name 기본값은 'default'."""
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )

        data = make_recovered_event_data()
        del data["service_name"]
        result = build_sla_recovered_message(**data)
        assert SVC_DEFAULT in result["message"]
