"""
CircuitMeshService 단위 테스트.

테스트 대상: services/circuit_mesh/service.py — CircuitMeshService
검증 기법: 싱글톤 라이프사이클, 상태 전이, 의존성 상호작용, 관리 API 위임
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.circuit_mesh.service import CircuitMeshService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후 싱글톤 리셋."""
    CircuitMeshService._reset()
    yield
    CircuitMeshService._reset()


@pytest.fixture
def mock_settings():
    """Mock CircuitMeshSettings."""
    settings = MagicMock()
    settings.enabled = True
    return settings


@pytest.fixture
def mock_coordinator():
    """Mock MeshCoordinator."""
    coordinator = MagicMock()
    coordinator.override_store = MagicMock()
    coordinator.override_store.get_all.return_value = {}
    coordinator.get_mesh_snapshot.return_value = {"recovery_queue": []}
    coordinator.get_recovery_order.return_value = []
    return coordinator


@pytest.fixture
def active_service(mock_settings, mock_coordinator):
    """활성 상태의 CircuitMeshService."""
    with patch(
        "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
        return_value=mock_settings,
    ):
        svc = CircuitMeshService()

    svc._coordinator = mock_coordinator
    svc._active = True
    return svc


# =============================================================================
# 계약 검증 (Contract)
# =============================================================================


class TestCircuitMeshServiceContract:
    """CircuitMeshService 싱글톤 계약 검증."""

    def test_singleton_returns_same_instance(self, mock_settings):
        """__new__는 동일 인스턴스를 반환한다."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            first = CircuitMeshService()
            second = CircuitMeshService()
        assert first is second

    def test_reset_clears_singleton(self, mock_settings):
        """_reset() 후 새 인스턴스가 생성된다."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            first = CircuitMeshService()
            CircuitMeshService._reset()
            second = CircuitMeshService()
        assert first is not second


# =============================================================================
# 동작 검증 (Behavior) — 라이프사이클
# =============================================================================


class TestCircuitMeshServiceLifecycleBehavior:
    """CircuitMeshService start/stop 라이프사이클 동작 검증."""

    def test_start_disabled_does_not_activate(self):
        """enabled=False면 start()가 활성화하지 않는다."""
        disabled_settings = MagicMock()
        disabled_settings.enabled = False
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=disabled_settings,
        ):
            svc = CircuitMeshService()
            svc.start()
        assert svc._active is False

    def test_start_already_active_is_noop(self, active_service):
        """이미 활성 상태면 start()는 no-op이다."""
        coordinator_before = active_service._coordinator
        active_service.start()
        assert active_service._coordinator is coordinator_before

    @patch(
        "selfhealing.services.circuit_mesh.service.unregister_mesh_handlers",
        autospec=True,
    )
    def test_stop_clears_l1_and_deactivates(self, mock_unregister, active_service):
        """stop()은 L1을 정리하고 비활성화한다."""
        active_service.stop()

        active_service._coordinator.override_store.clear_l1.assert_called_once()
        mock_unregister.assert_called_once_with(active_service._coordinator)
        assert active_service._active is False

    def test_stop_inactive_is_noop(self, mock_settings):
        """비활성 상태면 stop()은 no-op이다."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        svc.stop()
        assert svc._active is False

    @patch(
        "selfhealing.services.circuit_mesh.service.unregister_mesh_handlers",
        autospec=True,
    )
    def test_reset_stops_active_service(self, mock_unregister, active_service):
        """_reset()은 활성 서비스를 stop한 후 싱글톤 초기화한다."""
        CircuitMeshService._reset()
        assert CircuitMeshService._instance is None


# =============================================================================
# 동작 검증 (Behavior) — 조회 API 위임
# =============================================================================


class TestCircuitMeshServiceQueryApiBehavior:
    """관리 API가 coordinator에 올바르게 위임하는지 검증."""

    def test_get_mesh_state_without_coordinator_returns_empty(self, mock_settings):
        """coordinator 없으면 빈 스냅샷 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        result = svc.get_mesh_state()
        assert result.active_overrides == []
        assert result.recovery_queue == []

    def test_get_active_overrides_delegates_to_store(
        self, active_service, mock_coordinator
    ):
        """get_active_overrides는 override_store.get_all()에 위임."""
        from .conftest import make_override

        override = make_override("svc-a")
        mock_coordinator.override_store.get_all.return_value = {"svc-a": override}

        result = active_service.get_active_overrides()
        assert len(result) == 1
        assert result[0].service_name == "svc-a"

    def test_get_active_overrides_without_coordinator_returns_empty(
        self, mock_settings
    ):
        """coordinator 없으면 빈 리스트 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        assert svc.get_active_overrides() == []

    def test_get_recovery_order_delegates_to_coordinator(
        self, active_service, mock_coordinator
    ):
        """get_recovery_order는 coordinator에 위임."""
        mock_coordinator.get_recovery_order.return_value = ["svc-a", "svc-b"]
        result = active_service.get_recovery_order()
        assert result == ["svc-a", "svc-b"]

    def test_get_recovery_order_without_coordinator_returns_empty(self, mock_settings):
        """coordinator 없으면 빈 리스트 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        assert svc.get_recovery_order() == []


# =============================================================================
# 동작 검증 (Behavior) — 수동 제어 API 위임
# =============================================================================


class TestCircuitMeshServiceManualControlBehavior:
    """수동 제어 API 위임 동작 검증."""

    def test_force_release_override_delegates_to_coordinator(
        self, active_service, mock_coordinator
    ):
        """force_release_override는 coordinator.force_release에 위임."""
        mock_coordinator.force_release.return_value = True
        result = active_service.force_release_override("svc-a", "manual")
        mock_coordinator.force_release.assert_called_once_with("svc-a", "manual")
        assert result is True

    def test_force_release_override_without_coordinator_returns_false(
        self, mock_settings
    ):
        """coordinator 없으면 False 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        assert svc.force_release_override("svc-a") is False

    def test_force_release_all_delegates_to_coordinator(
        self, active_service, mock_coordinator
    ):
        """force_release_all은 coordinator.release_all_overrides에 위임."""
        mock_coordinator.release_all_overrides.return_value = 3
        result = active_service.force_release_all("emergency")
        mock_coordinator.release_all_overrides.assert_called_once_with("emergency")
        assert result == 3

    def test_force_release_all_without_coordinator_returns_zero(self, mock_settings):
        """coordinator 없으면 0 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        assert svc.force_release_all() == 0


# =============================================================================
# 동작 검증 (Behavior) — Dry-Run API
# =============================================================================


class TestCircuitMeshServiceDryRunBehavior:
    """Dry-Run 시뮬레이션 API 동작 검증."""

    def test_simulate_downstream_failure_delegates_to_coordinator(
        self, active_service, mock_coordinator
    ):
        """simulate_downstream_failure는 coordinator에 위임."""
        from .conftest import make_override

        expected = [make_override("svc-upstream")]
        mock_coordinator.simulate_downstream_open.return_value = expected
        result = active_service.simulate_downstream_failure("svc-down")
        mock_coordinator.simulate_downstream_open.assert_called_once_with("svc-down")
        assert result == expected

    def test_simulate_downstream_failure_without_coordinator_returns_empty(
        self, mock_settings
    ):
        """coordinator 없으면 빈 리스트 반환."""
        with patch(
            "selfhealing.services.circuit_mesh.service.get_circuit_mesh_settings",
            return_value=mock_settings,
        ):
            svc = CircuitMeshService()
        assert svc.simulate_downstream_failure("svc-a") == []
