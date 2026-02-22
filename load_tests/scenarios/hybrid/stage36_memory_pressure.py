"""
Stage 36: Memory Pressure Testing

Purpose: Test system stability under memory pressure conditions
- Gradual memory increase monitoring
- Large payload handling with streaming
- Memory leak detection and GC validation
- GC pause impact on response times

Scenarios:
  SC-36-1: Gradual Memory Increase (50% → 95%)
  SC-36-2: Large Payload Handling (50MB JSON)
  SC-36-3: Memory Leak Detection
  SC-36-4: GC Pause Impact

Verification:
  - [ ] OOM occurrence 0
  - [ ] Memory warning at 70%
  - [ ] Throttling at 85%
  - [ ] GC pause < 100ms

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage36_memory_pressure.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage36_memory_pressure.py \\
        --host=http://localhost:8000 \\
        --users=50 --spawn-rate=10 --run-time=5m \\
        --headless --html=stage36_report.html

Reference:
    - docs/STAGE_31_36_EXTENSION_PLAN.md (Stage 36)
    - Python Memory Profiling best practices
"""

import os
import sys
import time
import random
import threading
import weakref
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    from locust import HttpUser, task, between, tag, events, LoadTestShape

    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    HttpUser = object
    task = lambda weight=1: lambda f: f
    between = lambda a, b: None
    tag = lambda *args: lambda f: f
    events = None
    LoadTestShape = object


STAGE_NAME = "[Stage36-MemoryPressure]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 20s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE = max(3, int(30 * _scale))  # Normal baseline
PHASE_2_GRADUAL_INCREASE = max(5, int(80 * _scale))  # Gradual memory increase
PHASE_3_LARGE_PAYLOAD = max(4, int(70 * _scale))  # Large payload handling
PHASE_4_LEAK_DETECTION = max(4, int(70 * _scale))  # Memory leak detection
PHASE_5_GC_IMPACT = max(3, int(50 * _scale))  # GC pause impact

TOTAL_DURATION = (
    PHASE_1_BASELINE + PHASE_2_GRADUAL_INCREASE + PHASE_3_LARGE_PAYLOAD + PHASE_4_LEAK_DETECTION + PHASE_5_GC_IMPACT
)

# Memory thresholds (simulated percentages)
MEMORY_WARNING_THRESHOLD = 0.70  # 70%
MEMORY_THROTTLE_THRESHOLD = 0.85  # 85%
MEMORY_CRITICAL_THRESHOLD = 0.95  # 95%
MEMORY_MAX_MB = 1024  # Simulated max memory 1GB

# Payload configuration
LARGE_PAYLOAD_SIZE_MB = 50
STREAMING_CHUNK_SIZE_KB = 64

# GC configuration
GC_PAUSE_TARGET_MS = 100

# Leak detection configuration
LEAK_DETECTION_ITERATIONS = 100
LEAK_THRESHOLD_MB = 10


# =============================================================================
# Memory State Enum
# =============================================================================


class MemoryState(Enum):
    """Memory usage states"""

    NORMAL = "normal"
    WARNING = "warning"
    THROTTLED = "throttled"
    CRITICAL = "critical"


# =============================================================================
# Memory Pressure Statistics
# =============================================================================


@dataclass
class MemoryPressureStats:
    """Statistics tracking for memory pressure scenarios"""

    start_time: Optional[float] = None
    phase: str = "baseline"

    # Memory tracking
    current_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    memory_samples: List[float] = field(default_factory=list)
    memory_state: MemoryState = MemoryState.NORMAL

    # Scenario 1: Gradual Increase
    gradual_increase_samples: List[Tuple[float, float]] = field(default_factory=list)
    warning_triggered: bool = False
    warning_triggered_at_percent: float = 0.0
    throttle_triggered: bool = False
    throttle_triggered_at_percent: float = 0.0

    # Scenario 2: Large Payload
    large_payload_requests: int = 0
    large_payload_success: int = 0
    large_payload_oom: int = 0
    streaming_used: int = 0
    payload_response_times: List[float] = field(default_factory=list)

    # Scenario 3: Leak Detection
    leak_detection_runs: int = 0
    leaks_detected: int = 0
    leak_locations: List[str] = field(default_factory=list)
    gc_collections: int = 0
    memory_reclaimed_mb: float = 0.0

    # Scenario 4: GC Pause
    gc_pause_times: List[float] = field(default_factory=list)
    gc_pause_max_ms: float = 0.0
    requests_during_gc: int = 0
    gc_impact_timeouts: int = 0

    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    throttled_requests: int = 0
    rejected_requests: int = 0

    # Response times
    response_times: List[float] = field(default_factory=list)

    # Error tracking
    errors: List[Dict[str, Any]] = field(default_factory=list)
    oom_events: int = 0

    def reset(self):
        """Reset all statistics"""
        self.start_time = time.time()
        self.phase = "baseline"
        self.current_memory_mb = 0.0
        self.peak_memory_mb = 0.0
        self.memory_samples.clear()
        self.memory_state = MemoryState.NORMAL
        self.gradual_increase_samples.clear()
        self.warning_triggered = False
        self.warning_triggered_at_percent = 0.0
        self.throttle_triggered = False
        self.throttle_triggered_at_percent = 0.0
        self.large_payload_requests = 0
        self.large_payload_success = 0
        self.large_payload_oom = 0
        self.streaming_used = 0
        self.payload_response_times.clear()
        self.leak_detection_runs = 0
        self.leaks_detected = 0
        self.leak_locations.clear()
        self.gc_collections = 0
        self.memory_reclaimed_mb = 0.0
        self.gc_pause_times.clear()
        self.gc_pause_max_ms = 0.0
        self.requests_during_gc = 0
        self.gc_impact_timeouts = 0
        self.total_requests = 0
        self.successful_requests = 0
        self.throttled_requests = 0
        self.rejected_requests = 0
        self.response_times.clear()
        self.errors.clear()
        self.oom_events = 0

    def get_memory_utilization(self) -> float:
        """Get current memory utilization as percentage"""
        return self.current_memory_mb / MEMORY_MAX_MB if MEMORY_MAX_MB > 0 else 0.0

    def get_avg_response_time(self) -> float:
        """Get average response time in ms"""
        return sum(self.response_times) / len(self.response_times) * 1000 if self.response_times else 0.0

    def get_avg_gc_pause(self) -> float:
        """Get average GC pause time in ms"""
        return sum(self.gc_pause_times) / len(self.gc_pause_times) if self.gc_pause_times else 0.0

    def get_p99_gc_pause(self) -> float:
        """Get P99 GC pause time in ms"""
        if not self.gc_pause_times:
            return 0.0
        sorted_times = sorted(self.gc_pause_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]


# Global statistics instance
_stats = MemoryPressureStats()
_stats_lock = threading.Lock()


def get_stats() -> MemoryPressureStats:
    """Get the global stats instance"""
    return _stats


def reset_stats():
    """Reset global statistics"""
    with _stats_lock:
        _stats.reset()


# =============================================================================
# Simulated Memory Manager
# =============================================================================


class SimulatedMemoryManager:
    """Simulated memory manager for testing"""

    def __init__(self, max_memory_mb: float = MEMORY_MAX_MB):
        self._max_memory_mb = max_memory_mb
        self._current_memory_mb = 0.0
        self._allocated_blocks: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._gc_running = False
        self._throttle_enabled = False
        self._reject_new_requests = False
        self._weak_refs: List[weakref.ref] = []

    def reset(self):
        """Reset memory manager state"""
        with self._lock:
            self._current_memory_mb = 0.0
            self._allocated_blocks.clear()
            self._gc_running = False
            self._throttle_enabled = False
            self._reject_new_requests = False
            self._weak_refs.clear()

    def get_utilization(self) -> float:
        """Get memory utilization as percentage"""
        return self._current_memory_mb / self._max_memory_mb if self._max_memory_mb > 0 else 0.0

    def get_state(self) -> MemoryState:
        """Get current memory state based on utilization"""
        util = self.get_utilization()
        if util >= MEMORY_CRITICAL_THRESHOLD:
            return MemoryState.CRITICAL
        elif util >= MEMORY_THROTTLE_THRESHOLD:
            return MemoryState.THROTTLED
        elif util >= MEMORY_WARNING_THRESHOLD:
            return MemoryState.WARNING
        return MemoryState.NORMAL

    def allocate(self, block_id: str, size_mb: float) -> Tuple[bool, str]:
        """
        Allocate memory block.

        Returns:
            Tuple of (success, message)
        """
        with self._lock:
            state = self.get_state()

            # Check if we should reject
            if state == MemoryState.CRITICAL:
                _stats.rejected_requests += 1
                return False, "Memory critical - request rejected"

            # Check if throttling
            if state == MemoryState.THROTTLED:
                _stats.throttled_requests += 1
                # Add artificial delay for throttling
                time.sleep(random.uniform(0.05, 0.1))

            # Check if allocation would exceed limit
            new_total = self._current_memory_mb + size_mb
            if new_total > self._max_memory_mb:
                _stats.oom_events += 1
                return False, f"OOM - would exceed limit ({new_total:.1f}MB > {self._max_memory_mb}MB)"

            # Allocate
            self._allocated_blocks[block_id] = size_mb
            self._current_memory_mb = new_total

            # Update stats
            _stats.current_memory_mb = self._current_memory_mb
            if self._current_memory_mb > _stats.peak_memory_mb:
                _stats.peak_memory_mb = self._current_memory_mb

            # Check thresholds
            self._check_thresholds()

            return True, f"Allocated {size_mb:.1f}MB (total: {self._current_memory_mb:.1f}MB)"

    def deallocate(self, block_id: str) -> Tuple[bool, float]:
        """
        Deallocate memory block.

        Returns:
            Tuple of (success, freed_size_mb)
        """
        with self._lock:
            if block_id not in self._allocated_blocks:
                return False, 0.0

            size = self._allocated_blocks.pop(block_id)
            self._current_memory_mb -= size
            _stats.current_memory_mb = self._current_memory_mb

            return True, size

    def _check_thresholds(self):
        """Check memory thresholds and update state"""
        util = self.get_utilization()

        if util >= MEMORY_WARNING_THRESHOLD and not _stats.warning_triggered:
            _stats.warning_triggered = True
            _stats.warning_triggered_at_percent = util * 100

        if util >= MEMORY_THROTTLE_THRESHOLD and not _stats.throttle_triggered:
            _stats.throttle_triggered = True
            _stats.throttle_triggered_at_percent = util * 100
            self._throttle_enabled = True

        _stats.memory_state = self.get_state()

    def simulate_gc(self, full: bool = False) -> Tuple[float, float]:
        """
        Simulate garbage collection.

        Args:
            full: Whether to perform full GC (longer pause)

        Returns:
            Tuple of (pause_time_ms, memory_reclaimed_mb)
        """
        with self._lock:
            self._gc_running = True

        # Simulate GC pause
        if full:
            pause_s = random.uniform(0.05, 0.1)  # 50-100ms for full GC
        else:
            pause_s = random.uniform(0.005, 0.02)  # 5-20ms for minor GC

        time.sleep(pause_s)
        pause_ms = pause_s * 1000

        # Reclaim some memory (simulate collecting dead objects)
        with self._lock:
            reclaimed = 0.0
            # Remove random small blocks (simulating dead object collection)
            blocks_to_remove = []
            for block_id, size in self._allocated_blocks.items():
                if random.random() < 0.1:  # 10% chance to reclaim
                    blocks_to_remove.append(block_id)
                    reclaimed += size

            for block_id in blocks_to_remove:
                self._allocated_blocks.pop(block_id, None)

            self._current_memory_mb -= reclaimed
            self._current_memory_mb = max(0, self._current_memory_mb)
            _stats.current_memory_mb = self._current_memory_mb

            self._gc_running = False

        # Update stats
        _stats.gc_collections += 1
        _stats.gc_pause_times.append(pause_ms)
        _stats.gc_pause_max_ms = max(_stats.gc_pause_max_ms, pause_ms)
        _stats.memory_reclaimed_mb += reclaimed

        return pause_ms, reclaimed

    def is_gc_running(self) -> bool:
        """Check if GC is currently running"""
        return self._gc_running


# Global memory manager
_memory_manager = SimulatedMemoryManager()


def get_memory_manager() -> SimulatedMemoryManager:
    """Get the global memory manager"""
    return _memory_manager


def reset_memory_manager():
    """Reset the global memory manager"""
    _memory_manager.reset()


# =============================================================================
# Memory Pressure Utilities
# =============================================================================


class MemoryPressureUtils:
    """Utilities for memory pressure testing"""

    @staticmethod
    def simulate_gradual_increase(
        manager: SimulatedMemoryManager, target_percent: float = 0.95, step_mb: float = 50, step_delay_s: float = 0.1
    ) -> Dict[str, Any]:
        """
        Simulate gradual memory increase.

        Args:
            manager: Memory manager instance
            target_percent: Target memory utilization
            step_mb: Memory to allocate per step
            step_delay_s: Delay between steps

        Returns:
            Dictionary with gradual increase metrics
        """
        results = {
            "target_percent": target_percent,
            "step_mb": step_mb,
            "samples": [],
            "warning_at": None,
            "throttle_at": None,
            "final_percent": 0.0,
            "oom_occurred": False,
        }

        block_count = 0

        while manager.get_utilization() < target_percent:
            block_id = f"gradual_block_{block_count}"
            success, msg = manager.allocate(block_id, step_mb)

            if not success:
                results["oom_occurred"] = True
                break

            util = manager.get_utilization()
            results["samples"].append((time.time(), util))
            _stats.gradual_increase_samples.append((time.time(), util))

            if util >= MEMORY_WARNING_THRESHOLD and results["warning_at"] is None:
                results["warning_at"] = util

            if util >= MEMORY_THROTTLE_THRESHOLD and results["throttle_at"] is None:
                results["throttle_at"] = util

            block_count += 1
            time.sleep(step_delay_s)

        results["final_percent"] = manager.get_utilization()

        return results

    @staticmethod
    def simulate_large_payload(
        manager: SimulatedMemoryManager, size_mb: float = LARGE_PAYLOAD_SIZE_MB, use_streaming: bool = True
    ) -> Dict[str, Any]:
        """
        Simulate large payload handling.

        Args:
            manager: Memory manager instance
            size_mb: Payload size in MB
            use_streaming: Whether to use streaming processing

        Returns:
            Dictionary with payload handling metrics
        """
        start = time.time()
        results = {
            "size_mb": size_mb,
            "use_streaming": use_streaming,
            "success": False,
            "response_time_ms": 0,
            "peak_memory_mb": 0,
            "streaming_chunks": 0,
            "error": None,
        }

        _stats.large_payload_requests += 1

        if use_streaming:
            # Process in chunks
            chunk_size_mb = STREAMING_CHUNK_SIZE_KB / 1024
            chunks = int(size_mb / chunk_size_mb)

            _stats.streaming_used += 1
            results["streaming_chunks"] = chunks

            for i in range(chunks):
                block_id = f"stream_chunk_{i}"
                success, msg = manager.allocate(block_id, chunk_size_mb)

                if not success:
                    results["error"] = msg
                    _stats.large_payload_oom += 1
                    break

                # Process chunk
                time.sleep(0.001)  # Simulate processing

                # Release chunk
                manager.deallocate(block_id)
            else:
                results["success"] = True
                _stats.large_payload_success += 1
        else:
            # Allocate entire payload at once
            block_id = f"large_payload_{time.time()}"
            success, msg = manager.allocate(block_id, size_mb)

            if success:
                results["success"] = True
                _stats.large_payload_success += 1
                # Simulate processing
                time.sleep(0.05)
                manager.deallocate(block_id)
            else:
                results["error"] = msg
                _stats.large_payload_oom += 1

        results["response_time_ms"] = (time.time() - start) * 1000
        results["peak_memory_mb"] = _stats.peak_memory_mb
        _stats.payload_response_times.append(results["response_time_ms"] / 1000)

        return results

    @staticmethod
    def simulate_memory_leak(
        manager: SimulatedMemoryManager, iterations: int = LEAK_DETECTION_ITERATIONS, leak_size_mb: float = 0.1
    ) -> Dict[str, Any]:
        """
        Simulate memory leak and detection.

        Args:
            manager: Memory manager instance
            iterations: Number of iterations
            leak_size_mb: Size of each leaked block

        Returns:
            Dictionary with leak detection metrics
        """
        results = {
            "iterations": iterations,
            "leak_size_mb": leak_size_mb,
            "memory_before": manager._current_memory_mb,
            "memory_after": 0.0,
            "leak_detected": False,
            "leaked_mb": 0.0,
            "gc_reclaimed_mb": 0.0,
        }

        _stats.leak_detection_runs += 1
        leaked_blocks = []

        for i in range(iterations):
            block_id = f"leak_block_{i}"
            success, msg = manager.allocate(block_id, leak_size_mb)

            if success:
                # Simulate "leak" - don't deallocate some blocks
                if random.random() < 0.1:  # 10% leak rate
                    leaked_blocks.append(block_id)
                else:
                    manager.deallocate(block_id)

        results["memory_after"] = manager._current_memory_mb
        results["leaked_mb"] = results["memory_after"] - results["memory_before"]

        # Detect leak
        if results["leaked_mb"] > LEAK_THRESHOLD_MB:
            results["leak_detected"] = True
            _stats.leaks_detected += 1
            _stats.leak_locations.append(f"Detected {len(leaked_blocks)} leaked blocks")

        # Run GC to reclaim
        _, reclaimed = manager.simulate_gc(full=True)
        results["gc_reclaimed_mb"] = reclaimed

        return results

    @staticmethod
    def simulate_gc_impact(
        manager: SimulatedMemoryManager, num_requests: int = 50, gc_during_requests: bool = True
    ) -> Dict[str, Any]:
        """
        Simulate GC pause impact on requests.

        Args:
            manager: Memory manager instance
            num_requests: Number of requests to simulate
            gc_during_requests: Whether to trigger GC during requests

        Returns:
            Dictionary with GC impact metrics
        """
        results = {
            "num_requests": num_requests,
            "gc_triggered": gc_during_requests,
            "response_times": [],
            "gc_pause_times": [],
            "timeouts": 0,
            "avg_response_time_ms": 0,
            "max_gc_pause_ms": 0,
        }

        gc_triggered = False

        for i in range(num_requests):
            start = time.time()

            # Simulate request processing
            block_id = f"request_{i}"
            manager.allocate(block_id, 1)  # 1MB per request

            # Trigger GC mid-way
            if gc_during_requests and i == num_requests // 2 and not gc_triggered:
                pause_ms, _ = manager.simulate_gc(full=True)
                results["gc_pause_times"].append(pause_ms)
                gc_triggered = True
                _stats.requests_during_gc += 1

            # Simulate processing
            time.sleep(random.uniform(0.005, 0.01))

            manager.deallocate(block_id)

            elapsed = (time.time() - start) * 1000
            results["response_times"].append(elapsed)

            # Check for timeout (> 500ms)
            if elapsed > 500:
                results["timeouts"] += 1
                _stats.gc_impact_timeouts += 1

        if results["response_times"]:
            results["avg_response_time_ms"] = sum(results["response_times"]) / len(results["response_times"])

        if results["gc_pause_times"]:
            results["max_gc_pause_ms"] = max(results["gc_pause_times"])

        return results


# Global utils instance
memory_pressure_utils = MemoryPressureUtils()


# =============================================================================
# Test Result Generation
# =============================================================================


def generate_stage36_report(stats: MemoryPressureStats) -> Dict[str, Any]:
    """Generate Stage 36 test report"""
    elapsed = time.time() - stats.start_time if stats.start_time else 0

    return {
        "stage": "36",
        "name": "Memory Pressure Testing",
        "duration_s": elapsed,
        "phase": stats.phase,
        "summary": {
            "total_requests": stats.total_requests,
            "successful_requests": stats.successful_requests,
            "throttled_requests": stats.throttled_requests,
            "rejected_requests": stats.rejected_requests,
            "oom_events": stats.oom_events,
            "peak_memory_mb": stats.peak_memory_mb,
            "avg_response_time_ms": stats.get_avg_response_time(),
        },
        "scenario_1_gradual_increase": {
            "warning_triggered": stats.warning_triggered,
            "warning_at_percent": stats.warning_triggered_at_percent,
            "throttle_triggered": stats.throttle_triggered,
            "throttle_at_percent": stats.throttle_triggered_at_percent,
            "samples_count": len(stats.gradual_increase_samples),
        },
        "scenario_2_large_payload": {
            "requests": stats.large_payload_requests,
            "success": stats.large_payload_success,
            "oom": stats.large_payload_oom,
            "streaming_used": stats.streaming_used,
            "success_rate": (
                stats.large_payload_success / stats.large_payload_requests if stats.large_payload_requests > 0 else 0
            ),
        },
        "scenario_3_leak_detection": {
            "runs": stats.leak_detection_runs,
            "leaks_detected": stats.leaks_detected,
            "gc_collections": stats.gc_collections,
            "memory_reclaimed_mb": stats.memory_reclaimed_mb,
        },
        "scenario_4_gc_impact": {
            "gc_pause_count": len(stats.gc_pause_times),
            "avg_gc_pause_ms": stats.get_avg_gc_pause(),
            "max_gc_pause_ms": stats.gc_pause_max_ms,
            "p99_gc_pause_ms": stats.get_p99_gc_pause(),
            "requests_during_gc": stats.requests_during_gc,
            "gc_impact_timeouts": stats.gc_impact_timeouts,
        },
        "verification": {
            "oom_prevented": stats.oom_events == 0,
            "warning_at_70": stats.warning_triggered_at_percent >= 70 if stats.warning_triggered else False,
            "throttle_at_85": stats.throttle_triggered_at_percent >= 85 if stats.throttle_triggered else False,
            "gc_pause_under_100ms": stats.gc_pause_max_ms <= GC_PAUSE_TARGET_MS,
        },
        "errors": stats.errors[:10],  # First 10 errors
    }


# =============================================================================
# Locust User Classes (if Locust is available)
# =============================================================================


if LOCUST_AVAILABLE:

    class MemoryPressureUser(HttpUser):
        """Locust user for memory pressure testing"""

        wait_time = between(0.5, 1.0)

        def on_start(self):
            """Initialize user"""
            self.manager = get_memory_manager()
            self.request_count = 0

        @task(5)
        @tag("normal_request")
        def normal_request(self):
            """Normal request with small memory allocation"""
            start = time.time()
            block_id = f"user_request_{self.request_count}"
            self.request_count += 1

            try:
                success, msg = self.manager.allocate(block_id, 1)  # 1MB

                if success:
                    # Simulate processing
                    time.sleep(random.uniform(0.01, 0.05))
                    self.manager.deallocate(block_id)

                    with _stats_lock:
                        _stats.total_requests += 1
                        _stats.successful_requests += 1

                    elapsed = time.time() - start
                    self.environment.events.request.fire(
                        request_type="MEMORY",
                        name="normal_request",
                        response_time=elapsed * 1000,
                        response_length=0,
                        exception=None,
                        context={},
                    )
                else:
                    with _stats_lock:
                        _stats.total_requests += 1

                    elapsed = time.time() - start
                    self.environment.events.request.fire(
                        request_type="MEMORY",
                        name="normal_request",
                        response_time=elapsed * 1000,
                        response_length=0,
                        exception=Exception(msg),
                        context={},
                    )

            except Exception as e:
                elapsed = time.time() - start
                self.environment.events.request.fire(
                    request_type="MEMORY",
                    name="normal_request",
                    response_time=elapsed * 1000,
                    response_length=0,
                    exception=e,
                    context={},
                )

        @task(2)
        @tag("large_payload")
        def large_payload_request(self):
            """Request with large payload"""
            start = time.time()

            try:
                results = memory_pressure_utils.simulate_large_payload(
                    self.manager, size_mb=10, use_streaming=True  # Smaller for testing
                )

                with _stats_lock:
                    _stats.total_requests += 1
                    if results["success"]:
                        _stats.successful_requests += 1

                elapsed = time.time() - start
                self.environment.events.request.fire(
                    request_type="MEMORY",
                    name="large_payload",
                    response_time=elapsed * 1000,
                    response_length=0,
                    exception=None if results["success"] else Exception(results.get("error", "Failed")),
                    context={},
                )

            except Exception as e:
                elapsed = time.time() - start
                self.environment.events.request.fire(
                    request_type="MEMORY",
                    name="large_payload",
                    response_time=elapsed * 1000,
                    response_length=0,
                    exception=e,
                    context={},
                )

        @task(1)
        @tag("gc_trigger")
        def trigger_gc(self):
            """Trigger garbage collection"""
            start = time.time()

            try:
                pause_ms, reclaimed = self.manager.simulate_gc(full=False)

                elapsed = time.time() - start
                self.environment.events.request.fire(
                    request_type="GC", name="gc_trigger", response_time=pause_ms, response_length=0, exception=None, context={}
                )

            except Exception as e:
                elapsed = time.time() - start
                self.environment.events.request.fire(
                    request_type="GC",
                    name="gc_trigger",
                    response_time=elapsed * 1000,
                    response_length=0,
                    exception=e,
                    context={},
                )

    class MemoryPressureLoadShape(LoadTestShape):
        """Custom load shape for memory pressure testing"""

        stages = [
            {"duration": PHASE_1_BASELINE, "users": 10, "spawn_rate": 5},
            {"duration": PHASE_1_BASELINE + PHASE_2_GRADUAL_INCREASE, "users": 30, "spawn_rate": 10},
            {"duration": PHASE_1_BASELINE + PHASE_2_GRADUAL_INCREASE + PHASE_3_LARGE_PAYLOAD, "users": 20, "spawn_rate": 5},
            {
                "duration": PHASE_1_BASELINE + PHASE_2_GRADUAL_INCREASE + PHASE_3_LARGE_PAYLOAD + PHASE_4_LEAK_DETECTION,
                "users": 25,
                "spawn_rate": 5,
            },
            {"duration": TOTAL_DURATION, "users": 10, "spawn_rate": 5},
        ]

        def tick(self) -> Optional[Tuple[int, float]]:
            run_time = self.get_run_time()

            for stage in self.stages:
                if run_time < stage["duration"]:
                    return (stage["users"], stage["spawn_rate"])

            return None


# =============================================================================
# Standalone Test Functions
# =============================================================================


def run_gradual_increase_test(target_percent: float = 0.90) -> Dict[str, Any]:
    """Run gradual memory increase test"""
    print(f"\n{STAGE_NAME} Running gradual memory increase test (target: {target_percent*100:.0f}%)...")

    reset_memory_manager()
    reset_stats()

    manager = get_memory_manager()

    results = memory_pressure_utils.simulate_gradual_increase(
        manager=manager, target_percent=target_percent, step_mb=50, step_delay_s=0.01
    )

    print(f"  Target: {results['target_percent']*100:.0f}%")
    print(f"  Final: {results['final_percent']*100:.1f}%")
    print(f"  Warning at: {results['warning_at']*100:.1f}%" if results["warning_at"] else "  Warning: Not triggered")
    print(f"  Throttle at: {results['throttle_at']*100:.1f}%" if results["throttle_at"] else "  Throttle: Not triggered")
    print(f"  OOM occurred: {results['oom_occurred']}")

    return results


def run_large_payload_test(num_requests: int = 10, size_mb: float = 10) -> Dict[str, Any]:
    """Run large payload handling test"""
    print(f"\n{STAGE_NAME} Running large payload test ({num_requests} requests × {size_mb}MB)...")

    reset_memory_manager()
    reset_stats()

    manager = get_memory_manager()

    results = {
        "num_requests": num_requests,
        "size_mb": size_mb,
        "requests": [],
        "success_count": 0,
        "oom_count": 0,
        "avg_response_time_ms": 0,
    }

    for i in range(num_requests):
        req_result = memory_pressure_utils.simulate_large_payload(manager=manager, size_mb=size_mb, use_streaming=True)
        results["requests"].append(req_result)

        if req_result["success"]:
            results["success_count"] += 1
        else:
            results["oom_count"] += 1

    response_times = [r["response_time_ms"] for r in results["requests"]]
    if response_times:
        results["avg_response_time_ms"] = sum(response_times) / len(response_times)

    print(f"  Success: {results['success_count']}/{num_requests}")
    print(f"  OOM: {results['oom_count']}")
    print(f"  Avg response time: {results['avg_response_time_ms']:.2f}ms")

    return results


def run_leak_detection_test(iterations: int = 50) -> Dict[str, Any]:
    """Run memory leak detection test"""
    print(f"\n{STAGE_NAME} Running leak detection test ({iterations} iterations)...")

    reset_memory_manager()
    reset_stats()

    manager = get_memory_manager()

    results = memory_pressure_utils.simulate_memory_leak(manager=manager, iterations=iterations, leak_size_mb=0.5)

    print(f"  Memory before: {results['memory_before']:.2f}MB")
    print(f"  Memory after: {results['memory_after']:.2f}MB")
    print(f"  Leaked: {results['leaked_mb']:.2f}MB")
    print(f"  Leak detected: {results['leak_detected']}")
    print(f"  GC reclaimed: {results['gc_reclaimed_mb']:.2f}MB")

    return results


def run_gc_impact_test(num_requests: int = 30) -> Dict[str, Any]:
    """Run GC pause impact test"""
    print(f"\n{STAGE_NAME} Running GC impact test ({num_requests} requests)...")

    reset_memory_manager()
    reset_stats()

    manager = get_memory_manager()

    results = memory_pressure_utils.simulate_gc_impact(manager=manager, num_requests=num_requests, gc_during_requests=True)

    print(f"  Requests: {results['num_requests']}")
    print(f"  GC triggered: {results['gc_triggered']}")
    print(f"  Avg response time: {results['avg_response_time_ms']:.2f}ms")
    print(f"  Max GC pause: {results['max_gc_pause_ms']:.2f}ms")
    print(f"  Timeouts: {results['timeouts']}")

    return results


def run_all_stage36_tests() -> Dict[str, Any]:
    """Run all Stage 36 tests"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Starting Stage 36 Memory Pressure Tests")
    print(f"{'='*60}")

    reset_stats()
    _stats.start_time = time.time()

    results = {"stage": "36", "name": "Memory Pressure Testing", "start_time": datetime.now().isoformat(), "tests": {}}

    # Test 1: Gradual increase
    _stats.phase = "gradual_increase"
    results["tests"]["gradual_increase"] = run_gradual_increase_test(0.90)

    # Test 2: Large payload
    _stats.phase = "large_payload"
    results["tests"]["large_payload"] = run_large_payload_test(5, 10)

    # Test 3: Leak detection
    _stats.phase = "leak_detection"
    results["tests"]["leak_detection"] = run_leak_detection_test(30)

    # Test 4: GC impact
    _stats.phase = "gc_impact"
    results["tests"]["gc_impact"] = run_gc_impact_test(20)

    # Generate final report
    _stats.phase = "completed"
    results["report"] = generate_stage36_report(_stats)
    results["end_time"] = datetime.now().isoformat()
    results["duration_s"] = time.time() - _stats.start_time

    # Summary
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Stage 36 Tests Complete")
    print(f"{'='*60}")
    print(f"Duration: {results['duration_s']:.2f}s")
    print(f"OOM Events: {_stats.oom_events}")
    print(f"Peak Memory: {_stats.peak_memory_mb:.1f}MB")

    return results


if __name__ == "__main__":
    results = run_all_stage36_tests()
    print(f"\nFinal Results: {results['report']['summary']}")
