"""
MeshCoordinator 단위 테스트.

테스트 대상: services/circuit_mesh/mesh_coordinator.py
검증 기법: 상태 전이, 의존성 상호작용, 부수효과, 멱등성, 싱글톤
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from selfhealing.services.circuit_breaker.config import (
    CircuitBreakerConfig,
    CircuitState,
)
from selfhealing.services.circuit_mesh import ThresholdOverride
from selfhealing.services.circuit_mesh.mesh_coordinator import (
    MeshCoordinator,
    get_mesh_coordinator,
    reset_mesh_coordinator,
    set_mesh_coordinator,
)
from selfhealing.services.circuit_mesh.store import InMemoryMeshOverrideStore
from selfhealing.settings.circuit_mesh import CircuitMeshSettings

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def settings():
    """기본 CircuitMeshSettings."""
    return CircuitMeshSettings(
        threshold_multiplier=2.0,
        recovery_timeout_multiplier=3.0,
        override_ttl_seconds=600,
        propagation_max_depth=2,
        propagation_damping_factor=0.5,
        fast_recovery_timeout_seconds=5,
        max_renewals=3,
        renewal_check_threshold_seconds=60,
        max_concurrent_overrides=20,
    )


@pytest.fixture
def mock_graph():
    """Mock ServiceDependencyGraph."""
    graph = MagicMock()
    graph.get_dependencies.return_value = []
    graph.get_dependents_recursive.return_value = []
    graph.topological_sort_subset.return_value = []
    return graph


@pytest.fixture
def mock_cb_service():
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
    """InMemoryMeshOverrideStore 인스턴스."""
    return InMemoryMeshOverrideStore()


@pytest.fixture
def coordinator(mock_graph, mock_cb_service, store, settings):
    """MeshCoordinator 인스턴스."""
    return MeshCoordinator(
        dependency_graph=mock_graph,
        cb_service=mock_cb_service,
        override_store=store,
        settings=settings,
    )


def _make_event(service_name: str) -> MagicMock:
    """테스트용 SelfHealingEvent mock."""
    event = MagicMock()
    event.data = {"service_name": service_name}
    return event


# =============================================================================
# 동작 검증 (Behavior) — 초기화
# =============================================================================


class TestMeshCoordinatorInitializeBehavior:
    """MeshCoordinator 초기화 동작 검증."""

    def test_initialize_registers_downstream_checker(
        self, coordinator, mock_cb_service
    ):
        """initialize()는 CB 서비스에 downstream checker를 등록한다."""
        coordinator.initialize()
        mock_cb_service.register_downstream_checker.assert_called_once()

    def test_downstream_open_set_initially_empty(self, coordinator):
        """초기 _downstream_open_set은 비어있다."""
        assert len(coordinator._downstream_open_set) == 0

    def test_recovery_queue_initially_empty(self, coordinator):
        """초기 _recovery_queue는 비어있다."""
        assert coordinator._recovery_queue == []


# =============================================================================
# 동작 검증 (Behavior) — downstream checker
# =============================================================================


class TestMeshCoordinatorDownstreamCheckerBehavior:
    """_check_downstream_health 동작 검증."""

    def test_returns_true_when_no_downstream_open(self, coordinator, mock_graph):
        """하류 OPEN이 없으면 True 반환."""
        result = coordinator._check_downstream_health("svc-upstream")
        assert result is True

    def test_returns_false_when_dependency_is_open(self, coordinator, mock_graph):
        """의존 하류가 OPEN이면 False 반환."""
        coordinator._downstream_open_set.add("svc-downstream")
        mock_graph.get_dependencies.return_value = ["svc-downstream"]

        result = coordinator._check_downstream_health("svc-upstream")
        assert result is False

    def test_returns_true_when_open_service_is_not_dependency(
        self, coordinator, mock_graph
    ):
        """OPEN인 서비스가 의존 관계가 아니면 True 반환."""
        coordinator._downstream_open_set.add("svc-other")
        mock_graph.get_dependencies.return_value = ["svc-downstream"]

        result = coordinator._check_downstream_health("svc-upstream")
        assert result is True


# =============================================================================
# 동작 검증 (Behavior) — on_downstream_opened
# =============================================================================


class TestMeshCoordinatorOnDownstreamOpenedBehavior:
    """on_downstream_opened 이벤트 핸들러 동작 검증."""

    def test_adds_downstream_to_open_set(self, coordinator):
        """하류 서비스를 _downstream_open_set에 추가한다."""
        event = _make_event("svc-down")
        coordinator.on_downstream_opened(event)
        assert "svc-down" in coordinator._downstream_open_set

    def test_applies_overrides_to_affected_upstreams(
        self, coordinator, mock_graph, mock_cb_service, store
    ):
        """영향받는 상류에 오버라이드를 적용한다."""
        mock_graph.get_dependents_recursive.return_value = [("svc-up-1", 1)]
        event = _make_event("svc-down")

        coordinator.on_downstream_opened(event)

        mock_cb_service.apply_threshold_override.assert_called_once()
        assert store.get("svc-up-1") is not None

    def test_damping_factor_reduces_multiplier_at_depth_2(
        self, coordinator, mock_graph, mock_cb_service, store, settings
    ):
        """depth=2에서 감쇠 계수가 배율을 감소시킨다."""
        mock_graph.get_dependents_recursive.return_value = [
            ("svc-up-1", 1),
            ("svc-up-2", 2),
        ]
        event = _make_event("svc-down")

        coordinator.on_downstream_opened(event)

        # depth=1: damping = 0.5^0 = 1.0, effective_threshold_multiplier = 2.0
        # depth=2: damping = 0.5^1 = 0.5, effective_threshold_multiplier = 1.5
        override_depth1 = store.get("svc-up-1")
        override_depth2 = store.get("svc-up-2")

        assert override_depth1.adjusted_failure_threshold == int(5 * 2.0)
        assert override_depth2.adjusted_failure_threshold == int(5 * 1.5)

    def test_adds_downstream_to_recovery_queue(self, coordinator):
        """하류 서비스를 복구 대기열에 추가한다."""
        event = _make_event("svc-down")
        coordinator.on_downstream_opened(event)
        assert "svc-down" in coordinator._recovery_queue

    def test_does_not_duplicate_in_recovery_queue(self, coordinator):
        """이미 대기열에 있는 서비스는 중복 추가하지 않는다."""
        event = _make_event("svc-down")
        coordinator.on_downstream_opened(event)
        coordinator.on_downstream_opened(event)
        assert coordinator._recovery_queue.count("svc-down") == 1

    def test_empty_service_name_is_ignored(self, coordinator):
        """빈 service_name 이벤트는 무시한다."""
        event = MagicMock()
        event.data = {"service_name": ""}
        coordinator.on_downstream_opened(event)
        assert len(coordinator._downstream_open_set) == 0

    def test_max_concurrent_overrides_prevents_new_overrides(
        self, coordinator, mock_graph, store, settings
    ):
        """max_concurrent_overrides 초과 시 새 오버라이드 미적용."""
        # Given — 이미 max에 도달
        for i in range(settings.max_concurrent_overrides):
            store.set(
                f"svc-existing-{i}",
                ThresholdOverride(
                    service_name=f"svc-existing-{i}",
                    original_failure_threshold=5,
                    adjusted_failure_threshold=10,
                    original_recovery_timeout=60,
                    adjusted_recovery_timeout=180,
                    reason="test",
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
                ),
            )

        mock_graph.get_dependents_recursive.return_value = [("svc-new", 1)]
        event = _make_event("svc-down")

        # When
        coordinator.on_downstream_opened(event)

        # Then — 새 오버라이드는 적용되지 않음
        assert store.get("svc-new") is None


# =============================================================================
# 동작 검증 (Behavior) — on_downstream_closed
# =============================================================================


class TestMeshCoordinatorOnDownstreamClosedBehavior:
    """on_downstream_closed 이벤트 핸들러 동작 검증."""

    def test_removes_downstream_from_open_set(self, coordinator):
        """하류 서비스를 _downstream_open_set에서 제거한다."""
        coordinator._downstream_open_set.add("svc-down")
        event = _make_event("svc-down")
        coordinator.on_downstream_closed(event)
        assert "svc-down" not in coordinator._downstream_open_set

    def test_applies_fast_recovery_override_to_affected_upstreams(
        self, coordinator, mock_graph, mock_cb_service, store, settings
    ):
        """영향받는 상류에 fast-recovery 오버라이드를 적용한다."""
        # Given — 기존 오버라이드 존재
        existing = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        )
        store.set("svc-up-1", existing)
        mock_graph.get_dependents_recursive.return_value = [("svc-up-1", 1)]

        # When
        event = _make_event("svc-down")
        coordinator.on_downstream_closed(event)

        # Then
        fast_recovery = store.get("svc-up-1")
        assert fast_recovery is not None
        assert fast_recovery.adjusted_failure_threshold == 5  # 원래값으로 복원
        assert (
            fast_recovery.adjusted_recovery_timeout
            == settings.fast_recovery_timeout_seconds
        )
        assert "fast-recovery" in fast_recovery.reason

    def test_removes_downstream_from_recovery_queue(self, coordinator):
        """하류 서비스를 복구 대기열에서 제거한다."""
        coordinator._recovery_queue.append("svc-down")
        event = _make_event("svc-down")
        coordinator.on_downstream_closed(event)
        assert "svc-down" not in coordinator._recovery_queue

    def test_skips_upstream_without_existing_override(
        self, coordinator, mock_graph, mock_cb_service, store
    ):
        """기존 오버라이드가 없는 상류는 건너뛴다."""
        mock_graph.get_dependents_recursive.return_value = [("svc-up-1", 1)]
        event = _make_event("svc-down")
        coordinator.on_downstream_closed(event)

        mock_cb_service.apply_threshold_override.assert_not_called()

    def test_empty_service_name_is_ignored(self, coordinator):
        """빈 service_name 이벤트는 무시한다."""
        coordinator._downstream_open_set.add("svc-down")
        event = MagicMock()
        event.data = {"service_name": ""}
        coordinator.on_downstream_closed(event)
        assert "svc-down" in coordinator._downstream_open_set


# =============================================================================
# 동작 검증 (Behavior) — on_downstream_half_opened
# =============================================================================


class TestMeshCoordinatorOnDownstreamHalfOpenedBehavior:
    """on_downstream_half_opened 이벤트 핸들러 동작 검증."""

    def test_half_open_does_not_remove_from_open_set(self, coordinator):
        """HALF_OPEN 시 _downstream_open_set 유지."""
        coordinator._downstream_open_set.add("svc-down")
        event = _make_event("svc-down")
        coordinator.on_downstream_half_opened(event)
        assert "svc-down" in coordinator._downstream_open_set


# =============================================================================
# 동작 검증 (Behavior) — on_fast_recovery_completed
# =============================================================================


class TestMeshCoordinatorOnFastRecoveryCompletedBehavior:
    """on_fast_recovery_completed 이벤트 핸들러 동작 검증."""

    def test_removes_fast_recovery_override(self, coordinator, mock_cb_service, store):
        """fast-recovery 오버라이드를 정리한다."""
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=5,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=5,
            reason="downstream:svc-down RECOVERED → fast-recovery",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=15),
        )
        store.set("svc-up-1", override)

        event = _make_event("svc-up-1")
        coordinator.on_fast_recovery_completed(event)

        assert store.get("svc-up-1") is None
        mock_cb_service.remove_threshold_override.assert_called_once_with("svc-up-1")

    def test_ignores_non_fast_recovery_override(
        self, coordinator, mock_cb_service, store
    ):
        """fast-recovery가 아닌 오버라이드는 건너뛴다."""
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        )
        store.set("svc-up-1", override)

        event = _make_event("svc-up-1")
        coordinator.on_fast_recovery_completed(event)

        assert store.get("svc-up-1") is not None
        mock_cb_service.remove_threshold_override.assert_not_called()


# =============================================================================
# 동작 검증 (Behavior) — check_override_renewals
# =============================================================================


class TestMeshCoordinatorCheckOverrideRenewalsBehavior:
    """check_override_renewals TTL 갱신 동작 검증."""

    def test_renews_override_when_downstream_still_open(
        self, coordinator, mock_cb_service, store, settings
    ):
        """하류가 여전히 OPEN이면 TTL 갱신한다."""
        # Given — 만료 임박 오버라이드
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            renewal_count=0,
        )
        store.set("svc-up-1", override)
        mock_cb_service.get_state.return_value = CircuitState.OPEN

        # When
        result = coordinator.check_override_renewals()

        # Then
        assert result["renewed"] == 1
        renewed_override = store.get("svc-up-1")
        assert renewed_override.renewal_count == 1

    def test_renewal_creates_new_instance_not_in_place_mutation(
        self, coordinator, mock_cb_service, store, settings
    ):
        """갱신 시 원본 오버라이드를 변경하지 않고 새 인스턴스를 생성한다."""
        original_expires = datetime.now(timezone.utc) + timedelta(seconds=30)
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=original_expires,
            renewal_count=0,
        )
        store.set("svc-up-1", override)
        mock_cb_service.get_state.return_value = CircuitState.OPEN

        coordinator.check_override_renewals()

        # 원본 객체는 변경되지 않아야 한다
        assert override.renewal_count == 0
        assert override.expires_at == original_expires

    def test_expires_override_when_downstream_recovered(
        self, coordinator, mock_cb_service, store
    ):
        """하류가 복구되면 오버라이드 만료시킨다."""
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            renewal_count=0,
        )
        store.set("svc-up-1", override)
        mock_cb_service.get_state.return_value = CircuitState.CLOSED

        result = coordinator.check_override_renewals()

        assert result["expired"] == 1
        assert store.get("svc-up-1") is None
        mock_cb_service.remove_threshold_override.assert_called_once_with("svc-up-1")

    def test_escalates_when_max_renewals_exceeded(
        self, coordinator, mock_cb_service, store, settings
    ):
        """max_renewals 초과 시 에스컬레이션 발생."""
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            renewal_count=settings.max_renewals,
        )
        store.set("svc-up-1", override)
        mock_cb_service.get_state.return_value = CircuitState.OPEN

        result = coordinator.check_override_renewals()

        assert result["escalated"] == 1
        assert result["renewed"] == 0

    def test_skips_overrides_with_sufficient_ttl(
        self, coordinator, mock_cb_service, store, settings
    ):
        """TTL이 충분한 오버라이드는 건너뛴다."""
        override = ThresholdOverride(
            service_name="svc-up-1",
            original_failure_threshold=5,
            adjusted_failure_threshold=10,
            original_recovery_timeout=60,
            adjusted_recovery_timeout=180,
            reason="downstream:svc-down OPEN (depth=1)",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            renewal_count=0,
        )
        store.set("svc-up-1", override)

        result = coordinator.check_override_renewals()

        assert result["renewed"] == 0
        assert result["expired"] == 0
        assert result["escalated"] == 0

    def test_returns_correct_total_overrides_count(self, coordinator, store):
        """total_overrides가 전체 오버라이드 수를 반환."""
        store.set(
            "svc-a",
            ThresholdOverride(
                service_name="svc-a",
                original_failure_threshold=5,
                adjusted_failure_threshold=10,
                original_recovery_timeout=60,
                adjusted_recovery_timeout=180,
                reason="downstream:svc-down OPEN (depth=1)",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
            ),
        )
        result = coordinator.check_override_renewals()
        assert result["total_overrides"] == 1


# =============================================================================
# 동작 검증 (Behavior) — Query Methods
# =============================================================================


class TestMeshCoordinatorQueryBehavior:
    """MeshCoordinator 조회 메서드 동작 검증."""

    def test_get_recovery_order_returns_empty_when_no_queue(self, coordinator):
        """복구 대기열 비어있을 때 빈 리스트 반환."""
        assert coordinator.get_recovery_order() == []

    def test_get_recovery_order_delegates_to_topological_sort(
        self, coordinator, mock_graph
    ):
        """복구 순서 조회 시 topological_sort_subset에 위임한다."""
        coordinator._recovery_queue = ["svc-a", "svc-b"]
        mock_graph.topological_sort_subset.return_value = ["svc-b", "svc-a"]

        result = coordinator.get_recovery_order()

        mock_graph.topological_sort_subset.assert_called_once_with(
            ["svc-a", "svc-b"],
            direction="leaves_first",
        )
        assert result == ["svc-b", "svc-a"]

    def test_get_mesh_snapshot_returns_expected_keys(self, coordinator):
        """get_mesh_snapshot은 필수 키를 모두 포함한다."""
        snapshot = coordinator.get_mesh_snapshot()
        assert "timestamp" in snapshot
        assert "downstream_open_set" in snapshot
        assert "active_overrides" in snapshot
        assert "recovery_queue" in snapshot
        assert "recovery_order" in snapshot

    def test_get_mesh_snapshot_includes_downstream_open_set(self, coordinator):
        """스냅샷에 downstream_open_set 반영."""
        coordinator._downstream_open_set.add("svc-down")
        snapshot = coordinator.get_mesh_snapshot()
        assert "svc-down" in snapshot["downstream_open_set"]


# =============================================================================
# 동작 검증 (Behavior) — _extract_downstream_from_reason
# =============================================================================


class TestMeshCoordinatorExtractDownstreamBehavior:
    """_extract_downstream_from_reason 동작 검증."""

    def test_extracts_downstream_name_from_standard_reason(self):
        """표준 reason 문자열에서 하류 서비스명 추출."""
        result = MeshCoordinator._extract_downstream_from_reason(
            "downstream:svc-payment OPEN (depth=1)"
        )
        assert result == "svc-payment"

    def test_returns_none_for_non_downstream_reason(self):
        """downstream: 접두사가 없는 reason은 None 반환."""
        result = MeshCoordinator._extract_downstream_from_reason("manual override")
        assert result is None

    def test_extracts_from_fast_recovery_reason(self):
        """fast-recovery reason에서도 하류 서비스명 추출."""
        result = MeshCoordinator._extract_downstream_from_reason(
            "downstream:svc-payment RECOVERED → fast-recovery"
        )
        assert result == "svc-payment"


# =============================================================================
# 동작 검증 (Behavior) — 상태 전이 시퀀스
# =============================================================================


class TestMeshCoordinatorStateTransitionBehavior:
    """전체 상태 전이 시퀀스 (OPEN → HALF_OPEN → CLOSED) 검증."""

    def test_full_lifecycle_open_half_open_closed(
        self, coordinator, mock_graph, mock_cb_service, store, settings
    ):
        """하류 OPEN → HALF_OPEN → CLOSED 전체 라이프사이클."""
        mock_graph.get_dependents_recursive.return_value = [("svc-up-1", 1)]

        # Phase 1: downstream OPEN
        coordinator.on_downstream_opened(_make_event("svc-down"))
        assert "svc-down" in coordinator._downstream_open_set
        assert store.get("svc-up-1") is not None
        assert "svc-down" in coordinator._recovery_queue

        # Phase 2: downstream HALF_OPEN — 오버라이드 유지
        coordinator.on_downstream_half_opened(_make_event("svc-down"))
        assert "svc-down" in coordinator._downstream_open_set
        assert store.get("svc-up-1") is not None

        # Phase 3: downstream CLOSED — fast-recovery 적용
        coordinator.on_downstream_closed(_make_event("svc-down"))
        assert "svc-down" not in coordinator._downstream_open_set
        fast_recovery = store.get("svc-up-1")
        assert fast_recovery.adjusted_failure_threshold == 5
        assert "fast-recovery" in fast_recovery.reason
        assert "svc-down" not in coordinator._recovery_queue


# =============================================================================
# 동작 검증 (Behavior) — Singleton
# =============================================================================


class TestMeshCoordinatorSingletonBehavior:
    """MeshCoordinator 싱글톤 동작 검증."""

    def setup_method(self):
        reset_mesh_coordinator()

    def teardown_method(self):
        reset_mesh_coordinator()

    def test_get_returns_none_before_set(self):
        """set 전 get은 None 반환."""
        assert get_mesh_coordinator() is None

    def test_set_and_get_returns_same_instance(self):
        """set 후 get은 동일 인스턴스 반환."""
        coordinator = MagicMock()
        set_mesh_coordinator(coordinator)
        assert get_mesh_coordinator() is coordinator

    def test_reset_clears_singleton(self):
        """reset 후 get은 None 반환."""
        set_mesh_coordinator(MagicMock())
        reset_mesh_coordinator()
        assert get_mesh_coordinator() is None
