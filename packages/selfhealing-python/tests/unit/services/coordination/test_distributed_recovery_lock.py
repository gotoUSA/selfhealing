"""
Unit tests for Distributed Recovery Lock.

Tests:
- 락 획득/해제 기본 동작
- 중복 락 획득 방지
- 소유자 확인 후 해제 (원자성)
- TTL 연장 (하트비트)
- 컨텍스트 매니저
- InMemoryRecoveryLock (테스트용)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.3
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.coordination.distributed_recovery_lock import (
    DistributedRecoveryLock,
    InMemoryRecoveryLock,
    RecoveryLockError,
    get_distributed_recovery_lock,
    reset_distributed_recovery_lock,
)


class TestInMemoryRecoveryLock:
    """InMemoryRecoveryLock 테스트 (Redis 없이)."""

    @pytest.fixture
    def lock(self):
        """테스트용 인메모리 락."""
        mem_lock = InMemoryRecoveryLock()
        yield mem_lock
        mem_lock.clear()

    def test_acquire_lock_success(self, lock):
        """락 획득 성공."""
        result = lock.acquire("global", "session-001")
        
        assert result is True
        assert lock.is_locked("global") is True
        assert lock.get_lock_owner("global") == "session-001"

    def test_acquire_lock_already_locked(self, lock):
        """이미 잠긴 락은 획득 실패."""
        lock.acquire("global", "session-001")
        
        result = lock.acquire("global", "session-002")
        
        assert result is False
        assert lock.get_lock_owner("global") == "session-001"

    def test_release_lock_success(self, lock):
        """락 해제 성공."""
        lock.acquire("global", "session-001")
        
        result = lock.release("global", "session-001")
        
        assert result is True
        assert lock.is_locked("global") is False

    def test_release_lock_wrong_owner(self, lock):
        """다른 소유자는 락 해제 불가."""
        lock.acquire("global", "session-001")
        
        result = lock.release("global", "session-002")
        
        assert result is False
        assert lock.is_locked("global") is True

    def test_release_nonexistent_lock(self, lock):
        """없는 락 해제는 실패."""
        result = lock.release("global", "session-001")
        
        assert result is False

    def test_namespace_isolation(self, lock):
        """네임스페이스별 독립적인 락."""
        lock.acquire("global", "session-001")
        lock.acquire("seoul", "session-002")
        
        assert lock.get_lock_owner("global") == "session-001"
        assert lock.get_lock_owner("seoul") == "session-002"

    def test_extend_lock(self, lock):
        """락 연장 (테스트용, 소유자 확인만)."""
        lock.acquire("global", "session-001")
        
        result = lock.extend("global", "session-001")
        
        assert result is True

    def test_extend_lock_wrong_owner(self, lock):
        """다른 소유자는 락 연장 불가."""
        lock.acquire("global", "session-001")
        
        result = lock.extend("global", "session-002")
        
        assert result is False

    def test_context_manager_acquired(self, lock):
        """컨텍스트 매니저로 락 획득 및 자동 해제."""
        with lock.lock_for_recovery("global", "session-001") as acquired:
            assert acquired is True
            assert lock.is_locked("global") is True
        
        # with 블록 후 자동 해제
        assert lock.is_locked("global") is False

    def test_context_manager_already_locked(self, lock):
        """컨텍스트 매니저에서 락 획득 실패."""
        lock.acquire("global", "session-001")
        
        with lock.lock_for_recovery("global", "session-002") as acquired:
            assert acquired is False
            # 원래 락은 유지됨
            assert lock.get_lock_owner("global") == "session-001"

    def test_clear_all_locks(self, lock):
        """모든 락 클리어."""
        lock.acquire("global", "session-001")
        lock.acquire("seoul", "session-002")
        
        lock.clear()
        
        assert lock.is_locked("global") is False
        assert lock.is_locked("seoul") is False


class TestDistributedRecoveryLock:
    """DistributedRecoveryLock 테스트 (Mock Redis)."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        redis = MagicMock()
        return redis

    @pytest.fixture
    def lock(self, mock_redis):
        """테스트용 분산 락."""
        return DistributedRecoveryLock(redis_client=mock_redis)

    def test_acquire_lock_success(self, lock, mock_redis):
        """락 획득 성공."""
        mock_redis.set.return_value = True
        
        result = lock.acquire("global", "session-001")
        
        assert result is True
        mock_redis.set.assert_called_once()
        call_kwargs = mock_redis.set.call_args.kwargs
        assert call_kwargs["nx"] is True
        assert "px" in call_kwargs

    def test_acquire_lock_already_locked(self, lock, mock_redis):
        """이미 잠긴 락은 획득 실패."""
        mock_redis.set.return_value = None
        mock_redis.get.return_value = "session-001"
        
        result = lock.acquire("global", "session-002")
        
        assert result is False

    def test_release_lock_success(self, lock, mock_redis):
        """락 해제 성공 (Lua 스크립트)."""
        mock_redis.eval.return_value = 1
        
        result = lock.release("global", "session-001")
        
        assert result is True
        mock_redis.eval.assert_called_once()

    def test_release_lock_wrong_owner(self, lock, mock_redis):
        """다른 소유자는 락 해제 불가."""
        mock_redis.eval.return_value = 0
        
        result = lock.release("global", "session-002")
        
        assert result is False

    def test_extend_lock_success(self, lock, mock_redis):
        """락 TTL 연장 성공."""
        mock_redis.eval.return_value = 1
        
        result = lock.extend("global", "session-001")
        
        assert result is True
        # eval 호출 시 EXTEND_SCRIPT 사용
        mock_redis.eval.assert_called_once()

    def test_extend_lock_custom_seconds(self, lock, mock_redis):
        """커스텀 TTL로 락 연장."""
        mock_redis.eval.return_value = 1
        
        result = lock.extend("global", "session-001", additional_seconds=600)
        
        assert result is True
        # 600초 = 600000ms
        call_args = mock_redis.eval.call_args
        assert "600000" in str(call_args)

    def test_get_lock_owner(self, lock, mock_redis):
        """락 소유자 조회."""
        mock_redis.get.return_value = "session-001"
        
        owner = lock.get_lock_owner("global")
        
        assert owner == "session-001"

    def test_get_lock_owner_no_lock(self, lock, mock_redis):
        """락 없을 때 소유자는 None."""
        mock_redis.get.return_value = None
        
        owner = lock.get_lock_owner("global")
        
        assert owner is None

    def test_is_locked_true(self, lock, mock_redis):
        """락 존재 확인."""
        mock_redis.get.return_value = "session-001"
        
        assert lock.is_locked("global") is True

    def test_is_locked_false(self, lock, mock_redis):
        """락 없음 확인."""
        mock_redis.get.return_value = None
        
        assert lock.is_locked("global") is False

    def test_get_lock_ttl(self, lock, mock_redis):
        """락 TTL 조회."""
        mock_redis.ttl.return_value = 1800  # 30분
        
        ttl = lock.get_lock_ttl("global")
        
        assert ttl == 1800

    def test_get_lock_ttl_no_lock(self, lock, mock_redis):
        """락 없을 때 TTL은 None."""
        mock_redis.ttl.return_value = -2  # 키 없음
        
        ttl = lock.get_lock_ttl("global")
        
        assert ttl is None

    def test_lock_key_format(self, lock, mock_redis):
        """락 키 형식 확인."""
        mock_redis.set.return_value = True
        
        lock.acquire("global", "session-001")
        
        # 키는 selfhealing:{namespace}:recovery:lock 형식
        call_args = mock_redis.set.call_args
        key = call_args[0][0]
        assert "selfhealing:global:recovery:lock" in key

    def test_context_manager_with_redis(self, lock, mock_redis):
        """컨텍스트 매니저 테스트."""
        mock_redis.set.return_value = True
        mock_redis.eval.return_value = 1
        
        with lock.lock_for_recovery("global", "session-001") as acquired:
            assert acquired is True
        
        # 해제 호출 확인
        mock_redis.eval.assert_called_once()


class TestDistributedRecoveryLockErrorHandling:
    """에러 처리 테스트."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        return MagicMock()

    @pytest.fixture
    def lock(self, mock_redis):
        """테스트용 분산 락."""
        return DistributedRecoveryLock(redis_client=mock_redis)

    def test_release_redis_error(self, lock, mock_redis):
        """Redis 에러 시 해제 실패."""
        mock_redis.eval.side_effect = Exception("Redis connection error")
        
        result = lock.release("global", "session-001")
        
        assert result is False

    def test_extend_redis_error(self, lock, mock_redis):
        """Redis 에러 시 연장 실패."""
        mock_redis.eval.side_effect = Exception("Redis connection error")
        
        result = lock.extend("global", "session-001")
        
        assert result is False

    def test_get_owner_redis_error(self, lock, mock_redis):
        """Redis 에러 시 소유자는 None."""
        mock_redis.get.side_effect = Exception("Redis connection error")
        
        owner = lock.get_lock_owner("global")
        
        assert owner is None

    def test_get_ttl_redis_error(self, lock, mock_redis):
        """Redis 에러 시 TTL은 None."""
        mock_redis.ttl.side_effect = Exception("Redis connection error")
        
        ttl = lock.get_lock_ttl("global")
        
        assert ttl is None


class TestRecoveryLockError:
    """RecoveryLockError 예외 테스트."""

    def test_error_with_message(self):
        """에러 메시지."""
        error = RecoveryLockError("Lock already held")
        
        assert str(error) == "Lock already held"

    def test_error_with_namespace(self):
        """네임스페이스 포함."""
        error = RecoveryLockError(
            "Lock already held",
            namespace="global",
            current_owner="session-001",
        )
        
        assert error.namespace == "global"
        assert error.current_owner == "session-001"


class TestSingletonFactory:
    """싱글톤 팩토리 테스트."""

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        reset_distributed_recovery_lock()
        
        # 리셋 후 새 인스턴스 생성 시도
        # (실제 Redis 없으므로 에러 발생할 수 있음)
        reset_distributed_recovery_lock()

    def test_get_singleton_with_mock(self):
        """Mock으로 싱글톤 테스트."""
        reset_distributed_recovery_lock()
        
        # StateBackend를 mock하여 Redis 클라이언트 제공
        with patch("selfhealing.core.state_backend.get_state_backend") as mock_backend:
            mock_backend_instance = MagicMock()
            mock_backend_instance._client = MagicMock()
            mock_backend.return_value = mock_backend_instance
            
            lock = get_distributed_recovery_lock()
            
            assert lock is not None
        
        reset_distributed_recovery_lock()
