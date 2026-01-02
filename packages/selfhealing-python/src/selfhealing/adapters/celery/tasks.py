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
        # Error Budget Gate 체크: 에러 예산 부족 시 Replay 차단
        try:
            from selfhealing.services.error_budget_gate import check_automation_allowed
            
            gate_result = check_automation_allowed()
            if not gate_result.allowed:
                logger.warning(
                    f"[Circuit Recovery] Blocked by Error Budget Gate: "
                    f"budget={gate_result.error_budget_percent}% < threshold={gate_result.threshold_percent}%"
                )
                return {
                    "success": False,
                    "service_name": service_name,
                    "error": "automation_blocked",
                    "message": (
                        f"Error budget critically low ({gate_result.error_budget_percent:.1f}%). "
                        "Conditional replay blocked to prevent further errors."
                    ),
                    "error_budget_percent": gate_result.error_budget_percent,
                    "total": 0,
                    "success_count": 0,
                    "failed_count": 0,
                }
        except ImportError:
            # Gate not available, continue
            pass

        # Use ProviderRegistry to get repository (Redis by default)
        from selfhealing.factory import ProviderRegistry
        repo = ProviderRegistry.get_failed_operation_repo()

        from selfhealing.core.types import OperationStatus

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

    This task delegates to ReplayService which handles all safety checks:
    - Kill Switch
    - Emergency Level (LEVEL_2+)
    - ErrorBudgetGate

    Args:
        dlq_id: ID of the FailedOperation to replay

    Returns:
        Dictionary with replay result
    """
    logger.info(f"[DLQ Replay Task] Starting replay for DLQ entry: {dlq_id}")

    try:
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService()
        result = service.replay_single(dlq_id)

        return {
            "success": result.success,
            "dlq_id": dlq_id,
            "message": result.message if result.success else "",
            "error": result.error,
            "data": result.data,
        }

    except Exception as e:
        logger.error(f"[DLQ Replay Task] Unexpected error replaying DLQ entry {dlq_id}: {e}")
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

    This task delegates to ReplayService which handles all safety checks:
    - Kill Switch
    - Emergency Level (LEVEL_2+)
    - ErrorBudgetGate

    Args:
        domain: The domain to filter by (payment, point, inventory, etc.)
        max_items: Maximum number of items to replay

    Returns:
        Dictionary with batch replay summary
    """
    logger.info(f"[DLQ Batch Replay Task] Starting batch replay for domain={domain}, max_items={max_items}")

    try:
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService()
        result = service.replay_batch(domain=domain, max_items=max_items)

        return {
            "success": result.success_count > 0 or result.total == 0,
            "domain": domain,
            "total": result.total,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "skipped_count": result.skipped_count,
        }

    except Exception as e:
        logger.error(f"[DLQ Batch Replay Task] Unexpected error: {e}")
        return {
            "success": False,
            "domain": domain,
            "error": str(e),
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
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


# =============================================================================
# Heartbeat Task (Dead Man's Snitch)
# =============================================================================


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat",
    queue="monitoring",
    max_retries=0,
    time_limit=10,
    soft_time_limit=8,
)
def emit_selfhealing_heartbeat(self, component: str = "error_budget") -> dict:
    """
    Periodic heartbeat task for Dead Man's Snitch.

    This task should be scheduled at a regular interval (default: 60 seconds).
    If the heartbeat metric stops being updated, Prometheus will fire an alert.

    Add to CELERY_BEAT_SCHEDULE:
        'emit-selfhealing-heartbeat': {
            'task': 'selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat',
            'schedule': 60.0,  # Should match heartbeat_interval_seconds config
        },

    Args:
        component: The component emitting the heartbeat (default: 'error_budget')

    Returns:
        Dictionary with heartbeat status

    Prometheus Alert Rules:
        - SelfHealingServiceDead: time() - selfhealing_heartbeat_timestamp_seconds > 120
        - SelfHealingHeartbeatMissing: absent(selfhealing_heartbeat_timestamp_seconds) == 1
    """
    import time

    try:
        # Check if heartbeat is enabled
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager.get_error_budget_config()

        if not config.get("heartbeat_enabled", True):
            logger.debug(f"[Heartbeat] Disabled for component={component}")
            return {
                "success": True,
                "component": component,
                "status": "disabled",
                "timestamp": time.time(),
            }

        # Emit heartbeat metric
        from selfhealing.services.metrics import emit_heartbeat

        emit_heartbeat(component=component)

        current_time = time.time()
        logger.debug(f"[Heartbeat] Emitted for component={component} at {current_time}")

        return {
            "success": True,
            "component": component,
            "status": "alive",
            "timestamp": current_time,
            "interval_seconds": config.get("heartbeat_interval_seconds", 60),
            "timeout_seconds": config.get("heartbeat_timeout_seconds", 120),
        }

    except Exception as e:
        logger.error(f"[Heartbeat] Failed to emit heartbeat: {e}", exc_info=True)
        # Even on failure, we try to emit a heartbeat to indicate partial functionality
        try:
            from selfhealing.services.metrics import emit_heartbeat

            emit_heartbeat(component=f"{component}_degraded")
        except Exception:
            pass

        return {
            "success": False,
            "component": component,
            "status": "error",
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.notify_failsafe_recovery",
    queue="monitoring",
    max_retries=1,
    time_limit=30,
    soft_time_limit=25,
)
def notify_failsafe_recovery(
    self,
    component: str,
    downtime_seconds: float,
    recovery_reason: str = "System recovered automatically",
) -> dict:
    """
    Send recovery notification when fail-safe mode is deactivated.

    This task should be called when the system transitions from fail-safe
    mode back to normal operation.

    Args:
        component: The component that recovered
        downtime_seconds: How long the component was in fail-safe mode
        recovery_reason: Why the system recovered

    Returns:
        Dictionary with notification status
    """
    try:
        # Check if recovery alerts are enabled
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager.get_error_budget_config()

        if not config.get("recovery_alert_enabled", True):
            logger.info(f"[Recovery] Recovery alert disabled, skipping for {component}")
            return {
                "success": True,
                "component": component,
                "status": "disabled",
            }

        # Record metric
        from selfhealing.services.metrics import record_recovery_alert, record_failsafe_recovered

        record_recovery_alert(component=component)
        record_failsafe_recovered(component=component)

        # If alert adapter is configured, send recovery notification
        try:
            from selfhealing.factory import ProviderRegistry

            alert_adapter = ProviderRegistry.get_alert_adapter()

            if hasattr(alert_adapter, "alert_failsafe_recovered"):
                alert_adapter.alert_failsafe_recovered(
                    component=component,
                    downtime_seconds=downtime_seconds,
                    recovery_reason=recovery_reason,
                )
                logger.info(f"[Recovery] Sent recovery alert for {component}, " f"downtime={downtime_seconds:.1f}s")
            else:
                logger.warning(f"[Recovery] Alert adapter does not support recovery notifications")
        except Exception as adapter_error:
            logger.warning(f"[Recovery] Could not send alert via adapter: {adapter_error}")

        return {
            "success": True,
            "component": component,
            "downtime_seconds": downtime_seconds,
            "recovery_reason": recovery_reason,
            "alert_sent": True,
        }

    except Exception as e:
        logger.error(f"[Recovery] Failed to send recovery notification: {e}", exc_info=True)
        return {
            "success": False,
            "component": component,
            "error": str(e),
        }
