"""
Atomic State Query.

Lua 스크립트로 Global + Regional 상태를 한 번에 조회하고
우선순위 판단까지 원자적으로 처리합니다.

네트워크 왕복: 2회 → 1회 (50% 절감)
Race Condition: 원천 차단

Code reference:
    coordination/atomic_transition.py (Lua 스크립트 패턴)
    canary/locking.py#L177-187 (Lua 원자적 처리)

Reference:
    docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Lua Script: 원자적 Global + Regional 상태 조회 및 우선순위 판단
# =============================================================================

ATOMIC_STATE_QUERY_SCRIPT = """
-- KEYS[1]: global emergency state key (selfhealing:governance:emergency_state)
-- KEYS[2]: regional emergency state key (selfhealing:{namespace}:governance:emergency_state)
-- ARGV[1]: precedence level (0=AUTO, 1=MANUAL, 2=ADMIN_OVERRIDE, 3=KILL_SWITCH)

local global_data = redis.call("GET", KEYS[1])
local regional_data = redis.call("GET", KEYS[2])

-- JSON 파싱
local global_state = nil
local regional_state = nil

if global_data and global_data ~= false then
    global_state = cjson.decode(global_data)
end

if regional_data and regional_data ~= false then
    regional_state = cjson.decode(regional_data)
end

-- 기본값 설정
if not global_state then
    global_state = {
        namespace = "global",
        scope = "global",
        governance_mode = "NORMAL",
        is_active = false,
        emergency_level = 0
    }
end

if not regional_state then
    -- namespace 추출: selfhealing:{ns}:governance:emergency_state
    local ns = KEYS[2]:match("selfhealing:([^:]+):governance")
    regional_state = {
        namespace = ns or "unknown",
        scope = "regional",
        governance_mode = "NORMAL",
        is_active = false,
        emergency_level = 0
    }
end

local precedence = tonumber(ARGV[1]) or 0

-- 1순위: Admin Override (precedence >= 2)
-- 운영자가 명시적으로 오버라이드 요청한 경우 Regional 상태 사용
if precedence >= 2 then
    return {
        cjson.encode(regional_state),
        "ADMIN_OVERRIDE",
        "Admin override active, using regional state"
    }
end

-- is_active 판단: emergency_level이 0이 아니면 활성화
-- ScopedEmergencyState.to_dict()는 is_active를 저장하지 않으므로 emergency_level 사용
local function is_active(state)
    local level = state.emergency_level
    if level == nil then
        return state.is_active == true
    end
    return level ~= 0
end

-- 2순위: Safety-Max (둘 중 더 엄격한 상태 선택)
local global_is_strict = is_active(global_state) and
                         (global_state.governance_mode == "STRICT")
local regional_is_strict = is_active(regional_state) and
                           (regional_state.governance_mode == "STRICT")

if global_is_strict and regional_is_strict then
    -- 둘 다 STRICT: Global 우선 (더 넓은 범위)
    return {
        cjson.encode(global_state),
        "GLOBAL_OVERRIDE",
        "Both Global and Regional STRICT, using Global state"
    }
elseif global_is_strict then
    -- Global만 STRICT
    return {
        cjson.encode(global_state),
        "GLOBAL_OVERRIDE",
        "Global STRICT overrides regional " .. (regional_state.namespace or "unknown")
    }
elseif regional_is_strict then
    -- Regional만 STRICT
    return {
        cjson.encode(regional_state),
        "REGIONAL_STRICT",
        "Regional STRICT active"
    }
else
    -- 둘 다 NORMAL: Regional 반환 (로컬 상태 우선)
    return {
        cjson.encode(regional_state),
        "REGIONAL_DEFAULT",
        "Both states NORMAL, using regional"
    }
end
"""


class AtomicStateQuery:
    """
    원자적 상태 조회기.

    Lua 스크립트로 Global + Regional 상태를 한 번에 조회하고
    우선순위 판단까지 원자적으로 처리합니다.

    Benefits:
    - 네트워크 왕복 50% 절감 (2회 → 1회)
    - Race Condition 원천 차단
    - 우선순위 로직 서버사이드 처리

    Precedence Levels:
    - AUTO (0): 자동 모드 - Safety-Max 적용
    - MANUAL (1): 수동 모드 - Safety-Max 적용
    - ADMIN_OVERRIDE (2): 관리자 오버라이드 - Global 무시
    - KILL_SWITCH (3): 킬 스위치 - 모든 것 무시

    Code reference:
        coordination/atomic_transition.py (Lua 스크립트 패턴)

    Usage:
        query = AtomicStateQuery(redis_client)
        state, decision_type, reason = query.query_effective_state("seoul")
    """

    # Precedence 레벨 매핑
    PRECEDENCE_LEVELS = {
        "AUTO": 0,
        "MANUAL": 1,
        "ADMIN_OVERRIDE": 2,
        "KILL_SWITCH": 3,
    }

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing",
    ):
        """
        AtomicStateQuery 초기화.

        Args:
            redis_client: Redis 클라이언트 (redis-py 호환)
            key_prefix: Redis 키 접두사 (기본: "selfhealing")
        """
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _get_global_key(self) -> str:
        """Global 상태 Redis 키 반환."""
        return f"{self._key_prefix}:governance:emergency_state"

    def _get_regional_key(self, namespace: str) -> str:
        """Regional 상태 Redis 키 반환."""
        return f"{self._key_prefix}:{namespace}:governance:emergency_state"

    @staticmethod
    def _decode_raw(raw: Any) -> dict[str, Any] | None:
        """Decode raw Redis GET result to dict, or None."""
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _is_active(state: dict[str, Any]) -> bool:
        """Check if emergency state is active."""
        level = state.get("emergency_level")
        if level is not None:
            return level != 0
        return bool(state.get("is_active", False))

    def _resolve_precedence(
        self,
        global_raw: Any,
        regional_raw: Any,
        namespace: str,
        precedence_level: int,
    ) -> tuple[dict[str, Any], str, str]:
        """Resolve precedence between global and regional states (Python-side)."""
        global_state = self._decode_raw(global_raw)
        regional_state = self._decode_raw(regional_raw)

        if global_state is None:
            global_state = {
                "namespace": "global",
                "scope": "global",
                "governance_mode": "NORMAL",
                "is_active": False,
                "emergency_level": 0,
            }

        if regional_state is None:
            regional_state = {
                "namespace": namespace,
                "scope": "regional",
                "governance_mode": "NORMAL",
                "is_active": False,
                "emergency_level": 0,
            }

        # 1: Admin Override (precedence >= 2)
        if precedence_level >= 2:
            return (
                regional_state,
                "ADMIN_OVERRIDE",
                "Admin override active, using regional state",
            )

        # 2: Safety-Max
        global_is_strict = (
            self._is_active(global_state)
            and global_state.get("governance_mode") == "STRICT"
        )
        regional_is_strict = (
            self._is_active(regional_state)
            and regional_state.get("governance_mode") == "STRICT"
        )

        if global_is_strict and regional_is_strict:
            return (
                global_state,
                "GLOBAL_OVERRIDE",
                "Both Global and Regional STRICT, using Global state",
            )
        elif global_is_strict:
            ns_name = regional_state.get("namespace", "unknown")
            return (
                global_state,
                "GLOBAL_OVERRIDE",
                f"Global STRICT overrides regional {ns_name}",
            )
        elif regional_is_strict:
            return (
                regional_state,
                "REGIONAL_STRICT",
                "Regional STRICT active",
            )
        else:
            return (
                regional_state,
                "REGIONAL_DEFAULT",
                "Both states NORMAL, using regional",
            )

    def query_effective_state(
        self,
        namespace: str,
        precedence: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """
        유효한 상태 조회 (Pipeline 기반).

        Global과 Regional 상태를 Pipeline으로 조회하고
        우선순위에 따라 유효한 상태를 결정합니다.

        Args:
            namespace: 대상 네임스페이스 (예: "seoul", "tokyo")
            precedence: 명령 우선순위
                ("AUTO", "MANUAL", "ADMIN_OVERRIDE", "KILL_SWITCH")

        Returns:
            Tuple of:
            - effective_state: 유효한 상태 딕셔너리
            - decision_type: 의사결정 유형
                ("GLOBAL_OVERRIDE", "ADMIN_OVERRIDE", "REGIONAL_STRICT", "REGIONAL_DEFAULT")
            - decision_reason: 의사결정 이유 (Audit/로깅용)

        Example:
            state, decision_type, reason = query.query_effective_state("seoul")
            # state = {"namespace": "global", "governance_mode": "STRICT", ...}
            # decision_type = "GLOBAL_OVERRIDE"
            # reason = "Global STRICT overrides regional seoul"
        """
        global_key = self._get_global_key()
        regional_key = self._get_regional_key(namespace)
        precedence_level = self.PRECEDENCE_LEVELS.get(precedence or "AUTO", 0)

        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.get(global_key)
            pipe.get(regional_key)
            global_raw, regional_raw = pipe.execute()

            state, decision_type, decision_reason = self._resolve_precedence(
                global_raw,
                regional_raw,
                namespace,
                precedence_level,
            )

            logger.debug(
                "atomic_state_query.event",
                namespace=namespace,
                decision_type=decision_type,
                decision_reason=decision_reason,
            )

            return (state, decision_type, decision_reason)

        except Exception as e:
            logger.exception(
                "atomic_state_query.query_failed",
                error=e,
            )
            # 폴백: 안전한 기본값 (NORMAL 상태)
            return (
                {
                    "namespace": namespace,
                    "scope": "regional",
                    "governance_mode": "NORMAL",
                    "is_active": False,
                    "emergency_level": 0,
                },
                "FALLBACK",
                f"Query failed, using safe default: {e}",
            )

    def preload_script(self) -> None:
        """
        No-op retained for backward compatibility.

        Pipeline-based implementation does not use Lua scripts.
        """

    def query_with_sha(
        self,
        namespace: str,
        precedence: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """
        Delegates to query_effective_state (Pipeline-based).

        Retained for backward compatibility.

        Args:
            namespace: 대상 네임스페이스
            precedence: 명령 우선순위

        Returns:
            query_effective_state와 동일
        """
        return self.query_effective_state(namespace, precedence)


# =============================================================================
# Singleton
# =============================================================================

_atomic_query: AtomicStateQuery | None = None


def get_atomic_state_query() -> AtomicStateQuery:
    """
    AtomicStateQuery 싱글톤 반환.

    Returns:
        AtomicStateQuery 인스턴스
    """
    global _atomic_query
    if _atomic_query is None:
        from selfhealing.core.state_backend import get_redis_client

        _atomic_query = AtomicStateQuery(get_redis_client())
    return _atomic_query


def reset_atomic_state_query() -> None:
    """테스트용 싱글톤 리셋."""
    global _atomic_query
    _atomic_query = None
