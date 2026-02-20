"""
Tests for Root Cause Ranker — DAG 기반 근본 원인 순위 산정.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 설계 문서(§253)에 명시된 값/구조 검증 (하드코딩)
- Behavior: 함수/메서드 동작 검증 (소스 참조)

참조 소스:
- services/correlation_engine/root_cause_ranker.py (RootCauseRanker, RootCauseCandidate, RootCauseAnalysis)
- services/correlation_engine/event_graph.py (EventNode, CausalEdge, EventDAG)
- services/correlation_engine/co_occurrence_tracker.py (CorrelationResult, EventPairKey)
"""

from __future__ import annotations

import math
import time
import uuid
from unittest.mock import MagicMock, patch

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
    HISTORICAL_HIGH_THRESHOLD,
    MIN_CANDIDATE_SCORE,
    NEUTRAL_SCORE,
    SOFTMAX_TEMPERATURE,
    TIMESTAMP_EPSILON,
    TOP_P_THRESHOLD,
    TOPOLOGY_INFLUENCE_FACTOR_WEIGHT,
    TOPOLOGY_ROOT_FACTOR_WEIGHT,
    WEIGHT_BLAST_RADIUS,
    WEIGHT_HISTORICAL,
    WEIGHT_TEMPORAL,
    WEIGHT_TOPOLOGY,
    RootCauseAnalysis,
    RootCauseCandidate,
    RootCauseRanker,
)


# =============================================================================
# Fixtures (이 파일 전용 — conftest 분리 불필요)
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_blast_radius_singleton():
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


def _make_dag(
    nodes: list[EventNode],
    edges: list[CausalEdge] | None = None,
    incident_id: str = "inc_test",
) -> EventDAG:
    """테스트용 EventDAG 생성 헬퍼."""
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


def _make_edge(
    source: EventNode,
    target: EventNode,
    confidence: float = 0.9,
    evidence_type: str = "dependency",
) -> CausalEdge:
    """테스트용 CausalEdge 생성 헬퍼."""
    return CausalEdge(
        source=source,
        target=target,
        confidence=confidence,
        evidence_type=evidence_type,
        time_gap_seconds=abs(target.timestamp - source.timestamp),
    )


def _make_correlation_result(
    event_type_a: str,
    event_type_b: str,
    direction: str | None = "a_causes_b",
    correlation_score: float = 0.8,
    confidence: float = 0.9,
    sample_count: int = 10,
) -> CorrelationResult:
    """테스트용 CorrelationResult 생성 헬퍼."""
    return CorrelationResult(
        pair=EventPairKey(event_type_a=event_type_a, event_type_b=event_type_b),
        correlation_score=correlation_score,
        direction=direction,
        evidence=f"{event_type_a} → {event_type_b} 상관관계",
        sample_count=sample_count,
        confidence=confidence,
    )


def _ranker_with_neutral_blast() -> RootCauseRanker:
    """BlastRadiusService 의존성 없는 Ranker (blast_radius는 항상 중립 점수)."""
    ranker = RootCauseRanker()
    ranker._blast_radius_score = lambda node, dag: NEUTRAL_SCORE  # type: ignore[assignment]
    return ranker


# =============================================================================
# 1. RootCauseCandidate — 계약 검증 (Contract)
# =============================================================================


class TestRootCauseCandidateContract:
    """RootCauseCandidate 자료구조 설계 계약 검증."""

    def test_frozen_dataclass(self):
        """RootCauseCandidate는 immutable(frozen) dataclass여야 한다."""
        node = _make_node()
        candidate = RootCauseCandidate(
            event_node=node,
            score=0.87,
            rank=1,
            evidence=["DAG 루트 노드"],
            contributing_factors={},
            affected_services=["payment"],
            cascade_depth=3,
        )
        with pytest.raises(AttributeError):
            candidate.score = 0.5  # type: ignore[misc]

    def test_required_fields(self):
        """RootCauseCandidate는 설계 문서의 7개 필드를 가져야 한다."""
        node = _make_node(event_id="evt_001")
        candidate = RootCauseCandidate(
            event_node=node,
            score=0.87,
            rank=1,
            evidence=["근거1", "근거2"],
            contributing_factors={"topology": 0.9},
            affected_services=["payment", "order"],
            cascade_depth=3,
        )
        assert candidate.event_node.event_id == "evt_001"
        assert candidate.score == 0.87
        assert candidate.rank == 1
        assert len(candidate.evidence) == 2
        assert candidate.contributing_factors == {"topology": 0.9}
        assert candidate.affected_services == ["payment", "order"]
        assert candidate.cascade_depth == 3


# =============================================================================
# 2. RootCauseAnalysis — 계약 검증 (Contract)
# =============================================================================


class TestRootCauseAnalysisContract:
    """RootCauseAnalysis 자료구조 설계 계약 검증."""

    def test_required_fields(self):
        """RootCauseAnalysis는 설계 문서의 7개 필드를 가져야 한다."""
        node = _make_node()
        candidate = RootCauseCandidate(
            event_node=node,
            score=0.87,
            rank=1,
            evidence=[],
            contributing_factors={},
            affected_services=[],
            cascade_depth=0,
        )
        dag = _make_dag([node])
        analysis = RootCauseAnalysis(
            incident_id="inc_001",
            analyzed_at=1000.0,
            dag=dag,
            candidates=[candidate],
            primary_cause=candidate,
            confidence=0.82,
            summary="요약",
        )
        assert analysis.incident_id == "inc_001"
        assert analysis.analyzed_at == 1000.0
        assert analysis.dag is dag
        assert len(analysis.candidates) == 1
        assert analysis.primary_cause is candidate
        assert analysis.confidence == 0.82
        assert analysis.summary == "요약"


# =============================================================================
# 3. Factor 가중치 — 계약 검증 (Contract)
# =============================================================================


class TestWeightsContract:
    """Factor 가중치 설계 계약값 검증."""

    def test_weight_topology_contract(self):
        """Topology 가중치는 0.35이어야 한다."""
        assert WEIGHT_TOPOLOGY == 0.35

    def test_weight_temporal_contract(self):
        """Temporal 가중치는 0.25이어야 한다."""
        assert WEIGHT_TEMPORAL == 0.25

    def test_weight_blast_radius_contract(self):
        """Blast Radius 가중치는 0.25이어야 한다."""
        assert WEIGHT_BLAST_RADIUS == 0.25

    def test_weight_historical_contract(self):
        """Historical 가중치는 0.15이어야 한다."""
        assert WEIGHT_HISTORICAL == 0.15

    def test_weights_sum_to_one(self):
        """4가지 가중치의 합은 1.0이어야 한다."""
        total = WEIGHT_TOPOLOGY + WEIGHT_TEMPORAL + WEIGHT_BLAST_RADIUS + WEIGHT_HISTORICAL
        assert total == pytest.approx(1.0)

    def test_softmax_temperature_default(self):
        """Softmax temperature 기본값은 1.0이어야 한다."""
        assert SOFTMAX_TEMPERATURE == 1.0

    def test_top_p_threshold_default(self):
        """Top-P 임계값 기본값은 0.95이어야 한다."""
        assert TOP_P_THRESHOLD == 0.95

    def test_min_candidate_score_default(self):
        """최소 후보 점수 기본값은 0.01이어야 한다."""
        assert MIN_CANDIDATE_SCORE == 0.01

    def test_timestamp_epsilon_default(self):
        """타임스탬프 양자화 단위는 0.001이어야 한다."""
        assert TIMESTAMP_EPSILON == 0.001

    def test_topology_root_factor_weight(self):
        """Topology 내부 root_factor 가중치는 0.6이어야 한다."""
        assert TOPOLOGY_ROOT_FACTOR_WEIGHT == 0.6

    def test_topology_influence_factor_weight(self):
        """Topology 내부 influence_factor 가중치는 0.4이어야 한다."""
        assert TOPOLOGY_INFLUENCE_FACTOR_WEIGHT == 0.4

    def test_neutral_score_default(self):
        """중립 점수 기본값은 0.5이어야 한다."""
        assert NEUTRAL_SCORE == 0.5


# =============================================================================
# 4. Topology Score — 동작 검증 (Behavior)
# =============================================================================


class TestTopologyScoreBehavior:
    """_topology_score 동작 검증."""

    def test_single_node_returns_max_root_factor(self):
        """단일 노드 DAG에서 in_degree=0 → root_factor 최대."""
        ranker = RootCauseRanker()
        node = _make_node()
        dag = _make_dag([node])

        score = ranker._topology_score(node, dag)
        # in_degree=0 → root_factor=1.0, descendants=0 → influence=0.0
        expected = 1.0 * TOPOLOGY_ROOT_FACTOR_WEIGHT + 0.0 * TOPOLOGY_INFLUENCE_FACTOR_WEIGHT
        assert score == pytest.approx(expected)

    def test_root_node_higher_than_leaf_in_chain(self):
        """선형 체인 A→B→C에서 A가 C보다 높은 topology score."""
        ranker = RootCauseRanker()
        a = _make_node(event_id="a", timestamp=100.0, service_name="svc-a")
        b = _make_node(event_id="b", timestamp=102.0, service_name="svc-b")
        c = _make_node(event_id="c", timestamp=104.0, service_name="svc-c")
        edges = [_make_edge(a, b), _make_edge(b, c)]
        dag = _make_dag([a, b, c], edges)

        score_a = ranker._topology_score(a, dag)
        score_c = ranker._topology_score(c, dag)
        assert score_a > score_c

    def test_in_degree_reduces_score(self):
        """진입차수가 높을수록 topology score가 낮아진다."""
        ranker = RootCauseRanker()
        root = _make_node(event_id="root", service_name="svc-root")
        mid = _make_node(event_id="mid", service_name="svc-mid", timestamp=1001.0)
        leaf = _make_node(event_id="leaf", service_name="svc-leaf", timestamp=1002.0)
        edges = [_make_edge(root, mid), _make_edge(root, leaf), _make_edge(mid, leaf)]
        dag = _make_dag([root, mid, leaf], edges)

        # leaf의 in_degree=2, root의 in_degree=0
        score_root = ranker._topology_score(root, dag)
        score_leaf = ranker._topology_score(leaf, dag)
        assert score_root > score_leaf


# =============================================================================
# 5. Temporal Score — 동작 검증 (Behavior)
# =============================================================================


class TestTemporalScoreBehavior:
    """_temporal_score 동작 검증."""

    def test_single_node_returns_one(self):
        """단일 노드 DAG에서 temporal score = 1.0."""
        ranker = RootCauseRanker()
        node = _make_node(timestamp=500.0)
        dag = _make_dag([node])

        assert ranker._temporal_score(node, dag) == 1.0

    def test_earliest_gets_one_latest_gets_zero(self):
        """가장 먼저 발생한 노드 = 1.0, 가장 늦은 노드 = 0.0."""
        ranker = RootCauseRanker()
        first = _make_node(event_id="first", timestamp=100.0)
        last = _make_node(event_id="last", timestamp=200.0)
        dag = _make_dag([first, last])

        assert ranker._temporal_score(first, dag) == pytest.approx(1.0)
        assert ranker._temporal_score(last, dag) == pytest.approx(0.0)

    def test_middle_node_has_proportional_score(self):
        """중간 시점 노드는 비례적 점수를 가진다."""
        ranker = RootCauseRanker()
        first = _make_node(event_id="first", timestamp=100.0)
        mid = _make_node(event_id="mid", timestamp=150.0)
        last = _make_node(event_id="last", timestamp=200.0)
        dag = _make_dag([first, mid, last])

        assert ranker._temporal_score(mid, dag) == pytest.approx(0.5)

    def test_same_timestamp_all_return_one(self):
        """모든 노드가 동일 타임스탬프면 temporal score = 1.0."""
        ranker = RootCauseRanker()
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=100.0)
        dag = _make_dag([a, b])

        assert ranker._temporal_score(a, dag) == 1.0
        assert ranker._temporal_score(b, dag) == 1.0


# =============================================================================
# 6. Blast Radius Score — 동작 검증 (Behavior)
# =============================================================================


class TestBlastRadiusScoreBehavior:
    """_blast_radius_score 동작 검증."""

    def test_db_service_high_upstream_returns_one(self):
        """upstream이 많고 downstream이 없는 서비스(DB)는 1.0."""
        ranker = RootCauseRanker()
        blast = BlastRadiusService()
        blast.add_dependency("OrderService", "DB")
        blast.add_dependency("PaymentService", "DB")
        blast.add_dependency("PointService", "DB")

        node = _make_node(service_name="DB")
        dag = _make_dag([node])

        assert ranker._blast_radius_score(node, dag) == 1.0

    def test_gateway_service_returns_zero(self):
        """upstream이 없고 downstream만 있는 서비스(Gateway)는 0.0."""
        ranker = RootCauseRanker()
        blast = BlastRadiusService()
        blast.add_dependency("Gateway", "OrderService")
        blast.add_dependency("Gateway", "PaymentService")

        node = _make_node(service_name="Gateway")
        dag = _make_dag([node])

        assert ranker._blast_radius_score(node, dag) == pytest.approx(0.0)

    def test_no_dependencies_returns_neutral(self):
        """의존성 정보가 없으면 중립 점수."""
        ranker = RootCauseRanker()
        node = _make_node(service_name="unknown-service")
        dag = _make_dag([node])

        assert ranker._blast_radius_score(node, dag) == NEUTRAL_SCORE

    def test_duplicate_dependency_types_deduplication(self):
        """동일 서비스 간 다중 dependency_type은 set 중복 제거."""
        ranker = RootCauseRanker()
        blast = BlastRadiusService()
        # A→DB sync + A→DB async → upstream_count=1
        blast.add_dependency("OrderService", "DB", "sync")
        blast.add_dependency("OrderService", "DB", "async")

        node = _make_node(service_name="DB")
        dag = _make_dag([node])

        # upstream_services = {"OrderService"}, downstream = 0 → 1.0
        assert ranker._blast_radius_score(node, dag) == 1.0

    def test_mixed_upstream_downstream_ratio(self):
        """upstream과 downstream이 혼합된 서비스는 비율 점수."""
        ranker = RootCauseRanker()
        blast = BlastRadiusService()
        # MiddleService: upstream 2개, downstream 1개
        blast.add_dependency("A", "MiddleService")
        blast.add_dependency("B", "MiddleService")
        blast.add_dependency("MiddleService", "DB")

        node = _make_node(service_name="MiddleService")
        dag = _make_dag([node])

        # upstream=2, downstream=1 → 2/(2+1) ≈ 0.667
        assert ranker._blast_radius_score(node, dag) == pytest.approx(2 / 3)


# =============================================================================
# 7. Historical Score — 동작 검증 (Behavior)
# =============================================================================


class TestHistoricalScoreBehavior:
    """_historical_score 동작 검증."""

    def test_no_history_returns_neutral(self):
        """이력이 없으면 중립 점수."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="unknown_event")

        assert ranker._historical_score(node, {}) == NEUTRAL_SCORE

    def test_always_cause_returns_one(self):
        """항상 원인으로 식별되면 1.0."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="db_pool_exhaustion")

        # EventPairKey 알파벳 정렬: "cb_open" < "db_pool_exhaustion" < "timeout"
        # db_pool_exhaustion이 event_type_a이면 a_causes_b → 원인
        # db_pool_exhaustion이 event_type_b이면 b_causes_a → 원인
        index: dict[str, list[CorrelationResult]] = {
            "db_pool_exhaustion": [
                # db_pool_exhaustion > timeout 알파벳 X → db_pool < timeout → a=db_pool
                _make_correlation_result("db_pool_exhaustion", "timeout", "a_causes_b"),
                # cb_open < db_pool → a=cb_open, b=db_pool, b_causes_a → db_pool이 원인
                _make_correlation_result("cb_open", "db_pool_exhaustion", "b_causes_a"),
            ]
        }

        assert ranker._historical_score(node, index) == pytest.approx(1.0)

    def test_always_effect_returns_zero(self):
        """항상 결과로 식별되면 0.0."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="timeout")

        index: dict[str, list[CorrelationResult]] = {
            "timeout": [
                _make_correlation_result("db_pool_exhaustion", "timeout", "a_causes_b"),
                _make_correlation_result("cb_open", "timeout", "a_causes_b"),
            ]
        }

        assert ranker._historical_score(node, index) == pytest.approx(0.0)

    def test_mixed_direction_ratio(self):
        """원인 2회, 결과 1회 → 2/3."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="cb_open")

        # EventPairKey 알파벳 정렬: cb_open < db_pool, cb_open < timeout
        index: dict[str, list[CorrelationResult]] = {
            "cb_open": [
                # cb_open이 event_type_a, a_causes_b → 원인
                _make_correlation_result("cb_open", "timeout", "a_causes_b"),
                # cb_open이 event_type_a, a_causes_b → 원인
                _make_correlation_result("cb_open", "high_memory", "a_causes_b"),
                # cb_open이 event_type_b (알파벳 정렬됨), a_causes_b → cb_open은 결과
                _make_correlation_result("another_event", "cb_open", "a_causes_b"),
            ]
        }

        assert ranker._historical_score(node, index) == pytest.approx(2 / 3)

    def test_b_causes_a_direction_when_node_is_type_b(self):
        """node가 event_type_b이고 direction=b_causes_a이면 원인으로 카운트."""
        ranker = RootCauseRanker()
        node = _make_node(event_type="zebra_event")
        # 알파벳 정렬: alpha < zebra → alpha=a, zebra=b
        index: dict[str, list[CorrelationResult]] = {
            "zebra_event": [
                _make_correlation_result("alpha_event", "zebra_event", "b_causes_a"),
            ]
        }

        assert ranker._historical_score(node, index) == pytest.approx(1.0)


# =============================================================================
# 8. Softmax 정규화 — 동작 검증 (Behavior)
# =============================================================================


class TestSoftmaxNormalizationBehavior:
    """_normalize_and_rank 정규화 동작 검증."""

    def test_scores_sum_to_one_before_filtering(self):
        """Softmax 정규화 후 전체 점수 합은 1.0 (Top-P 필터링 전)."""
        ranker = RootCauseRanker(top_p_threshold=1.0, min_candidate_score=0.0)
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=200.0)
        dag = _make_dag([a, b])

        raw_entries = [
            {
                "node_id": "a",
                "total": 0.8,
                "topology": 0.9,
                "temporal": 1.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "b",
                "total": 0.3,
                "topology": 0.3,
                "temporal": 0.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        candidates = ranker._normalize_and_rank(raw_entries, dag)
        total_score = sum(c.score for c in candidates)
        assert total_score == pytest.approx(1.0, abs=0.01)

    def test_low_temperature_amplifies_gap(self):
        """낮은 temperature(0.5)는 점수 격차를 확대한다."""
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=200.0)
        dag = _make_dag([a, b])
        raw_entries = [
            {
                "node_id": "a",
                "total": 0.8,
                "topology": 0.9,
                "temporal": 1.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "b",
                "total": 0.3,
                "topology": 0.3,
                "temporal": 0.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        ranker_normal = RootCauseRanker(softmax_temperature=1.0, top_p_threshold=1.0, min_candidate_score=0.0)
        ranker_low_t = RootCauseRanker(softmax_temperature=0.5, top_p_threshold=1.0, min_candidate_score=0.0)

        cands_normal = ranker_normal._normalize_and_rank(raw_entries, dag)
        cands_low_t = ranker_low_t._normalize_and_rank(raw_entries, dag)

        # 낮은 T에서 1위 점수가 더 높아야 함
        assert cands_low_t[0].score > cands_normal[0].score

    def test_high_temperature_equalizes_scores(self):
        """높은 temperature(5.0)는 점수를 균등화한다."""
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=200.0)
        dag = _make_dag([a, b])
        raw_entries = [
            {
                "node_id": "a",
                "total": 0.8,
                "topology": 0.9,
                "temporal": 1.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "b",
                "total": 0.3,
                "topology": 0.3,
                "temporal": 0.0,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        ranker_normal = RootCauseRanker(softmax_temperature=1.0, top_p_threshold=1.0, min_candidate_score=0.0)
        ranker_high_t = RootCauseRanker(softmax_temperature=5.0, top_p_threshold=1.0, min_candidate_score=0.0)

        cands_normal = ranker_normal._normalize_and_rank(raw_entries, dag)
        cands_high_t = ranker_high_t._normalize_and_rank(raw_entries, dag)

        # 높은 T에서 1위와 2위 차이가 더 작아야 함
        gap_normal = cands_normal[0].score - cands_normal[1].score
        gap_high = cands_high_t[0].score - cands_high_t[1].score
        assert gap_high < gap_normal

    def test_empty_entries_returns_empty(self):
        """빈 입력은 빈 결과."""
        ranker = RootCauseRanker()
        dag = _make_dag([])
        assert ranker._normalize_and_rank([], dag) == []


# =============================================================================
# 9. Top-P 필터링 — 동작 검증 (Behavior)
# =============================================================================


class TestTopPFilteringBehavior:
    """_filter_top_p 필터링 동작 검증."""

    def test_dominant_candidate_limits_output(self):
        """1위가 90%이면 Top-P=0.95에서 2개 이하 반환."""
        ranker = RootCauseRanker()
        node_a = _make_node(event_id="a")
        node_b = _make_node(event_id="b")
        node_c = _make_node(event_id="c")

        candidates = [
            RootCauseCandidate(
                event_node=node_a,
                score=0.90,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
            RootCauseCandidate(
                event_node=node_b,
                score=0.07,
                rank=2,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
            RootCauseCandidate(
                event_node=node_c,
                score=0.03,
                rank=3,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
        ]

        filtered = ranker._filter_top_p(candidates)
        assert len(filtered) <= 2
        assert filtered[0].score == 0.90

    def test_min_score_cutoff(self):
        """MIN_CANDIDATE_SCORE 미만은 즉시 제외."""
        ranker = RootCauseRanker(min_candidate_score=MIN_CANDIDATE_SCORE)
        node_a = _make_node(event_id="a")
        node_b = _make_node(event_id="b")

        candidates = [
            RootCauseCandidate(
                event_node=node_a,
                score=0.95,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
            RootCauseCandidate(
                event_node=node_b,
                score=0.005,
                rank=2,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
        ]

        filtered = ranker._filter_top_p(candidates)
        assert len(filtered) == 1

    def test_minimum_one_candidate_guaranteed(self):
        """모든 후보가 MIN_CANDIDATE_SCORE 미만이어도 최소 1개 반환."""
        ranker = RootCauseRanker(min_candidate_score=0.5)
        node = _make_node(event_id="a")

        candidates = [
            RootCauseCandidate(
                event_node=node,
                score=0.001,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            ),
        ]

        filtered = ranker._filter_top_p(candidates)
        assert len(filtered) == 1

    def test_even_distribution_respects_min_threshold(self):
        """균등 분포(각 1%)에서 min_candidate_score로 컷오프."""
        ranker = RootCauseRanker(
            min_candidate_score=MIN_CANDIDATE_SCORE,
            top_p_threshold=TOP_P_THRESHOLD,
        )
        # 100개 후보, 각 1% → MIN_CANDIDATE_SCORE=0.01과 동일
        nodes = [_make_node(event_id=f"n{i}") for i in range(100)]
        candidates = [
            RootCauseCandidate(
                event_node=nodes[i],
                score=0.01,
                rank=i + 1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            )
            for i in range(100)
        ]

        filtered = ranker._filter_top_p(candidates)
        # 합이 TOP_P_THRESHOLD에 도달하거나 전체 반환
        cumulative = sum(c.score for c in filtered)
        assert cumulative >= TOP_P_THRESHOLD or len(filtered) == len(candidates)


# =============================================================================
# 10. Tie-breaker 정렬 — 동작 검증 (Behavior)
# =============================================================================


class TestTieBreakerBehavior:
    """Deterministic Tie-breaker 4단계 정렬 검증."""

    def test_same_score_tiebreak_by_cascade_depth(self):
        """동일 score → cascade_depth 깊은 순."""
        ranker = RootCauseRanker(top_p_threshold=1.0, min_candidate_score=0.0)
        deep = _make_node(event_id="deep", timestamp=100.0)
        shallow = _make_node(event_id="shallow", timestamp=100.0)
        child = _make_node(event_id="child", timestamp=101.0)
        grandchild = _make_node(event_id="grandchild", timestamp=102.0)

        edges = [_make_edge(deep, child), _make_edge(child, grandchild)]
        dag = _make_dag([deep, shallow, child, grandchild], edges)

        raw_entries = [
            {
                "node_id": "deep",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "shallow",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        candidates = ranker._normalize_and_rank(raw_entries, dag)
        # deep (cascade_depth=2) > shallow (cascade_depth=0)
        assert candidates[0].event_node.event_id == "deep"

    def test_same_score_same_depth_tiebreak_by_timestamp(self):
        """동일 score + 동일 cascade_depth → 먼저 발생한 순."""
        ranker = RootCauseRanker(top_p_threshold=1.0, min_candidate_score=0.0)
        earlier = _make_node(event_id="earlier", timestamp=100.0)
        later = _make_node(event_id="later", timestamp=200.0)
        dag = _make_dag([earlier, later])

        raw_entries = [
            {
                "node_id": "later",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "earlier",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        candidates = ranker._normalize_and_rank(raw_entries, dag)
        assert candidates[0].event_node.event_id == "earlier"

    def test_timestamp_quantization_within_epsilon(self):
        """TIMESTAMP_EPSILON(1ms) 이내 차이는 동일 양자화값 → event_id로 결정."""
        ranker = RootCauseRanker(top_p_threshold=1.0, min_candidate_score=0.0)
        # 0.3ms 차이 → 동일 양자화값
        node_b = _make_node(event_id="b_node", timestamp=100.0003)
        node_a = _make_node(event_id="a_node", timestamp=100.0000)
        dag = _make_dag([node_a, node_b])

        raw_entries = [
            {
                "node_id": "b_node",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
            {
                "node_id": "a_node",
                "total": 0.5,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            },
        ]

        candidates = ranker._normalize_and_rank(raw_entries, dag)
        # int(100.0003 / 0.001) = 100000, int(100.0000 / 0.001) = 100000
        # 동일 → event_id 알파벳순: a_node < b_node
        assert candidates[0].event_node.event_id == "a_node"

    def test_rank_numbering_sequential(self):
        """rank는 1부터 순차적으로 부여된다."""
        ranker = RootCauseRanker(top_p_threshold=1.0, min_candidate_score=0.0)
        nodes = [_make_node(event_id=f"n{i}", timestamp=1000.0 + i) for i in range(3)]
        dag = _make_dag(nodes)

        raw_entries = [
            {
                "node_id": f"n{i}",
                "total": 0.5 - i * 0.1,
                "topology": 0.5,
                "temporal": 0.5,
                "blast_radius": 0.5,
                "historical": 0.5,
                "component": dag,
            }
            for i in range(3)
        ]

        candidates = ranker._normalize_and_rank(raw_entries, dag)
        for i, c in enumerate(candidates):
            assert c.rank == i + 1


# =============================================================================
# 11. 전체 rank() — 동작 검증 (Behavior)
# =============================================================================


class TestRankEndToEndBehavior:
    """rank() 메서드 전체 랭킹 파이프라인 동작 검증."""

    def test_single_node_dag(self):
        """단일 노드 DAG → score=1.0, rank=1."""
        ranker = _ranker_with_neutral_blast()
        node = _make_node(event_id="only")
        dag = _make_dag([node])

        result = ranker.rank(dag, [])

        assert len(result.candidates) == 1
        assert result.candidates[0].score == pytest.approx(1.0, abs=0.01)
        assert result.candidates[0].rank == 1
        assert result.primary_cause.event_node.event_id == "only"

    def test_linear_chain_root_is_rank_one(self):
        """선형 체인 A→B→C에서 A가 rank=1 (in_degree=0, 최초 발생)."""
        ranker = _ranker_with_neutral_blast()
        a = _make_node(event_id="a", event_type="db_pool", service_name="db", timestamp=100.0)
        b = _make_node(event_id="b", event_type="timeout", service_name="api", timestamp=102.0)
        c = _make_node(event_id="c", event_type="error_500", service_name="web", timestamp=104.0)
        edges = [_make_edge(a, b), _make_edge(b, c)]
        dag = _make_dag([a, b, c], edges)

        result = ranker.rank(dag, [])

        assert result.primary_cause.event_node.event_id == "a"
        assert result.candidates[0].rank == 1

    def test_diamond_dag_root_is_rank_one(self):
        """다이아몬드 DAG (A→B, A→C, B→D, C→D)에서 A가 rank=1."""
        ranker = _ranker_with_neutral_blast()
        a = _make_node(event_id="a", service_name="svc-a", timestamp=100.0)
        b = _make_node(event_id="b", service_name="svc-b", timestamp=102.0)
        c = _make_node(event_id="c", service_name="svc-c", timestamp=103.0)
        d = _make_node(event_id="d", service_name="svc-d", timestamp=105.0)

        edges = [
            _make_edge(a, b),
            _make_edge(a, c),
            _make_edge(b, d),
            _make_edge(c, d),
        ]
        dag = _make_dag([a, b, c, d], edges)

        result = ranker.rank(dag, [])
        assert result.primary_cause.event_node.event_id == "a"

    def test_concurrent_roots_close_scores(self):
        """동시 발생 2개 root는 두 후보 모두 높은 점수, 차이 ≤ 0.1."""
        ranker = _ranker_with_neutral_blast()
        root1 = _make_node(event_id="root1", service_name="svc-1", timestamp=100.0)
        root2 = _make_node(event_id="root2", service_name="svc-2", timestamp=100.0)
        leaf = _make_node(event_id="leaf", service_name="svc-3", timestamp=102.0)

        edges = [_make_edge(root1, leaf), _make_edge(root2, leaf)]
        dag = _make_dag([root1, root2, leaf], edges)

        result = ranker.rank(dag, [])
        top_two = [c for c in result.candidates if c.rank <= 2]
        assert len(top_two) == 2
        assert abs(top_two[0].score - top_two[1].score) <= 0.1

    def test_analysis_has_correct_incident_id(self):
        """결과의 incident_id가 DAG의 incident_id와 일치."""
        ranker = _ranker_with_neutral_blast()
        node = _make_node()
        dag = _make_dag([node], incident_id="inc_42")

        result = ranker.rank(dag, [])
        assert result.incident_id == "inc_42"

    def test_analysis_has_summary(self):
        """결과에 사람이 읽을 수 있는 요약이 포함되어야 한다."""
        ranker = _ranker_with_neutral_blast()
        node = _make_node(event_type="cb_opened", service_name="payment")
        dag = _make_dag([node])

        result = ranker.rank(dag, [])
        assert "payment" in result.summary
        assert "cb_opened" in result.summary
        assert "확률" in result.summary

    def test_historical_data_affects_ranking(self):
        """과거 이력 데이터가 있으면 원인 빈도가 높은 이벤트의 점수가 올라간다."""
        ranker = _ranker_with_neutral_blast()
        cause_node = _make_node(
            event_id="cause",
            event_type="db_pool_exhaustion",
            service_name="db",
            timestamp=100.0,
        )
        effect_node = _make_node(
            event_id="effect",
            event_type="timeout",
            service_name="api",
            timestamp=100.0,
        )
        dag = _make_dag([cause_node, effect_node])

        # db_pool_exhaustion이 항상 원인
        co_data = [
            _make_correlation_result("db_pool_exhaustion", "timeout", "a_causes_b"),
            _make_correlation_result("db_pool_exhaustion", "error_spike", "a_causes_b"),
        ]

        result = ranker.rank(dag, co_data)
        # 과거 이력에서 원인으로 식별된 db_pool_exhaustion이 더 높아야 함
        cause_candidate = next(c for c in result.candidates if c.event_node.event_id == "cause")
        effect_candidate = next(c for c in result.candidates if c.event_node.event_id == "effect")
        assert cause_candidate.score > effect_candidate.score


# =============================================================================
# 12. Connected Component 랭킹 — 동작 검증 (Behavior)
# =============================================================================


class TestConnectedComponentRankingBehavior:
    """Connected Component별 독립 랭킹 동작 검증."""

    def test_disconnected_graphs_independent_temporal_score(self):
        """단절 서브그래프 2개에서 B그룹 루트의 temporal_score가 왜곡 없이 1.0."""
        ranker = RootCauseRanker()
        # 컴포넌트1: t=100~110
        a = _make_node(event_id="a", service_name="svc-1", timestamp=100.0)
        b = _make_node(event_id="b", service_name="svc-2", timestamp=110.0)
        # 컴포넌트2: t=145~150
        c = _make_node(event_id="c", service_name="svc-3", timestamp=145.0)
        d = _make_node(event_id="d", service_name="svc-4", timestamp=150.0)

        edges = [_make_edge(a, b), _make_edge(c, d)]
        dag = _make_dag([a, b, c, d], edges)

        # 컴포넌트 분리 후 c의 temporal_score는 컴포넌트2 기준 1.0
        components = dag.get_connected_components()
        comp2 = next(comp for comp in components if "c" in comp.nodes)
        c_node = comp2.nodes["c"]

        assert ranker._temporal_score(c_node, comp2) == pytest.approx(1.0)

    def test_component_weight_based_on_unique_services(self):
        """컴포넌트 가중치는 고유 서비스 수 기반."""
        ranker = _ranker_with_neutral_blast()
        # 컴포넌트1: 3개 서비스
        n1 = _make_node(event_id="n1", service_name="svc-a", timestamp=100.0)
        n2 = _make_node(event_id="n2", service_name="svc-b", timestamp=101.0)
        n3 = _make_node(event_id="n3", service_name="svc-c", timestamp=102.0)
        # 컴포넌트2: 1개 서비스 (동일 서비스에서 여러 이벤트)
        n4 = _make_node(event_id="n4", service_name="svc-x", timestamp=200.0)
        n5 = _make_node(event_id="n5", service_name="svc-x", timestamp=201.0)

        edges = [_make_edge(n1, n2), _make_edge(n2, n3), _make_edge(n4, n5)]
        dag = _make_dag([n1, n2, n3, n4, n5], edges)

        result = ranker.rank(dag, [])
        # 3서비스 컴포넌트의 루트(n1)가 1서비스 컴포넌트의 루트(n4)보다 높아야 함
        n1_rank = next(c for c in result.candidates if c.event_node.event_id == "n1")
        n4_rank = next(c for c in result.candidates if c.event_node.event_id == "n4")
        assert n1_rank.score > n4_rank.score


# =============================================================================
# 13. Evidence 생성 — 동작 검증 (Behavior)
# =============================================================================


class TestEvidenceBuildBehavior:
    """_build_evidence 동작 검증."""

    def test_root_node_evidence_contains_root_marker(self):
        """in_degree=0 노드에 'DAG 루트 노드' 근거가 포함된다."""
        ranker = RootCauseRanker()
        root = _make_node(event_id="root", timestamp=100.0)
        leaf = _make_node(event_id="leaf", timestamp=102.0)
        dag = _make_dag([root, leaf], [_make_edge(root, leaf)])

        scores = {
            "topology": 0.9,
            "temporal": 1.0,
            "blast_radius": 0.5,
            "historical": 0.8,
        }
        evidence = ranker._build_evidence(root, scores, dag)

        assert any("DAG 루트 노드" in e for e in evidence)

    def test_evidence_contains_temporal_position(self):
        """시간순 위치가 근거에 포함된다."""
        ranker = RootCauseRanker()
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=200.0)
        dag = _make_dag([a, b])

        scores = {"topology": 0.5, "temporal": 1.0, "blast_radius": 0.5, "historical": 0.3}
        evidence = ranker._build_evidence(a, scores, dag)

        assert any("시간순 1/2번째" in e for e in evidence)

    def test_evidence_contains_affected_services(self):
        """영향받은 서비스가 근거에 포함된다."""
        ranker = RootCauseRanker()
        root = _make_node(event_id="root", service_name="db", timestamp=100.0)
        child = _make_node(event_id="child", service_name="payment", timestamp=102.0)
        dag = _make_dag([root, child], [_make_edge(root, child)])

        scores = {"topology": 0.9, "temporal": 1.0, "blast_radius": 0.8, "historical": 0.5}
        evidence = ranker._build_evidence(root, scores, dag)

        assert any("payment" in e for e in evidence)

    def test_high_historical_score_adds_evidence(self):
        """historical > 0.7이면 과거 이력 근거가 추가된다."""
        ranker = RootCauseRanker()
        node = _make_node()
        dag = _make_dag([node])

        scores = {
            "topology": 0.5,
            "temporal": 1.0,
            "blast_radius": 0.5,
            "historical": HISTORICAL_HIGH_THRESHOLD + 0.1,
        }
        evidence = ranker._build_evidence(node, scores, dag)

        assert any("과거 인시던트" in e for e in evidence)


# =============================================================================
# 14. Summary 생성 — 동작 검증 (Behavior)
# =============================================================================


class TestSummaryGenerationBehavior:
    """_generate_summary 동작 검증."""

    def test_summary_contains_service_and_event_type(self):
        """요약에 서비스명과 이벤트 타입이 포함된다."""
        ranker = RootCauseRanker()
        node = _make_node(service_name="db-pool-service", event_type="circuit_breaker_opened")
        dag = _make_dag([node])

        candidate = RootCauseCandidate(
            event_node=node,
            score=0.87,
            rank=1,
            evidence=["DAG 루트 노드", "시간순 1/5번째 발생", "3서비스 cascading"],
            contributing_factors={},
            affected_services=["payment", "order", "point"],
            cascade_depth=3,
        )

        summary = ranker._generate_summary(candidate, dag)
        assert "db-pool-service" in summary
        assert "circuit_breaker_opened" in summary
        assert "87%" in summary
        assert "3개 서비스" in summary
        assert "3단계" in summary


# =============================================================================
# 15. Utility 메서드 — 동작 검증 (Behavior)
# =============================================================================


class TestUtilityMethodsBehavior:
    """유틸리티 메서드 동작 검증."""

    def test_count_in_edges(self):
        """진입차수 카운트가 정확해야 한다."""
        a = _make_node(event_id="a")
        b = _make_node(event_id="b", timestamp=1001.0)
        c = _make_node(event_id="c", timestamp=1002.0)
        edges = [_make_edge(a, c), _make_edge(b, c)]
        dag = _make_dag([a, b, c], edges)

        assert RootCauseRanker._count_in_edges(c, dag) == 2
        assert RootCauseRanker._count_in_edges(a, dag) == 0

    def test_count_out_edges(self):
        """진출차수 카운트가 정확해야 한다."""
        a = _make_node(event_id="a")
        b = _make_node(event_id="b", timestamp=1001.0)
        c = _make_node(event_id="c", timestamp=1002.0)
        edges = [_make_edge(a, b), _make_edge(a, c)]
        dag = _make_dag([a, b, c], edges)

        assert RootCauseRanker._count_out_edges(a, dag) == 2
        assert RootCauseRanker._count_out_edges(b, dag) == 0

    def test_max_depth_from_leaf(self):
        """leaf 노드의 max_depth_from은 0."""
        leaf = _make_node(event_id="leaf")
        dag = _make_dag([leaf])

        assert RootCauseRanker._max_depth_from(leaf, dag) == 0

    def test_max_depth_from_root_in_chain(self):
        """선형 체인 A→B→C에서 A의 max_depth_from은 2."""
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=101.0)
        c = _make_node(event_id="c", timestamp=102.0)
        edges = [_make_edge(a, b), _make_edge(b, c)]
        dag = _make_dag([a, b, c], edges)

        assert RootCauseRanker._max_depth_from(a, dag) == 2
        assert RootCauseRanker._max_depth_from(b, dag) == 1

    def test_max_depth_from_diamond(self):
        """다이아몬드 A→B, A→C, B→D, C→D에서 A의 max_depth_from은 2."""
        a = _make_node(event_id="a", timestamp=100.0)
        b = _make_node(event_id="b", timestamp=101.0)
        c = _make_node(event_id="c", timestamp=101.5)
        d = _make_node(event_id="d", timestamp=102.0)
        edges = [_make_edge(a, b), _make_edge(a, c), _make_edge(b, d), _make_edge(c, d)]
        dag = _make_dag([a, b, c, d], edges)

        assert RootCauseRanker._max_depth_from(a, dag) == 2


# =============================================================================
# 16. Confidence 산정 — 동작 검증 (Behavior)
# =============================================================================


class TestConfidenceCalculationBehavior:
    """_calculate_confidence 동작 검증."""

    def test_empty_candidates_returns_zero(self):
        """후보가 없으면 신뢰도 0.0."""
        ranker = RootCauseRanker()
        dag = _make_dag([])

        assert ranker._calculate_confidence([], dag, []) == 0.0

    def test_higher_primary_score_higher_confidence(self):
        """1위 점수가 높을수록 신뢰도가 높아진다."""
        ranker = RootCauseRanker()
        node = _make_node()
        dag = _make_dag([node])

        high_score = [
            RootCauseCandidate(
                event_node=node,
                score=0.95,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            )
        ]
        low_score = [
            RootCauseCandidate(
                event_node=node,
                score=0.30,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            )
        ]

        conf_high = ranker._calculate_confidence(high_score, dag, [])
        conf_low = ranker._calculate_confidence(low_score, dag, [])
        assert conf_high > conf_low

    def test_co_occurrence_data_boosts_confidence(self):
        """과거 이력 데이터가 있으면 신뢰도가 올라간다."""
        ranker = RootCauseRanker()
        node = _make_node()
        dag = _make_dag([node])

        candidates = [
            RootCauseCandidate(
                event_node=node,
                score=0.50,
                rank=1,
                evidence=[],
                contributing_factors={},
                affected_services=[],
                cascade_depth=0,
            )
        ]

        conf_with = ranker._calculate_confidence(
            candidates,
            dag,
            [_make_correlation_result("a", "b")],
        )
        conf_without = ranker._calculate_confidence(candidates, dag, [])
        assert conf_with > conf_without


# =============================================================================
# 17. RankerInit — 계약 검증 (Contract)
# =============================================================================


class TestRankerInitContract:
    """RootCauseRanker 초기화 파라미터 계약 검증."""

    def test_default_weights(self):
        """기본 가중치가 설계 문서의 값과 일치해야 한다."""
        ranker = RootCauseRanker()
        assert ranker.weight_topology == WEIGHT_TOPOLOGY
        assert ranker.weight_temporal == WEIGHT_TEMPORAL
        assert ranker.weight_blast_radius == WEIGHT_BLAST_RADIUS
        assert ranker.weight_historical == WEIGHT_HISTORICAL

    def test_custom_weights(self):
        """사용자 지정 가중치가 적용되어야 한다."""
        ranker = RootCauseRanker(
            weight_topology=0.5,
            weight_temporal=0.2,
            weight_blast_radius=0.2,
            weight_historical=0.1,
        )
        assert ranker.weight_topology == 0.5
        assert ranker.weight_temporal == 0.2

    def test_custom_softmax_temperature(self):
        """사용자 지정 softmax_temperature가 적용되어야 한다."""
        ranker = RootCauseRanker(softmax_temperature=0.5)
        assert ranker.softmax_temperature == 0.5
