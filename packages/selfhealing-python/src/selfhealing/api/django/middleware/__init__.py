"""
Self-Healing API Middleware Package.

Provides middleware components for security, logging, and performance.

Features:
- HealthBridgeMiddleware: DB-independent health endpoints (Worker Saturation 방지)
- SensitiveEndpointAccessLogger: Logs access to sensitive endpoints
- SelfHealingMiddleware: Automatic failure detection and DLQ storage
- SelfHealingRecoveryLogger: Recovery event chain logging
- Fail-Secure Permission Classes

Reference: docs/self_healing/07_CONTROL_API.md
"""

from __future__ import annotations

# ============================================================
# Health Bridge
# ============================================================
from selfhealing.api.django.middleware.health_bridge import HealthBridgeMiddleware

# ============================================================
# Access Logging
# ============================================================
from selfhealing.api.django.middleware.access_logging import (
    SENSITIVE_ENDPOINT_PATTERNS,
    AccessLogEntry,
    SensitiveAccessLoggingMiddleware,
    SensitiveEndpointAccessLogger,
)

# ============================================================
# Fail-Secure Permissions
# ============================================================
from selfhealing.api.django.middleware.permissions import (
    FailSecureIsAdminUser,
    FailSecureIsAuthenticated,
)

# ============================================================
# Self-Healing
# ============================================================
from selfhealing.api.django.middleware.self_healing import SelfHealingMiddleware

# ============================================================
# Recovery Logger
# ============================================================
from selfhealing.api.django.middleware.recovery_logger import (
    SelfHealingRecoveryLogger,
    get_recovery_logger,
)

# ============================================================
# Public API
# ============================================================
__all__ = [
    # Health Bridge
    "HealthBridgeMiddleware",
    # Access Logging
    "SensitiveEndpointAccessLogger",
    "SensitiveAccessLoggingMiddleware",
    "AccessLogEntry",
    "SENSITIVE_ENDPOINT_PATTERNS",
    # Self-Healing
    "SelfHealingMiddleware",
    "SelfHealingRecoveryLogger",
    "get_recovery_logger",
    # Fail-Secure Permissions
    "FailSecureIsAuthenticated",
    "FailSecureIsAdminUser",
]
