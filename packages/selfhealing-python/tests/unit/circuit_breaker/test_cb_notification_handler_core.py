"""
Circuit Breaker Notification Handler Tests.

Tests for:
1. _on_circuit_breaker_opened_notify - CB OPEN 알림 핸들러
2. EventBus 핸들러 등록 확인
3. Deduplication (dedup_key) 적용 확인
4. trace_url 노출 확인
5. 알림 실패 시 예외 격리 확인

핵심 알림 연결
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import List


class TestCircuitBreakerOpenedNotifyHandler:
    """_on_circuit_breaker_opened_notify 핸들러 테스트."""
    
    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()
    
    def test_handler_exists(self):
        """_on_circuit_breaker_opened_notify 핸들러가 존재하는지 확인."""
        from selfhealing.services.event_bus import _on_circuit_breaker_opened_notify
        
        assert callable(_on_circuit_breaker_opened_notify)
    
    def test_handler_registered_on_default_handlers(self):
        """register_default_handlers에서 CB OPENED 핸들러가 등록되는지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            _on_circuit_breaker_opened_notify,
        )
        
        register_default_handlers()
        
        # CIRCUIT_BREAKER_OPENED 이벤트에 핸들러가 등록되었는지 확인
        subscriptions = self.bus.get_subscriptions(EventType.CIRCUIT_BREAKER_OPENED)
        handler_names = [s["handler_name"] for s in subscriptions]
        
        assert "_on_circuit_breaker_opened_notify" in handler_names
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_notification_sent_on_cb_opened(self, mock_get_manager):
        """CB OPENED 이벤트 발생 시 알림이 전송되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={
                "service_name": "payment_service",
                "trace_id": "abc123",
                "trace_url": "https://jaeger.internal/trace/abc123",
                "timestamp": "2026-01-06T10:00:00Z",
            },
            source="circuit_breaker_service",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        # notify가 호출되었는지 확인
        mock_manager.notify.assert_called_once()
        
        # 호출된 payload 확인
        call_args = mock_manager.notify.call_args
        payload = call_args[0][0]  # 첫 번째 위치 인자
        
        assert "payment_service" in payload.title
        assert payload.category.value == "circuit_breaker"
        assert payload.priority.value == "high"
        assert payload.dedup_key == "cb:payment_service:open"
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_trace_url_included_in_metadata(self, mock_get_manager):
        """알림 metadata에 trace_url이 포함되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={
                "service_name": "order_service",
                "trace_id": "xyz789",
                "trace_url": "https://jaeger.internal/trace/xyz789",
            },
            source="circuit_breaker_service",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        call_args = mock_manager.notify.call_args
        payload = call_args[0][0]
        
        assert payload.metadata["trace_id"] == "xyz789"
        assert payload.metadata["trace_url"] == "https://jaeger.internal/trace/xyz789"
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_dedup_key_format(self, mock_get_manager):
        """dedup_key가 올바른 형식으로 생성되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "inventory_service"},
            source="test",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        call_args = mock_manager.notify.call_args
        payload = call_args[0][0]
        
        # 문서 명시: dedup_key=f"cb:{service_name}:open"
        assert payload.dedup_key == "cb:inventory_service:open"
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_notification_failure_does_not_raise(self, mock_get_manager):
        """알림 실패 시 예외가 전파되지 않는지 확인 (신뢰성 보장)."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_manager.notify.side_effect = Exception("Notification failed!")
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "failing_service"},
            source="test",
        )
        
        # 예외가 발생하지 않아야 함
        try:
            _on_circuit_breaker_opened_notify(event)
        except Exception:
            pytest.fail("Notification failure should not raise exception")
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_unknown_service_name_handled(self, mock_get_manager):
        """service_name이 없을 때 'unknown'으로 처리되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={},  # service_name 없음
            source="test",
        )
        
        _on_circuit_breaker_opened_notify(event)
        
        call_args = mock_manager.notify.call_args
        payload = call_args[0][0]
        
        assert "unknown" in payload.title
        assert payload.dedup_key == "cb:unknown:open"


class TestEventBusIntegration:
    """EventBus와 CB 알림 핸들러 통합 테스트."""
    
    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()
    
    @patch("selfhealing.services.unified_notification.get_unified_notification_manager")
    def test_full_integration_via_emit(self, mock_get_manager):
        """emit을 통한 전체 흐름 테스트."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
        )
        
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        
        register_default_handlers()
        
        # emit을 통해 이벤트 발행
        handlers_called = self.bus.emit(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={
                "service_name": "user_service",
                "trace_id": "trace123",
                "trace_url": "https://jaeger.internal/trace/trace123",
            },
            source="circuit_breaker_service",
        )
        
        # 핸들러가 호출되었는지 확인
        assert handlers_called >= 1
        
        # 알림이 전송되었는지 확인
        mock_manager.notify.assert_called_once()
    
    def test_handler_priority_is_high(self):
        """CB OPENED 핸들러의 우선순위가 HIGH인지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            EventPriority,
        )
        
        register_default_handlers()
        
        subscriptions = self.bus.get_subscriptions(EventType.CIRCUIT_BREAKER_OPENED)
        notify_handler = next(
            (s for s in subscriptions if s["handler_name"] == "_on_circuit_breaker_opened_notify"),
            None
        )
        
        assert notify_handler is not None
        assert notify_handler["priority"] == EventPriority.HIGH.name
