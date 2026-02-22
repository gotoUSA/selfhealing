"""
Async Audit Writer (Non-blocking writes).

Provides asynchronous audit writing with background thread.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger()


class AsyncAuditWriter:
    """
    Async audit writer with background thread.

    Problem:
        Synchronous file/Redis writes block request handling.
        Write latency directly impacts API response time.

    Solution:
        Queue entries for background thread processing.
        Request handler returns immediately after queueing.

    Pattern source:
        audit/audit_watchdog.py#L150-270 (daemon thread pattern)

    Usage:
        writer = AsyncAuditWriter(sync_writer)
        writer.start()
        writer.write_async(entry)  # Non-blocking
    """

    def __init__(
        self,
        sync_writer: Callable[[dict[str, Any]], bool],
        max_queue_size: int = 10000,
        batch_size: int = 50,
        flush_interval_seconds: float = 0.1,
    ):
        """
        Initialize async audit writer.

        Args:
            sync_writer: Synchronous write function to wrap
            max_queue_size: Maximum queue size before blocking
            batch_size: Number of entries to write per batch
            flush_interval_seconds: Max time between writes
        """
        self._sync_writer = sync_writer
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._entries_queued = 0
        self._entries_written = 0
        self._entries_dropped = 0

    def start(self) -> None:
        """Start background writer thread."""
        if self._is_running:
            return

        self._is_running = True
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="AsyncAuditWriter",
        )
        self._thread.start()
        logger.info("started")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background writer thread."""
        if not self._is_running:
            return

        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=timeout)

        self._is_running = False
        logger.info(
            "async_audit_writer.stopped_queued_written",
            _self=self._entries_queued,
            self_1=self._entries_written,
        )

    def write_async(
        self,
        entry: dict[str, Any],
        block: bool = False,
    ) -> bool:
        """
        Queue entry for async write.

        Args:
            entry: Entry to write
            block: Block if queue is full (default: drop)

        Returns:
            True if queued successfully
        """
        try:
            self._queue.put(entry, block=block, timeout=0.01)
            self._entries_queued += 1
            return True
        except queue.Full:
            self._entries_dropped += 1
            logger.warning(
                "async_audit_writer.queue_full_entry_dropped",
                _self=self._entries_dropped,
            )
            return False

    def _writer_loop(self) -> None:
        """Background writer loop."""
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()

        while not self._stop_event.is_set():
            try:
                # Collect batch
                try:
                    entry = self._queue.get(timeout=self._flush_interval)
                    batch.append(entry)
                except queue.Empty:
                    pass

                # Check flush conditions
                should_flush = len(batch) >= self._batch_size or (
                    batch and time.monotonic() - last_flush >= self._flush_interval
                )

                if should_flush and batch:
                    self._flush_batch(batch)
                    batch = []
                    last_flush = time.monotonic()

            except Exception as e:
                logger.exception(
                    "async_audit_writer.writer_loop_error",
                    error=e,
                )

        # Final flush on stop
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Flush a batch of entries."""
        for entry in batch:
            try:
                if self._sync_writer(entry):
                    self._entries_written += 1
            except Exception as e:
                logger.exception(
                    "async_audit_writer.write_failed",
                    error=e,
                )

    def get_stats(self) -> dict[str, Any]:
        """Get writer statistics."""
        return {
            "queued": self._entries_queued,
            "written": self._entries_written,
            "dropped": self._entries_dropped,
            "queue_size": self._queue.qsize(),
            "is_running": self._is_running,
        }
