"""
Self-Healing Tasks

Celery tasks for circuit breaker management and conditional replay operations.

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4, §9
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.conditional_replay_on_circuit_close",
    queue="dlq_processing",
    max_retries=0,
    time_limit=300,
    soft_time_limit=290,
    acks_late=True,
)
def conditional_replay_on_circuit_close(self, service_name: str, max_items: int = 50) -> dict:
    """
    Trigger conditional replay when a circuit breaker closes.

    This task is called by CircuitBreakerService.force_close() when
    trigger_replay=True is specified.

    Replays DLQ entries that failed due to the recovered service.

    Args:
        service_name: Name of the service that recovered
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with replay result summary
    """
    from selfhealing.services import get_replay_service

    logger.info(f"[Circuit Recovery] Starting conditional replay for '{service_name}', " f"max_items={max_items}")

    try:
        service = get_replay_service()
        result = service.replay_on_circuit_close(
            service_name=service_name,
            max_items=max_items,
        )

        logger.info(
            f"[Circuit Recovery] Completed for '{service_name}': "
            f"total={result.total}, success={result.success_count}, "
            f"failed={result.failed_count}"
        )

        return {
            "success": True,
            "service_name": service_name,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
        }

    except Exception as e:
        logger.error(
            f"[Circuit Recovery] Failed for '{service_name}': {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.check_circuit_breaker_recovery",
    queue="maintenance",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def check_circuit_breaker_recovery(self) -> dict:
    """
    Periodic task to check for circuit breaker state transitions.

    Checks if any circuit breakers in OPEN state should transition
    to HALF_OPEN based on recovery timeout.

    This task is a thin wrapper that delegates to CircuitBreakerService.
    All business logic is in the service layer.

    This task should be scheduled to run every minute.

    Returns:
        Dictionary with check results
    """
    from selfhealing.services import get_circuit_breaker_service

    logger.debug("[Circuit Check] Checking for circuit breakers to transition")

    try:
        service = get_circuit_breaker_service()
        result = service.check_recovery_transitions()
        
        if result.get("count", 0) > 0:
            logger.info(
                f"[Circuit Check] Transitioned {result['count']} circuit(s): "
                f"{result.get('transitioned', [])}"
            )
        
        return result

    except Exception as e:
        logger.error(f"[Circuit Check] Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.force_open_circuit_breaker",
    queue="critical",
    max_retries=0,
    time_limit=30,
    soft_time_limit=25,
)
def force_open_circuit_breaker(
    self,
    service_name: str,
    reason: str = "",
    user_id: int | None = None,
) -> dict:
    """
    Force open a circuit breaker (block all requests).

    This task can be triggered programmatically or via admin actions.

    Args:
        service_name: Name of the service to block
        reason: Reason for opening the circuit
        user_id: ID of the user who initiated the action

    Returns:
        Dictionary with operation result
    """
    from shopping.models.user import User
    from selfhealing.services import get_circuit_breaker_service

    logger.warning(f"[Circuit Breaker] Force opening circuit for '{service_name}': {reason}")

    try:
        controlled_by = None
        if user_id:
            try:
                controlled_by = User.objects.get(id=user_id)
            except User.DoesNotExist:
                pass

        service = get_circuit_breaker_service()
        result = service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )

        if result.success:
            logger.warning(f"[Circuit Breaker] Successfully opened circuit for '{service_name}'")
            return {
                "success": True,
                "service_name": service_name,
                "previous_state": result.previous_state,
                "new_state": result.new_state,
                "message": result.message,
            }
        else:
            return {
                "success": False,
                "service_name": service_name,
                "error": result.error,
            }

    except Exception as e:
        logger.error(f"[Circuit Breaker] Error opening circuit: {e}", exc_info=True)
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.force_close_circuit_breaker",
    queue="critical",
    max_retries=0,
    time_limit=30,
    soft_time_limit=25,
)
def force_close_circuit_breaker(
    self,
    service_name: str,
    reason: str = "",
    user_id: int | None = None,
    trigger_replay: bool = False,
) -> dict:
    """
    Force close a circuit breaker (allow all requests).

    This task can be triggered programmatically or via admin actions.

    Args:
        service_name: Name of the service to unblock
        reason: Reason for closing the circuit
        user_id: ID of the user who initiated the action
        trigger_replay: Whether to trigger conditional replay

    Returns:
        Dictionary with operation result
    """
    from shopping.models.user import User
    from selfhealing.services import get_circuit_breaker_service

    logger.info(f"[Circuit Breaker] Force closing circuit for '{service_name}': {reason}")

    try:
        controlled_by = None
        if user_id:
            try:
                controlled_by = User.objects.get(id=user_id)
            except User.DoesNotExist:
                pass

        service = get_circuit_breaker_service()
        result = service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )

        if result.success:
            logger.info(f"[Circuit Breaker] Successfully closed circuit for '{service_name}'")
            return {
                "success": True,
                "service_name": service_name,
                "previous_state": result.previous_state,
                "new_state": result.new_state,
                "message": result.message,
            }
        else:
            return {
                "success": False,
                "service_name": service_name,
                "error": result.error,
            }

    except Exception as e:
        logger.error(f"[Circuit Breaker] Error closing circuit: {e}", exc_info=True)
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.expire_manual_overrides",
    queue="maintenance",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def expire_manual_overrides(self) -> dict:
    """
    Periodic task to expire manual circuit breaker overrides.

    Manual overrides have a TTL to prevent "forgotten" blocks.
    When expired:
    - OPEN circuits transition to HALF_OPEN for gradual recovery
    - The manually_controlled flag is cleared

    This ensures operators cannot accidentally leave services blocked
    indefinitely. Default TTL is 90 minutes.

    This task should be scheduled to run every 5 minutes.

    Returns:
        Dictionary with expiration results
    """
    from selfhealing.services import get_circuit_breaker_service

    logger.debug("[Circuit Breaker] Checking for expired manual overrides")

    try:
        service = get_circuit_breaker_service()
        expired = service.check_and_expire_manual_overrides()

        if expired:
            logger.warning(f"[Circuit Breaker] Expired manual overrides: {expired}")

        return {
            "success": True,
            "expired_services": expired,
            "count": len(expired),
        }

    except Exception as e:
        logger.error(f"[Circuit Breaker] Error expiring overrides: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


# =============================================================================
# Observability Tasks (Phase 5)
# =============================================================================


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.collect_self_healing_metrics",
    queue="monitoring",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def collect_self_healing_metrics(self) -> dict:
    """
    Periodic task to collect and update self-healing Prometheus metrics.

    Updates gauge metrics that require database queries:
    - DLQ pending counts by domain
    - DLQ items by status
    - Circuit breaker states
    - Retry success rates

    This task should be scheduled to run every minute.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)

    Returns:
        Dictionary with collected metric values
    """
    from selfhealing.services import collect_all_metrics

    logger.debug("[Metrics] Collecting self-healing metrics")

    try:
        metrics = collect_all_metrics()

        logger.debug(f"[Metrics] Collection complete: " f"pending={sum(metrics.get('dlq_pending_by_domain', {}).values())}")

        return {
            "success": True,
            **metrics,
        }

    except Exception as e:
        logger.error(f"[Metrics] Failed to collect metrics: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="shopping.tasks.self_healing_tasks.check_and_report_sla_breaches",
    queue="monitoring",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_and_report_sla_breaches(self) -> dict:
    """
    Periodic task to check for SLA breaches and record metrics.

    SLA thresholds are configured in services/self_healing/config.py.
    See SLAThresholds class for default values and customization.

    This task should be scheduled to run every 5 minutes.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §3 (Recovery SLA)

    Returns:
        Dictionary with SLA breach information
    """
    from selfhealing.services import get_dlq_service, record_sla_breach

    logger.debug("[SLA Check] Checking for SLA breaches")

    try:
        dlq_service = get_dlq_service()
        breached_entries = dlq_service.get_sla_breached_entries()

        breaches_by_domain: dict[str, int] = {}

        for entry in breached_entries:
            domain = entry.domain
            breaches_by_domain[domain] = breaches_by_domain.get(domain, 0) + 1
            record_sla_breach(domain)

        total_breaches = sum(breaches_by_domain.values())

        if total_breaches > 0:
            logger.warning(f"[SLA Check] Found {total_breaches} SLA breaches: {breaches_by_domain}")
        else:
            logger.debug("[SLA Check] No SLA breaches found")

        return {
            "success": True,
            "total_breaches": total_breaches,
            "breaches_by_domain": breaches_by_domain,
        }

    except Exception as e:
        logger.error(f"[SLA Check] Failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


# =============================================================================
# Phase 6: Chaos Recovery Monitoring (32_CHAOS_SYSTEM_INTEGRATION.md §15.3, §22.2.3)
# =============================================================================


@shared_task(
    bind=True,
    name="chaos.check_recovery_monitoring",
    queue="chaos_monitoring",
    max_retries=0,
    time_limit=60,
    soft_time_limit=55,
)
def check_recovery_monitoring_experiments(self) -> dict:
    """
    Check RECOVERY_MONITORING state experiments for Canary recovery completion.
    
    Phase 6: 32_CHAOS_SYSTEM_INTEGRATION.md §15.3, §22.2.3
    
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


# =============================================================================
# Phase 7: Zombie Hunter Adapter (34_CHAOS_SAFETY_MECHANISMS.md §5)
# =============================================================================


@shared_task(
    bind=True,
    name="chaos.hunt_zombie_experiments",
    queue="chaos",
    max_retries=0,
    time_limit=120,
    soft_time_limit=110,
)
def hunt_zombie_experiments(self) -> dict:
    """
    Zombie Hunter: Celery Task 어댑터.
    
    실제 비즈니스 로직은 selfhealing.tasks.chaos_scheduler.hunt_zombie_experiments()에서 처리.
    
    Reference: 34_CHAOS_SAFETY_MECHANISMS.md §5
    
    Returns:
        Dictionary with hunt results
    """
    from selfhealing.tasks.chaos_scheduler import hunt_zombie_experiments as hunt_zombies
    
    return hunt_zombies()
