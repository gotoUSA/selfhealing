"""
Redis Air-Gap Storage Adapter.

Provides Air-Gap storage using Redis as the intermediate layer
between Self-Healing engine and business database.

Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md (Phase 4)

Architecture:
    Business DB → (Business Layer writes) → Redis Air-Gap → (Engine reads) → Self-Healing
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from selfhealing.adapters.airgap.base import BaseAirGapAdapter

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


class RedisAirGapAdapter(BaseAirGapAdapter):
    """
    Redis 기반 Air-Gap 저장소 어댑터.

    비즈니스 레이어에서 DB 변경 시 Redis에 요약 상태를 기록하고,
    Self-Healing 엔진은 Redis에서만 상태를 조회합니다.

    Features:
    - Atomic operations (INCR, DECR)
    - TTL support for automatic expiration
    - Batch read with MGET
    - JSON serialization for complex values

    Example:
        >>> import redis
        >>> client = redis.from_url("redis://localhost:6379/0")
        >>> adapter = RedisAirGapAdapter(client)
        >>> 
        >>> # Business layer writes summary
        >>> adapter.write_summary("dlq:payment:pending", 5)
        >>> 
        >>> # Self-Healing engine reads
        >>> count = adapter.read_summary("dlq:payment:pending")
        >>> print(count)  # 5
    """

    # 기본 TTL: 1시간 (장애 시 자동 정리)
    DEFAULT_TTL = 3600

    def __init__(
        self,
        redis_client: "redis.Redis",
        prefix: str = "sh:airgap:",
        default_ttl: Optional[int] = None,
    ) -> None:
        """
        Initialize the Redis Air-Gap adapter.

        Args:
            redis_client: Redis client instance
            prefix: Key prefix for all Air-Gap keys
            default_ttl: Default TTL in seconds (None = no expiration)
        """
        self.redis = redis_client
        self.prefix = prefix
        self.default_ttl = default_ttl or self.DEFAULT_TTL
        logger.info(f"[AirGap] RedisAirGapAdapter initialized (prefix={prefix})")

    def _make_key(self, key: str) -> str:
        """Create a Redis key with the configured prefix."""
        if key.startswith(self.prefix):
            return key
        return f"{self.prefix}{key}"

    def _serialize(self, value: Any) -> str:
        """Serialize value for Redis storage."""
        if isinstance(value, (str, int, float)):
            return str(value)
        return json.dumps(value)

    def _deserialize(self, value: Optional[bytes]) -> Any:
        """Deserialize value from Redis storage."""
        if value is None:
            return None

        str_value = value.decode("utf-8") if isinstance(value, bytes) else value

        # Try to parse as JSON first
        try:
            return json.loads(str_value)
        except (json.JSONDecodeError, TypeError):
            # Return as string if not valid JSON
            return str_value

    def write_summary(
        self, key: str, value: Any, ttl: Optional[int] = None
    ) -> bool:
        """
        요약 상태를 Redis에 기록.

        Args:
            key: 저장소 키
            value: 저장할 값
            ttl: TTL in seconds (None = use default_ttl)

        Returns:
            성공 여부
        """
        try:
            redis_key = self._make_key(key)
            serialized = self._serialize(value)
            effective_ttl = ttl if ttl is not None else self.default_ttl

            if effective_ttl:
                self.redis.setex(redis_key, effective_ttl, serialized)
            else:
                self.redis.set(redis_key, serialized)

            logger.debug(f"[AirGap] Written: {redis_key} = {value}")
            return True

        except Exception as e:
            logger.warning(f"[AirGap] Write failed for {key}: {e}")
            return False

    def read_summary(self, key: str) -> Any:
        """
        Redis에서 요약 상태 조회.

        Args:
            key: 저장소 키

        Returns:
            저장된 값 또는 None
        """
        try:
            redis_key = self._make_key(key)
            value = self.redis.get(redis_key)
            result = self._deserialize(value)
            logger.debug(f"[AirGap] Read: {redis_key} = {result}")
            return result

        except Exception as e:
            logger.warning(f"[AirGap] Read failed for {key}: {e}")
            return None

    def delete_summary(self, key: str) -> bool:
        """
        Redis에서 요약 상태 삭제.

        Args:
            key: 저장소 키

        Returns:
            성공 여부
        """
        try:
            redis_key = self._make_key(key)
            self.redis.delete(redis_key)
            logger.debug(f"[AirGap] Deleted: {redis_key}")
            return True

        except Exception as e:
            logger.warning(f"[AirGap] Delete failed for {key}: {e}")
            return False

    def read_many(self, keys: List[str]) -> Dict[str, Any]:
        """
        여러 키의 값을 한 번에 조회 (MGET).

        Args:
            keys: 조회할 키 목록

        Returns:
            키-값 딕셔너리
        """
        if not keys:
            return {}

        try:
            redis_keys = [self._make_key(k) for k in keys]
            values = self.redis.mget(redis_keys)

            result = {}
            for key, value in zip(keys, values):
                result[key] = self._deserialize(value)

            return result

        except Exception as e:
            logger.warning(f"[AirGap] Read many failed: {e}")
            return {key: None for key in keys}

    def increment(self, key: str, amount: int = 1) -> int:
        """
        카운터 값 증가 (atomic INCRBY).

        Args:
            key: 저장소 키
            amount: 증가량

        Returns:
            증가 후 값
        """
        try:
            redis_key = self._make_key(key)
            new_value = self.redis.incrby(redis_key, amount)

            # TTL 갱신
            if self.default_ttl:
                self.redis.expire(redis_key, self.default_ttl)

            logger.debug(f"[AirGap] Incremented: {redis_key} += {amount} = {new_value}")
            return new_value

        except Exception as e:
            logger.warning(f"[AirGap] Increment failed for {key}: {e}")
            return 0

    def decrement(self, key: str, amount: int = 1) -> int:
        """
        카운터 값 감소 (atomic, 음수 방지).

        Lua 스크립트를 사용하여 원자적으로 음수 방지를 보장합니다.

        Args:
            key: 저장소 키
            amount: 감소량

        Returns:
            감소 후 값 (최소 0)
        """
        # Lua script for atomic decrement with floor at 0
        lua_script = """
        local current = redis.call('GET', KEYS[1])
        if current == false then
            return 0
        end
        local new_value = tonumber(current) - tonumber(ARGV[1])
        if new_value < 0 then
            new_value = 0
        end
        redis.call('SET', KEYS[1], new_value)
        if tonumber(ARGV[2]) > 0 then
            redis.call('EXPIRE', KEYS[1], ARGV[2])
        end
        return new_value
        """

        try:
            redis_key = self._make_key(key)
            new_value = self.redis.eval(
                lua_script, 1, redis_key, amount, self.default_ttl or 0
            )
            logger.debug(f"[AirGap] Decremented: {redis_key} -= {amount} = {new_value}")
            return int(new_value)

        except Exception as e:
            logger.warning(f"[AirGap] Decrement failed for {key}: {e}")
            return 0

    def is_enabled(self) -> bool:
        """
        Air-Gap 활성화 상태.

        Redis 연결이 정상이면 True.

        Returns:
            True if Redis is connected
        """
        try:
            self.redis.ping()
            return True
        except Exception:
            return False

    def health_check(self) -> Dict[str, Any]:
        """
        Air-Gap 저장소 상태 확인.

        Returns:
            상태 정보 딕셔너리
        """
        try:
            self.redis.ping()
            info = self.redis.info("memory")
            return {
                "status": "healthy",
                "enabled": True,
                "prefix": self.prefix,
                "default_ttl": self.default_ttl,
                "used_memory": info.get("used_memory_human", "unknown"),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "enabled": False,
                "error": str(e),
            }


__all__ = ["RedisAirGapAdapter"]
