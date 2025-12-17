"""
Shopping System Chaos Injection Module

This module provides controlled, reproducible failure injection for testing
self-healing mechanisms and system resilience.

IMPORTANT:
- Chaos is DISABLED by default
- Enable via CHAOS_MODE environment variable
- All chaos events are logged for traceability

Usage:
    # Enable chaos mode
    export CHAOS_MODE=true
    
    # Or enable specific chaos scenarios
    export CHAOS_PAYMENT_CONFIRM_DELAY=true
    export CHAOS_PARTIAL_FAILURE=true
    export CHAOS_RACE_AMPLIFICATION=true
    export CHAOS_ASYNC_TASK_FAILURE=true
"""

from .config import ChaosConfig, chaos_config
from .decorators import chaos_breakpoint, with_chaos
from .breakpoints import (
    BREAKPOINT_PAYMENT_CONFIRM_PRE_DB,
    BREAKPOINT_PAYMENT_CONFIRM_POST_PG,
    BREAKPOINT_CANCEL_RACE_WINDOW,
    BREAKPOINT_CONFIRM_RACE_WINDOW,
    BREAKPOINT_ASYNC_TASK_EXECUTE,
    BREAKPOINT_ROLLBACK_PRE_RESTORE,
)

__all__ = [
    # Config
    "ChaosConfig",
    "chaos_config",
    # Decorators
    "chaos_breakpoint",
    "with_chaos",
    # Breakpoint names
    "BREAKPOINT_PAYMENT_CONFIRM_PRE_DB",
    "BREAKPOINT_PAYMENT_CONFIRM_POST_PG",
    "BREAKPOINT_CANCEL_RACE_WINDOW",
    "BREAKPOINT_CONFIRM_RACE_WINDOW",
    "BREAKPOINT_ASYNC_TASK_EXECUTE",
    "BREAKPOINT_ROLLBACK_PRE_RESTORE",
]
