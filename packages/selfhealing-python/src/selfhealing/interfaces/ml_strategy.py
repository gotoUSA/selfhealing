"""
ML Strategy Interfaces — AI/ML 확장 기반.

시스템 전체에서 공유 가능한 ML Strategy Protocol 정의.
기존 통계 기반 구현(ZScore, IQR, Holt-Winters)을 AI/ML 모델로
교체할 수 있는 확장 포인트를 제공한다.

Protocol(Duck typing) 기반으로 설계하여:
    - 구매자가 어떤 ML 프레임워크(scikit-learn, PyTorch, TensorFlow)를 사용해도 무관
    - @runtime_checkable로 런타임 타입 검증 가능
    - 기존 클래스에 메서드만 추가하면 호환 (상속 불필요)

Usage:
    from selfhealing.interfaces.ml_strategy import (
        AnomalyDetectionStrategy,
        ForecastStrategy,
        ClassificationStrategy,
        BatchCapable,
        StrategyLifecycle,
    )

    # Protocol 구현 여부 확인
    if isinstance(my_detector, AnomalyDetectionStrategy):
        is_anomaly, score = my_detector.detect(value)

    # 배치 처리 가능 여부 확인
    if isinstance(my_detector, BatchCapable):
        results = my_detector.detect_batch(values)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# =============================================================================
# AnomalyDetectionStrategy — 이상 탐지 전략
# =============================================================================


@runtime_checkable
class AnomalyDetectionStrategy(Protocol):
    """이상 탐지 전략 — 통계/ML/딥러닝 교체 가능.

    기본 제공:
        - ZScoreDetector: Z-Score 기반
        - IQRDetector: IQR 기반

    구매자 확장 예시:
        - IsolationForestDetector: scikit-learn Isolation Forest
        - AutoencoderDetector: PyTorch Autoencoder 기반
        - ProphetDetector: Facebook Prophet 기반 계절성 인지 이상 탐지

    사용처:
        - PredictiveForecasterService (메트릭 이상 탐지)
        - CoOccurrenceTracker (동시발생 빈도 이상 탐지)
        - CorruptionShield L3 (데이터 이상 탐지)
    """

    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        """단일 값의 이상 여부 판단.

        Args:
            value: 검사할 값
            context: ML 모델에 전달할 다차원 특성 (선택적).
                기본 통계 전략(ZScore, IQR)은 무시한다.
                ML 구현체는 이 dict에서 필요한 특성을 추출한다.

        Returns:
            (is_anomalous, score): 이상 여부와 이상 점수.
            score는 정규화 불필요 — 전략별 자유 (ZScore, probability 등).
        """
        ...

    def update(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """학습 데이터 추가 (온라인 학습).

        Args:
            value: 새로운 관측값
            context: ML 모델에 전달할 다차원 특성 (선택적)
        """
        ...

    def reset(self) -> None:
        """학습 상태 초기화."""
        ...

    def get_feature_schema(self) -> dict[str, str] | None:
        """이 전략이 기대하는 context 키/타입 스키마 반환.

        Returns:
            {"service_name": "str", "cpu_usage": "float", ...} 형태.
            스키마가 없으면 None (통계 기반 전략).
            Orchestrator가 사전에 입력 유효성을 검증하는 데 사용.
        """
        ...


# =============================================================================
# ForecastStrategy — 시계열 예측 전략
# =============================================================================


@runtime_checkable
class ForecastStrategy(Protocol):
    """시계열 예측 전략.

    기본 제공:
        - HoltLinearForecaster: 이중지수평활

    구매자 확장 예시:
        - ProphetForecaster: Facebook Prophet
        - LSTMForecaster: PyTorch LSTM
        - ARIMAForecaster: statsmodels ARIMA

    사용처:
        - PredictiveForecasterService (메트릭 예측)
        - CoOccurrenceTracker (동시발생 빈도 트렌드)
    """

    def update(self, value: float) -> float:
        """새 관측값으로 모델 업데이트.

        Args:
            value: 새로운 관측값

        Returns:
            현재 레벨 (smoothed value)
        """
        ...

    def predict(self, steps_ahead: int = 1) -> float | None:
        """미래 값 예측.

        Args:
            steps_ahead: 예측할 미래 스텝 수

        Returns:
            예측값. 데이터 부족 시 None.
        """
        ...

    def get_confidence(self) -> float:
        """현재 모델의 신뢰도 (0.0 ~ 1.0)."""
        ...


# =============================================================================
# ClassificationStrategy — 분류 전략
# =============================================================================


@runtime_checkable
class ClassificationStrategy(Protocol):
    """분류 전략 — 이벤트/스파이크 유형 분류.

    기본 제공:
        - SpikeClassifier: 규칙 기반 분류

    구매자 확장 예시:
        - RandomForestClassifier: scikit-learn RF
        - XGBoostClassifier: XGBoost
        - NeuralClassifier: PyTorch NN

    사용처:
        - PredictiveForecasterService (스파이크 분류)
        - CorrelationEngine (이벤트 패턴 분류)
    """

    def classify(
        self,
        features: dict[str, float],
        context: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        """특성 벡터 → 클래스 레이블 + 확신도.

        Args:
            features: 특성 이름 → 값 매핑
            context: ML 모델에 전달할 추가 메타데이터 (선택적)

        Returns:
            (label, confidence): 분류 레이블과 확신도 (0.0 ~ 1.0)
        """
        ...


# =============================================================================
# BatchCapable — 배치 추론 마커 Protocol
# =============================================================================


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
        """배치 이상 탐지.

        Args:
            values: 검사할 값 리스트
            contexts: 값별 메타데이터 리스트 (선택적)

        Returns:
            (is_anomalous, score) 튜플 리스트 (입력과 동일 순서)
        """
        ...

    def update_batch(self, values: list[float]) -> None:
        """배치 학습 데이터 추가."""
        ...


# =============================================================================
# StrategyLifecycle — 모델 로딩/웜업 + K8s Readiness 연동
# =============================================================================


@runtime_checkable
class StrategyLifecycle(Protocol):
    """ML 전략 라이프사이클 관리 — 선택적 구현.

    가벼운 통계 전략(ZScore, IQR)은 구현 불필요.
    무거운 ML 모델(PyTorch, XGBoost, LLM)이 구현한다.

    선례:
        - ShutdownHandler ABC: on_shutdown_start(), on_drain_complete()
        - ProviderRegistry.health_check_all(): 프로바이더 헬스체크 통합
        - HoltLinearForecaster: warmup_samples=30 미만 시 predict() → None
    """

    def initialize(self) -> None:
        """모델 가중치를 디스크 → 메모리(또는 GPU VRAM)로 로드.

        Orchestrator startup 시 호출.
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
