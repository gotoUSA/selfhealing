"""
Metric Registration Helpers, Domain Registry, and Label Utilities.

Provides safe metric registration to avoid duplicate registration errors,
dynamic domain management for metric labeling,
label sanitization for Prometheus safety,
and batch metric recording for high-throughput paths.
"""

from __future__ import annotations

import structlog
import queue
import re
import threading
import time
from typing import Any, Callable

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

logger = structlog.get_logger()


# =============================================================================
# Prometheus Label Sanitization
# =============================================================================

_LABEL_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_]")
UNKNOWN_LABEL_VALUE = "unknown"
DEFAULT_LABEL_MAX_LENGTH = 128


def sanitize_label_value(value: str, max_length: int = DEFAULT_LABEL_MAX_LENGTH) -> str:
    """
    Prometheus 메트릭 라벨 값을 안전한 형식으로 정규화.

    영숫자/언더스코어 이외 문자는 '_'로 치환하고,
    최대 길이 128자로 절단하며, 빈 문자열은 'unknown'을 반환합니다.

    Examples:
        >>> sanitize_label_value("my-service.v2")
        'my_service_v2'
        >>> sanitize_label_value("")
        'unknown'
    """
    if not value or not value.strip():
        return UNKNOWN_LABEL_VALUE
    sanitized = _LABEL_UNSAFE_PATTERN.sub("_", value.strip())
    return sanitized[:max_length]


# =============================================================================
# Metrics Batch Recorder (async batch for hot paths)
# =============================================================================


class MetricsBatchRecorder:
    """
    핫 패스에서 메트릭 기록을 비동기 배치로 처리.

    호출 스레드는 SimpleQueue.put()만 수행 (Lock-free, ~50ns).
    백그라운드 데몬 스레드가 100ms 간격 또는 배치 크기 256 도달 시 flush.
    flush 실패 시 해당 배치를 drop하고 경고 로깅 (Fail-Open).
    """

    __slots__ = (
        "_queue",
        "_batch_size",
        "_flush_interval",
        "_worker",
        "_running",
    )

    def __init__(
        self,
        batch_size: int = 256,
        flush_interval_ms: int = 100,
    ) -> None:
        self._queue: queue.SimpleQueue[tuple[Callable, tuple, dict]] = queue.SimpleQueue()
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000.0
        self._running = True
        self._worker = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="metrics-batch-recorder",
        )
        self._worker.start()

    def enqueue(
        self,
        metric_fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        메트릭 기록 요청을 큐에 적재 — Lock-free O(1).

        핫 패스에서 호출. SimpleQueue.put()은 Lock-free이므로
        prometheus_client 내부 Lock 경합을 회피합니다.
        """
        if self._running:
            self._queue.put((metric_fn, args, kwargs))

    def _flush_loop(self) -> None:
        """백그라운드 스레드: 배치 수집 후 일괄 기록."""
        while self._running:
            batch: list[tuple[Callable, tuple, dict]] = []
            deadline = time.monotonic() + self._flush_interval

            while len(batch) < self._batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=max(remaining, 0.001))
                    batch.append(item)
                except Exception:
                    break

            for metric_fn, args, kwargs in batch:
                try:
                    metric_fn(*args, **kwargs)
                except Exception as e:
                    logger.debug(
                        "metrics_batch_recorder.failed_record_metric",
                        error=e,
                    )

    def shutdown(self) -> None:
        """그레이스풀 셧다운 — 잔여 배치 flush."""
        self._running = False
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)


# =============================================================================
# Safe Metric Registration Helpers
# =============================================================================


def get_or_create_counter(name: str, description: str, labels: list[str]) -> Counter:
    """Get existing counter or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Counter(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


def get_or_create_gauge(name: str, description: str, labels: list[str]) -> Gauge:
    """Get existing gauge or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Gauge(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


def get_or_create_histogram(name: str, description: str, labels: list[str], buckets: tuple = None) -> Histogram:
    """Get existing histogram or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        if buckets:
            return Histogram(name, description, labels, buckets=buckets)
        return Histogram(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


# =============================================================================
# Domain Registry (Dynamic Domain Registration)
# =============================================================================

# Registered domains - populated dynamically by adapters at initialization
_registered_domains: set[str] = set()

# Default domains (domain-neutral fallbacks)
DEFAULT_DOMAINS: list[str] = [
    "external_service",
    "internal_process",
    "async_task",
    "notification",
    "data_sync",
]


def register_domain(domain: str) -> None:
    """
    Register a domain for metrics collection.

    Call this from adapters to register application-specific domains.
    Example: register_domain("payment"), register_domain("order")
    """
    _registered_domains.add(domain.lower())


def get_registered_domains() -> list[str]:
    """Get all registered domains, including defaults."""
    all_domains = _registered_domains | set(DEFAULT_DOMAINS)
    return sorted(all_domains)


# Pre-register common domains for backward compatibility
for _domain in DEFAULT_DOMAINS:
    register_domain(_domain)
