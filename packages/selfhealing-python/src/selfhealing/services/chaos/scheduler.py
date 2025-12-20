"""
Chaos Scheduler Service

Celery Beat-based scheduler for autonomous chaos experiments.
Implements scheduled execution with comprehensive pre-flight checks.

Features:
- Scheduled experiment execution (Celery Beat)
- Pre-flight safety checks (SafetyGuard integration)
- Blast radius enforcement
- Approval workflow for high-risk experiments
- Kill switch integration
- Audit trail recording

Reference: Netflix ChAP, Gremlin scheduled attacks, AWS FIS experiments
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from selfhealing.core.timezone import now

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ExperimentApprovalStatus(str, Enum):
    """Approval status for scheduled experiments."""
    
    AUTO_APPROVED = "auto_approved"
    """Automatically approved (low risk)."""
    
    PENDING = "pending"
    """Awaiting manual approval."""
    
    APPROVED = "approved"
    """Manually approved."""
    
    DENIED = "denied"
    """Denied by operator."""
    
    EXPIRED = "expired"
    """Approval request expired."""


class ScheduleType(str, Enum):
    """Types of experiment schedules."""
    
    ONCE = "once"
    """Run once at specified time."""
    
    DAILY = "daily"
    """Run daily at specified time."""
    
    WEEKLY = "weekly"
    """Run weekly on specified day/time."""
    
    CRON = "cron"
    """Custom cron expression."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ScheduledExperiment:
    """A scheduled chaos experiment."""
    
    id: str = field(default_factory=lambda: f"sched-{uuid.uuid4().hex[:12]}")
    
    # Experiment definition
    experiment_type: str = ""
    experiment_config: Dict[str, Any] = field(default_factory=dict)
    
    # Target
    target_service: str = ""
    target_domain: str = ""
    blast_radius: str = "instance"
    
    # Schedule
    schedule_type: str = ScheduleType.DAILY.value
    schedule_time: str = "02:00"  # HH:MM UTC
    schedule_day: int = 0  # 0=Monday for weekly
    schedule_cron: str = ""  # For custom cron
    
    # Approval
    approval_status: str = ExperimentApprovalStatus.PENDING.value
    approved_by: str = ""
    approved_at: str = ""
    
    # Execution tracking
    enabled: bool = True
    last_run_at: str = ""
    last_run_result: str = ""
    next_run_at: str = ""
    run_count: int = 0
    
    # Metadata
    created_by: str = ""
    created_at: str = field(default_factory=lambda: now().isoformat())
    description: str = ""
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "experiment_type": self.experiment_type,
            "experiment_config": self.experiment_config,
            "target_service": self.target_service,
            "target_domain": self.target_domain,
            "blast_radius": self.blast_radius,
            "schedule_type": self.schedule_type,
            "schedule_time": self.schedule_time,
            "schedule_day": self.schedule_day,
            "schedule_cron": self.schedule_cron,
            "approval_status": self.approval_status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "last_run_result": self.last_run_result,
            "next_run_at": self.next_run_at,
            "run_count": self.run_count,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "description": self.description,
            "tags": self.tags,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledExperiment":
        """Create from dictionary."""
        return cls(
            id=data.get("id", ""),
            experiment_type=data.get("experiment_type", ""),
            experiment_config=data.get("experiment_config", {}),
            target_service=data.get("target_service", ""),
            target_domain=data.get("target_domain", ""),
            blast_radius=data.get("blast_radius", "instance"),
            schedule_type=data.get("schedule_type", ScheduleType.DAILY.value),
            schedule_time=data.get("schedule_time", "02:00"),
            schedule_day=data.get("schedule_day", 0),
            schedule_cron=data.get("schedule_cron", ""),
            approval_status=data.get("approval_status", ExperimentApprovalStatus.PENDING.value),
            approved_by=data.get("approved_by", ""),
            approved_at=data.get("approved_at", ""),
            enabled=data.get("enabled", True),
            last_run_at=data.get("last_run_at", ""),
            last_run_result=data.get("last_run_result", ""),
            next_run_at=data.get("next_run_at", ""),
            run_count=data.get("run_count", 0),
            created_by=data.get("created_by", ""),
            created_at=data.get("created_at", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
        )


@dataclass
class SchedulerConfig:
    """Configuration for the chaos scheduler."""
    
    # Global enable/disable
    enabled: bool = True
    """Master switch for scheduler."""
    
    # Default schedule window (UTC hours)
    default_schedule_hour_start: int = 2
    """Default start hour for scheduled experiments (2 AM UTC)."""
    
    default_schedule_hour_end: int = 6
    """Default end hour for scheduled experiments (6 AM UTC)."""
    
    # Auto-approval settings
    auto_approve_instance_level: bool = True
    """Auto-approve INSTANCE level experiments."""
    
    auto_approve_service_level: bool = False
    """Auto-approve SERVICE level experiments."""
    
    # Limits
    max_concurrent_experiments: int = 3
    """Maximum concurrent experiments."""
    
    max_experiments_per_day: int = 10
    """Maximum experiments per day."""
    
    # Cooldown
    min_interval_between_experiments_minutes: int = 30
    """Minimum minutes between experiments."""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "enabled": self.enabled,
            "default_schedule_hour_start": self.default_schedule_hour_start,
            "default_schedule_hour_end": self.default_schedule_hour_end,
            "auto_approve_instance_level": self.auto_approve_instance_level,
            "auto_approve_service_level": self.auto_approve_service_level,
            "max_concurrent_experiments": self.max_concurrent_experiments,
            "max_experiments_per_day": self.max_experiments_per_day,
            "min_interval_between_experiments_minutes": self.min_interval_between_experiments_minutes,
        }


@dataclass
class ExecutionResult:
    """Result of a scheduled experiment execution."""
    
    schedule_id: str
    experiment_id: str
    status: str
    
    # Timing
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    
    # Results
    success: bool = False
    skipped: bool = False
    skip_reason: str = ""
    
    # Experiment result (if executed)
    experiment_result: Dict[str, Any] = field(default_factory=dict)
    
    # Errors
    error_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "schedule_id": self.schedule_id,
            "experiment_id": self.experiment_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "experiment_result": self.experiment_result,
            "error_message": self.error_message,
        }


# =============================================================================
# Chaos Scheduler Service
# =============================================================================


class ChaosSchedulerService:
    """
    Manages scheduled chaos experiments.
    
    Responsibilities:
    1. CRUD for scheduled experiments
    2. Pre-flight safety checks before execution
    3. Blast radius validation
    4. Approval workflow
    5. Kill switch integration
    6. Execution tracking and reporting
    
    Usage:
        scheduler = get_chaos_scheduler()
        
        # Create a scheduled experiment
        schedule = scheduler.create_schedule(
            experiment_type="latency_injection",
            target_service="payment",
            schedule_type=ScheduleType.DAILY,
            schedule_time="03:00",
        )
        
        # The Celery Beat task will execute it automatically
        
        # Or trigger manually
        result = scheduler.execute_now(schedule.id)
    """
    
    def __init__(self, config: Optional[SchedulerConfig] = None):
        """Initialize ChaosSchedulerService."""
        self._config = config or SchedulerConfig()
        self._lock = threading.RLock()
        
        # In-memory storage (use StateBackend in production)
        self._schedules: Dict[str, ScheduledExperiment] = {}
        self._execution_history: List[ExecutionResult] = []
        
        # Currently running experiments
        self._running_experiments: Dict[str, str] = {}  # schedule_id -> experiment_id
        
        # Load from persistent storage
        self._load_schedules()
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def get_config(self) -> SchedulerConfig:
        """Get current configuration."""
        return self._config
    
    def update_config(self, **kwargs) -> SchedulerConfig:
        """
        Update scheduler configuration.
        
        Args:
            **kwargs: Config fields to update
            
        Returns:
            Updated config
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[ChaosScheduler] Updated config.{key} = {value}")
            
            self._persist_config()
            return self._config
    
    def _persist_config(self) -> None:
        """Persist configuration to storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            manager.update_chaos_config(scheduler_config=self._config.to_dict())
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Could not persist config: {e}")
    
    def _load_config(self) -> None:
        """Load configuration from storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            config = manager.get_chaos_config()
            config_data = config.get("scheduler_config", {})
            
            if config_data:
                for key, value in config_data.items():
                    if hasattr(self._config, key):
                        setattr(self._config, key, value)
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Could not load config: {e}")
    
    # =========================================================================
    # Schedule CRUD
    # =========================================================================
    
    def create_schedule(
        self,
        experiment_type: str,
        target_service: str,
        target_domain: str = "",
        blast_radius: str = "instance",
        schedule_type: str = ScheduleType.DAILY.value,
        schedule_time: str = "02:00",
        schedule_day: int = 0,
        schedule_cron: str = "",
        experiment_config: Optional[Dict[str, Any]] = None,
        description: str = "",
        tags: Optional[List[str]] = None,
        created_by: str = "",
    ) -> ScheduledExperiment:
        """
        Create a new scheduled experiment.
        
        Args:
            experiment_type: Type of experiment
            target_service: Target service name
            target_domain: Target domain (optional)
            blast_radius: Blast radius level
            schedule_type: Schedule type (once, daily, weekly, cron)
            schedule_time: Time in HH:MM format (UTC)
            schedule_day: Day for weekly (0=Monday)
            schedule_cron: Cron expression for custom
            experiment_config: Additional experiment configuration
            description: Human-readable description
            tags: Tags for filtering
            created_by: Creator identity
            
        Returns:
            Created ScheduledExperiment
        """
        from .blast_radius import BlastRadius, get_blast_radius_manager
        
        with self._lock:
            schedule = ScheduledExperiment(
                experiment_type=experiment_type,
                experiment_config=experiment_config or {},
                target_service=target_service,
                target_domain=target_domain,
                blast_radius=blast_radius,
                schedule_type=schedule_type,
                schedule_time=schedule_time,
                schedule_day=schedule_day,
                schedule_cron=schedule_cron,
                description=description,
                tags=tags or [],
                created_by=created_by,
            )
            
            # Determine approval status
            blast_radius_enum = BlastRadius(blast_radius)
            
            if blast_radius_enum == BlastRadius.INSTANCE and self._config.auto_approve_instance_level:
                schedule.approval_status = ExperimentApprovalStatus.AUTO_APPROVED.value
            elif blast_radius_enum == BlastRadius.SERVICE and self._config.auto_approve_service_level:
                schedule.approval_status = ExperimentApprovalStatus.AUTO_APPROVED.value
            elif blast_radius_enum == BlastRadius.REGION:
                # REGION always requires approval
                schedule.approval_status = ExperimentApprovalStatus.PENDING.value
                
                # Request approval from BlastRadiusManager
                manager = get_blast_radius_manager()
                manager.request_approval(
                    experiment_id=schedule.id,
                    blast_radius=blast_radius_enum,
                    target_service=target_service,
                    target_domain=target_domain,
                    requested_by=created_by,
                    reason=description,
                )
            else:
                schedule.approval_status = ExperimentApprovalStatus.PENDING.value
            
            # Calculate next run time
            schedule.next_run_at = self._calculate_next_run(schedule).isoformat()
            
            # Store
            self._schedules[schedule.id] = schedule
            self._persist_schedules()
            
            logger.info(
                f"[ChaosScheduler] Created schedule {schedule.id} for {experiment_type} "
                f"targeting {target_service} ({blast_radius})"
            )
            
            return schedule
    
    def get_schedule(self, schedule_id: str) -> Optional[ScheduledExperiment]:
        """Get a schedule by ID."""
        return self._schedules.get(schedule_id)
    
    def list_schedules(
        self,
        enabled_only: bool = False,
        pending_approval_only: bool = False,
        target_service: Optional[str] = None,
    ) -> List[ScheduledExperiment]:
        """
        List schedules with optional filters.
        
        Args:
            enabled_only: Only return enabled schedules
            pending_approval_only: Only return pending approval
            target_service: Filter by target service
            
        Returns:
            List of matching schedules
        """
        with self._lock:
            schedules = list(self._schedules.values())
            
            if enabled_only:
                schedules = [s for s in schedules if s.enabled]
            
            if pending_approval_only:
                schedules = [
                    s for s in schedules
                    if s.approval_status == ExperimentApprovalStatus.PENDING.value
                ]
            
            if target_service:
                schedules = [s for s in schedules if s.target_service == target_service]
            
            return schedules
    
    def update_schedule(self, schedule_id: str, **kwargs) -> Optional[ScheduledExperiment]:
        """
        Update a schedule.
        
        Args:
            schedule_id: Schedule ID
            **kwargs: Fields to update
            
        Returns:
            Updated schedule or None if not found
        """
        with self._lock:
            if schedule_id not in self._schedules:
                return None
            
            schedule = self._schedules[schedule_id]
            
            for key, value in kwargs.items():
                if hasattr(schedule, key) and key not in ("id", "created_at"):
                    setattr(schedule, key, value)
            
            # Recalculate next run if schedule changed
            if any(k in kwargs for k in ("schedule_type", "schedule_time", "schedule_day", "schedule_cron")):
                schedule.next_run_at = self._calculate_next_run(schedule).isoformat()
            
            self._persist_schedules()
            
            logger.info(f"[ChaosScheduler] Updated schedule {schedule_id}")
            return schedule
    
    def delete_schedule(self, schedule_id: str) -> bool:
        """
        Delete a schedule.
        
        Args:
            schedule_id: Schedule ID
            
        Returns:
            True if deleted
        """
        with self._lock:
            if schedule_id in self._schedules:
                del self._schedules[schedule_id]
                self._persist_schedules()
                logger.info(f"[ChaosScheduler] Deleted schedule {schedule_id}")
                return True
            return False
    
    def enable_schedule(self, schedule_id: str) -> Optional[ScheduledExperiment]:
        """Enable a schedule."""
        return self.update_schedule(schedule_id, enabled=True)
    
    def disable_schedule(self, schedule_id: str) -> Optional[ScheduledExperiment]:
        """Disable a schedule."""
        return self.update_schedule(schedule_id, enabled=False)
    
    # =========================================================================
    # Approval Workflow
    # =========================================================================
    
    def approve_schedule(self, schedule_id: str, approved_by: str) -> Optional[ScheduledExperiment]:
        """
        Approve a pending schedule.
        
        Args:
            schedule_id: Schedule ID
            approved_by: Approver identity
            
        Returns:
            Updated schedule or None
        """
        with self._lock:
            if schedule_id not in self._schedules:
                return None
            
            schedule = self._schedules[schedule_id]
            schedule.approval_status = ExperimentApprovalStatus.APPROVED.value
            schedule.approved_by = approved_by
            schedule.approved_at = now().isoformat()
            
            self._persist_schedules()
            
            logger.info(f"[ChaosScheduler] Schedule {schedule_id} approved by {approved_by}")
            
            # Record audit
            self._record_audit("schedule_approved", {
                "schedule_id": schedule_id,
                "approved_by": approved_by,
            })
            
            return schedule
    
    def deny_schedule(self, schedule_id: str, denied_by: str, reason: str = "") -> Optional[ScheduledExperiment]:
        """
        Deny a pending schedule.
        
        Args:
            schedule_id: Schedule ID
            denied_by: Denier identity
            reason: Reason for denial
            
        Returns:
            Updated schedule or None
        """
        with self._lock:
            if schedule_id not in self._schedules:
                return None
            
            schedule = self._schedules[schedule_id]
            schedule.approval_status = ExperimentApprovalStatus.DENIED.value
            schedule.approved_by = denied_by
            schedule.approved_at = now().isoformat()
            schedule.enabled = False
            
            self._persist_schedules()
            
            logger.info(f"[ChaosScheduler] Schedule {schedule_id} denied by {denied_by}: {reason}")
            
            return schedule
    
    # =========================================================================
    # Execution
    # =========================================================================
    
    def execute_now(self, schedule_id: str, force: bool = False) -> ExecutionResult:
        """
        Execute a scheduled experiment immediately.
        
        Args:
            schedule_id: Schedule ID to execute
            force: Skip safety checks (for testing)
            
        Returns:
            ExecutionResult with outcome
        """
        from .experiments import create_experiment, ExperimentConfig, ExperimentResult
        from .safety_guard import get_safety_guard
        from .blast_radius import get_blast_radius_manager, BlastRadius
        
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return ExecutionResult(
                schedule_id=schedule_id,
                experiment_id="",
                status="error",
                error_message=f"Schedule not found: {schedule_id}",
            )
        
        experiment_id = f"chaos-{uuid.uuid4().hex[:12]}"
        started_at = now()
        
        try:
            # 1. Check if scheduler is enabled
            if not self._config.enabled:
                return ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status="skipped",
                    skipped=True,
                    skip_reason="Scheduler is disabled",
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                )
            
            # 2. Check if schedule is enabled
            if not schedule.enabled:
                return ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status="skipped",
                    skipped=True,
                    skip_reason="Schedule is disabled",
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                )
            
            # 3. Check approval status
            if schedule.approval_status not in (
                ExperimentApprovalStatus.AUTO_APPROVED.value,
                ExperimentApprovalStatus.APPROVED.value,
            ):
                return ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status="skipped",
                    skipped=True,
                    skip_reason=f"Not approved: {schedule.approval_status}",
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                )
            
            # 4. Safety checks (Pre-flight)
            if not force:
                guard = get_safety_guard()
                safety_result = guard.check(
                    experiment_id=experiment_id,
                    target_service=schedule.target_service,
                )
                
                if not safety_result.allowed:
                    # Record for audit
                    self._record_audit("experiment_blocked_safety", {
                        "schedule_id": schedule_id,
                        "experiment_id": experiment_id,
                        "block_reason": safety_result.block_reason,
                        "block_message": safety_result.block_message,
                    })
                    
                    return ExecutionResult(
                        schedule_id=schedule_id,
                        experiment_id=experiment_id,
                        status="skipped",
                        skipped=True,
                        skip_reason=safety_result.block_message,
                        started_at=started_at.isoformat(),
                        completed_at=now().isoformat(),
                    )
            
            # 5. Blast radius check
            if not force:
                br_manager = get_blast_radius_manager()
                br_result = br_manager.check(
                    blast_radius=schedule.blast_radius,
                    target_service=schedule.target_service,
                    target_domain=schedule.target_domain,
                    experiment_id=experiment_id,
                )
                
                if not br_result.allowed:
                    return ExecutionResult(
                        schedule_id=schedule_id,
                        experiment_id=experiment_id,
                        status="skipped",
                        skipped=True,
                        skip_reason=f"Blast radius check failed: {br_result.violations}",
                        started_at=started_at.isoformat(),
                        completed_at=now().isoformat(),
                    )
                
                # Register experiment
                br_manager.register_experiment(
                    experiment_id=experiment_id,
                    blast_radius=schedule.blast_radius,
                    target_service=schedule.target_service,
                    target_domain=schedule.target_domain,
                )
            
            # 6. Create and execute experiment
            with self._lock:
                self._running_experiments[schedule_id] = experiment_id
            
            try:
                config = ExperimentConfig(
                    target_service=schedule.target_service,
                    target_domain=schedule.target_domain,
                    **schedule.experiment_config,
                )
                
                experiment = create_experiment(
                    experiment_type=schedule.experiment_type,
                    config=config,
                )
                experiment.experiment_id = experiment_id
                
                # Execute
                result = experiment.execute()
                
                # Update schedule
                with self._lock:
                    schedule.last_run_at = now().isoformat()
                    schedule.last_run_result = result.status
                    schedule.run_count += 1
                    schedule.next_run_at = self._calculate_next_run(schedule).isoformat()
                    self._persist_schedules()
                
                # Record safety guard cooldown
                if not force:
                    guard.record_experiment_completed()
                
                execution_result = ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status=result.status,
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                    duration_seconds=(now() - started_at).total_seconds(),
                    success=result.status == "completed",
                    experiment_result=result.to_dict(),
                )
                
                # Store in history
                self._execution_history.append(execution_result)
                if len(self._execution_history) > 1000:
                    self._execution_history = self._execution_history[-1000:]
                
                return execution_result
                
            finally:
                # Cleanup
                with self._lock:
                    if schedule_id in self._running_experiments:
                        del self._running_experiments[schedule_id]
                
                if not force:
                    br_manager.unregister_experiment(experiment_id)
        
        except Exception as e:
            logger.exception(f"[ChaosScheduler] Error executing {schedule_id}: {e}")
            
            return ExecutionResult(
                schedule_id=schedule_id,
                experiment_id=experiment_id,
                status="error",
                started_at=started_at.isoformat(),
                completed_at=now().isoformat(),
                duration_seconds=(now() - started_at).total_seconds(),
                error_message=str(e),
            )
    
    def execute_due_schedules(self) -> List[ExecutionResult]:
        """
        Execute all schedules that are due.
        
        Called by Celery Beat task.
        
        Returns:
            List of execution results
        """
        results = []
        current = now()
        
        with self._lock:
            due_schedules = [
                s for s in self._schedules.values()
                if s.enabled and s.next_run_at and datetime.fromisoformat(s.next_run_at) <= current
            ]
        
        for schedule in due_schedules:
            result = self.execute_now(schedule.id)
            results.append(result)
        
        return results
    
    # =========================================================================
    # Kill Switch
    # =========================================================================
    
    def kill_experiment(self, experiment_id: str, reason: str = "") -> bool:
        """
        Kill a running experiment.
        
        Args:
            experiment_id: Experiment ID to kill
            reason: Reason for killing
            
        Returns:
            True if kill signal sent
        """
        from .experiments import ChaosExperiment
        
        logger.warning(f"[ChaosScheduler] Kill requested for {experiment_id}: {reason}")
        
        # Record audit
        self._record_audit("experiment_killed", {
            "experiment_id": experiment_id,
            "reason": reason,
        })
        
        # The experiment's kill switch is checked in monitor loop
        # For now, we set a flag that the experiment should check
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            backend.set(f"chaos:kill:{experiment_id}", {"killed": True, "reason": reason})
            return True
        except Exception as e:
            logger.error(f"[ChaosScheduler] Could not set kill flag: {e}")
            return False
    
    def kill_all(self, reason: str = "") -> int:
        """
        Kill all running experiments.
        
        Args:
            reason: Reason for killing
            
        Returns:
            Number of experiments killed
        """
        count = 0
        with self._lock:
            for schedule_id, experiment_id in list(self._running_experiments.items()):
                if self.kill_experiment(experiment_id, reason):
                    count += 1
        
        return count
    
    # =========================================================================
    # History & Reporting
    # =========================================================================
    
    def get_execution_history(
        self,
        schedule_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[ExecutionResult]:
        """Get execution history."""
        history = self._execution_history
        
        if schedule_id:
            history = [h for h in history if h.schedule_id == schedule_id]
        
        return list(reversed(history[-limit:]))
    
    def get_running_experiments(self) -> Dict[str, str]:
        """Get currently running experiments."""
        with self._lock:
            return self._running_experiments.copy()
    
    # =========================================================================
    # Internal Helpers
    # =========================================================================
    
    def _calculate_next_run(self, schedule: ScheduledExperiment) -> datetime:
        """Calculate the next run time for a schedule."""
        current = now()
        
        if schedule.schedule_type == ScheduleType.ONCE.value:
            # Already ran? No next run
            if schedule.last_run_at:
                return current + timedelta(days=36500)  # Far future
            
            # Parse time
            hour, minute = map(int, schedule.schedule_time.split(":"))
            next_run = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= current:
                next_run += timedelta(days=1)
            return next_run
        
        elif schedule.schedule_type == ScheduleType.DAILY.value:
            hour, minute = map(int, schedule.schedule_time.split(":"))
            next_run = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= current:
                next_run += timedelta(days=1)
            return next_run
        
        elif schedule.schedule_type == ScheduleType.WEEKLY.value:
            hour, minute = map(int, schedule.schedule_time.split(":"))
            
            # Find next occurrence of the target day
            days_ahead = schedule.schedule_day - current.weekday()
            if days_ahead < 0:
                days_ahead += 7
            elif days_ahead == 0:
                # Same day - check if time passed
                target_time = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if current >= target_time:
                    days_ahead = 7
            
            next_run = current + timedelta(days=days_ahead)
            return next_run.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        elif schedule.schedule_type == ScheduleType.CRON.value:
            # TODO: Implement cron parsing
            # For now, default to daily
            hour, minute = 2, 0
            next_run = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= current:
                next_run += timedelta(days=1)
            return next_run
        
        # Default: tomorrow at 2 AM
        return (current + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    
    def _persist_schedules(self) -> None:
        """Persist schedules to storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            
            data = {sid: s.to_dict() for sid, s in self._schedules.items()}
            backend.set("chaos:schedules", data)
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Could not persist schedules: {e}")
    
    def _load_schedules(self) -> None:
        """Load schedules from storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            
            data = backend.get("chaos:schedules")
            if data:
                self._schedules = {
                    sid: ScheduledExperiment.from_dict(s)
                    for sid, s in data.items()
                }
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Could not load schedules: {e}")
    
    def _record_audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record audit event."""
        logger.info(
            f"[ChaosSchedulerAudit] {event_type}",
            extra={"audit_data": data}
        )


# =============================================================================
# Singleton
# =============================================================================


_chaos_scheduler: Optional[ChaosSchedulerService] = None
_scheduler_lock = threading.Lock()


def get_chaos_scheduler() -> ChaosSchedulerService:
    """Get the singleton ChaosSchedulerService instance."""
    global _chaos_scheduler
    
    if _chaos_scheduler is None:
        with _scheduler_lock:
            if _chaos_scheduler is None:
                _chaos_scheduler = ChaosSchedulerService()
                _chaos_scheduler._load_config()
    
    return _chaos_scheduler


def reset_chaos_scheduler() -> None:
    """Reset the singleton (for testing)."""
    global _chaos_scheduler
    with _scheduler_lock:
        _chaos_scheduler = None
