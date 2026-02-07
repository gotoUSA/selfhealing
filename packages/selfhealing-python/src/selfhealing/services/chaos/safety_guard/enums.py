"""
Safety Guard Enums.

Contains SafetyStatus and ChaosBlockReason enums.
"""

from enum import Enum


class SafetyStatus(str, Enum):
    """Safety check status."""

    SAFE = "safe"
    """All checks passed. Safe to proceed."""

    WARNING = "warning"
    """Some checks raised warnings. Proceed with caution."""

    BLOCKED = "blocked"
    """Critical checks failed. Experiment blocked."""

    ERROR = "error"
    """Could not complete safety checks. Fail-safe: BLOCKED."""


class ChaosBlockReason(str, Enum):
    """Reasons for blocking an experiment."""

    LOW_ERROR_BUDGET = "low_error_budget"
    """Error budget below threshold."""

    ACTIVE_INCIDENT = "active_incident"
    """Active incident in progress."""

    KILL_SWITCH_ACTIVE = "kill_switch_active"
    """Kill switch is currently active."""

    DEPLOYMENT_FREEZE = "deployment_freeze"
    """Deployment freeze is active."""

    UNHEALTHY_SYSTEM = "unhealthy_system"
    """System health checks failed."""

    RECENT_EXPERIMENT = "recent_experiment"
    """Too soon after previous experiment."""

    BLAST_RADIUS_EXCEEDED = "blast_radius_exceeded"
    """Blast radius policy violated."""

    MANUAL_BLOCK = "manual_block"
    """Manually blocked by operator."""

    EMERGENCY_MODE_ACTIVE = "emergency_mode_active"
    """Emergency mode is active (LEVEL_2+)."""

    # Panic Threshold 연동
    PANIC_THRESHOLD_TRIGGERED = "panic_threshold_triggered"
    """Panic Threshold 발동 (70%+ CB OPEN)."""

    # Chaos Budget 연동
    CHAOS_BUDGET_EXCEEDED = "chaos_budget_exceeded"
    """월간 카오스 실험 예산 초과."""

    # CB Freeze Mode 연동
    CB_FREEZE_MODE_ACTIVE = "cb_freeze_mode_active"
    """Circuit Breaker Freeze Mode 활성화 - 모든 카오스 실험 차단."""
