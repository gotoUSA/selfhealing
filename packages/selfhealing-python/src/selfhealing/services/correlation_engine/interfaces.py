"""
Correlation Engine Interfaces — 상관관계 분석 전략 Protocol.

CorrelationEngine 전용 전략 인터페이스를 정의한다.
기본 제공 알고리즘(CoOccurrenceTracker, RootCauseRanker, EventGraphBuilder)을
AI/ML 모델로 교체할 수 있는 확장 포인트이다.

시스템 전체 공유 Protocol은 selfhealing.interfaces.ml_strategy에 정의되어 있다.

Usage:
    from selfhealing.services.correlation_engine.interfaces import (
        CorrelationStrategy,
        RootCauseStrategy,
        GraphBuildStrategy,
    )

    class GrangerCausalityStrategy:
        def analyze(self, event_pairs, time_window): ...
        def get_pair_score(self, event_type_a, event_type_b): ...

    # Protocol 구현 확인
    assert isinstance(GrangerCausalityStrategy(), CorrelationStrategy)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from selfhealing.services.correlation_engine.co_occurrence_tracker import (
        CorrelationResult,
    )
    from selfhealing.services.correlation_engine.event_graph import EventDAG
    from selfhealing.services.correlation_engine.root_cause_ranker import (
        RootCauseAnalysis,
    )


# =============================================================================
# CorrelationStrategy — 상관관계 분석 전략
# =============================================================================


@runtime_checkable
class CorrelationStrategy(Protocol):
    """상관관계 분석 전략.

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
        event_pairs: list[tuple[str, str, float]],
        time_window: float,
    ) -> list[CorrelationResult]:
        """이벤트 쌍 목록 → 상관관계 결과.

        Args:
            event_pairs: (이벤트타입A, 이벤트타입B, 시간간격) 튜플 리스트
            time_window: 분석 시간 윈도우 (초)

        Returns:
            상관관계 결과 리스트 (CorrelationResult)
        """
        ...

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        """특정 이벤트 쌍의 현재 상관 점수.

        Returns:
            0.0 ~ 1.0 점수. 데이터 부족 시 None.
        """
        ...


# =============================================================================
# RootCauseStrategy — 근본 원인 분석 전략
# =============================================================================


@runtime_checkable
class RootCauseStrategy(Protocol):
    """근본 원인 분석 전략.

    기본 제공:
        - RootCauseRanker: DAG 토폴로지 + 시간순 + BlastRadius 가중합

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
        """DAG + 동시발생 데이터 → 근본 원인 순위.

        Args:
            dag: 이벤트 인과관계 그래프
            co_occurrence_data: 동시발생 통계 분석 결과

        Returns:
            근본 원인 분석 결과 (순위 포함)
        """
        ...


# =============================================================================
# GraphBuildStrategy — DAG 구축 전략
# =============================================================================


@runtime_checkable
class GraphBuildStrategy(Protocol):
    """DAG 구축 전략.

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
        events: list,
        window_seconds: float,
    ) -> EventDAG:
        """이벤트 목록 → 인과관계 DAG.

        Args:
            events: 시간 윈도우 내 이벤트 목록 (SelfHealingEvent 또는 ObservedEvent)
            window_seconds: 분석 윈도우 크기

        Returns:
            EventDAG 인스턴스
        """
        ...
