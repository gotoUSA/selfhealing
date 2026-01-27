"""
AtomicBudgetConsumer Unit Tests.

Redis Lock 기반 버짓 소진 원자성 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §0.1 (7번)
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.error_budget.atomic_consumer import (
    AtomicBudgetConsumer,
    AtomicConsumeResult,
    get_atomic_budget_consumer,
    reset_atomic_budget_consumer,
)


# =============================================================================
# AtomicConsumeResult Tests
# =============================================================================

class TestAtomicConsumeResult:
    """AtomicConsumeResult 테스트."""
    
    def test_default_values(self):
        """기본값 확인."""
        result = AtomicConsumeResult()
        
        assert result.success is False
        assert result.lock_acquired is False
        assert result.consumed_minutes == 0.0
        assert result.remaining_budget is None
        assert result.error_message is None
        assert result.degraded_mode is False
    
    def test_consume_id_generated(self):
        """consume_id 자동 생성."""
        result = AtomicConsumeResult()
        
        assert result.consume_id.startswith("consume_")
        assert len(result.consume_id) > 10
    
    def test_consumed_at_set(self):
        """consumed_at 자동 설정."""
        result = AtomicConsumeResult()
        
        assert result.consumed_at is not None


# =============================================================================
# AtomicBudgetConsumer Tests
# =============================================================================

class TestAtomicBudgetConsumer:
    """AtomicBudgetConsumer 테스트."""
    
    # -------------------------------------------------------------------------
    # Successful Consumption Tests
    # -------------------------------------------------------------------------
    
    def test_consume_atomic_success(self):
        """정상적인 원자적 소진."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # Lock 획득 성공
        mock_redis.get.return_value = b"10.0"  # 현재 소진량
        
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=5.0,
            budget_key="budget:test",
        )
        
        assert result.success is True
        assert result.lock_acquired is True
        assert result.consumed_minutes == 5.0
    
    def test_consume_calculates_weighted_minutes(self):
        """가중치 적용된 소진량 계산."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=2.0,
            multiplier=3.0,
            budget_key="budget:test",
        )
        
        assert result.consumed_minutes == 6.0  # 2.0 * 3.0
    
    # -------------------------------------------------------------------------
    # Lock Acquisition Tests
    # -------------------------------------------------------------------------
    
    def test_lock_acquired_flag(self):
        """Lock 획득 시 플래그 설정."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_redis.get.return_value = b"0"
        
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=1.0,
            budget_key="budget:test",
        )
        
        assert result.lock_acquired is True
    
    def test_lock_acquisition_retries(self):
        """Lock 획득 실패 시 재시도."""
        mock_redis = MagicMock()
        # 첫 번째, 두 번째 Lock 시도 실패, 세 번째 Lock 성공, 네 번째는 버짓 업데이트
        # nx=True인 경우만 False/True 반환, 그 외에는 None(정상)
        lock_attempt_results = [False, False, True]
        lock_attempt_idx = [0]
        
        def set_side_effect(*args, **kwargs):
            if kwargs.get('nx'):
                # Lock 획득 시도
                idx = lock_attempt_idx[0]
                lock_attempt_idx[0] += 1
                if idx < len(lock_attempt_results):
                    return lock_attempt_results[idx]
                return True
            # 일반 set (버짓 업데이트)
            return None
        
        mock_redis.set.side_effect = set_side_effect
        mock_redis.get.return_value = b"0"
        
        consumer = AtomicBudgetConsumer(
            redis_client=mock_redis,
            lock_retry_count=3,
            lock_retry_delay=0.01,
        )
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=1.0,
            budget_key="budget:test",
        )
        
        assert result.success is True
        # 3번 Lock 시도 + 1번 버짓 업데이트 = 최소 4번 호출
        assert mock_redis.set.call_count >= 3
    
    # -------------------------------------------------------------------------
    # Degraded Mode Tests
    # -------------------------------------------------------------------------
    
    def test_degraded_mode_on_no_redis(self):
        """Redis 없을 때 Degraded Mode."""
        consumer = AtomicBudgetConsumer(
            redis_client=None,
            allow_degraded_mode=True,
        )
        
        # _get_redis_client가 None 반환하도록 mock
        with patch.object(consumer, '_get_redis_client', return_value=None):
            result = consumer.consume_atomic(
                namespace="test",
                raw_minutes=1.0,
                multiplier=5.0,
                budget_key="budget:test",
            )
        
        assert result.success is True
        assert result.lock_acquired is False
        assert result.degraded_mode is True
        assert result.consumed_minutes == 5.0
    
    def test_degraded_mode_on_lock_failure(self):
        """Lock 획득 실패 시 Degraded Mode."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # Lock 획득 실패
        
        consumer = AtomicBudgetConsumer(
            redis_client=mock_redis,
            lock_retry_count=1,
            allow_degraded_mode=True,
        )
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=5.0,
            budget_key="budget:test",
        )
        
        assert result.success is True
        assert result.lock_acquired is False
        assert result.degraded_mode is True
    
    def test_degraded_mode_disabled_fails(self):
        """Degraded Mode 비활성화 시 실패."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = False
        
        consumer = AtomicBudgetConsumer(
            redis_client=mock_redis,
            lock_retry_count=1,
            allow_degraded_mode=False,  # 비활성화
        )
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=5.0,
            budget_key="budget:test",
        )
        
        assert result.success is False
        assert result.lock_acquired is False
        assert result.error_message is not None
    
    # -------------------------------------------------------------------------
    # Error Handling Tests
    # -------------------------------------------------------------------------
    
    def test_consume_error_handling(self):
        """소진 중 오류 처리."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_redis.get.side_effect = Exception("Redis error")
        
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        result = consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=1.0,
            budget_key="budget:test",
        )
        
        assert result.success is False
        assert "Redis error" in result.error_message
    
    def test_lock_release_on_success(self):
        """성공 후 Lock 해제."""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_redis.get.return_value = b"0"
        
        consumer = AtomicBudgetConsumer(redis_client=mock_redis)
        
        consumer.consume_atomic(
            namespace="test",
            raw_minutes=1.0,
            multiplier=1.0,
            budget_key="budget:test",
        )
        
        # eval 호출로 Lock 해제 확인
        assert mock_redis.eval.called


# =============================================================================
# Singleton Tests
# =============================================================================

class TestAtomicBudgetConsumerSingleton:
    """싱글톤 팩토리 테스트."""
    
    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_atomic_budget_consumer()
    
    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_atomic_budget_consumer()
    
    def test_get_returns_singleton(self):
        """get_atomic_budget_consumer는 같은 인스턴스 반환."""
        consumer1 = get_atomic_budget_consumer()
        consumer2 = get_atomic_budget_consumer()
        
        assert consumer1 is consumer2
    
    def test_reset_clears_singleton(self):
        """reset_atomic_budget_consumer는 싱글톤 초기화."""
        consumer1 = get_atomic_budget_consumer()
        
        reset_atomic_budget_consumer()
        
        consumer2 = get_atomic_budget_consumer()
        
        assert consumer2 is not consumer1
