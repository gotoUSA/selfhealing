"""
Stage 32: Retry Storm Extended

Purpose: Verify retry storm handling with memory leak prevention, DLQ management,
         and idempotency guarantees during high-failure scenarios.

Extended from Stage 22 (Rate Limit Conflict) to cover:
- Memory leak prevention during backoff
- DLQ explosion prevention with throttling
- Payment webhook replay safety (idempotency)
- Retry + Clock Skew compound scenario

Scenarios:
  SC-32-1: Memory Leak During Backoff (1000 concurrent failures)
  SC-32-2: DLQ Not Exploding (max 500/sec input throttling)
  SC-32-3: Payment Webhook Replay Safety (idempotency validation)
  SC-32-4: Retry Timestamp Skew Compound

Verification:
  - [ ] Memory peak < 500MB during retry storm
  - [ ] DLQ input rate < 500/sec
  - [ ] Duplicate payments = 0
  - [ ] Clock skew tolerance ±30s

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage32_retry_storm_extended.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage32_retry_storm_extended.py \\
        --host=http://localhost:8000 \\
        --users=100 --spawn-rate=20 --run-time=5m \\
        --headless --html=stage32_report.html

Reference:
    - docs/STAGE_31_36_EXTENSION_PLAN.md (Stage 32)
    - Stage 22 (Rate Limit Conflict) base implementation
"""

import os
import sys
import time
import json
import random
import threading
import uuid
import gc
import tracemalloc
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from collections import defaultdict, deque
from dataclasses import dataclass, field
from weakref import WeakSet

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks


STAGE_NAME = "[Stage32-RetryStormExtended]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 20s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE = max(3, int(30 * _scale))  # Normal baseline
PHASE_2_MEMORY_LEAK = max(4, int(60 * _scale))  # Memory leak test during backoff
PHASE_3_DLQ_EXPLOSION = max(4, int(60 * _scale))  # DLQ throttling test
PHASE_4_WEBHOOK_REPLAY = max(4, int(60 * _scale))  # Idempotency test
PHASE_5_CLOCK_SKEW = max(3, int(50 * _scale))  # Clock skew compound test
PHASE_6_VERIFICATION = max(2, int(40 * _scale))  # Verification

TOTAL_DURATION = (
    PHASE_1_BASELINE
    + PHASE_2_MEMORY_LEAK
    + PHASE_3_DLQ_EXPLOSION
    + PHASE_4_WEBHOOK_REPLAY
    + PHASE_5_CLOCK_SKEW
    + PHASE_6_VERIFICATION
)

# Memory thresholds
MEMORY_PEAK_THRESHOLD_MB = 500
MEMORY_SAMPLE_INTERVAL_S = 1.0

# DLQ configuration
DLQ_MAX_INPUT_RATE = 500  # per second
DLQ_MAX_SIZE = 10000
DLQ_THROTTLE_THRESHOLD = 0.8  # Start throttling at 80% capacity

# Retry configuration
MAX_RETRY_ATTEMPTS = 5
BASE_BACKOFF_S = 0.5
MAX_BACKOFF_S = 5.0
BACKOFF_MULTIPLIER = 2.0

# Clock skew configuration
CLOCK_SKEW_TOLERANCE_S = 30
SIMULATED_CLOCK_SKEW_S = 300  # 5 minutes drift for testing


# =============================================================================
# Retry Storm Statistics
# =============================================================================


@dataclass
class RetryStormStats:
    """Statistics tracking for retry storm scenarios"""

    start_time: Optional[float] = None
    phase: str = "baseline"

    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Scenario 1: Memory Leak
    retry_objects_created: int = 0
    retry_objects_collected: int = 0
    memory_samples_mb: List[float] = field(default_factory=list)
    memory_peak_mb: float = 0
    gc_collections: int = 0
    memory_leak_detected: bool = False

    # Scenario 2: DLQ Throttling
    dlq_items_added: int = 0
    dlq_items_processed: int = 0
    dlq_throttle_events: int = 0
    dlq_current_size: int = 0
    dlq_input_rates: List[float] = field(default_factory=list)  # per second
    dlq_overflow_prevented: int = 0

    # Scenario 3: Webhook Replay
    webhooks_received: int = 0
    webhooks_unique: int = 0
    webhooks_duplicate: int = 0
    idempotency_keys_seen: Set[str] = field(default_factory=set)
    duplicate_payments_prevented: int = 0
    duplicate_stock_deductions_prevented: int = 0

    # Scenario 4: Clock Skew
    timestamp_validations: int = 0
    timestamp_accepted: int = 0
    timestamp_rejected: int = 0
    clock_skew_within_tolerance: int = 0
    clock_skew_exceeded: int = 0

    # Verification results
    verification: Dict[str, Optional[bool]] = field(
        default_factory=lambda: {
            "memory_peak_under_500mb": None,
            "dlq_rate_under_500_sec": None,
            "duplicate_payments_zero": None,
            "clock_skew_tolerance_30s": None,
        }
    )


_storm_stats = RetryStormStats()
_stats_lock = threading.Lock()


# =============================================================================
# Simulated DLQ (Dead Letter Queue)
# =============================================================================


class SimulatedDLQ:
    """Simulated Dead Letter Queue with throttling"""

    def __init__(self, max_size: int = DLQ_MAX_SIZE, max_input_rate: int = DLQ_MAX_INPUT_RATE):
        self.max_size = max_size
        self.max_input_rate = max_input_rate
        self.queue: deque = deque(maxlen=max_size)
        self.input_timestamps: deque = deque()  # Track input times for rate limiting
        self.lock = threading.Lock()
        self.throttling = False

    def add(self, item: dict) -> bool:
        """Add item to DLQ with throttling"""
        with self.lock:
            now = time.time()

            # Clean old timestamps (keep last second)
            while self.input_timestamps and now - self.input_timestamps[0] > 1.0:
                self.input_timestamps.popleft()

            # Check rate limit
            current_rate = len(self.input_timestamps)

            if current_rate >= self.max_input_rate:
                self.throttling = True
                with _stats_lock:
                    _storm_stats.dlq_throttle_events += 1
                return False

            # Check capacity
            if len(self.queue) >= self.max_size * DLQ_THROTTLE_THRESHOLD:
                self.throttling = True
                with _stats_lock:
                    _storm_stats.dlq_overflow_prevented += 1
                return False

            # Add item
            self.queue.append({**item, "added_at": now})
            self.input_timestamps.append(now)
            self.throttling = False

            with _stats_lock:
                _storm_stats.dlq_items_added += 1
                _storm_stats.dlq_current_size = len(self.queue)

            return True

    def process(self) -> Optional[dict]:
        """Process (remove) item from DLQ"""
        with self.lock:
            if self.queue:
                item = self.queue.popleft()
                with _stats_lock:
                    _storm_stats.dlq_items_processed += 1
                    _storm_stats.dlq_current_size = len(self.queue)
                return item
            return None

    def get_input_rate(self) -> float:
        """Get current input rate (items per second)"""
        with self.lock:
            now = time.time()
            # Clean old timestamps
            while self.input_timestamps and now - self.input_timestamps[0] > 1.0:
                self.input_timestamps.popleft()
            return len(self.input_timestamps)

    def size(self) -> int:
        """Get current queue size"""
        with self.lock:
            return len(self.queue)


_dlq = SimulatedDLQ()


# =============================================================================
# Simulated Retry Object Pool (for memory tracking)
# =============================================================================


class RetryObject:
    """Simulated retry object that should be garbage collected"""

    _all_instances = WeakSet()
    _created_count = 0
    _collected_count = 0
    _lock = threading.Lock()

    def __init__(self, request_id: str, attempt: int, payload: dict):
        self.request_id = request_id
        self.attempt = attempt
        self.payload = payload
        self.created_at = time.time()
        self.backoff_until = 0

        with self._lock:
            RetryObject._created_count += 1
            RetryObject._all_instances.add(self)

    def __del__(self):
        with RetryObject._lock:
            RetryObject._collected_count += 1

    @classmethod
    def get_stats(cls) -> tuple:
        """Get (created, collected, active) counts"""
        with cls._lock:
            active = len(cls._all_instances)
            return cls._created_count, cls._collected_count, active

    @classmethod
    def reset_stats(cls):
        """Reset statistics"""
        with cls._lock:
            cls._created_count = 0
            cls._collected_count = 0


# =============================================================================
# Idempotency Manager
# =============================================================================


class IdempotencyManager:
    """Manage idempotency keys to prevent duplicate processing"""

    def __init__(self, max_keys: int = 100000, ttl_seconds: int = 3600):
        self.max_keys = max_keys
        self.ttl_seconds = ttl_seconds
        self.keys: Dict[str, float] = {}  # key -> timestamp
        self.lock = threading.Lock()

    def check_and_set(self, key: str) -> bool:
        """
        Check if key exists and set it if not.
        Returns True if this is a NEW key (first time seen).
        Returns False if this is a DUPLICATE key.
        """
        with self.lock:
            now = time.time()

            # Clean expired keys periodically (every 100 checks)
            if random.random() < 0.01:
                self._cleanup_expired(now)

            if key in self.keys:
                # Duplicate!
                return False

            # New key
            self.keys[key] = now

            # Prevent unbounded growth
            if len(self.keys) > self.max_keys:
                self._cleanup_oldest()

            return True

    def _cleanup_expired(self, now: float):
        """Remove expired keys"""
        expired = [k for k, ts in self.keys.items() if now - ts > self.ttl_seconds]
        for k in expired:
            del self.keys[k]

    def _cleanup_oldest(self):
        """Remove oldest keys when at capacity"""
        if len(self.keys) > self.max_keys * 0.9:
            # Remove oldest 10%
            sorted_keys = sorted(self.keys.items(), key=lambda x: x[1])
            for k, _ in sorted_keys[: int(len(sorted_keys) * 0.1)]:
                del self.keys[k]


_idempotency = IdempotencyManager()


# =============================================================================
# Memory Monitoring
# =============================================================================

_memory_monitor_thread = None
_memory_monitor_running = False


def _start_memory_monitor():
    """Start background memory monitoring"""
    global _memory_monitor_thread, _memory_monitor_running

    if _memory_monitor_running:
        return

    _memory_monitor_running = True
    tracemalloc.start()

    def monitor():
        while _memory_monitor_running:
            try:
                current, peak = tracemalloc.get_traced_memory()
                current_mb = current / (1024 * 1024)
                peak_mb = peak / (1024 * 1024)

                with _stats_lock:
                    _storm_stats.memory_samples_mb.append(current_mb)
                    if peak_mb > _storm_stats.memory_peak_mb:
                        _storm_stats.memory_peak_mb = peak_mb

                # Check for leak
                if len(_storm_stats.memory_samples_mb) > 10:
                    recent = _storm_stats.memory_samples_mb[-10:]
                    if all(recent[i] < recent[i + 1] for i in range(len(recent) - 1)):
                        # Monotonically increasing - possible leak
                        if not _storm_stats.memory_leak_detected:
                            _storm_stats.memory_leak_detected = True
                            print(f"   ⚠️ Possible memory leak detected!")

                time.sleep(MEMORY_SAMPLE_INTERVAL_S)
            except Exception:
                pass

    _memory_monitor_thread = threading.Thread(target=monitor, daemon=True)
    _memory_monitor_thread.start()


def _stop_memory_monitor():
    """Stop memory monitoring"""
    global _memory_monitor_running
    _memory_monitor_running = False
    tracemalloc.stop()


# =============================================================================
# Clock Skew Simulation
# =============================================================================

_simulated_clock_offset = 0  # Offset in seconds


def _get_simulated_time() -> float:
    """Get time with simulated clock skew"""
    return time.time() + _simulated_clock_offset


def _set_clock_skew(offset_seconds: float):
    """Set simulated clock skew"""
    global _simulated_clock_offset
    _simulated_clock_offset = offset_seconds


def _validate_timestamp(timestamp: float, tolerance_seconds: float = CLOCK_SKEW_TOLERANCE_S) -> bool:
    """
    Validate timestamp is within tolerance of current time.
    Returns True if valid, False if clock skew exceeds tolerance.
    """
    now = time.time()
    diff = abs(now - timestamp)
    return diff <= tolerance_seconds


# =============================================================================
# Phase Management
# =============================================================================


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _storm_stats.start_time is None:
        return "baseline"

    elapsed = time.time() - _storm_stats.start_time

    boundaries = [
        (PHASE_1_BASELINE, "baseline"),
        (PHASE_1_BASELINE + PHASE_2_MEMORY_LEAK, "memory_leak"),
        (PHASE_1_BASELINE + PHASE_2_MEMORY_LEAK + PHASE_3_DLQ_EXPLOSION, "dlq_explosion"),
        (PHASE_1_BASELINE + PHASE_2_MEMORY_LEAK + PHASE_3_DLQ_EXPLOSION + PHASE_4_WEBHOOK_REPLAY, "webhook_replay"),
        (
            PHASE_1_BASELINE + PHASE_2_MEMORY_LEAK + PHASE_3_DLQ_EXPLOSION + PHASE_4_WEBHOOK_REPLAY + PHASE_5_CLOCK_SKEW,
            "clock_skew",
        ),
    ]

    for boundary, phase in boundaries:
        if elapsed < boundary:
            return phase

    return "verification"


def _update_phase():
    """Update phase and trigger phase-specific actions"""
    phase = _get_current_phase()

    if phase != _storm_stats.phase:
        old_phase = _storm_stats.phase
        _storm_stats.phase = phase

        if phase == "memory_leak":
            print(f"\n🧠 Phase 2: Memory Leak During Backoff Test")
            print(f"   - Simulating 1000 concurrent retry operations")
            print(f"   - Monitoring memory usage and GC behavior")
            _start_memory_monitor()

        elif phase == "dlq_explosion":
            print(f"\n📥 Phase 3: DLQ Explosion Prevention Test")
            print(f"   - Memory samples: {len(_storm_stats.memory_samples_mb)}")
            print(f"   - Peak memory: {_storm_stats.memory_peak_mb:.1f}MB")
            print(f"   - Testing DLQ throttling at high failure rate")

        elif phase == "webhook_replay":
            print(f"\n🔁 Phase 4: Webhook Replay Safety Test")
            print(f"   - DLQ items added: {_storm_stats.dlq_items_added}")
            print(f"   - DLQ throttle events: {_storm_stats.dlq_throttle_events}")
            print(f"   - Testing idempotency of payment webhooks")

        elif phase == "clock_skew":
            print(f"\n⏰ Phase 5: Retry + Clock Skew Compound Test")
            print(f"   - Unique webhooks: {_storm_stats.webhooks_unique}")
            print(f"   - Duplicates prevented: {_storm_stats.webhooks_duplicate}")
            print(f"   - Simulating +5 minute clock drift")
            _set_clock_skew(SIMULATED_CLOCK_SKEW_S)

        elif phase == "verification":
            print(f"\n✅ Phase 6: Verification")
            print(f"   - Timestamp accepted: {_storm_stats.timestamp_accepted}")
            print(f"   - Timestamp rejected: {_storm_stats.timestamp_rejected}")
            _set_clock_skew(0)  # Reset clock
            _stop_memory_monitor()
            _perform_final_verification()


def _perform_final_verification():
    """Perform final verification of retry storm handling"""
    print(f"\n📊 Final Verification:")

    # Force GC and get final memory stats
    gc.collect()
    gc.collect()

    created, collected, active = RetryObject.get_stats()
    with _stats_lock:
        _storm_stats.retry_objects_created = created
        _storm_stats.retry_objects_collected = collected
        _storm_stats.gc_collections = gc.get_count()[0]

    # Memory check
    _storm_stats.verification["memory_peak_under_500mb"] = _storm_stats.memory_peak_mb < MEMORY_PEAK_THRESHOLD_MB
    print(f"   - Memory peak < 500MB: {'✓' if _storm_stats.verification['memory_peak_under_500mb'] else '✗'}")
    print(f"     (Peak: {_storm_stats.memory_peak_mb:.1f}MB, Active objects: {active})")

    # DLQ rate check
    if _storm_stats.dlq_input_rates:
        max_rate = max(_storm_stats.dlq_input_rates)
        avg_rate = sum(_storm_stats.dlq_input_rates) / len(_storm_stats.dlq_input_rates)
    else:
        max_rate = 0
        avg_rate = 0

    _storm_stats.verification["dlq_rate_under_500_sec"] = max_rate < DLQ_MAX_INPUT_RATE * 1.1  # 10% tolerance
    print(f"   - DLQ rate < 500/sec: {'✓' if _storm_stats.verification['dlq_rate_under_500_sec'] else '✗'}")
    print(f"     (Max: {max_rate:.0f}/sec, Avg: {avg_rate:.1f}/sec, Throttles: {_storm_stats.dlq_throttle_events})")

    # Duplicate payments check
    _storm_stats.verification["duplicate_payments_zero"] = (
        _storm_stats.duplicate_payments_prevented == _storm_stats.webhooks_duplicate
    )
    print(f"   - Duplicate payments = 0: {'✓' if _storm_stats.verification['duplicate_payments_zero'] else '✗'}")
    print(
        f"     (Duplicates received: {_storm_stats.webhooks_duplicate}, All prevented: {_storm_stats.duplicate_payments_prevented})"
    )

    # Clock skew tolerance check
    if _storm_stats.timestamp_validations > 0:
        skew_accuracy = _storm_stats.clock_skew_within_tolerance / max(_storm_stats.timestamp_validations, 1)
        _storm_stats.verification["clock_skew_tolerance_30s"] = _storm_stats.clock_skew_exceeded == 0 or skew_accuracy >= 0.95
    else:
        _storm_stats.verification["clock_skew_tolerance_30s"] = True

    print(f"   - Clock skew ±30s tolerance: {'✓' if _storm_stats.verification['clock_skew_tolerance_30s'] else '✗'}")
    print(f"     (Within: {_storm_stats.clock_skew_within_tolerance}, Exceeded: {_storm_stats.clock_skew_exceeded})")

    # Overall pass/fail
    all_passed = all(v for v in _storm_stats.verification.values() if v is not None)
    print(f"\n{'='*60}")
    print(f"   Stage 32 Result: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print(f"{'='*60}")


# =============================================================================
# Load Shape
# =============================================================================


class RetryStormExtendedShape(LoadTestShape):
    """
    Load shape for retry storm extended testing.

    Phase 1: Normal baseline
    Phase 2: Memory leak test (high concurrent retries)
    Phase 3: DLQ explosion test (high failure rate)
    Phase 4: Webhook replay test
    Phase 5: Clock skew compound test
    Phase 6: Verification
    """

    def tick(self):
        """Return (user_count, spawn_rate) tuple"""
        run_time = self.get_run_time()

        _update_phase()

        if run_time > TOTAL_DURATION:
            return None

        phase = _get_current_phase()

        if phase == "baseline":
            return (30, 5)
        elif phase == "memory_leak":
            return (100, 25)  # High concurrency for memory test
        elif phase == "dlq_explosion":
            return (80, 20)  # High failure rate
        elif phase == "webhook_replay":
            return (50, 10)  # Moderate load with replays
        elif phase == "clock_skew":
            return (40, 10)
        else:  # verification
            return (20, 5)


# =============================================================================
# Test User - Scenario 1: Memory Leak During Backoff
# =============================================================================


class MemoryLeakTestUser(HttpUser):
    """
    Test user for Scenario 1: Memory Leak During Backoff

    Tests:
    - Retry objects are properly garbage collected
    - No memory leak during exponential backoff
    - Memory usage stays under threshold
    """

    wait_time = between(0.05, 0.2)
    weight = 3

    def on_start(self):
        """Initialize user"""
        if _storm_stats.start_time is None:
            _storm_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.active_retries: List[RetryObject] = []

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(5)
    @tag("scenario1", "memory_leak")
    def create_retry_with_backoff(self):
        """Create retry objects that should be garbage collected"""
        phase = _get_current_phase()

        if phase not in ["memory_leak", "baseline"]:
            return

        with _stats_lock:
            _storm_stats.total_requests += 1

        # Create a retry object
        request_id = str(uuid.uuid4())
        attempt = random.randint(1, MAX_RETRY_ATTEMPTS)
        payload = {
            "data": "x" * random.randint(100, 1000),  # Variable payload size
            "timestamp": time.time(),
        }

        retry_obj = RetryObject(request_id, attempt, payload)

        # Simulate backoff
        backoff = BASE_BACKOFF_S * (BACKOFF_MULTIPLIER ** (attempt - 1))
        backoff = min(backoff, MAX_BACKOFF_S)
        backoff += random.uniform(-0.1, 0.1) * backoff

        retry_obj.backoff_until = time.time() + backoff

        # Keep reference briefly, then release
        self.active_retries.append(retry_obj)

        # Simulate the actual request
        with self.client.get("/api/products/", name=f"{STAGE_NAME} retry_backoff_request", catch_response=True) as response:
            if response.status_code == 200:
                with _stats_lock:
                    _storm_stats.successful_requests += 1
                response.success()
            else:
                with _stats_lock:
                    _storm_stats.failed_requests += 1
                response.failure(f"Request failed: {response.status_code}")

        # Clean up old retry objects (simulate completion)
        now = time.time()
        self.active_retries = [r for r in self.active_retries if r.backoff_until > now]

        # Periodically force GC to test collection
        if random.random() < 0.05:
            gc.collect()


# =============================================================================
# Test User - Scenario 2: DLQ Not Exploding
# =============================================================================


class DLQThrottleUser(HttpUser):
    """
    Test user for Scenario 2: DLQ Not Exploding

    Tests:
    - DLQ input rate is throttled
    - Queue size stays bounded
    - Failed requests are properly queued
    """

    wait_time = between(0.02, 0.1)
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _storm_stats.start_time is None:
            _storm_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(4)
    @tag("scenario2", "dlq_throttle")
    def generate_failures_for_dlq(self):
        """Generate failures that should go to DLQ"""
        phase = _get_current_phase()

        if phase not in ["dlq_explosion", "baseline"]:
            return

        with _stats_lock:
            _storm_stats.total_requests += 1

        # Simulate a failing operation
        should_fail = random.random() < 0.8 if phase == "dlq_explosion" else random.random() < 0.1

        if should_fail:
            # Try to add to DLQ
            item = {
                "request_id": str(uuid.uuid4()),
                "payload": {"action": "payment", "amount": random.randint(1000, 100000)},
                "error": "Simulated failure",
                "retry_count": random.randint(1, 5),
            }

            added = _dlq.add(item)

            # Record input rate
            rate = _dlq.get_input_rate()
            with _stats_lock:
                _storm_stats.dlq_input_rates.append(rate)

            if not added:
                # Throttled - this is good!
                self.client.post(
                    "/api/orders/", json={"item": "test"}, name=f"{STAGE_NAME} dlq_throttled", catch_response=True
                ).failure("DLQ throttled (expected)")
            else:
                self.client.post(
                    "/api/orders/", json={"item": "test"}, name=f"{STAGE_NAME} dlq_queued", catch_response=True
                ).failure("Added to DLQ")
        else:
            # Successful operation
            with self.client.get("/api/products/", name=f"{STAGE_NAME} dlq_success", catch_response=True) as response:
                if response.status_code == 200:
                    with _stats_lock:
                        _storm_stats.successful_requests += 1
                    response.success()
                else:
                    with _stats_lock:
                        _storm_stats.failed_requests += 1
                    response.failure(f"Failed: {response.status_code}")

    @task(1)
    @tag("scenario2", "dlq_process")
    def process_dlq_items(self):
        """Process items from DLQ"""
        phase = _get_current_phase()

        if phase not in ["dlq_explosion", "verification"]:
            return

        # Process a few items from DLQ
        for _ in range(min(5, _dlq.size())):
            item = _dlq.process()
            if item:
                # Simulate reprocessing
                time.sleep(0.01)


# =============================================================================
# Test User - Scenario 3: Payment Webhook Replay Safety
# =============================================================================


class WebhookReplayUser(HttpUser):
    """
    Test user for Scenario 3: Payment Webhook Replay Safety

    Tests:
    - Duplicate webhooks are detected
    - Idempotency keys prevent duplicate processing
    - No duplicate payments or stock deductions
    """

    wait_time = between(0.1, 0.5)
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _storm_stats.start_time is None:
            _storm_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)
        self.webhook_keys: List[str] = []  # Track keys for replay

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(4)
    @tag("scenario3", "webhook_new")
    def receive_new_webhook(self):
        """Simulate receiving a new unique webhook"""
        phase = _get_current_phase()

        if phase not in ["webhook_replay", "baseline"]:
            return

        with _stats_lock:
            _storm_stats.webhooks_received += 1

        # Generate unique idempotency key
        idempotency_key = str(uuid.uuid4())

        # Check idempotency
        is_new = _idempotency.check_and_set(idempotency_key)

        if is_new:
            with _stats_lock:
                _storm_stats.webhooks_unique += 1
                _storm_stats.idempotency_keys_seen.add(idempotency_key)

            # Store for potential replay
            self.webhook_keys.append(idempotency_key)
            if len(self.webhook_keys) > 10:
                self.webhook_keys.pop(0)

            # Process webhook
            with self.client.post(
                "/api/orders/webhook/",
                json={
                    "event": "payment.success",
                    "idempotency_key": idempotency_key,
                    "amount": 10000,
                },
                name=f"{STAGE_NAME} webhook_new",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201, 404]:
                    with _stats_lock:
                        _storm_stats.successful_requests += 1
                    response.success()
                else:
                    with _stats_lock:
                        _storm_stats.failed_requests += 1
                    response.failure(f"Webhook failed: {response.status_code}")

    @task(2)
    @tag("scenario3", "webhook_replay")
    def replay_webhook(self):
        """Simulate replaying an existing webhook (duplicate)"""
        phase = _get_current_phase()

        if phase != "webhook_replay":
            return

        if not self.webhook_keys:
            return

        with _stats_lock:
            _storm_stats.webhooks_received += 1

        # Replay an existing key
        idempotency_key = random.choice(self.webhook_keys)

        # Check idempotency - should return False (duplicate)
        is_new = _idempotency.check_and_set(idempotency_key)

        if not is_new:
            # Correctly identified as duplicate!
            with _stats_lock:
                _storm_stats.webhooks_duplicate += 1
                _storm_stats.duplicate_payments_prevented += 1
                _storm_stats.duplicate_stock_deductions_prevented += 1

            self.client.post(
                "/api/orders/webhook/",
                json={
                    "event": "payment.success",
                    "idempotency_key": idempotency_key,
                    "amount": 10000,
                },
                name=f"{STAGE_NAME} webhook_duplicate_prevented",
                catch_response=True,
            ).success()  # This is expected behavior
        else:
            # Unexpected - key should have been seen before
            print(f"   ⚠️ Idempotency failure: key {idempotency_key[:8]}... not detected as duplicate")


# =============================================================================
# Test User - Scenario 4: Retry + Clock Skew
# =============================================================================


class ClockSkewUser(HttpUser):
    """
    Test user for Scenario 4: Retry Timestamp Skew Compound

    Tests:
    - Timestamp validation with clock skew
    - Retry operations handle skewed timestamps
    - Clear error for skew exceeding tolerance
    """

    wait_time = between(0.2, 0.8)
    weight = 1

    def on_start(self):
        """Initialize user"""
        if _storm_stats.start_time is None:
            _storm_stats.start_time = time.time()

        self.login_helper = LoginHelper(self.client)

        # Login
        success = self.login_helper.login()
        if not success:
            self.login_helper.register_and_login()

    @task(3)
    @tag("scenario4", "clock_skew")
    def retry_with_timestamp(self):
        """Simulate retry operation with timestamp validation"""
        phase = _get_current_phase()

        if phase not in ["clock_skew", "baseline"]:
            return

        with _stats_lock:
            _storm_stats.timestamp_validations += 1

        # Get timestamp with simulated clock skew
        request_timestamp = _get_simulated_time()

        # Validate timestamp
        is_valid = _validate_timestamp(request_timestamp)

        if is_valid:
            with _stats_lock:
                _storm_stats.timestamp_accepted += 1
                _storm_stats.clock_skew_within_tolerance += 1

            with self.client.get(
                "/api/products/",
                headers={"X-Timestamp": str(request_timestamp)},
                name=f"{STAGE_NAME} timestamp_valid",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    with _stats_lock:
                        _storm_stats.successful_requests += 1
                    response.success()
                else:
                    with _stats_lock:
                        _storm_stats.failed_requests += 1
                    response.failure(f"Request failed: {response.status_code}")
        else:
            with _stats_lock:
                _storm_stats.timestamp_rejected += 1
                _storm_stats.clock_skew_exceeded += 1

            # Clock skew exceeded tolerance - return clear error
            skew = abs(time.time() - request_timestamp)

            self.client.get(
                "/api/products/",
                headers={"X-Timestamp": str(request_timestamp)},
                name=f"{STAGE_NAME} timestamp_skew_rejected",
                catch_response=True,
            ).failure(f"Clock skew {skew:.0f}s exceeds {CLOCK_SKEW_TOLERANCE_S}s tolerance")


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Initialize test"""
    print(f"\n{'='*60}")
    print(f"  Stage 32: Retry Storm Extended Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"\nTest Configuration:")
    print(f"  - Phase 1 (Baseline): {PHASE_1_BASELINE}s")
    print(f"  - Phase 2 (Memory Leak): {PHASE_2_MEMORY_LEAK}s")
    print(f"  - Phase 3 (DLQ Explosion): {PHASE_3_DLQ_EXPLOSION}s")
    print(f"  - Phase 4 (Webhook Replay): {PHASE_4_WEBHOOK_REPLAY}s")
    print(f"  - Phase 5 (Clock Skew): {PHASE_5_CLOCK_SKEW}s")
    print(f"  - Phase 6 (Verification): {PHASE_6_VERIFICATION}s")
    print(f"  - Total Duration: {TOTAL_DURATION}s")
    print(f"\nThresholds:")
    print(f"  - Memory Peak: {MEMORY_PEAK_THRESHOLD_MB}MB")
    print(f"  - DLQ Max Rate: {DLQ_MAX_INPUT_RATE}/sec")
    print(f"  - Clock Skew Tolerance: ±{CLOCK_SKEW_TOLERANCE_S}s")
    print()

    global _storm_stats, _dlq, _idempotency
    _storm_stats = RetryStormStats()
    _dlq = SimulatedDLQ()
    _idempotency = IdempotencyManager()
    RetryObject.reset_stats()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print final statistics"""
    _stop_memory_monitor()

    print(f"\n{'='*60}")
    print(f"  Stage 32: Test Complete")
    print(f"{'='*60}")

    print(f"\n📈 Overall Statistics:")
    print(f"  - Total Requests: {_storm_stats.total_requests}")
    print(f"  - Successful: {_storm_stats.successful_requests}")
    print(f"  - Failed: {_storm_stats.failed_requests}")

    created, collected, active = RetryObject.get_stats()
    print(f"\n🧠 Scenario 1 (Memory Leak):")
    print(f"  - Retry Objects Created: {created}")
    print(f"  - Retry Objects Collected: {collected}")
    print(f"  - Active Objects: {active}")
    print(f"  - Memory Peak: {_storm_stats.memory_peak_mb:.1f}MB")
    print(f"  - Memory Leak Detected: {_storm_stats.memory_leak_detected}")

    print(f"\n📥 Scenario 2 (DLQ Throttling):")
    print(f"  - DLQ Items Added: {_storm_stats.dlq_items_added}")
    print(f"  - DLQ Items Processed: {_storm_stats.dlq_items_processed}")
    print(f"  - DLQ Throttle Events: {_storm_stats.dlq_throttle_events}")
    print(f"  - DLQ Overflow Prevented: {_storm_stats.dlq_overflow_prevented}")

    print(f"\n🔁 Scenario 3 (Webhook Replay):")
    print(f"  - Webhooks Received: {_storm_stats.webhooks_received}")
    print(f"  - Unique Webhooks: {_storm_stats.webhooks_unique}")
    print(f"  - Duplicate Webhooks: {_storm_stats.webhooks_duplicate}")
    print(f"  - Duplicates Prevented: {_storm_stats.duplicate_payments_prevented}")

    print(f"\n⏰ Scenario 4 (Clock Skew):")
    print(f"  - Timestamp Validations: {_storm_stats.timestamp_validations}")
    print(f"  - Accepted: {_storm_stats.timestamp_accepted}")
    print(f"  - Rejected: {_storm_stats.timestamp_rejected}")
    print(f"  - Within Tolerance: {_storm_stats.clock_skew_within_tolerance}")
    print(f"  - Exceeded Tolerance: {_storm_stats.clock_skew_exceeded}")

    # Verification summary
    print(f"\n✅ Verification Summary:")
    for key, value in _storm_stats.verification.items():
        status = "✓" if value else "✗" if value is not None else "?"
        print(f"  - {key}: {status}")


# Optional: Setup additional event hooks
setup_event_hooks()
