"""
Redis 기반 Cooldown 저장소 단위 테스트.

대상: selfhealing/services/throttle/redis_cooldown_store.py
- RedisCooldownStore.is_cooled_down()
- RedisCooldownStore.mark_sent()
- RedisCooldownStore.clear()
- Redis 장애 시 메모리 폴백
"""

from __future__ import annotations

import time
from unittest.mock import ANY, MagicMock, patch

import pytest

from tests.unit.throttle.conftest import (
    DEFAULT_COOLDOWN_SECONDS,
    DEDUP_KEY_PAYMENT,
    KEY_PREFIX,
    make_redis_key,
)


class TestRedisCooldownStoreWithRedis:
    """Redis 클라이언트가 있는 경우의 쿨다운 테스트."""

    def test_not_cooled_down_initially(self, mock_redis_ok):
        """초기 상태는 쿨다운 아님."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_ok, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        assert not store.is_cooled_down(DEDUP_KEY_PAYMENT)

    def test_cooled_down_when_key_exists(self, mock_redis_exists):
        """Redis에 키가 존재하면 쿨다운 중."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_exists, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        assert store.is_cooled_down(DEDUP_KEY_PAYMENT)

    def test_mark_sent_calls_redis_set(self, mock_redis_ok):
        """mark_sent가 Redis SET + TTL 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_ok, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.mark_sent(DEDUP_KEY_PAYMENT)

        mock_redis_ok.set.assert_called_once_with(
            make_redis_key(DEDUP_KEY_PAYMENT),
            ANY,
            ex=DEFAULT_COOLDOWN_SECONDS,
        )

    def test_mark_sent_key_prefix_format(self, mock_redis_ok):
        """Redis 키가 KEY_PREFIX:dedup_key 형식."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_ok, cooldown_seconds=300)

        store.mark_sent("test:key")
        call_args = mock_redis_ok.set.call_args
        assert call_args[0][0] == make_redis_key("test:key")
        assert call_args[1]["ex"] == 300 or call_args[0][2] == 300

    def test_clear_calls_redis_delete(self, mock_redis_ok):
        """clear가 Redis DELETE 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_ok, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.clear(DEDUP_KEY_PAYMENT)
        mock_redis_ok.delete.assert_called_once_with(make_redis_key(DEDUP_KEY_PAYMENT))

    def test_clear_also_clears_memory(self, mock_redis_down):
        """clear가 메모리 캐시도 함께 비움."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_down, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)

        # 메모리에 기록
        store.mark_sent(DEDUP_KEY_PAYMENT)
        assert store.is_cooled_down(DEDUP_KEY_PAYMENT)

        # clear 후 메모리에서도 제거
        store.clear(DEDUP_KEY_PAYMENT)
        assert not store.is_cooled_down(DEDUP_KEY_PAYMENT)

    def test_is_cooled_down_checks_redis_key(self, mock_redis_ok):
        """is_cooled_down이 올바른 키로 Redis.exists 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_ok, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.is_cooled_down("test:abc")

        mock_redis_ok.exists.assert_called_once_with(make_redis_key("test:abc"))


class TestRedisCooldownStoreRedisFallback:
    """Redis 장애 시 메모리 폴백 테스트."""

    def test_redis_exists_failure_falls_back_to_memory(self, mock_redis_down):
        """Redis.exists 실패 시 메모리 폴백."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_down, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)

        assert not store.is_cooled_down(DEDUP_KEY_PAYMENT)
        store.mark_sent(DEDUP_KEY_PAYMENT)
        assert store.is_cooled_down(DEDUP_KEY_PAYMENT)

    def test_redis_set_failure_stores_in_memory(self, mock_redis_down):
        """Redis.set 실패 시 메모리에 저장."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_down, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)

        store.mark_sent("key1")
        # 메모리에 저장되었으므로 is_cooled_down이 True
        assert store.is_cooled_down("key1")

    def test_redis_delete_failure_silent(self, mock_redis_down):
        """Redis.delete 실패 시 에러 없이 진행."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=mock_redis_down, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        # 에러 없이 수행됨
        store.clear("key1")


class TestRedisCooldownStoreMemoryOnly:
    """Redis 미사용(None) 시 메모리 전용 동작 테스트."""

    def test_no_redis_initial_not_cooled(self):
        """Redis=None, 초기 상태 쿨다운 아님."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        assert not store.is_cooled_down(DEDUP_KEY_PAYMENT)

    def test_no_redis_mark_and_check(self):
        """Redis=None, mark → is_cooled 동작."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.mark_sent("key1")
        assert store.is_cooled_down("key1")

    def test_memory_cooldown_expires(self):
        """메모리 쿨다운 TTL 만료 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1)
        store.mark_sent("key1")
        assert store.is_cooled_down("key1")

        # time.time()을 1.1초 전진시켜 쿨다운 만료를 시뮬레이션
        import selfhealing.services.throttle.redis_cooldown_store as _cd_mod

        original_time = time.time()
        with patch.object(_cd_mod.time, "time", return_value=original_time + 1.1):
            assert not store.is_cooled_down("key1")

    def test_clear_removes_from_memory(self):
        """clear가 메모리에서 제거."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.mark_sent("key1")
        assert store.is_cooled_down("key1")

        store.clear("key1")
        assert not store.is_cooled_down("key1")

    def test_multiple_keys_independent(self):
        """각 dedup_key가 독립적으로 동작."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        store.mark_sent("key1")
        store.mark_sent("key2")

        assert store.is_cooled_down("key1")
        assert store.is_cooled_down("key2")
        assert not store.is_cooled_down("key3")

        store.clear("key1")
        assert not store.is_cooled_down("key1")
        assert store.is_cooled_down("key2")

    def test_clear_nonexistent_key_no_error(self):
        """존재하지 않는 키 clear 시 에러 없음."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS)
        # 에러 없이 수행됨
        store.clear("nonexistent")

    def test_key_prefix_constant(self):
        """KEY_PREFIX 상수 값 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        assert RedisCooldownStore.KEY_PREFIX == KEY_PREFIX
