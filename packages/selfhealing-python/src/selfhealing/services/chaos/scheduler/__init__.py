"""
Chaos Scheduler Package.

Celery Beat-based scheduler for autonomous chaos experiments.
Implements scheduled execution with comprehensive pre-flight checks.

Features:
- Scheduled experiment execution (Celery Beat)
- Pre-flight safety checks (SafetyGuard integration)
- Blast radius enforcement
- Approval workflow for high-risk experiments
- Kill switch integration
- Audit trail recording

Usage:
    from selfhealing.services.chaos.scheduler import (
        ChaosSchedulerService,
        get_chaos_scheduler,
        reset_chaos_scheduler,
        ScheduledExperiment,
        SchedulerConfig,
        ExecutionResult,
        ScheduleType,
        ExperimentApprovalStatus,
    )

    # Get singleton
    scheduler = get_chaos_scheduler()

    # Create a schedule
    schedule = scheduler.create_schedule(
        experiment_type="latency_injection",
        target_service="payment",
        schedule_type=ScheduleType.DAILY,
        schedule_time="03:00",
    )

    # Execute manually
    result = scheduler.execute_now(schedule.id)

Structure:
- models.py: ScheduledExperiment, SchedulerConfig, ExecutionResult, Enums
- service.py: ChaosSchedulerService class
- helpers.py: get_chaos_scheduler, reset_chaos_scheduler
"""

# Import helpers
from .helpers import get_chaos_scheduler, reset_chaos_scheduler

# Import models
from .models import (
    ExecutionResult,
    ExperimentApprovalStatus,
    ScheduledExperiment,
    SchedulerConfig,
    ScheduleType,
)

# Import service
from .service import ChaosSchedulerService

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
