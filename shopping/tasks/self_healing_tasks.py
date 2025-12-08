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
    from shopping.services.self_healing.replay_service import get_replay_service

    logger.info(
        f"[Circuit Recovery] Starting conditional replay for '{service_name}', "
        f"max_items={max_items}"
    )

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

    This task should be scheduled to run every minute.

    Returns:
        Dictionary with check results
    """
    from django.conf import settings
    from django.utils import timezone

    from shopping.models.failed_payment import CircuitBreakerState

    # Check if circuit breaker is enabled
    self_healing = getattr(settings, "SELF_HEALING", {})
    cb_config = self_healing.get("CIRCUIT_BREAKER", {})

    if not cb_config.get("ENABLED", False):
        return {"success": True, "message": "Circuit breaker disabled, skipping check"}

    recovery_timeout = cb_config.get("RECOVERY_TIMEOUT", 60)

    logger.debug("[Circuit Check] Checking for circuit breakers to transition")

    try:
        now = timezone.now()
        transitioned = []

        # Find OPEN circuits that should transition to HALF_OPEN
        open_circuits = CircuitBreakerState.objects.filter(
            state="open",
            opened_at__isnull=False,
            manually_controlled=False,  # Skip manually controlled circuits
        )

        for circuit in open_circuits:
            elapsed = (now - circuit.opened_at).total_seconds()

            if elapsed >= recovery_timeout:
                circuit.state = "half_open"
                circuit.success_count = 0
                circuit.save(update_fields=["state", "success_count", "updated_at"])

                transitioned.append(circuit.service_name)
                logger.info(
                    f"[Circuit Check] Transitioned '{circuit.service_name}' "
                    f"from OPEN to HALF_OPEN after {elapsed:.0f}s"
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
    from shopping.services.self_healing.circuit_breaker_service import (
        get_circuit_breaker_service,
    )

    logger.warning(
        f"[Circuit Breaker] Force opening circuit for '{service_name}': {reason}"
    )

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
            logger.warning(
                f"[Circuit Breaker] Successfully opened circuit for '{service_name}'"
            )
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
    from shopping.services.self_healing.circuit_breaker_service import (
        get_circuit_breaker_service,
    )

    logger.info(
        f"[Circuit Breaker] Force closing circuit for '{service_name}': {reason}"
    )

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
            logger.info(
                f"[Circuit Breaker] Successfully closed circuit for '{service_name}'"
            )
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
    from shopping.services.self_healing.circuit_breaker_service import (
        get_circuit_breaker_service,
    )

    logger.debug("[Circuit Breaker] Checking for expired manual overrides")

    try:
        service = get_circuit_breaker_service()
        expired = service.check_and_expire_manual_overrides()

        if expired:
            logger.warning(
                f"[Circuit Breaker] Expired manual overrides: {expired}"
            )

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
