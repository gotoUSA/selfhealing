"""
HashChainDegradationManager 테스트.

Unified degradation level management 테스트.
"""

import pytest


class TestHashChainDegradationManager:
    """Tests for HashChainDegradationManager."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        from selfhealing.audit.graceful_degradation import HashChainDegradationManager
        HashChainDegradationManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        from selfhealing.audit.graceful_degradation import HashChainDegradationManager
        HashChainDegradationManager.reset_instance()
    
    def test_singleton_pattern(self, mock_redis):
        """Test singleton pattern works."""
        from selfhealing.audit.graceful_degradation import HashChainDegradationManager
        
        manager1 = HashChainDegradationManager(redis_client=mock_redis)
        manager2 = HashChainDegradationManager()
        
        assert manager1 is manager2
    
    def test_initial_level(self, mock_redis):
        """Test initial degradation level."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        assert manager.level == DegradationLevel.NORMAL
        assert manager.is_degraded is False
    
    def test_initial_level_without_redis(self):
        """Test initial level is DEGRADED without Redis."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=None)
        
        assert manager.level == DegradationLevel.DEGRADED
        assert manager.is_degraded is True
    
    def test_set_level(self, mock_redis):
        """Test setting degradation level."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.set_level(DegradationLevel.DEGRADED, "test")
        
        assert manager.level == DegradationLevel.DEGRADED
    
    def test_on_redis_failure(self, mock_redis):
        """Test Redis failure handling."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.on_redis_failure(ConnectionError("test"))
        
        assert manager.level == DegradationLevel.DEGRADED
        assert manager._failure_count == 1
    
    def test_repeated_failures_escalate(self, mock_redis):
        """Test repeated failures escalate to emergency."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        # Trigger many failures
        for _ in range(15):
            manager.on_redis_failure()
        
        assert manager.level == DegradationLevel.EMERGENCY
    
    def test_on_redis_recovery(self, mock_redis):
        """Test Redis recovery handling."""
        from selfhealing.audit.graceful_degradation import (
            HashChainDegradationManager,
            DegradationLevel,
        )
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        manager.on_redis_failure()
        assert manager.level == DegradationLevel.DEGRADED
        
        manager.on_redis_recovery()
        
        assert manager.level == DegradationLevel.NORMAL
        assert manager._failure_count == 0
    
    def test_callbacks(self, mock_redis):
        """Test degradation/recovery callbacks."""
        from selfhealing.audit.graceful_degradation import HashChainDegradationManager
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        degradation_called = []
        recovery_called = []
        
        manager.register_on_degradation(lambda l: degradation_called.append(l))
        manager.register_on_recovery(lambda l: recovery_called.append(l))
        
        manager.on_redis_failure()
        assert len(degradation_called) == 1
        
        manager.on_redis_recovery()
        assert len(recovery_called) == 1
    
    def test_get_status(self, mock_redis):
        """Test status retrieval."""
        from selfhealing.audit.graceful_degradation import HashChainDegradationManager
        
        manager = HashChainDegradationManager(redis_client=mock_redis)
        
        status = manager.get_status()
        
        assert status["level"] == "normal"
        assert status["is_degraded"] is False
        assert "failure_count" in status
