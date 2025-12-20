"""
Runtime Configuration Manager.

Provides runtime configuration management for self-healing system.
Allows updating configuration values without server restart via API.

Supports 3 apply strategies:
- IMMEDIATE: Apply changes right away
- DELAYED: Apply changes after N seconds (cancellable)
- GRACEFUL: Wait for in-progress operations to complete, then apply

Usage:
    from selfhealing.services.runtime_config import get_runtime_config_manager

    manager = get_runtime_config_manager()
    config = manager.get_all_config()

    # Immediate apply (default for safe configs)
    manager.update_circuit_breaker_config(failure_threshold=10)

    # With apply strategy
    result = manager.update_with_strategy(
        "circuit_breaker",
        {"failure_threshold": 10},
        strategy="delayed",
        delay_seconds=30,
    )
"""

import logging
import threading
from dataclasses import asdict, fields
from typing import Any, Dict, Optional, Tuple

from selfhealing.core.config import (
    CircuitBreakerConfig,
    DLQConfig,
    RetryConfig,
    SLAConfig,
    RateLimitConfig,
    SecurityConfig,
    IdempotencyConfig,
    NotificationConfig,
    ForensicConfig,
    MetricsConfig,
    ErrorBudgetConfig,
)
from selfhealing.core.state_backend import get_state_backend
from selfhealing.core.apply_strategy import (
    ApplyStrategy,
    ApplyOptions,
    get_default_apply_config,
    get_effective_apply_options,
)

logger = logging.getLogger(__name__)

# Singleton instance
_runtime_config_manager: Optional["RuntimeConfigManager"] = None
_manager_lock = threading.Lock()


class RuntimeConfigManager:
    """
    Runtime Configuration Manager.

    Thread-safe singleton that manages runtime configuration
    with persistent storage via StateBackend.

    Features:
    - Get/Update all 10 config types
    - Persistent storage (survives restarts)
    - Thread-safe operations
    - Audit logging
    """

    # Storage keys for each config type
    STORAGE_KEYS = {
        "circuit_breaker": "runtime_config:circuit_breaker",
        "dlq": "runtime_config:dlq",
        "retry": "runtime_config:retry",
        "sla": "runtime_config:sla",
        "rate_limit": "runtime_config:rate_limit",
        "security": "runtime_config:security",
        "idempotency": "runtime_config:idempotency",
        "notification": "runtime_config:notification",
        "forensic": "runtime_config:forensic",
        "metrics": "runtime_config:metrics",
        "error_budget": "runtime_config:error_budget",
    }

    # Default config classes
    CONFIG_CLASSES = {
        "circuit_breaker": CircuitBreakerConfig,
        "dlq": DLQConfig,
        "retry": RetryConfig,
        "sla": SLAConfig,
        "rate_limit": RateLimitConfig,
        "security": SecurityConfig,
        "idempotency": IdempotencyConfig,
        "notification": NotificationConfig,
        "forensic": ForensicConfig,
        "metrics": MetricsConfig,
        "error_budget": ErrorBudgetConfig,
    }

    def __init__(self):
        """Initialize RuntimeConfigManager."""
        self._lock = threading.RLock()
        self._backend = get_state_backend()
        self._cache: Dict[str, Any] = {}
        self._load_all_configs()

    def _load_all_configs(self) -> None:
        """Load all configs from storage or use defaults."""
        with self._lock:
            for config_type, storage_key in self.STORAGE_KEYS.items():
                stored = self._backend.get(storage_key)
                if stored:
                    self._cache[config_type] = stored
                else:
                    # Use defaults
                    config_class = self.CONFIG_CLASSES[config_type]
                    self._cache[config_type] = asdict(config_class())

    def _save_config(self, config_type: str, config_dict: Dict[str, Any]) -> None:
        """Save config to storage."""
        storage_key = self.STORAGE_KEYS[config_type]
        self._backend.set(storage_key, config_dict)
        self._cache[config_type] = config_dict

    def _get_config(self, config_type: str) -> Dict[str, Any]:
        """Get config by type."""
        with self._lock:
            if config_type not in self._cache:
                config_class = self.CONFIG_CLASSES[config_type]
                self._cache[config_type] = asdict(config_class())
            return self._cache[config_type].copy()

    def _update_config(self, config_type: str, **kwargs) -> Dict[str, Any]:
        """Update config fields."""
        with self._lock:
            current = self._get_config(config_type)

            # Update only provided fields
            for key, value in kwargs.items():
                if key in current:
                    current[key] = value
                    logger.info(f"[RuntimeConfig] Updated {config_type}.{key} = {value}")

            self._save_config(config_type, current)
            return current.copy()

    # =========================================================================
    # Public API - Get All
    # =========================================================================

    def get_all_config(self) -> Dict[str, Dict[str, Any]]:
        """Get all configuration."""
        with self._lock:
            return {
                config_type: self._get_config(config_type)
                for config_type in self.STORAGE_KEYS.keys()
            }

    def get_default_strategy(self, config_type: str) -> Dict[str, Any]:
        """Get the default apply strategy for a config type."""
        default = get_default_apply_config(config_type)
        return {
            "strategy": default.strategy.value,
            "delay_seconds": default.delay_seconds,
            "grace_timeout_seconds": default.grace_timeout_seconds,
            "warning_message": default.warning_message,
        }

    def update_with_strategy(
        self,
        config_type: str,
        changes: Dict[str, Any],
        strategy: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        grace_timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update configuration with specified apply strategy.

        Args:
            config_type: Type of config (e.g., "circuit_breaker")
            changes: Dict of field -> new value
            strategy: Apply strategy ("immediate", "delayed", "graceful")
            delay_seconds: Seconds to wait for "delayed" strategy
            grace_timeout_seconds: Max wait for "graceful" strategy

        Returns:
            Dict with:
            - status: "applied" | "scheduled" | "waiting"
            - config: Current/new config values
            - pending_id: ID if scheduled (for cancellation)
            - scheduled_at: When it will be applied (if delayed)
            - warning_message: Any warnings
        """
        # Get effective apply options
        apply_options = get_effective_apply_options(
            config_type,
            strategy=strategy,
            delay_seconds=delay_seconds,
            grace_timeout_seconds=grace_timeout_seconds,
        )

        # Get default config for warning message
        default_config = get_default_apply_config(config_type)

        # Filter changes to only valid fields
        current = self._get_config(config_type)
        valid_changes = {k: v for k, v in changes.items() if k in current and v is not None}

        if not valid_changes:
            return {
                "status": "error",
                "error": "No valid configuration changes provided",
            }

        # Handle based on strategy
        if apply_options.strategy == ApplyStrategy.IMMEDIATE:
            # Apply immediately
            new_config = self._update_config(config_type, **valid_changes)
            return {
                "status": "applied",
                "config": new_config,
                "applied_strategy": "immediate",
                "warning_message": default_config.warning_message,
            }

        elif apply_options.strategy == ApplyStrategy.DELAYED:
            # Schedule for later
            from selfhealing.services.pending_config import get_pending_config_service

            pending_service = get_pending_config_service()
            pending_change = pending_service.create_pending_change(
                config_type=config_type,
                changes=valid_changes,
                apply_options=apply_options,
                previous_values={k: current[k] for k in valid_changes.keys()},
            )

            return {
                "status": "scheduled",
                "pending_id": pending_change.id,
                "scheduled_at": pending_change.scheduled_at,
                "delay_seconds": apply_options.delay_seconds,
                "config_preview": {**current, **valid_changes},
                "applied_strategy": "delayed",
                "warning_message": default_config.warning_message,
                "cancel_available": True,
            }

        elif apply_options.strategy == ApplyStrategy.GRACEFUL:
            # Schedule graceful apply (will be handled by worker)
            from selfhealing.services.pending_config import get_pending_config_service

            pending_service = get_pending_config_service()
            pending_change = pending_service.create_pending_change(
                config_type=config_type,
                changes=valid_changes,
                apply_options=apply_options,
                previous_values={k: current[k] for k in valid_changes.keys()},
            )

            return {
                "status": "waiting",
                "pending_id": pending_change.id,
                "grace_timeout_seconds": apply_options.grace_timeout_seconds,
                "config_preview": {**current, **valid_changes},
                "applied_strategy": "graceful",
                "warning_message": default_config.warning_message or "Waiting for in-progress operations to complete",
                "cancel_available": True,
            }

        return {"status": "error", "error": "Unknown strategy"}

    def apply_pending_change(self, pending_id: str) -> Dict[str, Any]:
        """
        Apply a pending configuration change.

        Called by background worker when scheduled time arrives.
        """
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()
        pending_change = pending_service.get_pending_change(pending_id)

        if not pending_change:
            return {"status": "error", "error": f"Pending change {pending_id} not found"}

        if pending_change.status != "pending":
            return {"status": "error", "error": f"Change {pending_id} is not pending (status: {pending_change.status})"}

        try:
            # Apply the changes
            new_config = self._update_config(pending_change.config_type, **pending_change.changes)

            # Mark as applied
            pending_service.mark_applied(pending_id)

            logger.info(f"[RuntimeConfig] Applied pending change {pending_id}")
            return {
                "status": "applied",
                "pending_id": pending_id,
                "config": new_config,
            }
        except Exception as e:
            pending_service.mark_failed(pending_id, str(e))
            logger.error(f"[RuntimeConfig] Failed to apply pending change {pending_id}: {e}")
            return {
                "status": "error",
                "pending_id": pending_id,
                "error": str(e),
            }

    def cancel_pending_change(self, pending_id: str, cancelled_by: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a pending configuration change."""
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()
        cancelled = pending_service.cancel_pending_change(pending_id, cancelled_by)

        if cancelled:
            return {
                "status": "cancelled",
                "pending_id": pending_id,
                "config_type": cancelled.config_type,
            }
        else:
            return {
                "status": "error",
                "error": f"Pending change {pending_id} not found or already processed",
            }

    def get_pending_changes(self, config_type: Optional[str] = None) -> list:
        """Get all pending configuration changes."""
        from selfhealing.services.pending_config import get_pending_config_service

        pending_service = get_pending_config_service()

        if config_type:
            return [c.to_dict() for c in pending_service.get_pending_changes_for_config(config_type)]
        else:
            return [c.to_dict() for c in pending_service.get_all_pending_changes()]

    def reset_to_defaults(self) -> Dict[str, Dict[str, Any]]:
        """Reset all configuration to defaults."""
        with self._lock:
            for config_type, config_class in self.CONFIG_CLASSES.items():
                default_config = asdict(config_class())
                self._save_config(config_type, default_config)
                logger.info(f"[RuntimeConfig] Reset {config_type} to defaults")

            return self.get_all_config()

    # =========================================================================
    # Circuit Breaker Config
    # =========================================================================

    def get_circuit_breaker_config(self) -> Dict[str, Any]:
        """Get circuit breaker configuration."""
        return self._get_config("circuit_breaker")

    def update_circuit_breaker_config(
        self,
        enabled: Optional[bool] = None,
        failure_threshold: Optional[int] = None,
        recovery_timeout: Optional[int] = None,
        half_open_max_calls: Optional[int] = None,
        success_threshold: Optional[int] = None,
        failure_rate_threshold: Optional[float] = None,
        slow_call_threshold: Optional[float] = None,
        slow_call_rate_threshold: Optional[float] = None,
        minimum_calls: Optional[int] = None,
        sliding_window_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update circuit breaker configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("circuit_breaker", **updates)

    # =========================================================================
    # DLQ Config
    # =========================================================================

    def get_dlq_config(self) -> Dict[str, Any]:
        """Get DLQ configuration."""
        return self._get_config("dlq")

    def update_dlq_config(
        self,
        enabled: Optional[bool] = None,
        max_queue_size: Optional[int] = None,
        batch_size: Optional[int] = None,
        retention_days: Optional[int] = None,
        auto_replay_enabled: Optional[bool] = None,
        auto_replay_delay_seconds: Optional[int] = None,
        max_replay_attempts: Optional[int] = None,
        cleanup_batch_size: Optional[int] = None,
        archive_after_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update DLQ configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("dlq", **updates)

    # =========================================================================
    # Retry Config
    # =========================================================================

    def get_retry_config(self) -> Dict[str, Any]:
        """Get retry configuration."""
        return self._get_config("retry")

    def update_retry_config(
        self,
        max_retries: Optional[int] = None,
        initial_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        exponential_base: Optional[float] = None,
        jitter: Optional[bool] = None,
        jitter_factor: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update retry configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("retry", **updates)

    # =========================================================================
    # SLA Config
    # =========================================================================

    def get_sla_config(self) -> Dict[str, Any]:
        """Get SLA configuration."""
        return self._get_config("sla")

    def update_sla_config(
        self,
        response_time_p50_ms: Optional[int] = None,
        response_time_p95_ms: Optional[int] = None,
        response_time_p99_ms: Optional[int] = None,
        availability_target: Optional[float] = None,
        error_rate_threshold: Optional[float] = None,
        throughput_min_rps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update SLA configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("sla", **updates)

    # =========================================================================
    # Rate Limit Config
    # =========================================================================

    def get_rate_limit_config(self) -> Dict[str, Any]:
        """Get rate limit configuration."""
        return self._get_config("rate_limit")

    def update_rate_limit_config(
        self,
        enabled: Optional[bool] = None,
        requests_per_second: Optional[int] = None,
        burst_size: Optional[int] = None,
        window_seconds: Optional[int] = None,
        block_duration_seconds: Optional[int] = None,
        whitelist_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update rate limit configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("rate_limit", **updates)

    # =========================================================================
    # Security Config
    # =========================================================================

    def get_security_config(self) -> Dict[str, Any]:
        """Get security configuration."""
        return self._get_config("security")

    def update_security_config(
        self,
        max_login_attempts: Optional[int] = None,
        lockout_duration_minutes: Optional[int] = None,
        session_timeout_minutes: Optional[int] = None,
        token_expiry_hours: Optional[int] = None,
        require_mfa: Optional[bool] = None,
        allowed_ip_ranges: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Update security configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("security", **updates)

    # =========================================================================
    # Idempotency Config
    # =========================================================================

    def get_idempotency_config(self) -> Dict[str, Any]:
        """Get idempotency configuration."""
        return self._get_config("idempotency")

    def update_idempotency_config(
        self,
        enabled: Optional[bool] = None,
        key_ttl_seconds: Optional[int] = None,
        max_key_length: Optional[int] = None,
        storage_backend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update idempotency configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("idempotency", **updates)

    # =========================================================================
    # Notification Config
    # =========================================================================

    def get_notification_config(self) -> Dict[str, Any]:
        """Get notification configuration."""
        return self._get_config("notification")

    def update_notification_config(
        self,
        enabled: Optional[bool] = None,
        slack_enabled: Optional[bool] = None,
        email_enabled: Optional[bool] = None,
        pagerduty_enabled: Optional[bool] = None,
        critical_channel: Optional[str] = None,
        high_channel: Optional[str] = None,
        medium_channel: Optional[str] = None,
        rate_limit_per_minute: Optional[int] = None,
        batch_delay_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update notification configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("notification", **updates)

    # =========================================================================
    # Forensic Config
    # =========================================================================

    def get_forensic_config(self) -> Dict[str, Any]:
        """Get forensic configuration."""
        return self._get_config("forensic")

    def update_forensic_config(
        self,
        enabled: Optional[bool] = None,
        capture_request_body: Optional[bool] = None,
        capture_response_body: Optional[bool] = None,
        max_body_size_bytes: Optional[int] = None,
        retention_days: Optional[int] = None,
        sampling_rate: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update forensic configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("forensic", **updates)

    # =========================================================================
    # Metrics Config
    # =========================================================================

    def get_metrics_config(self) -> Dict[str, Any]:
        """Get metrics configuration."""
        return self._get_config("metrics")

    def update_metrics_config(
        self,
        enabled: Optional[bool] = None,
        collection_interval_seconds: Optional[int] = None,
        histogram_buckets: Optional[list] = None,
        export_prometheus: Optional[bool] = None,
        export_statsd: Optional[bool] = None,
        statsd_host: Optional[str] = None,
        statsd_port: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update metrics configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("metrics", **updates)

    # =========================================================================
    # Error Budget Config
    # =========================================================================

    def get_error_budget_config(self) -> Dict[str, Any]:
        """
        Get Error Budget configuration.
        
        Returns:
            dict: Error Budget 및 Burn Rate 임계값 설정
        """
        return self._get_config("error_budget")

    def update_error_budget_config(
        self,
        threshold_healthy: Optional[float] = None,
        threshold_caution: Optional[float] = None,
        threshold_warning: Optional[float] = None,
        threshold_critical: Optional[float] = None,
        burn_rate_fast_critical: Optional[float] = None,
        burn_rate_fast_warning: Optional[float] = None,
        burn_rate_slow_warning: Optional[float] = None,
        burn_rate_slow_info: Optional[float] = None,
        failsafe_alert_enabled: Optional[bool] = None,
        failsafe_cooldown_seconds: Optional[int] = None,
        # Heartbeat (Dead Man's Snitch) 설정
        heartbeat_enabled: Optional[bool] = None,
        heartbeat_interval_seconds: Optional[int] = None,
        heartbeat_timeout_seconds: Optional[int] = None,
        # 복구 알림 (Recovery Notification) 설정
        recovery_alert_enabled: Optional[bool] = None,
        recovery_alert_include_downtime: Optional[bool] = None,
        # Override 에스컬레이션 설정
        escalation_enabled: Optional[bool] = None,
        escalation_channel: Optional[str] = None,
        escalation_mention: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update Error Budget configuration.
        
        Args:
            threshold_healthy: 정상 상태 임계값 (%)
            threshold_caution: 주의 상태 임계값 (%)
            threshold_warning: 경고 상태 임계값 (%)
            threshold_critical: 위험 상태 임계값 (%)
            burn_rate_fast_critical: 빠른 소진 위험 임계값 (x)
            burn_rate_fast_warning: 빠른 소진 경고 임계값 (x)
            burn_rate_slow_warning: 느린 소진 경고 임계값 (x)
            burn_rate_slow_info: 정상 소진율 임계값 (x)
            failsafe_alert_enabled: Fail-Safe 발동 시 알림 발송 여부
            failsafe_cooldown_seconds: 연속 알림 방지 쿨다운 (초)
            heartbeat_enabled: Heartbeat (Dead Man's Snitch) 활성화 여부
            heartbeat_interval_seconds: Heartbeat 발송 주기 (초)
            heartbeat_timeout_seconds: Heartbeat 타임아웃 (초, 이 시간 내 미응답시 Dead)
            recovery_alert_enabled: 복구 완료 알림 발송 여부
            recovery_alert_include_downtime: 복구 알림에 장애 시간 포함 여부
            escalation_enabled: Override 에스컬레이션 활성화 여부
            escalation_channel: 에스컬레이션 알림 채널 (예: #governance)
            escalation_mention: 에스컬레이션 멘션 대상 (예: @cto @security)
            
        Returns:
            dict: 업데이트된 설정값
        """
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("error_budget", **updates)


def get_runtime_config_manager() -> RuntimeConfigManager:
    """Get singleton RuntimeConfigManager instance."""
    global _runtime_config_manager

    if _runtime_config_manager is None:
        with _manager_lock:
            if _runtime_config_manager is None:
                _runtime_config_manager = RuntimeConfigManager()

    return _runtime_config_manager


def reset_runtime_config_manager() -> None:
    """Reset singleton instance (for testing)."""
    global _runtime_config_manager
    with _manager_lock:
        _runtime_config_manager = None
