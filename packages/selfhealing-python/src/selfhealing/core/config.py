"""
Configuration management for the self-healing system.

NOTE: This module re-exports from selfhealing.settings for backward compatibility.
New code should use selfhealing.settings directly.

Data models like ApprovalRequest remain here as they are not configuration.
"""

from dataclasses import dataclass, field
from typing import Any

# Re-export from settings for backward compatibility
from selfhealing.settings import ChaosSettings as ChaosConfig
from selfhealing.settings import (
    CircuitBreakerAdvancedSettings as CircuitBreakerAdvancedConfig,
)
from selfhealing.settings import CircuitBreakerSettings as CircuitBreakerConfig
from selfhealing.settings import DLQSettings as DLQConfig
from selfhealing.settings import DriftThresholdSettings as DriftThresholdConfig
from selfhealing.settings import ErrorBudgetSettings as ErrorBudgetConfig
from selfhealing.settings import ForensicSettings as ForensicConfig
from selfhealing.settings import GovernanceSettings as GovernanceConfig
from selfhealing.settings import IdempotencySettings as IdempotencyConfig
from selfhealing.settings import L2StorageSettings as L2StorageConfig
from selfhealing.settings import LoggingSettings as LoggingConfig
from selfhealing.settings import MetricsSettings as MetricsConfig
from selfhealing.settings import NotificationSettings as NotificationConfig
from selfhealing.settings import RateLimitSettings as RateLimitConfig
from selfhealing.settings import ReplayAutomationSettings as ReplayAutomationConfig
from selfhealing.settings import RetrySettings as RetryConfig
from selfhealing.settings import SecuritySettings as SecurityConfig
from selfhealing.settings import SelfHealingSettings as SelfHealingConfig
from selfhealing.settings import SLASettings as SLAConfig
from selfhealing.settings import (
    configure,
    get_circuit_breaker_advanced_settings,
    get_config,
    get_dlq_settings,
    get_forensic_settings,
    get_notification_settings,
    get_retry_settings,
    get_security_thresholds,
    get_sla_thresholds,
    reload_config,
    reset_config,
    set_config,
)
from selfhealing.settings import (
    get_circuit_breaker_config as get_circuit_breaker_settings,
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

    PCI-DSS Dual Control Requirements 준수.
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
    payload: dict[str, Any] = field(default_factory=dict)

    # 만료 시간 (기본 24시간)
    expires_at: str = ""  # ISO format


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
]
