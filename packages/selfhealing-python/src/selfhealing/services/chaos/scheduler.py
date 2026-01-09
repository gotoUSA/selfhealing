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
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from selfhealing.core.timezone import now

# Import models from scheduler_models for backward compatibility
from .scheduler_models import (
    ExperimentApprovalStatus,
    ScheduleType,
    ScheduledExperiment,
    SchedulerConfig,
    ExecutionResult,
)

logger = logging.getLogger(__name__)


# Backward compatibility exports
__all__ = [
    # Enums
    "ExperimentApprovalStatus",
    "ScheduleType",
    # Data classes
    "ScheduledExperiment",
    "SchedulerConfig",
    "ExecutionResult",
    # Service
    "ChaosSchedulerService",
    # Factory functions
    "get_chaos_scheduler",
    "reset_chaos_scheduler",
]


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
        
        # Phase 6: Experiment instances by status (32_CHAOS_SYSTEM_INTEGRATION.md §22.2.3)
        # Maps experiment_id -> ChaosExperiment instance for recovery monitoring
        self._experiment_instances: Dict[str, Any] = {}
        
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

    def _make_skipped_result(
        self, schedule_id: str, experiment_id: str, started_at, reason: str, status: str = "skipped"
    ) -> "ExecutionResult":
        """Create a skipped/blocked execution result."""
        return ExecutionResult(
            schedule_id=schedule_id,
            experiment_id=experiment_id,
            status=status,
            skipped=True,
            skip_reason=reason,
            started_at=started_at.isoformat(),
            completed_at=now().isoformat(),
        )

    # =========================================================================
    # 순위 6: Idempotency 체크 (v2.5.0)
    # Reference: 28_IMPROVEMENT_PART3_ENUM_EXTENSION.md §3.3
    # =========================================================================

    def _check_idempotency(
        self,
        schedule: "ScheduledExperiment",
        schedule_id: str,
        experiment_id: str,
        started_at,
    ) -> ExecutionResult | None:
        """
        멱등성 체크: 동일 실험의 중복 실행 방지.
        
        Args:
            schedule: 스케줄 정보
            schedule_id: 스케줄 ID
            experiment_id: 실험 ID
            started_at: 시작 시간
        
        Returns:
            ExecutionResult if duplicate, None otherwise
        """
        try:
            from selfhealing.services.idempotency_service import (
                IdempotencyKey,
                get_idempotency_service,
            )
            
            # 멱등성 키 생성
            idempotency_key = IdempotencyKey.for_chaos_experiment(
                schedule_id=schedule_id,
                experiment_type=schedule.experiment_type,
                target_service=schedule.target_service,
            )
            
            # 중복 체크
            service = get_idempotency_service()
            idem_result = service.check(idempotency_key)
            
            if idem_result.is_duplicate:
                logger.warning(
                    f"[ChaosScheduler] Duplicate experiment blocked: "
                    f"schedule_id={schedule_id}, reason={idem_result.message}"
                )
                return ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status="duplicate",
                    skipped=True,
                    skip_reason=f"Duplicate experiment: {idem_result.message}",
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                )
            
            return None
            
        except ImportError:
            logger.debug("[ChaosScheduler] IdempotencyService not available")
            return None
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Idempotency check failed: {e}")
            return None

    def _mark_idempotency_processed(self, schedule: "ScheduledExperiment") -> None:
        """
        실험 완료 후 멱등성 처리 완료 마킹.
        
        Args:
            schedule: 스케줄 정보
        """
        try:
            from selfhealing.services.idempotency_service import (
                IdempotencyKey,
                get_idempotency_service,
            )
            
            idempotency_key = IdempotencyKey.for_chaos_experiment(
                schedule_id=schedule.id,
                experiment_type=schedule.experiment_type,
                target_service=schedule.target_service,
            )
            
            service = get_idempotency_service()
            service.mark_as_processed(idempotency_key)
            
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Failed to mark idempotency: {e}")

    def _check_error_budget_gate(self, schedule_id: str, experiment_id: str, started_at) -> ExecutionResult | None:
        """Check error budget gate. Returns ExecutionResult if blocked, None otherwise."""
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed

            gate_result = check_automation_allowed()
            if not gate_result.allowed:
                logger.warning(
                    f"[ChaosScheduler] Experiment blocked by Error Budget Gate: "
                    f"{gate_result.error_budget_percent}% < {gate_result.threshold_percent}%"
                )
                return self._make_skipped_result(
                    schedule_id, experiment_id, started_at,
                    f"Error budget critically low ({gate_result.error_budget_percent:.1f}%). "
                    f"Manual mode enforced. {gate_result.recommendation}",
                    status="blocked"
                )
        except ImportError:
            pass  # Gate not available
        return None

    def _check_pre_execution_conditions(
        self, schedule: "ScheduledExperiment", schedule_id: str, experiment_id: str, started_at
    ) -> ExecutionResult | None:
        """Check scheduler/schedule/approval conditions. Returns ExecutionResult if blocked."""
        if not self._config.enabled:
            return self._make_skipped_result(schedule_id, experiment_id, started_at, "Scheduler is disabled")

        if not schedule.enabled:
            return self._make_skipped_result(schedule_id, experiment_id, started_at, "Schedule is disabled")

        if schedule.approval_status not in (
            ExperimentApprovalStatus.AUTO_APPROVED.value,
            ExperimentApprovalStatus.APPROVED.value,
        ):
            return self._make_skipped_result(
                schedule_id, experiment_id, started_at, f"Not approved: {schedule.approval_status}"
            )
        return None

    def _check_safety_conditions(
        self, schedule: "ScheduledExperiment", schedule_id: str, experiment_id: str, started_at
    ) -> ExecutionResult | None:
        """Check safety guard conditions. Returns ExecutionResult if blocked."""
        from .safety_guard import get_safety_guard

        guard = get_safety_guard()
        safety_result = guard.check(experiment_id=experiment_id, target_service=schedule.target_service)

        if not safety_result.allowed:
            self._record_audit("experiment_blocked_safety", {
                "schedule_id": schedule_id,
                "experiment_id": experiment_id,
                "block_reason": safety_result.block_reason,
                "block_message": safety_result.block_message,
            })
            return self._make_skipped_result(schedule_id, experiment_id, started_at, safety_result.block_message)
        return None

    def _check_blast_radius_conditions(
        self, schedule: "ScheduledExperiment", schedule_id: str, experiment_id: str, started_at
    ) -> ExecutionResult | None:
        """Check blast radius conditions. Returns ExecutionResult if blocked."""
        from .blast_radius import get_blast_radius_manager

        br_manager = get_blast_radius_manager()
        br_result = br_manager.check(
            blast_radius=schedule.blast_radius,
            target_service=schedule.target_service,
            target_domain=schedule.target_domain,
            experiment_id=experiment_id,
        )

        if not br_result.allowed:
            return self._make_skipped_result(
                schedule_id, experiment_id, started_at,
                f"Blast radius check failed: {br_result.violations}"
            )

        # Register experiment
        br_manager.register_experiment(
            experiment_id=experiment_id,
            blast_radius=schedule.blast_radius,
            target_service=schedule.target_service,
            target_domain=schedule.target_domain,
        )
        return None

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
            # 순위 6: Idempotency 체크
            if not force:
                blocked = self._check_idempotency(schedule, schedule_id, experiment_id, started_at)
                if blocked:
                    return blocked
            
            # 0. Error Budget Gate Check
            if not force:
                blocked = self._check_error_budget_gate(schedule_id, experiment_id, started_at)
                if blocked:
                    return blocked

            # 1-3. Check pre-execution conditions (scheduler, schedule, approval)
            blocked = self._check_pre_execution_conditions(schedule, schedule_id, experiment_id, started_at)
            if blocked:
                return blocked

            # 4. Safety checks (Pre-flight)
            if not force:
                blocked = self._check_safety_conditions(schedule, schedule_id, experiment_id, started_at)
                if blocked:
                    return blocked

            # 5. Blast radius check
            if not force:
                blocked = self._check_blast_radius_conditions(schedule, schedule_id, experiment_id, started_at)
                if blocked:
                    return blocked
            
            # 6. Create and execute experiment
            with self._lock:
                self._running_experiments[schedule_id] = experiment_id
            
            try:
                config = ExperimentConfig(
                    target_service=schedule.target_service,
                    target_domain=schedule.target_domain,
                    **schedule.experiment_config,
                )
                
                # Apply dry_run mode from scheduler config
                if self._config.dry_run_mode:
                    config.dry_run = True
                    logger.info(
                        f"[ChaosScheduler] Running in DRY RUN mode: {self._config.dry_run_reason}"
                    )
                
                experiment = create_experiment(
                    experiment_type=schedule.experiment_type,
                    config=config,
                )
                experiment.experiment_id = experiment_id
                
                # Execute (will use dry run if config.dry_run is True)
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
                
                # 순위 6: 멱등성 처리 완료 마킹
                if not force:
                    self._mark_idempotency_processed(schedule)
                
                execution_result = ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id=experiment_id,
                    status=result.status,
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                    duration_seconds=(now() - started_at).total_seconds(),
                    success=result.status == "completed",
                    experiment_result=result.to_dict(),
                    dry_run=result.dry_run,
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
    
    def register_experiment_instance(self, experiment_id: str, experiment: Any) -> None:
        """
        Register an experiment instance for recovery monitoring.
        
        Phase 6: 32_CHAOS_SYSTEM_INTEGRATION.md §22.2.3
        
        Args:
            experiment_id: Unique experiment ID
            experiment: ChaosExperiment instance
        """
        with self._lock:
            self._experiment_instances[experiment_id] = experiment
            logger.debug(f"[ChaosScheduler] Registered experiment instance {experiment_id}")
    
    def unregister_experiment_instance(self, experiment_id: str) -> None:
        """
        Unregister an experiment instance.
        
        Args:
            experiment_id: Unique experiment ID
        """
        with self._lock:
            if experiment_id in self._experiment_instances:
                del self._experiment_instances[experiment_id]
                logger.debug(f"[ChaosScheduler] Unregistered experiment instance {experiment_id}")
    
    def get_experiments_by_status(self, status: str) -> List[Any]:
        """
        Get experiments by status.
        
        Phase 6: 32_CHAOS_SYSTEM_INTEGRATION.md §15.3, §22.2.3
        
        Used by check_recovery_monitoring_experiments Celery task
        to find experiments in RECOVERY_MONITORING state.
        
        Args:
            status: ExperimentStatus value (e.g., "recovery_monitoring")
            
        Returns:
            List of ChaosExperiment instances with matching status
        """
        with self._lock:
            matching = []
            for exp_id, experiment in list(self._experiment_instances.items()):
                try:
                    if hasattr(experiment, 'status'):
                        exp_status = experiment.status
                        # Handle both enum and string
                        if hasattr(exp_status, 'value'):
                            exp_status = exp_status.value
                        if exp_status == status:
                            matching.append(experiment)
                except Exception as e:
                    logger.warning(f"[ChaosScheduler] Error checking experiment {exp_id} status: {e}")
            return matching
    
    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _get_daily_next_run(self, current: datetime, hour: int, minute: int) -> datetime:
        """Calculate next run for daily schedule."""
        next_run = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= current:
            next_run += timedelta(days=1)
        return next_run

    def _get_weekly_next_run(self, current: datetime, schedule: ScheduledExperiment) -> datetime:
        """Calculate next run for weekly schedule."""
        hour, minute = map(int, schedule.schedule_time.split(":"))

        days_ahead = schedule.schedule_day - current.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            target_time = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if current >= target_time:
                days_ahead = 7

        next_run = current + timedelta(days=days_ahead)
        return next_run.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _get_cron_next_run(self, current: datetime, cron_expression: str) -> datetime:
        """Calculate next run for cron schedule."""
        try:
            from croniter import croniter
            cron_expr = cron_expression or "0 2 * * *"
            cron = croniter(cron_expr, current)
            return cron.get_next(datetime)
        except ImportError:
            logger.warning(
                "[ChaosScheduler] croniter not installed. "
                "Install with: pip install croniter. Using daily fallback."
            )
        except Exception as e:
            logger.warning(f"[ChaosScheduler] Invalid cron expression: {e}. Using daily fallback.")

        # Fallback to daily at 2 AM
        return self._get_daily_next_run(current, 2, 0)

    def _calculate_next_run(self, schedule: ScheduledExperiment) -> datetime:
        """Calculate the next run time for a schedule."""
        current = now()

        if schedule.schedule_type == ScheduleType.ONCE.value:
            if schedule.last_run_at:
                return current + timedelta(days=36500)  # Far future
            hour, minute = map(int, schedule.schedule_time.split(":"))
            return self._get_daily_next_run(current, hour, minute)

        if schedule.schedule_type == ScheduleType.DAILY.value:
            hour, minute = map(int, schedule.schedule_time.split(":"))
            return self._get_daily_next_run(current, hour, minute)

        if schedule.schedule_type == ScheduleType.WEEKLY.value:
            return self._get_weekly_next_run(current, schedule)

        if schedule.schedule_type == ScheduleType.CRON.value:
            return self._get_cron_next_run(current, schedule.cron_expression)

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
