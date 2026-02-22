"""
Redis-based Repository Adapters.

Provides Redis implementations for:
- CircuitBreakerStateRepository
- DLQRepository (FailedOperationRepository)

Uses ResilientStorageBackend for zero data loss guarantees.
"""

from __future__ import annotations

from typing import Any

import structlog

from selfhealing.adapters.redis.circuit_breaker import (
    RedisCircuitBreakerStateRepository,
)
from selfhealing.adapters.redis.dlq import RedisDLQRepository

logger = structlog.get_logger()


def get_redis_client() -> Any | None:
    """
    Redis 클라이언트를 가져옵니다.

    여러 전략을 순차적으로 시도합니다:
    1. ResilientStorageBackend에서 가져오기
    2. django_redis 캐시에서 가져오기
    3. Django 설정의 SELFHEALING_REDIS_URL로 직접 연결
    4. 환경변수 REDIS_URL로 직접 연결

    Returns:
        Redis 클라이언트 또는 None
    """
    # Strategy 1: ResilientStorageBackend
    try:
        from selfhealing.adapters.resilient.backend import ResilientStorageBackend

        backend = ResilientStorageBackend()
        client = backend.get_redis_client()
        if client:
            return client
    except (ImportError, Exception):
        pass

    # Strategy 2: django_redis
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except (ImportError, Exception):
        pass

    # Strategy 3: Django settings SELFHEALING_REDIS_URL
    try:
        import redis
        from django.conf import settings

        redis_url = getattr(settings, "SELFHEALING_REDIS_URL", None)
        if redis_url:
            return redis.from_url(redis_url)
    except (ImportError, Exception):
        pass

    # Strategy 4: Environment variable REDIS_URL
    try:
        import os

        import redis

        redis_url = os.environ.get("REDIS_URL") or os.environ.get("SELFHEALING_REDIS_URL")
        if redis_url:
            return redis.from_url(redis_url)
    except (ImportError, Exception):
        pass

    logger.debug("redis.no_redis_client_available")
    return None


__all__ = [
    "RedisCircuitBreakerStateRepository",
    "RedisDLQRepository",
    "get_redis_client",
]
