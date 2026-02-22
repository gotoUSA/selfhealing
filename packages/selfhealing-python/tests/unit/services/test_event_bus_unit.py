"""
Tests for SelfHealingEventBus.
event_bus.py의 이벤트 버스 구독/발행/해제, 히스토리, 통계, 제어 기능을 검증합니다.
"""

from unittest.mock import MagicMock

import pytest

from selfhealing.services.event_bus import (
    EventPriority,
    EventSubscription,
    EventType,
    SelfHealingEvent,
    SelfHealingEventBus,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_event_bus():
    """각 테스트 전후로 이벤트 버스 싱글톤을 초기화."""
    bus = SelfHealingEventBus()
    bus.reset()
    yield
    bus.reset()


@pytest.fixture
def bus():
    """초기화된 이벤트 버스 인스턴스 반환."""
    return SelfHealingEventBus()


def _make_event(
    event_type=EventType.CONFIG_UPDATED,
    data=None,
    source="test",
    priority=EventPriority.NORMAL,
):
    """테스트용 이벤트 생성 헬퍼."""
    return SelfHealingEvent(
        event_type=event_type,
        data=data or {},
        source=source,
        priority=priority,
    )


# =============================================================================
# SelfHealingEvent / EventSubscription Tests
# =============================================================================


class TestSelfHealingEvent:
    """SelfHealingEvent 데이터클래스 테스트."""

    def test_to_dict(self):
        """To dict
        to_dict()가 올바른 딕셔너리를 반환하는지 확인.
        """
        event = _make_event(data={"key": "value"}, source="test_src")
        d = event.to_dict()
        assert d["event_type"] == "config_updated"
        assert d["data"] == {"key": "value"}
        assert d["source"] == "test_src"
        assert "timestamp" in d

    def test_default_priority(self):
        """Default priority
        기본 우선순위가 NORMAL인지 확인.
        """
        event = _make_event()
        assert event.priority == EventPriority.NORMAL

    def test_correlation_id(self):
        """Correlation ID
        correlation_id가 올바르게 설정되는지 확인.
        """
        event = SelfHealingEvent(
            event_type=EventType.CONFIG_UPDATED,
            data={},
            source="test",
            correlation_id="abc-123",
        )
        assert event.correlation_id == "abc-123"
        assert event.to_dict()["correlation_id"] == "abc-123"


class TestEventSubscription:
    """EventSubscription 데이터클래스 테스트."""

    def test_hash_uniqueness(self):
        """Hash uniqueness
        같은 핸들러명의 구독은 동일한 해시를 갖는지 확인.
        """
        handler = lambda e: None
        handler.__name__ = "my_handler"
        sub1 = EventSubscription(
            event_type=EventType.CONFIG_UPDATED,
            handler=handler,
            handler_name="my_handler",
        )
        sub2 = EventSubscription(
            event_type=EventType.CONFIG_UPDATED,
            handler=handler,
            handler_name="my_handler",
        )
        assert hash(sub1) == hash(sub2)


# =============================================================================
# Subscribe / Unsubscribe Tests
# =============================================================================


class TestSubscription:
    """구독 관리 테스트."""

    def test_subscribe(self, bus):
        """Subscribe
        핸들러를 구독하면 구독 목록에 추가되는지 확인.
        """
        handler = MagicMock()
        sub = bus.subscribe(EventType.CONFIG_UPDATED, handler)
        assert isinstance(sub, EventSubscription)
        assert sub.event_type == EventType.CONFIG_UPDATED

    def test_duplicate_subscribe_returns_existing(self, bus):
        """Duplicate subscribe returns existing
        같은 핸들러를 중복 구독하면 기존 구독을 반환하는지 확인.
        """
        handler = MagicMock(__name__="my_handler")
        sub1 = bus.subscribe(EventType.CONFIG_UPDATED, handler)
        sub2 = bus.subscribe(EventType.CONFIG_UPDATED, handler)
        assert sub1 is sub2

    def test_unsubscribe(self, bus):
        """Unsubscribe
        구독 해제가 올바르게 동작하는지 확인.
        """
        handler = MagicMock(__name__="my_handler")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        removed = bus.unsubscribe(EventType.CONFIG_UPDATED, handler)
        assert removed is True

    def test_unsubscribe_not_subscribed(self, bus):
        """Unsubscribe not subscribed
        구독하지 않은 핸들러를 해제하면 False를 반환하는지 확인.
        """
        handler = MagicMock(__name__="my_handler")
        removed = bus.unsubscribe(EventType.CONFIG_UPDATED, handler)
        assert removed is False

    def test_unsubscribe_all(self, bus):
        """Unsubscribe all
        모든 구독을 해제하면 구독 목록이 비어있는지 확인.
        """
        handler1 = MagicMock(__name__="h1")
        handler2 = MagicMock(__name__="h2")
        bus.subscribe(EventType.CONFIG_UPDATED, handler1)
        bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler2)
        bus.unsubscribe_all()
        stats = bus.get_stats()
        assert stats["subscriptions_count"] == 0

    def test_unsubscribe_all_specific_type(self, bus):
        """Unsubscribe all specific type
        특정 이벤트 타입의 모든 구독만 해제되는지 확인.
        """
        handler1 = MagicMock(__name__="h1")
        handler2 = MagicMock(__name__="h2")
        bus.subscribe(EventType.CONFIG_UPDATED, handler1)
        bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler2)
        bus.unsubscribe_all(EventType.CONFIG_UPDATED)

        subs = bus.get_subscriptions()
        assert len(subs) == 1
        assert subs[0]["event_type"] == "error_budget_critical"


# =============================================================================
# Publish Tests
# =============================================================================


class TestPublish:
    """이벤트 발행 테스트."""

    def test_publish_calls_handler(self, bus):
        """Publish calls handler
        이벤트 발행 시 등록된 핸들러가 호출되는지 확인.
        """
        handler = MagicMock(__name__="h")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        event = _make_event()
        count = bus.publish(event)
        assert count == 1
        handler.assert_called_once_with(event)

    def test_publish_no_subscribers(self, bus):
        """Publish no subscribers
        구독자가 없으면 0을 반환하는지 확인.
        """
        event = _make_event()
        count = bus.publish(event)
        assert count == 0

    def test_publish_disabled_bus(self, bus):
        """Publish disabled bus
        비활성화된 버스에서 이벤트 발행 시 0을 반환하는지 확인.
        """
        handler = MagicMock(__name__="h")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        bus.disable()
        event = _make_event()
        count = bus.publish(event)
        assert count == 0
        handler.assert_not_called()

    def test_handler_exception_does_not_break_others(self, bus):
        """Handler exception does not break others
        한 핸들러가 예외를 발생시켜도 나머지 핸들러는 실행되는지 확인.
        """
        bad_handler = MagicMock(__name__="bad", side_effect=RuntimeError("oops"))
        good_handler = MagicMock(__name__="good")
        bus.subscribe(EventType.CONFIG_UPDATED, bad_handler, priority=EventPriority.HIGH)
        bus.subscribe(EventType.CONFIG_UPDATED, good_handler, priority=EventPriority.LOW)
        event = _make_event()
        count = bus.publish(event)
        # bad_handler는 실행됐지만 예외 발생, good_handler도 실행됨
        assert count == 1  # good_handler만 성공

    def test_publish_priority_order(self, bus):
        """Publish priority order
        높은 우선순위의 핸들러가 먼저 호출되는지 확인.
        """
        call_order = []
        low_handler = MagicMock(__name__="low", side_effect=lambda e: call_order.append("low"))
        high_handler = MagicMock(__name__="high", side_effect=lambda e: call_order.append("high"))
        bus.subscribe(EventType.CONFIG_UPDATED, low_handler, priority=EventPriority.LOW)
        bus.subscribe(EventType.CONFIG_UPDATED, high_handler, priority=EventPriority.HIGH)
        bus.publish(_make_event())
        assert call_order == ["high", "low"]

    def test_emit_convenience(self, bus):
        """Emit convenience
        emit() 간편 메서드가 올바르게 동작하는지 확인.
        """
        handler = MagicMock(__name__="h")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        count = bus.emit(
            EventType.CONFIG_UPDATED,
            data={"key": "val"},
            source="test",
        )
        assert count == 1
        handler.assert_called_once()

    def test_disabled_subscription_skipped(self, bus):
        """Disabled subscription skipped
        비활성화된 구독은 건너뛰는지 확인.
        """
        handler = MagicMock(__name__="h")
        sub = bus.subscribe(EventType.CONFIG_UPDATED, handler)
        sub.enabled = False
        count = bus.publish(_make_event())
        assert count == 0
        handler.assert_not_called()


# =============================================================================
# History Tests
# =============================================================================


class TestEventHistory:
    """이벤트 히스토리 테스트."""

    def test_history_recorded(self, bus):
        """History recorded
        발행된 이벤트가 히스토리에 기록되는지 확인.
        """
        bus.publish(_make_event())
        history = bus.get_history()
        assert len(history) == 1
        assert history[0]["event_type"] == "config_updated"

    def test_history_limit(self, bus):
        """History limit
        히스토리 조회 시 limit 파라미터가 동작하는지 확인.
        """
        for _ in range(10):
            bus.publish(_make_event())
        history = bus.get_history(limit=5)
        assert len(history) == 5

    def test_history_filter_by_type(self, bus):
        """History filter by type
        이벤트 타입으로 히스토리를 필터링할 수 있는지 확인.
        """
        bus.publish(_make_event(event_type=EventType.CONFIG_UPDATED))
        bus.publish(_make_event(event_type=EventType.ERROR_BUDGET_CRITICAL))

        history = bus.get_history(event_type=EventType.CONFIG_UPDATED)
        assert len(history) == 1
        assert history[0]["event_type"] == "config_updated"

    def test_clear_history(self, bus):
        """Clear history
        히스토리 초기화가 올바르게 동작하는지 확인.
        """
        bus.publish(_make_event())
        bus.clear_history()
        assert len(bus.get_history()) == 0

    def test_max_history_cap(self, bus):
        """Max history cap
        히스토리가 max_history를 초과하지 않는지 확인.
        """
        bus._max_history = 5
        for _ in range(10):
            bus.publish(_make_event())
        history = bus.get_history(limit=100)
        assert len(history) <= 5


# =============================================================================
# Control Tests
# =============================================================================


class TestEventBusControl:
    """이벤트 버스 제어 테스트."""

    def test_enable_disable(self, bus):
        """Enable disable
        enable/disable이 is_enabled에 반영되는지 확인.
        """
        assert bus.is_enabled() is True
        bus.disable()
        assert bus.is_enabled() is False
        bus.enable()
        assert bus.is_enabled() is True


# =============================================================================
# Statistics Tests
# =============================================================================


class TestEventBusStats:
    """이벤트 버스 통계 테스트."""

    def test_stats_structure(self, bus):
        """Stats structure
        get_stats()가 올바른 키를 포함하는지 확인.
        """
        stats = bus.get_stats()
        assert "enabled" in stats
        assert "subscriptions_count" in stats
        assert "history_count" in stats
        assert "max_history" in stats

    def test_stats_after_subscribe(self, bus):
        """Stats after subscribe
        구독 후 subscriptions_count가 증가하는지 확인.
        """
        handler = MagicMock(__name__="h")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        stats = bus.get_stats()
        assert stats["subscriptions_count"] == 1
        assert stats["event_types_with_subscribers"] == 1

    def test_get_subscriptions_list(self, bus):
        """Get subscriptions list
        get_subscriptions()가 올바른 형식의 리스트를 반환하는지 확인.
        """
        handler = MagicMock(__name__="my_handler")
        bus.subscribe(EventType.CONFIG_UPDATED, handler, priority=EventPriority.HIGH)
        subs = bus.get_subscriptions(event_type=EventType.CONFIG_UPDATED)
        assert len(subs) == 1
        assert subs[0]["handler_name"] == "my_handler"
        assert subs[0]["priority"] == "HIGH"


# =============================================================================
# Singleton Tests
# =============================================================================


class TestEventBusSingleton:
    """SelfHealingEventBus 싱글톤 동작 테스트."""

    def test_singleton_identity(self):
        """Singleton identity
        두 인스턴스가 동일한 객체인지 확인.
        """
        bus1 = SelfHealingEventBus()
        bus2 = SelfHealingEventBus()
        assert bus1 is bus2

    def test_reset_clears_state(self, bus):
        """Reset clears state
        reset()이 모든 상태를 초기화하는지 확인.
        """
        handler = MagicMock(__name__="h")
        bus.subscribe(EventType.CONFIG_UPDATED, handler)
        bus.publish(_make_event())
        bus.disable()

        bus.reset()

        assert bus.is_enabled() is True
        assert bus.get_stats()["subscriptions_count"] == 0
        assert bus.get_stats()["history_count"] == 0


# =============================================================================
# EventType / EventPriority Enum Tests
# =============================================================================


class TestEnums:
    """EventType, EventPriority enum 테스트."""

    def test_event_type_values(self):
        """Event type values
        주요 EventType 값들이 존재하는지 확인.
        """
        assert EventType.EMERGENCY_LEVEL_CHANGED.value == "emergency_level_changed"
        assert EventType.CIRCUIT_BREAKER_OPENED.value == "circuit_breaker_opened"
        assert EventType.CONFIG_UPDATED.value == "config_updated"
        assert EventType.ERROR_BUDGET_CRITICAL.value == "error_budget_critical"

    def test_event_priority_ordering(self):
        """Event priority ordering
        EventPriority 값이 순서대로 정렬되는지 확인.
        """
        assert EventPriority.LOW.value < EventPriority.NORMAL.value
        assert EventPriority.NORMAL.value < EventPriority.HIGH.value
        assert EventPriority.HIGH.value < EventPriority.CRITICAL.value
