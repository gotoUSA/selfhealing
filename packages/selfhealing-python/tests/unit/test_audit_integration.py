"""
Audit Integration Module Tests.

통합 연결 모듈 테스트:
1. AsyncLoggerAdapter 테스트
2. Observer 패턴 테스트
3. IntegratedAuditRecorder 테스트
4. CircuitBreaker ↔ AsyncLogger 연동 테스트
"""

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest

from selfhealing.audit.audit_integration import (
    AsyncLoggerAdapter,
    AsyncLoggerConfig,
    AsyncLoggerObserver,
    AuditEventData,
    AuditEventObserver,
    AuditEventType,
    EventSeverity,
    IntegratedAuditRecorder,
    configure_integration,
    create_command_center_callback,
)


# =============================================================================
# AsyncLoggerAdapter Tests
# =============================================================================


class TestAsyncLoggerAdapter:
    """AsyncLoggerAdapter 테스트."""

    def test_init_default_config(self):
        """기본 설정으로 초기화."""
        adapter = AsyncLoggerAdapter()

        assert adapter._config.batch_size == 5
        assert adapter._config.flush_interval_seconds == 2.0
        assert adapter._config.max_queue_size == 5000
        assert not adapter._running

    def test_init_custom_config(self):
        """커스텀 설정으로 초기화."""
        config = AsyncLoggerConfig(
            batch_size=10,
            flush_interval_seconds=1.0,
            max_queue_size=1000,
        )
        adapter = AsyncLoggerAdapter(config=config)

        assert adapter._config.batch_size == 10
        assert adapter._config.flush_interval_seconds == 1.0
        assert adapter._config.max_queue_size == 1000

    def test_start_stop(self):
        """워커 시작/중지."""
        adapter = AsyncLoggerAdapter()

        # 시작
        adapter.start()
        assert adapter._running
        assert adapter._worker_thread is not None
        assert adapter._worker_thread.is_alive()

        # 중복 시작 무시
        adapter.start()
        assert adapter._running

        # 중지
        adapter.stop()
        assert not adapter._running

    def test_log_info_event(self):
        """INFO 이벤트 로깅 (큐에 추가)."""
        adapter = AsyncLoggerAdapter()

        result = adapter.log({"action": "test"}, EventSeverity.INFO)

        assert result is True
        assert adapter._queue.qsize() == 1
        assert adapter._stats["events_logged"] == 1

    def test_log_critical_event_immediate_flush(self):
        """CRITICAL 이벤트 즉시 전송."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        adapter.log({"action": "critical_test"}, EventSeverity.CRITICAL)

        # 즉시 전송 (비동기이므로 잠시 대기)
        time.sleep(0.1)

        assert adapter._stats["immediate_flushes"] == 1
        assert len(flushed_events) == 1
        assert flushed_events[0]["action"] == "critical_test"

    def test_log_warning_event_immediate_flush(self):
        """WARNING 이벤트 즉시 전송."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        adapter.log({"action": "warning_test"}, EventSeverity.WARNING)

        time.sleep(0.1)

        assert adapter._stats["immediate_flushes"] == 1
        assert len(flushed_events) == 1

    def test_log_cb_event(self):
        """Circuit Breaker 이벤트 헬퍼."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        # OPEN 상태는 CRITICAL
        adapter.log_cb_event(service="test_service", state="OPEN", reason="too many failures")

        time.sleep(0.1)

        assert len(flushed_events) == 1
        assert flushed_events[0]["type"] == "circuit_breaker"
        assert flushed_events[0]["service"] == "test_service"
        assert flushed_events[0]["state"] == "OPEN"

    def test_log_recovery_event(self):
        """복구 이벤트 헬퍼."""
        adapter = AsyncLoggerAdapter()

        adapter.log_recovery_event(
            service="test_service",
            recovery_time_ms=150.5,
            success=True,
        )

        assert adapter._queue.qsize() == 1
        event = adapter._queue.get_nowait()
        assert event["type"] == "recovery"
        assert event["recovery_time_ms"] == 150.5

    def test_log_emergency_event(self):
        """Emergency 이벤트 헬퍼."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        adapter.log_emergency_event(
            level="CRITICAL",
            action="trigger",
            reason="all_backends_failed",
        )

        time.sleep(0.1)

        assert len(flushed_events) == 1
        assert flushed_events[0]["type"] == "emergency"

    def test_log_fallback_activated(self):
        """Fallback 활성화 이벤트 헬퍼."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        adapter.log_fallback_activated(
            fallback_type="file",
            reason="primary_failed",
        )

        time.sleep(0.1)

        assert len(flushed_events) == 1
        assert flushed_events[0]["type"] == "fallback_activated"
        assert flushed_events[0]["fallback_type"] == "file"

    def test_log_audit_event(self):
        """감사 이벤트 헬퍼."""
        adapter = AsyncLoggerAdapter()

        adapter.log_audit_event(
            action="auto_tuning",
            success=True,
            audit_id="audit-123",
        )

        assert adapter._queue.qsize() == 1
        event = adapter._queue.get_nowait()
        assert event["type"] == "audit"
        assert event["audit_id"] == "audit-123"

    def test_queue_overflow_drops_event(self):
        """큐 오버플로우 시 이벤트 드롭."""
        config = AsyncLoggerConfig(max_queue_size=2)
        adapter = AsyncLoggerAdapter(config=config)

        # 큐 채우기
        adapter.log({"id": 1}, EventSeverity.INFO)
        adapter.log({"id": 2}, EventSeverity.INFO)

        # 세 번째는 드롭됨
        result = adapter.log({"id": 3}, EventSeverity.INFO)

        assert result is False
        assert adapter._stats["queue_overflows"] == 1

    def test_batch_flush(self):
        """배치 플러시 테스트."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        config = AsyncLoggerConfig(batch_size=3, flush_interval_seconds=0.1)
        adapter = AsyncLoggerAdapter(flush_callback=callback, config=config)
        adapter.start()

        try:
            # 3개 이벤트 추가 (배치 크기 도달)
            adapter.log({"id": 1}, EventSeverity.INFO)
            adapter.log({"id": 2}, EventSeverity.INFO)
            adapter.log({"id": 3}, EventSeverity.INFO)

            # 배치 플러시 대기
            time.sleep(0.3)

            assert len(flushed_events) >= 3
        finally:
            adapter.stop()

    def test_flush_now(self):
        """수동 플러시."""
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)

        # 이벤트 추가
        adapter.log({"id": 1}, EventSeverity.INFO)
        adapter.log({"id": 2}, EventSeverity.INFO)

        # 수동 플러시
        count = adapter.flush_now()

        assert count == 2
        assert len(flushed_events) == 2

    def test_get_stats(self):
        """통계 조회."""
        adapter = AsyncLoggerAdapter()
        adapter.start()

        try:
            adapter.log({"test": 1}, EventSeverity.INFO)

            stats = adapter.get_stats()

            assert stats["events_logged"] == 1
            assert stats["is_running"] is True
        finally:
            adapter.stop()

    def test_reset_stats(self):
        """통계 초기화."""
        adapter = AsyncLoggerAdapter()
        adapter.log({"test": 1}, EventSeverity.INFO)

        adapter.reset_stats()

        assert adapter._stats["events_logged"] == 0

    def test_configure_runtime(self):
        """런타임 설정 변경."""
        adapter = AsyncLoggerAdapter()

        new_callback = Mock()
        adapter.configure(
            flush_callback=new_callback,
            batch_size=20,
            flush_interval=5.0,
        )

        assert adapter._flush_callback == new_callback
        assert adapter._config.batch_size == 20
        assert adapter._config.flush_interval_seconds == 5.0

    def test_thread_safety(self):
        """스레드 안전성 테스트."""
        flushed_events = []
        lock = threading.Lock()

        def callback(events):
            with lock:
                flushed_events.extend(events)

        adapter = AsyncLoggerAdapter(flush_callback=callback)
        adapter.start()

        try:
            # 여러 스레드에서 동시 로깅
            def log_events(start_id):
                for i in range(100):
                    adapter.log({"id": start_id + i}, EventSeverity.INFO)

            threads = [
                threading.Thread(target=log_events, args=(i * 100,))
                for i in range(5)
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # 플러시
            adapter.flush_now()
            time.sleep(0.5)

            # 이벤트 손실 없이 처리되어야 함
            assert adapter._stats["events_logged"] == 500
        finally:
            adapter.stop()


# =============================================================================
# Observer Tests
# =============================================================================


class TestAuditEventObserver:
    """Observer 패턴 테스트."""

    def test_observer_interface(self):
        """Observer 인터페이스 검증."""

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


# =============================================================================
# IntegratedAuditRecorder Tests
# =============================================================================


class TestIntegratedAuditRecorder:
    """IntegratedAuditRecorder 테스트."""

    def _create_mock_recorder(self):
        """Mock ResilientContinuousAuditRecorder 생성."""
        mock = MagicMock()
        mock._circuit_breaker = MagicMock()
        mock._circuit_breaker.state = MagicMock()
        mock._circuit_breaker.state.value = "closed"
        mock._record_with_integrity = MagicMock(return_value="audit-12345")
        mock.get_health_status = MagicMock(return_value={"healthy": True})
        mock.start = MagicMock()
        mock.stop = MagicMock()
        return mock

    def test_attach_observer(self):
        """Observer 등록."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        observer = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer)

        assert len(integrated._observers) == 1

    def test_detach_observer(self):
        """Observer 해제."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        observer = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer)
        integrated.detach_observer(observer)

        assert len(integrated._observers) == 0

    def test_attach_async_logger(self):
        """AsyncLoggerAdapter 연결."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        async_logger = AsyncLoggerAdapter()
        integrated.attach_async_logger(async_logger)

        assert integrated._async_logger == async_logger
        assert len(integrated._observers) == 1  # AsyncLoggerObserver 추가됨
        assert async_logger._running  # 자동 시작

        async_logger.stop()

    def test_notify_observers(self):
        """Observer 알림."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        observer1 = Mock(spec=AuditEventObserver)
        observer2 = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer1)
        integrated.attach_observer(observer2)

        event = AuditEventData(event_type=AuditEventType.CIRCUIT_OPENED)
        integrated._notify_observers(event)

        observer1.on_event.assert_called_once_with(event)
        observer2.on_event.assert_called_once_with(event)

    def test_record_with_events_success(self):
        """record_with_events 성공 시 이벤트 전파."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        observer = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer)

        entry = Mock()
        entry.action = "test_action"

        audit_id = integrated.record_with_events(entry)

        assert audit_id == "audit-12345"
        # RECORD_SUCCESS 이벤트가 전파되어야 함
        observer.on_event.assert_called()
        call_args = observer.on_event.call_args[0][0]
        assert call_args.event_type == AuditEventType.RECORD_SUCCESS

    def test_record_with_events_failure(self):
        """record_with_events 실패 시 이벤트 전파."""
        mock_recorder = self._create_mock_recorder()
        mock_recorder._record_with_integrity.side_effect = Exception("DB error")

        integrated = IntegratedAuditRecorder(mock_recorder)

        observer = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer)

        entry = Mock()
        entry.action = "test_action"

        with pytest.raises(Exception, match="DB error"):
            integrated.record_with_events(entry)

        # RECORD_FAILED 이벤트가 전파되어야 함
        observer.on_event.assert_called()
        call_args = observer.on_event.call_args[0][0]
        assert call_args.event_type == AuditEventType.RECORD_FAILED

    def test_circuit_state_change_detection(self):
        """Circuit Breaker 상태 변경 감지."""
        from selfhealing.audit.resilience import CircuitState
        
        mock_recorder = self._create_mock_recorder()
        # CircuitState enum을 직접 사용
        mock_recorder._circuit_breaker.state = CircuitState.CLOSED
        
        integrated = IntegratedAuditRecorder(mock_recorder)

        observer = Mock(spec=AuditEventObserver)
        integrated.attach_observer(observer)

        entry = Mock()
        entry.action = "test_action"

        # 첫 번째 record - CLOSED 상태
        integrated.record_with_events(entry)

        # Circuit OPEN으로 변경
        mock_recorder._circuit_breaker.state = CircuitState.OPEN

        # 두 번째 record - OPEN 감지
        integrated.record_with_events(entry)

        # CIRCUIT_OPENED 이벤트가 전파되어야 함
        calls = observer.on_event.call_args_list
        event_types = [call[0][0].event_type for call in calls]
        assert AuditEventType.CIRCUIT_OPENED in event_types

    def test_get_health_status_with_async_logger(self):
        """AsyncLogger 포함 헬스 상태."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        async_logger = AsyncLoggerAdapter()
        integrated.attach_async_logger(async_logger)

        try:
            health = integrated.get_health_status()

            assert "async_logger" in health
            assert "observers_count" in health
            assert health["observers_count"] == 1
        finally:
            async_logger.stop()

    def test_start_stop(self):
        """시작/중지."""
        mock_recorder = self._create_mock_recorder()
        integrated = IntegratedAuditRecorder(mock_recorder)

        async_logger = AsyncLoggerAdapter()
        integrated.attach_async_logger(async_logger)

        integrated.start()
        mock_recorder.start.assert_called()

        integrated.stop()
        mock_recorder.stop.assert_called()
        assert not async_logger._running


# =============================================================================
# Convenience Functions Tests
# =============================================================================


class TestConvenienceFunctions:
    """편의 함수 테스트."""

    def test_configure_integration(self):
        """configure_integration 함수."""
        mock_recorder = MagicMock()
        mock_recorder._circuit_breaker = MagicMock()
        mock_recorder._circuit_breaker.state = MagicMock()
        mock_recorder._circuit_breaker.state.value = "closed"

        callback = Mock()

        integrated = configure_integration(
            resilient_recorder=mock_recorder,
            flush_callback=callback,
        )

        assert isinstance(integrated, IntegratedAuditRecorder)
        assert integrated._async_logger is not None
        assert len(integrated._observers) == 1

        integrated._async_logger.stop()

    def test_configure_integration_without_callback(self):
        """callback 없이 configure_integration."""
        mock_recorder = MagicMock()
        mock_recorder._circuit_breaker = MagicMock()

        integrated = configure_integration(resilient_recorder=mock_recorder)

        assert integrated._async_logger is None
        assert len(integrated._observers) == 0

    def test_create_command_center_callback(self):
        """Command Center 콜백 생성."""
        callback = create_command_center_callback(
            endpoint="http://localhost:8000/api/events",
            timeout_seconds=3.0,
        )

        assert callable(callback)

    @patch("urllib.request.urlopen")
    def test_command_center_callback_success(self, mock_urlopen):
        """Command Center 콜백 성공."""
        mock_response = Mock()
        mock_response.status = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        callback = create_command_center_callback(
            endpoint="http://localhost:8000/api/events",
        )

        # 예외 없이 실행되어야 함
        callback([{"type": "test"}])

        mock_urlopen.assert_called_once()


# =============================================================================
# EventSeverity Tests
# =============================================================================


class TestEventSeverity:
    """EventSeverity enum 테스트."""

    def test_severity_values(self):
        """심각도 값 검증."""
        assert EventSeverity.DEBUG.value == 0
        assert EventSeverity.INFO.value == 1
        assert EventSeverity.WARNING.value == 2
        assert EventSeverity.CRITICAL.value == 3

    def test_severity_comparison(self):
        """심각도 비교."""
        assert EventSeverity.CRITICAL.value > EventSeverity.WARNING.value
        assert EventSeverity.WARNING.value > EventSeverity.INFO.value


# =============================================================================
# AuditEventType Tests
# =============================================================================


class TestAuditEventType:
    """AuditEventType enum 테스트."""

    def test_event_types_exist(self):
        """필수 이벤트 유형 존재 확인."""
        assert AuditEventType.RECORD_SUCCESS
        assert AuditEventType.RECORD_FAILED
        assert AuditEventType.CIRCUIT_OPENED
        assert AuditEventType.CIRCUIT_CLOSED
        assert AuditEventType.FALLBACK_ACTIVATED
        assert AuditEventType.SYSLOG_ACTIVATED
        assert AuditEventType.PRIMARY_RECOVERED
        assert AuditEventType.DEGRADED_MODE_ENTERED


# =============================================================================
# AuditEventData Tests
# =============================================================================


class TestAuditEventData:
    """AuditEventData dataclass 테스트."""

    def test_default_timestamp(self):
        """기본 타임스탬프 생성."""
        event = AuditEventData(event_type=AuditEventType.CIRCUIT_OPENED)

        assert event.timestamp is not None
        assert isinstance(event.timestamp, datetime)

    def test_custom_details(self):
        """커스텀 details."""
        event = AuditEventData(
            event_type=AuditEventType.FALLBACK_ACTIVATED,
            details={"fallback_type": "file", "reason": "primary_failed"},
        )

        assert event.details["fallback_type"] == "file"
        assert event.details["reason"] == "primary_failed"


# =============================================================================
# Integration Scenarios
# =============================================================================


class TestIntegrationScenarios:
    """통합 시나리오 테스트."""

    def test_full_flow_circuit_open_to_recovery(self):
        """전체 흐름: Circuit Open → 복구."""
        from selfhealing.audit.resilience import CircuitState
        
        # 1. Setup
        flushed_events = []

        def callback(events):
            flushed_events.extend(events)

        mock_recorder = MagicMock()
        mock_recorder._circuit_breaker = MagicMock()
        # CircuitState enum 사용
        mock_recorder._circuit_breaker.state = CircuitState.CLOSED
        mock_recorder._record_with_integrity = MagicMock(return_value="audit-001")
        mock_recorder.get_health_status = MagicMock(return_value={"healthy": True})

        async_logger = AsyncLoggerAdapter(flush_callback=callback)
        integrated = IntegratedAuditRecorder(mock_recorder)
        integrated.attach_async_logger(async_logger)

        entry = Mock()
        entry.action = "test_action"

        try:
            # 2. 정상 동작
            integrated.record_with_events(entry)

            # 3. Circuit OPEN
            mock_recorder._circuit_breaker.state = CircuitState.OPEN
            integrated.record_with_events(entry)

            # 4. 복구 (HALF_OPEN → CLOSED)
            mock_recorder._circuit_breaker.state = CircuitState.HALF_OPEN
            integrated.record_with_events(entry)

            mock_recorder._circuit_breaker.state = CircuitState.CLOSED
            integrated.record_with_events(entry)

            time.sleep(0.2)

            # 5. 이벤트 검증
            event_types = [e.get("type") for e in flushed_events]
            states = [e.get("state") for e in flushed_events if e.get("type") == "circuit_breaker"]

            # CB 이벤트가 있어야 함
            assert "circuit_breaker" in event_types
            assert "OPEN" in states

        finally:
            async_logger.stop()

    def test_multiple_observers(self):
        """다중 Observer 동시 동작."""
        mock_recorder = MagicMock()
        mock_recorder._circuit_breaker = MagicMock()
        mock_recorder._circuit_breaker.state = MagicMock()
        mock_recorder._circuit_breaker.state.value = "closed"
        mock_recorder._record_with_integrity = MagicMock(return_value="audit-001")

        integrated = IntegratedAuditRecorder(mock_recorder)

        # 여러 Observer 등록
        observer1 = Mock(spec=AuditEventObserver)
        observer2 = Mock(spec=AuditEventObserver)
        observer3 = Mock(spec=AuditEventObserver)

        integrated.attach_observer(observer1)
        integrated.attach_observer(observer2)
        integrated.attach_observer(observer3)

        entry = Mock()
        entry.action = "test"

        integrated.record_with_events(entry)

        # 모든 Observer가 호출되어야 함
        observer1.on_event.assert_called()
        observer2.on_event.assert_called()
        observer3.on_event.assert_called()

    def test_observer_error_isolation(self):
        """Observer 에러 격리."""
        mock_recorder = MagicMock()
        mock_recorder._circuit_breaker = MagicMock()
        mock_recorder._circuit_breaker.state = MagicMock()
        mock_recorder._circuit_breaker.state.value = "closed"
        mock_recorder._record_with_integrity = MagicMock(return_value="audit-001")

        integrated = IntegratedAuditRecorder(mock_recorder)

        # 에러 발생하는 Observer
        error_observer = Mock(spec=AuditEventObserver)
        error_observer.on_event.side_effect = Exception("Observer error")

        # 정상 Observer
        normal_observer = Mock(spec=AuditEventObserver)

        integrated.attach_observer(error_observer)
        integrated.attach_observer(normal_observer)

        entry = Mock()
        entry.action = "test"

        # 에러가 발생해도 다른 Observer는 호출됨
        integrated.record_with_events(entry)

        normal_observer.on_event.assert_called()
