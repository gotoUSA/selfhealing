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
    LoggingConfig,
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
        "logging": "runtime_config:logging",
        "metrics": "runtime_config:metrics",
        "error_budget": "runtime_config:error_budget",
        "slo": "runtime_config:slo",
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
        "logging": LoggingConfig,
        "metrics": MetricsConfig,
        "error_budget": ErrorBudgetConfig,
        "slo": None,  # SLO는 별도 처리 (SLOConfigRuntime)
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
                    if config_class is not None:
                        self._cache[config_type] = asdict(config_class())
                    else:
                        # SLO는 별도 기본값 사용
                        self._cache[config_type] = self._get_slo_defaults()

    def _get_slo_defaults(self) -> Dict[str, Any]:
        """Get default SLO configuration."""
        return {
            "default_window_days": 30,
            "default_target": 0.999,
            "default_fast_burn_rate": 14.4,
            "default_slow_burn_rate": 3.0,
            "slos": [
                {
                    "name": "availability",
                    "sli_type": "availability",
                    "target": 0.999,
                    "window_days": 30,
                    "description": "API availability - proportion of successful requests",
                    "service_name": "",
                    "domain": "",
                    "warning_threshold": None,
                    "critical_threshold": None,
                    "fast_burn_rate": 14.4,
                    "slow_burn_rate": 3.0,
                },
                {
                    "name": "latency_p99",
                    "sli_type": "latency_p99",
                    "target": 0.500,
                    "window_days": 7,
                    "description": "99th percentile response time should be under 500ms",
                    "service_name": "",
                    "domain": "",
                    "warning_threshold": None,
                    "critical_threshold": None,
                    "fast_burn_rate": 14.4,
                    "slow_burn_rate": 3.0,
                },
                {
                    "name": "error_rate",
                    "sli_type": "error_rate",
                    "target": 0.999,
                    "window_days": 30,
                    "description": "Error rate should be under 0.1%",
                    "service_name": "",
                    "domain": "",
                    "warning_threshold": None,
                    "critical_threshold": None,
                    "fast_burn_rate": 14.4,
                    "slow_burn_rate": 3.0,
                },
            ],
        }

    def _save_config(self, config_type: str, config_dict: Dict[str, Any]) -> None:
        """Save config to storage."""
        storage_key = self.STORAGE_KEYS[config_type]
        self._backend.set(storage_key, config_dict)
        self._cache[config_type] = config_dict

    def _get_config(self, config_type: str) -> Dict[str, Any]:
        """
        Get config by type.
        
        새 필드가 추가되었을 경우, 저장된 설정과 기본값을 병합하여 반환합니다.
        """
        with self._lock:
            config_class = self.CONFIG_CLASSES.get(config_type)
            
            # Get defaults
            if config_class is not None:
                defaults = asdict(config_class())
            elif config_type == "slo":
                defaults = self._get_slo_defaults()
            else:
                defaults = {}
            
            if config_type not in self._cache:
                self._cache[config_type] = defaults.copy()
            else:
                # Merge defaults with cached values (cached values take precedence)
                # This ensures new fields from defaults are included
                merged = defaults.copy()
                merged.update(self._cache[config_type])
                self._cache[config_type] = merged
            
            return self._cache[config_type].copy()

    def _update_config(
        self,
        config_type: str,
        changed_by: str = "system",
        reason: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """Update config fields with history tracking.
        
        Args:
            config_type: Type of config (e.g., "circuit_breaker")
            changed_by: User or system that made the change
            reason: Reason for the change
            **kwargs: Config fields to update
            
        Returns:
            Updated config values
        """
        with self._lock:
            current = self._get_config(config_type)
            previous = current.copy()  # Snapshot before changes
            config_class = self.CONFIG_CLASSES.get(config_type)
            
            # Get valid field names from config class (if available)
            if config_class is not None:
                valid_fields = {f.name for f in fields(config_class)}
            else:
                valid_fields = set(current.keys())

            # Track Safe Default applications
            applied_safe_defaults = []

            # Update only provided fields that are valid
            for key, value in kwargs.items():
                if key in valid_fields:
                    # Check if Safe Default should be applied
                    from selfhealing.core.safe_defaults import is_valid_value, get_safe_default
                    if not is_valid_value(config_type, key, value):
                        safe_value = get_safe_default(config_type, key)
                        if safe_value is not None:
                            applied_safe_defaults.append(
                                f"{key}: {value!r} → {safe_value!r}"
                            )
                            current[key] = safe_value
                            logger.warning(
                                f"[RuntimeConfig] Safe default applied: "
                                f"{config_type}.{key} ({value!r} → {safe_value!r})"
                            )
                        else:
                            current[key] = value
                            logger.info(
                                f"[RuntimeConfig] Updated {config_type}.{key} = {value}"
                            )
                    else:
                        current[key] = value
                        logger.info(
                            f"[RuntimeConfig] Updated {config_type}.{key} = {value}"
                        )

            # Diff-Aware: Only save if there are actual changes
            if previous == current:
                logger.debug(
                    f"[RuntimeConfig] No changes detected for {config_type}"
                )
                return current.copy()

            self._save_config(config_type, current)

            # Build final reason with Safe Default marker
            final_reason = reason or f"Updated: {list(kwargs.keys())}"
            if applied_safe_defaults:
                final_reason = (
                    f"⚠️ Safe Default applied: {', '.join(applied_safe_defaults)} | "
                    f"{final_reason}"
                )

            # Save to ConfigHistory (best-effort)
            self._save_to_history(
                config_type=config_type,
                values=current,
                changed_by=changed_by,
                reason=final_reason,
            )

            return current.copy()

    def _save_to_history(
        self,
        config_type: str,
        values: Dict[str, Any],
        changed_by: str,
        reason: str,
    ) -> None:
        """Save config version to history (best-effort).
        
        This method never raises exceptions - history saving failure
        should not break config updates.
        
        Args:
            config_type: Type of config
            values: Current config values
            changed_by: User or system that made the change
            reason: Reason for the change
        """
        try:
            from selfhealing.services.config_history import get_config_history_service
            history_service = get_config_history_service()
            history_service.save_version(
                config_type=config_type,
                values=values,
                changed_by=changed_by,
                reason=reason,
            )
            logger.debug(
                f"[RuntimeConfig] Saved history for {config_type} by {changed_by}"
            )
        except Exception as e:
            # Graceful degradation - history save failure should not break config update
            logger.warning(f"[RuntimeConfig] Failed to save history: {e}")

    # =========================================================================
    # Public API - Get All
    # =========================================================================

    def get_all_config(self) -> Dict[str, Dict[str, Any]]:
        """Get all configuration."""
        with self._lock:
            return {config_type: self._get_config(config_type) for config_type in self.STORAGE_KEYS.keys()}

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
        changed_by: str = "system",
        reason: str = "",
        strategy: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        grace_timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Update configuration with specified apply strategy.

        Args:
            config_type: Type of config (e.g., "circuit_breaker")
            changes: Dict of field -> new value
            changed_by: User or system that made the change
            reason: Reason for the change
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
            new_config = self._update_config(
                config_type,
                changed_by=changed_by,
                reason=reason,
                **valid_changes
            )
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
            new_config = self._update_config(
                pending_change.config_type,
                changed_by="pending_config_worker",
                reason=f"Pending change {pending_id} applied",
                **pending_change.changes
            )

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
                if config_class is not None:
                    default_config = asdict(config_class())
                else:
                    # SLO는 별도 기본값 사용
                    default_config = self._get_slo_defaults()
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
        # Phase 5: 추가 설정
        max_stack_frames: Optional[int] = None,
        max_context_size_bytes: Optional[int] = None,
        include_local_variables: Optional[bool] = None,
        sanitize_sensitive_data: Optional[bool] = None,
        sensitive_key_patterns: Optional[list] = None,
        error_message_max_length: Optional[int] = None,
        response_body_max_length: Optional[int] = None,
        user_agent_max_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update forensic configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("forensic", **updates)

    # =========================================================================
    # Logging Config (Phase 5)
    # Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
    # =========================================================================

    def get_logging_config(self) -> Dict[str, Any]:
        """
        Get logging configuration.
        
        Returns:
            dict: 컴포넌트별 로그 레벨 및 포맷 설정
        """
        return self._get_config("logging")

    def update_logging_config(
        self,
        # 컴포넌트별 로그 레벨
        dlq_log_level: Optional[str] = None,
        circuit_breaker_log_level: Optional[str] = None,
        replay_log_level: Optional[str] = None,
        sla_log_level: Optional[str] = None,
        forensic_log_level: Optional[str] = None,
        emergency_log_level: Optional[str] = None,
        chaos_log_level: Optional[str] = None,
        l2_storage_log_level: Optional[str] = None,
        # 로그 포맷 설정
        include_timestamps: Optional[bool] = None,
        include_request_id: Optional[bool] = None,
        include_user_info: Optional[bool] = None,
        # 로그 출력 설정
        console_output_enabled: Optional[bool] = None,
        file_output_enabled: Optional[bool] = None,
        structured_json: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update logging configuration.
        
        Args:
            dlq_log_level: DLQ 관련 로그 레벨
            circuit_breaker_log_level: Circuit Breaker 로그 레벨
            replay_log_level: DLQ Replay 로그 레벨
            sla_log_level: SLA/SLO 모니터링 로그 레벨
            forensic_log_level: Forensic 분석 로그 레벨
            emergency_log_level: Emergency Mode 로그 레벨
            chaos_log_level: Chaos Engineering 로그 레벨
            l2_storage_log_level: L2 Storage Resilience 로그 레벨
            include_timestamps: 로그에 타임스탬프 포함 여부
            include_request_id: 로그에 Request ID 포함 여부
            include_user_info: 로그에 사용자 정보 포함 여부
            console_output_enabled: 콘솔 로그 출력 활성화
            file_output_enabled: 파일 로그 출력 활성화
            structured_json: JSON 구조화 로그 포맷 사용
            
        Returns:
            dict: 업데이트된 설정값
        """
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("logging", **updates)

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

    # =========================================================================
    # SLO Config (Service Level Objectives)
    # =========================================================================

    def get_slo_config(self) -> Dict[str, Any]:
        """
        Get SLO configuration.

        Returns:
            dict: SLO 기본값 및 SLO 정의 목록
                - default_window_days: 새 SLO 생성 시 기본 윈도우
                - default_target: 새 SLO 생성 시 기본 타겟
                - default_fast_burn_rate: 새 SLO 생성 시 기본 빠른 소진율
                - default_slow_burn_rate: 새 SLO 생성 시 기본 느린 소진율
                - slos: SLO 정의 목록 (각각 name, target, window_days 등)
        """
        return self._get_config("slo")

    def update_slo_config(
        self,
        default_window_days: Optional[int] = None,
        default_target: Optional[float] = None,
        default_fast_burn_rate: Optional[float] = None,
        default_slow_burn_rate: Optional[float] = None,
        slo: Optional[Dict[str, Any]] = None,
        slos: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Update SLO configuration.

        Args:
            default_window_days: 새 SLO 생성 시 기본 윈도우 (일)
            default_target: 새 SLO 생성 시 기본 타겟 (0.0~1.0)
            default_fast_burn_rate: 새 SLO 생성 시 기본 빠른 소진율
            default_slow_burn_rate: 새 SLO 생성 시 기본 느린 소진율
            slo: 추가/수정할 단일 SLO 정의
            slos: 추가/수정할 SLO 정의 목록

        Returns:
            dict: 업데이트된 SLO 설정
        """
        with self._lock:
            current = self._get_config("slo")

            # Update defaults
            if default_window_days is not None:
                current["default_window_days"] = default_window_days
                logger.info(f"[RuntimeConfig] Updated slo.default_window_days = {default_window_days}")
            if default_target is not None:
                current["default_target"] = default_target
                logger.info(f"[RuntimeConfig] Updated slo.default_target = {default_target}")
            if default_fast_burn_rate is not None:
                current["default_fast_burn_rate"] = default_fast_burn_rate
                logger.info(f"[RuntimeConfig] Updated slo.default_fast_burn_rate = {default_fast_burn_rate}")
            if default_slow_burn_rate is not None:
                current["default_slow_burn_rate"] = default_slow_burn_rate
                logger.info(f"[RuntimeConfig] Updated slo.default_slow_burn_rate = {default_slow_burn_rate}")

            # Add/update SLOs
            slos_to_update = []
            if slo is not None:
                slos_to_update.append(slo)
            if slos is not None:
                slos_to_update.extend(slos)

            for slo_def in slos_to_update:
                self._upsert_slo(current, slo_def)

            self._save_config("slo", current)
            return current.copy()

    def _upsert_slo(self, config: Dict[str, Any], slo_def: Dict[str, Any]) -> None:
        """Insert or update an SLO definition."""
        if "slos" not in config:
            config["slos"] = []

        slo_name = slo_def.get("name")
        if not slo_name:
            logger.warning("[RuntimeConfig] SLO definition missing 'name' field, skipping")
            return

        # Find existing SLO by name
        existing_idx = None
        for idx, existing in enumerate(config["slos"]):
            if existing.get("name") == slo_name:
                existing_idx = idx
                break

        # Apply defaults for new SLO
        if existing_idx is None:
            new_slo = {
                "name": slo_name,
                "sli_type": slo_def.get("sli_type", "availability"),
                "target": slo_def.get("target", config.get("default_target", 0.999)),
                "window_days": slo_def.get("window_days", config.get("default_window_days", 30)),
                "description": slo_def.get("description", ""),
                "service_name": slo_def.get("service_name", ""),
                "domain": slo_def.get("domain", ""),
                "warning_threshold": slo_def.get("warning_threshold"),
                "critical_threshold": slo_def.get("critical_threshold"),
                "fast_burn_rate": slo_def.get("fast_burn_rate", config.get("default_fast_burn_rate", 14.4)),
                "slow_burn_rate": slo_def.get("slow_burn_rate", config.get("default_slow_burn_rate", 3.0)),
            }
            config["slos"].append(new_slo)
            logger.info(f"[RuntimeConfig] Added SLO: {slo_name}")
        else:
            # Update existing SLO (only provided fields)
            for key, value in slo_def.items():
                if value is not None:
                    config["slos"][existing_idx][key] = value
            logger.info(f"[RuntimeConfig] Updated SLO: {slo_name}")

    def delete_slo(self, slo_name: str) -> Dict[str, Any]:
        """
        Delete an SLO by name.

        Args:
            slo_name: 삭제할 SLO 이름

        Returns:
            dict: 결과 (status, deleted_slo, remaining_count)
        """
        with self._lock:
            current = self._get_config("slo")

            if "slos" not in current:
                return {"status": "not_found", "error": f"SLO '{slo_name}' not found"}

            original_count = len(current["slos"])
            deleted_slo = None

            for slo in current["slos"]:
                if slo.get("name") == slo_name:
                    deleted_slo = slo
                    break

            if deleted_slo is None:
                return {"status": "not_found", "error": f"SLO '{slo_name}' not found"}

            current["slos"] = [s for s in current["slos"] if s.get("name") != slo_name]
            self._save_config("slo", current)

            logger.info(f"[RuntimeConfig] Deleted SLO: {slo_name}")
            return {
                "status": "deleted",
                "deleted_slo": deleted_slo,
                "remaining_count": len(current["slos"]),
            }

    def get_slo_by_name(self, slo_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific SLO by name.

        Args:
            slo_name: SLO 이름

        Returns:
            dict or None: SLO 정의 또는 None
        """
        config = self._get_config("slo")
        for slo in config.get("slos", []):
            if slo.get("name") == slo_name:
                return slo
        return None

    # =========================================================================
    # Chaos Engineering Config
    # =========================================================================

    def get_chaos_config(self) -> Dict[str, Any]:
        """
        Get Chaos Engineering configuration.

        Returns:
            dict: Chaos scheduler, safety guard, and blast radius settings
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            stored = self._backend.get(storage_key)
            if stored:
                return stored
            # Return empty config if not set
            return {
                "scheduler_config": {},
                "safety_guard_config": {},
                "blast_radius_policy": {},
                "report_config": {},
            }

    def update_chaos_config(
        self,
        scheduler_config: Optional[Dict[str, Any]] = None,
        safety_guard_config: Optional[Dict[str, Any]] = None,
        blast_radius_policy: Optional[Dict[str, Any]] = None,
        report_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Engineering configuration.

        Args:
            scheduler_config: ChaosScheduler configuration
            safety_guard_config: SafetyGuard configuration
            blast_radius_policy: BlastRadius policy
            report_config: Report generator configuration

        Returns:
            dict: Updated configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()

            if scheduler_config is not None:
                current["scheduler_config"] = scheduler_config
                logger.info("[RuntimeConfig] Updated chaos.scheduler_config")

            if safety_guard_config is not None:
                current["safety_guard_config"] = safety_guard_config
                logger.info("[RuntimeConfig] Updated chaos.safety_guard_config")

            if blast_radius_policy is not None:
                current["blast_radius_policy"] = blast_radius_policy
                logger.info("[RuntimeConfig] Updated chaos.blast_radius_policy")

            if report_config is not None:
                current["report_config"] = report_config
                logger.info("[RuntimeConfig] Updated chaos.report_config")

            self._backend.set(storage_key, current)
            return current.copy()

    def update_chaos_ttl_config(
        self,
        default_ttl_seconds: Optional[int] = None,
        min_ttl_seconds: Optional[int] = None,
        max_ttl_seconds: Optional[int] = None,
        auto_expiration_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos TTL configuration.

        Args:
            default_ttl_seconds: Default TTL for experiments (10분 기본)
            min_ttl_seconds: Minimum allowed TTL
            max_ttl_seconds: Maximum allowed TTL
            auto_expiration_enabled: Enable auto-expiration

        Returns:
            dict: Updated TTL configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            ttl_config = current.get("ttl_config", {
                "default_ttl_seconds": 600,
                "min_ttl_seconds": 60,
                "max_ttl_seconds": 3600,
                "auto_expiration_enabled": True,
            })

            if default_ttl_seconds is not None:
                ttl_config["default_ttl_seconds"] = default_ttl_seconds
            if min_ttl_seconds is not None:
                ttl_config["min_ttl_seconds"] = min_ttl_seconds
            if max_ttl_seconds is not None:
                ttl_config["max_ttl_seconds"] = max_ttl_seconds
            if auto_expiration_enabled is not None:
                ttl_config["auto_expiration_enabled"] = auto_expiration_enabled

            current["ttl_config"] = ttl_config
            self._backend.set(storage_key, current)
            logger.info("[RuntimeConfig] Updated chaos.ttl_config")
            return ttl_config

    def update_chaos_stop_conditions_config(
        self,
        max_error_rate_percent: Optional[float] = None,
        max_latency_p99_ms: Optional[int] = None,
        max_latency_p95_ms: Optional[int] = None,
        min_error_budget_percent: Optional[float] = None,
        check_interval_seconds: Optional[int] = None,
        consecutive_breaches_required: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Stop Conditions configuration.

        Args:
            max_error_rate_percent: Max error rate before auto-stop
            max_latency_p99_ms: Max P99 latency before auto-stop
            max_latency_p95_ms: Max P95 latency before auto-stop
            min_error_budget_percent: Min error budget before auto-stop
            check_interval_seconds: Interval for checking conditions
            consecutive_breaches_required: Required consecutive breaches
            enabled: Enable/disable stop conditions

        Returns:
            dict: Updated stop conditions configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            stop_config = current.get("stop_conditions_config", {
                "max_error_rate_percent": 5.0,
                "max_latency_p99_ms": 2000,
                "max_latency_p95_ms": 1000,
                "min_error_budget_percent": 10.0,
                "check_interval_seconds": 10,
                "consecutive_breaches_required": 2,
                "enabled": True,
            })

            if max_error_rate_percent is not None:
                stop_config["max_error_rate_percent"] = max_error_rate_percent
            if max_latency_p99_ms is not None:
                stop_config["max_latency_p99_ms"] = max_latency_p99_ms
            if max_latency_p95_ms is not None:
                stop_config["max_latency_p95_ms"] = max_latency_p95_ms
            if min_error_budget_percent is not None:
                stop_config["min_error_budget_percent"] = min_error_budget_percent
            if check_interval_seconds is not None:
                stop_config["check_interval_seconds"] = check_interval_seconds
            if consecutive_breaches_required is not None:
                stop_config["consecutive_breaches_required"] = consecutive_breaches_required
            if enabled is not None:
                stop_config["enabled"] = enabled

            current["stop_conditions_config"] = stop_config
            self._backend.set(storage_key, current)
            logger.info("[RuntimeConfig] Updated chaos.stop_conditions_config")
            return stop_config

    def update_chaos_dry_run_config(
        self,
        enabled: Optional[bool] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update Chaos Dry Run configuration.

        Args:
            enabled: Enable/disable dry run mode
            reason: Reason for dry run mode

        Returns:
            dict: Updated dry run configuration
        """
        storage_key = "runtime_config:chaos"
        with self._lock:
            current = self.get_chaos_config()
            dry_run_config = current.get("dry_run_config", {
                "enabled": True,
                "reason": "Initial deployment - simulation mode",
            })

            if enabled is not None:
                dry_run_config["enabled"] = enabled
            if reason is not None:
                dry_run_config["reason"] = reason

            current["dry_run_config"] = dry_run_config
            self._backend.set(storage_key, current)
            logger.info(f"[RuntimeConfig] Updated chaos.dry_run_config: enabled={dry_run_config['enabled']}")
            return dry_run_config


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
