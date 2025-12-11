# Stage 27: Graceful Shutdown + In-flight Request 처리

## 🎯 목표

배포/재시작 시 진행 중인 요청을 안전하게 처리하고 데이터 손실 방지

## 📋 실제 장애 사례

- **2024년 쿠팡 롤링 배포 중 장애**: 새 버전 올라가는데 기존 요청이 rollback 안 되고 날아감

---

## 🏗️ 구현 내용

### 1. Shutdown Coordinator

**파일**: `packages/selfhealing-python/src/selfhealing/core/shutdown_coordinator.py`

```python
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
from typing import Optional, Callable, Dict, Set, Any
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
    def on_force_shutdown(self, pending_requests: list[TrackedRequest]) -> None:
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

    def get_pending_requests(self) -> list[TrackedRequest]:
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

    def abort_all(self) -> list[TrackedRequest]:
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
        logger.warning(f"Drain timeout reached, forcing shutdown")
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
```

---

### 2. Request Context Middleware (프레임워크 독립)

**파일**: `packages/selfhealing-python/src/selfhealing/core/request_context.py`

```python
"""
Request Context for Graceful Shutdown

Provides context manager and utilities for request tracking.
Framework adapters can use this for integration.
"""

from contextlib import contextmanager
from typing import Optional, Generator, Any, Dict
import uuid

from .shutdown_coordinator import RequestTracker, TrackedRequest


class RequestContext:
    """
    Context for tracking a single request.

    Usage:
        tracker = RequestTracker()

        with RequestContext(tracker, endpoint="/api/orders") as ctx:
            # Process request
            result = process_order()
            ctx.set_metadata("order_id", result.id)
    """

    def __init__(
        self,
        tracker: RequestTracker,
        request_id: Optional[str] = None,
        endpoint: str = "",
        method: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self._tracker = tracker
        self._request_id = request_id or str(uuid.uuid4())
        self._endpoint = endpoint
        self._method = method
        self._metadata = metadata or {}
        self._tracked: Optional[TrackedRequest] = None
        self._success = True

    @property
    def request_id(self) -> str:
        return self._request_id

    def set_metadata(self, key: str, value: Any) -> None:
        """Add metadata to the request"""
        self._metadata[key] = value
        if self._tracked:
            self._tracked.metadata[key] = value

    def mark_failed(self) -> None:
        """Mark the request as failed"""
        self._success = False

    def __enter__(self) -> "RequestContext":
        self._tracked = self._tracker.start_request(
            request_id=self._request_id,
            endpoint=self._endpoint,
            method=self._method,
            metadata=self._metadata,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self._success = False

        self._tracker.end_request(
            request_id=self._request_id,
            success=self._success,
        )


@contextmanager
def track_request(
    tracker: RequestTracker,
    request_id: Optional[str] = None,
    endpoint: str = "",
    method: str = "",
) -> Generator[RequestContext, None, None]:
    """
    Context manager for request tracking.

    Usage:
        with track_request(tracker, endpoint="/api/pay") as ctx:
            process_payment()
    """
    ctx = RequestContext(
        tracker=tracker,
        request_id=request_id,
        endpoint=endpoint,
        method=method,
    )
    with ctx:
        yield ctx
```

---

### 3. 테스트 케이스

**파일**: `packages/selfhealing-python/tests/unit/test_graceful_shutdown.py`

```python
"""
Stage 27: Graceful Shutdown Tests

Scenarios:
1. Normal request tracking
2. Graceful shutdown with drain
3. Timeout and forced shutdown
4. Signal handling
5. Request context manager
"""

import pytest
import time
import threading
from datetime import datetime, timezone
from unittest.mock import Mock

from selfhealing.core.shutdown_coordinator import (
    ShutdownPhase,
    RequestState,
    RequestTracker,
    GracefulShutdownCoordinator,
    ShutdownHandler,
)
from selfhealing.core.request_context import (
    RequestContext,
    track_request,
)


class TestRequestTracking:
    """Request tracking tests"""

    def test_track_request_lifecycle(self):
        """요청 추적 라이프사이클"""
        tracker = RequestTracker()

        # 요청 시작
        request = tracker.start_request(
            request_id="req_001",
            endpoint="/api/orders",
            method="POST",
        )

        assert request.state == RequestState.IN_PROGRESS
        assert tracker.get_pending_count() == 1

        # 요청 완료
        tracker.end_request("req_001", success=True)

        assert tracker.get_pending_count() == 0

    def test_multiple_concurrent_requests(self):
        """동시 다중 요청 추적"""
        tracker = RequestTracker()

        # 여러 요청 시작
        for i in range(10):
            tracker.start_request(f"req_{i}")

        assert tracker.get_pending_count() == 10

        # 일부 완료
        for i in range(5):
            tracker.end_request(f"req_{i}")

        assert tracker.get_pending_count() == 5

    def test_abort_all_requests(self):
        """모든 요청 중단"""
        tracker = RequestTracker()

        for i in range(5):
            tracker.start_request(f"req_{i}")

        aborted = tracker.abort_all()

        assert len(aborted) == 5
        assert all(r.state == RequestState.ABORTED for r in aborted)


class TestGracefulShutdown:
    """Graceful shutdown coordinator tests"""

    def test_normal_shutdown_no_pending(self):
        """대기 요청 없을 때 정상 종료"""
        tracker = RequestTracker()
        on_complete = Mock()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
            on_shutdown_complete=on_complete,
        )

        assert coordinator.is_accepting_requests() is True

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        assert on_complete.called

    def test_drain_pending_requests(self):
        """대기 요청 드레인"""
        tracker = RequestTracker()

        # 요청 시작
        tracker.start_request("req_001")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        # 종료 시작
        coordinator.initiate_shutdown()

        assert coordinator.is_accepting_requests() is False
        assert coordinator.is_shutting_down() is True

        # 요청 완료
        tracker.end_request("req_001")

        # 종료 대기
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        stats = coordinator.get_stats()
        assert stats.aborted_count == 0

    def test_timeout_forces_shutdown(self):
        """타임아웃 시 강제 종료"""
        tracker = RequestTracker()

        # 완료되지 않을 요청
        tracker.start_request("slow_req")

        handler = Mock(spec=ShutdownHandler)

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=0.5,  # 짧은 타임아웃
            shutdown_handler=handler,
        )

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        assert handler.on_force_shutdown.called

        stats = coordinator.get_stats()
        assert stats.aborted_count == 1

    def test_reject_new_requests_during_drain(self):
        """드레인 중 새 요청 거부"""
        tracker = RequestTracker()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        assert coordinator.is_accepting_requests() is True

        coordinator.initiate_shutdown()

        assert coordinator.is_accepting_requests() is False


class TestRequestContext:
    """Request context manager tests"""

    def test_context_manager_tracking(self):
        """컨텍스트 매니저로 요청 추적"""
        tracker = RequestTracker()

        with RequestContext(tracker, endpoint="/api/test") as ctx:
            assert tracker.get_pending_count() == 1
            ctx.set_metadata("user_id", 123)

        assert tracker.get_pending_count() == 0

    def test_context_manager_exception_handling(self):
        """예외 발생 시 실패로 마킹"""
        tracker = RequestTracker()

        try:
            with RequestContext(tracker, request_id="fail_req") as ctx:
                raise ValueError("Test error")
        except ValueError:
            pass

        # 요청은 종료되었지만 실패로 기록됨
        assert tracker.get_pending_count() == 0

    def test_track_request_helper(self):
        """track_request 헬퍼 함수"""
        tracker = RequestTracker()

        with track_request(tracker, endpoint="/api/orders") as ctx:
            assert ctx.request_id is not None
            assert tracker.get_pending_count() == 1

        assert tracker.get_pending_count() == 0


class TestShutdownIntegration:
    """통합 시나리오 테스트"""

    def test_realistic_shutdown_scenario(self):
        """
        실제 시나리오: 배포 중 graceful shutdown
        - 진행 중인 요청 3개
        - 2개는 정상 완료
        - 1개는 타임아웃으로 중단
        """
        tracker = RequestTracker()

        # 진행 중인 요청들
        tracker.start_request("fast_1")
        tracker.start_request("fast_2")
        tracker.start_request("slow_1")  # 이건 완료 안 됨

        shutdown_log = []

        class TestHandler(ShutdownHandler):
            def on_shutdown_start(self):
                shutdown_log.append("start")

            def on_drain_complete(self):
                shutdown_log.append("drained")

            def on_force_shutdown(self, pending):
                shutdown_log.append(f"forced:{len(pending)}")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=0.5,
            shutdown_handler=TestHandler(),
        )

        # 종료 시작
        coordinator.initiate_shutdown()

        # 빠른 요청들 완료
        time.sleep(0.1)
        tracker.end_request("fast_1")
        tracker.end_request("fast_2")

        # 종료 대기
        coordinator.wait_for_shutdown(timeout=2.0)

        assert "start" in shutdown_log
        assert "forced:1" in shutdown_log  # slow_1 강제 중단

        stats = coordinator.get_stats()
        assert stats.aborted_count == 1
```

---

## 📁 파일 생성 순서

1. `packages/selfhealing-python/src/selfhealing/core/shutdown_coordinator.py`
2. `packages/selfhealing-python/src/selfhealing/core/request_context.py`
3. `packages/selfhealing-python/src/selfhealing/core/__init__.py` 수정
4. `packages/selfhealing-python/tests/unit/test_graceful_shutdown.py`

---

## ✅ 완료 기준

- [ ] RequestTracker 구현
- [ ] GracefulShutdownCoordinator 구현
- [ ] RequestContext 컨텍스트 매니저 구현
- [ ] 드레인 테스트 통과
- [ ] 타임아웃 강제 종료 테스트 통과
- [ ] 시그널 핸들링 테스트 통과

---

## 📝 새 세션 시작 프롬프트

```
STAGE_27_GRACEFUL_SHUTDOWN.md 문서대로 구현해줘.
RequestTracker와 GracefulShutdownCoordinator 구현.
```
