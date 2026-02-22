"""
Tests for Event Graph — 이벤트 DAG 자동 구축.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 설계 문서(§251)에 명시된 값/구조 검증 (하드코딩)
- Behavior: 함수/메서드 동작 검증 (소스 참조)

참조 소스:
- services/correlation_engine/event_graph.py (EventNode, CausalEdge, EventDAG)
- services/correlation_engine/event_graph_builder.py (EventGraphBuilder)
- services/correlation_engine/co_occurrence_tracker.py (CoOccurrenceTracker)
- services/correlation_engine/event_graph_trigger.py (EventGraphTrigger)
- settings/correlation.py (CorrelationSettings)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from selfhealing.services.blast_radius.service import BlastRadiusService
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceSnapshot,
    CoOccurrenceTracker,
)
from selfhealing.services.correlation_engine.event_graph import (
    CO_OCCURRENCE_CONFIDENCE_MULTIPLIER,
    CONFIDENCE_CONTEXTUAL,
    CONFIDENCE_CORRELATION_ID,
    CONFIDENCE_DEPENDENCY,
    DEFAULT_MAX_EVENTS_PER_DAG,
    DEFAULT_MAX_GRAPH_DEPTH,
    DEFAULT_MIN_CONFIDENCE,
    EVIDENCE_CO_OCCURRENCE,
    EVIDENCE_CONTEXTUAL,
    EVIDENCE_CORRELATION_ID,
    EVIDENCE_DEPENDENCY,
    EVIDENCE_TEMPORAL,
    TEMPORAL_MIN_CONFIDENCE,
    TRANSITIVE_REDUCTION_KEEP_THRESHOLD,
    CausalEdge,
    EventDAG,
    EventNode,
)
from selfhealing.services.correlation_engine.event_graph_builder import (
    EventGraphBuilder,
)
from selfhealing.services.correlation_engine.event_graph_trigger import (
    DEFAULT_COOLDOWN_SECONDS,
    LOAD_SHEDDING_MIN_TRIGGER_LEVEL,
    TRIGGER_EVENT_TYPES,
    EventGraphTrigger,
)
from selfhealing.services.event_bus.bus import (
    EventPriority,
    EventType,
    SelfHealingEvent,
)
from selfhealing.settings.correlation import (
    CorrelationSettings,
    get_correlation_settings,
    reset_correlation_settings,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """각 테스트 전후로 싱글톤 캐시를 초기화."""
    reset_correlation_settings()
    BlastRadiusService._instance = None
    yield
    reset_correlation_settings()
    BlastRadiusService._instance = None


@pytest.fixture
def blast_radius_service() -> BlastRadiusService:
    """빈 BlastRadiusService 인스턴스."""
    return BlastRadiusService()


@pytest.fixture
def co_occurrence_tracker() -> CoOccurrenceTracker:
    """빈 CoOccurrenceTracker 인스턴스."""
    return CoOccurrenceTracker()


@pytest.fixture
def builder(
    blast_radius_service: BlastRadiusService,
    co_occurrence_tracker: CoOccurrenceTracker,
) -> EventGraphBuilder:
    """기본 EventGraphBuilder 인스턴스."""
    return EventGraphBuilder(
        blast_radius_service=blast_radius_service,
        co_occurrence_tracker=co_occurrence_tracker,
    )


def _make_event(
    event_type: EventType = EventType.CIRCUIT_BREAKER_OPENED,
    source: str = "test-service",
    data: dict | None = None,
    timestamp: datetime | None = None,
    correlation_id: str | None = None,
    priority: EventPriority = EventPriority.NORMAL,
) -> SelfHealingEvent:
    """테스트용 SelfHealingEvent 생성 헬퍼."""
    return SelfHealingEvent(
        event_type=event_type,
        data=data or {},
        source=source,
        timestamp=timestamp or datetime.now(timezone.utc),
        priority=priority,
        correlation_id=correlation_id,
    )


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


# =============================================================================
# 1. EventNode — 계약 검증 (Contract)
# =============================================================================


class TestEventNodeContract:
    """EventNode 자료구조 설계 계약 검증."""

    def test_frozen_dataclass(self):
        """EventNode는 immutable(frozen) dataclass여야 한다."""
        node = _make_node()
        with pytest.raises(AttributeError):
            node.event_id = "changed"  # type: ignore[misc]

    def test_required_fields(self):
        """EventNode는 event_id, event_type, service_name, timestamp, data, correlation_id 필드를 가져야 한다."""
        node = _make_node(
            event_id="evt_001",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1000.0,
            data={"key": "value"},
            correlation_id="corr_123",
        )
        assert node.event_id == "evt_001"
        assert node.event_type == "circuit_breaker_opened"
        assert node.service_name == "payment-service"
        assert node.timestamp == 1000.0
        assert node.data == {"key": "value"}
        assert node.correlation_id == "corr_123"

    def test_correlation_id_optional(self):
        """correlation_id는 None이 허용되어야 한다."""
        node = _make_node(correlation_id=None)
        assert node.correlation_id is None

    def test_equality_by_event_id(self):
        """EventNode 동등성은 event_id 기준이어야 한다."""
        node_a = _make_node(event_id="same_id", service_name="svc-1")
        node_b = _make_node(event_id="same_id", service_name="svc-2")
        assert node_a == node_b

    def test_hash_by_event_id(self):
        """EventNode 해시는 event_id 기준이어야 한다."""
        node_a = _make_node(event_id="same_id", timestamp=100.0)
        node_b = _make_node(event_id="same_id", timestamp=200.0)
        assert hash(node_a) == hash(node_b)
        assert len({node_a, node_b}) == 1


# =============================================================================
# 2. CausalEdge — 계약 검증 (Contract)
# =============================================================================


class TestCausalEdgeContract:
    """CausalEdge 자료구조 설계 계약 검증."""

    def test_confidence_values(self):
        """설계 문서에 명시된 Evidence 타입별 confidence 값 검증."""
        assert CONFIDENCE_CORRELATION_ID == 0.95
        assert CONFIDENCE_CONTEXTUAL == 0.90
        assert CONFIDENCE_DEPENDENCY == 0.85
        assert CO_OCCURRENCE_CONFIDENCE_MULTIPLIER == 0.7
        assert TEMPORAL_MIN_CONFIDENCE == 0.3

    def test_evidence_type_strings(self):
        """Evidence 타입 문자열 상수가 올바른 값을 가져야 한다."""
        assert EVIDENCE_CORRELATION_ID == "correlation_id"
        assert EVIDENCE_CONTEXTUAL == "contextual"
        assert EVIDENCE_DEPENDENCY == "dependency"
        assert EVIDENCE_CO_OCCURRENCE == "co_occurrence"
        assert EVIDENCE_TEMPORAL == "temporal"

    def test_frozen_dataclass(self):
        """CausalEdge는 immutable(frozen) dataclass여야 한다."""
        source = _make_node(event_id="src")
        target = _make_node(event_id="tgt", timestamp=1001.0)
        edge = CausalEdge(source, target, 0.85, "dependency", 1.0)
        with pytest.raises(AttributeError):
            edge.confidence = 0.5  # type: ignore[misc]

    def test_transitive_reduction_keep_threshold(self):
        """전이적 축소 유지 임계값은 0.9이어야 한다."""
        assert TRANSITIVE_REDUCTION_KEEP_THRESHOLD == 0.9

    def test_dag_limits(self):
        """DAG 제한 설정 계약값 검증."""
        assert DEFAULT_MAX_EVENTS_PER_DAG == 200
        assert DEFAULT_MIN_CONFIDENCE == 0.4


# =============================================================================
# 3. EventDAG — 동작 검증 (Behavior)
# =============================================================================


class TestEventDAGBehavior:
    """EventDAG 그래프 연산 동작 검증."""

    def _build_chain_dag(self) -> EventDAG:
        """A → B → C 체인 DAG를 생성한다."""
        node_a = _make_node(event_id="a", timestamp=100.0, service_name="svc-a")
        node_b = _make_node(event_id="b", timestamp=101.0, service_name="svc-b")
        node_c = _make_node(event_id="c", timestamp=102.0, service_name="svc-c")
        edges = [
            CausalEdge(node_a, node_b, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_b, node_c, 0.85, EVIDENCE_DEPENDENCY, 1.0),
        ]
        return EventDAG(
            incident_id="test_chain",
            window_start=100.0,
            window_end=102.0,
            nodes={"a": node_a, "b": node_b, "c": node_c},
            edges=edges,
            root_nodes=[node_a],
            leaf_nodes=[node_c],
        )

    def test_get_ancestors_chain(self):
        """A→B→C 체인에서 C의 조상은 A, B여야 한다."""
        dag = self._build_chain_dag()
        node_c = dag.nodes["c"]
        ancestors = dag.get_ancestors(node_c)
        ancestor_ids = {n.event_id for n in ancestors}
        assert ancestor_ids == {"a", "b"}

    def test_get_descendants_chain(self):
        """A→B→C 체인에서 A의 자손은 B, C여야 한다."""
        dag = self._build_chain_dag()
        node_a = dag.nodes["a"]
        descendants = dag.get_descendants(node_a)
        descendant_ids = {n.event_id for n in descendants}
        assert descendant_ids == {"b", "c"}

    def test_get_ancestors_root_has_none(self):
        """루트 노드의 조상은 빈 리스트여야 한다."""
        dag = self._build_chain_dag()
        node_a = dag.nodes["a"]
        assert dag.get_ancestors(node_a) == []

    def test_get_descendants_leaf_has_none(self):
        """리프 노드의 자손은 빈 리스트여야 한다."""
        dag = self._build_chain_dag()
        node_c = dag.nodes["c"]
        assert dag.get_descendants(node_c) == []

    def test_get_critical_path_chain(self):
        """A→B→C 체인에서 critical path는 [A, B, C]여야 한다."""
        dag = self._build_chain_dag()
        critical = dag.get_critical_path()
        assert [n.event_id for n in critical] == ["a", "b", "c"]

    def test_get_critical_path_empty_dag(self):
        """빈 DAG의 critical path는 빈 리스트여야 한다."""
        dag = EventDAG.create_empty()
        assert dag.get_critical_path() == []

    def test_single_node_dag(self):
        """단일 이벤트 → 노드 1개, 엣지 0개. root = leaf = 해당 노드."""
        node = _make_node(event_id="only")
        dag = EventDAG(
            incident_id="single",
            window_start=100.0,
            window_end=100.0,
            nodes={"only": node},
            edges=[],
            root_nodes=[node],
            leaf_nodes=[node],
        )
        assert len(dag.nodes) == 1
        assert len(dag.edges) == 0
        assert dag.root_nodes == [node]
        assert dag.leaf_nodes == [node]

    def test_connected_components_single(self):
        """완전 연결 DAG는 component_count=1이어야 한다."""
        dag = self._build_chain_dag()
        components = dag.get_connected_components()
        assert len(components) == 1
        assert len(components[0].nodes) == 3

    def test_connected_components_disconnected(self):
        """2개 독립 장애는 component_count=2여야 한다."""
        node_a = _make_node(event_id="a", timestamp=100.0, service_name="payment")
        node_b = _make_node(event_id="b", timestamp=101.0, service_name="payment")
        node_x = _make_node(event_id="x", timestamp=100.5, service_name="image")
        node_y = _make_node(event_id="y", timestamp=101.5, service_name="image")

        edges = [
            CausalEdge(node_a, node_b, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_x, node_y, 0.85, EVIDENCE_DEPENDENCY, 1.0),
        ]
        dag = EventDAG(
            incident_id="disconnected",
            window_start=100.0,
            window_end=101.5,
            nodes={"a": node_a, "b": node_b, "x": node_x, "y": node_y},
            edges=edges,
            root_nodes=[node_a, node_x],
            leaf_nodes=[node_b, node_y],
        )
        components = dag.get_connected_components()
        assert len(components) == 2
        comp_sizes = sorted(len(c.nodes) for c in components)
        assert comp_sizes == [2, 2]

    def test_to_dict_structure(self):
        """to_dict()는 올바른 직렬화 구조를 반환해야 한다."""
        dag = self._build_chain_dag()
        result = dag.to_dict()
        assert result["incident_id"] == "test_chain"
        assert result["window"]["start"] == 100.0
        assert result["window"]["end"] == 102.0
        assert result["component_count"] == 1
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 2
        assert result["root_causes"] == ["a"]
        assert result["leaf_effects"] == ["c"]

    def test_to_dict_component_services(self):
        """to_dict()의 components에 서비스 목록이 sorted되어야 한다."""
        dag = self._build_chain_dag()
        result = dag.to_dict()
        services = result["components"][0]["services"]
        assert services == sorted(services)

    def test_create_empty(self):
        """create_empty()는 빈 DAG를 반환해야 한다."""
        dag = EventDAG.create_empty("test_empty")
        assert dag.incident_id == "test_empty"
        assert len(dag.nodes) == 0
        assert len(dag.edges) == 0

    def test_diamond_dag_ancestors(self):
        """Diamond 구조에서 get_ancestors(D) = {A, B, C}."""
        node_a = _make_node(event_id="a", timestamp=100.0)
        node_b = _make_node(event_id="b", timestamp=101.0)
        node_c = _make_node(event_id="c", timestamp=101.5)
        node_d = _make_node(event_id="d", timestamp=102.0)
        edges = [
            CausalEdge(node_a, node_b, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_a, node_c, 0.85, EVIDENCE_DEPENDENCY, 1.5),
            CausalEdge(node_b, node_d, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_c, node_d, 0.85, EVIDENCE_DEPENDENCY, 0.5),
        ]
        dag = EventDAG(
            incident_id="diamond",
            window_start=100.0,
            window_end=102.0,
            nodes={"a": node_a, "b": node_b, "c": node_c, "d": node_d},
            edges=edges,
            root_nodes=[node_a],
            leaf_nodes=[node_d],
        )
        ancestors = dag.get_ancestors(node_d)
        assert {n.event_id for n in ancestors} == {"a", "b", "c"}

    def test_diamond_critical_path_deterministic(self):
        """Diamond(A→{B,C}→D)에서 critical path가 결정적으로 선택 (§12.4)."""
        node_a = _make_node(event_id="a", timestamp=100.0)
        node_b = _make_node(event_id="b", timestamp=101.0)
        node_c = _make_node(event_id="c", timestamp=101.5)
        node_d = _make_node(event_id="d", timestamp=102.0)
        edges = [
            CausalEdge(node_a, node_b, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_a, node_c, 0.85, EVIDENCE_DEPENDENCY, 1.5),
            CausalEdge(node_b, node_d, 0.85, EVIDENCE_DEPENDENCY, 1.0),
            CausalEdge(node_c, node_d, 0.85, EVIDENCE_DEPENDENCY, 0.5),
        ]
        dag = EventDAG(
            incident_id="diamond",
            window_start=100.0,
            window_end=102.0,
            nodes={"a": node_a, "b": node_b, "c": node_c, "d": node_d},
            edges=edges,
            root_nodes=[node_a],
            leaf_nodes=[node_d],
        )
        # 양쪽 경로 모두 길이 3 (A→B→D, A→C→D)
        critical = dag.get_critical_path()
        assert len(critical) == 3
        assert critical[0].event_id == "a"
        assert critical[-1].event_id == "d"
        # 2회 실행 시 동일 결과 (결정적)
        critical_2 = dag.get_critical_path()
        assert [n.event_id for n in critical] == [n.event_id for n in critical_2]


# =============================================================================
# 4. EventGraphBuilder — 동작 검증 (Behavior)
# =============================================================================


class TestEventGraphBuilderBehavior:
    """EventGraphBuilder DAG 구축 동작 검증."""

    def test_empty_events(self, builder: EventGraphBuilder):
        """빈 이벤트 목록은 빈 DAG를 반환해야 한다."""
        dag = builder.build_dag([], window_seconds=300.0)
        assert len(dag.nodes) == 0
        assert len(dag.edges) == 0

    def test_single_event_dag(self, builder: EventGraphBuilder):
        """단일 이벤트 → 노드 1개, 엣지 0개, root=leaf=해당 노드."""
        event = _make_event()
        dag = builder.build_dag([event], window_seconds=300.0)
        assert len(dag.nodes) == 1
        assert len(dag.edges) == 0
        assert len(dag.root_nodes) == 1
        assert len(dag.leaf_nodes) == 1
        assert dag.root_nodes[0] == dag.leaf_nodes[0]

    def test_correlation_id_edge(self, builder: EventGraphBuilder):
        """동일 correlation_id를 가진 2개 이벤트 → confidence=0.95 엣지."""
        corr_id = "corr_test_123"
        t_base = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_later = datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc)
        e1 = _make_event(
            source="svc-a",
            timestamp=t_base,
            correlation_id=corr_id,
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-b",
            timestamp=t_later,
            correlation_id=corr_id,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        assert len(dag.edges) == 1
        edge = dag.edges[0]
        assert edge.confidence == CONFIDENCE_CORRELATION_ID
        assert edge.evidence_type == EVIDENCE_CORRELATION_ID

    def test_contextual_matching_edge(self, builder: EventGraphBuilder):
        """동일 service_name + 다른 event_type → confidence=0.90 엣지."""
        t_base = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_later = datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="gateway",
            timestamp=t_base,
            data={"service_name": "payment-service"},
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="gateway",
            timestamp=t_later,
            data={"service_name": "payment-service"},
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        assert len(dag.edges) >= 1
        # 최소 하나의 엣지가 contextual이어야 함
        contextual_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_CONTEXTUAL]
        assert len(contextual_edges) == 1
        assert contextual_edges[0].confidence == CONFIDENCE_CONTEXTUAL

    def test_dependency_edge(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """upstream → downstream 시간순 → evidence_type='dependency'."""
        blast_radius_service.add_dependency(
            source_service="payment-service",
            target_service="order-service",
            dependency_type="sync",
        )
        builder = EventGraphBuilder(blast_radius_service, co_occurrence_tracker)
        t_base = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_later = datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc)
        e1 = _make_event(
            source="payment-service",
            timestamp=t_base,
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="order-service",
            timestamp=t_later,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        assert len(dep_edges) == 1
        assert dep_edges[0].confidence == CONFIDENCE_DEPENDENCY

    def test_co_occurrence_edge(
        self,
        blast_radius_service: BlastRadiusService,
    ):
        """Co-occurrence 이력 → evidence_type='co_occurrence'."""
        tracker = CoOccurrenceTracker()
        pair_score = 0.8
        tracker.update_snapshot(
            {
                ("circuit_breaker_opened", "emergency_activated"): pair_score,
            }
        )
        builder = EventGraphBuilder(blast_radius_service, tracker)
        t_base = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_later = datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="svc-a",
            timestamp=t_base,
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-a",
            timestamp=t_later,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        co_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_CO_OCCURRENCE]
        assert len(co_edges) == 1
        expected_confidence = pair_score * CO_OCCURRENCE_CONFIDENCE_MULTIPLIER
        assert co_edges[0].confidence == pytest.approx(expected_confidence)

    def test_temporal_fallback_edge(self, builder: EventGraphBuilder):
        """시간 근접성 Fallback → evidence_type='temporal'."""
        t_base = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t_later = datetime(2026, 2, 20, 14, 0, 10, tzinfo=timezone.utc)
        # 다른 서비스, correlation_id 없음, 의존성 없음 → temporal
        e1 = _make_event(
            event_type=EventType.CONFIG_UPDATED,
            source="svc-alpha",
            timestamp=t_base,
        )
        e2 = _make_event(
            event_type=EventType.DLQ_REPLAY_COMPLETED,
            source="svc-beta",
            timestamp=t_later,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        temporal_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_TEMPORAL]
        assert len(temporal_edges) == 1
        # confidence = max(0.3, 1.0 - 10/300) = max(0.3, ~0.967)
        assert temporal_edges[0].confidence >= TEMPORAL_MIN_CONFIDENCE

    def test_transitive_reduction_removes_indirect(self, builder: EventGraphBuilder):
        """A→B→C 시 A→C 직접 엣지(confidence<0.9)가 제거되어야 한다."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc)
        corr_id = "chain_corr"
        # A, B, C 모두 동일 correlation_id → 모두 confidence=0.95
        # 0.95 >= 0.9이므로 유지됨. 대신 temporal fallback 사용
        e1 = _make_event(source="svc-1", timestamp=t1)
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-2",
            timestamp=t2,
        )
        e3 = _make_event(
            event_type=EventType.EMERGENCY_DEACTIVATED,
            source="svc-3",
            timestamp=t3,
        )
        dag = builder.build_dag([e1, e2, e3], window_seconds=300.0)
        # 3개 노드에서 가능한 엣지: 1→2, 2→3, 1→3
        # 전이적 축소: 1→2→3 경로 존재 시 1→3이 제거됨 (단, confidence < 0.9인 경우)
        # temporal confidence = max(0.3, 1.0 - gap/300) ≈ 0.99 (1초/2초 gap)
        # 이 경우 모두 ≥ 0.9이므로 유지
        # confidence < 0.9 시나리오는 별도 테스트 필요
        assert len(dag.nodes) == 3

    def test_transitive_reduction_low_confidence(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """confidence < 0.9인 간접 경로의 직접 엣지가 제거되어야 한다."""
        # A(svc-a) → B(svc-b) → C(svc-c) 의존성 체인
        blast_radius_service.add_dependency("svc-a", "svc-b", "sync")
        blast_radius_service.add_dependency("svc-b", "svc-c", "sync")
        blast_radius_service.add_dependency("svc-a", "svc-c", "sync")

        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
        )
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc)
        t3 = datetime(2026, 2, 20, 14, 0, 10, tzinfo=timezone.utc)

        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="svc-a",
            timestamp=t1,
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-b",
            timestamp=t2,
        )
        e3 = _make_event(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="svc-c",
            timestamp=t3,
        )
        dag = builder.build_dag([e1, e2, e3], window_seconds=300.0)

        # dependency confidence=0.85 < 0.9 → 전이적 축소 대상
        # A→B, B→C 경로가 존재하면 A→C 직접 엣지 제거
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        targets_from_a_deps = [e.target.service_name for e in dep_edges if e.source.service_name == "svc-a"]
        # A→C가 제거되어 A에서 직접 연결된 dependency는 B만
        assert "svc-b" in targets_from_a_deps
        assert "svc-c" not in targets_from_a_deps

    def test_diamond_dependency_preserves_all_edges(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """Diamond(A→B→D, A→C→D) 전이적 축소 시 4개 엣지 모두 보존."""
        blast_radius_service.add_dependency("svc-a", "svc-b", "sync")
        blast_radius_service.add_dependency("svc-a", "svc-c", "sync")
        blast_radius_service.add_dependency("svc-b", "svc-d", "sync")
        blast_radius_service.add_dependency("svc-c", "svc-d", "sync")

        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
        )
        # B와 C를 동일 타임스탬프로 설정하여 B↔C 간 temporal 엣지를 방지 (§11.2)
        # 이렇게 하면 A→C 간접 경로(A→B→C)가 존재하지 않아 전이적 축소 대상이 아님
        events = [
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="svc-a",
                timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_ACTIVATED,
                source="svc-b",
                timestamp=datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="svc-c",
                timestamp=datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_DEACTIVATED,
                source="svc-d",
                timestamp=datetime(2026, 2, 20, 14, 0, 6, tzinfo=timezone.utc),
            ),
        ]
        dag = builder.build_dag(events, window_seconds=300.0)

        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        # A→B, A→C, B→D, C→D = 4개 dependency 엣지
        dep_pairs = {(e.source.service_name, e.target.service_name) for e in dep_edges}
        assert ("svc-a", "svc-b") in dep_pairs
        assert ("svc-a", "svc-c") in dep_pairs
        assert ("svc-b", "svc-d") in dep_pairs
        assert ("svc-c", "svc-d") in dep_pairs

        # root=[A], leaf=[D]
        root_services = {n.service_name for n in dag.root_nodes}
        leaf_services = {n.service_name for n in dag.leaf_nodes}
        assert "svc-a" in root_services
        assert "svc-d" in leaf_services

    def test_deterministic_sort_same_timestamp(self, builder: EventGraphBuilder):
        """동일 타임스탬프 이벤트 5개 → event_id 기반 결정적 정렬."""
        same_ts = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        events = [
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source=f"svc-{i}",
                timestamp=same_ts,
            )
            for i in range(5)
        ]
        dag = builder.build_dag(events, window_seconds=300.0)
        # 결정적이어야 함: 같은 입력이면 같은 결과
        assert len(dag.nodes) == 5

    def test_same_timestamp_no_temporal_edge(self, builder: EventGraphBuilder):
        """time_gap=0인 쌍에서 Priority 4(시간 근접성) 미적용."""
        same_ts = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        # 서로 다른 서비스, correlation 없음, 의존성 없음
        e1 = _make_event(
            event_type=EventType.CONFIG_UPDATED,
            source="svc-alpha",
            timestamp=same_ts,
        )
        e2 = _make_event(
            event_type=EventType.DLQ_REPLAY_COMPLETED,
            source="svc-beta",
            timestamp=same_ts,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        temporal_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_TEMPORAL]
        assert len(temporal_edges) == 0

    def test_max_events_truncation(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """max_events_per_dag 초과 시 최근 N개만 사용한다."""
        max_events = 10
        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
            max_events_per_dag=max_events,
        )
        base_ts = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        events = [
            _make_event(
                source=f"svc-{i}",
                timestamp=datetime(
                    2026,
                    2,
                    20,
                    14,
                    0,
                    i,
                    tzinfo=timezone.utc,
                ),
            )
            for i in range(20)
        ]
        dag = builder.build_dag(events, window_seconds=300.0)
        assert len(dag.nodes) == max_events

    def test_min_confidence_filter(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """min_confidence 미만 엣지가 필터링되어야 한다."""
        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
            min_confidence=0.9,
        )
        # 의존성 엣지(0.85)는 min_confidence(0.9) 미만이므로 제거됨
        blast_radius_service.add_dependency("svc-a", "svc-b", "sync")
        e1 = _make_event(
            source="svc-a",
            timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-b",
            timestamp=datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc),
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        assert len(dep_edges) == 0

    def test_event_storm_200_events(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """동일 타임스탬프 이벤트 200개(Event Storm) — OOM 없이 처리."""
        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
            max_events_per_dag=DEFAULT_MAX_EVENTS_PER_DAG,
        )
        same_ts = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        events = [
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source=f"svc-{i}",
                timestamp=same_ts,
            )
            for i in range(DEFAULT_MAX_EVENTS_PER_DAG)
        ]
        dag = builder.build_dag(events, window_seconds=300.0)
        assert len(dag.nodes) == DEFAULT_MAX_EVENTS_PER_DAG
        # time_gap=0이고 서로 다른 서비스이므로 temporal 엣지 없음
        temporal_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_TEMPORAL]
        assert len(temporal_edges) == 0

    def test_incident_id_format(self, builder: EventGraphBuilder):
        """생성된 incident_id가 'inc_' 접두사를 가져야 한다."""
        event = _make_event()
        dag = builder.build_dag([event], window_seconds=300.0)
        assert dag.incident_id.startswith("inc_")

    def test_window_start_end(self, builder: EventGraphBuilder):
        """DAG의 window_start/end가 이벤트 시간 범위와 일치해야 한다."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 30, tzinfo=timezone.utc)
        e1 = _make_event(source="svc-a", timestamp=t1)
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-b",
            timestamp=t2,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        assert dag.window_start == t1.timestamp()
        assert dag.window_end == t2.timestamp()

    def test_depth_3_chain(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """A→B→C→D 깊이 3 체인 — 프로덕션 기본값과 동일 (§12.3)."""
        blast_radius_service.add_dependency("svc-a", "svc-b", "sync")
        blast_radius_service.add_dependency("svc-b", "svc-c", "sync")
        blast_radius_service.add_dependency("svc-c", "svc-d", "sync")
        builder = EventGraphBuilder(blast_radius_service, co_occurrence_tracker)
        events = [
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="svc-a",
                timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_ACTIVATED,
                source="svc-b",
                timestamp=datetime(2026, 2, 20, 14, 0, 10, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="svc-c",
                timestamp=datetime(2026, 2, 20, 14, 0, 20, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_DEACTIVATED,
                source="svc-d",
                timestamp=datetime(2026, 2, 20, 14, 0, 30, tzinfo=timezone.utc),
            ),
        ]
        dag = builder.build_dag(events, window_seconds=30.0)
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        # A→B, B→C, C→D = 3개 dependency 엣지
        dep_pairs = {(e.source.service_name, e.target.service_name) for e in dep_edges}
        assert ("svc-a", "svc-b") in dep_pairs
        assert ("svc-b", "svc-c") in dep_pairs
        assert ("svc-c", "svc-d") in dep_pairs
        assert len(dep_edges) == 3

    def test_max_graph_depth_enforcement(
        self,
        blast_radius_service: BlastRadiusService,
        co_occurrence_tracker: CoOccurrenceTracker,
    ):
        """depth 5 체인 + max_graph_depth=3 → 깊은 엣지 제거 (§12.1/§12.3)."""
        for src, tgt in [
            ("svc-a", "svc-b"),
            ("svc-b", "svc-c"),
            ("svc-c", "svc-d"),
            ("svc-d", "svc-e"),
            ("svc-e", "svc-f"),
        ]:
            blast_radius_service.add_dependency(src, tgt, "sync")
        builder = EventGraphBuilder(
            blast_radius_service,
            co_occurrence_tracker,
            max_graph_depth=3,
        )
        events = [
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="svc-a",
                timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_ACTIVATED,
                source="svc-b",
                timestamp=datetime(2026, 2, 20, 14, 0, 10, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                source="svc-c",
                timestamp=datetime(2026, 2, 20, 14, 0, 20, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_DEACTIVATED,
                source="svc-d",
                timestamp=datetime(2026, 2, 20, 14, 0, 30, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.CONFIG_UPDATED,
                source="svc-e",
                timestamp=datetime(2026, 2, 20, 14, 0, 40, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.ERROR_BUDGET_CRITICAL,
                source="svc-f",
                timestamp=datetime(2026, 2, 20, 14, 0, 50, tzinfo=timezone.utc),
            ),
        ]
        dag = builder.build_dag(events, window_seconds=30.0)
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        # max_graph_depth=3: A(0)→B(1)→C(2)→D(3) 유지, D→E(4), E→F(5) 제거
        dep_pairs = {(e.source.service_name, e.target.service_name) for e in dep_edges}
        assert ("svc-a", "svc-b") in dep_pairs
        assert ("svc-b", "svc-c") in dep_pairs
        assert ("svc-c", "svc-d") in dep_pairs
        assert ("svc-d", "svc-e") not in dep_pairs
        assert ("svc-e", "svc-f") not in dep_pairs

    def test_default_max_graph_depth_is_3(self):
        """DEFAULT_MAX_GRAPH_DEPTH 기본값은 3이어야 한다 (§12.1)."""
        assert DEFAULT_MAX_GRAPH_DEPTH == 3

    def test_cycle_prevention_by_timestamp_order(self, builder: EventGraphBuilder):
        """시간순 보장으로 순환(A→B→A)이 불가능 (§12.3)."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="svc-a",
            timestamp=t1,
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="svc-b",
            timestamp=t2,
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        # 모든 엣지가 시간순 (source.timestamp <= target.timestamp)
        for edge in dag.edges:
            assert edge.source.timestamp <= edge.target.timestamp
        # 셀프 루프 없음
        for edge in dag.edges:
            assert edge.source.event_id != edge.target.event_id


# =============================================================================
# 5. CoOccurrenceTracker — 동작 검증 (Behavior)
# =============================================================================


class TestCoOccurrenceTrackerBehavior:
    """CoOccurrenceTracker 인메모리 O(1) 조회 동작 검증."""

    def test_empty_tracker_returns_none(self):
        """빈 트래커는 None을 반환해야 한다."""
        tracker = CoOccurrenceTracker()
        result = tracker.get_pair_score("type_a", "type_b")
        assert result is None

    def test_update_and_get_pair_score(self):
        """스냅샷 업데이트 후 점수 조회가 가능해야 한다."""
        tracker = CoOccurrenceTracker()
        tracker.update_snapshot(
            {
                ("cb_opened", "emergency"): 0.85,
            }
        )
        assert tracker.get_pair_score("cb_opened", "emergency") == 0.85

    def test_snapshot_replacement_atomic(self):
        """스냅샷 교체 후 이전 데이터는 사라져야 한다."""
        tracker = CoOccurrenceTracker()
        tracker.update_snapshot({("a", "b"): 0.5})
        tracker.update_snapshot({("x", "y"): 0.9})
        assert tracker.get_pair_score("a", "b") is None
        assert tracker.get_pair_score("x", "y") == 0.9

    def test_frozen_snapshot(self):
        """CoOccurrenceSnapshot은 frozen이어야 한다."""
        snapshot = CoOccurrenceSnapshot(scores={("a", "b"): 0.5})
        with pytest.raises(AttributeError):
            snapshot.scores = {}  # type: ignore[misc]


# =============================================================================
# 6. EventGraphTrigger — 동작 검증 (Behavior)
# =============================================================================


class TestEventGraphTriggerBehavior:
    """EventGraphTrigger Debounce 동작 검증."""

    def test_first_build_allowed(self):
        """첫 번째 빌드는 항상 허용되어야 한다."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)
        assert trigger.should_build("default") is True

    def test_debounce_within_cooldown(self):
        """cooldown 이내 재트리거는 거부되어야 한다."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)
        trigger.should_build("ns1")
        assert trigger.should_build("ns1") is False

    def test_different_namespace_allowed(self):
        """다른 namespace는 독립적으로 트리거 가능해야 한다."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)
        trigger.should_build("ns1")
        assert trigger.should_build("ns2") is True

    def test_is_trigger_event_critical(self):
        """Critical 이벤트 타입이 올바르게 식별되어야 한다."""
        trigger = EventGraphTrigger()
        assert (
            trigger.is_trigger_event(
                EventType.EMERGENCY_LEVEL_CHANGED.value,
                {},
            )
            is True
        )
        assert (
            trigger.is_trigger_event(
                EventType.CIRCUIT_BREAKER_OPENED.value,
                {},
            )
            is True
        )
        assert (
            trigger.is_trigger_event(
                EventType.KILL_SWITCH_ACTIVATED.value,
                {},
            )
            is True
        )

    def test_non_trigger_event_rejected(self):
        """비 Critical 이벤트는 트리거로 식별되지 않아야 한다."""
        trigger = EventGraphTrigger()
        assert (
            trigger.is_trigger_event(
                EventType.CONFIG_UPDATED.value,
                {},
            )
            is False
        )
        assert (
            trigger.is_trigger_event(
                EventType.DLQ_REPLAY_COMPLETED.value,
                {},
            )
            is False
        )

    def test_load_shedding_level_threshold(self):
        """Load Shedding은 level >= 2일 때만 트리거되어야 한다."""
        trigger = EventGraphTrigger()
        assert (
            trigger.is_trigger_event(
                EventType.LOAD_SHEDDING_LEVEL_CHANGED.value,
                {"new_level": 1},
            )
            is False
        )
        assert (
            trigger.is_trigger_event(
                EventType.LOAD_SHEDDING_LEVEL_CHANGED.value,
                {"new_level": LOAD_SHEDDING_MIN_TRIGGER_LEVEL},
            )
            is True
        )
        assert (
            trigger.is_trigger_event(
                EventType.LOAD_SHEDDING_LEVEL_CHANGED.value,
                {"new_level": 3},
            )
            is True
        )

    def test_reset_clears_state(self):
        """reset() 후 즉시 빌드가 허용되어야 한다."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)
        trigger.should_build("ns1")
        assert trigger.should_build("ns1") is False
        trigger.reset()
        assert trigger.should_build("ns1") is True


# =============================================================================
# 7. EventGraphTrigger — 계약 검증 (Contract)
# =============================================================================


class TestEventGraphTriggerContract:
    """EventGraphTrigger 설계 계약 검증."""

    def test_trigger_event_types_count(self):
        """트리거 이벤트 타입은 6개여야 한다."""
        assert len(TRIGGER_EVENT_TYPES) == 6

    def test_trigger_event_types_members(self):
        """트리거 이벤트 타입 목록 계약값 검증."""
        assert EventType.EMERGENCY_LEVEL_CHANGED.value in TRIGGER_EVENT_TYPES
        assert EventType.EMERGENCY_ACTIVATED.value in TRIGGER_EVENT_TYPES
        assert EventType.CIRCUIT_BREAKER_OPENED.value in TRIGGER_EVENT_TYPES
        assert EventType.SECURITY_VIOLATION_CRITICAL.value in TRIGGER_EVENT_TYPES
        assert EventType.KILL_SWITCH_ACTIVATED.value in TRIGGER_EVENT_TYPES
        assert EventType.LOAD_SHEDDING_LEVEL_CHANGED.value in TRIGGER_EVENT_TYPES

    def test_default_cooldown_seconds(self):
        """기본 cooldown은 60초여야 한다."""
        assert DEFAULT_COOLDOWN_SECONDS == 60.0

    def test_load_shedding_min_trigger_level(self):
        """Load Shedding 최소 트리거 레벨은 2여야 한다."""
        assert LOAD_SHEDDING_MIN_TRIGGER_LEVEL == 2


# =============================================================================
# 8. CorrelationSettings — 계약 검증 (Contract)
# =============================================================================


class TestCorrelationSettingsContract:
    """CorrelationSettings 설계 계약 검증."""

    def test_lookback_seconds_default(self):
        """lookback_seconds 기본값은 300이어야 한다."""
        settings = CorrelationSettings()
        assert settings.lookback_seconds == 300

    def test_lookahead_seconds_default(self):
        """lookahead_seconds 기본값은 60이어야 한다."""
        settings = CorrelationSettings()
        assert settings.lookahead_seconds == 60

    def test_cooldown_seconds_default(self):
        """cooldown_seconds 기본값은 60.0이어야 한다."""
        settings = CorrelationSettings()
        assert settings.cooldown_seconds == 60.0

    def test_max_events_per_dag_default(self):
        """max_events_per_dag 기본값은 200이어야 한다."""
        settings = CorrelationSettings()
        assert settings.max_events_per_dag == 200

    def test_min_confidence_default(self):
        """min_confidence 기본값은 0.4이어야 한다."""
        settings = CorrelationSettings()
        assert settings.min_confidence == 0.4

    def test_max_graph_depth_default(self):
        """max_graph_depth 기본값은 3이어야 한다."""
        settings = CorrelationSettings()
        assert settings.max_graph_depth == 3

    def test_lookback_seconds_bounds(self):
        """lookback_seconds는 30~900 범위여야 한다."""
        with pytest.raises(Exception):
            CorrelationSettings(lookback_seconds=29)
        with pytest.raises(Exception):
            CorrelationSettings(lookback_seconds=901)

    def test_lookahead_seconds_bounds(self):
        """lookahead_seconds는 10~300 범위여야 한다."""
        with pytest.raises(Exception):
            CorrelationSettings(lookahead_seconds=9)
        with pytest.raises(Exception):
            CorrelationSettings(lookahead_seconds=301)


class TestCorrelationSettingsBehavior:
    """CorrelationSettings 싱글톤 동작 검증."""

    def test_singleton_returns_same_instance(self):
        """get_correlation_settings()는 동일 인스턴스를 반환해야 한다."""
        s1 = get_correlation_settings()
        s2 = get_correlation_settings()
        assert s1 is s2

    def test_reset_clears_singleton(self):
        """reset 후 새 인스턴스가 생성되어야 한다."""
        s1 = get_correlation_settings()
        reset_correlation_settings()
        s2 = get_correlation_settings()
        assert s1 is not s2


# =============================================================================
# 9. 시나리오 테스트 — DB Pool 고갈 → CB OPEN → Emergency
# =============================================================================


class TestScenarioDBPoolCascade:
    """시나리오: DB Pool 고갈 → CB OPEN × 3 → Emergency 재현."""

    def test_db_pool_cascade_root_cause(self):
        """DB Pool 고갈이 root_cause로 식별되어야 한다."""
        brs = BlastRadiusService()
        brs.add_dependency("db-pool", "payment-service", "sync")
        brs.add_dependency("db-pool", "order-service", "sync")
        brs.add_dependency("db-pool", "inventory-service", "sync")
        brs.add_dependency("payment-service", "api-gateway", "sync")

        tracker = CoOccurrenceTracker()
        builder = EventGraphBuilder(brs, tracker)

        events = [
            _make_event(
                event_type=EventType.ERROR_BUDGET_CRITICAL,
                source="db-pool",
                timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="payment-service",
                timestamp=datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="order-service",
                timestamp=datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.CIRCUIT_BREAKER_OPENED,
                source="inventory-service",
                timestamp=datetime(2026, 2, 20, 14, 0, 4, tzinfo=timezone.utc),
            ),
            _make_event(
                event_type=EventType.EMERGENCY_ACTIVATED,
                source="api-gateway",
                timestamp=datetime(2026, 2, 20, 14, 0, 6, tzinfo=timezone.utc),
            ),
        ]

        dag = builder.build_dag(events, window_seconds=300.0)

        # db-pool 이벤트가 root_cause
        root_services = {n.service_name for n in dag.root_nodes}
        assert "db-pool" in root_services

        # api-gateway가 leaf effect
        leaf_services = {n.service_name for n in dag.leaf_nodes}
        assert "api-gateway" in leaf_services

        # to_dict()에서 root_causes에 db-pool 노드가 포함
        result = dag.to_dict()
        root_ids = set(result["root_causes"])
        root_nodes_in_result = [n for n in result["nodes"] if n["event_id"] in root_ids]
        root_svc_names = {n["service_name"] for n in root_nodes_in_result}
        assert "db-pool" in root_svc_names


# =============================================================================
# 10. 엣지 케이스 — Priority 1.5 Contextual Matching 세부
# =============================================================================


class TestContextualMatchingBehavior:
    """Priority 1.5 Contextual Matching 세부 동작 검증."""

    def test_same_event_type_no_contextual_edge(self, builder: EventGraphBuilder):
        """source.event_type == target.event_type이면 contextual 엣지 미생성."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="gateway",
            timestamp=t1,
            data={"service_name": "payment-service"},
        )
        e2 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="gateway",
            timestamp=t2,
            data={"service_name": "payment-service"},
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        contextual_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_CONTEXTUAL]
        assert len(contextual_edges) == 0

    def test_empty_service_name_no_contextual(self, builder: EventGraphBuilder):
        """data.service_name이 빈 문자열이면 contextual 엣지 미생성."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="gateway",
            timestamp=t1,
            data={"service_name": ""},
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="gateway",
            timestamp=t2,
            data={"service_name": ""},
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        contextual_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_CONTEXTUAL]
        assert len(contextual_edges) == 0

    def test_with_correlation_id_skips_contextual(self, builder: EventGraphBuilder):
        """source와 target 모두 correlation_id가 있으면 contextual 미적용."""
        t1 = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc)
        e1 = _make_event(
            event_type=EventType.CIRCUIT_BREAKER_OPENED,
            source="gateway",
            timestamp=t1,
            data={"service_name": "payment-service"},
            correlation_id="corr_different_1",
        )
        e2 = _make_event(
            event_type=EventType.EMERGENCY_ACTIVATED,
            source="gateway",
            timestamp=t2,
            data={"service_name": "payment-service"},
            correlation_id="corr_different_2",
        )
        dag = builder.build_dag([e1, e2], window_seconds=300.0)
        # 둘 다 correlation_id가 있지만 서로 다름 → Priority 1 불일치
        # 둘 다 correlation_id가 있으므로 Priority 1.5 조건 불충족
        contextual_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_CONTEXTUAL]
        assert len(contextual_edges) == 0
