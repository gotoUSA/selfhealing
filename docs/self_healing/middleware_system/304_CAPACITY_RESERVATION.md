# 304. Capacity Reservation — 예정 이벤트 기반 사전 용량 확보

> **Status**: Design
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/capacity_reservation/event_calendar.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/capacity_reservation/pre_warmer.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/capacity_reservation/service.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/settings/capacity_reservation.py` — 신규
> - `packages/selfhealing-python/tests/unit/capacity_reservation/` — 신규
> **References**:
> - `scaling/rate_controller.py` — RateController (AIMD), TokenBucket
> - `core/pool_watchdog.py` — PoolWatchdog (expand/shrink)
> - `scaling/graceful_degradation.py` — GracefulDegradation (단계별 기능 축소)
> - `resilience/bulkhead/base.py` — Bulkhead (동시 실행 격리)
> - `scaling/hpa_exporter.py` — HPAMetricsExporter (K8s HPA 메트릭)
> - `services/predictive_forecaster/proactive_action.py` — SpikeClassifier, ProactiveActionTrigger
> - `services/event_bus/bus/__init__.py` — EventType, SelfHealingEventBus
> - `interfaces/ml_strategy.py` — ForecastStrategy, ClassificationStrategy Protocol

---

## 1. 목적

현재 시스템의 모든 용량 관리 메커니즘은 **reactive**하다.
RateController는 큐 크기 변화에 AIMD로 반응하고,
PoolWatchdog는 사용률 임계치 초과 시에만 확장한다.

**예정된 트래픽 급증**(쿠폰 오픈, 타임세일, 정기 이벤트)에 대해서는
사전 대비가 불가능하여, 이벤트 시작 직후 수초~수십초간 서비스 품질이 저하된다.

CapacityReservationService는:
- 예정 이벤트를 등록하고, 이벤트 N분 전에 **기존 모듈을 사전 조정**한다
- 신규 중복 로직을 만들지 않고, 기존 RateController/PoolWatchdog/Bulkhead/GracefulDegradation에 **조정 신호를 주입**하는 오케스트레이터이다
- ML(PredictiveForecaster, SpikeClassifier)과 **Decision Authority Conflict**를 방지하기 위해 EventBus를 통해 이벤트 컨텍스트를 공유한다
- 이벤트 종료 후 점진적 복원을 기존 모듈의 메커니즘에 위임한다

**핵심 시나리오**:
```
11:00 쿠폰 오픈 이벤트 등록
→ 10:55 PreWarmer 실행:
    1. RateController.min_rate_per_second 임시 상향 (AIMD floor 변경)
    2. PoolWatchdog.expand_pool() 사전 호출
    3. Bulkhead.max_concurrent 임시 확장
    4. GracefulDegradation.update_level(NONE) 고정 (자동 축소 억제)
    5. EventBus → SCHEDULED_EVENT_STARTED 발행
→ 11:00~11:30 이벤트 기간:
    - SpikeClassifier가 context.scheduled_event=True로 HEALTHY_SURGE 판정
    - ProactiveActionTrigger가 이벤트 시간대 선제적 조치 억제
    - AIMD가 min_rate floor 이하로 내려가지 않음
→ 11:30 이벤트 종료:
    1. 임시 설정 원복
    2. EventBus → SCHEDULED_EVENT_ENDED 발행
    3. PoolWatchdog._try_shrink()에 의한 자연 축소
    4. AIMD에 의한 자연 rate 조정
```

---

## 2. 기존 코드 근거

### 2.1 RateController — AIMD Rate 조절 (확장 포인트)

`scaling/rate_controller.py:424-467`:

```python
def _adjust_rate(self) -> None:
    """Rate 조절 (AIMD 패턴)."""
    queue_size = self._queue_size_provider()
    new_level = self._settings.get_level_for_queue_size(queue_size)
    # ...
    # 범위 제한
    new_rate = max(
        self._settings.min_rate_per_second,
        min(self._settings.max_rate_per_second, new_rate),
    )
```

**활용**: `min_rate_per_second`를 이벤트 기간 동안 임시로 상향하면,
AIMD의 Multiplicative Decrease가 발동해도 rate가 이벤트 예상 RPS 이하로 내려가지 않는다.
기존 AIMD 로직은 변경하지 않고, settings 값만 동적으로 조정한다.

### 2.2 PoolWatchdog — 사전 확장 가능한 인터페이스

`core/pool_watchdog.py:49-60`:

```python
class PoolRecoveryHandler(ABC):
    @abstractmethod
    def expand_pool(self, additional_connections: int) -> bool:
        """Temporarily expand pool size"""
        pass
```

**활용**: 이미 `expand_pool()` 추상 메서드가 존재한다.
PreWarmer가 이벤트 시작 전 이 메서드를 호출하여 Pool을 사전 확장한다.
`_try_shrink()` 로직은 이벤트 종료 후 자연스럽게 축소를 처리한다.

### 2.3 Bulkhead — 동적 permit 확장 가능

`resilience/bulkhead/base.py:36-71`:

```python
@dataclass
class BulkheadState:
    max_concurrent: int
    active_count: int
    waiting_count: int
    rejected_count: int

    @property
    def available_permits(self) -> int:
        return max(0, self.max_concurrent - self.active_count)
```

**활용**: `max_concurrent`를 이벤트 기간 동안 임시로 증가시켜
버스트 트래픽을 수용한다. 이벤트 종료 시 원래 값으로 복원한다.

### 2.4 GracefulDegradation — 이벤트 중 축소 억제

`scaling/graceful_degradation.py:25-36`:

```python
class FeaturePriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    OPTIONAL = 4
```

**활용**: 이벤트 기간 동안 `update_level(BackpressureLevel.NONE)`을 강제하여
비필수 기능이 자동 비활성화되지 않도록 한다.
예: 이벤트 중 알림/통계 기능이 꺼지면 운영 가시성이 감소하므로 억제한다.

### 2.5 SpikeClassifier — ML과의 Decision Authority Conflict

`services/predictive_forecaster/proactive_action.py:114-159`:

```python
class SpikeClassifier:
    def classify(self, rps_history, error_rate_history, latency_history) -> SpikeType:
        # error_rate 급등 → ANOMALOUS_SPIKE
        # RPS 가속도 높음 + error 정상 → HEALTHY_SURGE
        # 나머지 → GRADUAL_DEGRADATION
```

**문제**: SpikeClassifier는 현재 error_rate + RPS + latency 3개 시그널만 사용한다.
예정 이벤트 트래픽을 DDoS(ANOMALOUS_SPIKE)로 오분류할 수 있다.

**해결**: `classify_features(features, context)` 메서드(line 163)에
`context={"scheduled_event": True}`를 전달하면 된다.
이미 `context: dict[str, Any] | None` 파라미터가 존재하므로
SpikeClassifier 내부에 이벤트 컨텍스트 참조 로직만 추가하면 된다.

### 2.6 ProactiveActionTrigger — 이벤트 기간 선제적 조치 억제

`services/predictive_forecaster/proactive_action.py:262-266`:

```python
ADJUSTMENT_INTENSITY: dict[SpikeType, float] = {
    SpikeType.HEALTHY_SURGE: 0.0,        # 조치 없음
    SpikeType.ANOMALOUS_SPIKE: 0.15,     # 15% 방어
    SpikeType.GRADUAL_DEGRADATION: 0.05, # 5% 조정
}
```

**활용**: SpikeClassifier가 이벤트 기간에 HEALTHY_SURGE를 올바르게 반환하면,
ProactiveActionTrigger는 자동으로 조치 강도 0.0 (조치 없음)을 적용한다.
별도 억제 로직이 불필요하다.

### 2.7 ML Strategy Protocol — context 파라미터

`interfaces/ml_strategy.py:59-76`:

```python
@runtime_checkable
class AnomalyDetectionStrategy(Protocol):
    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        ...
```

**활용**: ML Strategy Protocol의 모든 `detect()`, `classify()` 메서드에
`context` 파라미터가 이미 존재한다. 이벤트 메타데이터를
context에 주입하면 ML 모델이 이벤트 기간을 인지할 수 있다.

### 2.8 EventBus — 이벤트 발행/구독 패턴

`services/event_bus/bus/__init__.py:59-114`:

```python
class EventType(str, Enum):
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    ERROR_BUDGET_CRITICAL = "error_budget_critical"
    THROTTLE_LIMIT_CHANGED = "throttle_limit_changed"
    # ...
```

**활용**: `SCHEDULED_EVENT_STARTED`, `SCHEDULED_EVENT_ENDED` 이벤트를 추가하여,
기존 모듈들이 이벤트 기간을 구독할 수 있게 한다.
ThrottleLimitAdjuster가 이미 EventBus 이벤트를 구독하여 limit을 조정하는 패턴이 있으므로
동일한 패턴을 따른다.

---

## 3. 설계

### 3.1 파일 구조

```
services/capacity_reservation/
    __init__.py
    event_calendar.py       # 예정 이벤트 등록/관리/스케줄링
    pre_warmer.py           # 이벤트 전 기존 모듈 사전 조정 오케스트레이터
    service.py              # CapacityReservationService (싱글톤)

settings/
    capacity_reservation.py # Pydantic Settings (SELFHEALING_CAPACITY_RESERVATION_*)
```

**설계 결정: 3파일 구조 (6파일이 아닌 이유)**

| 원안 파일 | 결정 | 근거 |
|-----------|------|------|
| `event_calendar.py` | **신규** | 시간 기반 예약은 현재 코드베이스에 완전 부재 |
| `pre_warmer.py` | **신규** | 기존 모듈 조정을 오케스트레이션하는 코디네이터 |
| `service.py` | **신규** | 라이프사이클 관리 + 싱글톤 |
| ~~`burst_buffer.py`~~ | **불필요** | `RateController.TokenBucket` + `BackpressureStrategy.QUEUE`가 이미 커버 |
| ~~`elastic_scaler.py`~~ | **불필요** | `PoolWatchdog.expand_pool()` + `HPAMetricsExporter`가 이미 커버 |
| ~~`cooldown_manager.py`~~ | **불필요** | `PoolWatchdog._try_shrink()` + AIMD additive increase가 이미 커버 |

### 3.2 EventCalendar

```python
class ScheduledEvent:
    """예정 이벤트 정의."""
    event_id: str                    # 고유 ID (UUID)
    name: str                        # "11시 쿠폰 오픈"
    start_time: datetime             # 이벤트 시작 시각 (UTC)
    end_time: datetime               # 이벤트 종료 시각 (UTC)
    warmup_minutes: int              # 사전 워밍 시작 시간 (분)
    expected_rps_multiplier: float   # 평소 대비 RPS 배율 (예: 3.0)
    pool_multiplier: float           # Pool 확장 배율 (예: 2.0)
    bulkhead_extra_permits: int      # Bulkhead 추가 permit 수
    suppress_degradation: bool       # GracefulDegradation 축소 억제 여부
    tags: list[str]                  # 메타데이터 태그 (예: ["coupon", "flash_sale"])

class EventCalendar:
    """예정 이벤트 캘린더 — 등록/조회/스케줄링."""

    def register(self, event: ScheduledEvent) -> None:
        """이벤트 등록. 시작 시간이 과거이면 ValueError."""

    def cancel(self, event_id: str) -> bool:
        """이벤트 취소. 이미 워밍 시작된 경우 rollback 포함."""

    def get_upcoming(self, within_minutes: int = 60) -> list[ScheduledEvent]:
        """N분 이내 예정 이벤트 조회."""

    def get_active(self) -> list[ScheduledEvent]:
        """현재 진행 중인 이벤트 조회."""

    def is_event_period(self) -> bool:
        """현재 시각이 이벤트 기간인지 여부. ML context 주입에 사용."""
```

**데이터 저장**: 인메모리 dict + 선택적 Redis 영속화 (ProviderRegistry 패턴).
이벤트 수가 많지 않으므로 (일 수십 건 이하) 인메모리가 기본이다.

### 3.3 PreWarmer

```python
class PreWarmer:
    """이벤트 전 기존 모듈에 사전 조정 신호를 보내는 오케스트레이터."""

    def warm_up(self, event: ScheduledEvent) -> WarmUpResult:
        """
        이벤트 시작 N분 전에 호출.
        기존 모듈의 설정을 임시로 조정한다.
        """
        # 1. RateController: min_rate 상향 (AIMD floor)
        # 2. PoolWatchdog: expand_pool() 호출
        # 3. Bulkhead: max_concurrent 확장
        # 4. GracefulDegradation: 축소 억제
        # 5. EventBus: SCHEDULED_EVENT_STARTED 발행

    def cool_down(self, event: ScheduledEvent) -> CoolDownResult:
        """
        이벤트 종료 시 호출.
        임시 설정을 원래 값으로 복원한다.
        축소는 기존 모듈의 자연 메커니즘에 위임한다.
        """
        # 1. RateController: min_rate 원복
        # 2. Bulkhead: max_concurrent 원복
        # 3. GracefulDegradation: 억제 해제
        # 4. EventBus: SCHEDULED_EVENT_ENDED 발행
        # 5. Pool 축소는 PoolWatchdog._try_shrink()에 위임 (즉시 축소하지 않음)
```

**설계 원칙**:
- PreWarmer는 **새 로직을 구현하지 않는다**. 기존 모듈의 public API만 호출한다.
- 각 조정마다 원래 값을 `_original_settings` dict에 저장하여 rollback을 보장한다.
- 조정 실패 시 이미 적용된 조정을 부분 rollback한다 (트랜잭션 시맨틱).

### 3.4 CapacityReservationService

```python
class CapacityReservationService:
    """Capacity Reservation 서비스 — 싱글톤."""

    _instance: CapacityReservationService | None = None
    _lock = Lock()

    def __new__(cls) -> CapacityReservationService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def initialize(self, config=None):
        """초기화. EventCalendar + PreWarmer 생성, 스케줄러 시작."""

    def register_event(self, event: ScheduledEvent) -> None:
        """이벤트 등록 + 워밍 스케줄 등록."""

    def cancel_event(self, event_id: str) -> bool:
        """이벤트 취소."""

    def get_status(self) -> dict:
        """현재 상태 조회 (예정/진행중/완료 이벤트, 적용된 조정 목록)."""

    def start(self) -> None:
        """스케줄러 시작 (백그라운드 스레드)."""

    def stop(self) -> None:
        """스케줄러 중지 + 진행 중 이벤트 cooldown."""
```

**스케줄러**: 백그라운드 스레드가 매 30초마다 `event_calendar.get_upcoming()`을 확인.
워밍 시간에 도달한 이벤트가 있으면 `pre_warmer.warm_up()`을 실행한다.
종료 시간에 도달하면 `pre_warmer.cool_down()`을 실행한다.

---

## 4. ML 연동 — Decision Authority Conflict 방지

### 4.1 문제

Capacity Reservation과 ML(PredictiveForecaster)이 동시에 같은 파라미터를
조정하려 할 때 **진동(oscillation)**이 발생할 수 있다:

| 시간 | Capacity Reservation | ML |
|------|---------------------|-----|
| 10:55 | Rate 3x 완화 | — |
| 11:01 | Rate 유지 (이벤트 모드) | RPS 급증 → ANOMALOUS_SPIKE → throttle 강화 |
| 11:02 | Rate 유지 | error 미세 증가 → 추가 throttle 강화 |

### 4.2 해결: EventBus 기반 컨텍스트 공유

```
EventCalendar
    │
    ├──→ EventBus.publish(SCHEDULED_EVENT_STARTED, {
    │       event_id, start_time, end_time, expected_rps_multiplier, tags
    │    })
    │
    │    구독자:
    │    ├── SpikeClassifier → context.scheduled_event = True
    │    │   → error_rate 정상이면 무조건 HEALTHY_SURGE 반환
    │    │
    │    ├── ProactiveActionTrigger → HEALTHY_SURGE → intensity 0.0 (조치 없음)
    │    │
    │    ├── PoolWatchdog → shrink 억제 (이벤트 종료까지)
    │    │
    │    └── ThrottleLimitAdjuster → 이벤트 기간 limit 하향 억제
    │
    └──→ EventBus.publish(SCHEDULED_EVENT_ENDED, { event_id })
         → 모든 구독자가 정상 모드로 복귀
```

**구현 요구사항**:

1. **EventType 추가** (`services/event_bus/bus/__init__.py`):
```python
# Capacity Reservation Events
SCHEDULED_EVENT_STARTED = "scheduled_event_started"
SCHEDULED_EVENT_ENDED = "scheduled_event_ended"
```

2. **SpikeClassifier.classify() 변경** — context 참조 추가:
```python
def classify(self, rps_history, error_rate_history, latency_history,
             context: dict[str, Any] | None = None) -> SpikeType:
    # 예정 이벤트 기간이면 error_rate가 정상인 한 HEALTHY_SURGE
    if context and context.get("scheduled_event"):
        error_delta = error_rate_history[-1] - error_rate_history[-5]
        if error_delta <= self._error_rate_threshold / self._sensitivity_multiplier:
            return SpikeType.HEALTHY_SURGE
    # ... 기존 로직
```

3. **LearningService 연동** — 이벤트 기간 데이터 마킹:
```python
# 이벤트 기간 중 수집된 데이터에 has_adjustment=True 마킹
# ForecastDataPoint.has_adjustment (Self-Fulfilling Prophecy 방지)와 동일 메커니즘
```

---

## 5. Settings

```python
# settings/capacity_reservation.py

class CapacityReservationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CAPACITY_RESERVATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    enabled: bool = Field(
        default=False,
        description="Capacity Reservation 서비스 활성화 여부",
    )

    default_warmup_minutes: int = Field(
        default=5,
        ge=1, le=60,
        description="기본 사전 워밍 시간 (분)",
    )

    scheduler_interval_seconds: int = Field(
        default=30,
        ge=5, le=300,
        description="스케줄러 확인 주기 (초)",
    )

    max_rate_multiplier: float = Field(
        default=5.0,
        ge=1.0, le=20.0,
        description="Rate 확장 최대 배율",
    )

    max_pool_multiplier: float = Field(
        default=3.0,
        ge=1.0, le=10.0,
        description="Pool 확장 최대 배율",
    )

    max_bulkhead_extra_permits: int = Field(
        default=100,
        ge=0, le=1000,
        description="Bulkhead 추가 permit 최대값",
    )

    cooldown_grace_period_seconds: int = Field(
        default=300,
        ge=60, le=3600,
        description="이벤트 종료 후 설정 복원까지 유예 시간 (초)",
    )

    dry_run: bool = Field(
        default=True,
        description="True이면 로그만 기록, 실제 조정 미수행",
    )
```

---

## 6. Prometheus 메트릭

```python
# PreWarmer 메트릭
selfhealing_capacity_warmup_total           # Counter: 워밍 실행 횟수 (labels: event_id, outcome)
selfhealing_capacity_warmup_duration_seconds # Histogram: 워밍 소요 시간
selfhealing_capacity_cooldown_total         # Counter: 쿨다운 실행 횟수
selfhealing_capacity_active_events          # Gauge: 현재 진행 중 이벤트 수
selfhealing_capacity_rate_multiplier        # Gauge: 현재 적용 중인 Rate 배율
selfhealing_capacity_pool_multiplier        # Gauge: 현재 적용 중인 Pool 배율
```

---

## 7. 기존 모듈 변경 범위 (최소 침습)

| 모듈 | 변경 유형 | 내용 |
|------|----------|------|
| `EventType` (event_bus) | enum 추가 | `SCHEDULED_EVENT_STARTED`, `SCHEDULED_EVENT_ENDED` 2개 추가 |
| `SpikeClassifier` | 시그니처 확장 | `classify()`에 `context` 파라미터 추가 (기본값 None, 하위 호환) |
| `RateController` | **변경 없음** | `_settings.min_rate_per_second` 동적 변경으로 충분 |
| `PoolWatchdog` | **변경 없음** | 기존 `expand_pool()` API 사용 |
| `Bulkhead` | **변경 없음** | `max_concurrent` 직접 조정 |
| `GracefulDegradation` | **변경 없음** | 기존 `update_level()` API 사용 |
| `HPAMetricsExporter` | **변경 없음** | 기존 메트릭 export 활용 |

**총 기존 코드 변경**: EventType enum 2줄 + SpikeClassifier 파라미터 1개.
나머지는 기존 public API 호출만으로 구현한다.

---

## 8. 테스트 전략

### 8.1 단위 테스트 (`packages/selfhealing-python/tests/unit/capacity_reservation/`)

| 테스트 파일 | 대상 | 핵심 케이스 |
|------------|------|------------|
| `test_event_calendar.py` | EventCalendar | 등록/취소/조회, 과거 시간 거부, 중복 ID 거부, 겹치는 이벤트 경고 |
| `test_pre_warmer.py` | PreWarmer | warm_up/cool_down 정상 동작, 부분 실패 시 rollback, 원래 값 복원 검증 |
| `test_service.py` | CapacityReservationService | 싱글톤, 스케줄러 동작, dry-run 모드 |
| `test_ml_integration.py` | ML 연동 | SCHEDULED_EVENT_STARTED 발행 시 SpikeClassifier가 HEALTHY_SURGE 반환 확인 |

### 8.2 핵심 테스트 시나리오

1. **정상 플로우**: 이벤트 등록 → 워밍 시간 도달 → warm_up → 이벤트 종료 → cool_down → 설정 원복 검증
2. **ML 충돌 방지**: 이벤트 기간 중 RPS 급증 시 SpikeClassifier가 ANOMALOUS_SPIKE가 아닌 HEALTHY_SURGE 반환
3. **이벤트 취소**: 워밍 진행 중 취소 → rollback 완료 검증
4. **dry-run 모드**: 로그만 기록되고 실제 설정은 변경되지 않음 검증
5. **설정 복원 보장**: 프로세스 비정상 종료 후 재시작 시 임시 설정이 원복되는지 검증

---

## 9. 위험 요소 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| PreWarmer 실행 중 프로세스 종료 | 임시 설정이 영구화 | 시작 시 `_original_settings` Redis 복원 체크, 없으면 기본값 사용 |
| 이벤트 시간 오등록 (3시간 빠름 등) | 불필요한 리소스 확장 | `max_rate_multiplier`, `max_pool_multiplier` 상한 설정으로 피해 제한 |
| 다수 이벤트 동시 진행 | 리소스 과다 확장 | 동시 진행 이벤트 수 상한(기본 3개) + 배율 합산 상한 |
| PoolWatchdog shrink가 warm_up 직후 발동 | 사전 확장 무효화 | SCHEDULED_EVENT_STARTED 이벤트 구독 시 shrink 억제 |
| ML 학습 데이터 오염 | 이벤트 기간 데이터로 모델 편향 | `ForecastDataPoint.has_adjustment=True` 마킹 |

---

## 10. 구현 순서

1. **Phase 1**: `settings/capacity_reservation.py` + `event_calendar.py` + 단위 테스트
2. **Phase 2**: `pre_warmer.py` + 기존 모듈 연동 + 단위 테스트
3. **Phase 3**: `service.py` (스케줄러, 싱글톤) + EventBus 이벤트 추가 + 통합 테스트
4. **Phase 4**: SpikeClassifier context 확장 + ML 연동 테스트
