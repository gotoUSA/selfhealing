"""
Redis Integration Test Example.

실제 Redis 연결을 사용하는 통합 테스트 패턴 예제.
Docker Compose로 실행: docker-compose -f docker-compose.test.yml up -d

이 패턴을 다른 통합 테스트에 적용할 수 있습니다.
"""

import pytest
from dataclasses import dataclass

from selfhealing.services import CircuitBreakerService
from selfhealing.services.circuit_breaker import CircuitBreakerConfig


@dataclass
class MockUser:
    """Mock user for admin operations."""
    id: int
    username: str
    is_staff: bool = False
    is_superuser: bool = False


@pytest.mark.requires_redis
@pytest.mark.tier2
class TestCircuitBreakerWithRealRedis:
    """
    Circuit Breaker tests with real Redis.
    
    Uses redis_circuit_breaker_repository fixture from tests/conftest.py.
    """
    
    def test_force_open_and_close_with_real_redis(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Verify CB force open/close works with real Redis.
            
        Scenario:
            1. Create CB service with Redis repository
            2. Force open
            3. Force close
            4. Verify state transitions in Redis
        """
        # Arrange
        repository = redis_circuit_breaker_repository
        config = CircuitBreakerConfig(enabled=True)
        service = CircuitBreakerService(config=config, repository=repository)
        admin_user = MockUser(id=1, username="admin", is_staff=True, is_superuser=True)
        service_name = "test_payment_service"
        
        # Act: Force open
        open_result = service.force_open(
            service_name=service_name,
            reason="Test maintenance",
            controlled_by=admin_user,
        )
        
        # Assert: State is open
        state = repository.get_state(service_name)
        assert state is not None
        assert state.state == "open"
        assert state.manually_controlled is True
        
        # Act: Force close
        close_result = service.force_close(
            service_name=service_name,
            reason="Test recovered",
            controlled_by=admin_user,
        )
        
        # Assert: State is closed
        state = repository.get_state(service_name)
        assert state.state == "closed"
    
    def test_failure_count_persists_in_redis(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Verify failure counts are persisted in Redis.
            
        This test ensures Redis is actually being used (not just memory).
        """
        # Arrange
        repository = redis_circuit_breaker_repository
        service_name = "test_failure_service"
        
        # Act: Increment failures multiple times
        repository.get_or_create(service_name)
        repository.increment_failure(service_name)
        repository.increment_failure(service_name)
        repository.increment_failure(service_name)
        
        # Assert: Count is persisted
        state = repository.get_state(service_name)
        assert state.failure_count == 3
    
    def test_multiple_services_isolated(self, redis_circuit_breaker_repository):
        """
        Purpose:
            Verify multiple services are isolated in Redis.
        """
        # Arrange
        repository = redis_circuit_breaker_repository
        service_a = "service_a"
        service_b = "service_b"
        
        # Act: Create different states
        repository.get_or_create(service_a)
        repository.get_or_create(service_b)
        repository.increment_failure(service_a)
        repository.increment_failure(service_a)
        repository.increment_success(service_b)
        
        # Assert: States are isolated
        state_a = repository.get_state(service_a)
        state_b = repository.get_state(service_b)
        
        assert state_a.failure_count == 2
        assert state_a.success_count == 0
        assert state_b.failure_count == 0
        assert state_b.success_count == 1


@pytest.mark.requires_redis
@pytest.mark.tier2  
class TestDLQWithRealRedis:
    """
    DLQ tests with real Redis.
    
    Uses redis_dlq_repository fixture from tests/conftest.py.
    """
    
    def test_create_and_get_failed_operation(self, redis_dlq_repository):
        """
        Purpose:
            Verify DLQ operations work with real Redis.
        """
        # Arrange
        repository = redis_dlq_repository
        
        # Act: Create failed operation
        entry = repository.create(
            domain="payment",
            failure_type="TIMEOUT",
            error_message="Connection timeout",
            error_code="CONN_TIMEOUT",
            entity_type="Order",
            entity_id="12345",
            snapshot_data={"order_total": 10000},
        )
        
        # Assert
        assert entry.id is not None
        assert entry.domain == "payment"
        assert entry.failure_type == "TIMEOUT"
        assert entry.status == "pending"
        
        # Get by ID
        retrieved = repository.get_by_id(entry.id)
        assert retrieved is not None
        assert retrieved.error_message == "Connection timeout"
    
    def test_get_pending_by_domain(self, redis_dlq_repository):
        """
        Purpose:
            Verify pending operations can be filtered by domain.
        """
        # Arrange
        repository = redis_dlq_repository
        
        # Create multiple entries
        repository.create(domain="payment", failure_type="TIMEOUT", error_message="test1")
        repository.create(domain="payment", failure_type="NETWORK", error_message="test2")
        repository.create(domain="shipping", failure_type="TIMEOUT", error_message="test3")
        
        # Act
        payment_entries = repository.get_pending_by_domain("payment")
        shipping_entries = repository.get_pending_by_domain("shipping")
        
        # Assert
        assert len(payment_entries) == 2
        assert len(shipping_entries) == 1
    
    def test_update_status_to_resolved(self, redis_dlq_repository):
        """
        Purpose:
            Verify status updates work with real Redis.
        """
        # Arrange
        repository = redis_dlq_repository
        entry = repository.create(
            domain="payment",
            failure_type="TIMEOUT",
            error_message="test",
        )
        
        # Act
        result = repository.update_status(
            id=entry.id,
            status="resolved",
            resolution_type="manual",
            resolution_note="Fixed by admin",
            resolved_by_id=1,
        )
        
        # Assert
        assert result is True
        updated = repository.get_by_id(entry.id)
        assert updated.status == "resolved"
        assert updated.resolution_type == "manual"
