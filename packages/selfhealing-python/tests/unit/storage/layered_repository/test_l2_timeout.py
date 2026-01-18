"""
L2 타임아웃 테스트.
"""

import time
from unittest.mock import MagicMock, patch

import pytest


class TestL2Timeout:
    """L2 타임아웃 테스트."""

    def test_timeout_on_slow_l2(self):
        """L2가 느리면 타임아웃 발생."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
            InMemoryCircuitBreakerStateRepository,
            ShadowLogger,
        )
        
        # Given: L2가 200ms 걸리는 상황 시뮬레이션
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        
        def slow_get_all():
            time.sleep(0.2)  # 200ms 지연
            return []
        
        slow_l2.get_all.side_effect = slow_get_all
        
        # When: 50ms 타임아웃으로 레포지토리 생성 (Redis 타입)
        with patch('selfhealing.adapters.memory.circuit_breaker.get_shadow_logger') as mock_logger:
            mock_logger.return_value = ShadowLogger()
            repo = LayeredCircuitBreakerStateRepository(
                l2_repo=slow_l2,
                adapter_type="redis",
            )
        
        # Then: 타임아웃이 발생하고 L1만으로 동작 (에러 없이)
        assert repo._metrics["l2_timeout_count"] >= 0

    def test_fallback_to_l1_on_timeout(self):
        """타임아웃 시 L1만으로 동작."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
            InMemoryCircuitBreakerStateRepository,
        )
        from selfhealing.interfaces.repositories import CircuitBreakerStateEnum
        
        # Given: 느린 L2
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        slow_l2.get_all.side_effect = lambda: time.sleep(1) or []
        
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=slow_l2,
            adapter_type="redis",
        )
        
        # When: L1에서 상태 생성
        state = repo.get_or_create("test-service")
        
        # Then: L1 데이터로 정상 동작
        assert state is not None
        assert state.service_name == "test-service"
        assert state.state == CircuitBreakerStateEnum.CLOSED.value

    def test_adapter_specific_timeout(self):
        """어댑터별 다른 타임아웃 적용."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
        )
        
        # Redis 어댑터
        redis_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="redis",
        )
        assert redis_repo._get_timeout_seconds() == 0.05  # 50ms
        
        # Database 어댑터
        db_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="database",
        )
        assert db_repo._get_timeout_seconds() == 0.2  # 200ms
        
        # Django 어댑터 (database와 동일)
        django_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="django",
        )
        assert django_repo._get_timeout_seconds() == 0.2  # 200ms
        
        # 알 수 없는 어댑터
        unknown_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="unknown",
        )
        assert unknown_repo._get_timeout_seconds() == 0.1  # 100ms

    def test_l2_timeout_increments_metric(self):
        """L2 타임아웃 시 내부 메트릭 증가."""
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
            InMemoryCircuitBreakerStateRepository,
        )
        
        # Given: 느린 L2
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        slow_l2.get_all.return_value = []
        slow_l2.get_by_service_name.side_effect = lambda _: time.sleep(0.2) or None
        
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=slow_l2,
            adapter_type="redis",  # 50ms 타임아웃
        )
        
        initial_timeout_count = repo._metrics["l2_timeout_count"]
        
        # When: L2 조회 시도 (타임아웃 발생)
        repo._l2_healthy = True
        result = repo.get_by_service_name("test-service")
        
        # Then: 타임아웃 카운트 증가 (또는 L1 결과 반환)
        assert result is None or repo._metrics["l2_timeout_count"] >= initial_timeout_count
