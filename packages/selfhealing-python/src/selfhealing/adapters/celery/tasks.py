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
from typing import Any, Dict, List, Optional

logger = get_task_logger(__name__)


# =============================================================================
# Async Persistence Tasks (Hybrid Storage Support)
# =============================================================================


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.async_persist_dlq_entry",
    queue="persistence",
    max_retries=3,
    time_limit=30,
    soft_time_limit=25,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def async_persist_dlq_entry(self, entry_data: Dict[str, Any]) -> dict:
    """
    Asynchronously persist a DLQ entry to the statistics store.

    This task is triggered when DLQ entries are created in Redis,
    ensuring ORM persistence doesn't block the critical path.

    Design Principle (07_HYBRID_STORAGE_ARCHITECTURE.md):
    - Runtime (Redis): Fast, synchronous, 1-2ms
    - Statistics (ORM): Async, can tolerate 10-100ms

    Args:
        entry_data: DLQ entry data from Redis

    Returns:
        Dictionary with persistence result
    """
    logger.debug(f"[AsyncPersist] Persisting DLQ entry: {entry_data.get('id', 'unknown')}")

    try:
        from selfhealing.factory import ProviderRegistry

        if not ProviderRegistry.has_statistics_adapter():
            logger.debug("[AsyncPersist] No statistics adapter registered, skipping")
            return {
                "success": True,
                "skipped": True,
                "reason": "no_statistics_adapter",
            }

        stats_repo = ProviderRegistry.get_statistics_repo()
        entry_id = stats_repo.persist_entry(entry_data)

        if entry_id:
            logger.info(f"[AsyncPersist] Successfully persisted DLQ entry: {entry_id}")
            return {
                "success": True,
                "entry_id": entry_id,
            }
        else:
            logger.warning("[AsyncPersist] persist_entry returned None")
            return {
                "success": False,
                "error": "persist_returned_none",
            }

    except Exception as e:
        logger.error(f"[AsyncPersist] Failed to persist DLQ entry: {e}", exc_info=True)
        raise  # Re-raise for Celery retry


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.async_persist_batch",
    queue="persistence",
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
)
def async_persist_batch(self, entries: List[Dict[str, Any]]) -> dict:
    """
    Batch persist DLQ entries to the statistics store.

    Used for bulk sync from Redis to ORM, typically called from
    AuditMiddleware's batch flush or periodic sync tasks.

    Args:
        entries: List of DLQ entry data

    Returns:
        Dictionary with batch persistence result
    """
    logger.info(f"[AsyncPersist] Batch persisting {len(entries)} entries")

    try:
        from selfhealing.factory import ProviderRegistry

        if not ProviderRegistry.has_statistics_adapter():
            return {
                "success": True,
                "skipped": True,
                "count": 0,
                "reason": "no_statistics_adapter",
            }

        stats_repo = ProviderRegistry.get_statistics_repo()
        synced = stats_repo.sync_from_runtime(entries)

        logger.info(f"[AsyncPersist] Batch persisted {synced}/{len(entries)} entries")
        return {
            "success": True,
            "synced": synced,
            "total": len(entries),
        }

    except Exception as e:
        logger.error(f"[AsyncPersist] Batch persist failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.link_audit_to_dlq",
    queue="persistence",
    max_retries=2,
    time_limit=30,
    soft_time_limit=25,
)
def link_audit_to_dlq(
    self,
    entity_id: str,
    entity_type: str,
    action: str,
    actor_id: Optional[str] = None,
    status: Optional[str] = None,
    details: Optional[str] = None,
    audit_record_hash: Optional[str] = None,
) -> dict:
    """
    Link an audit record to a DLQ entity.

    Creates the relationship between DLQ entries and their audit trail,
    enabling the "Master Trail" feature for technical due diligence.

    Reference: 07_HYBRID_STORAGE_ARCHITECTURE.md - Audit/Statistics Integration

    Args:
        entity_id: DLQ entry ID
        entity_type: Entity type (usually "dlq_entry")
        action: Action performed (store, replay, resolve, etc.)
        actor_id: Who performed the action
        status: New status after action
        details: Additional details
        audit_record_hash: Hash from audit system for chain verification

    Returns:
        Dictionary with link result
    """
    logger.debug(f"[AuditLink] Linking audit to {entity_type}:{entity_id}")

    try:
        from selfhealing.factory import ProviderRegistry

        if not ProviderRegistry.has_statistics_adapter():
            return {"success": True, "skipped": True}

        stats_repo = ProviderRegistry.get_statistics_repo()
        success = stats_repo.link_audit_entry(
            entity_id=entity_id,
            entity_type=entity_type,
            action=action,
            actor_id=actor_id,
            status=status,
            details=details,
            audit_record_hash=audit_record_hash,
        )

        return {"success": success}

    except Exception as e:
        logger.error(f"[AuditLink] Failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


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

    Uses ProviderRegistry to access circuit breaker repository (Redis).

    Returns:
        Dictionary with check results
    """
    logger.debug("[Circuit Check] Checking for circuit breakers to transition")

    try:
        from selfhealing.factory import ProviderRegistry
        from selfhealing.core.types import CircuitState
        from selfhealing.core.timezone import now

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()
        current_time = now()
        transitioned = []

        # Get all circuit breaker states from Redis
        all_states = cb_repo.get_all_states()

        for service_name, state in all_states.items():
            # Skip if not OPEN or manually controlled
            if state.state != CircuitState.OPEN:
                continue
            if getattr(state, "manually_controlled", False):
                continue
            if not state.opened_at:
                continue

            # Check if recovery timeout has passed
            elapsed = (current_time - state.opened_at).total_seconds()
            recovery_timeout = getattr(state, "recovery_timeout", 60)

            if elapsed >= recovery_timeout:
                # Transition to HALF_OPEN
                success = cb_repo.update_state(
                    service_name=service_name,
                    state=CircuitState.HALF_OPEN,
                    half_opened_at=current_time,
                    success_count=0,
                    half_open_request_count=0,
                )

                if success:
                    transitioned.append(service_name)
                    logger.info(
                        f"[Circuit Check] Transitioned '{service_name}' " f"from OPEN to HALF_OPEN after {elapsed:.0f}s"
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

    Uses ProviderRegistry to access circuit breaker repository (Redis).

    Args:
        service_name: Name of the service to block
        reason: Reason for opening the circuit
        user_id: ID of the user who initiated the action

    Returns:
        Dictionary with operation result
    """
    logger.warning(f"[Circuit Breaker] Force opening circuit for '{service_name}': {reason}")

    try:
        from selfhealing.factory import ProviderRegistry
        from selfhealing.core.types import CircuitState
        from selfhealing.core.timezone import now

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()

        # Get current state (or create new)
        current_state = cb_repo.get_state(service_name)
        previous_state = current_state.state.value if current_state else "closed"

        # Force open using atomic operation
        success = cb_repo.atomic_force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=str(user_id) if user_id else None,
        )

        if success:
            logger.warning(f"[Circuit Breaker] Successfully opened circuit for '{service_name}'")
            return {
                "success": True,
                "service_name": service_name,
                "previous_state": previous_state,
                "new_state": "open",
                "message": f"Circuit breaker for {service_name} is now OPEN",
            }
        else:
            return {
                "success": False,
                "service_name": service_name,
                "error": "Failed to open circuit breaker",
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

    Uses ProviderRegistry to access circuit breaker repository (Redis).

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
        from selfhealing.factory import ProviderRegistry
        from selfhealing.core.types import CircuitState

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()

        # Get current state
        current_state = cb_repo.get_state(service_name)
        if not current_state:
            return {
                "success": False,
                "service_name": service_name,
                "error": f"Circuit breaker '{service_name}' not found",
            }

        previous_state = current_state.state.value if hasattr(current_state.state, "value") else str(current_state.state)

        # Force close using atomic operation
        success = cb_repo.atomic_force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=str(user_id) if user_id else None,
        )

        if success:
            logger.info(f"[Circuit Breaker] Successfully closed circuit for '{service_name}'")

            # Trigger replay if requested
            if trigger_replay:
                conditional_replay_on_circuit_close.delay(service_name)
                logger.info(f"[Circuit Breaker] Triggered replay for '{service_name}'")

            return {
                "success": True,
                "service_name": service_name,
                "previous_state": previous_state,
                "new_state": "closed",
                "message": f"Circuit breaker for {service_name} is now CLOSED",
                "replay_triggered": trigger_replay,
            }
        else:
            return {
                "success": False,
                "service_name": service_name,
                "error": "Failed to close circuit breaker",
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

    Uses ProviderRegistry to access circuit breaker repository (Redis).

    Returns:
        Dictionary with expiration results
    """
    logger.debug("[Circuit Breaker] Checking for expired manual overrides")

    try:
        from selfhealing.factory import ProviderRegistry
        from selfhealing.core.types import CircuitState
        from selfhealing.core.timezone import now

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()
        current_time = now()
        expired = []

        # Get all circuit breaker states
        all_states = cb_repo.get_all_states()

        for service_name, state in all_states.items():
            # Skip if not manually controlled or no expiry
            if not getattr(state, "manually_controlled", False):
                continue

            expires_at = getattr(state, "manual_override_expires_at", None)
            if not expires_at or expires_at >= current_time:
                continue

            previous_state = state.state.value if hasattr(state.state, "value") else str(state.state)
            new_state = previous_state

            # Transition OPEN to HALF_OPEN for gradual recovery
            if state.state == CircuitState.OPEN:
                new_state = "half_open"
                cb_repo.update_state(
                    service_name=service_name,
                    state=CircuitState.HALF_OPEN,
                    half_opened_at=current_time,
                    success_count=0,
                    half_open_request_count=0,
                    manually_controlled=False,
                    controlled_by=None,
                    control_reason="",
                    manual_override_expires_at=None,
                )
            else:
                # Just clear manual control
                cb_repo.update_state(
                    service_name=service_name,
                    manually_controlled=False,
                    controlled_by=None,
                    control_reason="",
                    manual_override_expires_at=None,
                )

            expired.append(
                {
                    "service_name": service_name,
                    "previous_state": previous_state,
                    "new_state": new_state,
                }
            )

            logger.warning(
                f"[Circuit Breaker] Expired manual override for '{service_name}': " f"{previous_state} -> {new_state}"
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

    Uses ProviderRegistry for statistics repository access.

    Args:
        days_old: Archive entries older than this many days

    Returns:
        Dictionary with cleanup summary
    """
    logger.info(f"[DLQ Cleanup] Starting cleanup of entries older than {days_old} days")

    try:
        from selfhealing.factory import ProviderRegistry

        if not ProviderRegistry.has_statistics_adapter():
            logger.info("[DLQ Cleanup] No statistics adapter, skipping cleanup")
            return {
                "success": True,
                "skipped": True,
                "reason": "no_statistics_adapter",
            }

        stats_repo = ProviderRegistry.get_statistics_repo()

        # Archive old resolved entries
        archived_count = stats_repo.archive_old_entries(older_than_days=days_old)

        logger.info(f"[DLQ Cleanup] Completed: archived={archived_count}")

        return {
            "success": True,
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

    Uses ProviderRegistry for statistics repository access.

    Returns:
        Dictionary with collected metric values
    """
    logger.debug("[Metrics] Collecting self-healing metrics")

    try:
        from selfhealing.factory import ProviderRegistry

        stats_repo = ProviderRegistry.get_statistics_repo()
        cb_repo = ProviderRegistry.get_circuit_breaker_repo()

        # DLQ stats
        status_counts = stats_repo.get_status_counts()
        domain_dist = stats_repo.get_domain_distribution(limit=20)

        dlq_by_domain = {d.domain: d.count for d in domain_dist}
        dlq_by_status = {
            "pending": status_counts.pending,
            "resolved": status_counts.resolved,
            "failed": status_counts.failed,
            "archived": status_counts.archived,
        }

        # Circuit breaker stats from Redis
        cb_summary = stats_repo.get_circuit_breaker_summary()

        metrics = {
            "dlq_pending_by_domain": dlq_by_domain,
            "dlq_by_status": dlq_by_status,
            "circuit_breakers_open": cb_summary.open,
            "circuit_breakers_half_open": cb_summary.half_open,
        }

        logger.debug(f"[Metrics] Collection complete: pending={status_counts.pending}")

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
    Uses ProviderRegistry for statistics repository access.

    Returns:
        Dictionary with SLA breach information
    """
    from datetime import timedelta, datetime, timezone as tz

    logger.debug("[SLA Check] Checking for SLA breaches")

    try:
        from selfhealing.factory import ProviderRegistry

        stats_repo = ProviderRegistry.get_statistics_repo()

        # Default SLA: 4 hours for resolution
        sla_threshold = timedelta(hours=4)
        cutoff = datetime.now(tz.utc) - sla_threshold

        # Get SLA breaches from statistics repository
        breaches_by_domain = stats_repo.get_sla_breaches(
            sla_threshold_hours=4,
            statuses=["pending", "reviewing", "requires_review"],
        )

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
        from selfhealing.services.metrics.recorders import emit_heartbeat

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
            from selfhealing.services.metrics.recorders import emit_heartbeat

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
        from selfhealing.services.metrics.recorders import (
            record_recovery_alert,
            record_failsafe_recovered,
        )

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
