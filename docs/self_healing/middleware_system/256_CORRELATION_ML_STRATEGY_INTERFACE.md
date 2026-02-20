# 256. ML Strategy Interface — AI/ML 확장 기반

> **Version**: 1.0.0
> **Created**: 2026-02-20
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
