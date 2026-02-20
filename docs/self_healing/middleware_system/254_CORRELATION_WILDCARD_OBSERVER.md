# 254. Wildcard Observer — 전체 이벤트 관찰자

> **Version**: 2.0.0
> **Created**: 2026-02-20
> **Updated**: 2026-02-20
> **Status**: Implemented
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/wildcard_observer.py`

---

## Changelog

| 버전 | 날짜 | 변경 사항 |
|------|------|----------|
| 1.0.0 | 2026-02-20 | 초안 |
| 2.0.0 | 2026-02-20 | 5개 설계 검토 결과 전면 반영 — Producer-Consumer 분리, fingerprint 제거, 참조 보존, handler_name 충돌 방지, Orchestrator Tick 확정 |

---

## 0. 요약

현재 EventBus에는 **"모든 이벤트를 관찰하는 관찰자"**가 없다. 각 핸들러는 특정 `EventType`만 구독하며, Wildcard(전체) 구독 메커니즘이 존재하지 않는다. 이 모듈은 42개 `EventType` 전체를 구독하여 이벤트 스트림을 수집하고, 다른 Correlation Engine 모듈(DAG Builder, Co-occurrence Tracker)에 공급하는 **데이터 수집 계층**이다.

이것이 Gap-1(EventType 폐쇄성)을 해결하는 핵심이며, **사전 정의되지 않은 미지의 상관관계 발견**의 전제 조건이다.

### 0.1 v1.0 → v2.0 주요 변경 요약

| # | 이슈 | v1.0 설계 | v2.0 확정 | 근거 |
|---|------|----------|----------|------|
| D1 | data_fingerprint 직렬화 안전성 | `str(sorted(data.items()))` + MD5 | **필드 자체 제거** | 소비처 없음 (YAGNI), `str()` 비결정적, CPU 낭비 |
| D2 | Lock Contention / 동기 처리 | `threading.Lock` 내부에서 window + co_occurrence 동기 처리 | **Producer-Consumer 패턴** (`queue.Queue` + daemon) | ThrottleAudit 선례, EventBus Hot Path 보호 |
| D3 | GC 스파이크 | 매 이벤트마다 `ObservedEvent` 데이터클래스 신규 생성 | **`SelfHealingEvent` 원본 참조 보존** | 추가 할당 0, D2와 자연 결합 |
| D4 | unsubscribe Bound Method 참조 | `self._on_event` 직접 전달 | **`_handler_ref` 고유 이름 래퍼** | EventBus가 `handler_name` 문자열로 식별 — 다중 인스턴스 시 이름 충돌 |
| D5 | rate_anomaly 호출 주도권 | 미정 (호출 시점 불명) | **Orchestrator Tick 주입** (자체 타이머 없음) | `analyze_tick()` 패턴 일관성, Zero-Feed 보장, LeaderScheduler 재사용 |

---

## 1. 현재 EventBus의 구독 한계

### 1.1 코드 분석

[bus/\_\_init\_\_.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus/bus/__init__.py) 확인 결과:

```python
# bus/__init__.py L298-L315 — handler_name 문자열로 구독자 식별
def subscribe(self, event_type, handler, priority=EventPriority.NORMAL):
    handler_name = getattr(handler, "__name__", str(handler))
    # ...
    existing = [s for s in self._subscriptions[event_type]
                if s.handler_name == handler_name]
    # 중복 구독 방지: handler_name이 같으면 무시
```

```python
# bus/__init__.py L344-L371 — unsubscribe도 handler_name 문자열 비교
def unsubscribe(self, event_type, handler):
    handler_name = getattr(handler, "__name__", str(handler))
    self._subscriptions[event_type] = [
        s for s in self._subscriptions[event_type]
        if s.handler_name != handler_name
    ]
```

```python
# bus/__init__.py L403-L419 — publish는 subscription_lock 내에서 복사 후 lock 밖 실행
def publish(self, event: SelfHealingEvent) -> int:
    with self._subscription_lock:
        subscriptions = list(subscriptions)  # 복사
    for subscription in subscriptions:       # lock 밖에서 순차 실행
        subscription.handler(event)          # ← 동기, 직렬
```

**발견된 특성:**

- **Wildcard 구독 없음** — 모든 이벤트 타입을 개별 구독해야 함
- **구독자 없는 이벤트는 무시** — 미처리 이벤트 감지 불가
- **handler_name 문자열 기반 식별** — 메모리 주소(id)가 아님 → D4의 근거
- **핸들러 실행이 동기 + 직렬** — 느린 핸들러가 후속 핸들러를 지연시킴 → D2의 근거
- **이벤트 히스토리** (`get_history()`) 존재하지만 최대 1000개, 분석용 아님

### 1.2 왜 Wildcard 구독이 필요한가

| 없이 | 있으면 |
|------|-------|
| CB_OPENED에만 반응하는 핸들러는 THROTTLE_LIMIT_CHANGED와의 상관관계를 알 수 없음 | 모든 이벤트를 시간순으로 관찰하여 **예상하지 못한 쌍**을 발견 |
| 새 EventType 추가 시 관찰자도 수동 업데이트 | 새 타입이 추가되면 **자동으로 관찰 범위에 포함** |
| 이벤트 빈도의 전체적 이상(갑자기 이벤트 폭주) 감지 불가 | 전체 이벤트 발생률 모니터링 가능 |

---

## 2. 설계

### 2.1 핵심 원칙

1. **Zero Impact**: Observer가 오류를 발생시켜도 기존 핸들러에 영향 없음
2. **Lowest Priority**: `EventPriority.LOW`로 구독하여 기존 핸들러보다 후순위 실행
3. **Non-blocking**: 이벤트 수신 시 `queue.Queue.put_nowait()` O(1)만 수행, 분석은 별도 Consumer 스레드에서
4. **Auto-Discovery**: `EventType` enum의 모든 멤버를 `__members__`로 자동 열거
5. **Fail-Open**: 큐 포화 시 이벤트를 드롭하되, EventBus를 절대 블로킹하지 않음
6. **Reference-Only**: 원본 `SelfHealingEvent` 참조만 보존, 추가 객체 생성 없음

### 2.2 아키텍처: Producer-Consumer 패턴

```
┌──────────────┐         ┌──────────────────┐         ┌───────────────────┐
│  EventBus    │  O(1)   │  queue.Queue      │  poll   │  Consumer Thread  │
│  publish()   │────────▶│  (maxsize=10000)  │────────▶│  (daemon=True)    │
│  → _on_event │put_nowait│                  │ get(1s) │                   │
└──────────────┘         └──────────────────┘         │  ├─ EventWindow    │
                          Fail-Open:                   │  ├─ co_occurrence  │
                          Full → _dropped_count += 1   │  └─ type_counts   │
                                                       └───────────────────┘
```

**선례**: `throttle/audit.py`의 `_audit_queue: Queue[dict] = Queue(maxsize=10000)` + daemon 워커와 동일 패턴.

> **왜 Producer-Consumer인가 (D2 결정 근거)**
>
> v1.0 설계는 `_on_event()` 내부에서 `self._lock`을 쥔 채 `self._window.add()` + `self._co_occurrence.record_event()`를 동기 실행했다.
> `CoOccurrenceTracker.record_event()`는 내부적으로 `dict(self._event_timestamps)` shallow copy (O(n)) + `list(other_timestamps)` copy를 수행한다
> (`co_occurrence_tracker.py` L222-L229). 이 비용이 EventBus `publish()` 임계 경로에 직접 편입되면,
> 이벤트 타입 42개 × 타입당 버퍼 500개 = 최대 21,000개 타임스탬프의 dict copy가 매 이벤트마다 발생한다.
>
> Producer-Consumer 분리로 Hot Path에서는 `put_nowait()` ~1μs만 소비하고,
> 모든 무거운 연산은 별도 daemon 스레드의 Cold Path에서 처리한다.

### 2.3 핵심 자료구조

#### D1/D3 반영: ObservedEvent 제거, SelfHealingEvent 참조 보존

v1.0의 `ObservedEvent` 데이터클래스와 `data_fingerprint` 필드를 **모두 제거**한다.

**제거 근거:**

1. **data_fingerprint** — `ObservedEvent.data_fingerprint`를 소비하는 모듈이 없다. DAG Builder에 전달되는 건 `get_current_window()`의 이벤트 리스트이고, Co-occurrence Tracker의 `record_event()`는 `event_type`, `timestamp`, `service_name` 3개만 받는다. 소비처 없는 데이터의 CPU 오버헤드(MD5 ~5μs)를 Hot Path에서 낭비할 이유가 없다 (YAGNI).

2. **ObservedEvent 자체** — 매 이벤트마다 새 데이터클래스 인스턴스를 생성(~1μs + 힙 할당)하면, Event Storm 시 초당 수천 개의 단명 객체가 GC 압력을 유발한다. `SelfHealingEvent`는 `publish()` 시점에 이미 1개 존재하므로, 그 참조를 deque에 넣으면 **추가 할당 0**.

3. **publish 후 data 불변성** — EventBus 코드(`bus/__init__.py` L403-L437)를 확인하면, `publish()` 이후 `event.data`를 수정하는 패턴이 어디에도 없다. `_record_event()`는 `event.to_dict()`로 별도 복사본을 히스토리에 저장한다. 따라서 참조 공유가 안전하다.

```python
class EventWindow:
    """시간 윈도우 내 이벤트 버퍼 — SelfHealingEvent 참조 보존"""

    def __init__(self, window_seconds: float, max_events: int):
        self._window_seconds = window_seconds
        self._max_events = max_events
        self._events: deque[SelfHealingEvent] = deque(maxlen=max_events)
        self._event_counts: Counter = Counter()  # event_type.value별 카운트

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
        return [
            e for e in self._events
            if e.timestamp.timestamp() >= since_timestamp
        ]

    def get_type_counts(self) -> dict[str, int]:
        """이벤트 타입별 누적 카운트."""
        return dict(self._event_counts)

    def clear_expired(self, before_timestamp: float) -> int:
        """윈도우 밖 이벤트 제거 (deque maxlen이 1차 방어, 이것은 2차)."""
        original = len(self._events)
        while self._events and self._events[0].timestamp.timestamp() < before_timestamp:
            expired = self._events.popleft()
            self._event_counts[expired.event_type.value] -= 1
        return original - len(self._events)
```

> **향후 Dedup 확장 시**: `SelfHealingEvent`에는 현재 `event_id` 필드가 없고 `correlation_id`만 존재한다 (`bus/__init__.py` L217).
> `correlation_id`는 여러 이벤트가 공유하는 트레이싱 컨텍스트이므로 개별 식별에 부적합하다.
> Dedup이 필요해지면 `SelfHealingEvent`에 `event_id: str = field(default_factory=lambda: uuid4().hex)` 필드를 추가하고,
> `(event_id)` 단독 또는 `(correlation_id + timestamp + event_type)` 복합키로 식별한다.
> 직렬화는 `json.dumps(default=str, sort_keys=True)` + SHA256[:16] 방식을 사용한다.

---

## 3. 구현

### 3.1 초기화 및 handler_ref 래핑

#### D4 반영: handler_name 충돌 방지

EventBus는 `getattr(handler, "__name__", str(handler))`로 핸들러를 식별한다 (`bus/__init__.py` L315).
`self._on_event`를 직접 넘기면 `handler_name = "_on_event"`가 되어,
만약 WildcardObserver 인스턴스가 2개 이상 생성되면 **이름 충돌로 두 번째 구독이 무시**되고,
한 인스턴스의 `unsubscribe`가 다른 인스턴스의 핸들러를 해제하는 문제가 발생한다.

`id(self)`를 포함한 고유 이름의 래퍼 함수를 생성하여 이를 방지한다.

```python
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

        # EventWindow — SelfHealingEvent 참조 보존 (D3)
        self._window = EventWindow(
            window_seconds=settings.window_seconds,
            max_events=settings.max_event_buffer,
        )

        # Producer-Consumer 큐 (D2)
        # 큐 크기는 max_event_buffer 설정 재사용 (le=10000 Pydantic 상한)
        # CorrelationSettings.max_event_buffer: default=500, le=10000
        self._queue: queue.Queue[SelfHealingEvent] = queue.Queue(
            maxsize=settings.max_event_buffer,
        )

        # 이벤트 발생률 이상 탐지 — ZScoreDetector
        # ZScoreDetector.__init__(threshold, window) 시그니처
        # anomaly_detector.py L76-L82
        self._rate_detector = ZScoreDetector(
            threshold=settings.zscore_threshold,  # 기본 2.5
            window=100,
        )

        # 통계 카운터
        self._total_observed: int = 0
        self._dropped_count: int = 0

        # 구독 상태
        self._subscribed = False

        # Consumer 스레드
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None

        # D4: handler_name 충돌 방지 래퍼
        # EventBus는 getattr(handler, "__name__", str(handler))로 식별하므로
        # id(self)를 포함한 고유 이름을 부여한다.
        def _wildcard_observer_handler(event: SelfHealingEvent) -> None:
            return self._on_event(event)

        _wildcard_observer_handler.__name__ = (
            f"WildcardObserver._on_event_{id(self)}"
        )
        self._handler_ref = _wildcard_observer_handler
```

### 3.2 구독 등록/해제

```python
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
                    handler=self._handler_ref,  # D4: 고유 이름 래퍼
                    priority=EventPriority.LOW,
                )
                subscribed_count += 1
            except Exception as e:
                logger.warning(
                    f"[WildcardObserver] Failed to subscribe to "
                    f"{event_type.value}: {e}"
                )

        # 2) Consumer 스레드 시작
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            daemon=True,
            name="WildcardObserverConsumer",
        )
        self._consumer_thread.start()

        self._subscribed = True
        logger.info(
            f"[WildcardObserver] Subscribed to {subscribed_count} event types, "
            f"consumer thread started"
        )

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
        self._handler_ref = None  # type: ignore[assignment]

        logger.info("[WildcardObserver] Unregistered and consumer stopped")
```

### 3.3 이벤트 수신: Producer (Hot Path)

```python
    def _on_event(self, event: SelfHealingEvent) -> None:
        """이벤트 수신 — O(1) 큐 삽입만 수행, 즉시 반환.

        EventBus publish()의 임계 경로에서 실행되므로
        Lock, dict copy, 해시 연산 등 일체 금지.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Fail-Open: EventBus를 절대 블로킹하지 않음
            self._dropped_count += 1
            # _dropped_count는 int 단일 할당이므로
            # GIL 하에서 atomic — Lock 불필요
```

> **v1.0 대비 제거된 것:**
> - `ObservedEvent` 데이터클래스 인스턴스 생성 (D3)
> - `_fingerprint()` MD5 해시 연산 (D1)
> - `threading.Lock` 획득 (D2)
> - `co_occurrence.record_event()` 동기 호출 (D2)
>
> **Hot Path 비용: `put_nowait()` ~1μs** (v1.0의 ~17μs 대비 94% 감소)

### 3.4 이벤트 처리: Consumer (Cold Path)

```python
    def _consumer_loop(self) -> None:
        """백그라운드 Consumer — 윈도우 버퍼링 + Co-occurrence 기록.

        ThrottleAudit의 _worker_loop (throttle/audit.py L168-L173)과 동일 패턴.
        daemon=True이므로 프로세스 종료 시 자동 정리된다.
        """
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                # 1) EventWindow에 원본 참조 추가 (D3)
                self._window.add(event)
                self._total_observed += 1

                # 2) Co-occurrence Tracker에 기록
                # record_event()는 내부적으로 dict shallow copy를 수행하지만 (O(n)),
                # Consumer 스레드에서 실행되므로 EventBus에 영향 없음 (D2)
                service_name = event.data.get(
                    "service_name", event.source or "unknown"
                )
                self._co_occurrence.record_event(
                    event_type=event.event_type.value,
                    timestamp=event.timestamp.timestamp(),  # datetime → epoch float
                    service_name=service_name,
                )
            except Exception as e:
                # Consumer 오류가 루프를 중단시키지 않음
                logger.debug(f"[WildcardObserver] Consumer error: {e}")

        logger.info("[WildcardObserver] Consumer loop exited")
```

### 3.5 이벤트 발생률 이상 탐지 (Orchestrator Tick 전용)

#### D5 반영: Orchestrator 주입, Zero-Feed 보장

이 메서드는 **자체 타이머를 가지지 않는다**. 257번 Orchestrator(`CorrelationEngineService`)의 메인 루프에서 `analysis_interval`(기본 60초)마다 호출한다.

**자체 타이머를 배제한 근거:**

| 기준 | 자체 타이머 | Orchestrator Tick |
|------|-----------|------------------|
| 스레드 수 | +1 daemon 추가 | 0 (기존 스케줄러 재사용) |
| Shutdown 조율 | Observer가 자체 관리 | Orchestrator가 일괄 shutdown |
| 0 트래픽 Zero-Feed | 타이머가 돌아도 직접 0 feed 필요 | **Tick에서 항상 is_anomaly() 호출 → 자동 0 feed** |
| Leader Election | 고려 불가 | LeaderScheduler가 리더만 실행 (`coordination/scheduler.py`) |
| 테스트 용이성 | 비동기 타이머 테스트 복잡 | `check_event_rate_anomaly()` 동기 호출로 단위 테스트 |

**Zero-Feed가 필수인 이유:**

`ZScoreDetector.is_anomaly()`는 **호출 시에만** `_values` deque에 값을 추가한다 (`anomaly_detector.py` L96: `self._values.append(value)`).
호출하지 않으면 윈도우가 갱신되지 않아, 조용한 구간 후 폭주 시 Z-Score가 과소평가되어 **미탐지**가 발생한다:

| 시나리오 | 윈도우 (window=100) | 결과 |
|---------|---------------------|------|
| 정상 30분 → 조용 10분 **(매 Tick 0 feed)** → 폭주 500건/분 | `[50,48,...,0,0,...,0,500]` mean≈20 | ✅ Z>3 **탐지** |
| 정상 30분 → 조용 10분 **(Tick 누락)** → 폭주 500건/분 | `[50,48,...,52,500]` mean≈52 | ❌ Z≈2.8 **미탐지** |

```python
    def check_event_rate_anomaly(self) -> dict | None:
        """전체 이벤트 발생률의 이상 여부 확인.

        MUST be called every analysis_interval (default 60s),
        even during zero-traffic periods.

        0 트래픽 구간에서도 호출하여 is_anomaly(0.0)을 feed해야
        ZScoreDetector의 평균/분산이 정상 하강하여 베이스라인이 유지된다.

        Returns:
            이상 탐지 시 dict, 정상 시 None.
        """
        current_time = time.time()
        # EventWindow에서 최근 60초 이벤트 카운트
        window_events = self._window.get_window(current_time - 60)
        recent_count = len(window_events)

        # 핵심: recent_count가 0이든 1000이든,
        # 반드시 is_anomaly()를 호출하여 윈도우에 feed (Zero-Feed 보장)
        is_anomalous, z_score = self._rate_detector.is_anomaly(float(recent_count))

        if is_anomalous:
            return {
                "type": "event_rate_anomaly",
                "current_rate_per_minute": recent_count,
                "z_score": z_score,
                "message": (
                    f"이벤트 발생률 이상: 최근 1분간 {recent_count}건 "
                    f"(Z-Score={z_score:.2f})"
                ),
                "timestamp": current_time,
            }
        return None
```

### 3.6 윈도우 내 이벤트 스냅샷 및 통계

```python
    def get_current_window(self) -> list[SelfHealingEvent]:
        """현재 시간 윈도우 내 이벤트 반환 (DAG Builder 입력용).

        Consumer 스레드와 동시 접근 가능하나,
        EventWindow.get_window()는 list comprehension으로
        방어적 복사를 수행하므로 안전하다.
        """
        current_time = time.time()
        return self._window.get_window(
            current_time - self._settings.window_seconds
        )

    def get_statistics(self) -> dict:
        """Observer 통계 (대시보드 + Prometheus 연동용).

        _dropped_count가 0보다 크면 분석 엔진이 인시던트 속도를
        따라가지 못해 블라인드 스팟이 발생하고 있다는 경고이다.
        대시보드 Alert와 직결되도록 반드시 노출한다.

        선례: ForensicRateLimiter.get_stats()의 _dropped 카운터 노출
        (forensic_audit_bridge.py L170-L175)
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
```

---

## 4. 미래 확장: EventType 개방성 대응

현재 `EventType`은 닫힌 Enum이지만, 향후 확장 시 Observer가 자동 대응하도록:

### 4.1 방안 A: 재등록 메커니즘

```python
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
```

### 4.2 방안 B: EventBus 확장 (별도 제안)

EventBus 자체에 Wildcard 구독 기능을 추가하는 것은 Correlation Engine의 범위 밖이지만, 향후 EventBus 확장 시 고려사항으로 기록:

```python
# 미래 EventBus 확장 (현재 범위 밖)
def subscribe_all(self, handler, priority=EventPriority.LOW):
    """모든 이벤트 타입에 대한 Wildcard 구독"""
    self._wildcard_subscribers.append(Subscription(handler, priority))
```

---

## 5. Thread Safety

### 5.1 v2.0 동시성 모델

v1.0의 단일 `threading.Lock` 기반에서 **Producer-Consumer 분리**로 전환했다. 각 자원의 접근 스레드가 명확히 분리되어 Lock 경합이 원천 제거된다.

| 자원 | 접근 스레드 | 동시성 보장 |
|------|-----------|-----------|
| `self._queue` | Producer(EventBus 스레드) + Consumer | `queue.Queue` 자체 thread-safe (내부 `threading.Condition`) |
| `self._window` | Consumer 단독 | 단일 스레드 접근 → Lock 불필요 |
| `self._co_occurrence` | Consumer 단독 | CoOccurrenceTracker 자체 thread-safe (dict/deque shallow copy 패턴) |
| `self._dropped_count` | Producer (write) + `get_statistics()` (read) | `int` 단일 할당은 GIL 하에서 atomic |
| `self._total_observed` | Consumer (write) + `get_statistics()` (read) | 동일 — GIL atomic |
| `self._rate_detector` | Orchestrator Tick (단일 스레드) | 단일 호출자 보장 (LeaderScheduler) |

### 5.2 잠재적 경합: get_current_window()

`get_current_window()`는 외부 스레드(Orchestrator/API)에서 호출되고, `EventWindow._events`는 Consumer 스레드가 쓴다. 그러나:

- `get_window()`는 list comprehension `[e for e in self._events if ...]`으로 **방어적 복사**를 수행
- `deque`의 iterate + append는 CPython에서 GIL 보호 하에 안전
- 최악 케이스: 진행 중인 `add()`가 반영 안 된 1개 이벤트 누락 — 분석 정확도에 무시할 수 있는 수준

---

## 6. 성능 영향 분석

### 6.1 EventBus publish() 오버헤드 (v2.0)

| 연산 | 비용 | v1.0 대비 | 비고 |
|------|------|----------|------|
| `queue.put_nowait()` | ~1μs | -94% | Lock-free fast path (큐 미포화 시) |
| ~~`ObservedEvent` 생성~~ | ~~1μs~~ | 제거 | D3: 참조 보존 |
| ~~`_fingerprint()` MD5~~ | ~~5μs~~ | 제거 | D1: YAGNI |
| ~~`threading.Lock` 획득~~ | ~~0.5μs~~ | 제거 | D2: Producer-Consumer |
| ~~`co_occurrence.record_event()`~~ | ~~10μs~~ | Consumer로 이동 | D2 |
| **합계 (Hot Path)** | **~1μs** | **v1.0 ~17μs → 94% 감소** | 비기능 요구사항 ≤ 1ms 대비 0.1% |

### 6.2 Consumer 스레드 오버헤드 (Cold Path)

| 연산 | 비용 | 비고 |
|------|------|------|
| `queue.get(timeout=1.0)` | ~1μs (이벤트 존재 시) | 블로킹 대기 시 CPU 0% |
| `EventWindow.add()` | ~1μs | deque append |
| `co_occurrence.record_event()` | ~10-50μs | dict copy O(n), n=이벤트 타입 수 |
| **합계** | **~12-52μs/이벤트** | EventBus와 완전 격리 |

### 6.3 메모리

| 구성 요소 | 크기 | 비고 |
|-----------|------|------|
| `queue.Queue` (maxsize=500, 기본) | ~4KB | SelfHealingEvent 참조(8B) × 500 |
| `EventWindow` deque (max 500) | ~4KB | 동일 참조 |
| `SelfHealingEvent` 수명 연장분 | 최대 ~5MB | 최악: 500개 × ~10KB(data dict 포함) |
| `Counter` (42 타입) | ~2KB | |
| `ZScoreDetector` (1개) | ~8KB | |
| **합계** | **~5MB (최악)** | v1.0 ~1.2MB 대비 증가하나, max_event_buffer=500(기본) 시 동일 수준 |

> **OOM 방어**: `CorrelationSettings.max_event_buffer`가 `queue.Queue.maxsize`와 `EventWindow.maxlen`을
> 동시에 제어한다. Pydantic 밸리데이션 `le=10000` (`settings/correlation.py` L122)이 상한을 보장하므로
> 별도 설정 필드 없이 기존 `max_event_buffer`를 재사용한다.
> 최대 10,000개 × ~10KB = ~100MB가 이론적 상한이며, 운영 시 기본값 500으로 ~5MB에 수렴한다.

---

## 7. 등록 시점 및 Orchestrator Tick 연동

### 7.1 초기화

`CorrelationEngineService.initialize()` (257번)에서 다른 모듈 초기화 후 마지막으로 등록:

```python
# service.py
def initialize(self):
    # 1. Settings 로드
    # 2. CoOccurrenceTracker 초기화
    # 3. EventGraphBuilder 초기화
    # 4. RootCauseRanker 초기화
    # 5. WildcardObserver 등록 (마지막)
    self._observer = WildcardObserver(self._settings, self._co_occurrence)
    self._observer.register(get_event_bus())
```

### 7.2 Orchestrator Tick 연동 (D5)

```python
# service.py — Orchestrator 메인 루프
def tick(self):
    """analysis_interval (기본 60초)마다 LeaderScheduler가 호출.

    check_event_rate_anomaly()와 analyze_tick()은 반드시 함께 호출한다.
    한쪽만 호출하고 다른 쪽을 빼먹는 실수를 방지하기 위해
    동일 tick() 메서드 내에 나란히 배치한다.
    """
    # 1) Co-occurrence 분석 (기존)
    correlation_results = self._co_occurrence.analyze_tick()

    # 2) 이벤트 발생률 이상 탐지 (D5: 반드시 매 tick 호출)
    rate_anomaly = self._observer.check_event_rate_anomaly()

    # 3) DAG 빌드 트리거 (기존)
    if correlation_results or rate_anomaly:
        self._trigger_dag_build(correlation_results, rate_anomaly)
```

### 7.3 해제

```python
def shutdown(self):
    if self._observer:
        self._observer.unregister(get_event_bus())
```

→ `unregister()`가 Consumer 스레드 종료 → 42개 구독 해제 → `_handler_ref` 참조 해제를 일괄 수행.
→ `correlation_engine/` 전체를 비활성화해도 이 한 번으로 정리.

---

## 8. 설계 결정 근거 상세 (D1-D5)

### D1. data_fingerprint 제거

| 항목 | 상세 |
|------|------|
| **문제** | `str(sorted(data.items()))` + MD5: 중첩 dict 순서 비결정적, datetime 등 비직렬화 타입 예외 위험 |
| **소비처 분석** | `ObservedEvent.data_fingerprint`를 읽는 모듈 = **없음**. DAG Builder 입력은 `get_current_window()` 이벤트 리스트, Co-occurrence는 `(event_type, timestamp, service_name)` 3-tuple |
| **결정** | 필드 자체 제거 (YAGNI). Hot Path에서 ~5μs(MD5) + ~1μs(ObservedEvent 생성) 절약 |
| **향후** | Dedup 필요 시 `SelfHealingEvent.event_id` UUID 필드 추가 → `json.dumps(default=str, sort_keys=True)` + SHA256[:16] |

### D2. Producer-Consumer 분리

| 항목 | 상세 |
|------|------|
| **문제** | v1.0: `self._lock` 내부에서 `co_occurrence.record_event()` 동기 호출 → `dict(self._event_timestamps)` O(n) copy가 EventBus Hot Path에 편입 |
| **선례** | `throttle/audit.py` L154-L178: `_audit_queue: Queue(maxsize=10000)` + daemon `_worker_loop` |
| **결정** | 동일 패턴 적용. Hot Path = `put_nowait()` O(1). Cold Path = daemon Consumer 스레드 |
| **큐 크기** | `CorrelationSettings.max_event_buffer` 재사용 (기본 500, 상한 10000). `le=10000` Pydantic 밸리데이션 |
| **Fail-Open** | `queue.Full` 시 `_dropped_count += 1` → `get_statistics()`에 노출 → Prometheus Counter로 Alert |

### D3. SelfHealingEvent 참조 보존

| 항목 | 상세 |
|------|------|
| **문제** | 매 이벤트마다 `ObservedEvent` 인스턴스 신규 생성 → Event Storm 시 GC 압력 |
| **안전성** | EventBus `publish()` 후 `event.data`를 수정하는 코드 없음 (`bus/__init__.py` L403-L437). `_record_event()`는 `to_dict()` 별도 복사본 사용 |
| **결정** | 원본 `SelfHealingEvent` 참조를 deque에 보존. 추가 할당 = 0 |
| **주의** | `SelfHealingEvent.timestamp`은 `datetime` 객체 (`bus/__init__.py` L215). epoch float 비교 시 `.timestamp()` 변환 필요 |

### D4. handler_name 충돌 방지

| 항목 | 상세 |
|------|------|
| **문제** | EventBus는 `getattr(handler, "__name__")` 문자열로 식별 (`bus/__init__.py` L315). `self._on_event` 직접 전달 시 `handler_name = "_on_event"` → 다중 인스턴스 이름 충돌 |
| **구체적 위험** | 인스턴스 A, B 동시 존재 시: B의 subscribe가 `"_on_event"` 중복으로 무시됨. A의 unsubscribe가 B의 핸들러까지 해제 |
| **결정** | `id(self)` 포함 고유 이름 래퍼 + `unregister()` 시 `self._handler_ref = None`으로 순환 참조 해제 |

### D5. Orchestrator Tick + Zero-Feed

| 항목 | 상세 |
|------|------|
| **문제** | `check_event_rate_anomaly()` 호출 시점 미정. `ZScoreDetector.is_anomaly()`는 호출 시에만 `_values.append()` |
| **선례** | `CoOccurrenceTracker.analyze_tick()` — 외부 Tick 주입 방식 (`co_occurrence_tracker.py` L255). `CorrelationSettings.analysis_interval` = 60s |
| **결정** | Orchestrator `tick()`에서 `analyze_tick()`과 나란히 호출. 자체 타이머 없음 |
| **Zero-Feed** | 매 tick에서 `is_anomaly(recent_count)` 호출하므로, 0 트래픽 구간에서도 `is_anomaly(0.0)` 자동 feed → 베이스라인 유지 |

---

## 9. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | register() 후 42개 구독 확인 | `bus.subscriber_count` 증가 |
| **단위** | 이벤트 발행 → _on_event → 큐 삽입 | `observer._queue.qsize()` > 0 |
| **단위** | Consumer가 큐 소비 → 윈도우 추가 | `_window._events`에 이벤트 존재 |
| **단위** | Observer 예외 발생 | 기존 핸들러에 영향 없음 |
| **단위** | unregister() 후 핸들러 해제 | subscribe 이전 상태로 복원 + `_handler_ref is None` |
| **단위** | 큐 포화 시 Fail-Open | `_dropped_count` 증가, EventBus 블로킹 없음 |
| **단위** | handler_name 고유성 | `observer._handler_ref.__name__`에 `id(self)` 포함 |
| **단위** | Zero-Feed: 0 트래픽 10회 tick 후 폭주 | Z-Score > threshold 탐지 |
| **단위** | Zero-Feed 누락: tick 없이 폭주 | Z-Score 과소평가 → **미탐지 확인** (네거티브 테스트) |
| **통합** | 100개 이벤트 연속 발행 | co_occurrence_tracker에 기록됨 |
| **통합** | 이벤트 폭주 (1분 1000건) | rate_anomaly 탐지 |
| **통합** | get_statistics() 대시보드 필드 | `events_dropped`, `queue_size`, `queue_maxsize` 포함 |
| **성능** | 10,000 이벤트 연속 처리 | Hot Path 총 오버헤드 ≤ 10ms (1μs × 10,000) |
| **Thread Safety** | 5개 스레드 동시 발행 | 큐 데이터 무결성 유지, 드롭 카운트 정확 |
