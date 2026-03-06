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

**shrink 억제 — Callback Guard 패턴**:

PoolWatchdog은 이미 `alert_callback: Callable | None`을 생성자에서 받는 패턴을 사용한다
(`core/pool_watchdog.py:87`). 동일한 패턴으로 `shrink_guard`를 추가하여,
이벤트 기간 중 `_try_shrink()`를 억제한다.

시그니처는 `RecoveryGate.check_recovery_allowed() -> tuple[bool, str]`
(`services/emergency_mode/recovery_gate.py:53-89`)의 reason 패턴을 따르되,
bool과 reason이 분리되어 모순 상태가 가능한 tuple 대신
`Optional[str]` (None=허용, str=억제 사유)로 단순화한다:

```python
# core/pool_watchdog.py — 변경

def __init__(
    self,
    monitor: ConnectionPoolMonitor,
    recovery_handler: PoolRecoveryHandler | None = None,
    alert_callback: Callable[[str, PoolHealthStatus], None] | None = None,
    auto_close_leaked: bool = True,
    auto_expand: bool = False,
    max_expansion: int = 10,
    shrink_guard: Callable[[], str | None] | None = None,
):
    """
    Args:
        shrink_guard: Optional guard for shrink suppression.
            Returns None to allow shrink, or a reason string to suppress.
            Contract: MUST be non-blocking (O(1), in-memory only, no I/O).
            Invoked on every check_and_recover() cycle.
    """
    # ... 기존 코드
    self._shrink_guard = shrink_guard

def _try_shrink(self, stats: PoolStats) -> PoolRecoveryResult:
    """Try to shrink pool back to normal if healthy"""
    if self._shrink_guard:
        suppress_reason = self._shrink_guard()
        if suppress_reason:
            return PoolRecoveryResult(
                action=PoolRecoveryAction.NONE,
                success=True,
                message=f"Shrink suppressed: {suppress_reason}",
                timestamp=datetime.now(timezone.utc),
            )
    if stats.usage_percent < 50 and self._recovery_handler:
        # ... 기존 shrink 로직 그대로
```

```python
# service.py — 호출 측에서 guard 주입

def _shrink_guard() -> str | None:
    """Non-blocking guard: 인메모리만 조회. I/O 금지."""
    if calendar.is_event_period():
        return "ScheduledEvent"
    if emergency_manager.is_active():
        return "EmergencyMode"
    return None

watchdog = PoolWatchdog(
    monitor=monitor,
    recovery_handler=handler,
    shrink_guard=_shrink_guard,
)
```

**설계 근거**:

| 기준 | 평가 | 이유 |
|------|------|------|
| 성능 | O(1) | guard 내부는 인메모리만 조회. Non-blocking 계약 필수 |
| 결합도 | 제로 | PoolWatchdog은 이벤트/캘린더/EventBus를 전혀 모름 |
| 패턴 정합 | `alert_callback`과 동일 | PoolWatchdog 자체 패턴의 자연 확장 |
| 확장성 | guard 내부에서 조건 자유 합성 | 이벤트 + 긴급모드 + 카나리 등 조합 가능 |
| Observability | `Optional[str]` reason | `PoolRecoveryResult.message`에 억제 사유 전파 |

**Non-blocking 계약**: guard 콜백 내부는 반드시 인메모리(O(1))만 조회해야 한다.
Redis/DB I/O는 백그라운드 스레드(EventBus 구독 등)에서 로컬 변수로 동기화하고,
guard는 그 로컬 변수만 읽는다. `EventCalendar.is_event_period()`는 이미 이 계약을 준수한다
(`event_calendar.py:163-174` — `self._events` dict 순회만 수행).

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

**Safety Valve — Degradation 억제의 조건부 해제**:

이벤트 기간이라도 예측 실패(예상 3배 → 실제 10배)나 DDoS 공격이 겹칠 경우,
Degradation 억제를 무조건 유지하면 시스템이 과부하로 죽을 수 있다.
기존 `RecoveryGate` (`services/emergency_mode/recovery_gate.py:53-89`)와 동일한 패턴으로
**하드 리밋 초과 시 즉시 방어 모드로 강제 전환**하는 Safety Valve를 적용한다.

```
상태 전이:

SCHEDULED_MODE ──[Safety Valve 발동]──→ SAFETY_OVERRIDE (CRITICAL)
  (NONE 고정)    cpu > threshold OR        (즉시 방어 모드)
                 error_rate > threshold          │
                                    ┌────────────┘
                                    │ min_hold_seconds 경과
                                    │ + RecoveryGate.check_recovery_allowed() == True
                                    ▼
                              SCHEDULED_MODE 복귀
                              (실패 시 SAFETY_OVERRIDE 유지)
```

Safety Valve 임계치는 Settings로 외부화하여 런타임 조정이 가능하도록 한다:

```python
# settings/capacity_reservation.py에 추가
safety_valve_cpu_threshold: float = Field(
    default=0.95, ge=0.5, le=1.0,
    description="Safety Valve 발동 CPU 임계치",
)
safety_valve_error_rate_threshold: float = Field(
    default=0.10, ge=0.01, le=1.0,
    description="Safety Valve 발동 Error Rate 임계치",
)
safety_valve_min_hold_seconds: int = Field(
    default=120, ge=30, le=600,
    description="Safety Valve 발동 후 최소 유지 시간 (Flapping 방지)",
)
```

PreWarmer의 스케줄러 루프에서 Safety Valve를 주기적으로 체크한다:

```python
class PreWarmer:
    def check_safety_valve(self) -> bool:
        """하드 리밋 초과 시 True 반환. 스케줄러가 매 주기 호출."""
        cpu = self._metrics_provider.get_cpu_usage()
        error_rate = self._metrics_provider.get_error_rate()
        return (
            cpu > self._settings.safety_valve_cpu_threshold
            or error_rate > self._settings.safety_valve_error_rate_threshold
        )

    def emergency_override(self) -> None:
        """Safety Valve 발동 — 이벤트 모드를 즉시 해제하고 CRITICAL 전환."""
        self._degradation.update_level(BackpressureLevel.CRITICAL)
        self._safety_valve_activated_at = time.monotonic()

    def check_safety_valve_recovery(self) -> bool:
        """min_hold_seconds 경과 + RecoveryGate 통과 시 이벤트 모드 복귀."""
        elapsed = time.monotonic() - self._safety_valve_activated_at
        if elapsed < self._settings.safety_valve_min_hold_seconds:
            return False
        allowed, _ = self._recovery_gate.check_recovery_allowed()
        return allowed
```

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

    def get_effective_multipliers(self) -> EffectiveMultipliers:
        """활성 이벤트들의 MAX 배율 계산 (겹침 병합)."""
```

**이벤트 겹침(Overlapping) 병합 전략 — MAX**:

시간대가 겹치는 이벤트가 동시에 진행될 경우, **가장 큰 값(MAX)**을 채택한다.
AWS Auto Scaling, K8s HPA 등 선언적 용량 관리 시스템의 표준 접근과 동일하다.

| 병합 전략 | 예시 (A=2x, B=3x) | 채택 여부 | 이유 |
|-----------|-------------------|-----------|------|
| **MAX** | **3x** | **✅ 채택** | 가장 보수적이면서 안전 |
| SUM | 5x | ❌ | 과도한 확장 위험 |
| 곱연산 | 6x | ❌ | 지수적 폭발 위험 |

MAX 결과에 Settings의 `max_rate_multiplier`, `max_pool_multiplier` 상한을 추가 적용한다:

```python
@dataclass
class EffectiveMultipliers:
    rate_multiplier: float
    pool_multiplier: float
    bulkhead_extra_permits: int
    suppress_degradation: bool
    source_event_ids: list[str]

class EventCalendar:
    def get_effective_multipliers(self) -> EffectiveMultipliers:
        """활성 이벤트 중 MAX 배율 계산 + Settings cap 적용."""
        active = self.get_active()
        if not active:
            return EffectiveMultipliers(1.0, 1.0, 0, False, [])

        return EffectiveMultipliers(
            rate_multiplier=min(
                max(e.expected_rps_multiplier for e in active),
                self._settings.max_rate_multiplier,
            ),
            pool_multiplier=min(
                max(e.pool_multiplier for e in active),
                self._settings.max_pool_multiplier,
            ),
            bulkhead_extra_permits=min(
                max(e.bulkhead_extra_permits for e in active),
                self._settings.max_bulkhead_extra_permits,
            ),
            suppress_degradation=any(e.suppress_degradation for e in active),
            source_event_ids=[e.event_id for e in active],
        )
```

**데이터 저장**: 인메모리 dict + StateBackend 영속화 (Pull + Push 하이브리드).
이벤트 수가 많지 않으므로 (일 수십 건 이하) 인메모리가 런타임 캐시이고,
StateBackend(Redis/File)가 SSOT(단일 진실 공급원)이다.

**다중 Pod 동기화 — Late Joiner 문제 해결**:

HPA에 의해 이벤트 도중 신규 Pod가 스케일 아웃되면, 해당 Pod는 과거의
`SCHEDULED_EVENT_STARTED` EventBus 메시지를 수신하지 못한다.
평시 용량으로 서비스하다가 트래픽을 맞고 Cascading Failure를 유발할 수 있다.

이를 방지하기 위해 `GracefulDegradationManager`의 Pull + Push 패턴을 따른다
(`services/emergency_mode/manager.py:94-143` 참조):

```
┌─────────────────────────────────────────────────────────┐
│  Pull (Initialize)                                      │
│  - Pod 기동 시 StateBackend에서 활성 이벤트 목록 로드    │
│  - 활성 이벤트가 있으면 즉시 warm_up 상태로 시작         │
│                                                         │
│  Push (Runtime)                                         │
│  - 리더 Pod가 스케줄러를 실행하여 EventBus로 브로드캐스트 │
│  - 다른 Pod는 EventBus 구독으로 실시간 동기화            │
│                                                         │
│  Check-on-Use (Drift Detection)                         │
│  - TTL 기반 캐시 유효성 검증                             │
│  - 인메모리 vs StateBackend 상태 비교로 drift 감지       │
└─────────────────────────────────────────────────────────┘
```

```python
class EventCalendar:
    def __init__(self, state_backend: StateBackend | None = None):
        self._events: dict[str, ScheduledEvent] = {}
        self._state_backend = state_backend
        self._cache_ttl_seconds: int = 30
        self._last_load_time: float | None = None

    def initialize(self) -> None:
        """Pod 기동 시 StateBackend에서 활성 이벤트 로드 (Pull)."""
        if self._state_backend:
            saved = self._state_backend.get("capacity_reservation:events")
            if saved:
                self._events = self._deserialize(saved)
                self._last_load_time = time.monotonic()

    def register(self, event: ScheduledEvent) -> None:
        """이벤트 등록 후 StateBackend에 영속화."""
        # ... 기존 로직
        if self._state_backend:
            self._state_backend.set(
                "capacity_reservation:events",
                self._serialize(self._events),
            )
```

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
        **이벤트별 원복이 아닌, 선언적 재계산(Re-evaluation)을 수행한다.**
        K8s Controller의 Reconciliation Loop 패턴과 동일하다.
        """
        # 1. 종료된 이벤트를 활성 목록에서 제거
        # 2. 남은 활성 이벤트가 있는가?
        #    YES → get_effective_multipliers()로 MAX 재계산 후 적용
        #    NO  → Global Baseline으로 완전 복원
        # 3. EventBus: SCHEDULED_EVENT_ENDED 발행
        # 4. Pool 축소는 PoolWatchdog._try_shrink()에 위임 (즉시 축소하지 않음)

    def _reconcile_settings(self) -> None:
        """
        활성 이벤트 기반 설정 재계산 (Reconciliation Loop).
        이벤트 추가/종료 시마다 현재 상태를 '선언적'으로 재계산한다.
        이벤트별 원복이 아닌 전체 재계산이므로, 겹침 타이밍 이슈가 원천 차단된다.
        """
        # effective = self._calendar.get_effective_multipliers()
        # self._apply_if_changed(effective)
```

**설계 원칙**:
- PreWarmer는 **새 로직을 구현하지 않는다**. 기존 모듈의 public API만 호출한다.
- `cool_down()`은 이벤트별 원복이 아닌 **선언적 재계산(Re-evaluation)**을 수행한다.
  이벤트가 종료될 때마다 남은 활성 이벤트들의 MAX를 재계산하여 적용하므로,
  겹치는 이벤트 A가 먼저 끝나도 이벤트 B의 설정이 유지된다.
- Global Baseline은 이벤트별이 아닌 **단일 스냅샷**으로 관리한다 (3.5절 참조).
- 조정 실패 시 이미 적용된 조정을 부분 rollback한다 (트랜잭션 시맨틱).

### 3.4 Global Baseline — 상태 영속화 및 복원

PreWarmer가 기존 모듈의 설정을 변경하기 전, **평시 원본값을 단일 스냅샷(Global Baseline)으로
StateBackend에 영속화**한다. `GracefulDegradationManager`의 Before Mutation Snapshot 패턴
(`services/emergency_mode/manager.py:286-307`)을 따른다.

**핵심 원칙**: 이벤트별(`event_id`별)이 아닌, **글로벌 단일 베이스라인**으로 관리한다.

```
이벤트 A 시작 (최초)  →  현재 설정 캡처 → Redis 저장 (global_baseline)
이벤트 B 시작 (겹침)  →  베이스라인 이미 존재 → 저장 건너뜀 (덮어쓰지 않음)
이벤트 A 종료          →  활성 이벤트 남아있음 → Re-evaluation만 수행
이벤트 B 종료 (마지막) →  활성 이벤트 0개 → global_baseline 복원 + Redis 삭제
```

```python
class PreWarmer:
    _global_baseline: dict[str, Any] | None = None

    def warm_up(self, event: ScheduledEvent) -> WarmUpResult:
        # 최초 이벤트일 때만 베이스라인 캡처
        if self._global_baseline is None:
            self._global_baseline = self._capture_current_settings()
            if self._state_backend:
                self._state_backend.set(
                    "capacity_reservation:global_baseline",
                    self._global_baseline,
                    ttl=self._calculate_max_event_horizon() + 3600,
                )
        # Re-evaluation: 활성 이벤트 MAX 기반 설정 적용
        self._reconcile_settings()

    def cool_down(self, event: ScheduledEvent) -> CoolDownResult:
        remaining = self._calendar.get_active()
        if not remaining:
            # 모든 이벤트 종료 → 베이스라인 복원
            self._restore_from_baseline()
            self._global_baseline = None
            if self._state_backend:
                self._state_backend.delete("capacity_reservation:global_baseline")
        else:
            # 아직 활성 이벤트 있음 → MAX 재계산만
            self._reconcile_settings()
```

**프로세스 재시작 시 초기화 훅**:

```python
class PreWarmer:
    def initialize(self) -> None:
        """시스템 기동 시 고아 베이스라인 검출 및 복원."""
        if not self._state_backend:
            return
        saved_baseline = self._state_backend.get(
            "capacity_reservation:global_baseline"
        )
        active_events = self._calendar.get_active()

        if saved_baseline and not active_events:
            # 고아 베이스라인: 활성 이벤트 없는데 원본값이 남아있음
            # → 이전 프로세스가 cool_down 전에 종료된 것
            self._restore_from(saved_baseline)
            self._state_backend.delete("capacity_reservation:global_baseline")
            logger.warning("capacity_reservation.orphan_baseline_restored")
        elif saved_baseline and active_events:
            # 이벤트 진행 중 재시작 → 베이스라인 유지 + Re-evaluation
            self._global_baseline = saved_baseline
            self._reconcile_settings()
```

### 3.5 CapacityReservationService

```python
class CapacityReservationService:
    """Capacity Reservation 서비스 — 싱글톤."""

    _instance: CapacityReservationService | None = None
    _singleton_lock = Lock()

    def __new__(cls) -> CapacityReservationService:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def initialize(
        self,
        rate_controller=None,
        pool_watchdog=None,
        bulkhead=None,
        graceful_degradation=None,
        event_bus=None,
        metrics_provider=None,
        recovery_gate=None,
        state_backend=None,
        settings=None,
    ):
        """초기화. EventCalendar + PreWarmer 생성, StateBackend 기반 복원."""

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

    max_concurrent_events: int = Field(
        default=3,
        ge=1, le=10,
        description="동시 진행 가능한 최대 이벤트 수",
    )

    dry_run: bool = Field(
        default=True,
        description="True이면 로그만 기록, 실제 조정 미수행",
    )

    # --- Safety Valve (하드 리밋 방어) ---

    safety_valve_cpu_threshold: float = Field(
        default=0.95,
        ge=0.5, le=1.0,
        description="Safety Valve 발동 CPU 임계치. 이벤트 모드라도 이 값 초과 시 즉시 CRITICAL 전환",
    )

    safety_valve_error_rate_threshold: float = Field(
        default=0.10,
        ge=0.01, le=1.0,
        description="Safety Valve 발동 Error Rate 임계치",
    )

    safety_valve_min_hold_seconds: int = Field(
        default=120,
        ge=30, le=600,
        description="Safety Valve 발동 후 최소 유지 시간 (Flapping 방지)",
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
| `PoolWatchdog` | 파라미터 추가 | `shrink_guard: Callable[[], str \| None]` 생성자 파라미터 1개 추가 (기본값 None, 하위 호환). 기존 `alert_callback` 패턴과 동일 |
| `Bulkhead` | **변경 없음** | `max_concurrent` 직접 조정 |
| `GracefulDegradation` | **변경 없음** | 기존 `update_level()` API 사용. Safety Valve는 PreWarmer 내부에서 호출 |
| `HPAMetricsExporter` | **변경 없음** | 기존 메트릭 export 활용 |
| `StateBackend` (core) | **사용** (변경 없음) | EventCalendar 영속화 + Global Baseline 저장에 기존 StateBackend 활용 |
| `RecoveryGate` (emergency_mode) | **사용** (변경 없음) | Safety Valve 복구 판단에 기존 RecoveryGate 활용 |

**총 기존 코드 변경**: EventType enum 2줄 + SpikeClassifier 파라미터 1개 + PoolWatchdog 파라미터 1개.
나머지는 기존 public API 호출만으로 구현한다.

---

## 8. 테스트 전략

### 8.1 단위 테스트 (`packages/selfhealing-python/tests/unit/capacity_reservation/`)

| 테스트 파일 | 대상 | 핵심 케이스 |
|------------|------|------------|
| `test_event_calendar.py` | EventCalendar | 등록/취소/조회, 과거 시간 거부, 중복 ID 거부, 겹치는 이벤트 경고, MAX 배율 계산, StateBackend 영속화 |
| `test_pre_warmer.py` | PreWarmer | warm_up/cool_down 정상 동작, Re-evaluation, Global Baseline 저장/복원, Safety Valve 발동/복구 |
| `test_service.py` | CapacityReservationService | 싱글톤, 스케줄러 동작, dry-run 모드, 초기화 훅 (고아 베이스라인 복원) |
| `test_ml_integration.py` | ML 연동 | SCHEDULED_EVENT_STARTED 발행 시 SpikeClassifier가 HEALTHY_SURGE 반환 확인 |

### 8.2 핵심 테스트 시나리오

1. **정상 플로우**: 이벤트 등록 → 워밍 시간 도달 → warm_up → 이벤트 종료 → cool_down → 설정 원복 검증
2. **ML 충돌 방지**: 이벤트 기간 중 RPS 급증 시 SpikeClassifier가 ANOMALOUS_SPIKE가 아닌 HEALTHY_SURGE 반환
3. **이벤트 취소**: 워밍 진행 중 취소 → rollback 완료 검증
4. **dry-run 모드**: 로그만 기록되고 실제 설정은 변경되지 않음 검증
5. **설정 복원 보장**: 프로세스 비정상 종료 후 재시작 시 초기화 훅이 고아 Global Baseline을 감지하여 원복
6. **이벤트 겹침 (Re-evaluation)**: 이벤트 A(2x) + B(3x) 동시 진행 → MAX=3x 적용 → A 종료 → B의 3x 유지 → B 종료 → Baseline 복원
7. **Safety Valve**: 이벤트 중 CPU 95% 초과 → CRITICAL 강제 전환 → min_hold 경과 + 메트릭 안정 → 이벤트 모드 복귀
8. **Safety Valve Flapping 방지**: 발동 후 min_hold_seconds 내 메트릭 안정화 → 복귀 차단 확인
9. **Late Joiner**: 이벤트 진행 중 신규 Pod 기동 → StateBackend에서 활성 이벤트 Pull → 즉시 warm_up 상태 확인
10. **shrink_guard 억제**: 이벤트 기간 중 `_try_shrink()` 호출 → guard가 "ScheduledEvent" 반환 → shrink 미수행 + reason이 message에 포함
11. **shrink_guard 복합 조건**: 이벤트 + 긴급모드 동시 활성 → guard가 첫 번째 매칭 사유 반환 → 올바른 억제 확인
12. **shrink_guard None (평시)**: guard가 None 반환 → 기존 shrink 로직 정상 동작 확인

---

## 9. 위험 요소 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| PreWarmer 실행 중 프로세스 종료 | 임시 설정이 영구화 | 초기화 훅에서 StateBackend의 Global Baseline을 감지하여 자동 복원 (3.4절) |
| 이벤트 시간 오등록 (3시간 빠름 등) | 불필요한 리소스 확장 | `max_rate_multiplier`, `max_pool_multiplier` 상한 설정으로 피해 제한 |
| 다수 이벤트 동시 진행 | 리소스 과다 확장 | MAX 병합 전략 + Settings cap 적용 (3.2절). 동시 진행 이벤트 수 상한(기본 3개) |
| PoolWatchdog shrink가 warm_up 직후 발동 | 사전 확장 무효화 | `shrink_guard` Callback Guard로 이벤트 기간 중 shrink 억제 (2.2절) |
| ML 학습 데이터 오염 | 이벤트 기간 데이터로 모델 편향 | `ForecastDataPoint.has_adjustment=True` 마킹 |
| 이벤트 겹침 시 원복 타이밍 오류 | 이벤트 A 종료가 B 설정을 원복 | Re-evaluation 패턴으로 원천 차단. cool_down은 이벤트별 원복이 아닌 전체 재계산 (3.3절) |
| 이벤트 중 예상 초과 트래픽/DDoS | Degradation 억제로 시스템 과부하 | Safety Valve가 하드 리밋 초과 시 즉시 CRITICAL 전환 (2.4절). Flapping 방지용 min_hold 적용 |
| HPA 신규 Pod가 이벤트 컨텍스트 미수신 | Late Joiner가 평시 용량으로 서비스 | Pull+Push 하이브리드: Pod 기동 시 StateBackend에서 활성 이벤트 즉시 로드 (3.2절) |

---

## 10. 구현 순서

1. **Phase 1**: `settings/capacity_reservation.py` (Safety Valve 설정 포함) + `event_calendar.py` (MAX 병합 + StateBackend 영속화) + 단위 테스트
2. **Phase 2**: `pre_warmer.py` (Global Baseline + Re-evaluation + Safety Valve) + 기존 모듈 연동 + 단위 테스트
3. **Phase 3**: `service.py` (스케줄러, 싱글톤, 초기화 훅) + EventBus 이벤트 추가 + PoolWatchdog `shrink_guard` 연동 + 통합 테스트
4. **Phase 4**: SpikeClassifier context 확장 + ML 연동 테스트
