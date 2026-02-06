"""
Throttle 감사 로그 테스트.

테스트 대상:
1. record_throttle_audit() 메인 함수
2. record_throttle_limit_adjusted() 한도 조정 이벤트
3. record_throttle_emergency_sync() Emergency 연동 이벤트
4. record_throttle_cb_sync() CB 연동 이벤트
5. record_throttle_sla_breach() SLA 위반 이벤트
6. _record_audit_safe() 헬퍼 함수
"""

import pytest
from unittest.mock import patch, MagicMock


class TestRecordThrottleAuditMain:
    """record_throttle_audit() 메인 함수 테스트."""

    def test_record_throttle_audit_basic(self):
        """기본 감사 로그 기록 테스트."""
        from selfhealing.services.throttle.audit import (
            record_throttle_audit,
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
        )

        # 예외 없이 완료되면 성공
        record_throttle_audit(
            action=AUDIT_THROTTLE_LIMIT_ADJUSTED,
        )

    def test_record_throttle_audit_with_details(self):
        """상세 정보 포함 감사 로그 테스트."""
        from selfhealing.services.throttle.audit import (
            record_throttle_audit,
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
        )

        record_throttle_audit(
            action=AUDIT_THROTTLE_LIMIT_ADJUSTED,
            old_limit=100,
            new_limit=80,
            reason="emergency_sync",
        )

    def test_record_throttle_audit_fail_open(self):
        """감사 로그 실패 시 Fail-Open 테스트."""
        from selfhealing.services.throttle.audit import (
            record_throttle_audit,
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
        )

        with patch(
            "selfhealing.audit.cascade_auditor.get_cascade_event_auditor",
            side_effect=Exception("Test error"),
        ):
            # 예외가 전파되지 않음
            record_throttle_audit(
                action=AUDIT_THROTTLE_LIMIT_ADJUSTED,
            )


class TestRecordThrottleLimitAdjusted:
    """record_throttle_limit_adjusted() 테스트."""

    def test_limit_adjusted_basic(self):
        """한도 조정 이벤트 기본 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_limit_adjusted

        record_throttle_limit_adjusted(
            old_limit=100,
            new_limit=80,
            reason="gradient_adjustment",
            trigger_source="gradient",
        )

    def test_limit_adjusted_with_reason(self):
        """한도 조정 이벤트 reason 포함 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_limit_adjusted

        record_throttle_limit_adjusted(
            old_limit=100,
            new_limit=50,
            reason="emergency_sync",
            trigger_source="emergency_mode",
        )


class TestRecordThrottleEmergencySync:
    """record_throttle_emergency_sync() 테스트."""

    def test_emergency_sync_basic(self):
        """Emergency 연동 이벤트 기본 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_emergency_sync

        record_throttle_emergency_sync(
            old_limit=100,
            new_limit=50,
            emergency_level=2,
            applied_multiplier=0.5,
        )

    def test_emergency_sync_level_0(self):
        """Emergency Level 0 이벤트 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_emergency_sync

        record_throttle_emergency_sync(
            old_limit=50,
            new_limit=100,
            emergency_level=0,
            applied_multiplier=1.0,
        )

    def test_emergency_sync_level_3(self):
        """Emergency Level 3 (Full Stop) 이벤트 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_emergency_sync

        record_throttle_emergency_sync(
            old_limit=50,
            new_limit=0,
            emergency_level=3,
            applied_multiplier=0.0,
        )


class TestRecordThrottleCbSync:
    """record_throttle_cb_sync() 테스트."""

    def test_cb_sync_open(self):
        """CB OPEN 상태 연동 이벤트 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_cb_sync

        record_throttle_cb_sync(
            old_limit=100,
            new_limit=0,
            service_name="test-service",
            cb_state="OPEN",
        )

    def test_cb_sync_half_open(self):
        """CB HALF_OPEN 상태 연동 이벤트 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_cb_sync

        record_throttle_cb_sync(
            old_limit=0,
            new_limit=50,
            service_name="test-service",
            cb_state="HALF_OPEN",
        )

    def test_cb_sync_closed(self):
        """CB CLOSED 상태 연동 이벤트 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_cb_sync

        record_throttle_cb_sync(
            old_limit=50,
            new_limit=100,
            service_name="test-service",
            cb_state="CLOSED",
        )


class TestRecordThrottleSlaBreachEvent:
    """record_throttle_sla_breach() 테스트."""

    def test_sla_breach_basic(self):
        """SLA 위반 이벤트 기본 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_sla_breach

        record_throttle_sla_breach(
            rtt_ms=250.0,
            threshold_ms=100,
            current_limit=80,
        )

    def test_sla_breach_with_high_latency(self):
        """SLA 위반 이벤트 높은 지연 테스트."""
        from selfhealing.services.throttle.audit import record_throttle_sla_breach

        record_throttle_sla_breach(
            rtt_ms=5000.0,
            threshold_ms=100,
            current_limit=10,
        )


class TestCascadeEventIntegration:
    """CascadeEvent 연동 테스트."""

    def test_cascade_event_called_for_emergency(self):
        """Emergency 이벤트 시 CascadeEvent 호출 테스트."""
        from selfhealing.services.throttle.audit import (
            record_throttle_emergency_sync,
            _process_audit_event,
        )

        with patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor") as mock_get_auditor:
            mock_auditor = MagicMock()
            mock_get_auditor.return_value = mock_auditor

            # 큐에 넣는 대신 직접 처리하도록 패치 (동기화)
            with patch(
                "selfhealing.services.throttle.audit._audit_queue.put_nowait",
                side_effect=lambda data: _process_audit_event(data),
            ):
                record_throttle_emergency_sync(
                    old_limit=100,
                    new_limit=50,
                    emergency_level=2,
                    applied_multiplier=0.5,
                )

            # CascadeEventAuditor가 호출되었는지 확인
            mock_get_auditor.assert_called()

    def test_cascade_event_called_for_cb(self):
        """CB 이벤트 시 CascadeEvent 호출 테스트."""
        from selfhealing.services.throttle.audit import (
            record_throttle_cb_sync,
            _process_audit_event,
        )

        with patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor") as mock_get_auditor:
            mock_auditor = MagicMock()
            mock_get_auditor.return_value = mock_auditor

            # 큐에 넣는 대신 직접 처리하도록 패치 (동기화)
            with patch(
                "selfhealing.services.throttle.audit._audit_queue.put_nowait",
                side_effect=lambda data: _process_audit_event(data),
            ):
                record_throttle_cb_sync(
                    old_limit=100,
                    new_limit=0,
                    service_name="test-service",
                    cb_state="OPEN",
                )

            # CascadeEventAuditor가 호출됨
            mock_get_auditor.assert_called()

    def test_cascade_event_not_called_for_limit_adjusted(self):
        """일반 한도 조정 시 CascadeEvent 미호출 확인."""
        from selfhealing.services.throttle.audit import record_throttle_limit_adjusted

        # record_throttle_limit_adjusted는 CascadeEvent를 호출하지 않음
        # (Emergency/CB 이벤트만 CascadeEvent 기록)
        with patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor") as mock_get_auditor:
            mock_auditor = MagicMock()
            mock_get_auditor.return_value = mock_auditor

            record_throttle_limit_adjusted(
                old_limit=100,
                new_limit=90,
                reason="gradient",
                trigger_source="gradient",
            )

            # record 메서드가 호출되지 않음 (LIMIT_ADJUSTED는 CascadeEvent 미기록)
            mock_auditor.record.assert_not_called()


class TestRecordAuditSafeHelper:
    """_record_audit_safe() 헬퍼 함수 테스트."""

    def test_record_audit_safe_success(self):
        """정상적인 감사 로그 기록 테스트."""
        from selfhealing.services.throttle.adaptive import _record_audit_safe
        from selfhealing.services.throttle.audit import AUDIT_THROTTLE_CB_SYNC

        _record_audit_safe(
            action=AUDIT_THROTTLE_CB_SYNC,
            old_limit=100,
            new_limit=0,
            cb_state="OPEN",
        )

    def test_record_audit_safe_fail_open(self):
        """감사 로그 실패 시 Fail-Open 테스트."""
        from selfhealing.services.throttle.adaptive import _record_audit_safe
        from selfhealing.services.throttle.audit import AUDIT_THROTTLE_CB_SYNC

        with patch(
            "selfhealing.services.throttle.audit.record_throttle_audit",
            side_effect=Exception("Test error"),
        ):
            # 예외가 전파되지 않음
            _record_audit_safe(
                action=AUDIT_THROTTLE_CB_SYNC,
                old_limit=100,
            )


class TestAuditEventTypeConstants:
    """감사 이벤트 타입 상수 테스트."""

    def test_audit_event_type_constants_exist(self):
        """감사 이벤트 타입 상수 존재 확인."""
        from selfhealing.services.throttle.audit import (
            AUDIT_THROTTLE_LIMIT_ADJUSTED,
            AUDIT_THROTTLE_EMERGENCY_SYNC,
            AUDIT_THROTTLE_CB_SYNC,
            AUDIT_THROTTLE_SLA_BREACH,
        )

        assert AUDIT_THROTTLE_LIMIT_ADJUSTED == "throttle_limit_adjusted"
        assert AUDIT_THROTTLE_EMERGENCY_SYNC == "throttle_emergency_sync"
        assert AUDIT_THROTTLE_CB_SYNC == "throttle_cb_sync"
        assert AUDIT_THROTTLE_SLA_BREACH == "throttle_sla_breach"
