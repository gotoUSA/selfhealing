"""
Chaos Scheduler Celery Tasks

Celery Beat-based tasks for autonomous chaos experiment execution.

Features:
- Scheduled experiment execution
- Pre-flight safety checks
- Daily resilience report generation
- Pending approval cleanup

Reference: docs/self_healing/CHAOS_ENGINEERING.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Task Wrappers (Celery-agnostic for testing)
# =============================================================================


def run_scheduled_experiments() -> Dict[str, Any]:
    """
    Run scheduled chaos experiments.
    
    This is the main entry point for the Celery Beat scheduler.
    Called at regular intervals (default: every 5 minutes).
    
    Workflow:
    1. Get due experiments from scheduler
    2. Run pre-flight safety checks
    3. Execute approved experiments
    4. Record results
    
    Returns:
        Summary of execution results
    """
    from selfhealing.services.chaos.scheduler import get_chaos_scheduler
    from selfhealing.services.chaos.safety_guard import get_safety_guard
    
    results = {
        "checked": 0,
        "executed": 0,
        "skipped": 0,
        "blocked": 0,
        "errors": [],
        "experiments": [],
    }
    
    try:
        scheduler = get_chaos_scheduler()
        safety_guard = get_safety_guard()
        
        # Get experiments that are due for execution
        due_experiments = scheduler.get_due_experiments()
        results["checked"] = len(due_experiments)
        
        if not due_experiments:
            logger.debug("[ChaosScheduler] No experiments due for execution")
            return results
        
        logger.info(f"[ChaosScheduler] Found {len(due_experiments)} due experiments")
        
        for experiment in due_experiments:
            try:
                # Check if globally killed
                if scheduler.is_kill_switch_active():
                    logger.warning(
                        f"[ChaosScheduler] Kill switch active, skipping {experiment.id}"
                    )
                    results["blocked"] += 1
                    results["experiments"].append({
                        "id": experiment.id,
                        "status": "blocked",
                        "reason": "kill_switch_active",
                    })
                    continue
                
                # Pre-flight safety check
                safety_result = safety_guard.pre_flight_check(
                    experiment_type=experiment.experiment_type,
                    blast_radius=experiment.blast_radius,
                    target_service=experiment.target_service,
                )
                
                if not safety_result.is_safe:
                    logger.warning(
                        f"[ChaosScheduler] Safety check failed for {experiment.id}: "
                        f"{safety_result.block_reasons}"
                    )
                    scheduler.skip_experiment(
                        experiment.id,
                        reason=f"Safety check failed: {safety_result.block_reasons}",
                    )
                    results["skipped"] += 1
                    results["experiments"].append({
                        "id": experiment.id,
                        "status": "skipped",
                        "reason": str(safety_result.block_reasons),
                    })
                    continue
                
                # Check approval status for high-risk experiments
                if experiment.requires_approval and not experiment.is_approved:
                    logger.info(
                        f"[ChaosScheduler] Experiment {experiment.id} awaiting approval"
                    )
                    results["blocked"] += 1
                    results["experiments"].append({
                        "id": experiment.id,
                        "status": "pending_approval",
                    })
                    continue
                
                # Execute the experiment
                exec_result = scheduler.execute_experiment(experiment.id)
                
                if exec_result.success:
                    results["executed"] += 1
                    results["experiments"].append({
                        "id": experiment.id,
                        "status": "executed",
                        "result": exec_result.to_dict() if hasattr(exec_result, 'to_dict') else str(exec_result),
                    })
                    logger.info(f"[ChaosScheduler] Executed experiment {experiment.id}")
                else:
                    results["errors"].append({
                        "id": experiment.id,
                        "error": str(exec_result.error) if hasattr(exec_result, 'error') else "Unknown error",
                    })
                    logger.error(f"[ChaosScheduler] Failed to execute {experiment.id}")
                
            except Exception as e:
                logger.exception(f"[ChaosScheduler] Error executing {experiment.id}")
                results["errors"].append({
                    "id": experiment.id,
                    "error": str(e),
                })
        
        logger.info(
            f"[ChaosScheduler] Completed: {results['executed']} executed, "
            f"{results['skipped']} skipped, {results['blocked']} blocked"
        )
        
    except Exception as e:
        logger.exception("[ChaosScheduler] Error in run_scheduled_experiments")
        results["errors"].append({"error": str(e)})
    
    return results


def generate_daily_resilience_report() -> Dict[str, Any]:
    """
    Generate daily resilience report.
    
    Called once per day (default: 6 AM UTC) to summarize
    the previous day's chaos experiments and system resilience.
    
    Returns:
        Report summary
    """
    from selfhealing.services.chaos.reports import get_report_generator
    
    result = {
        "success": False,
        "report_id": None,
        "grade": None,
        "error": None,
    }
    
    try:
        generator = get_report_generator()
        report = generator.generate_daily_report()
        
        result["success"] = True
        result["report_id"] = report.report_id
        result["grade"] = report.grade
        result["summary"] = {
            "total_experiments": report.total_experiments,
            "passed": report.passed_count,
            "failed": report.failed_count,
            "sla_compliance": report.sla_compliance_percent,
        }
        
        logger.info(
            f"[ChaosScheduler] Daily report generated: {report.report_id}, "
            f"grade={report.grade}"
        )
        
    except Exception as e:
        logger.exception("[ChaosScheduler] Error generating daily report")
        result["error"] = str(e)
    
    return result


def cleanup_expired_approvals() -> Dict[str, Any]:
    """
    Clean up expired approval requests.
    
    Marks approval requests as expired if they exceed
    the configured timeout (default: 24 hours).
    
    Returns:
        Cleanup summary
    """
    from selfhealing.services.chaos.scheduler import get_chaos_scheduler
    from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
    
    result = {
        "schedule_expired": 0,
        "blast_radius_expired": 0,
        "errors": [],
    }
    
    try:
        scheduler = get_chaos_scheduler()
        manager = get_blast_radius_manager()
        
        # Expire schedule approvals
        result["schedule_expired"] = scheduler.expire_pending_approvals()
        
        # Expire blast radius approvals
        result["blast_radius_expired"] = manager.expire_pending_approvals()
        
        total = result["schedule_expired"] + result["blast_radius_expired"]
        if total > 0:
            logger.info(f"[ChaosScheduler] Expired {total} pending approvals")
        
    except Exception as e:
        logger.exception("[ChaosScheduler] Error cleaning up approvals")
        result["errors"].append(str(e))
    
    return result


def check_and_alert_pending_approvals() -> Dict[str, Any]:
    """
    Check for pending approvals and send alerts.
    
    Notifies operators about pending high-risk experiments
    that require manual approval.
    
    Returns:
        Alert summary
    """
    from selfhealing.services.chaos.scheduler import get_chaos_scheduler
    from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
    
    result = {
        "pending_schedules": 0,
        "pending_blast_radius": 0,
        "alerts_sent": 0,
    }
    
    try:
        scheduler = get_chaos_scheduler()
        manager = get_blast_radius_manager()
        
        pending_schedules = scheduler.list_schedules(pending_approval_only=True)
        pending_blast = manager.get_pending_approvals()
        
        result["pending_schedules"] = len(pending_schedules)
        result["pending_blast_radius"] = len(pending_blast)
        
        total_pending = result["pending_schedules"] + result["pending_blast_radius"]
        
        if total_pending > 0:
            # Send notification via configured channels
            logger.info(
                f"[ChaosScheduler] {total_pending} experiments pending approval"
            )
            
            # Notification integration: Configure SELFHEALING_NOTIFICATION_WEBHOOK
            # environment variable to enable Slack/Teams/PagerDuty notifications.
            # See docs/self_healing/NOTIFICATION_SETUP.md for configuration.
            try:
                from selfhealing.services.notification import send_pending_approval_alert
                send_pending_approval_alert(
                    pending_count=total_pending,
                    schedules=pending_schedules,
                    blast_radius=pending_blast,
                )
                result["alerts_sent"] = 1
            except ImportError:
                # Notification service not configured - log only
                result["alerts_sent"] = 0
                result["notification_status"] = "not_configured"
        
    except Exception as e:
        logger.exception("[ChaosScheduler] Error checking pending approvals")
        result["error"] = str(e)
    
    return result


# =============================================================================
# Celery Task Definitions
# =============================================================================


def register_celery_tasks(app):
    """
    Register chaos scheduler tasks with Celery app.
    
    Args:
        app: Celery application instance
        
    Usage in your celery.py:
        from selfhealing.tasks.chaos_scheduler import register_celery_tasks
        register_celery_tasks(app)
    """
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.run_scheduled_experiments_task",
        bind=True,
        max_retries=0,  # Don't retry chaos experiments
        soft_time_limit=300,  # 5 minute soft limit
        time_limit=360,  # 6 minute hard limit
    )
    def run_scheduled_experiments_task(self):
        """Celery task wrapper for run_scheduled_experiments."""
        return run_scheduled_experiments()
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report_task",
        bind=True,
        max_retries=3,
        default_retry_delay=300,  # 5 minutes
    )
    def generate_daily_resilience_report_task(self):
        """Celery task wrapper for generate_daily_resilience_report."""
        try:
            return generate_daily_resilience_report()
        except Exception as exc:
            logger.exception("[ChaosScheduler] Daily report generation failed")
            raise self.retry(exc=exc)
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.cleanup_expired_approvals_task",
        bind=True,
        max_retries=1,
    )
    def cleanup_expired_approvals_task(self):
        """Celery task wrapper for cleanup_expired_approvals."""
        return cleanup_expired_approvals()
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.check_pending_approvals_task",
        bind=True,
        max_retries=1,
    )
    def check_pending_approvals_task(self):
        """Celery task wrapper for check_and_alert_pending_approvals."""
        return check_and_alert_pending_approvals()
    
    return {
        "run_scheduled_experiments": run_scheduled_experiments_task,
        "generate_daily_report": generate_daily_resilience_report_task,
        "cleanup_approvals": cleanup_expired_approvals_task,
        "check_pending": check_pending_approvals_task,
    }


# =============================================================================
# Celery Beat Schedule Configuration
# =============================================================================


CHAOS_SCHEDULER_BEAT_SCHEDULE = {
    # Run scheduled experiments every 5 minutes during maintenance window
    "chaos-run-scheduled-experiments": {
        "task": "selfhealing.tasks.chaos_scheduler.run_scheduled_experiments_task",
        "schedule": 300.0,  # Every 5 minutes
        "options": {"queue": "chaos"},
    },
    # Generate daily resilience report at 6 AM UTC
    "chaos-daily-resilience-report": {
        "task": "selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report_task",
        "schedule": {
            "hour": 6,
            "minute": 0,
        },
        "options": {"queue": "reports"},
    },
    # Clean up expired approvals every hour
    "chaos-cleanup-expired-approvals": {
        "task": "selfhealing.tasks.chaos_scheduler.cleanup_expired_approvals_task",
        "schedule": 3600.0,  # Every hour
        "options": {"queue": "maintenance"},
    },
    # Check pending approvals every 30 minutes
    "chaos-check-pending-approvals": {
        "task": "selfhealing.tasks.chaos_scheduler.check_pending_approvals_task",
        "schedule": 1800.0,  # Every 30 minutes
        "options": {"queue": "maintenance"},
    },
}


def get_beat_schedule_for_celery():
    """
    Get Celery Beat schedule configuration.
    
    Returns schedule dict compatible with Celery Beat.
    For crontab schedules, import and use celery.schedules.crontab.
    
    Usage in settings.py or celery.py:
        from selfhealing.tasks.chaos_scheduler import get_beat_schedule_for_celery
        app.conf.beat_schedule.update(get_beat_schedule_for_celery())
    
    Returns:
        Dict with Celery Beat schedule configuration
    """
    from celery.schedules import crontab
    
    return {
        # Run scheduled experiments every 5 minutes
        "chaos-run-scheduled-experiments": {
            "task": "selfhealing.tasks.chaos_scheduler.run_scheduled_experiments_task",
            "schedule": 300.0,  # Every 5 minutes
            "options": {"queue": "chaos"},
        },
        # Generate daily resilience report at 6 AM UTC
        "chaos-daily-resilience-report": {
            "task": "selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report_task",
            "schedule": crontab(hour=6, minute=0),
            "options": {"queue": "reports"},
        },
        # Clean up expired approvals every hour
        "chaos-cleanup-expired-approvals": {
            "task": "selfhealing.tasks.chaos_scheduler.cleanup_expired_approvals_task",
            "schedule": 3600.0,  # Every hour
            "options": {"queue": "maintenance"},
        },
        # Check pending approvals every 30 minutes during business hours
        "chaos-check-pending-approvals": {
            "task": "selfhealing.tasks.chaos_scheduler.check_pending_approvals_task",
            "schedule": 1800.0,  # Every 30 minutes
            "options": {"queue": "maintenance"},
        },
    }
