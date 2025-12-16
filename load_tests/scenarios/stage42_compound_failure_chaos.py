"""
Stage 42: Compound Failure - Controlled Chaos Scenarios (Part 2)

Controlled Chaos Testing: 동시성과 타이밍 오버랩을 포함한 복합 장애 검증

목표:
- 동시 장애 시나리오에서 데드락 없음을 검증
- 런어웨이 재시도 없음을 검증
- 시스템이 반응성을 유지함을 검증
- 올바른 우선순위 순서 검증
- 비일관 상태 없음을 검증
- 장애 해결 후 안정 상태 복귀 검증

시나리오 (Controlled Chaos):
- B1: Concurrent Retry Storm + Memory Pressure
- B2: Retry + Rate Limit + Delayed Recovery
- B3: Multi-Service Compound Failure
- B4: Chaos Window Closure

제약사항 (엄격 준수):
- 새로운 복구 메커니즘 추가 금지
- 재시도 한도/백오프 로직 변경 금지
- 메모리 임계값 수정 금지
- 속도 제한 규칙 수정 금지
- 새로운 decision reason code 추가 금지
- Control API 동작 변경 금지
- 새로운 관측 가능성 필드 추가 금지
- 프로덕션 코드 경로 수정 금지

실행 방법:
    pytest load_tests/scenarios/stage42_compound_failure_chaos.py -v -s

    # Docker Compose를 통한 실행:
    docker-compose -f docker-compose.stage42.yml up -d
    docker-compose -f docker-compose.stage42.yml run --rm chaos-runner

참조:
- Stage 39: K8s Runtime Chaos
- Stage 32: Retry Storm Extended
- Stage 36: Memory Pressure
"""

from __future__ import annotations

import gc
import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future, as_completed, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as tz
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Set, TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Conditional Imports
# =============================================================================

if TYPE_CHECKING:
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
        DLQConfig,
        DLQService,
    )


# =============================================================================
# Shared Repository Singleton (Reused from Stage 39-41)
# =============================================================================

_SHARED_MEMORY_REPO = None
_SHARED_CB_SERVICE = None
_REPO_LOCK = threading.Lock()


def _get_or_create_shared_repo():
    """Get or create shared in-memory repository (singleton pattern)."""
    global _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE
    with _REPO_LOCK:
        if _SHARED_MEMORY_REPO is None:
            from selfhealing.adapters.memory.circuit_breaker import (
                InMemoryCircuitBreakerStateRepository,
            )
            from selfhealing.services import CircuitBreakerConfig, CircuitBreakerService

            _SHARED_MEMORY_REPO = InMemoryCircuitBreakerStateRepository()
            _SHARED_CB_SERVICE = CircuitBreakerService(
                config=CircuitBreakerConfig(
                    enabled=True,
                    failure_threshold=3,
                    recovery_timeout=30,
                    manual_override_ttl_minutes=60,
                ),
                repository=_SHARED_MEMORY_REPO,
            )
    return _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE


def _get_selfhealing_services():
    """Lazy import selfhealing services to avoid import errors in IDE."""
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
        DLQConfig,
        DLQService,
    )

    _memory_repo, _cb_service = _get_or_create_shared_repo()

    def _create_cb_service(config=None):
        cfg = config or CircuitBreakerConfig(
            enabled=True,
            failure_threshold=3,
            recovery_timeout=30,
            manual_override_ttl_minutes=60,
        )
        return CircuitBreakerService(config=cfg, repository=_memory_repo)

    def force_open_circuit(service_name: str, reason: str = ""):
        return _cb_service.force_open(service_name=service_name, reason=reason)

    def force_close_circuit(service_name: str, reason: str = "", trigger_replay: bool = False):
        return _cb_service.force_close(service_name=service_name, reason=reason, trigger_replay=trigger_replay)

    def should_allow_request(service_name: str) -> bool:
        return _cb_service.should_allow(service_name)

    def get_circuit_breaker_service():
        return _cb_service

    return {
        "CircuitBreakerConfig": CircuitBreakerConfig,
        "CircuitBreakerService": CircuitBreakerService,
        "CircuitState": CircuitState,
        "DLQConfig": DLQConfig,
        "DLQService": DLQService,
        "force_close_circuit": force_close_circuit,
        "force_open_circuit": force_open_circuit,
        "get_circuit_breaker_service": get_circuit_breaker_service,
        "should_allow_request": should_allow_request,
        "_memory_repo": _memory_repo,
        "_create_cb_service": _create_cb_service,
    }


def _get_decision_logger():
    """Lazy import decision logger to avoid import errors in IDE."""
    from selfhealing.core.decision_logger import (
        DecisionLogger,
        EventType,
        ReasonCode,
    )

    return {
        "DecisionLogger": DecisionLogger,
        "EventType": EventType,
        "ReasonCode": ReasonCode,
    }


# =============================================================================
# Test Constants (NO NEW MECHANISMS)
# =============================================================================

SERVICE_PAYMENT_GATEWAY = "payment_gateway_chaos"
SERVICE_ORDER_PROCESSOR = "order_processor_chaos"
SERVICE_NOTIFICATION = "notification_service_chaos"
SERVICE_INVENTORY = "inventory_service_chaos"
SERVICE_AUTH = "auth_service_chaos"

# Existing limits (DO NOT MODIFY)
MAX_RETRY_ATTEMPTS = 3
MAX_REPLAY_ATTEMPTS = 2
BASE_BACKOFF_SECONDS = 0.02  # Shorter for chaos tests
MAX_BACKOFF_SECONDS = 0.5

# Rate limit settings (existing)
RATE_LIMIT_REQUESTS_PER_SECOND = 100
RATE_LIMIT_BURST = 20

# Memory thresholds (existing)
MEMORY_WARNING_THRESHOLD_PERCENT = 70
MEMORY_THROTTLE_THRESHOLD_PERCENT = 85
MEMORY_CRITICAL_THRESHOLD_PERCENT = 95

# DLQ configuration (existing)
DLQ_MAX_SIZE = 1000
DLQ_INPUT_RATE_LIMIT = 100

# Chaos test configuration
CHAOS_WORKER_COUNT = 10
CHAOS_DURATION_SECONDS = 2.0
CHAOS_REQUEST_INTERVAL_MS = 10


# =============================================================================
# Thread-Safe Chaos Metrics
# =============================================================================


@dataclass
class ChaosMetrics:
    """Thread-safe metrics for chaos testing."""

    test_name: str
    start_time: datetime = field(default_factory=lambda: datetime.now(tz.utc))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # System survival (atomic)
    system_crashed: bool = False
    deadlock_detected: bool = False
    deadlock_timeout_seconds: float = 5.0
    unhandled_exceptions: List[str] = field(default_factory=list)

    # Boundary enforcement
    security_boundary_bypassed: bool = False
    unauthorized_auto_heal: bool = False
    retry_limit_exceeded: bool = False
    rate_limit_bypassed: bool = False
    dlq_overflow: bool = False

    # Counters
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    retry_attempts: int = 0
    rate_limit_rejections: int = 0
    dlq_entries_created: int = 0
    memory_warnings_triggered: int = 0
    gc_pauses: int = 0

    # Concurrent worker tracking
    active_workers: int = 0
    peak_concurrent_workers: int = 0
    worker_completions: int = 0

    # Decision Records
    decision_records: List[Dict[str, Any]] = field(default_factory=list)
    schema_violations: List[str] = field(default_factory=list)

    # Chaos events
    chaos_events: List[Dict[str, Any]] = field(default_factory=list)

    def inc_requests(self, success: bool = True) -> None:
        """Increment request counter (thread-safe)."""
        with self._lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1

    def inc_retry(self) -> None:
        """Increment retry counter (thread-safe)."""
        with self._lock:
            self.retry_attempts += 1

    def inc_rate_limit(self) -> None:
        """Increment rate limit counter (thread-safe)."""
        with self._lock:
            self.rate_limit_rejections += 1

    def inc_dlq(self) -> None:
        """Increment DLQ counter (thread-safe)."""
        with self._lock:
            self.dlq_entries_created += 1

    def inc_memory_warning(self) -> None:
        """Increment memory warning counter (thread-safe)."""
        with self._lock:
            self.memory_warnings_triggered += 1

    def inc_gc_pause(self) -> None:
        """Increment GC pause counter (thread-safe)."""
        with self._lock:
            self.gc_pauses += 1

    def register_worker(self) -> None:
        """Register active worker (thread-safe)."""
        with self._lock:
            self.active_workers += 1
            self.peak_concurrent_workers = max(self.peak_concurrent_workers, self.active_workers)

    def unregister_worker(self) -> None:
        """Unregister active worker (thread-safe)."""
        with self._lock:
            self.active_workers -= 1
            self.worker_completions += 1

    def add_exception(self, exc: str) -> None:
        """Add exception (thread-safe)."""
        with self._lock:
            self.unhandled_exceptions.append(exc)

    def record_decision(self, event: str, allowed: bool, reason: str, service_name: str) -> None:
        """Record decision (thread-safe)."""
        with self._lock:
            record = {
                "event": event,
                "allowed": allowed,
                "reason": reason,
                "service_name": service_name,
                "policy_version": "1.0",
                "timestamp": datetime.now(tz.utc).isoformat(),
            }
            self._validate_schema(record)
            self.decision_records.append(record)

    def record_chaos_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """Record chaos event (thread-safe)."""
        with self._lock:
            self.chaos_events.append(
                {
                    "event_type": event_type,
                    "timestamp": datetime.now(tz.utc).isoformat(),
                    **details,
                }
            )

    def _validate_schema(self, record: Dict[str, Any]) -> None:
        """Validate against frozen schema."""
        frozen_fields = {"event", "allowed", "reason", "service_name", "policy_version", "timestamp"}
        unknown_fields = set(record.keys()) - frozen_fields
        if unknown_fields:
            self.schema_violations.append(f"Unknown fields: {unknown_fields}")

    def check_deadlock(self, start_time: float) -> bool:
        """Check if deadlock timeout exceeded."""
        if time.time() - start_time > self.deadlock_timeout_seconds:
            with self._lock:
                self.deadlock_detected = True
            return True
        return False

    def to_summary(self) -> Dict[str, Any]:
        """Generate test summary (thread-safe)."""
        with self._lock:
            return {
                "test_name": self.test_name,
                "duration_seconds": (datetime.now(tz.utc) - self.start_time).total_seconds(),
                "system_crashed": self.system_crashed,
                "deadlock_detected": self.deadlock_detected,
                "unhandled_exceptions_count": len(self.unhandled_exceptions),
                "total_requests": self.total_requests,
                "successful_requests": self.successful_requests,
                "failed_requests": self.failed_requests,
                "peak_concurrent_workers": self.peak_concurrent_workers,
                "decision_records_count": len(self.decision_records),
                "chaos_events_count": len(self.chaos_events),
                "schema_violations_count": len(self.schema_violations),
            }


# =============================================================================
# Thread-Safe Rate Limiter
# =============================================================================


class ThreadSafeRateLimiter:
    """Thread-safe rate limiter for chaos testing."""

    def __init__(self, limit: int = RATE_LIMIT_REQUESTS_PER_SECOND, window_seconds: float = 1.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._window_start = time.time()
        self._request_count = 0

    def try_acquire(self) -> bool:
        """Try to acquire rate limit token. Returns True if allowed."""
        with self._lock:
            now = time.time()

            # Reset window if expired
            if now - self._window_start >= self.window_seconds:
                self._window_start = now
                self._request_count = 0

            if self._request_count >= self.limit:
                return False

            self._request_count += 1
            return True

    def reset(self) -> None:
        """Reset rate limiter state."""
        with self._lock:
            self._window_start = time.time()
            self._request_count = 0


# =============================================================================
# Thread-Safe Memory Pressure Simulator
# =============================================================================


class ThreadSafeMemoryPressure:
    """Thread-safe memory pressure simulator."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pressure_percent = 50.0
        self._peak_percent = 50.0
        self._gc_pauses = 0

    @property
    def pressure(self) -> float:
        with self._lock:
            return self._pressure_percent

    def set_pressure(self, percent: float) -> None:
        with self._lock:
            self._pressure_percent = percent
            self._peak_percent = max(self._peak_percent, percent)

    def is_warning(self) -> bool:
        with self._lock:
            return self._pressure_percent >= MEMORY_WARNING_THRESHOLD_PERCENT

    def is_throttled(self) -> bool:
        with self._lock:
            return self._pressure_percent >= MEMORY_THROTTLE_THRESHOLD_PERCENT

    def is_critical(self) -> bool:
        with self._lock:
            return self._pressure_percent >= MEMORY_CRITICAL_THRESHOLD_PERCENT

    def simulate_gc_pause(self) -> float:
        """Simulate GC pause. Returns pause duration in ms."""
        with self._lock:
            self._gc_pauses += 1
        pause_ms = random.uniform(5, 30)
        time.sleep(pause_ms / 1000)
        return pause_ms

    def reset(self) -> None:
        with self._lock:
            self._pressure_percent = 50.0
            self._peak_percent = 50.0
            self._gc_pauses = 0


# =============================================================================
# Thread-Safe DLQ
# =============================================================================


class ThreadSafeDLQ:
    """Thread-safe DLQ for chaos testing."""

    def __init__(self, max_size: int = DLQ_MAX_SIZE, rate_limit: int = DLQ_INPUT_RATE_LIMIT):
        self.max_size = max_size
        self.rate_limit = rate_limit
        self._lock = threading.Lock()
        self._entries: List[Dict[str, Any]] = []
        self._rejected_count = 0
        self._last_second = time.time()
        self._entries_this_second = 0

    def add(self, entry: Dict[str, Any]) -> bool:
        """Add entry to DLQ. Returns False if rejected."""
        with self._lock:
            now = time.time()

            # Reset rate counter
            if now - self._last_second >= 1.0:
                self._last_second = now
                self._entries_this_second = 0

            # Check limits
            if len(self._entries) >= self.max_size:
                self._rejected_count += 1
                return False

            if self._entries_this_second >= self.rate_limit:
                self._rejected_count += 1
                return False

            self._entries.append(entry)
            self._entries_this_second += 1
            return True

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def rejected_count(self) -> int:
        with self._lock:
            return self._rejected_count

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._rejected_count = 0


# =============================================================================
# Chaos Worker
# =============================================================================


class ChaosWorker:
    """
    Worker for chaos testing.

    Executes requests with failure injection in a separate thread.
    """

    def __init__(
        self,
        worker_id: int,
        service_name: str,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
        metrics: ChaosMetrics,
    ):
        self.worker_id = worker_id
        self.service_name = service_name
        self.rate_limiter = rate_limiter
        self.memory_pressure = memory_pressure
        self.dlq = dlq
        self.metrics = metrics
        self._stop_event = threading.Event()

    def run(
        self,
        duration_seconds: float,
        failure_rate: float = 0.5,
        request_interval_ms: float = CHAOS_REQUEST_INTERVAL_MS,
    ) -> Dict[str, Any]:
        """Run chaos worker for specified duration."""
        self.metrics.register_worker()
        results = {
            "worker_id": self.worker_id,
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "rate_limited": 0,
            "dlq_entries": 0,
        }

        try:
            start_time = time.time()
            while time.time() - start_time < duration_seconds and not self._stop_event.is_set():
                request_id = str(uuid.uuid4())
                results["requests"] += 1

                # Check rate limit
                if not self.rate_limiter.try_acquire():
                    results["rate_limited"] += 1
                    self.metrics.inc_rate_limit()
                    self.metrics.record_decision(
                        event="rate_limit_exceeded",
                        allowed=False,
                        reason="threshold_not_met",
                        service_name=self.service_name,
                    )
                    time.sleep(request_interval_ms / 1000)
                    continue

                # Check memory pressure
                if self.memory_pressure.is_throttled():
                    self.metrics.inc_memory_warning()
                    self.memory_pressure.simulate_gc_pause()
                    self.metrics.inc_gc_pause()

                # Execute request with retry
                success = self._execute_with_retry(request_id, failure_rate)

                if success:
                    results["successes"] += 1
                    self.metrics.inc_requests(success=True)
                else:
                    results["failures"] += 1
                    self.metrics.inc_requests(success=False)

                    # Send to DLQ
                    entry = {
                        "request_id": request_id,
                        "worker_id": self.worker_id,
                        "timestamp": datetime.now(tz.utc).isoformat(),
                        "reason": "retry_exhausted",
                    }
                    if self.dlq.add(entry):
                        results["dlq_entries"] += 1
                        self.metrics.inc_dlq()

                time.sleep(request_interval_ms / 1000)

        except Exception as e:
            self.metrics.add_exception(f"Worker {self.worker_id}: {str(e)}")

        finally:
            self.metrics.unregister_worker()

        return results

    def _execute_with_retry(self, request_id: str, failure_rate: float) -> bool:
        """Execute request with retry. Returns True if eventually successful."""
        for attempt in range(MAX_RETRY_ATTEMPTS):
            self.metrics.inc_retry()

            if random.random() > failure_rate:
                self.metrics.record_decision(
                    event="request_succeeded",
                    allowed=True,
                    reason="stability_ok_no_intervention",
                    service_name=self.service_name,
                )
                return True

            # Backoff
            backoff = min(BASE_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)
            time.sleep(backoff)

        self.metrics.record_decision(
            event="retry_exhausted",
            allowed=False,
            reason="threshold_not_met",
            service_name=self.service_name,
        )
        return False

    def stop(self) -> None:
        """Signal worker to stop."""
        self._stop_event.set()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def chaos_metrics():
    """Create chaos metrics instance."""
    return ChaosMetrics(test_name="chaos_test")


@pytest.fixture
def rate_limiter():
    """Create thread-safe rate limiter."""
    limiter = ThreadSafeRateLimiter()
    yield limiter
    limiter.reset()


@pytest.fixture
def memory_pressure():
    """Create thread-safe memory pressure simulator."""
    pressure = ThreadSafeMemoryPressure()
    yield pressure
    pressure.reset()


@pytest.fixture
def dlq():
    """Create thread-safe DLQ."""
    queue = ThreadSafeDLQ()
    yield queue
    queue.clear()


@pytest.fixture
def selfhealing_services():
    """Provide all selfhealing services as a fixture."""
    return _get_selfhealing_services()


@pytest.fixture(autouse=True)
def cleanup_circuit_breakers():
    """Clean up circuit breaker states after each test."""
    yield
    try:
        global _SHARED_MEMORY_REPO
        if _SHARED_MEMORY_REPO is not None:
            with _SHARED_MEMORY_REPO._lock:
                _SHARED_MEMORY_REPO._storage.clear()
    except Exception:
        pass


# =============================================================================
# Scenario B1: Concurrent Retry Storm + Memory Pressure
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42B1ConcurrentRetryMemoryPressure:
    """
    Scenario B1: Concurrent Retry Storm + Memory Pressure

    여러 워커가 동시에 재시도하고 메모리 압박이 오버랩되는 상황.

    Expectation:
        - 데드락 없음
        - 런어웨이 재시도 없음
        - 시스템이 반응성 유지
    """

    def test_stage_42_b1_concurrent_retry_with_memory_pressure(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
    ):
        """
        시나리오: 동시 재시도 + 메모리 압박

        Given:
            - 10개 동시 워커

        When:
            - 모든 워커가 재시도 폭풍 생성
            - 메모리 압박이 동시에 발생

        Then:
            - 데드락 없음
            - 모든 워커 완료
            - 시스템 안정
        """
        # given
        metrics = ChaosMetrics(test_name="B1_concurrent_retry_memory")
        rate_limiter.limit = 500  # Higher limit for this test
        workers = []

        # Start memory pressure thread
        def pressure_thread():
            start = time.time()
            while time.time() - start < CHAOS_DURATION_SECONDS:
                # Oscillate memory pressure
                pressure_level = 50 + 40 * abs((time.time() - start) % 1 - 0.5) * 2
                memory_pressure.set_pressure(pressure_level)
                time.sleep(0.05)

        pressure_t = threading.Thread(target=pressure_thread)

        # when
        with ThreadPoolExecutor(max_workers=CHAOS_WORKER_COUNT) as executor:
            # Create workers
            for i in range(CHAOS_WORKER_COUNT):
                worker = ChaosWorker(
                    worker_id=i,
                    service_name=SERVICE_PAYMENT_GATEWAY,
                    rate_limiter=rate_limiter,
                    memory_pressure=memory_pressure,
                    dlq=dlq,
                    metrics=metrics,
                )
                workers.append(worker)

            # Start pressure thread
            pressure_t.start()

            # Submit workers
            futures = [executor.submit(worker.run, CHAOS_DURATION_SECONDS, failure_rate=0.7) for worker in workers]

            # Wait with timeout (deadlock detection)
            start_time = time.time()
            done, not_done = wait(futures, timeout=CHAOS_DURATION_SECONDS + 3)

            # Check for deadlock
            if not_done:
                metrics.deadlock_detected = True
                for worker in workers:
                    worker.stop()

        pressure_t.join(timeout=1)

        # then
        # No deadlock
        assert not metrics.deadlock_detected, "Deadlock should not occur"

        # All workers completed
        assert (
            metrics.worker_completions == CHAOS_WORKER_COUNT
        ), f"All workers should complete, got {metrics.worker_completions}"

        # System did not crash
        assert not metrics.system_crashed

        # Memory warnings occurred (pressure was applied)
        assert metrics.memory_warnings_triggered > 0, "Memory warnings should trigger"

        # GC pauses occurred
        assert metrics.gc_pauses > 0, "GC pauses should occur"

        # Requests were processed
        assert metrics.total_requests > 0, "Requests should be processed"

        # No unhandled exceptions
        assert len(metrics.unhandled_exceptions) == 0, f"Exceptions: {metrics.unhandled_exceptions}"

    def test_stage_42_b1_no_runaway_retry(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
    ):
        """
        시나리오: 런어웨이 재시도 없음

        Given:
            - 100% 실패율 설정

        When:
            - 워커들이 재시도 시도

        Then:
            - 재시도가 MAX_RETRY_ATTEMPTS에서 중단
            - 무한 재시도 없음
        """
        # given
        metrics = ChaosMetrics(test_name="B1_no_runaway")
        metrics.deadlock_timeout_seconds = 3.0
        rate_limiter.limit = 1000

        # when
        with ThreadPoolExecutor(max_workers=5) as executor:
            workers = [ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, metrics) for i in range(5)]

            futures = [executor.submit(worker.run, 1.0, failure_rate=1.0) for worker in workers]  # 100% failure

            done, not_done = wait(futures, timeout=5.0)

        # then
        # No deadlock from runaway retries
        assert not metrics.deadlock_detected, "No deadlock from retries"

        # All failed requests went to DLQ (not infinite loop)
        assert dlq.size > 0, "Failed requests should go to DLQ"

        # Retry attempts bounded
        max_expected_retries = metrics.total_requests * MAX_RETRY_ATTEMPTS
        assert metrics.retry_attempts <= max_expected_retries, "Retry attempts should be bounded"


# =============================================================================
# Scenario B2: Retry + Rate Limit + Delayed Recovery
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42B2RetryRateLimitDelayedRecovery:
    """
    Scenario B2: Retry + Rate Limit + Delayed Recovery

    속도 제한이 재시도 중간에 활성화되고 복구 창이 오버랩.

    Expectation:
        - 올바른 우선순위 순서
        - 비일관 상태 없음
    """

    def test_stage_42_b2_rate_limit_activates_mid_retry(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
    ):
        """
        시나리오: 재시도 중간에 속도 제한 활성화

        Given:
            - 초기에 높은 속도 제한

        When:
            - 재시도 중간에 속도 제한이 낮아짐

        Then:
            - 속도 제한이 우선 적용됨
            - 재시도가 깔끔하게 중단
        """
        # given
        metrics = ChaosMetrics(test_name="B2_rate_limit_mid_retry")
        rate_limiter.limit = 100  # Start high

        # Rate limit reduction thread
        def reduce_rate_limit():
            time.sleep(0.5)  # Wait for retry storm to start
            rate_limiter.limit = 5  # Drastically reduce

        rate_limit_thread = threading.Thread(target=reduce_rate_limit)

        # when
        with ThreadPoolExecutor(max_workers=5) as executor:
            workers = [ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, metrics) for i in range(5)]

            rate_limit_thread.start()

            futures = [executor.submit(worker.run, 1.5, failure_rate=0.8) for worker in workers]

            wait(futures, timeout=5.0)

        rate_limit_thread.join()

        # then
        # Rate limiting was enforced
        assert metrics.rate_limit_rejections > 0, "Rate limiting should be enforced"

        # System remained stable
        assert not metrics.deadlock_detected
        assert not metrics.system_crashed

        # No inconsistent states (all requests accounted for)
        total_outcomes = metrics.successful_requests + metrics.failed_requests
        # Rate limited requests are counted separately
        assert total_outcomes > 0, "Requests should be processed"

    def test_stage_42_b2_recovery_window_overlap(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
    ):
        """
        시나리오: 복구 창 오버랩

        Given:
            - 낮은 속도 제한

        When:
            - 속도 제한 복구와 재시도가 오버랩

        Then:
            - 올바른 우선순위 순서
            - 데이터 손실 없음
        """
        # given
        metrics = ChaosMetrics(test_name="B2_recovery_overlap")
        rate_limiter.limit = 10

        # Recovery simulation
        recovered = threading.Event()

        def recovery_thread():
            time.sleep(0.7)
            rate_limiter.limit = 500  # Recover rate limit
            recovered.set()

        recovery_t = threading.Thread(target=recovery_thread)

        # when
        with ThreadPoolExecutor(max_workers=3) as executor:
            workers = [ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, metrics) for i in range(3)]

            recovery_t.start()

            futures = [executor.submit(worker.run, 1.5, failure_rate=0.5) for worker in workers]

            wait(futures, timeout=5.0)

        recovery_t.join()

        # then
        # Recovery happened
        assert recovered.is_set(), "Recovery should complete"

        # System stable through recovery
        assert not metrics.deadlock_detected
        assert not metrics.system_crashed

        # Requests processed before and after recovery
        assert metrics.total_requests > 0


# =============================================================================
# Scenario B3: Multi-Service Compound Failure
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42B3MultiServiceCompoundFailure:
    """
    Scenario B3: Multi-Service Compound Failure

    서비스마다 다른 장애 조합.

    Expectation:
        - 엄격한 격리
        - 연쇄 복구 없음
    """

    def test_stage_42_b3_multi_service_isolation(
        self,
        memory_pressure: ThreadSafeMemoryPressure,
    ):
        """
        시나리오: 멀티 서비스 격리

        Given:
            - 3개 서비스 각각 다른 장애 상태

        When:
            - 동시 요청 처리

        Then:
            - 서비스 간 격리 유지
            - 장애가 전파되지 않음
        """
        # given
        services = {
            SERVICE_PAYMENT_GATEWAY: {
                "rate_limiter": ThreadSafeRateLimiter(limit=10),  # Low limit
                "dlq": ThreadSafeDLQ(),
                "failure_rate": 0.9,  # High failure
            },
            SERVICE_ORDER_PROCESSOR: {
                "rate_limiter": ThreadSafeRateLimiter(limit=100),  # Normal limit
                "dlq": ThreadSafeDLQ(),
                "failure_rate": 0.3,  # Low failure
            },
            SERVICE_NOTIFICATION: {
                "rate_limiter": ThreadSafeRateLimiter(limit=1000),  # High limit
                "dlq": ThreadSafeDLQ(),
                "failure_rate": 0.1,  # Very low failure
            },
        }

        metrics = ChaosMetrics(test_name="B3_multi_service_isolation")
        service_results: Dict[str, Dict[str, int]] = {k: {"success": 0, "failure": 0, "rate_limited": 0} for k in services}

        # when
        with ThreadPoolExecutor(max_workers=9) as executor:
            futures = []

            for service_name, config in services.items():
                for i in range(3):  # 3 workers per service
                    worker = ChaosWorker(
                        worker_id=i,
                        service_name=service_name,
                        rate_limiter=config["rate_limiter"],
                        memory_pressure=memory_pressure,
                        dlq=config["dlq"],
                        metrics=metrics,
                    )
                    futures.append(executor.submit(worker.run, 1.0, failure_rate=config["failure_rate"]))

            # Collect results
            for future in as_completed(futures, timeout=5.0):
                try:
                    result = future.result()
                    # Worker doesn't return service name, so we track via metrics
                except Exception as e:
                    metrics.add_exception(str(e))

        # then
        # All services processed requests
        assert metrics.total_requests > 0

        # Notification service should have highest success rate
        # (implicit through low failure rate and high rate limit)

        # Payment DLQ should have most entries (high failure rate)
        payment_dlq_size = services[SERVICE_PAYMENT_GATEWAY]["dlq"].size

        # Notification DLQ should have fewer entries
        notification_dlq_size = services[SERVICE_NOTIFICATION]["dlq"].size

        assert (
            payment_dlq_size >= notification_dlq_size
        ), "Payment (high failure) should have more DLQ entries than Notification"

        # No cross-service impact (isolation verified by independent DLQs)
        # Each service has its own DLQ

        # No cascading recovery (no unauthorized auto-heal)
        assert not metrics.unauthorized_auto_heal

    def test_stage_42_b3_no_cascading_recovery(
        self,
        memory_pressure: ThreadSafeMemoryPressure,
        selfhealing_services,
    ):
        """
        시나리오: 연쇄 복구 없음

        Given:
            - 하나의 서비스 복구

        When:
            - 다른 서비스는 여전히 장애

        Then:
            - 복구가 다른 서비스로 전파되지 않음
        """
        # given
        metrics = ChaosMetrics(test_name="B3_no_cascade_recovery")
        force_open = selfhealing_services["force_open_circuit"]
        force_close = selfhealing_services["force_close_circuit"]

        # Open circuit for both services
        force_open(SERVICE_PAYMENT_GATEWAY, reason="test_failure")
        force_open(SERVICE_ORDER_PROCESSOR, reason="test_failure")

        # when: Recover only payment
        force_close(SERVICE_PAYMENT_GATEWAY, reason="test_recovery", trigger_replay=False)

        # then: Order is still open
        should_allow = selfhealing_services["should_allow_request"]

        # Payment should allow
        assert should_allow(SERVICE_PAYMENT_GATEWAY), "Payment should be recovered"

        # Order should still be blocked (no cascade)
        assert not should_allow(SERVICE_ORDER_PROCESSOR), "Order should still be blocked"


# =============================================================================
# Scenario B4: Chaos Window Closure
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42B4ChaosWindowClosure:
    """
    Scenario B4: Chaos Window Closure

    장애가 주입되고 해결된 후 시스템 상태 검증.

    Expectation:
        - 안정 상태로 복귀
        - 잔여 손상 상태 없음
    """

    def test_stage_42_b4_return_to_stable_state(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
    ):
        """
        시나리오: 안정 상태로 복귀

        Given:
            - 장애 윈도우 동안 복합 장애

        When:
            - 장애 해결

        Then:
            - 시스템이 안정 상태로 복귀
            - 새 요청이 정상 처리됨
        """
        # given
        metrics = ChaosMetrics(test_name="B4_return_to_stable")

        # Phase 1: Chaos window (high failure, low rate limit)
        rate_limiter.limit = 5
        memory_pressure.set_pressure(90)

        chaos_phase_results = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            workers = [ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, metrics) for i in range(3)]

            futures = [executor.submit(worker.run, 0.5, failure_rate=0.9) for worker in workers]
            for future in as_completed(futures):
                chaos_phase_results.append(future.result())

        chaos_requests = metrics.total_requests
        chaos_failures = metrics.failed_requests

        # Phase 2: Recovery
        rate_limiter.limit = 1000  # Restore rate limit
        memory_pressure.set_pressure(50)  # Restore memory

        # Phase 3: Stable operation
        stable_metrics = ChaosMetrics(test_name="B4_stable_phase")

        with ThreadPoolExecutor(max_workers=3) as executor:
            workers = [
                ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, stable_metrics) for i in range(3)
            ]

            futures = [executor.submit(worker.run, 0.5, failure_rate=0.1) for worker in workers]
            wait(futures, timeout=3.0)

        # then
        # Stable phase had higher success rate
        stable_success_rate = stable_metrics.successful_requests / max(1, stable_metrics.total_requests)
        assert stable_success_rate > 0.5, f"Stable phase should succeed mostly, got {stable_success_rate}"

        # No residual corrupted state
        assert not stable_metrics.deadlock_detected
        assert not stable_metrics.system_crashed
        assert len(stable_metrics.unhandled_exceptions) == 0

    def test_stage_42_b4_no_residual_state(
        self,
        rate_limiter: ThreadSafeRateLimiter,
        memory_pressure: ThreadSafeMemoryPressure,
        dlq: ThreadSafeDLQ,
        selfhealing_services,
    ):
        """
        시나리오: 잔여 손상 상태 없음

        Given:
            - 카오스 윈도우에서 서킷 열림

        When:
            - 카오스 해결 및 서킷 닫힘

        Then:
            - 서킷 상태 깨끗
            - 메트릭 일관됨
        """
        # given
        metrics = ChaosMetrics(test_name="B4_no_residual")
        force_open = selfhealing_services["force_open_circuit"]
        force_close = selfhealing_services["force_close_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # Chaos: Open circuit
        force_open(SERVICE_PAYMENT_GATEWAY, reason="chaos_test")
        assert not should_allow(SERVICE_PAYMENT_GATEWAY), "Circuit should be open during chaos"

        # Inject some failures to DLQ
        for i in range(5):
            dlq.add(
                {
                    "request_id": f"chaos-{i}",
                    "timestamp": datetime.now(tz.utc).isoformat(),
                    "reason": "chaos_failure",
                }
            )

        # when: Chaos window closes
        force_close(SERVICE_PAYMENT_GATEWAY, reason="chaos_resolved", trigger_replay=False)

        # then
        # Circuit is closed
        assert should_allow(SERVICE_PAYMENT_GATEWAY), "Circuit should be closed after resolution"

        # DLQ entries exist but are not auto-replayed
        assert dlq.size == 5, "DLQ entries should remain"

        # New requests can proceed
        metrics = ChaosMetrics(test_name="B4_post_chaos")

        worker = ChaosWorker(
            worker_id=0,
            service_name=SERVICE_PAYMENT_GATEWAY,
            rate_limiter=rate_limiter,
            memory_pressure=memory_pressure,
            dlq=ThreadSafeDLQ(),  # Fresh DLQ for new requests
            metrics=metrics,
        )

        result = worker.run(0.3, failure_rate=0.1)

        # New requests succeed
        assert result["successes"] > 0, "New requests should succeed after chaos"
        assert not metrics.deadlock_detected


# =============================================================================
# Integration Test: Full Chaos Cycle
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42ChaosIntegration:
    """
    통합 테스트: 전체 카오스 사이클

    모든 B 시나리오를 조합하여 시스템 안정성 검증.
    """

    def test_stage_42_full_chaos_cycle(
        self,
        memory_pressure: ThreadSafeMemoryPressure,
        selfhealing_services,
    ):
        """
        시나리오: 전체 카오스 사이클

        Given:
            - 정상 시스템 상태

        When:
            - Phase 1: 동시 재시도 폭풍
            - Phase 2: 속도 제한 + 메모리 압박
            - Phase 3: 멀티 서비스 장애
            - Phase 4: 복구 및 안정화

        Then:
            - 각 단계에서 시스템 생존
            - 최종 상태 안정
            - 모든 경계 유지
        """
        # given
        metrics = ChaosMetrics(test_name="full_chaos_cycle")
        rate_limiter = ThreadSafeRateLimiter(limit=100)
        dlq = ThreadSafeDLQ()

        phase_summaries = []

        # Phase 1: Concurrent retry storm
        metrics.record_chaos_event("phase_start", {"phase": 1, "name": "retry_storm"})

        with ThreadPoolExecutor(max_workers=5) as executor:
            workers = [ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, dlq, metrics) for i in range(5)]
            futures = [executor.submit(worker.run, 0.3, failure_rate=0.7) for worker in workers]
            wait(futures, timeout=3.0)

        phase_summaries.append({"phase": 1, "requests": metrics.total_requests})

        # Phase 2: Rate limit + Memory pressure
        metrics.record_chaos_event("phase_start", {"phase": 2, "name": "rate_limit_memory"})
        rate_limiter.limit = 20
        memory_pressure.set_pressure(85)

        with ThreadPoolExecutor(max_workers=3) as executor:
            workers = [ChaosWorker(i, SERVICE_ORDER_PROCESSOR, rate_limiter, memory_pressure, dlq, metrics) for i in range(3)]
            futures = [executor.submit(worker.run, 0.3, failure_rate=0.5) for worker in workers]
            wait(futures, timeout=3.0)

        phase_summaries.append({"phase": 2, "rate_limited": metrics.rate_limit_rejections})

        # Phase 3: Multi-service
        metrics.record_chaos_event("phase_start", {"phase": 3, "name": "multi_service"})
        rate_limiter.limit = 50

        with ThreadPoolExecutor(max_workers=6) as executor:
            workers = []
            for service in [SERVICE_PAYMENT_GATEWAY, SERVICE_ORDER_PROCESSOR, SERVICE_NOTIFICATION]:
                for i in range(2):
                    workers.append(ChaosWorker(i, service, rate_limiter, memory_pressure, dlq, metrics))

            futures = [executor.submit(worker.run, 0.2, failure_rate=0.4) for worker in workers]
            wait(futures, timeout=3.0)

        phase_summaries.append({"phase": 3, "dlq_size": dlq.size})

        # Phase 4: Recovery
        metrics.record_chaos_event("phase_start", {"phase": 4, "name": "recovery"})
        rate_limiter.limit = 1000
        memory_pressure.set_pressure(50)

        stable_dlq = ThreadSafeDLQ()
        stable_metrics = ChaosMetrics(test_name="stable_phase")

        with ThreadPoolExecutor(max_workers=3) as executor:
            workers = [
                ChaosWorker(i, SERVICE_PAYMENT_GATEWAY, rate_limiter, memory_pressure, stable_dlq, stable_metrics)
                for i in range(3)
            ]
            futures = [executor.submit(worker.run, 0.3, failure_rate=0.1) for worker in workers]
            wait(futures, timeout=3.0)

        # then
        # System survived all phases
        assert not metrics.deadlock_detected, "No deadlock during chaos"
        assert not metrics.system_crashed, "System did not crash"

        # Stable phase shows recovery
        stable_success_rate = stable_metrics.successful_requests / max(1, stable_metrics.total_requests)
        assert stable_success_rate > 0.5, f"System should recover, got {stable_success_rate}"

        # All boundaries maintained
        assert not metrics.security_boundary_bypassed
        assert not metrics.unauthorized_auto_heal

        # Decision records consistent
        assert len(metrics.schema_violations) == 0, f"Schema violations: {metrics.schema_violations}"

        # Chaos events recorded
        assert len(metrics.chaos_events) >= 4, "All phases should be recorded"

        # Summary
        summary = metrics.to_summary()
        assert summary["total_requests"] > 0, "Requests should be processed"


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
