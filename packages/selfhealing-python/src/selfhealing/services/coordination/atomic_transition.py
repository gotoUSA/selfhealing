"""
Atomic Level Transition.

원자적 Emergency Level 전환.
분산 환경에서 여러 노드가 동시에 상태를 바꿀 때 발생할 수 있는
데이터 경합(Race Condition)을 Lua 스크립트로 원천 차단합니다.

Code reference:
    canary/locking.py#L177-187 (Lua 스크립트 원자적 처리)
    canary/locking.py#L279-289 (TTL 연장 Lua 스크립트)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# Lua 스크립트: 레벨 확인 + 모드 변경 + 인과관계 ID 기록 (원자적)
# Code reference: canary/locking.py#L177-187
ATOMIC_TRANSITION_SCRIPT = """
-- KEYS[1]: emergency state key (예: selfhealing:emergency:seoul)
-- ARGV[1]: expected current level (검증용)
-- ARGV[2]: new level
-- ARGV[3]: new governance mode
-- ARGV[4]: causation event id
-- ARGV[5]: updated_at timestamp

local current_level = redis.call("HGET", KEYS[1], "level")

-- Optimistic Lock: 현재 레벨이 예상과 다르면 실패
if current_level and current_level ~= ARGV[1] then
    return {0, "level_mismatch", current_level}
end

-- 원자적 상태 업데이트
redis.call("HMSET", KEYS[1], 
    "level", ARGV[2],
    "governance_mode", ARGV[3],
    "causation_id", ARGV[4],
    "updated_at", ARGV[5]
)

return {1, "success", ARGV[2]}
"""


# Lua 스크립트: 조건부 레벨 전환 (현재 레벨이 특정 값일 때만)
CONDITIONAL_TRANSITION_SCRIPT = """
-- KEYS[1]: emergency state key
-- ARGV[1]: required current level (이 레벨일 때만 전환)
-- ARGV[2]: new level
-- ARGV[3]: new governance mode
-- ARGV[4]: causation event id
-- ARGV[5]: updated_at timestamp
-- ARGV[6]: reason

local current_level = redis.call("HGET", KEYS[1], "level")

-- 조건 확인: 현재 레벨이 요구 레벨이 아니면 무시
if current_level and current_level ~= ARGV[1] then
    return {0, "condition_not_met", current_level}
end

-- 원자적 상태 업데이트
redis.call("HMSET", KEYS[1],
    "level", ARGV[2],
    "governance_mode", ARGV[3],
    "causation_id", ARGV[4],
    "updated_at", ARGV[5],
    "reason", ARGV[6]
)

return {1, "success", ARGV[2]}
"""


# Lua 스크립트: 레벨 비교 후 상승만 허용 (하락 방지)
ESCALATE_ONLY_SCRIPT = """
-- KEYS[1]: emergency state key
-- ARGV[1]: new level value (숫자로 비교)
-- ARGV[2]: new level name
-- ARGV[3]: new governance mode
-- ARGV[4]: causation event id
-- ARGV[5]: updated_at timestamp

local current_level_str = redis.call("HGET", KEYS[1], "level")

-- 레벨 값 매핑 (NORMAL=0, LEVEL_1=1, LEVEL_2=2, LEVEL_3=3)
local level_map = {
    NORMAL = 0,
    LEVEL_1 = 1,
    LEVEL_2 = 2,
    LEVEL_3 = 3
}

local current_value = level_map[current_level_str] or 0
local new_value = tonumber(ARGV[1])

-- 상승만 허용
if new_value <= current_value then
    return {0, "level_not_escalating", current_level_str}
end

-- 원자적 상태 업데이트
redis.call("HMSET", KEYS[1],
    "level", ARGV[2],
    "governance_mode", ARGV[3],
    "causation_id", ARGV[4],
    "updated_at", ARGV[5]
)

return {1, "success", ARGV[2]}
"""


class AtomicLevelTransition:
    """
    원자적 Emergency Level 전환.

    분산 환경에서 여러 노드가 동시에 상태를 바꿀 때 발생할 수 있는
    데이터 경합(Race Condition)을 Lua 스크립트로 원천 차단합니다.

    Code reference:
        canary/locking.py#L177-187 (Lua 스크립트 원자적 처리)
        canary/locking.py#L279-289 (TTL 연장 Lua 스크립트)

    Usage:
        transition = AtomicLevelTransition(redis_client)
        success, msg, level = transition.transition(
            namespace="seoul",
            expected_level="NORMAL",
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="trigger-001",
        )
    """

    # 레벨 값 매핑 (Lua 스크립트와 동일)
    LEVEL_VALUES = {
        "NORMAL": 0,
        "LEVEL_1": 1,
        "LEVEL_2": 2,
        "LEVEL_3": 3,
    }

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing",
    ):
        """
        Args:
            redis_client: Redis 클라이언트 (redis-py)
            key_prefix: Redis 키 접두사
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._script_sha: str | None = None

    def _get_key(self, namespace: str) -> str:
        """Redis 키 생성."""
        return f"{self._key_prefix}:emergency:{namespace}"

    def transition(
        self,
        namespace: str,
        expected_level: str,
        new_level: str,
        new_mode: str,
        causation_id: str,
    ) -> tuple[bool, str, str]:
        """
        원자적 레벨 전환 실행.

        Optimistic Lock 방식으로 현재 레벨이 예상과 일치할 때만 전환합니다.

        Args:
            namespace: 대상 네임스페이스
            expected_level: 예상 현재 레벨 (Optimistic Lock)
            new_level: 새 레벨
            new_mode: 새 Governance 모드
            causation_id: 인과관계 이벤트 ID

        Returns:
            (success, message, resulting_level)

        Example:
            success, msg, level = transition.transition(
                namespace="seoul",
                expected_level="NORMAL",
                new_level="LEVEL_3",
                new_mode="STRICT",
                causation_id="trigger-001",
            )
            if success:
                print(f"Transitioned to {level}")
            else:
                print(f"Failed: {msg} (current: {level})")
        """
        key = self._get_key(namespace)
        now = datetime.now(timezone.utc).isoformat()

        try:
            result = self._redis.eval(
                ATOMIC_TRANSITION_SCRIPT,
                1,  # KEYS count
                key,
                expected_level,
                new_level,
                new_mode,
                causation_id,
                now,
            )

            success = result[0] == 1
            message = (
                result[1] if isinstance(result[1], str) else result[1].decode("utf-8")
            )
            level = (
                result[2] if isinstance(result[2], str) else result[2].decode("utf-8")
            )

            if success:
                logger.info(
                    "atomic_transition.success",
                    namespace=namespace,
                    expected_level=expected_level,
                    new_level=new_level,
                )
            else:
                logger.warning(
                    "atomic_transition.failed",
                    message=message,
                    level=level,
                    expected_level=expected_level,
                )

            return (success, message, level)

        except Exception as e:
            logger.exception(
                "atomic_transition.error",
                error=e,
            )
            return (False, str(e), expected_level)

    def transition_conditional(
        self,
        namespace: str,
        required_level: str,
        new_level: str,
        new_mode: str,
        causation_id: str,
        reason: str = "",
    ) -> tuple[bool, str, str]:
        """
        조건부 레벨 전환 (특정 레벨일 때만).

        현재 레벨이 required_level과 일치할 때만 전환합니다.
        복구 시 LEVEL_3에서만 NORMAL로 전환 등에 사용.

        Args:
            namespace: 대상 네임스페이스
            required_level: 요구 현재 레벨 (이 레벨일 때만 전환)
            new_level: 새 레벨
            new_mode: 새 Governance 모드
            causation_id: 인과관계 이벤트 ID
            reason: 전환 사유

        Returns:
            (success, message, resulting_level)
        """
        key = self._get_key(namespace)
        now = datetime.now(timezone.utc).isoformat()

        try:
            result = self._redis.eval(
                CONDITIONAL_TRANSITION_SCRIPT,
                1,
                key,
                required_level,
                new_level,
                new_mode,
                causation_id,
                now,
                reason,
            )

            success = result[0] == 1
            message = (
                result[1] if isinstance(result[1], str) else result[1].decode("utf-8")
            )
            level = (
                result[2] if isinstance(result[2], str) else result[2].decode("utf-8")
            )

            return (success, message, level)

        except Exception as e:
            logger.exception(
                "atomic_transition.conditional_error",
                error=e,
            )
            return (False, str(e), required_level)

    def escalate_only(
        self,
        namespace: str,
        new_level: str,
        new_mode: str,
        causation_id: str,
    ) -> tuple[bool, str, str]:
        """
        상승 전용 레벨 전환 (하락 방지).

        현재 레벨보다 높은 레벨로만 전환합니다.
        장애 에스컬레이션 시 안전하게 사용.

        Args:
            namespace: 대상 네임스페이스
            new_level: 새 레벨
            new_mode: 새 Governance 모드
            causation_id: 인과관계 이벤트 ID

        Returns:
            (success, message, resulting_level)
        """
        key = self._get_key(namespace)
        now = datetime.now(timezone.utc).isoformat()
        new_value = self.LEVEL_VALUES.get(new_level, 0)

        try:
            result = self._redis.eval(
                ESCALATE_ONLY_SCRIPT,
                1,
                key,
                str(new_value),
                new_level,
                new_mode,
                causation_id,
                now,
            )

            success = result[0] == 1
            message = (
                result[1] if isinstance(result[1], str) else result[1].decode("utf-8")
            )
            level = (
                result[2] if isinstance(result[2], str) else result[2].decode("utf-8")
            )

            return (success, message, level)

        except Exception as e:
            logger.exception(
                "atomic_transition.escalate_error",
                error=e,
            )
            return (False, str(e), new_level)

    def get_current_state(
        self,
        namespace: str,
    ) -> dict | None:
        """
        현재 상태 조회.

        Args:
            namespace: 대상 네임스페이스

        Returns:
            상태 딕셔너리 또는 None
        """
        key = self._get_key(namespace)

        try:
            data = self._redis.hgetall(key)
            if not data:
                return None

            result = {}
            for k, v in data.items():
                key_str = k.decode("utf-8") if isinstance(k, bytes) else k
                val_str = v.decode("utf-8") if isinstance(v, bytes) else v
                result[key_str] = val_str

            return result

        except Exception as e:
            logger.exception(
                "atomic_transition.get_state_error",
                error=e,
            )
            return None
