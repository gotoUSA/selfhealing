# 299. Config Shadow Evaluator — 설정 변경 사전 시뮬레이션 엔진

> **Status**: Proposed
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/` — 서비스 디렉터리
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/service.py` — ShadowEvaluatorService
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/evaluators/` — Evaluator 구현체
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/models.py` — 데이터 모델
> **References**:
> - [298_EVENT_JOURNAL.md](298_EVENT_JOURNAL.md) — EventJournal 저장소 (데이터 소스)
> - [300_CANARY_SHADOW_GATE.md](300_CANARY_SHADOW_GATE.md) — Canary 연동 및 Governance Soft Gate
> - `services/circuit_breaker/service.py` — CB 상태 전이 로직 (시뮬레이션 대상)
> - `services/error_budget/calculator.py` — Error Budget 계산 (시뮬레이션 대상)
> - `interfaces/resilience_policy.py` — Protocol 패턴 참고

---

## 1. 목적

Canary 롤아웃으로 Self-Healing 설정을 변경하기 **전에**,
과거 이벤트를 리플레이하여 "새 설정이었다면 어떤 결과가 나왔을까"를 시뮬레이션한다.

**예시**: CB `failure_threshold`를 5에서 10으로 변경하려 할 때:
1. 지난 2주간의 CB 관련 이벤트를 EventJournal(298)에서 조회
2. 기존 설정(threshold=5)으로 가상 재생 → CB 8회 개방
3. 후보 설정(threshold=10)으로 가상 재생 → CB 3회 개방
4. 비교 리포트 생성: "CB 개방 62.5% 감소, 단 MTTR 12분 증가 위험"

---

## 2. 기존 코드 근거

### 2.1 CB 상태 전이 로직 — 시뮬레이션 대상

`services/circuit_breaker/service.py:585-631`에서 CB 개방 판정:

```python
# service.py:604-605 — minimum_calls 체크
if total_calls < self.config.minimum_calls:
    return False

# service.py:615-616 — rate-based threshold
failure_rate = (state.failure_count / total_calls * 100) if total_calls > 0 else 0
if failure_rate >= self.config.failure_rate_threshold:
    return True

# service.py:628 — count-based threshold
if state.failure_count >= self.config.failure_threshold:
    return True
```

이 로직을 그대로 가져와 "가상 상태"에 대해 실행하면
설정 변경 효과를 정확히 예측할 수 있다.

### 2.2 Error Budget Calculator — 시뮬레이션 선례

`services/error_budget/service.py:207-236`에 이미 시뮬레이션이 존재한다:

```python
def simulate_budget_exhaustion(
    self,
    target_remaining_percent: float = 0.0,
) -> dict:
    budget_status = self.get_budget_status()
    current_remaining = budget_status.budget_remaining_percent
    if current_remaining > target_remaining_percent:
        errors_needed = int((current_remaining - target_remaining_percent) * 100)
        self._simulated_errors += errors_needed
```

이 **"시뮬레이션 메서드를 서비스에 직접 두는 패턴"**은 Config Shadow Evaluator의 선례이다.
다만 기존 시뮬레이션은 단순 숫자 조작이고, Config Shadow Evaluator는
과거 이벤트 스트림을 리플레이하는 정밀 시뮬레이션이다.

### 2.3 Burn Rate 계산

`services/error_budget/calculator.py:210-254`:

```python
def _calculate_burn_rate(self, slo, window_hours, current_time):
    # Burn Rate = (actual_error_rate / allowed_error_rate)
```

이 계산 로직을 ErrorBudgetEvaluator가 활용하여
"후보 설정에서의 예상 burn rate"를 산출한다.

### 2.4 ResiliencePolicy Protocol 패턴

`interfaces/resilience_policy.py:66-73`:

```python
class PolicyOutcome(str, Enum):
    SUCCESS = "success"
    SUCCESS_WITH_FALLBACK = "fallback"
    REJECTED = "rejected"
    FAILURE = "failure"
    TIMEOUT = "timeout"
```

`interfaces/resilience_policy.py:82-112`:

```python
@dataclass
class PolicyResult(Generic[T]):
    value: T | None = None
    outcome: PolicyOutcome = PolicyOutcome.SUCCESS
    error: Exception | None = None
    executed_policies: list[str] = field(default_factory=list)
    total_attempts: int = 1
    total_duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
```

ConfigEvaluator의 `EvaluationResult`는 이 `PolicyResult` 패턴을 따르되,
시뮬레이션에 특화된 비교 필드를 추가한다.

### 2.5 Sliding Window — 시뮬레이션 정확도의 핵심

`services/circuit_breaker/service.py:589-602`:

```python
window_size = self.config.sliding_window_size
if window_size > 0 and total_calls > window_size:
    total_calls = window_size
```

CB는 sliding window 기반으로 failure rate를 계산한다.
시뮬레이션에서도 이 윈도우를 정확히 재현해야 한다.
InMemory CB 어댑터(`adapters/memory/circuit_breaker.py:48`)의 `deque(maxlen=window_size)`
ring buffer 패턴을 시뮬레이터에서 재활용한다.

---

## 3. 데이터 모델

### 3.1 ShadowEvaluation — 평가 엔티티

Canary의 `CanaryState`에 상태를 추가하지 않고 독립 엔티티로 관리한다.

```python
class EvaluationStatus(str, Enum):
    """Shadow Evaluation 상태."""
    PENDING = "pending"        # 생성됨, 실행 대기
    RUNNING = "running"        # 시뮬레이션 실행 중
    COMPLETED = "completed"    # 완료
    FAILED = "failed"          # 시뮬레이션 자체 오류


@dataclass
class ShadowEvaluation:
    """단일 Shadow Evaluation 실행."""

    evaluation_id: str                    # UUID
    rollout_id: str | None                # Canary rollout 연결 (선택)
    status: EvaluationStatus
    created_at: datetime
    completed_at: datetime | None = None

    # 입력
    config_type: str = ""                 # "circuit_breaker", "error_budget"
    baseline_config: dict[str, Any] = field(default_factory=dict)
    candidate_config: dict[str, Any] = field(default_factory=dict)
    service_name: str = ""                # 대상 서비스
    time_window_hours: int = 336          # 분석 시간 범위 (기본 14일)
    region: str = ""                      # 멀티리전 격리 (JournalEntry.region과 동일)

    # 출력
    report: EvaluationReport | None = None
    error_message: str = ""
```

**설계 결정 — 환경/테넌트 격리 (Multi-tenancy)**:

`service_name`에 환경을 인코딩하는 것(`payment_gateway_prod`)은 안티패턴이다.
기존 `JournalEntry`(`interfaces/event_journal.py:33-34`)에 이미 `region`, `tier_id` 필드가 존재하며,
`JournalQueryFilter`(`interfaces/event_journal.py:45`)에도 `region` 필터가 있다.

- **region**: `ShadowEvaluation.region` 필드를 추가하여 `JournalQueryFilter.region`으로 전달한다(6.2 참조).
- **env/tenant_id**: 현재 아키텍처는 단일 클러스터 마이크로서비스이므로 별도 필드 불필요.
  향후 필요 시 `JournalEntry.context`에 저장하고, `JournalQueryFilter`에 `context_filters` 필드를 추가하여
  JSON 내부 값(예: `{"env": "staging"}`)을 매칭 필터링한다(아래 참조).

#### JournalQueryFilter 확장 — context_filters

298의 `JournalQueryFilter`(`interfaces/event_journal.py:37-46`)에 `context_filters` 필드를 추가하여
`context` JSON 내부 필터링을 지원한다:

```python
@dataclass
class JournalQueryFilter:
    """저널 조회 필터."""
    event_types: list[str] | None = None
    service_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    region: str | None = None
    limit: int = 1000
    # 추가: context JSON 내부 키-값 매칭 필터
    context_filters: dict[str, str] | None = None  # 예: {"env": "staging", "tenant_id": "acme"}
```

**어댑터 구현 영향**:
- `adapters/memory/event_journal.py:90-117`의 `_apply_filter()`: `context_filters` 각 키-값이
  `entry.context`에 존재하고 일치하는지 추가 체크 (in-memory 순회)
- `adapters/redis/event_journal.py:236-262`의 `_matches_filter()`: 동일 로직.
  Redis는 JSON 내부 검색 불가(Sorted Set 구조)이므로 `ZRANGEBYSCORE` 후
  클라이언트 사이드 필터링. `limit`과 시간 범위가 1차 필터로 작동하여 실용적 성능 유지.

**설계 결정 — CanaryState 미확장 이유**:
`services/canary/models.py:56-78`의 `CanaryState` enum에 EVALUATING/EVALUATED를 추가하면
`service.py:287-293`의 `start_rollout()` 상태 검증, `models.py:313-320`의 `is_terminal` 프로퍼티,
`pause()`, `rollback()` 등 전체 상태 전이 로직에 영향이 간다.
독립 엔티티로 분리하면 Canary 서비스 1196줄을 건드리지 않는다.

**설계 결정 — `rollout_id` 선택 필드 이유**:
Shadow Evaluation은 Canary 없이도 독립 실행 가능해야 한다.
운영자가 "이 설정 변경의 효과를 미리 보고 싶다"고 할 때
Canary rollout 없이 바로 시뮬레이션을 실행할 수 있다.

### 3.2 EvaluationReport — 비교 결과

```python
@dataclass
class EvaluationReport:
    """시뮬레이션 비교 결과 리포트."""

    # 메타데이터
    events_analyzed: int                  # 분석된 이벤트 수
    time_range_start: datetime
    time_range_end: datetime

    # 개별 Evaluator 결과
    evaluator_results: list[EvaluatorResult] = field(default_factory=list)

    # 종합 판정
    passed: bool = False
    confidence_score: float = 0.0         # 0.0 ~ 1.0
    summary: str = ""                     # 사람이 읽을 수 있는 요약
    warnings: list[str] = field(default_factory=list)


@dataclass
class EvaluatorResult:
    """개별 Evaluator의 비교 결과."""

    evaluator_name: str                   # "circuit_breaker", "error_budget"
    passed: bool
    confidence_score: float               # 0.0 ~ 1.0

    # 비교 수치
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    candidate_metrics: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, Any] = field(default_factory=dict)

    # 상세
    details: str = ""
    warnings: list[str] = field(default_factory=list)
```

**`confidence_score` 설계 근거**:
`core/decision_engine.py:258-311`에 이미 confidence 계산 로직이 존재한다:
- Sample count confidence
- Stability factor (variance/mean coefficient of variation)

이벤트가 충분하면 confidence가 높고, 적으면 낮다.
운영자가 "confidence 0.3이면 시뮬레이션 결과를 믿을 수 없다"고 판단할 수 있게 한다.

---

## 4. Evaluator 인터페이스

### 4.1 ConfigEvaluator Protocol

`interfaces/resilience_policy.py`의 `ResiliencePolicy` Protocol 패턴을 따른다:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class ConfigEvaluator(Protocol):
    """설정 변경 효과를 시뮬레이션하는 Evaluator 프로토콜."""

    @property
    def name(self) -> str:
        """Evaluator 이름 (예: "circuit_breaker")."""
        ...

    @property
    def event_types(self) -> list[str]:
        """이 Evaluator가 처리하는 이벤트 타입 리스트."""
        ...

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult:
        """
        이벤트 스트림에 대해 baseline과 candidate 설정을 비교 평가한다.

        Args:
            events: EventJournal에서 조회한 이벤트 리스트 (시퀀스 오름차순)
            baseline_config: 현재 적용된 설정
            candidate_config: 변경하려는 후보 설정

        Returns:
            비교 결과
        """
        ...
```

**설계 결정 — Protocol 선택 이유**:
`ResiliencePolicy`가 Protocol로 정의된 이유 (`interfaces/resilience_policy.py:1-22`)와 동일:
"Protocol-based for structural subtyping (duck typing)".
ABC 상속 대신 Protocol을 사용하여 기존 클래스를 래핑하지 않고도 Evaluator로 등록할 수 있다.

### 4.2 TimeSeriesMetricsProvider Protocol — Raw Metrics 연동 (DIP)

Journal에는 의사결정 이벤트만 저장되므로, 임계치 상향 시뮬레이션의 정확도를 높이려면
외부 메트릭 시계열(Prometheus, Datadog 등)에서 Raw request rate/error rate를 가져와야 한다.

**기존 코드 근거**: 프로젝트에 이미 3개의 MetricsProvider Protocol이 존재한다:
- `core/auto_rollback_guard.py:90-104` — `MetricsProvider(Protocol)`: `get_error_rate()`, `get_latency_p99()`, `get_throughput()`
- `services/auto_tuning/metrics_provider.py:17-22` — `MetricsAdapterProtocol`: `fetch_current_metrics() -> dict[str, float]`
- `services/runbook/metrics_provider.py:17-62` — `RunbookMetricsProvider`: `get_metric(name, labels)`, `get_metrics_snapshot(names, labels)`

기존 Protocol들은 **현재 시점의 값**만 반환한다.
Config Shadow Evaluator는 **과거 시간 범위의 시계열**이 필요하므로 확장 Protocol을 정의한다.

```python
@runtime_checkable
class TimeSeriesMetricsProvider(Protocol):
    """시계열 메트릭 제공자. 시뮬레이션 시 과거 Raw 데이터 조회.

    기존 MetricsProvider(core/auto_rollback_guard.py:90)는 "현재값"만 반환.
    Config Shadow는 과거 시간 범위의 시계열이 필요하므로 별도 Protocol 정의.

    Implementations:
    - MockTimeSeriesProvider: 테스트 및 개발용 (현재 스프린트)
    - PrometheusTimeSeriesProvider: Prometheus PromQL 기반 (향후)
    - DatadogTimeSeriesProvider: Datadog Metrics API 기반 (향후)
    """

    def query_error_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        """시간 범위의 에러율 시계열을 반환한다.

        Args:
            service_name: 대상 서비스
            start: 조회 시작 시각 (UTC)
            end: 조회 종료 시각 (UTC)
            step_seconds: 시계열 간격 (기본 60초)

        Returns:
            (timestamp, error_rate) 튜플 리스트. error_rate는 0.0 ~ 1.0.
        """
        ...

    def query_request_rate(
        self,
        service_name: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        """시간 범위의 요청률(RPS) 시계열을 반환한다."""
        ...
```

#### MockTimeSeriesProvider — 개발/테스트용

외부 시스템(Prometheus/Datadog) 없이 시뮬레이터 로직을 100% 검증하기 위한 Mock.
`services/auto_tuning/metrics_provider.py:25-85`의 `MetricsProviderWrapper` 래핑 패턴을 참고.

```python
class MockTimeSeriesProvider:
    """테스트용 시계열 메트릭 제공자.

    임의의 시계열 데이터를 주입하여 시뮬레이터 로직을 검증한다.
    프로덕션에서는 Prometheus/Datadog 어댑터로 교체.
    """

    def __init__(self, data: dict[str, list[tuple[datetime, float]]] | None = None):
        self._data = data or {}

    def query_error_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        key = f"{service_name}:error_rate"
        return [
            (ts, val) for ts, val in self._data.get(key, [])
            if start <= ts < end
        ]

    def query_request_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]:
        key = f"{service_name}:request_rate"
        return [
            (ts, val) for ts, val in self._data.get(key, [])
            if start <= ts < end
        ]
```

**설계 결정 — DIP(의존성 역전) 구조**:
현재 스프린트에서는 `MockTimeSeriesProvider`로 시뮬레이터 로직을 완성하고,
추후 프로덕션 환경에 맞춰 어댑터만 갈아 끼운다.
`ShadowEvaluatorService.__init__`에서 `metrics_provider` 파라미터로 주입받는다(6.1 참조).

---

## 5. Evaluator 구현체

### 5.1 CircuitBreakerEvaluator

CB 서비스의 `_should_open()` 로직(`service.py:585-631`)을 가상 상태에 대해 재실행한다.

```python
class CircuitBreakerEvaluator:
    """CB 설정 변경 효과 시뮬레이터."""

    @property
    def name(self) -> str:
        return "circuit_breaker"

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult:
        baseline_opens = self._simulate(events, baseline_config)
        candidate_opens = self._simulate(events, candidate_config)

        delta_opens = candidate_opens.open_count - baseline_opens.open_count
        delta_pct = (
            (delta_opens / baseline_opens.open_count * 100)
            if baseline_opens.open_count > 0
            else 0.0
        )

        passed = self._check_pass_criteria(baseline_opens, candidate_opens)
        confidence, conf_warnings = self._calculate_confidence(
            events, baseline_config, candidate_config,
        )

        return EvaluatorResult(
            evaluator_name=self.name,
            passed=passed,
            confidence_score=confidence,
            baseline_metrics={
                "open_count": baseline_opens.open_count,
                "total_open_duration_seconds": baseline_opens.total_open_seconds,
                "avg_recovery_time_seconds": baseline_opens.avg_recovery_seconds,
            },
            candidate_metrics={
                "open_count": candidate_opens.open_count,
                "total_open_duration_seconds": candidate_opens.total_open_seconds,
                "avg_recovery_time_seconds": candidate_opens.avg_recovery_seconds,
            },
            delta={
                "open_count_delta": delta_opens,
                "open_count_change_percent": delta_pct,
            },
            details=(
                f"CB open {baseline_opens.open_count} -> "
                f"{candidate_opens.open_count} ({delta_pct:+.1f}%)"
            ),
            warnings=conf_warnings,
        )
```

#### 가상 상태 머신

CB 서비스의 실제 상태 전이 로직을 재현한다:

```python
def _simulate(
    self,
    events: list[JournalEntry],
    config: dict[str, Any],
) -> SimulationResult:
    """이벤트 스트림에 대해 가상 CB 상태 머신을 구동한다."""

    failure_threshold = config.get("failure_threshold", 5)
    recovery_timeout = config.get("recovery_timeout", 30)
    minimum_calls = config.get("minimum_calls", 5)
    failure_rate_threshold = config.get("failure_rate_threshold", 0)
    sliding_window_size = config.get("sliding_window_size", 100)

    # 가상 상태 — adapters/memory/circuit_breaker.py의 ring buffer 패턴 재활용
    state = "closed"
    failure_window: deque[bool] = deque(maxlen=sliding_window_size)
    opened_at: datetime | None = None
    open_count = 0
    total_open_seconds = 0.0
    recovery_durations: list[float] = []

    for event in events:
        if state == "open":
            # recovery_timeout 경과 확인 — service.py:245-248 로직
            if opened_at and (event.timestamp - opened_at).total_seconds() >= recovery_timeout:
                state = "half_open"

        if event.event_type == "circuit_breaker_opened":
            # failure_count in context is optional; defaults to 1 per event.
            # When present (e.g., via enriched journal), seeds the window
            # with the reported count. Safe because close events clear the window.
            event_failures = event.context.get("failure_count", 1)
            for _ in range(min(event_failures, sliding_window_size)):
                failure_window.append(True)

            if state == "closed":
                total_calls = len(failure_window)
                failure_count = sum(1 for x in failure_window if x)

                # service.py:604-605 — minimum_calls 체크
                if total_calls < minimum_calls:
                    continue

                should_open = False

                # service.py:615-616 — rate-based threshold
                if failure_rate_threshold > 0:
                    rate = (failure_count / total_calls * 100) if total_calls > 0 else 0
                    if rate >= failure_rate_threshold:
                        should_open = True

                # service.py:628 — count-based threshold
                if failure_count >= failure_threshold:
                    should_open = True

                if should_open:
                    state = "open"
                    opened_at = event.timestamp
                    open_count += 1

        elif event.event_type == "circuit_breaker_closed":
            if state in ("open", "half_open") and opened_at:
                duration = (event.timestamp - opened_at).total_seconds()
                total_open_seconds += duration
                recovery_durations.append(duration)
            state = "closed"
            failure_window.clear()
            opened_at = None

    avg_recovery = (
        sum(recovery_durations) / len(recovery_durations)
        if recovery_durations
        else 0.0
    )

    return SimulationResult(
        open_count=open_count,
        total_open_seconds=total_open_seconds,
        avg_recovery_seconds=avg_recovery,
    )
```

**핵심**: 실제 CB 서비스의 `_should_open()` (`service.py:585-631`) 판정 로직을 그대로 재현한다.
sliding_window_size 캡핑(`service.py:592-602`), minimum_calls 체크(`service.py:604-605`),
rate-based/count-based 이중 threshold(`service.py:615-628`) 모두 포함.

#### 통과 기준

```python
def _check_pass_criteria(
    self,
    baseline: SimulationResult,
    candidate: SimulationResult,
) -> bool:
    """후보 설정이 기존보다 나쁘지 않은지 판정한다."""
    # 규칙 1: CB 개방 횟수가 200% 이상 증가하면 실패
    if baseline.open_count > 0:
        increase_ratio = candidate.open_count / baseline.open_count
        if increase_ratio > 2.0:
            return False

    # 규칙 2: 평균 복구 시간이 300% 이상 증가하면 실패
    if baseline.avg_recovery_seconds > 0:
        recovery_ratio = candidate.avg_recovery_seconds / baseline.avg_recovery_seconds
        if recovery_ratio > 3.0:
            return False

    return True
```

#### Confidence 계산 — 방향성 패널티 포함

`core/decision_engine.py:258-311`의 `sample_confidence * stability_factor` 패턴을 차용하되,
**Counterfactual 데이터 부재 문제**를 해결하기 위해 방향성 패널티를 추가한다.

**문제**: Journal에는 의사결정 이벤트(상태 변경)만 저장된다(`services/event_journal/subscriber.py:28-38`).
CB가 열리면 이후 트래픽은 Fast-fail로 차단되므로, 임계치를 **상향**(예: 5→10)하는 시뮬레이션에서는
"6번째~10번째 실패"에 해당하는 원시 트래픽 데이터 자체가 존재하지 않는다.
반대로 임계치를 **하향**(예: 5→3)하는 시뮬레이션은 기존 스냅샷의 `failure_count`로 추론 가능하다.

**해결**: 임계치 상향 방향에는 confidence를 비례적으로 할인하고, warnings에 명시적 한계를 기술한다.

```python
def _calculate_confidence(
    self,
    events: list[JournalEntry],
    baseline_config: dict[str, Any],
    candidate_config: dict[str, Any],
) -> tuple[float, list[str]]:
    """이벤트 충분성 + 방향성 기반 신뢰도 계산."""
    cb_events = [e for e in events if e.event_type.startswith("circuit_breaker_")]
    warnings: list[str] = []

    # 1단계: 샘플 수 기반 confidence — decision_engine.py:278-280 패턴
    if len(cb_events) < 5:
        base_confidence = 0.2
    elif len(cb_events) < 20:
        base_confidence = 0.5
    elif len(cb_events) < 50:
        base_confidence = 0.8
    else:
        base_confidence = 0.95

    # 2단계: 방향성 패널티 — 임계치 상향 시 데이터 부재 반영
    baseline_threshold = baseline_config.get("failure_threshold", 5)
    candidate_threshold = candidate_config.get("failure_threshold", 5)

    if candidate_threshold > baseline_threshold:
        # 상향 비율만큼 confidence 할인 (예: 5→10 = 0.5배)
        ratio = baseline_threshold / candidate_threshold
        base_confidence *= ratio
        warnings.append(
            f"threshold_increase: threshold raised ({baseline_threshold}->"
            f"{candidate_threshold}), simulation accuracy limited due to "
            f"missing raw traffic data after CB open (confidence x{ratio:.2f})"
        )

    return min(base_confidence, 0.95), warnings
```

이 warnings는 `EvaluatorResult.warnings` 필드를 통해 운영자에게 전달된다.

### 5.2 ErrorBudgetEvaluator

Error Budget Calculator의 burn rate 로직(`calculator.py:210-254`)을 활용한다.

```python
class ErrorBudgetEvaluator:
    """Error Budget 설정 변경 효과 시뮬레이터."""

    @property
    def name(self) -> str:
        return "error_budget"

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult:
        baseline_budget = self._simulate(events, baseline_config)
        candidate_budget = self._simulate(events, candidate_config)

        delta_drain = candidate_budget.total_drain_percent - baseline_budget.total_drain_percent
        delta_critical_episodes = (
            candidate_budget.critical_episodes - baseline_budget.critical_episodes
        )

        return EvaluatorResult(
            evaluator_name=self.name,
            passed=self._check_pass_criteria(baseline_budget, candidate_budget),
            confidence_score=self._calculate_confidence(events),
            baseline_metrics={
                "total_drain_percent": baseline_budget.total_drain_percent,
                "critical_episodes": baseline_budget.critical_episodes,
                "max_burn_rate_1h": baseline_budget.max_burn_rate_1h,
            },
            candidate_metrics={
                "total_drain_percent": candidate_budget.total_drain_percent,
                "critical_episodes": candidate_budget.critical_episodes,
                "max_burn_rate_1h": candidate_budget.max_burn_rate_1h,
            },
            delta={
                "drain_percent_delta": delta_drain,
                "critical_episodes_delta": delta_critical_episodes,
            },
        )
```

#### 시뮬레이션 로직

```python
def _simulate(
    self,
    events: list[JournalEntry],
    config: dict[str, Any],
) -> BudgetSimulationResult:
    """Error Budget 이벤트를 기반으로 예산 소모를 시뮬레이션한다."""

    # 설정값 — settings/error_budget.py 및 settings/error_budget_gate.py 참조
    critical_threshold = config.get("critical_threshold_percent", 10)
    burn_rate_fast_critical = config.get("burn_rate_fast_critical", 14.4)
    total_drain = 0.0
    critical_episodes = 0
    max_burn_rate_1h = 0.0

    for event in events:
        if event.event_type == "error_budget_critical":
            budget_pct = event.context.get("budget_remaining_percent", 100)
            burn_rate = event.context.get("burn_rate_1h", 0)

            # 임계값 기반 판정 — error_budget_gate.py의 로직
            if budget_pct < critical_threshold:
                critical_episodes += 1
            elif burn_rate >= burn_rate_fast_critical:
                critical_episodes += 1

            max_burn_rate_1h = max(max_burn_rate_1h, burn_rate)
            total_drain += event.context.get("drain_amount", 0)

    return BudgetSimulationResult(
        total_drain_percent=total_drain,
        critical_episodes=critical_episodes,
        max_burn_rate_1h=max_burn_rate_1h,
    )
```

#### 통과 기준

```python
def _check_pass_criteria(
    self,
    baseline: BudgetSimulationResult,
    candidate: BudgetSimulationResult,
) -> bool:
    # 규칙 1: critical episodes가 증가하면 실패
    if candidate.critical_episodes > baseline.critical_episodes:
        return False

    # 규칙 2: 총 소모량이 50% 이상 증가하면 실패
    if baseline.total_drain_percent > 0:
        drain_ratio = candidate.total_drain_percent / baseline.total_drain_percent
        if drain_ratio > 1.5:
            return False

    return True
```

---

## 6. ShadowEvaluatorService

### 6.1 비동기 실행 모델

**설계 결정 — 동기(MVP) 건너뛰고 비동기 직행 이유**:
시스템 부하가 겹치면 동기식 API는 HTTP Timeout의 원인이 되며,
동기식에 맞춰 개발한 UI/API 스펙을 나중에 비동기로 전환하면
프론트엔드·백엔드 전체를 갈아엎는 대공사가 발생한다.

처음부터 **HTTP 202 Accepted + evaluation_id 반환 + 상태 폴링** 아키텍처를 확정하여
기술 부채를 원천 차단한다.

**기존 코드 근거**:
- `interfaces/task_queue.py:31-39` — `TaskStatus`(PENDING/STARTED/SUCCESS/FAILURE/RETRY/REVOKED) 완비
- `interfaces/task_queue.py:56-96` — `TaskResult` frozen dataclass (`is_finished`, `is_successful` 프로퍼티)
- `adapters/celery/tasks/runbook.py:20-54` — `@shared_task(bind=True, max_retries=1, acks_late=True)` 패턴

### 6.2 서비스 클래스

```python
class ShadowEvaluatorService:
    """Config Shadow Evaluation 서비스."""

    def __init__(
        self,
        journal_repo: EventJournalRepository | None = None,
        evaluators: list[ConfigEvaluator] | None = None,
        metrics_provider: TimeSeriesMetricsProvider | None = None,
    ):
        self._journal_repo = journal_repo or ProviderRegistry.get_event_journal_repo()
        self._evaluators = evaluators or self._default_evaluators()
        self._metrics_provider = metrics_provider
        self._evaluations: dict[str, ShadowEvaluation] = {}

    def _default_evaluators(self) -> list[ConfigEvaluator]:
        return [
            CircuitBreakerEvaluator(),
            ErrorBudgetEvaluator(),
        ]

    def submit_evaluation(
        self,
        config_type: str,
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
        service_name: str = "",
        time_window_hours: int = 336,
        rollout_id: str | None = None,
        region: str = "",
    ) -> ShadowEvaluation:
        """
        Shadow Evaluation을 생성하고 비동기 실행을 예약한다.

        즉시 PENDING 상태의 ShadowEvaluation을 반환한다.
        실제 시뮬레이션은 Celery 워커가 수행한다 (6.3 참조).
        클라이언트는 get_evaluation()으로 상태를 폴링한다.

        Returns:
            PENDING 상태의 ShadowEvaluation (evaluation_id 포함)
        """
        evaluation = ShadowEvaluation(
            evaluation_id=uuid4().hex[:12],
            rollout_id=rollout_id,
            status=EvaluationStatus.PENDING,
            created_at=utc_now(),
            config_type=config_type,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            service_name=service_name,
            time_window_hours=time_window_hours,
            region=region,
        )
        self._evaluations[evaluation.evaluation_id] = evaluation

        # Celery task 디스패치 — 전체 파라미터를 전달하여 워커 독립 실행 보장
        run_shadow_evaluation.delay(
            evaluation_id=evaluation.evaluation_id,
            config_type=config_type,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            service_name=service_name,
            time_window_hours=time_window_hours,
            region=region,
            rollout_id=rollout_id,
        )

        return evaluation

    def execute_evaluation(self, evaluation_id: str) -> ShadowEvaluation:
        """인프로세스 시뮬레이션 실행. 로컬 dict에서 evaluation을 조회한다."""
        evaluation = self._evaluations.get(evaluation_id)
        if evaluation is None:
            raise ValueError(f"Unknown evaluation_id: {evaluation_id}")
        return self._run_evaluation(evaluation)

    def execute_from_params(
        self,
        evaluation_id: str,
        config_type: str,
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
        service_name: str = "",
        time_window_hours: int = 336,
        region: str = "",
        rollout_id: str | None = None,
    ) -> ShadowEvaluation:
        """Celery 워커에서 호출. 파라미터로부터 evaluation을 생성하고 실행한다."""
        evaluation = ShadowEvaluation(
            evaluation_id=evaluation_id,
            rollout_id=rollout_id,
            status=EvaluationStatus.PENDING,
            created_at=utc_now(),
            config_type=config_type,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            service_name=service_name,
            time_window_hours=time_window_hours,
            region=region,
        )
        self._evaluations[evaluation_id] = evaluation
        return self._run_evaluation(evaluation)

    def _run_evaluation(self, evaluation: ShadowEvaluation) -> ShadowEvaluation:
        """실제 시뮬레이션 로직. submit/execute/execute_from_params에서 호출."""
        evaluation.status = EvaluationStatus.RUNNING

        try:
            # 1. 해당 config_type의 Evaluator 찾기
            evaluator = self._find_evaluator(evaluation.config_type)
            if evaluator is None:
                evaluation.status = EvaluationStatus.FAILED
                evaluation.error_message = f"No evaluator for config_type: {evaluation.config_type}"
                return evaluation

            # 2. EventJournal에서 이벤트 조회 (evaluator의 event_types로 필터링)
            end_time = utc_now()
            start_time = end_time - timedelta(hours=evaluation.time_window_hours)

            query_filter = JournalQueryFilter(
                event_types=evaluator.event_types,
                service_name=evaluation.service_name or None,
                start_time=start_time,
                end_time=end_time,
                region=evaluation.region or None,
            )
            query_result = self._journal_repo.query(query_filter)
            events = query_result.entries

            # 3. 시뮬레이션 실행
            result = evaluator.evaluate(
                events, evaluation.baseline_config, evaluation.candidate_config,
            )

            # 4. 리포트 생성
            evaluation.report = EvaluationReport(
                events_analyzed=len(events),
                time_range_start=start_time,
                time_range_end=end_time,
                evaluator_results=[result],
                passed=result.passed,
                confidence_score=result.confidence_score,
                summary=result.details,
                warnings=result.warnings,
            )
            evaluation.status = EvaluationStatus.COMPLETED
            evaluation.completed_at = utc_now()

        except Exception as e:
            evaluation.status = EvaluationStatus.FAILED
            evaluation.error_message = str(e)
            logger.error(
                "config_shadow.evaluation_failed",
                evaluation_id=evaluation.evaluation_id,
                error=str(e),
            )

        return evaluation

    def get_evaluation(self, evaluation_id: str) -> ShadowEvaluation | None:
        """evaluation_id로 상태를 조회한다. 클라이언트 폴링용."""
        return self._evaluations.get(evaluation_id)

    def _find_evaluator(self, config_type: str) -> ConfigEvaluator | None:
        for evaluator in self._evaluators:
            if evaluator.name == config_type:
                return evaluator
        return None
```

### 6.3 Celery Task — 비동기 워커

`adapters/celery/tasks/runbook.py:20-54`의 패턴을 따른다:
`@shared_task`, `bind=True`, `max_retries=1`, `acks_late=True`.

```python
# adapters/celery/tasks/config_shadow.py
from celery import shared_task


@shared_task(
    bind=True,
    name="selfhealing.tasks.config_shadow.run_shadow_evaluation",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def run_shadow_evaluation(
    self,
    evaluation_id: str,
    config_type: str = "",
    baseline_config: dict | None = None,
    candidate_config: dict | None = None,
    service_name: str = "",
    time_window_hours: int = 336,
    region: str = "",
    rollout_id: str | None = None,
) -> dict:
    """Shadow Evaluation을 비동기로 실행한다.

    submit_evaluation()에서 전체 파라미터를 받아 워커 프로세스에서
    독립적으로 evaluation을 생성·실행한다.
    """
    try:
        service = get_shadow_evaluator_service()
        evaluation = service.execute_from_params(
            evaluation_id=evaluation_id,
            config_type=config_type,
            baseline_config=baseline_config or {},
            candidate_config=candidate_config or {},
            service_name=service_name,
            time_window_hours=time_window_hours,
            region=region,
            rollout_id=rollout_id,
        )
        return {
            "evaluation_id": evaluation.evaluation_id,
            "status": evaluation.status.value,
            "passed": evaluation.report.passed if evaluation.report else None,
        }
    except self.MaxRetriesExceededError:
        logger.error(
            "config_shadow.task_max_retries_exceeded",
            evaluation_id=evaluation_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "config_shadow.task_failed",
            evaluation_id=evaluation_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)
```

**API 레벨 사용 예시**:
```python
# POST /api/shadow-evaluations/ → HTTP 202 Accepted
evaluation = service.submit_evaluation(
    config_type="circuit_breaker",
    baseline_config={"failure_threshold": 5},
    candidate_config={"failure_threshold": 10},
    service_name="payment_gateway",
)
# → {"evaluation_id": "a1b2c3d4e5f6", "status": "pending"}

# GET /api/shadow-evaluations/a1b2c3d4e5f6/ → HTTP 200
evaluation = service.get_evaluation("a1b2c3d4e5f6")
# → {"evaluation_id": "a1b2c3d4e5f6", "status": "completed", "report": {...}}
```

### 6.4 싱글톤 패턴

기존 서비스 싱글톤 패턴(`services/replay_service/service.py:764-770`)을 따른다:

```python
import threading

_service: ShadowEvaluatorService | None = None
_lock = threading.Lock()


def get_shadow_evaluator_service() -> ShadowEvaluatorService:
    global _service
    if _service is None:
        with _lock:
            if _service is None:
                _service = ShadowEvaluatorService()
    return _service
```

---

## 7. What-if 다중 실행 지원

운영자가 여러 후보 설정을 비교할 수 있다:

```python
def compare_candidates(
    self,
    config_type: str,
    baseline_config: dict[str, Any],
    candidates: list[dict[str, Any]],
    service_name: str = "",
    time_window_hours: int = 336,
) -> list[ShadowEvaluation]:
    """여러 후보 설정을 baseline과 비교한다."""
    results = []
    for candidate in candidates:
        result = self.submit_evaluation(
            config_type=config_type,
            baseline_config=baseline_config,
            candidate_config=candidate,
            service_name=service_name,
            time_window_hours=time_window_hours,
        )
        results.append(result)
    return results
```

**사용 시나리오**:
```python
service = get_shadow_evaluator_service()
results = service.compare_candidates(
    config_type="circuit_breaker",
    baseline_config={"failure_threshold": 5},
    candidates=[
        {"failure_threshold": 7},
        {"failure_threshold": 10},
        {"failure_threshold": 15},
    ],
    service_name="payment_gateway",
)
# → 3개의 ShadowEvaluation 반환, 각각 baseline 대비 비교 리포트 포함
```

---

## 8. 파일 구조

```
packages/selfhealing-python/src/selfhealing/services/config_shadow/
├── __init__.py                      # get_shadow_evaluator_service()
├── service.py                       # ShadowEvaluatorService
├── models.py                        # ShadowEvaluation, EvaluationReport, EvaluatorResult, etc.
├── metrics_provider.py              # TimeSeriesMetricsProvider Protocol + MockTimeSeriesProvider
└── evaluators/
    ├── __init__.py                  # ConfigEvaluator Protocol
    ├── circuit_breaker.py           # CircuitBreakerEvaluator
    └── error_budget.py              # ErrorBudgetEvaluator

packages/selfhealing-python/src/selfhealing/adapters/celery/tasks/
└── config_shadow.py                 # run_shadow_evaluation Celery task (6.3)
```

---

## 9. 확장 가이드

향후 새로운 Evaluator를 추가할 때:

1. `evaluators/` 아래에 새 파일 생성 (예: `dlq.py`)
2. `ConfigEvaluator` Protocol을 만족하는 클래스 구현
3. `ShadowEvaluatorService._default_evaluators()`에 등록

```python
# 예시: DLQEvaluator 추가
class DLQEvaluator:
    @property
    def name(self) -> str:
        return "dlq"

    def evaluate(self, events, baseline, candidate) -> EvaluatorResult:
        ...
```

Evaluator 추가 시 기존 코드 변경은 `_default_evaluators()` 리스트에 1줄 추가뿐이다.
