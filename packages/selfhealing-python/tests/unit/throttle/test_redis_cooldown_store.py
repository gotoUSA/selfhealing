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
from unittest.mock import ANY, MagicMock

import pytest


class TestRedisCooldownStoreWithRedis:
    """Redis 클라이언트가 있는 경우의 쿨다운 테스트."""

    def test_not_cooled_down_initially(self):
        """초기 상태는 쿨다운 아님."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)
        assert not store.is_cooled_down("sla:throttle:payment")

    def test_cooled_down_when_key_exists(self):
        """Redis에 키가 존재하면 쿨다운 중."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)
        assert store.is_cooled_down("sla:throttle:payment")

    def test_mark_sent_calls_redis_set(self):
        """mark_sent가 Redis SET + TTL 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        store.mark_sent("sla:throttle:payment")

        mock_redis.set.assert_called_once_with(
            "selfhealing:notification:cooldown:sla:throttle:payment",
            ANY,
            ex=1800,
        )

    def test_mark_sent_key_prefix_format(self):
        """Redis 키가 KEY_PREFIX:dedup_key 형식."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=300)

        store.mark_sent("test:key")
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "selfhealing:notification:cooldown:test:key"
        assert call_args[1]["ex"] == 300 or call_args[0][2] == 300

    def test_clear_calls_redis_delete(self):
        """clear가 Redis DELETE 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        store.clear("sla:throttle:payment")
        mock_redis.delete.assert_called_once_with("selfhealing:notification:cooldown:sla:throttle:payment")

    def test_clear_also_clears_memory(self):
        """clear가 메모리 캐시도 함께 비움."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.side_effect = Exception("Redis down")
        mock_redis.set.side_effect = Exception("Redis down")

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        # 메모리에 기록
        store.mark_sent("sla:throttle:payment")
        assert store.is_cooled_down("sla:throttle:payment")

        # clear 후 메모리에서도 제거
        store.clear("sla:throttle:payment")
        assert not store.is_cooled_down("sla:throttle:payment")

    def test_is_cooled_down_checks_redis_key(self):
        """is_cooled_down이 올바른 키로 Redis.exists 호출."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)
        store.is_cooled_down("test:abc")

        mock_redis.exists.assert_called_once_with("selfhealing:notification:cooldown:test:abc")


class TestRedisCooldownStoreRedisFallback:
    """Redis 장애 시 메모리 폴백 테스트."""

    def test_redis_exists_failure_falls_back_to_memory(self):
        """Redis.exists 실패 시 메모리 폴백."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.exists.side_effect = Exception("Redis down")
        mock_redis.set.side_effect = Exception("Redis down")

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        assert not store.is_cooled_down("sla:throttle:payment")
        store.mark_sent("sla:throttle:payment")
        assert store.is_cooled_down("sla:throttle:payment")

    def test_redis_set_failure_stores_in_memory(self):
        """Redis.set 실패 시 메모리에 저장."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.set.side_effect = Exception("Redis down")
        mock_redis.exists.side_effect = Exception("Redis down")

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)

        store.mark_sent("key1")
        # 메모리에 저장되었으므로 is_cooled_down이 True
        assert store.is_cooled_down("key1")

    def test_redis_delete_failure_silent(self):
        """Redis.delete 실패 시 에러 없이 진행."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        mock_redis = MagicMock()
        mock_redis.delete.side_effect = Exception("Redis down")

        store = RedisCooldownStore(redis_client=mock_redis, cooldown_seconds=1800)
        # 에러 없이 수행됨
        store.clear("key1")


class TestRedisCooldownStoreMemoryOnly:
    """Redis 미사용(None) 시 메모리 전용 동작 테스트."""

    def test_no_redis_initial_not_cooled(self):
        """Redis=None, 초기 상태 쿨다운 아님."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)
        assert not store.is_cooled_down("sla:throttle:payment")

    def test_no_redis_mark_and_check(self):
        """Redis=None, mark → is_cooled 동작."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)
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

        time.sleep(1.1)
        assert not store.is_cooled_down("key1")

    def test_clear_removes_from_memory(self):
        """clear가 메모리에서 제거."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)
        store.mark_sent("key1")
        assert store.is_cooled_down("key1")

        store.clear("key1")
        assert not store.is_cooled_down("key1")

    def test_multiple_keys_independent(self):
        """각 dedup_key가 독립적으로 동작."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)
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

        store = RedisCooldownStore(redis_client=None, cooldown_seconds=1800)
        # 에러 없이 수행됨
        store.clear("nonexistent")

    def test_key_prefix_constant(self):
        """KEY_PREFIX 상수 값 확인."""
        from selfhealing.services.throttle.redis_cooldown_store import (
            RedisCooldownStore,
        )

        assert RedisCooldownStore.KEY_PREFIX == "selfhealing:notification:cooldown"
