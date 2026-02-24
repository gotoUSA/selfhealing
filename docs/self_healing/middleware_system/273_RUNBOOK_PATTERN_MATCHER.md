# 273. Pattern Matcher 설계

## 1. 목적

현재 시스템 증상(메트릭 조합, 이벤트 시퀀스)을 `RunbookRegistry`에 등록된 런북의 trigger 조건과 대조하여, 실행할 런북을 자동으로 선택한다.

## 2. 현재 시스템의 감지 경로 — 코드 근거

감지된 이벤트가 어디서 발생하는지:

| 이벤트 소스 | EventType | 코드 위치 |
|---|---|---|
| Circuit Breaker 열림 | `CIRCUIT_BREAKER_OPENED` | `services/event_bus/bus/__init__.py` — `EventType` enum |
| Emergency 활성화 | `EMERGENCY_ACTIVATED` | 동일 |
| Error Budget 위험 | `ERROR_BUDGET_CRITICAL` | 동일 |
| SLA 위반 | `THROTTLE_SLA_CRITICAL` | 동일 |
| 보안 위반 감지 | `SECURITY_VIOLATION_DETECTED` | 동일 |
| Saga Step 실패 | `SAGA_STEP_FAILED` | 동일 |
| Rate Limit 429 | `RATE_LIMIT_429` | 동일 |
| Load Shedding 변경 | `LOAD_SHEDDING_LEVEL_CHANGED` | 동일 |

현재 이 이벤트들은 각 컴포넌트가 개별 구독하지만, **"복합 조건"을 평가하여 런북을 선택하는 구독자는 없다.**

## 3. 데이터 모델

### 3.1 PatternCondition — 트리거 조건

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConditionOperator(str, Enum):
    """조건 비교 연산자."""
    GT = "gt"          # >
    GTE = "gte"        # >=
    LT = "lt"          # <
    LTE = "lte"        # <=
    EQ = "eq"          # ==
    NEQ = "neq"        # !=
    IN = "in"          # in [...]
    CONTAINS = "contains"  # 문자열 포함


@dataclass
class MetricCondition:
    """단일 메트릭 조건.

    state-based 조건으로 설계 — 현재 상태를 판단한다.
    """
    metric_name: str           # "error_rate", "db_pool_usage", "latency_p99_ms" 등
    operator: ConditionOperator
    threshold: float | str | list[str]

    def evaluate(self, current_value: float | str) -> bool:
        """현재 값이 조건을 만족하는지 평가."""
        ...


@dataclass
class EventCondition:
    """특정 EventType 발생 조건."""
    event_type: str            # EventType.value (예: "circuit_breaker_opened")
    source_filter: str | None = None  # 이벤트 소스 필터 (예: "payment_service")
    data_filter: dict[str, Any] = field(default_factory=dict)  # 이벤트 data 필드 필터


@dataclass
class PatternCondition:
    """런북 트리거 조건 — AND 조합.

    metric_conditions: 모든 메트릭 조건이 동시 만족해야 함 (AND)
    event_conditions: 하나 이상의 이벤트가 발생해야 함 (OR trigger)

    설계 근거:
    - 메트릭 조건은 AND — "에러율 5% 이상 AND DB 풀 90% 이상"
    - 이벤트 조건은 OR trigger — CB 열림 또는 Emergency 발생 시 평가 시작
    """
    metric_conditions: list[MetricCondition] = field(default_factory=list)
    event_conditions: list[EventCondition] = field(default_factory=list)
    min_duration_seconds: int = 0  # 조건 지속 시간 (짧은 스파이크 필터링)

    def evaluate(self, metrics: dict[str, float], triggered_event: str | None = None) -> bool:
        """모든 조건을 평가하여 매칭 여부 반환."""
        ...
```

### 3.2 MatchResult — 매칭 결과

```python
@dataclass
class MatchResult:
    """패턴 매칭 결과."""
    runbook_id: str
    confidence: float         # 0.0 ~ 1.0
    matched_conditions: list[str]  # 매칭된 조건 설명
    historical_success_rate: float | None  # 과거 실행 성공률
    similar_pattern_count: int  # LearningService에서 유사 패턴 수
    triggered_by_event: str | None  # 트리거 이벤트
    metric_snapshot: dict[str, float]  # 매칭 시점 메트릭 스냅샷
```

## 4. PatternMatcher 클래스

```python
class PatternMatcher:
    """현재 증상을 런북 트리거 조건과 대조.

    EventBus에서 이벤트를 구독하고, 이벤트 발생 시
    메트릭을 수집하여 등록된 런북의 조건을 평가한다.

    연동:
    - EventBus: 이벤트 구독 (SelfHealingEventBus.subscribe)
    - RunbookRegistry: 활성 런북 조회
    - LearningService: 과거 유사 패턴 조회 (confidence 보강)
    - MetricsProvider: 현재 메트릭 수집
    """

    # 구독할 이벤트 타입 목록 (trigger 평가 시작점)
    TRIGGER_EVENTS: list[str] = [
        "circuit_breaker_opened",
        "emergency_activated",
        "error_budget_critical",
        "error_budget_warning",
        "throttle_sla_critical",
        "load_shedding_level_changed",
        "rate_limit_429",
    ]

    def __init__(
        self,
        registry: RunbookRegistry,
        learning_service: LearningService | None = None,
        metrics_provider: MetricsProvider | None = None,
    ):
        """
        Args:
            registry: 런북 레지스트리 (활성 런북 목록 조회)
            learning_service: 패턴 학습 서비스 (과거 사례 조회용, optional)
            metrics_provider: 메트릭 제공자 (현재 시스템 상태 수집)
                AutoRollbackGuard.MetricsProvider 프로토콜 재사용:
                    get_error_rate() -> float
                    get_latency_p99() -> float
        """
        ...

    def initialize(self) -> None:
        """EventBus에 이벤트 구독 등록.

        SelfHealingEventBus.subscribe(event_type, handler, priority)를 사용.
        priority=EventPriority.NORMAL — 다른 핸들러와 동등.
        """
        ...

    def _on_event(self, event: SelfHealingEvent) -> None:
        """이벤트 수신 시 콜백.

        1. 현재 메트릭 수집
        2. 등록된 모든 활성 런북의 trigger 조건 평가
        3. 매칭된 런북이 있으면 MatchResult 생성
        4. 최고 confidence 런북 선택 → service.py로 전달
        """
        ...

    def evaluate_all(
        self,
        metrics: dict[str, float],
        triggered_event: str | None = None,
    ) -> list[MatchResult]:
        """등록된 모든 런북의 트리거 조건을 평가.

        Args:
            metrics: 현재 메트릭 스냅샷
            triggered_event: 트리거 이벤트 타입 (optional)

        Returns:
            매칭된 런북 목록 (confidence 내림차순)
        """
        ...

    def _calculate_confidence(
        self,
        runbook_id: str,
        matched_conditions: list[str],
        total_conditions: int,
    ) -> float:
        """매칭 confidence 계산.

        기본 confidence = matched_conditions / total_conditions

        LearningService 연동 시 보강:
        - LearningService.get_patterns(pattern_type=PatternType.FAILURE)
          과거 유사 패턴이 있으면 confidence를 가중
        - 과거 런북 실행 성공률 반영
        """
        ...
```

## 5. 메트릭 수집 — MetricsProvider 재사용

`AutoRollbackGuard`에 정의된 `MetricsProvider` 프로토콜을 재사용한다:

```python
# core/auto_rollback_guard.py에 이미 정의됨
class MetricsProvider(Protocol):
    def get_error_rate(self) -> float: ...
    def get_latency_p99(self) -> float: ...
    def get_throughput(self) -> float: ...
```

런북에서 추가 메트릭이 필요하면 (예: `db_pool_usage`, `cache_hit_rate`), `MetricsProvider`를 확장하거나 별도 메트릭 소스를 주입한다.

## 6. LearningService 연동

`LearningService.get_patterns()` 호출로 과거 유사 패턴을 조회한다:

```python
# services/learning/service.py (기존 코드)
def get_patterns(
    self,
    pattern_type: PatternType | None = None,
    min_confidence: float = 0.0,
) -> list[LearningPattern]:
    """학습된 패턴 조회."""
```

`PatternType.FAILURE` 패턴 중 현재 증상과 유사한 것을 찾아 confidence를 보강한다. 유사도 판단 기준:

- `LearningPattern.features`의 키-값이 현재 메트릭 조건과 겹치는 정도
- `LearningPattern.occurrence_count` — 빈번할수록 신뢰도 높음
- `LearningPattern.confidence` — 기존 학습 신뢰도

## 7. 중복 트리거 방지

같은 장애에 대해 런북이 여러 번 트리거되지 않도록:

1. **쿨다운 타이머** — 동일 런북은 마지막 실행 후 `cooldown_seconds` 내 재트리거 방지
2. **진행 중 체크** — `DistributedRecoveryLock`이 잡혀있으면 트리거 스킵
3. **IdempotencyKey** — `IdempotencyKey.for_recovery_action(action_type, target, region, session_id)` 활용

## 8. 참조

- `SelfHealingEventBus`: `services/event_bus/bus/__init__.py` — `EventType`, `subscribe()`, `emit()`
- `LearningService`: `services/learning/service.py` — `get_patterns()`, `LearningPattern`
- `PatternType`: `services/learning/models.py` — `FAILURE`, `RECOVERY`, `ANOMALY`
- `MetricsProvider`: `core/auto_rollback_guard.py` — Protocol
- `AutoRollbackGuard._assess_degradation()`: 에러율/레이턴시 → `RollbackSeverity` 판정 로직 참고
