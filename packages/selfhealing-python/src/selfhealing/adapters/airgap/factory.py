"""
Air-Gap Adapter Factory.

Creates the appropriate Air-Gap storage adapter based on configuration.
Default is NullAirGapAdapter (disabled).

Configuration:
    SELFHEALING_AIRGAP_ENABLED: "true" or "false" (default: "false")
    SELFHEALING_AIRGAP_REDIS_URL: Redis connection URL
    SELFHEALING_AIRGAP_PREFIX: Key prefix (default: "sh:airgap:")
    SELFHEALING_AIRGAP_TTL: Default TTL in seconds (default: 3600)
"""

from __future__ import annotations

import structlog
import os

from selfhealing.adapters.airgap.base import AirGapStorageAdapter
from selfhealing.adapters.airgap.null_adapter import NullAirGapAdapter

logger = structlog.get_logger()


# Singleton adapter instance
_adapter_instance: AirGapStorageAdapter | None = None
_adapter_configured: bool = False


def get_airgap_adapter() -> AirGapStorageAdapter:
    """
    설정에 따라 적절한 Air-Gap 어댑터를 반환합니다.

    환경 변수:
        SELFHEALING_AIRGAP_ENABLED: "true"면 Redis 어댑터, 아니면 Null 어댑터
        SELFHEALING_AIRGAP_REDIS_URL: Redis 연결 URL
        SELFHEALING_AIRGAP_PREFIX: 키 접두사 (기본: "sh:airgap:")
        SELFHEALING_AIRGAP_TTL: 기본 TTL (초, 기본: 3600)

    Returns:
        AirGapStorageAdapter 구현체

    Example:
        >>> adapter = get_airgap_adapter()
        >>> if adapter.is_enabled():
        ...     adapter.write_summary("dlq:payment:pending", 5)
    """
    global _adapter_instance, _adapter_configured

    if _adapter_instance is not None:
        return _adapter_instance

    enabled = os.environ.get("SELFHEALING_AIRGAP_ENABLED", "false").lower() == "true"

    if enabled:
        _adapter_instance = _create_redis_adapter()
        if _adapter_instance is None:
            logger.warning(
                "[AirGap] Redis adapter creation failed, falling back to NullAdapter"
            )
            _adapter_instance = NullAirGapAdapter()
    else:
        _adapter_instance = NullAirGapAdapter()
        logger.info("air_gap.air_gap_disabled_using")

    _adapter_configured = True
    return _adapter_instance


def configure_airgap_adapter(adapter: AirGapStorageAdapter) -> None:
    """
    Air-Gap 어댑터를 직접 설정합니다.

    Django 앱의 ready() 훅 등에서 어댑터를 직접 설정할 때 사용합니다.

    Args:
        adapter: 사용할 Air-Gap 어댑터

    Example:
        >>> import redis
        >>> from selfhealing.adapters.airgap import configure_airgap_adapter
        >>> from selfhealing.adapters.airgap.redis_adapter import RedisAirGapAdapter
        >>>
        >>> client = redis.from_url("redis://localhost:6379/0")
        >>> adapter = RedisAirGapAdapter(client)
        >>> configure_airgap_adapter(adapter)
    """
    global _adapter_instance, _adapter_configured

    _adapter_instance = adapter
    _adapter_configured = True
    logger.info(
        "air_gap.configured",
        value=type(adapter).__name__,
    )


def reset_airgap_adapter() -> None:
    """
    어댑터 설정을 리셋합니다 (테스트용).
    """
    global _adapter_instance, _adapter_configured
    _adapter_instance = None
    _adapter_configured = False
    logger.debug("air_gap.adapter_reset")


def _create_redis_adapter() -> AirGapStorageAdapter | None:
    """Create Redis-based Air-Gap adapter."""
    redis_url = os.environ.get("SELFHEALING_AIRGAP_REDIS_URL")

    if not redis_url:
        # Django settings에서 가져오기 시도
        redis_url = _get_redis_url_from_django()

    if not redis_url:
        logger.error(
            "[AirGap] SELFHEALING_AIRGAP_REDIS_URL not set and Django REDIS_URL not found"
        )
        return None

    try:
        import redis

        client = redis.from_url(redis_url)
        client.ping()  # 연결 테스트

        prefix = os.environ.get("SELFHEALING_AIRGAP_PREFIX", "sh:airgap:")
        ttl_str = os.environ.get("SELFHEALING_AIRGAP_TTL", "3600")
        ttl = int(ttl_str) if ttl_str else 3600

        from selfhealing.adapters.airgap.redis_adapter import RedisAirGapAdapter

        adapter = RedisAirGapAdapter(client, prefix=prefix, default_ttl=ttl)
        logger.info(
            "air_gap.redisairgapadapter_created",
            redis_url=redis_url,
        )
        return adapter

    except ImportError:
        logger.error("air_gap.redis_package_installed")
        return None
    except Exception as e:
        logger.error(
            "air_gap.failed_create_redis_adapter",
            error=e,
        )
        return None


def _get_redis_url_from_django() -> str | None:
    """Try to get Redis URL from Django settings."""
    try:
        from django.conf import settings

        # 다양한 설정 이름 시도
        for attr in ["REDIS_URL", "CACHES", "CELERY_BROKER_URL"]:
            if hasattr(settings, attr):
                value = getattr(settings, attr)

                if attr == "REDIS_URL" and isinstance(value, str):
                    return value

                if attr == "CACHES" and isinstance(value, dict):
                    default_cache = value.get("default", {})
                    location = default_cache.get("LOCATION")
                    if location and "redis" in str(location):
                        return location if isinstance(location, str) else location[0]

                if attr == "CELERY_BROKER_URL" and isinstance(value, str):
                    if "redis" in value:
                        return value

        return None

    except Exception:
        return None


__all__ = [
    "get_airgap_adapter",
    "configure_airgap_adapter",
    "reset_airgap_adapter",
]
