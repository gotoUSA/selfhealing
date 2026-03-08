"""
Celery Beat Schedule for Self-Healing Autonomous Tasks

Consolidates all autonomous task schedules from 3 lanes:
- Cleanup Lane (Cleanup & Expire)
- Intelligence Lane (Analyze & Learn)
- Compliance Lane (Compliance & Report)

Usage:
    # Option 1: One-line wrapper (recommended)
    from selfhealing.adapters.celery.beat_schedule import configure_selfhealing_celery
    configure_selfhealing_celery(app)

    # Option 2: Manual merge
    from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule
    app.conf.beat_schedule.update(get_selfhealing_beat_schedule())
"""

from __future__ import annotations

from typing import Any

import structlog
from kombu import Exchange, Queue

logger = structlog.get_logger()


# =============================================================================
# kombu Queue/Exchange Definitions (321 — Beat Internalization, Q4)
# =============================================================================

_selfhealing_exchange = Exchange("selfhealing", type="direct", durable=True)

_selfhealing_dlx = Exchange("selfhealing.dlx", type="direct", durable=True)

_QUEUE_DEFINITIONS: list[Queue] = [
    # Cleanup Lane
    Queue(
        "maintenance",
        exchange=_selfhealing_exchange,
        routing_key="maintenance",
        queue_arguments={
            "x-max-priority": 3,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "critical_maintenance",
        exchange=_selfhealing_exchange,
        routing_key="critical_maintenance",
        queue_arguments={
            "x-max-priority": 10,
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": "selfhealing.dlx",
        },
    ),
    # Intelligence Lane
    Queue(
        "analysis",
        exchange=_selfhealing_exchange,
        routing_key="analysis",
        queue_arguments={
            "x-max-priority": 5,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "realtime",
        exchange=_selfhealing_exchange,
        routing_key="realtime",
        queue_arguments={
            "x-max-priority": 10,
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": "selfhealing.dlx",
            "x-message-ttl": 30000,
        },
    ),
    # Compliance Lane
    Queue(
        "compliance",
        exchange=_selfhealing_exchange,
        routing_key="compliance",
        queue_arguments={
            "x-max-priority": 7,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "reports",
        exchange=_selfhealing_exchange,
        routing_key="reports",
        queue_arguments={
            "x-max-priority": 2,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "metrics",
        exchange=_selfhealing_exchange,
        routing_key="metrics",
        queue_arguments={
            "x-max-priority": 1,
            "x-queue-type": "quorum",
        },
    ),
    # Audit Flush
    Queue(
        "audit_flush",
        exchange=_selfhealing_exchange,
        routing_key="audit_flush",
        queue_arguments={
            "x-max-priority": 4,
            "x-queue-type": "quorum",
        },
    ),
    # Chaos Engineering
    Queue(
        "chaos",
        exchange=_selfhealing_exchange,
        routing_key="chaos",
        queue_arguments={
            "x-max-priority": 5,
            "x-queue-type": "quorum",
        },
    ),
    Queue(
        "chaos_monitoring",
        exchange=_selfhealing_exchange,
        routing_key="chaos.monitoring",
        queue_arguments={
            "x-max-priority": 6,
            "x-queue-type": "quorum",
        },
    ),
    # Critical (Recovery)
    Queue(
        "selfhealing.critical",
        exchange=Exchange("selfhealing.critical", type="direct", durable=True),
        routing_key="selfhealing.critical",
        queue_arguments={
            "x-max-priority": 10,
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": "selfhealing.dlx",
        },
    ),
]

# Backward-compatible dict (gradual migration support)
SELFHEALING_QUEUE_CONFIG = {
    q.name: {
        "exchange": q.exchange.name,
        "routing_key": q.routing_key,
        "queue_arguments": q.queue_arguments or {},
    }
    for q in _QUEUE_DEFINITIONS
}


# =============================================================================
# Queue Namespace Isolation (321, Q3)
# =============================================================================


def get_selfhealing_queues(prefix: str = "") -> list[Queue]:
    """Return kombu.Queue list with optional namespace prefix.

    When prefix is specified, queue name, Exchange name, and routing key
    all get the prefix applied for broker-level message isolation.
    """
    if not prefix:
        return list(_QUEUE_DEFINITIONS)

    return [
        Queue(
            f"{prefix}.{q.name}",
            exchange=Exchange(
                f"{prefix}.{q.exchange.name}",
                type=q.exchange.type,
                durable=q.exchange.durable,
            ),
            routing_key=f"{prefix}.{q.routing_key}",
            queue_arguments=q.queue_arguments,
        )
        for q in _QUEUE_DEFINITIONS
    ]


# =============================================================================
# Task Routes (321, Q3/Q6)
# =============================================================================

_CRITICAL_TASK_ROUTES = {
    "selfhealing.celery_tasks.execute_recovery_step": "selfhealing.critical",
    "selfhealing.celery_tasks.check_recovery_trigger": "selfhealing.critical",
    "selfhealing.celery_tasks.monitor_recovery_health": "selfhealing.critical",
    "selfhealing.celery_tasks.check_circuit_breaker_recovery": "selfhealing.critical",
}


def get_selfhealing_task_routes(prefix: str = "") -> dict[str, dict[str, str]]:
    """Return task routing configuration for critical selfhealing tasks.

    When prefix is specified, queue names and routing keys include the prefix.
    """
    routes: dict[str, dict[str, str]] = {}
    for task_name, queue_name in _CRITICAL_TASK_ROUTES.items():
        if prefix:
            routes[task_name] = {
                "queue": f"{prefix}.{queue_name}",
                "routing_key": f"{prefix}.{queue_name}",
            }
        else:
            routes[task_name] = {
                "queue": queue_name,
                "routing_key": queue_name,
            }
    return routes


# =============================================================================
# Consolidated Beat Schedule
# =============================================================================


# Module load config: (include_flag_name, module_path, getter_func_name, debug_message)
_SCHEDULE_MODULES = [
    (
        "cleanup",
        "selfhealing.tasks.cleanup_tasks",
        "get_cleanup_beat_schedule",
        "cleanup lane",
    ),
    (
        "intelligence",
        "selfhealing.tasks.intelligence_tasks",
        "get_intelligence_beat_schedule",
        "intelligence lane",
    ),
    (
        "compliance",
        "selfhealing.tasks.compliance_tasks",
        "get_compliance_beat_schedule",
        "compliance lane",
    ),
    (
        "traffic_aware",
        "selfhealing.tasks.traffic_aware_replay",
        "get_traffic_aware_beat_schedule",
        "traffic-aware replay (Track 3)",
    ),
    (
        "canary_watchdog",
        "selfhealing.tasks.canary_watchdog",
        "get_canary_watchdog_beat_schedule",
        "canary watchdog",
    ),
    (
        "governance",
        "selfhealing.tasks.governance",
        "get_governance_beat_schedule",
        "governance (emergency mode expiry)",
    ),
    (
        "xtest_cleanup",
        "selfhealing.tasks.xtest_cleanup_tasks",
        "get_xtest_cleanup_beat_schedule",
        "X-Test cleanup",
    ),
    (
        "audit_flush",
        "selfhealing.tasks.audit_flush",
        "get_audit_flush_beat_schedule",
        "Redis Audit flush",
    ),
    (
        "saga",
        "selfhealing.services.saga.tasks",
        "get_saga_beat_schedule",
        "Saga orchestrator",
    ),
]


def _load_schedule_module(
    module_path: str,
    getter_func_name: str,
    debug_message: str,
) -> dict[str, Any]:
    """Load a single schedule module dynamically."""
    try:
        import importlib

        module = importlib.import_module(module_path)
        getter_func = getattr(module, getter_func_name)
        schedule = getter_func()
        logger.debug(
            "beat_schedule.added_schedules",
            debug_message=debug_message,
        )
        return schedule
    except ImportError as e:
        logger.warning(
            "beat_schedule.load_tasks",
            debug_message=debug_message,
            error=e,
        )
    except AttributeError as e:
        logger.warning(
            "beat_schedule.function_found",
            module_path=module_path,
            error=e,
        )
    return {}


def get_selfhealing_beat_schedule(
    include_cleanup: bool = True,
    include_intelligence: bool = True,
    include_compliance: bool = True,
    include_traffic_aware: bool = True,
    include_canary_watchdog: bool = True,
    include_governance: bool = True,
    include_xtest_cleanup: bool = True,
    include_audit_flush: bool = True,
    include_saga: bool = True,
    include_legacy: bool = True,
) -> dict[str, Any]:
    """Get consolidated Celery Beat schedule for all self-healing tasks.

    Args:
        include_cleanup: Include Cleanup Lane tasks
        include_intelligence: Include Intelligence Lane tasks
        include_compliance: Include Compliance Lane tasks
        include_traffic_aware: Include Traffic-Aware Replay tasks (Track 3)
        include_canary_watchdog: Include Canary Watchdog tasks
        include_governance: Include Governance tasks (emergency mode expiry)
        include_xtest_cleanup: Include X-Test Artifact Cleanup tasks
        include_audit_flush: Include Redis Audit buffer flush tasks
        include_saga: Include Saga Orchestrator tasks (orphan saga scan)
        include_legacy: Include legacy tasks from adapters/celery/tasks.py

    Returns:
        Complete Celery Beat schedule configuration dict.

    Usage:
        from selfhealing.adapters.celery.beat_schedule import get_selfhealing_beat_schedule

        CELERY_BEAT_SCHEDULE = {
            **get_selfhealing_beat_schedule(),
            # ... your custom schedules
        }
    """
    include_flags = {
        "cleanup": include_cleanup,
        "intelligence": include_intelligence,
        "compliance": include_compliance,
        "traffic_aware": include_traffic_aware,
        "canary_watchdog": include_canary_watchdog,
        "governance": include_governance,
        "xtest_cleanup": include_xtest_cleanup,
        "audit_flush": include_audit_flush,
        "saga": include_saga,
    }

    schedule: dict[str, Any] = {}

    for flag_name, module_path, getter_func, debug_msg in _SCHEDULE_MODULES:
        if include_flags.get(flag_name, False):
            schedule.update(_load_schedule_module(module_path, getter_func, debug_msg))

    if include_legacy:
        schedule.update(_get_legacy_beat_schedule())
        logger.debug("beat_schedule.added_legacy_schedules")

    return schedule


def _get_legacy_beat_schedule() -> dict[str, Any]:
    """Legacy tasks from existing adapters/celery/tasks.py.

    These will be gradually migrated to lane-based tasks.
    """
    from celery.schedules import crontab

    return {
        "replay-failed-operations": {
            "task": "selfhealing.adapters.celery.tasks.replay_batch_by_domain",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "dlq"},
            "kwargs": {"max_entries": 50},
        },
        "check-circuit-breaker-recovery-legacy": {
            "task": "selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
            "schedule": crontab(minute="*/2"),
            "options": {"queue": "realtime"},
        },
        "expire-manual-overrides": {
            "task": "selfhealing.adapters.celery.tasks.expire_manual_overrides",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "maintenance"},
        },
    }


# =============================================================================
# Consumer Integration Wrapper (321, Q6)
# =============================================================================


def configure_selfhealing_celery(
    app,
    *,
    include_cleanup: bool = True,
    include_intelligence: bool = True,
    include_compliance: bool = True,
    include_traffic_aware: bool = True,
    include_canary_watchdog: bool = True,
    include_governance: bool = True,
    include_xtest_cleanup: bool = True,
    include_audit_flush: bool = True,
    include_saga: bool = True,
    include_legacy: bool = True,
    queue_prefix: str = "",
) -> None:
    """Inject selfhealing Beat schedule, queues, routes, and tasks into a Celery app.

    Symmetric with configure_selfhealing(namespace=globals()) for Django settings.

    Args:
        app: Celery application instance.
        include_*: Module-level Beat task inclusion flags.
        queue_prefix: Queue namespace prefix for multi-service isolation.
    """
    # 1. Beat Schedule merge
    schedule = get_selfhealing_beat_schedule(
        include_cleanup=include_cleanup,
        include_intelligence=include_intelligence,
        include_compliance=include_compliance,
        include_traffic_aware=include_traffic_aware,
        include_canary_watchdog=include_canary_watchdog,
        include_governance=include_governance,
        include_xtest_cleanup=include_xtest_cleanup,
        include_audit_flush=include_audit_flush,
        include_saga=include_saga,
        include_legacy=include_legacy,
    )
    if not hasattr(app.conf, "beat_schedule") or app.conf.beat_schedule is None:
        app.conf.beat_schedule = {}
    app.conf.beat_schedule.update(schedule)

    # 2. Queue definitions merge (kombu.Queue objects)
    queues = get_selfhealing_queues(prefix=queue_prefix)
    existing = list(app.conf.task_queues or [])
    app.conf.task_queues = existing + queues

    # 3. Task Routes merge
    existing_routes = dict(app.conf.task_routes or {})
    existing_routes.update(get_selfhealing_task_routes(prefix=queue_prefix))
    app.conf.task_routes = existing_routes

    # 4. Task registration
    register_all_tasks_with_celery(app)

    logger.info(
        "beat_schedule.celery_configured",
        queue_prefix=queue_prefix or "(none)",
    )


# =============================================================================
# Schedule Helpers
# =============================================================================


def get_schedule_summary() -> dict[str, Any]:
    """Get human-readable summary of all scheduled tasks.

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

        if queue not in summary["by_queue"]:
            summary["by_queue"][queue] = []
        summary["by_queue"][queue].append(name)

        categorized = False
        for lane, prefixes in lane_prefixes.items():
            if any(name.startswith(prefix) for prefix in prefixes):
                summary["by_lane"][lane].append(name)
                categorized = True
                break

        if not categorized:
            summary["by_lane"]["legacy"].append(name)

    return summary


def validate_schedule() -> dict[str, Any]:
    """Validate schedule configuration.

    Returns:
        Dict with validation results.
    """
    schedule = get_selfhealing_beat_schedule()
    errors = []
    warnings = []

    for name, config in schedule.items():
        if "task" not in config:
            errors.append(f"{name}: missing 'task' field")

        if "schedule" not in config:
            errors.append(f"{name}: missing 'schedule' field")

        queue = config.get("options", {}).get("queue")
        if (
            queue
            and queue not in SELFHEALING_QUEUE_CONFIG
            and queue != "default"
            and queue != "dlq"
        ):
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
    """Register all self-healing tasks with a Celery application.

    Args:
        app: Celery application instance.
    """
    from selfhealing.tasks.compliance_tasks import register_compliance_tasks_with_celery
    from selfhealing.tasks.intelligence_tasks import (
        register_intelligence_tasks_with_celery,
    )
    from selfhealing.tasks.traffic_aware_replay import (
        register_traffic_aware_tasks_with_celery,
    )

    register_intelligence_tasks_with_celery(app)
    register_compliance_tasks_with_celery(app)
    register_traffic_aware_tasks_with_celery(app)

    logger.info("beat_schedule.all_self_healing_tasks")


__all__ = [
    "get_selfhealing_beat_schedule",
    "get_selfhealing_queues",
    "get_selfhealing_task_routes",
    "configure_selfhealing_celery",
    "get_schedule_summary",
    "validate_schedule",
    "register_all_tasks_with_celery",
    "SELFHEALING_QUEUE_CONFIG",
]
