"""
Chaos Breakpoint Definitions

Defines all chaos injection points in the shopping system.
Each breakpoint has a unique name and specific behavior.
"""

# ============================================================
# BREAKPOINT NAMES (Constants)
# ============================================================

# Payment Confirm Flow
BREAKPOINT_PAYMENT_CONFIRM_PRE_DB = "payment_confirm_pre_db"
"""Inject delay BEFORE database commit in payment confirmation.
Expands the race window for cancel/confirm conflicts."""

BREAKPOINT_PAYMENT_CONFIRM_POST_PG = "payment_confirm_post_pg"
"""Inject failure AFTER PG (Payment Gateway) success.
Simulates internal processing failure after external success."""

# Cancel Flow
BREAKPOINT_CANCEL_RACE_WINDOW = "cancel_race_window"
"""Inject delay in cancel path to amplify race conditions.
Forces concurrent state conflicts with confirm operations."""

BREAKPOINT_CONFIRM_RACE_WINDOW = "confirm_race_window"
"""Inject delay in confirm path to amplify race conditions.
Forces concurrent state conflicts with cancel operations."""

# Async Tasks
BREAKPOINT_ASYNC_TASK_EXECUTE = "async_task_execute"
"""Inject failure in Celery task execution.
Simulates task failure after queueing."""

# Rollback Flow
BREAKPOINT_ROLLBACK_PRE_RESTORE = "rollback_pre_restore"
"""Inject failure before stock/points restoration.
Tests rollback retry mechanism."""


# ============================================================
# BREAKPOINT DEFINITIONS (Detailed Behavior)
# ============================================================

BREAKPOINT_DEFINITIONS = {
    BREAKPOINT_PAYMENT_CONFIRM_PRE_DB: {
        "name": BREAKPOINT_PAYMENT_CONFIRM_PRE_DB,
        "description": "Payment confirm delay before DB commit",
        "scenario": "payment_confirm_delay",
        "action_type": "delay",
        "target_stages": ["stage4", "stage7"],
        "default_delay_ms": 2000,
        "effect": "Expands race window for cancel/confirm conflicts",
    },
    
    BREAKPOINT_PAYMENT_CONFIRM_POST_PG: {
        "name": BREAKPOINT_PAYMENT_CONFIRM_POST_PG,
        "description": "Failure after PG success, before internal commit",
        "scenario": "partial_failure",
        "action_type": "exception",
        "target_stages": ["stage5"],
        "default_probability": 0.3,
        "effect": "Triggers rollback/DLQ for successful PG transactions",
    },
    
    BREAKPOINT_CANCEL_RACE_WINDOW: {
        "name": BREAKPOINT_CANCEL_RACE_WINDOW,
        "description": "Delay in cancel path",
        "scenario": "race_amplification",
        "action_type": "delay",
        "target_stages": ["stage4", "stage7"],
        "default_delay_ms": 500,
        "effect": "Forces concurrent state conflicts",
    },
    
    BREAKPOINT_CONFIRM_RACE_WINDOW: {
        "name": BREAKPOINT_CONFIRM_RACE_WINDOW,
        "description": "Delay in confirm path",
        "scenario": "race_amplification",
        "action_type": "delay",
        "target_stages": ["stage4", "stage7"],
        "default_delay_ms": 500,
        "effect": "Forces concurrent state conflicts",
    },
    
    BREAKPOINT_ASYNC_TASK_EXECUTE: {
        "name": BREAKPOINT_ASYNC_TASK_EXECUTE,
        "description": "Celery task execution failure",
        "scenario": "async_task_failure",
        "action_type": "exception",
        "target_stages": ["stage5", "stage10"],
        "default_probability": 0.2,
        "effect": "Tests task retry and dead letter queue",
    },
    
    BREAKPOINT_ROLLBACK_PRE_RESTORE: {
        "name": BREAKPOINT_ROLLBACK_PRE_RESTORE,
        "description": "Failure before rollback restoration",
        "scenario": "partial_failure",
        "action_type": "exception",
        "target_stages": ["stage5"],
        "default_probability": 0.1,
        "effect": "Tests rollback retry mechanism",
    },
}


def get_breakpoint_info(breakpoint_name: str) -> dict:
    """
    Get detailed information about a breakpoint.
    
    Args:
        breakpoint_name: Name of the breakpoint
        
    Returns:
        Breakpoint definition dict or empty dict if not found
    """
    return BREAKPOINT_DEFINITIONS.get(breakpoint_name, {})


def list_breakpoints() -> list:
    """
    List all available breakpoints.
    
    Returns:
        List of breakpoint names
    """
    return list(BREAKPOINT_DEFINITIONS.keys())


def get_breakpoints_for_stage(stage: str) -> list:
    """
    Get breakpoints relevant to a specific test stage.
    
    Args:
        stage: Stage name (e.g., "stage4", "stage5")
        
    Returns:
        List of breakpoint names for the stage
    """
    return [
        name for name, definition in BREAKPOINT_DEFINITIONS.items()
        if stage in definition.get("target_stages", [])
    ]
