"""
Tests for CorrelationEngineService — 전략 교체 가능한 오케스트레이터.

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Contract: Protocol 미구현 객체 등록 시 TypeError 발생
- Behavior: 전략 교체 후 analyze() 호출, Fallback 발동,
    BatchCapable 분기, StrategyLifecycle 관리

참조 소스:
- services/correlation_engine/service.py (CorrelationEngineService)
- services/correlation_engine/interfaces.py (CorrelationStrategy, RootCauseStrategy)
- services/correlation_engine/root_cause_ranker.py (StrategyMetadata, RootCauseAnalysis)
- interfaces/ml_strategy.py (BatchCapable, StrategyLifecycle, AnomalyDetectionStrategy)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

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
    ML_PRIORITY_WATERMARKS,
    CorrelationEngineService,
)
from selfhealing.settings.correlation import CorrelationSettings

# =============================================================================
# Fixtures (이 파일 전용)
# =============================================================================


@pytest.fixture
def settings():
    """기본 CorrelationSettings."""
    return CorrelationSettings()


@pytest.fixture
def service(settings):
    """기본 CorrelationEngineService."""
    return CorrelationEngineService(settings)


@pytest.fixture
def sample_dag():
    """테스트용 간단한 EventDAG."""
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
    """테스트용 CorrelationResult."""
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


# =============================================================================
# Stub 전략 구현 (테스트 전용)
# =============================================================================


class MockCorrelationStrategy:
    """CorrelationStrategy 목 구현."""

    def __init__(self) -> None:
        self.analyze_called = False

    def analyze(
        self,
        event_pairs: list[tuple[str, str, float]],
        time_window: float,
    ) -> list:
        self.analyze_called = True
        return []

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        return 0.5


class MockRootCauseStrategy:
    """RootCauseStrategy 목 구현."""

    def __init__(self, label: str = "MockRootCause") -> None:
        self.label = label
        self.rank_called = False

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        self.rank_called = True
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
            summary=f"Mock analysis by {self.label}",
        )


class FailingRootCauseStrategy:
    """항상 예외를 발생시키는 RootCauseStrategy."""

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        raise RuntimeError("LLM API timeout")


class MockGraphBuildStrategy:
    """GraphBuildStrategy 목 구현."""

    def build_dag(self, events: list, window_seconds: float) -> EventDAG:
        return EventDAG.create_empty("mock-incident")


class MockLifecycleStrategy:
    """StrategyLifecycle + RootCauseStrategy 결합 목 구현."""

    def __init__(self) -> None:
        self._ready = False
        self._initialized = False
        self._warmed_up = False
        self._torn_down = False

    def initialize(self) -> None:
        self._initialized = True

    def warmup(self) -> None:
        self._warmed_up = True
        self._ready = True

    def is_ready(self) -> bool:
        return self._ready

    def teardown(self) -> None:
        self._ready = False
        self._torn_down = True

    def rank_causes(
        self,
        dag: EventDAG,
        co_occurrence_data: list[CorrelationResult],
    ) -> RootCauseAnalysis:
        primary = RootCauseCandidate(
            event_node=list(dag.nodes.values())[0],
            score=0.95,
            rank=1,
            evidence=["Lifecycle strategy evidence"],
            contributing_factors={},
            affected_services=[],
            cascade_depth=0,
        )
        return RootCauseAnalysis(
            incident_id=dag.incident_id,
            analyzed_at=time.time(),
            dag=dag,
            candidates=[primary],
            primary_cause=primary,
            confidence=0.9,
            summary="Lifecycle analysis",
        )


class MockBatchAnomalyDetector:
    """AnomalyDetectionStrategy + BatchCapable 목 구현."""

    def detect(self, value: float, context: dict[str, Any] | None = None) -> tuple[bool, float]:
        return (value > 100.0, abs(value))

    def update(self, value: float, context: dict[str, Any] | None = None) -> None:
        pass

    def reset(self) -> None:
        pass

    def get_feature_schema(self) -> dict[str, str] | None:
        return None

    def detect_batch(
        self,
        values: list[float],
        contexts: list[dict[str, Any]] | None = None,
    ) -> list[tuple[bool, float]]:
        return [(v > 100.0, abs(v)) for v in values]

    def update_batch(self, values: list[float]) -> None:
        pass


class MockSingleAnomalyDetector:
    """AnomalyDetectionStrategy (BatchCapable 미구현) 목 구현."""

    def detect(self, value: float, context: dict[str, Any] | None = None) -> tuple[bool, float]:
        return (value > 100.0, abs(value))

    def update(self, value: float, context: dict[str, Any] | None = None) -> None:
        pass

    def reset(self) -> None:
        pass

    def get_feature_schema(self) -> dict[str, str] | None:
        return None


# =============================================================================
# Contract Tests — Protocol 미구현 시 TypeError
# =============================================================================


class TestStrategySetterContract:
    """전략 설정 시 Protocol 검증 계약 테스트."""

    def test_set_correlation_strategy_rejects_non_protocol(self, service):
        """CorrelationStrategy 미구현 객체 등록 시 TypeError 발생."""
        with pytest.raises(TypeError, match="CorrelationStrategy"):
            service.set_correlation_strategy(object())

    def test_set_root_cause_strategy_rejects_non_protocol(self, service):
        """RootCauseStrategy 미구현 객체 등록 시 TypeError 발생."""
        with pytest.raises(TypeError, match="RootCauseStrategy"):
            service.set_root_cause_strategy(object())

    def test_set_graph_strategy_rejects_non_protocol(self, service):
        """GraphBuildStrategy 미구현 객체 등록 시 TypeError 발생."""
        with pytest.raises(TypeError, match="GraphBuildStrategy"):
            service.set_graph_strategy(object())

    def test_set_correlation_strategy_accepts_protocol(self, service):
        """CorrelationStrategy 구현 객체는 정상 등록."""
        mock_strategy = MockCorrelationStrategy()
        service.set_correlation_strategy(mock_strategy)
        assert service.correlation_strategy is mock_strategy

    def test_set_root_cause_strategy_accepts_protocol(self, service):
        """RootCauseStrategy 구현 객체는 정상 등록."""
        mock_strategy = MockRootCauseStrategy()
        service.set_root_cause_strategy(mock_strategy)
        assert service.root_cause_strategy is mock_strategy

    def test_set_graph_strategy_accepts_protocol(self, service):
        """GraphBuildStrategy 구현 객체는 정상 등록."""
        mock_strategy = MockGraphBuildStrategy()
        service.set_graph_strategy(mock_strategy)
        assert service.graph_strategy is mock_strategy


# =============================================================================
# Behavior Tests — 전략 교체 후 동작
# =============================================================================


class TestStrategyReplacementBehavior:
    """전략 교체 후 analyze() 호출 동작 검증."""

    @patch("selfhealing.resilience.bulkhead.registry.get_bulkhead_registry")
    @patch("selfhealing.settings.bulkhead.get_bulkhead_settings")
    def test_analyze_uses_replaced_strategy(
        self,
        mock_bh_settings,
        mock_bh_registry,
        service,
        sample_dag,
        sample_co_occurrence_data,
    ):
        """교체된 전략의 rank_causes()가 호출되어야 한다."""
        # Bulkhead를 우회하여 직접 실행하도록 mock 설정
        mock_bulkhead = MagicMock()
        mock_bulkhead.execute.side_effect = lambda fn, *a, **kw: fn(*a)
        mock_bh_registry.return_value.get_or_create.return_value = mock_bulkhead
        mock_bh_settings.return_value.ml_inference_max_workers = 3
        mock_bh_settings.return_value.ml_inference_timeout = 30.0

        mock_strategy = MockRootCauseStrategy("CustomML")
        service.set_root_cause_strategy(mock_strategy)

        result = service.analyze_root_cause(sample_dag, sample_co_occurrence_data)

        assert mock_strategy.rank_called
        assert "CustomML" in result.summary

    def test_set_root_cause_with_custom_fallback(self, service):
        """커스텀 fallback 전략 설정."""
        primary = MockRootCauseStrategy("Primary")
        fallback = MockRootCauseStrategy("Fallback")
        service.set_root_cause_strategy(primary, fallback=fallback)

        assert service.root_cause_strategy is primary
        assert service._root_cause_fallback is fallback


class TestFallbackBehavior:
    """Fallback 발동 시 동작 검증."""

    @patch("selfhealing.resilience.bulkhead.registry.get_bulkhead_registry")
    @patch("selfhealing.settings.bulkhead.get_bulkhead_settings")
    def test_fallback_on_primary_failure(
        self,
        mock_bh_settings,
        mock_bh_registry,
        service,
        sample_dag,
        sample_co_occurrence_data,
    ):
        """주 전략 실패 시 Fallback 전략이 실행되고 메타데이터에 기록."""
        mock_bulkhead = MagicMock()
        mock_bulkhead.execute.side_effect = RuntimeError("LLM timeout")
        mock_bh_registry.return_value.get_or_create.return_value = mock_bulkhead
        mock_bh_settings.return_value.ml_inference_max_workers = 3
        mock_bh_settings.return_value.ml_inference_timeout = 30.0

        failing = FailingRootCauseStrategy()
        fallback = MockRootCauseStrategy("FallbackRanker")
        service.set_root_cause_strategy(failing, fallback=fallback)

        result = service.analyze_root_cause(sample_dag, sample_co_occurrence_data)

        assert result.strategy_metadata is not None
        assert result.strategy_metadata.fallback_used is True
        assert "LLM API timeout" in result.strategy_metadata.fallback_reason
        assert result.strategy_metadata.primary_strategy_name == "FailingRootCauseStrategy"
        assert result.strategy_metadata.strategy_name == "MockRootCauseStrategy"

    @patch("selfhealing.resilience.bulkhead.registry.get_bulkhead_registry")
    @patch("selfhealing.settings.bulkhead.get_bulkhead_settings")
    def test_success_metadata_no_fallback(
        self,
        mock_bh_settings,
        mock_bh_registry,
        service,
        sample_dag,
        sample_co_occurrence_data,
    ):
        """주 전략 성공 시 fallback_used=False, 전략 이름 기록."""
        mock_bulkhead = MagicMock()
        mock_bulkhead.execute.side_effect = lambda fn, *a, **kw: fn(*a)
        mock_bh_registry.return_value.get_or_create.return_value = mock_bulkhead
        mock_bh_settings.return_value.ml_inference_max_workers = 3
        mock_bh_settings.return_value.ml_inference_timeout = 30.0

        mock_strategy = MockRootCauseStrategy("PrimaryML")
        service.set_root_cause_strategy(mock_strategy)

        result = service.analyze_root_cause(sample_dag, sample_co_occurrence_data)

        assert result.strategy_metadata is not None
        assert result.strategy_metadata.fallback_used is False
        assert result.strategy_metadata.strategy_name == "MockRootCauseStrategy"
        assert result.strategy_metadata.analysis_duration_ms >= 0.0


# =============================================================================
# Behavior Tests — BatchCapable 분기
# =============================================================================


class TestDetectAnomaliesBehavior:
    """detect_anomalies() BatchCapable 분기 동작 검증."""

    def test_batch_strategy_uses_detect_batch(self):
        """BatchCapable 전략은 detect_batch()가 호출된다."""
        batch_detector = MockBatchAnomalyDetector()
        values = [10.0, 200.0, 50.0]

        results = CorrelationEngineService.detect_anomalies(batch_detector, values)

        assert len(results) == len(values)
        # 200.0 > 100.0이므로 두 번째만 anomaly
        assert results[1][0] is True
        assert results[0][0] is False

    def test_non_batch_strategy_uses_single_detect(self):
        """비배치 전략은 단건 detect() 루프가 호출된다."""
        single_detector = MockSingleAnomalyDetector()
        values = [10.0, 200.0]

        results = CorrelationEngineService.detect_anomalies(single_detector, values)

        assert len(results) == len(values)
        assert results[1][0] is True

    def test_batch_with_contexts(self):
        """context 리스트가 전달되어도 정상 동작."""
        batch_detector = MockBatchAnomalyDetector()
        values = [10.0, 200.0]
        contexts = [{"svc": "a"}, {"svc": "b"}]

        results = CorrelationEngineService.detect_anomalies(batch_detector, values, contexts)

        assert len(results) == 2


# =============================================================================
# Behavior Tests — StrategyLifecycle 관리
# =============================================================================


class TestLifecycleManagementBehavior:
    """startup()/shutdown() StrategyLifecycle 관리 동작 검증."""

    def test_startup_initializes_lifecycle_strategies(self, service):
        """startup() 시 StrategyLifecycle 구현 전략의 라이프사이클 실행."""
        lifecycle_strategy = MockLifecycleStrategy()
        service.set_root_cause_strategy(lifecycle_strategy)

        service.startup()

        assert lifecycle_strategy._initialized
        assert lifecycle_strategy._warmed_up
        assert lifecycle_strategy.is_ready()

    def test_shutdown_tears_down_lifecycle_strategies(self, service):
        """shutdown() 시 StrategyLifecycle.teardown() 호출."""
        lifecycle_strategy = MockLifecycleStrategy()
        service.set_root_cause_strategy(lifecycle_strategy)
        service.startup()
        assert lifecycle_strategy.is_ready()

        service.shutdown()

        assert not lifecycle_strategy.is_ready()
        assert lifecycle_strategy._torn_down

    def test_startup_skips_non_lifecycle_strategies(self, service):
        """StrategyLifecycle 미구현 전략은 startup() 시 무시."""
        # 기본 전략(RootCauseRanker)은 StrategyLifecycle 미구현
        service.startup()  # 예외 발생하지 않음

    def test_get_ml_strategies_returns_all(self, service):
        """get_ml_strategies()는 등록된 전략 목록을 반환."""
        strategies = service.get_ml_strategies()
        # 최소 correlation + root_cause
        names = [name for name, _ in strategies]
        assert "correlation" in names
        assert "root_cause" in names

    def test_get_ml_strategies_includes_graph_when_set(self, service):
        """graph_strategy 설정 시 get_ml_strategies()에 포함."""
        service.set_graph_strategy(MockGraphBuildStrategy())
        strategies = service.get_ml_strategies()
        names = [name for name, _ in strategies]
        assert "graph_build" in names


# =============================================================================
# Contract Tests — ML_PRIORITY_WATERMARKS 상수
# =============================================================================


class TestMLPriorityWatermarksContract:
    """ML 우선순위 워터마크 계약값 검증."""

    def test_critical_watermark_is_zero(self):
        """critical 우선순위는 항상 처리 (0.0)."""
        assert ML_PRIORITY_WATERMARKS["critical"] == 0.0

    def test_standard_watermark(self):
        """standard 우선순위: 0.4."""
        assert ML_PRIORITY_WATERMARKS["standard"] == 0.4

    def test_background_watermark(self):
        """background 우선순위: 0.7."""
        assert ML_PRIORITY_WATERMARKS["background"] == 0.7

    def test_watermark_count(self):
        """3가지 우선순위 레벨."""
        assert len(ML_PRIORITY_WATERMARKS) == 3


# =============================================================================
# Behavior Tests — RootCauseAnalysis.to_dict()
# =============================================================================


class TestRootCauseAnalysisSerializationBehavior:
    """RootCauseAnalysis.to_dict() 직렬화 동작 검증."""

    def test_to_dict_without_strategy_metadata(self, sample_dag):
        """strategy_metadata=None이면 결과에 키 미포함."""
        primary = RootCauseCandidate(
            event_node=list(sample_dag.nodes.values())[0],
            score=0.9,
            rank=1,
            evidence=["test"],
            contributing_factors={},
            affected_services=[],
            cascade_depth=0,
        )
        analysis = RootCauseAnalysis(
            incident_id="INC-001",
            analyzed_at=1000.0,
            dag=sample_dag,
            candidates=[primary],
            primary_cause=primary,
            confidence=0.85,
            summary="test summary",
        )

        result = analysis.to_dict()

        assert "strategy_metadata" not in result
        assert result["incident_id"] == "INC-001"
        assert result["confidence"] == 0.85

    def test_to_dict_with_strategy_metadata(self, sample_dag):
        """strategy_metadata 설정 시 결과에 포함."""
        primary = RootCauseCandidate(
            event_node=list(sample_dag.nodes.values())[0],
            score=0.9,
            rank=1,
            evidence=["test"],
            contributing_factors={},
            affected_services=[],
            cascade_depth=0,
        )
        meta = StrategyMetadata(
            strategy_name="RootCauseRanker",
            fallback_used=True,
            fallback_reason="Timeout",
            primary_strategy_name="LLMAnalyzer",
            analysis_duration_ms=500.0,
            model_version="v1.0",
        )
        analysis = RootCauseAnalysis(
            incident_id="INC-002",
            analyzed_at=2000.0,
            dag=sample_dag,
            candidates=[primary],
            primary_cause=primary,
            confidence=0.72,
            summary="fallback summary",
            strategy_metadata=meta,
        )

        result = analysis.to_dict()

        assert "strategy_metadata" in result
        sm = result["strategy_metadata"]
        assert sm["strategy_name"] == "RootCauseRanker"
        assert sm["fallback_used"] is True
        assert sm["fallback_reason"] == "Timeout"
        assert sm["primary_strategy_name"] == "LLMAnalyzer"
        assert sm["analysis_duration_ms"] == 500.0
        assert sm["model_version"] == "v1.0"
