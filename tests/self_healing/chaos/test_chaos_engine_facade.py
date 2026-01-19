"""
Tests for ChaosEngine Facade.

Validates that:
1. ChaosEngine singleton works correctly
2. All subsystem properties are lazily loaded
3. Backward compatibility with individual get_* functions
4. Reset functionality works properly
"""

import pytest
from unittest.mock import patch, MagicMock


class TestChaosEngineFacade:
    """Test ChaosEngine Facade implementation."""
    
    def test_get_chaos_engine_returns_singleton(self):
        """get_chaos_engine() should return the same instance."""
        from selfhealing.services.chaos import get_chaos_engine, reset_chaos_engine
        
        # Reset to ensure clean state
        reset_chaos_engine()
        
        engine1 = get_chaos_engine()
        engine2 = get_chaos_engine()
        
        assert engine1 is engine2
        
        # Cleanup
        reset_chaos_engine()
    
    def test_chaos_engine_has_all_subsystem_properties(self):
        """ChaosEngine should have all required subsystem properties."""
        from selfhealing.services.chaos import get_chaos_engine, reset_chaos_engine, ChaosEngine
        
        reset_chaos_engine()
        engine = get_chaos_engine()
        
        # Check that all properties exist
        assert hasattr(engine, 'scheduler')
        assert hasattr(engine, 'safety_guard')
        assert hasattr(engine, 'blast_radius')
        assert hasattr(engine, 'reports')
        assert hasattr(engine, 'analyzer')
        
        # Cleanup
        reset_chaos_engine()
    
    def test_subsystems_are_lazily_loaded(self):
        """Subsystems should not be loaded until accessed."""
        from selfhealing.services.chaos import ChaosEngine
        
        engine = ChaosEngine()
        
        # All internal references should be None initially
        assert engine._scheduler is None
        assert engine._safety_guard is None
        assert engine._blast_radius is None
        assert engine._reports is None
        assert engine._analyzer is None
    
    def test_scheduler_property_loads_correctly(self):
        """scheduler property should load ChaosSchedulerService."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            reset_chaos_engine,
            ChaosSchedulerService,
        )
        from selfhealing.services.chaos.scheduler import reset_chaos_scheduler
        
        reset_chaos_engine()
        reset_chaos_scheduler()
        
        engine = get_chaos_engine()
        scheduler = engine.scheduler
        
        assert isinstance(scheduler, ChaosSchedulerService)
        # Second access should return the same instance
        assert engine.scheduler is scheduler
        
        # Cleanup
        reset_chaos_engine()
        reset_chaos_scheduler()
    
    def test_safety_guard_property_loads_correctly(self):
        """safety_guard property should load SafetyGuard."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            reset_chaos_engine,
            SafetyGuard,
        )
        from selfhealing.services.chaos.safety_guard import reset_safety_guard
        
        reset_chaos_engine()
        reset_safety_guard()
        
        engine = get_chaos_engine()
        guard = engine.safety_guard
        
        assert isinstance(guard, SafetyGuard)
        # Second access should return the same instance
        assert engine.safety_guard is guard
        
        # Cleanup
        reset_chaos_engine()
        reset_safety_guard()
    
    def test_blast_radius_property_loads_correctly(self):
        """blast_radius property should load BlastRadiusManager."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            reset_chaos_engine,
            BlastRadiusManager,
        )
        from selfhealing.services.chaos.blast_radius import reset_blast_radius_manager
        
        reset_chaos_engine()
        reset_blast_radius_manager()
        
        engine = get_chaos_engine()
        manager = engine.blast_radius
        
        assert isinstance(manager, BlastRadiusManager)
        # Second access should return the same instance
        assert engine.blast_radius is manager
        
        # Cleanup
        reset_chaos_engine()
        reset_blast_radius_manager()
    
    def test_reports_property_loads_correctly(self):
        """reports property should load ResilienceReportGenerator."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            reset_chaos_engine,
            ResilienceReportGenerator,
        )
        from selfhealing.services.chaos.reports import reset_report_generator
        
        reset_chaos_engine()
        reset_report_generator()
        
        engine = get_chaos_engine()
        generator = engine.reports
        
        assert isinstance(generator, ResilienceReportGenerator)
        # Second access should return the same instance
        assert engine.reports is generator
        
        # Cleanup
        reset_chaos_engine()
        reset_report_generator()
    
    def test_analyzer_property_loads_correctly(self):
        """analyzer property should load BlastRadiusAnalyzer."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            reset_chaos_engine,
            BlastRadiusAnalyzer,
        )
        
        reset_chaos_engine()
        
        engine = get_chaos_engine()
        analyzer = engine.analyzer
        
        assert isinstance(analyzer, BlastRadiusAnalyzer)
        # Second access should return the same instance
        assert engine.analyzer is analyzer
        
        # Cleanup
        reset_chaos_engine()
    
    def test_reset_chaos_engine_clears_instance(self):
        """reset_chaos_engine() should clear the singleton."""
        from selfhealing.services.chaos import get_chaos_engine, reset_chaos_engine
        
        engine1 = get_chaos_engine()
        reset_chaos_engine()
        engine2 = get_chaos_engine()
        
        # After reset, a new instance should be created
        assert engine1 is not engine2
        
        # Cleanup
        reset_chaos_engine()
    
    def test_engine_reset_clears_all_subsystems(self):
        """ChaosEngine.reset() should clear all subsystem references."""
        from selfhealing.services.chaos import ChaosEngine
        
        engine = ChaosEngine()
        
        # Force set some mock values
        engine._scheduler = "mock_scheduler"
        engine._safety_guard = "mock_guard"
        engine._blast_radius = "mock_manager"
        engine._reports = "mock_generator"
        engine._analyzer = "mock_analyzer"
        
        engine.reset()
        
        assert engine._scheduler is None
        assert engine._safety_guard is None
        assert engine._blast_radius is None
        assert engine._reports is None
        assert engine._analyzer is None
    
    def test_backward_compatibility_get_chaos_scheduler(self):
        """get_chaos_scheduler() should still work (backward compatible)."""
        from selfhealing.services.chaos import get_chaos_scheduler, ChaosSchedulerService
        from selfhealing.services.chaos.scheduler import reset_chaos_scheduler
        
        reset_chaos_scheduler()
        
        scheduler = get_chaos_scheduler()
        assert isinstance(scheduler, ChaosSchedulerService)
        
        # Cleanup
        reset_chaos_scheduler()
    
    def test_backward_compatibility_get_safety_guard(self):
        """get_safety_guard() should still work (backward compatible)."""
        from selfhealing.services.chaos import get_safety_guard, SafetyGuard
        from selfhealing.services.chaos.safety_guard import reset_safety_guard
        
        reset_safety_guard()
        
        guard = get_safety_guard()
        assert isinstance(guard, SafetyGuard)
        
        # Cleanup
        reset_safety_guard()
    
    def test_backward_compatibility_get_blast_radius_manager(self):
        """get_blast_radius_manager() should still work (backward compatible)."""
        from selfhealing.services.chaos import get_blast_radius_manager, BlastRadiusManager
        from selfhealing.services.chaos.blast_radius import reset_blast_radius_manager
        
        reset_blast_radius_manager()
        
        manager = get_blast_radius_manager()
        assert isinstance(manager, BlastRadiusManager)
        
        # Cleanup
        reset_blast_radius_manager()
    
    def test_backward_compatibility_get_report_generator(self):
        """get_report_generator() should still work (backward compatible)."""
        from selfhealing.services.chaos import get_report_generator, ResilienceReportGenerator
        from selfhealing.services.chaos.reports import reset_report_generator
        
        reset_report_generator()
        
        generator = get_report_generator()
        assert isinstance(generator, ResilienceReportGenerator)
        
        # Cleanup
        reset_report_generator()
    
    def test_backward_compatibility_get_blast_radius_analyzer(self):
        """get_blast_radius_analyzer() should still work (backward compatible)."""
        from selfhealing.services.chaos import get_blast_radius_analyzer, BlastRadiusAnalyzer
        
        analyzer = get_blast_radius_analyzer()
        assert isinstance(analyzer, BlastRadiusAnalyzer)
    
    def test_chaos_engine_in_module_all(self):
        """ChaosEngine should be exported in __all__."""
        from selfhealing.services import chaos
        
        assert 'ChaosEngine' in chaos.__all__
        assert 'get_chaos_engine' in chaos.__all__
        assert 'reset_chaos_engine' in chaos.__all__


class TestChaosEngineFacadeIntegration:
    """Integration tests for ChaosEngine facade with actual subsystems."""
    
    def test_engine_scheduler_matches_direct_singleton(self):
        """engine.scheduler should return the same as get_chaos_scheduler()."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            get_chaos_scheduler,
            reset_chaos_engine,
        )
        from selfhealing.services.chaos.scheduler import reset_chaos_scheduler
        
        reset_chaos_engine()
        reset_chaos_scheduler()
        
        engine = get_chaos_engine()
        
        # Access through facade
        scheduler_via_engine = engine.scheduler
        
        # Access directly
        scheduler_direct = get_chaos_scheduler()
        
        # Both should be the same singleton
        assert scheduler_via_engine is scheduler_direct
        
        # Cleanup
        reset_chaos_engine()
        reset_chaos_scheduler()
    
    def test_engine_safety_guard_matches_direct_singleton(self):
        """engine.safety_guard should return the same as get_safety_guard()."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            get_safety_guard,
            reset_chaos_engine,
        )
        from selfhealing.services.chaos.safety_guard import reset_safety_guard
        
        reset_chaos_engine()
        reset_safety_guard()
        
        engine = get_chaos_engine()
        
        guard_via_engine = engine.safety_guard
        guard_direct = get_safety_guard()
        
        assert guard_via_engine is guard_direct
        
        # Cleanup
        reset_chaos_engine()
        reset_safety_guard()
    
    def test_engine_blast_radius_matches_direct_singleton(self):
        """engine.blast_radius should return the same as get_blast_radius_manager()."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            get_blast_radius_manager,
            reset_chaos_engine,
        )
        from selfhealing.services.chaos.blast_radius import reset_blast_radius_manager
        
        reset_chaos_engine()
        reset_blast_radius_manager()
        
        engine = get_chaos_engine()
        
        manager_via_engine = engine.blast_radius
        manager_direct = get_blast_radius_manager()
        
        assert manager_via_engine is manager_direct
        
        # Cleanup
        reset_chaos_engine()
        reset_blast_radius_manager()
    
    def test_engine_reports_matches_direct_singleton(self):
        """engine.reports should return the same as get_report_generator()."""
        from selfhealing.services.chaos import (
            get_chaos_engine,
            get_report_generator,
            reset_chaos_engine,
        )
        from selfhealing.services.chaos.reports import reset_report_generator
        
        reset_chaos_engine()
        reset_report_generator()
        
        engine = get_chaos_engine()
        
        generator_via_engine = engine.reports
        generator_direct = get_report_generator()
        
        assert generator_via_engine is generator_direct
        
        # Cleanup
        reset_chaos_engine()
        reset_report_generator()
