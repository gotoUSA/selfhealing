"""
Redis Lua 스크립트 기반 Throttle Limit 원자적 업데이트.

분산 환경에서 다수 워커가 동시에 limit 업데이트 시
Race Condition을 방지하기 위해 Lua 스크립트를 사용합니다.

주요 기능:
- 원자적 limit 업데이트 (1 RTT)
- 마지막 안전 limit 저장 (Cold Start 복구용)
- 조건부 limit 업데이트 (CAS)
"""

from __future__ import annotations

import structlog
import time
from typing import Any

logger = structlog.get_logger()


class ThrottleLuaScripts:
    """
    Throttle Limit 관리를 위한 Lua 스크립트.

    모든 스크립트는 원자적으로 실행되어 Race Condition을 방지합니다.
    """

    # ==========================================================================
    # Lua Script: 원자적 Limit 업데이트
    # ==========================================================================
    LUA_ATOMIC_LIMIT_UPDATE = """
    -- KEYS[1] = throttle:limit:{service}
    -- KEYS[2] = throttle:last_safe_limit:{service}
    -- ARGV[1] = new_limit
    -- ARGV[2] = min_limit
    -- ARGV[3] = max_limit
    -- ARGV[4] = save_as_safe (1 = save, 0 = skip)
    -- ARGV[5] = timestamp

    local new_limit = tonumber(ARGV[1])
    local min_limit = tonumber(ARGV[2])
    local max_limit = tonumber(ARGV[3])
    local save_as_safe = tonumber(ARGV[4])

    -- Bounds checking
    if new_limit < min_limit then
        new_limit = min_limit
    elseif new_limit > max_limit then
        new_limit = max_limit
    end

    -- Get previous limit
    local prev_limit = redis.call('GET', KEYS[1])
    if prev_limit then
        prev_limit = tonumber(prev_limit)
    else
        prev_limit = new_limit
    end

    -- Update limit
    redis.call('SET', KEYS[1], new_limit)

    -- Optionally save as last safe limit
    if save_as_safe == 1 then
        redis.call('HSET', KEYS[2],
                   'limit', new_limit,
                   'updated_at', ARGV[5])
    end

    return {prev_limit, new_limit}
    """

    # ==========================================================================
    # Lua Script: 조건부 Limit 업데이트 (CAS - Compare And Swap)
    # ==========================================================================
    LUA_CAS_LIMIT_UPDATE = """
    -- KEYS[1] = throttle:limit:{service}
    -- ARGV[1] = expected_current
    -- ARGV[2] = new_limit
    -- ARGV[3] = min_limit
    -- ARGV[4] = max_limit

    local expected = tonumber(ARGV[1])
    local new_limit = tonumber(ARGV[2])
    local min_limit = tonumber(ARGV[3])
    local max_limit = tonumber(ARGV[4])

    -- Get current limit
    local current = redis.call('GET', KEYS[1])
    if current then
        current = tonumber(current)
    else
        -- No current value, allow update
        current = nil
    end

    -- Compare
    if current ~= nil and current ~= expected then
        return {0, current, 'MISMATCH'}
    end

    -- Bounds checking
    if new_limit < min_limit then
        new_limit = min_limit
    elseif new_limit > max_limit then
        new_limit = max_limit
    end

    -- Update
    redis.call('SET', KEYS[1], new_limit)

    return {1, new_limit, 'OK'}
    """

    # ==========================================================================
    # Lua Script: 마지막 안전 Limit 로드 (Cold Start)
    # ==========================================================================
    LUA_LOAD_SAFE_LIMIT = """
    -- KEYS[1] = throttle:limit:{service}
    -- KEYS[2] = throttle:last_safe_limit:{service}
    -- ARGV[1] = default_limit
    -- ARGV[2] = max_age_seconds (stale 판단 기준)

    local default_limit = tonumber(ARGV[1])
    local max_age = tonumber(ARGV[2])

    -- Check current limit
    local current = redis.call('GET', KEYS[1])
    if current then
        return {tonumber(current), 'CURRENT'}
    end

    -- Try to load safe limit
    local safe_data = redis.call('HGETALL', KEYS[2])
    if #safe_data == 0 then
        -- No safe limit, use default
        redis.call('SET', KEYS[1], default_limit)
        return {default_limit, 'DEFAULT'}
    end

    -- Parse safe data
    local safe_limit = nil
    local updated_at = nil
    for i = 1, #safe_data, 2 do
        if safe_data[i] == 'limit' then
            safe_limit = tonumber(safe_data[i + 1])
        elseif safe_data[i] == 'updated_at' then
            updated_at = safe_data[i + 1]
        end
    end

    if safe_limit == nil then
        redis.call('SET', KEYS[1], default_limit)
        return {default_limit, 'DEFAULT'}
    end

    -- Use safe limit (age check is done in Python for precision)
    redis.call('SET', KEYS[1], safe_limit)
    return {safe_limit, 'SAFE', updated_at or ''}
    """

    # ==========================================================================
    # Lua Script: RTT 샘플 추가 (ZSET 기반)
    # ==========================================================================
    LUA_ADD_RTT_SAMPLE = """
    -- KEYS[1] = throttle:rtt:{service}
    -- ARGV[1] = rtt_ms
    -- ARGV[2] = timestamp (score)
    -- ARGV[3] = window_seconds
    -- ARGV[4] = max_samples

    local rtt_ms = ARGV[1]
    local timestamp = tonumber(ARGV[2])
    local window = tonumber(ARGV[3])
    local max_samples = tonumber(ARGV[4])

    -- Add sample (score = timestamp, member = "rtt:timestamp")
    local member = rtt_ms .. ':' .. ARGV[2]
    redis.call('ZADD', KEYS[1], timestamp, member)

    -- Remove old samples (outside window)
    local cutoff = timestamp - window
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)

    -- Trim to max samples
    local count = redis.call('ZCARD', KEYS[1])
    if count > max_samples then
        redis.call('ZREMRANGEBYRANK', KEYS[1], 0, count - max_samples - 1)
    end

    -- Set TTL
    redis.call('EXPIRE', KEYS[1], window * 2)

    return redis.call('ZCARD', KEYS[1])
    """


class RedisThrottleLimitManager:
    """
    Redis 기반 Throttle Limit 관리자.

    Lua 스크립트를 사용하여 원자적 limit 업데이트를 수행합니다.
    """

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        default_ttl_seconds: int = 3600,
    ):
        """
        초기화.

        Args:
            redis_client: Redis 클라이언트
            key_prefix: Redis 키 prefix
            default_ttl_seconds: 기본 TTL (초)
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._default_ttl = default_ttl_seconds

        # Script SHA 캐시
        self._script_shas: dict[str, str] = {}
        self._scripts_loaded = False

    def _get_limit_key(self, service_name: str) -> str:
        """서비스별 limit 키."""
        return f"{self._key_prefix}throttle:limit:{service_name}"

    def _get_safe_limit_key(self, service_name: str) -> str:
        """서비스별 마지막 안전 limit 키."""
        return f"{self._key_prefix}throttle:last_safe_limit:{service_name}"

    def _get_rtt_key(self, service_name: str) -> str:
        """서비스별 RTT 샘플 키."""
        return f"{self._key_prefix}throttle:rtt:{service_name}"

    def _ensure_scripts_loaded(self) -> None:
        """Lua 스크립트 로드."""
        if self._scripts_loaded:
            return

        try:
            self._script_shas["atomic_update"] = self._redis.script_load(ThrottleLuaScripts.LUA_ATOMIC_LIMIT_UPDATE)
            self._script_shas["cas_update"] = self._redis.script_load(ThrottleLuaScripts.LUA_CAS_LIMIT_UPDATE)
            self._script_shas["load_safe"] = self._redis.script_load(ThrottleLuaScripts.LUA_LOAD_SAFE_LIMIT)
            self._script_shas["add_rtt"] = self._redis.script_load(ThrottleLuaScripts.LUA_ADD_RTT_SAMPLE)
            self._scripts_loaded = True
            logger.debug("redis_throttle_limit_manager.scripts_loaded")
        except Exception as e:
            logger.warning(
                "redis_throttle_limit_manager.script_load_failed",
                error=e,
            )

    def _run_script(
        self,
        script_name: str,
        script_body: str,
        keys: list[str],
        args: list,
    ) -> Any:
        """스크립트 실행 (evalsha 또는 eval 폴백)."""
        self._ensure_scripts_loaded()

        sha = self._script_shas.get(script_name)

        try:
            if sha:
                return self._redis.evalsha(sha, len(keys), *keys, *args)
            else:
                return self._redis.eval(script_body, len(keys), *keys, *args)
        except Exception as e:
            # NOSCRIPT 에러 시 eval로 폴백
            if "NOSCRIPT" in str(e):
                self._scripts_loaded = False
                return self._redis.eval(script_body, len(keys), *keys, *args)
            raise

    def update_limit_atomic(
        self,
        service_name: str,
        new_limit: int,
        min_limit: int,
        max_limit: int,
        save_as_safe: bool = False,
    ) -> tuple[int, int]:
        """
        원자적 limit 업데이트.

        Args:
            service_name: 서비스 이름
            new_limit: 새 limit
            min_limit: 최소 limit
            max_limit: 최대 limit
            save_as_safe: 마지막 안전 limit으로 저장할지 여부

        Returns:
            (이전 limit, 새 limit)
        """
        keys = [
            self._get_limit_key(service_name),
            self._get_safe_limit_key(service_name),
        ]
        args = [
            new_limit,
            min_limit,
            max_limit,
            1 if save_as_safe else 0,
            str(time.time()),
        ]

        result = self._run_script(
            "atomic_update",
            ThrottleLuaScripts.LUA_ATOMIC_LIMIT_UPDATE,
            keys,
            args,
        )

        prev_limit, actual_new = result
        logger.debug(
            "redis_throttle_limit_manager.updated",
            service_name=service_name,
            prev_limit=prev_limit,
            actual_new=actual_new,
        )
        return (int(prev_limit), int(actual_new))

    def update_limit_cas(
        self,
        service_name: str,
        expected_current: int,
        new_limit: int,
        min_limit: int,
        max_limit: int,
    ) -> tuple[bool, int, str]:
        """
        조건부 limit 업데이트 (Compare-And-Swap).

        현재 값이 expected_current와 일치할 때만 업데이트합니다.

        Args:
            service_name: 서비스 이름
            expected_current: 예상 현재 값
            new_limit: 새 limit
            min_limit: 최소 limit
            max_limit: 최대 limit

        Returns:
            (성공 여부, 실제 현재 값, 메시지)
        """
        keys = [self._get_limit_key(service_name)]
        args = [expected_current, new_limit, min_limit, max_limit]

        result = self._run_script(
            "cas_update",
            ThrottleLuaScripts.LUA_CAS_LIMIT_UPDATE,
            keys,
            args,
        )

        success = bool(result[0])
        actual_value = int(result[1])
        message = result[2].decode() if isinstance(result[2], bytes) else result[2]

        return (success, actual_value, message)

    def load_safe_limit(
        self,
        service_name: str,
        default_limit: int,
        max_age_seconds: int = 3600,
    ) -> tuple[int, str]:
        """
        Cold Start 시 마지막 안전 limit 로드.

        현재 limit이 없으면 마지막 안전 limit을 사용합니다.

        Args:
            service_name: 서비스 이름
            default_limit: 기본 limit
            max_age_seconds: 안전 limit의 최대 유효 시간

        Returns:
            (limit 값, 소스: "CURRENT", "SAFE", "DEFAULT")
        """
        keys = [
            self._get_limit_key(service_name),
            self._get_safe_limit_key(service_name),
        ]
        args = [default_limit, max_age_seconds]

        result = self._run_script(
            "load_safe",
            ThrottleLuaScripts.LUA_LOAD_SAFE_LIMIT,
            keys,
            args,
        )

        limit_value = int(result[0])
        source = result[1].decode() if isinstance(result[1], bytes) else result[1]

        logger.info(
            f"[RedisThrottleLimitManager] Loaded limit: " f"service={service_name}, limit={limit_value}, source={source}"
        )

        return (limit_value, source)

    def save_safe_limit(self, service_name: str, limit: int) -> bool:
        """
        마지막 안전 limit 저장.

        Args:
            service_name: 서비스 이름
            limit: 저장할 limit

        Returns:
            성공 여부
        """
        try:
            key = self._get_safe_limit_key(service_name)
            self._redis.hset(
                key,
                mapping={
                    "limit": limit,
                    "updated_at": str(time.time()),
                },
            )
            self._redis.expire(key, self._default_ttl * 24)  # 24시간 유지
            return True
        except Exception as e:
            logger.error(
                "redis_throttle_limit_manager.save_safe_limit_failed",
                error=e,
            )
            return False

    def get_current_limit(self, service_name: str) -> int | None:
        """현재 limit 조회."""
        try:
            value = self._redis.get(self._get_limit_key(service_name))
            return int(value) if value else None
        except Exception as e:
            logger.error(
                "redis_throttle_limit_manager.get_limit_failed",
                error=e,
            )
            return None

    def add_rtt_sample(
        self,
        service_name: str,
        rtt_ms: float,
        window_seconds: float = 60.0,
        max_samples: int = 100,
    ) -> int:
        """
        RTT 샘플 추가.

        Args:
            service_name: 서비스 이름
            rtt_ms: RTT (ms)
            window_seconds: 샘플 유지 시간
            max_samples: 최대 샘플 수

        Returns:
            현재 샘플 수
        """
        keys = [self._get_rtt_key(service_name)]
        args = [rtt_ms, time.time(), window_seconds, max_samples]

        result = self._run_script(
            "add_rtt",
            ThrottleLuaScripts.LUA_ADD_RTT_SAMPLE,
            keys,
            args,
        )

        return int(result)

    def get_rtt_samples(
        self,
        service_name: str,
        window_seconds: float = 60.0,
    ) -> list[float]:
        """
        최근 RTT 샘플 조회.

        Args:
            service_name: 서비스 이름
            window_seconds: 조회 윈도우

        Returns:
            RTT 값 리스트
        """
        try:
            cutoff = time.time() - window_seconds
            key = self._get_rtt_key(service_name)
            samples = self._redis.zrangebyscore(key, cutoff, "+inf")

            rtt_values = []
            for sample in samples:
                if isinstance(sample, bytes):
                    sample = sample.decode()
                # Format: "rtt:timestamp"
                parts = sample.split(":")
                if parts:
                    try:
                        rtt_values.append(float(parts[0]))
                    except ValueError:
                        pass

            return rtt_values
        except Exception as e:
            logger.error(
                "redis_throttle_limit_manager.get_rtt_samples_failed",
                error=e,
            )
            return []
