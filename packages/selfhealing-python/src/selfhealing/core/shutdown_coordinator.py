"""
Graceful Shutdown Coordinator

Manages graceful shutdown with in-flight request handling:
- Signal handling (SIGTERM, SIGINT)
- Request tracking
- Drain period
- Forced shutdown timeout

Framework-agnostic design.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Callable, Dict, List, Any
import threading
import time
import logging


logger = logging.getLogger(__name__)


class ShutdownPhase(str, Enum):
    """Shutdown process phases"""
    RUNNING = "running"           # 정상 운영 중
    DRAINING = "draining"         # 새 요청 거부, 기존 요청 처리 중
    TERMINATING = "terminating"   # 강제 종료 중
    TERMINATED = "terminated"     # 종료 완료


class RequestState(str, Enum):
    """State of an in-flight request"""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"
    TIMED_OUT = "timed_out"


@dataclass
class TrackedRequest:
    """Information about a tracked in-flight request"""
    request_id: str
    started_at: datetime
    endpoint: str = ""
    method: str = ""
    state: RequestState = RequestState.IN_PROGRESS
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()


@dataclass
class ShutdownStats:
    """Statistics about the shutdown process"""
    phase: ShutdownPhase
    shutdown_started_at: Optional[datetime]
    in_flight_count: int
    completed_during_drain: int
    aborted_count: int
    drain_timeout_seconds: float
    remaining_drain_time: Optional[float]


class ShutdownHandler(ABC):
    """Abstract handler for shutdown actions"""

    @abstractmethod
    def on_shutdown_start(self) -> None:
        """Called when shutdown process begins"""
        pass

    @abstractmethod
    def on_drain_complete(self) -> None:
        """Called when all in-flight requests are drained"""
        pass

    @abstractmethod
    def on_force_shutdown(self, pending_requests: List[TrackedRequest]) -> None:
        """Called when forced shutdown with pending requests"""
        pass


class RequestTracker:
    """
    Tracks in-flight requests for graceful shutdown.

    Usage:
        tracker = RequestTracker()

        # On request start
        tracker.start_request(request_id, endpoint="/api/payment")

        # On request end
        tracker.end_request(request_id, success=True)

        # During shutdown
        pending = tracker.get_pending_requests()
    """

    def __init__(self, max_request_age_seconds: float = 300.0):
        self._requests: Dict[str, TrackedRequest] = {}
        self._lock = threading.Lock()
        self._max_age = max_request_age_seconds
        self._completed_count = 0

    def start_request(
        self,
        request_id: str,
        endpoint: str = "",
        method: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrackedRequest:
        """Start tracking a request"""
        request = TrackedRequest(
            request_id=request_id,
            started_at=datetime.now(timezone.utc),
            endpoint=endpoint,
            method=method,
            metadata=metadata or {},
        )

        with self._lock:
            self._requests[request_id] = request
            # Clean up old requests
            self._cleanup_old_requests()

        return request

    def end_request(
        self,
        request_id: str,
        success: bool = True,
    ) -> Optional[TrackedRequest]:
        """End tracking a request"""
        with self._lock:
            request = self._requests.pop(request_id, None)
            if request:
                request.state = RequestState.COMPLETED if success else RequestState.ABORTED
                self._completed_count += 1
            return request

    def get_pending_requests(self) -> List[TrackedRequest]:
        """Get all pending (in-progress) requests"""
        with self._lock:
            return [
                r for r in self._requests.values()
                if r.state == RequestState.IN_PROGRESS
            ]

    def get_pending_count(self) -> int:
        """Get count of pending requests"""
        with self._lock:
            return sum(
                1 for r in self._requests.values()
                if r.state == RequestState.IN_PROGRESS
            )

    def abort_all(self) -> List[TrackedRequest]:
        """Abort all pending requests"""
        with self._lock:
            aborted = []
            for request in self._requests.values():
                if request.state == RequestState.IN_PROGRESS:
                    request.state = RequestState.ABORTED
                    aborted.append(request)
            return aborted

    def _cleanup_old_requests(self) -> None:
        """Remove requests older than max age"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._max_age)
        old_ids = [
            rid for rid, req in self._requests.items()
            if req.started_at < cutoff and req.state != RequestState.IN_PROGRESS
        ]
        for rid in old_ids:
            del self._requests[rid]

    @property
    def completed_count(self) -> int:
        return self._completed_count


class GracefulShutdownCoordinator:
    """
    Coordinates graceful shutdown process.

    Usage:
        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=30.0,
            shutdown_handler=my_handler,
        )

        # Register signal handlers
        coordinator.register_signals()

        # Or manually trigger
        coordinator.initiate_shutdown()

        # Check if accepting requests
        if coordinator.is_accepting_requests():
            process_request()
    """

    def __init__(
        self,
        request_tracker: RequestTracker,
        drain_timeout: float = 30.0,
        shutdown_handler: Optional[ShutdownHandler] = None,
        on_shutdown_complete: Optional[Callable[[], None]] = None,
        check_interval: float = 0.5,
    ):
        self._tracker = request_tracker
        self._drain_timeout = drain_timeout
        self._handler = shutdown_handler
        self._on_complete = on_shutdown_complete
        self._check_interval = check_interval

        self._phase = ShutdownPhase.RUNNING
        self._shutdown_started_at: Optional[datetime] = None
        self._shutdown_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Stats
        self._drained_count = 0
        self._aborted_count = 0

    @property
    def phase(self) -> ShutdownPhase:
        return self._phase

    def is_accepting_requests(self) -> bool:
        """Check if server should accept new requests"""
        return self._phase == ShutdownPhase.RUNNING

    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress"""
        return self._phase in (ShutdownPhase.DRAINING, ShutdownPhase.TERMINATING)

    def register_signals(self) -> None:
        """Register signal handlers for graceful shutdown"""
        import signal

        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown")
            self.initiate_shutdown()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def initiate_shutdown(self) -> None:
        """Start the graceful shutdown process"""
        with self._lock:
            if self._phase != ShutdownPhase.RUNNING:
                return

            self._phase = ShutdownPhase.DRAINING
            self._shutdown_started_at = datetime.now(timezone.utc)

        logger.info("Graceful shutdown initiated, entering drain phase")

        if self._handler:
            try:
                self._handler.on_shutdown_start()
            except Exception as e:
                logger.error(f"Error in shutdown handler: {e}")

        # Start drain process in background
        self._shutdown_thread = threading.Thread(target=self._drain_and_shutdown)
        self._shutdown_thread.daemon = True
        self._shutdown_thread.start()

    def _drain_and_shutdown(self) -> None:
        """Drain in-flight requests and complete shutdown"""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=self._drain_timeout)

        # Wait for requests to complete or timeout
        while datetime.now(timezone.utc) < deadline:
            pending = self._tracker.get_pending_count()

            if pending == 0:
                logger.info("All in-flight requests drained successfully")
                self._phase = ShutdownPhase.TERMINATED
                self._drained_count = self._tracker.completed_count

                if self._handler:
                    self._handler.on_drain_complete()

                if self._on_complete:
                    self._on_complete()

                return

            logger.debug(f"Waiting for {pending} requests to complete...")
            time.sleep(self._check_interval)

        # Timeout reached, force shutdown
        logger.warning("Drain timeout reached, forcing shutdown")
        self._phase = ShutdownPhase.TERMINATING

        pending_requests = self._tracker.get_pending_requests()
        aborted = self._tracker.abort_all()
        self._aborted_count = len(aborted)

        if self._handler:
            self._handler.on_force_shutdown(pending_requests)

        self._phase = ShutdownPhase.TERMINATED

        if self._on_complete:
            self._on_complete()

    def get_stats(self) -> ShutdownStats:
        """Get current shutdown statistics"""
        remaining = None
        if self._shutdown_started_at and self._phase == ShutdownPhase.DRAINING:
            elapsed = (datetime.now(timezone.utc) - self._shutdown_started_at).total_seconds()
            remaining = max(0, self._drain_timeout - elapsed)

        return ShutdownStats(
            phase=self._phase,
            shutdown_started_at=self._shutdown_started_at,
            in_flight_count=self._tracker.get_pending_count(),
            completed_during_drain=self._drained_count,
            aborted_count=self._aborted_count,
            drain_timeout_seconds=self._drain_timeout,
            remaining_drain_time=remaining,
        )

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """Wait for shutdown to complete. Returns True if completed."""
        if self._shutdown_thread:
            self._shutdown_thread.join(timeout=timeout)
            return self._phase == ShutdownPhase.TERMINATED
        return False
