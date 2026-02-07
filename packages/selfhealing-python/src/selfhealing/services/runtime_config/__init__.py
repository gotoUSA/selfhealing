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

This package has been refactored from a single 1,764-line file into:
- constants.py: Storage keys and config class mappings
- base.py: BaseConfigManager with core infrastructure
- strategy.py: Apply strategy handling (IMMEDIATE/DELAYED/GRACEFUL)
- core_configs.py: Core configuration mixins (CB, DLQ, Retry, SLA, etc.)
- advanced_configs.py: Advanced configuration mixins (SLO, Governance, etc.)
- chaos_storage.py: Chaos Engineering and L2 Storage mixins
- approval.py: 4-Eyes approval workflow mixin

All exports are maintained for backward compatibility.
"""

from __future__ import annotations

import threading

from .advanced_configs import AdvancedConfigMixin
from .approval import ApprovalMixin
from .base import BaseConfigManager
from .chaos_storage import ChaosStorageMixin

# Re-export constants for convenience
from .constants import CONFIG_CLASSES, DEFAULT_SLO_CONFIG, STORAGE_KEYS
from .core_configs import CoreConfigMixin
from .strategy import StrategyMixin

# Singleton instance
_runtime_config_manager: RuntimeConfigManager | None = None
_manager_lock = threading.Lock()


class RuntimeConfigManager(
    BaseConfigManager,
    StrategyMixin,
    CoreConfigMixin,
    AdvancedConfigMixin,
    ChaosStorageMixin,
    ApprovalMixin,
):
    """
    Runtime Configuration Manager.

    Thread-safe singleton that manages runtime configuration
    with persistent storage via StateBackend.

    Features:
    - Get/Update all config types
    - Persistent storage (survives restarts)
    - Thread-safe operations
    - Audit logging
    - Apply strategies (IMMEDIATE, DELAYED, GRACEFUL)
    - 4-Eyes approval workflow
    """

    pass  # All functionality provided by mixins


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


__all__ = [
    # Main class
    "RuntimeConfigManager",
    # Singleton functions
    "get_runtime_config_manager",
    "reset_runtime_config_manager",
    # Constants
    "STORAGE_KEYS",
    "CONFIG_CLASSES",
    "DEFAULT_SLO_CONFIG",
]
