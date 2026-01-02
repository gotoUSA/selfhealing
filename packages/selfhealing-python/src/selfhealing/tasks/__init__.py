"""
Self-Healing Celery Tasks

This package contains task definitions and base classes for self-healing
autonomous operations. Tasks are designed to be framework-agnostic and
can be registered with any task queue system (Celery, RQ, etc.).

Key Components:
- BaseNotifyingTask: Base class with built-in notifications
- NotificationPolicy: Configurable notification policies
- SLADriftDetector: SLA drift detection logic
- ForensicAnalyzer: Forensic analysis for pending operations

Usage:
    from selfhealing.tasks import (
        BaseNotifyingTask,
        NotificationPolicy,
        NotificationTiming,
        DailyAutonomousReport,
    )

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md
"""

from .base_notifying_task import (
    BaseNotifyingTask,
    NotificationPolicy,
    NotificationTiming,
    NotificationThreshold,
    DailyAutonomousReport,
    reset_cooldowns,
    get_cooldown_status,
)

from .drift_detection import (
    SLADriftDetector,
    ForensicAnalyzer,
)

__all__ = [
    # Base Notifying Task
    "BaseNotifyingTask",
    "NotificationPolicy",
    "NotificationTiming",
    "NotificationThreshold",
    "DailyAutonomousReport",
    "reset_cooldowns",
    "get_cooldown_status",
    # Drift Detection
    "SLADriftDetector",
    "ForensicAnalyzer",
]
