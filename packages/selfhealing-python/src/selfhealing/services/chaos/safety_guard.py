"""
Safety Guard

Pre-flight safety checks for chaos experiments.
Implements error budget-based gating and system health verification.

Core Principle: "Never sacrifice production stability for testing."

Features:
- Error budget threshold checks (default: 20% minimum)
- System health verification
- Active incident detection
- Kill switch status check
- Deployment freeze detection

Reference: Google SRE Workbook - Alerting on SLOs, AWS FIS Safety Controls
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


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


class BlockReason(str, Enum):
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


# =============================================================================
# Data Classes
# =============================================================================


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
            "last_experiment_at": self.last_experiment_at,
            "cooldown_remaining_minutes": self.cooldown_remaining_minutes,
            "checked_at": self.checked_at,
        }


# =============================================================================
# Safety Guard
# =============================================================================


class SafetyGuard:
    """
    Pre-flight safety checks for chaos experiments.
    
    Implements a multi-layer safety check system:
    1. Error Budget Check - Primary gate
    2. System Health Check - Secondary gate
    3. Incident Detection - Tertiary gate
    4. Deployment Freeze - Policy gate
    5. Kill Switch - Override gate
    6. Cooldown - Rate limiting
    
    Usage:
        guard = get_safety_guard()
        
        result = guard.check(experiment_id="chaos-abc123")
        
        if not result.allowed:
            if result.block_reason == BlockReason.LOW_ERROR_BUDGET.value:
                notify("ChaosSkippedDueToLowBudget", result)
            return
        
        # Proceed with experiment
    """
    
    def __init__(self, config: Optional[SafetyConfig] = None):
        """Initialize SafetyGuard."""
        self._config = config or SafetyConfig()
        self._lock = threading.RLock()
        
        # State tracking
        self._last_experiment_at: Optional[datetime] = None
        self._manual_blocks: Dict[str, str] = {}  # experiment_id -> reason
        self._global_block: bool = False
        self._global_block_reason: str = ""
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self) -> SafetyConfig:
        """Get current configuration."""
        return self._config
    
    def update_config(self, **kwargs) -> SafetyConfig:
        """
        Update configuration.
        
        Args:
            **kwargs: Config fields to update
            
        Returns:
            Updated config
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[SafetyGuard] Updated config.{key} = {value}")
            
            self._persist_config()
            return self._config
    
    def _persist_config(self) -> None:
        """Persist configuration to storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(safety_guard_config=self._config.to_dict())
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not persist config: {e}")
    
    def _load_config(self) -> None:
        """Load configuration from storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            config = manager.get_chaos_config()
            config_data = config.get("safety_guard_config", {})
            
            if config_data:
                for key, value in config_data.items():
                    if hasattr(self._config, key):
                        setattr(self._config, key, value)
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not load config: {e}")
    
    # =========================================================================
    # Main Safety Check
    # =========================================================================
    
    def check(
        self,
        experiment_id: str = "",
        target_service: str = "",
        force: bool = False,
    ) -> SafetyCheckResult:
        """
        Perform comprehensive safety checks.
        
        Args:
            experiment_id: Experiment ID for tracking
            target_service: Target service name
            force: Skip non-critical checks (for testing)
            
        Returns:
            SafetyCheckResult with detailed check results
        """
        result = SafetyCheckResult(
            status=SafetyStatus.SAFE.value,
            allowed=True,
        )
        
        try:
            with self._lock:
                # 1. Check global block
                result.checks_performed.append("global_block")
                if self._global_block:
                    result.status = SafetyStatus.BLOCKED.value
                    result.allowed = False
                    result.block_reason = BlockReason.MANUAL_BLOCK.value
                    result.block_message = self._global_block_reason
                    result.checks_failed.append("global_block")
                    return result
                result.checks_passed.append("global_block")
                
                # 2. Check kill switch
                result.checks_performed.append("kill_switch")
                kill_switch_result = self._check_kill_switch()
                if kill_switch_result:
                    result.kill_switch_active = True
                    result.status = SafetyStatus.BLOCKED.value
                    result.allowed = False
                    result.block_reason = BlockReason.KILL_SWITCH_ACTIVE.value
                    result.block_message = "Kill switch is active"
                    result.checks_failed.append("kill_switch")
                    return result
                result.checks_passed.append("kill_switch")
                
                # 3. Check error budget (CRITICAL)
                result.checks_performed.append("error_budget")
                budget_result = self._check_error_budget()
                result.error_budget_remaining_percent = budget_result["remaining_percent"]
                result.error_budget_threshold = self._config.error_budget_min_percent
                
                if budget_result["remaining_percent"] < self._config.error_budget_min_percent:
                    result.status = SafetyStatus.BLOCKED.value
                    result.allowed = False
                    result.block_reason = BlockReason.LOW_ERROR_BUDGET.value
                    result.block_message = (
                        f"Error budget at {budget_result['remaining_percent']:.1f}% "
                        f"(minimum: {self._config.error_budget_min_percent}%)"
                    )
                    result.checks_failed.append("error_budget")
                    
                    # Send notification
                    self._notify_low_budget(experiment_id, budget_result)
                    return result
                
                if budget_result["remaining_percent"] < self._config.error_budget_warning_percent:
                    result.warnings.append(
                        f"Error budget low: {budget_result['remaining_percent']:.1f}%"
                    )
                    result.status = SafetyStatus.WARNING.value
                
                result.checks_passed.append("error_budget")
                
                # 4. Check system health
                if self._config.require_healthy_system and not force:
                    result.checks_performed.append("system_health")
                    health_result = self._check_system_health()
                    result.system_healthy = health_result["healthy"]
                    
                    if not health_result["healthy"]:
                        result.status = SafetyStatus.BLOCKED.value
                        result.allowed = False
                        result.block_reason = BlockReason.UNHEALTHY_SYSTEM.value
                        result.block_message = health_result.get("message", "System unhealthy")
                        result.checks_failed.append("system_health")
                        return result
                    result.checks_passed.append("system_health")
                
                # 5. Check active incidents
                if self._config.require_no_active_incidents and not force:
                    result.checks_performed.append("active_incidents")
                    incident_result = self._check_active_incidents()
                    result.active_incidents = incident_result["count"]
                    
                    if incident_result["count"] > 0:
                        result.status = SafetyStatus.BLOCKED.value
                        result.allowed = False
                        result.block_reason = BlockReason.ACTIVE_INCIDENT.value
                        result.block_message = f"{incident_result['count']} active incident(s)"
                        result.checks_failed.append("active_incidents")
                        return result
                    result.checks_passed.append("active_incidents")
                
                # 6. Check deployment freeze
                if self._config.require_no_deployment_freeze and not force:
                    result.checks_performed.append("deployment_freeze")
                    freeze_result = self._check_deployment_freeze()
                    result.deployment_freeze_active = freeze_result["active"]
                    
                    if freeze_result["active"]:
                        result.status = SafetyStatus.BLOCKED.value
                        result.allowed = False
                        result.block_reason = BlockReason.DEPLOYMENT_FREEZE.value
                        result.block_message = "Deployment freeze is active"
                        result.checks_failed.append("deployment_freeze")
                        return result
                    result.checks_passed.append("deployment_freeze")
                
                # 7. Check cooldown
                result.checks_performed.append("cooldown")
                cooldown_result = self._check_cooldown()
                result.last_experiment_at = cooldown_result.get("last_experiment_at", "")
                result.cooldown_remaining_minutes = cooldown_result.get("remaining_minutes", 0)
                
                if cooldown_result.get("in_cooldown", False):
                    result.warnings.append(
                        f"Cooldown active: {cooldown_result['remaining_minutes']} minutes remaining"
                    )
                    if result.status == SafetyStatus.SAFE.value:
                        result.status = SafetyStatus.WARNING.value
                
                result.checks_passed.append("cooldown")
                
                # All checks passed
                if result.status == SafetyStatus.SAFE.value:
                    logger.info(f"[SafetyGuard] All checks passed for {experiment_id}")
                else:
                    logger.warning(
                        f"[SafetyGuard] Checks passed with warnings for {experiment_id}: "
                        f"{result.warnings}"
                    )
                
                return result
                
        except Exception as e:
            logger.exception(f"[SafetyGuard] Error during safety check: {e}")
            
            if self._config.fail_safe_on_error:
                return SafetyCheckResult(
                    status=SafetyStatus.ERROR.value,
                    allowed=False,
                    block_reason="safety_check_error",
                    block_message=f"Safety check failed: {e}",
                )
            else:
                # Fail-open (not recommended for production)
                return SafetyCheckResult(
                    status=SafetyStatus.WARNING.value,
                    allowed=True,
                    warnings=[f"Safety check error (fail-open): {e}"],
                )
    
    # =========================================================================
    # Individual Checks
    # =========================================================================
    
    def _check_error_budget(self) -> Dict[str, Any]:
        """Check current error budget status."""
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            service = get_error_budget_service()
            status = service.get_status()
            
            return {
                "remaining_percent": status.get("remaining_percent", 100.0),
                "consumed_percent": 100.0 - status.get("remaining_percent", 100.0),
                "is_healthy": status.get("is_healthy", True),
            }
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check error budget: {e}")
            # Fail-safe: assume budget is available
            return {"remaining_percent": 100.0, "consumed_percent": 0.0, "is_healthy": True}
    
    def _check_kill_switch(self) -> bool:
        """Check if kill switch is active."""
        try:
            from selfhealing.services.system_control import get_system_control
            
            control = get_system_control()
            return not control.is_selfhealing_enabled()
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check kill switch: {e}")
            return False
    
    def _check_system_health(self) -> Dict[str, Any]:
        """Check overall system health."""
        try:
            from selfhealing.services.health_check import get_health_check_service
            
            service = get_health_check_service()
            status = service.check_health()
            
            return {
                "healthy": status.is_healthy,
                "message": status.message if hasattr(status, 'message') else "",
            }
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check system health: {e}")
            # Fail-safe: assume healthy
            return {"healthy": True, "message": ""}
    
    def _check_active_incidents(self) -> Dict[str, Any]:
        """Check for active incidents."""
        try:
            from selfhealing.services.dlq_service import get_dlq_service
            
            service = get_dlq_service()
            # Consider DLQ items with status 'pending' as active incidents
            pending_count = service.get_pending_count()
            
            # Only block if there are significant pending items
            return {
                "count": pending_count if pending_count > 10 else 0,
                "items": [],
            }
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check active incidents: {e}")
            return {"count": 0, "items": []}
    
    def _check_deployment_freeze(self) -> Dict[str, Any]:
        """Check if deployment freeze is active."""
        try:
            from selfhealing.services.error_budget_service import get_error_budget_service
            
            service = get_error_budget_service()
            verdict = service.get_deployment_verdict()
            
            freeze_active = verdict.get("status") in ("freeze_recommended", "warning")
            
            return {
                "active": freeze_active,
                "reason": verdict.get("reason", ""),
            }
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check deployment freeze: {e}")
            return {"active": False, "reason": ""}
    
    def _check_cooldown(self) -> Dict[str, Any]:
        """Check if cooldown period is active."""
        if self._last_experiment_at is None:
            return {
                "in_cooldown": False,
                "remaining_minutes": 0,
                "last_experiment_at": "",
            }
        
        cooldown_end = self._last_experiment_at + timedelta(
            minutes=self._config.experiment_cooldown_minutes
        )
        
        current = now()
        if current < cooldown_end:
            remaining = (cooldown_end - current).total_seconds() / 60
            return {
                "in_cooldown": True,
                "remaining_minutes": int(remaining),
                "last_experiment_at": self._last_experiment_at.isoformat(),
            }
        
        return {
            "in_cooldown": False,
            "remaining_minutes": 0,
            "last_experiment_at": self._last_experiment_at.isoformat(),
        }
    
    # =========================================================================
    # Manual Controls
    # =========================================================================
    
    def block_globally(self, reason: str) -> None:
        """
        Block all chaos experiments globally.
        
        Args:
            reason: Reason for global block
        """
        with self._lock:
            self._global_block = True
            self._global_block_reason = reason
            logger.warning(f"[SafetyGuard] Global block activated: {reason}")
    
    def unblock_globally(self) -> None:
        """Remove global block."""
        with self._lock:
            self._global_block = False
            self._global_block_reason = ""
            logger.info("[SafetyGuard] Global block removed")
    
    def is_globally_blocked(self) -> tuple[bool, str]:
        """Check if globally blocked."""
        return self._global_block, self._global_block_reason
    
    def record_experiment_completed(self) -> None:
        """Record that an experiment just completed (for cooldown tracking)."""
        with self._lock:
            self._last_experiment_at = now()
    
    # =========================================================================
    # Notifications
    # =========================================================================
    
    def _notify_low_budget(self, experiment_id: str, budget_result: Dict[str, Any]) -> None:
        """Send notification for low error budget blocking experiment."""
        try:
            from selfhealing.adapters.alert import get_alert_adapter
            
            adapter = get_alert_adapter()
            if adapter:
                adapter.alert(
                    severity="warning",
                    title="ChaosSkippedDueToLowBudget",
                    message=(
                        f"Chaos experiment {experiment_id} was automatically skipped.\n\n"
                        f"Error budget remaining: {budget_result['remaining_percent']:.1f}%\n"
                        f"Minimum required: {self._config.error_budget_min_percent}%\n\n"
                        "Increase error budget or adjust threshold to enable chaos experiments."
                    ),
                    tags=["chaos", "safety", "error_budget"],
                )
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not send notification: {e}")


# =============================================================================
# Singleton
# =============================================================================


_safety_guard: Optional[SafetyGuard] = None
_guard_lock = threading.Lock()


def get_safety_guard() -> SafetyGuard:
    """Get the singleton SafetyGuard instance."""
    global _safety_guard
    
    if _safety_guard is None:
        with _guard_lock:
            if _safety_guard is None:
                _safety_guard = SafetyGuard()
                _safety_guard._load_config()
    
    return _safety_guard


def reset_safety_guard() -> None:
    """Reset the singleton (for testing)."""
    global _safety_guard
    with _guard_lock:
        _safety_guard = None
