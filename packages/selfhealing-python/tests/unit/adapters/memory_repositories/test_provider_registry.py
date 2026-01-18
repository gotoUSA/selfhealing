"""
ProviderRegistry 테스트.
"""

import pytest


class TestProviderRegistry:
    """Tests for ProviderRegistry with In-Memory repositories."""

    def test_registry_has_inmemory_repositories_registered(self):
        """Test that in-memory repositories are auto-registered."""
        from selfhealing.factory import ProviderRegistry

        providers = ProviderRegistry.list_providers()
        assert "memory" in providers["failed_operation_repo"]
        assert "memory" in providers["circuit_breaker_repo"]
        assert "memory" in providers["security_repo"]

    def test_registry_creates_inmemory_repositories(self):
        """Test that registry creates in-memory repositories."""
        from selfhealing.factory import ProviderRegistry
        from selfhealing.adapters.memory import (
            InMemoryFailedOperationRepository,
            InMemoryCircuitBreakerStateRepository,
            InMemorySecurityIncidentRepository,
        )

        ProviderRegistry.clear_instances()

        failed_op_repo = ProviderRegistry.get_failed_operation_repo(name="memory")
        cb_repo = ProviderRegistry.get_circuit_breaker_repo(name="memory")
        security_repo = ProviderRegistry.get_security_repo(name="memory")

        assert isinstance(failed_op_repo, InMemoryFailedOperationRepository)
        assert isinstance(cb_repo, InMemoryCircuitBreakerStateRepository)
        assert isinstance(security_repo, InMemorySecurityIncidentRepository)

    def test_registry_caches_repositories(self):
        """Test that registry caches repository instances (singleton)."""
        from selfhealing.factory import ProviderRegistry

        ProviderRegistry.clear_instances()

        repo1 = ProviderRegistry.get_failed_operation_repo(name="memory")
        repo2 = ProviderRegistry.get_failed_operation_repo(name="memory")

        assert repo1 is repo2

    def test_registry_set_defaults_to_memory(self):
        """Test setting default to memory provider."""
        from selfhealing.factory import ProviderRegistry
        from selfhealing.adapters.memory import InMemoryFailedOperationRepository

        ProviderRegistry.clear_instances()
        ProviderRegistry.set_defaults(repo="memory")

        defaults = ProviderRegistry.get_defaults()
        assert defaults["repo"] == "memory"

        repo = ProviderRegistry.get_failed_operation_repo()
        assert isinstance(repo, InMemoryFailedOperationRepository)
