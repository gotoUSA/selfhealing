"""
Tests for Circuit Breaker Service

Covers:
- CircuitBreakerService class
- State management
- Repository integration
- should_allow method
- get_state method

Refactored to use Factory Pattern (Phase 2):
- MockCircuitBreakerStateData → factories.MockCircuitBreakerStateData
- MockRepository → factories.InMemoryCircuitBreakerRepository
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Factory Pattern imports
from tests.factories import (
    TestDataFactory,
    MockCircuitBreakerStateData,
    InMemoryCircuitBreakerRepository,
)


class TestCircuitBreakerServiceInit:
    """Tests for CircuitBreakerService initialization."""
    
    def test_init_with_defaults(self):
        """Test initialization with default config."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.config is config
        assert service._repository is mock_repo
    
    def test_init_without_config(self):
        """Test initialization without config loads from settings."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(repository=mock_repo)
        
        assert service.config is not None
    
    def test_is_enabled_property(self):
        """Test is_enabled property."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.is_enabled is True
    
    def test_is_enabled_false_by_default(self):
        """Test is_enabled is False by default."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.is_enabled is False


class TestCircuitBreakerStateQuery:
    """Tests for state query operations."""
    
    def test_get_or_create_state(self):
        """Test get_or_create_state creates new state."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        state = service.get_or_create_state("new_service")
        
        assert state.service_name == "new_service"
        assert state.state == "closed"
    
    def test_get_state(self):
        """Test get_state returns current state."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        state = service.get_state("test_service")
        assert state == "closed"
    
    def test_get_state_existing_service(self):
        """Test get_state for existing service."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        
        # Pre-populate with open state
        mock_repo._states["test_service"] = MockCircuitBreakerStateData(
            service_name="test_service",
            state="open"
        )
        
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        state = service.get_state("test_service")
        assert state == "open"


class TestShouldAllow:
    """Tests for should_allow method."""
    
    def test_should_allow_when_closed(self):
        """Test should_allow returns True when closed."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.should_allow("test_service") is True
    
    def test_should_allow_when_open(self):
        """Test should_allow returns False when open."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        mock_repo._states["test_service"] = MockCircuitBreakerStateData(
            service_name="test_service",
            state="open"
        )
        
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.should_allow("test_service") is False
    
    def test_should_allow_when_disabled(self):
        """Test should_allow returns True when CB disabled."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=False)
        mock_repo = InMemoryCircuitBreakerRepository()
        mock_repo._states["test_service"] = MockCircuitBreakerStateData(
            service_name="test_service",
            state="open"
        )
        
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        # When disabled, should always allow
        assert service.should_allow("test_service") is True
    
    def test_should_allow_half_open(self):
        """Test should_allow behavior when half-open."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        mock_repo._states["test_service"] = MockCircuitBreakerStateData(
            service_name="test_service",
            state="half_open"
        )
        
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        # Half-open should allow limited requests
        result = service.should_allow("test_service")
        assert isinstance(result, bool)


class TestRepositoryProperty:
    """Tests for repository property."""
    
    def test_repository_returns_injected(self):
        """Test repository property returns injected repository."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        assert service.repository is mock_repo
    
    def test_repository_lazy_creates_default(self):
        """Test repository creates default when not injected."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig()
        
        # Mock the factory/registry to avoid DB connection
        with patch('selfhealing.factory.ProviderRegistry') as mock_registry:
            mock_repo = InMemoryCircuitBreakerRepository()
            mock_registry.get_circuit_breaker_repo.return_value = mock_repo
            
            service = CircuitBreakerService(config=config)
            repo = service.repository
            
            assert repo is not None


class TestCircuitBreakerServiceIntegration:
    """Integration-style tests for CircuitBreakerService."""
    
    def test_full_lifecycle(self):
        """Test full circuit breaker lifecycle."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        # Initially closed
        assert service.get_state("test_service") == "closed"
        assert service.should_allow("test_service") is True
        
        # Force open (mocking system control)
        with patch('selfhealing.services.circuit_breaker.manual_control._is_system_enabled', return_value=True):
            result = service.force_open(
                service_name="test_service",
                reason="Test",
                controlled_by_id=1,
            )
            assert result.success is True
        
        # Now should be open
        assert service.get_state("test_service") == "open"
        assert service.should_allow("test_service") is False
    
    def test_multiple_services_independent(self):
        """Test multiple services are independent."""
        from selfhealing.services.circuit_breaker.service import CircuitBreakerService
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        config = CircuitBreakerConfig(enabled=True)
        mock_repo = InMemoryCircuitBreakerRepository()
        service = CircuitBreakerService(config=config, repository=mock_repo)
        
        # Open service A
        mock_repo._states["service_a"] = MockCircuitBreakerStateData(
            service_name="service_a",
            state="open"
        )
        
        # Service B should still be closed
        assert service.get_state("service_a") == "open"
        assert service.get_state("service_b") == "closed"
        
        assert service.should_allow("service_a") is False
        assert service.should_allow("service_b") is True
