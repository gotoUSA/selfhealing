"""
Metric Tracking Decorators.

Provides decorators for automatic metric tracking.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, Optional, TypeVar, ParamSpec, Any

from selfhealing.metrics.event_handlers import (
    DLQMetricEventHandler,
    ReplayEventHandler,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def track_dlq_creation(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 생성 함수에 메트릭 추적을 추가하는 데코레이터.

    데코레이터가 적용된 함수가 성공적으로 실행되면
    DLQ 생성 메트릭을 자동으로 기록합니다.

    Args:
        domain: 도메인 이름 (payment, point 등)

    Example:
        >>> @track_dlq_creation(domain="payment")
        ... def create_payment_dlq(failure_type: str, payload: dict):
        ...     return DLQItem.objects.create(
        ...         domain="payment",
        ...         failure_type=failure_type,
        ...         payload=payload,
        ...     )
        >>>
        >>> item = create_payment_dlq(failure_type="PG_TIMEOUT", payload={"order_id": "123"})
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = func(*args, **kwargs)

            # failure_type은 키워드 인자에서 추출
            failure_type = kwargs.get("failure_type", "unknown")

            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result

        return wrapper

    return decorator


def track_dlq_resolution(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    DLQ 해결 함수에 메트릭 추적을 추가하는 데코레이터.

    데코레이터가 적용된 함수가 성공적으로 실행되면
    DLQ 해결 메트릭을 자동으로 기록합니다.

    Args:
        domain: 도메인 이름

    Example:
        >>> @track_dlq_resolution(domain="payment")
        ... def resolve_payment_dlq(dlq_item, resolution_type: str = "auto_replay"):
        ...     dlq_item.status = "resolved"
        ...     dlq_item.save()
        ...     return dlq_item
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
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

        return wrapper

    return decorator


def track_replay(domain: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Replay 함수에 메트릭 추적을 추가하는 데코레이터.

    Replay 시작/완료를 자동으로 추적하고 소요 시간을 기록합니다.

    Args:
        domain: 도메인 이름

    Example:
        >>> @track_replay(domain="payment")
        ... async def replay_payment(dlq_item):
        ...     # Replay 로직
        ...     await process_payment(dlq_item.payload)
        ...     return True
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            replay_type = kwargs.get("replay_type", "auto")
            ReplayEventHandler.on_replay_started(domain, replay_type)

            start_time = time.monotonic()
            success = False
            try:
                result = func(*args, **kwargs)
                # 결과가 boolean이면 그대로 사용, 아니면 성공으로 간주
                success = result if isinstance(result, bool) else True
                return result
            except Exception:
                success = False
                raise
            finally:
                duration = time.monotonic() - start_time
                ReplayEventHandler.on_replay_completed(domain, success, duration)

        return wrapper

    return decorator


def track_execution_time(
    metric_name: str,
    labels: Optional[dict[str, str]] = None,
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
    labels: Optional[dict[str, str]] = None,
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
