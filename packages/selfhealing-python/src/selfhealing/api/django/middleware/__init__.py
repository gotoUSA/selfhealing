"""
Self-Healing API Middleware Package.

Provides middleware components for security, logging, and performance.

Features:
- HealthBridgeMiddleware: DB-independent health endpoints (Worker Saturation 방지)
- SensitiveEndpointAccessLogger: Logs access to sensitive endpoints
- SelfHealingMiddleware: Automatic failure detection and DLQ storage
- SelfHealingRecoveryLogger: Recovery event chain logging
- Fail-Secure Permission Classes
"""

from __future__ import annotations

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
# Health Bridge
# ============================================================
from selfhealing.api.django.middleware.health_bridge import HealthBridgeMiddleware

# ============================================================
# Fail-Secure Permissions
# ============================================================
from selfhealing.api.django.middleware.permissions import (
    FailSecureIsAdminUser,
    FailSecureIsAuthenticated,
)

# ============================================================
# Recovery Logger
# ============================================================
from selfhealing.api.django.middleware.recovery_logger import (
    SelfHealingRecoveryLogger,
    get_recovery_logger,
)

# ============================================================
# Self-Healing
# ============================================================
from selfhealing.api.django.middleware.self_healing import SelfHealingMiddleware

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
