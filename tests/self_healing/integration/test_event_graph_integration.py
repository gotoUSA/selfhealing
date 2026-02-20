"""
Event Graph Integration Tests — 이벤트 DAG 구축 통합 워크플로우.

EventGraphBuilder + BlastRadiusService + CoOccurrenceTracker의
조합 동작을 검증한다. 모두 인메모리 구현이므로 Docker 불필요.

테스트 시나리오:
- DB Pool 고갈 연쇄 장애 → root cause 식별
- Debounce trigger → DAG 중복 방지
- Disconnected components → 독립 장애 식별
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from selfhealing.services.blast_radius.service import BlastRadiusService
from selfhealing.services.correlation_engine.co_occurrence_tracker import (
    CoOccurrenceTracker,
)
from selfhealing.services.correlation_engine.event_graph import (
    CONFIDENCE_DEPENDENCY,
    EVIDENCE_DEPENDENCY,
    EventDAG,
)
from selfhealing.services.correlation_engine.event_graph_builder import (
    EventGraphBuilder,
)
from selfhealing.services.correlation_engine.event_graph_trigger import (
    EventGraphTrigger,
)
from selfhealing.services.event_bus.bus import (
    EventPriority,
    EventType,
    SelfHealingEvent,
)
from selfhealing.settings.correlation import (
    CorrelationSettings,
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
    """서비스 의존성이 등록된 BlastRadiusService 인스턴스."""
    brs = BlastRadiusService()
    # 전형적인 마이크로서비스 토폴로지: API Gateway → Services → DB/Cache
    brs.add_dependency("db-pool", "payment-service", "sync", "critical")
    brs.add_dependency("db-pool", "order-service", "sync", "critical")
    brs.add_dependency("db-pool", "inventory-service", "sync", "high")
    brs.add_dependency("payment-service", "api-gateway", "sync", "critical")
    brs.add_dependency("order-service", "api-gateway", "sync", "high")
    return brs


@pytest.fixture
def co_occurrence_tracker() -> CoOccurrenceTracker:
    """Co-occurrence 학습 데이터가 로드된 트래커."""
    tracker = CoOccurrenceTracker()
    tracker.update_snapshot(
        {
            ("error_budget_critical", "circuit_breaker_opened"): 0.82,
            ("circuit_breaker_opened", "emergency_activated"): 0.75,
        }
    )
    return tracker


@pytest.fixture
def builder(
    blast_radius_service: BlastRadiusService,
    co_occurrence_tracker: CoOccurrenceTracker,
) -> EventGraphBuilder:
    """통합 테스트용 EventGraphBuilder."""
    return EventGraphBuilder(
        blast_radius_service=blast_radius_service,
        co_occurrence_tracker=co_occurrence_tracker,
    )


def _make_event(
    event_type: EventType,
    source: str,
    timestamp: datetime,
    data: dict | None = None,
    correlation_id: str | None = None,
) -> SelfHealingEvent:
    """테스트용 SelfHealingEvent 생성."""
    return SelfHealingEvent(
        event_type=event_type,
        data=data or {},
        source=source,
        timestamp=timestamp,
        correlation_id=correlation_id,
    )


# =============================================================================
# 통합 시나리오 1: DB Pool 고갈 연쇄 장애
# =============================================================================


class TestDBPoolCascadeWorkflow:
    """DB Pool 고갈 → CB OPEN × 3 → Emergency 전체 워크플로우."""

    def test_full_cascade_dag_topology(self, builder: EventGraphBuilder):
        """5개 이벤트 연쇄 장애의 DAG 토폴로지가 올바른 인과관계를 반영해야 한다."""
        events = [
            _make_event(
                EventType.ERROR_BUDGET_CRITICAL,
                "db-pool",
                datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "payment-service",
                datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "order-service",
                datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "inventory-service",
                datetime(2026, 2, 20, 14, 0, 4, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.EMERGENCY_ACTIVATED,
                "api-gateway",
                datetime(2026, 2, 20, 14, 0, 6, tzinfo=timezone.utc),
            ),
        ]

        dag = builder.build_dag(events, window_seconds=300.0)

        # db-pool이 root cause
        root_services = {n.service_name for n in dag.root_nodes}
        assert "db-pool" in root_services

        # api-gateway가 최종 영향(leaf)
        leaf_services = {n.service_name for n in dag.leaf_nodes}
        assert "api-gateway" in leaf_services

        # 단일 연결 컴포넌트 (모두 연결됨)
        components = dag.get_connected_components()
        assert len(components) == 1

    def test_cascade_serialization(self, builder: EventGraphBuilder):
        """연쇄 장애 DAG의 to_dict()가 올바른 구조를 반환해야 한다."""
        events = [
            _make_event(
                EventType.ERROR_BUDGET_CRITICAL,
                "db-pool",
                datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "payment-service",
                datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.EMERGENCY_ACTIVATED,
                "api-gateway",
                datetime(2026, 2, 20, 14, 0, 5, tzinfo=timezone.utc),
            ),
        ]

        dag = builder.build_dag(events, window_seconds=300.0)
        result = dag.to_dict()

        assert result["component_count"] == 1
        assert len(result["nodes"]) == 3
        assert len(result["root_causes"]) >= 1
        assert len(result["leaf_effects"]) >= 1
        # 인시던트 ID 형식 검증
        assert result["incident_id"].startswith("inc_")


# =============================================================================
# 통합 시나리오 2: Trigger + Builder + Debounce 워크플로우
# =============================================================================


class TestTriggerBuilderWorkflow:
    """EventGraphTrigger + EventGraphBuilder 통합 워크플로우."""

    def test_trigger_filters_critical_then_builds_dag(
        self,
        builder: EventGraphBuilder,
    ):
        """Critical 이벤트만 트리거하고 DAG를 구축하는 전체 흐름."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)

        # Critical 이벤트 수신
        event_type = EventType.CIRCUIT_BREAKER_OPENED.value
        assert trigger.is_trigger_event(event_type, {})
        assert trigger.should_build("default")

        # DAG 구축
        events = [
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "payment-service",
                datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.EMERGENCY_ACTIVATED,
                "api-gateway",
                datetime(2026, 2, 20, 14, 0, 3, tzinfo=timezone.utc),
            ),
        ]
        dag = builder.build_dag(events, window_seconds=300.0)
        assert len(dag.nodes) == 2

    def test_debounce_prevents_duplicate_dag(self, builder: EventGraphBuilder):
        """Debounce가 동일 namespace에서 중복 DAG를 방지해야 한다."""
        trigger = EventGraphTrigger(cooldown_seconds=60.0)

        # 첫 번째 트리거 → 허용
        assert trigger.should_build("payment")
        events = [
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "payment-service",
                datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        dag1 = builder.build_dag(events, window_seconds=300.0)
        assert len(dag1.nodes) == 1

        # 즉시 두 번째 트리거 → Debounce로 거부
        assert trigger.should_build("payment") is False


# =============================================================================
# 통합 시나리오 3: Disconnected Components (독립 장애)
# =============================================================================


class TestDisconnectedComponentsWorkflow:
    """2개 독립 장애가 동시 발생하는 시나리오."""

    def test_independent_failures_detected(self):
        """결제 장애 + 이미지 장애가 독립 컴포넌트로 식별되어야 한다.

        두 장애 그룹을 시간적으로 충분히 분리(100초 간격)하고
        window_seconds를 10초로 설정하여, 그룹 간 temporal 엣지가
        형성되지 않도록 한다. 이렇게 해야 dependency 엣지만으로
        2개 독립 컴포넌트가 올바르게 식별된다.
        """
        BlastRadiusService._instance = None
        brs = BlastRadiusService()
        # 기존 의존성 초기화 후 추가
        brs._dependencies.clear()
        # 결제 서비스 의존성
        brs.add_dependency("db-pool", "payment-service", "sync")
        # 이미지 서비스 의존성 (결제와 무관)
        brs.add_dependency("cdn", "image-service", "sync")

        tracker = CoOccurrenceTracker()
        builder = EventGraphBuilder(brs, tracker)

        events = [
            # 결제 장애 체인 (t=0초 부근)
            _make_event(
                EventType.ERROR_BUDGET_CRITICAL,
                "db-pool",
                datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.CIRCUIT_BREAKER_OPENED,
                "payment-service",
                datetime(2026, 2, 20, 14, 0, 2, tzinfo=timezone.utc),
            ),
            # 이미지 장애 체인 (t=100초 부근, 결제 장애와 시간적으로 분리)
            _make_event(
                EventType.ERROR_BUDGET_WARNING,
                "cdn",
                datetime(2026, 2, 20, 14, 1, 40, tzinfo=timezone.utc),
            ),
            _make_event(
                EventType.THROTTLE_LIMIT_CHANGED,
                "image-service",
                datetime(2026, 2, 20, 14, 1, 43, tzinfo=timezone.utc),
            ),
        ]

        # window_seconds=10으로 설정하여 100초 떨어진 그룹 간 엣지를 차단
        dag = builder.build_dag(events, window_seconds=10.0)

        # 4개 노드가 모두 존재
        assert len(dag.nodes) == 4

        # 의존성 엣지가 존재해야 함 (db-pool→payment, cdn→image)
        dep_edges = [e for e in dag.edges if e.evidence_type == EVIDENCE_DEPENDENCY]
        assert len(dep_edges) == 2

        # 2개 독립 컴포넌트로 식별
        components = dag.get_connected_components()
        assert len(components) == 2

        # to_dict()에서 component_count 확인
        result = dag.to_dict()
        assert result["component_count"] == 2
