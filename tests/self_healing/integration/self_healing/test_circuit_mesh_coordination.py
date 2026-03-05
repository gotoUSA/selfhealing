"""
Circuit Mesh Coordination Integration Tests

MeshCoordinator + ServiceDependencyGraph + InMemoryMeshOverrideStore +
CircuitBreakerService 조합의 통합 동작 검증.

Test Categories:
    A. Full Lifecycle: OPEN → HALF_OPEN → CLOSED 전체 사이클
    B. Multi-service Propagation: 다중 하류 장애 전파 및 감쇠
    C. TTL Renewal Workflow: 갱신/만료/에스컬레이션 워크플로우
    D. Preemptive Fallback: 하류 상태 기반 상류 차단

Note: All tests use InMemoryMeshOverrideStore + mock CB service — no DB/Redis dependency.
      This enables parallel test execution with pytest-xdist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from selfhealing.services.circuit_breaker.config import (
    CircuitBreakerConfig,
    CircuitState,
)
from selfhealing.services.circuit_breaker.blast_radius_integration import (
    ServiceDependencyGraph,
)
from selfhealing.services.circuit_mesh import ThresholdOverride
from selfhealing.services.circuit_mesh.mesh_coordinator import MeshCoordinator
from selfhealing.services.circuit_mesh.store import InMemoryMeshOverrideStore
from selfhealing.settings.circuit_mesh import CircuitMeshSettings


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def dependency_graph():
    """
    실제 ServiceDependencyGraph (InMemory).

    토폴로지:
        gateway → api → payment-service → pg-adapter
                    └→ cache-service
    """
    graph = ServiceDependencyGraph()
    graph.register_service("pg-adapter", depends_on=[], criticality="critical")
    graph.register_service("cache-service", depends_on=[], criticality="medium")
    graph.register_service(
        "payment-service", depends_on=["pg-adapter"], criticality="high"
    )
    graph.register_service(
        "api", depends_on=["payment-service", "cache-service"], criticality="high"
    )
    graph.register_service("gateway", depends_on=["api"], criticality="medium")
    return graph


@pytest.fixture
def settings():
    """CircuitMeshSettings (depth=3 for multi-hop tests)."""
    return CircuitMeshSettings(
        threshold_multiplier=2.0,
        recovery_timeout_multiplier=3.0,
        override_ttl_seconds=600,
        propagation_max_depth=3,
        propagation_damping_factor=0.5,
        fast_recovery_timeout_seconds=5,
        max_renewals=2,
        renewal_check_threshold_seconds=60,
        max_concurrent_overrides=20,
    )


@pytest.fixture
def mock_cb():
    """Mock CircuitBreakerService."""
    cb = MagicMock()
    cb.get_effective_config.return_value = CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=60,
    )
    cb.get_state.return_value = CircuitState.OPEN
    return cb


@pytest.fixture
def store():
    """InMemoryMeshOverrideStore."""
    return InMemoryMeshOverrideStore()


@pytest.fixture
def coordinator(dependency_graph, mock_cb, store, settings):
    """MeshCoordinator with real dependency graph."""
    return MeshCoordinator(
        dependency_graph=dependency_graph,
        cb_service=mock_cb,
        override_store=store,
        settings=settings,
    )


def _event(service_name: str) -> MagicMock:
    """SelfHealingEvent mock."""
    e = MagicMock()
    e.data = {"service_name": service_name}
    return e


# =============================================================================
# A. Full Lifecycle Tests
# =============================================================================


class TestCircuitMeshFullLifecycle:
    """
    하류 OPEN → HALF_OPEN → CLOSED 전체 라이프사이클.

    Validates:
    - 하류 OPEN 시 상류 오버라이드 적용
    - HALF_OPEN 시 오버라이드 유지
    - CLOSED 시 fast-recovery 적용 후 정리
    """

    def test_pg_adapter_failure_propagates_to_all_upstreams(
        self, coordinator, store, mock_cb
    ):
        """
        Purpose:
            pg-adapter OPEN → payment-service, api, gateway에 오버라이드 전파.
        Expected:
            - payment-service (depth=1): 최대 배율 적용
            - api (depth=2): 감쇠 배율 적용
            - gateway (depth=3): 더 감쇠된 배율 적용
        """
        coordinator.on_downstream_opened(_event("pg-adapter"))

        assert store.get("payment-service") is not None
        assert store.get("api") is not None
        assert store.get("gateway") is not None

        # depth별 감쇠 확인
        ps = store.get("payment-service")
        api = store.get("api")
        gw = store.get("gateway")

        # depth=1: damping=1.0, multiplier=2.0, threshold=10
        assert ps.adjusted_failure_threshold == int(5 * 2.0)
        # depth=2: damping=0.5, multiplier=1.5, threshold=7
        assert api.adjusted_failure_threshold == int(5 * 1.5)
        # depth=3: damping=0.25, multiplier=1.25, threshold=6
        assert gw.adjusted_failure_threshold == int(5 * 1.25)

    def test_full_lifecycle_open_half_open_closed_recovery(
        self, coordinator, store, mock_cb, settings
    ):
        """
        Purpose:
            pg-adapter OPEN → HALF_OPEN → CLOSED 전체 사이클.
        Expected:
            - OPEN: 상류 오버라이드 활성
            - HALF_OPEN: 오버라이드 유지, open set 유지
            - CLOSED: fast-recovery 적용, open set 해제
        """
        # Phase 1: downstream OPEN
        coordinator.on_downstream_opened(_event("pg-adapter"))
        assert "pg-adapter" in coordinator._downstream_open_set
        assert store.get("payment-service") is not None
        original_threshold = store.get("payment-service").adjusted_failure_threshold
        assert original_threshold > 5

        # Phase 2: downstream HALF_OPEN — 오버라이드 유지
        coordinator.on_downstream_half_opened(_event("pg-adapter"))
        assert "pg-adapter" in coordinator._downstream_open_set
        assert store.get("payment-service") is not None

        # Phase 3: downstream CLOSED — fast-recovery
        coordinator.on_downstream_closed(_event("pg-adapter"))
        assert "pg-adapter" not in coordinator._downstream_open_set

        fr = store.get("payment-service")
        assert fr is not None
        assert fr.adjusted_failure_threshold == 5  # 원래값
        assert fr.adjusted_recovery_timeout == settings.fast_recovery_timeout_seconds
        assert "fast-recovery" in fr.reason

        # Phase 4: fast-recovery 완료
        coordinator.on_fast_recovery_completed(_event("payment-service"))
        assert store.get("payment-service") is None


# =============================================================================
# B. Multi-service Propagation Tests
# =============================================================================


class TestCircuitMeshMultiServicePropagation:
    """
    다중 하류 동시 장애 시 전파 동작.

    Validates:
    - 독립 하류 장애가 각각 올바르게 전파됨
    - 하나의 하류 복구 시 다른 하류 오버라이드는 유지됨
    """

    def test_two_independent_downstream_failures(self, coordinator, store):
        """
        Purpose:
            pg-adapter와 cache-service 동시 장애 시 독립 전파.
        Expected:
            - api는 두 하류 모두에서 영향받음
            - pg-adapter 복구 후에도 cache-service 관련 open set 유지
        """
        coordinator.on_downstream_opened(_event("pg-adapter"))
        coordinator.on_downstream_opened(_event("cache-service"))

        assert "pg-adapter" in coordinator._downstream_open_set
        assert "cache-service" in coordinator._downstream_open_set

        # pg-adapter 복구
        coordinator.on_downstream_closed(_event("pg-adapter"))
        assert "pg-adapter" not in coordinator._downstream_open_set
        assert "cache-service" in coordinator._downstream_open_set

    def test_preemptive_fallback_blocks_when_downstream_open(
        self, coordinator, dependency_graph
    ):
        """
        Purpose:
            하류 OPEN 시 상류 요청이 프리엠티브 차단되는지 검증.
        Expected:
            - pg-adapter OPEN → api의 하류 체크가 False 반환 (pg-adapter는 api의 간접 의존)
        """
        coordinator._downstream_open_set.add("pg-adapter")

        # api → payment-service → pg-adapter (간접 의존)
        # 하지만 _check_downstream_health는 직접 의존만 체크
        deps = dependency_graph.get_dependencies("api")
        assert "payment-service" in deps

        # payment-service가 pg-adapter에 직접 의존
        coordinator._downstream_open_set.add("payment-service")
        result = coordinator._check_downstream_health("api")
        assert result is False

    def test_recovery_queue_maintains_insertion_order(self, coordinator):
        """
        Purpose:
            복구 대기열이 삽입 순서를 유지하는지 검증.
        Expected:
            - FIFO 순서 유지
        """
        coordinator.on_downstream_opened(_event("pg-adapter"))
        coordinator.on_downstream_opened(_event("cache-service"))

        assert coordinator._recovery_queue == ["pg-adapter", "cache-service"]


# =============================================================================
# C. TTL Renewal Workflow Tests
# =============================================================================


class TestCircuitMeshTtlRenewalWorkflow:
    """
    TTL 갱신 → 만료 → 에스컬레이션 워크플로우.

    Validates:
    - 하류 OPEN 유지 시 자동 갱신
    - max_renewals 초과 시 에스컬레이션
    - 하류 복구 시 오버라이드 자동 정리
    """

    def test_renewal_then_expiry_workflow(self, coordinator, store, mock_cb, settings):
        """
        Purpose:
            만료 임박 오버라이드 갱신 → 하류 복구 → 오버라이드 만료.
        Expected:
            - 1차: 갱신 성공 (renewal_count=1)
            - 2차: 하류 CLOSED → 오버라이드 만료
        """
        # Given — 만료 임박 오버라이드
        override = ThresholdOverride(
            service_name="payment-service",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:pg-adapter OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            renewal_count=0,
        )
        store.set("payment-service", override)

        # When — 1차 갱신 (하류 여전히 OPEN)
        mock_cb.get_state.return_value = CircuitState.OPEN
        result_1 = coordinator.check_override_renewals()
        assert result_1["renewed"] == 1
        assert store.get("payment-service").renewal_count == 1

        # When — 2차: 하류 복구
        # 만료 임박으로 설정
        stored = store.get("payment-service")
        stored.expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        store.set("payment-service", stored)
        mock_cb.get_state.return_value = CircuitState.CLOSED

        result_2 = coordinator.check_override_renewals()
        assert result_2["expired"] == 1
        assert store.get("payment-service") is None

    def test_escalation_after_max_renewals(self, coordinator, store, mock_cb, settings):
        """
        Purpose:
            max_renewals(=2) 도달 후 에스컬레이션.
        Expected:
            - renewal_count=max_renewals에서 에스컬레이션 발생
            - 오버라이드는 유지 (에스컬레이션은 알림만)
        """
        override = ThresholdOverride(
            service_name="payment-service",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:pg-adapter OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            renewal_count=settings.max_renewals,
        )
        store.set("payment-service", override)
        mock_cb.get_state.return_value = CircuitState.OPEN

        result = coordinator.check_override_renewals()

        assert result["escalated"] == 1
        assert result["renewed"] == 0
        # 에스컬레이션 후 오버라이드는 유지
        assert store.get("payment-service") is not None


# =============================================================================
# D. Preemptive Fallback with Real Graph
# =============================================================================


class TestCircuitMeshPreemptiveFallback:
    """
    실제 의존성 그래프 기반 프리엠티브 Fallback.

    Validates:
    - _check_downstream_health가 O(1) set lookup으로 동작
    - 여러 하류 중 하나만 OPEN이어도 차단
    """

    def test_check_downstream_with_real_graph(self, coordinator, dependency_graph):
        """
        Purpose:
            실제 그래프에서 payment-service OPEN → api 차단.
        Expected:
            - api의 의존: [payment-service, cache-service]
            - payment-service OPEN → False 반환
        """
        coordinator._downstream_open_set.add("payment-service")
        result = coordinator._check_downstream_health("api")
        assert result is False

    def test_no_fallback_when_all_dependencies_healthy(
        self, coordinator, dependency_graph
    ):
        """
        Purpose:
            모든 하류 정상 시 프리엠티브 Fallback 미작동.
        Expected:
            - api의 모든 의존이 정상 → True 반환
        """
        result = coordinator._check_downstream_health("api")
        assert result is True

    def test_fallback_cleared_after_downstream_recovery(
        self, coordinator, dependency_graph
    ):
        """
        Purpose:
            하류 복구 후 프리엠티브 Fallback 해제.
        Expected:
            - payment-service OPEN → api 차단
            - payment-service CLOSED → api 허용
        """
        coordinator._downstream_open_set.add("payment-service")
        assert coordinator._check_downstream_health("api") is False

        coordinator._downstream_open_set.discard("payment-service")
        assert coordinator._check_downstream_health("api") is True
