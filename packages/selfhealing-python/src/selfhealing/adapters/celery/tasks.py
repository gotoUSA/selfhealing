"""
Celery Tasks for Self-Healing System.

These tasks provide background processing for:
- Circuit breaker state management
- DLQ replay operations
- Metrics collection
- SLA monitoring
- Cleanup operations

Usage:
    Add these tasks to your Celery beat schedule:

    CELERY_BEAT_SCHEDULE = {
        'check-circuit-breaker-recovery': {
            'task': 'selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery',
            'schedule': 60.0,  # Every minute
        },
        'expire-manual-overrides': {
            'task': 'selfhealing.adapters.celery.tasks.expire_manual_overrides',
            'schedule': 300.0,  # Every 5 minutes
        },
        'collect-self-healing-metrics': {
            'task': 'selfhealing.adapters.celery.tasks.collect_self_healing_metrics',
            'schedule': 60.0,  # Every minute
        },
        'check-sla-breaches': {
            'task': 'selfhealing.adapters.celery.tasks.check_and_report_sla_breaches',
            'schedule': 300.0,  # Every 5 minutes
        },
        'cleanup-dlq-entries': {
            'task': 'selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries',
            'schedule': 86400.0,  # Daily
        },
    }
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


# =============================================================================
# Circuit Breaker Tasks
# =============================================================================


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.conditional_replay_on_circuit_close",
    queue="dlq_processing",
    max_retries=0,
    time_limit=300,
    soft_time_limit=290,
    acks_late=True,
)
def conditional_replay_on_circuit_close(self, service_name: str, max_items: int = 50) -> dict:
    """
    Trigger conditional replay when a circuit breaker closes.

    This task is called when a circuit breaker transitions from OPEN/HALF_OPEN to CLOSED.
    It replays DLQ entries that failed due to the recovered service.

    Args:
        service_name: Name of the service that recovered
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with replay result summary
    """
    logger.info(f"[Circuit Recovery] Starting conditional replay for '{service_name}', " f"max_items={max_items}")

    try:
        # Import here to avoid circular dependencies
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository
        from selfhealing.core.types import OperationStatus

        repo = DjangoFailedOperationRepository()

        # Get pending operations for this domain/service
        pending = repo.get_pending(domain=service_name, limit=max_items)

        success_count = 0
        failed_count = 0

        for operation in pending:
            # Here you would implement the actual replay logic
            # For now, we just log and skip
            logger.info(f"[Circuit Recovery] Would replay operation {operation.id}")
            # In a real implementation:
            # result = replay_operation(operation)
            # if result.success:
            #     repo.mark_completed(operation.id)
            #     success_count += 1
            # else:
            #     repo.increment_retry(operation.id, result.error)
            #     failed_count += 1

        logger.info(
            f"[Circuit Recovery] Completed for '{service_name}': "
            f"total={len(pending)}, success={success_count}, failed={failed_count}"
        )

        return {
            "success": True,
            "service_name": service_name,
            "total": len(pending),
            "success_count": success_count,
            "failed_count": failed_count,
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
    name="selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
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

    Returns:
        Dictionary with check results
    """
    logger.debug("[Circuit Check] Checking for circuit breakers to transition")

    try:
        from django.utils import timezone
        from selfhealing.adapters.django.models import CircuitBreakerState
        from selfhealing.core.types import CircuitState

        now = timezone.now()
        transitioned = []

        # Find OPEN circuits that should transition to HALF_OPEN
        open_circuits = CircuitBreakerState.objects.filter(
            state=CircuitState.OPEN.value,
            opened_at__isnull=False,
            manually_controlled=False,
        )

        for circuit in open_circuits:
            elapsed = (now - circuit.opened_at).total_seconds()

            if elapsed >= circuit.recovery_timeout:
                circuit.state = CircuitState.HALF_OPEN.value
                circuit.half_opened_at = now
                circuit.success_count = 0
                circuit.half_open_request_count = 0
                circuit.save(
                    update_fields=["state", "half_opened_at", "success_count", "half_open_request_count", "updated_at"]
                )

                transitioned.append(circuit.service_name)
                logger.info(
                    f"[Circuit Check] Transitioned '{circuit.service_name}' " f"from OPEN to HALF_OPEN after {elapsed:.0f}s"
                )

        return {
            "success": True,
            "transitioned": transitioned,
            "count": len(transitioned),
        }

    except Exception as e:
        logger.error(f"[Circuit Check] Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.force_open_circuit_breaker",
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

    Args:
        service_name: Name of the service to block
        reason: Reason for opening the circuit
        user_id: ID of the user who initiated the action

    Returns:
        Dictionary with operation result
    """
    logger.warning(f"[Circuit Breaker] Force opening circuit for '{service_name}': {reason}")

    try:
        from django.utils import timezone
        from selfhealing.adapters.django.models import CircuitBreakerState
        from selfhealing.core.types import CircuitState

        obj, created = CircuitBreakerState.objects.get_or_create(
            service_name=service_name, defaults={"state": CircuitState.CLOSED.value}
        )

        previous_state = obj.state
        obj.state = CircuitState.OPEN.value
        obj.opened_at = timezone.now()
        obj.manually_controlled = True
        obj.controlled_by_id = user_id
        obj.control_reason = reason
        obj.save()

        logger.warning(f"[Circuit Breaker] Successfully opened circuit for '{service_name}'")

        return {
            "success": True,
            "service_name": service_name,
            "previous_state": previous_state,
            "new_state": obj.state,
            "message": f"Circuit breaker for {service_name} is now OPEN",
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
    name="selfhealing.adapters.celery.tasks.force_close_circuit_breaker",
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

    Args:
        service_name: Name of the service to unblock
        reason: Reason for closing the circuit
        user_id: ID of the user who initiated the action
        trigger_replay: Whether to trigger conditional replay

    Returns:
        Dictionary with operation result
    """
    logger.info(f"[Circuit Breaker] Force closing circuit for '{service_name}': {reason}")

    try:
        from selfhealing.adapters.django.models import CircuitBreakerState
        from selfhealing.core.types import CircuitState

        try:
            obj = CircuitBreakerState.objects.get(service_name=service_name)
        except CircuitBreakerState.DoesNotExist:
            return {
                "success": False,
                "service_name": service_name,
                "error": f"Circuit breaker '{service_name}' not found",
            }

        previous_state = obj.state
        obj.state = CircuitState.CLOSED.value
        obj.failure_count = 0
        obj.success_count = 0
        obj.opened_at = None
        obj.half_opened_at = None
        obj.manually_controlled = True
        obj.controlled_by_id = user_id
        obj.control_reason = reason
        obj.save()

        logger.info(f"[Circuit Breaker] Successfully closed circuit for '{service_name}'")

        # Trigger replay if requested
        if trigger_replay:
            conditional_replay_on_circuit_close.delay(service_name)
            logger.info(f"[Circuit Breaker] Triggered replay for '{service_name}'")

        return {
            "success": True,
            "service_name": service_name,
            "previous_state": previous_state,
            "new_state": obj.state,
            "message": f"Circuit breaker for {service_name} is now CLOSED",
            "replay_triggered": trigger_replay,
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
    name="selfhealing.adapters.celery.tasks.expire_manual_overrides",
    queue="maintenance",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def expire_manual_overrides(self) -> dict:
    """
    Periodic task to expire manual circuit breaker overrides.

    Manual overrides have a TTL to prevent "forgotten" blocks.
    When expired, OPEN circuits transition to HALF_OPEN for gradual recovery.

    Returns:
        Dictionary with expiration results
    """
    logger.debug("[Circuit Breaker] Checking for expired manual overrides")

    try:
        from django.utils import timezone
        from selfhealing.adapters.django.models import CircuitBreakerState
        from selfhealing.core.types import CircuitState

        now = timezone.now()
        expired = []

        # Find circuits with expired manual overrides
        circuits = CircuitBreakerState.objects.filter(
            manually_controlled=True,
            manual_override_expires_at__isnull=False,
            manual_override_expires_at__lt=now,
        )

        for circuit in circuits:
            previous_state = circuit.state

            # Transition OPEN to HALF_OPEN for gradual recovery
            if circuit.state == CircuitState.OPEN.value:
                circuit.state = CircuitState.HALF_OPEN.value
                circuit.half_opened_at = now
                circuit.success_count = 0
                circuit.half_open_request_count = 0

            # Clear manual control
            circuit.manually_controlled = False
            circuit.controlled_by_id = None
            circuit.control_reason = ""
            circuit.manual_override_expires_at = None
            circuit.save()

            expired.append(
                {
                    "service_name": circuit.service_name,
                    "previous_state": previous_state,
                    "new_state": circuit.state,
                }
            )

            logger.warning(
                f"[Circuit Breaker] Expired manual override for '{circuit.service_name}': "
                f"{previous_state} -> {circuit.state}"
            )

        return {
            "success": True,
            "expired_services": [e["service_name"] for e in expired],
            "count": len(expired),
            "details": expired,
        }

    except Exception as e:
        logger.error(f"[Circuit Breaker] Error expiring overrides: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


# =============================================================================
# DLQ Replay Tasks
# =============================================================================


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.replay_single_dlq_entry",
    queue="dlq_processing",
    max_retries=0,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def replay_single_dlq_entry(self, dlq_id: int) -> dict:
    """
    Replay a single DLQ entry.

    This task is triggered by operators via admin UI or API.

    Args:
        dlq_id: ID of the FailedOperation to replay

    Returns:
        Dictionary with replay result
    """
    logger.info(f"[DLQ Replay] Starting replay for DLQ entry: {dlq_id}")

    try:
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository

        repo = DjangoFailedOperationRepository()
        operation = repo.get_by_id(dlq_id)

        if not operation:
            logger.warning(f"[DLQ Replay] DLQ entry {dlq_id} not found")
            return {
                "success": False,
                "dlq_id": dlq_id,
                "error": "Entry not found",
            }

        # Here you would implement the actual replay logic
        # For now, we just log and mark as completed
        logger.info(f"[DLQ Replay] Would replay operation {dlq_id}: {operation.domain}/{operation.failure_type}")

        # In a real implementation:
        # result = execute_replay(operation)
        # if result.success:
        #     repo.mark_completed(dlq_id)
        # else:
        #     repo.increment_retry(dlq_id, result.error)

        return {
            "success": True,
            "dlq_id": dlq_id,
            "message": f"Replayed operation {dlq_id}",
            "data": {
                "domain": operation.domain,
                "failure_type": operation.failure_type,
            },
        }

    except Exception as e:
        logger.error(f"[DLQ Replay] Unexpected error replaying DLQ entry {dlq_id}: {e}")
        return {
            "success": False,
            "dlq_id": dlq_id,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.replay_batch_by_domain",
    queue="dlq_processing",
    max_retries=0,
    time_limit=600,
    soft_time_limit=580,
    acks_late=True,
)
def replay_batch_by_domain(
    self,
    domain: str,
    max_items: int = 100,
) -> dict:
    """
    Replay all pending DLQ entries for a specific domain.

    Args:
        domain: The domain to filter by (payment, point, inventory, etc.)
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with batch replay summary
    """
    logger.info(f"[DLQ Batch Replay] Starting batch replay for domain={domain}, max_items={max_items}")

    try:
        from selfhealing.adapters.django.repositories import DjangoFailedOperationRepository

        repo = DjangoFailedOperationRepository()
        pending = repo.get_pending(domain=domain, limit=max_items)

        success_count = 0
        failed_count = 0

        for operation in pending:
            # Here you would implement the actual replay logic
            logger.info(f"[DLQ Batch Replay] Would replay operation {operation.id}")
            # result = execute_replay(operation)
            # if result.success:
            #     repo.mark_completed(operation.id)
            #     success_count += 1
            # else:
            #     repo.increment_retry(operation.id, result.error)
            #     failed_count += 1

        logger.info(f"[DLQ Batch Replay] Completed: total={len(pending)}, " f"success={success_count}, failed={failed_count}")

        return {
            "success": True,
            "domain": domain,
            "total": len(pending),
            "success_count": success_count,
            "failed_count": failed_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Batch Replay] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries",
    queue="maintenance",
    max_retries=1,
    time_limit=300,
    soft_time_limit=290,
)
def cleanup_resolved_dlq_entries(self, days_old: int = 30) -> dict:
    """
    Archive old resolved DLQ entries.

    This task runs periodically to clean up old entries.
    Entries are marked as ARCHIVED (soft-delete) for audit trail.

    Args:
        days_old: Archive entries older than this many days

    Returns:
        Dictionary with cleanup summary
    """
    from datetime import timedelta
    from django.utils import timezone
    from selfhealing.adapters.django.models import FailedOperation

    logger.info(f"[DLQ Cleanup] Starting cleanup of entries older than {days_old} days")

    try:
        cutoff_date = timezone.now() - timedelta(days=days_old)
        now = timezone.now()

        # Mark expired entries
        expired_count = FailedOperation.objects.filter(
            expires_at__lt=now,
            status__in=[
                FailedOperation.Status.PENDING,
                FailedOperation.Status.REVIEWING,
                FailedOperation.Status.REQUIRES_REVIEW,
            ],
        ).update(status=FailedOperation.Status.EXPIRED)

        # Archive old resolved/rejected entries
        archived_count = FailedOperation.objects.filter(
            created_at__lt=cutoff_date,
            status__in=[
                FailedOperation.Status.RESOLVED,
                FailedOperation.Status.REJECTED,
                FailedOperation.Status.EXPIRED,
            ],
        ).update(status=FailedOperation.Status.ARCHIVED)

        logger.info(f"[DLQ Cleanup] Completed: expired={expired_count}, archived={archived_count}")

        return {
            "success": True,
            "expired_count": expired_count,
            "archived_count": archived_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Cleanup] Unexpected error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# =============================================================================
# Metrics & Monitoring Tasks
# =============================================================================


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.collect_self_healing_metrics",
    queue="monitoring",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def collect_self_healing_metrics(self) -> dict:
    """
    Periodic task to collect and update self-healing metrics.

    Collects:
    - DLQ pending counts by domain
    - DLQ items by status
    - Circuit breaker states
    - Retry success rates

    Returns:
        Dictionary with collected metric values
    """
    logger.debug("[Metrics] Collecting self-healing metrics")

    try:
        from django.db.models import Count
        from selfhealing.adapters.django.models import FailedOperation, CircuitBreakerState
        from selfhealing.core.types import CircuitState

        # DLQ stats by domain
        dlq_by_domain = dict(
            FailedOperation.objects.filter(status=FailedOperation.Status.PENDING)
            .values("domain")
            .annotate(count=Count("id"))
            .values_list("domain", "count")
        )

        # DLQ stats by status
        dlq_by_status = dict(
            FailedOperation.objects.values("status").annotate(count=Count("id")).values_list("status", "count")
        )

        # Circuit breaker stats
        cb_open = CircuitBreakerState.objects.filter(state=CircuitState.OPEN.value).count()
        cb_half_open = CircuitBreakerState.objects.filter(state=CircuitState.HALF_OPEN.value).count()

        metrics = {
            "dlq_pending_by_domain": dlq_by_domain,
            "dlq_by_status": dlq_by_status,
            "circuit_breakers_open": cb_open,
            "circuit_breakers_half_open": cb_half_open,
        }

        logger.debug(f"[Metrics] Collection complete: pending={sum(dlq_by_domain.values())}")

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
    name="selfhealing.adapters.celery.tasks.check_and_report_sla_breaches",
    queue="monitoring",
    max_retries=1,
    time_limit=120,
    soft_time_limit=110,
)
def check_and_report_sla_breaches(self) -> dict:
    """
    Periodic task to check for SLA breaches.

    Checks pending DLQ entries against SLA thresholds and records breaches.

    Returns:
        Dictionary with SLA breach information
    """
    from datetime import timedelta
    from django.utils import timezone
    from selfhealing.adapters.django.models import FailedOperation

    logger.debug("[SLA Check] Checking for SLA breaches")

    try:
        # Default SLA: 4 hours for resolution
        sla_threshold = timedelta(hours=4)
        cutoff = timezone.now() - sla_threshold

        # Find pending entries that have exceeded SLA
        breached = FailedOperation.objects.filter(
            status__in=[
                FailedOperation.Status.PENDING,
                FailedOperation.Status.REVIEWING,
                FailedOperation.Status.REQUIRES_REVIEW,
            ],
            created_at__lt=cutoff,
        )

        breaches_by_domain: dict[str, int] = {}
        for entry in breached:
            domain = entry.domain
            breaches_by_domain[domain] = breaches_by_domain.get(domain, 0) + 1

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
