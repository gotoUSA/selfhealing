"""
Healing Events Redis Store.

Healing Events를 Redis에 저장하여 다중 워커 간 데이터 동기화를 지원합니다.
TTL 7일로 자동 만료되며, In-Memory 캐시도 유지합니다.

Key Pattern:
    selfhealing:events:{YYYY-MM-DD} - 날짜별 이벤트 List

Features:
    - Redis에 이벤트 저장 (LPUSH)
    - TTL 7일 자동 설정
    - In-Memory fallback (Redis 실패 시)
    - 다중 워커 동기화
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Constants
# =============================================================================

EVENTS_KEY_PREFIX = "selfhealing:events"
EVENTS_TTL_DAYS = 7
EVENTS_TTL_SECONDS = EVENTS_TTL_DAYS * 24 * 60 * 60  # 604800초

# In-Memory fallback (Redis 실패 시 사용)
_events_memory_lock = threading.Lock()
_events_memory: list[dict[str, Any]] = []
_max_events_memory = 500

# Redis 사용 플래그 (테스트에서 비활성화 가능)
_redis_enabled = True


# =============================================================================
# Configuration
# =============================================================================


def set_redis_events_enabled(enabled: bool) -> None:
    """Redis 이벤트 저장 활성화/비활성화."""
    global _redis_enabled
    _redis_enabled = enabled


def get_redis_events_enabled() -> bool:
    """Redis 이벤트 저장 활성화 여부."""
    return _redis_enabled


# =============================================================================
# Redis Client Helper
# =============================================================================


def _get_redis_client() -> Any | None:
    """Redis 클라이언트 가져오기."""
    if not _redis_enabled:
        return None

    try:
        from selfhealing.adapters.redis import get_redis_client

        return get_redis_client()
    except ImportError:
        return None
    except Exception as e:
        logger.debug(
            "healing_events.redis_client_unavailable",
            error=e,
        )
        return None


def _get_today_key() -> str:
    """오늘 날짜의 Redis 키 생성."""
    today = datetime.now(dt_timezone.utc).strftime("%Y-%m-%d")
    return f"{EVENTS_KEY_PREFIX}:{today}"


def _get_date_key(date_str: str) -> str:
    """특정 날짜의 Redis 키 생성."""
    return f"{EVENTS_KEY_PREFIX}:{date_str}"


# =============================================================================
# Event Storage Functions
# =============================================================================


def add_healing_event_redis(event: dict[str, Any]) -> bool:
    """
    Healing Event를 Redis에 저장.

    Redis LPUSH로 리스트 앞에 추가하고 TTL 7일 설정.
    Redis 실패 시 In-Memory에 저장.

    Args:
        event: 이벤트 데이터 딕셔너리

    Returns:
        Redis 저장 성공 여부
    """
    global _events_memory

    # 타임스탬프 추가
    if "recorded_at" not in event:
        event["recorded_at"] = datetime.now(dt_timezone.utc).isoformat()

    # Redis 저장 시도
    redis_client = _get_redis_client()
    if redis_client:
        try:
            key = _get_today_key()
            event_json = json.dumps(event, default=str)

            # LPUSH로 리스트 앞에 추가
            redis_client.lpush(key, event_json)

            # TTL 설정 (키가 처음 생성될 때만)
            if redis_client.ttl(key) < 0:
                redis_client.expire(key, EVENTS_TTL_SECONDS)

            logger.debug(
                "healing_events.event_saved_redis",
                event_store_key=key,
            )
            return True

        except Exception as e:
            logger.warning(
                "healing_events.redis_save_failed",
                error=e,
            )

    # In-Memory fallback
    with _events_memory_lock:
        _events_memory.append(event)
        if len(_events_memory) > _max_events_memory:
            # 제자리에서 오래된 이벤트 삭제
            del _events_memory[: len(_events_memory) - _max_events_memory]

    logger.debug("healing_events.event_saved_memory_fallback")
    return False


def get_healing_events_redis(
    limit: int = 50,
    days_back: int = 1,
) -> list[dict[str, Any]]:
    """
    Healing Events를 Redis에서 조회.

    최근 N일간의 이벤트를 조회합니다.
    Redis 실패 시 In-Memory에서 조회.

    Args:
        limit: 반환할 최대 이벤트 수
        days_back: 조회할 과거 일수 (기본 1일 = 오늘만)

    Returns:
        이벤트 딕셔너리 리스트 (최신순)
    """
    redis_client = _get_redis_client()

    if redis_client:
        try:
            events = []
            today = datetime.now(dt_timezone.utc)

            # 지정된 일수만큼 과거 날짜 순회
            for day_offset in range(days_back):
                if len(events) >= limit:
                    break

                date = today.replace(hour=0, minute=0, second=0, microsecond=0)
                date = date.replace(day=today.day - day_offset)
                date_str = date.strftime("%Y-%m-%d")
                key = _get_date_key(date_str)

                # LRANGE로 조회 (최신순)
                remaining = limit - len(events)
                raw_events = redis_client.lrange(key, 0, remaining - 1)

                for raw in raw_events:
                    try:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        events.append(json.loads(raw))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.warning(
                            "healing_events.failed_parse_event",
                            error=e,
                        )

            return events[:limit]

        except Exception as e:
            logger.warning(
                "healing_events.redis_query_failed",
                error=e,
            )

    # In-Memory fallback
    with _events_memory_lock:
        return list(_events_memory[-limit:])


def get_healing_events_count_redis(days_back: int = 1) -> int:
    """
    Healing Events 총 개수 조회.

    Args:
        days_back: 조회할 과거 일수

    Returns:
        이벤트 총 개수
    """
    redis_client = _get_redis_client()

    if redis_client:
        try:
            total = 0
            today = datetime.now(dt_timezone.utc)

            for day_offset in range(days_back):
                date = today.replace(hour=0, minute=0, second=0, microsecond=0)
                date = date.replace(day=today.day - day_offset)
                date_str = date.strftime("%Y-%m-%d")
                key = _get_date_key(date_str)

                count = redis_client.llen(key)
                total += count

            return total

        except Exception as e:
            logger.warning(
                "healing_events.redis_count_failed",
                error=e,
            )

    # In-Memory fallback
    with _events_memory_lock:
        return len(_events_memory)


def clear_healing_events_redis() -> int:
    """
    Healing Events 초기화 (테스트용).

    Redis와 In-Memory 모두 초기화합니다.

    Returns:
        초기화된 이벤트 개수
    """
    count = 0

    # In-Memory 초기화
    with _events_memory_lock:
        count = len(_events_memory)
        _events_memory.clear()  # 기존 리스트 객체 유지하며 클리어

    # Redis 초기화 (오늘 키만)
    redis_client = _get_redis_client()
    if redis_client:
        try:
            key = _get_today_key()
            redis_count = redis_client.llen(key)
            redis_client.delete(key)
            count = max(count, redis_count)
        except Exception as e:
            logger.warning(
                "healing_events.redis_clear_failed",
                error=e,
            )

    return count


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "add_healing_event_redis",
    "get_healing_events_redis",
    "get_healing_events_count_redis",
    "clear_healing_events_redis",
    "set_redis_events_enabled",
    "get_redis_events_enabled",
    "EVENTS_KEY_PREFIX",
    "EVENTS_TTL_DAYS",
    "EVENTS_TTL_SECONDS",
]
