"""
Integration Tests for API Tiering Fallback (Defense-in-Depth).

These tests require Django REST Framework settings.

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md (Section 4)
"""

import pytest


@pytest.mark.django_db
class TestResolveTierWithFallback:
    """Tests for resolve_tier_with_fallback (Defense-in-Depth)."""

    def test_normal_resolution(self):
        """Should resolve tier normally when no fallback needed."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
            get_tiering_circuit_breaker,
        )

        # Reset circuit breaker
        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        result = registry.resolve_tier_with_fallback("/api/self-healing/control/")

        assert result.tier_id == "critical"
        assert result.is_fallback is False
        assert result.fallback_reason == TierFallbackReason.NONE
        assert result.latency_ms >= 0

    def test_static_critical_path_fallback(self):
        """Static critical paths should always resolve to critical tier."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
            get_tiering_circuit_breaker,
        )

        # Reset circuit breaker
        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Clear all dynamic mappings
        registry._mappings = []

        # Control path should still be critical (static)
        result = registry.resolve_tier_with_fallback("/api/self-healing/control/allow/")

        assert result.tier_id == "critical"
        assert result.is_fallback is True
        assert result.fallback_reason == TierFallbackReason.STATIC_PATH_MATCH

    def test_unknown_path_fail_closed(self):
        """Unknown paths should fail closed (non_essential)."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
            get_tiering_circuit_breaker,
        )

        # Reset circuit breaker
        get_tiering_circuit_breaker().reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Clear all mappings
        registry._mappings = []

        # Unknown path should be non_essential (fail-closed)
        result = registry.resolve_tier_with_fallback("/api/unknown/random/")

        assert result.tier_id == "non_essential"
        assert result.multiplier == 0.0
        assert result.is_fallback is True
        assert result.fallback_reason == TierFallbackReason.CONFIG_MISSING

    def test_circuit_open_bypasses_engine_for_non_static_path(self):
        """When circuit is open, non-static paths should use fallback."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
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

        # Non-static path should get CIRCUIT_OPEN fallback
        result = registry.resolve_tier_with_fallback("/api/unknown/path/")

        assert result.is_fallback is True
        assert result.fallback_reason == TierFallbackReason.CIRCUIT_OPEN
        assert result.tier_id == "non_essential"

    def test_static_path_protected_even_when_circuit_open(self):
        """Static critical paths are protected even when circuit is open."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
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

        # Static critical path should still be protected
        result = registry.resolve_tier_with_fallback("/api/self-healing/control/")

        assert result.is_fallback is True
        # Static path match takes precedence over circuit open
        assert result.fallback_reason == TierFallbackReason.STATIC_PATH_MATCH
        assert result.tier_id == "critical"

    def test_exception_triggers_fallback(self):
        """Exception in tier resolution should trigger fallback."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            TierFallbackReason,
            get_tiering_circuit_breaker,
        )

        cb = get_tiering_circuit_breaker()
        cb.reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Mock resolve_tier to raise exception
        def raise_error(*args, **kwargs):
            raise RuntimeError("Simulated engine failure")

        original_resolve = registry.resolve_tier
        registry.resolve_tier = raise_error

        try:
            result = registry.resolve_tier_with_fallback("/api/unknown/")

            assert result.is_fallback is True
            assert result.fallback_reason == TierFallbackReason.ENGINE_ERROR
            assert result.tier_id == "non_essential"
        finally:
            registry.resolve_tier = original_resolve

    def test_critical_path_protected_even_on_exception(self):
        """Static critical paths should be protected even when engine fails."""
        from selfhealing.api.django.tiering import (
            TierRegistry,
            get_tiering_circuit_breaker,
        )

        cb = get_tiering_circuit_breaker()
        cb.reset()

        registry = TierRegistry.__new__(TierRegistry)
        registry._init()

        # Mock resolve_tier to raise exception
        def raise_error(*args, **kwargs):
            raise RuntimeError("Simulated engine failure")

        original_resolve = registry.resolve_tier
        registry.resolve_tier = raise_error

        try:
            # Control path should still be critical
            result = registry.resolve_tier_with_fallback("/api/self-healing/control/")

            assert result.tier_id == "critical"
            assert result.is_fallback is True
        finally:
            registry.resolve_tier = original_resolve
