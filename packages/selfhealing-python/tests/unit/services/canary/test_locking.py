"""
Canary Config Lock 단위 테스트.

테스트 대상:
1. ConfigLockError - 락 획득 실패 예외
2. CanaryConfigLock.acquire() - 락 획득
3. CanaryConfigLock.release() - 락 해제
4. CanaryConfigLock.is_locked() - 락 상태 확인
5. CanaryConfigLock.get_lock_owner() - 소유자 조회

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.canary.locking import (
    ConfigLockError,
    CanaryConfigLock,
)


# =============================================================================
# Test: ConfigLockError
# =============================================================================


class TestConfigLockError:
    """ConfigLockError 예외 테스트."""

    def test_error_with_message_only(self):
        """메시지만 있는 에러."""
        error = ConfigLockError("Lock acquisition failed")
        assert str(error) == "Lock acquisition failed"
        assert error.config_type == ""
        assert error.current_owner is None

    def test_error_with_full_info(self):
        """전체 정보가 있는 에러."""
        error = ConfigLockError(
            "Lock already held",
            config_type="circuit_breaker",
            current_owner="rollout-abc123",
        )
        assert "Lock already held" in str(error)
        assert error.config_type == "circuit_breaker"
        assert error.current_owner == "rollout-abc123"


# =============================================================================
# Test: CanaryConfigLock
# =============================================================================


class TestCanaryConfigLock:
    """CanaryConfigLock 테스트."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis 클라이언트."""
        redis = MagicMock()
        return redis

    @pytest.fixture
    def config_lock(self, mock_redis):
        """CanaryConfigLock 인스턴스."""
        return CanaryConfigLock(mock_redis)

    # -------------------------------------------------------------------------
    # acquire() 테스트
    # -------------------------------------------------------------------------

    def test_acquire_success(self, config_lock, mock_redis):
        """락 획득 성공."""
        mock_redis.set.return_value = True  # SET NX 성공

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.acquire("circuit_breaker", "rollout-123")

        assert result is True
        mock_redis.set.assert_called_once()

    def test_acquire_failure_already_locked(self, config_lock, mock_redis):
        """이미 락이 있을 때 실패."""
        mock_redis.set.return_value = False  # SET NX 실패
        mock_redis.get.return_value = b"rollout-other"

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.acquire("circuit_breaker", "rollout-new")

        assert result is False

    def test_acquire_tracks_acquired_lock(self, config_lock, mock_redis):
        """획득한 락이 내부 상태에 추적됨."""
        mock_redis.set.return_value = True

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            config_lock.acquire("circuit_breaker", "rollout-123")

        assert config_lock._acquired_locks.get("circuit_breaker") == "rollout-123"

    # -------------------------------------------------------------------------
    # release() 테스트
    # -------------------------------------------------------------------------

    def test_release_success(self, config_lock, mock_redis):
        """락 해제 성공."""
        mock_redis.eval.return_value = 1  # Lua 스크립트 삭제 성공
        config_lock._acquired_locks["circuit_breaker"] = "rollout-123"

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.release("circuit_breaker", "rollout-123")

        assert result is True
        mock_redis.eval.assert_called_once()

    def test_release_failure_not_owner(self, config_lock, mock_redis):
        """소유자가 아닐 때 해제 실패."""
        mock_redis.eval.return_value = 0  # Lua 스크립트 실패

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.release("circuit_breaker", "wrong-owner")

        assert result is False

    def test_release_clears_tracked_lock(self, config_lock, mock_redis):
        """해제 후 내부 추적 상태가 정리됨."""
        mock_redis.eval.return_value = 1
        config_lock._acquired_locks["circuit_breaker"] = "rollout-123"

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            config_lock.release("circuit_breaker", "rollout-123")

        assert "circuit_breaker" not in config_lock._acquired_locks

    # -------------------------------------------------------------------------
    # is_locked() 테스트
    # -------------------------------------------------------------------------

    def test_is_locked_true(self, config_lock, mock_redis):
        """락이 있을 때 True."""
        mock_redis.exists.return_value = 1

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.is_locked("circuit_breaker")

        assert result is True

    def test_is_locked_false(self, config_lock, mock_redis):
        """락이 없을 때 False."""
        mock_redis.exists.return_value = 0

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            result = config_lock.is_locked("circuit_breaker")

        assert result is False

    # -------------------------------------------------------------------------
    # get_lock_owner() 테스트
    # -------------------------------------------------------------------------

    def test_get_lock_owner_returns_owner(self, config_lock, mock_redis):
        """소유자 ID 반환."""
        mock_redis.get.return_value = b"rollout-abc"

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            owner = config_lock.get_lock_owner("circuit_breaker")

        assert owner == "rollout-abc"

    def test_get_lock_owner_returns_none(self, config_lock, mock_redis):
        """락이 없을 때 None."""
        mock_redis.get.return_value = None

        with patch("selfhealing.settings.namespace.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            owner = config_lock.get_lock_owner("circuit_breaker")

        assert owner is None
