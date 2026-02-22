"""
Chaos Scheduler Service.

Celery Beat-based scheduler for autonomous chaos experiments.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog

from selfhealing.core.timezone import now

from .models import (
    ExecutionResult,
    ExperimentApprovalStatus,
    ScheduledExperiment,
    SchedulerConfig,
    ScheduleType,
)

logger = structlog.get_logger()


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

    def __init__(self, config: SchedulerConfig | None = None):
        """Initialize ChaosSchedulerService."""
        self._config = config or SchedulerConfig()
        self._lock = threading.RLock()

        # In-memory storage (use StateBackend in production)
        self._schedules: dict[str, ScheduledExperiment] = {}
        self._execution_history: list[ExecutionResult] = []

        # Currently running experiments
        self._running_experiments: dict[str, str] = {}  # schedule_id -> experiment_id

        # Experiment instances by status
        # Maps experiment_id -> ChaosExperiment instance for recovery monitoring
        self._experiment_instances: dict[str, Any] = {}

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
                    logger.info(
                        "chaos_scheduler.updated_config",
                        key=key,
                        value=value,
                    )

            self._persist_config()
            return self._config

    def _persist_config(self) -> None:
        """Persist configuration to storage."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            manager.update_chaos_config(scheduler_config=self._config.to_dict())
        except Exception as e:
            logger.warning(
                "chaos_scheduler.persist_config",
                error=e,
            )

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
            logger.warning(
                "chaos_scheduler.load_config",
                error=e,
            )

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
        experiment_config: dict[str, Any] | None = None,
        description: str = "",
        tags: list[str] | None = None,
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
        from selfhealing.services.chaos.blast_radius import (
            BlastRadius,
            get_blast_radius_manager,
        )

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

            if (
                blast_radius_enum == BlastRadius.INSTANCE
                and self._config.auto_approve_instance_level
            ):
                schedule.approval_status = ExperimentApprovalStatus.AUTO_APPROVED.value
            elif (
                blast_radius_enum == BlastRadius.SERVICE
                and self._config.auto_approve_service_level
            ):
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
                "chaos_scheduler.created_schedule_targeting",
                schedule=schedule.id,
                experiment_type=experiment_type,
                target_service=target_service,
                blast_radius=blast_radius,
            )

            return schedule

    def get_schedule(self, schedule_id: str) -> ScheduledExperiment | None:
        """Get a schedule by ID."""
        return self._schedules.get(schedule_id)

    def list_schedules(
        self,
        enabled_only: bool = False,
        pending_approval_only: bool = False,
        target_service: str | None = None,
    ) -> list[ScheduledExperiment]:
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
                    s
                    for s in schedules
                    if s.approval_status == ExperimentApprovalStatus.PENDING.value
                ]

            if target_service:
                schedules = [s for s in schedules if s.target_service == target_service]

            return schedules

    def update_schedule(
        self, schedule_id: str, **kwargs
    ) -> ScheduledExperiment | None:
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
            if any(
                k in kwargs
                for k in (
                    "schedule_type",
                    "schedule_time",
                    "schedule_day",
                    "schedule_cron",
                )
            ):
                schedule.next_run_at = self._calculate_next_run(schedule).isoformat()

            self._persist_schedules()

            logger.info(
                "chaos_scheduler.updated_schedule",
                schedule_id=schedule_id,
            )
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
                logger.info(
                    "chaos_scheduler.deleted_schedule",
                    schedule_id=schedule_id,
                )
                return True
            return False

    def enable_schedule(self, schedule_id: str) -> ScheduledExperiment | None:
        """Enable a schedule."""
        return self.update_schedule(schedule_id, enabled=True)

    def disable_schedule(self, schedule_id: str) -> ScheduledExperiment | None:
        """Disable a schedule."""
        return self.update_schedule(schedule_id, enabled=False)

    # =========================================================================
    # Approval Workflow
    # =========================================================================

    def approve_schedule(
        self, schedule_id: str, approved_by: str
    ) -> ScheduledExperiment | None:
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

            logger.info(
                "chaos_scheduler.schedule_approved",
                schedule_id=schedule_id,
                approved_by=approved_by,
            )

            # Record audit
            self._record_audit(
                "schedule_approved",
                {
                    "schedule_id": schedule_id,
                    "approved_by": approved_by,
                },
            )

            return schedule

    def deny_schedule(
        self, schedule_id: str, denied_by: str, reason: str = ""
    ) -> ScheduledExperiment | None:
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

            logger.info(
                "chaos_scheduler.schedule_denied",
                schedule_id=schedule_id,
                denied_by=denied_by,
                reason=reason,
            )

            return schedule

    # =========================================================================
    # Execution
    # =========================================================================

    def _make_skipped_result(
        self,
        schedule_id: str,
        experiment_id: str,
        started_at,
        reason: str,
        status: str = "skipped",
    ) -> ExecutionResult:
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

    def _check_idempotency(
        self,
        schedule: ScheduledExperiment,
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
                    "chaos_scheduler.duplicate_experiment_blocked",
                    schedule_id=schedule_id,
                    idem_result=idem_result.message,
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
            logger.debug("chaos_scheduler.idempotencyservice_available")
            return None
        except Exception as e:
            logger.warning(
                "chaos_scheduler.idempotency_check_failed",
                error=e,
            )
            return None

    def _mark_idempotency_processed(self, schedule: ScheduledExperiment) -> None:
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
            logger.warning(
                "chaos_scheduler.failed_mark_idempotency",
                error=e,
            )

    def _check_error_budget_gate(
        self, schedule_id: str, experiment_id: str, started_at
    ) -> ExecutionResult | None:
        """Check error budget gate. Returns ExecutionResult if blocked, None otherwise."""
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed

            gate_result = check_automation_allowed()
            if not gate_result.allowed:
                logger.warning(
                    "chaos_scheduler.experiment_blocked_error_budget",
                    gate_result=gate_result.error_budget_percent,
                    gate_result_1=gate_result.threshold_percent,
                )
                return self._make_skipped_result(
                    schedule_id,
                    experiment_id,
                    started_at,
                    f"Error budget critically low ({gate_result.error_budget_percent:.1f}%). "
                    f"Manual mode enforced. {gate_result.recommendation}",
                    status="blocked",
                )
        except ImportError:
            pass  # Gate not available
        return None

    def _check_pre_execution_conditions(
        self,
        schedule: ScheduledExperiment,
        schedule_id: str,
        experiment_id: str,
        started_at,
    ) -> ExecutionResult | None:
        """Check scheduler/schedule/approval conditions. Returns ExecutionResult if blocked."""
        if not self._config.enabled:
            return self._make_skipped_result(
                schedule_id, experiment_id, started_at, "Scheduler is disabled"
            )

        if not schedule.enabled:
            return self._make_skipped_result(
                schedule_id, experiment_id, started_at, "Schedule is disabled"
            )

        if schedule.approval_status not in (
            ExperimentApprovalStatus.AUTO_APPROVED.value,
            ExperimentApprovalStatus.APPROVED.value,
        ):
            return self._make_skipped_result(
                schedule_id,
                experiment_id,
                started_at,
                f"Not approved: {schedule.approval_status}",
            )
        return None

    def _check_safety_conditions(
        self,
        schedule: ScheduledExperiment,
        schedule_id: str,
        experiment_id: str,
        started_at,
    ) -> ExecutionResult | None:
        """Check safety guard conditions. Returns ExecutionResult if blocked."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard

        guard = get_safety_guard()
        safety_result = guard.check(
            experiment_id=experiment_id, target_service=schedule.target_service
        )

        if not safety_result.allowed:
            self._record_audit(
                "experiment_blocked_safety",
                {
                    "schedule_id": schedule_id,
                    "experiment_id": experiment_id,
                    "block_reason": safety_result.block_reason,
                    "block_message": safety_result.block_message,
                },
            )
            return self._make_skipped_result(
                schedule_id, experiment_id, started_at, safety_result.block_message
            )
        return None

    def _check_blast_radius_conditions(
        self,
        schedule: ScheduledExperiment,
        schedule_id: str,
        experiment_id: str,
        started_at,
    ) -> ExecutionResult | None:
        """Check blast radius conditions. Returns ExecutionResult if blocked."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager

        br_manager = get_blast_radius_manager()
        br_result = br_manager.check(
            blast_radius=schedule.blast_radius,
            target_service=schedule.target_service,
            target_domain=schedule.target_domain,
            experiment_id=experiment_id,
        )

        if not br_result.allowed:
            return self._make_skipped_result(
                schedule_id,
                experiment_id,
                started_at,
                f"Blast radius check failed: {br_result.violations}",
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
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return self._create_error_result(
                schedule_id, "", f"Schedule not found: {schedule_id}"
            )

        experiment_id = f"chaos-{uuid.uuid4().hex[:12]}"
        started_at = now()

        try:
            # Pre-execution checks
            blocked = self._run_pre_execution_checks(
                schedule, schedule_id, experiment_id, started_at, force
            )
            if blocked:
                return blocked

            # Execute experiment
            return self._execute_experiment(
                schedule, schedule_id, experiment_id, started_at, force
            )

        except Exception as e:
            logger.exception(
                "chaos_scheduler.error_executing",
                schedule_id=schedule_id,
                error=e,
            )
            return self._create_error_result(
                schedule_id, experiment_id, str(e), started_at
            )

    def _create_error_result(
        self,
        schedule_id: str,
        experiment_id: str,
        error_message: str,
        started_at: Any | None = None,
    ) -> ExecutionResult:
        """Create an error ExecutionResult."""
        current_time = now()
        result = ExecutionResult(
            schedule_id=schedule_id,
            experiment_id=experiment_id,
            status="error",
            error_message=error_message,
        )
        if started_at:
            result.started_at = started_at.isoformat()
            result.completed_at = current_time.isoformat()
            result.duration_seconds = (current_time - started_at).total_seconds()
        return result

    def _run_pre_execution_checks(
        self,
        schedule: ScheduledExperiment,
        schedule_id: str,
        experiment_id: str,
        started_at: Any,
        force: bool,
    ) -> ExecutionResult | None:
        """Run all pre-execution checks. Returns blocked result or None."""
        if not force:
            blocked = self._check_idempotency(
                schedule, schedule_id, experiment_id, started_at
            )
            if blocked:
                return blocked

            blocked = self._check_error_budget_gate(
                schedule_id, experiment_id, started_at
            )
            if blocked:
                return blocked

        blocked = self._check_pre_execution_conditions(
            schedule, schedule_id, experiment_id, started_at
        )
        if blocked:
            return blocked

        if not force:
            blocked = self._check_safety_conditions(
                schedule, schedule_id, experiment_id, started_at
            )
            if blocked:
                return blocked

            blocked = self._check_blast_radius_conditions(
                schedule, schedule_id, experiment_id, started_at
            )
            if blocked:
                return blocked

        return None

    def _execute_experiment(
        self,
        schedule: ScheduledExperiment,
        schedule_id: str,
        experiment_id: str,
        started_at: Any,
        force: bool,
    ) -> ExecutionResult:
        """Execute the experiment and return result."""
        from selfhealing.services.chaos.base import ExperimentConfig
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        from selfhealing.services.chaos.experiments import create_experiment
        from selfhealing.services.chaos.safety_guard import get_safety_guard

        guard = get_safety_guard()
        br_manager = get_blast_radius_manager()

        with self._lock:
            self._running_experiments[schedule_id] = experiment_id

        try:
            config = ExperimentConfig(
                target_service=schedule.target_service,
                target_domain=schedule.target_domain,
                **schedule.experiment_config,
            )

            if self._config.dry_run_mode:
                config.dry_run = True
                logger.info(
                    "chaos_scheduler.running_dry_run_mode",
                    self=self._config.dry_run_reason,
                )

            experiment = create_experiment(
                experiment_type=schedule.experiment_type,
                config=config,
            )
            experiment.experiment_id = experiment_id

            result = experiment.execute()

            self._update_schedule_after_execution(schedule, result)

            if not force:
                guard.record_experiment_completed()
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

            self._execution_history.append(execution_result)
            if len(self._execution_history) > 1000:
                self._execution_history = self._execution_history[-1000:]

            return execution_result

        finally:
            with self._lock:
                if schedule_id in self._running_experiments:
                    del self._running_experiments[schedule_id]

            if not force:
                br_manager.unregister_experiment(experiment_id)

    def _update_schedule_after_execution(
        self, schedule: ScheduledExperiment, result: Any
    ) -> None:
        """Update schedule after experiment execution."""
        with self._lock:
            schedule.last_run_at = now().isoformat()
            schedule.last_run_result = result.status
            schedule.run_count += 1
            schedule.next_run_at = self._calculate_next_run(schedule).isoformat()
            self._persist_schedules()

    def execute_due_schedules(self) -> list[ExecutionResult]:
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
                s
                for s in self._schedules.values()
                if s.enabled
                and s.next_run_at
                and datetime.fromisoformat(s.next_run_at) <= current
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
        logger.warning(
            "chaos_scheduler.kill_requested",
            experiment_id=experiment_id,
            reason=reason,
        )

        # Record audit
        self._record_audit(
            "experiment_killed",
            {
                "experiment_id": experiment_id,
                "reason": reason,
            },
        )

        # The experiment's kill switch is checked in monitor loop
        # For now, we set a flag that the experiment should check
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            backend.set(
                f"chaos:kill:{experiment_id}", {"killed": True, "reason": reason}
            )
            return True
        except Exception as e:
            logger.exception(
                "chaos_scheduler.set_kill_flag",
                error=e,
            )
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
        schedule_id: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionResult]:
        """Get execution history."""
        history = self._execution_history

        if schedule_id:
            history = [h for h in history if h.schedule_id == schedule_id]

        return list(reversed(history[-limit:]))

    def get_running_experiments(self) -> dict[str, str]:
        """Get currently running experiments."""
        with self._lock:
            return self._running_experiments.copy()

    def register_experiment_instance(self, experiment_id: str, experiment: Any) -> None:
        """
        Register an experiment instance for recovery monitoring.

        Args:
            experiment_id: Unique experiment ID
            experiment: ChaosExperiment instance
        """
        with self._lock:
            self._experiment_instances[experiment_id] = experiment
            logger.debug(
                "cell_registry.bulkheads_registered",
                experiment_id=experiment_id,
            )

    def unregister_experiment_instance(self, experiment_id: str) -> None:
        """
        Unregister an experiment instance.

        Args:
            experiment_id: Unique experiment ID
        """
        with self._lock:
            if experiment_id in self._experiment_instances:
                del self._experiment_instances[experiment_id]
                logger.debug(
                    "chaos_scheduler.unregistered_experiment_instance",
                    experiment_id=experiment_id,
                )

    def get_experiments_by_status(self, status: str) -> list[Any]:
        """
        Get experiments by status.

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
                    if hasattr(experiment, "status"):
                        exp_status = experiment.status
                        # Handle both enum and string
                        if hasattr(exp_status, "value"):
                            exp_status = exp_status.value
                        if exp_status == status:
                            matching.append(experiment)
                except Exception as e:
                    logger.warning(
                        "chaos_scheduler.error_checking_experiment_status",
                        exp_id=exp_id,
                        error=e,
                    )
            return matching

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _get_daily_next_run(
        self, current: datetime, hour: int, minute: int
    ) -> datetime:
        """Calculate next run for daily schedule."""
        next_run = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= current:
            next_run += timedelta(days=1)
        return next_run

    def _get_weekly_next_run(
        self, current: datetime, schedule: ScheduledExperiment
    ) -> datetime:
        """Calculate next run for weekly schedule."""
        hour, minute = map(int, schedule.schedule_time.split(":"))

        days_ahead = schedule.schedule_day - current.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            target_time = current.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
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
            logger.warning("chaos_scheduler.croniter_installed_install_pip")
        except Exception as e:
            logger.warning(
                "chaos_scheduler.invalid_cron_expression_using",
                error=e,
            )

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
        return (current + timedelta(days=1)).replace(
            hour=2, minute=0, second=0, microsecond=0
        )

    def _persist_schedules(self) -> None:
        """Persist schedules to storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()

            data = {sid: s.to_dict() for sid, s in self._schedules.items()}
            backend.set("chaos:schedules", data)
        except Exception as e:
            logger.warning(
                "chaos_scheduler.persist_schedules",
                error=e,
            )

    def _load_schedules(self) -> None:
        """Load schedules from storage."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()

            data = backend.get("chaos:schedules")
            if data:
                self._schedules = {
                    sid: ScheduledExperiment.from_dict(s) for sid, s in data.items()
                }
        except Exception as e:
            logger.warning(
                "chaos_scheduler.load_schedules",
                error=e,
            )

    def _record_audit(self, event_type: str, data: dict[str, Any]) -> None:
        """Record audit event."""
        logger.info(f"[ChaosSchedulerAudit] {event_type}", extra={"audit_data": data})  # noqa: G004
