"""
Tests for CorrelationEngineService 오케스트레이터 기능.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: 싱글톤 보장, Deterministic ID 포맷, Status 구조
- Behavior: 초기화/종료, 분석 파이프라인, EventBus 연동, 멱등성,
           동적 설정 리로드, Postmortem/Learning 연동

참조 소스:
- services/correlation_engine/service.py (CorrelationEngineService)
- settings/correlation_engine.py (CorrelationEngineSettings)
"""

from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

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
    RootCauseAnalysis,
    RootCauseCandidate,
    StrategyMetadata,
)
from selfhealing.services.correlation_engine.service import (
    CorrelationEngineService,
)
from selfhealing.settings.correlation import CorrelationSettings
from selfhealing.settings.correlation_engine import (
    CorrelationEngineSettings,
    reset_correlation_engine_settings,
)


# =============================================================================
# Stub 전략 (테스트 전용)
# =============================================================================


class _MockRootCauseStrategy:
    """RootCauseStrategy mock — rank_causes() 구현."""

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        primary = RootCauseCandidate(
            event_node=list(dag.nodes.values())[0],
            score=0.9,
            rank=1,
            evidence=["Mock evidence"],
            contributing_factors={},
            affected_services=[],
            cascade_depth=1,
        )
        return RootCauseAnalysis(
            incident_id=dag.incident_id,
            analyzed_at=time.time(),
            dag=dag,
            candidates=[primary],
            primary_cause=primary,
            confidence=0.85,
            summary="Mock root cause analysis",
        )


# =============================================================================
# Fixtures (이 파일 전용)
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons():
    """테스트 간 싱글톤 격리."""
    CorrelationEngineService._instance = None
    reset_correlation_engine_settings()
    yield
    CorrelationEngineService._instance = None
    reset_correlation_engine_settings()


@pytest.fixture
def engine_settings():
    """오케스트레이터 기본 설정."""
    return CorrelationEngineSettings()


@pytest.fixture
def disabled_engine_settings():
    """비활성 오케스트레이터 설정."""
    return CorrelationEngineSettings(enabled=False)


@pytest.fixture
def service():
    """기본 CorrelationEngineService (초기화 전)."""
    return CorrelationEngineService(CorrelationSettings())


@pytest.fixture
def sample_dag():
    """테스트용 EventDAG — 2개 노드, 1개 엣지."""
    node_a = EventNode(
        event_id="evt-1",
        event_type="CB_OPENED",
        service_name="payment-api",
        timestamp=1000.0,
        data={},
        correlation_id="corr-1",
    )
    node_b = EventNode(
        event_id="evt-2",
        event_type="ERROR_SPIKE",
        service_name="order-api",
        timestamp=1001.0,
        data={},
        correlation_id="corr-1",
    )
    edge = CausalEdge(
        source=node_a,
        target=node_b,
        confidence=0.85,
        evidence_type="dependency",
        time_gap_seconds=1.0,
    )
    return EventDAG(
        incident_id="INC-001",
        window_start=999.0,
        window_end=1002.0,
        nodes={node_a.event_id: node_a, node_b.event_id: node_b},
        edges=[edge],
        root_nodes=[node_a],
        leaf_nodes=[node_b],
    )


@pytest.fixture
def sample_co_occurrence_data():
    """테스트용 CorrelationResult 리스트."""
    return [
        CorrelationResult(
            pair=EventPairKey("CB_OPENED", "ERROR_SPIKE"),
            correlation_score=0.8,
            direction="a_causes_b",
            evidence="Co-occurrence 8회",
            sample_count=8,
            confidence=0.7,
        ),
    ]


def _make_root_cause_analysis(dag: EventDAG) -> RootCauseAnalysis:
    """테스트 헬퍼: 간단한 RootCauseAnalysis 생성."""
    primary = RootCauseCandidate(
        event_node=list(dag.nodes.values())[0],
        score=0.9,
        rank=1,
        evidence=["test evidence"],
        contributing_factors={},
        affected_services=[],
        cascade_depth=1,
    )
    return RootCauseAnalysis(
        incident_id=dag.incident_id,
        analyzed_at=time.time(),
        dag=dag,
        candidates=[primary],
        primary_cause=primary,
        confidence=0.85,
        summary="Test root cause analysis",
    )


# =============================================================================
# Contract Tests — 싱글톤
# =============================================================================


class TestSingletonContract:
    """CorrelationEngineService 싱글톤 계약 검증."""

    def test_get_instance_returns_same_object(self):
        """get_instance()는 동일 인스턴스를 반환한다."""
        a = CorrelationEngineService.get_instance()
        b = CorrelationEngineService.get_instance()
        assert a is b

    def test_reset_instance_clears_singleton(self):
        """reset_instance() 후 새 인스턴스가 생성된다."""
        a = CorrelationEngineService.get_instance()
        CorrelationEngineService.reset_instance()
        b = CorrelationEngineService.get_instance()
        assert a is not b


# =============================================================================
# Contract Tests — Deterministic Incident ID
# =============================================================================


class TestDeterministicIncidentIdContract:
    """결정론적 인시던트 ID 생성 계약 검증."""

    def test_id_prefix_corr(self, sample_dag):
        """생성된 ID는 'corr_' 접두사를 갖는다."""
        generated_id = CorrelationEngineService._generate_deterministic_incident_id(sample_dag)
        assert generated_id.startswith("corr_")

    def test_id_hex_digest_length(self, sample_dag):
        """접두사 제외 hex digest 길이 = 12."""
        generated_id = CorrelationEngineService._generate_deterministic_incident_id(sample_dag)
        hex_part = generated_id[len("corr_") :]
        assert len(hex_part) == 12

    def test_deterministic_same_input_same_output(self, sample_dag):
        """동일 DAG에 대해 항상 동일한 ID 반환."""
        id1 = CorrelationEngineService._generate_deterministic_incident_id(sample_dag)
        id2 = CorrelationEngineService._generate_deterministic_incident_id(sample_dag)
        assert id1 == id2

    def test_deterministic_matches_sha256_calculation(self, sample_dag):
        """ID가 SHA256 알고리즘 기반 계산과 일치한다."""
        root = sample_dag.root_nodes[0]
        quantized_ts = int(root.timestamp // 60)
        content = f"{root.event_type}:{root.service_name}:{quantized_ts}"
        expected_digest = hashlib.sha256(content.encode()).hexdigest()[:12]

        generated_id = CorrelationEngineService._generate_deterministic_incident_id(sample_dag)
        assert generated_id == f"corr_{expected_digest}"

    def test_no_root_nodes_uses_window_start(self):
        """root_nodes가 비어있을 때 window_start 기반 ID 생성."""
        dag = EventDAG(
            incident_id="INC-EMPTY",
            window_start=3600.0,
            window_end=3660.0,
            nodes={},
            edges=[],
            root_nodes=[],
            leaf_nodes=[],
        )
        generated_id = CorrelationEngineService._generate_deterministic_incident_id(dag)
        quantized_ts = int(3600.0 // 60)
        content = f"no_root:{quantized_ts}"
        expected_digest = hashlib.sha256(content.encode()).hexdigest()[:12]
        assert generated_id == f"corr_{expected_digest}"


# =============================================================================
# Behavior Tests — 초기화/종료 수명주기
# =============================================================================


class TestInitializeShutdownBehavior:
    """initialize() / shutdown() 수명주기 동작 검증."""

    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._subscribe_config_updates")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._register_event_handlers")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_event_bus")
    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_blast_radius_service",
        return_value=None,
    )
    def test_initialize_success_sets_flag(
        self,
        mock_blast_radius,
        mock_event_bus,
        mock_register,
        mock_subscribe,
        service,
    ):
        """initialize() 성공 시 _initialized=True."""
        mock_event_bus.return_value = MagicMock()

        result = service.initialize()

        assert result is True
        assert service._initialized is True

    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._subscribe_config_updates")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._register_event_handlers")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_event_bus")
    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_blast_radius_service",
        return_value=None,
    )
    def test_initialize_creates_sub_modules(
        self,
        mock_blast_radius,
        mock_event_bus,
        mock_register,
        mock_subscribe,
        service,
    ):
        """initialize()는 서브 모듈(co_occurrence, graph_builder, timeline)을 생성한다."""
        mock_event_bus.return_value = MagicMock()

        service.initialize()

        assert service._co_occurrence is not None
        assert service._graph_builder is not None
        assert service._timeline_builder is not None
        assert service._root_cause_ranker is not None

    def test_initialize_disabled_returns_false(self):
        """enabled=False 설정 시 initialize()는 False 반환."""
        with patch("selfhealing.services.correlation_engine.service." "get_correlation_engine_settings") as mock_settings:
            mock_settings.return_value = CorrelationEngineSettings(enabled=False)
            svc = CorrelationEngineService(CorrelationSettings())
            result = svc.initialize()

        assert result is False
        assert svc._initialized is False

    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._subscribe_config_updates")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._register_event_handlers")
    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_event_bus")
    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._get_blast_radius_service",
        return_value=None,
    )
    def test_initialize_idempotent(
        self,
        mock_blast_radius,
        mock_event_bus,
        mock_register,
        mock_subscribe,
        service,
    ):
        """이미 초기화된 상태에서 재호출 시 True 반환, 다시 초기화하지 않음."""
        mock_event_bus.return_value = MagicMock()

        service.initialize()
        # 두 번째 호출 — 서브모듈 재생성 안 함
        co_occ_ref = service._co_occurrence
        result = service.initialize()

        assert result is True
        assert service._co_occurrence is co_occ_ref  # 같은 객체 유지

    def test_shutdown_resets_initialized_flag(self, service):
        """shutdown() 실행 시 _initialized=False."""
        service._initialized = True
        service.shutdown()
        assert service._initialized is False

    def test_shutdown_saves_state_when_enabled(self, service):
        """shutdown() 시 state_persistence_enabled=True이면 save_state() 호출."""
        mock_co_occ = MagicMock()
        service._co_occurrence = mock_co_occ
        service._initialized = True
        service._engine_settings = CorrelationEngineSettings(
            state_persistence_enabled=True,
        )

        service.shutdown()

        mock_co_occ.save_state.assert_called_once()


# =============================================================================
# Behavior Tests — 분석 루프 (LeaderScheduler)
# =============================================================================


class TestAnalysisLoopBehavior:
    """주기적 분석 루프 동작 검증."""

    @patch("selfhealing.coordination.scheduler.get_leader_scheduler")
    def test_start_analysis_loop_registers_job(self, mock_get_scheduler, service):
        """start_analysis_loop()는 LeaderScheduler에 job을 등록한다."""
        mock_scheduler = MagicMock()
        mock_get_scheduler.return_value = mock_scheduler

        service.start_analysis_loop()

        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs[1]["name"] == "correlation_periodic_analysis"
        mock_scheduler.start.assert_called_once()
        assert service._running is True

    @patch("selfhealing.coordination.factory.get_leader_elector")
    def test_periodic_analysis_aborts_on_expired_lease(self, mock_get_elector, service):
        """lease 만료 시 _run_periodic_analysis()는 분석을 중단한다."""
        mock_elector = MagicMock()
        mock_elector.is_lease_valid.return_value = False
        mock_get_elector.return_value = mock_elector
        service._co_occurrence = MagicMock()

        service._run_periodic_analysis()

        # co_occurrence.analyze_tick()이 호출되지 않음
        service._co_occurrence.analyze_tick.assert_not_called()

    @patch("selfhealing.coordination.factory.get_leader_elector")
    def test_periodic_analysis_runs_co_occurrence(self, mock_get_elector, service):
        """유효 lease 상태에서 co_occurrence.analyze_tick()이 호출된다."""
        mock_elector = MagicMock()
        mock_elector.is_lease_valid.return_value = True
        mock_get_elector.return_value = mock_elector

        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = []
        service._co_occurrence = mock_co_occ

        service._run_periodic_analysis()

        mock_co_occ.analyze_tick.assert_called_once()

    @patch("selfhealing.coordination.factory.get_leader_elector")
    def test_periodic_analysis_saves_state_every_5_ticks(self, mock_get_elector, service):
        """5틱마다 save_state()가 호출된다."""
        mock_elector = MagicMock()
        mock_elector.is_lease_valid.return_value = True
        mock_get_elector.return_value = mock_elector

        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = []
        service._co_occurrence = mock_co_occ
        service._engine_settings = CorrelationEngineSettings(
            state_persistence_enabled=True,
        )

        # 5번 실행
        for _ in range(5):
            service._run_periodic_analysis()

        mock_co_occ.save_state.assert_called_once()

    @patch("selfhealing.coordination.factory.get_leader_elector")
    def test_periodic_analysis_reports_to_learning(self, mock_get_elector, service, sample_co_occurrence_data):
        """상관관계 발견 시 _report_to_learning()이 호출된다."""
        mock_elector = MagicMock()
        mock_elector.is_lease_valid.return_value = True
        mock_get_elector.return_value = mock_elector

        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = sample_co_occurrence_data
        service._co_occurrence = mock_co_occ
        service._engine_settings = CorrelationEngineSettings(
            learning_integration_enabled=True,
            state_persistence_enabled=False,
        )

        with patch.object(service, "_report_to_learning") as mock_report:
            service._run_periodic_analysis()
            mock_report.assert_called_once_with(sample_co_occurrence_data)


# =============================================================================
# Behavior Tests — 온디맨드 분석 (analyze_incident)
# =============================================================================


class TestAnalyzeIncidentBehavior:
    """analyze_incident() 온디맨드 분석 동작 검증."""

    def test_returns_none_when_not_initialized(self, service):
        """_initialized=False 시 None 반환."""
        result = service.analyze_incident(incident_id="INC-001")
        assert result is None

    def test_returns_none_with_insufficient_events(self, service):
        """이벤트 2개 미만 시 None 반환."""
        service._initialized = True
        mock_observer = MagicMock()
        mock_observer.get_current_window.return_value = [{"event": "only_one"}]
        service._observer = mock_observer

        result = service.analyze_incident()
        assert result is None

    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._mark_analysis_complete")
    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._check_already_analyzed",
        return_value=False,
    )
    @patch("selfhealing.resilience.bulkhead.registry.get_bulkhead_registry")
    @patch("selfhealing.settings.bulkhead.get_bulkhead_settings")
    def test_returns_result_dict_on_success(
        self,
        mock_bh_settings,
        mock_bh_registry,
        mock_check,
        mock_mark,
        service,
        sample_dag,
        sample_co_occurrence_data,
    ):
        """분석 성공 시 incident_id, dag, root_cause 등을 포함하는 dict 반환."""
        # Bulkhead 우회
        mock_bulkhead = MagicMock()
        mock_bulkhead.execute.side_effect = lambda fn, *a, **kw: fn(*a)
        mock_bh_registry.return_value.get_or_create.return_value = mock_bulkhead
        mock_bh_settings.return_value.ml_inference_max_workers = 3
        mock_bh_settings.return_value.ml_inference_timeout = 30.0

        # rank_causes() 구현 전략으로 교체
        mock_strategy = _MockRootCauseStrategy()
        service.set_root_cause_strategy(mock_strategy)

        service._initialized = True
        events = [{"e": 1}, {"e": 2}, {"e": 3}]
        mock_observer = MagicMock()
        mock_observer.get_current_window.return_value = events
        service._observer = mock_observer

        mock_graph_builder = MagicMock()
        mock_graph_builder.build_dag.return_value = sample_dag
        service._graph_builder = mock_graph_builder

        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = sample_co_occurrence_data
        service._co_occurrence = mock_co_occ

        mock_timeline_builder = MagicMock()
        mock_timeline = MagicMock()
        mock_timeline_builder.build.return_value = mock_timeline
        service._timeline_builder = mock_timeline_builder

        service._engine_settings = CorrelationEngineSettings(
            postmortem_integration_enabled=False,
        )

        result = service.analyze_incident(incident_id="INC-TEST")

        assert result is not None
        assert result["incident_id"] == "INC-TEST"
        assert "dag" in result
        assert "root_cause" in result
        assert "timeline" in result

    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._check_already_analyzed",
        return_value=True,
    )
    def test_idempotency_skips_duplicate(self, mock_check, service, sample_dag):
        """이미 분석된 incident_id는 None 반환 (멱등성)."""
        service._initialized = True
        mock_observer = MagicMock()
        mock_observer.get_current_window.return_value = [{"e": 1}, {"e": 2}]
        service._observer = mock_observer

        mock_graph_builder = MagicMock()
        mock_graph_builder.build_dag.return_value = sample_dag
        service._graph_builder = mock_graph_builder

        result = service.analyze_incident(incident_id="INC-DUP")
        assert result is None

    @patch("selfhealing.services.correlation_engine.service." "CorrelationEngineService._mark_analysis_complete")
    @patch(
        "selfhealing.services.correlation_engine.service." "CorrelationEngineService._check_already_analyzed",
        return_value=False,
    )
    @patch("selfhealing.resilience.bulkhead.registry.get_bulkhead_registry")
    @patch("selfhealing.settings.bulkhead.get_bulkhead_settings")
    def test_auto_generates_deterministic_id_when_none(
        self,
        mock_bh_settings,
        mock_bh_registry,
        mock_check,
        mock_mark,
        service,
        sample_dag,
    ):
        """incident_id=None 시 Deterministic ID가 자동 생성된다."""
        # Bulkhead 우회
        mock_bulkhead = MagicMock()
        mock_bulkhead.execute.side_effect = lambda fn, *a, **kw: fn(*a)
        mock_bh_registry.return_value.get_or_create.return_value = mock_bulkhead
        mock_bh_settings.return_value.ml_inference_max_workers = 3
        mock_bh_settings.return_value.ml_inference_timeout = 30.0

        # rank_causes() 구현 전략으로 교체
        mock_strategy = _MockRootCauseStrategy()
        service.set_root_cause_strategy(mock_strategy)

        service._initialized = True
        mock_observer = MagicMock()
        mock_observer.get_current_window.return_value = [{"e": 1}, {"e": 2}]
        service._observer = mock_observer

        mock_graph_builder = MagicMock()
        mock_graph_builder.build_dag.return_value = sample_dag
        service._graph_builder = mock_graph_builder

        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = []
        service._co_occurrence = mock_co_occ

        mock_timeline = MagicMock()
        mock_timeline_builder = MagicMock()
        mock_timeline_builder.build.return_value = mock_timeline
        service._timeline_builder = mock_timeline_builder

        service._engine_settings = CorrelationEngineSettings(
            postmortem_integration_enabled=False,
        )

        result = service.analyze_incident(incident_id=None)

        assert result is not None
        assert result["incident_id"].startswith("corr_")


# =============================================================================
# Behavior Tests — EventBus 연동
# =============================================================================


class TestEventBusIntegrationBehavior:
    """EventBus 이벤트 핸들러 동작 검증."""

    def test_on_incident_resolved_calls_analyze(self, service):
        """_on_incident_resolved()는 analyze_incident()를 호출한다."""
        event = SimpleNamespace(
            data={"incident_id": "INC-123"},
        )

        with patch.object(service, "analyze_incident", return_value=None) as mock_analyze:
            service._on_incident_resolved(event)
            mock_analyze.assert_called_once_with(incident_id="INC-123")

    def test_on_incident_resolved_extracts_id_from_data(self, service):
        """event.data['incident_id']를 추출하여 전달한다."""
        event = SimpleNamespace(
            data={"incident_id": "INC-456", "extra": "info"},
        )

        with patch.object(service, "analyze_incident", return_value=None) as mock_analyze:
            service._on_incident_resolved(event)
            call_kwargs = mock_analyze.call_args[1]
            assert call_kwargs["incident_id"] == "INC-456"

    def test_on_incident_resolved_passes_none_when_no_data(self, service):
        """event.data에 incident_id가 없으면 None으로 호출."""
        event = SimpleNamespace(data={})

        with patch.object(service, "analyze_incident", return_value=None) as mock_analyze:
            service._on_incident_resolved(event)
            mock_analyze.assert_called_once_with(incident_id=None)

    def test_on_incident_resolved_handles_non_dict_data(self, service):
        """event.data가 dict가 아닐 때 incident_id=None으로 호출."""
        event = SimpleNamespace(data="not-a-dict")

        with patch.object(service, "analyze_incident", return_value=None) as mock_analyze:
            service._on_incident_resolved(event)
            mock_analyze.assert_called_once_with(incident_id=None)


# =============================================================================
# Behavior Tests — Postmortem 연동
# =============================================================================


class TestPostmortemIntegrationBehavior:
    """_inject_to_postmortem() 동작 검증."""

    @patch("selfhealing.services.postmortem.store.update_incident_fields")
    @patch("selfhealing.services.postmortem.store.get_incident_by_id")
    def test_inject_updates_existing_incident(self, mock_get_incident, mock_update_fields, service, sample_dag):
        """인시던트가 존재하면 update_incident_fields()를 호출한다."""
        mock_get_incident.return_value = {"incident_id": "INC-001"}
        root_cause = _make_root_cause_analysis(sample_dag)
        mock_timeline = MagicMock()
        mock_timeline.to_dict.return_value = {"events": []}

        service._inject_to_postmortem("INC-001", mock_timeline, root_cause)

        mock_update_fields.assert_called_once()
        call_args = mock_update_fields.call_args
        assert call_args[1]["incident_id"] == "INC-001"
        fields = call_args[1]["fields"]
        assert "correlation_timeline" in fields
        assert "root_cause_analysis" in fields

    @patch("selfhealing.services.postmortem.store.update_incident_fields")
    @patch("selfhealing.services.postmortem.store.get_incident_by_id")
    def test_inject_skips_when_incident_not_found(self, mock_get_incident, mock_update_fields, service, sample_dag):
        """인시던트가 존재하지 않으면 update를 호출하지 않는다."""
        mock_get_incident.return_value = None
        root_cause = _make_root_cause_analysis(sample_dag)
        mock_timeline = MagicMock()

        service._inject_to_postmortem("INC-MISSING", mock_timeline, root_cause)

        mock_update_fields.assert_not_called()


# =============================================================================
# Behavior Tests — Learning 연동
# =============================================================================


class TestLearningIntegrationBehavior:
    """_report_to_learning() 동작 검증."""

    def test_report_calls_learn_pattern(self, service, sample_co_occurrence_data):
        """상관관계 결과별 learn_pattern()이 호출된다."""
        mock_learning = MagicMock()
        mock_pattern_type = MagicMock()
        mock_pattern_type.ANOMALY = "anomaly"

        mock_module = MagicMock()
        mock_module.LearningService.get_instance.return_value = mock_learning
        mock_module.PatternType = mock_pattern_type

        import sys

        with patch.dict(sys.modules, {"selfhealing.services.learning": mock_module}):
            service._report_to_learning(sample_co_occurrence_data)

        assert mock_learning.learn_pattern.call_count == len(sample_co_occurrence_data)

    def test_report_includes_pair_key_in_name(self, service, sample_co_occurrence_data):
        """learn_pattern name에 pair key가 포함된다."""
        mock_learning = MagicMock()
        mock_pattern_type = MagicMock()
        mock_pattern_type.ANOMALY = "anomaly"

        mock_module = MagicMock()
        mock_module.LearningService.get_instance.return_value = mock_learning
        mock_module.PatternType = mock_pattern_type

        import sys

        with patch.dict(sys.modules, {"selfhealing.services.learning": mock_module}):
            service._report_to_learning(sample_co_occurrence_data)

        call_kwargs = mock_learning.learn_pattern.call_args[1]
        expected_pair_key = sample_co_occurrence_data[0].pair.key
        assert expected_pair_key in call_kwargs["name"]


# =============================================================================
# Behavior Tests — BlastRadius 연동
# =============================================================================


class TestBlastRadiusIntegrationBehavior:
    """_get_blast_radius_service() 동작 검증."""

    def test_returns_service_when_available(self):
        """BlastRadiusService 사용 가능 시 인스턴스 반환."""
        mock_instance = MagicMock()

        with patch("selfhealing.services.blast_radius.service.BlastRadiusService") as mock_cls:
            mock_cls.get_instance.return_value = mock_instance
            result = CorrelationEngineService._get_blast_radius_service()

        assert result is mock_instance

    def test_returns_none_when_unavailable(self):
        """BlastRadiusService.get_instance() 실패 시 None 반환."""
        with patch("selfhealing.services.blast_radius.service.BlastRadiusService") as mock_cls:
            mock_cls.get_instance.side_effect = RuntimeError("not available")
            result = CorrelationEngineService._get_blast_radius_service()

        assert result is None


# =============================================================================
# Behavior Tests — 멱등성 (IdempotencyService)
# =============================================================================


class TestIdempotencyBehavior:
    """_check_already_analyzed() / _mark_analysis_complete() 동작 검증."""

    @patch("selfhealing.services.idempotency.service.IdempotencyService")
    @patch("selfhealing.services.idempotency.models.IdempotencyKey")
    def test_check_returns_true_for_duplicate(self, mock_key_cls, mock_service_cls):
        """이미 분석된 건은 True 반환."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_duplicate = True
        mock_service.check.return_value = mock_result
        mock_service_cls.return_value = mock_service

        result = CorrelationEngineService._check_already_analyzed("INC-001")
        assert result is True

    @patch("selfhealing.services.idempotency.service.IdempotencyService")
    @patch("selfhealing.services.idempotency.models.IdempotencyKey")
    def test_check_returns_false_for_new(self, mock_key_cls, mock_service_cls):
        """새 건은 False 반환."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_duplicate = False
        mock_service.check.return_value = mock_result
        mock_service_cls.return_value = mock_service

        result = CorrelationEngineService._check_already_analyzed("INC-NEW")
        assert result is False

    def test_check_returns_false_on_exception(self):
        """IdempotencyService 장애 시 False (Fail-Open)."""
        with patch(
            "selfhealing.services.idempotency.service.IdempotencyService",
            side_effect=RuntimeError("Redis down"),
        ):
            result = CorrelationEngineService._check_already_analyzed("INC-ERR")
            assert result is False

    @patch("selfhealing.services.idempotency.service.IdempotencyService")
    @patch("selfhealing.services.idempotency.models.IdempotencyKey")
    def test_mark_complete_calls_service(self, mock_key_cls, mock_service_cls):
        """_mark_analysis_complete()는 mark_as_processed()를 호출한다."""
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service

        CorrelationEngineService._mark_analysis_complete("INC-001")

        mock_service.mark_as_processed.assert_called_once()


# =============================================================================
# Behavior Tests — 동적 설정 리로드
# =============================================================================


class TestDynamicConfigReloadBehavior:
    """_on_config_updated() 동적 설정 변경 동작 검증."""

    def test_ignores_non_correlation_config(self, service):
        """config_type != 'correlation'이면 무시한다."""
        event = SimpleNamespace(data={"config_type": "bulkhead"})
        mock_co_occ = MagicMock()
        service._co_occurrence = mock_co_occ

        service._on_config_updated(event)

        # resize가 호출되지 않음
        mock_co_occ.resize.assert_not_called()

    @patch("selfhealing.settings.correlation.reset_correlation_settings")
    @patch("selfhealing.settings.correlation_engine.reset_correlation_engine_settings")
    def test_resets_settings_caches(
        self,
        mock_reset_engine,
        mock_reset_corr,
        service,
    ):
        """설정 변경 시 양쪽 Settings 캐시가 무효화된다."""
        event = SimpleNamespace(data={"config_type": "correlation"})

        service._on_config_updated(event)

        mock_reset_corr.assert_called_once()
        mock_reset_engine.assert_called_once()

    @patch("selfhealing.settings.correlation_engine." "reset_correlation_engine_settings")
    @patch("selfhealing.settings.correlation.reset_correlation_settings")
    def test_calls_resize_on_co_occurrence(
        self,
        mock_reset_corr,
        mock_reset_engine,
        service,
    ):
        """설정 변경 시 co_occurrence.resize()가 호출된다."""
        mock_co_occ = MagicMock()
        service._co_occurrence = mock_co_occ
        event = SimpleNamespace(data={"config_type": "correlation"})

        service._on_config_updated(event)

        mock_co_occ.resize.assert_called_once()

    @patch("selfhealing.settings.correlation_engine." "reset_correlation_engine_settings")
    @patch("selfhealing.settings.correlation.reset_correlation_settings")
    def test_shutdown_on_disabled(
        self,
        mock_reset_corr,
        mock_reset_engine,
        service,
    ):
        """enabled=False로 변경 시 shutdown()이 호출된다."""
        event = SimpleNamespace(data={"config_type": "correlation"})

        with patch("selfhealing.services.correlation_engine.service." "get_correlation_engine_settings") as mock_get_engine:
            mock_get_engine.return_value = CorrelationEngineSettings(
                enabled=False,
            )
            with patch.object(service, "shutdown") as mock_shutdown:
                service._on_config_updated(event)
                mock_shutdown.assert_called_once()


# =============================================================================
# Behavior Tests — 대시보드/API
# =============================================================================


class TestStatusApiBehavior:
    """get_status() / get_top_correlations() 동작 검증."""

    def test_get_status_structure(self, service):
        """get_status()는 필수 키를 가진 dict를 반환한다."""
        status = service.get_status()

        assert "enabled" in status
        assert "initialized" in status
        assert "running" in status
        assert "settings" in status
        assert "tracked_pairs" in status

    def test_get_top_correlations_empty_when_no_co_occurrence(self, service):
        """co_occurrence 미초기화 시 빈 리스트 반환."""
        result = service.get_top_correlations()
        assert result == []

    def test_get_top_correlations_respects_limit(self, service, sample_co_occurrence_data):
        """limit 파라미터가 적용된다."""
        mock_co_occ = MagicMock()
        mock_co_occ.analyze_tick.return_value = sample_co_occurrence_data
        service._co_occurrence = mock_co_occ

        result = service.get_top_correlations(limit=1)
        assert len(result) <= 1
