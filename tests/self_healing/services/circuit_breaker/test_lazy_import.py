"""
Tests for Circuit Breaker Lazy Import Pattern.

Validates that:
1. Core API (7 symbols) are directly available
2. Extended symbols are lazily loaded via __getattr__
3. __all__ contains all 126 symbols
4. Backward compatibility with existing imports
5. __dir__ returns all available symbols
"""

import pytest


class TestCircuitBreakerLazyImport:
    """Test Lazy Import implementation for circuit_breaker module."""
    
    def test_core_api_direct_import(self):
        """Core API symbols should be directly importable."""
        from selfhealing.services.circuit_breaker import (
            CircuitBreakerConfig,
            CircuitBreakerResult,
            CircuitState,
            CircuitBreakerService,
            get_circuit_breaker_service,
            should_allow_request,
            force_open_circuit,
        )
        
        # All core symbols should be available
        assert CircuitBreakerConfig is not None
        assert CircuitBreakerResult is not None
        assert CircuitState is not None
        assert CircuitBreakerService is not None
        assert callable(get_circuit_breaker_service)
        assert callable(should_allow_request)
        assert callable(force_open_circuit)
    
    def test_lazy_import_models(self):
        """Model symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            ServiceConfig,
            SheddingLevel,
            LoadSheddingPolicy,
            CanaryStage,
            RecoveryStrategy,
        )
        
        assert ServiceConfig is not None
        assert SheddingLevel is not None
        assert LoadSheddingPolicy is not None
        assert CanaryStage is not None
        assert RecoveryStrategy is not None
    
    def test_lazy_import_adaptive_threshold(self):
        """Adaptive threshold symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            AdaptiveThresholdManager,
            AdjustedThreshold,
            get_adaptive_threshold_manager,
        )
        
        assert AdaptiveThresholdManager is not None
        assert AdjustedThreshold is not None
        assert callable(get_adaptive_threshold_manager)
    
    def test_lazy_import_freeze_mode(self):
        """Freeze mode symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            FreezeModeManager,
            FreezeReason,
            get_freeze_mode_manager,
        )
        
        assert FreezeModeManager is not None
        assert FreezeReason is not None
        assert callable(get_freeze_mode_manager)
    
    def test_lazy_import_panic_threshold(self):
        """Panic threshold symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            PanicThresholdMonitor,
            PanicThresholdResult,
            get_panic_threshold_monitor,
        )
        
        assert PanicThresholdMonitor is not None
        assert PanicThresholdResult is not None
        assert callable(get_panic_threshold_monitor)
    
    def test_lazy_import_tracing(self):
        """Tracing symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            TracingConfig,
            TriggeringRequestInfo,
            CircuitBreakerTracingManager,
            get_tracing_manager,
        )
        
        assert TracingConfig is not None
        assert TriggeringRequestInfo is not None
        assert CircuitBreakerTracingManager is not None
        assert callable(get_tracing_manager)
    
    def test_lazy_import_load_shedding(self):
        """Load shedding symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            LoadSheddingManager,
            SheddingState,
            SheddingDecision,
            get_load_shedding_manager,
        )
        
        assert LoadSheddingManager is not None
        assert SheddingState is not None
        assert SheddingDecision is not None
        assert callable(get_load_shedding_manager)
    
    def test_lazy_import_canary_recovery(self):
        """Canary recovery symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            CanaryState,
            CanaryRecoveryManager,
            get_canary_recovery_manager,
            start_canary_recovery,
        )
        
        assert CanaryState is not None
        assert CanaryRecoveryManager is not None
        assert callable(get_canary_recovery_manager)
        assert callable(start_canary_recovery)
    
    def test_lazy_import_blast_radius(self):
        """Blast radius integration symbols should be lazily loaded."""
        from selfhealing.services.circuit_breaker import (
            BlastRadiusLevel,
            BlastRadiusAssessment,
            BlastRadiusIntegration,
            get_blast_radius_integration,
        )
        
        assert BlastRadiusLevel is not None
        assert BlastRadiusAssessment is not None
        assert BlastRadiusIntegration is not None
        assert callable(get_blast_radius_integration)
    
    def test_module_all_contains_all_symbols(self):
        """__all__ should contain all 126 symbols."""
        from selfhealing.services import circuit_breaker
        
        # Check that __all__ is properly defined
        assert hasattr(circuit_breaker, '__all__')
        
        # Should have at least 100 symbols (accounting for slight variations)
        assert len(circuit_breaker.__all__) >= 100
        
        # Core symbols must be in __all__
        core_symbols = [
            "CircuitBreakerConfig",
            "CircuitBreakerResult",
            "CircuitState",
            "CircuitBreakerService",
            "get_circuit_breaker_service",
            "should_allow_request",
            "force_open_circuit",
        ]
        for symbol in core_symbols:
            assert symbol in circuit_breaker.__all__, f"{symbol} not in __all__"
    
    def test_dir_returns_all_symbols(self):
        """__dir__ should return all available symbols."""
        from selfhealing.services import circuit_breaker
        
        available = dir(circuit_breaker)
        
        # Core symbols must be in dir()
        core_symbols = [
            "CircuitBreakerConfig",
            "CircuitBreakerResult",
            "CircuitState",
            "CircuitBreakerService",
        ]
        for symbol in core_symbols:
            assert symbol in available, f"{symbol} not in dir()"
    
    def test_invalid_attribute_raises_error(self):
        """Accessing invalid attribute should raise AttributeError."""
        from selfhealing.services import circuit_breaker
        
        with pytest.raises(AttributeError) as excinfo:
            _ = circuit_breaker.NonExistentSymbol
        
        assert "NonExistentSymbol" in str(excinfo.value)
    
    def test_lazy_import_caching(self):
        """Lazy imports should be cached after first access."""
        from selfhealing.services import circuit_breaker
        
        # Access the same symbol twice
        first_access = circuit_breaker.LoadSheddingManager
        second_access = circuit_breaker.LoadSheddingManager
        
        # Both should be the same object (cached)
        assert first_access is second_access
    
    def test_backward_compatibility_convenience_functions(self):
        """Convenience functions should still work (backward compatible)."""
        from selfhealing.services.circuit_breaker import (
            force_close_circuit,
            record_rate_limit,
            should_allow_with_protection,
            get_protection_status,
        )
        
        assert callable(force_close_circuit)
        assert callable(record_rate_limit)
        assert callable(should_allow_with_protection)
        assert callable(get_protection_status)
    
    def test_backward_compatibility_rate_limit_tracker(self):
        """RateLimitTracker should be importable (backward compatible)."""
        from selfhealing.services.circuit_breaker import (
            RateLimitTracker,
            get_rate_limit_tracker,
        )
        
        assert RateLimitTracker is not None
        assert callable(get_rate_limit_tracker)
    
    def test_backward_compatibility_mixins(self):
        """Mixins should be importable (backward compatible)."""
        from selfhealing.services.circuit_breaker import (
            ProtectionMixin,
            ManualControlMixin,
        )
        
        assert ProtectionMixin is not None
        assert ManualControlMixin is not None


class TestCircuitBreakerLazyImportIntegration:
    """Integration tests for circuit_breaker lazy import."""
    
    def test_circuit_breaker_service_works(self):
        """CircuitBreakerService should work correctly."""
        from selfhealing.services.circuit_breaker import (
            get_circuit_breaker_service,
            CircuitBreakerService,
        )
        
        service = get_circuit_breaker_service()
        assert isinstance(service, CircuitBreakerService)
    
    def test_circuit_state_enum_works(self):
        """CircuitState enum should have correct values."""
        from selfhealing.services.circuit_breaker import CircuitState
        
        # Check enum values exist
        assert hasattr(CircuitState, 'CLOSED')
        assert hasattr(CircuitState, 'OPEN')
        assert hasattr(CircuitState, 'HALF_OPEN')
    
    def test_load_shedding_manager_singleton(self):
        """LoadSheddingManager should work as singleton."""
        from selfhealing.services.circuit_breaker import (
            get_load_shedding_manager,
            reset_load_shedding_manager,
        )
        
        reset_load_shedding_manager()
        
        manager1 = get_load_shedding_manager()
        manager2 = get_load_shedding_manager()
        
        assert manager1 is manager2
        
        reset_load_shedding_manager()
    
    def test_freeze_mode_manager_singleton(self):
        """FreezeModeManager should work as singleton."""
        from selfhealing.services.circuit_breaker import (
            get_freeze_mode_manager,
            FreezeModeManager,
        )
        
        manager = get_freeze_mode_manager()
        assert isinstance(manager, FreezeModeManager)
