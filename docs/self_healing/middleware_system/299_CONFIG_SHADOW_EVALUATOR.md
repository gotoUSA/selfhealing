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

    # 출력
    report: EvaluationReport | None = None
    error_message: str = ""
```

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
        confidence = self._calculate_confidence(events)

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
            details=f"CB 개방 {baseline_opens.open_count}회 → {candidate_opens.open_count}회 ({delta_pct:+.1f}%)",
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

    for event in events:
        if state == "open":
            # recovery_timeout 경과 확인 — service.py:245-248 로직
            if opened_at and (event.timestamp - opened_at).total_seconds() >= recovery_timeout:
                state = "half_open"

        if event.event_type == "circuit_breaker_opened":
            # 실패 이벤트: failure_window에 추가
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
                total_open_seconds += (event.timestamp - opened_at).total_seconds()
            state = "closed"
            failure_window.clear()
            opened_at = None
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

#### Confidence 계산

```python
def _calculate_confidence(self, events: list[JournalEntry]) -> float:
    """이벤트 충분성 기반 신뢰도 계산."""
    cb_events = [e for e in events if e.event_type.startswith("circuit_breaker_")]

    # decision_engine.py:258-311의 sample count confidence 패턴 차용
    if len(cb_events) < 5:
        return 0.2      # 데이터 부족
    elif len(cb_events) < 20:
        return 0.5      # 중간
    elif len(cb_events) < 50:
        return 0.8      # 양호
    else:
        return 0.95     # 충분
```

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
    warning_threshold = config.get("warning_threshold_percent", 20)
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

### 6.1 서비스 클래스

```python
class ShadowEvaluatorService:
    """Config Shadow Evaluation 서비스."""

    def __init__(
        self,
        journal_repo: EventJournalRepository | None = None,
        evaluators: list[ConfigEvaluator] | None = None,
    ):
        self._journal_repo = journal_repo or ProviderRegistry.get_event_journal_repo()
        self._evaluators = evaluators or self._default_evaluators()

    def _default_evaluators(self) -> list[ConfigEvaluator]:
        return [
            CircuitBreakerEvaluator(),
            ErrorBudgetEvaluator(),
        ]

    def evaluate(
        self,
        config_type: str,
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
        service_name: str = "",
        time_window_hours: int = 336,
        rollout_id: str | None = None,
    ) -> ShadowEvaluation:
        """
        설정 변경에 대한 Shadow Evaluation을 실행한다.

        Args:
            config_type: 설정 종류 ("circuit_breaker", "error_budget")
            baseline_config: 현재 설정
            candidate_config: 후보 설정
            service_name: 대상 서비스 (비어있으면 전체)
            time_window_hours: 분석 시간 범위 (기본 14일)
            rollout_id: 연결할 Canary rollout ID (선택)

        Returns:
            ShadowEvaluation 결과
        """
        evaluation = ShadowEvaluation(
            evaluation_id=str(uuid4())[:8],
            rollout_id=rollout_id,
            status=EvaluationStatus.RUNNING,
            created_at=utc_now(),
            config_type=config_type,
            baseline_config=baseline_config,
            candidate_config=candidate_config,
            service_name=service_name,
            time_window_hours=time_window_hours,
        )

        try:
            # 1. EventJournal에서 이벤트 조회
            end_time = utc_now()
            start_time = end_time - timedelta(hours=time_window_hours)

            filter = JournalQueryFilter(
                service_name=service_name or None,
                start_time=start_time,
                end_time=end_time,
            )
            events = self._journal_repo.query(filter)

            # 2. 해당 config_type의 Evaluator 찾기
            evaluator = self._find_evaluator(config_type)
            if evaluator is None:
                evaluation.status = EvaluationStatus.FAILED
                evaluation.error_message = f"No evaluator for config_type: {config_type}"
                return evaluation

            # 3. 시뮬레이션 실행
            result = evaluator.evaluate(events, baseline_config, candidate_config)

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

    def _find_evaluator(self, config_type: str) -> ConfigEvaluator | None:
        for evaluator in self._evaluators:
            if evaluator.name == config_type:
                return evaluator
        return None
```

### 6.2 싱글톤 패턴

기존 서비스 싱글톤 패턴(`services/replay_service/service.py:764-770`)을 따른다:

```python
_service: ShadowEvaluatorService | None = None


def get_shadow_evaluator_service() -> ShadowEvaluatorService:
    global _service
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
        result = self.evaluate(
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
└── evaluators/
    ├── __init__.py                  # ConfigEvaluator Protocol
    ├── circuit_breaker.py           # CircuitBreakerEvaluator
    └── error_budget.py              # ErrorBudgetEvaluator
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
