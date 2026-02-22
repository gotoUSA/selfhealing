"""
Unified Notification Manager

Central notification management for the Self-Healing system.
Consolidates all notification logic into a single point of control,
solving the problem of fragmented notification sources.

Architecture Problem Solved:
- Before: 4 scattered notification sources (AlertAdapter, SecurityNotificationService,
          GateAlertManager, GovernanceService._send_notification)
- After: Single UnifiedNotificationManager that routes all notifications

Key Features:
- Centralized notification routing
- Policy-based channel selection
- Rate limiting and cooldown
- Audit trail integration
- Emergency level escalation

중앙화된 알림 라우팅 및 관리를 제공합니다.

Reference:
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 3 [15] NotificationChannelSettings 참조.
"""

from __future__ import annotations

# Convenience functions
from .convenience import (
    notify,
    notify_error,
    notify_security,
    notify_sla,
)

# Formatters
from .formatters import (
    format_cb_notification_with_actions,
    format_cb_slack_blocks,
    format_sla_slack_blocks,
)

# === Explicit re-exports ===
# Models
from .models import (
    NotificationCategory,
    NotificationPayload,
    NotificationPriority,
    NotificationResult,
)

# Routing
from .routing import (
    RoutingPolicy,
    _get_notification_channel_settings,
)

# Service
from .service import (
    UnifiedNotificationManager,
    _manager,
    get_notification_service,
    get_unified_notification_manager,
    logger,
    reset_notification_manager,
)

__all__ = [
    # models
    "NotificationPriority",
    "NotificationCategory",
    "NotificationPayload",
    "NotificationResult",
    # routing
    "RoutingPolicy",
    "_get_notification_channel_settings",
    # service
    "UnifiedNotificationManager",
    "get_unified_notification_manager",
    "reset_notification_manager",
    "get_notification_service",
    "_manager",
    "logger",
    # convenience
    "notify",
    "notify_security",
    "notify_sla",
    "notify_error",
    # formatters
    "format_cb_slack_blocks",
    "format_sla_slack_blocks",
    "format_cb_notification_with_actions",
]


# === Dynamic forwarding for test patch compatibility ===
# Ensures `selfhealing.services.unified_notification.X` resolves to the actual
# object in whichever sub-module defines it, so mock.patch targets keep working.

import importlib as _importlib  # noqa: E402
import types as _types  # noqa: E402

_SUB_MODULES = ("models", "routing", "service", "convenience", "formatters")


def __getattr__(name: str):
    """Dynamic attribute forwarding from all sub-modules."""
    for _sub in _SUB_MODULES:
        _mod = _importlib.import_module(f".{_sub}", __name__)
        try:
            _val = getattr(_mod, name)
            # Cache on package for future access
            setattr(_types.ModuleType(__name__), name, _val)
            globals()[name] = _val
            return _val
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
