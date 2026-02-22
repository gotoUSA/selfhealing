"""
Metric Tracking Decorators.

Provides decorators for automatic metric tracking.

Universal Async Support:
- 모든 데코레이터가 동기/비동기 함수 모두 지원
- asyncio.iscoroutinefunction()으로 자동 분기
- with_jitter 패턴과 동일한 구조
"""

from __future__ import annotations

import asyncio
import structlog
import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from selfhealing.metrics.event_handlers import (
    DLQMetricEventHandler,
    ReplayEventHandler,
)

logger = structlog.get_logger()

P = ParamSpec("P")
R = TypeVar("R")


def track_dlq_creation(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 생성 함수에 메트릭 추적을 추가하는 데코레이터 (동기/비동기 지원).

    데코레이터가 적용된 함수가 성공적으로 실행되면
    DLQ 생성 메트릭을 자동으로 기록합니다.

    Args:
        domain: 도메인 이름 (payment, point 등)

    Example:
        >>> @track_dlq_creation(domain="payment")
        ... def create_payment_dlq(failure_type: str, payload: dict):
        ...     return DLQItem.objects.create(...)

        >>> @track_dlq_creation(domain="payment")
        ... async def async_create_dlq(failure_type: str, payload: dict):
        ...     return await DLQItem.objects.acreate(...)
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = func(*args, **kwargs)
            failure_type = kwargs.get("failure_type", "unknown")
            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = await func(*args, **kwargs)
            failure_type = kwargs.get("failure_type", "unknown")
            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper

    return decorator


def track_dlq_resolution(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 해결 함수에 메트릭 추적을 추가하는 데코레이터 (동기/비동기 지원).

    데코레이터가 적용된 함수가 성공적으로 실행되면
    DLQ 해결 메트릭을 자동으로 기록합니다.

    Args:
        domain: 도메인 이름

    Example:
        >>> @track_dlq_resolution(domain="payment")
        ... def resolve_payment_dlq(dlq_item, resolution_type: str = "auto_replay"):
        ...     dlq_item.status = "resolved"
        ...     dlq_item.save()

        >>> @track_dlq_resolution(domain="payment")
        ... async def async_resolve_dlq(dlq_item, resolution_type: str = "auto_replay"):
        ...     dlq_item.status = "resolved"
        ...     await dlq_item.asave()
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.monotonic()
            result = func(*args, **kwargs)
            duration = time.monotonic() - start_time
            resolution_type = kwargs.get("resolution_type", "auto_replay")
            DLQMetricEventHandler.on_item_resolved(
                domain=domain,
                resolution_type=resolution_type,
                duration_seconds=duration,
            )
            return result

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.monotonic()
            result = await func(*args, **kwargs)
            duration = time.monotonic() - start_time
            resolution_type = kwargs.get("resolution_type", "auto_replay")
            DLQMetricEventHandler.on_item_resolved(
                domain=domain,
                resolution_type=resolution_type,
                duration_seconds=duration,
            )
            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper

    return decorator


def track_replay(
    domain: str = "",
    replay_type: str = "auto",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Replay 함수에 메트릭 추적을 추가하는 데코레이터 (동기/비동기 지원).

    Replay 시작/완료를 자동으로 추적하고 소요 시간을 기록합니다.

    Args:
        domain: 도메인 이름 (빈 문자열이면 kwargs에서 추출)
        replay_type: Replay 유형 (auto, manual, batch)

    Example:
        >>> @track_replay(domain="payment")
        ... def sync_replay(dlq_item):
        ...     process_payment(dlq_item.payload)
        ...     return True

        >>> @track_replay(domain="payment")
        ... async def async_replay(dlq_item):
        ...     await process_payment(dlq_item.payload)
        ...     return True
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _domain = domain or kwargs.get("domain", "unknown")
            _replay_type = kwargs.get("replay_type", replay_type)
            ReplayEventHandler.on_replay_started(_domain, _replay_type)

            start_time = time.monotonic()
            success = False
            try:
                result = func(*args, **kwargs)
                success = result if isinstance(result, bool) else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(_domain, success, duration)

        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            _domain = domain or kwargs.get("domain", "unknown")
            _replay_type = kwargs.get("replay_type", replay_type)
            ReplayEventHandler.on_replay_started(_domain, _replay_type)

            start_time = time.monotonic()
            success = False
            try:
                result = await func(*args, **kwargs)
                success = result if isinstance(result, bool) else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(_domain, success, duration)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper

    return decorator


def track_execution_time(
    metric_name: str,
    labels: dict[str, str] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    함수 실행 시간을 Histogram으로 기록하는 데코레이터.

    Args:
        metric_name: 메트릭 이름 (예: "processing_time_seconds")
        labels: 추가할 라벨

    Example:
        >>> @track_execution_time("payment_processing_seconds", labels={"type": "credit"})
        ... def process_payment(amount: float):
        ...     # 결제 처리
        ...     pass
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.monotonic() - start_time
                # 메트릭 기록은 구체적인 구현에서 처리
                logger.debug(
                    f"[Metrics] {metric_name}: {duration:.4f}s, labels={labels}"
                )

        return wrapper

    return decorator


def track_counter(
    metric_name: str,
    labels: dict[str, str] | None = None,
    on_success: bool = True,
    on_failure: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    함수 호출을 Counter로 기록하는 데코레이터.

    Args:
        metric_name: 메트릭 이름
        labels: 추가할 라벨
        on_success: 성공 시 카운트 증가
        on_failure: 실패 시 카운트 증가

    Example:
        >>> @track_counter("api_calls_total", labels={"endpoint": "/payment"})
        ... def payment_api(data: dict):
        ...     return process(data)
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                result = func(*args, **kwargs)
                if on_success:
                    logger.debug(
                        f"[Metrics] Counter {metric_name}.inc(), labels={labels}"
                    )
                return result
            except Exception:
                if on_failure:
                    logger.debug(
                        f"[Metrics] Counter {metric_name}.inc() (failure), labels={labels}"
                    )
                raise

        return wrapper

    return decorator


__all__ = [
    "track_dlq_creation",
    "track_dlq_resolution",
    "track_replay",
    "track_execution_time",
    "track_counter",
]
