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
# PHASE 2 BREAKPOINTS (BP-21 ~ BP-30)
# ============================================================

# BP-21: Orphaned PG Transaction
BREAKPOINT_BP21_ORPHAN_PG = "bp21_orphan_pg_transaction"
"""PG succeeds but internal DB commit fails. Creates orphaned payment state."""

# BP-22: Rollback Failure (Secondary Failure)
BREAKPOINT_BP22_ROLLBACK_FAILURE = "bp22_rollback_secondary_failure"
"""Rollback task fails during stock restoration. Creates stuck order."""

# BP-23: Silent Task Failure (DLQ Missing)
BREAKPOINT_BP23_SILENT_TASK = "bp23_silent_task_failure"
"""Celery task max retries exhausted without DLQ entry."""

# BP-24: Webhook Order Inversion
BREAKPOINT_BP24_WEBHOOK_INVERSION = "bp24_webhook_order_inversion"
"""Webhook events arrive out of order (CANCELED before DONE)."""

# BP-25: Idempotency TTL Boundary
BREAKPOINT_BP25_IDEMPOTENCY_TTL = "bp25_idempotency_ttl_attack"
"""Attack idempotency key at TTL boundary (60s)."""

# BP-26: CB-DLQ Disconnect
BREAKPOINT_BP26_CB_DLQ_DISCONNECT = "bp26_cb_dlq_disconnect"
"""Circuit Breaker close doesn't trigger DLQ replay."""

# BP-27: Race Window Amplification
BREAKPOINT_BP27_RACE_AMPLIFICATION = "bp27_race_window_amplify"
"""Confirm and Cancel execute simultaneously with partial success."""

# BP-28: ForensicContext Missing Fields
BREAKPOINT_BP28_FORENSIC_INCOMPLETE = "bp28_forensic_context_incomplete"
"""DLQ entry created but ForensicContext fields empty."""

# BP-29: Point Accumulation Orphan
BREAKPOINT_BP29_POINT_ORPHAN = "bp29_point_accumulation_orphan"
"""Payment succeeds but point task fails. Payment complete, points missing."""

# BP-30: Cache-DB Divergence
BREAKPOINT_BP30_CACHE_DIVERGENCE = "bp30_cache_db_divergence"
"""Cache invalidation fails after DB commit. Stale data served."""


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
    
    # ============================================================
    # PHASE 2 BREAKPOINT DEFINITIONS (BP-21 ~ BP-30)
    # ============================================================
    
    BREAKPOINT_BP21_ORPHAN_PG: {
        "name": BREAKPOINT_BP21_ORPHAN_PG,
        "description": "BP-21: PG succeeds, internal DB fails (orphaned payment)",
        "scenario": "phase2_orphan_pg",
        "action_type": "exception",
        "target_stages": ["stage6", "stage9", "stage12"],
        "default_probability": 0.15,
        "effect": "Creates PG/DB state inconsistency requiring manual reconciliation",
    },
    
    BREAKPOINT_BP22_ROLLBACK_FAILURE: {
        "name": BREAKPOINT_BP22_ROLLBACK_FAILURE,
        "description": "BP-22: Secondary failure during rollback",
        "scenario": "phase2_rollback_failure",
        "action_type": "exception",
        "target_stages": ["stage6", "stage11"],
        "default_probability": 0.2,
        "effect": "Rollback fails, order stuck in inconsistent state",
    },
    
    BREAKPOINT_BP23_SILENT_TASK: {
        "name": BREAKPOINT_BP23_SILENT_TASK,
        "description": "BP-23: Celery task fails without DLQ entry",
        "scenario": "phase2_silent_task",
        "action_type": "exception",
        "target_stages": ["stage9", "stage11"],
        "default_probability": 0.25,
        "effect": "Task silently fails, no recovery signal",
    },
    
    BREAKPOINT_BP24_WEBHOOK_INVERSION: {
        "name": BREAKPOINT_BP24_WEBHOOK_INVERSION,
        "description": "BP-24: Webhook events out of order",
        "scenario": "phase2_webhook_inversion",
        "action_type": "delay",
        "target_stages": ["stage12", "stage13"],
        "default_delay_ms": 2000,
        "effect": "State machine receives events in wrong order",
    },
    
    BREAKPOINT_BP25_IDEMPOTENCY_TTL: {
        "name": BREAKPOINT_BP25_IDEMPOTENCY_TTL,
        "description": "BP-25: Attack idempotency at TTL boundary",
        "scenario": "phase2_idempotency_ttl",
        "action_type": "delay",
        "target_stages": ["stage11", "stage13"],
        "default_delay_ms": 61000,  # Just past 60s TTL
        "effect": "Duplicate payment bypasses idempotency check",
    },
    
    BREAKPOINT_BP26_CB_DLQ_DISCONNECT: {
        "name": BREAKPOINT_BP26_CB_DLQ_DISCONNECT,
        "description": "BP-26: CB close doesn't trigger DLQ replay",
        "scenario": "phase2_cb_dlq_disconnect",
        "action_type": "skip",
        "target_stages": ["stage6", "stage12"],
        "default_probability": 1.0,
        "effect": "DLQ entries stuck after CB close",
    },
    
    BREAKPOINT_BP27_RACE_AMPLIFICATION: {
        "name": BREAKPOINT_BP27_RACE_AMPLIFICATION,
        "description": "BP-27: Confirm+Cancel race window",
        "scenario": "phase2_race_amplify",
        "action_type": "delay",
        "target_stages": ["stage11", "stage12", "stage13"],
        "default_delay_ms": 3000,
        "effect": "Both operations partially succeed",
    },
    
    BREAKPOINT_BP28_FORENSIC_INCOMPLETE: {
        "name": BREAKPOINT_BP28_FORENSIC_INCOMPLETE,
        "description": "BP-28: ForensicContext fields empty",
        "scenario": "phase2_forensic_incomplete",
        "action_type": "skip_fields",
        "target_stages": ["stage6", "stage9"],
        "default_probability": 0.3,
        "effect": "DLQ exists but forensic data missing",
    },
    
    BREAKPOINT_BP29_POINT_ORPHAN: {
        "name": BREAKPOINT_BP29_POINT_ORPHAN,
        "description": "BP-29: Payment OK but points not accumulated",
        "scenario": "phase2_point_orphan",
        "action_type": "exception",
        "target_stages": ["stage9", "stage11"],
        "default_probability": 0.2,
        "effect": "Payment complete, points missing (customer complaint)",
    },
    
    BREAKPOINT_BP30_CACHE_DIVERGENCE: {
        "name": BREAKPOINT_BP30_CACHE_DIVERGENCE,
        "description": "BP-30: Cache stale after DB commit",
        "scenario": "phase2_cache_divergence",
        "action_type": "skip_invalidation",
        "target_stages": ["stage12", "stage13"],
        "default_probability": 0.25,
        "effect": "User sees stale data after successful operation",
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
