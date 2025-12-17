"""
Chaos Injection Decorators and Utilities

Provides easy-to-use decorators and functions for injecting
chaos at specific breakpoints in the shopping system.
"""

import time
import functools
import logging
from typing import Callable, Optional, Any

from .config import chaos_config
from .breakpoints import (
    get_breakpoint_info,
    BREAKPOINT_PAYMENT_CONFIRM_PRE_DB,
    BREAKPOINT_PAYMENT_CONFIRM_POST_PG,
    BREAKPOINT_CANCEL_RACE_WINDOW,
    BREAKPOINT_CONFIRM_RACE_WINDOW,
    BREAKPOINT_ASYNC_TASK_EXECUTE,
    BREAKPOINT_ROLLBACK_PRE_RESTORE,
)

logger = logging.getLogger(__name__)


class ChaosException(Exception):
    """Exception raised by chaos injection."""
    
    def __init__(self, breakpoint_name: str, message: str = None):
        self.breakpoint_name = breakpoint_name
        self.message = message or f"Chaos injection at {breakpoint_name}"
        super().__init__(self.message)


class PartialFailureException(ChaosException):
    """Exception simulating partial transaction failure."""
    pass


class AsyncTaskChaosException(ChaosException):
    """Exception for async task failures."""
    pass


def chaos_breakpoint(
    breakpoint_name: str,
    context: dict = None,
    force: bool = False
) -> bool:
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
        ChaosException: If the breakpoint action is 'exception'
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
    chaos_config.log_chaos_event(
        breakpoint_name=breakpoint_name,
        action=action_type,
        context=context or {},
        level="warning"
    )
    
    # Execute chaos action
    if action_type == "delay":
        delay_seconds = chaos_config.get_delay_seconds(scenario)
        logger.warning(
            f"[CHAOS] {breakpoint_name}: Injecting delay of {delay_seconds:.3f}s"
        )
        time.sleep(delay_seconds)
        return True
        
    elif action_type == "exception":
        if breakpoint_name == BREAKPOINT_PAYMENT_CONFIRM_POST_PG:
            raise PartialFailureException(
                breakpoint_name,
                "[CHAOS] Simulated internal failure after PG success"
            )
        elif breakpoint_name == BREAKPOINT_ASYNC_TASK_EXECUTE:
            raise AsyncTaskChaosException(
                breakpoint_name,
                "[CHAOS] Simulated async task failure"
            )
        else:
            raise ChaosException(
                breakpoint_name,
                f"[CHAOS] Simulated failure at {breakpoint_name}"
            )
    
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


def maybe_inject_delay(
    breakpoint_name: str,
    context: dict = None
) -> bool:
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


def maybe_inject_failure(
    breakpoint_name: str,
    context: dict = None
) -> bool:
    """
    Convenience function to inject only failures (exceptions).
    
    Use in places where partial failures should be simulated.
    
    Args:
        breakpoint_name: Name of the breakpoint
        context: Additional context
        
    Returns:
        True if about to raise (never actually returns True)
        
    Raises:
        ChaosException subclass based on breakpoint type
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
