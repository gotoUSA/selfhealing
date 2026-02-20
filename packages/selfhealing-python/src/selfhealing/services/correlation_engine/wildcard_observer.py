"""
Wildcard Observer — EventBus 전체 이벤트 관찰자.

42개 EventType 전체를 구독하여 이벤트 스트림을 수집하고,
Correlation Engine 모듈(DAG Builder, Co-occurrence Tracker)에 공급하는 데이터 수집 계층.

아키텍처: Producer-Consumer 패턴
    - Producer (_on_event): EventBus Hot Path에서 queue.put_nowait() O(1)만 수행
    - Consumer (_consumer_loop): daemon 스레드에서 윈도우 버퍼링 + Co-occurrence 기록

선례: throttle/audit.py의 _audit_queue + daemon _worker_loop 패턴.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import Counter, deque

from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
)
from selfhealing.services.event_bus.bus import (
    EventPriority,
    EventType,
    SelfHealingEvent,
    SelfHealingEventBus,
)
from selfhealing.services.predictive_forecaster.anomaly_detector import (
    ZScoreDetector,
)
from selfhealing.settings.correlation import CorrelationSettings

logger = logging.getLogger(__name__)


# =============================================================================
# EventWindow — 시간 윈도우 내 이벤트 버퍼
# =============================================================================


class EventWindow:
    """시간 윈도우 내 이벤트 버퍼 — SelfHealingEvent 원본 참조 보존.

    추가 객체 할당 없이 원본 SelfHealingEvent 참조를 deque에 저장한다.
    Consumer 스레드에서만 add()가 호출되므로 Lock 불필요.
    """

    def __init__(self, window_seconds: float, max_events: int):
        self._window_seconds = window_seconds
        self._max_events = max_events
        self._events: deque[SelfHealingEvent] = deque(maxlen=max_events)
        self._event_counts: Counter = Counter()

    def add(self, event: SelfHealingEvent) -> None:
        """이벤트 참조 추가 (Consumer 스레드에서만 호출)."""
        self._events.append(event)
        self._event_counts[event.event_type.value] += 1

    def get_window(self, since_timestamp: float) -> list[SelfHealingEvent]:
        """시간 윈도우 내 이벤트 반환.

        SelfHealingEvent.timestamp은 datetime 객체이므로
        epoch float 비교를 위해 .timestamp() 변환을 수행한다.

        Args:
            since_timestamp: epoch seconds 기준 시작 시각.

        Returns:
            윈도우 내 이벤트 리스트 (방어적 list 복사).
        """
        return [e for e in self._events if e.timestamp.timestamp() >= since_timestamp]

    def get_type_counts(self) -> dict[str, int]:
        """이벤트 타입별 누적 카운트."""
        return dict(self._event_counts)

    def clear_expired(self, before_timestamp: float) -> int:
        """윈도우 밖 이벤트 제거 (deque maxlen이 1차 방어, 이것은 2차).

        Args:
            before_timestamp: 이 시각 이전의 이벤트를 제거.

        Returns:
            제거된 이벤트 수.
        """
        original = len(self._events)
        while self._events and self._events[0].timestamp.timestamp() < before_timestamp:
            expired = self._events.popleft()
            self._event_counts[expired.event_type.value] -= 1
        return original - len(self._events)


# =============================================================================
# WildcardObserver — 전체 이벤트 관찰자
# =============================================================================


class WildcardObserver:
    """EventBus의 모든 이벤트를 관찰하는 수집 계층.

    Producer-Consumer 아키텍처:
        _on_event (Producer) — EventBus Hot Path에서 O(1) put_nowait만 수행
        _consumer_loop (Consumer) — daemon 스레드에서 윈도우 버퍼링 + Co-occurrence 기록

    MUST: check_event_rate_anomaly()는 Orchestrator에서 매 analysis_interval마다
    반드시 호출해야 한다. 0 트래픽 구간에서도 호출하여 ZScoreDetector 윈도우에
    0.0을 feed해야 정상 베이스라인이 유지된다.
    """

    def __init__(
        self,
        settings: CorrelationSettings,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        self._settings = settings
        self._co_occurrence = co_occurrence_tracker

        # EventWindow — SelfHealingEvent 참조 보존
        self._window = EventWindow(
            window_seconds=settings.window_seconds,
            max_events=settings.max_event_buffer,
        )

        # Producer-Consumer 큐
        # 큐 크기는 max_event_buffer 설정 재사용 (le=10000 Pydantic 상한)
        self._queue: queue.Queue[SelfHealingEvent] = queue.Queue(
            maxsize=settings.max_event_buffer,
        )

        # 이벤트 발생률 이상 탐지 — ZScoreDetector
        self._rate_detector = ZScoreDetector(
            threshold=settings.zscore_threshold,
            window=100,
        )

        # 통계 카운터
        self._total_observed: int = 0
        self._dropped_count: int = 0

        # 구독 상태
        self._subscribed = False

        # Consumer 스레드 제어
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None

        # handler_name 충돌 방지 래퍼
        # EventBus는 getattr(handler, "__name__", str(handler))로 식별하므로
        # id(self)를 포함한 고유 이름을 부여한다.
        def _wildcard_observer_handler(event: SelfHealingEvent) -> None:
            return self._on_event(event)

        _wildcard_observer_handler.__name__ = f"WildcardObserver._on_event_{id(self)}"
        self._handler_ref: callable | None = _wildcard_observer_handler  # type: ignore[assignment]

    # -------------------------------------------------------------------------
    # 구독 등록 / 해제
    # -------------------------------------------------------------------------

    def register(self, event_bus: SelfHealingEventBus) -> None:
        """모든 EventType에 대해 구독 등록 + Consumer 스레드 시작."""
        if self._subscribed:
            return

        # 1) 전체 EventType 구독
        subscribed_count = 0
        for event_type in EventType.__members__.values():
            try:
                event_bus.subscribe(
                    event_type=event_type,
                    handler=self._handler_ref,
                    priority=EventPriority.LOW,
                )
                subscribed_count += 1
            except Exception as e:
                logger.warning(f"[WildcardObserver] Failed to subscribe to " f"{event_type.value}: {e}")

        # 2) Consumer 스레드 시작
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            daemon=True,
            name="WildcardObserverConsumer",
        )
        self._consumer_thread.start()

        self._subscribed = True
        logger.info(f"[WildcardObserver] Subscribed to {subscribed_count} event types, " f"consumer thread started")

    def unregister(self, event_bus: SelfHealingEventBus) -> None:
        """구독 해제 + Consumer 스레드 종료.

        루프 내부에 try-except를 배치하여 한 타입의 해제 실패가
        다른 타입에 영향을 주지 않도록 보장한다.
        """
        # 1) Consumer 스레드 종료 신호
        self._stop_event.set()
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5.0)
        self._consumer_thread = None

        # 2) 전체 EventType 순회하며 구독 해제 — 개별 try-except로 보호
        handler = self._handler_ref
        if handler is not None:
            for event_type in EventType.__members__.values():
                try:
                    event_bus.unsubscribe(event_type, handler)
                except Exception:
                    pass

        self._subscribed = False

        # 3) 래퍼 참조 해제 → 클로저가 self를 캡처하므로 순환 참조 끊기
        self._handler_ref = None

        logger.info("[WildcardObserver] Unregistered and consumer stopped")

    # -------------------------------------------------------------------------
    # 이벤트 수신: Producer (Hot Path)
    # -------------------------------------------------------------------------

    def _on_event(self, event: SelfHealingEvent) -> None:
        """이벤트 수신 — O(1) 큐 삽입만 수행, 즉시 반환.

        EventBus publish()의 임계 경로에서 실행되므로
        Lock, dict copy, 해시 연산 등 일체 금지.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Fail-Open: EventBus를 절대 블로킹하지 않음
            # _dropped_count는 int 단일 할당이므로
            # GIL 하에서 atomic — Lock 불필요
            self._dropped_count += 1

    # -------------------------------------------------------------------------
    # 이벤트 처리: Consumer (Cold Path)
    # -------------------------------------------------------------------------

    def _consumer_loop(self) -> None:
        """백그라운드 Consumer — 윈도우 버퍼링 + Co-occurrence 기록.

        daemon=True이므로 프로세스 종료 시 자동 정리된다.
        """
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                # 1) EventWindow에 원본 참조 추가
                self._window.add(event)
                self._total_observed += 1

                # 2) Co-occurrence Tracker에 기록
                # record_event()는 내부적으로 dict shallow copy를 수행하지만 (O(n)),
                # Consumer 스레드에서 실행되므로 EventBus에 영향 없음
                service_name = event.data.get("service_name", event.source or "unknown")
                self._co_occurrence.record_event(
                    event_type=event.event_type.value,
                    timestamp=event.timestamp.timestamp(),
                    service_name=service_name,
                )
            except Exception as e:
                # Consumer 오류가 루프를 중단시키지 않음
                logger.debug(f"[WildcardObserver] Consumer error: {e}")

        logger.info("[WildcardObserver] Consumer loop exited")

    # -------------------------------------------------------------------------
    # 이벤트 발생률 이상 탐지 (Orchestrator Tick 전용)
    # -------------------------------------------------------------------------

    def check_event_rate_anomaly(self) -> dict | None:
        """전체 이벤트 발생률의 이상 여부 확인.

        Orchestrator tick()에서 analysis_interval(기본 60초)마다 호출된다.
        0 트래픽 구간에서도 호출하여 is_anomaly(0.0)을 feed해야
        ZScoreDetector의 평균/분산이 정상 하강하여 베이스라인이 유지된다.

        Returns:
            이상 탐지 시 dict, 정상 시 None.
        """
        current_time = time.time()
        window_events = self._window.get_window(current_time - 60)
        recent_count = len(window_events)

        # recent_count가 0이든 1000이든,
        # 반드시 is_anomaly()를 호출하여 윈도우에 feed (Zero-Feed 보장)
        is_anomalous, z_score = self._rate_detector.is_anomaly(float(recent_count))

        if is_anomalous:
            return {
                "type": "event_rate_anomaly",
                "current_rate_per_minute": recent_count,
                "z_score": z_score,
                "message": (f"이벤트 발생률 이상: 최근 1분간 {recent_count}건 " f"(Z-Score={z_score:.2f})"),
                "timestamp": current_time,
            }
        return None

    # -------------------------------------------------------------------------
    # 윈도우 내 이벤트 스냅샷 및 통계
    # -------------------------------------------------------------------------

    def get_current_window(self) -> list[SelfHealingEvent]:
        """현재 시간 윈도우 내 이벤트 반환 (DAG Builder 입력용).

        Consumer 스레드와 동시 접근 가능하나,
        EventWindow.get_window()는 list comprehension으로
        방어적 복사를 수행하므로 안전하다.
        """
        current_time = time.time()
        return self._window.get_window(current_time - self._settings.window_seconds)

    def get_statistics(self) -> dict:
        """Observer 통계 (대시보드 + Prometheus 연동용).

        _dropped_count가 0보다 크면 분석 엔진이 인시던트 속도를
        따라가지 못해 블라인드 스팟이 발생하고 있다는 경고이다.
        """
        window_events = self._window.get_window(time.time() - 60)
        return {
            "total_observed": self._total_observed,
            "window_size": len(self._window._events),
            "events_last_minute": len(window_events),
            "type_distribution": self._window.get_type_counts(),
            "subscribed_types": len(EventType.__members__),
            "is_active": self._subscribed,
            # Backpressure 지표
            "events_dropped": self._dropped_count,
            "queue_size": self._queue.qsize(),
            "queue_maxsize": self._queue.maxsize,
        }

    # -------------------------------------------------------------------------
    # EventType 확장 대응: 재등록 메커니즘
    # -------------------------------------------------------------------------

    def refresh_subscriptions(self, event_bus: SelfHealingEventBus) -> int:
        """EventType 변경 시 누락된 타입에 대해 추가 구독.

        _handler_ref를 재사용하여 handler_name 일관성을 유지한다.

        Returns:
            새로 추가된 구독 수.
        """
        if not self._subscribed or self._handler_ref is None:
            return 0

        current_types = set(EventType.__members__.values())
        new_count = 0
        for event_type in current_types:
            try:
                event_bus.subscribe(event_type, self._handler_ref, EventPriority.LOW)
                # subscribe()는 중복 구독 시 기존 것을 반환하므로
                # 이미 구독된 타입은 추가되지 않음
                new_count += 1
            except Exception:
                pass

        return new_count
