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
    REGEX = "regex"    # 정규식 매칭 (=~ 연산자, PromQL 호환)


@dataclass
class LabelFilter:
    """메트릭 라벨 필터 — MSA 환경에서 대상 서비스/엔드포인트를 특정.

    PromQL의 라벨 셀렉터 모델과 일치하도록 설계.
    EventCondition에 source_filter/data_filter가 있는 것처럼,
    MetricCondition에도 라벨 기반 타겟팅이 필요하다.

    코드 근거:
    - PrometheusMetricsAdapter._query_metric()에서 PromQL의 {job="...", status=~"5.."}
      라벨 필터를 직접 사용 (adapters/metrics/auto_tuning_adapter.py L169-L171)
    - IdempotencyKey.for_recovery_action()이 target(서비스명)을 구분하는 것과 동일 원리
      (services/idempotency/models.py L488)

    Example:
        # 결제 서비스의 checkout 엔드포인트 에러율만 필터링
        LabelFilter(key="service", operator=ConditionOperator.EQ, value="payment")
        LabelFilter(key="endpoint", operator=ConditionOperator.REGEX, value="/api/v[12]/.*")

        # 여러 리전을 하나로 묶어 평가
        LabelFilter(key="region", operator=ConditionOperator.IN, value=["ap-northeast-2", "us-west-2"])
    """
    key: str                                        # 라벨 키 (예: "service", "endpoint", "region")
    operator: ConditionOperator                     # EQ, NEQ, IN, REGEX 등
    value: str | list[str]                          # 값 또는 정규식 패턴


@dataclass
class MetricCondition:
    """단일 메트릭 조건.

    state-based 조건으로 설계 — 현재 상태를 판단한다.
    """
    metric_name: str           # "error_rate", "db_pool_usage", "latency_p99_ms" 등
    operator: ConditionOperator
    threshold: float | str | list[str]
    labels: list[LabelFilter] = field(default_factory=list)  # MSA 라벨 필터링

    # Hysteresis — Flapping/Chattering 방지
    # 임계치 근처에서 미세하게 오르락내리락하는 경우를 처리한다.
    # clear_threshold가 설정되면, 진입 조건(threshold)과 해제 조건(clear_threshold)이
    # 다른 값을 사용하여 잦은 상태 전환을 방지한다.
    #
    # 코드 근거:
    # - AutoRollbackGuard._consecutive_failures 카운터가 단일 실패가 아닌
    #   연속 실패를 요구하는 것이 사실상 Flapping 방어 (core/auto_rollback_guard.py L248)
    # - LearningService BlacklistReason.FLAPPING이 파라미터 플래핑 감지를 위해
    #   이미 정의되어 있음 (services/learning/models.py L44)
    clear_threshold: float | None = None       # None이면 threshold와 동일 (해제 조건)
    grace_period_seconds: int = 0              # 짧은 단절 허용 시간

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

    중요: 메트릭 조건이 AND이므로 조건이 하나라도 미충족이면 매칭 실패(False).
    부분 매칭(Partial Match)은 허용하지 않는다. evaluate()가 True를 반환해야만
    후보로 올라가며, 이후 confidence는 별도 Factor로 산정한다.
    → §4.3 Confidence 계산 참조.
    """
    metric_conditions: list[MetricCondition] = field(default_factory=list)
    event_conditions: list[EventCondition] = field(default_factory=list)
    min_duration_seconds: int = 0  # 조건 지속 시간 (짧은 스파이크 필터링)

    def evaluate(
        self,
        metrics: dict[str, float],
        triggered_event: str | None = None,
        event_source: str | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> bool:
        """모든 조건을 평가하여 매칭 여부 반환.

        AND 게이트: 모든 메트릭 조건이 충족되어야 True.
        OR 트리거: event_conditions 중 하나라도 매칭되면 트리거 인정.

        이벤트 조건이 있는 런북은 Reactive 경로(triggered_event != None)에서만
        매칭된다. Proactive 경로(triggered_event=None)에서는 이벤트 조건 있는
        런북은 반드시 False를 반환한다.
        """
        # 1단계: 메트릭 AND 게이트 — 하나라도 미충족이면 즉시 False
        for mc in self.metric_conditions:
            value = metrics.get(mc.metric_name)
            if value is None or not mc.evaluate(value):
                return False

        # 2단계: 이벤트 OR 트리거
        if self.event_conditions:
            if triggered_event is None:
                # 이벤트 조건이 있는 런북은 Proactive 경로에서 매칭 불가
                return False
            return any(
                _event_condition_matches(ec, triggered_event, event_source, event_data)
                for ec in self.event_conditions
            )

        # 이벤트 조건 없음 — 메트릭만으로 매칭 (Proactive/Reactive 모두 허용)
        return True


def _event_condition_matches(
    ec: EventCondition,
    triggered_event: str,
    event_source: str | None,
    event_data: dict[str, Any] | None,
) -> bool:
    """단일 EventCondition과 이벤트를 대조.

    event_type 일치 + source_filter(None이면 무조건 통과) +
    data_filter 키-값 AND 검사.
    """
    if ec.event_type != triggered_event:
        return False
    if ec.source_filter is not None:
        if event_source is None or event_source != ec.source_filter:
            return False
    if ec.data_filter:
        for key, expected in ec.data_filter.items():
            if (event_data or {}).get(key) != expected:
                return False
    return True
```

### 3.2 MatchResult — 매칭 결과

```python
@dataclass
class MatchResult:
    """패턴 매칭 결과.

    event_context 필드는 원본 SelfHealingEvent의 데이터를 보존하여
    275번 Executor에서 런북 Step 파라미터 치환에 사용한다.

    코드 근거 — 이벤트 data에 이미 service_name이 표준으로 포함:
    - emit_circuit_breaker_state_changed(): data={"service_name": ..., "new_state": ...}
      (services/event_bus/bus/__init__.py L993-L997)
    - EMERGENCY_ACTIVATED: data={"level": ..., "reason": ..., "incident_id": ...}
      (services/security/orchestrator.py L154-L158)

    코드 근거 — 기존 템플릿 치환 패턴:
    - IncidentTimeline._describe_node()에서 format_map() + _SafeFormatDict 사용
      (services/correlation_engine/incident_timeline.py L979-L992)
    - DESCRIPTION_MAP: "Circuit Breaker OPEN — {service_name} 트래픽 차단"
      → event.data에서 변수 추출하여 치환
    """
    runbook_id: str
    confidence: float         # 0.0 ~ 1.0
    matched_conditions: list[str]  # 매칭된 조건 설명
    historical_success_rate: float | None  # 과거 실행 성공률
    similar_pattern_count: int  # LearningService에서 유사 패턴 수
    triggered_by_event: str | None  # 트리거 이벤트
    metric_snapshot: dict[str, float]  # 매칭 시점 메트릭 스냅샷
    event_context: dict[str, Any] = field(default_factory=dict)  # 원본 이벤트 data + source
    runner_up_runbook_ids: list[str] = field(default_factory=list)  # 탈락 후보 런북 ID 목록
    risk_level: int = 0  # 런북 위험도 (RunbookLike.risk_level, 기본 0). select_runbook Tie-breaker 2차 키.
```

### 3.3 MatchSelectionResult — 최종 선택 결과

```python
@dataclass
class MatchSelectionResult:
    """최종 런북 선택 결과 — 1위 + Runner-up 전체 보존.

    설계 근거 — RootCauseAnalysis 패턴:
    - RootCauseAnalysis가 primary_cause + candidates 리스트를 모두 포함하듯
      (services/correlation_engine/root_cause_ranker.py L152-L160),
      런북 선택도 1위와 전체 후보를 함께 보존한다.
    - Runner-up 정보는 PlaybackRecorder와 Audit Log에 기록하여,
      1위 런북 실패 시 운영자가 대안을 확인할 수 있도록 한다.
    """
    selected: MatchResult                   # 1위 런북 (confidence 최고)
    all_candidates: list[MatchResult]       # 전체 후보 (confidence 내림차순)
    selection_reason: str                   # 선택 근거 설명
```

## 4. PatternMatcher 클래스

### 4.1 하이브리드 접근 — Reactive + Proactive 경로

PatternMatcher는 두 가지 평가 경로를 운영한다:

| 경로 | 트리거 | 용도 | 코드 근거 |
|---|---|---|---|
| **Reactive** | EventBus 이벤트 수신 | CB 열림 등 즉시 대응이 필요한 장애 | `SelfHealingEventBus.subscribe()` (services/event_bus/bus/__init__.py L330-L382) |
| **Proactive** | Celery Beat 주기적 실행 | 메모리 누수 등 이벤트 없이 메트릭만 서서히 악화되는 경우 | `CELERY_BEAT_SCHEDULE` 패턴 (tasks/governance.py L16), `AutoRollbackGuard._monitor_loop()` (core/auto_rollback_guard.py L237-L251) |

Proactive 경로는 `min_duration_seconds > 0`인 런북이 하나라도 있으면 **필수**다.
Reactive 경로가 "감지"를, Proactive 경로가 "지속 시간 확인"을 담당하여
단일 스냅샷의 한계를 극복한다.

### 4.2 Thundering Herd 방지 — Jitter 적용

Celery Beat로 주기적 평가 시, 모든 워커가 동시에 Prometheus에 쿼리하면
트래픽 스파이크가 발생한다. 기존 Jitter 유틸리티를 적용하여 분산한다.

```python
# utils/jitter.py (기존 코드)
# @with_jitter 데코레이터 또는 calculate_jitter() 직접 호출

# 환경별 권장 설정 (jitter.py docstring 참조):
# - 단일 서버: 0초 (비활성화)
# - K8s 10 Pods: 30초
# - K8s 100+ Pods: 60초
```

```python
from selfhealing.utils.jitter import with_jitter


class PatternMatcher:
    """현재 증상을 런북 트리거 조건과 대조.

    두 가지 평가 경로:
    1. Reactive: EventBus 이벤트 구독 → _on_event() → 즉시 평가
    2. Proactive: Celery Beat/타이머 → evaluate_all_proactive() → 주기적 평가

    연동:
    - EventBus: 이벤트 구독 (SelfHealingEventBus.subscribe)
    - RunbookRegistry: 활성 런북 조회
    - LearningService: 과거 유사 패턴 조회 (confidence 보강)
    - RunbookMetricsProvider: 범용 메트릭 수집 (§5 참조)
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
        metrics_provider: RunbookMetricsProvider | None = None,
        duration_tracker: DurationTracker | None = None,
        on_runbook_selected: Callable[[MatchSelectionResult], None] | None = None,
    ):
        """
        Args:
            registry: 런북 레지스트리 (활성 런북 목록 조회)
            learning_service: 패턴 학습 서비스 (과거 사례 조회용, optional)
            metrics_provider: 범용 메트릭 제공자 (§5의 RunbookMetricsProvider)
            duration_tracker: min_duration 추적기 (None이면 인메모리 생성)
            on_runbook_selected: 런북 선택 시 콜백 (275 Executor와의 연결용).
                MatchSelectionResult를 인자로 받으며, None이면 실행 안 함.
        """
        self._registry = registry
        self._learning_service = learning_service
        self._metrics_provider = metrics_provider
        self._duration_tracker = duration_tracker or DurationTracker()
        self._on_runbook_selected = on_runbook_selected

    def initialize(self) -> None:
        """EventBus에 이벤트 구독 등록.

        SelfHealingEventBus.subscribe(event_type, handler, priority)를 사용.
        priority=EventPriority.NORMAL — 다른 핸들러와 동등.
        """
        ...

    def _on_event(self, event: SelfHealingEvent) -> None:
        """이벤트 수신 시 콜백 — Reactive 경로.

        1. 현재 메트릭 수집 (RunbookMetricsProvider.get_metrics_snapshot)
        2. 등록된 모든 활성 런북의 trigger 조건 평가 (AND 게이트)
        3. min_duration > 0: Redis에 first_met_at 기록, "pending" 상태
        4. min_duration == 0: 즉시 매칭 → MatchResult 생성
        5. event.data를 MatchResult.event_context에 보존 (컨텍스트 전달)
        6. 최고 confidence 런북 선택 → service.py로 전달

        컨텍스트 보존 예시:
            CIRCUIT_BREAKER_OPENED 이벤트 → event.data에 service_name 포함
            → MatchResult.event_context = {"service_name": "payment_api", ...}
            → 275번 Executor에서 "{service_name}" 치환에 사용
        """
        ...

    @with_jitter(max_delay_seconds=10.0, min_delay_seconds=0.0)
    def evaluate_all_proactive(self) -> list[MatchResult]:
        """Proactive 경로 — Celery Beat에서 주기적으로 호출.

        Thundering Herd 방지:
        - @with_jitter 데코레이터로 워커별 랜덤 지연 적용
        - utils/jitter.py (기존 코드) 재사용

        동작:
        1. RunbookMetricsProvider.get_metrics_snapshot()으로 전체 메트릭 수집
        2. evaluate_all(metrics, triggered_event=None) 호출
        3. "pending" 상태인 조건의 min_duration 충족 여부 확인
        """
        metrics = self._metrics_provider.get_metrics_snapshot(
            metric_names=self._collect_required_metrics(),
        )
        return self.evaluate_all(metrics, triggered_event=None)

    def evaluate_all(
        self,
        metrics: dict[str, float],
        triggered_event: str | None = None,
        event_context: dict[str, Any] | None = None,
    ) -> list[MatchResult]:
        """등록된 모든 런북의 트리거 조건을 평가.

        AND 게이트 기반 필터링:
        - PatternCondition.evaluate()가 True인 런북만 후보로 진입
        - 부분 매칭(Partial Match)은 허용하지 않음
        - 후보 진입 후 confidence는 별도 Factor로 산정 (§4.3)

        Args:
            metrics: 현재 메트릭 스냅샷
            triggered_event: 트리거 이벤트 타입 (optional)
            event_context: 원본 이벤트 데이터 (optional, Executor 파라미터 치환용)

        Returns:
            매칭된 런북 목록 (confidence 내림차순)
        """
        results: list[MatchResult] = []

        # 이벤트 콘텍스트에서 소스/데이터 분리 (_source 키: _on_event() 내부 컨벤션)
        ctx = event_context or {}
        event_source: str | None = ctx.get("_source")
        event_data: dict[str, Any] | None = ctx if ctx else None

        # LearningService 패턴을 1회 조회하여 캐시 (O(3N) → O(1) RPC)
        # N 런북 × 3 패턴 종류 반복 호출 대신 호출 시작 시 1회만 조회.
        learning = self._precompute_learning_snapshot()

        for runbook in self._registry.get_active_runbooks():
            condition = runbook.trigger_condition

            # 라벨이 있는 메트릭은 개별 조회로 스냅샷 보완
            resolved_metrics = self._resolve_labeled_metrics(condition, metrics)

            # AND 게이트 — 이벤트 소스/데이터 포함 전체 조건 평가
            if not condition.evaluate(resolved_metrics, triggered_event, event_source, event_data):
                # 조건 미충족: min_duration pending 상태였다면 기록 삭제
                if condition.min_duration_seconds > 0:
                    self._duration_tracker.clear_condition(runbook.id)
                continue

            # min_duration 확인 (§4.4)
            if condition.min_duration_seconds > 0:
                if not self._duration_tracker.check_duration_met(
                    runbook.id, condition.min_duration_seconds
                ):
                    self._duration_tracker.record_condition_met(
                        runbook.id, condition.min_duration_seconds
                    )
                    continue

            # Confidence 산정 (§4.3) — learning 스냅샷 재사용
            confidence = self._calculate_confidence(runbook, learning)

            results.append(MatchResult(
                runbook_id=runbook.id,
                confidence=confidence,
                matched_conditions=self._describe_conditions(condition),
                historical_success_rate=self._get_historical_success_rate(runbook.id, learning),
                similar_pattern_count=self._count_similar_patterns(runbook.id, learning),
                triggered_by_event=triggered_event,
                metric_snapshot=dict(metrics),
                event_context=event_context or {},
                risk_level=getattr(runbook, "risk_level", 0),
            ))

        # Confidence 내림차순 정렬
        results.sort(key=lambda r: -r.confidence)
        return results

    def select_runbook(
        self,
        candidates: list[MatchResult],
    ) -> MatchSelectionResult | None:
        """다중 런북 매칭 시 최종 1개 선택 — Deterministic Tie-breaker.

        RootCauseRanker의 4단계 Tie-breaker 패턴을 적용:
        (services/correlation_engine/root_cause_ranker.py L507-L516)

        ```python
        # RootCauseRanker 기존 코드:
        candidates.sort(key=lambda c: (
            -c.score,
            -c.cascade_depth,
            int(c.event_node.timestamp / epsilon),
            c.event_node.event_id,
        ))
        ```

        런북 선택 Tie-breaker 4단계:
        1차: confidence 높은 순
        2차: risk_level 낮은 것 우선 (SAFE > MODERATE > DANGEROUS)
        3차: historical_success_rate 높은 순
        4차: runbook_id 알파벳순 (결정론적)
        """
        if not candidates:
            return None

        candidates.sort(key=lambda m: (
            -m.confidence,
            getattr(m, 'risk_level', 0),
            -(m.historical_success_rate or 0.0),
            m.runbook_id,
        ))

        selected = candidates[0]
        selected.runner_up_runbook_ids = [c.runbook_id for c in candidates[1:]]

        return MatchSelectionResult(
            selected=selected,
            all_candidates=candidates,
            selection_reason=self._build_selection_reason(selected, candidates),
        )
```

### 4.3 Confidence 계산 — AND 게이트 통과 후 품질 점수

기존 설계의 `matched_conditions / total_conditions` 공식은 AND 조건과 모순된다.
메트릭 조건이 AND이면 하나라도 미충족 시 매칭 실패이므로, 부분 점수 기반 confidence는
위험하다 (3개 중 2개만 만족해도 confidence 0.67로 후보에 올라갈 수 있음).

**수정**: AND 게이트를 통과한 후보에 대해서만, 별도 Factor로 confidence를 산정한다.

```python
    # --- Confidence 가중치 (Settings으로 외부화 권장) ---
    CONDITION_WEIGHT: float = 0.6   # 룰 기반 조건 매칭 60%
    LEARNING_WEIGHT: float = 0.4    # 학습 보강 최대 40%

    def _calculate_confidence(self, runbook: Runbook) -> float:
        """AND 게이트 통과 후 confidence 산정.

        기존 설계의 matched/total 비율이 아닌, 다음 Factor의 가중합:
        - 런북에 정의된 priority/weight (운영자 의도)
        - LearningService 과거 성공률 (Time Decay 적용)
        - 과거 유사 패턴 발생 빈도

        콜드스타트 안전: 학습 데이터가 없으면 룰 기반 점수 100%.

        가중치 근거:
        - RootCauseRanker도 historical factor를 전체의 25% 수준으로 보조적 사용
          (services/correlation_engine/root_cause_ranker.py L303-L308)
        - 명시적 룰은 운영자가 의도한 트리거이므로 주(Primary) 신호로 취급
        """
        # 1단계: 룰 기반 기본 점수 (AND 게이트 통과 = 1.0)
        base_score = 1.0

        # 런북에 우선순위가 정의되어 있으면 가중 (0.5 ~ 1.0)
        if hasattr(runbook, 'priority_weight'):
            base_score = max(0.5, min(1.0, runbook.priority_weight))

        # 2단계: 학습 보강 (optional)
        learning_boost = 0.0
        if self._learning_service:
            learning_boost = self._calculate_learning_boost(runbook.id)

        # 3단계: 가중 합성
        if learning_boost > 0:
            final = self.CONDITION_WEIGHT * base_score + self.LEARNING_WEIGHT * learning_boost
        else:
            final = base_score  # 콜드스타트: 룰 기반 100%

        return min(final, 1.0)
```

### 4.4 `min_duration_seconds` 구현 — Redis 상태 추적 + Proactive 확인

단일 이벤트 시점의 메트릭 스냅샷만으로는 "이 상태가 30초간 유지되었는가?"를
증명할 수 없다. Reactive 경로(감지)와 Proactive 경로(지속 확인)의 협력으로 해결한다.

```
[Reactive 경로: 이벤트 수신]
1. _on_event() → 메트릭 수집 → 조건 충족 확인
2. min_duration > 0: Redis에 first_met_at 기록 (SET NX), "pending" 상태
3. min_duration == 0: 즉시 매칭

[Proactive 경로: Celery Beat 주기적 평가]
1. 매 tick마다 evaluate_all_proactive() 호출
2. "pending" 상태인 조건의 first_met_at 확인
3. (now - first_met_at) >= min_duration_seconds 이고 조건 여전히 충족 → 매칭 확정
4. 조건 미충족 → grace_period 적용 후 Redis 키 삭제
```

```python
class DurationTracker:
    """min_duration_seconds 추적 — Redis SET NX PX 패턴.

    코드 근거:
    - DistributedRecoveryLock: Redis SET NX PX로 원자적 락 획득
      (services/coordination/distributed_recovery_lock.py L196-L200)
    - RateLimitMemoryAdapter: set_cooldown(key, until_timestamp) 패턴
      (adapters/rate_limit/memory_adapter.py L129)
    """

    KEY_TEMPLATE = "selfhealing:pattern:{runbook_id}:condition_met_at"

    def record_condition_met(self, runbook_id: str, duration_seconds: int) -> None:
        """조건 최초 충족 시점 기록.

        SET NX: 이미 기록되어 있으면 무시 (최초 충족 시점 보존).
        EX: duration_seconds * 2 + grace_period로 자동 만료 설정.
        """
        key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
        ttl = duration_seconds * 2 + 30  # 여유분 포함
        self._redis.set(key, time.time(), nx=True, ex=ttl)

    def check_duration_met(self, runbook_id: str, min_duration: int) -> bool:
        """지속 시간 충족 여부 확인."""
        key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
        first_met_at = self._redis.get(key)
        if first_met_at is None:
            return False
        return (time.time() - float(first_met_at)) >= min_duration

    def clear_condition(self, runbook_id: str) -> None:
        """조건 해소 시 기록 삭제 (grace_period 후)."""
        key = self.KEY_TEMPLATE.format(runbook_id=runbook_id)
        self._redis.delete(key)
```

## 5. 메트릭 수집 — RunbookMetricsProvider (범용 프로토콜)

### 5.1 기존 MetricsProvider의 한계

`AutoRollbackGuard`의 `MetricsProvider`는 고정 메서드 3개만 제공하므로 재사용하지 **않는다**:

```python
# core/auto_rollback_guard.py L90-L105 — 고정 인터페이스
class MetricsProvider(Protocol):
    def get_error_rate(self) -> float: ...    # 고정
    def get_latency_p99(self) -> float: ...   # 고정
    def get_throughput(self) -> float: ...     # 고정
```

런북은 `db_pool_usage`, `memory_usage`, `cache_hit_rate` 등 임의의 `metric_name`을
문자열로 받아서 평가해야 하므로, 고정 메서드로는 확장 불가.

프로젝트 내 3개의 메트릭 인터페이스가 모두 동일한 한계를 가진다:

| 인터페이스 | 파일 | 형태 |
|---|---|---|
| `MetricsProvider` | `core/auto_rollback_guard.py` L90 | 고정 메서드 3개 |
| `MetricSourceAdapter` | `adapters/metrics/base.py` L15 | 고정 메서드 4개 (DLQ, CB 등 도메인 특화) |
| `MetricsAdapterProtocol` | `services/auto_tuning/metrics_provider.py` L15 | `fetch_current_metrics() -> dict[str, float]` (가장 유연하나 라벨 필터 없음) |

### 5.2 RunbookMetricsProvider — 신규 Protocol

기존 Protocol을 수정하지 않고, 런북 전용 범용 Protocol을 신규 정의한다.
`PrometheusMetricsAdapter._query_metric()` 내부 메서드를 재활용하여 구현 가능하다.

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class RunbookMetricsProvider(Protocol):
    """런북 패턴 매칭을 위한 범용 메트릭 제공자.

    기존 MetricsProvider(고정 메서드 3개), MetricSourceAdapter(DLQ 특화),
    MetricsAdapterProtocol(라벨 없음)과 공존한다.

    설계 근거 — PrometheusMetricsAdapter:
    - _query_metric(query, default) 메서드가 임의 PromQL 실행 가능
      (adapters/metrics/auto_tuning_adapter.py L233-L248)
    - 라벨 필터를 PromQL {service="payment"} 형태로 직접 매핑

    설계 근거 — MetricsProviderWrapper 패턴:
    - MetricsAdapterProtocol의 fetch_current_metrics()를 래핑하여
      개별 값에 접근하는 기존 패턴 (services/auto_tuning/metrics_provider.py L25-L85)
    """

    def get_metric(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
    ) -> float | None:
        """단일 메트릭 값 조회.

        Args:
            metric_name: 메트릭 이름 (예: "error_rate", "db_pool_usage")
            labels: 라벨 필터 (예: {"service": "payment", "endpoint": "/checkout"})

        Returns:
            메트릭 값, 조회 실패 시 None
        """
        ...

    def get_metrics_snapshot(
        self,
        metric_names: list[str],
        labels: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """여러 메트릭을 한 번에 조회 (배치).

        Proactive 경로에서 매 tick마다 N개 메트릭을 개별 호출하면
        Prometheus에 N번 쿼리가 발생하므로, 배치 조회가 필수.

        Args:
            metric_names: 조회할 메트릭 이름 목록
            labels: 공통 라벨 필터

        Returns:
            metric_name → value 딕셔너리
        """
        ...
```

## 6. LearningService 연동 — Time Decay 적용

### 6.1 기존 연동

`LearningService.get_patterns()` 호출로 과거 유사 패턴을 조회한다:

```python
# services/learning/service.py L552-L575 (기존 코드)
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

### 6.2 Time Decay — 시간 감가상각

시스템 아키텍처와 트래픽 패턴은 계속 변하므로, 과거의 성공 이력일수록
가중치를 깎아야 최근 실패 경험이 과거의 영광에 묻히지 않는다.

현재 `LearningService`는 Time Decay를 적용하지 않는다:

```python
# services/learning/service.py L368-L371 (기존 코드 — 단순 평균)
existing.occurrence_count += 1
existing.last_seen = datetime.now(timezone.utc)
existing.confidence = (existing.confidence + confidence) / 2  # 감쇠 없음
```

`LearningPattern.last_seen`은 기록만 되고 confidence 감쇠에 사용되지 않는다.
PatternMatcher에서 소비 시점에 감쇠를 적용하여 LearningService 수정 없이 해결한다.

```python
from math import exp


def _calculate_learning_boost(self, runbook_id: str) -> float:
    """LearningService 패턴 기반 confidence 보강 — Time Decay 적용.

    코드 근거:
    - LearningPattern.last_seen: datetime (services/learning/models.py L65)
    - LearningPattern.occurrence_count: int — 빈번할수록 가중
    - LearningPattern.features: dict[str, Any] — 유사도 비교 대상

    Time Decay 공식: decay = exp(-0.693 * age_days / HALF_LIFE_DAYS)
    - 반감기 90일: 90일 전 패턴은 confidence 50%로 감쇠
    - 180일 전 패턴은 25%로 감쇠
    """
    HALF_LIFE_DAYS = 90  # Settings으로 외부화 권장

    patterns = self._learning_service.get_patterns(
        pattern_type=PatternType.FAILURE,
        min_confidence=0.3,
    )

    similar = self._find_similar_patterns(runbook_id, patterns)
    if not similar:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0

    for pattern in similar:
        age_days = (datetime.now(timezone.utc) - pattern.last_seen).days
        decay_factor = exp(-0.693 * age_days / HALF_LIFE_DAYS)
        weight = pattern.occurrence_count * decay_factor
        weighted_sum += pattern.confidence * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0
```

## 7. 중복 트리거 방지

같은 장애에 대해 런북이 여러 번 트리거되지 않도록:

### 7.1 쿨다운 타이머 — `runbook_id + target + region` Granularity

쿨다운 키는 런북 ID만이 아닌, **런북 ID + 타겟 서비스 + 리전** 수준으로 분리한다.
결제 서비스에서 런북이 실행되었다고 해서, 장바구니 서비스의 동일 런북이 차단되면 안 된다.

```python
# 쿨다운 키 구조 — IdempotencyKey.for_recovery_action() 패턴과 일치
# (services/idempotency/models.py L488):
# key = f"recovery:{action_type}:{target}:{session_id}"

cooldown_key = f"selfhealing:runbook:cooldown:{runbook_id}:{target_service}:{region_id}"
redis.set(cooldown_key, "1", ex=cooldown_seconds)

# 트리거 시 체크
if redis.exists(cooldown_key):
    # 쿨다운 Skip 이벤트 발행 (§7.4)
    return
```

### 7.2 진행 중 체크

`DistributedRecoveryLock`이 잡혀있으면 트리거 스킵:

```python
# services/coordination/distributed_recovery_lock.py L196-L200
# Redis SET NX PX: 키가 없을 때만 설정 + 만료시간
acquired = redis.set(lock_key, session_id, nx=True, px=timeout_ms)
```

### 7.3 멱등성 키

`IdempotencyKey.for_recovery_action(action_type, target, region, session_id)` 활용.

### 7.4 쿨다운 Skip 이벤트 발행 — Observability 확보

런북이 쿨다운에 걸려 차단되었을 때, 로그만 남기면 운영자 입장에서
"장애가 발생했는데 시스템이 아무것도 안 한다"고 오해할 수 있다.

`EventType`에 이미 런북 관련 이벤트 슬롯이 정의되어 있으므로
(`services/event_bus/bus/__init__.py` L195-L218: `RUNBOOK_TRIGGERED`, `RUNBOOK_COMPLETED` 등),
동일한 패턴으로 Skip 이벤트를 추가한다:

```python
# EventType enum 확장 (services/event_bus/bus/__init__.py에 추가)
RUNBOOK_SKIPPED_COOLDOWN = "runbook_skipped_cooldown"
"""런북 트리거 조건 충족되었으나 쿨다운 중이라 스킵."""

# 발행 시 data 구조
bus.emit(
    event_type=EventType.RUNBOOK_SKIPPED_COOLDOWN,
    data={
        "runbook_id": runbook_id,
        "target_service": target_service,
        "region_id": region_id,
        "cooldown_remaining_seconds": remaining,
        "original_trigger_event": triggered_event,
        "matched_confidence": confidence,
    },
    source="pattern_matcher",
)
```

## 8. 컨텍스트 전달 — Event Payload → Runbook Parameter

이벤트 발생 시 어떤 서비스에서 장애가 발생했는지 알아야
해당 서비스의 캐시를 지우거나 재시작하는 런북을 실행할 수 있다.

### 8.1 이벤트 data의 표준 필드

이벤트 data에 이미 `service_name`이 표준으로 포함된다:

| 이벤트 | data 필드 | 코드 위치 |
|---|---|---|
| `CIRCUIT_BREAKER_OPENED` | `{"service_name": ..., "new_state": ..., "previous_state": ...}` | `services/event_bus/bus/__init__.py` L993-L997 |
| `CIRCUIT_BREAKER_OPENED` (service) | `{"service_name": ..., "burn_rate_multiplier": ..., "timestamp": ...}` | `services/circuit_breaker/service.py` L786-L792 |
| `EMERGENCY_ACTIVATED` | `{"level": ..., "reason": ..., "trigger_source": ..., "incident_id": ...}` | `services/security/orchestrator.py` L154-L158 |
| `ERROR_BUDGET_CRITICAL` | `{"budget_percent": ..., "threshold": ...}` | `services/event_bus/bus/__init__.py` L961-L963 |

### 8.2 템플릿 치환 — 기존 `format_map()` 패턴 재사용

프로젝트에서 Jinja2는 사용하지 않는다. Python 내장 `str.format_map()`이 표준이다.

```python
# services/correlation_engine/incident_timeline.py L979-L995 (기존 코드)
template = self.DESCRIPTION_MAP.get(node.event_type)
# 예: "Circuit Breaker OPEN — {service_name} 트래픽 차단"

format_vars: dict[str, Any] = {"service_name": node.service_name}
if "level" in node.data:
    format_vars["level"] = node.data["level"]

desc = template.format_map(_SafeFormatDict(format_vars))
```

```python
# services/correlation_engine/incident_timeline.py L1082-L1095 (기존 코드)
class _SafeFormatDict(dict):
    """format_map()에서 누락 키를 빈 문자열로 대체하는 dict."""
    def __missing__(self, key: str) -> str:
        logger.warning("Missing template variable '%s'", key)
        return ""
```

### 8.3 Executor에서의 파라미터 치환

275번 Executor는 `MatchResult.event_context`를 받아서 런북 Step의 파라미터를 치환한다.
`_SafeFormatDict`는 현재 `incident_timeline.py`의 private 클래스이므로,
공용 유틸리티(`utils/template.py`)로 승격하여 재사용한다.

```python
# 275번 Executor에서의 사용 예시
# 런북 Step 정의: {"action": "restart_service", "target": "{service_name}"}
# MatchResult.event_context = {"service_name": "payment_api", "level": 3}

from selfhealing.utils.template import SafeFormatDict

step_params = {
    k: v.format_map(SafeFormatDict(match_result.event_context))
    if isinstance(v, str) else v
    for k, v in step.raw_params.items()
}
# 결과: {"action": "restart_service", "target": "payment_api"}
```

## 9. 참조

- `SelfHealingEventBus`: `services/event_bus/bus/__init__.py` — `EventType`, `subscribe()`, `emit()`
- `EventType` (Runbook): `services/event_bus/bus/__init__.py` L195-L218 — `RUNBOOK_TRIGGERED` ~ `RUNBOOK_APPROVAL_REJECTED`
- `SelfHealingEvent.data`: `services/event_bus/bus/__init__.py` L237 — `data: dict[str, Any]`
- `emit_circuit_breaker_state_changed()`: `services/event_bus/bus/__init__.py` L972-L1001 — data에 `service_name` 포함
- `LearningService`: `services/learning/service.py` — `get_patterns()`, `learn_pattern()`
- `LearningPattern`: `services/learning/models.py` L56-L67 — `last_seen`, `features`, `confidence`
- `PatternType`: `services/learning/models.py` — `FAILURE`, `RECOVERY`, `ANOMALY`, `OPTIMIZATION`
- `BlacklistReason.FLAPPING`: `services/learning/models.py` L44 — Flapping 감지 사유
- `MetricsProvider`: `core/auto_rollback_guard.py` L90-L105 — 고정 Protocol (재사용하지 않음)
- `MetricsAdapterProtocol`: `services/auto_tuning/metrics_provider.py` L15-L20 — `fetch_current_metrics()`
- `PrometheusMetricsAdapter`: `adapters/metrics/auto_tuning_adapter.py` L144-L248 — `_query_metric()`
- `AutoRollbackGuard._monitor_loop()`: `core/auto_rollback_guard.py` L237-L251 — 주기적 모니터링 루프
- `AutoRollbackGuard._execute_rollback()` 쿨다운: `core/auto_rollback_guard.py` L373-L376
- `DistributedRecoveryLock`: `services/coordination/distributed_recovery_lock.py` L58 — Redis SET NX PX
- `IdempotencyKey.for_recovery_action()`: `services/idempotency/models.py` L446-L492
- `RootCauseRanker` Tie-breaker: `services/correlation_engine/root_cause_ranker.py` L507-L516
- `RootCauseAnalysis` (primary + candidates): `services/correlation_engine/root_cause_ranker.py` L152-L160
- `_SafeFormatDict`: `services/correlation_engine/incident_timeline.py` L1082-L1095
- `IncidentTimeline.DESCRIPTION_MAP`: `services/correlation_engine/incident_timeline.py` L687-L697
- `with_jitter()`: `utils/jitter.py` L27-L66 — Thundering Herd Prevention
- `JitterConfig`: `utils/jitter.py` L193-L254 — 환경별 설정
