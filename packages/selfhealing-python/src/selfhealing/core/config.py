"""
Configuration management for the self-healing system.

NOTE: This module re-exports from selfhealing.settings for backward compatibility.
New code should use selfhealing.settings directly.

Data models like ApprovalRequest remain here as they are not configuration.

Reference: docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List

# Re-export from settings for backward compatibility
from selfhealing.settings import (
    SelfHealingSettings as SelfHealingConfig,
    CircuitBreakerSettings as CircuitBreakerConfig,
    CircuitBreakerAdvancedSettings as CircuitBreakerAdvancedConfig,
    DLQSettings as DLQConfig,
    RetrySettings as RetryConfig,
    SLASettings as SLAConfig,
    RateLimitSettings as RateLimitConfig,
    SecuritySettings as SecurityConfig,
    IdempotencySettings as IdempotencyConfig,
    ForensicSettings as ForensicConfig,
    MetricsSettings as MetricsConfig,
    NotificationSettings as NotificationConfig,
    GovernanceSettings as GovernanceConfig,
    ErrorBudgetSettings as ErrorBudgetConfig,
    ChaosSettings as ChaosConfig,
    DriftThresholdSettings as DriftThresholdConfig,
    L2StorageSettings as L2StorageConfig,
    LoggingSettings as LoggingConfig,
    ReplayAutomationSettings as ReplayAutomationConfig,
    get_config,
    set_config,
    reset_config,
    reload_config,
    configure,
    get_sla_thresholds,
    get_security_thresholds,
    get_forensic_settings,
    get_notification_settings,
    get_dlq_settings,
    get_retry_settings,
    get_circuit_breaker_config as get_circuit_breaker_settings,
    get_circuit_breaker_advanced_settings,
)


# =============================================================================
# Data Models (not configuration, so kept here)
# =============================================================================

@dataclass
class ApprovalRequest:
    """
    4-Eyes Approval Request (듀얼 승인 요청).

    Admin A가 요청 → Admin B가 승인/거부하는 워크플로우.
    금융권 컴플라이언스 요구사항 충족.

    Workflow:
        1. Admin A: 요청 생성 (PENDING)
        2. Admin B: 알림 수신
        3. Admin B: 24시간 내 APPROVED/REJECTED
        4. 만료 시: EXPIRED

    Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md Phase 3
    - PCI-DSS Dual Control Requirements
    """

    id: str = ""
    request_type: str = ""  # config_change, mode_change, emergency_action
    description: str = ""

    # 요청자
    requested_by: str = ""
    requested_at: str = ""  # ISO format

    # 승인자
    approved_by: str = ""
    approved_at: str = ""  # ISO format

    # 상태: PENDING, APPROVED, REJECTED, EXPIRED
    status: str = "PENDING"

    # 요청 데이터
    payload: Dict[str, Any] = field(default_factory=dict)

    # 만료 시간 (기본 24시간)
    expires_at: str = ""  # ISO format


# =============================================================================
# Legacy Aliases for external code that imports specific types
# =============================================================================

# For code that still uses from selfhealing.core.config import SLAThresholds
SLAThresholds = SLAConfig
SecurityThresholds = SecurityConfig
NotificationLimits = NotificationConfig


__all__ = [
    # Settings classes (re-exported)
    "SelfHealingConfig",
    "CircuitBreakerConfig",
    "CircuitBreakerAdvancedConfig",
    "DLQConfig",
    "RetryConfig",
    "SLAConfig",
    "RateLimitConfig",
    "SecurityConfig",
    "IdempotencyConfig",
    "ForensicConfig",
    "MetricsConfig",
    "NotificationConfig",
    "GovernanceConfig",
    "ErrorBudgetConfig",
    "ChaosConfig",
    "DriftThresholdConfig",
    "L2StorageConfig",
    "LoggingConfig",
    "ReplayAutomationConfig",
    # Functions
    "get_config",
    "set_config",
    "reset_config",
    "reload_config",
    "configure",
    "get_sla_thresholds",
    "get_security_thresholds",
    "get_forensic_settings",
    "get_notification_settings",
    "get_dlq_settings",
    "get_retry_settings",
    "get_circuit_breaker_settings",
    "get_circuit_breaker_advanced_settings",
    # Data models
    "ApprovalRequest",
    # Legacy aliases
    "SLAThresholds",
    "SecurityThresholds",
    "NotificationLimits",
]
