"""
Celery Beat Schedule for Self-Healing Autonomous Tasks

Consolidates all autonomous task schedules from 3 lanes:
- 🧹 청소부 레인 (Cleanup & Expire)
- 🧠 지능 레인 (Analyze & Learn)  
- 📋 증명 레인 (Compliance & Report)

Usage:
    # In your celery.py or Django settings:
    from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule
    
    CELERY_BEAT_SCHEDULE = {
        # ... your existing schedules
        **get_selfhealing_beat_schedule(),
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# =============================================================================
# Queue Configuration
# =============================================================================

SELFHEALING_QUEUE_CONFIG = {
    # 🧹 청소부 레인 큐
    "maintenance": {
        "exchange": "selfhealing",
        "routing_key": "maintenance",
        "priority": 3,  # 낮은 우선순위
    },
    "critical_maintenance": {
        "exchange": "selfhealing",
        "routing_key": "critical_maintenance",
        "priority": 8,  # 높은 우선순위
    },
    
    # 🧠 지능 레인 큐
    "analysis": {
        "exchange": "selfhealing",
        "routing_key": "analysis",
        "priority": 5,
    },
    "realtime": {
        "exchange": "selfhealing",
        "routing_key": "realtime",
        "priority": 9,  # 최고 우선순위
    },
    
    # 📋 증명 레인 큐
    "compliance": {
        "exchange": "selfhealing",
        "routing_key": "compliance",
        "priority": 7,
    },
    "reports": {
        "exchange": "selfhealing",
        "routing_key": "reports",
        "priority": 2,
    },
    "metrics": {
        "exchange": "selfhealing",
        "routing_key": "metrics",
        "priority": 1,
    },
}


# =============================================================================
# Consolidated Beat Schedule
# =============================================================================


def get_selfhealing_beat_schedule(
    include_cleanup: bool = True,
    include_intelligence: bool = True,
    include_compliance: bool = True,
    include_traffic_aware: bool = True,
    include_legacy: bool = True,
) -> Dict[str, Any]:
    """
    Get consolidated Celery Beat schedule for all self-healing tasks.
    
    Args:
        include_cleanup: Include 🧹 청소부 레인 tasks
        include_intelligence: Include 🧠 지능 레인 tasks
        include_compliance: Include 📋 증명 레인 tasks
        include_traffic_aware: Include 🚦 Traffic-Aware Replay tasks (Track 3)
        include_legacy: Include legacy tasks from adapters/celery/tasks.py
    
    Returns:
        Dict[str, Any]: Complete Celery Beat schedule configuration
        
    Usage:
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule
        
        CELERY_BEAT_SCHEDULE = {
            **get_selfhealing_beat_schedule(),
            # ... your custom schedules
        }
    """
    schedule: Dict[str, Any] = {}
    
    if include_cleanup:
        try:
            from selfhealing.tasks.cleanup_tasks import get_cleanup_beat_schedule
            schedule.update(get_cleanup_beat_schedule())
            logger.debug("[BeatSchedule] Added cleanup lane schedules")
        except ImportError as e:
            logger.warning(f"[BeatSchedule] Could not load cleanup tasks: {e}")
    
    if include_intelligence:
        try:
            from selfhealing.tasks.intelligence_tasks import get_intelligence_beat_schedule
            schedule.update(get_intelligence_beat_schedule())
            logger.debug("[BeatSchedule] Added intelligence lane schedules")
        except ImportError as e:
            logger.warning(f"[BeatSchedule] Could not load intelligence tasks: {e}")
    
    if include_compliance:
        try:
            from selfhealing.tasks.compliance_tasks import get_compliance_beat_schedule
            schedule.update(get_compliance_beat_schedule())
            logger.debug("[BeatSchedule] Added compliance lane schedules")
        except ImportError as e:
            logger.warning(f"[BeatSchedule] Could not load compliance tasks: {e}")
    
    if include_traffic_aware:
        try:
            from selfhealing.tasks.traffic_aware_replay import get_traffic_aware_beat_schedule
            schedule.update(get_traffic_aware_beat_schedule())
            logger.debug("[BeatSchedule] Added traffic-aware replay schedule (Track 3)")
        except ImportError as e:
            logger.warning(f"[BeatSchedule] Could not load traffic-aware tasks: {e}")
    
    if include_legacy:
        schedule.update(_get_legacy_beat_schedule())
        logger.debug("[BeatSchedule] Added legacy schedules")
    
    return schedule


def _get_legacy_beat_schedule() -> Dict[str, Any]:
    """
    Legacy tasks from existing adapters/celery/tasks.py.
    
    These will be gradually migrated to lane-based tasks.
    """
    from celery.schedules import crontab
    
    return {
        # DLQ Replay - 5분마다
        "replay-failed-operations": {
            "task": "selfhealing.adapters.celery.tasks.replay_batch_by_domain",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "dlq"},
            "kwargs": {"max_entries": 50},
        },
        # Circuit Breaker Recovery Check - 2분마다 (기존)
        "check-circuit-breaker-recovery-legacy": {
            "task": "selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
            "schedule": crontab(minute="*/2"),
            "options": {"queue": "realtime"},
        },
        # Manual Override Expiry - 10분마다
        "expire-manual-overrides": {
            "task": "selfhealing.adapters.celery.tasks.expire_manual_overrides",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "maintenance"},
        },
    }


# =============================================================================
# Schedule Helpers
# =============================================================================


def get_schedule_summary() -> Dict[str, Any]:
    """
    Get human-readable summary of all scheduled tasks.
    
    Useful for documentation and debugging.
    """
    schedule = get_selfhealing_beat_schedule()
    
    summary = {
        "total_tasks": len(schedule),
        "by_lane": {
            "cleanup": [],
            "intelligence": [],
            "compliance": [],
            "legacy": [],
        },
        "by_queue": {},
    }
    
    lane_prefixes = {
        "cleanup": ["cleanup-", "archive-", "expire-", "purge-"],
        "intelligence": ["check-sla", "analyze-", "check-recovery"],
        "compliance": ["run-compliance", "generate-", "collect-self-healing"],
    }
    
    for name, config in schedule.items():
        queue = config.get("options", {}).get("queue", "default")
        
        # Count by queue
        if queue not in summary["by_queue"]:
            summary["by_queue"][queue] = []
        summary["by_queue"][queue].append(name)
        
        # Categorize by lane
        categorized = False
        for lane, prefixes in lane_prefixes.items():
            if any(name.startswith(prefix) for prefix in prefixes):
                summary["by_lane"][lane].append(name)
                categorized = True
                break
        
        if not categorized:
            summary["by_lane"]["legacy"].append(name)
    
    return summary


def validate_schedule() -> Dict[str, Any]:
    """
    Validate schedule configuration.
    
    Returns:
        Dict with validation results
    """
    schedule = get_selfhealing_beat_schedule()
    errors = []
    warnings = []
    
    for name, config in schedule.items():
        # Check required fields
        if "task" not in config:
            errors.append(f"{name}: missing 'task' field")
        
        if "schedule" not in config:
            errors.append(f"{name}: missing 'schedule' field")
        
        # Check queue exists
        queue = config.get("options", {}).get("queue")
        if queue and queue not in SELFHEALING_QUEUE_CONFIG and queue != "default" and queue != "dlq":
            warnings.append(f"{name}: queue '{queue}' not in SELFHEALING_QUEUE_CONFIG")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "task_count": len(schedule),
    }


# =============================================================================
# Registration Helper
# =============================================================================


def register_all_tasks_with_celery(app) -> None:
    """
    Register all self-healing tasks with a Celery application.
    
    Args:
        app: Celery application instance
    """
    from selfhealing.tasks.cleanup_tasks import register_cleanup_tasks_with_celery
    from selfhealing.tasks.intelligence_tasks import register_intelligence_tasks_with_celery
    from selfhealing.tasks.compliance_tasks import register_compliance_tasks_with_celery
    from selfhealing.tasks.traffic_aware_replay import register_traffic_aware_tasks_with_celery
    
    register_cleanup_tasks_with_celery(app)
    register_intelligence_tasks_with_celery(app)
    register_compliance_tasks_with_celery(app)
    register_traffic_aware_tasks_with_celery(app)
    
    logger.info("[BeatSchedule] All self-healing tasks registered (including Track 3)")


__all__ = [
    "get_selfhealing_beat_schedule",
    "get_schedule_summary",
    "validate_schedule",
    "register_all_tasks_with_celery",
    "SELFHEALING_QUEUE_CONFIG",
]
