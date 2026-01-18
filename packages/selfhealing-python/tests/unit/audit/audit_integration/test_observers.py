"""
Observer Pattern Tests.

AuditEventObserver 및 AsyncLoggerObserver 테스트.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

import time
from unittest.mock import Mock

import pytest


class TestAuditEventObserver:
    """Observer 패턴 테스트."""

    def test_observer_interface(self):
        """Observer 인터페이스 검증."""
        from selfhealing.audit.audit_integration import (
            AuditEventObserver,
            AuditEventData,
            AuditEventType,
        )

        class TestObserver(AuditEventObserver):
            def __init__(self):
                self.events = []

            def on_event(self, event: AuditEventData) -> None:
                self.events.append(event)

        observer = TestObserver()
        event = AuditEventData(event_type=AuditEventType.CIRCUIT_OPENED)

        observer.on_event(event)

        assert len(observer.events) == 1
        assert observer.events[0].event_type == AuditEventType.CIRCUIT_OPENED


class TestAsyncLoggerObserver:
    """AsyncLoggerObserver 테스트."""

    def test_circuit_opened_event(self):
        """Circuit Breaker OPEN 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        flushed = []
        adapter = AsyncLoggerAdapter(flush_callback=lambda e: flushed.extend(e))
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.CIRCUIT_OPENED,
            details={"service": "test_service"},
        )

        observer.on_event(event)
        time.sleep(0.1)

        assert len(flushed) == 1
        assert flushed[0]["type"] == "circuit_breaker"
        assert flushed[0]["state"] == "OPEN"

    def test_circuit_closed_event(self):
        """Circuit Breaker CLOSED 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        adapter = AsyncLoggerAdapter()
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.CIRCUIT_CLOSED,
            details={"service": "test_service"},
        )

        observer.on_event(event)

        # INFO는 큐에 들어감
        assert adapter._queue.qsize() == 1

    def test_fallback_activated_event(self):
        """Fallback 활성화 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        flushed = []
        adapter = AsyncLoggerAdapter(flush_callback=lambda e: flushed.extend(e))
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.FALLBACK_ACTIVATED,
            details={"fallback_type": "file", "reason": "primary_failed"},
        )

        observer.on_event(event)
        time.sleep(0.1)

        assert len(flushed) == 1
        assert flushed[0]["type"] == "fallback_activated"

    def test_syslog_activated_event(self):
        """Syslog 활성화 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        flushed = []
        adapter = AsyncLoggerAdapter(flush_callback=lambda e: flushed.extend(e))
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(event_type=AuditEventType.SYSLOG_ACTIVATED)

        observer.on_event(event)
        time.sleep(0.1)

        assert len(flushed) == 1
        assert flushed[0]["type"] == "emergency"
        assert flushed[0]["action"] == "trigger"

    def test_primary_recovered_event(self):
        """Primary 복구 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        adapter = AsyncLoggerAdapter()
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.PRIMARY_RECOVERED,
            details={"service": "audit_primary", "recovery_time_ms": 100},
        )

        observer.on_event(event)

        assert adapter._queue.qsize() == 1
        queued = adapter._queue.get_nowait()
        assert queued["type"] == "recovery"
        assert queued["success"] is True

    def test_degraded_mode_event(self):
        """Degraded Mode 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        flushed = []
        adapter = AsyncLoggerAdapter(flush_callback=lambda e: flushed.extend(e))
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(event_type=AuditEventType.DEGRADED_MODE_ENTERED)

        observer.on_event(event)
        time.sleep(0.1)

        assert len(flushed) == 1
        assert flushed[0]["type"] == "emergency"
        assert flushed[0]["reason"] == "degraded_mode"

    def test_record_success_event(self):
        """Record 성공 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        adapter = AsyncLoggerAdapter()
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.RECORD_SUCCESS,
            details={"action": "auto_tuning", "audit_id": "audit-123"},
        )

        observer.on_event(event)

        assert adapter._queue.qsize() == 1
        queued = adapter._queue.get_nowait()
        assert queued["type"] == "audit"
        assert queued["success"] is True

    def test_record_failed_event(self):
        """Record 실패 이벤트 변환."""
        from selfhealing.audit.audit_integration import (
            AsyncLoggerAdapter,
            AsyncLoggerObserver,
            AuditEventData,
            AuditEventType,
        )
        
        flushed = []
        adapter = AsyncLoggerAdapter(flush_callback=lambda e: flushed.extend(e))
        observer = AsyncLoggerObserver(adapter)

        event = AuditEventData(
            event_type=AuditEventType.RECORD_FAILED,
            details={"action": "auto_tuning", "error": "DB error"},
        )

        observer.on_event(event)
        time.sleep(0.1)

        assert len(flushed) == 1
        assert flushed[0]["success"] is False
