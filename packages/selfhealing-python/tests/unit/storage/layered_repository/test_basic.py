"""
LayeredRepository 기본 동작 테스트.
"""

import time
from unittest.mock import MagicMock

import pytest


class TestLayeredRepositoryBasic:
    """LayeredRepository 기본 동작 테스트."""

    def test_l1_always_returns_immediately(self):
        """L1은 항상 즉시 반환."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
        )
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=None)
        
        start = time.time()
        repo.get_or_create("test-service")
        elapsed = time.time() - start
        
        assert elapsed < 0.01, f"L1 조회가 너무 느림: {elapsed*1000:.2f}ms"

    def test_l2_sync_is_async(self):
        """L2 동기화는 비동기."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
            InMemoryCircuitBreakerStateRepository,
        )
        
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        def slow_update(*args, **kwargs):
            time.sleep(0.5)
            return True
        
        mock_l2.update_state.side_effect = slow_update
        mock_l2.get_or_create.side_effect = slow_update
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        start = time.time()
        repo.record_failure("test-service")
        elapsed = time.time() - start
        
        # L1 업데이트는 빠르게 완료되어야 함
        assert elapsed < 0.2, f"L1 업데이트가 너무 느림: {elapsed*1000:.2f}ms"

    def test_get_storage_info(self):
        """저장소 정보 조회."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
        )
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=None)
        info = repo.get_storage_info()
        
        assert "l1_type" in info
        assert "l2_enabled" in info
        assert info["l1_type"] is not None

    def test_get_storage_info_with_l2(self):
        """L2가 있을 때 저장소 정보 조회."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
            InMemoryCircuitBreakerStateRepository,
        )
        
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        info = repo.get_storage_info()
        
        assert info["l2_enabled"] is True
        assert "l2_healthy" in info
        assert "metrics" in info
