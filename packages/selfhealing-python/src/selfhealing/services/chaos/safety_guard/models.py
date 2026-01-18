"""
Safety Guard Models (Data Classes).

Contains SafetyConfig and SafetyCheckResult dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from selfhealing.core.timezone import now


@dataclass
class SafetyConfig:
    """Configuration for safety guard checks."""
    
    # Error budget thresholds
    error_budget_min_percent: float = 20.0
    """Minimum error budget % required (default: 20%)."""
    
    error_budget_warning_percent: float = 50.0
    """Error budget % that triggers warning (default: 50%)."""
    
    # Cooldown between experiments
    experiment_cooldown_minutes: int = 30
    """Minimum minutes between experiments (default: 30)."""
    
    # Health check requirements
    require_healthy_system: bool = True
    """Require system health checks to pass."""
    
    require_no_active_incidents: bool = True
    """Require no active incidents."""
    
    require_no_deployment_freeze: bool = True
    """Require no active deployment freeze."""
    
    # CB Freeze Mode 체크
    require_no_freeze_mode: bool = True
    """Require CB Freeze Mode to be inactive for chaos experiments."""
    
    # Fail-safe behavior
    fail_safe_on_error: bool = True
    """Block experiments if safety checks fail (fail-closed)."""
    
    # Skip conditions
    skip_in_test_environment: bool = True
    """Skip safety checks in test environment."""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "error_budget_min_percent": self.error_budget_min_percent,
            "error_budget_warning_percent": self.error_budget_warning_percent,
            "experiment_cooldown_minutes": self.experiment_cooldown_minutes,
            "require_healthy_system": self.require_healthy_system,
            "require_no_active_incidents": self.require_no_active_incidents,
            "require_no_deployment_freeze": self.require_no_deployment_freeze,
            "require_no_freeze_mode": self.require_no_freeze_mode,
            "fail_safe_on_error": self.fail_safe_on_error,
            "skip_in_test_environment": self.skip_in_test_environment,
        }


@dataclass
class SafetyCheckResult:
    """Result of a safety check evaluation."""
    
    status: str
    """Overall status: safe, warning, blocked, error."""
    
    allowed: bool
    """Whether the experiment is allowed to proceed."""
    
    # Check details
    checks_performed: List[str] = field(default_factory=list)
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    # Block reason (if blocked)
    block_reason: str = ""
    block_message: str = ""
    
    # Error budget details
    error_budget_remaining_percent: float = 100.0
    error_budget_threshold: float = 20.0
    
    # System state
    system_healthy: bool = True
    active_incidents: int = 0
    deployment_freeze_active: bool = False
    kill_switch_active: bool = False
    
    # Emergency mode
    emergency_mode_active: bool = False
    emergency_level: str = "NORMAL"
    
    # Panic threshold
    panic_threshold_triggered: bool = False
    panic_open_rate: float = 0.0
    panic_open_circuits: List[str] = field(default_factory=list)
    
    # CB Freeze Mode
    freeze_mode_active: bool = False
    """CB Freeze Mode 활성화 여부."""
    
    # Timing
    last_experiment_at: str = ""
    cooldown_remaining_minutes: int = 0
    
    # Metadata
    checked_at: str = field(default_factory=lambda: now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status,
            "allowed": self.allowed,
            "checks_performed": self.checks_performed,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "warnings": self.warnings,
            "block_reason": self.block_reason,
            "block_message": self.block_message,
            "error_budget_remaining_percent": self.error_budget_remaining_percent,
            "error_budget_threshold": self.error_budget_threshold,
            "system_healthy": self.system_healthy,
            "active_incidents": self.active_incidents,
            "deployment_freeze_active": self.deployment_freeze_active,
            "kill_switch_active": self.kill_switch_active,
            "emergency_mode_active": self.emergency_mode_active,
            "emergency_level": self.emergency_level,
            "panic_threshold_triggered": self.panic_threshold_triggered,
            "panic_open_rate": self.panic_open_rate,
            "panic_open_circuits": self.panic_open_circuits,
            "freeze_mode_active": self.freeze_mode_active,
            "last_experiment_at": self.last_experiment_at,
            "cooldown_remaining_minutes": self.cooldown_remaining_minutes,
            "checked_at": self.checked_at,
        }
