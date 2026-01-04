"""
Chaos Scheduler Celery Tasks

Celery Beat-based tasks for autonomous chaos experiment execution.

Thin Task, Fat Service Architecture:
    - 이 파일의 Celery Task들은 단순 위임자 역할만 수행
    - 모든 비즈니스 로직은 ChaosExecutionService에서 처리
    - 안전 체크 (Kill Switch, ErrorBudget)는 서비스 레이어에서 수행

Features:
- Scheduled experiment execution
- Pre-flight safety checks (via ChaosExecutionService)
- Daily resilience report generation
- Pending approval cleanup

Reference:
- docs/self_healing/CHAOS_ENGINEERING.md
- docs/self_healing/17_SYSTEM_ARCHITECTURE_DIAGRAM.md §8
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# =============================================================================
# Task Wrappers (Celery-agnostic for testing)
# =============================================================================


def run_scheduled_experiments(task_id: str = None) -> Dict[str, Any]:
    """
    Run scheduled chaos experiments.
    
    This function is a thin wrapper that delegates to ChaosExecutionService.
    All governance checks and safety validations are performed in the service layer.
    
    Audit 기록 (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md):
    - CHAOS_EXPERIMENT_STARTED/COMPLETED 이벤트 기록
    
    Called at regular intervals (default: every 5 minutes) via Celery Beat.
    
    Args:
        task_id: Celery task ID (for audit tracking)
    
    Returns:
        Summary of execution results
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    
    try:
        service = get_chaos_execution_service()
        result = service.run_scheduled_experiments()
        result_dict = result.to_dict()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            status = "completed" if result_dict.get("success", True) else "failed"
            log_chaos_scheduler_audit(
                action="scheduled",
                status=status,
                task_id=task_id,
                details={
                    "executed_count": result_dict.get("executed_count", 0),
                    "skipped_count": result_dict.get("skipped_count", 0),
                    "blocked_count": result_dict.get("blocked_count", 0),
                },
            )
        except Exception as audit_error:
            logger.debug(f"[ChaosScheduler] Audit logging failed: {audit_error}")
        
        return result_dict
        
    except Exception as e:
        # === Audit 기록 (실패) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            log_chaos_scheduler_audit(
                action="scheduled",
                status="failed",
                error_message=str(e),
                task_id=task_id,
            )
        except Exception:
            pass
        raise


def generate_daily_resilience_report(task_id: str = None) -> Dict[str, Any]:
    """
    Generate daily resilience report.
    
    This function is a thin wrapper that delegates to ChaosExecutionService.
    Called once per day (default: 6 AM UTC).
    
    Audit 기록 (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md):
    - 리포트 생성 결과 기록
    
    Args:
        task_id: Celery task ID (for audit tracking)
    
    Returns:
        Report summary
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    
    try:
        service = get_chaos_execution_service()
        result = service.generate_daily_report()
        result_dict = result.to_dict()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            log_chaos_scheduler_audit(
                action="report_generated",
                status="completed",
                task_id=task_id,
                details=result_dict,
            )
        except Exception as audit_error:
            logger.debug(f"[ChaosScheduler] Audit logging failed: {audit_error}")
        
        return result_dict
        
    except Exception as e:
        # === Audit 기록 (실패) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            log_chaos_scheduler_audit(
                action="report_generated",
                status="failed",
                error_message=str(e),
                task_id=task_id,
            )
        except Exception:
            pass
        raise


def cleanup_expired_approvals(task_id: str = None) -> Dict[str, Any]:
    """
    Clean up expired approval requests.
    
    This function is a thin wrapper that delegates to ChaosExecutionService.
    
    Audit 기록 (Phase 4: 20_AUDIT_UNIFICATION_PLAN.md):
    - 만료된 승인 정리 결과 기록
    
    Args:
        task_id: Celery task ID (for audit tracking)
    
    Returns:
        Cleanup summary
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    
    try:
        service = get_chaos_execution_service()
        result = service.cleanup_expired_approvals()
        result_dict = result.to_dict()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            log_chaos_scheduler_audit(
                action="cleanup",
                status="completed",
                task_id=task_id,
                details={
                    "cleaned_count": result_dict.get("cleaned_count", 0),
                },
            )
        except Exception as audit_error:
            logger.debug(f"[ChaosScheduler] Audit logging failed: {audit_error}")
        
        return result_dict
        
    except Exception as e:
        # === Audit 기록 (실패) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            
            log_chaos_scheduler_audit(
                action="cleanup",
                status="failed",
                error_message=str(e),
                task_id=task_id,
            )
        except Exception:
            pass
        raise


def check_and_alert_pending_approvals(task_id: str = None) -> Dict[str, Any]:
    """
    Check for pending approvals and send alerts.
    
    This function is a thin wrapper that delegates to ChaosExecutionService.
    
    Args:
        task_id: Celery task ID (for audit tracking)
    
    Returns:
        Alert summary
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    
    service = get_chaos_execution_service()
    result = service.check_pending_approvals()
    
    return result.to_dict()


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
        return run_scheduled_experiments(task_id=self.request.id)
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report_task",
        bind=True,
        max_retries=3,
        default_retry_delay=300,  # 5 minutes
    )
    def generate_daily_resilience_report_task(self):
        """Celery task wrapper for generate_daily_resilience_report."""
        try:
            return generate_daily_resilience_report(task_id=self.request.id)
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
        return cleanup_expired_approvals(task_id=self.request.id)
    
    @app.task(
        name="selfhealing.tasks.chaos_scheduler.check_pending_approvals_task",
        bind=True,
        max_retries=1,
    )
    def check_pending_approvals_task(self):
        """Celery task wrapper for check_and_alert_pending_approvals."""
        return check_and_alert_pending_approvals(task_id=self.request.id)
    
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
