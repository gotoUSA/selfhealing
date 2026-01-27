"""
Core Configuration Mixins.

Provides get/update methods for core configuration types:
- Circuit Breaker
- DLQ
- Retry
- SLA
- Rate Limit
- Security
- Idempotency
- Notification
- Forensic
"""

from __future__ import annotations

from typing import Any


class CoreConfigMixin:
    """Mixin providing core configuration methods."""

    # =========================================================================
    # Circuit Breaker Config
    # =========================================================================

    def get_circuit_breaker_config(self) -> dict[str, Any]:
        """Get circuit breaker configuration."""
        return self._get_config("circuit_breaker")

    def update_circuit_breaker_config(
        self,
        enabled: bool | None = None,
        failure_threshold: int | None = None,
        recovery_timeout: int | None = None,
        half_open_max_calls: int | None = None,
        success_threshold: int | None = None,
        failure_rate_threshold: float | None = None,
        slow_call_threshold: float | None = None,
        slow_call_rate_threshold: float | None = None,
        minimum_calls: int | None = None,
        sliding_window_size: int | None = None,
    ) -> dict[str, Any]:
        """Update circuit breaker configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("circuit_breaker", **updates)

    # =========================================================================
    # DLQ Config
    # =========================================================================

    def get_dlq_config(self) -> dict[str, Any]:
        """Get DLQ configuration."""
        return self._get_config("dlq")

    def update_dlq_config(
        self,
        enabled: bool | None = None,
        max_queue_size: int | None = None,
        batch_size: int | None = None,
        retention_days: int | None = None,
        auto_replay_enabled: bool | None = None,
        auto_replay_delay_seconds: int | None = None,
        max_replay_attempts: int | None = None,
        cleanup_batch_size: int | None = None,
        archive_after_days: int | None = None,
    ) -> dict[str, Any]:
        """Update DLQ configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("dlq", **updates)

    # =========================================================================
    # Retry Config
    # =========================================================================

    def get_retry_config(self) -> dict[str, Any]:
        """Get retry configuration."""
        return self._get_config("retry")

    def update_retry_config(
        self,
        max_retries: int | None = None,
        initial_delay: float | None = None,
        max_delay: float | None = None,
        exponential_base: float | None = None,
        jitter: bool | None = None,
        jitter_factor: float | None = None,
    ) -> dict[str, Any]:
        """Update retry configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("retry", **updates)

    # =========================================================================
    # SLA Config
    # =========================================================================

    def get_sla_config(self) -> dict[str, Any]:
        """Get SLA configuration."""
        return self._get_config("sla")

    def update_sla_config(
        self,
        response_time_p50_ms: int | None = None,
        response_time_p95_ms: int | None = None,
        response_time_p99_ms: int | None = None,
        availability_target: float | None = None,
        error_rate_threshold: float | None = None,
        throughput_min_rps: int | None = None,
    ) -> dict[str, Any]:
        """Update SLA configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("sla", **updates)

    # =========================================================================
    # Rate Limit Config
    # =========================================================================

    def get_rate_limit_config(self) -> dict[str, Any]:
        """Get rate limit configuration."""
        return self._get_config("rate_limit")

    def update_rate_limit_config(
        self,
        enabled: bool | None = None,
        requests_per_second: int | None = None,
        burst_size: int | None = None,
        window_seconds: int | None = None,
        block_duration_seconds: int | None = None,
        whitelist_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update rate limit configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("rate_limit", **updates)

    # =========================================================================
    # Security Config
    # =========================================================================

    def get_security_config(self) -> dict[str, Any]:
        """Get security configuration."""
        return self._get_config("security")

    def update_security_config(
        self,
        max_login_attempts: int | None = None,
        lockout_duration_minutes: int | None = None,
        session_timeout_minutes: int | None = None,
        token_expiry_hours: int | None = None,
        require_mfa: bool | None = None,
        allowed_ip_ranges: list | None = None,
    ) -> dict[str, Any]:
        """Update security configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("security", **updates)

    # =========================================================================
    # Idempotency Config
    # =========================================================================

    def get_idempotency_config(self) -> dict[str, Any]:
        """Get idempotency configuration."""
        return self._get_config("idempotency")

    def update_idempotency_config(
        self,
        enabled: bool | None = None,
        key_ttl_seconds: int | None = None,
        max_key_length: int | None = None,
        storage_backend: str | None = None,
    ) -> dict[str, Any]:
        """Update idempotency configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("idempotency", **updates)

    # =========================================================================
    # Notification Config
    # =========================================================================

    def get_notification_config(self) -> dict[str, Any]:
        """Get notification configuration."""
        return self._get_config("notification")

    def update_notification_config(
        self,
        enabled: bool | None = None,
        slack_enabled: bool | None = None,
        email_enabled: bool | None = None,
        pagerduty_enabled: bool | None = None,
        critical_channel: str | None = None,
        high_channel: str | None = None,
        medium_channel: str | None = None,
        rate_limit_per_minute: int | None = None,
        batch_delay_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Update notification configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("notification", **updates)

    # =========================================================================
    # Forensic Config
    # =========================================================================

    def get_forensic_config(self) -> dict[str, Any]:
        """Get forensic configuration."""
        return self._get_config("forensic")

    def update_forensic_config(
        self,
        enabled: bool | None = None,
        capture_request_body: bool | None = None,
        capture_response_body: bool | None = None,
        max_body_size_bytes: int | None = None,
        retention_days: int | None = None,
        sampling_rate: float | None = None,
        # 추가 설정
        max_stack_frames: int | None = None,
        max_context_size_bytes: int | None = None,
        include_local_variables: bool | None = None,
        sanitize_sensitive_data: bool | None = None,
        sensitive_key_patterns: list | None = None,
        error_message_max_length: int | None = None,
        response_body_max_length: int | None = None,
        user_agent_max_length: int | None = None,
    ) -> dict[str, Any]:
        """Update forensic configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("forensic", **updates)
