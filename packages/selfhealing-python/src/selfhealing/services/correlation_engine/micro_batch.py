"""
Micro-Batch Consumer — WildcardObserver._consumer_loop() 패턴 확장.

이벤트를 짧은 시간 윈도우(10~50ms) 동안 큐에 모은 뒤
detect_batch()로 일괄 처리하는 마이크로배칭 메커니즘이다.

선례:
    - WildcardObserver._consumer_loop(): queue.get(timeout=1.0) 루프 + EventWindow 버퍼
    - AsyncHealingLogger: PriorityQueue + 배치 플러시 + BatchRetryPolicy

Usage:
    from selfhealing.services.correlation_engine.micro_batch import (
        MicroBatchConsumer,
    )

    consumer = MicroBatchConsumer(strategy, flush_interval_ms=50.0)
    consumer.start()
    consumer.submit(42.0, {"service": "payment"})
    consumer.stop()
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

import structlog

from selfhealing.interfaces.ml_strategy import (
    AnomalyDetectionStrategy,
    BatchCapable,
)

logger = structlog.get_logger()


# =============================================================================
# 상수
# =============================================================================

DEFAULT_FLUSH_INTERVAL_MS = 50.0
"""기본 마이크로배치 플러시 간격 (밀리초)"""

DEFAULT_MAX_BATCH_SIZE = 128
"""기본 최대 배치 크기"""

DEFAULT_QUEUE_MAX_SIZE = 10000
"""기본 큐 최대 크기"""

QUEUE_POLL_TIMEOUT = 0.01
"""큐 폴링 타임아웃 (초) — 10ms"""


# =============================================================================
# MicroBatchConsumer
# =============================================================================


class MicroBatchConsumer:
    """마이크로배칭 Consumer — WildcardObserver._consumer_loop() 패턴 확장.

    BatchCapable 전략이면 detect_batch()로 배치 처리,
    아니면 단건 detect() 루프로 처리한다.

    Hot Path: submit()은 O(1) 큐 삽입만 수행.
    Cold Path: daemon 스레드에서 마이크로배치 수집 + 일괄 추론.

    Args:
        strategy: 이상 탐지 전략
        flush_interval_ms: 배치 플러시 간격 (밀리초)
        max_batch_size: 최대 배치 크기
        queue_max_size: 큐 최대 크기
    """

    def __init__(
        self,
        strategy: AnomalyDetectionStrategy,
        flush_interval_ms: float = DEFAULT_FLUSH_INTERVAL_MS,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        queue_max_size: int = DEFAULT_QUEUE_MAX_SIZE,
    ) -> None:
        self._strategy = strategy
        self._flush_interval = flush_interval_ms / 1000.0
        self._max_batch_size = max_batch_size
        self._queue: queue.Queue[tuple[float, dict[str, Any] | None]] = queue.Queue(maxsize=queue_max_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._dropped_count = 0
        self._processed_count = 0
        self._flush_count = 0

    # ─────────────────────────────────────────────
    # Hot Path — O(1) 큐 삽입
    # ─────────────────────────────────────────────

    def submit(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Hot Path — O(1) 큐 삽입. WildcardObserver._on_event() 패턴.

        큐가 가득 차면 Fail-Open 전략으로 드랍한다.
        """
        try:
            self._queue.put_nowait((value, context))
        except queue.Full:
            self._dropped_count += 1

    # ─────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────

    def start(self) -> None:
        """daemon 스레드 시작."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._consumer_loop,
            daemon=True,
            name="MicroBatchConsumer",
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """daemon 스레드 중지.

        Args:
            timeout: 스레드 종료 대기 타임아웃 (초)
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def is_running(self) -> bool:
        """Consumer 스레드 실행 중 여부."""
        return self._thread is not None and self._thread.is_alive()

    # ─────────────────────────────────────────────
    # Cold Path — 마이크로배치 수집 + 일괄 추론
    # ─────────────────────────────────────────────

    def _consumer_loop(self) -> None:
        """daemon 스레드 — 마이크로배치 수집 + 일괄 추론."""
        batch_values: list[float] = []
        batch_contexts: list[dict[str, Any] | None] = []
        last_flush = time.monotonic()

        while not self._stop_event.is_set():
            try:
                value, ctx = self._queue.get(timeout=QUEUE_POLL_TIMEOUT)
                batch_values.append(value)
                batch_contexts.append(ctx)
            except queue.Empty:
                pass

            elapsed = time.monotonic() - last_flush
            should_flush = len(batch_values) >= self._max_batch_size or (batch_values and elapsed >= self._flush_interval)

            if should_flush:
                self._flush_batch(batch_values, batch_contexts)
                batch_values = []
                batch_contexts = []
                last_flush = time.monotonic()

        # 종료 전 잔여 배치 처리
        if batch_values:
            self._flush_batch(batch_values, batch_contexts)

    def _flush_batch(
        self,
        values: list[float],
        contexts: list[dict[str, Any] | None],
    ) -> None:
        """배치 추론 실행 — BatchCapable 분기."""
        try:
            if isinstance(self._strategy, BatchCapable):
                self._strategy.detect_batch(values, contexts)
            else:
                for v, c in zip(values, contexts):
                    self._strategy.detect(v, c)
            self._processed_count += len(values)
            self._flush_count += 1
        except Exception:
            logger.exception("micro_batch_consumer.batch_flush_failed")

    # ─────────────────────────────────────────────
    # 메트릭
    # ─────────────────────────────────────────────

    @property
    def dropped_count(self) -> int:
        """큐 포화로 드랍된 이벤트 수."""
        return self._dropped_count

    @property
    def processed_count(self) -> int:
        """처리 완료된 이벤트 수."""
        return self._processed_count

    @property
    def flush_count(self) -> int:
        """실행된 배치 플러시 횟수."""
        return self._flush_count

    @property
    def queue_size(self) -> int:
        """현재 큐에 대기 중인 이벤트 수."""
        return self._queue.qsize()
