"""
Tests for Core Settings Deprecation Pattern.

Validates that:
1. Settings re-exports from core emit DeprecationWarning
2. The actual functionality still works (backward compatibility)
3. Core's own symbols are directly available without warning
"""

import pytest
import warnings


class TestCoreSettingsDeprecation:
    """Test deprecation warning for settings imports from core."""
    
    def test_sla_thresholds_emits_deprecation_warning(self):
        """Importing SLAThresholds from core should emit DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from selfhealing.core import SLAThresholds
            
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "SLAThresholds" in str(w[0].message)
            assert "selfhealing.settings" in str(w[0].message)
            assert "v3.0.0" in str(w[0].message)
    
    def test_circuit_breaker_config_emits_deprecation_warning(self):
        """Importing CircuitBreakerConfig from core should emit DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Clear cache to ensure warning is emitted
            import selfhealing.core
            selfhealing.core._deprecated_cache.pop("CircuitBreakerConfig", None)
            
            from selfhealing.core import CircuitBreakerConfig
            
            assert len(w) >= 1
            warning_messages = [str(warning.message) for warning in w]
            assert any("CircuitBreakerConfig" in msg for msg in warning_messages)
    
    def test_get_config_emits_deprecation_warning(self):
        """Importing get_config from core should emit DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import selfhealing.core
            selfhealing.core._deprecated_cache.pop("get_config", None)
            
            from selfhealing.core import get_config
            
            assert len(w) >= 1
            warning_messages = [str(warning.message) for warning in w]
            assert any("get_config" in msg for msg in warning_messages)
    
    def test_deprecated_import_still_works(self):
        """Deprecated imports should still return the correct symbol."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core import SLAThresholds, get_config
            
            # Verify the classes are correct
            assert SLAThresholds is not None
            # SLASettings is a Pydantic model
            from pydantic import BaseModel
            assert issubclass(SLAThresholds, BaseModel)
            assert callable(get_config)
    
    def test_legacy_aliases_work(self):
        """Legacy aliases (SLAThresholds, SecurityThresholds, NotificationLimits) should work."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core import (
                SLAThresholds,
                SecurityThresholds,
                NotificationLimits,
            )
            
            # All should resolve to their respective Settings classes
            from selfhealing.settings import (
                SLASettings,
                SecuritySettings,
                NotificationSettings,
            )
            
            assert SLAThresholds is SLASettings
            assert SecurityThresholds is SecuritySettings
            assert NotificationLimits is NotificationSettings


class TestCoreOwnSymbols:
    """Test that core's own symbols are directly available without warning."""
    
    def test_circuit_state_no_warning(self):
        """CircuitState (core's own symbol) should not emit warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from selfhealing.core import CircuitState
            
            # Filter for deprecation warnings only
            deprecation_warnings = [
                warning for warning in w 
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0
            assert CircuitState is not None
    
    def test_failure_type_no_warning(self):
        """FailureType (core's own symbol) should not emit warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from selfhealing.core import FailureType
            
            deprecation_warnings = [
                warning for warning in w 
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0
            assert FailureType is not None
    
    def test_exponential_backoff_no_warning(self):
        """ExponentialBackoff (core's own symbol) should not emit warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from selfhealing.core import ExponentialBackoff
            
            deprecation_warnings = [
                warning for warning in w 
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0
            assert ExponentialBackoff is not None
    
    def test_time_provider_no_warning(self):
        """TimeProvider (core's own symbol) should not emit warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from selfhealing.core import TimeProvider
            
            deprecation_warnings = [
                warning for warning in w 
                if issubclass(warning.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0
            assert TimeProvider is not None


class TestCoreInvalidAttribute:
    """Test invalid attribute access."""
    
    def test_invalid_attribute_raises_error(self):
        """Accessing non-existent attribute should raise AttributeError or ImportError."""
        # Python's import system may raise ImportError instead of AttributeError
        # when using 'from X import Y' syntax
        with pytest.raises((AttributeError, ImportError)) as exc_info:
            from selfhealing.core import NonExistentSymbol
        
        assert "NonExistentSymbol" in str(exc_info.value)


class TestDeprecationCaching:
    """Test that deprecated symbols are cached."""
    
    def test_deprecated_symbol_cached(self):
        """Deprecated symbols should be cached after first access."""
        import selfhealing.core
        
        # Clear cache
        selfhealing.core._deprecated_cache.clear()
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # First access
            from selfhealing.core import SLAConfig
            
        # Verify it's cached
        assert "SLAConfig" in selfhealing.core._deprecated_cache
    
    def test_second_access_no_new_warning(self):
        """Second access to deprecated symbol should not emit new warning."""
        import selfhealing.core
        
        # Ensure it's cached
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from selfhealing.core import DLQConfig
        
        # Second access should use cache
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = selfhealing.core.DLQConfig
            
            # No new deprecation warning should be emitted (using cache)
            new_dlq_warnings = [
                warning for warning in w 
                if issubclass(warning.category, DeprecationWarning) 
                and "DLQConfig" in str(warning.message)
            ]
            assert len(new_dlq_warnings) == 0
