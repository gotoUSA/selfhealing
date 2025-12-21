"""
Self-Healing Configuration Module

Provides configuration access for the selfhealing package.
This module provides compatibility layer that re-exports from
the appropriate configuration source.

For Django applications, configuration is loaded from Django settings.
For other frameworks, uses environment variables or defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


# =============================================================================
# Configuration Data Classes
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


@dataclass(frozen=True)
class ForensicSettings:
    """
    Forensic context configuration.

    Reference: docs/L3_SELF_HEALING_OPERATIONS.md §6 (Forensic Context)
    """

    # Stack trace limits
    max_stack_frames: int = 50
    max_stacktrace_length: int = 10000

    # Context size limits
    max_context_size_bytes: int = 65536  # 64KB

    # Data collection settings
    collect_request_body: bool = False
    collect_response_body: bool = False

    # Sensitive field masking
    mask_sensitive_fields: bool = True
    sensitive_field_patterns: tuple[str, ...] = (
        # Authentication & Secrets
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "private_key",
        "access_key",
        "secret_key",
        # Payment related
        "card_number",
        "cvv",
        "cvc",
        "credit_card",
        # Internal infrastructure (should not be exposed in logs)
        "internal_ip",
        "server_path",
        "db_password",
        "redis_password",
        "connection_string",
    )

    # IP address masking patterns (regex)
    # Private IP ranges that should be masked in logs
    mask_internal_ip: bool = True
    internal_ip_patterns: tuple[str, ...] = (
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}",       # 10.0.0.0/8
        r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}",  # 172.16.0.0/12
        r"192\.168\.\d{1,3}\.\d{1,3}",          # 192.168.0.0/16
    )

    # Server path patterns to mask
    mask_server_paths: bool = True
    server_path_patterns: tuple[str, ...] = (
        r"/home/[^/]+",                         # Home directories
        r"/var/[^/]+/[^/]+",                    # Var subdirectories
        r"/etc/[^/]+",                          # Config files
        r"[A-Z]:\\Users\\[^\\]+",               # Windows user paths
        r"/app/[^/]+/[^/]+",                    # Container app paths
    )


# =============================================================================
# Configuration Loading Functions
# =============================================================================


def _try_django_config():
    """Try to load config from Django settings."""
    # NOTE: For projects integrating with Django, configuration should be
    # loaded from Django settings directly. The selfhealing package uses
    # environment variables or defaults for standalone deployments.
    return None, None


@lru_cache(maxsize=1)
def get_notification_limits() -> NotificationLimits:
    """
    Get notification limits configuration.

    Tries Django settings first, falls back to environment/defaults.
    """
    django_getter, _ = _try_django_config()
    if django_getter:
        return django_getter()

    # Fall back to environment variables or defaults
    return NotificationLimits(
        slack_block_text_limit=int(os.environ.get("SELFHEALING_SLACK_BLOCK_TEXT_LIMIT", 3000)),
        description_max_length=int(os.environ.get("SELFHEALING_DESCRIPTION_MAX_LENGTH", 500)),
        action_taken_max_length=int(os.environ.get("SELFHEALING_ACTION_TAKEN_MAX_LENGTH", 200)),
        title_max_length=int(os.environ.get("SELFHEALING_TITLE_MAX_LENGTH", 150)),
        notification_timeout_seconds=int(os.environ.get("SELFHEALING_NOTIFICATION_TIMEOUT", 10)),
    )


@lru_cache(maxsize=1)
def get_forensic_settings() -> ForensicSettings:
    """
    Get forensic context configuration.

    Tries Django settings first, falls back to environment/defaults.
    """
    _, django_getter = _try_django_config()
    if django_getter:
        return django_getter()

    # Fall back to environment variables or defaults
    return ForensicSettings(
        max_stack_frames=int(os.environ.get("SELFHEALING_MAX_STACK_FRAMES", 50)),
        max_stacktrace_length=int(os.environ.get("SELFHEALING_MAX_STACKTRACE_LENGTH", 10000)),
        max_context_size_bytes=int(os.environ.get("SELFHEALING_MAX_CONTEXT_SIZE", 65536)),
        collect_request_body=os.environ.get("SELFHEALING_COLLECT_REQUEST_BODY", "false").lower() == "true",
        collect_response_body=os.environ.get("SELFHEALING_COLLECT_RESPONSE_BODY", "false").lower() == "true",
        mask_sensitive_fields=os.environ.get("SELFHEALING_MASK_SENSITIVE_FIELDS", "true").lower() == "true",
    )


# =============================================================================
# Convenience exports
# =============================================================================


__all__ = [
    "NotificationLimits",
    "ForensicSettings",
    "get_notification_limits",
    "get_forensic_settings",
]
