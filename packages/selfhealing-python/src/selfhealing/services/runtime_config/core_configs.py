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

from typing import Any, Dict, Optional


class CoreConfigMixin:
    """Mixin providing core configuration methods."""

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
