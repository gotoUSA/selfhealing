"""
DI fallback pattern tests for services (commit 0b59f932).

Tests for the unified fallback pattern in:
- CircuitBreakerService.repository
- DLQServiceBase.repository
- ReplayService.repository

Test Categories:
    A. Behavior: Normal path (ProviderRegistry), ALLOW fallback, FAIL_FAST raises
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.settings.root import FallbackPolicy

# =============================================================================
# CircuitBreakerService DI Fallback
# =============================================================================


class TestCircuitBreakerServiceDIFallbackBehavior:
    """Verify CircuitBreakerService.repository fallback pattern."""

    def _make_service(self):
        """Create a fresh CircuitBreakerService with no cached repository."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService

        service = CircuitBreakerService.__new__(CircuitBreakerService)
        service._repository = None
        service._config = None
        service._event_bus = None
        service._sync_callbacks = []
        return service

    def test_repository_uses_provider_registry_when_available(self):
        """Normal path: repository comes from ProviderRegistry."""
        service = self._make_service()
        mock_repo = MagicMock()

        with patch(
            "selfhealing.factory.ProviderRegistry.get_circuit_breaker_repo",
            return_value=mock_repo,
        ):
            repo = service.repository

        assert repo is mock_repo

    def test_repository_falls_back_to_inmemory_when_allow_policy(self):
        """ALLOW policy: falls back to InMemory when ProviderRegistry fails."""
        from selfhealing.adapters.memory import InMemoryCircuitBreakerStateRepository

        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.ALLOW

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_circuit_breaker_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.settings.get_config",
                return_value=mock_config,
            ),
        ):
            repo = service.repository

        assert isinstance(repo, InMemoryCircuitBreakerStateRepository)

    def test_repository_raises_runtime_error_when_fail_fast_policy(self):
        """FAIL_FAST policy: raises RuntimeError when ProviderRegistry fails."""
        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.FAIL_FAST

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_circuit_breaker_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.settings.get_config",
                return_value=mock_config,
            ),
            pytest.raises(RuntimeError, match="ProviderRegistry unavailable"),
        ):
            _ = service.repository

    def test_repository_caches_after_first_access(self):
        """Repository is cached after first successful access."""
        service = self._make_service()
        mock_repo = MagicMock()

        with patch(
            "selfhealing.factory.ProviderRegistry.get_circuit_breaker_repo",
            return_value=mock_repo,
        ):
            repo1 = service.repository
            repo2 = service.repository

        assert repo1 is repo2


# =============================================================================
# DLQServiceBase DI Fallback
# =============================================================================


class TestDLQServiceBaseDIFallbackBehavior:
    """Verify DLQServiceBase.repository fallback pattern."""

    def _make_service(self):
        """Create a fresh DLQServiceBase with no cached repository."""
        from selfhealing.services.dlq.base import DLQServiceBase

        service = DLQServiceBase.__new__(DLQServiceBase)
        service._repository = None
        service.config = MagicMock(enabled=True)
        return service

    def test_repository_uses_provider_registry_when_available(self):
        """Normal path: repository comes from ProviderRegistry."""
        service = self._make_service()
        mock_repo = MagicMock()

        with patch(
            "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
            return_value=mock_repo,
        ):
            repo = service.repository

        assert repo is mock_repo

    def test_repository_falls_back_to_inmemory_when_allow_policy(self):
        """ALLOW policy: falls back to InMemory when ProviderRegistry fails."""
        from selfhealing.adapters.memory import InMemoryFailedOperationRepository

        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.ALLOW

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.settings.get_config",
                return_value=mock_config,
            ),
        ):
            repo = service.repository

        assert isinstance(repo, InMemoryFailedOperationRepository)

    def test_repository_raises_runtime_error_when_fail_fast_policy(self):
        """FAIL_FAST policy: raises RuntimeError when ProviderRegistry fails."""
        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.FAIL_FAST

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.settings.get_config",
                return_value=mock_config,
            ),
            pytest.raises(RuntimeError, match="ProviderRegistry unavailable"),
        ):
            _ = service.repository


# =============================================================================
# ReplayService DI Fallback
# =============================================================================


class TestReplayServiceDIFallbackBehavior:
    """Verify ReplayService.repository fallback pattern."""

    def _make_service(self):
        """Create a fresh ReplayService with no cached repository."""
        from selfhealing.services.replay_service.service import ReplayService

        service = ReplayService.__new__(ReplayService)
        service._repository = None
        service._config = {}
        service._adaptive_replay = None
        return service

    def test_repository_uses_provider_registry_when_available(self):
        """Normal path: repository comes from ProviderRegistry."""
        service = self._make_service()
        mock_repo = MagicMock()

        with patch(
            "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
            return_value=mock_repo,
        ):
            repo = service.repository

        assert repo is mock_repo

    def test_repository_falls_back_to_inmemory_when_allow_policy(self):
        """ALLOW policy: falls back to InMemory when ProviderRegistry fails."""
        from selfhealing.adapters.memory import InMemoryFailedOperationRepository

        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.ALLOW

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.services.replay_service.service.get_config",
                return_value=mock_config,
            ),
        ):
            repo = service.repository

        assert isinstance(repo, InMemoryFailedOperationRepository)

    def test_repository_raises_runtime_error_when_fail_fast_policy(self):
        """FAIL_FAST policy: raises RuntimeError when ProviderRegistry fails."""
        service = self._make_service()
        mock_config = MagicMock()
        mock_config.fallback_policy = FallbackPolicy.FAIL_FAST

        with (
            patch(
                "selfhealing.factory.ProviderRegistry.get_failed_operation_repo",
                side_effect=ValueError("No repo"),
            ),
            patch(
                "selfhealing.services.replay_service.service.get_config",
                return_value=mock_config,
            ),
            pytest.raises(RuntimeError, match="ProviderRegistry unavailable"),
        ):
            _ = service.repository
