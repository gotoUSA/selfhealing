"""
Chaos Injection Decorators and Utilities

Provides easy-to-use decorators and functions for injecting
chaos at specific breakpoints in the shopping system.
"""

import time
import functools
import logging
from typing import Callable

from .config import chaos_config
from .breakpoints import (
    get_breakpoint_info,
    BREAKPOINT_PAYMENT_CONFIRM_PRE_DB,
    BREAKPOINT_PAYMENT_CONFIRM_POST_PG,
    BREAKPOINT_CANCEL_RACE_WINDOW,
    BREAKPOINT_CONFIRM_RACE_WINDOW,
    BREAKPOINT_ASYNC_TASK_EXECUTE,
    BREAKPOINT_ROLLBACK_PRE_RESTORE,
    # Phase 2 Breakpoints
    BREAKPOINT_BP21_ORPHAN_PG,
    BREAKPOINT_BP22_ROLLBACK_FAILURE,
    BREAKPOINT_BP23_SILENT_TASK,
    BREAKPOINT_BP29_POINT_ORPHAN,
    BREAKPOINT_BP27_RACE_AMPLIFICATION,
    BREAKPOINT_BP30_CACHE_DIVERGENCE,
)

logger = logging.getLogger(__name__)


class ChaosError(Exception):
    """Exception raised by chaos injection."""

    def __init__(self, breakpoint_name: str, message: str = None):
        self.breakpoint_name = breakpoint_name
        self.message = message or f"Chaos injection at {breakpoint_name}"
        super().__init__(self.message)


class PartialFailureError(ChaosError):
    """Exception simulating partial transaction failure."""

    pass


class AsyncTaskChaosError(ChaosError):
    """Exception for async task failures."""

    pass


def chaos_breakpoint(breakpoint_name: str, context: dict = None, force: bool = False) -> bool:
    """
    Execute a chaos breakpoint.

    This function checks if chaos should be triggered at the named breakpoint
    and performs the appropriate chaos action (delay or exception).

    Args:
        breakpoint_name: Name of the breakpoint
        context: Additional context for logging
        force: Force trigger regardless of probability

    Returns:
        True if chaos was triggered, False otherwise

    Raises:
        ChaosError: If the breakpoint action is 'exception'
    """
    breakpoint_info = get_breakpoint_info(breakpoint_name)
    if not breakpoint_info:
        logger.debug(f"[CHAOS] Unknown breakpoint: {breakpoint_name}")
        return False

    scenario = breakpoint_info.get("scenario")

    # Check if chaos is active for this scenario
    if not chaos_config.is_chaos_active(scenario):
        return False

    action_type = breakpoint_info.get("action_type")

    # Check probability trigger
    if not force:
        probability = breakpoint_info.get("default_probability", 1.0)
        if action_type == "delay":
            probability = chaos_config.race_trigger_probability
        elif action_type == "exception":
            if scenario == "partial_failure":
                probability = chaos_config.partial_failure_probability
            elif scenario == "async_task_failure":
                probability = chaos_config.async_task_failure_probability

        if not chaos_config.should_trigger(probability):
            return False

    # Log chaos event
    chaos_config.log_chaos_event(breakpoint_name=breakpoint_name, action=action_type, context=context or {}, level="warning")

    # Execute chaos action
    if action_type == "delay":
        delay_seconds = chaos_config.get_delay_seconds(scenario)
        logger.warning(f"[CHAOS] {breakpoint_name}: Injecting delay of {delay_seconds:.3f}s")
        time.sleep(delay_seconds)
        return True

    elif action_type == "exception":
        if breakpoint_name == BREAKPOINT_PAYMENT_CONFIRM_POST_PG:
            raise PartialFailureError(breakpoint_name, "[CHAOS] Simulated internal failure after PG success")
        elif breakpoint_name == BREAKPOINT_ASYNC_TASK_EXECUTE:
            raise AsyncTaskChaosError(breakpoint_name, "[CHAOS] Simulated async task failure")
        else:
            raise ChaosError(breakpoint_name, f"[CHAOS] Simulated failure at {breakpoint_name}")

    return False


def with_chaos(breakpoint_name: str, position: str = "before"):
    """
    Decorator to inject chaos before or after a function.

    Args:
        breakpoint_name: Name of the chaos breakpoint
        position: 'before' or 'after' the function

    Returns:
        Decorated function

    Example:
        @with_chaos("payment_confirm_pre_db", position="before")
        def confirm_payment(...):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            context = {
                "function": func.__name__,
                "module": func.__module__,
            }

            if position == "before":
                chaos_breakpoint(breakpoint_name, context)

            result = func(*args, **kwargs)

            if position == "after":
                chaos_breakpoint(breakpoint_name, context)

            return result

        return wrapper

    return decorator


def maybe_inject_delay(breakpoint_name: str, context: dict = None) -> bool:
    """
    Convenience function to inject only delays (no exceptions).

    Safe to use in production code paths as it only adds delays
    when chaos mode is enabled.

    Args:
        breakpoint_name: Name of the breakpoint
        context: Additional context

    Returns:
        True if delay was injected
    """
    breakpoint_info = get_breakpoint_info(breakpoint_name)
    if not breakpoint_info:
        return False

    if breakpoint_info.get("action_type") != "delay":
        return False

    return chaos_breakpoint(breakpoint_name, context)


def maybe_inject_failure(breakpoint_name: str, context: dict = None) -> bool:
    """
    Convenience function to inject only failures (exceptions).

    Use in places where partial failures should be simulated.

    Args:
        breakpoint_name: Name of the breakpoint
        context: Additional context

    Returns:
        True if about to raise (never actually returns True)

    Raises:
        ChaosError subclass based on breakpoint type
    """
    breakpoint_info = get_breakpoint_info(breakpoint_name)
    if not breakpoint_info:
        return False

    if breakpoint_info.get("action_type") != "exception":
        return False

    return chaos_breakpoint(breakpoint_name, context)


# ============================================================
# SPECIFIC CHAOS INJECTION HELPERS
# ============================================================


def inject_payment_confirm_delay(payment_id: int = None, order_id: int = None):
    """
    Inject delay before payment confirm DB commit.

    Call this in PaymentService.confirm_payment_sync() before final commit.
    """
    context = {}
    if payment_id:
        context["payment_id"] = payment_id
    if order_id:
        context["order_id"] = order_id

    return maybe_inject_delay(BREAKPOINT_PAYMENT_CONFIRM_PRE_DB, context)


def inject_partial_failure_after_pg(payment_id: int = None, pg_response: dict = None):
    """
    Inject failure after PG success, before internal processing.

    Call this in PaymentService after successful Toss API call.
    """
    context = {"pg_success": True}
    if payment_id:
        context["payment_id"] = payment_id
    if pg_response:
        context["pg_status"] = pg_response.get("status")

    return maybe_inject_failure(BREAKPOINT_PAYMENT_CONFIRM_POST_PG, context)


def inject_cancel_race_delay(payment_id: int = None):
    """
    Inject delay in cancel path to amplify race conditions.

    Call this in PaymentService.cancel_payment() before processing.
    """
    context = {}
    if payment_id:
        context["payment_id"] = payment_id

    return maybe_inject_delay(BREAKPOINT_CANCEL_RACE_WINDOW, context)


def inject_confirm_race_delay(payment_id: int = None):
    """
    Inject delay in confirm path to amplify race conditions.

    Call this in PaymentService.confirm_payment_sync() before lock.
    """
    context = {}
    if payment_id:
        context["payment_id"] = payment_id

    return maybe_inject_delay(BREAKPOINT_CONFIRM_RACE_WINDOW, context)


def inject_async_task_failure(task_name: str = None, task_id: str = None):
    """
    Inject failure in Celery task execution.

    Call this at the start of Celery tasks.
    """
    context = {}
    if task_name:
        context["task_name"] = task_name
    if task_id:
        context["task_id"] = task_id

    return maybe_inject_failure(BREAKPOINT_ASYNC_TASK_EXECUTE, context)


def inject_rollback_failure(order_id: int = None):
    """
    Inject failure before rollback restoration.

    Call this in rollback_payment_failure task.
    """
    context = {}
    if order_id:
        context["order_id"] = order_id

    return maybe_inject_failure(BREAKPOINT_ROLLBACK_PRE_RESTORE, context)


# ============================================================
# PHASE 2 CHAOS INJECTION HELPERS (BP-21 ~ BP-30)
# ============================================================


class Phase2OrphanPGError(ChaosError):
    """BP-21: PG succeeded but internal commit will fail."""

    pass


class Phase2RollbackFailureError(ChaosError):
    """BP-22: Secondary failure during rollback."""

    pass


class Phase2SilentTaskError(ChaosError):
    """BP-23: Task fails without DLQ entry (intentional for testing)."""

    pass


class Phase2PointOrphanError(ChaosError):
    """BP-29: Point accumulation task failure."""

    pass


def inject_phase2_orphan_pg(payment_id: int = None, pg_response: dict = None):
    """
    BP-21: Inject failure after PG success to create orphaned payment.

    This simulates the scenario where:
    - Toss API call succeeds (money is charged)
    - Internal DB commit fails
    - Payment is orphaned (PG says paid, DB says not paid)

    Self-healing should detect this via DLQ and trigger reconciliation.
    """
    if not chaos_config.is_chaos_active("phase2_orphan_pg"):
        return False

    if not chaos_config.should_trigger(chaos_config.partial_failure_probability):
        return False

    context = {
        "breakpoint": "BP-21",
        "payment_id": payment_id,
        "pg_status": pg_response.get("status") if pg_response else None,
        "pg_amount": pg_response.get("totalAmount") if pg_response else None,
    }

    chaos_config.log_chaos_event(breakpoint_name=BREAKPOINT_BP21_ORPHAN_PG, action="exception", context=context, level="error")

    raise Phase2OrphanPGError(
        BREAKPOINT_BP21_ORPHAN_PG, f"[CHAOS BP-21] Internal failure after PG success. payment_id={payment_id}"
    )


def inject_phase2_rollback_failure(order_id: int = None, rollback_type: str = None):
    """
    BP-22: Inject secondary failure during rollback process.

    This simulates:
    - Original payment fails
    - Rollback task starts
    - Stock/points restoration fails (secondary failure)
    - Order stuck in inconsistent state
    """
    if not chaos_config.is_chaos_active("phase2_rollback_failure"):
        return False

    if not chaos_config.should_trigger(0.2):
        return False

    context = {
        "breakpoint": "BP-22",
        "order_id": order_id,
        "rollback_type": rollback_type,
    }

    chaos_config.log_chaos_event(
        breakpoint_name=BREAKPOINT_BP22_ROLLBACK_FAILURE, action="exception", context=context, level="error"
    )

    raise Phase2RollbackFailureError(
        BREAKPOINT_BP22_ROLLBACK_FAILURE, f"[CHAOS BP-22] Secondary rollback failure. order_id={order_id}"
    )


def inject_phase2_silent_task_failure(task_name: str = None, task_id: str = None):
    """
    BP-23: Inject silent task failure (no DLQ entry).

    This simulates Celery task exhausting retries without proper DLQ routing.
    Self-healing should detect orphaned tasks via forensic scans.
    """
    if not chaos_config.is_chaos_active("phase2_silent_task"):
        return False

    if not chaos_config.should_trigger(0.25):
        return False

    context = {
        "breakpoint": "BP-23",
        "task_name": task_name,
        "task_id": task_id,
        "silent": True,  # No DLQ will be created
    }

    chaos_config.log_chaos_event(
        breakpoint_name=BREAKPOINT_BP23_SILENT_TASK, action="silent_exception", context=context, level="error"
    )

    # This exception intentionally does NOT route to DLQ
    raise Phase2SilentTaskError(BREAKPOINT_BP23_SILENT_TASK, f"[CHAOS BP-23] Silent task failure (no DLQ). task={task_name}")


def inject_phase2_point_orphan(user_id: int = None, order_id: int = None, points: int = None):
    """
    BP-29: Inject point accumulation failure.

    This simulates:
    - Payment completed successfully
    - Point accumulation task fails
    - Customer didn't receive earned points
    """
    if not chaos_config.is_chaos_active("phase2_point_orphan"):
        return False

    if not chaos_config.should_trigger(0.2):
        return False

    context = {
        "breakpoint": "BP-29",
        "user_id": user_id,
        "order_id": order_id,
        "points_lost": points,
    }

    chaos_config.log_chaos_event(
        breakpoint_name=BREAKPOINT_BP29_POINT_ORPHAN, action="exception", context=context, level="error"
    )

    raise Phase2PointOrphanError(
        BREAKPOINT_BP29_POINT_ORPHAN, f"[CHAOS BP-29] Point accumulation failed. user={user_id}, points={points}"
    )


def inject_phase2_race_delay(payment_id: int = None, operation: str = None):
    """
    BP-27: Inject delay to amplify race window.

    3 second delay allowing confirm and cancel to execute simultaneously.
    """
    if not chaos_config.is_chaos_active("phase2_race_amplify"):
        return False

    if not chaos_config.should_trigger(0.4):
        return False

    delay_seconds = 3.0

    context = {
        "breakpoint": "BP-27",
        "payment_id": payment_id,
        "operation": operation,
        "delay_seconds": delay_seconds,
    }

    chaos_config.log_chaos_event(
        breakpoint_name=BREAKPOINT_BP27_RACE_AMPLIFICATION, action="delay", context=context, level="warning"
    )

    logger.warning(f"[CHAOS BP-27] Race window delay: {delay_seconds}s for {operation}")
    time.sleep(delay_seconds)
    return True


def inject_phase2_cache_skip_invalidation(cache_key: str = None):
    """
    BP-30: Skip cache invalidation after DB commit.

    Returns True if cache invalidation should be SKIPPED (causing divergence).
    """
    if not chaos_config.is_chaos_active("phase2_cache_divergence"):
        return False

    if not chaos_config.should_trigger(0.25):
        return False

    context = {
        "breakpoint": "BP-30",
        "cache_key": cache_key,
        "action": "skip_invalidation",
    }

    chaos_config.log_chaos_event(
        breakpoint_name=BREAKPOINT_BP30_CACHE_DIVERGENCE, action="skip_invalidation", context=context, level="warning"
    )

    logger.warning(f"[CHAOS BP-30] Skipping cache invalidation for: {cache_key}")
    return True  # Caller should skip cache invalidation


# ── Deprecated aliases (하위 호환성) ──────────────────────────
ChaosException = ChaosError
PartialFailureException = PartialFailureError
AsyncTaskChaosException = AsyncTaskChaosError
Phase2OrphanPGException = Phase2OrphanPGError
Phase2RollbackFailureException = Phase2RollbackFailureError
Phase2SilentTaskException = Phase2SilentTaskError
Phase2PointOrphanException = Phase2PointOrphanError
