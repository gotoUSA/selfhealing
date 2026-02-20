"""
Correlation Engine Service — 전략 교체 가능한 오케스트레이터.

Correlation Engine의 각 분석 단계를 Strategy Protocol로 추상화하여,
기본 제공 알고리즘을 AI/ML 모델로 교체할 수 있는 오케스트레이터이다.

전략 패턴:
    - CorrelationStrategy: 상관관계 분석 (기본: CoOccurrenceTracker)
    - RootCauseStrategy: 근본 원인 분석 (기본: RootCauseRanker)
    - GraphBuildStrategy: DAG 구축 (기본: EventGraphBuilder)

ML 추론 격리:
    - BulkheadRegistry의 "ml_inference" 도메인을 사용하여
      ML/LLM 추론을 전용 스레드 풀에서 격리 실행한다.
    - BulkheadFullError/BulkheadTimeoutError 발생 시 Fallback 전략으로 전환한다.

Usage:
    from selfhealing.services.correlation_engine.service import (
        CorrelationEngineService,
    )

    service = CorrelationEngineService(settings)
    service.set_root_cause_strategy(LLMRootCauseAnalyzer(api_key="..."))
"""

from __future__ import annotations

import logging
import time
from typing import Any

from selfhealing.interfaces.ml_strategy import (
    AnomalyDetectionStrategy,
    BatchCapable,
    StrategyLifecycle,
)
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
    CorrelationResult,
)
from selfhealing.services.correlation_engine.event_graph import EventDAG
from selfhealing.services.correlation_engine.interfaces import (
    CorrelationStrategy,
    GraphBuildStrategy,
    RootCauseStrategy,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
    RootCauseRanker,
    StrategyMetadata,
)
from selfhealing.settings.correlation import CorrelationSettings

logger = logging.getLogger(__name__)


# =============================================================================
# ML 추론 인시던트 심각도별 우선순위 watermark
# =============================================================================

ML_PRIORITY_WATERMARKS: dict[str, float] = {
    "critical": 0.0,  # 항상 처리
    "standard": 0.4,  # 토큰 40% 이상 시 처리
    "background": 0.7,  # 토큰 70% 이상 시만 처리 (학습 등)
}
"""RateController의 PRIORITY_WATERMARKS 패턴 적용."""


class CorrelationEngineService:
    """전략 교체 가능한 Correlation Engine 오케스트레이터.

    각 분석 단계를 Strategy Protocol로 추상화하여,
    기본 제공 알고리즘을 AI/ML 모델로 교체할 수 있다.

    ML 추론은 BulkheadRegistry의 "ml_inference" 격벽에서 격리 실행되며,
    실패 시 Fallback 전략으로 자동 전환한다.
    """

    def __init__(self, settings: CorrelationSettings) -> None:
        self._settings = settings

        # 기본 전략 (Day-1 제공)
        self._correlation_strategy: CorrelationStrategy = CoOccurrenceTracker(settings)
        self._root_cause_strategy: RootCauseStrategy = RootCauseRanker()

        # Fallback 전략 (기본: RootCauseRanker)
        self._root_cause_fallback: RootCauseStrategy = RootCauseRanker()
        self._primary_strategy_name: str = type(self._root_cause_strategy).__name__
        self._fallback_strategy_name: str = type(self._root_cause_fallback).__name__

        # GraphBuildStrategy는 EventGraphBuilder 의존성이 있으므로 지연 초기화
        self._graph_strategy: GraphBuildStrategy | None = None

        # ML 추론 격벽 — 지연 초기화 (BulkheadRegistry import 비용 절감)
        self._ml_bulkhead: Any = None

    def _get_ml_bulkhead(self) -> Any:
        """ML 추론 격벽을 지연 초기화하여 반환."""
        if self._ml_bulkhead is None:
            from selfhealing.resilience.bulkhead.registry import (
                get_bulkhead_registry,
            )
            from selfhealing.settings.bulkhead import get_bulkhead_settings

            bh_settings = get_bulkhead_settings()
            registry = get_bulkhead_registry()
            self._ml_bulkhead = registry.get_or_create(
                name="ml_inference",
                max_concurrent=bh_settings.ml_inference_max_workers,
                bulkhead_type="thread_pool",
            )
        return self._ml_bulkhead

    # ─────────────────────────────────────────────
    # 전략 교체 API
    # ─────────────────────────────────────────────

    def set_correlation_strategy(self, strategy: CorrelationStrategy) -> None:
        """상관관계 분석 전략 교체."""
        if not isinstance(strategy, CorrelationStrategy):
            raise TypeError(f"CorrelationStrategy Protocol을 구현해야 합니다: " f"{type(strategy).__name__}")
        self._correlation_strategy = strategy

    def set_root_cause_strategy(
        self,
        primary: RootCauseStrategy,
        fallback: RootCauseStrategy | None = None,
    ) -> None:
        """근본 원인 분석 전략 교체.

        Args:
            primary: 주 전략
            fallback: 대체 전략 (None이면 기본 RootCauseRanker)
        """
        if not isinstance(primary, RootCauseStrategy):
            raise TypeError(f"RootCauseStrategy Protocol을 구현해야 합니다: " f"{type(primary).__name__}")
        self._root_cause_strategy = primary
        self._root_cause_fallback = fallback or RootCauseRanker()
        self._primary_strategy_name = type(primary).__name__
        self._fallback_strategy_name = type(self._root_cause_fallback).__name__

    def set_graph_strategy(self, strategy: GraphBuildStrategy) -> None:
        """DAG 구축 전략 교체."""
        if not isinstance(strategy, GraphBuildStrategy):
            raise TypeError(f"GraphBuildStrategy Protocol을 구현해야 합니다: " f"{type(strategy).__name__}")
        self._graph_strategy = strategy

    # ─────────────────────────────────────────────
    # 전략 접근자
    # ─────────────────────────────────────────────

    @property
    def correlation_strategy(self) -> CorrelationStrategy:
        """현재 상관관계 분석 전략."""
        return self._correlation_strategy

    @property
    def root_cause_strategy(self) -> RootCauseStrategy:
        """현재 근본 원인 분석 전략."""
        return self._root_cause_strategy

    @property
    def graph_strategy(self) -> GraphBuildStrategy | None:
        """현재 DAG 구축 전략."""
        return self._graph_strategy

    # ─────────────────────────────────────────────
    # ML Bulkhead 격리 실행
    # ─────────────────────────────────────────────

    def _execute_with_ml_bulkhead(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """ML/LLM 추론을 격벽 내에서 실행.

        ThreadPoolBulkhead.execute()가 타임아웃 + 큐 제한을 처리한다.
        BulkheadFullError 발생 시 호출부에서 Fallback으로 전환한다.
        """
        from selfhealing.settings.bulkhead import get_bulkhead_settings

        timeout = get_bulkhead_settings().ml_inference_timeout
        bulkhead = self._get_ml_bulkhead()
        return bulkhead.execute(fn, *args, timeout=timeout, **kwargs)

    # ─────────────────────────────────────────────
    # 근본 원인 분석 (Fallback 내장)
    # ─────────────────────────────────────────────

    def analyze_root_cause(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        """Fallback + Bulkhead 내장 근본 원인 분석.

        주 전략을 ML Bulkhead 내에서 실행하고,
        실패 시 Fallback 전략으로 자동 전환한다.

        Args:
            dag: 이벤트 인과관계 그래프
            co_occurrence_data: 동시발생 통계 분석 결과

        Returns:
            근본 원인 분석 결과 (StrategyMetadata 포함)
        """
        from selfhealing.resilience.bulkhead.exceptions import (
            BulkheadFullError,
            BulkheadTimeoutError,
        )

        start_ms = time.monotonic() * 1000

        try:
            # ML Bulkhead 내에서 주 전략 실행
            result = self._execute_with_ml_bulkhead(
                self._root_cause_strategy.rank_causes,
                dag,
                co_occurrence_data,
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
            logger.warning(f"[CorrelationEngine] Primary strategy failed, " f"falling back: {type(e).__name__}: {e}")
            result = self._root_cause_fallback.rank_causes(dag, co_occurrence_data)
            duration_ms = time.monotonic() * 1000 - start_ms

            result.strategy_metadata = StrategyMetadata(
                strategy_name=self._fallback_strategy_name,
                fallback_used=True,
                fallback_reason=f"{type(e).__name__}: {str(e)[:200]}",
                primary_strategy_name=self._primary_strategy_name,
                analysis_duration_ms=duration_ms,
            )
            return result

    # ─────────────────────────────────────────────
    # 배치 이상 탐지 (BatchCapable 분기)
    # ─────────────────────────────────────────────

    @staticmethod
    def detect_anomalies(
        strategy: AnomalyDetectionStrategy,
        values: list[float],
        contexts: list[dict[str, Any]] | None = None,
    ) -> list[tuple[bool, float]]:
        """배치 가능 전략이면 배치 호출, 아니면 단건 루프.

        Args:
            strategy: 이상 탐지 전략
            values: 검사할 값 리스트
            contexts: 값별 메타데이터 리스트 (선택적)

        Returns:
            (is_anomalous, score) 튜플 리스트
        """
        if isinstance(strategy, BatchCapable):
            return strategy.detect_batch(values, contexts)
        ctx_list = contexts or [None] * len(values)
        return [strategy.detect(v, c) for v, c in zip(values, ctx_list)]

    # ─────────────────────────────────────────────
    # 라이프사이클 관리
    # ─────────────────────────────────────────────

    def startup(self) -> None:
        """앱 시작 시 1회 호출 — Django AppConfig.ready()에서.

        순서: initialize() → warmup() → is_ready() 검증.
        """
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))

        for name, strategy in strategies:
            if isinstance(strategy, StrategyLifecycle):
                logger.info(f"[CorrelationEngine] Initializing ML strategy: {name}")
                strategy.initialize()
                strategy.warmup()
                if not strategy.is_ready():
                    logger.error(f"[CorrelationEngine] Strategy '{name}' " f"failed readiness check")

    def shutdown(self) -> None:
        """GracefulShutdownCoordinator에서 호출."""
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))

        for name, strategy in strategies:
            if isinstance(strategy, StrategyLifecycle):
                logger.info(f"[CorrelationEngine] Tearing down ML strategy: {name}")
                strategy.teardown()

    def get_ml_strategies(self) -> list[tuple[str, Any]]:
        """등록된 ML 전략 목록.

        HealthCheckService.get_readiness()에서 ML 전략 readiness를
        확인하는 데 사용한다.

        Returns:
            (이름, 전략) 튜플 리스트
        """
        strategies: list[tuple[str, Any]] = [
            ("correlation", self._correlation_strategy),
            ("root_cause", self._root_cause_strategy),
        ]
        if self._graph_strategy is not None:
            strategies.append(("graph_build", self._graph_strategy))
        return strategies
