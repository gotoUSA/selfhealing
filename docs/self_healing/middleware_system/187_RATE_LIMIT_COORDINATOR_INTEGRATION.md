# 187. Rate Limit Coordinator - AdaptiveThrottle 연동 구현

> **문서 버전**: 1.1.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/rate_limit_coordinator.py`, `selfhealing/services/throttle/adaptive.py`, `selfhealing/services/throttle/registry.py`
>
> **v1.1.0 변경사항**: 9가지 보완 구현 사항 추가 (§10)

## 1. 개요

본 문서는 `RateLimitCoordinator`와 `AdaptiveThrottle`의 429 통합 대응 연동 구현을 정의합니다.

### 1.1 문제 정의

현재 두 시스템이 **독립적으로 429 응답을 처리**:
- `RateLimitCoordinator`: 외부 API의 429 응답에 대한 분산 cooldown 관리
- `AdaptiveThrottle`: 내부 요청 limit 동적 조절

**문제점**: 외부 API에서 429 발생 시 내부 throttle limit이 연동되지 않아 **Self-DDoS 위험**

---

## 2. 현재 구현 분석

### 2.1 RateLimitCoordinator 핵심 구조

**코드 위치**: [rate_limit_coordinator.py](../../packages/selfhealing-python/src/selfhealing/services/rate_limit_coordinator.py)

```python
class RateLimitCoordinator:
    """
    Coordinates rate limiting across distributed workers.

    Prevents Self-DDoS by:
    1. Detecting 429 responses
    2. Setting global cooldown (shared across all workers)
    3. Making all workers wait before retrying
    4. Using exponential backoff with jitter
    """

    def wait_if_needed(self, key: str) -> RateLimitResult:
        """Wait if currently in cooldown period."""
        state = self._storage.get_state(key)
        if not state.is_in_cooldown:
            return RateLimitResult(waited=False, ...)
        # Cooldown 기간 동안 대기
        time.sleep(state.remaining_cooldown)
        return RateLimitResult(waited=True, wait_time=wait_time, ...)

    def on_rate_limited(self, key: str, retry_after: float | None = None, status_code: int = 429) -> float:
        """Handle a rate limit (429) response."""
        # Exponential backoff 계산
        consecutive = self._storage.increment_consecutive_429s(key)
        delay = base_delay * (self._config.backoff_multiplier ** (consecutive - 1))
        delay = min(delay, self._config.max_delay)
        # Jitter 추가 (Thundering Herd 방지)
        jitter = random.uniform(-jitter_range, jitter_range)
        delay = max(0.1, delay + jitter)
        # Global cooldown 설정
        self._storage.set_cooldown(key, time.time() + delay)
        return delay
```

**핵심 설정** (`RateLimitCoordinatorConfig`):

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `base_delay` | 1.0초 | 기본 대기 시간 |
| `max_delay` | 60.0초 | 최대 대기 시간 |
| `jitter_percent` | 30% | ±30% 랜덤 지터 |
| `default_retry_after` | 5.0초 | Retry-After 없을 때 기본값 |
| `backoff_multiplier` | 2.0 | 지수 백오프 배율 |

### 2.2 AdaptiveThrottle의 EventBus 연동 구조

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
def _emit_throttle_event(
    event_type_name: str,
    data: dict,
    priority_name: str = "NORMAL",
) -> None:
    """Throttle 관련 이벤트를 EventBus에 발행."""
    bus = _get_event_bus_safe()
    if bus is None:
        return
    # EventType, EventPriority 매핑
    event_type = getattr(EventType, event_type_name, None)
    priority = getattr(EventPriority, priority_name, EventPriority.NORMAL)
    bus.emit(event_type=event_type, data=data, source="throttle", priority=priority)
```

**기존 이벤트 유형**:
- `THROTTLE_LIMIT_CHANGED`: limit 변경 시
- `THROTTLE_SLA_WARNING`: SLA 경고 임계값 도달
- `THROTTLE_SLA_CRITICAL`: SLA 위험 임계값 도달
- `THROTTLE_LIMIT_RECOVERED`: min_limit에서 복구

---

## 3. 연동 설계

### 3.1 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         429 통합 대응 아키텍처                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐    on_rate_limited()    ┌───────────────────┐           │
│  │  External API     │ ──────────────────────► │RateLimitCoordinator│           │
│  │  (429 Response)   │                         └─────────┬─────────┘           │
│  └───────────────────┘                                   │                      │
│                                                          │ emit(RATE_LIMIT_429) │
│                                                          ▼                      │
│                                                 ┌───────────────────┐           │
│                                                 │    EventBus       │           │
│                                                 └─────────┬─────────┘           │
│                                                          │                      │
│                           ┌──────────────────────────────┼──────────────────┐   │
│                           │                              │                  │   │
│                           ▼                              ▼                  ▼   │
│               ┌───────────────────┐        ┌───────────────────┐   ┌──────────┐│
│               │ AdaptiveThrottle  │        │ Prometheus Metrics│   │  Audit   ││
│               │ adjust_for_429()  │        │                   │   │  System  ││
│               └───────────────────┘        └───────────────────┘   └──────────┘│
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 신규 이벤트 정의

**추가할 EventType** (event_bus.py):

```python
class EventType(Enum):
    # 기존 이벤트...

    # 429 Rate Limit 이벤트 (신규)
    RATE_LIMIT_429 = "rate_limit_429"              # 외부 API 429 발생
    RATE_LIMIT_COOLDOWN_START = "rate_limit_cooldown_start"  # Cooldown 시작
    RATE_LIMIT_COOLDOWN_END = "rate_limit_cooldown_end"      # Cooldown 종료
```

---

## 4. 구현 코드

### 4.1 RateLimitCoordinator EventBus 연동

**수정 위치**: `rate_limit_coordinator.py`

```python
def _emit_rate_limit_event(
    event_type_name: str,
    data: dict,
    priority_name: str = "HIGH",
) -> None:
    """
    Rate Limit 관련 이벤트를 EventBus에 발행.

    코드 근거: adaptive.py의 _emit_throttle_event 패턴 활용
    """
    try:
        from selfhealing.services.event_bus import EventType, EventPriority, get_event_bus

        bus = get_event_bus()
        event_type = getattr(EventType, event_type_name, None)
        if event_type is None:
            logger.warning(f"[RateLimitCoordinator] Unknown event type: {event_type_name}")
            return

        priority = getattr(EventPriority, priority_name, EventPriority.HIGH)
        bus.emit(
            event_type=event_type,
            data=data,
            source="rate_limit_coordinator",
            priority=priority,
        )
        logger.debug(f"[RateLimitCoordinator] Emitted {event_type_name}")
    except ImportError:
        logger.debug("[RateLimitCoordinator] EventBus not available")
    except Exception as e:
        logger.warning(f"[RateLimitCoordinator] Failed to emit event: {e}")
```

**on_rate_limited() 수정**:

```python
def on_rate_limited(
    self,
    key: str,
    retry_after: float | None = None,
    status_code: int = 429,
) -> float:
    """Handle a rate limit (429) response with EventBus integration."""
    # 기존 로직...
    consecutive = self._storage.increment_consecutive_429s(key)
    delay = base_delay * (self._config.backoff_multiplier ** (consecutive - 1))
    delay = min(delay, self._config.max_delay)
    jitter = random.uniform(-jitter_range, jitter_range)
    delay = max(0.1, delay + jitter)
    cooldown_until = time.time() + delay
    self._storage.set_cooldown(key, cooldown_until)

    # EventBus 연동 (신규)
    _emit_rate_limit_event(
        "RATE_LIMIT_429",
        {
            "key": key,
            "status_code": status_code,
            "retry_after_header": retry_after,
            "calculated_delay": delay,
            "consecutive_429s": consecutive,
            "cooldown_until": cooldown_until,
        },
        priority_name="HIGH",
    )

    logger.warning(
        f"[RateLimitCoordinator] Rate limited on '{key}' "
        f"(status={status_code}, consecutive={consecutive}, cooldown={delay:.2f}s)"
    )
    return delay
```

### 4.2 AdaptiveThrottle 429 이벤트 수신

**수정 위치**: `adaptive.py`

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    """AdaptiveThrottle with 429 event integration."""

    def __init__(self, config: ThrottleConfig | None = None):
        super().__init__(config)
        # 기존 초기화...

        # 429 연동 상태
        self._rate_limit_keys: dict[str, float] = {}  # key -> cooldown_until
        self._429_limit_reduction_percent: float = 0.5  # 429 발생 시 50% 감소

        # EventBus 구독 등록
        self._subscribe_rate_limit_events()

    def _subscribe_rate_limit_events(self) -> None:
        """Rate Limit 이벤트 구독 등록."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.RATE_LIMIT_429,
                self._handle_rate_limit_429,
                subscriber_id="adaptive_throttle",
            )
            logger.info("[AdaptiveThrottle] Subscribed to RATE_LIMIT_429 events")
        except ImportError:
            logger.debug("[AdaptiveThrottle] EventBus not available for subscription")
        except Exception as e:
            logger.warning(f"[AdaptiveThrottle] Failed to subscribe: {e}")

    def _handle_rate_limit_429(self, event_data: dict) -> None:
        """
        429 이벤트 수신 시 limit 조정.

        전략:
        - consecutive_429s에 따른 단계별 감소
        - 1회: 20% 감소
        - 2회: 40% 감소
        - 3회 이상: 50% 감소 + SLA Warning 발행
        """
        key = event_data.get("key", "unknown")
        consecutive = event_data.get("consecutive_429s", 1)
        cooldown_until = event_data.get("cooldown_until", 0)

        # Cooldown 상태 저장
        self._rate_limit_keys[key] = cooldown_until

        # 감소 비율 결정
        if consecutive >= 3:
            reduction_percent = 0.5  # 50%
        elif consecutive == 2:
            reduction_percent = 0.6  # 40%
        else:
            reduction_percent = 0.8  # 20%

        previous_limit = self._current_limit
        new_limit = max(int(self._current_limit * reduction_percent), self.config.min_limit)

        logger.warning(
            f"[AdaptiveThrottle] 429 response on '{key}', "
            f"reducing limit: {previous_limit} → {new_limit} "
            f"(consecutive={consecutive}, reduction={int((1-reduction_percent)*100)}%)"
        )

        self.current_limit = new_limit

        # 메트릭 기록
        _record_throttle_metrics(
            service="default",
            limit=new_limit,
            denied_reason="rate_limit_429",
        )

        # SLA Warning 발행 (3회 이상)
        if consecutive >= 3:
            _emit_throttle_event(
                "THROTTLE_SLA_WARNING",
                {
                    "trigger": "rate_limit_429",
                    "key": key,
                    "consecutive_429s": consecutive,
                    "current_limit": new_limit,
                    "previous_limit": previous_limit,
                },
                priority_name="HIGH",
            )

        # Limit 변경 이벤트 발행
        _emit_throttle_event(
            "THROTTLE_LIMIT_CHANGED",
            {
                "previous_limit": previous_limit,
                "new_limit": new_limit,
                "reason": "rate_limit_429",
                "key": key,
                "consecutive_429s": consecutive,
            },
            priority_name="HIGH",
        )

    def is_rate_limited_for_key(self, key: str) -> bool:
        """특정 외부 API가 현재 cooldown 상태인지 확인."""
        cooldown_until = self._rate_limit_keys.get(key, 0)
        return time.time() < cooldown_until
```

### 4.3 Prometheus 메트릭 정의

**추가 위치**: `services/metrics/definitions.py`

```python
# =============================================================================
# Rate Limit Coordinator Metrics (429 통합)
# =============================================================================

rate_limit_429_total = get_or_create_counter(
    "selfhealing_rate_limit_429_total",
    "Total 429 responses received from external APIs",
    ["key", "status_code"],
)

rate_limit_cooldown_seconds = get_or_create_histogram(
    "selfhealing_rate_limit_cooldown_seconds",
    "Cooldown duration after 429 response",
    ["key"],
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

rate_limit_consecutive_429s = get_or_create_gauge(
    "selfhealing_rate_limit_consecutive_429s",
    "Current consecutive 429 count per key",
    ["key"],
)

rate_limit_throttle_adjustments_total = get_or_create_counter(
    "selfhealing_rate_limit_throttle_adjustments_total",
    "Total throttle limit adjustments triggered by 429",
    ["key", "reduction_percent"],
)
```

---

## 5. 통합 흐름

### 5.1 429 발생 시 처리 흐름

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 1. External API에서 429 응답 수신                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  response = requests.get("https://payment.api/...")                         │
│  if response.status_code == 429:                                            │
│      coordinator.on_rate_limited("payment_api", retry_after=5)              │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ 2. RateLimitCoordinator.on_rate_limited()                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  - consecutive_429s 증가                                                     │
│  - Exponential backoff 계산 (base × 2^consecutive)                           │
│  - Jitter 추가 (±30%)                                                        │
│  - Storage에 cooldown 설정 (Redis/DB/InMemory)                              │
│  - EventBus에 RATE_LIMIT_429 발행                                            │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ 3. EventBus 이벤트 전파                                                       │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  bus.emit(                                                                   │
│      event_type=EventType.RATE_LIMIT_429,                                   │
│      data={"key": "payment_api", "consecutive_429s": 2, ...},               │
│      priority=EventPriority.HIGH,                                           │
│  )                                                                          │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ 4. AdaptiveThrottle._handle_rate_limit_429()                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  - consecutive에 따른 감소 비율 결정 (20%/40%/50%)                            │
│  - limit 즉시 감소                                                           │
│  - Prometheus 메트릭 기록                                                    │
│  - THROTTLE_LIMIT_CHANGED 이벤트 발행                                        │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ 5. Prometheus 메트릭 수집                                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  - rate_limit_429_total{key="payment_api"} ++                               │
│  - rate_limit_cooldown_seconds{key="payment_api"} observe(15.3)             │
│  - selfhealing_throttle_limit{service="default"} = 50                       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. 데코레이터 통합 사용법

```python
# 기존 rate_limit_aware 데코레이터와 통합
@coordinator.rate_limit_aware("payment_api")
def call_payment_api():
    """
    자동으로:
    1. Cooldown 대기 (wait_if_needed)
    2. 429 감지 시 on_rate_limited 호출
    3. EventBus를 통해 AdaptiveThrottle에 전파
    """
    return requests.post("https://payment.api/process", ...)
```

---

## 7. 설정

### 7.1 settings.py 추가

```python
class RateLimitThrottleIntegrationSettings(BaseModel):
    """429-Throttle 연동 설정."""

    # 429 발생 시 throttle limit 감소 활성화
    enabled: bool = True

    # 연속 429 횟수별 limit 감소 비율
    reduction_ratios: dict[int, float] = {
        1: 0.8,   # 1회: 20% 감소
        2: 0.6,   # 2회: 40% 감소
        3: 0.5,   # 3회 이상: 50% 감소
    }

    # SLA Warning 발행 임계값 (연속 429 횟수)
    sla_warning_threshold: int = 3

    # Cooldown 해제 후 limit 복구 전략
    recovery_strategy: str = "gradual"  # "immediate" | "gradual"
    recovery_dampening_steps: int = 3   # gradual 시 단계 수
```

---

## 8. 테스트 시나리오

### 8.1 단위 테스트

```python
class TestRateLimitThrottleIntegration:
    """429-Throttle 연동 테스트."""

    def test_429_reduces_throttle_limit(self):
        """429 발생 시 throttle limit 감소 확인."""
        coordinator = RateLimitCoordinator()
        throttle = AdaptiveThrottle()

        initial_limit = throttle.current_limit

        # 429 발생 시뮬레이션
        coordinator.on_rate_limited("test_api", retry_after=5)

        # EventBus 이벤트 처리 대기
        time.sleep(0.1)

        # limit 감소 확인
        assert throttle.current_limit < initial_limit

    def test_consecutive_429_progressive_reduction(self):
        """연속 429 시 점진적 감소 확인."""
        coordinator = RateLimitCoordinator()
        throttle = AdaptiveThrottle()

        limits = [throttle.current_limit]

        # 연속 429 발생
        for i in range(3):
            coordinator.on_rate_limited("test_api")
            time.sleep(0.1)
            limits.append(throttle.current_limit)

        # 점진적 감소 확인
        assert limits[1] > limits[2] > limits[3]
```

---

## 9. 모니터링 대시보드

### 9.1 Grafana 패널

```yaml
# 429 발생률 (Rate)
- title: "429 Response Rate"
  query: |
    rate(selfhealing_rate_limit_429_total[5m])

# Cooldown 지속 시간 분포
- title: "Cooldown Duration Distribution"
  query: |
    histogram_quantile(0.95,
      rate(selfhealing_rate_limit_cooldown_seconds_bucket[5m])
    )

# Throttle Limit 추이
- title: "Throttle Limit (429 영향)"
  query: |
    selfhealing_throttle_limit{service="default"}
```

---

## 10. 보완 구현 사항 (v1.1.0)

> **추가일**: 2026-02-06
> **보완 근거**: 코드 리뷰 및 기존 구현 분석

### 10.1 전역 EventBus - Kafka 기반 분산 전파

#### 10.1.1 문제점

현재 `SelfHealingEventBus`는 **In-memory 단일 프로세스 버스**입니다.

**코드 근거**: [services/event_bus.py](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py)
```python
"""
Self-Healing Event Bus - Component Decoupling System.
Features:
- In-memory event bus (single-process)
- Thread-safe event handling
"""
```

한 Pod가 429를 받아도 다른 Pod에는 전파되지 않아 **집단 방어** 불가.

#### 10.1.2 구현: DistributedRateLimitChannel

**코드 근거**: 기존 [adapters/kafka/event_bus.py](../../packages/selfhealing-python/src/selfhealing/adapters/kafka/event_bus.py)의 `KafkaEventBus` 활용

```python
# 파일: services/rate_limit/distributed_channel.py
"""
분산 Rate Limit 이벤트 채널.

Kafka를 통해 429 이벤트를 전체 클러스터에 전파합니다.
"""
from __future__ import annotations

import logging
from typing import Callable

from selfhealing.adapters.kafka.event_bus import KafkaEventBus

logger = logging.getLogger(__name__)

RATE_LIMIT_TOPIC = "selfhealing.rate_limit.events"


class DistributedRateLimitChannel:
    """
    Kafka 기반 분산 Rate Limit 이벤트 채널.

    네이밍 선택 이유:
    - 'Channel': Kafka Topic을 추상화한 개념으로, EventBus와 구분
    - 'Distributed': 단일 Pod 내 In-memory와 명확히 구분
    - 기존 KafkaEventBus의 publish/subscribe 패턴 재사용

    Usage:
        channel = DistributedRateLimitChannel()

        # 429 발생 시 전체 클러스터에 전파
        channel.broadcast_rate_limit_429("payment_api", consecutive=3)

        # 구독 (각 Pod에서 호출)
        channel.subscribe_rate_limit_429(handler_callback)
    """

    def __init__(self, kafka_bus: KafkaEventBus | None = None):
        self._kafka_bus = kafka_bus or KafkaEventBus()
        self._handlers: list[Callable[[dict], None]] = []

    def broadcast_rate_limit_429(
        self,
        key: str,
        consecutive_429s: int,
        cooldown_until: float,
        calculated_delay: float,
    ) -> bool:
        """
        429 이벤트를 전체 클러스터에 브로드캐스트.

        Returns:
            전송 성공 여부
        """
        event = {
            "event_type": "RATE_LIMIT_429",
            "key": key,
            "consecutive_429s": consecutive_429s,
            "cooldown_until": cooldown_until,
            "calculated_delay": calculated_delay,
        }

        success = self._kafka_bus.publish(
            topic=RATE_LIMIT_TOPIC,
            event=event,
            key=key,  # 동일 key는 동일 파티션으로 순서 보장
        )

        if success:
            logger.info(
                f"[DistributedRateLimitChannel] Broadcasted 429 for '{key}' "
                f"(consecutive={consecutive_429s})"
            )

        return success

    def subscribe_rate_limit_429(
        self,
        handler: Callable[[dict], None],
    ) -> None:
        """
        429 이벤트 구독 등록.

        Args:
            handler: 이벤트 핸들러 (event_data dict를 받음)
        """
        self._handlers.append(handler)
        self._kafka_bus.subscribe(RATE_LIMIT_TOPIC, self._dispatch_to_handlers)

    def _dispatch_to_handlers(self, event) -> bool:
        """Kafka 이벤트를 등록된 핸들러들에 전달."""
        for handler in self._handlers:
            try:
                handler(event.value)
            except Exception as e:
                logger.error(f"[DistributedRateLimitChannel] Handler error: {e}")
        return True

    def start(self) -> None:
        """Kafka Consumer 시작."""
        self._kafka_bus.start()

    def stop(self) -> None:
        """정지."""
        self._kafka_bus.stop()
```

#### 10.1.3 RateLimitCoordinator 연동

```python
# rate_limit_coordinator.py 수정
class RateLimitCoordinator:
    def __init__(
        self,
        storage: RateLimitStorageInterface | None = None,
        config: RateLimitCoordinatorConfig | None = None,
        distributed_channel: DistributedRateLimitChannel | None = None,
    ) -> None:
        # ... 기존 코드 ...
        self._distributed_channel = distributed_channel

    def on_rate_limited(self, key: str, ...) -> float:
        # ... 기존 로직 ...

        # 분산 전파 (Kafka)
        if self._distributed_channel:
            self._distributed_channel.broadcast_rate_limit_429(
                key=key,
                consecutive_429s=consecutive,
                cooldown_until=cooldown_until,
                calculated_delay=delay,
            )

        # 로컬 EventBus 전파 (기존 유지)
        _emit_rate_limit_event("RATE_LIMIT_429", {...})

        return delay
```

---

### 10.2 이벤트 폭풍 방지 - 디바운싱

#### 10.2.1 문제점

수백 개 요청이 동시에 429를 받으면 이벤트가 폭발적으로 발행됨.

#### 10.2.2 구현: debounce_window_seconds

**네이밍 선택 이유**:
- `debounce_window_seconds`: "Sliding Window"는 RTT 계산에서 이미 사용 중
- 기존 코드의 `_emergency_cache_ttl_seconds` 패턴과 일관

```python
# rate_limit_coordinator.py 수정

@dataclass
class RateLimitCoordinatorConfig:
    # ... 기존 설정 ...

    # 디바운싱 설정 (신규)
    debounce_window_seconds: float = 5.0  # 동일 key에 대한 이벤트 중복 방지 윈도우


class RateLimitCoordinator:
    def __init__(self, ...):
        # ... 기존 코드 ...

        # 디바운싱 상태
        self._last_event_emit_times: dict[str, float] = {}
        self._debounce_lock = threading.Lock()

    def _should_emit_event(self, key: str) -> bool:
        """
        디바운싱 확인 - 동일 key에 대해 윈도우 내 중복 이벤트 방지.

        Args:
            key: Rate limit key

        Returns:
            이벤트 발행 여부
        """
        now = time.time()

        with self._debounce_lock:
            last_time = self._last_event_emit_times.get(key, 0)

            if now - last_time < self._config.debounce_window_seconds:
                logger.debug(
                    f"[RateLimitCoordinator] Debounced event for '{key}' "
                    f"(last emit {now - last_time:.2f}s ago)"
                )
                return False

            self._last_event_emit_times[key] = now
            return True

    def on_rate_limited(self, key: str, ...) -> float:
        # ... 기존 cooldown 로직 ...

        # 디바운싱 적용
        if self._should_emit_event(key):
            _emit_rate_limit_event("RATE_LIMIT_429", {...})

            # 분산 전파 (Kafka)
            if self._distributed_channel:
                self._distributed_channel.broadcast_rate_limit_429(...)

        return delay
```

#### 10.2.3 설정

```python
# settings.py 수정
class RateLimitThrottleIntegrationSettings(BaseModel):
    # ... 기존 설정 ...

    # 디바운싱 설정
    debounce_window_seconds: float = 5.0
    """동일 key에 대한 이벤트 중복 방지 윈도우 (초)."""
```

---

### 10.3 Key to Service 매핑 - ThrottleRegistry 활용

#### 10.3.1 기존 구현 확인

**코드 근거**: [services/throttle/registry.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/registry.py)

```python
class ThrottleRegistry:
    """
    서비스별 Throttle 인스턴스 관리 레지스트리.

    각 외부 서비스마다 독립적인 AdaptiveThrottle을 관리하며,
    Circuit Breaker 상태에 따라 limit을 자동 조정합니다.
    """

    def get_throttle(self, service_name: str) -> AdaptiveThrottle:
        """서비스별 Throttle 인스턴스 가져오기 (없으면 생성)."""
        with self._throttle_lock:
            if service_name not in self._throttles:
                self._create_throttle(service_name)
            return self._throttles[service_name].throttle
```

**ThrottleRegistry가 이미 존재!** 서비스별 격리가 가능합니다.

#### 10.3.2 Key 매핑 확장

**네이밍 선택**: `key_to_service_mapping` (기존 `ThrottleRegistry` 활용)

```python
# settings.py 수정
class RateLimitThrottleIntegrationSettings(BaseModel):
    # ... 기존 설정 ...

    # Key-Service 매핑 (인접 간섭 방지)
    key_to_service_mapping: dict[str, str] = {
        "payment_api": "payment",
        "notification_api": "notification",
        "analytics_api": "analytics",
    }
    """
    Rate Limit Key와 AdaptiveThrottle 서비스 매핑.

    미지정 Key는 'default' 서비스 Throttle 사용.

    네이밍 선택 이유:
    - 기존 ThrottleRegistry의 service_name 개념과 일치
    - key_mapping보다 명확한 방향성 표현 (key → service)
    """

    default_service: str = "default"
    """매핑되지 않은 Key의 기본 서비스."""
```

#### 10.3.3 AdaptiveThrottle 429 핸들러 수정

```python
# adaptive.py 수정
def _handle_rate_limit_429(self, event_data: dict) -> None:
    """429 이벤트 수신 시 해당 서비스의 limit만 조정."""
    key = event_data.get("key", "unknown")
    consecutive = event_data.get("consecutive_429s", 1)

    # Key → Service 매핑 조회
    try:
        from selfhealing.settings import get_rate_limit_throttle_settings

        settings = get_rate_limit_throttle_settings()
        service_name = settings.key_to_service_mapping.get(
            key, settings.default_service
        )
    except ImportError:
        service_name = "default"

    # ThrottleRegistry에서 해당 서비스 Throttle 가져오기
    try:
        from selfhealing.services.throttle.registry import get_throttle_registry

        registry = get_throttle_registry()
        throttle = registry.get_throttle(service_name)
    except ImportError:
        # 레지스트리 없으면 self (기존 동작)
        throttle = self

    # 해당 서비스 Throttle에만 limit 감소 적용
    previous_limit = throttle._current_limit
    new_limit = max(
        int(throttle._current_limit * reduction_percent),
        throttle.config.min_limit,
    )

    logger.warning(
        f"[AdaptiveThrottle] 429 on '{key}' → service '{service_name}', "
        f"limit: {previous_limit} → {new_limit}"
    )

    throttle.current_limit = new_limit
```

---

### 10.4 Gradual Recovery - COOLDOWN_END 이벤트 연동

#### 10.4.1 기존 구현 확인

**코드 근거**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
# 이미 구현된 Recovery Dampening
RECOVERY_DAMPENING_MULTIPLIERS: tuple[float, ...] = (0.8, 0.9, 1.0)
_recovery_dampening_interval_seconds: float = 30.0  # 30초 간격
```

80% → 90% → 100%로 30초 간격 복구 **이미 구현됨**.

#### 10.4.2 RATE_LIMIT_COOLDOWN_END 이벤트 발행

```python
# rate_limit_coordinator.py 수정

def _schedule_cooldown_end_event(self, key: str, cooldown_until: float) -> None:
    """
    Cooldown 종료 시점에 RATE_LIMIT_COOLDOWN_END 이벤트 예약.

    Threading Timer 사용으로 비동기 발행.
    """
    delay = cooldown_until - time.time()
    if delay <= 0:
        return

    def emit_cooldown_end():
        _emit_rate_limit_event(
            "RATE_LIMIT_COOLDOWN_END",
            {
                "key": key,
                "cooldown_ended_at": time.time(),
            },
            priority_name="NORMAL",
        )
        logger.info(f"[RateLimitCoordinator] Cooldown ended for '{key}'")

    timer = threading.Timer(delay, emit_cooldown_end)
    timer.daemon = True
    timer.start()


def on_rate_limited(self, key: str, ...) -> float:
    # ... 기존 로직 ...
    cooldown_until = time.time() + delay
    self._storage.set_cooldown(key, cooldown_until)

    # Cooldown 종료 이벤트 예약 (신규)
    self._schedule_cooldown_end_event(key, cooldown_until)

    return delay
```

#### 10.4.3 AdaptiveThrottle에서 COOLDOWN_END 구독

```python
# adaptive.py 수정

def _subscribe_rate_limit_events(self) -> None:
    """Rate Limit 이벤트 구독 등록."""
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus

        bus = get_event_bus()

        # 429 이벤트 구독
        bus.subscribe(EventType.RATE_LIMIT_429, self._handle_rate_limit_429)

        # Cooldown 종료 이벤트 구독 (신규)
        bus.subscribe(
            EventType.RATE_LIMIT_COOLDOWN_END,
            self._handle_cooldown_end,
        )

        logger.info("[AdaptiveThrottle] Subscribed to rate limit events")
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available")


def _handle_cooldown_end(self, event_data: dict) -> None:
    """
    Cooldown 종료 시 Recovery Dampening 시작.

    기존 RECOVERY_DAMPENING_MULTIPLIERS (80% → 90% → 100%) 활용.
    """
    key = event_data.get("key", "unknown")

    # 해당 Key의 429 상태 제거
    if key in self._rate_limit_keys:
        del self._rate_limit_keys[key]

    # Recovery Dampening 시작 (기존 메서드 활용)
    self.start_recovery_dampening()

    logger.info(
        f"[AdaptiveThrottle] Cooldown ended for '{key}', "
        f"starting recovery dampening (80% → 90% → 100%)"
    )
```

---

### 10.5 Canary Request (복구 정찰 요청)

#### 10.5.1 설계 근거

**코드 근거**: Circuit Breaker의 HALF_OPEN 패턴

```python
# circuit_breaker/service.py 참조
class CircuitBreaker:
    def _try_half_open(self) -> bool:
        """HALF_OPEN 상태에서 1개 요청만 허용."""
```

**네이밍 선택**: `CanaryRequest`
- "Probe"보다 CB의 HALF_OPEN과 일관된 개념
- 카나리아 배포의 "선별적 트래픽" 개념과 유사

#### 10.5.2 구현

```python
# rate_limit_coordinator.py 수정

@dataclass
class RateLimitResult:
    """Result of a rate limit check or wait operation."""

    waited: bool = False
    wait_time: float = 0.0
    was_rate_limited: bool = False
    consecutive_429s: int = 0

    # Canary 모드 (신규)
    is_canary: bool = False
    """Cooldown 직후 첫 요청 - 정찰 요청 모드."""


class RateLimitCoordinator:
    def __init__(self, ...):
        # ... 기존 코드 ...

        # Canary 상태 추적
        self._canary_in_progress: dict[str, bool] = {}
        self._canary_lock = threading.Lock()

    def wait_if_needed(self, key: str) -> RateLimitResult:
        """
        Wait if in cooldown, return canary mode info.

        Cooldown 종료 직후 첫 요청은 is_canary=True로 표시.
        호출자는 이를 활용해 단일 요청만 선별적으로 허용 가능.
        """
        state = self._storage.get_state(key)

        if state.is_in_cooldown:
            time.sleep(state.remaining_cooldown)
            return RateLimitResult(
                waited=True,
                wait_time=state.remaining_cooldown,
                was_rate_limited=True,
                consecutive_429s=state.consecutive_429s,
            )

        # Cooldown 종료 직후 - Canary 모드 확인
        is_canary = False
        if state.consecutive_429s > 0:
            with self._canary_lock:
                if key not in self._canary_in_progress:
                    self._canary_in_progress[key] = True
                    is_canary = True

        return RateLimitResult(
            waited=False,
            was_rate_limited=state.consecutive_429s > 0,
            consecutive_429s=state.consecutive_429s,
            is_canary=is_canary,
        )

    def on_success(self, key: str) -> None:
        """Handle a successful response - clear canary state."""
        # Canary 상태 해제
        with self._canary_lock:
            if key in self._canary_in_progress:
                del self._canary_in_progress[key]

        # 기존 로직
        state = self._storage.get_state(key)
        if state.consecutive_429s > 0:
            self._storage.reset_consecutive_429s(key)
```

#### 10.5.3 사용 예시

```python
@coordinator.rate_limit_aware("payment_api")
def call_payment_api():
    result = coordinator.wait_if_needed("payment_api")

    if result.is_canary:
        # Canary 모드: 제한된 timeout으로 정찰
        logger.info("[Canary] Testing recovery...")
        response = requests.post(url, timeout=5)  # 짧은 timeout
    else:
        response = requests.post(url, timeout=30)

    return response
```

---

### 10.6 Conservative Limit (Min-Winner 정책)

#### 10.6.1 설계

RTT 기반 조절과 429 기반 조절이 충돌 시 **더 보수적인 값 선택**.

$$Limit_{effective} = \min(Limit_{RTT}, Limit_{429})$$

**네이밍 선택**: `conservative_limit`
- `min_winner`보다 의도가 명확
- "보수적" = 안전 우선 정책

#### 10.6.2 구현

```python
# adaptive.py 수정

class AdaptiveThrottle(SlidingWindowThrottle):
    def __init__(self, config: ThrottleConfig | None = None):
        super().__init__(config)
        # ... 기존 코드 ...

        # Conservative Limit 상태 (신규)
        self._rtt_suggested_limit: int = config.initial_limit if config else 100
        self._429_suggested_limit: int = config.max_limit if config else 1000
        self._conservative_enabled: bool = True

    @property
    def conservative_limit(self) -> int:
        """
        Min-Winner 정책 적용한 보수적 limit.

        RTT 기반 limit과 429 기반 limit 중 낮은 값 반환.
        """
        if not self._conservative_enabled:
            return self._current_limit

        return min(self._rtt_suggested_limit, self._429_suggested_limit)

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """RTT 기반 limit 조정 (conservative_limit 적용)."""
        # ... 기존 gradient 계산 ...

        # RTT 기반 limit 계산
        if gradient > 0.1:
            self._rtt_suggested_limit = int(self._current_limit * self.config.decrease_ratio)
        elif gradient < -0.05:
            self._rtt_suggested_limit = self._current_limit + self.config.increase_step

        # Conservative Limit 적용
        if self._conservative_enabled:
            self.current_limit = self.conservative_limit
        else:
            self.current_limit = self._rtt_suggested_limit

    def _handle_rate_limit_429(self, event_data: dict) -> None:
        """429 이벤트 수신 시 429 suggested limit 조정."""
        # ... 감소 비율 계산 ...

        # 429 기반 limit 저장
        self._429_suggested_limit = max(
            int(self._current_limit * reduction_percent),
            self.config.min_limit,
        )

        # Conservative Limit 적용
        self.current_limit = self.conservative_limit

        logger.warning(
            f"[AdaptiveThrottle] Conservative limit applied: "
            f"RTT={self._rtt_suggested_limit}, 429={self._429_suggested_limit}, "
            f"effective={self.conservative_limit}"
        )
```

---

### 10.7 Priority-aware Throttle (우선순위 보호)

#### 10.7.1 기존 티어 시스템 활용

**코드 근거**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
# 이미 존재하는 티어 시스템
def get_effective_limit(self, tier_id: str = "standard") -> int:
    """티어별 실효 limit 조회."""
    return self._apply_emergency_cap(self._current_limit, tier_id)
```

#### 10.7.2 429 상황 CRITICAL 보호 확장

```python
# adaptive.py 수정

# 429 상황에서 보호할 티어
PROTECTED_TIERS_ON_429: set[str] = {"critical"}


class AdaptiveThrottle(SlidingWindowThrottle):
    def __init__(self, ...):
        # ... 기존 코드 ...

        # 429 상황 CRITICAL 보호 (신규)
        self._429_reduction_active: bool = False
        self._limit_before_429: int = self.config.initial_limit

    def check(self, key: str, tier_id: str = "standard") -> ThrottleResult:
        """
        Request 허용 여부 확인 (우선순위 보호 적용).

        Args:
            key: 요청 식별자
            tier_id: 요청 티어 (critical/standard/non_essential)

        Returns:
            ThrottleResult
        """
        # 429 감소 상태에서 CRITICAL 티어 보호
        if self._429_reduction_active and tier_id in PROTECTED_TIERS_ON_429:
            # CRITICAL 요청은 429 감소 전 limit 기준으로 검사
            effective_limit = self._limit_before_429
            logger.debug(
                f"[AdaptiveThrottle] CRITICAL tier protected: "
                f"using pre-429 limit {effective_limit}"
            )
        else:
            effective_limit = self._current_limit

        # ... 나머지 기존 check 로직 ...

    def _handle_rate_limit_429(self, event_data: dict) -> None:
        """429 이벤트 수신 시 limit 조정 (CRITICAL 보호)."""
        # 429 감소 전 limit 저장 (CRITICAL 보호용)
        if not self._429_reduction_active:
            self._limit_before_429 = self._current_limit

        self._429_reduction_active = True

        # ... 기존 감소 로직 ...

    def _handle_cooldown_end(self, event_data: dict) -> None:
        """Cooldown 종료 시 CRITICAL 보호 해제."""
        self._429_reduction_active = False

        # ... 기존 Recovery Dampening 로직 ...
```

---

### 10.8 Meta-Watchdog 에스컬레이션

#### 10.8.1 기존 구현 활용

**코드 근거**: [meta/escalation.py](../../packages/selfhealing-python/src/selfhealing/meta/escalation.py)

```python
class EscalationManager:
    """
    Escalation Manager.
    자동 복구 실패 시 인간에게 에스컬레이션합니다.
    """

    def escalate(self, event: EscalationEvent) -> EscalationResult:
        # CRITICAL → PagerDuty
        if event.level == EscalationLevel.CRITICAL:
            if self._send_pagerduty(event):
                channels_sent.append("pagerduty")
```

#### 10.8.2 RateLimitEscalationHandler 구현

**네이밍 선택**: `RateLimitEscalationHandler`
- 기존 `EscalationManager` 패턴과 일관
- "Handler": EventBus 구독자 역할 명시

```python
# 파일: meta/rate_limit_escalation.py
"""
Rate Limit 에스컬레이션 핸들러.

연속 429 임계치 도달 시 PagerDuty 알림을 발송합니다.
"""
from __future__ import annotations

import logging

from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
    EscalationManager,
)

logger = logging.getLogger(__name__)

# 에스컬레이션 임계값
ESCALATION_THRESHOLD_CONSECUTIVE_429S = 10


class RateLimitEscalationHandler:
    """
    Rate Limit 429 에스컬레이션 핸들러.

    EventBus에서 RATE_LIMIT_429 이벤트를 구독하고,
    consecutive_429s가 임계치를 초과하면 PagerDuty로 에스컬레이션합니다.

    Usage:
        handler = RateLimitEscalationHandler()
        handler.subscribe()  # EventBus 구독 시작
    """

    def __init__(
        self,
        escalation_manager: EscalationManager | None = None,
        threshold: int = ESCALATION_THRESHOLD_CONSECUTIVE_429S,
    ):
        self._escalation_manager = escalation_manager or EscalationManager()
        self._threshold = threshold
        self._escalated_keys: set[str] = set()  # 중복 에스컬레이션 방지

    def subscribe(self) -> None:
        """EventBus에 RATE_LIMIT_429 이벤트 구독."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.RATE_LIMIT_429,
                self._handle_rate_limit_429,
            )
            logger.info(
                f"[RateLimitEscalationHandler] Subscribed "
                f"(threshold={self._threshold})"
            )
        except ImportError:
            logger.debug("[RateLimitEscalationHandler] EventBus not available")

    def _handle_rate_limit_429(self, event_data: dict) -> None:
        """429 이벤트 처리 - 임계치 초과 시 에스컬레이션."""
        key = event_data.get("key", "unknown")
        consecutive = event_data.get("consecutive_429s", 0)

        if consecutive < self._threshold:
            return

        # 이미 에스컬레이션 했으면 스킵
        if key in self._escalated_keys:
            return

        self._escalated_keys.add(key)

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title=f"Rate Limit Critical: {key}",
            description=(
                f"External API '{key}'에서 연속 {consecutive}회 429 응답 발생. "
                f"시스템이 방어 모드로 전환되었습니다. "
                f"외부 API 상태 확인 및 조치가 필요합니다."
            ),
            component="rate_limit_coordinator",
            details={
                "key": key,
                "consecutive_429s": consecutive,
                "threshold": self._threshold,
                "cooldown_until": event_data.get("cooldown_until"),
            },
        )

        result = self._escalation_manager.escalate(event)

        if result.success:
            logger.critical(
                f"[RateLimitEscalationHandler] ESCALATED: {key} "
                f"(consecutive={consecutive}, channels={result.channels_sent})"
            )
        else:
            logger.error(
                f"[RateLimitEscalationHandler] Escalation failed: "
                f"{result.error_message}"
            )

    def reset_escalation(self, key: str) -> None:
        """에스컬레이션 상태 초기화 (복구 후 호출)."""
        self._escalated_keys.discard(key)
```

#### 10.8.3 설정

```python
# settings.py 수정
class RateLimitThrottleIntegrationSettings(BaseModel):
    # ... 기존 설정 ...

    # 에스컬레이션 설정
    escalation_enabled: bool = True
    """연속 429 임계치 도달 시 에스컬레이션 활성화."""

    escalation_threshold_consecutive_429s: int = 10
    """에스컬레이션 발동 임계값 (연속 429 횟수)."""
```

---

### 10.9 결정론적 테스트 - freezegun 활용

#### 10.9.1 기존 패턴 확인

**코드 근거**: [test_time_based_behaviors.py](../../../tests/self_healing/integration/test_time_based_behaviors.py)

```python
from freezegun import freeze_time

@freeze_time("2025-01-01 12:00:00")
def test_circuit_breaker_remains_open_before_timeout(self):
    # ... 시간 기반 테스트 ...
    with freeze_time("2025-01-01 12:00:03"):
        # 3초 후 상태 확인
```

**프로젝트에서 이미 `freezegun` 사용 중!**

#### 10.9.2 Rate Limit 통합 테스트 (freezegun)

```python
# 파일: tests/unit/rate_limit/test_coordinator_throttle_integration.py
"""
RateLimitCoordinator-AdaptiveThrottle 통합 테스트.

freezegun을 사용하여 시간 기반 동작을 결정론적으로 테스트합니다.
"""
import pytest
from unittest.mock import MagicMock, patch
from freezegun import freeze_time

from selfhealing.services.rate_limit_coordinator import (
    RateLimitCoordinator,
    RateLimitCoordinatorConfig,
)
from selfhealing.services.throttle.adaptive import AdaptiveThrottle
from selfhealing.services.throttle.config import ThrottleConfig
from selfhealing.adapters.rate_limit.memory_adapter import InMemoryRateLimitStorage


class TestRateLimitThrottleIntegration:
    """429-Throttle 연동 결정론적 테스트."""

    @pytest.fixture
    def storage(self):
        """InMemory Storage for testing."""
        return InMemoryRateLimitStorage()

    @pytest.fixture
    def coordinator(self, storage):
        """테스트용 Coordinator."""
        config = RateLimitCoordinatorConfig(
            base_delay=5.0,
            max_delay=60.0,
            debounce_window_seconds=5.0,
        )
        return RateLimitCoordinator(storage=storage, config=config)

    @pytest.fixture
    def throttle(self):
        """테스트용 Throttle."""
        config = ThrottleConfig(initial_limit=100, min_limit=10)
        return AdaptiveThrottle(config=config)

    @freeze_time("2026-02-06 12:00:00")
    def test_429_reduces_throttle_limit_deterministic(
        self, coordinator, throttle
    ):
        """
        429 발생 시 throttle limit 감소 - 시간 고정 테스트.

        freezegun으로 time.sleep 없이 결정론적 테스트 가능.
        """
        initial_limit = throttle.current_limit
        assert initial_limit == 100

        # Mock EventBus 연동
        with patch("selfhealing.services.event_bus.get_event_bus") as mock_bus:
            mock_event_bus = MagicMock()
            mock_bus.return_value = mock_event_bus

            # 429 발생 시뮬레이션
            coordinator.on_rate_limited("test_api", retry_after=5)

            # EventBus emit 호출 확인
            mock_event_bus.emit.assert_called()
            call_args = mock_event_bus.emit.call_args

            # 이벤트 데이터 추출
            event_data = call_args.kwargs.get("data", {})
            assert event_data["key"] == "test_api"
            assert event_data["consecutive_429s"] == 1

        # 직접 핸들러 호출 (EventBus 우회)
        throttle._handle_rate_limit_429({
            "key": "test_api",
            "consecutive_429s": 1,
            "cooldown_until": 1738843205.0,  # 12:00:05
        })

        # limit 감소 확인 (20% 감소 → 80)
        assert throttle.current_limit == 80

    @freeze_time("2026-02-06 12:00:00")
    def test_consecutive_429_progressive_reduction_deterministic(
        self, coordinator, throttle
    ):
        """
        연속 429 시 점진적 감소 - freezegun 결정론적 테스트.

        1회: 100 → 80 (20% 감소)
        2회: 80 → 48 (40% 감소)
        3회: 48 → 24 (50% 감소)
        """
        limits = [throttle.current_limit]  # [100]

        for i in range(1, 4):
            throttle._handle_rate_limit_429({
                "key": "test_api",
                "consecutive_429s": i,
                "cooldown_until": 1738843200.0 + (i * 10),
            })
            limits.append(throttle.current_limit)

        # 점진적 감소 확인
        assert limits == [100, 80, 48, 24]

    @freeze_time("2026-02-06 12:00:00")
    def test_debounce_window_prevents_duplicate_events(self, coordinator):
        """
        디바운싱 윈도우 내 중복 이벤트 방지 테스트.
        """
        # 첫 번째 이벤트 - 발행됨
        assert coordinator._should_emit_event("test_api") is True

        # 즉시 두 번째 이벤트 - 디바운싱됨
        assert coordinator._should_emit_event("test_api") is False

        # 5초 후로 시간 이동
        with freeze_time("2026-02-06 12:00:06"):
            # 윈도우 지남 - 발행됨
            assert coordinator._should_emit_event("test_api") is True

    @freeze_time("2026-02-06 12:00:00")
    def test_cooldown_wait_deterministic(self, coordinator, storage):
        """
        Cooldown 대기 테스트 - freezegun으로 시간 점프.
        """
        # Cooldown 설정 (12:00:10까지)
        storage.set_cooldown("test_api", 1738843210.0)
        storage.increment_consecutive_429s("test_api")

        # 12:00:00 - Cooldown 중
        state = storage.get_state("test_api")
        assert state.is_in_cooldown is True
        assert state.remaining_cooldown == 10.0

        # 12:00:15로 시간 이동
        with freeze_time("2026-02-06 12:00:15"):
            state = storage.get_state("test_api")
            assert state.is_in_cooldown is False
            assert state.remaining_cooldown == 0.0

    @freeze_time("2026-02-06 12:00:00")
    def test_canary_request_after_cooldown(self, coordinator, storage):
        """
        Cooldown 종료 후 Canary 요청 테스트.
        """
        # 이전에 429 발생했던 상태 설정
        storage.increment_consecutive_429s("test_api")
        # Cooldown은 이미 종료됨 (cooldown_until=0)

        # 첫 요청 - Canary 모드
        result = coordinator.wait_if_needed("test_api")
        assert result.is_canary is True

        # 성공 후 Canary 상태 해제
        coordinator.on_success("test_api")

        # 다음 요청 - Canary 아님
        result2 = coordinator.wait_if_needed("test_api")
        assert result2.is_canary is False

    @freeze_time("2026-02-06 12:00:00")
    def test_recovery_dampening_with_frozen_time(self, throttle):
        """
        Recovery Dampening 테스트 - freezegun으로 시간 단계 이동.
        """
        # 429로 limit 감소된 상태
        throttle._base_limit_before_emergency = 100
        throttle.current_limit = 50

        # Recovery Dampening 시작
        throttle.start_recovery_dampening()
        assert throttle.current_limit == 80  # 80%

        # 30초 후
        with freeze_time("2026-02-06 12:00:30"):
            throttle.advance_recovery_dampening()
            assert throttle.current_limit == 90  # 90%

        # 60초 후
        with freeze_time("2026-02-06 12:01:00"):
            throttle.advance_recovery_dampening()
            assert throttle.current_limit == 100  # 100%


class TestConservativeLimitPolicy:
    """Conservative Limit (Min-Winner) 정책 테스트."""

    @freeze_time("2026-02-06 12:00:00")
    def test_min_winner_selects_lower_limit(self):
        """RTT와 429 limit 중 낮은 값 선택."""
        config = ThrottleConfig(initial_limit=100, min_limit=10)
        throttle = AdaptiveThrottle(config=config)

        # RTT 기반 limit: 80
        throttle._rtt_suggested_limit = 80
        # 429 기반 limit: 60
        throttle._429_suggested_limit = 60

        # Min-Winner: 60
        assert throttle.conservative_limit == 60
```

---

## 11. 구현 우선순위

| 순위 | 항목 | 긴급도 | 복잡도 | 근거 |
|:---:|------|:------:|:------:|------|
| 1 | 전역 EventBus (Kafka) | 🔴 높음 | 중 | Self-DDoS 방지 핵심 |
| 2 | 에스컬레이션 연동 | 🔴 높음 | 낮 | 운영 가시성 |
| 3 | 디바운싱 | 🟡 중간 | 낮 | 안정성 필수 |
| 4 | Conservative Limit | 🟡 중간 | 중 | 안전한 limit 결정 |
| 5 | Key-Service 매핑 | 🟡 중간 | 낮 | ThrottleRegistry 이미 존재 |
| 6 | COOLDOWN_END 이벤트 | 🟡 중간 | 낮 | Recovery 연동 |
| 7 | Priority-aware | 🟢 낮음 | 중 | 티어 시스템 확장 |
| 8 | Canary Request | 🟢 낮음 | 중 | CB Half-Open 패턴 |
| 9 | 결정론적 테스트 | 🟢 낮음 | 낮 | freezegun 이미 사용 중 |

---

## 12. 참조

- [RateLimitCoordinator 소스](../../packages/selfhealing-python/src/selfhealing/services/rate_limit_coordinator.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [ThrottleRegistry 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/registry.py)
- [KafkaEventBus 소스](../../packages/selfhealing-python/src/selfhealing/adapters/kafka/event_bus.py)
- [EscalationManager 소스](../../packages/selfhealing-python/src/selfhealing/meta/escalation.py)
- [freezegun 테스트 예시](../../../tests/self_healing/integration/test_time_based_behaviors.py)
- [152_ADAPTIVE_THROTTLE_EVENTBUS_INTEGRATION.md](152_ADAPTIVE_THROTTLE_EVENTBUS_INTEGRATION.md)
- [155_ADAPTIVE_THROTTLE_P0_MASTER_PLAN.md](155_ADAPTIVE_THROTTLE_P0_MASTER_PLAN.md)
