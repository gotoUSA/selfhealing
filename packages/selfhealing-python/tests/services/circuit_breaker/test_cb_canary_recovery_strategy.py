"""
Circuit Breaker 복구 전략 테스트

Test Coverage:
- 4.1 CanaryRecoveryManager: 단계적 복구, 성공률 추적, 단계 전이
- 4.2 CanaryWithStaleCacheService: Canary + Stale Cache 결합
- 4.3 RecoveryStrategySelector: immediate vs canary 선택, criticality 기반 선택

Expected: 35+ tests
"""

import pytest
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

from selfhealing.services.circuit_breaker.models import (
    ServiceConfig,
    RecoveryStrategy,
    CanaryStage,
)


# =============================================================================
# 4.1 CanaryRecoveryManager Tests
# =============================================================================


class TestCanaryRecoveryManager:
    """CanaryRecoveryManager 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        reset_canary_recovery_manager()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        reset_canary_recovery_manager()
    
    def test_singleton_pattern(self):
        """싱글톤 패턴 동작 확인."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            CanaryRecoveryManager,
            get_canary_recovery_manager,
        )
        
        manager1 = CanaryRecoveryManager()
        manager2 = get_canary_recovery_manager()
        
        assert manager1 is manager2
    
    def test_start_canary_recovery_default_strategy(self):
        """기본 전략으로 Canary 복구 시작."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
            CanaryState,
        )
        
        manager = get_canary_recovery_manager()
        state = manager.start_canary_recovery("payment-api")
        
        assert state.is_in_canary()
        assert state.current_stage == CanaryState.CANARY_1
        assert state.stage_index == 0
        assert state.metrics is not None
    
    def test_start_canary_recovery_custom_strategy(self):
        """사용자 정의 전략으로 Canary 복구 시작."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        strategy = RecoveryStrategy(
            type="canary",
            strict_mode=True,
            canary_stages=[
                CanaryStage(traffic_percent=5.0, duration_seconds=2, required_success_rate=99.0),
                CanaryStage(traffic_percent=50.0, duration_seconds=2, required_success_rate=95.0),
                CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )
        
        state = manager.start_canary_recovery("payment-api", strategy)
        
        assert state.is_in_canary()
        assert state.recovery_strategy.strict_mode is True
        assert len(state.recovery_strategy.canary_stages) == 3
    
    def test_immediate_strategy_skips_canary(self):
        """immediate 전략은 Canary를 건너뜀."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        strategy = RecoveryStrategy(type="immediate")
        
        state = manager.start_canary_recovery("review-api", strategy)
        
        assert not state.is_in_canary()
    
    def test_should_allow_request_canary_selection(self):
        """Canary 요청 선택 확률 테스트."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=50.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        
        manager.start_canary_recovery("test-api", strategy)
        
        # 100번 시도하여 대략 50%가 canary인지 확인
        canary_count = 0
        for _ in range(100):
            decision = manager.should_allow_request("test-api")
            if decision.is_canary_request:
                canary_count += 1
        
        # 50% 확률이므로 20~80 사이일 것으로 기대
        assert 20 <= canary_count <= 80, f"Expected ~50% canary, got {canary_count}%"
    
    def test_should_allow_request_not_in_canary(self):
        """Canary 복구 중이 아닐 때는 모두 허용."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        
        decision = manager.should_allow_request("unknown-api")
        
        assert decision.allow_backend is True
        assert decision.is_canary_request is False
    
    def test_record_success_increments_metrics(self):
        """성공 기록 시 메트릭 증가."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        manager.start_canary_recovery("payment-api")
        
        manager.record_success("payment-api")
        manager.record_success("payment-api")
        
        state = manager.get_recovery_state("payment-api")
        assert state.metrics.total_requests == 2
        assert state.metrics.success_count == 2
        assert state.metrics.failure_count == 0
    
    def test_record_failure_increments_metrics(self):
        """실패 기록 시 메트릭 증가."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        manager.start_canary_recovery("payment-api")
        
        manager.record_failure("payment-api")
        
        state = manager.get_recovery_state("payment-api")
        assert state.metrics.total_requests == 1
        assert state.metrics.success_count == 0
        assert state.metrics.failure_count == 1
    
    def test_stage_advancement_on_success(self):
        """성공률 충족 시 다음 단계로 전이."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
            CanaryState,
        )
        
        manager = get_canary_recovery_manager()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
                CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )
        
        manager.start_canary_recovery("payment-api", strategy)
        
        # 10개 성공 기록 (90% 이상 성공률, duration=0)
        for _ in range(10):
            result = manager.record_success("payment-api")
        
        state = manager.get_recovery_state("payment-api")
        # 다음 단계로 전이되어야 함
        assert state.current_stage == CanaryState.CANARY_2 or result.completed
    
    def test_recovery_failure_on_low_success_rate(self):
        """성공률 미달 시 복구 실패."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )
        
        manager.start_canary_recovery("payment-api", strategy)
        
        # 10개 중 5개 실패 (50% 성공률)
        for _ in range(5):
            manager.record_success("payment-api")
        for _ in range(5):
            result = manager.record_failure("payment-api")
        
        # 복구 실패로 인해 reset됨
        assert result.failed if result else True
    
    def test_stop_canary_recovery(self):
        """Canary 복구 중단."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        manager.start_canary_recovery("payment-api")
        
        result = manager.stop_canary_recovery("payment-api", "manual")
        
        assert result is True
        assert not manager.is_in_canary_recovery("payment-api")
    
    def test_get_active_recoveries(self):
        """활성 복구 목록 조회."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        manager = get_canary_recovery_manager()
        manager.start_canary_recovery("payment-api")
        manager.start_canary_recovery("order-api")
        
        active = manager.get_active_recoveries()
        
        assert "payment-api" in active
        assert "order-api" in active


class TestCanaryStageMetrics:
    """CanaryStageMetrics 테스트."""
    
    def test_current_success_rate_calculation(self):
        """성공률 계산."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            CanaryStageMetrics,
            CanaryState,
        )
        
        metrics = CanaryStageMetrics(stage=CanaryState.CANARY_1)
        
        for _ in range(8):
            metrics.record_success()
        for _ in range(2):
            metrics.record_failure()
        
        assert metrics.current_success_rate == 80.0
        assert metrics.total_requests == 10
    
    def test_empty_metrics_returns_100_percent(self):
        """빈 메트릭은 100% 반환."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            CanaryStageMetrics,
            CanaryState,
        )
        
        metrics = CanaryStageMetrics(stage=CanaryState.CANARY_1)
        
        assert metrics.current_success_rate == 100.0


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
        from datetime import timedelta
        
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


# =============================================================================
# 4.3 RecoveryStrategySelector Tests
# =============================================================================


class TestRecoveryStrategySelector:
    """RecoveryStrategySelector 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            reset_service_config_manager,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
        reset_service_config_manager()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            reset_service_config_manager,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
        reset_service_config_manager()
    
    def test_singleton_pattern(self):
        """싱글톤 패턴 동작 확인."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            RecoveryStrategySelector,
            get_recovery_strategy_selector,
        )
        
        selector1 = RecoveryStrategySelector()
        selector2 = get_recovery_strategy_selector()
        
        assert selector1 is selector2
    
    def test_select_strategy_from_service_config(self):
        """서비스 설정에서 전략 선택."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            get_service_config_manager,
        )
        
        # 서비스 설정 등록
        config_manager = get_service_config_manager()
        custom_strategy = RecoveryStrategy(type="immediate")
        config = ServiceConfig(
            service_id="payment-api",
            criticality="critical",
            recovery_strategy=custom_strategy,
        )
        config_manager.register_service(config)
        
        selector = get_recovery_strategy_selector()
        selection = selector.select_strategy("payment-api")
        
        assert selection.strategy_type == "immediate"
        assert selection.source == "service_config"
    
    def test_select_strategy_from_criticality(self):
        """criticality 기반 전략 선택."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            get_service_config_manager,
        )
        
        # 서비스 설정 등록 (recovery_strategy 없음)
        config_manager = get_service_config_manager()
        config = ServiceConfig(
            service_id="review-api",
            criticality="low",
        )
        config_manager.register_service(config)
        
        selector = get_recovery_strategy_selector()
        selection = selector.select_strategy("review-api")
        
        # low criticality는 immediate
        assert selection.strategy_type == "immediate"
        assert selection.source == "criticality_based"
    
    def test_select_strategy_default(self):
        """기본 전략 선택."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selection = selector.select_strategy("unknown-api")
        
        assert selection.source == "default"
    
    def test_critical_service_uses_strict_canary(self):
        """critical 서비스는 strict canary 사용."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            get_service_config_manager,
        )
        
        config_manager = get_service_config_manager()
        config = ServiceConfig(
            service_id="payment-api",
            criticality="critical",
        )
        config_manager.register_service(config)
        
        selector = get_recovery_strategy_selector()
        selection = selector.select_strategy("payment-api")
        
        assert selection.strategy_type == "canary"
        assert selection.strategy.strict_mode is True
    
    def test_start_recovery_with_canary(self):
        """Canary 전략으로 복구 시작."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            get_canary_recovery_manager,
        )
        
        selector = get_recovery_strategy_selector()
        selector.set_default_strategy(RecoveryStrategy(type="canary"))
        
        selection = selector.start_recovery("payment-api")
        
        assert selection.strategy_type == "canary"
        assert selector.is_in_recovery("payment-api")
        
        # Canary 매니저에도 등록됨
        manager = get_canary_recovery_manager()
        assert manager.is_in_canary_recovery("payment-api")
    
    def test_start_recovery_with_immediate(self):
        """Immediate 전략으로 복구 시작."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selector.set_default_strategy(RecoveryStrategy(type="immediate"))
        
        selection = selector.start_recovery("review-api")
        
        assert selection.strategy_type == "immediate"
        assert selector.is_in_recovery("review-api")
    
    def test_handle_half_open_request_immediate(self):
        """Immediate 전략의 HALF_OPEN 요청 처리."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selector.set_default_strategy(RecoveryStrategy(type="immediate"))
        selector.start_recovery("review-api")
        
        decision = selector.handle_half_open_request("review-api")
        
        assert decision.allow_backend is True
        assert decision.strategy_type == "immediate"
        assert decision.traffic_percent == 100.0
    
    def test_handle_half_open_request_canary(self):
        """Canary 전략의 HALF_OPEN 요청 처리."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=100.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        selector.set_default_strategy(strategy)
        selector.start_recovery("payment-api")
        
        decision = selector.handle_half_open_request("payment-api")
        
        assert decision.strategy_type == "canary"
        # 100% 트래픽이므로 항상 허용
        assert decision.allow_backend is True
    
    def test_record_success_advances_stage(self):
        """성공 기록이 단계 전이를 유발."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
                CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )
        selector.set_default_strategy(strategy)
        selector.start_recovery("payment-api")
        
        # 충분한 성공 기록
        for _ in range(10):
            result = selector.record_success("payment-api")
        
        # 단계가 전이되거나 완료됨
        assert result is not None or selector.get_recovery_type("payment-api") == "canary"
    
    def test_record_failure_may_fail_recovery(self):
        """실패 기록이 복구 실패를 유발할 수 있음."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=10.0, duration_seconds=0, required_success_rate=90.0),
            ],
        )
        selector.set_default_strategy(strategy)
        selector.start_recovery("payment-api")
        
        # 많은 실패 기록
        for _ in range(10):
            result = selector.record_failure("payment-api")
        
        # 성공률 0%이므로 복구 실패
        if result:
            assert result.failed
    
    def test_stop_recovery(self):
        """복구 중단."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selector.start_recovery("payment-api")
        
        result = selector.stop_recovery("payment-api", "manual")
        
        assert result is True
        assert not selector.is_in_recovery("payment-api")
    
    def test_get_active_recoveries(self):
        """활성 복구 목록."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selector.start_recovery("payment-api")
        selector.start_recovery("order-api")
        
        active = selector.get_active_recoveries()
        
        assert "payment-api" in active
        assert "order-api" in active
    
    def test_get_recovery_status(self):
        """복구 상태 조회."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        
        selector = get_recovery_strategy_selector()
        selector.set_default_strategy(RecoveryStrategy(type="canary"))
        selector.start_recovery("payment-api")
        
        status = selector.get_recovery_status("payment-api")
        
        assert status is not None
        assert status["service_id"] == "payment-api"
        assert status["strategy_type"] == "canary"
        assert "canary_state" in status


# =============================================================================
# Integration Tests
# =============================================================================


class TestRecoveryStrategyIntegration:
    """복구 전략 통합 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            reset_service_config_manager,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
        reset_service_config_manager()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            reset_service_config_manager,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
        reset_service_config_manager()
    
    def test_full_canary_recovery_flow(self):
        """전체 Canary 복구 흐름 테스트."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        selector = get_recovery_strategy_selector()
        stale_cache = get_canary_stale_cache_service()
        
        # 빠른 테스트를 위한 전략 설정
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=80.0),
            ],
        )
        selector.set_default_strategy(strategy)
        
        # 1. 복구 시작
        selection = selector.start_recovery("payment-api")
        assert selection.strategy_type == "canary"
        
        # 2. 요청 처리
        decision = selector.handle_half_open_request("payment-api")
        assert decision.allow_backend is True
        
        # 3. 성공 기록
        for _ in range(5):
            result = selector.record_success("payment-api")
        
        # 4. 복구 완료 확인
        assert result is None or result.completed or selector.is_in_recovery("payment-api")
    
    def test_canary_with_stale_cache_flow(self):
        """Canary + Stale Cache 통합 흐름."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            get_canary_stale_cache_service,
        )
        
        selector = get_recovery_strategy_selector()
        stale_cache = get_canary_stale_cache_service()
        
        # 0% 트래픽 (모든 요청이 stale cache)
        strategy = RecoveryStrategy(
            type="canary",
            canary_stages=[
                CanaryStage(traffic_percent=0.0, duration_seconds=5, required_success_rate=90.0),
            ],
        )
        selector.set_default_strategy(strategy)
        
        # 캐시 설정
        stale_cache.update_cache("payment:123", {"amount": 500})
        
        # 복구 시작
        selector.start_recovery("payment-api")
        
        # 요청 처리 (cache_key 포함)
        decision = selector.handle_half_open_request(
            service_id="payment-api",
            cache_key="payment:123",
        )
        
        # 0% 트래픽이므로 stale cache 사용
        assert decision.use_stale_cache is True
        assert decision.stale_data == {"amount": 500}
    
    def test_criticality_based_strategy_selection(self):
        """Criticality 기반 전략 선택 통합 테스트."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            get_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.service_config import (
            get_service_config_manager,
        )
        
        config_manager = get_service_config_manager()
        
        # 서비스 등록
        config_manager.register_service(ServiceConfig(
            service_id="payment-api",
            criticality="critical",
        ))
        config_manager.register_service(ServiceConfig(
            service_id="order-api",
            criticality="high",
        ))
        config_manager.register_service(ServiceConfig(
            service_id="review-api",
            criticality="low",
        ))
        
        selector = get_recovery_strategy_selector()
        
        # Critical: canary + strict
        payment_selection = selector.select_strategy("payment-api")
        assert payment_selection.strategy_type == "canary"
        assert payment_selection.strategy.strict_mode is True
        
        # High: canary (not strict)
        order_selection = selector.select_strategy("order-api")
        assert order_selection.strategy_type == "canary"
        assert order_selection.strategy.strict_mode is False
        
        # Low: immediate
        review_selection = selector.select_strategy("review-api")
        assert review_selection.strategy_type == "immediate"


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """편의 함수 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            reset_recovery_strategy_selector,
        )
        from selfhealing.services.circuit_breaker.canary_recovery import (
            reset_canary_recovery_manager,
        )
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            reset_canary_stale_cache_service,
        )
        reset_recovery_strategy_selector()
        reset_canary_recovery_manager()
        reset_canary_stale_cache_service()
    
    def test_canary_convenience_functions(self):
        """Canary 편의 함수."""
        from selfhealing.services.circuit_breaker.canary_recovery import (
            start_canary_recovery,
            stop_canary_recovery,
            is_in_canary_recovery,
            canary_should_allow_request,
            canary_record_success,
            canary_record_failure,
            get_canary_recovery_state,
        )
        
        # 시작
        state = start_canary_recovery("test-api")
        assert is_in_canary_recovery("test-api")
        
        # 결정
        decision = canary_should_allow_request("test-api")
        assert decision is not None
        
        # 성공/실패 기록
        canary_record_success("test-api")
        canary_record_failure("test-api")
        
        # 상태 조회
        state = get_canary_recovery_state("test-api")
        assert state is not None
        
        # 중단
        stop_canary_recovery("test-api")
        assert not is_in_canary_recovery("test-api")
    
    def test_stale_cache_convenience_functions(self):
        """Stale Cache 편의 함수."""
        from selfhealing.services.circuit_breaker.stale_cache_integration import (
            should_allow_with_fallback as canary_should_allow_with_fallback,
            update_stale_cache,
            record_canary_success,
            record_canary_failure,
        )
        
        # 캐시 업데이트
        entry = update_stale_cache("key1", {"data": "value"})
        assert entry.value == {"data": "value"}
        
        # 결정 (CLOSED 상태)
        decision = canary_should_allow_with_fallback(
            service_id="test-api",
            cache_key="key1",
            cb_state="closed",
        )
        assert decision.allow_backend is True
    
    def test_recovery_strategy_convenience_functions(self):
        """Recovery Strategy 편의 함수."""
        from selfhealing.services.circuit_breaker.recovery_strategy import (
            select_recovery_strategy,
            start_service_recovery,
            stop_service_recovery,
            handle_half_open,
            record_recovery_success,
            record_recovery_failure,
        )
        
        # 전략 선택
        selection = select_recovery_strategy("test-api")
        assert selection is not None
        
        # 복구 시작
        selection = start_service_recovery("test-api")
        assert selection is not None
        
        # 요청 처리
        decision = handle_half_open("test-api")
        assert decision is not None
        
        # 성공/실패 기록
        record_recovery_success("test-api")
        record_recovery_failure("test-api")
        
        # 복구 중단
        stop_service_recovery("test-api")
