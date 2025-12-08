"""
Self-Healing Configuration Module

Centralized configuration for all self-healing layer parameters.
This module provides a single source of truth for all constants,
thresholds, and configuration values used across the self-healing layer.

Configuration can be overridden via Django settings.SELF_HEALING dict.

Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §10 (Configuration Reference)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    pass


# =============================================================================
# SLA Thresholds (Recovery Time SLA by Domain)
# =============================================================================


@dataclass(frozen=True)
class SLAThresholds:
    """
    SLA thresholds for each domain.

    These define the maximum allowed time for a failed operation
    to remain in PENDING status before it's considered an SLA breach.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §3 (Recovery SLA)
    """

    payment_hours: int = 1
    point_hours: int = 4
    inventory_hours: int = 2
    webhook_hours: int = 8
    notification_hours: int = 24
    default_hours: int = 24  # Fallback for unknown domains

    @classmethod
    def from_settings(cls) -> "SLAThresholds":
        """Load SLA thresholds from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        sla_config = self_healing.get("SLA", {})

        return cls(
            payment_hours=sla_config.get("PAYMENT_HOURS", 1),
            point_hours=sla_config.get("POINT_HOURS", 4),
            inventory_hours=sla_config.get("INVENTORY_HOURS", 2),
            webhook_hours=sla_config.get("WEBHOOK_HOURS", 8),
            notification_hours=sla_config.get("NOTIFICATION_HOURS", 24),
            default_hours=sla_config.get("DEFAULT_HOURS", 24),
        )

    def get_threshold(self, domain: str) -> timedelta:
        """
        Get the SLA threshold for a domain.

        Args:
            domain: Domain name (payment, point, inventory, webhook, notification)

        Returns:
            timedelta representing the SLA threshold
        """
        domain_map = {
            "payment": self.payment_hours,
            "point": self.point_hours,
            "inventory": self.inventory_hours,
            "webhook": self.webhook_hours,
            "notification": self.notification_hours,
        }
        hours = domain_map.get(domain.lower(), self.default_hours)
        return timedelta(hours=hours)

    def get_all_thresholds(self) -> dict[str, timedelta]:
        """Get all domain thresholds as a dictionary."""
        return {
            "payment": timedelta(hours=self.payment_hours),
            "point": timedelta(hours=self.point_hours),
            "inventory": timedelta(hours=self.inventory_hours),
            "webhook": timedelta(hours=self.webhook_hours),
            "notification": timedelta(hours=self.notification_hours),
        }


# =============================================================================
# Idempotency Configuration
# =============================================================================


@dataclass(frozen=True)
class IdempotencyConfig:
    """
    Configuration for idempotency service.

    Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §7 (Idempotency Guarantees)
    """

    # Default TTL for cache-based idempotency (seconds)
    default_cache_ttl: int = 60

    # Extended TTL for payment operations (seconds)
    payment_cache_ttl: int = 300

    # Webhook idempotency TTL (seconds)
    webhook_cache_ttl: int = 60

    @classmethod
    def from_settings(cls) -> "IdempotencyConfig":
        """Load idempotency configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        idempotency_config = self_healing.get("IDEMPOTENCY", {})

        return cls(
            default_cache_ttl=idempotency_config.get("DEFAULT_CACHE_TTL", 60),
            payment_cache_ttl=idempotency_config.get("PAYMENT_CACHE_TTL", 300),
            webhook_cache_ttl=idempotency_config.get("WEBHOOK_CACHE_TTL", 60),
        )


# =============================================================================
# Security Configuration
# =============================================================================


@dataclass(frozen=True)
class SecurityThresholds:
    """
    Security-related thresholds and timeouts.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §5 (Security Violation Handling)
    """

    # Rate limit abuse detection
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 100

    # IP ban settings
    temporary_ban_hours: int = 1
    permanent_ban_threshold: int = 5  # violations before permanent ban

    # Suspicious IP tracking cache timeout (seconds)
    suspicious_ip_cache_timeout: int = 86400  # 24 hours

    # Injection attempt ban duration (hours)
    injection_ban_hours: int = 24

    # Failed login threshold
    failed_login_threshold: int = 5

    # Cache key prefixes
    suspicious_ip_cache_prefix: str = "security:suspicious_ip:"
    banned_ip_cache_prefix: str = "security:banned_ip:"

    @classmethod
    def from_settings(cls) -> "SecurityThresholds":
        """Load security configuration from Django settings."""
        security_config = getattr(settings, "SECURITY", {})
        self_healing = getattr(settings, "SELF_HEALING", {})
        security_sh = self_healing.get("SECURITY", {})

        return cls(
            rate_limit_window_seconds=security_config.get(
                "RATE_LIMIT_WINDOW", security_sh.get("RATE_LIMIT_WINDOW", 60)
            ),
            rate_limit_max_requests=security_config.get(
                "RATE_LIMIT_MAX", security_sh.get("RATE_LIMIT_MAX", 100)
            ),
            temporary_ban_hours=security_config.get(
                "TEMP_BAN_HOURS", security_sh.get("TEMP_BAN_HOURS", 1)
            ),
            permanent_ban_threshold=security_config.get(
                "PERM_BAN_THRESHOLD", security_sh.get("PERM_BAN_THRESHOLD", 5)
            ),
            suspicious_ip_cache_timeout=security_sh.get("SUSPICIOUS_IP_CACHE_TIMEOUT", 86400),
            injection_ban_hours=security_sh.get("INJECTION_BAN_HOURS", 24),
            failed_login_threshold=security_config.get(
                "FAILED_LOGIN_THRESHOLD", security_sh.get("FAILED_LOGIN_THRESHOLD", 5)
            ),
        )


# =============================================================================
# Notification Configuration (Message Limits)
# =============================================================================


@dataclass(frozen=True)
class NotificationLimits:
    """
    Limits for notification message formatting.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4 (Escalation & Notifications)
    """

    # Slack API limits
    slack_block_text_limit: int = 3000

    # Message truncation limits
    description_max_length: int = 500
    action_taken_max_length: int = 200
    title_max_length: int = 150

    # HTTP request timeout
    notification_timeout_seconds: int = 10

    @classmethod
    def from_settings(cls) -> "NotificationLimits":
        """Load notification limits from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        notification_config = self_healing.get("NOTIFICATIONS", {})
        limits = notification_config.get("LIMITS", {})

        return cls(
            slack_block_text_limit=limits.get("SLACK_BLOCK_TEXT_LIMIT", 3000),
            description_max_length=limits.get("DESCRIPTION_MAX_LENGTH", 500),
            action_taken_max_length=limits.get("ACTION_TAKEN_MAX_LENGTH", 200),
            title_max_length=limits.get("TITLE_MAX_LENGTH", 150),
            notification_timeout_seconds=limits.get("TIMEOUT_SECONDS", 10),
        )


# =============================================================================
# Retry Configuration
# =============================================================================


@dataclass(frozen=True)
class RetrySettings:
    """
    Retry behavior configuration.

    Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §8 (Retry Strategy)
    """

    max_attempts: int = 3
    backoff_base: int = 4  # Base for exponential (4^n seconds)
    backoff_max: int = 180  # Maximum wait time (3 minutes)
    jitter_percent: int = 25  # ±25% random jitter
    min_delay: int = 1  # Minimum delay in seconds

    @classmethod
    def from_settings(cls) -> "RetrySettings":
        """Load retry configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        retry_config = self_healing.get("RETRY", {})

        return cls(
            max_attempts=retry_config.get("MAX_ATTEMPTS", 3),
            backoff_base=retry_config.get("BACKOFF_BASE", 4),
            backoff_max=retry_config.get("BACKOFF_MAX", 180),
            jitter_percent=retry_config.get("JITTER_PERCENT", 25),
            min_delay=retry_config.get("MIN_DELAY", 1),
        )


# =============================================================================
# Circuit Breaker Configuration
# =============================================================================


@dataclass(frozen=True)
class CircuitBreakerSettings:
    """
    Circuit breaker configuration.

    Reference: docs/L3_SELF_HEALING_ARCHITECTURE.md §10 (Circuit Breaker Policy)
    """

    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    success_threshold: int = 2
    manual_override_ttl_minutes: int = 90  # Default 90 min, max recommended 180
    half_open_request_limit: int = 10  # Max requests allowed in half-open state
    max_pending_duration_hours: int = 4  # SLA for pending DLQ items
    max_retry_lifetime_hours: int = 24  # Max time to attempt retries

    @classmethod
    def from_settings(cls) -> "CircuitBreakerSettings":
        """Load circuit breaker configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        cb_config = self_healing.get("CIRCUIT_BREAKER", {})
        governance = self_healing.get("GOVERNANCE", {})

        return cls(
            enabled=cb_config.get("ENABLED", False),
            failure_threshold=cb_config.get("FAILURE_THRESHOLD", 5),
            recovery_timeout=cb_config.get("RECOVERY_TIMEOUT", 60),
            success_threshold=cb_config.get("SUCCESS_THRESHOLD", 2),
            manual_override_ttl_minutes=governance.get("MANUAL_OVERRIDE_TTL_MINUTES", 90),
            half_open_request_limit=governance.get("HALF_OPEN_REQUEST_LIMIT", 10),
            max_pending_duration_hours=governance.get("MAX_PENDING_DURATION_HOURS", 4),
            max_retry_lifetime_hours=governance.get("MAX_RETRY_LIFETIME_HOURS", 24),
        )


# =============================================================================
# DLQ Configuration
# =============================================================================


@dataclass(frozen=True)
class DLQSettings:
    """
    Dead Letter Queue configuration.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §1 (Dead Letter Queue)
    """

    enabled: bool = True
    retention_days: int = 30
    max_replay_attempts: int = 2

    @classmethod
    def from_settings(cls) -> "DLQSettings":
        """Load DLQ configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        dlq_config = self_healing.get("DLQ", {})

        return cls(
            enabled=dlq_config.get("ENABLED", True),
            retention_days=dlq_config.get("RETENTION_DAYS", 30),
            max_replay_attempts=dlq_config.get("MAX_REPLAY_ATTEMPTS", 2),
        )


# =============================================================================
# Forensic Context Configuration
# =============================================================================


@dataclass(frozen=True)
class ForensicSettings:
    """
    Forensic context truncation limits.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §6 (Forensic Context)
    """

    error_message_max_length: int = 500
    response_body_max_length: int = 5000
    user_agent_max_length: int = 500

    @classmethod
    def from_settings(cls) -> "ForensicSettings":
        """Load forensic configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        forensic_config = self_healing.get("FORENSIC", {})

        return cls(
            error_message_max_length=forensic_config.get("ERROR_MESSAGE_MAX_LENGTH", 500),
            response_body_max_length=forensic_config.get("RESPONSE_BODY_MAX_LENGTH", 5000),
            user_agent_max_length=forensic_config.get("USER_AGENT_MAX_LENGTH", 500),
        )


# =============================================================================
# Slack Channel Configuration
# =============================================================================


@dataclass(frozen=True)
class SlackChannels:
    """
    Slack channel configuration for alerts.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §4 (Escalation & Notifications)
    """

    critical_channel: str = "#critical-alerts"
    high_channel: str = "#ops-alerts"
    medium_channel: str = "#dev-alerts"

    @classmethod
    def from_settings(cls) -> "SlackChannels":
        """Load Slack channel configuration from Django settings."""
        self_healing = getattr(settings, "SELF_HEALING", {})
        notifications = self_healing.get("NOTIFICATIONS", {})

        return cls(
            critical_channel=notifications.get("CRITICAL_CHANNEL", "#critical-alerts"),
            high_channel=notifications.get("HIGH_CHANNEL", "#ops-alerts"),
            medium_channel=notifications.get("MEDIUM_CHANNEL", "#dev-alerts"),
        )


# =============================================================================
# Self-Healing Master Configuration
# =============================================================================


@dataclass
class SelfHealingConfig:
    """
    Master configuration class that aggregates all self-healing settings.

    Usage:
        config = SelfHealingConfig.load()
        sla_threshold = config.sla.get_threshold("payment")
    """

    sla: SLAThresholds = field(default_factory=SLAThresholds)
    idempotency: IdempotencyConfig = field(default_factory=IdempotencyConfig)
    security: SecurityThresholds = field(default_factory=SecurityThresholds)
    notification_limits: NotificationLimits = field(default_factory=NotificationLimits)
    slack_channels: SlackChannels = field(default_factory=SlackChannels)
    retry: RetrySettings = field(default_factory=RetrySettings)
    circuit_breaker: CircuitBreakerSettings = field(default_factory=CircuitBreakerSettings)
    dlq: DLQSettings = field(default_factory=DLQSettings)
    forensic: ForensicSettings = field(default_factory=ForensicSettings)

    @classmethod
    def load(cls) -> "SelfHealingConfig":
        """
        Load all configuration from Django settings.

        Returns:
            SelfHealingConfig with all sub-configurations loaded
        """
        return cls(
            sla=SLAThresholds.from_settings(),
            idempotency=IdempotencyConfig.from_settings(),
            security=SecurityThresholds.from_settings(),
            notification_limits=NotificationLimits.from_settings(),
            slack_channels=SlackChannels.from_settings(),
            retry=RetrySettings.from_settings(),
            circuit_breaker=CircuitBreakerSettings.from_settings(),
            dlq=DLQSettings.from_settings(),
            forensic=ForensicSettings.from_settings(),
        )


# =============================================================================
# Module-level cached instances
# =============================================================================

_config_cache: SelfHealingConfig | None = None


def get_config() -> SelfHealingConfig:
    """
    Get the cached configuration instance.

    Returns:
        SelfHealingConfig instance (cached)
    """
    global _config_cache
    if _config_cache is None:
        _config_cache = SelfHealingConfig.load()
    return _config_cache


def reload_config() -> SelfHealingConfig:
    """
    Force reload of configuration from settings.

    Returns:
        Fresh SelfHealingConfig instance
    """
    global _config_cache
    _config_cache = SelfHealingConfig.load()
    return _config_cache


def get_sla_thresholds() -> SLAThresholds:
    """Get SLA thresholds configuration."""
    return get_config().sla


def get_idempotency_config() -> IdempotencyConfig:
    """Get idempotency configuration."""
    return get_config().idempotency


def get_security_thresholds() -> SecurityThresholds:
    """Get security thresholds configuration."""
    return get_config().security


def get_notification_limits() -> NotificationLimits:
    """Get notification limits configuration."""
    return get_config().notification_limits


def get_slack_channels() -> SlackChannels:
    """Get Slack channel configuration."""
    return get_config().slack_channels


def get_retry_settings() -> RetrySettings:
    """Get retry configuration."""
    return get_config().retry


def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    """Get circuit breaker configuration."""
    return get_config().circuit_breaker


def get_dlq_settings() -> DLQSettings:
    """Get DLQ configuration."""
    return get_config().dlq


def get_forensic_settings() -> ForensicSettings:
    """Get forensic context configuration."""
    return get_config().forensic
