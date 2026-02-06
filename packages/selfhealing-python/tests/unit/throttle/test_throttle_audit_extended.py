"""
Throttle Audit 확장 기능 단위 테스트.

테스트 대상:
- 감사 이벤트 타입 확장 상수
- AuditSeverity enum
- 샘플링 함수
- Correlation ID 생성
- 확장 편의 함수들
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestThrottleAuditExtendedEventTypes:
    """확장된 감사 이벤트 타입 상수 테스트."""

    def test_extended_event_type_constants_defined(self):
        """확장된 이벤트 타입 상수들이 정의되어 있는지 확인."""
        from selfhealing.services.throttle.audit import (
            AUDIT_THROTTLE_SLA_WARNING,
            AUDIT_THROTTLE_SLA_CRITICAL,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            AUDIT_THROTTLE_429_RESPONSE,
            AUDIT_THROTTLE_RECOVERY_STARTED,
            AUDIT_THROTTLE_RECOVERY_COMPLETED,
        )

        assert AUDIT_THROTTLE_SLA_WARNING == "throttle_sla_warning"
        assert AUDIT_THROTTLE_SLA_CRITICAL == "throttle_sla_critical"
        assert AUDIT_THROTTLE_FULL_STOP_ACTIVATED == "throttle_full_stop_activated"
        assert AUDIT_THROTTLE_FULL_STOP_DEACTIVATED == "throttle_full_stop_deactivated"
        assert AUDIT_THROTTLE_429_RESPONSE == "throttle_429_response"
        assert AUDIT_THROTTLE_RECOVERY_STARTED == "throttle_recovery_started"
        assert AUDIT_THROTTLE_RECOVERY_COMPLETED == "throttle_recovery_completed"

    def test_cascade_event_actions_contains_critical_events(self):
        """CASCADE_EVENT_ACTIONS에 중요 이벤트가 포함되어 있는지 확인."""
        from selfhealing.services.throttle.audit import (
            CASCADE_EVENT_ACTIONS,
            AUDIT_THROTTLE_EMERGENCY_SYNC,
            AUDIT_THROTTLE_CB_SYNC,
            AUDIT_THROTTLE_SLA_CRITICAL,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
        )

        assert AUDIT_THROTTLE_EMERGENCY_SYNC in CASCADE_EVENT_ACTIONS
        assert AUDIT_THROTTLE_CB_SYNC in CASCADE_EVENT_ACTIONS
        assert AUDIT_THROTTLE_SLA_CRITICAL in CASCADE_EVENT_ACTIONS
        assert AUDIT_THROTTLE_FULL_STOP_ACTIVATED in CASCADE_EVENT_ACTIONS
        assert AUDIT_THROTTLE_FULL_STOP_DEACTIVATED in CASCADE_EVENT_ACTIONS


class TestExtendedAuditSeverity:
    """AuditSeverity enum 테스트."""

    def test_severity_enum_values(self):
        """Severity enum 값들 확인."""
        from selfhealing.services.throttle.audit import AuditSeverity

        assert AuditSeverity.DEBUG.value == "debug"
        assert AuditSeverity.INFO.value == "info"
        assert AuditSeverity.WARNING.value == "warning"
        assert AuditSeverity.CRITICAL.value == "critical"

    def test_severity_map_assigns_critical_to_cascade_events(self):
        """CASCADE_EVENT에 CRITICAL severity가 할당되는지 확인."""
        from selfhealing.services.throttle.audit import (
            AUDIT_SEVERITY_MAP,
            AuditSeverity,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_EMERGENCY_SYNC,
            AUDIT_THROTTLE_SLA_CRITICAL,
        )

        assert AUDIT_SEVERITY_MAP[AUDIT_THROTTLE_FULL_STOP_ACTIVATED] == AuditSeverity.CRITICAL
        assert AUDIT_SEVERITY_MAP[AUDIT_THROTTLE_EMERGENCY_SYNC] == AuditSeverity.CRITICAL
        assert AUDIT_SEVERITY_MAP[AUDIT_THROTTLE_SLA_CRITICAL] == AuditSeverity.CRITICAL


class TestExtendedGetSeverity:
    """_get_severity 함수 확장 테스트."""

    def test_critical_events_have_critical_severity(self):
        """중요 이벤트는 CRITICAL severity를 가지는지 확인."""
        from selfhealing.services.throttle.audit import (
            _get_severity,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            AUDIT_THROTTLE_EMERGENCY_SYNC,
            AUDIT_THROTTLE_SLA_CRITICAL,
        )

        assert _get_severity(AUDIT_THROTTLE_FULL_STOP_ACTIVATED) == "critical"
        assert _get_severity(AUDIT_THROTTLE_FULL_STOP_DEACTIVATED) == "critical"
        assert _get_severity(AUDIT_THROTTLE_EMERGENCY_SYNC) == "critical"
        assert _get_severity(AUDIT_THROTTLE_SLA_CRITICAL) == "critical"

    def test_override_severity_works(self):
        """severity 오버라이드 기능 확인."""
        from selfhealing.services.throttle.audit import (
            _get_severity,
            AuditSeverity,
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
        )

        # 기본값은 debug
        assert _get_severity(AUDIT_THROTTLE_LIMIT_ADJUSTED) == "debug"

        # 오버라이드
        assert _get_severity(AUDIT_THROTTLE_LIMIT_ADJUSTED, AuditSeverity.CRITICAL) == "critical"


class TestExtendedSampling:
    """확장된 샘플링 함수 테스트."""

    def test_critical_events_never_sampled_out(self):
        """CRITICAL 이벤트는 샘플링되지 않는지 확인."""
        from selfhealing.services.throttle.audit import (
            should_sample,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            AUDIT_THROTTLE_EMERGENCY_SYNC,
            AUDIT_THROTTLE_SLA_CRITICAL,
        )

        # CRITICAL 이벤트는 항상 True (100번 테스트)
        for _ in range(100):
            assert should_sample(AUDIT_THROTTLE_FULL_STOP_ACTIVATED) is True
            assert should_sample(AUDIT_THROTTLE_FULL_STOP_DEACTIVATED) is True
            assert should_sample(AUDIT_THROTTLE_EMERGENCY_SYNC) is True
            assert should_sample(AUDIT_THROTTLE_SLA_CRITICAL) is True

    def test_warning_events_never_sampled_out(self):
        """WARNING 이벤트는 샘플링되지 않는지 확인."""
        from selfhealing.services.throttle.audit import (
            should_sample,
            AUDIT_THROTTLE_SLA_WARNING,
            AUDIT_THROTTLE_CB_SYNC,
        )

        # WARNING 이벤트는 항상 True
        for _ in range(100):
            assert should_sample(AUDIT_THROTTLE_SLA_WARNING) is True
            assert should_sample(AUDIT_THROTTLE_CB_SYNC) is True

    def test_is_critical_action_returns_correct_values(self):
        """_is_critical_action 함수가 올바른 값을 반환하는지 확인."""
        from selfhealing.services.throttle.audit import (
            _is_critical_action,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
            AUDIT_THROTTLE_429_RESPONSE,
        )

        assert _is_critical_action(AUDIT_THROTTLE_FULL_STOP_ACTIVATED) is True
        assert _is_critical_action(AUDIT_THROTTLE_FULL_STOP_DEACTIVATED) is True
        assert _is_critical_action(AUDIT_THROTTLE_LIMIT_ADJUSTED) is False
        assert _is_critical_action(AUDIT_THROTTLE_429_RESPONSE) is False


class TestExtendedCorrelationId:
    """Correlation ID 관련 함수 확장 테스트."""

    def test_generate_event_id_format(self):
        """이벤트 ID 형식 확인."""
        from selfhealing.services.throttle.audit import _generate_event_id

        event_id = _generate_event_id()
        assert event_id.startswith("evt-")
        assert len(event_id) == 16  # "evt-" + 12자리 hex

    def test_event_chain_parent_relationship(self):
        """이벤트 체인에서 parent 관계가 형성되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _get_correlation_ids,
            reset_event_chain,
        )

        reset_event_chain()

        # 1번 이벤트 (root)
        id1, parent1 = _get_correlation_ids()
        assert parent1 is None

        # 2번 이벤트 - 1번이 부모
        id2, parent2 = _get_correlation_ids()
        assert parent2 == id1

        # 3번 이벤트 - 2번이 부모
        id3, parent3 = _get_correlation_ids()
        assert parent3 == id2


class TestExtendedMapActionToTriggerType:
    """_map_action_to_trigger_type 함수 확장 테스트."""

    def test_maps_new_actions(self):
        """새 액션이 트리거 타입으로 매핑되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _map_action_to_trigger_type,
            AUDIT_THROTTLE_SLA_WARNING,
            AUDIT_THROTTLE_SLA_CRITICAL,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            AUDIT_THROTTLE_429_RESPONSE,
            AUDIT_THROTTLE_RECOVERY_STARTED,
            AUDIT_THROTTLE_RECOVERY_COMPLETED,
        )

        assert _map_action_to_trigger_type(AUDIT_THROTTLE_SLA_WARNING) == "THROTTLE_SLA_WARNING"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_SLA_CRITICAL) == "THROTTLE_SLA_CRITICAL"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_FULL_STOP_ACTIVATED) == "THROTTLE_FULL_STOP_ACTIVATED"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_FULL_STOP_DEACTIVATED) == "THROTTLE_FULL_STOP_DEACTIVATED"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_429_RESPONSE) == "THROTTLE_429_RESPONSE"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_RECOVERY_STARTED) == "THROTTLE_RECOVERY_STARTED"
        assert _map_action_to_trigger_type(AUDIT_THROTTLE_RECOVERY_COMPLETED) == "THROTTLE_RECOVERY_COMPLETED"


class TestExtendedBuildThrottleEffects:
    """_build_throttle_effects 함수 확장 테스트."""

    def test_builds_full_stop_activated_effect(self):
        """Full Stop 활성화 효과가 빌드되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_throttle_effects,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
        )

        audit_data = {
            "action": AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            "old_limit": 100,
            "new_limit": 0,
            "full_stop_reason": "LEVEL_3+DB_CB_OPEN",
        }

        effects = _build_throttle_effects(audit_data)

        full_stop_effect = next((e for e in effects if e["action_type"] == "FULL_STOP_ACTIVATED"), None)
        assert full_stop_effect is not None
        assert full_stop_effect["details"]["all_requests_blocked"] is True
        assert full_stop_effect["details"]["reason"] == "LEVEL_3+DB_CB_OPEN"

    def test_builds_full_stop_deactivated_effect(self):
        """Full Stop 비활성화 효과가 빌드되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_throttle_effects,
            AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
        )

        audit_data = {
            "action": AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
            "old_limit": 0,
            "new_limit": 80,
        }

        effects = _build_throttle_effects(audit_data)

        deactivated_effect = next((e for e in effects if e["action_type"] == "FULL_STOP_DEACTIVATED"), None)
        assert deactivated_effect is not None
        assert deactivated_effect["details"]["recovery_started"] is True

    def test_builds_sla_critical_effect(self):
        """SLA Critical 효과가 빌드되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_throttle_effects,
            AUDIT_THROTTLE_SLA_CRITICAL,
        )

        audit_data = {
            "action": AUDIT_THROTTLE_SLA_CRITICAL,
            "old_limit": 100,
            "new_limit": 70,
            "rtt_ms": 600.0,
            "threshold_ms": 500,
        }

        effects = _build_throttle_effects(audit_data)

        sla_effect = next((e for e in effects if e["action_type"] == "SLA_CRITICAL_TRIGGERED"), None)
        assert sla_effect is not None
        assert sla_effect["details"]["rtt_ms"] == 600.0
        assert sla_effect["details"]["threshold_ms"] == 500


class TestExtendedConvenienceFunctions:
    """확장 편의 함수 테스트."""

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_sla_warning(self, mock_worker, mock_queue):
        """record_throttle_sla_warning 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_sla_warning

        record_throttle_sla_warning(
            rtt_ms=400.0,
            threshold_ms=300,
            current_limit=90,
            previous_limit=100,
            gradient=0.1,
        )

        mock_worker.assert_called()

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_sla_critical(self, mock_worker, mock_queue):
        """record_throttle_sla_critical 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_sla_critical

        record_throttle_sla_critical(
            rtt_ms=600.0,
            threshold_ms=500,
            current_limit=70,
            previous_limit=100,
            reduction_percent=30,
        )

        mock_worker.assert_called()

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_full_stop_activated(self, mock_worker, mock_queue):
        """record_throttle_full_stop_activated 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_full_stop_activated

        record_throttle_full_stop_activated(
            previous_limit=100,
            reason="LEVEL_3+DB_CB_OPEN+BUDGET_EXHAUSTED",
        )

        mock_worker.assert_called()

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_full_stop_deactivated(self, mock_worker, mock_queue):
        """record_throttle_full_stop_deactivated 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_full_stop_deactivated

        record_throttle_full_stop_deactivated(new_limit=80)

        mock_worker.assert_called()

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_recovery_started(self, mock_worker, mock_queue):
        """record_throttle_recovery_started 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_recovery_started

        record_throttle_recovery_started(
            base_limit=100,
            initial_limit=80,
            step=0,
        )

        mock_worker.assert_called()

    @patch("selfhealing.services.throttle.audit._audit_queue")
    @patch("selfhealing.services.throttle.audit._start_audit_worker")
    def test_record_throttle_recovery_completed(self, mock_worker, mock_queue):
        """record_throttle_recovery_completed 함수 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_recovery_completed

        record_throttle_recovery_completed(final_limit=100)

        mock_worker.assert_called()


class TestExtendedBuildAuditData:
    """_build_audit_data 함수 확장 테스트."""

    def test_includes_rtt_trend_data(self):
        """RTT 추세 데이터가 포함되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_audit_data,
            reset_event_chain,
        )

        reset_event_chain()

        audit_data = _build_audit_data(
            action="throttle_limit_adjusted",
            smoothed_rtt_ms=150.5,
            gradient=0.15,
            long_rtt_ms=50.0,
        )

        assert audit_data["smoothed_rtt_ms"] == 150.5
        assert audit_data["gradient"] == 0.15  # 소수점 6자리로 반올림
        assert audit_data["long_rtt_ms"] == 50.0

    def test_includes_recovery_step(self):
        """recovery_step이 포함되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_audit_data,
            reset_event_chain,
        )

        reset_event_chain()

        audit_data = _build_audit_data(
            action="throttle_recovery_started",
            recovery_step=1,
        )

        assert audit_data["recovery_step"] == 1

    def test_includes_429_data(self):
        """429 관련 데이터가 포함되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_audit_data,
            reset_event_chain,
        )

        reset_event_chain()

        audit_data = _build_audit_data(
            action="throttle_429_response",
            consecutive_429s=3,
            cooldown_seconds=30.0,
        )

        assert audit_data["consecutive_429s"] == 3
        assert audit_data["cooldown_seconds"] == 30.0

    def test_includes_config_snapshot_for_cascade_events(self):
        """CASCADE_EVENT에 config_snapshot이 포함되는지 확인."""
        from selfhealing.services.throttle.audit import (
            _build_audit_data,
            AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            reset_event_chain,
        )

        reset_event_chain()

        config = {"base_limit": 100, "min_limit": 10}

        audit_data = _build_audit_data(
            action=AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
            config_snapshot=config,
        )

        assert "config_snapshot" in audit_data
        assert audit_data["config_snapshot"]["base_limit"] == 100
        assert audit_data["config_snapshot"]["min_limit"] == 10
