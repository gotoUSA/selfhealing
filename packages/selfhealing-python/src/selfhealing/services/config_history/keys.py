"""
Configuration History - Redis Key Helpers & Legacy Constants.

Redis Key Helpers (Multi-Cluster Support)
Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

from selfhealing.settings.audit_settings import get_audit_settings


# =============================================================================
# Redis Key Helpers (Multi-Cluster Support)
# Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
# =============================================================================


def _get_key_prefix() -> str:
    """
    Get namespace-aware key prefix.

    Returns:
        Key prefix like "selfhealing:seoul:" or "selfhealing:"
    """
    from selfhealing.settings.namespace import get_namespace_settings

    return get_namespace_settings().get_key_prefix()


def _get_config_history_key(config_type: str) -> str:
    """Get config history key with namespace support."""
    return f"{_get_key_prefix()}config:history:{config_type}"


def _get_config_version_key(config_type: str) -> str:
    """Get config version counter key with namespace support."""
    return f"{_get_key_prefix()}config:version:{config_type}"


def _get_config_current_key(config_type: str) -> str:
    """Get current config key with namespace support."""
    return f"{_get_key_prefix()}config:current:{config_type}"


# Legacy constants (for backward compatibility with imports)
# These still work but use the dynamic functions internally
CONFIG_HISTORY_KEY = "selfhealing:config:history:{config_type}"
CONFIG_VERSION_COUNTER_KEY = "selfhealing:config:version:{config_type}"
CONFIG_CURRENT_KEY = "selfhealing:config:current:{config_type}"


def _get_max_history_entries() -> int:
    """Get max history entries from AuditSettings."""
    return get_audit_settings().config_history_entries


# Legacy constant for backward compatibility
MAX_HISTORY_ENTRIES = 50  # Deprecated: use _get_max_history_entries() instead
