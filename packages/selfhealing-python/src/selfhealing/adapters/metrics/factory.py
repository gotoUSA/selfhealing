"""
Metric Adapter Factory.

Creates the appropriate metric source adapter based on configuration.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional, TYPE_CHECKING

from selfhealing.adapters.metrics.base import (
    MetricSourceAdapter,
    NullMetricSourceAdapter,
)

if TYPE_CHECKING:
    from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter
    from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

logger = logging.getLogger(__name__)


# Singleton adapter instance
_adapter_instance: Optional[MetricSourceAdapter] = None
_adapter_configured: bool = False


def get_metric_adapter() -> MetricSourceAdapter:
    """
    설정에 따라 적절한 메트릭 어댑터를 반환합니다.

    환경 변수:
        SELFHEALING_METRICS_ADAPTER_TYPE: "django", "redis", "null" (기본: "null")
        SELFHEALING_REDIS_URL: Redis 연결 URL (redis 타입 사용 시)

    Returns:
        MetricSourceAdapter 구현체
    """
    global _adapter_instance, _adapter_configured

    if _adapter_instance is not None:
        return _adapter_instance

    adapter_type = os.environ.get("SELFHEALING_METRICS_ADAPTER_TYPE", "null").lower()

    if adapter_type == "redis":
        _adapter_instance = _create_redis_adapter()
    elif adapter_type == "django":
        _adapter_instance = _create_django_adapter()
    else:
        # null or unknown type
        _adapter_instance = NullMetricSourceAdapter()
        logger.info("[MetricAdapter] Using NullMetricSourceAdapter (no-op)")

    _adapter_configured = True
    return _adapter_instance


def configure_adapter(adapter: MetricSourceAdapter) -> None:
    """
    메트릭 어댑터를 직접 설정합니다.

    Django 앱의 ready() 훅 등에서 모델을 사용한 어댑터를 설정할 때 사용합니다.

    Args:
        adapter: 사용할 메트릭 어댑터

    Example:
        >>> from selfhealing.adapters.metrics import configure_adapter
        >>> from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter
        >>> from myapp.models import DLQItem, CircuitBreakerState
        >>> adapter = DjangoMetricSourceAdapter(
        ...     dlq_model=DLQItem,
        ...     circuit_breaker_model=CircuitBreakerState,
        ... )
        >>> configure_adapter(adapter)
    """
    global _adapter_instance, _adapter_configured

    _adapter_instance = adapter
    _adapter_configured = True
    logger.info(f"[MetricAdapter] Configured: {type(adapter).__name__}")


def reset_adapter() -> None:
    """
    어댑터 설정을 리셋합니다 (테스트용).
    """
    global _adapter_instance, _adapter_configured
    _adapter_instance = None
    _adapter_configured = False


def _create_redis_adapter() -> MetricSourceAdapter:
    """Create Redis-based adapter."""
    try:
        import redis as redis_lib
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

        redis_url = os.environ.get("SELFHEALING_REDIS_URL", "redis://localhost:6379/0")
        prefix = os.environ.get("SELFHEALING_METRICS_REDIS_PREFIX", "sh:metrics:")

        client = redis_lib.from_url(redis_url, decode_responses=True)
        # Test connection
        client.ping()

        logger.info(f"[MetricAdapter] Redis adapter connected: {redis_url}")
        return RedisMetricSourceAdapter(redis_client=client, prefix=prefix)

    except ImportError:
        logger.warning("[MetricAdapter] redis package not installed, falling back to NullAdapter")
        return NullMetricSourceAdapter()
    except Exception as e:
        logger.warning(f"[MetricAdapter] Redis connection failed: {e}, falling back to NullAdapter")
        return NullMetricSourceAdapter()


def _create_django_adapter() -> MetricSourceAdapter:
    """Create Django ORM-based adapter."""
    try:
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter

        # Django 모델은 사용자가 configure_adapter()로 직접 설정해야 함
        # 여기서는 모델 없이 빈 어댑터 생성
        logger.info(
            "[MetricAdapter] Django adapter created without models. "
            "Call configure_adapter() with your models."
        )
        return DjangoMetricSourceAdapter()

    except ImportError as e:
        logger.warning(f"[MetricAdapter] Django not available: {e}")
        return NullMetricSourceAdapter()


__all__ = [
    "get_metric_adapter",
    "configure_adapter",
    "reset_adapter",
]
