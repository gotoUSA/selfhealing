"""
Circuit Breaker Celery Tasks

Tasks for managing circuit breaker states and recovery.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.check_circuit_breaker_recovery",
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
    from selfhealing.services import get_circuit_breaker_service

    logger.debug("circuit_check.transition_check_started")

    try:
        service = get_circuit_breaker_service()
        result = service.check_recovery_transitions()

        if result.get("count", 0) > 0:
            logger.info(
                "circuit_check_transitioned_circuit",
                transitioned_count=result["count"],
                transitioned=result.get("transitioned", []),
            )

        return result

    except Exception as e:
        logger.exception(
            "circuit_check_error",
            error=e,
        )
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.expire_manual_overrides",
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

    logger.debug("circuit_breaker.expired_overrides_checked")

    try:
        service = get_circuit_breaker_service()
        expired = service.check_and_expire_manual_overrides()

        if expired:
            logger.warning(
                "circuit_breaker_expired_manual",
                expired=expired,
            )

        return {
            "success": True,
            "expired_services": expired,
            "count": len(expired),
        }

    except Exception as e:
        logger.exception(
            "circuit_breaker_error_expiring",
            error=e,
        )
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.force_open_circuit_breaker",
    queue="critical",
    max_retries=0,
    time_limit=30,
    soft_time_limit=25,
)
def force_open_circuit_breaker(
    self,
    service_name: str,
    reason: str = "",
    controlled_by: object = None,
) -> dict:
    """
    Force open a circuit breaker (block all requests).

    This task can be triggered programmatically or via admin actions.

    Args:
        service_name: Name of the service to block
        reason: Reason for opening the circuit
        controlled_by: Object representing the user who initiated the action (optional)

    Returns:
        Dictionary with operation result
    """
    from selfhealing.services import get_circuit_breaker_service

    logger.warning(
        "circuit_breaker_force_opening",
        service_name=service_name,
        reason=reason,
    )

    try:
        service = get_circuit_breaker_service()
        result = service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )

        if result.success:
            logger.warning(
                "circuit_breaker_successfully_opened",
                service_name=service_name,
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
        logger.exception(
            "circuit_breaker_error_opening",
            error=e,
        )
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.force_close_circuit_breaker",
    queue="critical",
    max_retries=0,
    time_limit=30,
    soft_time_limit=25,
)
def force_close_circuit_breaker(
    self,
    service_name: str,
    reason: str = "",
    controlled_by: object = None,
    trigger_replay: bool = False,
) -> dict:
    """
    Force close a circuit breaker (allow all requests).

    This task can be triggered programmatically or via admin actions.

    Args:
        service_name: Name of the service to unblock
        reason: Reason for closing the circuit
        controlled_by: Object representing the user who initiated the action (optional)
        trigger_replay: Whether to trigger conditional replay

    Returns:
        Dictionary with operation result
    """
    from selfhealing.services import get_circuit_breaker_service

    logger.info(
        "circuit_breaker_force_closing",
        service_name=service_name,
        reason=reason,
    )

    try:
        service = get_circuit_breaker_service()
        result = service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )

        if result.success:
            logger.info(
                "circuit_breaker_successfully_closed",
                service_name=service_name,
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
        logger.exception(
            "circuit_breaker_error_closing",
            error=e,
        )
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.celery_tasks.check_mesh_override_renewals",
    queue="maintenance",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def check_mesh_override_renewals(self) -> dict:
    """
    Periodic task to check mesh coordinator override TTL renewals.

    Renews overrides whose TTL is about to expire if the downstream
    service is still OPEN. Escalates to EmergencyCoordinator if
    max_renewals is exceeded.

    This task should be scheduled to run every 60 seconds (snapshot_interval_seconds).

    Returns:
        Dictionary with renewal check results
    """
    logger.debug("mesh_coordinator.renewal_check_started")

    try:
        from selfhealing.settings.circuit_mesh import get_circuit_mesh_settings

        settings = get_circuit_mesh_settings()
        if not settings.enabled:
            return {"success": True, "message": "Circuit mesh disabled", "count": 0}

        from selfhealing.services.circuit_mesh.mesh_coordinator import (
            get_mesh_coordinator,
        )

        coordinator = get_mesh_coordinator()
        if coordinator is None:
            return {
                "success": True,
                "message": "Mesh coordinator not initialized",
                "count": 0,
            }

        result = coordinator.check_override_renewals()

        if result.get("renewed", 0) > 0 or result.get("escalated", 0) > 0:
            logger.info(
                "mesh_coordinator.renewal_check_completed",
                renewed=result.get("renewed", 0),
                expired=result.get("expired", 0),
                escalated=result.get("escalated", 0),
            )

        return result

    except Exception as e:
        logger.exception("mesh_coordinator.renewal_check_error", error=e)
        return {"success": False, "error": str(e)}
