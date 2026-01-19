"""
Circuit Breaker Stale Cache 통합 테스트.

Test Coverage:
- CanaryWithStaleCacheService: Canary + Stale Cache 결합
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

from selfhealing.services.circuit_breaker.models import (
    ServiceConfig,
    RecoveryStrategy,
    CanaryStage,
)


# =============================================================================
# 4.2 CanaryWithStaleCacheService Tests
# =============================================================================


class TestStaleCacheStore:
    """StaleCacheStore 테스트."""
    
    def test_set_and_get(self):
        """캐시 저장 및 조회."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )
        
        store = StaleCacheStore()
        store.set("key1", {"data": "value"}, ttl_seconds=300)
        
        entry = store.get("key1", max_stale_age=300)
        
        assert entry is not None
        assert entry.value == {"data": "value"}
    
    def test_get_nonexistent_returns_none(self):
        """없는 키 조회 시 None 반환."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )
        
        store = StaleCacheStore()
        
        entry = store.get("nonexistent")
        
        assert entry is None
    
    def test_stale_detection(self):
        """Stale 상태 감지."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheEntry,
        )
        
        entry = StaleCacheEntry(
            key="key1",
            value="data",
            ttl_seconds=1,
        )
        # TTL 초과 시뮬레이션
        entry.cached_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        
        assert entry.is_stale() is True
    
    def test_cache_stats(self):
        """캐시 통계."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            StaleCacheStore,
        )
        
        store = StaleCacheStore()
        store.set("key1", "value1")
        store.get("key1")
        store.get("key2")  # miss
        
        stats = store.get_stats()
        
        assert stats["sets"] == 1
        assert stats["hits"] >= 1 or stats["stale_hits"] >= 1
        assert stats["misses"] >= 1


class TestCanaryWithStaleCacheService:
    """CanaryWithStaleCacheService 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        reset_canary_stale_cache_service()
        reset_canary_recovery_manager()
    
    def test_closed_state_allows_backend(self):
        """CLOSED 상태에서 백엔드 허용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        service = get_canary_stale_cache_service()
        
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="closed",
        )
        
        assert decision.allow_backend is True
        assert decision.use_stale is False
    
    def test_open_state_uses_stale_cache(self):
        """OPEN 상태에서 Stale Cache 사용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        service = get_canary_stale_cache_service()
        service.update_cache("payment:123", {"amount": 100}, "payment-api")
        
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="open",
        )
        
        assert decision.allow_backend is False
        assert decision.use_stale is True
        assert decision.stale_data == {"amount": 100}
    
    def test_open_state_rejects_without_cache(self):
        """OPEN 상태에서 캐시 없으면 거부."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        service = get_canary_stale_cache_service()
        
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:unknown",
            cb_state="open",
        )
        
        assert decision.allow_backend is False
        assert decision.reject is True
    
    def test_half_open_canary_request(self):
        """HALF_OPEN 상태에서 Canary 요청."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()
        
        # Canary 복구 시작 (100% 트래픽으로 설정)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=100.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        manager.start_canary_recovery("payment-api", strategy)
        
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="half_open",
        )
        
        # 100% 트래픽이므로 항상 canary
        assert decision.allow_backend is True
        assert decision.is_canary_request is True
    
    def test_half_open_non_canary_uses_stale(self):
        """HALF_OPEN에서 non-canary 요청은 Stale Cache 사용."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()
        
        # 캐시 설정
        service.update_cache("payment:123", {"amount": 100}, "payment-api")
        
        # 0% 트래픽 Canary (모든 요청이 non-canary)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=0.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        manager.start_canary_recovery("payment-api", strategy)
        
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:123",
            cb_state="half_open",
        )
        
        # 0% 트래픽이므로 모두 stale cache 사용
        assert decision.allow_backend is False
        assert decision.use_stale is True
        assert decision.stale_data == {"amount": 100}
    
    def test_record_success_updates_canary(self):
        """성공 기록이 Canary 매니저에 전달됨."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        service = get_canary_stale_cache_service()
        manager = get_canary_recovery_manager()
        
        manager.start_canary_recovery("payment-api")
        service.record_success("payment-api")
        
        state = manager.get_recovery_state("payment-api")
        assert state.metrics.success_count == 1
    
    def test_get_stats(self):
        """통합 통계 조회."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        service = get_canary_stale_cache_service()
        
        stats = service.get_stats()
        
        assert "canary_allowed" in stats
        assert "stale_served" in stats
        assert "cache_stats" in stats
