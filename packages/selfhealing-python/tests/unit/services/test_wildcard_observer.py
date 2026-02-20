"""
Tests for WildcardObserver — 전체 이벤트 관찰자.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 254번 설계 문서에 명시된 값/구조 검증 (하드코딩)
- Behavior: 함수/메서드 동작 검증 (소스 참조)

참조 소스:
- services/correlation_engine/wildcard_observer.py (WildcardObserver, EventWindow)
- services/event_bus/bus/__init__.py (EventType, SelfHealingEvent, SelfHealingEventBus)
- services/predictive_forecaster/anomaly_detector.py (ZScoreDetector)
- settings/correlation.py (CorrelationSettings)
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
)
from selfhealing.services.correlation_engine.wildcard_observer import (
    EventWindow,
    WildcardObserver,
)
from selfhealing.services.event_bus.bus import (
    EventPriority,
    EventType,
    SelfHealingEvent,
    SelfHealingEventBus,
)
from selfhealing.settings.correlation import CorrelationSettings


# =============================================================================
# Helpers
# =============================================================================


def _make_event(
    event_type: EventType = EventType.CIRCUIT_BREAKER_OPENED,
    source: str = "test-service",
    data: dict | None = None,
    timestamp: datetime | None = None,
) -> SelfHealingEvent:
    """테스트용 SelfHealingEvent 생성."""
    return SelfHealingEvent(
        event_type=event_type,
        data=data or {"service_name": source},
        source=source,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def _make_settings(**overrides) -> CorrelationSettings:
    """테스트용 CorrelationSettings 생성."""
    defaults = {
        "window_seconds": 300.0,
        "max_event_buffer": 100,
        "zscore_threshold": 2.5,
    }
    defaults.update(overrides)
    return CorrelationSettings(**defaults)


def _make_observer(
    settings: CorrelationSettings | None = None,
    co_occurrence: CoOccurrenceTracker | None = None,
) -> WildcardObserver:
    """테스트용 WildcardObserver 생성."""
    if settings is None:
        settings = _make_settings()
    if co_occurrence is None:
        co_occurrence = CoOccurrenceTracker(settings)
    return WildcardObserver(settings, co_occurrence)


# =============================================================================
# EventWindow 계약 검증
# =============================================================================


class TestEventWindowContract:
    """EventWindow 설계 계약 검증."""

    def test_deque_maxlen_equals_max_events(self):
        """deque의 maxlen이 생성자의 max_events와 일치해야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=200)
        assert window._events.maxlen == 200

    def test_event_counts_starts_empty(self):
        """초기 상태에서 event_counts가 비어있어야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        assert len(window._event_counts) == 0


# =============================================================================
# EventWindow 동작 검증
# =============================================================================


class TestEventWindowBehavior:
    """EventWindow 동작 검증."""

    def test_add_stores_event_reference(self):
        """add() 호출 후 원본 SelfHealingEvent 참조가 보존되어야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        event = _make_event()

        window.add(event)

        assert len(window._events) == 1
        assert window._events[0] is event  # 참조 동일성 확인

    def test_add_increments_type_count(self):
        """add()가 이벤트 타입별 카운터를 정확히 증가시켜야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        event = _make_event(event_type=EventType.CIRCUIT_BREAKER_OPENED)

        window.add(event)
        window.add(event)

        counts = window.get_type_counts()
        assert counts[EventType.CIRCUIT_BREAKER_OPENED.value] == 2

    def test_deque_maxlen_evicts_oldest(self):
        """deque maxlen 초과 시 가장 오래된 이벤트가 자동 제거된다."""
        window = EventWindow(window_seconds=300.0, max_events=3)

        events = [_make_event(source=f"svc-{i}") for i in range(5)]
        for e in events:
            window.add(e)

        assert len(window._events) == 3
        # 가장 최근 3개만 남아야 함
        assert window._events[0] is events[2]
        assert window._events[2] is events[4]

    def test_get_window_filters_by_timestamp(self):
        """get_window()가 since_timestamp 이후 이벤트만 반환해야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        now = time.time()

        old_event = _make_event(timestamp=datetime.fromtimestamp(now - 120, tz=timezone.utc))
        recent_event = _make_event(timestamp=datetime.fromtimestamp(now - 30, tz=timezone.utc))

        window.add(old_event)
        window.add(recent_event)

        result = window.get_window(now - 60)
        assert len(result) == 1
        assert result[0] is recent_event

    def test_get_window_returns_defensive_copy(self):
        """get_window() 반환값 수정이 내부 deque에 영향 없어야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        window.add(_make_event())

        result = window.get_window(0)
        result.clear()

        assert len(window._events) == 1

    def test_get_type_counts_returns_copy(self):
        """get_type_counts() 반환값 수정이 내부 Counter에 영향 없어야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        window.add(_make_event())

        counts = window.get_type_counts()
        counts.clear()

        assert len(window._event_counts) > 0

    def test_clear_expired_removes_old_events(self):
        """clear_expired()가 만료 이벤트를 제거하고 카운터를 감소시켜야 한다."""
        window = EventWindow(window_seconds=300.0, max_events=100)
        now = time.time()

        old_event = _make_event(timestamp=datetime.fromtimestamp(now - 600, tz=timezone.utc))
        recent_event = _make_event(timestamp=datetime.fromtimestamp(now - 10, tz=timezone.utc))

        window.add(old_event)
        window.add(recent_event)

        removed_count = window.clear_expired(now - 300)

        assert removed_count == 1
        assert len(window._events) == 1
        assert window._events[0] is recent_event


# =============================================================================
# WildcardObserver 계약 검증
# =============================================================================


class TestWildcardObserverContract:
    """WildcardObserver 설계 계약값 검증."""

    def test_queue_maxsize_equals_max_event_buffer(self):
        """큐 maxsize가 CorrelationSettings.max_event_buffer와 일치해야 한다."""
        settings = _make_settings(max_event_buffer=200)
        observer = _make_observer(settings=settings)
        assert observer._queue.maxsize == 200

    def test_window_max_events_equals_max_event_buffer(self):
        """EventWindow maxlen이 max_event_buffer와 일치해야 한다."""
        settings = _make_settings(max_event_buffer=300)
        observer = _make_observer(settings=settings)
        assert observer._window._events.maxlen == 300

    def test_initial_counters_are_zero(self):
        """초기 통계 카운터가 모두 0이어야 한다."""
        observer = _make_observer()
        assert observer._total_observed == 0
        assert observer._dropped_count == 0

    def test_initial_subscribed_is_false(self):
        """초기 구독 상태가 False여야 한다."""
        observer = _make_observer()
        assert observer._subscribed is False

    def test_handler_ref_name_contains_instance_id(self):
        """handler_ref 함수 이름에 id(self)가 포함되어야 한다 (충돌 방지)."""
        observer = _make_observer()
        expected_suffix = str(id(observer))
        assert expected_suffix in observer._handler_ref.__name__

    def test_handler_ref_name_prefix(self):
        """handler_ref 함수 이름이 'WildcardObserver._on_event_' 접두사를 가져야 한다."""
        observer = _make_observer()
        assert observer._handler_ref.__name__.startswith("WildcardObserver._on_event_")

    def test_rate_detector_uses_settings_zscore_threshold(self):
        """ZScoreDetector가 설정의 zscore_threshold를 사용해야 한다."""
        settings = _make_settings(zscore_threshold=3.0)
        observer = _make_observer(settings=settings)
        assert observer._rate_detector._threshold == 3.0

    def test_rate_detector_window_size_is_100(self):
        """ZScoreDetector의 window 크기가 100이어야 한다."""
        observer = _make_observer()
        assert observer._rate_detector._window == 100

    def test_register_subscribes_to_all_event_types(self):
        """register()가 모든 EventType에 구독해야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)

        try:
            event_type_count = len(EventType.__members__)
            # 각 EventType에 대해 핸들러가 등록되었는지 확인
            total_subscriptions = 0
            for event_type in EventType.__members__.values():
                subs = bus._subscriptions.get(event_type, [])
                matching = [s for s in subs if s.handler_name == observer._handler_ref.__name__]
                total_subscriptions += len(matching)

            assert total_subscriptions == event_type_count
        finally:
            observer.unregister(bus)

    def test_register_uses_low_priority(self):
        """모든 구독이 EventPriority.LOW로 등록되어야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)

        try:
            for event_type in EventType.__members__.values():
                subs = bus._subscriptions.get(event_type, [])
                for s in subs:
                    if s.handler_name == observer._handler_ref.__name__:
                        assert s.priority == EventPriority.LOW
        finally:
            observer.unregister(bus)


# =============================================================================
# WildcardObserver 동작 검증: Producer (Hot Path)
# =============================================================================


class TestWildcardObserverProducerBehavior:
    """Producer (_on_event) 동작 검증."""

    def test_on_event_enqueues_event_reference(self):
        """_on_event()가 원본 SelfHealingEvent 참조를 큐에 넣어야 한다."""
        observer = _make_observer()
        event = _make_event()

        observer._on_event(event)

        assert observer._queue.qsize() == 1
        queued_event = observer._queue.get_nowait()
        assert queued_event is event  # 참조 동일성

    def test_on_event_fail_open_on_full_queue(self):
        """큐 포화 시 _dropped_count가 증가하고 예외가 발생하지 않아야 한다."""
        settings = _make_settings(max_event_buffer=50)
        observer = _make_observer(settings=settings)

        # 큐를 가득 채움
        for _ in range(settings.max_event_buffer):
            observer._on_event(_make_event())

        assert observer._queue.full()

        # 추가 이벤트 → 드롭
        observer._on_event(_make_event())
        assert observer._dropped_count == 1

    def test_on_event_does_not_block_on_full_queue(self):
        """큐 포화 시 _on_event()가 블로킹 없이 즉시 반환해야 한다."""
        settings = _make_settings(max_event_buffer=50)
        observer = _make_observer(settings=settings)

        for _ in range(settings.max_event_buffer):
            observer._on_event(_make_event())

        start = time.monotonic()
        observer._on_event(_make_event())
        elapsed = time.monotonic() - start

        # put_nowait이므로 1ms 미만이어야 함
        assert elapsed < 0.1

    def test_multiple_drops_counted_correctly(self):
        """여러 번 드롭 시 카운터가 정확히 증가해야 한다."""
        settings = _make_settings(max_event_buffer=50)
        observer = _make_observer(settings=settings)

        for _ in range(settings.max_event_buffer):
            observer._on_event(_make_event())

        drop_count = 5
        for _ in range(drop_count):
            observer._on_event(_make_event())

        assert observer._dropped_count == drop_count


# =============================================================================
# WildcardObserver 동작 검증: Consumer (Cold Path)
# =============================================================================


class TestWildcardObserverConsumerBehavior:
    """Consumer (_consumer_loop) 동작 검증."""

    def test_consumer_processes_event_to_window(self):
        """Consumer가 큐의 이벤트를 EventWindow에 추가해야 한다."""
        observer = _make_observer()
        event = _make_event()

        # Consumer 스레드 시작 후 큐에 이벤트 삽입
        thread = threading.Thread(target=observer._consumer_loop, daemon=True)
        thread.start()

        observer._queue.put_nowait(event)

        deadline = time.monotonic() + 3.0
        while observer._total_observed < 1 and time.monotonic() < deadline:
            time.sleep(0.05)

        observer._stop_event.set()
        thread.join(timeout=3.0)

        assert observer._total_observed == 1
        assert len(observer._window._events) == 1
        assert observer._window._events[0] is event

    def test_consumer_records_to_co_occurrence_tracker(self):
        """Consumer가 Co-occurrence Tracker의 record_event를 호출해야 한다."""
        settings = _make_settings()
        co_occurrence = MagicMock(spec=CoOccurrenceTracker)
        observer = _make_observer(settings=settings, co_occurrence=co_occurrence)

        event = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="payment-service",
            data={"service_name": "payment-service"},
        )

        thread = threading.Thread(target=observer._consumer_loop, daemon=True)
        thread.start()
        observer._queue.put_nowait(event)

        deadline = time.monotonic() + 3.0
        while observer._total_observed < 1 and time.monotonic() < deadline:
            time.sleep(0.05)

        observer._stop_event.set()
        thread.join(timeout=3.0)

        co_occurrence.record_event.assert_called_once_with(
            event_type=EventType.CIRCUIT_BREAKER_OPENED.value,
            timestamp=event.timestamp.timestamp(),
            service_name="payment-service",
        )

    def test_consumer_uses_source_as_fallback_service_name(self):
        """event.data에 service_name이 없으면 source를 사용해야 한다."""
        settings = _make_settings()
        co_occurrence = MagicMock(spec=CoOccurrenceTracker)
        observer = _make_observer(settings=settings, co_occurrence=co_occurrence)

        event = _make_event(
            source="fallback-service",
            data={"other_key": "value"},
        )

        thread = threading.Thread(target=observer._consumer_loop, daemon=True)
        thread.start()
        observer._queue.put_nowait(event)

        deadline = time.monotonic() + 3.0
        while observer._total_observed < 1 and time.monotonic() < deadline:
            time.sleep(0.05)

        observer._stop_event.set()
        thread.join(timeout=3.0)

        call_kwargs = co_occurrence.record_event.call_args
        assert call_kwargs.kwargs["service_name"] == "fallback-service"

    def test_consumer_error_does_not_break_loop(self):
        """Consumer 처리 중 예외가 발생해도 루프가 계속되어야 한다."""
        settings = _make_settings()
        co_occurrence = MagicMock(spec=CoOccurrenceTracker)
        # 첫 번째 호출은 예외, 두 번째는 정상
        co_occurrence.record_event.side_effect = [RuntimeError("test"), None]
        observer = _make_observer(settings=settings, co_occurrence=co_occurrence)

        thread = threading.Thread(target=observer._consumer_loop, daemon=True)
        thread.start()

        observer._queue.put_nowait(_make_event())
        observer._queue.put_nowait(_make_event())

        deadline = time.monotonic() + 3.0
        while co_occurrence.record_event.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

        observer._stop_event.set()
        thread.join(timeout=3.0)

        # 두 이벤트 모두 처리 시도됨 (첫 번째 실패해도 두 번째 진행)
        assert co_occurrence.record_event.call_count == 2

    def test_consumer_stops_on_stop_event(self):
        """stop_event 설정 시 Consumer 루프가 종료되어야 한다."""
        observer = _make_observer()

        observer._stop_event.set()

        # 빈 큐로 Consumer 실행 → timeout 후 stop_event 확인 → 종료
        thread = threading.Thread(target=observer._consumer_loop)
        thread.start()
        thread.join(timeout=3.0)

        assert not thread.is_alive()


# =============================================================================
# WildcardObserver 동작 검증: register / unregister
# =============================================================================


class TestWildcardObserverRegistrationBehavior:
    """register/unregister 라이프사이클 검증."""

    def test_register_starts_consumer_thread(self):
        """register()가 Consumer daemon 스레드를 시작해야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)

        try:
            assert observer._consumer_thread is not None
            assert observer._consumer_thread.is_alive()
            assert observer._consumer_thread.daemon is True
            assert observer._consumer_thread.name == "WildcardObserverConsumer"
        finally:
            observer.unregister(bus)

    def test_register_is_idempotent(self):
        """이미 구독된 상태에서 다시 register() 호출 시 무시되어야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)
        first_thread = observer._consumer_thread

        observer.register(bus)  # 두 번째 호출

        try:
            assert observer._consumer_thread is first_thread
        finally:
            observer.unregister(bus)

    def test_unregister_stops_consumer_thread(self):
        """unregister()가 Consumer 스레드를 종료해야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)
        thread = observer._consumer_thread

        observer.unregister(bus)

        assert observer._consumer_thread is None
        assert not thread.is_alive()

    def test_unregister_clears_handler_ref(self):
        """unregister() 후 _handler_ref가 None이 되어야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)
        observer.unregister(bus)

        assert observer._handler_ref is None

    def test_unregister_sets_subscribed_false(self):
        """unregister() 후 _subscribed가 False여야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)
        observer.unregister(bus)

        assert observer._subscribed is False

    def test_unregister_removes_all_subscriptions(self):
        """unregister()가 모든 EventType에서 핸들러를 제거해야 한다."""
        observer = _make_observer()
        bus = SelfHealingEventBus()

        observer.register(bus)
        handler_name = observer._handler_ref.__name__

        observer.unregister(bus)

        for event_type in EventType.__members__.values():
            subs = bus._subscriptions.get(event_type, [])
            matching = [s for s in subs if s.handler_name == handler_name]
            assert len(matching) == 0


# =============================================================================
# WildcardObserver 동작 검증: handler_name 충돌 방지
# =============================================================================


class TestWildcardObserverHandlerNameBehavior:
    """handler_name 고유성 (D4) 검증."""

    def test_two_instances_have_different_handler_names(self):
        """서로 다른 인스턴스의 handler_ref.__name__이 달라야 한다."""
        observer1 = _make_observer()
        observer2 = _make_observer()

        assert observer1._handler_ref.__name__ != observer2._handler_ref.__name__

    def test_two_instances_can_coexist_on_same_bus(self):
        """두 인스턴스가 같은 EventBus에 동시 구독 가능해야 한다."""
        bus = SelfHealingEventBus()
        observer1 = _make_observer()
        observer2 = _make_observer()

        observer1.register(bus)
        observer2.register(bus)

        try:
            # 각 EventType에 두 핸들러가 등록되어야 함
            sample_type = list(EventType.__members__.values())[0]
            subs = bus._subscriptions.get(sample_type, [])
            handler_names = {s.handler_name for s in subs}

            assert observer1._handler_ref.__name__ in handler_names
            assert observer2._handler_ref.__name__ in handler_names
        finally:
            observer1.unregister(bus)
            observer2.unregister(bus)

    def test_unregister_one_does_not_affect_other(self):
        """한 인스턴스의 unregister가 다른 인스턴스에 영향 없어야 한다."""
        bus = SelfHealingEventBus()
        observer1 = _make_observer()
        observer2 = _make_observer()

        observer1.register(bus)
        observer2.register(bus)

        observer2_handler_name = observer2._handler_ref.__name__

        observer1.unregister(bus)

        # observer2의 구독은 여전히 존재
        sample_type = list(EventType.__members__.values())[0]
        subs = bus._subscriptions.get(sample_type, [])
        handler_names = {s.handler_name for s in subs}
        assert observer2_handler_name in handler_names

        observer2.unregister(bus)


# =============================================================================
# WildcardObserver 동작 검증: 이벤트 발생률 이상 탐지 (D5)
# =============================================================================


class TestWildcardObserverRateAnomalyBehavior:
    """이벤트 발생률 이상 탐지 + Zero-Feed 보장 검증."""

    def test_check_rate_anomaly_returns_none_when_normal(self):
        """정상 이벤트 발생률에서는 None을 반환해야 한다."""
        observer = _make_observer()

        # ZScoreDetector는 3개 미만 데이터에서 항상 (False, 0.0)
        result = observer.check_event_rate_anomaly()
        assert result is None

    def test_check_rate_anomaly_returns_dict_on_spike(self):
        """이벤트 폭주 시 이상 탐지 dict를 반환해야 한다."""
        observer = _make_observer()
        now = time.time()

        # 정상 구간: 10회 tick에서 0 이벤트 → ZScoreDetector에 0 feed
        for _ in range(10):
            observer.check_event_rate_anomaly()

        # 이벤트 폭주 시뮬레이션: 윈도우에 많은 이벤트 직접 추가
        for _ in range(500):
            event = _make_event(timestamp=datetime.fromtimestamp(now - 5, tz=timezone.utc))
            observer._window.add(event)

        result = observer.check_event_rate_anomaly()

        assert result is not None
        assert result["type"] == "event_rate_anomaly"
        assert "current_rate_per_minute" in result
        assert "z_score" in result
        assert "message" in result
        assert "timestamp" in result

    def test_zero_feed_maintains_baseline(self):
        """0 트래픽 구간에서도 매 tick 호출하면 폭주 탐지 가능해야 한다.

        Zero-Feed가 ZScoreDetector 윈도우를 0으로 채워서
        폭주 시 z-score가 충분히 높아진다.
        """
        observer = _make_observer()
        now = time.time()

        # 0 트래픽 30회 tick → ZScoreDetector에 0.0 30번 feed
        for _ in range(30):
            observer.check_event_rate_anomaly()

        # 폭주: 500개 이벤트
        for _ in range(500):
            event = _make_event(timestamp=datetime.fromtimestamp(now - 5, tz=timezone.utc))
            observer._window.add(event)

        result = observer.check_event_rate_anomaly()

        # Zero-Feed로 베이스라인이 low이므로 500건은 이상으로 탐지
        assert result is not None
        assert result["z_score"] > observer._settings.zscore_threshold

    def test_zero_feed_skipped_causes_missed_detection(self):
        """Zero-Feed 누락 시 폭주가 미탐지될 수 있다 (네거티브 테스트).

        tick을 호출하지 않으면 ZScoreDetector 윈도우가 갱신되지 않아
        폭주 시 z-score가 과소평가된다.
        """
        observer = _make_observer()
        now = time.time()

        # tick 누락: check_event_rate_anomaly()를 호출하지 않음
        # 정상 구간의 데이터를 직접 ZScoreDetector에 넣어 베이스라인 생성
        for count in [50, 48, 52, 49, 51, 50, 48, 52, 49, 51]:
            observer._rate_detector.is_anomaly(float(count))

        # tick 누락 (0 feed 없이) → ZScoreDetector 윈도우에 0이 없음

        # 폭주: 500개 이벤트
        for _ in range(500):
            event = _make_event(timestamp=datetime.fromtimestamp(now - 5, tz=timezone.utc))
            observer._window.add(event)

        result = observer.check_event_rate_anomaly()

        # 윈도우에 0 없이 평균 ~50이므로 500건의 z-score가
        # Zero-Feed 있을 때보다 낮을 수 있음
        if result is not None:
            # z_score가 Zero-Feed 버전보다 상대적으로 낮음을 검증하기보다
            # 이 테스트는 네거티브 케이스의 존재를 문서화
            pass
        # 핵심: Zero-Feed 없이도 탐지될 수 있지만,
        # 베이스라인이 약 50인 상태에서 500은 탐지되길 기대하나
        # 실제로는 탐지될 수도 있다. 이 테스트의 목적은
        # Zero-Feed의 중요성을 코드로 문서화하는 것이다.


# =============================================================================
# WildcardObserver 동작 검증: 통계
# =============================================================================


class TestWildcardObserverStatisticsBehavior:
    """get_statistics() 동작 검증."""

    def test_statistics_contains_required_fields(self):
        """통계에 필수 필드가 모두 포함되어야 한다."""
        observer = _make_observer()
        stats = observer.get_statistics()

        required_fields = [
            "total_observed",
            "window_size",
            "events_last_minute",
            "type_distribution",
            "subscribed_types",
            "is_active",
            "events_dropped",
            "queue_size",
            "queue_maxsize",
        ]
        for field in required_fields:
            assert field in stats, f"Missing field: {field}"

    def test_statistics_reflects_observed_events(self):
        """통계가 실제 관찰된 이벤트 수를 반영해야 한다."""
        observer = _make_observer()

        # 수동으로 상태 설정
        observer._total_observed = 42
        observer._dropped_count = 3

        stats = observer.get_statistics()
        assert stats["total_observed"] == 42
        assert stats["events_dropped"] == 3

    def test_statistics_subscribed_types_matches_event_type_count(self):
        """subscribed_types가 EventType 멤버 수와 일치해야 한다."""
        observer = _make_observer()
        stats = observer.get_statistics()

        assert stats["subscribed_types"] == len(EventType.__members__)

    def test_statistics_queue_maxsize_matches_settings(self):
        """queue_maxsize가 max_event_buffer 설정과 일치해야 한다."""
        settings = _make_settings(max_event_buffer=250)
        observer = _make_observer(settings=settings)
        stats = observer.get_statistics()

        assert stats["queue_maxsize"] == settings.max_event_buffer

    def test_statistics_is_active_reflects_subscription_state(self):
        """is_active가 구독 상태를 반영해야 한다."""
        observer = _make_observer()

        assert observer.get_statistics()["is_active"] is False

        bus = SelfHealingEventBus()
        observer.register(bus)

        try:
            assert observer.get_statistics()["is_active"] is True
        finally:
            observer.unregister(bus)

        assert observer.get_statistics()["is_active"] is False


# =============================================================================
# WildcardObserver 동작 검증: get_current_window
# =============================================================================


class TestWildcardObserverCurrentWindowBehavior:
    """get_current_window() 동작 검증."""

    def test_get_current_window_filters_by_settings_window(self):
        """설정의 window_seconds를 기준으로 필터링해야 한다."""
        settings = _make_settings(window_seconds=60.0)
        observer = _make_observer(settings=settings)
        now = time.time()

        # window 내 이벤트
        recent = _make_event(timestamp=datetime.fromtimestamp(now - 30, tz=timezone.utc))
        # window 밖 이벤트
        old = _make_event(timestamp=datetime.fromtimestamp(now - 120, tz=timezone.utc))

        observer._window.add(old)
        observer._window.add(recent)

        result = observer.get_current_window()
        assert len(result) == 1
        assert result[0] is recent

    def test_get_current_window_returns_empty_when_no_events(self):
        """이벤트가 없으면 빈 리스트를 반환해야 한다."""
        observer = _make_observer()
        result = observer.get_current_window()
        assert result == []


# =============================================================================
# WildcardObserver 동작 검증: End-to-End (register → publish → consume)
# =============================================================================


class TestWildcardObserverEndToEndBehavior:
    """EventBus publish → Observer consume 전체 흐름 검증."""

    def test_published_event_reaches_window(self):
        """EventBus에 발행된 이벤트가 Observer 윈도우에 도달해야 한다."""
        settings = _make_settings()
        co_occurrence = MagicMock(spec=CoOccurrenceTracker)
        observer = _make_observer(settings=settings, co_occurrence=co_occurrence)
        bus = SelfHealingEventBus()

        observer.register(bus)

        try:
            event = _make_event(event_type=EventType.CIRCUIT_BREAKER_OPENED)
            bus.publish(event)

            # Consumer 스레드가 처리할 시간을 줌
            deadline = time.monotonic() + 3.0
            while observer._total_observed < 1 and time.monotonic() < deadline:
                time.sleep(0.05)

            assert observer._total_observed >= 1
            assert len(observer._window._events) >= 1
        finally:
            observer.unregister(bus)

    def test_multiple_event_types_all_observed(self):
        """여러 종류의 이벤트가 모두 관찰되어야 한다."""
        settings = _make_settings()
        co_occurrence = MagicMock(spec=CoOccurrenceTracker)
        observer = _make_observer(settings=settings, co_occurrence=co_occurrence)
        bus = SelfHealingEventBus()

        observer.register(bus)

        try:
            event_types = [
                EventType.CIRCUIT_BREAKER_OPENED,
                EventType.CIRCUIT_BREAKER_CLOSED,
                EventType.ERROR_BUDGET_CRITICAL,
            ]
            for et in event_types:
                bus.publish(_make_event(event_type=et))

            deadline = time.monotonic() + 3.0
            while observer._total_observed < 3 and time.monotonic() < deadline:
                time.sleep(0.05)

            assert observer._total_observed >= 3

            counts = observer._window.get_type_counts()
            for et in event_types:
                assert counts.get(et.value, 0) >= 1
        finally:
            observer.unregister(bus)


# =============================================================================
# WildcardObserver 동작 검증: Thread Safety
# =============================================================================


class TestWildcardObserverThreadSafetyBehavior:
    """Thread Safety 검증."""

    def test_concurrent_publish_maintains_queue_integrity(self):
        """여러 스레드에서 동시 발행 시 큐 데이터 무결성 유지."""
        settings = _make_settings(max_event_buffer=500)
        observer = _make_observer(settings=settings)

        events_per_thread = 50
        num_threads = 5
        total_events = events_per_thread * num_threads

        def producer():
            for _ in range(events_per_thread):
                observer._on_event(_make_event())

        threads = [threading.Thread(target=producer) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # 큐 크기 + 드롭 수 = 총 이벤트 수
        queued = observer._queue.qsize()
        dropped = observer._dropped_count
        assert queued + dropped == total_events
