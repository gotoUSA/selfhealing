"""
Manual Control Mixin for Circuit Breaker Service

Provides manual force open/close and TTL management functionality.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from selfhealing.core.timezone import now
from selfhealing.core.decision_logger import DecisionLogger, ReasonCode

from .config import CircuitBreakerResult, CircuitState

if TYPE_CHECKING:
    from .config import CircuitBreakerConfig
    from selfhealing.interfaces.repositories import CircuitBreakerStateRepository

logger = logging.getLogger(__name__)


def _is_system_enabled() -> bool:
    """Check if self-healing system is enabled (Kill Switch not activated)."""
    try:
        from selfhealing.services.system_control import SystemControlManager
        manager = SystemControlManager()
        return manager.is_enabled()
    except Exception:
        # If SystemControlManager not available, assume enabled
        return True


class ManualControlMixin:
    """
    Mixin class providing manual control functionality for CircuitBreakerService.

    Includes:
    - Force open/close operations
    - Reset operation
    - Manual override TTL management
    - Conditional replay trigger
    """

    # These will be provided by the main service class
    config: "CircuitBreakerConfig"
    repository: "CircuitBreakerStateRepository"

    # =========================================================================
    # Manual Control Operations
    # =========================================================================

    def force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by: Any = None,
        controlled_by_id: int | None = None,
    ) -> CircuitBreakerResult:
        """
        Force the circuit breaker to OPEN state (block all requests).

        Use this when an external service is detected as down
        and you want to stop sending requests.

        This operation uses atomic locking to prevent race conditions
        when multiple operators try to change the state simultaneously.

        Args:
            service_name: Name of the external service
            reason: Reason for opening (for audit)
            controlled_by: User object who initiated the change (for backward compat)
            controlled_by_id: User ID who initiated the change

        Returns:
            CircuitBreakerResult with operation outcome
        """
        # Kill Switch 체크: 시스템이 비활성화되면 모든 self-healing 작업 중단
        if not _is_system_enabled():
            logger.warning(
                f"[CircuitBreaker] force_open blocked: Kill Switch is active. "
                f"service={service_name}"
            )
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error="Kill Switch is active: self-healing system is disabled",
            )

        # Handle both controlled_by (User object) and controlled_by_id
        if controlled_by_id is None and controlled_by is not None:
            controlled_by_id = getattr(controlled_by, "id", None) or getattr(controlled_by, "pk", None)

        decision_logger = DecisionLogger(service_name=service_name)
        decision_logger.intervention_evaluated(
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )

        # 행동 직전 로깅 - Circuit Breaker OPEN 전에 기록
        logger.info(
            f"[CircuitBreaker] FORCE_OPEN service={service_name}, " f"reason={reason}, controlled_by_id={controlled_by_id}"
        )

        try:
            # Use atomic operation to prevent race conditions
            success, previous_state, new_state = self.repository.atomic_force_open(
                service_name=service_name,
                reason=reason,
                controlled_by_id=controlled_by_id,
                ttl_minutes=self.config.manual_override_ttl_minutes,
            )

            if success:
                if previous_state == new_state:
                    logger.info(f"[CircuitBreaker] Circuit '{service_name}' already open")
                    return CircuitBreakerResult.succeeded(
                        service_name=service_name,
                        previous_state=previous_state,
                        new_state=new_state,
                        message="Circuit breaker already open",
                    )
                else:
                    logger.warning(
                        f"[CircuitBreaker] Force opened circuit for '{service_name}': "
                        f"{previous_state} -> {new_state} | Reason: {reason}"
                    )
                    # Audit 기록 - 수동 OPEN은 중요 운영 이벤트
                    try:
                        from selfhealing.services.audit_helpers import log_cb_state_change_audit
                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=previous_state,
                            new_state=new_state,
                            reason=f"force_open: {reason}" if reason else "force_open: manual",
                        )
                    except Exception as e:
                        logger.debug(f"[CircuitBreaker] Audit log failed: {e}")
                    # Phase 3: Push 이벤트 - CB 상태 변경 메트릭 기록
                    try:
                        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler
                        CircuitBreakerEventHandler.on_state_changed(
                            service=service_name,
                            from_state=previous_state,
                            to_state=new_state,
                        )
                    except ImportError:
                        pass  # Metrics not available
                    return CircuitBreakerResult.succeeded(
                        service_name=service_name,
                        previous_state=previous_state,
                        new_state=new_state,
                        message=f"Circuit breaker opened for {service_name}",
                    )
            else:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error="Failed to force open circuit breaker",
                )
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to force open: {e}")
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=str(e),
            )

    def force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by: Any = None,
        controlled_by_id: int | None = None,
        trigger_replay: bool = False,
    ) -> CircuitBreakerResult:
        """
        Force the circuit breaker to CLOSED state (allow all requests).

        Use this when an external service has recovered
        and you want to resume normal operations.

        This operation uses atomic locking to prevent race conditions
        when multiple operators try to change the state simultaneously.

        Args:
            service_name: Name of the external service
            reason: Reason for closing (for audit)
            controlled_by: User object who initiated the change (for backward compat)
            controlled_by_id: User ID who initiated the change
            trigger_replay: Whether to trigger conditional replay for queued items

        Returns:
            CircuitBreakerResult with operation outcome
        """
        # Kill Switch 체크: 시스템이 비활성화되면 모든 self-healing 작업 중단
        if not _is_system_enabled():
            logger.warning(
                f"[CircuitBreaker] force_close blocked: Kill Switch is active. "
                f"service={service_name}"
            )
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error="Kill Switch is active: self-healing system is disabled",
            )

        # Handle both controlled_by (User object) and controlled_by_id
        if controlled_by_id is None and controlled_by is not None:
            controlled_by_id = getattr(controlled_by, "id", None) or getattr(controlled_by, "pk", None)

        decision_logger = DecisionLogger(service_name=service_name)
        decision_logger.intervention_evaluated(
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )

        # 행동 직전 로깅 - Circuit Breaker CLOSE 전에 기록
        logger.info(
            f"[CircuitBreaker] FORCE_CLOSE service={service_name}, "
            f"reason={reason}, controlled_by_id={controlled_by_id}, trigger_replay={trigger_replay}"
        )

        try:
            # Use atomic operation to prevent race conditions
            success, previous_state, new_state = self.repository.atomic_force_close(
                service_name=service_name,
                reason=reason,
                controlled_by_id=controlled_by_id,
            )

            if success:
                if previous_state == new_state:
                    logger.info(f"[CircuitBreaker] Circuit '{service_name}' already closed")
                    return CircuitBreakerResult.succeeded(
                        service_name=service_name,
                        previous_state=previous_state,
                        new_state=new_state,
                        message="Circuit breaker already closed",
                    )
                else:
                    logger.info(
                        f"[CircuitBreaker] Force closed circuit for '{service_name}': "
                        f"{previous_state} -> {new_state} | Reason: {reason}"
                    )

                    # Audit 기록 - 수동 CLOSE는 중요 복구 이벤트
                    try:
                        from selfhealing.services.audit_helpers import log_cb_state_change_audit
                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=previous_state,
                            new_state=new_state,
                            reason=f"force_close: {reason}" if reason else "force_close: manual",
                        )
                    except Exception as e:
                        logger.debug(f"[CircuitBreaker] Audit log failed: {e}")

                    # Phase 3: Push 이벤트 - CB 상태 변경 메트릭 기록
                    try:
                        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler
                        CircuitBreakerEventHandler.on_state_changed(
                            service=service_name,
                            from_state=previous_state,
                            to_state=new_state,
                        )
                    except ImportError:
                        pass  # Metrics not available

                    result = CircuitBreakerResult.succeeded(
                        service_name=service_name,
                        previous_state=previous_state,
                        new_state=new_state,
                        message=f"Circuit breaker closed for {service_name}",
                    )

                    # Trigger conditional replay if requested
                    if trigger_replay:
                        self._trigger_conditional_replay(service_name)

                    return result
            else:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error="Failed to force close circuit breaker",
                )
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to force close: {e}")
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=str(e),
            )

    # =========================================================================
    # Reset Operations
    # =========================================================================

    def reset(
        self,
        service_name: str,
        controlled_by: int | None = None,
        reason: str = "",
    ) -> CircuitBreakerResult:
        """
        Reset a circuit breaker to initial state.

        Clears all counters and sets state to CLOSED.
        Uses atomic operation to prevent race conditions.

        Args:
            service_name: Name of the external service
            controlled_by: User ID who initiated the reset (optional)
            reason: Reason for reset (for audit)

        Returns:
            CircuitBreakerResult with operation outcome
        """
        try:
            # Use atomic operation to prevent race conditions
            success, previous_state, new_state = self.repository.atomic_reset(
                service_name=service_name,
                reason=reason,
                controlled_by_id=controlled_by,
            )

            if success:
                logger.info(
                    f"[CircuitBreaker] Reset circuit for '{service_name}': "
                    f"{previous_state} -> {new_state} | Reason: {reason}"
                )
                # Audit 기록 - reset은 상태 초기화 이벤트
                if previous_state != new_state:
                    try:
                        from selfhealing.services.audit_helpers import log_cb_state_change_audit
                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=previous_state,
                            new_state=new_state,
                            reason=f"reset: {reason}" if reason else "reset: manual",
                        )
                    except Exception as e:
                        logger.debug(f"[CircuitBreaker] Audit log failed: {e}")
                # Phase 3: Push 이벤트 - CB 상태 변경 메트릭 기록
                if previous_state != new_state:
                    try:
                        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler
                        CircuitBreakerEventHandler.on_state_changed(
                            service=service_name,
                            from_state=previous_state,
                            to_state=new_state,
                        )
                    except ImportError:
                        pass  # Metrics not available
                return CircuitBreakerResult.succeeded(
                    service_name=service_name,
                    previous_state=previous_state,
                    new_state=new_state,
                    message=f"Circuit breaker reset for {service_name}",
                )
            else:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error=f"Circuit breaker for '{service_name}' does not exist",
                )
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to reset: {e}")
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=str(e),
            )

    # =========================================================================
    # Manual Override TTL Management
    # =========================================================================

    def check_and_expire_manual_overrides(self) -> list[str]:
        """
        Check all circuit breakers for expired manual overrides.

        Manual overrides have a TTL to prevent "forgotten" blocks.
        When expired, circuits transition from OPEN to HALF_OPEN
        for gradual recovery testing.

        Returns:
            List of service names that had their overrides expired
        """
        expired_services = []

        try:
            # Get all states and filter manually controlled ones
            all_states = self.repository.get_all_states()
            current_time = now()

            for state in all_states:
                if (
                    state.manually_controlled
                    and state.manual_override_expires_at
                    and state.manual_override_expires_at <= current_time
                ):
                    previous_state = state.state
                    previous_reason = state.control_reason or ""
                    expired_reason = f"{previous_reason} [EXPIRED]".strip()

                    # Expire the override - transition to HALF_OPEN for testing
                    self.repository.update_state(
                        service_name=state.service_name,
                        state=CircuitState.HALF_OPEN,
                    )
                    # Note: If you need to update control_reason,
                    # implement it in your repository adapter

                    self.repository.clear_manual_control(state.service_name, preserve_reason=True)

                    expired_services.append(state.service_name)
                    logger.warning(
                        f"[CircuitBreaker] Manual override expired for '{state.service_name}': "
                        f"{previous_state} -> half_open"
                    )
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to check expired overrides: {e}")

        return expired_services

    def extend_manual_override(
        self,
        service_name: str,
        additional_minutes: int = 90,
        controlled_by_id: int | None = None,
        reason: str = "",
    ) -> CircuitBreakerResult:
        """
        Extend the TTL of an existing manual override.

        Use this when an operator needs more time to resolve an issue.

        Args:
            service_name: Name of the external service
            additional_minutes: Minutes to extend the override
            controlled_by_id: User ID who initiated the extension
            reason: Reason for extension

        Returns:
            CircuitBreakerResult with operation outcome
        """
        try:
            state = self.repository.get_by_service_name(service_name)
            if state is None:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error=f"Circuit breaker for '{service_name}' does not exist",
                )

            if not state.manually_controlled:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error="Circuit is not under manual control",
                )

            # Extend TTL
            current_time = now()
            if state.manual_override_expires_at:
                new_expires_at = state.manual_override_expires_at + timedelta(minutes=additional_minutes)
            else:
                new_expires_at = current_time + timedelta(minutes=additional_minutes)

            new_reason = f"{state.control_reason} | Extended: {reason}" if reason else state.control_reason

            self.repository.set_manual_control(
                service_name=service_name,
                state=state.state,
                controlled_by_id=controlled_by_id,
                reason=new_reason,
                expires_at=new_expires_at,
            )

            logger.info(f"[CircuitBreaker] Extended manual override for '{service_name}' " f"by {additional_minutes} minutes")

            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=state.state,
                new_state=state.state,
                message=f"Manual override extended by {additional_minutes} minutes",
            )
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to extend override: {e}")
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=str(e),
            )

    # =========================================================================
    # Conditional Replay
    # =========================================================================

    def _trigger_conditional_replay(self, service_name: str) -> None:
        """
        Trigger conditional replay when circuit breaker closes.

        Queues a task to replay DLQ entries related to the
        recovered service.

        Note: The actual task execution is delegated to the application's
        task queue system. This method logs the intent and attempts to
        use the TaskQueue interface if available.

        Args:
            service_name: Name of the service that recovered
        """
        try:
            from selfhealing.factory import ProviderRegistry

            queue = ProviderRegistry.get_queue()
            task_id = queue.enqueue(
                "selfhealing.tasks.conditional_replay_on_circuit_close",
                kwargs={"service_name": service_name},
            )

            logger.info(f"[CircuitBreaker] Triggered conditional replay for '{service_name}': " f"task_id={task_id}")
        except (ImportError, ValueError):
            # Task queue not configured, log warning
            logger.warning(f"[CircuitBreaker] Cannot trigger replay for '{service_name}': " "Task queue not available")
        except Exception as e:
            # Non-critical error, log but don't fail
            logger.error(f"[CircuitBreaker] Failed to trigger replay for '{service_name}': {e}")
