"""
Correlation Engine Package — 이벤트 인과관계 분석 엔진.

이벤트 버스에서 발생하는 이벤트를 수집하고,
서비스 의존성 그래프와 교차하여 방향성 비순환 그래프(DAG)를 자동 구축한다.

Modules:
    - event_graph: EventNode, CausalEdge, EventDAG 자료구조
    - event_graph_builder: EventGraphBuilder DAG 구축 엔진
    - event_graph_trigger: EventGraphTrigger DAG 빌드 트리거
    - co_occurrence_tracker: 이벤트 동시 발생 패턴 추적 및 이상 탐지기

Usage:
    from selfhealing.services.correlation_engine.event_graph import (
        EventDAG,
        EventNode,
        CausalEdge,
    )
    from selfhealing.services.correlation_engine.event_graph_builder import (
        EventGraphBuilder,
    )
    from selfhealing.services.correlation_engine.co_occurrence_tracker import (
        CoOccurrenceTracker,
        EventPairKey,
        CoOccurrenceRecord,
        CorrelationResult,
    )
    from selfhealing.services.correlation_engine.root_cause_ranker import (
        RootCauseRanker,
        RootCauseCandidate,
        RootCauseAnalysis,
    )
"""
