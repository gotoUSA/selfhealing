"""
Tiering Integration Tests.

These tests require Django settings and should be run in Docker environment.
Run with: docker-compose exec web pytest tests/self_healing/django/test_tiering_integration.py
"""

import pytest


@pytest.mark.django_db
class TestTierRegistryIntegration:
    """Integration tests for TierRegistry with Django settings."""

    def test_circuit_open_bypasses_engine(self):
        """When circuit is open, static path match still works."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            get_tiering_circuit_breaker,
        )
        
        cb = get_tiering_circuit_breaker()
        cb.reset()
        
        registry = TierRegistry.__new__(TierRegistry)
        registry._init()
        
        # Trip the circuit breaker
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure(Exception("test"))
        
        assert cb.is_open is True
        
        # Static path match takes priority over circuit open
        result = registry.resolve_tier_with_fallback("/api/self-healing/control/")
        
        # Static path match is still honored
        assert result.tier_id == "critical"
