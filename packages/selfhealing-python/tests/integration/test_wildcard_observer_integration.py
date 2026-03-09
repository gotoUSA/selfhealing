"""
통합 테스트: WildcardObserver + EventBus + CoOccurrenceTracker 조합 검증.

Mock 기반 통합 (DB/Redis 불필요, 로컬 직접 실행).
WildcardObserver가 실제 EventBus, CoOccurrenceTracker와 조합되어
이벤트 수집→분석 파이프라인이 올바르게 동작하는지 검증한다.

검증 시나리오:
- 100개 이벤트 연속 발행 → CoOccurrenceTracker에 기록 확인
- 이벤트 폭주 (1분 내 대량) → rate_anomaly 탐지
- get_statistics() 대시보드 필수 필드 포함 확인
- register → publish → consume → unregister 전체 라이프사이클
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
)
from selfhealing.services.correlation_engine.wildcard_observer import (
    WildcardObserver,
)
from selfhealing.services.event_bus.bus import (
    EventType,
    SelfHealingEvent,
    SelfHealingEventBus,
)
from selfhealing.settings.correlation import CorrelationSettings

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def settings():
    """통합 테스트용 CorrelationSettings."""
    return CorrelationSettings(
        window_seconds=300.0,
        max_event_buffer=500,
        zscore_threshold=2.5,
    )


@pytest.fixture
def event_bus():
    """테스트용 독립 EventBus 인스턴스."""
    return SelfHealingEventBus()


@pytest.fixture
def co_occurrence(settings):
    """실제 CoOccurrenceTracker 인스턴스."""
    return CoOccurrenceTracker(settings)


@pytest.fixture
def observer(settings, co_occurrence):
    """WildcardObserver 인스턴스 (미등록 상태)."""
    return WildcardObserver(settings, co_occurrence)


@pytest.fixture
def registered_observer(observer, event_bus):
    """등록된 WildcardObserver — 테스트 후 자동 해제."""
    observer.register(event_bus)
    yield observer
    if observer._subscribed:
        observer.unregister(event_bus)


def _make_event(
    event_type: EventType = EventType.CIRCUIT_BREAKER_OPENED,
    source: str = "test-service",
    data: dict | None = None,
    timestamp: datetime | None = None,
) -> SelfHealingEvent:
    return SelfHealingEvent(
        event_type=event_type,
        data=data or {"service_name": source},
        source=source,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def _wait_for_observed(observer: WildcardObserver, expected: int, timeout: float = 5.0):
    """Consumer가 expected 수만큼 이벤트를 처리할 때까지 대기."""
    deadline = time.monotonic() + timeout
    while observer._total_observed < expected and time.monotonic() < deadline:
        time.sleep(0.05)


# =============================================================================
# 통합 시나리오: 대량 이벤트 발행 → CoOccurrenceTracker 기록
# =============================================================================


class TestWildcardObserverEventFlowIntegration:
    """EventBus → WildcardObserver → CoOccurrenceTracker 파이프라인 검증."""

    def test_100_events_reach_co_occurrence_tracker(
        self, registered_observer, event_bus, co_occurrence
    ):
        """100개 이벤트 연속 발행 시 CoOccurrenceTracker에 기록되어야 한다."""
        event_types = [
            EventType.CIRCUIT_BREAKER_OPENED,
            EventType.CIRCUIT_BREAKER_CLOSED,
            EventType.ERROR_BUDGET_CRITICAL,
        ]

        event_count = 100
        for i in range(event_count):
            et = event_types[i % len(event_types)]
            event_bus.publish(_make_event(event_type=et, source=f"svc-{i % 5}"))

        _wait_for_observed(registered_observer, event_count)

        assert registered_observer._total_observed >= event_count
        # EventWindow에 이벤트가 기록되었는지 확인
        assert len(registered_observer._window._events) >= event_count

    def test_multiple_event_types_recorded_in_window(
        self, registered_observer, event_bus
    ):
        """다양한 EventType이 윈도우의 type_distribution에 모두 반영되어야 한다."""
        types_to_publish = [
            EventType.CIRCUIT_BREAKER_OPENED,
            EventType.ERROR_BUDGET_CRITICAL,
            EventType.CONFIG_UPDATED,
        ]

        for et in types_to_publish:
            for _ in range(10):
                event_bus.publish(_make_event(event_type=et))

        _wait_for_observed(registered_observer, 30)

        counts = registered_observer._window.get_type_counts()
        for et in types_to_publish:
            assert counts.get(et.value, 0) >= 10


# =============================================================================
# 통합 시나리오: 이벤트 폭주 → rate_anomaly 탐지
# =============================================================================


class TestWildcardObserverRateAnomalyIntegration:
    """이벤트 발생률 이상 탐지 통합 검증."""

    def test_event_burst_triggers_rate_anomaly(self, registered_observer, event_bus):
        """정상 구간 후 이벤트 폭주 시 rate_anomaly가 탐지되어야 한다."""
        # 정상 구간: tick 호출로 베이스라인 형성 (0 이벤트)
        for _ in range(20):
            registered_observer.check_event_rate_anomaly()

        # 이벤트 폭주
        burst_count = 500
        for _ in range(burst_count):
            event_bus.publish(_make_event())

        _wait_for_observed(registered_observer, burst_count)

        # 폭주 후 rate_anomaly 확인
        result = registered_observer.check_event_rate_anomaly()

        assert result is not None
        assert result["type"] == "event_rate_anomaly"
        assert result["current_rate_per_minute"] >= burst_count


# =============================================================================
# 통합 시나리오: get_statistics() 대시보드 필드
# =============================================================================


class TestWildcardObserverStatisticsIntegration:
    """get_statistics() 통합 검증."""

    def test_statistics_backpressure_fields_present(
        self, registered_observer, event_bus
    ):
        """통계에 backpressure 지표가 포함되어야 한다."""
        event_bus.publish(_make_event())
        _wait_for_observed(registered_observer, 1)

        stats = registered_observer.get_statistics()

        assert "events_dropped" in stats
        assert "queue_size" in stats
        assert "queue_maxsize" in stats
        assert stats["events_dropped"] == 0
        assert stats["total_observed"] >= 1

    def test_statistics_type_distribution_reflects_published(
        self, registered_observer, event_bus
    ):
        """type_distribution이 실제 발행된 이벤트 타입을 반영해야 한다."""
        event_bus.publish(_make_event(event_type=EventType.CIRCUIT_BREAKER_OPENED))
        event_bus.publish(_make_event(event_type=EventType.ERROR_BUDGET_CRITICAL))

        _wait_for_observed(registered_observer, 2)

        # type_distribution은 consumer 스레드의 _event_counts에서 조회되므로
        # _total_observed와 별도로 가시성을 폴링 대기한다.
        cb_key = EventType.CIRCUIT_BREAKER_OPENED.value
        eb_key = EventType.ERROR_BUDGET_CRITICAL.value
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            dist = registered_observer.get_statistics()["type_distribution"]
            if dist.get(cb_key, 0) >= 1 and dist.get(eb_key, 0) >= 1:
                break
            time.sleep(0.05)

        dist = registered_observer.get_statistics()["type_distribution"]
        assert dist.get(cb_key, 0) >= 1
        assert dist.get(eb_key, 0) >= 1


# =============================================================================
# 통합 시나리오: 전체 라이프사이클
# =============================================================================


class TestWildcardObserverLifecycleIntegration:
    """register → publish → consume → unregister 전체 라이프사이클."""

    def test_full_lifecycle(self, observer, event_bus):
        """전체 라이프사이클이 오류 없이 완료되어야 한다."""
        # 1) register
        observer.register(event_bus)
        assert observer._subscribed is True
        assert observer._consumer_thread.is_alive()

        # 2) publish + consume
        for _ in range(10):
            event_bus.publish(_make_event())
        _wait_for_observed(observer, 10)
        assert observer._total_observed >= 10

        # 3) unregister
        observer.unregister(event_bus)
        assert observer._subscribed is False
        assert observer._consumer_thread is None
        assert observer._handler_ref is None

        # 4) EventBus에서 WildcardObserver 핸들러 구독 해제 확인
        #    싱글톤 EventBus에 다른 컴포넌트의 구독이 남아 있을 수 있으므로
        #    WildcardObserver 핸들러만 제거되었는지 검증한다.
        for et in EventType.__members__.values():
            subs = event_bus._subscriptions.get(et, [])
            observer_subs = [s for s in subs if "WildcardObserver" in s.handler_name]
            assert len(observer_subs) == 0
