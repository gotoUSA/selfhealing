# 254. Wildcard Observer — 전체 이벤트 관찰자

> **Version**: 1.0.0
> **Created**: 2026-02-20
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `services/correlation_engine/wildcard_observer.py`

---

## 0. 요약

현재 EventBus에는 **"모든 이벤트를 관찰하는 관찰자"**가 없다. 각 핸들러는 특정 `EventType`만 구독하며, Wildcard(전체) 구독 메커니즘이 존재하지 않는다. 이 모듈은 42개 `EventType` 전체를 구독하여 이벤트 스트림을 수집하고, 다른 Correlation Engine 모듈(DAG Builder, Co-occurrence Tracker)에 공급하는 **데이터 수집 계층**이다.

이것이 Gap-1(EventType 폐쇄성)을 해결하는 핵심이며, **사전 정의되지 않은 미지의 상관관계 발견**의 전제 조건이다.

---

## 1. 현재 EventBus의 구독 한계

### 1.1 코드 분석

[bus/\_\_init\_\_.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus/bus/__init__.py) 확인 결과:

```python
def subscribe(self, event_type: EventType, handler, priority=EventPriority.NORMAL):
    # EventType enum 값만 수락 — Wild card 없음
```

```python
def publish(self, event: SelfHealingEvent) -> int:
    subscriptions = self._subscribers.get(event.event_type, [])
    if not subscriptions:
        logger.debug(f"[EventBus] No subscribers for {event.event_type.value}")
        return 0  # 구독자 없으면 조용히 무시
```

- **Wildcard 구독 없음** — 모든 이벤트 타입을 개별 구독해야 함
- **구독자 없는 이벤트는 무시** — 미처리 이벤트 감지 불가
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
3. **Non-blocking**: 이벤트 수신 시 버퍼링만 수행, 분석은 별도 tick에서
4. **Auto-Discovery**: `EventType` enum의 모든 멤버를 `__members__`로 자동 열거

### 2.2 핵심 자료구조

```python
@dataclass
class ObservedEvent:
    """관찰된 이벤트의 경량 레코드"""
    event_type: str
    service_name: str
    timestamp: float
    correlation_id: str | None
    data_fingerprint: str     # event.data의 해시 (전체 데이터 저장 안 함)
    priority: int


class EventWindow:
    """시간 윈도우 내 이벤트 버퍼"""
    def __init__(self, window_seconds: float, max_events: int):
        self._window_seconds = window_seconds
        self._max_events = max_events
        self._events: deque[ObservedEvent] = deque(maxlen=max_events)
        self._event_counts: Counter = Counter()  # 타입별 카운트

    def add(self, event: ObservedEvent) -> None: ...
    def get_window(self, since: float) -> list[ObservedEvent]: ...
    def get_type_counts(self) -> dict[str, int]: ...
    def clear_expired(self) -> int: ...
```

---

## 3. 구현

### 3.1 전체 EventType 자동 구독

```python
class WildcardObserver:
    """EventBus의 모든 이벤트를 관찰하는 수집 계층"""

    def __init__(
        self,
        settings: CorrelationEngineSettings,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        self._settings = settings
        self._co_occurrence = co_occurrence_tracker
        self._window = EventWindow(
            window_seconds=settings.window_seconds,
            max_events=settings.max_event_buffer,
        )
        self._subscribed = False
        self._total_observed: int = 0
        self._rate_detector = ZScoreDetector(
            window_size=100,
            threshold=settings.event_rate_zscore_threshold,  # 기본 3.0
        )
        self._lock = threading.Lock()

    def register(self, event_bus: SelfHealingEventBus) -> None:
        """모든 EventType에 대해 구독 등록"""
        if self._subscribed:
            return

        for event_type in EventType.__members__.values():
            try:
                event_bus.subscribe(
                    event_type=event_type,
                    handler=self._on_event,
                    priority=EventPriority.LOW,  # 기존 핸들러보다 후순위
                )
            except Exception as e:
                logger.warning(
                    f"[WildcardObserver] Failed to subscribe to {event_type.value}: {e}"
                )

        self._subscribed = True
        logger.info(
            f"[WildcardObserver] Subscribed to {len(EventType.__members__)} event types"
        )

    def unregister(self, event_bus: SelfHealingEventBus) -> None:
        """구독 해제 (모듈 제거 시)"""
        for event_type in EventType.__members__.values():
            try:
                event_bus.unsubscribe(event_type, self._on_event)
            except Exception:
                pass
        self._subscribed = False
```

### 3.2 이벤트 수신 핸들러

```python
    def _on_event(self, event: SelfHealingEvent) -> None:
        """이벤트 수신 — 버퍼링만 수행 (빠르게 반환)"""
        try:
            observed = ObservedEvent(
                event_type=event.event_type.value,
                service_name=event.data.get("service_name", event.source or "unknown"),
                timestamp=event.timestamp,
                correlation_id=event.correlation_id,
                data_fingerprint=self._fingerprint(event.data),
                priority=event.priority.value if event.priority else 0,
            )

            with self._lock:
                self._window.add(observed)
                self._total_observed += 1

                # Co-occurrence Tracker에 즉시 기록 (O(1) 연산)
                self._co_occurrence.record_event(
                    event_type=observed.event_type,
                    timestamp=observed.timestamp,
                    service_name=observed.service_name,
                )

        except Exception as e:
            # Observer 오류가 기존 핸들러에 영향 주지 않음
            logger.debug(f"[WildcardObserver] Error recording event: {e}")

    @staticmethod
    def _fingerprint(data: dict) -> str:
        """이벤트 데이터의 경량 해시 (전체 저장 안 함)"""
        try:
            content = str(sorted(data.items()))
            return hashlib.md5(content.encode()).hexdigest()[:8]
        except Exception:
            return "unknown"
```

### 3.3 이벤트 발생률 이상 탐지

```python
    def check_event_rate_anomaly(self) -> dict | None:
        """전체 이벤트 발생률의 이상 여부 확인"""
        with self._lock:
            current_time = time.time()
            # 최근 1분간 이벤트 수
            recent_count = len(self._window.get_window(current_time - 60))

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
                "timestamp": time.time(),
            }
        return None
```

### 3.4 윈도우 내 이벤트 스냅샷

```python
    def get_current_window(self) -> list[ObservedEvent]:
        """현재 시간 윈도우 내 이벤트 반환 (DAG Builder 입력용)"""
        with self._lock:
            current_time = time.time()
            return self._window.get_window(current_time - self._settings.window_seconds)

    def get_statistics(self) -> dict:
        """Observer 통계 (대시보드용)"""
        with self._lock:
            window_events = self._window.get_window(time.time() - 60)
            return {
                "total_observed": self._total_observed,
                "window_size": len(self._window._events),
                "events_last_minute": len(window_events),
                "type_distribution": self._window.get_type_counts(),
                "subscribed_types": len(EventType.__members__),
                "is_active": self._subscribed,
            }
```

---

## 4. 미래 확장: EventType 개방성 대응

현재 `EventType`은 닫힌 Enum이지만, 향후 확장 시 Observer가 자동 대응하도록:

### 4.1 방안 A: 재등록 메커니즘

```python
    def refresh_subscriptions(self, event_bus: SelfHealingEventBus) -> int:
        """EventType 변경 시 재등록"""
        current_types = set(EventType.__members__.values())
        subscribed_types = set(self._subscribed_types)

        new_types = current_types - subscribed_types
        for event_type in new_types:
            event_bus.subscribe(event_type, self._on_event, EventPriority.LOW)
            self._subscribed_types.add(event_type)

        return len(new_types)
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

| 연산 | 동시성 보장 |
|------|-----------|
| `_on_event()` | `threading.Lock`으로 `_window` 접근 보호 |
| `get_current_window()` | 동일 Lock 사용 |
| `check_event_rate_anomaly()` | Lock 범위 최소화 (카운트만 보호) |
| `co_occurrence.record_event()` | CoOccurrenceTracker 자체 thread-safe |

Lock 구간을 최소화하여 EventBus `publish()` 성능에 미치는 영향 ≤ 0.1ms.

---

## 6. 성능 영향 분석

### 6.1 EventBus publish() 오버헤드

| 연산 | 비용 | 비고 |
|------|------|------|
| `ObservedEvent` 생성 | ~1μs | 데이터클래스 생성 |
| `_fingerprint()` | ~5μs | MD5 해시 |
| `_window.add()` | ~1μs | deque append |
| `co_occurrence.record_event()` | ~10μs | dict lookup + append |
| **합계** | **~17μs** | 비기능 요구사항 ≤ 1ms 대비 1.7% |

### 6.2 메모리

| 구성 요소 | 크기 |
|-----------|------|
| `EventWindow` (10,000 이벤트) | ~1.2MB |
| `Counter` (42 타입) | ~2KB |
| `ZScoreDetector` (1개) | ~8KB |
| **합계** | **~1.2MB** |

---

## 7. 등록 시점

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

해제 시:
```python
def shutdown(self):
    if self._observer:
        self._observer.unregister(get_event_bus())
```

→ `correlation_engine/` 전체를 비활성화해도 `unregister()` 호출 한 번으로 정리.

---

## 8. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | register() 후 42개 구독 확인 | `bus.subscriber_count` 증가 |
| **단위** | 이벤트 발행 → _on_event 호출 | window에 이벤트 추가됨 |
| **단위** | Observer 예외 발생 | 기존 핸들러에 영향 없음 |
| **단위** | unregister() 후 핸들러 해제 | subscribe 이전 상태로 복원 |
| **통합** | 100개 이벤트 연속 발행 | co_occurrence_tracker에 기록됨 |
| **통합** | 이벤트 폭주 (1분 1000건) | rate_anomaly 탐지 |
| **성능** | 10,000 이벤트 연속 처리 | 총 오버헤드 ≤ 170ms |
| **Thread Safety** | 5개 스레드 동시 발행 | 데이터 무결성 유지 |
