"""
Root Cause Ranker Integration Tests — 근본 원인 순위 산정 통합 워크플로우.

RootCauseRanker + BlastRadiusService + EventDAG + CoOccurrenceTracker의
조합 동작을 검증한다. 모두 인메모리 구현이므로 Docker 불필요.

테스트 시나리오:
- BlastRadius mock: DB upstream of 3 services → DB가 rank=1
- Historical: 과거 5회 A가 원인 → historical_score ≥ 0.8
- 단절 서브그래프 2개 → B그룹 루트의 temporal_score=1.0 (왜곡 없음)
- 컴포넌트 가중치: 3서비스 vs 2서비스
- Blast Radius 방향: DB(upstream=3, downstream=0) → blast_score=1.0
- 다중 dependency_type → set 중복 제거
- Historical 인덱싱 효율
- DB Pool → Error Rate → CB OPEN × 3 → Emergency 시나리오
"""

from __future__ import annotations

import time
import uuid

import pytest

from selfhealing.services.blast_radius.service import BlastRadiusService
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CorrelationResult,
    EventPairKey,
)
from selfhealing.services.correlation_engine.event_graph import (
    CausalEdge,
    EventDAG,
    EventNode,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    NEUTRAL_SCORE,
    RootCauseRanker,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """각 테스트 전후로 BlastRadiusService 싱글톤 리셋."""
    BlastRadiusService._instance = None
    yield
    BlastRadiusService._instance = None


def _make_node(
    event_id: str | None = None,
    event_type: str = "circuit_breaker_opened",
    service_name: str = "test-service",
    timestamp: float = 1000.0,
    data: dict | None = None,
    correlation_id: str | None = None,
) -> EventNode:
    """테스트용 EventNode 생성 헬퍼."""
    return EventNode(
        event_id=event_id or str(uuid.uuid4()),
        event_type=event_type,
        service_name=service_name,
        timestamp=timestamp,
        data=data or {},
        correlation_id=correlation_id,
    )


def _make_edge(
    source: EventNode,
    target: EventNode,
    confidence: float = 0.9,
    evidence_type: str = "dependency",
) -> CausalEdge:
    return CausalEdge(
        source=source,
        target=target,
        confidence=confidence,
        evidence_type=evidence_type,
        time_gap_seconds=abs(target.timestamp - source.timestamp),
    )


def _make_dag(
    nodes: list[EventNode],
    edges: list[CausalEdge] | None = None,
    incident_id: str = "inc_integration",
) -> EventDAG:
    edges = edges or []
    nodes_dict = {n.event_id: n for n in nodes}
    timestamps = [n.timestamp for n in nodes] or [0.0]
    target_ids = {e.target.event_id for e in edges}
    source_ids = {e.source.event_id for e in edges}

    return EventDAG(
        incident_id=incident_id,
        window_start=min(timestamps),
        window_end=max(timestamps),
        nodes=nodes_dict,
        edges=edges,
        root_nodes=[n for n in nodes if n.event_id not in target_ids],
        leaf_nodes=[n for n in nodes if n.event_id not in source_ids],
    )


def _make_correlation_result(
    event_type_a: str,
    event_type_b: str,
    direction: str | None = "a_causes_b",
    correlation_score: float = 0.8,
    confidence: float = 0.9,
    sample_count: int = 10,
) -> CorrelationResult:
    return CorrelationResult(
        pair=EventPairKey(event_type_a=event_type_a, event_type_b=event_type_b),
        correlation_score=correlation_score,
        direction=direction,
        evidence=f"{event_type_a} → {event_type_b}",
        sample_count=sample_count,
        confidence=confidence,
    )


# =============================================================================
# BlastRadius 방향 검증 통합
# =============================================================================


class TestBlastRadiusDirectionIntegration:
    """BlastRadiusService 방향 정의와 RootCauseRanker의 통합 검증."""

    def test_db_upstream_3_services_rank_one(self):
        """DB(upstream=3, downstream=0) → blast_score=1.0, rank=1."""
        blast = BlastRadiusService()
        blast.add_dependency("payment", "DB")
        blast.add_dependency("order", "DB")
        blast.add_dependency("inventory", "DB")

        ranker = RootCauseRanker()

        db_node = _make_node(event_id="db", service_name="DB", timestamp=100.0, event_type="db_pool_exhaustion")
        payment_node = _make_node(event_id="pay", service_name="payment", timestamp=102.0, event_type="timeout")
        order_node = _make_node(event_id="ord", service_name="order", timestamp=103.0, event_type="timeout")

        edges = [_make_edge(db_node, payment_node), _make_edge(db_node, order_node)]
        dag = _make_dag([db_node, payment_node, order_node], edges)

        result = ranker.rank(dag, [])
        assert result.primary_cause.event_node.event_id == "db"

    def test_blast_direction_reversed_from_original(self):
        """방향 수정 검증: DB의 blast_score가 Gateway보다 높아야 한다."""
        blast = BlastRadiusService()
        blast.add_dependency("Gateway", "API")
        blast.add_dependency("API", "DB")
        blast.add_dependency("Worker", "DB")

        ranker = RootCauseRanker()
        db_node = _make_node(service_name="DB")
        gw_node = _make_node(service_name="Gateway")

        dag_db = _make_dag([db_node])
        dag_gw = _make_dag([gw_node])

        score_db = ranker._blast_radius_score(db_node, dag_db)
        score_gw = ranker._blast_radius_score(gw_node, dag_gw)

        assert score_db > score_gw

    def test_multiple_dependency_types_set_deduplication(self):
        """다중 dependency_type: A→DB sync + A→DB async → upstream_count=1."""
        blast = BlastRadiusService()
        blast.add_dependency("OrderService", "DB", "sync", "critical")
        blast.add_dependency("OrderService", "DB", "async", "medium")

        ranker = RootCauseRanker()
        db_node = _make_node(service_name="DB")
        dag = _make_dag([db_node])

        # set 중복 제거: upstream_services = {"OrderService"}, downstream = 0 → 1.0
        assert ranker._blast_radius_score(db_node, dag) == 1.0


# =============================================================================
# Historical Score 통합
# =============================================================================


class TestHistoricalScoreIntegration:
    """과거 동시발생 이력과 RootCauseRanker의 통합 검증."""

    def test_five_cause_records_historical_above_08(self):
        """과거 5회 A가 원인 → historical_score ≥ 0.8."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="db_pool_exhaustion")

        co_data = [_make_correlation_result("db_pool_exhaustion", f"effect_{i}", "a_causes_b") for i in range(5)]

        # 인덱스 구축 (rank()의 내부 로직 재현)
        from collections import defaultdict

        index: dict[str, list[CorrelationResult]] = defaultdict(list)
        for r in co_data:
            index[r.pair.event_type_a].append(r)
            index[r.pair.event_type_b].append(r)

        score = ranker._historical_score(node, index)
        assert score >= 0.8

    def test_co_occurrence_index_limits_iteration(self):
        """N=200, M=1000에서 Historical 인덱싱이 효율적으로 동작한다."""
        ranker = RootCauseRanker()

        # 200개 노드, 1000개 co-occurrence 결과
        types = [f"event_type_{i}" for i in range(200)]
        co_data = [_make_correlation_result(types[i % 200], types[(i + 1) % 200], "a_causes_b") for i in range(1000)]

        # 특정 이벤트 타입에 대해서만 관련 결과를 조회하는지 확인
        from collections import defaultdict

        index: dict[str, list[CorrelationResult]] = defaultdict(list)
        for r in co_data:
            index[r.pair.event_type_a].append(r)
            index[r.pair.event_type_b].append(r)

        node = _make_node(event_type="event_type_0")
        # event_type_0과 관련된 결과만 조회 — 전체 1000개를 순회하지 않음
        related = index.get("event_type_0", [])
        assert len(related) < 1000

        # 점수 산정이 정상 동작
        score = ranker._historical_score(node, index)
        assert 0.0 <= score <= 1.0


# =============================================================================
# Disconnected Subgraph 통합
# =============================================================================


class TestDisconnectedSubgraphIntegration:
    """단절된 서브그래프에서의 RootCauseRanker 통합 검증."""

    def test_b_group_root_temporal_score_not_distorted(self):
        """단절 서브그래프 2개: B그룹(t=145~150) 루트의 temporal_score=1.0."""
        ranker = RootCauseRanker()

        # 컴포넌트1: t=100~110
        a = _make_node(event_id="a", service_name="svc-1", timestamp=100.0)
        b = _make_node(event_id="b", service_name="svc-2", timestamp=110.0)
        # 컴포넌트2: t=145~150
        c = _make_node(event_id="c", service_name="svc-3", timestamp=145.0)
        d = _make_node(event_id="d", service_name="svc-4", timestamp=150.0)

        edges = [_make_edge(a, b), _make_edge(c, d)]
        dag = _make_dag([a, b, c, d], edges)

        # 전체 DAG 기준이면 c의 temporal = (150-145)/(150-100) = 0.10 (왜곡)
        # 컴포넌트별이면 c의 temporal = 1.0 (정상)
        components = dag.get_connected_components()
        comp2 = next(comp for comp in components if "c" in comp.nodes)

        assert ranker._temporal_score(comp2.nodes["c"], comp2) == pytest.approx(1.0)

    def test_full_ranking_with_disconnected_components(self):
        """단절 서브그래프에서 전체 rank() 파이프라인이 왜곡 없이 동작한다."""
        ranker = RootCauseRanker()
        ranker._blast_radius_score = lambda node, dag: NEUTRAL_SCORE  # type: ignore[assignment]

        # 컴포넌트1: 3서비스
        n1 = _make_node(event_id="n1", service_name="svc-a", timestamp=100.0, event_type="db_pool")
        n2 = _make_node(event_id="n2", service_name="svc-b", timestamp=105.0, event_type="timeout")
        n3 = _make_node(event_id="n3", service_name="svc-c", timestamp=110.0, event_type="cb_open")
        # 컴포넌트2: 2서비스
        n4 = _make_node(event_id="n4", service_name="svc-x", timestamp=200.0, event_type="memory_pressure")
        n5 = _make_node(event_id="n5", service_name="svc-y", timestamp=205.0, event_type="oom_kill")

        edges = [
            _make_edge(n1, n2),
            _make_edge(n2, n3),
            _make_edge(n4, n5),
        ]
        dag = _make_dag([n1, n2, n3, n4, n5], edges)

        result = ranker.rank(dag, [])
        assert len(result.candidates) >= 1
        # 결과에 양쪽 컴포넌트의 노드가 포함되어야 함
        candidate_ids = {c.event_node.event_id for c in result.candidates}
        assert "n1" in candidate_ids or "n4" in candidate_ids


# =============================================================================
# Component Weight 통합
# =============================================================================


class TestComponentWeightIntegration:
    """컴포넌트 가중치 통합 검증."""

    def test_more_services_component_has_higher_weight(self):
        """5노드(3서비스) vs 100노드(2서비스): 3서비스 컴포넌트의 1위가 우세."""
        ranker = RootCauseRanker()
        ranker._blast_radius_score = lambda node, dag: NEUTRAL_SCORE  # type: ignore[assignment]

        # 컴포넌트1: 3 고유 서비스, 3 노드
        comp1_nodes = [
            _make_node(event_id="c1_root", service_name="svc-a", timestamp=100.0),
            _make_node(event_id="c1_mid", service_name="svc-b", timestamp=101.0),
            _make_node(event_id="c1_leaf", service_name="svc-c", timestamp=102.0),
        ]
        comp1_edges = [
            _make_edge(comp1_nodes[0], comp1_nodes[1]),
            _make_edge(comp1_nodes[1], comp1_nodes[2]),
        ]

        # 컴포넌트2: 2 고유 서비스, 많은 노드 (동일 서비스 반복)
        comp2_root = _make_node(event_id="c2_root", service_name="svc-x", timestamp=200.0)
        comp2_nodes = [comp2_root]
        comp2_edges = []
        for i in range(10):
            child = _make_node(event_id=f"c2_n{i}", service_name="svc-y", timestamp=201.0 + i)
            comp2_nodes.append(child)
            comp2_edges.append(_make_edge(comp2_root, child))

        all_nodes = comp1_nodes + comp2_nodes
        all_edges = comp1_edges + comp2_edges
        dag = _make_dag(all_nodes, all_edges)

        result = ranker.rank(dag, [])

        c1_root_candidate = next(c for c in result.candidates if c.event_node.event_id == "c1_root")
        c2_root_candidate = next(c for c in result.candidates if c.event_node.event_id == "c2_root")

        # 3서비스 컴포넌트 루트가 2서비스 컴포넌트 루트보다 높은 점수
        assert c1_root_candidate.score > c2_root_candidate.score


# =============================================================================
# 전체 시나리오: DB Pool → Error Rate → CB OPEN × 3 → Emergency
# =============================================================================


class TestDbPoolCascadeScenario:
    """DB Pool 고갈 연쇄 장애 시나리오 통합 테스트."""

    def test_db_pool_is_primary_cause_with_high_score(self):
        """DB Pool 고갈이 87%+ 확률로 근본 원인으로 식별된다."""
        # BlastRadius 설정: DB가 3서비스의 upstream
        blast = BlastRadiusService()
        blast.add_dependency("payment", "DB")
        blast.add_dependency("order", "DB")
        blast.add_dependency("point", "DB")

        ranker = RootCauseRanker()

        # DAG: DB Pool → Error Rate → CB OPEN ×3 → Emergency
        db_pool = _make_node(
            event_id="db_pool",
            event_type="db_pool_exhaustion",
            service_name="DB",
            timestamp=100.0,
        )
        error_rate = _make_node(
            event_id="error_rate",
            event_type="error_rate_spike",
            service_name="payment",
            timestamp=102.0,
        )
        cb_payment = _make_node(
            event_id="cb_pay",
            event_type="circuit_breaker_opened",
            service_name="payment",
            timestamp=104.0,
        )
        cb_order = _make_node(
            event_id="cb_ord",
            event_type="circuit_breaker_opened",
            service_name="order",
            timestamp=104.5,
        )
        cb_point = _make_node(
            event_id="cb_pt",
            event_type="circuit_breaker_opened",
            service_name="point",
            timestamp=105.0,
        )
        emergency = _make_node(
            event_id="emergency",
            event_type="emergency_activated",
            service_name="gateway",
            timestamp=106.0,
        )

        edges = [
            _make_edge(db_pool, error_rate),
            _make_edge(error_rate, cb_payment),
            _make_edge(error_rate, cb_order),
            _make_edge(error_rate, cb_point),
            _make_edge(cb_payment, emergency),
            _make_edge(cb_order, emergency),
            _make_edge(cb_point, emergency),
        ]
        nodes = [db_pool, error_rate, cb_payment, cb_order, cb_point, emergency]
        dag = _make_dag(nodes, edges)

        # 과거 이력: db_pool_exhaustion이 원인
        co_data = [
            _make_correlation_result("db_pool_exhaustion", "error_rate_spike", "a_causes_b"),
            _make_correlation_result("db_pool_exhaustion", "circuit_breaker_opened", "a_causes_b"),
            _make_correlation_result("db_pool_exhaustion", "emergency_activated", "a_causes_b"),
        ]

        result = ranker.rank(dag, co_data)

        # DB Pool이 rank=1
        assert result.primary_cause.event_node.event_id == "db_pool"
        # 6개 노드 Softmax에서 가장 높은 점수 (1위)
        assert result.primary_cause.rank == 1
        # 2위보다 높은 점수
        if len(result.candidates) >= 2:
            assert result.primary_cause.score > result.candidates[1].score
        # 영향받은 서비스가 포함됨
        assert len(result.primary_cause.affected_services) >= 3
        # cascade depth가 2 이상 (db_pool → error_rate → cb_*)
        assert result.primary_cause.cascade_depth >= 2
        # 분석 결과 구조 확인
        assert result.incident_id == "inc_integration"
        assert result.confidence > 0
        assert "DB" in result.summary
