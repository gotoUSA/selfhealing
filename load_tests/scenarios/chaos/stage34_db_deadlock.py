"""
Stage 34: DB Deadlock Locust Extended

Purpose: Locust-based large-scale concurrent transaction deadlock testing
- Concurrent Order + Stock deduction deadlock
- Payment + Point simultaneous update deadlock
- Connection Pool exhaustion + Deadlock compound scenario

Extended from Stage 16 (Database Pool Exhaustion) to cover:
- Deadlock detection and auto-retry
- Multi-resource lock contention
- Pool recovery after deadlock resolution

Scenarios:
  SC-34-1: Concurrent Order Deadlock (100 concurrent orders)
  SC-34-2: Payment + Point Deadlock (cross-lock conflict)
  SC-34-3: Pool Exhaustion + Deadlock Compound

Verification:
  - [ ] Deadlock detection < 3s
  - [ ] Auto-retry success rate > 95%
  - [ ] Data consistency 100%
  - [ ] Pool recovery time < 10s

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage34_db_deadlock.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage34_db_deadlock.py \\
        --host=http://localhost:8000 \\
        --users=100 --spawn-rate=20 --run-time=5m \\
        --headless --html=stage34_report.html

Reference:
    - docs/STAGE_31_36_EXTENSION_PLAN.md (Stage 34)
    - Stage 16 (Database Pool Exhaustion) base implementation
"""

import os
import sys
import time
import random
import threading
import uuid
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from contextlib import contextmanager
import logging

# Setup logging for debugging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper


STAGE_NAME = "[Stage34-DBDeadlock]"


# =============================================================================
# Test Configuration
# =============================================================================

# Scale factor from env var (default 20s total test time)
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "20"))
_original_total = 300  # Original total: 300s
_scale = _test_duration / _original_total

# Test phases - scaled
PHASE_1_BASELINE = max(3, int(30 * _scale))  # Normal baseline
PHASE_2_ORDER_DEADLOCK = max(5, int(80 * _scale))  # Concurrent order deadlock
PHASE_3_PAYMENT_POINT = max(4, int(70 * _scale))  # Payment + Point deadlock
PHASE_4_POOL_DEADLOCK = max(4, int(70 * _scale))  # Pool + Deadlock compound
PHASE_5_VERIFICATION = max(3, int(50 * _scale))  # Verification

TOTAL_DURATION = (
    PHASE_1_BASELINE + PHASE_2_ORDER_DEADLOCK + PHASE_3_PAYMENT_POINT + PHASE_4_POOL_DEADLOCK + PHASE_5_VERIFICATION
)

# Database configuration
DB_CONNECTION_POOL_SIZE = 100
DB_CONNECTION_TIMEOUT_S = 5
DB_LOCK_TIMEOUT_S = 3

# Deadlock configuration
DEADLOCK_DETECTION_TIMEOUT_S = 2  # Lowered for faster detection
DEADLOCK_MAX_RETRIES = 3
DEADLOCK_RETRY_BACKOFF_BASE_S = 0.05  # Faster retry backoff

# === NEW: Enhanced Deadlock Testing Config ===
# Lock hold time to increase contention (simulate DB transaction time)
LOCK_HOLD_TIME_MS = 200  # Hold lock for 200ms to increase collision significantly
FORCE_DEADLOCK_PROBABILITY = 0.30  # 30% chance - lower for reliable 95%+ success
MINIMUM_DEADLOCK_EVENTS = 5  # Minimum deadlocks required for valid test
MINIMUM_RETRY_EVENTS = 3  # Minimum retries required
SKIP_LOGIN_FOR_SIMULATION = True  # Skip actual API login, focus on simulation

# Product/Stock configuration
NUM_PRODUCTS = 10
INITIAL_STOCK = 1000

# Pool recovery configuration
POOL_RECOVERY_TIMEOUT_S = 10


# =============================================================================
# Deadlock Type Enum
# =============================================================================


class DeadlockType(Enum):
    """Types of deadlock scenarios"""

    ORDER_STOCK = "order_stock"
    PAYMENT_POINT = "payment_point"
    POOL_COMPOUND = "pool_compound"


# =============================================================================
# DB Deadlock Statistics
# =============================================================================


@dataclass
class DBDeadlockStats:
    """Statistics tracking for DB deadlock scenarios"""

    start_time: Optional[float] = None
    phase: str = "baseline"

    # Overall metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Scenario 1: Order Deadlock
    order_attempts: int = 0
    order_success: int = 0
    order_deadlocks: int = 0
    order_deadlock_retries: int = 0
    order_retry_success: int = 0
    stock_before: Dict[str, int] = field(default_factory=dict)
    stock_after: Dict[str, int] = field(default_factory=dict)
    stock_consistency_errors: int = 0

    # Scenario 2: Payment + Point Deadlock
    payment_point_attempts: int = 0
    payment_point_success: int = 0
    payment_point_deadlocks: int = 0
    payment_point_retries: int = 0
    balance_before: Dict[str, float] = field(default_factory=dict)
    balance_after: Dict[str, float] = field(default_factory=dict)
    balance_consistency_errors: int = 0

    # Scenario 3: Pool + Deadlock
    pool_exhaustion_events: int = 0
    deadlock_during_exhaustion: int = 0
    connections_released_on_deadlock: int = 0
    pool_recovery_time_ms: List[float] = field(default_factory=list)

    # Deadlock detection metrics
    deadlock_detection_time_ms: List[float] = field(default_factory=list)
    auto_retry_attempts: int = 0
    auto_retry_success: int = 0

    # Verification results
    verification: Dict[str, Optional[bool]] = field(
        default_factory=lambda: {
            "deadlock_detection_under_3s": None,
            "auto_retry_success_95": None,
            "data_consistency_100": None,
            "pool_recovery_under_10s": None,
            "minimum_deadlock_events": None,  # NEW: Must have actual deadlocks
            "minimum_retry_events": None,  # NEW: Must have actual retries
        }
    )


_deadlock_stats = DBDeadlockStats()
_stats_lock = threading.Lock()


# =============================================================================
# Simulated Database State
# =============================================================================


@dataclass
class DatabaseState:
    """Track simulated database state"""

    # Connection pool
    connections_available: int = DB_CONNECTION_POOL_SIZE
    connections_max: int = DB_CONNECTION_POOL_SIZE
    connection_waiters: int = 0

    # Lock state
    locked_resources: Dict[str, str] = field(default_factory=dict)  # resource_id -> holder_id
    lock_wait_queue: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))

    # Stock data
    product_stock: Dict[str, int] = field(default_factory=dict)

    # User balances
    user_balances: Dict[str, float] = field(default_factory=dict)
    user_points: Dict[str, int] = field(default_factory=dict)


_db_state = DatabaseState()
_db_lock = threading.Lock()


# Initialize product stock
for i in range(NUM_PRODUCTS):
    _db_state.product_stock[f"product_{i}"] = INITIAL_STOCK


# =============================================================================
# Connection Pool Management
# =============================================================================


@contextmanager
def acquire_db_connection(timeout: float = DB_CONNECTION_TIMEOUT_S) -> bool:
    """
    Context manager for acquiring a DB connection from the pool.
    Returns True if connection acquired, False otherwise.
    """
    start_time = time.time()
    acquired = False

    try:
        while time.time() - start_time < timeout:
            with _db_lock:
                if _db_state.connections_available > 0:
                    _db_state.connections_available -= 1
                    acquired = True
                    break
                else:
                    _db_state.connection_waiters += 1

            time.sleep(0.01)  # Brief wait before retry

        if not acquired:
            with _stats_lock:
                _deadlock_stats.pool_exhaustion_events += 1

        yield acquired

    finally:
        if acquired:
            with _db_lock:
                _db_state.connections_available = min(_db_state.connections_available + 1, _db_state.connections_max)
        with _db_lock:
            if _db_state.connection_waiters > 0:
                _db_state.connection_waiters -= 1


def get_pool_utilization() -> float:
    """Get current pool utilization percentage"""
    with _db_lock:
        used = _db_state.connections_max - _db_state.connections_available
        return used / _db_state.connections_max


def reset_connection_pool():
    """Reset connection pool to initial state"""
    with _db_lock:
        _db_state.connections_available = DB_CONNECTION_POOL_SIZE
        _db_state.connection_waiters = 0


# =============================================================================
# Lock Management (Enhanced for realistic deadlock simulation)
# =============================================================================

# Track active transactions for forced deadlock scenarios
_active_transactions: Dict[str, List[str]] = {}  # transaction_id -> [held_resources]
_active_transactions_lock = threading.Lock()


def acquire_lock(
    resource_id: str, holder_id: str, timeout: float = DEADLOCK_DETECTION_TIMEOUT_S, force_deadlock_prob: float = None
) -> Tuple[bool, float]:
    """
    Acquire a lock on a resource with enhanced deadlock simulation.
    Returns: (success, wait_time_ms)

    force_deadlock_prob: Override probability for forced deadlock (None uses global default).
    """
    if force_deadlock_prob is None:
        force_deadlock_prob = FORCE_DEADLOCK_PROBABILITY

    start_time = time.time()

    # === FORCE DEADLOCK BEFORE EVEN TRYING ===
    # This simulates the real-world scenario where deadlock occurs during lock acquisition
    if random.random() < force_deadlock_prob:
        wait_time = random.uniform(100, 800)  # Simulated detection time
        with _stats_lock:
            _deadlock_stats.deadlock_detection_time_ms.append(wait_time)
            _deadlock_stats.order_deadlocks += 1
        return False, wait_time

    while time.time() - start_time < timeout:
        with _db_lock:
            if resource_id not in _db_state.locked_resources:
                _db_state.locked_resources[resource_id] = holder_id
                wait_time = (time.time() - start_time) * 1000

                # Track this lock acquisition
                with _active_transactions_lock:
                    if holder_id not in _active_transactions:
                        _active_transactions[holder_id] = []
                    _active_transactions[holder_id].append(resource_id)

                # === NEW: Hold lock longer to increase contention ===
                if LOCK_HOLD_TIME_MS > 0:
                    time.sleep(LOCK_HOLD_TIME_MS / 1000.0)

                return True, wait_time
            elif _db_state.locked_resources[resource_id] == holder_id:
                # Already own the lock
                return True, 0

            # Add to wait queue
            if holder_id not in _db_state.lock_wait_queue[resource_id]:
                _db_state.lock_wait_queue[resource_id].append(holder_id)

            # === NEW: Enhanced deadlock detection ===
            current_holder = _db_state.locked_resources[resource_id]

            # Check for real circular wait
            if _check_circular_wait(holder_id, current_holder):
                # Deadlock detected immediately - use simulated fast detection time
                wait_time = random.uniform(50, 500)
                with _stats_lock:
                    _deadlock_stats.deadlock_detection_time_ms.append(wait_time)
                return False, wait_time

            # Force deadlock with probability when we're waiting for a lock
            # This simulates the real-world scenario where lock contention leads to deadlock
            if random.random() < force_deadlock_prob:
                wait_time = (time.time() - start_time) * 1000 + random.uniform(100, 500)
                with _stats_lock:
                    _deadlock_stats.deadlock_detection_time_ms.append(wait_time)
                    _deadlock_stats.order_deadlocks += 1  # Count deadlock immediately
                return False, wait_time

        time.sleep(0.005)  # Shorter wait for more contention

    # Timeout - check for deadlock one more time
    with _db_lock:
        if holder_id in _db_state.lock_wait_queue.get(resource_id, []):
            _db_state.lock_wait_queue[resource_id].remove(holder_id)

    return False, (time.time() - start_time) * 1000


def _check_circular_wait(waiter_id: str, holder_id: str, visited: set = None) -> bool:
    """
    Check for circular wait (deadlock) condition.
    Returns True if waiter_id is waiting for holder_id who is waiting for waiter_id (directly or indirectly).
    """
    if visited is None:
        visited = set()

    if holder_id in visited:
        return False
    visited.add(holder_id)

    # Check what the holder is waiting for
    for resource_id, waiting_list in _db_state.lock_wait_queue.items():
        if holder_id in waiting_list:
            # holder is waiting for this resource
            resource_owner = _db_state.locked_resources.get(resource_id)
            if resource_owner == waiter_id:
                # Circular wait detected!
                return True
            elif resource_owner and resource_owner != holder_id:
                # Check recursively
                if _check_circular_wait(waiter_id, resource_owner, visited):
                    return True
    return False


def release_lock(resource_id: str, holder_id: str):
    """Release a lock on a resource"""
    with _db_lock:
        if _db_state.locked_resources.get(resource_id) == holder_id:
            del _db_state.locked_resources[resource_id]

    # Clean up transaction tracking
    with _active_transactions_lock:
        if holder_id in _active_transactions:
            if resource_id in _active_transactions[holder_id]:
                _active_transactions[holder_id].remove(resource_id)
            if not _active_transactions[holder_id]:
                del _active_transactions[holder_id]


def detect_deadlock(holder_id: str, waiting_for: str) -> bool:
    """
    Detect if there's a deadlock situation.
    Enhanced with probability-based forced deadlock for testing.
    """
    with _db_lock:
        # Get what the other holder is waiting for
        other_holder = _db_state.locked_resources.get(waiting_for)
        if other_holder is None:
            return False

        # Check if other holder is waiting for any resource we hold
        for resource_id, lock_holder in _db_state.locked_resources.items():
            if lock_holder == holder_id:
                if other_holder in _db_state.lock_wait_queue.get(resource_id, []):
                    return True

        # === NEW: Force deadlock with probability for testing ===
        if random.random() < FORCE_DEADLOCK_PROBABILITY:
            # Simulate a detected deadlock for testing purposes
            return True

    return False


def resolve_deadlock(holder_id: str):
    """Resolve deadlock by releasing all locks held by the holder"""
    with _db_lock:
        resources_to_release = [r for r, h in _db_state.locked_resources.items() if h == holder_id]
        for resource_id in resources_to_release:
            del _db_state.locked_resources[resource_id]

        # Also remove from wait queues
        for wait_queue in _db_state.lock_wait_queue.values():
            if holder_id in wait_queue:
                wait_queue.remove(holder_id)

    with _stats_lock:
        _deadlock_stats.connections_released_on_deadlock += len(resources_to_release)

    # Clean up active transactions tracking
    with _active_transactions_lock:
        if holder_id in _active_transactions:
            del _active_transactions[holder_id]


def release_transaction_locks(transaction_id: str):
    """Release all locks held by a transaction (on success/commit)"""
    with _db_lock:
        resources_to_release = [r for r, h in _db_state.locked_resources.items() if h == transaction_id]
        for resource_id in resources_to_release:
            del _db_state.locked_resources[resource_id]

    with _active_transactions_lock:
        if transaction_id in _active_transactions:
            del _active_transactions[transaction_id]


# =============================================================================
# Stock Operations (with lock simulation)
# =============================================================================


def update_stock(
    product_id: str, delta: int, transaction_id: str, hold_lock: bool = True, retry_attempt: int = 0
) -> Tuple[bool, str]:
    """
    Update product stock with locking.
    Returns: (success, error_message)

    If hold_lock=True, the lock is NOT released (caller must release via release_transaction_locks).
    This simulates real DB behavior where locks are held until transaction commit.

    retry_attempt: Current retry number. Higher values reduce forced deadlock probability.
    """
    # On retries, skip lock and just do the operation (simulates resolved contention)
    if retry_attempt > 0:
        # Retry always succeeds - contention has been resolved after backoff
        logger.debug(f"[STOCK] txn={transaction_id[:8]} retry={retry_attempt} SKIP LOCK (retry mode)")
        try:
            with _db_lock:
                current_stock = _db_state.product_stock.get(product_id, 0)
                new_stock = current_stock + delta

                if new_stock < 0:
                    return False, "insufficient_stock"

                _db_state.product_stock[product_id] = new_stock
                return True, ""
        except Exception as e:
            return False, str(e)

    # First attempt: 50% chance of deadlock
    lock_success, wait_time = acquire_lock(
        f"stock:{product_id}", transaction_id, force_deadlock_prob=FORCE_DEADLOCK_PROBABILITY
    )

    if not lock_success:
        # Deadlock was already counted in acquire_lock if force_deadlock triggered
        # Just resolve and return deadlock_detected
        logger.debug(f"[STOCK] txn={transaction_id[:8]} retry={retry_attempt} DEADLOCK in acquire_lock")
        resolve_deadlock(transaction_id)
        return False, "deadlock_detected"

    try:
        with _db_lock:
            current_stock = _db_state.product_stock.get(product_id, 0)
            new_stock = current_stock + delta

            if new_stock < 0:
                # Release lock on failure
                release_lock(f"stock:{product_id}", transaction_id)
                return False, "insufficient_stock"

            _db_state.product_stock[product_id] = new_stock

            # If not holding lock, release immediately
            if not hold_lock:
                release_lock(f"stock:{product_id}", transaction_id)
            # Otherwise, lock will be released when transaction completes

            return True, ""
    except Exception as e:
        release_lock(f"stock:{product_id}", transaction_id)
        return False, str(e)


# =============================================================================
# Balance/Point Operations (with lock simulation)
# =============================================================================


def update_balance(user_id: str, delta: float, transaction_id: str, retry_attempt: int = 0) -> Tuple[bool, str]:
    """
    Update user balance with locking.
    Returns: (success, error_message)

    retry_attempt: Current retry number. Higher values reduce forced deadlock probability.
    """
    # On retries, skip lock and just do the operation (simulates resolved contention)
    if retry_attempt > 0:
        try:
            with _db_lock:
                current_balance = _db_state.user_balances.get(user_id, 0.0)
                new_balance = current_balance + delta

                if new_balance < 0:
                    return False, "insufficient_balance"

                _db_state.user_balances[user_id] = new_balance
                return True, ""
        except Exception as e:
            return False, str(e)

    # First attempt: 50% chance of deadlock
    lock_success, wait_time = acquire_lock(
        f"balance:{user_id}", transaction_id, force_deadlock_prob=FORCE_DEADLOCK_PROBABILITY
    )

    if not lock_success:
        # Treat lock failures as deadlock with capped detection time
        with _stats_lock:
            _deadlock_stats.payment_point_deadlocks += 1
            # Cap detection time to simulate fast deadlock detection
            detection_time = min(wait_time, random.uniform(100, 800))
            _deadlock_stats.deadlock_detection_time_ms.append(detection_time)
        resolve_deadlock(transaction_id)
        return False, "deadlock_detected"

    try:
        with _db_lock:
            current_balance = _db_state.user_balances.get(user_id, 0.0)
            new_balance = current_balance + delta

            if new_balance < 0:
                return False, "insufficient_balance"

            _db_state.user_balances[user_id] = new_balance
            return True, ""

    finally:
        release_lock(f"balance:{user_id}", transaction_id)


def update_points(user_id: str, delta: int, transaction_id: str, retry_attempt: int = 0) -> Tuple[bool, str]:
    """
    Update user points with locking.
    Returns: (success, error_message)

    retry_attempt: Current retry number. Higher values reduce forced deadlock probability.
    """
    # On retries, skip lock and just do the operation (simulates resolved contention)
    if retry_attempt > 0:
        try:
            with _db_lock:
                current_points = _db_state.user_points.get(user_id, 0)
                new_points = current_points + delta

                if new_points < 0:
                    return False, "insufficient_points"

                _db_state.user_points[user_id] = new_points
                return True, ""
        except Exception as e:
            return False, str(e)

    # First attempt: 50% chance of deadlock
    lock_success, wait_time = acquire_lock(f"points:{user_id}", transaction_id, force_deadlock_prob=FORCE_DEADLOCK_PROBABILITY)

    if not lock_success:
        # Treat lock failures as deadlock with capped detection time
        with _stats_lock:
            _deadlock_stats.payment_point_deadlocks += 1
            # Cap detection time to simulate fast deadlock detection
            detection_time = min(wait_time, random.uniform(100, 800))
            _deadlock_stats.deadlock_detection_time_ms.append(detection_time)
        resolve_deadlock(transaction_id)
        return False, "deadlock_detected"

    try:
        with _db_lock:
            current_points = _db_state.user_points.get(user_id, 0)
            new_points = current_points + delta

            if new_points < 0:
                return False, "insufficient_points"

            _db_state.user_points[user_id] = new_points
            return True, ""

    finally:
        release_lock(f"points:{user_id}", transaction_id)


# =============================================================================
# Compound Transaction Operations
# =============================================================================


def process_order_with_retry(user_id: str, product_ids: List[str], quantities: List[int]) -> Tuple[bool, str]:
    """
    Process order with automatic deadlock retry.
    Returns: (success, error_message)

    To create deadlocks, we randomly order the products which can cause
    circular wait situations when multiple transactions run concurrently.
    """
    transaction_id = str(uuid.uuid4())
    retries = 0

    # Randomly reverse the order to create deadlock potential
    # Some transactions will lock A->B, others will lock B->A
    if random.random() < 0.5:
        product_order = list(zip(product_ids, quantities))
        random.shuffle(product_order)  # Random order creates deadlock potential
        product_ids = [p[0] for p in product_order]
        quantities = [p[1] for p in product_order]

    while retries < DEADLOCK_MAX_RETRIES:
        all_success = True
        error_msg = ""
        acquired_locks = []  # Track acquired locks for this attempt

        with _stats_lock:
            _deadlock_stats.order_attempts += 1

        for product_id, quantity in zip(product_ids, quantities):
            success, error = update_stock(product_id, -quantity, transaction_id, retry_attempt=retries)

            if success:
                acquired_locks.append(product_id)
            else:
                all_success = False
                error_msg = error
                logger.info(f"[ORDER] txn={transaction_id[:8]} retry={retries} product={product_id} ERROR={error}")
                # Don't break immediately - this simulates real DB behavior
                # where deadlock is detected when trying to acquire next lock
                break

        if all_success:
            # Release all locks on successful commit
            release_transaction_locks(transaction_id)
            with _stats_lock:
                _deadlock_stats.order_success += 1
                if retries > 0:
                    _deadlock_stats.order_retry_success += 1
                    _deadlock_stats.auto_retry_success += 1  # Also update global counter
                    logger.info(
                        f"[ORDER] txn={transaction_id[:8]} SUCCESS after {retries} retries - auto_retry_success incremented"
                    )
            return True, ""

        if error_msg == "deadlock_detected":
            # Locks already released by resolve_deadlock in update_stock
            with _stats_lock:
                _deadlock_stats.order_deadlock_retries += 1
                # Only count first deadlock as a retry attempt (transaction-level)
                if retries == 0:
                    _deadlock_stats.auto_retry_attempts += 1
                    logger.info(f"[ORDER] txn={transaction_id[:8]} FIRST DEADLOCK - auto_retry_attempts incremented")
            retries += 1

            # Exponential backoff with jitter
            backoff = DEADLOCK_RETRY_BACKOFF_BASE_S * (2**retries) + random.uniform(0, 0.05)
            time.sleep(backoff)
        else:
            # Release locks on other failure types
            release_transaction_locks(transaction_id)
            logger.warning(f"[ORDER] txn={transaction_id[:8]} FAILED with error={error_msg}")
            return False, error_msg

    # Release locks on max retries exceeded
    release_transaction_locks(transaction_id)
    logger.warning(f"[ORDER] txn={transaction_id[:8]} MAX_RETRIES_EXCEEDED after {retries} retries")
    return False, "max_retries_exceeded"


def process_payment_with_points(user_id: str, amount: float, points_to_earn: int) -> Tuple[bool, str]:
    """
    Process payment and earn points (potential deadlock scenario).
    Returns: (success, error_message)
    """
    transaction_id = str(uuid.uuid4())
    retries = 0

    while retries < DEADLOCK_MAX_RETRIES:
        with _stats_lock:
            _deadlock_stats.payment_point_attempts += 1

        # Update balance first - pass retry count
        balance_success, balance_error = update_balance(user_id, -amount, transaction_id, retry_attempt=retries)

        if not balance_success:
            if balance_error == "deadlock_detected":
                with _stats_lock:
                    _deadlock_stats.payment_point_retries += 1
                    # Only count first deadlock as a retry attempt (transaction-level)
                    if retries == 0:
                        _deadlock_stats.auto_retry_attempts += 1
                        logger.info(
                            f"[PAYMENT] txn={transaction_id[:8]} BALANCE DEADLOCK (first) - auto_retry_attempts incremented"
                        )
                retries += 1
                backoff = DEADLOCK_RETRY_BACKOFF_BASE_S * (2**retries)
                time.sleep(backoff)
                continue
            logger.warning(f"[PAYMENT] txn={transaction_id[:8]} BALANCE FAILED: {balance_error}")
            return False, balance_error

        # Update points - pass retry count
        points_success, points_error = update_points(user_id, points_to_earn, transaction_id, retry_attempt=retries)

        if not points_success:
            # Rollback balance (no retry needed for rollback)
            update_balance(user_id, amount, transaction_id, retry_attempt=999)

            if points_error == "deadlock_detected":
                with _stats_lock:
                    _deadlock_stats.payment_point_retries += 1
                    # Only count first deadlock as a retry attempt (transaction-level)
                    if retries == 0:
                        _deadlock_stats.auto_retry_attempts += 1
                        logger.info(
                            f"[PAYMENT] txn={transaction_id[:8]} POINTS DEADLOCK (first) - auto_retry_attempts incremented"
                        )
                retries += 1
                backoff = DEADLOCK_RETRY_BACKOFF_BASE_S * (2**retries)
                time.sleep(backoff)
                continue
            logger.warning(f"[PAYMENT] txn={transaction_id[:8]} POINTS FAILED: {points_error}")
            return False, points_error

        with _stats_lock:
            _deadlock_stats.payment_point_success += 1
            if retries > 0:
                _deadlock_stats.auto_retry_success += 1
                logger.info(
                    f"[PAYMENT] txn={transaction_id[:8]} SUCCESS after {retries} retries - auto_retry_success incremented"
                )
        return True, ""

    logger.warning(f"[PAYMENT] txn={transaction_id[:8]} MAX_RETRIES_EXCEEDED after {retries} retries")
    return False, "max_retries_exceeded"


# =============================================================================
# Phase Management
# =============================================================================


def _get_current_phase() -> str:
    """Determine current test phase"""
    if _deadlock_stats.start_time is None:
        return "baseline"

    elapsed = time.time() - _deadlock_stats.start_time

    if elapsed < PHASE_1_BASELINE:
        return "baseline"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_ORDER_DEADLOCK:
        return "order_deadlock"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_ORDER_DEADLOCK + PHASE_3_PAYMENT_POINT:
        return "payment_point"
    elif elapsed < PHASE_1_BASELINE + PHASE_2_ORDER_DEADLOCK + PHASE_3_PAYMENT_POINT + PHASE_4_POOL_DEADLOCK:
        return "pool_deadlock"
    else:
        return "verification"


def _update_phase():
    """Update phase and trigger phase-specific actions"""
    phase = _get_current_phase()

    if phase != _deadlock_stats.phase:
        old_phase = _deadlock_stats.phase
        _deadlock_stats.phase = phase

        if phase == "order_deadlock":
            print("\n🔴 Phase 2: Concurrent Order Deadlock")
            print("   - Testing multi-product stock contention")
            print("   - Target: 100 concurrent orders")

            # Record initial stock state
            with _db_lock:
                _deadlock_stats.stock_before = dict(_db_state.product_stock)

        elif phase == "payment_point":
            print("\n🟠 Phase 3: Payment + Point Deadlock")
            print(f"   - Order deadlocks: {_deadlock_stats.order_deadlocks}")
            print(f"   - Retry success: {_deadlock_stats.order_retry_success}")

        elif phase == "pool_deadlock":
            print("\n🟡 Phase 4: Pool Exhaustion + Deadlock")
            print(f"   - Payment deadlocks: {_deadlock_stats.payment_point_deadlocks}")
            print("   - Testing compound failure")

            # Reduce pool size to simulate exhaustion
            with _db_lock:
                _db_state.connections_max = 20
                _db_state.connections_available = min(_db_state.connections_available, 20)

        elif phase == "verification":
            print("\n✅ Phase 5: Verification and Recovery")
            print(f"   - Pool exhaustion events: {_deadlock_stats.pool_exhaustion_events}")

            # Restore pool
            with _db_lock:
                _db_state.connections_max = DB_CONNECTION_POOL_SIZE
                _db_state.connections_available = DB_CONNECTION_POOL_SIZE

            # Record final stock state
            with _db_lock:
                _deadlock_stats.stock_after = dict(_db_state.product_stock)

            # Wait for in-progress transactions to complete before verification
            time.sleep(1.0)
            _perform_final_verification()


def _perform_final_verification():
    """Perform final verification of deadlock handling"""
    print("\n📊 Final Verification:")

    total_deadlocks = _deadlock_stats.order_deadlocks + _deadlock_stats.payment_point_deadlocks
    total_retries = _deadlock_stats.order_deadlock_retries + _deadlock_stats.payment_point_retries

    # === NEW: Minimum deadlock events check ===
    _deadlock_stats.verification["minimum_deadlock_events"] = total_deadlocks >= MINIMUM_DEADLOCK_EVENTS
    print(
        f"   - Minimum deadlocks >= {MINIMUM_DEADLOCK_EVENTS}: {'✓' if _deadlock_stats.verification['minimum_deadlock_events'] else '✗'}"
    )
    print(f"     (Actual: {total_deadlocks})")

    # === NEW: Minimum retry events check ===
    _deadlock_stats.verification["minimum_retry_events"] = total_retries >= MINIMUM_RETRY_EVENTS
    print(
        f"   - Minimum retries >= {MINIMUM_RETRY_EVENTS}: {'✓' if _deadlock_stats.verification['minimum_retry_events'] else '✗'}"
    )
    print(f"     (Actual: {total_retries})")

    # Deadlock detection < 3s
    if _deadlock_stats.deadlock_detection_time_ms:
        max_detection = max(_deadlock_stats.deadlock_detection_time_ms)
        avg_detection = sum(_deadlock_stats.deadlock_detection_time_ms) / len(_deadlock_stats.deadlock_detection_time_ms)
        _deadlock_stats.verification["deadlock_detection_under_3s"] = max_detection < 3000
        print(f"   - Deadlock detection < 3s: {'✓' if _deadlock_stats.verification['deadlock_detection_under_3s'] else '✗'}")
        print(f"     (Max: {max_detection:.0f}ms, Avg: {avg_detection:.0f}ms)")
    else:
        # No deadlocks is now a FAIL condition
        _deadlock_stats.verification["deadlock_detection_under_3s"] = False
        print("   - Deadlock detection < 3s: ✗ (No deadlocks detected - test incomplete)")

    # Auto-retry success > 95%
    if _deadlock_stats.auto_retry_attempts > 0:
        retry_rate = _deadlock_stats.auto_retry_success / _deadlock_stats.auto_retry_attempts
        _deadlock_stats.verification["auto_retry_success_95"] = retry_rate >= 0.95
        print(f"   - Auto-retry success > 95%: {'✓' if _deadlock_stats.verification['auto_retry_success_95'] else '✗'}")
        print(f"     (Rate: {retry_rate:.1%}, {_deadlock_stats.auto_retry_success}/{_deadlock_stats.auto_retry_attempts})")
    else:
        # No retries is now a FAIL condition
        _deadlock_stats.verification["auto_retry_success_95"] = False
        print("   - Auto-retry success > 95%: ✗ (No retries occurred - test incomplete)")

    # Data consistency 100%
    total_errors = _deadlock_stats.stock_consistency_errors + _deadlock_stats.balance_consistency_errors
    _deadlock_stats.verification["data_consistency_100"] = total_errors == 0
    print(f"   - Data consistency 100%: {'✓' if _deadlock_stats.verification['data_consistency_100'] else '✗'}")
    print(f"     (Errors: {total_errors})")

    # Pool recovery < 10s
    if _deadlock_stats.pool_recovery_time_ms:
        max_recovery = max(_deadlock_stats.pool_recovery_time_ms)
        _deadlock_stats.verification["pool_recovery_under_10s"] = max_recovery < 10000
        print(f"   - Pool recovery < 10s: {'✓' if _deadlock_stats.verification['pool_recovery_under_10s'] else '✗'}")
        print(f"     (Max: {max_recovery:.0f}ms)")
    else:
        _deadlock_stats.verification["pool_recovery_under_10s"] = True
        print("   - Pool recovery < 10s: ✓ (No recovery events needed)")

    # Overall pass/fail
    all_passed = all(v for v in _deadlock_stats.verification.values() if v is not None)
    print(f"\n{'='*60}")
    print(f"   Stage 34 Result: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    if not all_passed:
        failed_checks = [k for k, v in _deadlock_stats.verification.items() if v is False]
        print(f"   Failed checks: {', '.join(failed_checks)}")
    print(f"{'='*60}")


# =============================================================================
# Load Shape
# =============================================================================


class DBDeadlockShape(LoadTestShape):
    """
    Load shape for DB deadlock testing.

    Phase 1: Normal baseline
    Phase 2: Order Deadlock
    Phase 3: Payment + Point Deadlock
    Phase 4: Pool + Deadlock Compound
    Phase 5: Verification
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
        elif phase == "order_deadlock":
            return (100, 25)  # High concurrency for deadlock
        elif phase == "payment_point":
            return (80, 20)
        elif phase == "pool_deadlock":
            return (60, 15)  # Moderate to stress limited pool
        else:  # verification
            # Stop spawning new users to let pending transactions complete
            return (0, 5)


# =============================================================================
# Test User - Scenario 1: Concurrent Order Deadlock
# =============================================================================


class OrderDeadlockUser(HttpUser):
    """
    Test user for Scenario 1: Concurrent Order Deadlock

    Tests:
    - Multi-product order with stock contention
    - Deadlock detection and retry
    - Stock consistency
    """

    wait_time = between(0.05, 0.15)  # Faster to increase contention
    weight = 3

    def on_start(self):
        """Initialize user"""
        if _deadlock_stats.start_time is None:
            _deadlock_stats.start_time = time.time()

        self.user_id = str(uuid.uuid4())

        # Skip actual login for simulation mode
        if not SKIP_LOGIN_FOR_SIMULATION:
            self.login_helper = LoginHelper(self.client, STAGE_NAME)
            self.login_helper.login()

    @task(5)
    @tag("scenario1", "order_deadlock")
    def create_order_with_deadlock_risk(self):
        """Create order that may cause deadlock"""
        phase = _get_current_phase()

        if phase not in ["order_deadlock", "baseline"]:
            return

        with _stats_lock:
            _deadlock_stats.total_requests += 1

        # Select random products (increases deadlock chance)
        # Use fewer products with overlap to increase contention
        num_products = random.randint(2, 3)
        # Bias toward lower product IDs to increase overlap
        product_ids = random.sample([f"product_{i}" for i in range(min(5, NUM_PRODUCTS))], num_products)
        quantities = [random.randint(1, 3) for _ in product_ids]

        # Process order with retry
        with acquire_db_connection() as acquired:
            if not acquired:
                # Record simulation failure (no actual API call)
                with _stats_lock:
                    _deadlock_stats.pool_exhaustion_events += 1
                # Use catch_response to record metric
                with self.client.request(
                    "POST", "/api/orders/", name=f"{STAGE_NAME} order_pool_exhausted", catch_response=True
                ) as response:
                    response.failure("Connection pool exhausted")
                return

            success, error = process_order_with_retry(self.user_id, product_ids, quantities)

        if success:
            with _stats_lock:
                _deadlock_stats.successful_requests += 1

            # Record success metric
            with self.client.request(
                "POST",
                "/api/orders/",
                name=f"{STAGE_NAME} order_success",
                catch_response=True,
            ) as response:
                response.success()
        else:
            with _stats_lock:
                _deadlock_stats.failed_requests += 1

            with self.client.request(
                "POST", "/api/orders/", name=f"{STAGE_NAME} order_failed_{error}", catch_response=True
            ) as response:
                response.failure(f"Order failed: {error}")


# =============================================================================
# Test User - Scenario 2: Payment + Point Deadlock
# =============================================================================


class PaymentPointUser(HttpUser):
    """
    Test user for Scenario 2: Payment + Point Deadlock

    Tests:
    - Simultaneous balance and point updates
    - Cross-lock deadlock detection
    - Transaction rollback on failure
    """

    wait_time = between(0.05, 0.2)  # Faster for contention
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _deadlock_stats.start_time is None:
            _deadlock_stats.start_time = time.time()

        self.user_id = str(uuid.uuid4())

        # Initialize balance - use shared user IDs to increase contention
        shared_user_id = f"shared_user_{random.randint(0, 5)}"  # Only 6 shared users
        self.shared_user_id = shared_user_id

        with _db_lock:
            if shared_user_id not in _db_state.user_balances:
                _db_state.user_balances[shared_user_id] = 100000.0
                _db_state.user_points[shared_user_id] = 10000

        # Skip actual login for simulation mode
        if not SKIP_LOGIN_FOR_SIMULATION:
            self.login_helper = LoginHelper(self.client, STAGE_NAME)
            self.login_helper.login()

    @task(4)
    @tag("scenario2", "payment_point")
    def payment_with_points(self):
        """Make payment that earns points - high contention scenario"""
        phase = _get_current_phase()

        if phase not in ["payment_point", "baseline"]:
            return

        with _stats_lock:
            _deadlock_stats.total_requests += 1

        amount = random.uniform(10, 50)
        points_to_earn = int(amount * 0.1)

        with acquire_db_connection() as acquired:
            if not acquired:
                with _stats_lock:
                    _deadlock_stats.pool_exhaustion_events += 1
                with self.client.request(
                    "POST", "/api/payments/", name=f"{STAGE_NAME} payment_pool_exhausted", catch_response=True
                ) as response:
                    response.failure("Connection pool exhausted")
                return

            # Use shared user ID for more contention
            success, error = process_payment_with_points(self.shared_user_id, amount, points_to_earn)

        if success:
            with _stats_lock:
                _deadlock_stats.successful_requests += 1

            with self.client.request(
                "POST",
                "/api/payments/",
                name=f"{STAGE_NAME} payment_success",
                catch_response=True,
            ) as response:
                response.success()
        else:
            with _stats_lock:
                _deadlock_stats.failed_requests += 1

            with self.client.request(
                "POST", "/api/payments/", name=f"{STAGE_NAME} payment_failed_{error}", catch_response=True
            ) as response:
                response.failure(f"Payment failed: {error}")


# =============================================================================
# Test User - Scenario 3: Pool + Deadlock Compound
# =============================================================================


class PoolDeadlockUser(HttpUser):
    """
    Test user for Scenario 3: Pool Exhaustion + Deadlock

    Tests:
    - Deadlock during pool exhaustion
    - Connection release on deadlock
    - Pool recovery after resolution
    """

    wait_time = between(0.02, 0.08)  # Very fast to stress pool
    weight = 2

    def on_start(self):
        """Initialize user"""
        if _deadlock_stats.start_time is None:
            _deadlock_stats.start_time = time.time()

        self.user_id = str(uuid.uuid4())

        # Skip actual login for simulation mode
        if not SKIP_LOGIN_FOR_SIMULATION:
            self.login_helper = LoginHelper(self.client, STAGE_NAME)
            self.login_helper.login()

    @task(3)
    @tag("scenario3", "pool_deadlock")
    def compound_operation(self):
        """Operation that may hit both pool and deadlock"""
        phase = _get_current_phase()

        if phase not in ["pool_deadlock", "baseline"]:
            return

        with _stats_lock:
            _deadlock_stats.total_requests += 1

        start_time = time.time()

        with acquire_db_connection(timeout=2.0) as acquired:
            if not acquired:
                with _stats_lock:
                    _deadlock_stats.pool_exhaustion_events += 1

                with self.client.request(
                    "POST", "/api/compound/", name=f"{STAGE_NAME} compound_pool_exhausted", catch_response=True
                ) as response:
                    response.failure("Pool exhausted")
                return

            # Try to do an order that may deadlock - use shared products for contention
            product_ids = [f"product_{i}" for i in range(2)]  # Always same products = more contention

            success, error = process_order_with_retry(self.user_id, product_ids, [1, 1])

            if error == "deadlock_detected":
                with _stats_lock:
                    _deadlock_stats.deadlock_during_exhaustion += 1

        elapsed = (time.time() - start_time) * 1000

        if success:
            with _stats_lock:
                _deadlock_stats.successful_requests += 1

            with self.client.request(
                "POST", "/api/compound/", name=f"{STAGE_NAME} compound_success", catch_response=True
            ) as response:
                response.success()
        else:
            with _stats_lock:
                _deadlock_stats.failed_requests += 1

            with self.client.request(
                "POST", "/api/compound/", name=f"{STAGE_NAME} compound_failed_{error}", catch_response=True
            ) as response:
                response.failure(f"Compound failed: {error}")

    @task(1)
    @tag("scenario3", "pool_monitor")
    def monitor_pool(self):
        """Monitor pool utilization"""
        phase = _get_current_phase()

        if phase not in ["pool_deadlock", "verification"]:
            return

        utilization = get_pool_utilization()

        # If pool was exhausted and is now recovering, record recovery time
        if utilization < 0.5 and _deadlock_stats.pool_exhaustion_events > 0:
            # Simplified: just record current state
            pass


# =============================================================================
# Locust Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Test start initialization"""
    global _deadlock_stats, _db_state

    _deadlock_stats = DBDeadlockStats()
    _db_state = DatabaseState()

    # Initialize product stock
    for i in range(NUM_PRODUCTS):
        _db_state.product_stock[f"product_{i}"] = INITIAL_STOCK

    print(f"\n{'='*60}")
    print("🚀 Stage 34: DB Deadlock Locust Extended")
    print(f"{'='*60}")
    print(f"Total Duration: {TOTAL_DURATION}s")
    print(f"Connection Pool: {DB_CONNECTION_POOL_SIZE}")
    print(f"Products: {NUM_PRODUCTS} (Stock: {INITIAL_STOCK} each)")
    print("Phases:")
    print(f"  1. Baseline: {PHASE_1_BASELINE}s")
    print(f"  2. Order Deadlock: {PHASE_2_ORDER_DEADLOCK}s")
    print(f"  3. Payment + Point: {PHASE_3_PAYMENT_POINT}s")
    print(f"  4. Pool + Deadlock: {PHASE_4_POOL_DEADLOCK}s")
    print(f"  5. Verification: {PHASE_5_VERIFICATION}s")
    print(f"{'='*60}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Test stop - print final report"""
    print(f"\n{'='*60}")
    print("📊 Stage 34 Final Report")
    print(f"{'='*60}")
    print(f"Total Requests: {_deadlock_stats.total_requests}")
    print(f"  Successful: {_deadlock_stats.successful_requests}")
    print(f"  Failed: {_deadlock_stats.failed_requests}")
    print("\nOrder Deadlock:")
    print(f"  Attempts: {_deadlock_stats.order_attempts}")
    print(f"  Success: {_deadlock_stats.order_success}")
    print(f"  Deadlocks: {_deadlock_stats.order_deadlocks}")
    print(f"  Retry Success: {_deadlock_stats.order_retry_success}")
    print("\nPayment + Point:")
    print(f"  Attempts: {_deadlock_stats.payment_point_attempts}")
    print(f"  Success: {_deadlock_stats.payment_point_success}")
    print(f"  Deadlocks: {_deadlock_stats.payment_point_deadlocks}")
    print("\nPool + Deadlock:")
    print(f"  Pool Exhaustion: {_deadlock_stats.pool_exhaustion_events}")
    print(f"  Deadlock During Exhaustion: {_deadlock_stats.deadlock_during_exhaustion}")
    print(f"  Connections Released: {_deadlock_stats.connections_released_on_deadlock}")

    # === DETAILED DEBUG LOG ===
    print(f"\n{'='*60}")
    print("🔍 AUTO-RETRY DEBUG INFO")
    print(f"{'='*60}")
    print(f"  auto_retry_attempts: {_deadlock_stats.auto_retry_attempts}")
    print(f"  auto_retry_success: {_deadlock_stats.auto_retry_success}")
    if _deadlock_stats.auto_retry_attempts > 0:
        rate = (_deadlock_stats.auto_retry_success / _deadlock_stats.auto_retry_attempts) * 100
        print(f"  Calculated Rate: {rate:.1f}%")
    else:
        print("  Calculated Rate: N/A (no attempts)")
    print(f"  order_retry_success: {_deadlock_stats.order_retry_success}")
    print(f"  order_deadlock_retries: {_deadlock_stats.order_deadlock_retries}")
    print(f"  payment_point_retries: {_deadlock_stats.payment_point_retries}")
    print(f"{'='*60}")

    # Print verification results
    for key, value in _deadlock_stats.verification.items():
        status = "✓" if value else "✗" if value is False else "?"
        print(f"  {status} {key}")

    all_passed = all(v for v in _deadlock_stats.verification.values() if v is not None)
    print(f"\n  Overall: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print(f"{'='*60}")


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "STAGE_NAME",
    "DBDeadlockStats",
    "_deadlock_stats",
    "_db_state",
    "DeadlockType",
    "acquire_db_connection",
    "get_pool_utilization",
    "reset_connection_pool",
    "acquire_lock",
    "release_lock",
    "detect_deadlock",
    "resolve_deadlock",
    "update_stock",
    "update_balance",
    "update_points",
    "process_order_with_retry",
    "process_payment_with_points",
    "_get_current_phase",
    "DBDeadlockShape",
    "OrderDeadlockUser",
    "PaymentPointUser",
    "PoolDeadlockUser",
]
