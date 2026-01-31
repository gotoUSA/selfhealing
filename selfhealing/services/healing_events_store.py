"""
Healing Events Redis Storage Module.

Redis를 사용하여 Self-Healing 이벤트를 저장하고 관리합니다.
문서 132 섹션 7.4 요구사항 구현:
- Healing Events Redis 저장
- TTL 설정 (7일)
- 다중 워커 동기화
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis import Redis


logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

EVENTS_KEY_PREFIX = "selfhealing:events"
EVENTS_TTL_DAYS = 7
EVENTS_TTL_SECONDS = EVENTS_TTL_DAYS * 24 * 60 * 60  # 604800 seconds

# Redis 활성화 플래그 (테스트용)
_redis_events_enabled = True

# In-Memory fallback storage
_events_memory: list[dict[str, Any]] = []
_events_memory_lock = threading.RLock()
_max_events_memory = 500


# =============================================================================
# Configuration
# =============================================================================


def set_redis_events_enabled(enabled: bool) -> None:
    """Redis 이벤트 저장 활성화/비활성화 설정."""
    global _redis_events_enabled
    _redis_events_enabled = enabled


def is_redis_events_enabled() -> bool:
    """Redis 이벤트 저장 활성화 여부 반환."""
    return _redis_events_enabled


# =============================================================================
# Key Management
# =============================================================================


def _get_today_key() -> str:
    """오늘 날짜 기반 Redis 키 반환."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{EVENTS_KEY_PREFIX}:{today}"


def _get_date_key(date_str: str) -> str:
    """특정 날짜 기반 Redis 키 반환."""
    return f"{EVENTS_KEY_PREFIX}:{date_str}"


# =============================================================================
# Redis Client
# =============================================================================


def _get_redis_client() -> "Redis | None":
    """Redis 클라이언트 인스턴스 반환."""
    try:
        from selfhealing.adapters.redis.client import get_redis_client

        return get_redis_client()
    except ImportError:
        logger.debug("Redis client module not available")
        return None
    except Exception as e:
        logger.warning(f"Failed to get Redis client: {e}")
        return None


# =============================================================================
# Event Storage Operations
# =============================================================================


def add_healing_event_redis(event: dict[str, Any]) -> bool:
    """
    Healing 이벤트를 Redis에 저장합니다.

    Args:
        event: 저장할 이벤트 데이터

    Returns:
        저장 성공 여부
    """
    # recorded_at 타임스탬프 추가
    if "recorded_at" not in event:
        event["recorded_at"] = datetime.now(timezone.utc).isoformat()

    # Redis 비활성화 시 In-Memory에만 저장
    if not _redis_events_enabled:
        _add_to_memory(event)
        return True

    redis_client = _get_redis_client()

    if redis_client is None:
        # Redis 미사용 시 In-Memory fallback
        _add_to_memory(event)
        return True

    try:
        key = _get_today_key()
        event_json = json.dumps(event, ensure_ascii=False, default=str)

        # LPUSH로 최신 이벤트를 맨 앞에 추가
        redis_client.lpush(key, event_json)

        # 키가 새로 생성된 경우 TTL 설정
        if redis_client.ttl(key) == -1:
            redis_client.expire(key, EVENTS_TTL_SECONDS)

        # In-Memory에도 저장 (로컬 캐시)
        _add_to_memory(event)

        logger.debug(f"Healing event stored in Redis: {key}")
        return True

    except Exception as e:
        logger.error(f"Failed to store healing event in Redis: {e}")
        # Redis 실패 시 In-Memory fallback
        _add_to_memory(event)
        return False


def _add_to_memory(event: dict[str, Any]) -> None:
    """이벤트를 In-Memory 저장소에 추가합니다."""
    with _events_memory_lock:
        _events_memory.append(event.copy())
        # 최대 개수 초과 시 가장 오래된 것 삭제
        while len(_events_memory) > _max_events_memory:
            _events_memory.pop(0)


def get_healing_events_redis(
    limit: int = 100,
    date_str: str | None = None,
) -> list[dict[str, Any]]:
    """
    Redis에서 Healing 이벤트 목록을 조회합니다.

    Args:
        limit: 조회할 최대 이벤트 수
        date_str: 조회할 날짜 (YYYY-MM-DD). None이면 오늘

    Returns:
        이벤트 목록 (최신순)
    """
    if not _redis_events_enabled:
        return _get_from_memory(limit)

    redis_client = _get_redis_client()

    if redis_client is None:
        return _get_from_memory(limit)

    try:
        key = _get_date_key(date_str) if date_str else _get_today_key()
        raw_events = redis_client.lrange(key, 0, limit - 1)

        events = []
        for raw in raw_events:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                events.append(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to parse event from Redis: {e}")
                continue

        return events

    except Exception as e:
        logger.error(f"Failed to get healing events from Redis: {e}")
        return _get_from_memory(limit)


def _get_from_memory(limit: int) -> list[dict[str, Any]]:
    """In-Memory 저장소에서 이벤트 조회."""
    with _events_memory_lock:
        # 최신순 반환
        return list(reversed(_events_memory[-limit:]))


def get_healing_events_count_redis(date_str: str | None = None) -> int:
    """
    Redis에서 Healing 이벤트 개수를 조회합니다.

    Args:
        date_str: 조회할 날짜 (YYYY-MM-DD). None이면 오늘

    Returns:
        이벤트 개수
    """
    if not _redis_events_enabled:
        return _get_memory_count()

    redis_client = _get_redis_client()

    if redis_client is None:
        return _get_memory_count()

    try:
        key = _get_date_key(date_str) if date_str else _get_today_key()
        return redis_client.llen(key)

    except Exception as e:
        logger.error(f"Failed to get healing events count from Redis: {e}")
        return _get_memory_count()


def _get_memory_count() -> int:
    """In-Memory 저장소의 이벤트 개수 반환."""
    with _events_memory_lock:
        return len(_events_memory)


def clear_healing_events_redis(date_str: str | None = None) -> bool:
    """
    Redis에서 Healing 이벤트를 삭제합니다.

    Args:
        date_str: 삭제할 날짜 (YYYY-MM-DD). None이면 오늘

    Returns:
        삭제 성공 여부
    """
    # In-Memory도 클리어
    with _events_memory_lock:
        _events_memory.clear()

    if not _redis_events_enabled:
        return True

    redis_client = _get_redis_client()

    if redis_client is None:
        return True

    try:
        key = _get_date_key(date_str) if date_str else _get_today_key()
        redis_client.delete(key)
        logger.debug(f"Healing events cleared from Redis: {key}")
        return True

    except Exception as e:
        logger.error(f"Failed to clear healing events from Redis: {e}")
        return False
