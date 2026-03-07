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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.core.timezone import now

from .config import (
    CircuitBreakerConfig,
    CircuitBreakerFallbackResult,
    CircuitBreakerResult,
    CircuitState,
)
from .manual_control import ManualControlMixin
from .protection import ProtectionMixin

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        CircuitBreakerStateData,
        CircuitBreakerStateRepository,
    )

logger = structlog.get_logger()


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

        # MeshCoordinator 연동: 하류 상태 pre-check 함수 목록
        # checker(service_name) → True면 하류 정상, False면 프리엠티브 Fallback
        self._downstream_checkers: list[Callable[[str], bool]] = []

        # MeshCoordinator 연동: 서비스별 임계치 오버라이드 맵
        self._threshold_overrides: dict[str, Any] = {}

    def register_state_change_callback(
        self,
        state: str,
        callback: Callable[[str, str, str], None],
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
            logger.warning(
                "circuit_breaker.invalid_callback_state",
                circuit_state=state,
            )
            return

        if callback not in self._state_change_callbacks[state]:
            self._state_change_callbacks[state].append(callback)
            logger.debug(
                "cell_registry.bulkheads_registered",
                circuit_state=state,
                getattr=getattr(callback, "__name__", str(callback)),
            )

    def unregister_state_change_callback(
        self,
        state: str,
        callback: Callable[[str, str, str], None],
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
                logger.exception(
                    "circuit_breaker.sync_callback_failed",
                    new_state=new_state,
                    error=e,
                )

    @property
    def repository(self) -> CircuitBreakerStateRepository:
        """Get the repository using ProviderRegistry with fallback policy."""
        if self._repository is None:
            from selfhealing.adapters.memory import (
                InMemoryCircuitBreakerStateRepository,
            )
            from selfhealing.core.di_fallback import resolve_with_fallback
            from selfhealing.factory import ProviderRegistry

            self._repository = resolve_with_fallback(
                registry_method=ProviderRegistry.get_circuit_breaker_repo,
                fallback_class=InMemoryCircuitBreakerStateRepository,
                service_name=self.__class__.__name__,
            )
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if circuit breaker is enabled."""
        return self.config.enabled

    # =========================================================================
    # Mesh Coordinator Extension Points
    # =========================================================================

    def register_downstream_checker(
        self,
        checker: Callable[[str], bool],
    ) -> None:
        """
        should_allow() pre-check hook 등록.

        checker(service_name) → False면 프리엠티브 Fallback.
        checker는 반드시 로컬 인메모리 조회만 수행해야 한다 (외부 I/O 금지).
        """
        self._downstream_checkers.append(checker)

    def apply_threshold_override(self, service_name: str, override: Any) -> None:
        """
        메쉬 코디네이터가 설정한 임계치 오버라이드 적용.

        오버라이드가 활성인 동안 해당 서비스의 failure_threshold와
        recovery_timeout은 오버라이드 값을 사용한다.
        """
        self._threshold_overrides[service_name] = override

    def remove_threshold_override(self, service_name: str) -> None:
        """임계치 오버라이드 해제, 원래 config로 복귀."""
        self._threshold_overrides.pop(service_name, None)

    @staticmethod
    def _record_preemptive_fallback_metric() -> None:
        """프리엠티브 Fallback 메트릭 기록 (graceful degradation)."""
        try:
            from selfhealing.metrics.prometheus import get_metrics

            metrics = get_metrics()
            if metrics._initialized and hasattr(
                metrics, "mesh_preemptive_fallback_total"
            ):
                metrics.mesh_preemptive_fallback_total.inc()
        except Exception:
            pass

    def get_effective_config(self, service_name: str) -> CircuitBreakerConfig:
        """
        오버라이드 적용된 실효 config 반환.

        L1 로컬 캐시에서 조회하므로 외부 I/O 없음.
        오버라이드가 없으면 기본 config, 있으면 해당 필드만 교체.
        """
        if service_name not in self._threshold_overrides:
            return self.config

        override = self._threshold_overrides[service_name]
        if now() > override.expires_at:
            self._threshold_overrides.pop(service_name)
            return self.config

        return CircuitBreakerConfig(
            **{
                **vars(self.config),
                "failure_threshold": override.adjusted_failure_threshold,
                "recovery_timeout": override.adjusted_recovery_timeout,
            }
        )

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

        # 하류 상태 pre-check (MeshCoordinator 연동)
        # O(1) 인메모리 조회만 수행, 외부 I/O 없음
        for checker in self._downstream_checkers:
            try:
                if not checker(service_name):
                    logger.info(
                        "circuit_breaker.downstream_preemptive_fallback",
                        service=service_name,
                    )
                    self._record_preemptive_fallback_metric()
                    return False
            except Exception as e:
                logger.warning(
                    "circuit_breaker.downstream_checker_failed",
                    service=service_name,
                    error=str(e),
                )

        state = self.get_or_create_state(service_name)

        if state.state == CircuitState.CLOSED:
            return True

        effective_config = self.get_effective_config(service_name)

        if state.state == CircuitState.OPEN:
            # Check recovery timeout for automatic transition to half-open
            if state.opened_at:
                elapsed = (now() - state.opened_at).total_seconds()
                if elapsed >= effective_config.recovery_timeout:
                    # Transition to half-open via repository
                    self.repository.update_state(
                        service_name=service_name,
                        state=CircuitState.HALF_OPEN,
                        success_count=0,
                    )
                    # Audit 기록 - 자동 복구 시도 (OPEN → HALF_OPEN)
                    try:
                        from selfhealing.services.audit import (
                            log_cb_state_change_audit,
                        )

                        log_cb_state_change_audit(
                            cb_name=service_name,
                            old_state=CircuitState.OPEN,
                            new_state=CircuitState.HALF_OPEN,
                            reason=f"auto_recovery: recovery_timeout ({self.config.recovery_timeout}s) elapsed",
                        )
                    except Exception as e:
                        logger.debug(
                            "circuit_breaker.audit_log_failed",
                            error=e,
                        )

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
                        logger.debug(
                            "circuit_breaker.event_publish_failed",
                            error=e,
                        )

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
    ) -> CircuitBreakerFallbackResult:
        """
        Check if requests should be allowed with fallback strategy support.

        .. deprecated::
            이 메서드는 deprecated 되었습니다.
            CircuitBreakerPolicy + FallbackPolicy 조합으로 대체하세요.

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
            CircuitBreakerFallbackResult with decision and optional fallback data
        """
        import warnings

        warnings.warn(
            "should_allow_with_fallback() is deprecated. "
            "Use CircuitBreakerPolicy + FallbackPolicy 조합으로 대체하세요.",
            DeprecationWarning,
            stacklevel=2,
        )

        if not self.is_enabled:
            return CircuitBreakerFallbackResult.allow()

        state = self.get_or_create_state(service_name)

        if state.state == CircuitState.CLOSED:
            return CircuitBreakerFallbackResult.allow()

        if state.state == CircuitState.HALF_OPEN:
            # Allow limited requests for testing
            return CircuitBreakerFallbackResult.allow()

        # CB is OPEN - apply fallback strategy
        strategy = self.config.fallback_strategy

        if strategy == "cache" and cache_key:
            # Try to get cached data
            cached_data = self._get_cached_data(cache_key)
            if cached_data is not None:
                logger.info(
                    "circuit_breaker.stale_cache_served",
                    service_name=service_name,
                    cache_key=cache_key,
                )
                return CircuitBreakerFallbackResult.from_cache(
                    data=cached_data,
                    message=f"Circuit open for {service_name}, serving cached data",
                )

        if strategy == "dlq" and request_data:
            # Queue to DLQ for later retry
            success = self._enqueue_to_dlq(service_name, request_data)
            if success:
                logger.info(
                    "circuit_breaker.request_queued_to_dlq",
                    service_name=service_name,
                )
                return CircuitBreakerFallbackResult.to_dlq(
                    message=f"Circuit open for {service_name}, request queued for retry"
                )

        if strategy == "default_response" and default_response is not None:
            logger.info(
                "circuit_breaker.default_response_returned",
                service_name=service_name,
            )
            return CircuitBreakerFallbackResult.default_response(
                data=default_response,
                message=f"Circuit open for {service_name}, using default response",
            )

        # Default: block
        return CircuitBreakerFallbackResult.block(
            message=f"Circuit breaker open for {service_name}"
        )

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
            logger.debug(
                "circuit_breaker.cache_lookup_failed",
                error=e,
            )
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
            from selfhealing.services.dlq import enqueue_failed_operation

            enqueue_failed_operation(
                operation_type=f"cb_fallback_{service_name}",
                operation_data=request_data,
                error_message=f"Circuit breaker open for {service_name}",
                snapshot_data={"service_name": service_name, "fallback_type": "dlq"},
            )
            return True
        except Exception as e:
            logger.exception(
                "circuit_breaker.failed_enqueue_dlq",
                error=e,
            )
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
                "metadata": s.metadata,
            }
            for s in states
        ]

    # =========================================================================
    # Failure/Success Recording (for automatic mode)
    # =========================================================================

    def record_failure(
        self, service_name: str, error_context: dict[str, Any] | None = None
    ) -> None:
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
            logger.debug(
                "circuit_breaker.skipping_failure_recording_manually",
                service_name=service_name,
            )
            return

        # Use repository to record failure (handles atomic update)
        updated_state = self.repository.record_failure(service_name)

        # Check if threshold exceeded and circuit should open
        effective_config = self.get_effective_config(service_name)
        should_open = self._should_open_circuit(updated_state, effective_config)

        if should_open and updated_state.state == "closed":
            # Collect snapshot before opening
            snapshot = self._collect_failure_snapshot(
                service_name, updated_state, error_context
            )

            # Open the circuit
            self.repository.update_state(
                service_name=service_name,
                state="open",
                opened_at=now(),
            )

            # Log with snapshot
            logger.warning(
                "circuit_breaker.circuit_auto_opened_failures",
                service_name=service_name,
                updated_state=updated_state.failure_count,
                total_calls=self.get_total_calls(service_name),
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

    def _should_open_circuit(
        self,
        state: CircuitBreakerStateData,
        effective_config: CircuitBreakerConfig | None = None,
    ) -> bool:
        """
        Determine if circuit should be opened based on failure threshold and minimum calls.

        Implements both count-based and rate-based thresholds with minimum_calls protection.
        Rate-based threshold uses sliding_window_size to bound the calculation,
        ensuring repository가 window-based count를 제공하지 않는 경우에도
        누적 카운트 오염을 방지한다.

        Args:
            state: Current circuit breaker state
            effective_config: Optional overridden config from MeshCoordinator

        Returns:
            True if circuit should open
        """
        cfg = effective_config or self.config
        total_calls = state.failure_count + state.success_count

        # Sliding Window: total_calls를 window 크기로 제한 (§9)
        # InMemoryRepo(ring buffer)는 이미 window-based count를 반환하므로 no-op.
        # RedisRepo 등 non-windowed repo 사용 시 누적 카운트 오염을 방지한다.
        window_size = cfg.sliding_window_size
        if window_size > 0 and total_calls > window_size:
            logger.debug(
                "circuit_breaker.capping",
                target_service_name=state.service_name,
                total_calls=total_calls,
                window_size=window_size,
            )
            # 비율 계산 시 window 범위 내 카운트만 사용
            # count-based threshold는 failure_count 원본을 사용 (§9.4)
            total_calls = window_size

        # Check minimum_calls - prevent false positives with low traffic
        if total_calls < cfg.minimum_calls:
            logger.debug(
                "circuit_breaker.opening",
                target_service_name=state.service_name,
                total_calls=total_calls,
                minimum_calls=cfg.minimum_calls,
            )
            return False

        # Check rate-based threshold if configured
        if cfg.failure_rate_threshold > 0:
            failure_rate = (
                (state.failure_count / total_calls * 100) if total_calls > 0 else 0
            )
            if failure_rate >= cfg.failure_rate_threshold:
                logger.info(
                    "circuit_breaker.rate_threshold_exceeded",
                    target_service_name=state.service_name,
                    failure_rate=failure_rate,
                    failure_rate_threshold=cfg.failure_rate_threshold,
                    window_size=window_size,
                )
                return True

        # Check count-based threshold
        if state.failure_count >= cfg.failure_threshold:
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
                    state.failure_count
                    / (state.failure_count + state.success_count)
                    * 100
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
            from selfhealing.services.system_metrics_cache import (
                get_system_metrics_cache,
            )

            cache = get_system_metrics_cache()
            if cache.is_running():
                snapshot["system_metrics"] = {
                    "cpu_percent": cache.get_cpu_percent(),
                    "memory_percent": cache.get_memory_percent(),
                }
            else:
                import psutil

                snapshot["system_metrics"] = {
                    "cpu_percent": psutil.cpu_percent(interval=None),
                    "memory_percent": psutil.virtual_memory().percent,
                }
        except Exception:
            pass  # system_metrics_cache or psutil not available

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

    def _log_circuit_open_audit(
        self, service_name: str, snapshot: dict[str, Any]
    ) -> None:
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
            from selfhealing.services.audit import log_cb_state_change_audit

            # snapshot에서 값을 참조한다 (flat / nested 구조 모두 지원)
            cb_data = snapshot.get("circuit_breaker", {})
            failure_count = cb_data.get("failure_count") or snapshot.get(
                "failure_count", "N/A"
            )
            threshold_data = cb_data.get("threshold_config", {})
            threshold_value = threshold_data.get("failure_threshold") or snapshot.get(
                "threshold", "N/A"
            )
            reason = (
                f"auto_trigger|failures={failure_count}|threshold={threshold_value}"
            )

            log_cb_state_change_audit(
                cb_name=service_name,
                old_state="closed",
                new_state="open",
                reason=reason,
                request=None,  # System-triggered, no HTTP context
            )

            # Log detailed snapshot separately for debugging
            logger.info(
                "circuit_breaker.audit_logged",
                service_name=service_name,
                snapshot=snapshot,
            )
        except Exception as e:
            logger.debug(
                "circuit_breaker.audit_log_failed",
                error=e,
            )

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
            logger.warning(
                "circuit_breaker.applying_burn_rate_multiplier",
                multiplier=multiplier,
                service_name=service_name,
            )

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
            logger.debug(
                "circuit_breaker.burn_rate_multiplier_failed",
                error=e,
            )

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
            logger.debug(
                "circuit_breaker.skipping_success_recording_manually",
                service_name=service_name,
            )
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
                "circuit_breaker.circuit_auto_closed_successes",
                service_name=service_name,
                success_threshold=self.config.success_threshold,
            )

            # 동기 콜백 즉시 호출 (이벤트 버스보다 먼저 실행)
            self._invoke_state_change_callbacks(
                service_name=service_name,
                old_state="half_open",
                new_state="closed",
            )

            # Audit 기록 - 자동 복구 완료 (HALF_OPEN → CLOSED)
            try:
                from selfhealing.services.audit import log_cb_state_change_audit

                log_cb_state_change_audit(
                    cb_name=service_name,
                    old_state="half_open",
                    new_state="closed",
                    reason=f"auto_recovery: success_threshold ({self.config.success_threshold}) reached",
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
            open_states = [
                s
                for s in all_states
                if s.state == CircuitState.OPEN and not s.manually_controlled
            ]

            for state in open_states:
                if state.opened_at is None:
                    continue

                elapsed = (now() - state.opened_at).total_seconds()

                effective_cfg = self.get_effective_config(state.service_name)
                if elapsed >= effective_cfg.recovery_timeout:
                    # Transition to half-open
                    self.repository.update_state(
                        service_name=state.service_name,
                        state=CircuitState.HALF_OPEN,
                        success_count=0,
                    )
                    transitioned.append(state.service_name)
                    logger.info(
                        "circuit_breaker.transitioned_open_after",
                        target_service_name=state.service_name,
                        elapsed=elapsed,
                    )

            return {
                "success": True,
                "transitioned": transitioned,
                "count": len(transitioned),
            }

        except Exception as e:
            logger.exception(
                "circuit_breaker.error_checking_recovery_transitions",
                error=e,
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
            # 수동 제어 해제 — 상태/카운터는 유지하고 수동 제어 플래그만 해제
            self.repository.clear_manual_control(service_name, preserve_reason=True)
            logger.info(
                "circuit_breaker.switched_auto_mode",
                service_name=service_name,
            )
            return CircuitBreakerResult(
                success=True,
                service_name=service_name,
                previous_state=previous_state,
                new_state=state.state,
                message=f"Circuit breaker for '{service_name}' switched to auto mode",
            )

    # =========================================================================
    # Ring Resize Reconciliation — 고아 CB 정리
    # =========================================================================

    def reconcile_cb_cell_mapping(self) -> dict[str, Any]:
        """
        Ring Resize 후 CB-Cell 매핑 정합성 보정.

        1. 모든 CB를 순회하여 Composite Key에서 cell_id 추출
        2. 현재 Hash Ring 기준으로 올바른 cell_id 비교
        3. 불일치 시: 고아 CB 아카이브 + 삭제 (상태 전이 없음)
        4. 신규 Cell의 CB는 get_or_create()에 의해 Lazy 생성

        Returns:
            ``{"archived": [...], "errors": [...]}``
        """
        from selfhealing.services.cell_topology import get_cell_registry
        from selfhealing.services.cell_topology.cb_namespace import (
            parse_composite_cb_name,
        )

        registry = get_cell_registry()
        result: dict[str, Any] = {"archived": [], "errors": []}

        try:
            all_states = self.repository.get_all_states()

            for state in all_states:
                base_name, old_cell_id = parse_composite_cb_name(state.service_name)
                if not old_cell_id:
                    continue  # 레거시 단일 키 — 건너뜀

                # 현재 Hash Ring 기준 올바른 Cell
                current_cell_id = registry.get_cell_for_key(base_name)

                if old_cell_id != current_cell_id:
                    # 고아 CB — 아카이브 후 삭제 (상태 복사 절대 금지)
                    try:
                        self._archive_orphan_cb(state)
                        self.repository.delete_state(state.service_name)
                        result["archived"].append(state.service_name)
                    except Exception as e:
                        result["errors"].append(
                            {
                                "service_name": state.service_name,
                                "error": str(e),
                            }
                        )

        except Exception as e:
            logger.exception(
                "cb_reconciliation_failed",
                error=e,
            )
            result["errors"].append({"error": str(e)})

        return result

    def _archive_orphan_cb(self, state: CircuitBreakerStateData) -> None:
        """고아 CB를 히스토리에 기록 후 삭제 준비."""
        try:
            if hasattr(self.repository, "_record_history"):
                self.repository._record_history(
                    state.service_name,
                    state.state,
                    now(),
                    note=f"ring_resize_eviction|old_state={state.state}",
                )
        except Exception as e:
            logger.debug(
                "cb_reconciliation_history_recording",
                target_service_name=state.service_name,
                error=e,
            )
