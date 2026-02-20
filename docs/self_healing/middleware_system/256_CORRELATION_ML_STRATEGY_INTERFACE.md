# 256. ML Strategy Interface — AI/ML 확장 기반

> **Version**: 1.1.0
> **Created**: 2026-02-20
> **Updated**: 2026-02-21
> **Status**: Approved
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `interfaces/ml_strategy.py`, `services/correlation_engine/interfaces.py`

---

## 0. 요약

Correlation Engine의 각 분석 단계를 **Strategy Protocol**로 추상화하여, 구매자가 기본 제공 알고리즘(ZScore, DAG, 가중합)을 **AI/ML 모델로 교체**할 수 있는 기반을 구축한다. 현재 시스템에는 AI/ML이 전혀 없지만, 이 인터페이스 계층이 있으면 **코어 코드 수정 없이** ML 전략을 연결할 수 있다.

이 문서는 Correlation Engine 전용 인터페이스뿐 아니라, **시스템 전체에서 공유 가능한 ML Strategy Protocol**도 정의한다. 기존 `PredictiveForecasterService`의 `ZScoreDetector`, `SpikeClassifier`, `HoltLinearForecaster`도 향후 이 인터페이스를 통해 교체 가능한 구조를 제시한다.

---

## 1. 설계 원칙

### 1.1 기존 인터페이스 패턴 준수

프로젝트의 [interfaces/](../../packages/selfhealing-python/src/selfhealing/interfaces/) 디렉토리에서 확인된 패턴:

```python
# 기존 패턴 1: ABC 기반 (엄격한 계약)
class CacheProviderInterface(ABC):
    @abstractmethod
    def get(self, key: str) -> Any: ...

# 기존 패턴 2: Protocol 기반 (Duck typing)
@runtime_checkable
class NotificationAdapter(Protocol):
    def send(self, message: str, channel: str) -> bool: ...
```

ML Strategy는 **Protocol(Duck typing)** 패턴을 채택한다:
- 구매자가 어떤 ML 프레임워크(scikit-learn, PyTorch, TensorFlow)를 사용해도 무관
- `@runtime_checkable`로 런타임 타입 검증 가능
- 기존 클래스에 메서드만 추가하면 호환 (상속 불필요)

### 1.2 레이어 분리

```
interfaces/ml_strategy.py          ← 시스템 전체 공유 (Correlation Engine 외 모듈도 사용)
services/correlation_engine/interfaces.py  ← Correlation Engine 전용 전략
```

---

## 2. 시스템 전체 공유 — `interfaces/ml_strategy.py`

### 2.1 AnomalyDetectionStrategy

```python
@runtime_checkable
class AnomalyDetectionStrategy(Protocol):
    """이상 탐지 전략 — 통계/ML/딥러닝 교체 가능

    기본 제공:
        - ZScoreDetector: Z-Score 기반 (현재 구현)
        - IQRDetector: IQR 기반 (현재 구현)

    구매자 확장 예시:
        - IsolationForestDetector: scikit-learn Isolation Forest
        - AutoencoderDetector: PyTorch Autoencoder 기반
        - ProphetDetector: Facebook Prophet 기반 계절성 인지 이상 탐지

    사용처:
        - PredictiveForecasterService (메트릭 이상 탐지)
        - CoOccurrenceTracker (동시발생 빈도 이상 탐지)
        - CorruptionShield L3 (데이터 이상 탐지)
    """

    def detect(self, value: float) -> tuple[bool, float]:
        """단일 값의 이상 여부 판단

        Args:
            value: 검사할 값

        Returns:
            (is_anomalous, score): 이상 여부와 이상 점수
            score는 정규화 불필요 — 전략별 자유 (ZScore, probability 등)
        """
        ...

    def update(self, value: float) -> None:
        """학습 데이터 추가 (온라인 학습)

        Args:
            value: 새로운 관측값
        """
        ...

    def reset(self) -> None:
        """학습 상태 초기화"""
        ...
```

### 2.2 ForecastStrategy

```python
@runtime_checkable
class ForecastStrategy(Protocol):
    """시계열 예측 전략

    기본 제공:
        - HoltLinearForecaster: 이중지수평활 (현재 구현)

    구매자 확장 예시:
        - ProphetForecaster: Facebook Prophet
        - LSTMForecaster: PyTorch LSTM
        - ARIMAForecaster: statsmodels ARIMA

    사용처:
        - PredictiveForecasterService (메트릭 예측)
        - CoOccurrenceTracker (동시발생 빈도 트렌드)
    """

    def update(self, value: float) -> float:
        """새 관측값으로 모델 업데이트

        Args:
            value: 새로운 관측값

        Returns:
            현재 레벨 (smoothed value)
        """
        ...

    def predict(self, steps_ahead: int = 1) -> float | None:
        """미래 값 예측

        Args:
            steps_ahead: 예측할 미래 스텝 수

        Returns:
            예측값. 데이터 부족 시 None
        """
        ...

    def get_confidence(self) -> float:
        """현재 모델의 신뢰도 (0.0 ~ 1.0)"""
        ...
```

### 2.3 ClassificationStrategy

```python
@runtime_checkable
class ClassificationStrategy(Protocol):
    """분류 전략 — 이벤트/스파이크 유형 분류

    기본 제공:
        - SpikeClassifier: 규칙 기반 분류 (현재 구현)

    구매자 확장 예시:
        - RandomForestClassifier: scikit-learn RF
        - XGBoostClassifier: XGBoost
        - NeuralClassifier: PyTorch NN

    사용처:
        - PredictiveForecasterService (스파이크 분류)
        - CorrelationEngine (이벤트 패턴 분류)
    """

    def classify(self, features: dict[str, float]) -> tuple[str, float]:
        """특성 벡터 → 클래스 레이블 + 확신도

        Args:
            features: 특성 이름 → 값 매핑

        Returns:
            (label, confidence): 분류 레이블과 확신도 (0.0 ~ 1.0)
        """
        ...
```

---

## 3. Correlation Engine 전용 — `services/correlation_engine/interfaces.py`

### 3.1 CorrelationStrategy

```python
@runtime_checkable
class CorrelationStrategy(Protocol):
    """상관관계 분석 전략

    기본 제공:
        - CoOccurrenceTracker: ZScore 기반 동시발생 빈도 분석

    구매자 확장 예시:
        - GrangerCausalityStrategy: Granger 인과관계 검정 (scipy 의존)
        - BayesianNetworkStrategy: pgmpy 기반 베이지안 네트워크
        - MutualInfoStrategy: 상호정보량 기반 비선형 상관
        - TransferEntropyStrategy: 전달 엔트로피 기반 방향성 인과

    사용처:
        - CorrelationEngineService.analyze()
    """

    def analyze(
        self,
        event_pairs: list[tuple[str, str, float]],  # (type_a, type_b, time_gap)
        time_window: float,
    ) -> list[CorrelationResult]:
        """이벤트 쌍 목록 → 상관관계 결과

        Args:
            event_pairs: (이벤트타입A, 이벤트타입B, 시간간격) 튜플 리스트
            time_window: 분석 시간 윈도우 (초)

        Returns:
            상관관계 결과 리스트 (CorrelationResult)
        """
        ...

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        """특정 이벤트 쌍의 현재 상관 점수

        Returns:
            0.0 ~ 1.0 점수. 데이터 부족 시 None
        """
        ...
```

### 3.2 RootCauseStrategy

```python
@runtime_checkable
class RootCauseStrategy(Protocol):
    """근본 원인 분석 전략

    기본 제공:
        - DefaultRootCauseRanker: DAG 토폴로지 + 시간순 + BlastRadius 가중합

    구매자 확장 예시:
        - LLMRootCauseAnalyzer: OpenAI/Claude API로 DAG + 이벤트 맥락 분석
        - BayesianRootCause: 조건부 확률 표 기반 확률적 추론
        - GNNRootCause: Graph Neural Network로 DAG 패턴 학습

    사용처:
        - CorrelationEngineService.analyze()
    """

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """DAG + 동시발생 데이터 → 근본 원인 순위

        Args:
            dag: 이벤트 인과관계 그래프
            co_occurrence_data: 동시발생 통계 분석 결과

        Returns:
            근본 원인 분석 결과 (순위 포함)
        """
        ...
```

### 3.3 GraphBuildStrategy

```python
@runtime_checkable
class GraphBuildStrategy(Protocol):
    """DAG 구축 전략

    기본 제공:
        - EventGraphBuilder: BlastRadius × 시간순 교차

    구매자 확장 예시:
        - DynamicDependencyGraphBuilder: 실시간 트래픽 패턴으로 의존성 자동 추론
        - TraceBasedGraphBuilder: OpenTelemetry trace 데이터 기반 DAG 구축

    사용처:
        - CorrelationEngineService.build_dag()
    """

    def build_dag(
        self,
        events: list,     # SelfHealingEvent 또는 ObservedEvent
        window_seconds: float,
    ) -> EventDAG:
        """이벤트 목록 → 인과관계 DAG

        Args:
            events: 시간 윈도우 내 이벤트 목록
            window_seconds: 분석 윈도우 크기

        Returns:
            EventDAG 인스턴스
        """
        ...
```

---

## 4. Strategy Registry (전략 등록/교체)

### 4.1 CorrelationEngineService에서의 전략 관리

```python
class CorrelationEngineService:
    """전략 교체 가능한 오케스트레이터"""

    def __init__(self, settings: CorrelationEngineSettings):
        # 기본 전략 (Day-1 제공)
        self._correlation_strategy: CorrelationStrategy = CoOccurrenceTracker(settings)
        self._root_cause_strategy: RootCauseStrategy = DefaultRootCauseRanker()
        self._graph_strategy: GraphBuildStrategy = EventGraphBuilder(
            BlastRadiusService(), self._correlation_strategy
        )

    def set_correlation_strategy(self, strategy: CorrelationStrategy) -> None:
        """상관관계 분석 전략 교체"""
        if not isinstance(strategy, CorrelationStrategy):
            raise TypeError(f"CorrelationStrategy Protocol을 구현해야 합니다")
        self._correlation_strategy = strategy

    def set_root_cause_strategy(self, strategy: RootCauseStrategy) -> None:
        """근본 원인 분석 전략 교체"""
        if not isinstance(strategy, RootCauseStrategy):
            raise TypeError(f"RootCauseStrategy Protocol을 구현해야 합니다")
        self._root_cause_strategy = strategy

    def set_graph_strategy(self, strategy: GraphBuildStrategy) -> None:
        """DAG 구축 전략 교체"""
        if not isinstance(strategy, GraphBuildStrategy):
            raise TypeError(f"GraphBuildStrategy Protocol을 구현해야 합니다")
        self._graph_strategy = strategy
```

### 4.2 ProviderRegistry 확장 (선택사항)

기존 `ProviderRegistry` 패턴과 일관성을 위해:

```python
# factory.py 확장 (선택사항)
class ProviderRegistry:
    _correlation_strategies: dict[str, type] = {}
    _root_cause_strategies: dict[str, type] = {}

    @classmethod
    def register_correlation_strategy(cls, name: str, strategy_class: type) -> None:
        cls._correlation_strategies[name] = strategy_class

    @classmethod
    def register_root_cause_strategy(cls, name: str, strategy_class: type) -> None:
        cls._root_cause_strategies[name] = strategy_class

    @classmethod
    def get_correlation_strategy(cls, name: str | None = None) -> CorrelationStrategy:
        name = name or "co_occurrence"
        return cls._correlation_strategies[name]()
```

---

## 5. 구매자 확장 예시: LLM 기반 Root Cause 분석

```python
# 구매자가 작성하는 플러그인 코드 예시
class LLMRootCauseAnalyzer:
    """OpenAI/Claude API를 사용한 근본 원인 분석

    selfhealing[all] 설치 후, 별도로:
    pip install openai
    """

    def __init__(self, api_key: str, model: str = "gpt-4"):
        self._client = openai.Client(api_key=api_key)
        self._model = model

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        # 1) DAG를 프롬프트로 변환
        prompt = self._build_prompt(dag, co_occurrence_data)

        # 2) LLM 호출
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": "..."}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        # 3) 응답 → RootCauseAnalysis 변환
        return self._parse_response(response, dag)

# 등록
service = CorrelationEngineService(settings)
service.set_root_cause_strategy(LLMRootCauseAnalyzer(api_key="..."))
```

---

## 6. pyproject.toml extras 확장

```toml
[project.optional-dependencies]
# 기존 extras 유지
# ...

# ML 확장 (신규)
ml = [
    "scikit-learn>=1.3.0",
    "numpy>=1.24.0",
]
ml-deep = [
    "torch>=2.0.0",
    "scikit-learn>=1.3.0",
    "numpy>=1.24.0",
]
ml-llm = [
    "openai>=1.0.0",
]
ml-timeseries = [
    "prophet>=1.1.0",
    "statsmodels>=0.14.0",
]
```

설치:
```bash
pip install selfhealing[ml]              # scikit-learn 기반 전략
pip install selfhealing[ml-llm]          # LLM 기반 전략
pip install selfhealing[ml-timeseries]   # 고급 시계열 분석
pip install selfhealing[all,ml]          # 전체 + ML
```

---

## 7. 기존 모듈 역호환

기존 `ZScoreDetector`, `IQRDetector`, `HoltLinearForecaster`는 이미 `AnomalyDetectionStrategy` / `ForecastStrategy`의 메서드 시그니처와 **호환**된다:

| 기존 클래스 | Protocol | 호환 여부 | 비고 |
|------------|----------|----------|------|
| `ZScoreDetector.is_anomaly(value)` | `AnomalyDetectionStrategy.detect(value)` | ⚠️ 메서드명 다름 | Adapter wrapper 필요 |
| `IQRDetector.is_anomaly(value)` | `AnomalyDetectionStrategy.detect(value)` | ⚠️ 메서드명 다름 | Adapter wrapper 필요 |
| `HoltLinearForecaster.update(value)` | `ForecastStrategy.update(value)` | ✅ 호환 | |
| `HoltLinearForecaster.predict(steps)` | `ForecastStrategy.predict(steps)` | ✅ 호환 | |
| `SpikeClassifier.classify(...)` | `ClassificationStrategy.classify(features)` | ⚠️ 시그니처 다름 | Adapter wrapper 필요 |

Adapter wrapper 예시:
```python
class ZScoreDetectorAdapter:
    """ZScoreDetector → AnomalyDetectionStrategy 어댑터"""
    def __init__(self, detector: ZScoreDetector):
        self._detector = detector

    def detect(self, value: float) -> tuple[bool, float]:
        return self._detector.is_anomaly(value)

    def update(self, value: float) -> None:
        self._detector.is_anomaly(value)  # is_anomaly가 내부 업데이트도 수행

    def reset(self) -> None:
        self._detector._values.clear()
```

→ Phase 1에서는 Adapter로 감싸서 호환, 향후 기존 클래스 리팩토링 시 Protocol 직접 구현.

---

## 8. 테스트 전략

| 테스트 유형 | 시나리오 | 검증 |
|------------|---------|------|
| **단위** | `@runtime_checkable` Protocol 검증 | `isinstance(ZScoreDetectorAdapter(), AnomalyDetectionStrategy)` = True |
| **단위** | 전략 교체 후 analyze() 호출 | 기본 전략 대신 mock 전략 실행 |
| **단위** | Protocol 미구현 객체 등록 시도 | TypeError 발생 |
| **통합** | LLM mock + DAG 입력 → RootCauseAnalysis | 유효한 결과 반환 |
| **통합** | ProviderRegistry 전략 등록/조회 | 등록된 전략 정상 반환 |
| **단위** | `BatchCapable` Protocol isinstance 분기 | 배치 전략은 `detect_batch()` 호출, 비배치 전략은 단건 루프 |
| **단위** | `StrategyLifecycle.is_ready()` False 시 | `HealthCheckService.get_readiness()` not_ready 전파 |
| **단위** | Fallback 발동 시 `RootCauseAnalysis.strategy_metadata` | `fallback_used=True`, `fallback_reason` 기록 확인 |
| **통합** | ML Bulkhead 큐 포화 시 | `BulkheadFullError` → Fallback 전략 실행 |
| **통합** | `StrategyLifecycle.initialize()` + `warmup()` 순서 | K8s readinessProbe 통과 전 트래픽 차단 확인 |

---

## 9. ML 추론 스레드 격리 — ThreadPoolBulkhead 통합

### 9.1 문제

LLM API 호출(3~30초)이나 GPU 추론을 `CorrelationEngineService`의 60초 Tick 내에서 동기 호출하면 메인 스레드가 블로킹된다. `ThreadPoolExecutor(max_workers=1)` 패턴(Saga Orchestrator 선례)은 개별 호출을 격리하지만, 동시 인시던트 폭주 시 **스레드 풀 고갈(Starvation)** 위험이 있다.

### 9.2 선택: BulkheadRegistry + ThreadPoolBulkhead 재사용

**선택지 A**: 매 호출마다 `ThreadPoolExecutor(max_workers=1)` 생성 (Saga 패턴)
**선택지 B**: `BulkheadRegistry`에 `"ml_inference"` 도메인을 등록하고 `ThreadPoolBulkhead` 재사용

→ **B안 채택**

**선택 이유**:
1. `BulkheadRegistry`가 이미 `ConnectionType.EXTERNAL_API`를 `ThreadPoolBulkhead(max_workers=5, queue_size=10)`로 격리하고 있음 ([resilience/bulkhead/registry.py L83](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/registry.py))
2. `ThreadPoolBulkhead.submit()`이 `contextvars.copy_context()`로 트레이싱 컨텍스트를 전파함 — Saga의 `ThreadPoolExecutor(max_workers=1)`에는 없는 기능 ([resilience/bulkhead/threadpool.py L165](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/threadpool.py))
3. `BulkheadState`의 `rejected_count`, `waiting_count`, `utilization_percent` 메트릭으로 Starvation을 사전 감지 가능 ([resilience/bulkhead/threadpool.py L238](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/threadpool.py))
4. `BulkheadRegistry._on_config_updated()`가 `CONFIG_UPDATED` 이벤트로 **런타임 동적 재구성** 지원 ([resilience/bulkhead/registry.py L116](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/registry.py))

### 9.3 설계

```
BulkheadRegistry
├── DATABASE        → SemaphoreBulkhead(max=10)    ← 기존
├── CACHE           → SemaphoreBulkhead(max=20)    ← 기존
├── EXTERNAL_API    → ThreadPoolBulkhead(max=5)    ← 기존
├── MESSAGE_QUEUE   → SemaphoreBulkhead(max=15)    ← 기존
└── ml_inference    → ThreadPoolBulkhead(max=3)    ← 신규
```

### 9.4 BulkheadSettings 확장

```python
# settings/bulkhead.py 확장
class BulkheadSettings(BaseSettings):
    # ... 기존 필드 유지 ...

    # ML/LLM 추론 전용 격벽 (신규)
    ml_inference_max_workers: int = Field(
        default=3, ge=1, le=20,
        description="ML/LLM 추론 전용 스레드 풀 워커 수",
    )
    ml_inference_queue_size: int = Field(
        default=5, ge=0, le=50,
        description="ML/LLM 추론 전용 대기 큐 크기",
    )
    ml_inference_timeout: float = Field(
        default=30.0, ge=1.0, le=120.0,
        description="ML/LLM 추론 타임아웃 (초)",
    )
```

환경변수:
```bash
SELFHEALING_BULKHEAD_ML_INFERENCE_MAX_WORKERS=3
SELFHEALING_BULKHEAD_ML_INFERENCE_QUEUE_SIZE=5
SELFHEALING_BULKHEAD_ML_INFERENCE_TIMEOUT=30.0
```

### 9.5 CorrelationEngineService 통합

```python
class CorrelationEngineService:
    def __init__(self, settings: CorrelationEngineSettings):
        # ... 기존 전략 초기화 ...

        # ML 추론 격벽 — BulkheadRegistry에서 조회 또는 생성
        from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        bh_settings = get_bulkhead_settings()
        registry = get_bulkhead_registry()
        self._ml_bulkhead = registry.get_or_create(
            name="ml_inference",
            max_concurrent=bh_settings.ml_inference_max_workers,
            bulkhead_type="thread_pool",
        )

    def _execute_with_ml_bulkhead(self, fn, *args, **kwargs):
        """ML/LLM 추론을 격벽 내에서 실행.

        ThreadPoolBulkhead.execute()가 타임아웃 + 큐 제한을 처리한다.
        BulkheadFullError 발생 시 호출부에서 Fallback으로 전환한다.
        """
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        timeout = get_bulkhead_settings().ml_inference_timeout
        return self._ml_bulkhead.execute(fn, *args, timeout=timeout, **kwargs)
```

### 9.6 Priority Watermark 연동 (선택사항)

`RateController`의 `PRIORITY_WATERMARKS` 패턴([scaling/rate_controller.py L36](../../packages/selfhealing-python/src/selfhealing/scaling/rate_controller.py))을 ML 격벽에도 적용하여, 토큰 잔량이 낮을 때 `non_essential` 추론 요청을 우선 드랍:

```python
# 인시던트 심각도별 ML 추론 우선순위
ML_PRIORITY_WATERMARKS = {
    "critical": 0.0,    # 항상 처리
    "standard": 0.4,    # 토큰 40% 이상 시 처리
    "background": 0.7,  # 토큰 70% 이상 시만 처리 (학습 등)
}
```

`RateController`의 `STARVATION_RELIEF_SECONDS = 300.0` 패턴도 적용하여 5분 연속 거부 시 watermark 완화.

---

## 10. Feature Context 주입 — 구현체 레벨 Schema 검증

### 10.1 문제

`AnomalyDetectionStrategy.detect(value: float)` 시그니처는 단일 스칼라만 받는다. ML 모델(Isolation Forest, Autoencoder)은 다차원 특성(서비스명, 시간대, CPU 사용률 등)이 필요하다. 동시에 `dict[str, Any]`를 무검증 전달하면 키 오타/타입 불일치로 추론 런타임 에러가 발생한다.

### 10.2 선택: Protocol은 `dict[str, Any]` 유지, 구현체에서 Schema 검증

**선택지 A**: Protocol 시그니처에 `Pydantic BaseModel`을 강제
**선택지 B**: Protocol은 `dict[str, Any]`로 유연하게, 구현체 레벨에서 Pydantic/TypedDict 검증

→ **B안 채택**

**선택 이유**:
1. `PolicyContext`가 `frozen=True` dataclass + `extra: dict[str, Any]`로 범용 확장 필드를 유지하며 Pydantic을 강제하지 않음 ([interfaces/resilience_policy.py L129](../../packages/selfhealing-python/src/selfhealing/interfaces/resilience_policy.py))
2. ML Strategy Protocol의 핵심 원칙이 "구매자가 어떤 ML 프레임워크를 사용해도 무관"한 Duck typing인데, Protocol에 Pydantic 강제가 들어가면 이 원칙 위배
3. `Settings`가 `BaseSettings`(Pydantic)로 환경변수를 검증하면서도, 상위 인터페이스(`ConfigProviderInterface`)는 `dict` 기반인 것과 동일한 레이어 분리 패턴

### 10.3 Protocol 시그니처 확장

```python
@runtime_checkable
class AnomalyDetectionStrategy(Protocol):
    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        """단일 값의 이상 여부 판단

        Args:
            value: 검사할 값
            context: ML 모델에 전달할 다차원 특성 (선택적)
                기본 통계 전략(ZScore, IQR)은 무시한다.
                ML 구현체는 이 dict에서 필요한 특성을 추출한다.

        Returns:
            (is_anomalous, score): 이상 여부와 이상 점수
        """
        ...

    def update(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """학습 데이터 추가 (온라인 학습)"""
        ...

    def reset(self) -> None:
        """학습 상태 초기화"""
        ...

    def get_feature_schema(self) -> dict[str, str] | None:
        """이 전략이 기대하는 context 키/타입 스키마 반환

        Returns:
            {"service_name": "str", "cpu_usage": "float", ...} 형태.
            스키마가 없으면 None (통계 기반 전략).
            Orchestrator가 사전에 입력 유효성을 검증하는 데 사용.
        """
        ...
```

`ClassificationStrategy`도 동일하게 `context` 파라미터 추가:

```python
@runtime_checkable
class ClassificationStrategy(Protocol):
    def classify(
        self,
        features: dict[str, float],
        context: dict[str, Any] | None = None,
    ) -> tuple[str, float]: ...
```

### 10.4 구현체 레벨 Pydantic 검증 예시

```python
from pydantic import BaseModel, Field

class IsolationForestDetector:
    """scikit-learn Isolation Forest 기반 이상 탐지"""

    class FeatureSchema(BaseModel):
        """이 모델이 기대하는 context 스키마.
        settings/ 디렉토리의 BaseSettings 패턴과 동일하게
        Pydantic 검증을 구현체 레벨에서 적용한다.
        """
        service_name: str
        metric_name: str
        cpu_usage: float = Field(ge=0.0, le=1.0)
        memory_usage: float = Field(ge=0.0, le=1.0)
        region: str | None = None

    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        if context:
            validated = self.FeatureSchema(**context)  # Pydantic 검증
            features = [value, validated.cpu_usage, validated.memory_usage]
        else:
            features = [value]
        # Isolation Forest 추론 ...

    def get_feature_schema(self) -> dict[str, str] | None:
        return {
            "service_name": "str",
            "metric_name": "str",
            "cpu_usage": "float (0.0~1.0)",
            "memory_usage": "float (0.0~1.0)",
            "region": "str | None",
        }
```

### 10.5 기존 Adapter 역호환

`ZScoreDetectorAdapter`는 `context`를 무시하므로 breaking change 없음:

```python
class ZScoreDetectorAdapter:
    def detect(self, value: float, context: dict[str, Any] | None = None) -> tuple[bool, float]:
        return self._detector.is_anomaly(value)  # context 무시

    def update(self, value: float, context: dict[str, Any] | None = None) -> None:
        self._detector.is_anomaly(value)

    def reset(self) -> None:
        self._detector._values.clear()

    def get_feature_schema(self) -> dict[str, str] | None:
        return None  # 통계 기반 — context 불필요
```

---

## 11. Batch Processing — BatchCapable 마커 Protocol

### 11.1 문제

ML/딥러닝 프레임워크는 데이터를 건건이 처리하는 것보다 100~1000건을 텐서 배치로 묶어 연산할 때 수십 배 빠르다. 기존 `PredictiveForecasterService.ingest_metrics_batch()`([service.py L254](../../packages/selfhealing-python/src/selfhealing/services/predictive_forecaster/service.py))도 배치 API를 제공하지만, 내부적으로는 `for name, value in metrics.items(): self.ingest_metric(name, value)` 루프로 단건 호출한다.

### 11.2 선택: 별도 `BatchCapable` 마커 Protocol

**선택지 A**: 기존 Protocol에 `detect_batch()` 메서드를 필수로 추가
**선택지 B**: 별도 `BatchCapable` Protocol로 분리하여 `isinstance` 분기

→ **B안 채택**

**선택 이유**:
1. `ZScoreDetector`, `IQRDetector` 등 기존 통계 전략은 배치 연산의 이점이 없으므로 구현 의무를 부과하면 불필요한 코드 증가
2. `resilience/bulkhead/decorator.py`에서 `asyncio.iscoroutinefunction()` 분기로 sync/async를 자동 전환하는 기존 선례와 동일한 isinstance 분기 패턴
3. Protocol의 기본 구현은 `@runtime_checkable` 검사에서 무시되는 Python 특성상, 선택적 메서드를 기본 Protocol에 넣으면 타입 검증이 불완전해짐

### 11.3 BatchCapable Protocol

```python
@runtime_checkable
class BatchCapable(Protocol):
    """배치 추론 가능한 전략 마커 Protocol.

    ML/딥러닝 구현체가 텐서 배치 연산을 지원할 때 구현한다.
    통계 기반 전략(ZScore, IQR)은 구현 불필요.

    사용처:
        - CorrelationEngineService (배치 분기)
        - PredictiveForecasterService (배치 분기)
    """

    def detect_batch(
        self,
        values: list[float],
        contexts: list[dict[str, Any]] | None = None,
    ) -> list[tuple[bool, float]]:
        """배치 이상 탐지

        Args:
            values: 검사할 값 리스트
            contexts: 값별 메타데이터 리스트 (선택적)

        Returns:
            (is_anomalous, score) 튜플 리스트 (입력과 동일 순서)
        """
        ...

    def update_batch(self, values: list[float]) -> None:
        """배치 학습 데이터 추가"""
        ...
```

### 11.4 호출부 분기 패턴

```python
# CorrelationEngineService 또는 PredictiveForecasterService 내부
def _detect_anomalies(
    self,
    strategy: AnomalyDetectionStrategy,
    values: list[float],
    contexts: list[dict[str, Any]] | None = None,
) -> list[tuple[bool, float]]:
    """배치 가능 전략이면 배치 호출, 아니면 단건 루프."""
    if isinstance(strategy, BatchCapable):
        return strategy.detect_batch(values, contexts)
    else:
        ctx_list = contexts or [None] * len(values)
        return [strategy.detect(v, c) for v, c in zip(values, ctx_list)]
```

### 11.5 Micro-batching 오케스트레이션

`WildcardObserver`의 Producer-Consumer 패턴([wildcard_observer.py L152](../../packages/selfhealing-python/src/selfhealing/services/correlation_engine/wildcard_observer.py))을 확장하여, 이벤트를 짧은 시간 윈도우(10~50ms) 동안 큐에 모은 뒤 `detect_batch()`로 일괄 처리하는 마이크로배칭 메커니즘:

```python
class MicroBatchConsumer:
    """마이크로배칭 Consumer — WildcardObserver._consumer_loop() 패턴 확장.

    선례:
        - WildcardObserver._consumer_loop(): queue.get(timeout=1.0) 루프 + EventWindow 버퍼
        - AsyncHealingLogger: PriorityQueue + 배치 플러시 + BatchRetryPolicy
    """

    def __init__(
        self,
        strategy: AnomalyDetectionStrategy,
        flush_interval_ms: float = 50.0,
        max_batch_size: int = 128,
    ):
        self._strategy = strategy
        self._flush_interval = flush_interval_ms / 1000.0
        self._max_batch_size = max_batch_size
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._stop_event = threading.Event()

    def submit(self, value: float, context: dict[str, Any] | None = None) -> None:
        """Hot Path — O(1) 큐 삽입. WildcardObserver._on_event() 패턴."""
        try:
            self._queue.put_nowait((value, context))
        except queue.Full:
            pass  # Fail-Open: WildcardObserver._dropped_count 패턴

    def _consumer_loop(self) -> None:
        """daemon 스레드 — 마이크로배치 수집 + 일괄 추론."""
        batch_values: list[float] = []
        batch_contexts: list[dict[str, Any] | None] = []
        last_flush = time.monotonic()

        while not self._stop_event.is_set():
            try:
                value, ctx = self._queue.get(timeout=0.01)
                batch_values.append(value)
                batch_contexts.append(ctx)
            except queue.Empty:
                pass

            elapsed = time.monotonic() - last_flush
            should_flush = (
                len(batch_values) >= self._max_batch_size
                or (batch_values and elapsed >= self._flush_interval)
            )

            if should_flush:
                self._flush_batch(batch_values, batch_contexts)
                batch_values.clear()
                batch_contexts.clear()
                last_flush = time.monotonic()

    def _flush_batch(self, values, contexts):
        """배치 추론 실행 — BatchCapable 분기."""
        if isinstance(self._strategy, BatchCapable):
            self._strategy.detect_batch(values, contexts)
        else:
            for v, c in zip(values, contexts):
                self._strategy.detect(v, c)
```

적용 경로:
- **Hot Path** (실시간 이상 탐지): 10~50ms 마이크로배칭
- **Cold Path** (60초 tick 분석): 기존 `CoOccurrenceTracker.analyze_tick()` 패턴 유지

---

## 12. Strategy Lifecycle — 모델 로딩/웜업 + K8s Readiness 연동

### 12.1 문제

PyTorch/XGBoost 모델은 가중치 로드에 수 초~수십 초가 걸린다. `__init__`에서 로드하면 앱 시작 지연, 첫 `detect()` 시 Lazy Load하면 골든 타임 누락. 또한 K8s 로드밸런서가 모델 로딩 중인 파드로 트래픽을 보내면 인시던트 대응 실패.

### 12.2 선택: 별도 `StrategyLifecycle` Protocol 분리

**선택지 A**: 기존 ML Strategy Protocol에 `initialize()`, `warmup()` 필수 추가
**선택지 B**: 별도 `StrategyLifecycle` Protocol로 분리 (선택적 구현)

→ **B안 채택**

**선택 이유**:
1. `GracefulShutdownCoordinator`가 종료 라이프사이클을 `ShutdownHandler` ABC로 분리한 선례 ([core/shutdown_coordinator.py L73](../../packages/selfhealing-python/src/selfhealing/core/shutdown_coordinator.py)) — 시작 라이프사이클도 같은 패턴으로 분리
2. `ZScoreDetector`, `IQRDetector` 등 가벼운 통계 전략에 `initialize()`를 강제하면 불필요한 구현 부담
3. `BatchCapable` Protocol과 동일한 isinstance 분기 패턴으로 일관성 유지

### 12.3 StrategyLifecycle Protocol

```python
@runtime_checkable
class StrategyLifecycle(Protocol):
    """ML 전략 라이프사이클 관리 — 선택적 구현.

    가벼운 통계 전략(ZScore, IQR)은 구현 불필요.
    무거운 ML 모델(PyTorch, XGBoost, LLM)이 구현한다.

    선례:
        - ShutdownHandler ABC: on_shutdown_start(), on_drain_complete() (종료 라이프사이클)
        - ProviderRegistry.health_check_all(): 프로바이더 헬스체크 통합
        - HoltLinearForecaster: warmup_samples=30 미만 시 predict() → None (암묵적 웜업)
    """

    def initialize(self) -> None:
        """모델 가중치를 디스크 → 메모리(또는 GPU VRAM)로 로드.

        Orchestrator startup 시 호출. ShutdownHandler.on_shutdown_start()와 대칭.
        """
        ...

    def warmup(self) -> None:
        """더미 입력으로 첫 추론 실행.

        목적: JIT 컴파일(PyTorch), CUDA 커널 캐시, TensorRT 엔진 빌드 등.
        initialize() 이후, 첫 실제 detect() 전에 호출.
        """
        ...

    def is_ready(self) -> bool:
        """추론 준비 완료 여부.

        K8s Readiness Probe와 연동된다.
        반드시 O(1) 캐시된 boolean 반환이어야 함
        (readinessProbe.timeoutSeconds=3 제약).
        """
        ...

    def teardown(self) -> None:
        """리소스 해제 (GPU VRAM, 임시 파일).

        GracefulShutdownCoordinator.initiate_shutdown() 시 호출.
        """
        ...
```

### 12.4 K8s Readiness Probe 연동

현재 `HealthCheckService.get_readiness()`([services/health_check.py L253](../../packages/selfhealing-python/src/selfhealing/services/health_check.py))는 DB 연결만 확인한다:

```python
# 현재: DB만 체크
def get_readiness(self) -> ReadinessStatus:
    db_checks = self.check_all_databases()
    for db_check in db_checks:
        if not db_check.is_connected:
            ready = False
```

ML Strategy readiness를 추가:

```python
# 확장: DB + ML Strategy 체크
def get_readiness(self) -> ReadinessStatus:
    db_checks = self.check_all_databases()
    checks = {}
    ready = True

    # 기존 DB 체크
    for db_check in db_checks:
        key = f"database_{db_check.alias}"
        if db_check.is_connected:
            checks[key] = "ready"
        else:
            checks[key] = "not_ready"
            ready = False

    # ML Strategy readiness 체크 (신규)
    for name, strategy in self._get_ml_strategies():
        if isinstance(strategy, StrategyLifecycle):
            if strategy.is_ready():
                checks[f"ml_{name}"] = "ready"
            else:
                checks[f"ml_{name}"] = "not_ready"
                ready = False

    return ReadinessStatus(
        status="ready" if ready else "not_ready",
        checks=checks,
        is_ready=ready,
    )
```

K8s 프로브 설정([k8s/django-api-deployment.yaml L126](../../k8s/django-api-deployment.yaml))은 변경 불필요:

```yaml
# 기존 설정 그대로 사용 — startupProbe가 ML 모델 로딩 시간을 커버
readinessProbe:
  httpGet: { path: /health/ready/, port: 8000 }
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3       # is_ready()는 O(1) boolean이므로 충분
  failureThreshold: 3

startupProbe:
  httpGet: { path: /health/live/, port: 8000 }
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 30    # 150초까지 대기 — 대형 모델 로딩 커버
```

### 12.5 Orchestrator Startup 통합

```python
class CorrelationEngineService:
    def startup(self) -> None:
        """앱 시작 시 1회 호출 — Django AppConfig.ready()에서.

        순서: initialize() → warmup() → is_ready() 검증.
        ShutdownHandler 등록 패턴과 대칭.
        """
        strategies = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
            ("graph_build", self._graph_strategy),
        ]

        for name, strategy in strategies:
            if isinstance(strategy, StrategyLifecycle):
                logger.info(f"[CorrelationEngine] Initializing ML strategy: {name}")
                strategy.initialize()
                strategy.warmup()
                if not strategy.is_ready():
                    logger.error(
                        f"[CorrelationEngine] Strategy '{name}' failed readiness check"
                    )

    def shutdown(self) -> None:
        """GracefulShutdownCoordinator에서 호출."""
        for name, strategy in [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
            ("graph_build", self._graph_strategy),
        ]:
            if isinstance(strategy, StrategyLifecycle):
                strategy.teardown()
```

---

## 13. Fallback Observability — 분석 결과 투명성

### 13.1 문제

LLM 전략이 타임아웃으로 실패하여 `DefaultRootCauseRanker`로 폴백된 경우, 최종 `RootCauseAnalysis`에 이 사실이 기록되지 않으면:
1. 고객이 분석 결과의 신뢰도를 판단할 수 없음
2. SRE 팀이 LLM 인프라 가용성을 추적할 수 없음
3. 감사(Audit) 로그에 "정상 분석"으로 기록되어 규정 준수 문제 발생

### 13.2 근거: 기존 패턴과의 일관성

| 기존 구현 | 투명성 메커니즘 | 위치 |
|-----------|---------------|------|
| `FallbackPolicy` | `metadata: {"fallback_used": True, "original_error": str}` | [resilience/policies/fallback.py L173](../../packages/selfhealing-python/src/selfhealing/resilience/policies/fallback.py) |
| `FallbackResult` | `used_fallback: bool`, `fallback_mode: FallbackMode` | [core/fallback_strategy.py L38](../../packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py) |
| `PolicyResult` | `outcome: SUCCESS_WITH_FALLBACK` (성공과 구별) | [interfaces/resilience_policy.py L72](../../packages/selfhealing-python/src/selfhealing/interfaces/resilience_policy.py) |
| `HealthCheckService` | `status: "healthy" \| "degraded" \| "unhealthy"` | [services/health_check.py L65](../../packages/selfhealing-python/src/selfhealing/services/health_check.py) |
| `BulkheadPolicy` | `metadata: {"bulkhead_name", "state": {utilization_percent}}` | [resilience/bulkhead/policy.py](../../packages/selfhealing-python/src/selfhealing/resilience/bulkhead/policy.py) |

`RootCauseAnalysis`만 이 투명성 원칙을 따르지 않으면 설계 일관성이 깨진다.

### 13.3 RootCauseAnalysis 확장

현재 `RootCauseAnalysis`([root_cause_ranker.py L125](../../packages/selfhealing-python/src/selfhealing/services/correlation_engine/root_cause_ranker.py)):

```python
@dataclass
class RootCauseAnalysis:
    incident_id: str
    analyzed_at: float
    dag: EventDAG
    candidates: list[RootCauseCandidate]
    primary_cause: RootCauseCandidate
    confidence: float
    summary: str
```

확장:

```python
@dataclass
class StrategyMetadata:
    """분석에 사용된 전략의 투명성 메타데이터.

    FallbackPolicy의 metadata 패턴과 PolicyResult의 outcome 패턴을 준수한다.
    """

    strategy_name: str
    """실제 사용된 전략 이름 ("default_ranker", "llm_gpt4", "bayesian" 등)"""

    fallback_used: bool = False
    """Fallback 발동 여부. PolicyResult의 SUCCESS_WITH_FALLBACK 구분과 동일."""

    fallback_reason: str | None = None
    """Fallback 사유 ("LLM timeout after 30s", "API rate limit 429" 등).
    FallbackPolicy.metadata["original_error"] 패턴과 동일."""

    primary_strategy_name: str | None = None
    """원래 의도된 주 전략 이름 (Fallback 발동 시에만 설정)"""

    analysis_duration_ms: float = 0.0
    """분석 소요 시간 (밀리초). PolicyResult.total_duration_ms 패턴."""

    model_version: str | None = None
    """ML 모델 버전 (MLOps 추적용)"""


@dataclass
class RootCauseAnalysis:
    """근본 원인 분석 결과 전체."""

    incident_id: str
    analyzed_at: float
    dag: EventDAG
    candidates: list[RootCauseCandidate]
    primary_cause: RootCauseCandidate
    confidence: float
    summary: str

    # 전략 투명성 메타데이터 (신규)
    strategy_metadata: StrategyMetadata | None = None
    """분석에 사용된 전략 정보. None이면 기본 전략 사용 (하위 호환)."""
```

### 13.4 CorrelationEngineService Fallback 통합

기존 `FallbackPolicy`([resilience/policies/fallback.py](../../packages/selfhealing-python/src/selfhealing/resilience/policies/fallback.py))를 재사용하여 LLM 타임아웃 + 폴백을 조합:

```python
import time
from selfhealing.resilience.policies.fallback import FallbackPolicy
from selfhealing.resilience.bulkhead.exceptions import (
    BulkheadFullError,
    BulkheadTimeoutError,
)

class CorrelationEngineService:
    def set_root_cause_strategy(
        self,
        primary: RootCauseStrategy,
        fallback: RootCauseStrategy | None = None,
    ) -> None:
        """주 전략 + 대체 전략 설정."""
        self._root_cause_strategy = primary
        self._root_cause_fallback = fallback or DefaultRootCauseRanker()
        self._primary_strategy_name = type(primary).__name__
        self._fallback_strategy_name = type(self._root_cause_fallback).__name__

    def _analyze_root_cause(
        self,
        dag: EventDAG,
        co_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """Fallback + Bulkhead 내장 근본 원인 분석."""
        start_ms = time.monotonic() * 1000

        try:
            # ML Bulkhead 내에서 주 전략 실행
            result = self._execute_with_ml_bulkhead(
                self._root_cause_strategy.rank_causes, dag, co_data,
            )
            duration_ms = time.monotonic() * 1000 - start_ms

            # 성공 — 전략 메타데이터 첨부
            result.strategy_metadata = StrategyMetadata(
                strategy_name=self._primary_strategy_name,
                fallback_used=False,
                analysis_duration_ms=duration_ms,
            )
            return result

        except (BulkheadFullError, BulkheadTimeoutError, Exception) as e:
            # Fallback — FallbackPolicy.metadata 패턴 준수
            logger.warning(
                f"[CorrelationEngine] Primary strategy failed, "
                f"falling back: {type(e).__name__}: {e}"
            )
            result = self._root_cause_fallback.rank_causes(dag, co_data)
            duration_ms = time.monotonic() * 1000 - start_ms

            result.strategy_metadata = StrategyMetadata(
                strategy_name=self._fallback_strategy_name,
                fallback_used=True,
                fallback_reason=f"{type(e).__name__}: {str(e)[:200]}",
                primary_strategy_name=self._primary_strategy_name,
                analysis_duration_ms=duration_ms,
            )
            return result
```

### 13.5 결과 직렬화 및 감사 로그

```python
# RootCauseAnalysis.to_dict() 확장
def to_dict(self) -> dict[str, Any]:
    result = {
        "incident_id": self.incident_id,
        "analyzed_at": self.analyzed_at,
        "confidence": self.confidence,
        "summary": self.summary,
        "candidates": [c.to_dict() for c in self.candidates],
    }
    if self.strategy_metadata:
        result["strategy_metadata"] = {
            "strategy_name": self.strategy_metadata.strategy_name,
            "fallback_used": self.strategy_metadata.fallback_used,
            "fallback_reason": self.strategy_metadata.fallback_reason,
            "primary_strategy_name": self.strategy_metadata.primary_strategy_name,
            "analysis_duration_ms": self.strategy_metadata.analysis_duration_ms,
            "model_version": self.strategy_metadata.model_version,
        }
    return result
```

감사 로그 예시:
```json
{
  "incident_id": "INC-2026-0221-001",
  "confidence": 0.72,
  "strategy_metadata": {
    "strategy_name": "DefaultRootCauseRanker",
    "fallback_used": true,
    "fallback_reason": "BulkheadTimeoutError: ml_inference timeout after 30.0s",
    "primary_strategy_name": "LLMRootCauseAnalyzer",
    "analysis_duration_ms": 30142.5,
    "model_version": null
  }
}
```

---

## 14. 종합 아키텍처 — 전략 실행 파이프라인

```
[EventBus] ──publish()──▶ [WildcardObserver._on_event()]
                              │ queue.put_nowait() O(1)
                              ▼
                        [Consumer Thread]
                              │ queue.get(timeout)
                              ▼
                  ┌─── BatchCapable? ───┐
                  │ Yes                  │ No
                  ▼                      ▼
            MicroBatchConsumer     detect(value, context)
            detect_batch([...])
                  │
                  ▼
        [CorrelationEngineService]
                  │
         ┌────────┴────────┐
         ▼                  ▼
  [CorrelationStrategy]  [GraphBuildStrategy]
  analyze_tick()          build_dag()
         │                  │
         └────────┬─────────┘
                  ▼
         [RootCauseStrategy]
                  │
    ┌─── StrategyLifecycle? ───┐
    │ Yes: is_ready()?          │ No
    │   └─ True ──┐             │
    │              ▼             │
    │   [ML Bulkhead]           │
    │   ThreadPoolBulkhead      │
    │   (max_workers=3)         │
    │              │             │
    │         rank_causes()     rank_causes()
    │              │             │
    │     ┌── 성공? ──┐         │
    │     │ Yes      │ No       │
    │     ▼          ▼          │
    │  Result    [Fallback]     │
    │              │             │
    │     DefaultRootCauseRanker│
    │              │             │
    └──────────────┴─────────────┘
                  │
                  ▼
         RootCauseAnalysis
         + StrategyMetadata
         (fallback_used, fallback_reason,
          strategy_name, analysis_duration_ms)
                  │
                  ▼
    [HealthCheckService.get_readiness()]
    checks: {database_default, ml_root_cause, ...}
         │
         ▼
    K8s /health/ready/ → 200 OK / 503
```
