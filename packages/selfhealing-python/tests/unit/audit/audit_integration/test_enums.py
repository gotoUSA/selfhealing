"""
EventSeverity and AuditEventType Enum Tests.

Enum 테스트.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

import pytest


class TestEventSeverity:
    """EventSeverity enum 테스트."""

    def test_severity_values(self):
        """심각도 값 검증."""
        from selfhealing.audit.audit_integration import EventSeverity
        
        assert EventSeverity.DEBUG.value == 0
        assert EventSeverity.INFO.value == 1
        assert EventSeverity.WARNING.value == 2
        assert EventSeverity.CRITICAL.value == 3

    def test_severity_comparison(self):
        """심각도 비교."""
        from selfhealing.audit.audit_integration import EventSeverity
        
        assert EventSeverity.CRITICAL.value > EventSeverity.WARNING.value
        assert EventSeverity.WARNING.value > EventSeverity.INFO.value


class TestAuditEventType:
    """AuditEventType enum 테스트."""

    def test_event_types_exist(self):
        """필수 이벤트 유형 존재 확인."""
        from selfhealing.audit.audit_integration import AuditEventType
        
        assert AuditEventType.RECORD_SUCCESS
        assert AuditEventType.RECORD_FAILED
        assert AuditEventType.CIRCUIT_OPENED
        assert AuditEventType.CIRCUIT_CLOSED
        assert AuditEventType.FALLBACK_ACTIVATED
        assert AuditEventType.SYSLOG_ACTIVATED
        assert AuditEventType.PRIMARY_RECOVERED
        assert AuditEventType.DEGRADED_MODE_ENTERED
