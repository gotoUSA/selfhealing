"""
Chaos Scheduler Models.

Data classes and enums for chaos scheduler operations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from selfhealing.core.timezone import now

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
    experiment_config: dict[str, Any] = field(default_factory=dict)

    # Target
    target_service: str = ""
    target_domain: str = ""
    blast_radius: str = "instance"

    # Schedule
    schedule_type: str = ScheduleType.DAILY.value
    schedule_time: str = "02:00"  # HH:MM UTC
    schedule_day: int = 0  # 0=Monday for weekly
    schedule_cron: str = ""  # For custom cron

    # Alias for cron expression (used in some places)
    @property
    def cron_expression(self) -> str:
        return self.schedule_cron

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
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
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
    def from_dict(cls, data: dict[str, Any]) -> ScheduledExperiment:
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
            approval_status=data.get(
                "approval_status", ExperimentApprovalStatus.PENDING.value
            ),
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

    # Dry Run mode (default True for safety)
    dry_run_mode: bool = True
    """
    Dry Run 모드.
    True: 실제 장애 주입 없이 전체 워크플로우만 검증 (기본값 - 안전)
    False: 실제 장애 주입 (프로덕션 모드)
    """

    dry_run_reason: str = "Initial deployment - simulation mode"
    """Dry Run 모드 활성화 이유."""

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

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "enabled": self.enabled,
            "dry_run_mode": self.dry_run_mode,
            "dry_run_reason": self.dry_run_reason,
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
    experiment_result: dict[str, Any] = field(default_factory=dict)

    # Errors
    error_message: str = ""

    # Dry Run info
    dry_run: bool = False
    """True이면 실제 장애 주입 없이 시뮬레이션만 수행됨."""

    def to_dict(self) -> dict[str, Any]:
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
            "dry_run": self.dry_run,
        }
