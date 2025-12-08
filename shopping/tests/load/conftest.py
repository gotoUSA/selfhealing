"""
Load Test Fixtures

Provides specialized fixtures for load and stress testing including
concurrent execution utilities, queue simulators, and metrics trackers.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable
import threading
import time

import pytest
from django.utils import timezone


# =============================================================================
# Load Test Utilities
# =============================================================================


@dataclass
class ConcurrentExecutionResult:
    """
    Results from concurrent execution.

    Tracks success/failure counts and timing.
    """

    total_operations: int = 0
    successful: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    execution_times: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_operations == 0:
            return 0.0
        return self.successful / self.total_operations

    @property
    def failure_rate(self) -> float:
        """Calculate failure rate."""
        if self.total_operations == 0:
            return 0.0
        return self.failed / self.total_operations

    @property
    def total_duration(self) -> float:
        """Total execution duration in seconds."""
        return self.end_time - self.start_time

    @property
    def avg_execution_time(self) -> float:
        """Average execution time per operation."""
        if not self.execution_times:
            return 0.0
        return sum(self.execution_times) / len(self.execution_times)

    @property
    def throughput(self) -> float:
        """Operations per second."""
        if self.total_duration == 0:
            return 0.0
        return self.total_operations / self.total_duration


class ConcurrentExecutor:
    """
    Executes operations concurrently for load testing.

    Tracks results and provides metrics.
    """

    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        self.results = ConcurrentExecutionResult()
        self._lock = threading.Lock()

    def execute(
        self,
        operation: Callable[[], bool],
        count: int,
    ) -> ConcurrentExecutionResult:
        """
        Execute operation concurrently.

        Args:
            operation: Callable that returns True on success
            count: Number of times to execute

        Returns:
            ConcurrentExecutionResult with metrics
        """
        self.results = ConcurrentExecutionResult()
        self.results.total_operations = count
        self.results.start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._execute_one, operation) for _ in range(count)]

            for future in as_completed(futures):
                try:
                    success, execution_time = future.result()
                    with self._lock:
                        if success:
                            self.results.successful += 1
                        else:
                            self.results.failed += 1
                        self.results.execution_times.append(execution_time)
                except Exception as e:
                    with self._lock:
                        self.results.failed += 1
                        self.results.errors.append(str(e))

        self.results.end_time = time.time()
        return self.results

    def _execute_one(self, operation: Callable[[], bool]) -> tuple[bool, float]:
        """Execute single operation and measure time."""
        start = time.time()
        try:
            success = operation()
            return success, time.time() - start
        except Exception:
            return False, time.time() - start


@dataclass
class LoadTestQueue:
    """
    Thread-safe queue for load testing.

    Simulates DLQ/retry queue with capacity limits.
    """

    max_size: int = 10000
    _items: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    enqueue_count: int = 0
    dequeue_count: int = 0
    overflow_count: int = 0
    peak_size: int = 0

    def enqueue(self, item: dict) -> bool:
        """
        Thread-safe enqueue.

        Returns True if successful, False if queue full.
        """
        with self._lock:
            if len(self._items) >= self.max_size:
                self.overflow_count += 1
                return False
            self._items.append(item)
            self.enqueue_count += 1
            self.peak_size = max(self.peak_size, len(self._items))
            return True

    def dequeue(self) -> dict | None:
        """Thread-safe dequeue."""
        with self._lock:
            if self._items:
                self.dequeue_count += 1
                return self._items.pop(0)
            return None

    @property
    def size(self) -> int:
        """Current queue size."""
        with self._lock:
            return len(self._items)

    def clear(self) -> int:
        """Clear queue and return count."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def get_stats(self) -> dict:
        """Get queue statistics."""
        return {
            "current_size": self.size,
            "peak_size": self.peak_size,
            "enqueue_count": self.enqueue_count,
            "dequeue_count": self.dequeue_count,
            "overflow_count": self.overflow_count,
            "utilization": self.size / self.max_size if self.max_size > 0 else 0,
        }


@dataclass
class SLATracker:
    """
    Tracks SLA compliance for load tests.

    Records timing and calculates percentiles.
    """

    sla_threshold_ms: int = 5000  # 5 second SLA
    measurements: list = field(default_factory=list)
    breaches: list = field(default_factory=list)

    def record(self, duration_ms: float) -> bool:
        """
        Record a measurement.

        Returns True if within SLA, False if breached.
        """
        self.measurements.append({
            "duration_ms": duration_ms,
            "timestamp": timezone.now(),
        })

        if duration_ms > self.sla_threshold_ms:
            self.breaches.append({
                "duration_ms": duration_ms,
                "exceeded_by_ms": duration_ms - self.sla_threshold_ms,
                "timestamp": timezone.now(),
            })
            return False
        return True

    @property
    def breach_count(self) -> int:
        """Number of SLA breaches."""
        return len(self.breaches)

    @property
    def compliance_rate(self) -> float:
        """SLA compliance rate."""
        if not self.measurements:
            return 1.0
        return 1 - (self.breach_count / len(self.measurements))

    def get_percentile(self, p: float) -> float:
        """Get the p-th percentile duration."""
        if not self.measurements:
            return 0.0
        sorted_durations = sorted(m["duration_ms"] for m in self.measurements)
        idx = int(len(sorted_durations) * p / 100)
        return sorted_durations[min(idx, len(sorted_durations) - 1)]

    @property
    def p50(self) -> float:
        """50th percentile (median)."""
        return self.get_percentile(50)

    @property
    def p95(self) -> float:
        """95th percentile."""
        return self.get_percentile(95)

    @property
    def p99(self) -> float:
        """99th percentile."""
        return self.get_percentile(99)

    def get_stats(self) -> dict:
        """Get SLA statistics."""
        return {
            "total_measurements": len(self.measurements),
            "breach_count": self.breach_count,
            "compliance_rate": self.compliance_rate,
            "p50_ms": self.p50,
            "p95_ms": self.p95,
            "p99_ms": self.p99,
            "sla_threshold_ms": self.sla_threshold_ms,
        }


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def concurrent_executor():
    """Provides a concurrent executor for load tests."""
    return ConcurrentExecutor(max_workers=20)


@pytest.fixture
def load_test_queue():
    """Provides a thread-safe queue for load tests."""
    return LoadTestQueue(max_size=10000)


@pytest.fixture
def sla_tracker():
    """Provides an SLA tracker for load tests."""
    return SLATracker(sla_threshold_ms=5000)


@pytest.fixture
def high_volume_sla_tracker():
    """Provides an SLA tracker with tighter thresholds."""
    return SLATracker(sla_threshold_ms=100)  # 100ms for high-volume
