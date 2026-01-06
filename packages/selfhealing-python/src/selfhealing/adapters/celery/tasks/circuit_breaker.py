"""
Circuit Breaker Celery Tasks.

These tasks manage circuit breaker state transitions, recovery checks,
and manual override expiration.

Usage in CELERY_BEAT_SCHEDULE:
    'check-circuit-breaker-recovery': {
        'task': 'selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery',
        'schedule': 60.0,  # Every minute
    },
    'expire-manual-overrides': {
        'task': 'selfhealing.adapters.celery.tasks.expire_manual_overrides',
        'schedule': 300.0,  # Every 5 minutes
    },
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


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
    logger.info(
        f"[Circuit Recovery] Starting conditional replay for '{service_name}', "
        f"max_items={max_items}"
    )

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
            pass

        from selfhealing.factory import ProviderRegistry

        repo = ProviderRegistry.get_failed_operation_repo()

        # Get pending operations for this domain/service
        pending = repo.get_pending(domain=service_name, limit=max_items)

        success_count = 0
        failed_count = 0

        for operation in pending:
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
        from selfhealing.core.timezone import now
        from selfhealing.core.types import CircuitState
        from selfhealing.factory import ProviderRegistry

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()
        current_time = now()
        transitioned = []

        all_states = cb_repo.get_all_states()

        for service_name, state in all_states.items():
            if state.state != CircuitState.OPEN:
                continue
            if getattr(state, "manually_controlled", False):
                continue
            if not state.opened_at:
                continue

            elapsed = (current_time - state.opened_at).total_seconds()
            recovery_timeout = getattr(state, "recovery_timeout", 60)

            if elapsed >= recovery_timeout:
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
                        f"[Circuit Check] Transitioned '{service_name}' "
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

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()

        current_state = cb_repo.get_state(service_name)
        previous_state = current_state.state.value if current_state else "closed"

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

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()

        current_state = cb_repo.get_state(service_name)
        if not current_state:
            return {
                "success": False,
                "service_name": service_name,
                "error": f"Circuit breaker '{service_name}' not found",
            }

        previous_state = (
            current_state.state.value
            if hasattr(current_state.state, "value")
            else str(current_state.state)
        )

        success = cb_repo.atomic_force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=str(user_id) if user_id else None,
        )

        if success:
            logger.info(f"[Circuit Breaker] Successfully closed circuit for '{service_name}'")

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
        from selfhealing.core.timezone import now
        from selfhealing.core.types import CircuitState
        from selfhealing.factory import ProviderRegistry

        cb_repo = ProviderRegistry.get_circuit_breaker_repo()
        current_time = now()
        expired = []

        all_states = cb_repo.get_all_states()

        for service_name, state in all_states.items():
            if not getattr(state, "manually_controlled", False):
                continue

            expires_at = getattr(state, "manual_override_expires_at", None)
            if not expires_at or expires_at >= current_time:
                continue

            previous_state = (
                state.state.value if hasattr(state.state, "value") else str(state.state)
            )
            new_state = previous_state

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
                f"[Circuit Breaker] Expired manual override for '{service_name}': "
                f"{previous_state} -> {new_state}"
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
