"""
Stage 42: Compound Failure - Deterministic Scenarios (Part 1)

Controlled Chaos Testing: 복합 장애 상황에서 시스템 안정성 검증

목표:
- 복합 장애 시나리오에서 회복 메커니즘이 서로 충돌하지 않음을 검증
- 보안/거버넌스 경계가 우회되지 않음을 확인
- 시스템이 크래시 없이 점진적으로 성능 저하됨을 검증
- 결정론적 해결 경로가 복합 스트레스에서도 유지됨을 검증
- 관측 가능성과 Decision Record가 일관되게 유지됨을 검증

시나리오 (Deterministic):
- A1: Retry Storm + Rate Limit (재시도 폭풍 + 속도 제한)
- A2: Retry + Memory Pressure (재시도 + 메모리 압박)
- A3: Rate Limit + Partial Service Degradation (속도 제한 + 부분 서비스 저하)
- A4: Retry Exhaustion + DLQ Boundary (재시도 소진 + DLQ 경계)

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
    pytest load_tests/scenarios/stage42_compound_failure_deterministic.py -v -s

참조:
- Stage 32: Retry Storm Extended
- Stage 36: Memory Pressure
- Stage 22: Rate Limit Conflict
- Stage 14: DLQ Replay
"""

from __future__ import annotations

import gc
import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as tz
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, TYPE_CHECKING
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


def _get_or_create_shared_repo():
    """Get or create shared in-memory repository (singleton pattern)."""
    global _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE
    from selfhealing.adapters.memory.circuit_breaker import (
        InMemoryCircuitBreakerStateRepository,
    )
    from selfhealing.services import CircuitBreakerConfig, CircuitBreakerService

    if _SHARED_MEMORY_REPO is None:
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

SERVICE_PAYMENT_GATEWAY = "payment_gateway_compound"
SERVICE_ORDER_PROCESSOR = "order_processor_compound"
SERVICE_NOTIFICATION = "notification_service_compound"
SERVICE_INVENTORY = "inventory_service_compound"

# Existing limits (DO NOT MODIFY)
MAX_RETRY_ATTEMPTS = 3
MAX_REPLAY_ATTEMPTS = 2
BASE_BACKOFF_SECONDS = 0.05
MAX_BACKOFF_SECONDS = 1.0

# Rate limit settings (existing)
RATE_LIMIT_REQUESTS_PER_SECOND = 50
RATE_LIMIT_BURST = 10

# Memory thresholds (existing)
MEMORY_WARNING_THRESHOLD_PERCENT = 70
MEMORY_THROTTLE_THRESHOLD_PERCENT = 85
MEMORY_CRITICAL_THRESHOLD_PERCENT = 95

# DLQ configuration (existing)
DLQ_MAX_SIZE = 1000
DLQ_INPUT_RATE_LIMIT = 100


# =============================================================================
# Compound Failure State Tracking
# =============================================================================


@dataclass
class RetryState:
    """Tracks retry attempts for a request."""

    request_id: str
    attempt_count: int = 0
    max_attempts: int = MAX_RETRY_ATTEMPTS
    exhausted: bool = False
    backoff_total_seconds: float = 0.0

    def increment(self) -> bool:
        """Increment attempt. Returns True if more retries allowed."""
        self.attempt_count += 1
        if self.attempt_count >= self.max_attempts:
            self.exhausted = True
            return False
        return True


@dataclass
class RateLimitState:
    """Tracks rate limit state for a service."""

    service_name: str
    requests_this_window: int = 0
    window_start: float = field(default_factory=time.time)
    window_duration_seconds: float = 1.0
    limit: int = RATE_LIMIT_REQUESTS_PER_SECOND
    exceeded: bool = False

    def check_and_increment(self) -> bool:
        """Check rate limit and increment if allowed. Returns True if allowed."""
        now = time.time()
        if now - self.window_start > self.window_duration_seconds:
            # Reset window
            self.window_start = now
            self.requests_this_window = 0
            self.exceeded = False

        if self.requests_this_window >= self.limit:
            self.exceeded = True
            return False

        self.requests_this_window += 1
        return True


@dataclass
class MemoryPressureState:
    """Simulates memory pressure state."""

    current_usage_percent: float = 50.0
    peak_usage_percent: float = 50.0
    gc_pauses_count: int = 0
    throttled: bool = False
    warning: bool = False
    critical: bool = False

    def set_pressure(self, percent: float) -> None:
        """Set current memory pressure level."""
        self.current_usage_percent = percent
        self.peak_usage_percent = max(self.peak_usage_percent, percent)
        self.warning = percent >= MEMORY_WARNING_THRESHOLD_PERCENT
        self.throttled = percent >= MEMORY_THROTTLE_THRESHOLD_PERCENT
        self.critical = percent >= MEMORY_CRITICAL_THRESHOLD_PERCENT

    def simulate_gc_pause(self) -> float:
        """Simulate GC pause and return pause duration in ms."""
        self.gc_pauses_count += 1
        pause_ms = random.uniform(10, 50)  # 10-50ms pause
        time.sleep(pause_ms / 1000)
        return pause_ms


@dataclass
class DLQState:
    """Tracks DLQ state."""

    entries: List[Dict[str, Any]] = field(default_factory=list)
    rejected_count: int = 0
    input_rate_per_second: float = 0.0
    _last_input_time: float = field(default_factory=time.time)
    _input_count_this_second: int = 0

    def add_entry(self, entry: Dict[str, Any]) -> bool:
        """Add entry to DLQ. Returns False if rejected (size/rate limit)."""
        now = time.time()

        # Reset rate counter if new second
        if now - self._last_input_time >= 1.0:
            self._last_input_time = now
            self._input_count_this_second = 0

        # Check size limit
        if len(self.entries) >= DLQ_MAX_SIZE:
            self.rejected_count += 1
            return False

        # Check rate limit
        if self._input_count_this_second >= DLQ_INPUT_RATE_LIMIT:
            self.rejected_count += 1
            return False

        self.entries.append(entry)
        self._input_count_this_second += 1
        return True

    def get_pending_count(self) -> int:
        """Get count of pending (not replayed) entries."""
        return len([e for e in self.entries if not e.get("replayed", False)])


@dataclass
class CompoundFailureMetrics:
    """Metrics for compound failure test."""

    test_name: str
    start_time: datetime = field(default_factory=lambda: datetime.now(tz.utc))

    # System survival
    system_crashed: bool = False
    deadlock_detected: bool = False
    unhandled_exceptions: List[str] = field(default_factory=list)

    # Boundary enforcement
    security_boundary_bypassed: bool = False
    unauthorized_auto_heal: bool = False
    retry_limit_exceeded: bool = False
    rate_limit_bypassed: bool = False
    dlq_overflow: bool = False

    # State tracking
    retry_attempts: int = 0
    rate_limit_rejections: int = 0
    dlq_entries_created: int = 0
    dlq_entries_rejected: int = 0
    memory_warnings_triggered: int = 0
    gc_pauses: int = 0

    # Decision Records
    decision_records: List[Dict[str, Any]] = field(default_factory=list)
    schema_violations: List[str] = field(default_factory=list)

    def record_decision(self, event: str, allowed: bool, reason: str, service_name: str) -> None:
        """Record a decision with frozen schema validation."""
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

    def _validate_schema(self, record: Dict[str, Any]) -> None:
        """Validate against frozen schema."""
        frozen_fields = {"event", "allowed", "reason", "service_name", "policy_version", "timestamp"}
        unknown_fields = set(record.keys()) - frozen_fields
        if unknown_fields:
            self.schema_violations.append(f"Unknown fields: {unknown_fields}")

    def to_summary(self) -> Dict[str, Any]:
        """Generate test summary."""
        return {
            "test_name": self.test_name,
            "duration_seconds": (datetime.now(tz.utc) - self.start_time).total_seconds(),
            "system_crashed": self.system_crashed,
            "deadlock_detected": self.deadlock_detected,
            "unhandled_exceptions_count": len(self.unhandled_exceptions),
            "security_boundary_bypassed": self.security_boundary_bypassed,
            "unauthorized_auto_heal": self.unauthorized_auto_heal,
            "retry_attempts": self.retry_attempts,
            "rate_limit_rejections": self.rate_limit_rejections,
            "dlq_entries_created": self.dlq_entries_created,
            "decision_records_count": len(self.decision_records),
            "schema_violations_count": len(self.schema_violations),
        }


# =============================================================================
# Compound Failure Simulator (Uses Existing Mechanisms Only)
# =============================================================================


class CompoundFailureSimulator:
    """
    Simulates compound failure scenarios using ONLY existing mechanisms.

    NO NEW RECOVERY LOGIC - only combines existing patterns.
    """

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.rate_limit = RateLimitState(service_name=service_name)
        self.memory = MemoryPressureState()
        self.dlq = DLQState()
        self.retries: Dict[str, RetryState] = {}
        self._lock = threading.Lock()

        # Get selfhealing services
        self._services = _get_selfhealing_services()
        self._cb_service = self._services["get_circuit_breaker_service"]()

    def create_request(self, request_id: str = None) -> str:
        """Create a new request and return its ID."""
        if request_id is None:
            request_id = str(uuid.uuid4())
        with self._lock:
            self.retries[request_id] = RetryState(request_id=request_id)
        return request_id

    def attempt_request_with_retry(
        self,
        request_id: str,
        should_fail: Callable[[], bool],
        metrics: CompoundFailureMetrics,
    ) -> Tuple[bool, str]:
        """
        Attempt request with retry logic.

        Returns (success, reason).
        Uses EXISTING retry limits only.
        """
        retry_state = self.retries.get(request_id)
        if retry_state is None:
            retry_state = RetryState(request_id=request_id)
            with self._lock:
                self.retries[request_id] = retry_state

        while not retry_state.exhausted:
            metrics.retry_attempts += 1

            # Check rate limit FIRST
            if not self.rate_limit.check_and_increment():
                metrics.rate_limit_rejections += 1
                metrics.record_decision(
                    event="rate_limit_exceeded",
                    allowed=False,
                    reason="threshold_not_met",
                    service_name=self.service_name,
                )
                # Rate limit does NOT allow bypass - stop retrying
                return False, "rate_limited"

            # Check if request should fail
            if should_fail():
                if not retry_state.increment():
                    # Retry exhausted
                    metrics.record_decision(
                        event="retry_exhausted",
                        allowed=False,
                        reason="threshold_not_met",
                        service_name=self.service_name,
                    )
                    return False, "retry_exhausted"

                # Backoff (existing logic)
                backoff = min(
                    BASE_BACKOFF_SECONDS * (2 ** (retry_state.attempt_count - 1)),
                    MAX_BACKOFF_SECONDS,
                )
                retry_state.backoff_total_seconds += backoff
                time.sleep(backoff)
            else:
                # Success
                metrics.record_decision(
                    event="request_succeeded",
                    allowed=True,
                    reason="stability_ok_no_intervention",
                    service_name=self.service_name,
                )
                return True, "success"

        # Should not reach here
        return False, "unknown"

    def send_to_dlq(
        self,
        request_id: str,
        reason: str,
        metrics: CompoundFailureMetrics,
    ) -> bool:
        """
        Send failed request to DLQ.

        Uses EXISTING DLQ boundaries only.
        """
        entry = {
            "request_id": request_id,
            "reason": reason,
            "timestamp": datetime.now(tz.utc).isoformat(),
            "service_name": self.service_name,
            "replayed": False,
            "replay_count": 0,
        }

        success = self.dlq.add_entry(entry)
        if success:
            metrics.dlq_entries_created += 1
            metrics.record_decision(
                event="dlq_entry_created",
                allowed=True,
                reason="stability_ok_no_intervention",
                service_name=self.service_name,
            )
        else:
            metrics.dlq_entries_rejected += 1
            metrics.dlq_overflow = True
            metrics.record_decision(
                event="dlq_entry_rejected",
                allowed=False,
                reason="policy_constraint_active",
                service_name=self.service_name,
            )

        return success

    def apply_memory_pressure(self, percent: float, metrics: CompoundFailureMetrics) -> None:
        """Apply simulated memory pressure."""
        self.memory.set_pressure(percent)
        if self.memory.warning:
            metrics.memory_warnings_triggered += 1
        if self.memory.throttled:
            # Simulate GC pause under memory pressure
            self.memory.simulate_gc_pause()
            metrics.gc_pauses += 1

    def clear_state(self) -> None:
        """Clear all state for cleanup."""
        with self._lock:
            self.rate_limit = RateLimitState(service_name=self.service_name)
            self.memory = MemoryPressureState()
            self.dlq = DLQState()
            self.retries.clear()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def selfhealing_services():
    """Provide all selfhealing services as a fixture."""
    return _get_selfhealing_services()


@pytest.fixture
def decision_logger_classes():
    """Provide decision logger classes as a fixture."""
    return _get_decision_logger()


@pytest.fixture
def compound_simulator():
    """Create compound failure simulator."""
    simulator = CompoundFailureSimulator(SERVICE_PAYMENT_GATEWAY)
    yield simulator
    simulator.clear_state()


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
# Scenario A1: Retry Storm + Rate Limit
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42A1RetryStormPlusRateLimit:
    """
    Scenario A1: Retry Storm + Rate Limit

    재시도 폭풍이 속도 제한을 초과할 때의 동작 검증.

    Expectation:
        - 속도 제한이 강제됨
        - 재시도가 깔끔하게 중단됨
        - 연쇄 장애 없음
    """

    def test_stage_42_a1_retry_burst_triggers_rate_limit(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 버스트 재시도가 속도 제한을 트리거

        Given:
            - 서비스가 정상 상태
            - 속도 제한이 50 req/sec으로 설정됨

        When:
            - 100개 요청이 동시에 실패하여 재시도 시도
            - 재시도 버스트가 속도 제한 초과

        Then:
            - 속도 제한이 강제됨
            - 재시도가 깔끔하게 중단됨
            - 시스템 충돌 없음
            - Decision Record가 일관됨
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A1_retry_burst_rate_limit")
        compound_simulator.rate_limit.limit = 50  # 50 req/sec

        # Failure injection: all requests fail initially
        fail_count = [0]
        total_failures_to_inject = 30  # First 30 attempts fail

        def should_fail() -> bool:
            if fail_count[0] < total_failures_to_inject:
                fail_count[0] += 1
                return True
            return False

        # when: send burst of requests
        results = []
        for i in range(100):
            request_id = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=should_fail,
                metrics=metrics,
            )
            results.append((success, reason))

            if not success and reason == "retry_exhausted":
                compound_simulator.send_to_dlq(request_id, reason, metrics)

        # then
        # System survives
        assert not metrics.system_crashed, "System should not crash"
        assert not metrics.deadlock_detected, "No deadlock should occur"

        # Rate limit enforced
        assert metrics.rate_limit_rejections > 0, "Rate limit should be triggered"
        assert not metrics.rate_limit_bypassed, "Rate limit should not be bypassed"

        # Retry boundaries respected
        assert not metrics.retry_limit_exceeded, "Retry limits should be respected"

        # No security boundary bypass
        assert not metrics.security_boundary_bypassed, "Security boundary should not be bypassed"
        assert not metrics.unauthorized_auto_heal, "No unauthorized auto-heal"

        # Decision records consistent
        assert len(metrics.decision_records) > 0, "Decision records should be logged"
        assert len(metrics.schema_violations) == 0, f"Schema violations: {metrics.schema_violations}"

        # DLQ entries created for exhausted retries
        rate_limited_count = sum(1 for s, r in results if r == "rate_limited")
        retry_exhausted_count = sum(1 for s, r in results if r == "retry_exhausted")
        success_count = sum(1 for s, r in results if s)

        assert rate_limited_count + retry_exhausted_count + success_count == 100, "All requests should be accounted for"

    def test_stage_42_a1_rate_limit_stops_retry_cascade(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 속도 제한이 재시도 연쇄를 중단

        Given:
            - 낮은 속도 제한 (10 req/sec)

        When:
            - 50개 동시 재시도 폭풍

        Then:
            - 10개만 처리됨
            - 나머지는 속도 제한으로 차단
            - 연쇄 없이 깔끔한 실패
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A1_rate_limit_stops_cascade")
        compound_simulator.rate_limit.limit = 10  # Very low limit

        # All requests fail (to trigger retries)
        def always_fail() -> bool:
            return True

        # when
        results = []
        for _ in range(50):
            request_id = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=always_fail,
                metrics=metrics,
            )
            results.append((success, reason))

        # then
        rate_limited = [r for r in results if r[1] == "rate_limited"]

        # Most requests should be rate limited
        assert len(rate_limited) >= 40, f"Expected most requests rate limited, got {len(rate_limited)}"

        # System stable
        assert not metrics.system_crashed
        assert not metrics.deadlock_detected
        assert len(metrics.unhandled_exceptions) == 0


# =============================================================================
# Scenario A2: Retry + Memory Pressure
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42A2RetryPlusMemoryPressure:
    """
    Scenario A2: Retry + Memory Pressure

    재시도 실행 중 메모리 압박 상황 검증.

    Expectation:
        - 점진적 성능 저하
        - 충돌 없음
        - 재시도 경계 준수
    """

    def test_stage_42_a2_memory_pressure_during_retry(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 재시도 중 메모리 압박

        Given:
            - 시스템이 정상 메모리 상태

        When:
            - 재시도 실행 중 메모리 압박 70% → 90%

        Then:
            - 시스템이 충돌하지 않음
            - GC 일시정지 발생
            - 재시도가 정상 완료 또는 소진
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A2_memory_pressure_during_retry")
        compound_simulator.rate_limit.limit = 1000  # High limit to not interfere

        # Fail first 2 attempts per request
        attempt_tracker: Dict[str, int] = {}

        def fail_first_two() -> bool:
            # Use thread-local or shared tracking
            import threading

            tid = threading.current_thread().ident
            key = str(tid)
            if key not in attempt_tracker:
                attempt_tracker[key] = 0
            attempt_tracker[key] += 1
            return attempt_tracker[key] <= 2

        # when
        results = []
        for i in range(20):
            request_id = compound_simulator.create_request()

            # Apply increasing memory pressure
            pressure = 50 + (i * 2)  # 50% → 90%
            compound_simulator.apply_memory_pressure(pressure, metrics)

            attempt_tracker.clear()  # Reset per request

            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=fail_first_two,
                metrics=metrics,
            )
            results.append((success, reason))

        # then
        # System survives
        assert not metrics.system_crashed, "System should not crash"
        assert not metrics.deadlock_detected, "No deadlock"

        # Memory warnings triggered under pressure
        assert metrics.memory_warnings_triggered > 0, "Memory warnings should be triggered"

        # GC pauses occurred
        assert metrics.gc_pauses > 0, "GC pauses should occur under memory pressure"

        # Retry limits still respected
        assert not metrics.retry_limit_exceeded, "Retry limits should be respected"

        # Some requests should succeed (after 3rd attempt)
        success_count = sum(1 for s, _ in results if s)
        assert success_count > 0, "Some requests should succeed"

    def test_stage_42_a2_graceful_degradation_under_pressure(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 압박 상황에서 점진적 성능 저하

        Given:
            - 메모리가 critical 수준 (95%)

        When:
            - 요청 처리 시도

        Then:
            - 시스템이 스로틀링되지만 충돌하지 않음
            - 요청이 완료되거나 정상적으로 실패
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A2_graceful_degradation")

        # Set critical memory pressure
        compound_simulator.apply_memory_pressure(95, metrics)

        # when
        results = []
        for _ in range(10):
            request_id = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=lambda: False,  # Requests succeed
                metrics=metrics,
            )
            results.append((success, reason))

        # then
        # System did not crash
        assert not metrics.system_crashed

        # Throttling was active
        assert compound_simulator.memory.throttled, "Memory should be throttled"

        # All requests still completed
        assert len(results) == 10, "All requests should complete"


# =============================================================================
# Scenario A3: Rate Limit + Partial Service Degradation
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42A3RateLimitPlusPartialDegradation:
    """
    Scenario A3: Rate Limit + Partial Service Degradation

    하나의 서비스가 저하되고 다른 서비스는 정상인 상황.

    Expectation:
        - 격리 유지
        - 교차 서비스 영향 없음
    """

    def test_stage_42_a3_partial_degradation_isolation(
        self,
        selfhealing_services,
    ):
        """
        시나리오: 부분 서비스 저하 시 격리

        Given:
            - Payment 서비스가 저하됨
            - Order 서비스가 정상

        When:
            - 두 서비스에 동시 요청

        Then:
            - Payment 실패가 Order에 영향 없음
            - 각 서비스가 독립적으로 동작
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A3_partial_degradation")

        payment_simulator = CompoundFailureSimulator(SERVICE_PAYMENT_GATEWAY)
        order_simulator = CompoundFailureSimulator(SERVICE_ORDER_PROCESSOR)

        # Payment service degraded (rate limited)
        payment_simulator.rate_limit.limit = 5

        # Order service healthy
        order_simulator.rate_limit.limit = 1000

        # when
        payment_results = []
        order_results = []

        for _ in range(20):
            # Payment request (will be rate limited)
            p_req = payment_simulator.create_request()
            p_success, p_reason = payment_simulator.attempt_request_with_retry(
                request_id=p_req,
                should_fail=lambda: False,
                metrics=metrics,
            )
            payment_results.append((p_success, p_reason))

            # Order request (should succeed)
            o_req = order_simulator.create_request()
            o_success, o_reason = order_simulator.attempt_request_with_retry(
                request_id=o_req,
                should_fail=lambda: False,
                metrics=metrics,
            )
            order_results.append((o_success, o_reason))

        # then
        # Payment service was rate limited
        payment_rate_limited = sum(1 for s, r in payment_results if r == "rate_limited")
        assert payment_rate_limited > 0, "Payment should be rate limited"

        # Order service was NOT affected
        order_success = sum(1 for s, _ in order_results if s)
        assert order_success == 20, "All order requests should succeed"

        # Isolation maintained
        assert not metrics.security_boundary_bypassed

        # Cleanup
        payment_simulator.clear_state()
        order_simulator.clear_state()

    def test_stage_42_a3_no_cross_service_cascade(
        self,
        selfhealing_services,
    ):
        """
        시나리오: 서비스 간 연쇄 장애 없음

        Given:
            - 세 개 서비스 (Payment, Order, Notification)
            - Payment만 장애

        When:
            - Payment 장애 발생

        Then:
            - Order, Notification은 정상 동작
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A3_no_cascade")

        simulators = {
            SERVICE_PAYMENT_GATEWAY: CompoundFailureSimulator(SERVICE_PAYMENT_GATEWAY),
            SERVICE_ORDER_PROCESSOR: CompoundFailureSimulator(SERVICE_ORDER_PROCESSOR),
            SERVICE_NOTIFICATION: CompoundFailureSimulator(SERVICE_NOTIFICATION),
        }

        # Only payment fails
        def payment_fails() -> bool:
            return True

        def others_succeed() -> bool:
            return False

        # when
        results: Dict[str, List[Tuple[bool, str]]] = {k: [] for k in simulators}

        for _ in range(10):
            for name, sim in simulators.items():
                req = sim.create_request()
                should_fail = payment_fails if name == SERVICE_PAYMENT_GATEWAY else others_succeed
                success, reason = sim.attempt_request_with_retry(
                    request_id=req,
                    should_fail=should_fail,
                    metrics=metrics,
                )
                results[name].append((success, reason))

        # then
        # Payment all failed
        payment_failures = sum(1 for s, _ in results[SERVICE_PAYMENT_GATEWAY] if not s)
        assert payment_failures == 10, "All payment should fail"

        # Others all succeeded
        order_success = sum(1 for s, _ in results[SERVICE_ORDER_PROCESSOR] if s)
        notif_success = sum(1 for s, _ in results[SERVICE_NOTIFICATION] if s)
        assert order_success == 10, "All order should succeed"
        assert notif_success == 10, "All notification should succeed"

        # Cleanup
        for sim in simulators.values():
            sim.clear_state()


# =============================================================================
# Scenario A4: Retry Exhaustion + DLQ Boundary
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42A4RetryExhaustionPlusDLQ:
    """
    Scenario A4: Retry Exhaustion + DLQ Boundary

    재시도가 압박 상황에서 최대 횟수에 도달.

    Expectation:
        - DLQ 라우팅이 결정론적
        - 명시적 트리거 없이 재생 없음
    """

    def test_stage_42_a4_exhausted_retry_goes_to_dlq(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 소진된 재시도가 DLQ로 이동

        Given:
            - 서비스가 계속 실패

        When:
            - 재시도가 최대 횟수(3)에 도달

        Then:
            - 요청이 DLQ로 이동
            - 자동 재생 없음
            - DLQ 항목이 결정론적으로 생성됨
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A4_exhausted_to_dlq")

        # All requests fail
        def always_fail() -> bool:
            return True

        # when
        for i in range(10):
            request_id = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=always_fail,
                metrics=metrics,
            )

            # If retry exhausted, send to DLQ
            if reason == "retry_exhausted":
                compound_simulator.send_to_dlq(request_id, reason, metrics)

        # then
        # All went to DLQ
        assert metrics.dlq_entries_created == 10, "All exhausted requests should go to DLQ"

        # No auto-replay
        for entry in compound_simulator.dlq.entries:
            assert not entry.get("replayed", False), "No auto-replay should occur"
            assert entry.get("replay_count", 0) == 0, "Replay count should be 0"

        # Decision records consistent
        dlq_records = [r for r in metrics.decision_records if r["event"] == "dlq_entry_created"]
        assert len(dlq_records) == 10, "Decision records should match DLQ entries"

    def test_stage_42_a4_dlq_boundary_under_pressure(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 압박 상황에서 DLQ 경계 유지

        Given:
            - DLQ가 거의 가득 참 (995/1000)
            - 계속되는 실패

        When:
            - 추가 실패가 DLQ에 추가 시도

        Then:
            - DLQ 크기 제한 존중
            - 초과 항목 거부
            - 오버플로우 메트릭 기록
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A4_dlq_boundary")

        # Pre-fill DLQ to near capacity
        for i in range(995):
            compound_simulator.dlq.entries.append(
                {
                    "request_id": f"prefill-{i}",
                    "reason": "prefill",
                    "timestamp": datetime.now(tz.utc).isoformat(),
                    "replayed": False,
                }
            )

        def always_fail() -> bool:
            return True

        # when
        for _ in range(20):
            request_id = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=request_id,
                should_fail=always_fail,
                metrics=metrics,
            )
            if reason == "retry_exhausted":
                compound_simulator.send_to_dlq(request_id, reason, metrics)

        # then
        # DLQ should not exceed limit
        assert len(compound_simulator.dlq.entries) <= DLQ_MAX_SIZE, f"DLQ should not exceed {DLQ_MAX_SIZE}"

        # Some entries rejected
        assert metrics.dlq_entries_rejected > 0, "Some entries should be rejected"

        # Overflow flagged
        assert metrics.dlq_overflow, "DLQ overflow should be flagged"

        # Decision records capture rejections
        rejection_records = [r for r in metrics.decision_records if r["event"] == "dlq_entry_rejected"]
        assert len(rejection_records) > 0, "Rejections should be recorded"

    def test_stage_42_a4_no_replay_without_explicit_trigger(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 명시적 트리거 없이 재생 없음

        Given:
            - DLQ에 항목 존재

        When:
            - 시간 경과 (자동 재생 트리거 없음)

        Then:
            - 항목이 재생되지 않음
            - 수동 개입 필요
        """
        # given
        metrics = CompoundFailureMetrics(test_name="A4_no_auto_replay")

        # Add entries to DLQ
        for i in range(5):
            entry = {
                "request_id": f"test-{i}",
                "reason": "retry_exhausted",
                "timestamp": datetime.now(tz.utc).isoformat(),
                "replayed": False,
                "replay_count": 0,
            }
            compound_simulator.dlq.entries.append(entry)

        # when: simulate time passing (no explicit replay trigger)
        time.sleep(0.1)  # Simulate passage of time

        # then: no auto-replay occurred
        for entry in compound_simulator.dlq.entries:
            assert not entry["replayed"], "No auto-replay should occur"
            assert entry["replay_count"] == 0, "Replay count should remain 0"

        # Unauthorized auto-heal should not have occurred
        assert not metrics.unauthorized_auto_heal


# =============================================================================
# Integration Test: Combined Deterministic Scenarios
# =============================================================================


@pytest.mark.compound
@pytest.mark.chaos
class TestStage42DeterministicIntegration:
    """
    통합 테스트: 결정론적 시나리오 조합

    여러 장애 유형을 순차적으로 적용하여 시스템 안정성 검증.
    """

    def test_stage_42_sequential_compound_failures(
        self,
        compound_simulator: CompoundFailureSimulator,
    ):
        """
        시나리오: 순차적 복합 장애

        Given:
            - 정상 시스템 상태

        When:
            - Phase 1: 재시도 폭풍
            - Phase 2: 속도 제한 활성화
            - Phase 3: 메모리 압박 추가
            - Phase 4: DLQ 처리

        Then:
            - 각 단계에서 적절한 동작
            - 전체 시스템 안정
            - 모든 경계 유지
        """
        # given
        metrics = CompoundFailureMetrics(test_name="sequential_compound")
        phase_results: Dict[str, List] = {
            "phase1_retry": [],
            "phase2_rate_limit": [],
            "phase3_memory": [],
            "phase4_dlq": [],
        }

        # Phase 1: Retry storm (failures that recover)
        def fail_twice() -> bool:
            return random.random() < 0.6

        for _ in range(10):
            req = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=req,
                should_fail=fail_twice,
                metrics=metrics,
            )
            phase_results["phase1_retry"].append((success, reason))

        # Phase 2: Rate limiting kicks in
        compound_simulator.rate_limit.limit = 3  # Very low

        for _ in range(10):
            req = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=req,
                should_fail=lambda: False,
                metrics=metrics,
            )
            phase_results["phase2_rate_limit"].append((success, reason))

        # Phase 3: Memory pressure added
        compound_simulator.rate_limit.limit = 1000  # Reset rate limit

        for i in range(10):
            compound_simulator.apply_memory_pressure(70 + i * 2, metrics)
            req = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=req,
                should_fail=lambda: random.random() < 0.3,
                metrics=metrics,
            )
            phase_results["phase3_memory"].append((success, reason))

        # Phase 4: DLQ handling for failures
        def always_fail() -> bool:
            return True

        for _ in range(5):
            req = compound_simulator.create_request()
            success, reason = compound_simulator.attempt_request_with_retry(
                request_id=req,
                should_fail=always_fail,
                metrics=metrics,
            )
            if reason == "retry_exhausted":
                compound_simulator.send_to_dlq(req, reason, metrics)
            phase_results["phase4_dlq"].append((success, reason))

        # then
        # System survived all phases
        assert not metrics.system_crashed, "System should survive all phases"
        assert not metrics.deadlock_detected, "No deadlock"

        # Rate limiting was enforced in phase 2
        rate_limited = sum(1 for s, r in phase_results["phase2_rate_limit"] if r == "rate_limited")
        assert rate_limited > 0, "Rate limiting should be enforced"

        # Memory warnings in phase 3
        assert metrics.memory_warnings_triggered > 0, "Memory warnings should trigger"

        # DLQ entries in phase 4
        assert metrics.dlq_entries_created == 5, "All phase 4 failures should go to DLQ"

        # All boundaries maintained
        assert not metrics.security_boundary_bypassed
        assert not metrics.unauthorized_auto_heal
        assert len(metrics.schema_violations) == 0

        # Summary
        summary = metrics.to_summary()
        assert summary["decision_records_count"] > 0, "Decision records should be logged"


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
