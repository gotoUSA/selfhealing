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

"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.settings.chaos import get_chaos_settings

logger = logging.getLogger(__name__)

# 모듈 로드 시점에 설정값 캐싱
_chaos_settings = get_chaos_settings()


# =============================================================================
# Task Wrappers (Celery-agnostic for testing)
# =============================================================================


def run_scheduled_experiments() -> dict[str, Any]:
    """
    Run scheduled chaos experiments.

    This function is a thin wrapper that delegates to ChaosExecutionService.
    All governance checks and safety validations are performed in the service layer.

    Audit 기록:
    - CHAOS_EXPERIMENT_STARTED/COMPLETED 이벤트 기록

    Note: task_id는 task_prerun 시그널에서 celery_context로 자동 설정됨

    Called at regular intervals (default: every 5 minutes) via Celery Beat.

    Returns:
        Summary of execution results
    """
    from selfhealing.services.execution_services import get_chaos_execution_service

    try:
        service = get_chaos_execution_service()
        result = service.run_scheduled_experiments()
        result_dict = result.to_dict()

        # === Audit 기록 ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

            status = "completed" if result_dict.get("success", True) else "failed"
            log_chaos_scheduler_audit(
                action="scheduled",
                status=status,
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
            )
        except Exception:
            pass
        raise


def generate_daily_resilience_report() -> dict[str, Any]:
    """
    Generate daily resilience report.

    This function is a thin wrapper that delegates to ChaosExecutionService.
    Called once per day (default: 6 AM UTC).

    Audit 기록:
    - 리포트 생성 결과 기록

    Note: task_id는 task_prerun 시그널에서 celery_context로 자동 설정됨

    Returns:
        Report summary
    """
    from selfhealing.services.execution_services import get_chaos_execution_service

    try:
        service = get_chaos_execution_service()
        result = service.generate_daily_report()
        result_dict = result.to_dict()

        # === Audit 기록 ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

            log_chaos_scheduler_audit(
                action="report_generated",
                status="completed",
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
            )
        except Exception:
            pass
        raise


def cleanup_expired_approvals() -> dict[str, Any]:
    """
    Clean up expired approval requests.

    This function is a thin wrapper that delegates to ChaosExecutionService.

    Audit 기록:
    - 만료된 승인 정리 결과 기록

    Note: task_id는 task_prerun 시그널에서 celery_context로 자동 설정됨

    Returns:
        Cleanup summary
    """
    from selfhealing.services.execution_services import get_chaos_execution_service

    try:
        service = get_chaos_execution_service()
        result = service.cleanup_expired_approvals()
        result_dict = result.to_dict()

        # === Audit 기록 ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit

            log_chaos_scheduler_audit(
                action="cleanup",
                status="completed",
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
            )
        except Exception:
            pass
        raise


def check_and_alert_pending_approvals() -> dict[str, Any]:
    """
    Check for pending approvals and send alerts.

    This function is a thin wrapper that delegates to ChaosExecutionService.

    Note: task_id는 task_prerun 시그널에서 celery_context로 자동 설정됨

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
        max_retries=_chaos_settings.scheduler_experiment_max_retries,
        soft_time_limit=_chaos_settings.scheduler_experiment_soft_time_limit,
        time_limit=_chaos_settings.scheduler_experiment_time_limit,
    )
    def run_scheduled_experiments_task(self):
        """Celery task wrapper for run_scheduled_experiments."""
        return run_scheduled_experiments()

    @app.task(
        name="selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report_task",
        bind=True,
        max_retries=_chaos_settings.scheduler_report_max_retries,
        default_retry_delay=_chaos_settings.scheduler_report_retry_delay,
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
        max_retries=_chaos_settings.scheduler_cleanup_max_retries,
    )
    def cleanup_expired_approvals_task(self):
        """Celery task wrapper for cleanup_expired_approvals."""
        return cleanup_expired_approvals()

    @app.task(
        name="selfhealing.tasks.chaos_scheduler.check_pending_approvals_task",
        bind=True,
        max_retries=_chaos_settings.scheduler_pending_check_max_retries,
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
        # Zombie Hunter: Hunt orphaned experiments every 1 minute
        "chaos-hunt-zombie-experiments": {
            "task": "selfhealing.tasks.chaos_scheduler.hunt_zombie_experiments_task",
            "schedule": 60.0,  # Every 1 minute
            "options": {"queue": "chaos"},
        },
    }


# =============================================================================
# Zombie Hunter
# =============================================================================


def hunt_zombie_experiments() -> dict[str, Any]:
    """
    Zombie Hunter: 고아 실험 정리 함수.

    RUNNING 상태인데 TTL이 만료된 실험 = 워커 크래시로 간주
    → 분산 락 획득 후 강제 rollback → ABORTED 처리

    Fail-Safe 보장:
    - 워커가 크래시해도 주입된 장애가 운영 환경에 남지 않도록 보장
    - 분산 락으로 중복 rollback 방지

    Returns:
        Dictionary with hunt results:
        - success: bool
        - hunted: int (처리된 좀비 수)
        - skipped: int (락 경쟁으로 스킵된 수)
        - errors: List[Dict] (에러 발생한 실험 정보)
    """
    logger.info("[ZombieHunter] Starting zombie experiment hunt")

    try:
        from selfhealing.services.chaos import get_chaos_scheduler
        from selfhealing.services.chaos.base import ExperimentStatus
        from selfhealing.services.idempotency_service import (
            IdempotencyDomain,
            IdempotencyKey,
            IdempotencyService,
        )

        scheduler = get_chaos_scheduler()
        idempotency = IdempotencyService()

        # RUNNING 상태 실험 조회
        running_experiments = scheduler.get_experiments_by_status(
            ExperimentStatus.RUNNING.value
        )

        hunted = 0
        skipped = 0
        errors = []

        for experiment in running_experiments:
            exp_id = getattr(experiment, "experiment_id", "unknown")

            try:
                # TTL 만료 체크 (Monotonic 지원)
                is_expired = False

                # Monotonic TTL 우선 체크 (ClockSkew 실험 보호)
                if hasattr(experiment, "_is_expired_monotonic"):
                    is_expired = experiment._is_expired_monotonic()
                elif hasattr(experiment, "is_expired"):
                    is_expired = experiment.is_expired()

                if not is_expired:
                    continue  # TTL 아직 유효 → 스킵

                # === 분산 락 획득 (레이스 컨디션 방지) ===
                lock_key = IdempotencyKey(
                    domain=IdempotencyDomain.CHAOS_ZOMBIE_HUNTER,
                    key=f"zombie_rollback:{exp_id}",
                    components={"experiment_id": exp_id},
                )

                if not idempotency.acquire_lock(
                    lock_key, ttl_seconds=_chaos_settings.experiment_lock_ttl
                ):
                    # 다른 스케줄러가 이미 처리 중
                    skipped += 1
                    logger.debug(f"[ZombieHunter] {exp_id} already being handled")
                    continue

                try:
                    logger.warning(f"[ZombieHunter] Zombie detected: {exp_id}")

                    # 강제 rollback
                    if hasattr(experiment, "rollback"):
                        experiment.rollback()

                    # 상태 변경
                    experiment.status = ExperimentStatus.ABORTED

                    # 스케줄러에서 등록 해제
                    scheduler.unregister_experiment_instance(exp_id)

                    hunted += 1
                    logger.info(f"[ZombieHunter] Aborted zombie experiment {exp_id}")

                finally:
                    # 락 해제
                    idempotency.release_lock(lock_key)

            except Exception as e:
                logger.error(f"[ZombieHunter] Failed to abort {exp_id}: {e}")
                errors.append({"experiment_id": exp_id, "error": str(e)})

        result = {
            "success": True,
            "hunted": hunted,
            "skipped": skipped,
            "errors": errors,
        }

        if hunted > 0:
            logger.warning(f"[ZombieHunter] Hunted {hunted} zombie experiments")

        return result

    except Exception as e:
        logger.error(f"[ZombieHunter] Task failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }
