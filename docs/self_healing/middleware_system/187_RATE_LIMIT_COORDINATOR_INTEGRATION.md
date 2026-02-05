# 187. Rate Limit Coordinator - AdaptiveThrottle 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/rate_limit_coordinator.py`, `selfhealing/services/throttle/adaptive.py`

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

## 10. 참조

- [RateLimitCoordinator 소스](../../packages/selfhealing-python/src/selfhealing/services/rate_limit_coordinator.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [152_ADAPTIVE_THROTTLE_EVENTBUS_INTEGRATION.md](152_ADAPTIVE_THROTTLE_EVENTBUS_INTEGRATION.md)
- [155_ADAPTIVE_THROTTLE_P0_MASTER_PLAN.md](155_ADAPTIVE_THROTTLE_P0_MASTER_PLAN.md)
