# 301. Live Canary Evaluator — promote() 시점의 실시간 메트릭 기반 평가

> **Status**: Implemented (post-review fixes applied)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/evaluators/live_canary.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/evaluators/__init__.py` — Protocol 리팩토링 (Q1)
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/models.py` — EvaluationContext 추가 (Q1)
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/metrics_provider.py` — Protocol 확장 (Q2/Q4/Q5)
> - `packages/selfhealing-python/src/selfhealing/services/canary/service.py` — `promote()` 플로우 단순화 (Q3)
> - `packages/selfhealing-python/src/selfhealing/services/canary/models.py` — PassCriteria DTO 격하 (Q3)
> - `packages/selfhealing-python/src/selfhealing/settings/config_shadow.py` — Settings 추가
> **References**:
> - [299_CONFIG_SHADOW_EVALUATOR.md](299_CONFIG_SHADOW_EVALUATOR.md) — 시뮬레이션 엔진
> - [300_CANARY_SHADOW_GATE.md](300_CANARY_SHADOW_GATE.md) — start_rollout() Shadow Gate
> - `services/canary/models.py` — PassCriteria, CanaryMetrics
> - `services/config_shadow/evaluators/__init__.py` — ConfigEvaluator Protocol
> - `services/config_shadow/metrics_provider.py` — TimeSeriesMetricsProvider Protocol

---

## 1. 목적

300번 문서의 Shadow Evaluation은 **과거 이벤트 리플레이** 기반의 "사전 안전 검증"이다.
그러나 `promote()` 시점에는 Canary 노드에서 **실제 트래픽이 이미 흐르고 있다**.
이 시점에서 과거 시뮬레이션 결과를 다시 확인하는 것은 무의미하다.

Live Canary Evaluator는 `ConfigEvaluator` Protocol을 구현하되,
**EventJournal 이벤트가 아닌 실시간 메트릭(Prometheus/Datadog)**을 소스로 사용하여
Canary 노드의 실제 동작을 평가한다.

**설계 원칙**:
- Shadow Evaluation(300) = 과거 데이터 → `start_rollout()` 전 검증
- Live Canary Evaluation(301) = 실시간 데이터 → `promote()` 전 검증
- 두 평가자 모두 `EvaluationContext`를 통해 통합된 Protocol을 따른다

---

## 2. 기존 코드 근거

### 2.1 ConfigEvaluator Protocol (현행)

`services/config_shadow/evaluators/__init__.py:15-45`:

```python
@runtime_checkable
class ConfigEvaluator(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def event_types(self) -> list[str]: ...

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult: ...
```

**문제점**: `events: list[JournalEntry]`가 시그니처에 고정되어 있어,
실시간 메트릭 기반 평가자는 이 파라미터를 무시해야 하는 LSP 위반이 발생한다.
→ §3.1에서 `EvaluationContext` 도입으로 해결.

### 2.2 TimeSeriesMetricsProvider Protocol (현행)

`services/config_shadow/metrics_provider.py:15-53`:

```python
@runtime_checkable
class TimeSeriesMetricsProvider(Protocol):
    def query_error_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]: ...

    def query_request_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]: ...
```

**문제점 3가지**:
1. `service_name: str` 단일 문자열로는 K8s 복합 레이블(`app`, `track`, `namespace`)을 표현할 수 없다 → §3.2에서 `labels` 파라미터 추가.
2. 시계열 반환값의 단순 평균(`_avg`)은 트래픽 가중치를 무시하는 수학적 오류 → §3.2에서 스칼라 집계 메서드 추가.
3. Latency(P95/P99) 조회 메서드 부재 → §3.2에서 추가.

### 2.3 PassCriteria — Canary 헬스 기준 (현행)

`services/canary/models.py:82-166`:

```python
@dataclass
class PassCriteria:
    error_rate_absolute_max: float = 0.05       # 5%
    error_rate_increase_max: float = 0.01       # 1%
    latency_p95_delta_ms: float = 50.0
    latency_p99_delta_pct: float = 0.2          # 20%
    error_budget_drain_rate_max: float = 1.2
    error_budget_remaining_min: float = 0.1     # 10%
    min_requests_required: int = 100
    evaluation_window_seconds: int = 300        # 5분

    def evaluate(self, metrics: "CanaryMetrics") -> tuple[bool, str | None]:
        # 에러율 + p99 latency 검사 로직 (models.py:137-166)
        ...
```

**문제점**: `PassCriteria.evaluate()`의 판정 로직이 `LiveCanaryEvaluator`의 판정 로직과 중복된다.
또한 `promote()`에서 호출하는 `_collect_stage_metrics()`는 현재 빈 리스트를 반환하는 스텁이다
(`service.py:1226-1236`):

```python
def _collect_stage_metrics(self, rollout: CanaryRollout) -> list[CanaryMetrics]:
    """TODO: Prometheus/메트릭 시스템 연동"""
    # 현재는 빈 목록 반환 (메트릭 시스템 연동 필요)
    return []
```

따라서 `_is_stage_healthy()`는 항상 `True`를 반환한다 (`service.py:1258-1260`):

```python
if not metrics:
    return True, None  # 메트릭 없으면 통과 (샘플 부족)
```

→ §3.3에서 `PassCriteria`를 순수 DTO로 격하하고, 판정 로직을 `LiveCanaryEvaluator`로 통합.

### 2.4 promote()의 현재 메트릭 검증 경로 (현행)

`services/canary/service.py:459-473`:

```python
# 현재 단계 메트릭 검증 (force가 아니면)
if not force:
    metrics = self._collect_stage_metrics(rollout)      # ← 항상 []
    is_healthy, failure_reason = self._is_stage_healthy(
        rollout.current_stage, metrics, tier_id=tier_id, # ← 항상 True
    )
    if not is_healthy:
        ...
        return False
```

→ §4에서 이 경로를 `LiveCanaryEvaluator` 단일 경로로 대체.

### 2.5 기존 Evaluator 구현체 (영향 범위)

`ShadowEvaluatorService._default_evaluators()` (`service.py:59-63`):

```python
def _default_evaluators(self) -> list[ConfigEvaluator]:
    return [
        CircuitBreakerEvaluator(),
        ErrorBudgetEvaluator(),
    ]
```

현재 `ConfigEvaluator` Protocol 구현체는 **2개**만 존재하며,
`ShadowEvaluatorService._run_evaluation()` (`service.py:175-179`)에서 호출:

```python
result = evaluator.evaluate(
    events,
    evaluation.baseline_config,
    evaluation.candidate_config,
)
```

→ §3.1의 Protocol 변경 시 수정 대상: 이 2개 evaluator + 1개 오케스트레이터.

### 2.6 promote()의 자동 승격 트리거

`tasks/canary_watchdog.py:624-651`:

- Celery Beat: 매 1분 실행
- 조건: `auto_promote=True` + `elapsed >= stage.duration_minutes` + 메트릭 통과

현재 promote()의 메트릭 검증은 `PassCriteria.evaluate(metrics)`로 수행된다.
Live Canary Evaluator는 이 검증을 **Config Shadow 프레임워크와 통합**하여
일관된 `EvaluatorResult` 형태로 결과를 반환한다.

---

## 3. 선행 리팩토링 (Q1-Q5)

301 구현 전, 기존 코드의 설계 결함 5가지를 함께 해결한다.
현재 evaluator가 2개뿐이고 `_collect_stage_metrics`가 스텁인 지금이 변경 비용이 가장 낮다.

### 3.1 Q1: EvaluationContext 도입 — LSP 위반 해소

**문제**: 현행 `ConfigEvaluator.evaluate()` 시그니처의 `events: list[JournalEntry]`는
Shadow Evaluator 전용이다. Live Evaluator는 이 파라미터를 무시해야 하므로
리스코프 치환 원칙(LSP)을 위반한다.

**해결**: `EvaluationContext` 데이터클래스를 도입하여 evaluate 시그니처를 통합한다.

#### 3.1.1 EvaluationContext 모델 (신규)

`services/config_shadow/models.py`에 추가:

```python
@dataclass
class EvaluationContext:
    """Evaluator에 전달되는 통합 평가 컨텍스트.

    Shadow Evaluator는 events를 사용하고,
    Live Evaluator는 time_window_seconds + labels를 사용한다.
    """

    baseline_config: dict[str, Any]
    candidate_config: dict[str, Any]

    # Shadow 용 (과거 이벤트 리플레이)
    events: list["JournalEntry"] = field(default_factory=list)

    # Live 용 (실시간 메트릭 쿼리)
    time_window_seconds: int = 300
    baseline_labels: dict[str, str] = field(default_factory=dict)
    candidate_labels: dict[str, str] = field(default_factory=dict)

    # 공통
    service_name: str = ""
```

**`service_name` 필드 근거**: `ShadowEvaluatorService._run_evaluation()`
(`service.py:167`)이 `evaluation.service_name`으로 이벤트를 필터링하고,
Live 평가에서도 메트릭 쿼리에 서비스명이 필수이므로 Context에 포함한다.

#### 3.1.2 ConfigEvaluator Protocol 변경

`services/config_shadow/evaluators/__init__.py`:

```python
@runtime_checkable
class ConfigEvaluator(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def event_types(self) -> list[str]: ...

    def evaluate(self, context: EvaluationContext) -> EvaluatorResult: ...
```

시그니처가 `evaluate(self, context: EvaluationContext) -> EvaluatorResult`
단일 형태로 통합된다.

#### 3.1.3 기존 Evaluator 마이그레이션

**CircuitBreakerEvaluator** (`evaluators/circuit_breaker.py:28-34`):

```python
# Before
def evaluate(self, events: list[JournalEntry], baseline_config, candidate_config) -> EvaluatorResult:
    baseline_opens = self._simulate(events, baseline_config)
    ...

# After
def evaluate(self, context: EvaluationContext) -> EvaluatorResult:
    baseline_opens = self._simulate(context.events, context.baseline_config)
    candidate_opens = self._simulate(context.events, context.candidate_config)
    ...
```

**ErrorBudgetEvaluator** (`evaluators/error_budget.py:30-35`):

```python
# Before
def evaluate(self, events: list[JournalEntry], baseline_config, candidate_config) -> EvaluatorResult:
    baseline_budget = self._simulate(events, baseline_config)
    ...

# After
def evaluate(self, context: EvaluationContext) -> EvaluatorResult:
    baseline_budget = self._simulate(context.events, context.baseline_config)
    candidate_budget = self._simulate(context.events, context.candidate_config)
    ...
```

#### 3.1.4 ShadowEvaluatorService 호출부 수정

`services/config_shadow/service.py:165-179`:

```python
# Before
query_result = self._journal_repo.query(query_filter)
events = query_result.entries

result = evaluator.evaluate(
    events,
    evaluation.baseline_config,
    evaluation.candidate_config,
)

# After
query_result = self._journal_repo.query(query_filter)

context = EvaluationContext(
    baseline_config=evaluation.baseline_config,
    candidate_config=evaluation.candidate_config,
    events=query_result.entries,
    service_name=evaluation.service_name,
)
result = evaluator.evaluate(context)
```

#### 3.1.5 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| `config_shadow/models.py` | `EvaluationContext` 추가 |
| `config_shadow/evaluators/__init__.py` | Protocol 시그니처 변경 |
| `config_shadow/evaluators/circuit_breaker.py` | `evaluate()` 시그니처 변경 |
| `config_shadow/evaluators/error_budget.py` | `evaluate()` 시그니처 변경 |
| `config_shadow/service.py` | `_run_evaluation()` 호출부 변경 |
| `tests/unit/services/config_shadow/test_circuit_breaker_evaluator.py` | 테스트 시그니처 변경 |
| `tests/unit/services/config_shadow/test_error_budget_evaluator.py` | 테스트 시그니처 변경 |

---

### 3.2 Q2/Q4/Q5: TimeSeriesMetricsProvider Protocol 확장

**3가지 문제를 한 번에 해결**한다:
- Q2: 시계열 단순 평균의 수학적 오류 → 스칼라 집계 메서드 추가
- Q4: `service_name: str` 단일 문자열의 한계 → `labels` 파라미터 추가
- Q5: Latency(P95/P99) 조회 누락 → latency 메서드 추가

#### 3.2.1 확장된 Protocol

`services/config_shadow/metrics_provider.py`:

```python
@runtime_checkable
class TimeSeriesMetricsProvider(Protocol):
    """시계열 메트릭 제공자.

    추세 데이터(시계열 List)와 판정용 집계값(Scalar)을 분리하여 제공한다.
    """

    # --- 시계열 메서드 (추세 분석 / UI 대시보드용) ---

    def query_error_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
        labels: dict[str, str] | None = None,
    ) -> list[tuple[datetime, float]]: ...

    def query_request_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
        labels: dict[str, str] | None = None,
    ) -> list[tuple[datetime, float]]: ...

    # --- 스칼라 집계 메서드 (Evaluator 판정용) ---

    def query_error_rate_aggregated(
        self, service_name: str, start: datetime, end: datetime,
        labels: dict[str, str] | None = None,
    ) -> float:
        """윈도우 전체의 가중치 기반 에러율 스칼라.

        내부적으로 sum(rate(errors)) / sum(rate(requests)) 형태의
        PromQL/Datadog 쿼리를 실행한다.

        Returns:
            가중치 기반 에러율 (0.0 ~ 1.0)
        """
        ...

    def query_request_count(
        self, service_name: str, start: datetime, end: datetime,
        labels: dict[str, str] | None = None,
    ) -> int:
        """윈도우 전체의 총 요청 수."""
        ...

    def query_latency_aggregated(
        self, service_name: str, start: datetime, end: datetime,
        percentile: float = 0.99,
        labels: dict[str, str] | None = None,
    ) -> float:
        """윈도우 전체의 Latency Percentile 스칼라 (밀리초).

        내부적으로 histogram_quantile(percentile, ...) 형태의
        PromQL을 실행한다. Percentile은 평균할 수 없으므로
        반드시 Provider 수준에서 한 번에 계산해야 한다.

        Args:
            percentile: 0.95 (P95) 또는 0.99 (P99)

        Returns:
            해당 percentile의 latency (밀리초)
        """
        ...
```

#### 3.2.2 Q2 해결 — 수학적 오류 수정

**Before** (문서 원안의 `_avg`):

```python
@staticmethod
def _avg(series: list[tuple[datetime, float]]) -> float:
    if not series:
        return 0.0
    return sum(v for _, v in series) / len(series)  # ← 단순 산술 평균
```

분 단위 에러율(%)의 단순 평균은 심슨의 역설을 유발한다.
예: 10건/50% 에러 + 10,000건/1% 에러 → 단순 평균 25.5% vs 실제 ≈1.05%.

**After** — Provider에 계산을 위임:

```python
# LiveCanaryEvaluator 내부
baseline_error = self._metrics.query_error_rate_aggregated(
    service_name=context.service_name,
    start=start, end=now,
    labels=context.baseline_labels,
)
```

`query_error_rate_aggregated()`는 Prometheus 구현체에서
`sum(rate(http_errors_total[5m])) / sum(rate(http_requests_total[5m]))` 단일 쿼리로
실행되므로, 정확한 가중치 기반 에러율이 보장된다.

#### 3.2.3 Q4 해결 — Label Selectors

**Before** (문서 원안):

```python
baseline_errors = self._metrics.query_error_rate(
    service_name=f"{baseline_service}:{baseline_cluster}",  # ← 문자열 결합
    ...
)
```

K8s 환경에서 `app=payment, track=canary, namespace=prod` 같은 복합 레이블을
`service_name` 문자열 하나로 표현할 수 없다.

**After** — `labels` 딕셔너리를 EvaluationContext에서 릴레이:

```python
# EvaluationContext 생성 시 (promote 내부)
context = EvaluationContext(
    service_name=rollout.config_type,
    baseline_labels={"track": "stable", "namespace": "prod"},
    candidate_labels={"track": "canary", "namespace": "prod"},
    ...
)

# LiveCanaryEvaluator 내부
baseline_error = self._metrics.query_error_rate_aggregated(
    service_name=context.service_name,
    start=start, end=now,
    labels=context.baseline_labels,  # ← 복합 레이블 전달
)
```

`labels=None` 기본값이므로 기존 `MockTimeSeriesProvider`와의 하위 호환성이 유지된다.

#### 3.2.4 Q5 해결 — Latency 검증 추가

기존 `PassCriteria` (`models.py:95-96`)에는 latency 임계값이 정의되어 있으나,
원안의 `LiveCanaryEvaluator`는 에러율만 검사한다.

설정 변경(타임아웃 증가, 재시도 횟수 증가)은 에러율보다
P95/P99 Latency 폭등을 먼저 유발한다.

**추가되는 검증 로직** (LiveCanaryEvaluator.evaluate 내부):

```python
# 5c. P95 Latency 절대 증가
latency_p95_max = criteria.latency_p95_delta_ms
baseline_p95 = self._metrics.query_latency_aggregated(
    service_name=context.service_name,
    start=start, end=now, percentile=0.95,
    labels=context.baseline_labels,
)
candidate_p95 = self._metrics.query_latency_aggregated(
    service_name=context.service_name,
    start=start, end=now, percentile=0.95,
    labels=context.candidate_labels,
)
p95_delta = candidate_p95 - baseline_p95
if p95_delta > latency_p95_max:
    passed = False
    details_parts.append(
        f"P95 latency delta {p95_delta:.1f}ms > threshold {latency_p95_max:.1f}ms"
    )

# 5d. P99 Latency 비율 증가
latency_p99_max_pct = criteria.latency_p99_delta_pct
baseline_p99 = self._metrics.query_latency_aggregated(
    service_name=context.service_name,
    start=start, end=now, percentile=0.99,
    labels=context.baseline_labels,
)
candidate_p99 = self._metrics.query_latency_aggregated(
    service_name=context.service_name,
    start=start, end=now, percentile=0.99,
    labels=context.candidate_labels,
)
if baseline_p99 > 0:
    p99_pct = (candidate_p99 - baseline_p99) / baseline_p99
    if p99_pct > latency_p99_max_pct:
        passed = False
        details_parts.append(
            f"P99 latency increased by {p99_pct:.1%} > "
            f"threshold {latency_p99_max_pct:.1%}"
        )
```

Percentile은 산술 평균이 불가하므로, `query_latency_aggregated()`가
`histogram_quantile()` 스칼라를 반환한다.

#### 3.2.5 MockTimeSeriesProvider 확장

```python
class MockTimeSeriesProvider:
    """테스트용. 신규 스칼라/latency 메서드 추가."""

    def __init__(self, data: dict[str, list[tuple[datetime, float]]] | None = None):
        self._data = data or {}
        self._scalars: dict[str, float] = {}

    # 기존 시계열 메서드 (labels 파라미터 추가, 기본값 None)
    def query_error_rate(self, service_name, start, end, step_seconds=60,
                         labels=None) -> list[tuple[datetime, float]]:
        key = f"{service_name}:error_rate"
        return [(ts, val) for ts, val in self._data.get(key, []) if start <= ts < end]

    def query_request_rate(self, service_name, start, end, step_seconds=60,
                           labels=None) -> list[tuple[datetime, float]]:
        key = f"{service_name}:request_rate"
        return [(ts, val) for ts, val in self._data.get(key, []) if start <= ts < end]

    # 신규 스칼라 메서드
    def query_error_rate_aggregated(self, service_name, start, end,
                                    labels=None) -> float:
        return self._scalars.get(f"{service_name}:error_rate_agg", 0.0)

    def query_request_count(self, service_name, start, end,
                            labels=None) -> int:
        return int(self._scalars.get(f"{service_name}:request_count", 0))

    def query_latency_aggregated(self, service_name, start, end,
                                 percentile=0.99, labels=None) -> float:
        key = f"{service_name}:latency_p{int(percentile * 100)}"
        return self._scalars.get(key, 0.0)
```

---

### 3.3 Q3: PassCriteria DTO 격하 — 중복 검증 제거

**문제**: `PassCriteria.evaluate()` (`models.py:137-166`)와 `LiveCanaryEvaluator`의
판정 로직이 에러율 + latency 검증에서 중복된다.
또한 `_collect_stage_metrics()`가 빈 리스트 스텁이므로
현재 `promote()`의 PassCriteria 검증 경로는 **사실상 dead code**이다.

**해결**: `PassCriteria`를 순수 임계값 DTO로 격하하고,
판정 로직은 `LiveCanaryEvaluator`가 단독으로 담당한다.

#### 3.3.1 PassCriteria 변경

`services/canary/models.py`:

```python
@dataclass
class PassCriteria:
    """
    자동 프로모션을 위한 합격 기준 (임계값 DTO).

    판정 로직은 LiveCanaryEvaluator가 담당한다.
    이 클래스는 임계값 데이터만 보유한다.
    """

    # 에러율 관련
    error_rate_absolute_max: float = 0.05
    error_rate_increase_max: float = 0.01

    # 레이턴시 관련
    latency_p95_delta_ms: float = 50.0
    latency_p99_delta_pct: float = 0.2

    # Error Budget 관련
    error_budget_drain_rate_max: float = 1.2
    error_budget_remaining_min: float = 0.1

    # 평가 기간
    min_requests_required: int = 100
    evaluation_window_seconds: int = 300

    @classmethod
    def for_tier(cls, tier_id: str) -> "PassCriteria":
        """티어별 기본 PassCriteria 반환. (기존 로직 유지)"""
        ...
```

**제거 대상**: `PassCriteria.evaluate()` 메서드 (`models.py:137-166`)
**유지 대상**: `for_tier()`, `apply_tier_floor()` — 임계값 데이터 구성 유틸리티

#### 3.3.2 promote() 플로우 단순화

`services/canary/service.py` — promote() 내부:

```python
# Before (service.py:459-473) — dead code 경로
if not force:
    metrics = self._collect_stage_metrics(rollout)      # 항상 []
    is_healthy, failure_reason = self._is_stage_healthy(
        rollout.current_stage, metrics, tier_id=tier_id, # 항상 True
    )
    if not is_healthy:
        return False

# After — LiveCanaryEvaluator 단일 경로
if not force:
    live_check = self._check_live_canary_evaluation(rollout, tier_id=tier_id)
    if live_check is not None and not live_check:
        logger.warning(
            "canary_rollout.promotion_blocked",
            rollout_id=rollout.id,
        )
        return False
```

**제거 대상**: `_collect_stage_metrics()`, `_is_stage_healthy()`, `get_stage_metrics()`
**영향 확인**: `tasks/` 폴더에서 이 메서드들의 직접 호출이 없음.
테스트에서 `_collect_stage_metrics`를 mock하는 곳은
`test_canary_tier_floor_wiring.py:246,285` 2곳뿐 — 테스트도 함께 수정.

---

## 4. LiveCanaryEvaluator 구현

### 4.1 클래스 정의

```python
# services/config_shadow/evaluators/live_canary.py

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from selfhealing.services.canary.models import PassCriteria
from selfhealing.services.config_shadow.metrics_provider import TimeSeriesMetricsProvider
from selfhealing.services.config_shadow.models import EvaluationContext, EvaluatorResult

logger = logging.getLogger(__name__)


class LiveCanaryEvaluator:
    """
    Canary 노드의 실시간 메트릭을 기반으로 설정 변경의 실제 영향을 평가한다.

    ConfigEvaluator Protocol을 구현하며, EvaluationContext의
    time_window_seconds + labels를 사용하여 TimeSeriesMetricsProvider에서
    실시간 데이터를 조회한다.

    PassCriteria의 임계값 데이터를 읽어 판정 기준으로 사용한다.
    """

    def __init__(
        self,
        metrics_provider: TimeSeriesMetricsProvider,
        pass_criteria: PassCriteria | None = None,
    ) -> None:
        self._metrics = metrics_provider
        self._criteria = pass_criteria or PassCriteria()

    @property
    def name(self) -> str:
        return "live_canary"

    @property
    def event_types(self) -> list[str]:
        return ["canary_metrics"]

    def evaluate(self, context: EvaluationContext) -> EvaluatorResult:
        """
        실시간 메트릭을 조회하여 baseline(기존 클러스터)과
        candidate(Canary 클러스터)의 동작을 비교한다.

        PassCriteria의 임계값을 판정 기준으로 사용한다.
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(seconds=context.time_window_seconds)
        warnings: list[str] = []
        criteria = self._criteria

        # 1. 가중치 기반 에러율 스칼라 조회 (Q2 해결)
        baseline_error = self._metrics.query_error_rate_aggregated(
            service_name=context.service_name,
            start=start, end=now,
            labels=context.baseline_labels,
        )
        candidate_error = self._metrics.query_error_rate_aggregated(
            service_name=context.service_name,
            start=start, end=now,
            labels=context.candidate_labels,
        )
        error_delta = candidate_error - baseline_error

        # 2. 총 요청 수 조회
        candidate_request_count = self._metrics.query_request_count(
            service_name=context.service_name,
            start=start, end=now,
            labels=context.candidate_labels,
        )

        # 3. Latency P95/P99 스칼라 조회 (Q5 해결)
        baseline_p95 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start, end=now, percentile=0.95,
            labels=context.baseline_labels,
        )
        candidate_p95 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start, end=now, percentile=0.95,
            labels=context.candidate_labels,
        )
        baseline_p99 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start, end=now, percentile=0.99,
            labels=context.baseline_labels,
        )
        candidate_p99 = self._metrics.query_latency_aggregated(
            service_name=context.service_name,
            start=start, end=now, percentile=0.99,
            labels=context.candidate_labels,
        )

        # 4. 데이터 충분성 → confidence
        confidence, conf_warnings = self._calculate_confidence(
            candidate_request_count,
        )
        warnings.extend(conf_warnings)

        # 5. 통과 판정 (PassCriteria 임계값 사용)
        passed = True
        details_parts: list[str] = []

        # 5a. 에러율 절대 임계값
        if candidate_error > criteria.error_rate_absolute_max:
            passed = False
            details_parts.append(
                f"Canary error rate {candidate_error:.3f} > "
                f"threshold {criteria.error_rate_absolute_max:.3f}"
            )

        # 5b. 에러율 증가 임계값
        if error_delta > criteria.error_rate_increase_max:
            passed = False
            details_parts.append(
                f"Error rate increase {error_delta:.3f} > "
                f"threshold {criteria.error_rate_increase_max:.3f}"
            )

        # 5c. P95 Latency 절대 증가
        p95_delta = candidate_p95 - baseline_p95
        if p95_delta > criteria.latency_p95_delta_ms:
            passed = False
            details_parts.append(
                f"P95 latency delta {p95_delta:.1f}ms > "
                f"threshold {criteria.latency_p95_delta_ms:.1f}ms"
            )

        # 5d. P99 Latency 비율 증가
        if baseline_p99 > 0:
            p99_pct = (candidate_p99 - baseline_p99) / baseline_p99
            if p99_pct > criteria.latency_p99_delta_pct:
                passed = False
                details_parts.append(
                    f"P99 latency increased by {p99_pct:.1%} > "
                    f"threshold {criteria.latency_p99_delta_pct:.1%}"
                )

        if passed:
            details_parts.append(
                f"Canary healthy: error_rate={candidate_error:.3f}, "
                f"delta={error_delta:+.3f}, "
                f"p95={candidate_p95:.1f}ms, p99={candidate_p99:.1f}ms, "
                f"requests={candidate_request_count}"
            )

        return EvaluatorResult(
            evaluator_name=self.name,
            passed=passed,
            confidence_score=confidence,
            baseline_metrics={
                "error_rate": baseline_error,
                "latency_p95_ms": baseline_p95,
                "latency_p99_ms": baseline_p99,
            },
            candidate_metrics={
                "error_rate": candidate_error,
                "request_count": candidate_request_count,
                "latency_p95_ms": candidate_p95,
                "latency_p99_ms": candidate_p99,
            },
            delta={
                "error_rate_delta": error_delta,
                "p95_delta_ms": p95_delta,
                "p99_delta_pct": (candidate_p99 - baseline_p99) / baseline_p99
                if baseline_p99 > 0
                else 0.0,
            },
            details="; ".join(details_parts),
            warnings=warnings,
        )

    def _calculate_confidence(
        self,
        request_count: int,
    ) -> tuple[float, list[str]]:
        """요청량 기반 신뢰도 계산."""
        warnings: list[str] = []
        min_requests = self._criteria.min_requests_required

        if request_count < min_requests:
            warnings.append(
                f"Low request volume ({request_count} < {min_requests}). "
                f"Confidence reduced."
            )
            if request_count == 0:
                return 0.1, warnings
            return 0.4, warnings

        if request_count < min_requests * 5:
            return 0.7, warnings

        return 0.95, warnings
```

### 4.2 설계 결정

**EvaluationContext를 통한 다형성**:
- Shadow Evaluator는 `context.events`를 사용
- Live Evaluator는 `context.time_window_seconds` + `context.baseline_labels` + `context.candidate_labels`를 사용
- Protocol 시그니처가 하나이므로 LSP 위반 없음

**PassCriteria 임계값 주입**:
- `LiveCanaryEvaluator.__init__(pass_criteria=)` 로 주입
- 기존 `PassCriteria.for_tier()`, `apply_tier_floor()`로 구성된 임계값을 그대로 활용
- 판정 로직이 `LiveCanaryEvaluator` 한 곳에만 존재 (Single Source of Truth)

**Provider 스칼라 위임**:
- 에러율: `query_error_rate_aggregated()` → `sum(rate(errors)) / sum(rate(requests))`
- Latency: `query_latency_aggregated(percentile=)` → `histogram_quantile()`
- 클라이언트에서 시계열을 평균하는 수학적 오류 원천 차단

---

## 5. promote() 연동

### 5.1 _check_live_canary_evaluation

```python
# services/canary/service.py — promote() 내부에 추가

def _check_live_canary_evaluation(
    self,
    rollout: CanaryRollout,
    tier_id: str | None = None,
) -> bool | None:
    """
    Canary 노드의 실시간 메트릭을 평가한다.

    Returns:
        True: 통과 (승격 가능)
        False: 차단 (승격 불가)
        None: 비활성화 또는 오류 (체크 생략)
    """
    try:
        from selfhealing.settings.config_shadow import get_config_shadow_settings
        settings = get_config_shadow_settings()
        if not settings.live_evaluation_enabled:
            return None
    except ImportError:
        return None

    current_stage = rollout.current_stage
    if not current_stage:
        return None

    try:
        from selfhealing.services.canary.models import apply_tier_floor
        from selfhealing.services.config_shadow.evaluators.live_canary import (
            LiveCanaryEvaluator,
        )
        from selfhealing.services.config_shadow.metrics_provider import (
            get_metrics_provider,
        )
        from selfhealing.services.config_shadow.models import EvaluationContext

        # 티어별 최소 보안 기준 적용
        criteria = current_stage.pass_criteria
        if tier_id:
            criteria = apply_tier_floor(criteria, tier_id)

        evaluator = LiveCanaryEvaluator(
            metrics_provider=get_metrics_provider(),
            pass_criteria=criteria,
        )

        context = EvaluationContext(
            baseline_config=rollout.previous_values,
            candidate_config=rollout.new_values,
            service_name=rollout.config_type,
            time_window_seconds=criteria.evaluation_window_seconds,
            baseline_labels=_BASELINE_LABELS.copy(),
            candidate_labels={
                **_CANDIDATE_LABELS_BASE,
                "cluster": current_stage.clusters[0]
                if current_stage.clusters
                else _CANDIDATE_LABELS_BASE["track"],
            },
        )

        result = evaluator.evaluate(context)

        if result.passed:
            logger.info(
                "canary_promote.live_evaluation_passed",
                rollout_id=rollout.id,
                confidence=result.confidence_score,
            )
            return True

        logger.warning(
            "canary_promote.live_evaluation_failed",
            rollout_id=rollout.id,
            details=result.details,
            confidence=result.confidence_score,
        )
        return False

    except ImportError:
        return None
    except Exception as e:
        logger.error("canary_promote.live_evaluation_error", error=e, exc_info=True)
        return None  # Fail-Open
```

### 5.2 promote() 변경

```python
def promote(self, rollout_id, force=False, bypass_governance=False, ...):
    ...
    # 거버넌스 체크 (기존 로직 유지, service.py:406-457)
    ...

    # === 변경: LiveCanaryEvaluator 단일 경로 ===
    if not force:
        live_check = self._check_live_canary_evaluation(rollout, tier_id=tier_id)
        if live_check is not None and not live_check:
            logger.warning(
                "canary_rollout.promotion_blocked",
                rollout_id=rollout.id,
            )
            return False

    # 다음 단계로 이동 (기존 로직 유지, service.py:475-508)
    ...
```

**삽입 위치 결정 이유**:
거버넌스 체크(시스템 전체 건강) 이후, 단계 이동 이전에 배치한다.
Canary 노드가 불건강하면 조기 반환하여 불필요한 상태 전이를 방지한다.

---

## 6. Settings

### 6.1 ConfigShadowSettings 확장

```python
# settings/config_shadow.py에 추가

live_evaluation_enabled: bool = Field(
    default=False,
    description=(
        "promote() 시 Live Canary Evaluation 활성화 여부. "
        "TimeSeriesMetricsProvider 구현체가 등록된 후 True로 전환."
    ),
)
```

**`live_evaluation_enabled=False` 기본값 이유**:
Live Canary Evaluator는 `TimeSeriesMetricsProvider`의 실제 구현체
(PrometheusTimeSeriesProvider 등)가 있어야 동작한다.
Mock만 있는 상태에서 True로 켜면 무의미하므로,
운영 환경에서 Provider를 등록한 후 명시적으로 활성화한다.

---

## 7. Shadow Evaluation(300)과의 역할 분담

| 관점 | Shadow Evaluation (300) | Live Canary Evaluation (301) |
|------|------------------------|------------------------------|
| **시점** | start_rollout() 전 | promote() 전 |
| **데이터 소스** | EventJournal (과거 이벤트 리플레이) | Prometheus/Datadog (실시간 메트릭) |
| **질문** | "이 설정으로 바꾸면 과거에 어땠을까?" | "지금 Canary에서 실제로 어떤가?" |
| **Protocol** | ConfigEvaluator (EvaluationContext) | ConfigEvaluator (EvaluationContext) |
| **Context 사용** | `context.events` | `context.time_window_seconds` + `context.*_labels` |
| **Fail 모드** | Fail-Open | Fail-Open |
| **bypass** | bypass_shadow + reason | force=True (기존 promote 패턴) |
| **메트릭 검증** | 에러율, CB open count | 에러율 + P95/P99 Latency |
| **집계 방식** | 이벤트 카운트 | Provider 스칼라 (가중치 기반) |

---

## 8. 전체 플로우 (300 + 301 통합)

```
운영자                    Canary Service              Shadow/Live Evaluator
  │                           │                            │
  ├─ create_rollout() ───────▶│                            │
  │                           │                            │
  ├─ evaluate_for_rollout() ──┼───────── Shadow(300) ─────▶│
  │   (과거 이벤트 리플레이)  │                            ├─ context.events 사용
  │◀── passed ────────────────┼────────────────────────────│
  │                           │                            │
  ├─ start_rollout() ────────▶│                            │
  │                           ├─ Shadow Gate(300) 체크     │
  │                           ├─ 클러스터 적용             │
  │                           │                            │
  │   [bake time 경과]        │                            │
  │                           │                            │
  ├─ promote() ──────────────▶│                            │
  │   (또는 auto-promote)     ├─ Governance 체크           │
  │                           ├─ Live Evaluation(301) ────▶│
  │                           │   (실시간 메트릭)          ├─ context.*_labels 사용
  │                           │                            ├─ Prometheus 스칼라 조회
  │                           │                            ├─ 에러율 + P95/P99 검증
  │                           │◀── EvaluatorResult ────────│
  │                           ├─ 다음 단계 적용            │
  │◀── 결과 ──────────────────│                            │
```

---

## 9. 구현 순서

Q1-Q5를 포함한 전체 구현 순서:

```
Step 1: EvaluationContext 모델 정의 (models.py — 신규, 충돌 없음)
   ↓
Step 2: ConfigEvaluator Protocol 시그니처 변경 (evaluators/__init__.py)
   ↓
Step 3: 기존 Evaluator 2개 마이그레이션 (circuit_breaker.py, error_budget.py)
   ↓  ← 테스트 실행: 기존 기능 검증
Step 4: ShadowEvaluatorService._run_evaluation() 호출부 수정 (service.py)
   ↓
Step 5: TimeSeriesMetricsProvider Protocol 확장 + MockProvider 업데이트 (metrics_provider.py)
   ↓
Step 6: PassCriteria.evaluate() 제거 → 순수 DTO (canary/models.py)
   ↓
Step 7: LiveCanaryEvaluator 구현 (evaluators/live_canary.py — 신규)
   ↓
Step 8: promote() 플로우 단순화 + _check_live_canary_evaluation (canary/service.py)
   ↓  ← 테스트 실행: canary 기능 검증
Step 9: Settings 추가 (settings/config_shadow.py)
   ↓
Step 10: 테스트 작성/수정
```

---

## 10. 선행 조건

| 조건 | 상태 | 비고 |
|------|------|------|
| ConfigEvaluator Protocol | ✅ 구현 완료 → 리팩토링 대상 (Q1) | evaluators/__init__.py:15-45 |
| TimeSeriesMetricsProvider Protocol | ✅ 정의 완료 → 확장 대상 (Q2/Q4/Q5) | metrics_provider.py:15-53 |
| PrometheusTimeSeriesProvider | ❌ 미구현 | 별도 구현 필요 |
| PassCriteria | ✅ 구현 완료 → DTO 격하 대상 (Q3) | canary/models.py:82-166 |
| Shadow Gate (300) | ✅ 구현 완료 | 300번 문서 |

**구현 순서**: Q1(Protocol 리팩토링) → Q2/Q4/Q5(Provider 확장) → Q3(PassCriteria DTO) → 301(LiveCanaryEvaluator) → Prometheus Provider

---

## 11. 파일 변경 요약

| 파일 | 변경 유형 | 내용 | 관련 |
|------|-----------|------|------|
| `services/config_shadow/models.py` | 수정 | `EvaluationContext` 추가 | Q1 |
| `services/config_shadow/evaluators/__init__.py` | 수정 | Protocol 시그니처 변경 | Q1 |
| `services/config_shadow/evaluators/circuit_breaker.py` | 수정 | `evaluate(context)` 마이그레이션 | Q1 |
| `services/config_shadow/evaluators/error_budget.py` | 수정 | `evaluate(context)` 마이그레이션 | Q1 |
| `services/config_shadow/service.py` | 수정 | `_run_evaluation()` 호출부 변경 | Q1 |
| `services/config_shadow/metrics_provider.py` | 수정 | Protocol 확장 + Mock 업데이트 | Q2/Q4/Q5 |
| `services/canary/models.py` | 수정 | `PassCriteria.evaluate()` 제거 | Q3 |
| `services/canary/service.py` | 수정 | `promote()` 단순화 + `_check_live_canary_evaluation()` | Q3/301 |
| `services/config_shadow/evaluators/live_canary.py` | 신규 | LiveCanaryEvaluator 구현 | 301 |
| `settings/config_shadow.py` | 수정 | `live_evaluation_enabled` 설정 추가 | 301 |

기존 `promote()` 호출부는 `force=True`로 Live Evaluation을 우회할 수 있으므로
**호출부 변경 불필요**.

---

## 12. Post-Review Fixes

구현 후 코드 리뷰에서 발견된 6가지 이슈와 적용된 수정사항.

### 12.1 collect_metrics() Regression (Critical)

`_collect_stage_metrics()` 제거 시 공개 메서드 `collect_metrics()`가 무조건 `[]`을 반환하게 됨.
호출처 4곳(canary_watchdog.py:675, canary views:528, 테스트 2곳)이 영향을 받음.

**수정**: `collect_metrics()`를 `LiveCanaryEvaluator`를 통해 실시간 메트릭을 조회하고
`CanaryMetrics` 형태로 변환하여 반환하도록 재구현.

### 12.2 Exception Handling (Warning)

`_check_live_canary_evaluation()`의 `except Exception as e`에서 `logger.warning` 사용.
프로그래밍 오류가 묻힐 수 있음.

**수정**: `logger.warning` → `logger.error` + `exc_info=True`로 변경.
Fail-open 패턴은 프로젝트 컨벤션(Shadow eval, Chaos guard 등)에 부합하므로 유지.

### 12.3 Hardcoded Labels (Warning)

`baseline_labels={"track": "stable"}` 등이 메서드 내부에 하드코딩.

**수정**: 모듈 상수 `_BASELINE_LABELS`, `_CANDIDATE_LABELS_BASE`로 추출.

### 12.4 MockTimeSeriesProvider Label-Aware (Warning)

Mock이 `labels` 파라미터를 무시하여 baseline/candidate 분리 테스트 불가.

**수정**: `_resolve_scalar()` 도입으로 label-aware 키 조회 구현.
`"{service}:{metric}:{k}={v},..."` 형식의 labeled 키를 우선 조회하고,
없으면 unlabeled 키로 폴백 (하위 호환).

### 12.5 P99 Delta Duplicated Computation (Suggestion)

`p99_pct` 계산이 판정 블록(line 147)과 delta dict(line 181-183)에서 중복.

**수정**: 판정 블록 전에 한 번 계산하고 delta dict에서 재사용. P95와 동일 패턴.

### 12.6 Negative Threshold Tests (Suggestion)

Mock의 label 미지원으로 인해 `error_rate_increase_max=-0.01` 같은 비현실적 값으로 테스트.

**수정**: 12.4의 label-aware Mock 활용으로 현실적 시나리오 테스트로 전환.
예: baseline=0.01, candidate=0.03 → delta=0.02 > 0.01 (기본 임계값).
