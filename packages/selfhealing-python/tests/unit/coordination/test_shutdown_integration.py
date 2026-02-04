"""
Leader Elector Graceful Shutdown 통합 테스트.
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.coordination.shutdown_integration import (
    register_for_graceful_shutdown,
    unregister_from_graceful_shutdown,
    _shutdown_all_electors,
    _registered_electors,
    integrate_with_shutdown_coordinator,
)


@pytest.fixture(autouse=True)
def cleanup():
    """각 테스트 전후 정리."""
    _registered_electors.clear()
    yield
    _registered_electors.clear()


class TestRegisterForGracefulShutdown:
    """register_for_graceful_shutdown 테스트."""

    def test_register_elector(self):
        """Elector 등록 테스트."""
        mock_elector = MagicMock()
        mock_elector.resource_name = "test-resource"

        register_for_graceful_shutdown(mock_elector)

        assert mock_elector in _registered_electors

    def test_register_multiple_electors(self):
        """여러 Elector 등록 테스트."""
        mock_elector1 = MagicMock()
        mock_elector1.resource_name = "resource-1"
        mock_elector2 = MagicMock()
        mock_elector2.resource_name = "resource-2"

        register_for_graceful_shutdown(mock_elector1)
        register_for_graceful_shutdown(mock_elector2)

        assert len(_registered_electors) == 2

    def test_no_duplicate_registration(self):
        """중복 등록 방지 테스트."""
        mock_elector = MagicMock()
        mock_elector.resource_name = "test-resource"

        register_for_graceful_shutdown(mock_elector)
        register_for_graceful_shutdown(mock_elector)

        assert len(_registered_electors) == 1


class TestUnregisterFromGracefulShutdown:
    """unregister_from_graceful_shutdown 테스트."""

    def test_unregister_elector(self):
        """Elector 등록 해제 테스트."""
        mock_elector = MagicMock()
        mock_elector.resource_name = "test-resource"

        register_for_graceful_shutdown(mock_elector)
        assert mock_elector in _registered_electors

        unregister_from_graceful_shutdown(mock_elector)
        assert mock_elector not in _registered_electors

    def test_unregister_not_registered(self):
        """등록되지 않은 Elector 해제 (오류 없음)."""
        mock_elector = MagicMock()
        mock_elector.resource_name = "test-resource"

        # 등록되지 않았지만 오류 발생 안 함
        unregister_from_graceful_shutdown(mock_elector)


class TestShutdownAllElectors:
    """_shutdown_all_electors 테스트."""

    def test_stops_all_electors(self):
        """모든 등록된 Elector 중지 테스트."""
        mock_elector1 = MagicMock()
        mock_elector1.resource_name = "resource-1"
        mock_elector2 = MagicMock()
        mock_elector2.resource_name = "resource-2"

        _registered_electors.append(mock_elector1)
        _registered_electors.append(mock_elector2)

        _shutdown_all_electors()

        mock_elector1.stop.assert_called_once()
        mock_elector2.stop.assert_called_once()
        assert len(_registered_electors) == 0

    def test_handles_stop_exception(self):
        """stop() 예외 처리 테스트."""
        mock_elector = MagicMock()
        mock_elector.resource_name = "test-resource"
        mock_elector.stop.side_effect = Exception("Stop failed")

        _registered_electors.append(mock_elector)

        # 예외가 발생해도 계속 진행
        _shutdown_all_electors()

        assert len(_registered_electors) == 0


class TestIntegrateWithShutdownCoordinator:
    """integrate_with_shutdown_coordinator 테스트."""

    def test_returns_handler_when_available(self):
        """ShutdownCoordinator가 있을 때 핸들러 반환."""
        # 이 테스트는 실제 shutdown_coordinator 모듈이 있을 때만 의미 있음
        result = integrate_with_shutdown_coordinator()
        # None이거나 핸들러 객체여야 함
        assert result is None or hasattr(result, "on_shutdown_start")
