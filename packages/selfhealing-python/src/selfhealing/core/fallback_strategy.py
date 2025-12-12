"""
Fallback Strategy for Partial Partitions

Provides graceful degradation strategies when connections fail:
- Cache miss → DB fallback
- External API down → cached/default response
- Message queue down → sync processing

Reference: docs/STAGE_24_PARTIAL_PARTITION.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar, Generic
from enum import Enum
import logging

from .connection_health import PartitionState, ConnectionType


logger = logging.getLogger(__name__)

T = TypeVar("T")


class FallbackMode(str, Enum):
    """Fallback behavior modes"""

    FAIL_FAST = "fail_fast"  # 즉시 실패
    USE_CACHE = "use_cache"  # 캐시된 값 사용
    USE_DEFAULT = "use_default"  # 기본값 사용
    DEGRADE_GRACEFULLY = "degrade"  # 기능 축소
    RETRY_ALTERNATIVE = "retry_alt"  # 대체 경로 시도


@dataclass
class FallbackResult(Generic[T]):
    """Result of a fallback operation"""

    value: Optional[T]
    used_fallback: bool
    fallback_mode: Optional[FallbackMode] = None
    original_error: Optional[str] = None

    @property
    def success(self) -> bool:
        """True if we have a value (either primary or fallback)"""
        return self.value is not None or (self.used_fallback and self.fallback_mode != FallbackMode.FAIL_FAST)


class FallbackStrategy(ABC):
    """Abstract fallback strategy"""

    @abstractmethod
    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        """Execute with fallback"""
        pass


class SimpleFallback(FallbackStrategy):
    """Simple fallback that tries fallback_fn then default_value."""

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        try:
            result = primary_fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            logger.warning(f"Primary function failed: {e}")

            # Try explicit fallback
            if fallback_fn:
                try:
                    result = fallback_fn()
                    return FallbackResult(
                        value=result, used_fallback=True, fallback_mode=FallbackMode.RETRY_ALTERNATIVE, original_error=str(e)
                    )
                except Exception as fallback_e:
                    logger.warning(f"Fallback function also failed: {fallback_e}")

            # Use default value
            if default_value is not None:
                return FallbackResult(
                    value=default_value, used_fallback=True, fallback_mode=FallbackMode.USE_DEFAULT, original_error=str(e)
                )

            # All failed
            return FallbackResult(value=None, used_fallback=True, fallback_mode=FallbackMode.FAIL_FAST, original_error=str(e))


class PartitionAwareFallback(FallbackStrategy):
    """
    Fallback strategy aware of partition state.
    Automatically selects fallback based on which connections are available.
    """

    def __init__(
        self,
        partition_state: PartitionState,
        cache_fallback: Optional[Callable[[], Any]] = None,
        db_fallback: Optional[Callable[[], Any]] = None,
    ):
        """
        Initialize partition-aware fallback.

        Args:
            partition_state: Current partition state
            cache_fallback: Function to get data from cache
            db_fallback: Function to get data from database
        """
        self._partition_state = partition_state
        self._cache_fallback = cache_fallback
        self._db_fallback = db_fallback

    def execute(
        self,
        primary_fn: Callable[[], T],
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        try:
            result = primary_fn()
            return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            logger.warning(f"Primary operation failed: {e}")
            return self._handle_failure(e, fallback_fn, default_value)

    def _handle_failure(
        self,
        error: Exception,
        fallback_fn: Optional[Callable[[], T]],
        default_value: Optional[T],
    ) -> FallbackResult[T]:
        # 1. 명시적 fallback 함수가 있으면 시도
        if fallback_fn:
            try:
                result = fallback_fn()
                return FallbackResult(
                    value=result, used_fallback=True, fallback_mode=FallbackMode.RETRY_ALTERNATIVE, original_error=str(error)
                )
            except Exception as e:
                logger.warning(f"Explicit fallback failed: {e}")

        # 2. 캐시 사용 불가 + DB 가용 → DB fallback
        if not self._partition_state.cache_available and self._partition_state.db_available:
            if self._db_fallback:
                try:
                    result = self._db_fallback()
                    logger.info("Using DB fallback due to cache unavailability")
                    return FallbackResult(
                        value=result,
                        used_fallback=True,
                        fallback_mode=FallbackMode.DEGRADE_GRACEFULLY,
                        original_error=str(error),
                    )
                except Exception as e:
                    logger.warning(f"DB fallback failed: {e}")

        # 3. DB 사용 불가 + 캐시 가용 → 캐시 fallback
        if not self._partition_state.db_available and self._partition_state.cache_available:
            if self._cache_fallback:
                try:
                    result = self._cache_fallback()
                    logger.info("Using cache fallback due to DB unavailability")
                    return FallbackResult(
                        value=result, used_fallback=True, fallback_mode=FallbackMode.USE_CACHE, original_error=str(error)
                    )
                except Exception as e:
                    logger.warning(f"Cache fallback failed: {e}")

        # 4. 기본값 반환
        if default_value is not None:
            logger.info("Using default value as all fallbacks exhausted")
            return FallbackResult(
                value=default_value, used_fallback=True, fallback_mode=FallbackMode.USE_DEFAULT, original_error=str(error)
            )

        # 5. 모든 fallback 실패
        logger.error(f"All fallback strategies failed. Original error: {error}")
        return FallbackResult(value=None, used_fallback=True, fallback_mode=FallbackMode.FAIL_FAST, original_error=str(error))

    def update_partition_state(self, new_state: PartitionState) -> None:
        """Update the partition state for dynamic adjustment."""
        self._partition_state = new_state


class CacheFirstFallback(FallbackStrategy):
    """
    Fallback strategy that tries cache first, then DB.
    Useful for read-heavy operations.
    """

    def __init__(
        self,
        cache_fn: Callable[[], T],
        db_fn: Callable[[], T],
        update_cache_fn: Optional[Callable[[T], None]] = None,
    ):
        """
        Initialize cache-first fallback.

        Args:
            cache_fn: Function to get from cache
            db_fn: Function to get from database
            update_cache_fn: Optional function to update cache after DB read
        """
        self._cache_fn = cache_fn
        self._db_fn = db_fn
        self._update_cache_fn = update_cache_fn

    def execute(
        self,
        primary_fn: Optional[Callable[[], T]] = None,
        fallback_fn: Optional[Callable[[], T]] = None,
        default_value: Optional[T] = None,
    ) -> FallbackResult[T]:
        # Try cache first
        try:
            result = self._cache_fn()
            if result is not None:
                return FallbackResult(value=result, used_fallback=False)
        except Exception as e:
            logger.debug(f"Cache lookup failed: {e}")

        # Cache miss or error, try DB
        try:
            result = self._db_fn()
            if result is not None and self._update_cache_fn:
                try:
                    self._update_cache_fn(result)
                except Exception as e:
                    logger.warning(f"Failed to update cache: {e}")

            return FallbackResult(
                value=result,
                used_fallback=True,
                fallback_mode=FallbackMode.DEGRADE_GRACEFULLY,
            )
        except Exception as e:
            logger.warning(f"DB lookup failed: {e}")

        # Both failed
        if default_value is not None:
            return FallbackResult(
                value=default_value,
                used_fallback=True,
                fallback_mode=FallbackMode.USE_DEFAULT,
            )

        return FallbackResult(
            value=None,
            used_fallback=True,
            fallback_mode=FallbackMode.FAIL_FAST,
        )
