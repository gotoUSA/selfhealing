"""
Chaos Engineering Celery Tasks

Tasks for chaos engineering safety mechanisms including Zombie Hunter
and Recovery Monitoring.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.hunt_zombie_experiments",
    queue="chaos",
    max_retries=0,
    time_limit=120,
    soft_time_limit=110,
)
def hunt_zombie_experiments(self) -> dict:
    """
    Zombie Hunter: Hunt orphaned experiments after worker crash.
    
    This task runs periodically to find and clean up experiments that
    are stuck in RUNNING state but have exceeded their TTL. This can
    happen when a Celery worker crashes mid-experiment.
    
    Uses distributed lock (IdempotencyDomain.CHAOS_ZOMBIE_HUNTER) to
    prevent race conditions between multiple workers.
    
    Returns:
        Dictionary with hunt results
    """
    from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments as hunt_zombies
    
    return hunt_zombies()


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.check_recovery_monitoring",
    queue="chaos_monitoring",
    max_retries=0,
    time_limit=60,
    soft_time_limit=55,
)
def check_recovery_monitoring_experiments(self) -> dict:
    """
    Check RECOVERY_MONITORING state experiments for Canary recovery completion.
    
    This task should be scheduled via Celery Beat every 30 seconds.
    It polls experiments in RECOVERY_MONITORING state and:
    1. Checks if Canary recovery is complete → marks COMPLETED
    2. Checks if Hard TTL expired → force completes
    
    Returns:
        Dictionary with check results
    """
    logger.info("[ChaosRecoveryMonitor] Checking RECOVERY_MONITORING experiments")
    
    try:
        from selfhealing.services.chaos import get_chaos_scheduler
        from selfhealing.services.chaos.base import ExperimentStatus
        
        scheduler = get_chaos_scheduler()
        monitoring_experiments = scheduler.get_experiments_by_status(
            ExperimentStatus.RECOVERY_MONITORING.value
        )
        
        checked = 0
        completed = 0
        force_completed = 0
        errors = []
        
        for experiment in monitoring_experiments:
            try:
                checked += 1
                exp_id = getattr(experiment, 'experiment_id', 'unknown')
                
                # Check Canary recovery
                canary_status = {}
                if hasattr(experiment, '_verify_canary_recovery'):
                    canary_status = experiment._verify_canary_recovery()
                
                # If not in canary anymore, recovery complete
                if not canary_status.get("in_canary", True):
                    if hasattr(experiment, 'complete_recovery_monitoring'):
                        experiment.complete_recovery_monitoring()
                        completed += 1
                        logger.info(f"[ChaosRecoveryMonitor] Experiment {exp_id} recovery completed")
                        
                        # Unregister from scheduler
                        scheduler.unregister_experiment_instance(exp_id)
                    continue
                
                # Check Hard TTL
                if hasattr(experiment, 'is_hard_ttl_expired') and experiment.is_hard_ttl_expired():
                    if hasattr(experiment, 'force_complete'):
                        experiment.force_complete(reason="hard_ttl_expired")
                        force_completed += 1
                        logger.warning(
                            f"[ChaosRecoveryMonitor] Experiment {exp_id} force completed (Hard TTL)"
                        )
                        
                        # Unregister from scheduler
                        scheduler.unregister_experiment_instance(exp_id)
                        
            except Exception as e:
                exp_id = getattr(experiment, 'experiment_id', 'unknown')
                logger.warning(f"[ChaosRecoveryMonitor] Error checking {exp_id}: {e}")
                errors.append({"experiment_id": exp_id, "error": str(e)})
        
        result = {
            "success": True,
            "checked": checked,
            "completed": completed,
            "force_completed": force_completed,
            "errors": errors,
        }
        
        if checked > 0:
            logger.info(
                f"[ChaosRecoveryMonitor] Checked {checked} experiments: "
                f"{completed} completed, {force_completed} force completed"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"[ChaosRecoveryMonitor] Task failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }
