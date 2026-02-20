"""
Correlation Engine Package — 이벤트 인과관계 분석 엔진.

이벤트 버스에서 발생하는 이벤트를 수집하고,
서비스 의존성 그래프와 교차하여 방향성 비순환 그래프(DAG)를 자동 구축한다.

Modules:
    - event_graph: EventDAG 자료구조 및 EventGraphBuilder
    - co_occurrence_tracker: 이벤트 동시 발생 패턴 추적기

Usage:
    from selfhealing.services.correlation_engine.event_graph import (
        EventGraphBuilder,
        EventDAG,
        EventNode,
        CausalEdge,
    )
"""
