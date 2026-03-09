"""
Self-Healing Reliability Layer for Python Applications.

Public API:
    from selfhealing import ProviderRegistry
    from selfhealing import get_circuit_breaker_service
    from selfhealing import CircuitState
    from selfhealing import FailedOperationData
    from selfhealing import ReplayService, ReplayRequest
    from selfhealing import SelfHealingError, AdapterNotFoundError
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

# === Core Types (Eager — lightweight, no heavy deps) ===
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
)
from selfhealing.interfaces.repositories import (
    FailedOperationData as FailedOperationData,
)

# =========================================================================
# Lazy Import (PEP 562) — Heavy objects are loaded on first access.
#
# Applies the same verified pattern from adapters/__init__.py.
# Prevents chain-loading of ProviderRegistry → structlog → settings
# when Consumer only needs CircuitState Enum.
# =========================================================================
if TYPE_CHECKING:
    from selfhealing.core.exceptions import (
        AdapterNotFoundError as AdapterNotFoundError,
    )
    from selfhealing.core.exceptions import (
        CircuitBreakerError as CircuitBreakerError,
    )
    from selfhealing.core.exceptions import (
        ConfigurationError as ConfigurationError,
    )
    from selfhealing.core.exceptions import (
        DLQReplayError as DLQReplayError,
    )
    from selfhealing.core.exceptions import (
        RetryExhaustedError as RetryExhaustedError,
    )
    from selfhealing.core.exceptions import (
        SelfHealingError as SelfHealingError,
    )
    from selfhealing.factory import ProviderRegistry as ProviderRegistry
    from selfhealing.services import (
        get_circuit_breaker_service as get_circuit_breaker_service,
    )
    from selfhealing.services.replay_service import (
        ReplayRequest as ReplayRequest,
    )
    from selfhealing.services.replay_service import (
        ReplayService as ReplayService,
    )

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Service Access
    "ProviderRegistry": ("selfhealing.factory", "ProviderRegistry"),
    "get_circuit_breaker_service": (
        "selfhealing.services",
        "get_circuit_breaker_service",
    ),
    # Replay
    "ReplayService": ("selfhealing.services.replay_service", "ReplayService"),
    "ReplayRequest": ("selfhealing.services.replay_service", "ReplayRequest"),
    # Exceptions
    "SelfHealingError": ("selfhealing.core.exceptions", "SelfHealingError"),
    "AdapterNotFoundError": ("selfhealing.core.exceptions", "AdapterNotFoundError"),
    "CircuitBreakerError": ("selfhealing.core.exceptions", "CircuitBreakerError"),
    "RetryExhaustedError": ("selfhealing.core.exceptions", "RetryExhaustedError"),
    "DLQReplayError": ("selfhealing.core.exceptions", "DLQReplayError"),
    "ConfigurationError": ("selfhealing.core.exceptions", "ConfigurationError"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    # Core Types
    "CircuitState",
    "FailedOperationData",
    # Service Access
    "ProviderRegistry",
    "get_circuit_breaker_service",
    # Replay
    "ReplayService",
    "ReplayRequest",
    # Exceptions
    "SelfHealingError",
    "AdapterNotFoundError",
    "CircuitBreakerError",
    "RetryExhaustedError",
    "DLQReplayError",
    "ConfigurationError",
]
