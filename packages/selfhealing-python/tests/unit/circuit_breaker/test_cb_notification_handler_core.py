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

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_notification_sent_on_cb_opened(self, mock_delay):
        """CB OPENED 이벤트 발생 시 Celery task로 알림이 위임되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )

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

        # Celery task .delay() 호출 확인
        mock_delay.assert_called_once_with(
            service_name="payment_service",
            trace_id="abc123",
            trace_url="https://jaeger.internal/trace/abc123",
            timestamp="2026-01-06T10:00:00Z",
        )

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_trace_url_included_in_delay_args(self, mock_delay):
        """Celery task 위임 시 trace_url이 전달되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )

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

        call_kwargs = mock_delay.call_args[1]
        assert call_kwargs["trace_id"] == "xyz789"
        assert call_kwargs["trace_url"] == "https://jaeger.internal/trace/xyz789"

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_service_name_passed_to_delay(self, mock_delay):
        """Celery task 위임 시 service_name이 정확히 전달되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "inventory_service"},
            source="test",
        )

        _on_circuit_breaker_opened_notify(event)

        call_kwargs = mock_delay.call_args[1]
        assert call_kwargs["service_name"] == "inventory_service"

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_celery_enqueue_failure_does_not_raise(self, mock_delay):
        """Celery task 위임 실패 시 예외가 전파되지 않는지 확인 (신뢰성 보장)."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )

        mock_delay.side_effect = Exception("Broker connection failed!")

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={"service_name": "failing_service"},
            source="test",
        )

        # 예외가 발생하지 않아야 함
        try:
            _on_circuit_breaker_opened_notify(event)
        except Exception:
            pytest.fail("Celery enqueue failure should not raise exception")

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_unknown_service_name_handled(self, mock_delay):
        """service_name이 없을 때 'unknown'으로 전달되는지 확인."""
        from selfhealing.services.event_bus import (
            _on_circuit_breaker_opened_notify,
            SelfHealingEvent,
            EventType,
        )

        event = SelfHealingEvent(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            data={},  # service_name 없음
            source="test",
        )

        _on_circuit_breaker_opened_notify(event)

        call_kwargs = mock_delay.call_args[1]
        assert call_kwargs["service_name"] == "unknown"


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

    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.collect_cb_open_snapshot.delay")
    @patch("selfhealing.services.event_bus._on_circuit_breaker_opened_throttle")
    @patch("selfhealing.adapters.celery.tasks.circuit_breaker.send_cb_open_notification.delay")
    def test_full_integration_via_emit(self, mock_notify_delay, mock_throttle, mock_snapshot_delay):
        """emit을 통한 전체 흐름 테스트 — Celery task 위임 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
        )

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

        # Celery 알림 task가 위임되었는지 확인
        mock_notify_delay.assert_called_once()

    def test_handler_priority_is_high(self):
        """CB OPENED 핸들러의 우선순위가 HIGH인지 확인."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
            EventPriority,
        )

        register_default_handlers()

        subscriptions = self.bus.get_subscriptions(EventType.CIRCUIT_BREAKER_OPENED)
        notify_handler = next((s for s in subscriptions if s["handler_name"] == "_on_circuit_breaker_opened_notify"), None)

        assert notify_handler is not None
        assert notify_handler["priority"] == EventPriority.HIGH.name
