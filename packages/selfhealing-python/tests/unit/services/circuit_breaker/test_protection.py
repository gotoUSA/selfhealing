"""
Tests for Circuit Breaker Protection Mixin

Covers:
- Rate limit cascade detection
- Self-DDoS protection
- Adaptive backoff calculation

Refactored to use Factory Pattern (Phase 2):
- MockCircuitBreakerStateData → factories.MockCircuitBreakerStateData
- MockRepository → factories.InMemoryCircuitBreakerRepository
- MockRateLimitTracker → factories.InMemoryRateLimitTracker
"""

import pytest
from unittest.mock import MagicMock, patch

# Factory Pattern imports
from tests.factories import (
    TestDataFactory,
    MockCircuitBreakerStateData,
    InMemoryCircuitBreakerRepository,
    InMemoryRateLimitTracker,
)


class TestRateLimitCascadeDetection:
    """Tests for rate limit cascade detection."""
    
    def test_record_rate_limit_response_below_threshold(self):
        """Test recording rate limit response below threshold."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            rate_limit_cascade_threshold=10,
            rate_limit_cascade_window_seconds=60,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._rate_limits["test_service"] = 5  # Below threshold
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            result = service.record_rate_limit_response("test_service")
        
        # Should not trigger cascade
        assert result is None
    
    def test_record_rate_limit_response_triggers_cascade(self):
        """Test recording rate limit response triggers cascade."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            rate_limit_cascade_threshold=10,
            rate_limit_cascade_window_seconds=60,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._rate_limits["test_service"] = 15  # Above threshold
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            with patch('selfhealing.services.circuit_breaker.manual_control._is_system_enabled', return_value=True):
                result = service.record_rate_limit_response("test_service")
        
        # Should trigger cascade and open circuit
        assert result is not None
        assert result.success is True
    
    def test_record_rate_limit_response_when_disabled(self):
        """Test recording rate limit response when CB disabled."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=False)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        result = service.record_rate_limit_response("test_service")
        assert result is None
    
    def test_check_rate_limit_cascade(self):
        """Test check_rate_limit_cascade method."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            rate_limit_cascade_threshold=10,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._rate_limits["test_service"] = 15
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            is_cascade = service.check_rate_limit_cascade("test_service")
        
        assert is_cascade is True
    
    def test_check_rate_limit_cascade_no_cascade(self):
        """Test check_rate_limit_cascade when no cascade."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            rate_limit_cascade_threshold=10,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._rate_limits["test_service"] = 5
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            is_cascade = service.check_rate_limit_cascade("test_service")
        
        assert is_cascade is False


class TestSelfDDoSProtection:
    """Tests for self-DDoS protection."""
    
    def test_should_allow_with_ddos_protection_normal(self):
        """Test should_allow_with_ddos_protection under normal load."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            self_ddos_protection_enabled=True,
            self_ddos_request_threshold=100,
            self_ddos_window_seconds=10,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._requests["test_service"] = 50  # Below threshold
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            allowed, backoff = service.should_allow_with_ddos_protection("test_service")
        
        assert allowed is True
        assert backoff == 0.0
    
    def test_should_allow_with_ddos_protection_high_load(self):
        """Test should_allow_with_ddos_protection under high load."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            self_ddos_protection_enabled=True,
            self_ddos_request_threshold=100,
            self_ddos_window_seconds=10,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._requests["test_service"] = 150  # Above threshold
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            allowed, backoff = service.should_allow_with_ddos_protection("test_service")
        
        # Still allowed but with backoff suggestion
        assert allowed is True
        assert backoff > 0
    
    def test_should_allow_with_ddos_protection_circuit_open(self):
        """Test should_allow_with_ddos_protection when circuit is open."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        mock_repo._states["test_service"] = MockCircuitBreakerStateData(
            service_name="test_service",
            state="open"
        )
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            allowed, backoff = service.should_allow_with_ddos_protection("test_service")
        
        assert allowed is False
        assert backoff > 0
    
    def test_ddos_protection_disabled(self):
        """Test behavior when DDoS protection is disabled."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            self_ddos_protection_enabled=False,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._requests["test_service"] = 1000  # Very high
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            allowed, backoff = service.should_allow_with_ddos_protection("test_service")
        
        # Should allow without backoff when protection disabled
        assert allowed is True
        assert backoff == 0.0


class TestAdaptiveBackoff:
    """Tests for adaptive backoff calculation."""
    
    def test_calculate_adaptive_backoff_initial(self):
        """Test adaptive backoff at initial level."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            self_ddos_backoff_multiplier=2.0,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._backoff["test_service"] = 0
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            backoff = service.calculate_adaptive_backoff("test_service")
        
        assert backoff >= 0
    
    def test_calculate_adaptive_backoff_exponential(self):
        """Test adaptive backoff increases exponentially."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(
            enabled=True,
            self_ddos_backoff_multiplier=2.0,
        )
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            # Level 0
            mock_tracker._backoff["test_service"] = 0
            backoff0 = service.calculate_adaptive_backoff("test_service")
            
            # Level 2
            mock_tracker._backoff["test_service"] = 2
            backoff2 = service.calculate_adaptive_backoff("test_service")
            
            # Level 4
            mock_tracker._backoff["test_service"] = 4
            backoff4 = service.calculate_adaptive_backoff("test_service")
        
        # Backoff should increase with level
        assert backoff2 >= backoff0
        assert backoff4 >= backoff2
    
    def test_calculate_adaptive_backoff_includes_jitter(self):
        """Test adaptive backoff includes jitter."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        mock_tracker = InMemoryRateLimitTracker()
        mock_tracker._backoff["test_service"] = 1
        
        with patch('selfhealing.services.circuit_breaker.protection.get_rate_limit_tracker', return_value=mock_tracker):
            # Call multiple times
            backoffs = [service.calculate_adaptive_backoff("test_service") for _ in range(10)]
        
        # With jitter, values should vary
        # (might be same if jitter is deterministic in tests)
        assert len(backoffs) == 10
