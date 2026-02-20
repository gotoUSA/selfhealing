"""
Tests for Incident Timeline — 통합 타임라인 자동 생성.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 설계 문서(§255)에 명시된 값/구조 검증 (하드코딩)
- Behavior: 함수/메서드 동작 검증 (소스 참조)

참조 소스:
- services/correlation_engine/incident_timeline.py (IncidentTimelineBuilder, IncidentTimeline, TimelineEntry, TimelinePhase)
- services/correlation_engine/event_graph.py (EventNode, CausalEdge, EventDAG)
- services/correlation_engine/root_cause_ranker.py (RootCauseCandidate, RootCauseAnalysis)
"""

from __future__ import annotations

import uuid

import pytest

from selfhealing.services.correlation_engine.event_graph import (
    CausalEdge,
    EventDAG,
    EventNode,
)
from selfhealing.services.correlation_engine.incident_timeline import (
    CATEGORY_SEVERITY_DEFAULTS,
    DEFAULT_SEVERITY,
    DESCRIPTION_MAX_LENGTH,
    HUMAN_AVG_RESPONSE_SECONDS,
    MAX_METADATA_VALUE_BYTES,
    MITIGATION_EVENT_TYPES,
    RESOLUTION_EVENT_TYPES,
    IncidentTimeline,
    IncidentTimelineBuilder,
    TimelineEntry,
    TimelinePhase,
    TimelineStatus,
    _calc_speedup_factor,
    _format_duration,
    _humanize_event_type,
    _resolve_severity,
    _sanitize_value,
    _severity_icon,
    _status_badge,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
    RootCauseCandidate,
)


# =============================================================================
# Fixtures (이 파일 전용)
# =============================================================================


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
    incident_id: str = "inc_test_001",
) -> EventDAG:
    """테스트용 EventDAG 생성 헬퍼."""
    nodes_dict = {n.event_id: n for n in nodes}
    edges = edges or []

    target_ids = {e.target.event_id for e in edges}
    source_ids = {e.source.event_id for e in edges}

    root_nodes = [n for n in nodes if n.event_id not in target_ids]
    leaf_nodes = [n for n in nodes if n.event_id not in source_ids]

    timestamps = [n.timestamp for n in nodes]
    return EventDAG(
        incident_id=incident_id,
        window_start=min(timestamps) if timestamps else 0.0,
        window_end=max(timestamps) if timestamps else 0.0,
        nodes=nodes_dict,
        edges=edges,
        root_nodes=root_nodes,
        leaf_nodes=leaf_nodes,
    )


def _make_candidate(
    node: EventNode,
    score: float = 0.87,
    rank: int = 1,
) -> RootCauseCandidate:
    """테스트용 RootCauseCandidate 생성 헬퍼."""
    return RootCauseCandidate(
        event_node=node,
        score=score,
        rank=rank,
        evidence=["DAG root node", "Earliest event"],
        contributing_factors={"topology": 0.9, "temporal": 0.85},
        affected_services=["payment-service", "order-service"],
        cascade_depth=2,
    )


def _make_analysis(
    dag: EventDAG,
    primary_node: EventNode,
    candidates: list[RootCauseCandidate] | None = None,
    summary: str = "db-pool-service의 circuit_breaker_opened (확률 87%)",
) -> RootCauseAnalysis:
    """테스트용 RootCauseAnalysis 생성 헬퍼."""
    primary = _make_candidate(primary_node)
    cands = candidates or [primary]
    return RootCauseAnalysis(
        incident_id=dag.incident_id,
        analyzed_at=1000.0,
        dag=dag,
        candidates=cands,
        primary_cause=primary,
        confidence=0.87,
        summary=summary,
    )


# =============================================================================
# Contract 테스트: 상수 값 검증 (설계 문서 §255)
# =============================================================================


class TestConstants:
    """§255에 명시된 상수 값 검증."""

    def test_default_severity(self):
        """SEVERITY_MAP 미등록 이벤트 기본값은 'info'."""
        assert DEFAULT_SEVERITY == "info"

    def test_description_max_length(self):
        """설명 최대 길이 = 500 (PagerDuty 패턴)."""
        assert DESCRIPTION_MAX_LENGTH == 500

    def test_resolution_event_types_contains_cb_closed(self):
        """circuit_breaker_closed는 resolution 이벤트."""
        assert "circuit_breaker_closed" in RESOLUTION_EVENT_TYPES

    def test_resolution_event_types_contains_emergency_deactivated(self):
        """emergency_deactivated는 resolution 이벤트."""
        assert "emergency_deactivated" in RESOLUTION_EVENT_TYPES

    def test_resolution_event_types_count(self):
        """Resolution 이벤트 타입은 7개."""
        assert len(RESOLUTION_EVENT_TYPES) == 7

    def test_mitigation_event_types_count(self):
        """R3: Mitigation 이벤트 타입은 6개 (EventGraphTrigger 매핑)."""
        assert len(MITIGATION_EVENT_TYPES) == 6

    def test_mitigation_event_types_contains_throttle(self):
        """R3: throttle_limit_changed는 mitigation 이벤트."""
        assert "throttle_limit_changed" in MITIGATION_EVENT_TYPES

    def test_max_metadata_value_bytes(self):
        """R4: 메타데이터 값 최대 바이트는 1024."""
        assert MAX_METADATA_VALUE_BYTES == 1024

    def test_human_avg_response_seconds(self):
        """R3: 인간 SRE 평균 대응 시간 기준선은 900초(15분)."""
        assert HUMAN_AVG_RESPONSE_SECONDS == 900.0


# =============================================================================
# Contract 테스트: TimelineEntry 자료구조
# =============================================================================


class TestTimelineEntry:
    """TimelineEntry dataclass 검증."""

    def test_frozen_dataclass(self):
        """TimelineEntry는 immutable."""
        entry = TimelineEntry(
            timestamp=1000.0,
            event_type="circuit_breaker_opened",
            service_name="test-svc",
            description="test",
            severity="critical",
            is_root_cause=False,
            is_resolution=False,
            causal_parent=None,
            metadata={},
        )
        with pytest.raises(AttributeError):
            entry.severity = "info"  # type: ignore[misc]

    def test_formatted_time(self):
        """formatted_time은 HH:MM:SS UTC 포맷."""
        # 1000.0 = 1970-01-01T00:16:40 UTC
        entry = TimelineEntry(
            timestamp=1000.0,
            event_type="test",
            service_name="svc",
            description="desc",
            severity="info",
            is_root_cause=False,
            is_resolution=False,
            causal_parent=None,
        )
        assert entry.formatted_time == "00:16:40"

    def test_to_dict_keys(self):
        """to_dict()는 설계 문서에 명시된 모든 키를 포함 (R1: phase_name, is_re_escalation 추가)."""
        entry = TimelineEntry(
            timestamp=1000.0,
            event_type="circuit_breaker_opened",
            service_name="svc",
            description="desc",
            severity="critical",
            is_root_cause=True,
            is_resolution=False,
            causal_parent="parent-id",
            metadata={"level": 3},
        )
        d = entry.to_dict()
        expected_keys = {
            "timestamp",
            "formatted_time",
            "event_type",
            "service_name",
            "description",
            "severity",
            "is_root_cause",
            "is_resolution",
            "causal_parent",
            "phase_name",
            "is_re_escalation",
            "metadata",
        }
        assert set(d.keys()) == expected_keys

    def test_default_metadata_empty_dict(self):
        """metadata 기본값은 빈 dict."""
        entry = TimelineEntry(
            timestamp=1000.0,
            event_type="test",
            service_name="svc",
            description="desc",
            severity="info",
            is_root_cause=False,
            is_resolution=False,
            causal_parent=None,
        )
        assert entry.metadata == {}


# =============================================================================
# Contract 테스트: TimelinePhase 자료구조
# =============================================================================


class TestTimelinePhase:
    """TimelinePhase dataclass 검증."""

    def test_frozen_dataclass(self):
        """TimelinePhase는 immutable."""
        phase = TimelinePhase(
            name="detection",
            started_at=1000.0,
            ended_at=1003.0,
            duration_seconds=3.0,
            key_events=["circuit_breaker_opened"],
        )
        with pytest.raises(AttributeError):
            phase.name = "recovery"  # type: ignore[misc]

    def test_to_dict_keys(self):
        """to_dict()은 6개 필드를 포함 (R1: re_escalation_count 추가)."""
        phase = TimelinePhase(
            name="escalation",
            started_at=1000.0,
            ended_at=1019.0,
            duration_seconds=19.0,
            key_events=["circuit_breaker_opened", "emergency_level_changed"],
        )
        d = phase.to_dict()
        assert set(d.keys()) == {
            "name",
            "started_at",
            "ended_at",
            "duration_seconds",
            "key_events",
            "re_escalation_count",
        }


# =============================================================================
# Contract 테스트: IncidentTimeline 메트릭
# =============================================================================


class TestIncidentTimelineMetrics:
    """IncidentTimeline 메트릭 메서드 검증."""

    def test_get_mttd_basic(self):
        """MTTD = 첫 이벤트 ~ 첫 critical 시간 차."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=1000.0,
            resolved_at=1200.0,
            duration_seconds=200.0,
            root_cause_summary="test",
            entries=[
                TimelineEntry(
                    timestamp=1000.0,
                    event_type="info_event",
                    service_name="svc",
                    description="first",
                    severity="info",
                    is_root_cause=False,
                    is_resolution=False,
                    causal_parent=None,
                ),
                TimelineEntry(
                    timestamp=1003.0,
                    event_type="circuit_breaker_opened",
                    service_name="svc",
                    description="cb open",
                    severity="critical",
                    is_root_cause=True,
                    is_resolution=False,
                    causal_parent=None,
                ),
            ],
            affected_services=["svc"],
            phases=[],
            dag_reference="inc_001",
        )
        assert timeline.get_mttd() == 3.0

    def test_get_mttd_no_critical_returns_none(self):
        """critical 이벤트 없으면 MTTD = None."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=1000.0,
            resolved_at=None,
            duration_seconds=None,
            root_cause_summary="test",
            entries=[
                TimelineEntry(
                    timestamp=1000.0,
                    event_type="info_event",
                    service_name="svc",
                    description="first",
                    severity="info",
                    is_root_cause=False,
                    is_resolution=False,
                    causal_parent=None,
                ),
            ],
            affected_services=["svc"],
            phases=[],
            dag_reference="inc_001",
        )
        # 첫 번째 이벤트 = first, critical 없음 → 그래도 first와 first_critical 둘다
        # first_critical은 None이므로 MTTD = None
        assert timeline.get_mttd() is None

    def test_get_mttd_empty_entries(self):
        """빈 entries면 MTTD = None."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=0.0,
            resolved_at=None,
            duration_seconds=None,
            root_cause_summary="test",
            entries=[],
            affected_services=[],
            phases=[],
            dag_reference=None,
        )
        assert timeline.get_mttd() is None

    def test_get_mttr_basic(self):
        """MTTR = resolved_at - started_at."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=1000.0,
            resolved_at=1222.0,
            duration_seconds=222.0,
            root_cause_summary="test",
            entries=[],
            affected_services=[],
            phases=[],
            dag_reference=None,
        )
        assert timeline.get_mttr() == 222.0

    def test_get_mttr_unresolved_returns_none(self):
        """미해결 인시던트면 MTTR = None."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=1000.0,
            resolved_at=None,
            duration_seconds=None,
            root_cause_summary="test",
            entries=[],
            affected_services=[],
            phases=[],
            dag_reference=None,
        )
        assert timeline.get_mttr() is None

    def test_escalation_rate(self):
        """escalation_rate = critical 수 / 전체 수."""
        entries = [
            TimelineEntry(
                timestamp=1000.0 + i,
                event_type="e",
                service_name="svc",
                description="d",
                severity="critical" if i < 2 else "info",
                is_root_cause=False,
                is_resolution=False,
                causal_parent=None,
            )
            for i in range(5)
        ]
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=1000.0,
            resolved_at=None,
            duration_seconds=None,
            root_cause_summary="test",
            entries=entries,
            affected_services=["svc"],
            phases=[],
            dag_reference=None,
        )
        assert timeline.get_escalation_rate() == pytest.approx(0.4)

    def test_escalation_rate_empty(self):
        """빈 entries면 escalation_rate = 0."""
        timeline = IncidentTimeline(
            incident_id="inc_001",
            started_at=0.0,
            resolved_at=None,
            duration_seconds=None,
            root_cause_summary="test",
            entries=[],
            affected_services=[],
            phases=[],
            dag_reference=None,
        )
        assert timeline.get_escalation_rate() == 0.0


# =============================================================================
# Contract 테스트: SEVERITY_MAP 매핑
# =============================================================================


class TestSeverityMap:
    """SEVERITY_MAP — §255에 명시된 매핑 검증."""

    def test_cb_opened_is_critical(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["circuit_breaker_opened"] == "critical"

    def test_emergency_level_changed_is_critical(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["emergency_level_changed"] == "critical"

    def test_error_budget_critical_is_warning(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["error_budget_critical"] == "warning"

    def test_cb_half_opened_is_info(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["circuit_breaker_half_opened"] == "info"

    def test_cb_closed_is_recovery(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["circuit_breaker_closed"] == "recovery"

    def test_emergency_deactivated_is_recovery(self):
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP["emergency_deactivated"] == "recovery"

    def test_unknown_event_defaults_to_info(self):
        """SEVERITY_MAP에 없는 이벤트는 'info' 기본값."""
        builder = IncidentTimelineBuilder()
        assert builder.SEVERITY_MAP.get("unknown_event_xyz", DEFAULT_SEVERITY) == "info"


# =============================================================================
# Behavior 테스트: IncidentTimelineBuilder.build()
# =============================================================================


class TestBuildSingleEvent:
    """단일 이벤트 DAG → 1항목 타임라인."""

    def test_single_event_produces_one_entry(self):
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert len(timeline.entries) == 1
        assert timeline.entries[0].event_type == "circuit_breaker_opened"
        assert timeline.entries[0].service_name == "payment-service"

    def test_single_event_is_root_cause(self):
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].is_root_cause is True

    def test_single_event_severity(self):
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].severity == "critical"


class TestBuildOpenCloseChain:
    """OPEN → CLOSE 체인 — started_at/resolved_at 검증."""

    def test_duration_calculation(self):
        """duration = resolved_at - started_at."""
        node_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1000.0,
        )
        node_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            service_name="payment-service",
            timestamp=1200.0,
        )
        edge = CausalEdge(
            source=node_open,
            target=node_close,
            confidence=0.9,
            evidence_type="contextual",
            time_gap_seconds=200.0,
        )
        dag = _make_dag([node_open, node_close], [edge])
        analysis = _make_analysis(dag, node_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.started_at == 1000.0
        assert timeline.resolved_at == 1200.0
        assert timeline.duration_seconds == 200.0

    def test_entries_time_sorted(self):
        """entries는 시간순 정렬."""
        node_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        node_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            timestamp=1200.0,
        )
        dag = _make_dag([node_close, node_open])  # 역순으로 추가
        analysis = _make_analysis(dag, node_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].timestamp <= timeline.entries[1].timestamp

    def test_causal_parent_set_for_close(self):
        """CLOSE 이벤트의 causal_parent는 OPEN 이벤트."""
        node_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
            service_name="svc",
        )
        node_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            timestamp=1200.0,
            service_name="svc",
        )
        edge = CausalEdge(
            source=node_open,
            target=node_close,
            confidence=0.9,
            evidence_type="contextual",
            time_gap_seconds=200.0,
        )
        dag = _make_dag([node_open, node_close], [edge])
        analysis = _make_analysis(dag, node_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        close_entry = next(e for e in timeline.entries if e.event_type == "circuit_breaker_closed")
        assert close_entry.causal_parent == "e1"


class TestBuildEmptyDAG:
    """빈 DAG → 최소 타임라인."""

    def test_empty_dag_returns_empty_entries(self):
        dag = EventDAG.create_empty("inc_empty")
        node_dummy = _make_node(event_id="dummy")
        analysis = _make_analysis(dag, node_dummy, summary="No events")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert len(timeline.entries) == 0
        assert len(timeline.phases) == 0
        assert timeline.duration_seconds is None


class TestBuildMultiServiceScenario:
    """DB Pool → CB × 3 → Emergency → Recovery 시나리오."""

    def test_full_scenario_phases(self):
        """다중 서비스 인시던트 → escalation, mitigation, recovery 3 phases.

        R1 상태 머신: 첫 이벤트가 critical이면 즉시 ESCALATION 전이,
        DETECTION phase는 생성되지 않음.
        """
        # 1) Root cause
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="db-pool-service",
            timestamp=1000.0,
        )
        # 2) Cascading failures
        cb_payment = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1003.0,
        )
        throttle = _make_node(
            event_id="e3",
            event_type="throttle_limit_changed",
            service_name="payment-service",
            timestamp=1005.0,
        )
        emergency = _make_node(
            event_id="e4",
            event_type="emergency_level_changed",
            service_name="system",
            timestamp=1020.0,
            data={"level": 2},
        )
        # 3) Recovery
        cb_close = _make_node(
            event_id="e5",
            event_type="circuit_breaker_closed",
            service_name="payment-service",
            timestamp=1200.0,
        )
        em_deactivated = _make_node(
            event_id="e6",
            event_type="emergency_deactivated",
            service_name="system",
            timestamp=1222.0,
        )

        edges = [
            CausalEdge(source=root, target=cb_payment, confidence=0.85, evidence_type="dependency", time_gap_seconds=3.0),
            CausalEdge(source=cb_payment, target=throttle, confidence=0.9, evidence_type="contextual", time_gap_seconds=2.0),
            CausalEdge(source=root, target=emergency, confidence=0.8, evidence_type="dependency", time_gap_seconds=20.0),
        ]

        all_nodes = [root, cb_payment, throttle, emergency, cb_close, em_deactivated]
        dag = _make_dag(all_nodes, edges)
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        # R1 상태 머신: 첫 이벤트(critical) → 바로 ESCALATION → MITIGATION → RECOVERY
        phase_names = [p.name for p in timeline.phases]
        assert "escalation" in phase_names
        assert "mitigation" in phase_names
        assert "recovery" in phase_names

    def test_full_scenario_affected_services(self):
        """다중 서비스 → affected_services에 모든 서비스 포함."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="db-pool-service",
            timestamp=1000.0,
        )
        cb_payment = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1003.0,
        )
        cb_close = _make_node(
            event_id="e3",
            event_type="circuit_breaker_closed",
            service_name="payment-service",
            timestamp=1200.0,
        )

        dag = _make_dag([root, cb_payment, cb_close])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert "db-pool-service" in timeline.affected_services
        assert "payment-service" in timeline.affected_services

    def test_full_scenario_root_cause_marked(self):
        """근본 원인 노드의 is_root_cause=True."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="db-pool-service",
            timestamp=1000.0,
        )
        cb2 = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1003.0,
        )
        dag = _make_dag([root, cb2])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        root_entries = [e for e in timeline.entries if e.is_root_cause]
        assert len(root_entries) == 1
        assert root_entries[0].service_name == "db-pool-service"

    def test_full_scenario_mttd_mttr(self):
        """MTTD와 MTTR 정확 산출."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="db-pool-service",
            timestamp=1000.0,
        )
        cb_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            service_name="db-pool-service",
            timestamp=1222.0,
        )

        dag = _make_dag([root, cb_close])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        # MTTD: 첫 이벤트(e1) ~ 첫 critical(e1) = 0
        assert timeline.get_mttd() == 0.0
        # MTTR: started(1000) ~ resolved(1222) = 222
        assert timeline.get_mttr() == 222.0


# =============================================================================
# Behavior 테스트: Phase 분류 로직
# =============================================================================


class TestPhaseClassification:
    """_classify_phases() 동작 검증."""

    def test_detection_phase_duration(self):
        """Detection phase = 첫 이벤트 ~ 첫 critical."""
        root = _make_node(
            event_id="e1",
            event_type="config_updated",
            service_name="svc",
            timestamp=1000.0,
        )
        critical = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1003.0,
        )
        dag = _make_dag([root, critical])
        # root가 primary_cause여도 severity mapping에 따라 결정
        analysis = _make_analysis(dag, root, summary="config change")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        detection = next((p for p in timeline.phases if p.name == "detection"), None)
        assert detection is not None
        assert detection.duration_seconds == 3.0

    def test_no_recovery_events_no_recovery_phase(self):
        """recovery 이벤트 없으면 recovery phase 없음."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        dag = _make_dag([root])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        recovery_phases = [p for p in timeline.phases if p.name == "recovery"]
        assert len(recovery_phases) == 0

    def test_only_info_events_stays_in_detection_phase(self):
        """R1: critical 이벤트 없으면 모든 이벤트가 detection phase에 유지."""
        n1 = _make_node(
            event_id="e1",
            event_type="config_updated",
            service_name="svc",
            timestamp=1000.0,
        )
        n2 = _make_node(
            event_id="e2",
            event_type="saga_started",
            service_name="svc",
            timestamp=1010.0,
        )
        dag = _make_dag([n1, n2])
        analysis = _make_analysis(dag, n1, summary="info only")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        # R1: 상태 머신에서 전이가 없으면 DETECTION에 머무름
        detection_phases = [p for p in timeline.phases if p.name == "detection"]
        assert len(detection_phases) == 1
        assert all(e.phase_name == "detection" for e in timeline.entries)


# =============================================================================
# Behavior 테스트: 설명 포맷팅
# =============================================================================


class TestDescriptionFormatting:
    """_format_description() 동작 검증."""

    def test_root_cause_prefix(self):
        """근본 원인에는 [ROOT CAUSE] 접두사."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert "[ROOT CAUSE]" in timeline.entries[0].description

    def test_causal_propagation_suffix(self):
        """인과관계 전파 시 '← xxx에서 전파' 접미사."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="db-pool",
            timestamp=1000.0,
        )
        child = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="payment",
            timestamp=1003.0,
        )
        edge = CausalEdge(
            source=root,
            target=child,
            confidence=0.85,
            evidence_type="dependency",
            time_gap_seconds=3.0,
        )
        dag = _make_dag([root, child], [edge])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        child_entry = next(e for e in timeline.entries if e.service_name == "payment")
        assert "db-pool에서 전파" in child_entry.description

    def test_emergency_level_in_description(self):
        """emergency_level_changed에 level 값 포함."""
        node = _make_node(
            event_id="e1",
            event_type="emergency_level_changed",
            service_name="system",
            timestamp=1000.0,
            data={"level": 2},
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node, summary="emergency")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert "2" in timeline.entries[0].description

    def test_description_truncated_at_500(self):
        """설명이 500자 초과 시 잘림."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="a" * 600,  # 매우 긴 서비스명
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert len(timeline.entries[0].description) <= DESCRIPTION_MAX_LENGTH

    def test_unknown_event_type_fallback_description(self):
        """R5: DESCRIPTION_MAP에 없는 이벤트는 _humanize_event_type 적용."""
        node = _make_node(
            event_id="e1",
            event_type="custom_unknown_event",
            service_name="my-svc",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        desc = timeline.entries[0].description
        # R5: _humanize_event_type → 'Custom Unknown Event'
        assert "Custom Unknown Event" in desc
        assert "my-svc" in desc


# =============================================================================
# Behavior 테스트: to_dict() 직렬화
# =============================================================================


class TestToDict:
    """IncidentTimeline.to_dict() 검증."""

    def test_to_dict_contains_metrics(self):
        """to_dict()에 metrics 섹션 포함 (R2: status, R3: ttar 추가)."""
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        d = timeline.to_dict()

        # R2: status 필드
        assert "status" in d
        assert d["status"] in [s.value for s in TimelineStatus]

        assert "metrics" in d
        metrics = d["metrics"]
        assert "mttd_seconds" in metrics
        assert "mttr_seconds" in metrics
        assert "affected_services" in metrics
        assert "total_events" in metrics
        assert "escalation_rate" in metrics
        # R3: TTAR 메트릭
        assert "ttar_seconds" in metrics
        assert "ttar_speedup_factor" in metrics
        assert "human_baseline_seconds" in metrics
        # R1: re-escalation count
        assert "re_escalation_count" in metrics

    def test_to_dict_entries_serialized(self):
        """entries는 dict 리스트로 직렬화됨."""
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        d = timeline.to_dict()

        assert isinstance(d["entries"], list)
        assert len(d["entries"]) == 1
        assert isinstance(d["entries"][0], dict)


# =============================================================================
# Behavior 테스트: to_markdown() 출력
# =============================================================================


class TestToMarkdown:
    """IncidentTimeline.to_markdown() 검증."""

    def test_markdown_contains_header(self):
        """마크다운에 incident_id 헤더 포함."""
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node], incident_id="inc_md_test")
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "## Incident Timeline: inc_md_test" in md

    def test_markdown_contains_root_cause_summary(self):
        """마크다운에 root cause summary 포함."""
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node])
        summary = "db-pool-service CB OPEN (87%)"
        analysis = _make_analysis(dag, node, summary=summary)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert summary in md

    def test_markdown_table_format(self):
        """마크다운에 테이블 헤더 포함."""
        root = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            service_name="svc",
            timestamp=1200.0,
        )
        dag = _make_dag([root, close])
        analysis = _make_analysis(dag, root)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "| Time | Service | Event | Severity |" in md

    def test_markdown_root_cause_bold(self):
        """근본 원인 이벤트는 마크다운에서 볼드 처리."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "**[ROOT CAUSE]" in md


# =============================================================================
# Behavior 테스트: 메타데이터 추출
# =============================================================================


class TestMetadataExtraction:
    """_extract_metadata() 동작 검증."""

    def test_level_from_data(self):
        """node.data의 level 필드가 metadata에 포함."""
        node = _make_node(
            event_id="e1",
            event_type="emergency_level_changed",
            service_name="system",
            timestamp=1000.0,
            data={"level": 3, "previous_level": 0},
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        meta = timeline.entries[0].metadata
        assert meta["level"] == 3
        assert meta["previous_level"] == 0

    def test_snapshot_data_merged(self):
        """snapshot_data의 서비스별 데이터가 metadata에 포함."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="payment-service",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        snapshot = {"payment-service": {"error_rate": 0.15, "p99_ms": 2500}}

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis, snapshot_data=snapshot)

        meta = timeline.entries[0].metadata
        assert meta["snapshot"]["error_rate"] == 0.15


# =============================================================================
# Behavior 테스트: _severity_icon()
# =============================================================================


class TestSeverityIcon:
    """_severity_icon() 아이콘 매핑 검증."""

    def test_critical_icon(self):
        assert _severity_icon("critical") == "\U0001f534"  # 🔴

    def test_warning_icon(self):
        assert _severity_icon("warning") == "\U0001f7e1"  # 🟡

    def test_info_icon(self):
        assert _severity_icon("info") == "\U0001f535"  # 🔵

    def test_recovery_icon(self):
        assert _severity_icon("recovery") == "\U0001f7e2"  # 🟢

    def test_unknown_icon(self):
        assert _severity_icon("unknown") == "\u26aa"  # ⚪


# =============================================================================
# Behavior 테스트: _format_duration()
# =============================================================================


class TestFormatDuration:
    """_format_duration() 사람이 읽기 쉬운 변환."""

    def test_none_returns_na(self):
        assert IncidentTimeline._format_duration(None) == "N/A"

    def test_sub_second(self):
        result = IncidentTimeline._format_duration(0.005)
        assert "0.005초" == result

    def test_seconds_only(self):
        result = IncidentTimeline._format_duration(45.0)
        assert result == "45초"

    def test_exact_minutes(self):
        result = IncidentTimeline._format_duration(120.0)
        assert result == "2분"

    def test_minutes_and_seconds(self):
        result = IncidentTimeline._format_duration(222.0)
        assert result == "3분 42초"


# =============================================================================
# Behavior 테스트: is_resolution 판정
# =============================================================================


class TestResolutionDetection:
    """Resolution 이벤트 자동 감지."""

    def test_cb_closed_is_resolution(self):
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_closed",
            timestamp=1200.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].is_resolution is True

    def test_cb_opened_is_not_resolution(self):
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].is_resolution is False

    def test_resolution_sets_resolved_at(self):
        """마지막 recovery 이벤트 시간이 resolved_at."""
        node_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        node_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            timestamp=1200.0,
        )
        dag = _make_dag([node_open, node_close])
        analysis = _make_analysis(dag, node_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.resolved_at == 1200.0


# =============================================================================
# R1: State Machine Phase Classification 테스트
# =============================================================================


class TestStateMachinePhases:
    """R1: 상태 머신 기반 Phase 분류 검증."""

    def test_detection_to_escalation_on_critical(self):
        """첫 critical 이벤트에서 DETECTION → ESCALATION 전이."""
        info_node = _make_node(
            event_id="e1",
            event_type="config_updated",
            service_name="svc",
            timestamp=1000.0,
        )
        critical_node = _make_node(
            event_id="e2",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1003.0,
        )
        dag = _make_dag([info_node, critical_node])
        analysis = _make_analysis(dag, info_node, summary="test")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].phase_name == "detection"
        assert timeline.entries[1].phase_name == "escalation"

    def test_escalation_to_mitigation_on_recovery(self):
        """recovery 이벤트에서 ESCALATION → MITIGATION 전이."""
        cb_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        cb_half = _make_node(
            event_id="e2",
            event_type="circuit_breaker_half_opened",
            service_name="svc",
            timestamp=1100.0,
        )
        dag = _make_dag([cb_open, cb_half])
        analysis = _make_analysis(dag, cb_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.entries[0].phase_name == "escalation"
        assert timeline.entries[1].phase_name == "mitigation"

    def test_re_escalation_tagging_in_recovery(self):
        """R1: Recovery 중 critical 재발 → is_re_escalation=True, Phase 역전 없음."""
        cb_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        cb_half = _make_node(
            event_id="e2",
            event_type="circuit_breaker_half_opened",
            service_name="svc",
            timestamp=1100.0,
        )
        cb_close = _make_node(
            event_id="e3",
            event_type="circuit_breaker_closed",
            service_name="svc",
            timestamp=1200.0,
        )
        # Recovery 중 재발
        cb_open_again = _make_node(
            event_id="e4",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1300.0,
        )
        dag = _make_dag([cb_open, cb_half, cb_close, cb_open_again])
        analysis = _make_analysis(dag, cb_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        re_esc_entry = (
            next(e for e in timeline.entries if e.event_id == "e4")
            if hasattr(timeline.entries[0], "event_id")
            else timeline.entries[3]
        )
        assert re_esc_entry.is_re_escalation is True
        # Phase는 역전되지 않음 — recovery 유지
        assert re_esc_entry.phase_name == "recovery"

    def test_re_escalation_count_in_phase(self):
        """R1: Phase의 re_escalation_count가 정확히 집계됨."""
        cb_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1000.0,
        )
        cb_half = _make_node(
            event_id="e2",
            event_type="circuit_breaker_half_opened",
            service_name="svc",
            timestamp=1100.0,
        )
        cb_close = _make_node(
            event_id="e3",
            event_type="circuit_breaker_closed",
            service_name="svc",
            timestamp=1200.0,
        )
        # Recovery 중 재발 2건
        re_esc1 = _make_node(
            event_id="e4",
            event_type="circuit_breaker_opened",
            service_name="svc",
            timestamp=1300.0,
        )
        re_esc2 = _make_node(
            event_id="e5",
            event_type="emergency_activated",
            service_name="svc",
            timestamp=1400.0,
        )
        dag = _make_dag([cb_open, cb_half, cb_close, re_esc1, re_esc2])
        analysis = _make_analysis(dag, cb_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        recovery_phase = next((p for p in timeline.phases if p.name == "recovery"), None)
        assert recovery_phase is not None
        assert recovery_phase.re_escalation_count == 2

    def test_forward_only_transitions(self):
        """R1: Phase 전이는 단방향만 허용 — 역전 불가."""
        nodes = [
            _make_node(event_id="e1", event_type="config_updated", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="circuit_breaker_opened", service_name="svc", timestamp=1003.0),
            _make_node(event_id="e3", event_type="circuit_breaker_half_opened", service_name="svc", timestamp=1100.0),
            _make_node(event_id="e4", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0], summary="test")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        # Phase 순서가 단조 증가
        phase_order = {"detection": 0, "escalation": 1, "mitigation": 2, "recovery": 3, "post_recovery": 4}
        prev_order = -1
        for phase in timeline.phases:
            order = phase_order[phase.name]
            assert order > prev_order, f"Phase 역전 감지: {phase.name}"
            prev_order = order


# =============================================================================
# R2: TimelineStatus 테스트
# =============================================================================


class TestTimelineStatus:
    """R2: TimelineStatus 열거형 및 상태 결정 로직 검증."""

    def test_timeline_status_enum_values(self):
        """TimelineStatus는 4개 값."""
        assert TimelineStatus.ONGOING.value == "ongoing"
        assert TimelineStatus.RESOLVED.value == "resolved"
        assert TimelineStatus.FLAPPING.value == "flapping"
        assert TimelineStatus.CONFIRMED.value == "confirmed"

    def test_status_ongoing_when_no_recovery(self):
        """recovery 이벤트 없음 → ONGOING."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.status == TimelineStatus.ONGOING

    def test_status_resolved_after_recovery(self):
        """마지막 recovery 후 critical 없음 → RESOLVED."""
        node_open = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        node_close = _make_node(
            event_id="e2",
            event_type="circuit_breaker_closed",
            timestamp=1200.0,
        )
        dag = _make_dag([node_open, node_close])
        analysis = _make_analysis(dag, node_open)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.status == TimelineStatus.RESOLVED

    def test_status_flapping_when_critical_after_recovery(self):
        """recovery 후 critical 재발 → FLAPPING."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
            _make_node(event_id="e3", event_type="circuit_breaker_opened", service_name="svc", timestamp=1300.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.status == TimelineStatus.FLAPPING  # recovery 후 critical 재발 → FLAPPING

    def test_status_badge_rendering(self):
        """_status_badge() 아이콘 반환."""
        assert "Ongoing" in _status_badge(TimelineStatus.ONGOING)
        assert "Resolved" in _status_badge(TimelineStatus.RESOLVED)
        assert "Flapping" in _status_badge(TimelineStatus.FLAPPING)
        assert "Confirmed" in _status_badge(TimelineStatus.CONFIRMED)

    def test_status_in_markdown(self):
        """to_markdown()에 Status 배지 포함."""
        node = _make_node(event_id="e1", timestamp=1000.0)
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "**Status**:" in md

    def test_empty_dag_status_is_ongoing(self):
        """빈 DAG → status=ONGOING."""
        dag = EventDAG.create_empty("inc_empty")
        node_dummy = _make_node(event_id="dummy")
        analysis = _make_analysis(dag, node_dummy, summary="No events")

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.status == TimelineStatus.ONGOING


# =============================================================================
# R3: TTAR (Time to Automated Response) 테스트
# =============================================================================


class TestTTAR:
    """R3: TTAR 메트릭 검증."""

    def test_ttar_basic(self):
        """TTAR = 첫 critical ~ 첫 mitigation 이벤트."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="throttle_limit_changed", service_name="svc", timestamp=1005.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.get_ttar() == 5.0

    def test_ttar_none_when_no_mitigation(self):
        """mitigation 이벤트 없으면 TTAR = None."""
        node = _make_node(
            event_id="e1",
            event_type="circuit_breaker_opened",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.get_ttar() is None

    def test_ttar_none_when_no_critical(self):
        """critical 이벤트 없으면 TTAR = None."""
        node = _make_node(
            event_id="e1",
            event_type="throttle_limit_changed",
            timestamp=1000.0,
        )
        dag = _make_dag([node])
        analysis = _make_analysis(dag, node)

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        assert timeline.get_ttar() is None

    def test_speedup_factor_calculation(self):
        """R3: speedup_factor = HUMAN_AVG / TTAR."""
        # TTAR = 5초 → speedup = 900/5 = 180
        assert _calc_speedup_factor(5.0) == pytest.approx(180.0)

    def test_speedup_factor_none_when_zero_ttar(self):
        """TTAR=0이면 speedup=None (0으로 나눗셈 방지)."""
        assert _calc_speedup_factor(0.0) is None

    def test_speedup_factor_none_when_ttar_none(self):
        """TTAR=None이면 speedup=None."""
        assert _calc_speedup_factor(None) is None

    def test_ttar_in_to_dict_metrics(self):
        """to_dict() metrics에 ttar_seconds, ttar_speedup_factor 포함."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="throttle_limit_changed", service_name="svc", timestamp=1005.0),
            _make_node(event_id="e3", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        d = timeline.to_dict()

        assert d["metrics"]["ttar_seconds"] == 5.0
        assert d["metrics"]["ttar_speedup_factor"] == pytest.approx(180.0)

    def test_ttar_in_markdown_when_present(self):
        """TTAR이 있으면 to_markdown()에 TTAR 하이라이트 포함."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="throttle_limit_changed", service_name="svc", timestamp=1005.0),
            _make_node(event_id="e3", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "**TTAR**:" in md
        assert "자동 대응" in md


# =============================================================================
# R4: Payload Trimming (_sanitize_value) 테스트
# =============================================================================


class TestSanitizeValue:
    """R4: _sanitize_value() 크기 제한 검증."""

    def test_short_string_unchanged(self):
        """1024바이트 이하 문자열은 그대로 반환."""
        short = "hello"
        assert _sanitize_value(short) == "hello"

    def test_long_string_truncated(self):
        """1024바이트 초과 문자열은 잘림 + [Truncated] 태그."""
        long_str = "x" * 2000
        result = _sanitize_value(long_str)
        assert result.endswith("[Truncated]")
        assert len(result) < 2000

    def test_dict_values_sanitized(self):
        """dict 내부 문자열 값도 재귀적으로 정리."""
        data = {"key": "x" * 2000}
        result = _sanitize_value(data)
        assert result["key"].endswith("[Truncated]")

    def test_nested_dict_handling(self):
        """중첩 dict도 재귀 처리."""
        data = {"outer": {"inner": "x" * 2000}}
        result = _sanitize_value(data)
        assert result["outer"]["inner"].endswith("[Truncated]")

    def test_list_values_sanitized(self):
        """리스트 내부 값도 정리."""
        data = ["x" * 2000, "short"]
        result = _sanitize_value(data)
        assert result[0].endswith("[Truncated]")
        assert result[1] == "short"

    def test_max_depth_exceeded(self):
        """재귀 깊이 6 초과 시 '[Max depth exceeded]' 반환."""
        result = _sanitize_value("test", _depth=6)
        assert result == "[Max depth exceeded]"

    def test_primitive_types_unchanged(self):
        """int, float, bool, None은 그대로."""
        assert _sanitize_value(42) == 42
        assert _sanitize_value(3.14) == 3.14
        assert _sanitize_value(True) is True
        assert _sanitize_value(None) is None

    def test_entry_to_dict_applies_sanitization(self):
        """TimelineEntry.to_dict()에서 metadata가 _sanitize_value로 처리됨."""
        entry = TimelineEntry(
            timestamp=1000.0,
            event_type="test",
            service_name="svc",
            description="desc",
            severity="info",
            is_root_cause=False,
            is_resolution=False,
            causal_parent=None,
            metadata={"payload": "x" * 2000},
        )
        d = entry.to_dict()
        assert d["metadata"]["payload"].endswith("[Truncated]")


# =============================================================================
# R5: _humanize_event_type 및 _resolve_severity 테스트
# =============================================================================


class TestHumanizeEventType:
    """R5: _humanize_event_type() snake_case → Title Case 변환."""

    def test_basic_conversion(self):
        assert _humanize_event_type("circuit_breaker_opened") == "Circuit Breaker Opened"

    def test_single_word(self):
        assert _humanize_event_type("test") == "Test"

    def test_saga_timed_out(self):
        assert _humanize_event_type("saga_timed_out") == "Saga Timed Out"

    def test_kill_switch_activated(self):
        assert _humanize_event_type("kill_switch_activated") == "Kill Switch Activated"


class TestResolveSeverity:
    """R5: _resolve_severity() 다단계 fallback 검증."""

    def test_exact_match(self):
        """1단계: 정확 매칭."""
        severity_map = {"circuit_breaker_opened": "critical"}
        assert _resolve_severity("circuit_breaker_opened", severity_map) == "critical"

    def test_prefix_match(self):
        """2단계: prefix 매칭 — 'emergency_' → 'critical'."""
        assert _resolve_severity("emergency_new_event", {}) == "critical"

    def test_suffix_match(self):
        """3단계: suffix 매칭 — '_recovered' → 'recovery'."""
        assert _resolve_severity("custom_service_recovered", {}) == "recovery"

    def test_suffix_failed_match(self):
        """3단계: suffix 매칭 — '_failed' → 'warning'."""
        assert _resolve_severity("some_operation_failed", {}) == "warning"

    def test_fallback_to_default(self):
        """4단계: 모든 매칭 실패 → DEFAULT_SEVERITY ('info')."""
        assert _resolve_severity("totally_unknown_xyz", {}) == DEFAULT_SEVERITY

    def test_exact_match_takes_precedence(self):
        """정확 매칭이 prefix/suffix보다 우선."""
        severity_map = {"emergency_custom": "warning"}
        # prefix 매칭 시 'critical'이지만 정확 매칭이 우선
        assert _resolve_severity("emergency_custom", severity_map) == "warning"

    def test_category_defaults_prefix_emergency(self):
        """CATEGORY_SEVERITY_DEFAULTS: 'emergency_' prefix → 'critical'."""
        assert CATEGORY_SEVERITY_DEFAULTS["emergency_"] == "critical"

    def test_category_defaults_suffix_recovered(self):
        """CATEGORY_SEVERITY_DEFAULTS: '_recovered' suffix → 'recovery'."""
        assert CATEGORY_SEVERITY_DEFAULTS["_recovered"] == "recovery"

    def test_category_defaults_kill_switch(self):
        """CATEGORY_SEVERITY_DEFAULTS: 'kill_switch_' prefix → 'critical'."""
        assert CATEGORY_SEVERITY_DEFAULTS["kill_switch_"] == "critical"


# =============================================================================
# R1+R2: Flapping 감지 통합 테스트
# =============================================================================


class TestFlappingDetection:
    """R1 재에스컬레이션 + R2 상태 결정 통합 검증."""

    def test_flapping_scenario_re_escalation_and_status(self):
        """Recovery 중 critical 재발 → re_escalation 태그 + resolved_at=None + FLAPPING."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="circuit_breaker_half_opened", service_name="svc", timestamp=1100.0),
            _make_node(event_id="e3", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
            _make_node(event_id="e4", event_type="circuit_breaker_opened", service_name="svc", timestamp=1300.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)

        # resolved_at는 None (마지막 recovery 후 critical 재발)
        assert timeline.resolved_at is None
        # 상태는 FLAPPING (recovery 이벤트 존재하나 이후 critical 재발)
        assert timeline.status == TimelineStatus.FLAPPING
        # 재에스컬레이션 이벤트 존재
        re_esc_entries = [e for e in timeline.entries if e.is_re_escalation]
        assert len(re_esc_entries) >= 1

    def test_markdown_flapping_warning(self):
        """Flapping 시 마크다운에 경고 표시."""
        nodes = [
            _make_node(event_id="e1", event_type="circuit_breaker_opened", service_name="svc", timestamp=1000.0),
            _make_node(event_id="e2", event_type="circuit_breaker_half_opened", service_name="svc", timestamp=1100.0),
            _make_node(event_id="e3", event_type="circuit_breaker_closed", service_name="svc", timestamp=1200.0),
            _make_node(event_id="e4", event_type="circuit_breaker_opened", service_name="svc", timestamp=1300.0),
        ]
        dag = _make_dag(nodes)
        analysis = _make_analysis(dag, nodes[0])

        builder = IncidentTimelineBuilder()
        timeline = builder.build(dag, analysis)
        md = timeline.to_markdown()

        assert "Flapping 감지" in md
        assert "재에스컬레이션" in md
