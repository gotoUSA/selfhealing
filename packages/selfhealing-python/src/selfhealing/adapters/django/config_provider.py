"""
Django Configuration Provider

Provides a ConfigProvider implementation that reads from Django settings.
This keeps Django-specific code isolated in the adapters layer.

Usage:
    from selfhealing.adapters.django.config_provider import DjangoConfigProvider
    from selfhealing.services.config import set_config_provider

    set_config_provider(DjangoConfigProvider())
"""

from __future__ import annotations

from typing import Any

from selfhealing.interfaces.config_provider import ConfigProviderInterface


class DjangoConfigProvider(ConfigProviderInterface):
    """
    Configuration provider that reads from Django settings.

    Maps selfhealing configuration keys to Django settings.
    """

    def __init__(self):
        # Lazy import to avoid AppRegistryNotReady
        pass

    def _get_settings(self):
        """Get Django settings (lazy import)."""
        from django.conf import settings

        return settings

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value using dot notation.

        Args:
            key: Configuration key (e.g., "SELF_HEALING.SLA.DEFAULT_HOURS")
            default: Default value if not found
        """
        keys = key.split(".")
        return self.get_nested(*keys, default=default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """
        Get a nested configuration value.

        Args:
            keys: Path to configuration value
            default: Default value if not found
        """
        if not keys:
            return default

        settings = self._get_settings()

        # Start from Django settings
        value = getattr(settings, keys[0], None)

        if value is None:
            return default

        # Navigate through nested keys
        for key in keys[1:]:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default

        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """
        Get an entire configuration section as a dictionary.

        Args:
            section: Section name (e.g., "SELF_HEALING")
        """
        settings = self._get_settings()
        value = getattr(settings, section, {})
        return value if isinstance(value, dict) else {}


def _build_circuit_breaker_config(cb: dict) -> dict:
    """Build circuit breaker configuration from Django settings."""
    return {
        "enabled": cb.get("ENABLED", True),
        "failure_threshold": cb.get("FAILURE_THRESHOLD", 5),
        "recovery_timeout": cb.get("RECOVERY_TIMEOUT", 60),
        "success_threshold": cb.get("SUCCESS_THRESHOLD", 2),
        "rate_limit_cascade_threshold": cb.get("RATE_LIMIT_CASCADE_THRESHOLD", 10),
        "rate_limit_cascade_window_seconds": cb.get("RATE_LIMIT_CASCADE_WINDOW_SECONDS", 60),
        "self_ddos_protection_enabled": cb.get("SELF_DDOS_PROTECTION_ENABLED", True),
        "self_ddos_request_threshold": cb.get("SELF_DDOS_REQUEST_THRESHOLD", 100),
        "self_ddos_window_seconds": cb.get("SELF_DDOS_WINDOW_SECONDS", 10),
        "self_ddos_backoff_multiplier": cb.get("SELF_DDOS_BACKOFF_MULTIPLIER", 2.0),
    }


def _build_dlq_config(dlq: dict) -> dict:
    """Build DLQ configuration from Django settings."""
    return {
        "enabled": dlq.get("ENABLED", True),
        "max_retries": dlq.get("MAX_RETRIES", 3),
        "retry_delay": dlq.get("RETRY_DELAY", 60),
        "retention_days": dlq.get("RETENTION_DAYS", 30),
        "max_replay_attempts": dlq.get("MAX_REPLAY_ATTEMPTS", 2),
    }


def _build_retry_config(retry: dict) -> dict:
    """Build retry configuration from Django settings."""
    return {
        "max_attempts": retry.get("MAX_ATTEMPTS", 3),
        "backoff_base": retry.get("BACKOFF_BASE", 4),
        "max_delay": retry.get("BACKOFF_MAX", 300),
        "jitter_percent": retry.get("JITTER_PERCENT", 25),
        "min_delay": retry.get("MIN_DELAY", 1),
    }


def _build_sla_config(sla: dict) -> dict:
    """Build SLA configuration from Django settings."""
    thresholds = {}
    threshold_keys = [
        ("PAYMENT_HOURS", "payment"),
        ("POINT_HOURS", "point"),
        ("INVENTORY_HOURS", "inventory"),
        ("WEBHOOK_HOURS", "webhook"),
        ("NOTIFICATION_HOURS", "notification"),
    ]
    for key, domain in threshold_keys:
        if key in sla:
            thresholds[domain] = sla[key]
    thresholds.update(sla.get("THRESHOLDS_BY_DOMAIN", {}))
    return {
        "default_hours": sla.get("DEFAULT_HOURS", 24),
        "thresholds_by_domain": thresholds,
    }


def _build_idempotency_config(idemp: dict) -> dict:
    """Build idempotency configuration from Django settings."""
    cache_ttls = {}
    if "PAYMENT_CACHE_TTL" in idemp:
        cache_ttls["payment"] = idemp["PAYMENT_CACHE_TTL"]
    if "WEBHOOK_CACHE_TTL" in idemp:
        cache_ttls["webhook"] = idemp["WEBHOOK_CACHE_TTL"]
    cache_ttls.update(idemp.get("CACHE_TTL_BY_DOMAIN", {}))
    return {
        "default_cache_ttl": idemp.get("DEFAULT_CACHE_TTL", 60),
        "extended_cache_ttl": idemp.get("EXTENDED_CACHE_TTL", 300),
        "short_cache_ttl": idemp.get("SHORT_CACHE_TTL", 60),
    }


def _build_security_config(sec: dict) -> dict:
    """Build security configuration from Django settings."""
    return {
        "rate_limit_window_seconds": sec.get("RATE_LIMIT_WINDOW", 60),
        "rate_limit_max_requests": sec.get("RATE_LIMIT_MAX", 100),
        "temporary_ban_hours": sec.get("TEMP_BAN_HOURS", 1),
        "permanent_ban_threshold": sec.get("PERM_BAN_THRESHOLD", 5),
        "suspicious_ip_cache_timeout": sec.get("SUSPICIOUS_IP_CACHE_TIMEOUT", 86400),
        "injection_ban_hours": sec.get("INJECTION_BAN_HOURS", 24),
        "failed_login_threshold": sec.get("FAILED_LOGIN_THRESHOLD", 5),
    }


def _build_forensic_config(forensic: dict) -> dict:
    """Build forensic configuration from Django settings."""
    return {
        "error_message_max_length": forensic.get("ERROR_MESSAGE_MAX_LENGTH", 500),
        "response_body_max_length": forensic.get("RESPONSE_BODY_MAX_LENGTH", 5000),
        "user_agent_max_length": forensic.get("USER_AGENT_MAX_LENGTH", 500),
    }


def _build_notification_config(notif: dict) -> dict:
    """Build notification configuration from Django settings."""
    limits = notif.get("LIMITS", {})
    return {
        "critical_channel": notif.get("CRITICAL_CHANNEL", "#critical-alerts"),
        "high_channel": notif.get("HIGH_CHANNEL", "#ops-alerts"),
        "medium_channel": notif.get("MEDIUM_CHANNEL", "#dev-alerts"),
        "slack_block_text_limit": limits.get("SLACK_BLOCK_TEXT_LIMIT", 3000),
        "description_max_length": limits.get("DESCRIPTION_MAX_LENGTH", 500),
        "action_taken_max_length": limits.get("ACTION_TAKEN_MAX_LENGTH", 200),
        "title_max_length": limits.get("TITLE_MAX_LENGTH", 150),
        "notification_timeout_seconds": limits.get("TIMEOUT_SECONDS", 10),
    }


def _build_rate_limit_config(rl: dict) -> dict:
    """Build rate limit configuration from Django settings."""
    return {
        "base_delay": rl.get("BASE_DELAY", 1.0),
        "max_delay": rl.get("MAX_DELAY", 60.0),
        "jitter_percent": rl.get("JITTER_PERCENT", 30.0),
        "default_retry_after": rl.get("DEFAULT_RETRY_AFTER", 5.0),
        "backoff_multiplier": rl.get("BACKOFF_MULTIPLIER", 2.0),
    }


# Mapping of Django settings keys to config builder functions
_CONFIG_BUILDERS: dict[str, tuple[str, callable]] = {
    "CIRCUIT_BREAKER": ("circuit_breaker", _build_circuit_breaker_config),
    "DLQ": ("dlq", _build_dlq_config),
    "RETRY": ("retry", _build_retry_config),
    "SLA": ("sla", _build_sla_config),
    "IDEMPOTENCY": ("idempotency", _build_idempotency_config),
    "SECURITY": ("security", _build_security_config),
    "FORENSIC": ("forensic", _build_forensic_config),
    "NOTIFICATIONS": ("notification", _build_notification_config),
    "RATE_LIMIT": ("rate_limit", _build_rate_limit_config),
}


def configure_selfhealing_from_django() -> None:
    """
    Configure the selfhealing system using Django settings.

    Call this during Django app initialization (e.g., in AppConfig.ready()).

    Usage in apps.py:
        class MyAppConfig(AppConfig):
            def ready(self):
                from selfhealing.adapters.django.config_provider import configure_selfhealing_from_django
                configure_selfhealing_from_django()
    """
    from selfhealing.core.config import set_config
    from selfhealing.core.config import SelfHealingConfig

    # Import Django settings
    from django.conf import settings

    # Build config from Django settings
    sh_settings = getattr(settings, "SELF_HEALING", {})

    config_dict = {}

    # Apply all config builders
    for django_key, (config_key, builder) in _CONFIG_BUILDERS.items():
        if django_key in sh_settings:
            config_dict[config_key] = builder(sh_settings[django_key])

    # Additional top-level settings
    config_dict["auto_replay_enabled"] = sh_settings.get("AUTO_REPLAY_ENABLED", True)
    config_dict["security_monitoring_enabled"] = sh_settings.get("SECURITY_MONITORING_ENABLED", True)
    config_dict["debug_mode"] = sh_settings.get("DEBUG_MODE", False)

    # Create and set configuration
    config = SelfHealingConfig.from_dict(config_dict)
    set_config(config)
