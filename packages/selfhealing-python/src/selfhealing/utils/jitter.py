"""
Jitter Utilities for Thundering Herd Prevention.

Provides random delay mechanisms to prevent all instances from
hitting the database simultaneously during startup.

Note: This module was moved from metrics/jitter.py since jitter
utilities are general-purpose and not metrics-specific.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from functools import wraps
from typing import Callable, TypeVar, ParamSpec

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def with_jitter(
    max_delay_seconds: float = 60.0,
    min_delay_seconds: float = 0.0,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    동기화 함수에 무작위 지연을 추가하는 데코레이터.

    분산 환경에서 동시 시작되는 인스턴스들의 DB 쿼리를
    시간적으로 분산시켜 Thundering Herd를 방지합니다.

    Args:
        max_delay_seconds: 최대 지연 시간 (초). 기본 60초.
        min_delay_seconds: 최소 지연 시간 (초). 기본 0초.

    Example:
        >>> @with_jitter(max_delay_seconds=30.0)
        ... def sync_metrics():
        ...     # 0~30초 사이 무작위 지연 후 실행
        ...     return do_sync()

    환경별 권장 설정:
        - 단일 서버: 0초 (비활성화)
        - K8s 10 Pods: 30초
        - K8s 100+ Pods: 60초
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            jitter = random.uniform(min_delay_seconds, max_delay_seconds)
            logger.debug(f"[Jitter] Sleeping for {jitter:.2f}s before {func.__name__}")
            time.sleep(jitter)
            return func(*args, **kwargs)

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            jitter = random.uniform(min_delay_seconds, max_delay_seconds)
            logger.debug(f"[Jitter] Sleeping for {jitter:.2f}s before {func.__name__}")
            await asyncio.sleep(jitter)
            return await func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper

    return decorator


def calculate_jitter(
    max_delay_seconds: float = 60.0,
    min_delay_seconds: float = 0.0,
) -> float:
    """
    Jitter 지연 시간을 계산합니다.

    데코레이터를 사용할 수 없는 경우 직접 호출하여 사용합니다.

    Args:
        max_delay_seconds: 최대 지연 시간 (초)
        min_delay_seconds: 최소 지연 시간 (초)

    Returns:
        계산된 지연 시간 (초)

    Example:
        >>> delay = calculate_jitter(max_delay_seconds=30.0)
        >>> time.sleep(delay)
        >>> do_sync()
    """
    return random.uniform(min_delay_seconds, max_delay_seconds)


def sleep_with_jitter(
    max_delay_seconds: float = 60.0,
    min_delay_seconds: float = 0.0,
) -> float:
    """
    Jitter를 적용하여 동기적으로 대기합니다.

    Args:
        max_delay_seconds: 최대 지연 시간 (초)
        min_delay_seconds: 최소 지연 시간 (초)

    Returns:
        실제 대기한 시간 (초)

    Example:
        >>> waited = sleep_with_jitter(max_delay_seconds=30.0)
        >>> print(f"Waited {waited:.2f} seconds")
    """
    delay = calculate_jitter(max_delay_seconds, min_delay_seconds)
    time.sleep(delay)
    return delay


async def async_sleep_with_jitter(
    max_delay_seconds: float = 60.0,
    min_delay_seconds: float = 0.0,
) -> float:
    """
    Jitter를 적용하여 비동기적으로 대기합니다.

    Args:
        max_delay_seconds: 최대 지연 시간 (초)
        min_delay_seconds: 최소 지연 시간 (초)

    Returns:
        실제 대기한 시간 (초)

    Example:
        >>> waited = await async_sleep_with_jitter(max_delay_seconds=30.0)
        >>> print(f"Waited {waited:.2f} seconds")
    """
    delay = calculate_jitter(max_delay_seconds, min_delay_seconds)
    await asyncio.sleep(delay)
    return delay


class JitterConfig:
    """
    Jitter 설정 클래스.

    환경 변수 또는 직접 설정으로 Jitter를 구성합니다.
    """

    def __init__(
        self,
        enabled: bool = True,
        max_delay_seconds: float = 60.0,
        min_delay_seconds: float = 0.0,
    ):
        """
        Initialize JitterConfig.

        Args:
            enabled: Jitter 활성화 여부
            max_delay_seconds: 최대 지연 시간
            min_delay_seconds: 최소 지연 시간
        """
        self.enabled = enabled
        self.max_delay_seconds = max_delay_seconds
        self.min_delay_seconds = min_delay_seconds

    @classmethod
    def from_env(cls) -> "JitterConfig":
        """환경 변수에서 설정을 로드합니다."""
        import os

        enabled = os.environ.get("SELFHEALING_METRICS_JITTER_ENABLED", "true").lower() == "true"
        max_delay = float(os.environ.get("SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS", "60.0"))
        min_delay = float(os.environ.get("SELFHEALING_METRICS_JITTER_MIN_DELAY_SECONDS", "0.0"))

        return cls(
            enabled=enabled,
            max_delay_seconds=max_delay,
            min_delay_seconds=min_delay,
        )

    def get_delay(self) -> float:
        """Jitter 지연 시간을 반환합니다 (비활성화 시 0)."""
        if not self.enabled:
            return 0.0
        return calculate_jitter(self.max_delay_seconds, self.min_delay_seconds)

    def sleep(self) -> float:
        """Jitter를 적용하여 대기합니다."""
        delay = self.get_delay()
        if delay > 0:
            time.sleep(delay)
        return delay

    async def async_sleep(self) -> float:
        """Jitter를 적용하여 비동기적으로 대기합니다."""
        delay = self.get_delay()
        if delay > 0:
            await asyncio.sleep(delay)
        return delay


__all__ = [
    "with_jitter",
    "calculate_jitter",
    "sleep_with_jitter",
    "async_sleep_with_jitter",
    "JitterConfig",
]
