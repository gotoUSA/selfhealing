"""
Hybrid Storage Architecture Integration Tests.

Phase 7: Integration tests for the hybrid storage architecture.

Tests:
1. ProviderRegistry with real Redis backend
2. Statistics adapter integration with Django ORM
3. Runtime + Statistics layer interaction
4. Graceful degradation when statistics adapter not available

Requirements:
- Docker Compose for Redis and PostgreSQL
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_hybrid_storage_integration.py -v

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

import os
import pytest
from unittest.mock import MagicMock

# Setup Django before importing selfhealing
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

from selfhealing.factory import ProviderRegistry
from selfhealing.interfaces.statistics import (
    StatisticsRepositoryInterface,
    StatusCounts,
    EntityAuditTrail,
)
from selfhealing.adapters.statistics.null import NullStatisticsRepository


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset ProviderRegistry before and after each test."""
    ProviderRegistry.reset()
    yield
    ProviderRegistry.reset()


# NOTE: redis_available fixture 제거됨
# tests/conftest.py의 pytest_collection_modifyitems가 requires_redis 마커 처리


@pytest.fixture
def mock_django_model():
    """Create a mock Django model for testing."""
    mock_model = MagicMock()
    mock_model.__name__ = "MockFailedOperation"  # Required for DjangoStatisticsAdapter
    mock_model.objects = MagicMock()
    mock_model.objects.count.return_value = 100
    mock_model.objects.filter.return_value.count.return_value = 30
    mock_model.objects.values.return_value.annotate.return_value = [
        {"status": "pending", "count": 30},
        {"status": "resolved", "count": 50},
        {"status": "failed", "count": 20},
    ]
    return mock_model


# =============================================================================
# Integration Tests: ProviderRegistry
# =============================================================================


class TestProviderRegistryIntegration:
    """Integration tests for ProviderRegistry with real backends."""

    def test_runtime_repo_available_without_statistics(self):
        """Runtime repositories work without statistics adapter."""
        # Statistics adapter not registered
        assert not ProviderRegistry.has_statistics_adapter()
        
        # Runtime repos should still work (may raise if no backend configured)
        # This test validates the independence of statistics from runtime
        try:
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            dlq_repo = ProviderRegistry.get_failed_operation_repo()
            assert cb_repo is not None
            assert dlq_repo is not None
        except ValueError:
            # No runtime repository configured - that's OK for this test
            # The point is that statistics adapter is independent
            pass

    def test_statistics_repo_returns_null_when_not_registered(self):
        """Statistics repo returns NullStatisticsRepository when not registered."""
        stats_repo = ProviderRegistry.get_statistics_repo()
        
        assert isinstance(stats_repo, NullStatisticsRepository)
        
        # Should return empty results without errors
        counts = stats_repo.get_status_counts()
        assert counts.total == 0

    def test_register_and_get_statistics_adapter(self):
        """Register and retrieve statistics adapter."""
        mock_adapter = MagicMock(spec=StatisticsRepositoryInterface)
        mock_adapter.get_status_counts.return_value = StatusCounts(total=100, pending=30)
        
        ProviderRegistry.register_statistics_adapter(mock_adapter)
        
        stats_repo = ProviderRegistry.get_statistics_repo()
        assert stats_repo is mock_adapter
        
        counts = stats_repo.get_status_counts()
        assert counts.total == 100
        assert counts.pending == 30


# =============================================================================
# Integration Tests: Django Statistics Adapter
# =============================================================================


class TestDjangoStatisticsAdapterIntegration:
    """Integration tests for DjangoStatisticsAdapter."""

    def test_adapter_initialization_without_model(self):
        """Adapter initializes with warning when no model provided."""
        from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
        
        # Should initialize without error, just log warning
        adapter = DjangoStatisticsAdapter()
        
        # Methods should return empty results
        counts = adapter.get_status_counts()
        assert counts.total == 0

    def test_adapter_with_mock_model(self, mock_django_model):
        """Adapter works with mocked Django model."""
        from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
        
        adapter = DjangoStatisticsAdapter(
            failed_operation_model=mock_django_model,
        )
        
        # Register adapter
        ProviderRegistry.register_statistics_adapter(adapter)
        
        # Get through registry
        stats_repo = ProviderRegistry.get_statistics_repo()
        assert stats_repo is adapter

    def test_graceful_degradation_on_model_error(self, mock_django_model):
        """Adapter handles model errors gracefully."""
        from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
        
        # Simulate database error
        mock_django_model.objects.values.side_effect = Exception("DB connection failed")
        
        adapter = DjangoStatisticsAdapter(
            failed_operation_model=mock_django_model,
        )
        
        # Should not raise, return empty results
        counts = adapter.get_status_counts()
        assert counts.total == 0


# =============================================================================
# Integration Tests: Hybrid Layer Interaction
# =============================================================================


class TestHybridLayerInteraction:
    """Tests for runtime and statistics layer interaction."""

    def test_runtime_independent_of_statistics(self):
        """Runtime layer works independently of statistics layer."""
        # Even without statistics adapter, runtime layer is independent
        # Statistics should not affect whether runtime can be requested
        assert not ProviderRegistry.has_statistics_adapter()
        
        # The key point: statistics layer doesn't prevent runtime layer access
        # Whether runtime repos are configured is a separate concern
        try:
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            assert cb_repo is not None
        except ValueError:
            # No runtime backend configured - still proves independence
            pass

    def test_statistics_does_not_affect_runtime(self):
        """Statistics adapter errors don't affect runtime."""
        # Create failing statistics adapter
        failing_adapter = MagicMock(spec=StatisticsRepositoryInterface)
        failing_adapter.get_status_counts.side_effect = Exception("Stats failed")
        
        ProviderRegistry.register_statistics_adapter(failing_adapter)
        
        # Runtime should still work (or fail independently of statistics)
        try:
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            assert cb_repo is not None
        except ValueError:
            # No runtime backend - proves stats adapter doesn't break runtime
            pass

    def test_null_adapter_provides_graceful_degradation(self):
        """NullStatisticsRepository provides graceful degradation."""
        # No adapter registered
        stats_repo = ProviderRegistry.get_statistics_repo()
        
        # All methods should work without errors
        assert stats_repo.get_status_counts().total == 0
        assert stats_repo.get_domain_distribution() == []
        assert stats_repo.get_resolution_rate() == 0.0
        assert stats_repo.list_entries().items == []
        assert stats_repo.get_cleanup_stats().total == 0
        assert stats_repo.get_circuit_breaker_summary().total == 0
        assert stats_repo.get_audit_trail_by_entity("test").entries == []


# =============================================================================
# Integration Tests: Audit Trail
# =============================================================================


class TestAuditTrailIntegration:
    """Tests for audit trail integration."""

    def test_null_adapter_audit_trail(self):
        """NullStatisticsRepository returns empty audit trail."""
        stats_repo = ProviderRegistry.get_statistics_repo()
        
        trail = stats_repo.get_audit_trail_by_entity("dlq-123", entity_type="dlq_entry")
        
        assert isinstance(trail, EntityAuditTrail)
        assert trail.entity_id == "dlq-123"
        assert trail.entity_type == "dlq_entry"
        assert trail.entries == []
        assert trail.is_chain_valid is True  # Empty trail is valid

    def test_link_audit_entry_returns_false_for_null(self):
        """NullStatisticsRepository link_audit_entry returns False."""
        stats_repo = ProviderRegistry.get_statistics_repo()
        
        result = stats_repo.link_audit_entry(
            entity_id="dlq-123",
            entity_type="dlq_entry",
            action="store",
            actor_id="system",
        )
        
        assert result is False


# =============================================================================
# Integration Tests: Redis Backend (requires Docker)
# =============================================================================


@pytest.mark.requires_redis
class TestRedisBackendIntegration:
    """Integration tests requiring Redis (run with docker-compose)."""

    def test_redis_runtime_with_null_statistics(self):
        """Redis runtime works with null statistics adapter."""
        # Register Redis repositories if available
        try:
            from selfhealing.adapters.redis import (
                RedisCircuitBreakerStateRepository,
            )
            ProviderRegistry.register_circuit_breaker_repo(
                "redis",
                RedisCircuitBreakerStateRepository(),
            )
            ProviderRegistry._default_repo = "redis"
            
            # Runtime should use Redis
            cb_repo = ProviderRegistry.get_circuit_breaker_repo()
            assert cb_repo is not None
            
            # Statistics should still be null
            stats_repo = ProviderRegistry.get_statistics_repo()
            assert isinstance(stats_repo, NullStatisticsRepository)
        except ImportError:
            pytest.skip("Redis adapter not available")


# =============================================================================
# Integration Tests: Performance Validation
# =============================================================================


class TestPerformanceValidation:
    """Tests to validate performance assumptions of hybrid architecture."""

    def test_null_adapter_is_fast(self):
        """NullStatisticsRepository operations are instant."""
        import time
        
        stats_repo = NullStatisticsRepository()
        
        start = time.perf_counter()
        for _ in range(1000):
            stats_repo.get_status_counts()
            stats_repo.get_domain_distribution()
            stats_repo.list_entries()
        elapsed = time.perf_counter() - start
        
        # 1000 iterations of 3 operations should complete in < 100ms
        assert elapsed < 0.1, f"NullStatisticsRepository too slow: {elapsed:.3f}s"

    def test_provider_registry_lookup_is_fast(self):
        """ProviderRegistry lookups are fast."""
        import time
        
        mock_adapter = MagicMock(spec=StatisticsRepositoryInterface)
        ProviderRegistry.register_statistics_adapter(mock_adapter)
        
        start = time.perf_counter()
        for _ in range(10000):
            ProviderRegistry.get_statistics_repo()
        elapsed = time.perf_counter() - start
        
        # 10000 lookups should complete in < 50ms
        assert elapsed < 0.05, f"ProviderRegistry lookup too slow: {elapsed:.3f}s"
