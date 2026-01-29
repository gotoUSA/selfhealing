"""
Circuit Breaker Service

Provides toggle-based circuit breaker management for external service protection.
Supports manual force open/close controls and conditional replay on recovery.

Features:
- Toggle-based circuit breaker (not automatic failure counting)
- Manual force open/close by operators
- Conditional replay trigger when circuit breaker closes
- Admin integration for operational control
- Rate limit cascade detection (auto-open CB on 429 storm)
- Self-DDoS protection (prevent retry amplification)
- Minimum calls check (prevents false positives with low traffic)
- Fallback strategies (cache, DLQ, default response)
- Error Budget burn rate integration
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from selfhealing.core.timezone import now

from .config import (
    CircuitBreakerConfig,
    CircuitBreakerResult,
    CircuitState,
    FallbackResult,
)
from .manual_control import ManualControlMixin
from .protection import ProtectionMixin

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateData,
        CircuitBreakerStateRepository,
    )

logger = logging.getLogger(__name__)


class CircuitBreakerService(ProtectionMixin, ManualControlMixin):
    """
    Circuit Breaker Service.

    Provides management operations for circuit breaker states.
    Designed for manual (toggle-based) control by operators.

    Usage:
        service = CircuitBreakerService()

        # Force open (block requests)
        result = service.force_open(
            service_name="external_api",
            reason="External service maintenance",
            controlled_by=admin_user
        )

        # Force close (allow requests)
        result = service.force_close(
            service_name="external_api",
            reason="Service recovered",
            controlled_by=admin_user,
            trigger_replay=True
        )

        # Check if requests should be allowed
        if service.should_allow("external_api"):
            # proceed with request

    For testing with mock repository:
        mock_repo = Mock(spec=CircuitBreakerStateRepository)
        service = CircuitBreakerService(repository=mock_repo)
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        repository: CircuitBreakerStateRepository | None = None,
    ):
        """
        Initialize the circuit breaker service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        self.config = config or CircuitBreakerConfig.from_settings()
        self._repository = repository

        # 동기 콜백 저장소: {상태: [콜백 함수들]}
        # 이벤트 버스 비동기 전파보다 먼저 동일 프로세스 내에서 즉시 실행됨
        self._state_change_callbacks: dict[str, list] = {
            "open": [],
            "closed": [],
            "half_open": [],
        }

    def register_state_change_callback(
        self,
        state: str,
        callback: "Callable[[str, str, str], None]",
    ) -> None:
        """
        CB 상태 변경 시 호출될 동기 콜백 등록.

        이벤트 버스 비동기 전파보다 먼저 동일 프로세스에서 즉시 실행됩니다.
        Throttle 등 즉각적인 제동이 필요한 컴포넌트에서 사용합니다.

        Args:
            state: 대상 상태 ("open", "closed", "half_open")
            callback: 콜백 함수 (service_name, old_state, new_state) -> None
        """
        if state not in self._state_change_callbacks:
            logger.warning(f"[CircuitBreaker] Invalid state for callback: {state}")
            return

        if callback not in self._state_change_callbacks[state]:
            self._state_change_callbacks[state].append(callback)
            logger.debug(
                f"[CircuitBreaker] Registered sync callback for state '{state}': "
                f"{getattr(callback, '__name__', str(callback))}"
            )

    def unregister_state_change_callback(
        self,
        state: str,
        callback: "Callable[[str, str, str], None]",
    ) -> bool:
        """
        등록된 동기 콜백 해제.

        Args:
            state: 대상 상태
            callback: 해제할 콜백 함수

        Returns:
            해제 성공 여부
        """
        if state not in self._state_change_callbacks:
            return False

        if callback in self._state_change_callbacks[state]:
            self._state_change_callbacks[state].remove(callback)
            return True
        return False

    def _invoke_state_change_callbacks(
        self,
        service_name: str,
        old_state: str,
        new_state: str,
    ) -> None:
        """
        상태 변경 시 등록된 동기 콜백들을 즉시 호출.

        이벤트 버스 전파보다 먼저 실행되어 즉각적인 제동을 보장합니다.

        Args:
            service_name: 서비스 이름
            old_state: 이전 상태
            new_state: 새 상태
        """
        callbacks = self._state_change_callbacks.get(new_state, [])
        for callback in callbacks:
            try:
                callback(service_name, old_state, new_state)
            except Exception as e:
                logger.error(f"[CircuitBreaker] Sync callback failed for '{new_state}': {e}")

    @property
    def repository(self) -> CircuitBreakerStateRepository:
        """Get the repository using ProviderRegistry (Redis by default)."""
        if self._repository is None:
            from selfhealing.factory import ProviderRegistry

            self._repository = ProviderRegistry.get_circuit_breaker_repo()
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if circuit breaker is enabled."""
        return self.config.enabled

    # =========================================================================
    # State Query Operations
    # =========================================================================

    def get_or_create_state(self, service_name: str) -> CircuitBreakerStateData:
        """
        Get or create a circuit breaker state for a service.

        Args:
            service_name: Name of the external service

        Returns:
            CircuitBreakerStateData instance
        """
        return self.repository.get_or_create(service_name)

    def get_state(self, service_name: str) -> str:
        """
        Get the current state of a circuit breaker.

        Args:
            service_name: Name of the external service

        Returns:
            Current state (closed, open, half_open)
        """
        state = self.get_or_create_state(service_name)
        return state.state

    def should_allow(self, service_name: str) -> bool:
        """
        Check if requests should be allowed through the circuit breaker.

        Args:
            service_name: Name of the external service

        Returns:
            True if requests should be allowed, False if blocked
        """
        if not self.is_enabled:
            return True

        state = self.get_or_create_state(service_name)

        if state.state == CircuitState.CLOSED:
            return True

        if state.state == CircuitState.OPEN:
            # Check recovery timeout for automatic transition to half-open
            if state.opened_at:
                elapsed = (now() - state.opened_at).total_seconds()
                if elapsed >= self.config.recovery_timeout:
                    # Transition to half-open via repository
                    self.repository.update_state(
                        service_name=service_name,
                        state=CircuitState.HALF_OPEN,
                        success_count=0,
                    )
                    # Audit 기록 - 자동 복구 시도 (OPEN → HALF_OPEN)
                    try:
                        from selfhealing.services.audit_helpers import (
                            log_cb_state_change_audit,
                        )

                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=CircuitState.OPEN,
                            new_state=CircuitState.HALF_OPEN,
                            reason=f"auto_recovery: recovery_timeout ({self.config.recovery_timeout}s) elapsed",
                        )
                    except Exception as e:
                        logger.debug(f"[CircuitBreaker] Audit log failed: {e}")

                    # 동기 콜백 즉시 호출 (이벤트 버스보다 먼저 실행)
                    self._invoke_state_change_callbacks(
                        service_name=service_name,
                        old_state="open",
                        new_state="half_open",
                    )

                    # HALF_OPEN 이벤트 발행 (비동기 전파)
                    try:
                        from selfhealing.services.event_bus import (
                            EventType,
                            get_event_bus,
                        )

                        bus = get_event_bus()
                        bus.emit(
                            EventType.CIRCUIT_BREAKER_HALF_OPENED,
                            {
                                "service_name": service_name,
                                "previous_state": "open",
                                "timestamp": now().isoformat(),
                            },
                            source="circuit_breaker_service",
                        )
                    except Exception as e:
                        logger.debug(f"[CircuitBreaker] Event publish failed: {e}")

                    return True
            return False

        # half_open state: allow limited requests for testing
        return True

    def should_allow_with_fallback(
        self,
        service_name: str,
        cache_key: str | None = None,
        default_response: Any | None = None,
        request_data: dict[str, Any] | None = None,
    ) -> FallbackResult:
        """
        Check if requests should be allowed with fallback strategy support.

        When CB is open, instead of simply blocking, this method can:
        1. Return cached (stale) data
        2. Queue the request to DLQ for later retry
        3. Return a default/static response

        Args:
            service_name: Name of the external service
            cache_key: Optional Redis key for cached data lookup
            default_response: Optional default response to return
            request_data: Optional request data for DLQ queueing

        Returns:
            FallbackResult with decision and optional fallback data
        """
        if not self.is_enabled:
            return FallbackResult.allow()

        state = self.get_or_create_state(service_name)

        if state.state == CircuitState.CLOSED:
            return FallbackResult.allow()

        if state.state == CircuitState.HALF_OPEN:
            # Allow limited requests for testing
            return FallbackResult.allow()

        # CB is OPEN - apply fallback strategy
        strategy = self.config.fallback_strategy

        if strategy == "cache" and cache_key:
            # Try to get cached data
            cached_data = self._get_cached_data(cache_key)
            if cached_data is not None:
                logger.info(f"[CircuitBreaker] Serving stale cache for '{service_name}' " f"(key: {cache_key})")
                return FallbackResult.from_cache(
                    data=cached_data,
                    message=f"Circuit open for {service_name}, serving cached data",
                )

        if strategy == "dlq" and request_data:
            # Queue to DLQ for later retry
            success = self._enqueue_to_dlq(service_name, request_data)
            if success:
                logger.info(f"[CircuitBreaker] Queued request to DLQ for '{service_name}'")
                return FallbackResult.to_dlq(message=f"Circuit open for {service_name}, request queued for retry")

        if strategy == "default_response" and default_response is not None:
            logger.info(f"[CircuitBreaker] Returning default response for '{service_name}'")
            return FallbackResult.default_response(
                data=default_response,
                message=f"Circuit open for {service_name}, using default response",
            )

        # Default: block
        return FallbackResult.block(message=f"Circuit breaker open for {service_name}")

    def _get_cached_data(self, cache_key: str) -> Any | None:
        """
        Get cached data from Redis.

        Args:
            cache_key: Redis key for the cached data

        Returns:
            Cached data or None if not found/expired
        """
        try:
            from django.core.cache import cache

            return cache.get(cache_key)
        except Exception as e:
            logger.debug(f"[CircuitBreaker] Cache lookup failed: {e}")
            return None

    def _enqueue_to_dlq(
        self,
        service_name: str,
        request_data: dict[str, Any],
    ) -> bool:
        """
        Enqueue a failed request to DLQ for later retry.

        Args:
            service_name: Name of the service
            request_data: Request data to queue

        Returns:
            True if successfully queued
        """
        try:
            from selfhealing.services.dlq_service import enqueue_failed_operation

            enqueue_failed_operation(
                operation_type=f"cb_fallback_{service_name}",
                operation_data=request_data,
                error_message=f"Circuit breaker open for {service_name}",
                snapshot_data={"service_name": service_name, "fallback_type": "dlq"},
            )
            return True
        except Exception as e:
            logger.error(f"[CircuitBreaker] Failed to enqueue to DLQ: {e}")
            return False

    def get_total_calls(self, service_name: str) -> int:
        """
        Get total call count for a service (success + failure).

        Used for minimum_calls check to prevent false positives.

        Args:
            service_name: Name of the external service

        Returns:
            Total number of calls tracked
        """
        state = self.get_or_create_state(service_name)
        # Total calls = failure_count + success_count
        return state.failure_count + state.success_count

    def get_all_states(self) -> list[dict[str, Any]]:
        """
        Get all circuit breaker states.

        Returns:
            List of state dictionaries
        """
        states = self.repository.get_all_states()
        return [
            {
                "service_name": s.service_name,
                "state": s.state,
                "failure_count": s.failure_count,
                "success_count": s.success_count,
                "last_failure_at": s.last_failure_at,
                "opened_at": s.opened_at,
                "manually_controlled": s.manually_controlled,
                "controlled_by_id": s.controlled_by_id,
                "control_reason": s.control_reason,
            }
            for s in states
        ]

    # =========================================================================
    # Failure/Success Recording (for automatic mode)
    # =========================================================================

    def record_failure(self, service_name: str, error_context: dict[str, Any] | None = None) -> None:
        """
        Record a failure for a service.

        This is used for automatic circuit breaker mode.
        If the threshold is exceeded AND minimum_calls is met, the circuit opens automatically.

        Args:
            service_name: Name of the external service
            error_context: Optional context about the failure (for snapshot)
        """
        if not self.is_enabled:
            return

        state = self.get_or_create_state(service_name)

        # Skip if manually controlled
        if state.manually_controlled:
            logger.debug(f"[CircuitBreaker] Skipping failure recording for '{service_name}': " "manually controlled")
            return

        # Use repository to record failure (handles atomic update)
        updated_state = self.repository.record_failure(service_name)

        # Check if threshold exceeded and circuit should open
        should_open = self._should_open_circuit(updated_state)

        if should_open and updated_state.state == "closed":
            # Collect snapshot before opening
            snapshot = self._collect_failure_snapshot(service_name, updated_state, error_context)

            # Open the circuit
            self.repository.update_state(
                service_name=service_name,
                state="open",
                opened_at=now(),
            )

            # Log with snapshot
            logger.warning(
                f"[CircuitBreaker] Circuit auto-opened for '{service_name}' "
                f"(failures: {updated_state.failure_count}, "
                f"total_calls: {self.get_total_calls(service_name)})"
            )

            # 동기 콜백 즉시 호출 (이벤트 버스보다 먼저 실행)
            self._invoke_state_change_callbacks(
                service_name=service_name,
                old_state="closed",
                new_state="open",
            )

            # Save audit log with snapshot
            self._log_circuit_open_audit(service_name, snapshot)

            # Apply burn rate multiplier to Error Budget
            self._apply_burn_rate_multiplier(service_name)

            # Push 이벤트 - CB 상태 변경 메트릭 기록
            try:
                from selfhealing.metrics.event_handlers import (
                    CircuitBreakerEventHandler,
                )

                CircuitBreakerEventHandler.on_state_changed(
                    service=service_name,
                    from_state="closed",
                    to_state="open",
                )
            except ImportError:
                pass  # Metrics not available

    def _should_open_circuit(self, state: CircuitBreakerStateData) -> bool:
        """
        Determine if circuit should be opened based on failure threshold and minimum calls.

        Implements both count-based and rate-based thresholds with minimum_calls protection.

        Args:
            state: Current circuit breaker state

        Returns:
            True if circuit should open
        """
        total_calls = state.failure_count + state.success_count

        # Check minimum_calls - prevent false positives with low traffic
        if total_calls < self.config.minimum_calls:
            logger.debug(
                f"[CircuitBreaker] Not opening '{state.service_name}': "
                f"total_calls ({total_calls}) < minimum_calls ({self.config.minimum_calls})"
            )
            return False

        # Check rate-based threshold if configured
        if self.config.failure_rate_threshold > 0:
            failure_rate = (state.failure_count / total_calls * 100) if total_calls > 0 else 0
            if failure_rate >= self.config.failure_rate_threshold:
                logger.info(
                    f"[CircuitBreaker] Rate threshold exceeded for '{state.service_name}': "
                    f"{failure_rate:.1f}% >= {self.config.failure_rate_threshold}%"
                )
                return True

        # Check count-based threshold
        if state.failure_count >= self.config.failure_threshold:
            return True

        return False

    def _collect_failure_snapshot(
        self,
        service_name: str,
        state: CircuitBreakerStateData,
        error_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Collect a snapshot of system state when circuit opens.

        This data is valuable for post-mortem analysis and ML training.

        Args:
            service_name: Name of the service
            state: Current circuit breaker state
            error_context: Optional error context

        Returns:
            Snapshot dictionary with failure details
        """
        snapshot = {
            "service_name": service_name,
            "timestamp": now().isoformat(),
            "circuit_breaker": {
                "failure_count": state.failure_count,
                "success_count": state.success_count,
                "total_calls": state.failure_count + state.success_count,
                "failure_rate_percent": (
                    state.failure_count / (state.failure_count + state.success_count) * 100
                    if (state.failure_count + state.success_count) > 0
                    else 0
                ),
                "threshold_config": {
                    "failure_threshold": self.config.failure_threshold,
                    "minimum_calls": self.config.minimum_calls,
                    "failure_rate_threshold": self.config.failure_rate_threshold,
                },
            },
            "trigger_reason": "auto_threshold_exceeded",
        }

        # Add system metrics if available
        try:
            import psutil

            snapshot["system_metrics"] = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "memory_percent": psutil.virtual_memory().percent,
            }
        except Exception:
            pass  # psutil not available or failed

        # Add error context if provided
        if error_context:
            snapshot["error_context"] = error_context

        # Add latency metrics if available
        try:
            from selfhealing.metrics.reliability_manager import get_reliability_manager

            manager = get_reliability_manager()
            latency_info = manager.get_effective_value("latency", service_name)
            if latency_info:
                snapshot["latency"] = {
                    "value": latency_info[0],
                    "source": latency_info[1],
                }
        except Exception:
            pass  # Metrics not available

        return snapshot

    def _log_circuit_open_audit(self, service_name: str, snapshot: dict[str, Any]) -> None:
        """
        Log circuit open event to audit log with snapshot.

        Uses unified audit_helpers.log_cb_state_change_audit for:
        - WAL-based zero-loss guarantee
        - Hash chain integrity connection
        - Consistent CB audit format

        Args:
            service_name: Name of the service
            snapshot: Failure snapshot data
        """
        try:
            from selfhealing.services.audit_helpers import log_cb_state_change_audit

            # Build reason with snapshot context
            reason = (
                f"auto_trigger|failures={snapshot.get('failure_count', 'N/A')}|threshold={snapshot.get('threshold', 'N/A')}"
            )

            log_cb_state_change_audit(
                cb_name=service_name,
                old_state="closed",
                new_state="open",
                reason=reason,
                request=None,  # System-triggered, no HTTP context
            )

            # Log detailed snapshot separately for debugging
            logger.info(f"[CircuitBreaker] AUTO_OPEN audit logged | service={service_name} | " f"snapshot={snapshot}")
        except Exception as e:
            logger.debug(f"[CircuitBreaker] Audit log failed: {e}")

    def _apply_burn_rate_multiplier(self, service_name: str) -> None:
        """
        Apply burn rate multiplier to Error Budget when CB opens.

        This accelerates Error Budget consumption to trigger EmergencyMode faster.

        Args:
            service_name: Name of the service
        """
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager

            manager = get_emergency_manager()
            multiplier = self.config.cb_open_burn_rate_multiplier

            # Record accelerated burn event
            logger.warning(f"[CircuitBreaker] Applying burn rate multiplier {multiplier}x " f"for '{service_name}' (CB OPEN)")

            # Emit event for burn rate acceleration
            try:
                from selfhealing.services.event_bus import EventType, get_event_bus

                bus = get_event_bus()
                bus.emit(
                    EventType.CIRCUIT_BREAKER_OPENED,
                    {
                        "service_name": service_name,
                        "burn_rate_multiplier": multiplier,
                        "timestamp": now().isoformat(),
                    },
                    source="circuit_breaker_service",
                )
            except Exception:
                pass  # Event bus not available

        except Exception as e:
            logger.debug(f"[CircuitBreaker] Burn rate multiplier failed: {e}")

    def record_success(self, service_name: str) -> None:
        """
        Record a success for a service.

        This is used for automatic circuit breaker mode.
        In half-open state, enough successes will close the circuit.

        Args:
            service_name: Name of the external service
        """
        if not self.is_enabled:
            return

        state = self.get_or_create_state(service_name)

        # Skip if manually controlled
        if state.manually_controlled:
            logger.debug(f"[CircuitBreaker] Skipping success recording for '{service_name}': " "manually controlled")
            return

        circuit_closed = False

        if state.state == "half_open":
            # Use repository to record success
            updated_state = self.repository.record_success(service_name)

            if updated_state.success_count >= self.config.success_threshold:
                # Close the circuit - use atomic operation
                self.repository.update_state(
                    service_name=service_name,
                    state="closed",
                    failure_count=0,
                    success_count=0,
                    opened_at=None,
                )
                circuit_closed = True

        elif state.state == "closed":
            # Reset failure count on success in closed state
            self.repository.update_state(
                service_name=service_name,
                state="closed",
                failure_count=0,
            )

        if circuit_closed:
            logger.info(
                f"[CircuitBreaker] Circuit auto-closed for '{service_name}' " f"(successes: {self.config.success_threshold})"
            )

            # 동기 콜백 즉시 호출 (이벤트 버스보다 먼저 실행)
            self._invoke_state_change_callbacks(
                service_name=service_name,
                old_state="half_open",
                new_state="closed",
            )

            # Audit 기록 - 자동 복구 완료 (HALF_OPEN → CLOSED)
            try:
                from selfhealing.services.audit_helpers import log_cb_state_change_audit

                log_cb_state_change_audit(
                    cb_name=service_name,
                    old_state="half_open",
                    new_state="closed",
                    reason=f"auto_recovery: success_threshold ({self.config.success_threshold}) reached",
                )
            except Exception as e:
                logger.debug(f"[CircuitBreaker] Audit log failed: {e}")
            # Push 이벤트 - CB 상태 변경 메트릭 기록
            try:
                from selfhealing.metrics.event_handlers import (
                    CircuitBreakerEventHandler,
                )

                CircuitBreakerEventHandler.on_state_changed(
                    service=service_name,
                    from_state="half_open",
                    to_state="closed",
                )
            except ImportError:
                pass  # Metrics not available
            # Trigger conditional replay on auto-close
            self._trigger_conditional_replay(service_name)

    # =========================================================================
    # Recovery Transition Check (for periodic task)
    # =========================================================================

    def check_recovery_transitions(self) -> dict:
        """
        Check for circuit breakers that should transition from OPEN to HALF_OPEN.

        This method should be called periodically (e.g., every minute) to check
        if any OPEN circuits have exceeded the recovery timeout and should
        transition to HALF_OPEN for testing.

        Returns:
            Dictionary with transitioned service names and count
        """
        if not self.is_enabled:
            return {"success": True, "message": "Circuit breaker disabled", "count": 0}

        transitioned = []

        try:
            # Get all states and filter for OPEN, non-manually-controlled ones
            all_states = self.repository.get_all_states()
            open_states = [s for s in all_states if s.state == CircuitState.OPEN and not s.manually_controlled]

            for state in open_states:
                if state.opened_at is None:
                    continue

                elapsed = (now() - state.opened_at).total_seconds()

                if elapsed >= self.config.recovery_timeout:
                    # Transition to half-open
                    self.repository.update_state(
                        service_name=state.service_name,
                        state=CircuitState.HALF_OPEN,
                        success_count=0,
                    )
                    transitioned.append(state.service_name)
                    logger.info(
                        f"[CircuitBreaker] Transitioned '{state.service_name}' " f"from OPEN to HALF_OPEN after {elapsed:.0f}s"
                    )

            return {
                "success": True,
                "transitioned": transitioned,
                "count": len(transitioned),
            }

        except Exception as e:
            logger.error(
                f"[CircuitBreaker] Error checking recovery transitions: {e}",
                exc_info=True,
            )
            return {
                "success": False,
                "error": str(e),
                "transitioned": transitioned,
                "count": len(transitioned),
            }

    def manual_control(
        self,
        service_name: str,
        action: str,
        reason: str = "",
        controlled_by: Any = None,
    ) -> CircuitBreakerResult:
        """
        Manually control a circuit breaker state.

        Args:
            service_name: Name of the service
            action: 'open', 'close', or 'auto'
            reason: Reason for the control action
            controlled_by: User who initiated the action

        Returns:
            CircuitBreakerResult with operation details
        """
        state = self.get_or_create_state(service_name)
        previous_state = state.state

        if action == "open":
            return self.force_open(
                service_name=service_name,
                reason=reason,
                controlled_by=controlled_by,
            )
        elif action == "close":
            return self.force_close(
                service_name=service_name,
                reason=reason,
                controlled_by=controlled_by,
            )
        else:  # auto
            # Clear manual control flag
            self.repository.update_state(
                service_name=service_name,
                manually_controlled=False,
            )
            logger.info(f"[CircuitBreaker] '{service_name}' switched to auto mode")
            return CircuitBreakerResult(
                success=True,
                service_name=service_name,
                previous_state=previous_state,
                new_state=state.state,
                message=f"Circuit breaker for '{service_name}' switched to auto mode",
            )
