"""
Manual Control Mixin for Circuit Breaker Service

Provides manual force open/close and TTL management functionality.
"""

from __future__ import annotations

import structlog
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from selfhealing.core.decision_logger import DecisionLogger, ReasonCode
from selfhealing.core.timezone import now

from .config import CircuitBreakerResult, CircuitState

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import CircuitBreakerStateRepository

    from .config import CircuitBreakerConfig

logger = structlog.get_logger()


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
    config: CircuitBreakerConfig
    repository: CircuitBreakerStateRepository

    # =========================================================================
    # Manual Control Operations
    # =========================================================================

    def _resolve_controlled_by_id(
        self, controlled_by: Any, controlled_by_id: int | None
    ) -> int | None:
        """Resolve controlled_by_id from User object or direct ID."""
        if controlled_by_id is not None:
            return controlled_by_id
        if controlled_by is not None:
            return getattr(controlled_by, "id", None) or getattr(
                controlled_by, "pk", None
            )
        return None

    def force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by: Any = None,
        controlled_by_id: int | None = None,
        override_kill_switch: bool = False,
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
            override_kill_switch: If True, bypass Kill Switch check (운영자 권한)

        Returns:
            CircuitBreakerResult with operation outcome
        """
        controlled_by_id = self._resolve_controlled_by_id(
            controlled_by, controlled_by_id
        )

        # Kill Switch 체크
        kill_switch_result = self._check_kill_switch(
            service_name, "force_open", reason, controlled_by_id, override_kill_switch
        )
        if kill_switch_result:
            return kill_switch_result

        decision_logger = DecisionLogger(service_name=service_name)
        decision_logger.intervention_evaluated(
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )

        # 행동 직전 로깅 - Circuit Breaker OPEN 전에 기록
        logger.info(
            f"[CircuitBreaker] FORCE_OPEN service={service_name}, "
            f"reason={reason}, controlled_by_id={controlled_by_id}"
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
                    logger.info(
                        f"[CircuitBreaker] Circuit '{service_name}' already open"
                    )
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
                        from selfhealing.services.audit_helpers import (
                            log_cb_state_change_audit,
                        )

                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=previous_state,
                            new_state=new_state,
                            reason=(
                                f"force_open: {reason}"
                                if reason
                                else "force_open: manual"
                            ),
                        )
                    except Exception as e:
                        logger.debug(
                            "circuit_breaker.audit_log_failed",
                            error=e,
                        )
                    # Push 이벤트 - CB 상태 변경 메트릭 기록
                    try:
                        from selfhealing.metrics.event_handlers import (
                            CircuitBreakerEventHandler,
                        )

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
            logger.error(
                "circuit_breaker.failed_force_open",
                error=e,
            )
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
        override_kill_switch: bool = False,
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
            override_kill_switch: If True, bypass Kill Switch check (운영자 권한)

        Returns:
            CircuitBreakerResult with operation outcome
        """
        # Kill Switch 체크
        kill_switch_result = self._check_kill_switch(
            service_name, "force_close", reason, controlled_by_id, override_kill_switch
        )
        if kill_switch_result:
            return kill_switch_result

        # Handle both controlled_by (User object) and controlled_by_id
        if controlled_by_id is None and controlled_by is not None:
            controlled_by_id = getattr(controlled_by, "id", None) or getattr(
                controlled_by, "pk", None
            )

        decision_logger = DecisionLogger(service_name=service_name)
        decision_logger.intervention_evaluated(
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )

        logger.info(
            f"[CircuitBreaker] FORCE_CLOSE service={service_name}, "
            f"reason={reason}, controlled_by_id={controlled_by_id}, trigger_replay={trigger_replay}"
        )

        try:
            success, previous_state, new_state = self.repository.atomic_force_close(
                service_name=service_name,
                reason=reason,
                controlled_by_id=controlled_by_id,
            )

            if success:
                return self._handle_force_close_success(
                    service_name, previous_state, new_state, reason, trigger_replay
                )
            else:
                return CircuitBreakerResult.failed(
                    service_name=service_name,
                    error="Failed to force close circuit breaker",
                )
        except Exception as e:
            logger.error(
                "circuit_breaker.failed_force_close",
                error=e,
            )
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error=str(e),
            )

    def _check_kill_switch(
        self,
        service_name: str,
        action: str,
        reason: str,
        controlled_by_id: int | None,
        override_kill_switch: bool,
    ) -> CircuitBreakerResult | None:
        """Kill Switch 체크. 차단 시 결과 반환, 통과 시 None."""
        if _is_system_enabled():
            return None

        if not override_kill_switch:
            logger.warning(
                f"[CircuitBreaker] {action} blocked: Kill Switch is active. "
                f"service={service_name}. Use override_kill_switch=True for manual control."
            )
            return CircuitBreakerResult.failed(
                service_name=service_name,
                error="Kill Switch is active: use override_kill_switch=True for manual control",
            )

        # Kill Switch override 시 Audit 기록
        logger.warning(
            f"[CircuitBreaker] Kill Switch override for {action}: "
            f"service={service_name}, controlled_by_id={controlled_by_id}"
        )
        try:
            from selfhealing.services.audit_helpers import (
                log_kill_switch_override_audit,
            )

            log_kill_switch_override_audit(
                service_name=service_name,
                action=action,
                reason=reason,
                controlled_by_id=controlled_by_id,
            )
        except Exception as e:
            logger.debug(
                "circuit_breaker.kill_switch_override_audit",
                error=e,
            )

        return None

    def _handle_force_close_success(
        self,
        service_name: str,
        previous_state: str,
        new_state: str,
        reason: str,
        trigger_replay: bool,
    ) -> CircuitBreakerResult:
        """Force close 성공 시 처리."""
        if previous_state == new_state:
            logger.info(
                "circuit_breaker.circuit_already_closed",
                service_name=service_name,
            )
            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=previous_state,
                new_state=new_state,
                message="Circuit breaker already closed",
            )

        logger.info(
            f"[CircuitBreaker] Force closed circuit for '{service_name}': "
            f"{previous_state} -> {new_state} | Reason: {reason}"
        )

        self._log_state_change_audit(
            service_name, previous_state, new_state, reason, "force_close"
        )
        self._emit_state_change_metric(service_name, previous_state, new_state)

        result = CircuitBreakerResult.succeeded(
            service_name=service_name,
            previous_state=previous_state,
            new_state=new_state,
            message=f"Circuit breaker closed for {service_name}",
        )

        if trigger_replay:
            self._trigger_conditional_replay(service_name)

        return result

    def _log_state_change_audit(
        self,
        service_name: str,
        previous_state: str,
        new_state: str,
        reason: str,
        action: str,
    ) -> None:
        """CB 상태 변경 Audit 로그 기록."""
        try:
            from selfhealing.services.audit_helpers import log_cb_state_change_audit

            log_cb_state_change_audit(
                cb_name=service_name,
                old_state=previous_state,
                new_state=new_state,
                reason=f"{action}: {reason}" if reason else f"{action}: manual",
            )
        except Exception as e:
            logger.debug(
                "circuit_breaker.audit_log_failed",
                error=e,
            )

    def _emit_state_change_metric(
        self,
        service_name: str,
        previous_state: str,
        new_state: str,
    ) -> None:
        """CB 상태 변경 메트릭 기록."""
        try:
            from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler

            CircuitBreakerEventHandler.on_state_changed(
                service=service_name,
                from_state=previous_state,
                to_state=new_state,
            )
        except ImportError:
            pass

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
                        from selfhealing.services.audit_helpers import (
                            log_cb_state_change_audit,
                        )

                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=previous_state,
                            new_state=new_state,
                            reason=f"reset: {reason}" if reason else "reset: manual",
                        )
                    except Exception as e:
                        logger.debug(
                            "circuit_breaker.audit_log_failed",
                            error=e,
                        )
                # Push 이벤트 - CB 상태 변경 메트릭 기록
                if previous_state != new_state:
                    try:
                        from selfhealing.metrics.event_handlers import (
                            CircuitBreakerEventHandler,
                        )

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
            logger.error(
                "circuit_breaker.failed_reset",
                error=e,
            )
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

                    self.repository.clear_manual_control(
                        state.service_name, preserve_reason=True
                    )

                    expired_services.append(state.service_name)
                    logger.warning(
                        f"[CircuitBreaker] Manual override expired for '{state.service_name}': "
                        f"{previous_state} -> half_open"
                    )
        except Exception as e:
            logger.error(
                "circuit_breaker.failed_check_expired_overrides",
                error=e,
            )

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
                new_expires_at = state.manual_override_expires_at + timedelta(
                    minutes=additional_minutes
                )
            else:
                new_expires_at = current_time + timedelta(minutes=additional_minutes)

            new_reason = (
                f"{state.control_reason} | Extended: {reason}"
                if reason
                else state.control_reason
            )

            self.repository.set_manual_control(
                service_name=service_name,
                state=state.state,
                controlled_by_id=controlled_by_id,
                reason=new_reason,
                expires_at=new_expires_at,
            )

            logger.info(
                f"[CircuitBreaker] Extended manual override for '{service_name}' "
                f"by {additional_minutes} minutes"
            )

            return CircuitBreakerResult.succeeded(
                service_name=service_name,
                previous_state=state.state,
                new_state=state.state,
                message=f"Manual override extended by {additional_minutes} minutes",
            )
        except Exception as e:
            logger.error(
                "circuit_breaker.failed_extend_override",
                error=e,
            )
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

            logger.info(
                f"[CircuitBreaker] Triggered conditional replay for '{service_name}': "
                f"task_id={task_id}"
            )
        except (ImportError, ValueError):
            # Task queue not configured, log warning
            logger.warning(
                f"[CircuitBreaker] Cannot trigger replay for '{service_name}': "
                "Task queue not available"
            )
        except Exception as e:
            # Non-critical error, log but don't fail
            logger.error(
                f"[CircuitBreaker] Failed to trigger replay for '{service_name}': {e}"
            )
