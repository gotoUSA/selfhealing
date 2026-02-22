"""
ProviderRegistry 테스트.
"""

import pytest


class TestProviderRegistry:
    """Tests for ProviderRegistry with In-Memory repositories."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset ProviderRegistry before and after each test for isolation."""
        from selfhealing.adapters.memory import (
            InMemoryCircuitBreakerStateRepository,
            InMemoryFailedOperationRepository,
            InMemorySecurityIncidentRepository,
        )
        from selfhealing.factory import ProviderRegistry

        # Store original state
        original_instances = ProviderRegistry._instances.copy()
        original_defaults = {
            "cache": ProviderRegistry._default_cache,
            "queue": ProviderRegistry._default_queue,
            "repo": ProviderRegistry._default_repo,
        }
        original_failed_op_repos = ProviderRegistry._failed_op_repos.copy()
        original_cb_repos = ProviderRegistry._circuit_breaker_repos.copy()
        original_security_repos = ProviderRegistry._security_repos.copy()

        # Clear instances for fresh test
        ProviderRegistry.clear_instances()

        # Ensure memory adapters are registered (병렬 테스트에서 reset()으로 초기화될 수 있음)
        if "memory" not in ProviderRegistry._failed_op_repos:
            ProviderRegistry.register_failed_operation_repo("memory", InMemoryFailedOperationRepository)
        if "memory" not in ProviderRegistry._circuit_breaker_repos:
            ProviderRegistry.register_circuit_breaker_repo("memory", InMemoryCircuitBreakerStateRepository)
        if "memory" not in ProviderRegistry._security_repos:
            ProviderRegistry.register_security_repo("memory", InMemorySecurityIncidentRepository)

        yield

        # Restore original state
        ProviderRegistry._instances = original_instances
        ProviderRegistry._default_cache = original_defaults["cache"]
        ProviderRegistry._default_queue = original_defaults["queue"]
        ProviderRegistry._default_repo = original_defaults["repo"]
        ProviderRegistry._failed_op_repos = original_failed_op_repos
        ProviderRegistry._circuit_breaker_repos = original_cb_repos
        ProviderRegistry._security_repos = original_security_repos

    def test_registry_has_inmemory_repositories_registered(self):
        """Test that in-memory repositories are auto-registered."""
        from selfhealing.factory import ProviderRegistry

        providers = ProviderRegistry.list_providers()
        assert "memory" in providers["failed_operation_repo"]
        assert "memory" in providers["circuit_breaker_repo"]
        assert "memory" in providers["security_repo"]

    def test_registry_creates_inmemory_repositories(self):
        """Test that registry creates in-memory repositories."""
        from selfhealing.adapters.memory import (
            InMemoryCircuitBreakerStateRepository,
            InMemoryFailedOperationRepository,
            InMemorySecurityIncidentRepository,
        )
        from selfhealing.factory import ProviderRegistry

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
        from selfhealing.adapters.memory import InMemoryFailedOperationRepository
        from selfhealing.factory import ProviderRegistry

        ProviderRegistry.clear_instances()
        ProviderRegistry.set_defaults(repo="memory")

        defaults = ProviderRegistry.get_defaults()
        assert defaults["repo"] == "memory"

        repo = ProviderRegistry.get_failed_operation_repo()
        assert isinstance(repo, InMemoryFailedOperationRepository)
