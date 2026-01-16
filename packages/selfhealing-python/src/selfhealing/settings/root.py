"""
Root Settings - SelfHealingSettings.

Unified Pydantic Settings replacing core/config.py:SelfHealingConfig.

All sub-settings are composed here for single-point access.
"""

from typing import Any, Dict, Optional

from pydantic import Field, ConfigDict
from pydantic_settings import BaseSettings

from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
from selfhealing.settings.dlq import DLQSettings
from selfhealing.settings.retry import RetrySettings
from selfhealing.settings.rate_limit import RateLimitSettings
from selfhealing.settings.security import SecuritySettings
from selfhealing.settings.sla import SLASettings
from selfhealing.settings.idempotency import IdempotencySettings
from selfhealing.settings.forensic import ForensicSettings
from selfhealing.settings.metrics import MetricsSettings
from selfhealing.settings.notification import NotificationSettings
from selfhealing.settings.governance import GovernanceSettings
from selfhealing.settings.error_budget import ErrorBudgetSettings
from selfhealing.settings.chaos import ChaosSettings
from selfhealing.settings.drift_threshold import DriftThresholdSettings
from selfhealing.settings.l2_storage import L2StorageSettings
from selfhealing.settings.logging_config import LoggingSettings


class SelfHealingSettings(BaseSettings):
    """
    Root configuration for the self-healing system.
    
    Unified Pydantic Settings replacing legacy SelfHealingConfig dataclass.
    
    Usage:
        from selfhealing.settings import get_config
        config = get_config()
        print(config.circuit_breaker.failure_threshold)
    """
    
    model_config = ConfigDict(
        extra="ignore",
        validate_default=True,
    )
    
    # ==========================================================================
    # Sub-settings (composed, not nested BaseSettings)
    # ==========================================================================
    circuit_breaker: CircuitBreakerSettings = Field(
        default_factory=CircuitBreakerSettings,
        description="Circuit breaker configuration",
    )
    dlq: DLQSettings = Field(
        default_factory=DLQSettings,
        description="Dead Letter Queue configuration",
    )
    retry: RetrySettings = Field(
        default_factory=RetrySettings,
        description="Retry mechanism configuration",
    )
    rate_limit: RateLimitSettings = Field(
        default_factory=RateLimitSettings,
        description="Rate limiting configuration",
    )
    security: SecuritySettings = Field(
        default_factory=SecuritySettings,
        description="Security configuration",
    )
    sla: SLASettings = Field(
        default_factory=SLASettings,
        description="SLA configuration",
    )
    idempotency: IdempotencySettings = Field(
        default_factory=IdempotencySettings,
        description="Idempotency configuration",
    )
    forensic: ForensicSettings = Field(
        default_factory=ForensicSettings,
        description="Forensic logging configuration",
    )
    metrics: MetricsSettings = Field(
        default_factory=MetricsSettings,
        description="Metrics configuration",
    )
    notification: NotificationSettings = Field(
        default_factory=NotificationSettings,
        description="Notification configuration",
    )
    governance: GovernanceSettings = Field(
        default_factory=GovernanceSettings,
        description="Governance configuration",
    )
    error_budget: ErrorBudgetSettings = Field(
        default_factory=ErrorBudgetSettings,
        description="Error budget configuration",
    )
    chaos: ChaosSettings = Field(
        default_factory=ChaosSettings,
        description="Chaos engineering configuration",
    )
    drift_threshold: DriftThresholdSettings = Field(
        default_factory=DriftThresholdSettings,
        description="Drift threshold configuration",
    )
    l2_storage: L2StorageSettings = Field(
        default_factory=L2StorageSettings,
        description="L2 storage configuration",
    )
    logging: LoggingSettings = Field(
        default_factory=LoggingSettings,
        description="Logging configuration",
    )
    
    # ==========================================================================
    # Feature flags
    # ==========================================================================
    auto_replay_enabled: bool = Field(
        default=True,
        description="Enable automatic replay of failed requests",
    )
    security_monitoring_enabled: bool = Field(
        default=True,
        description="Enable security monitoring",
    )
    debug_mode: bool = Field(
        default=False,
        description="Enable debug mode",
    )
    
    # ==========================================================================
    # Site configuration
    # ==========================================================================
    site_url: str = Field(
        default="http://localhost:8000",
        description="Base URL for the site",
    )
    
    # ==========================================================================
    # Domain-specific overrides
    # ==========================================================================
    domain_configs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-domain configuration overrides",
    )

    # ==========================================================================
    # Convenience methods for backward compatibility
    # ==========================================================================
    def get_circuit_breaker_config(self, domain: Optional[str] = None) -> CircuitBreakerSettings:
        """Get circuit breaker config, with optional domain overrides."""
        if domain and domain in self.domain_configs:
            domain_cb = self.domain_configs[domain].get("circuit_breaker", {})
            if domain_cb:
                return CircuitBreakerSettings(**{
                    **self.circuit_breaker.model_dump(),
                    **domain_cb,
                })
        return self.circuit_breaker
    
    def get_retry_config(self, domain: Optional[str] = None) -> RetrySettings:
        """Get retry config, with optional domain overrides."""
        if domain and domain in self.domain_configs:
            domain_retry = self.domain_configs[domain].get("retry", {})
            if domain_retry:
                return RetrySettings(**{
                    **self.retry.model_dump(),
                    **domain_retry,
                })
        return self.retry


# =============================================================================
# Singleton pattern
# =============================================================================
_settings: Optional[SelfHealingSettings] = None


def get_config() -> SelfHealingSettings:
    """
    Get the global SelfHealingSettings instance.
    
    Creates a default instance if none exists.
    
    Returns:
        SelfHealingSettings singleton
    """
    global _settings
    if _settings is None:
        _settings = SelfHealingSettings()
    return _settings


def set_config(config: Optional[SelfHealingSettings]) -> None:
    """
    Set the global configuration.
    
    Args:
        config: SelfHealingSettings instance or None to reset
    """
    global _settings
    _settings = config


def reset_config() -> None:
    """Reset the global configuration (for testing)."""
    global _settings
    _settings = None


def reload_config() -> SelfHealingSettings:
    """Force reload of configuration."""
    global _settings
    _settings = SelfHealingSettings()
    return _settings


def configure(**kwargs: Any) -> SelfHealingSettings:
    """
    Configure the self-healing system with the given parameters.
    
    Args:
        **kwargs: Configuration parameters
        
    Returns:
        Configured SelfHealingSettings instance
    """
    global _settings
    _settings = SelfHealingSettings(**kwargs)
    return _settings


# =============================================================================
# Convenience getters for sub-configurations
# These provide shortcuts to access specific settings without going through get_config()
# =============================================================================

def get_circuit_breaker_config() -> CircuitBreakerSettings:
    """Get circuit breaker configuration."""
    return get_config().circuit_breaker


def get_dlq_config() -> DLQSettings:
    """Get DLQ configuration."""
    return get_config().dlq


def get_retry_config() -> RetrySettings:
    """Get retry configuration."""
    return get_config().retry


def get_sla_thresholds() -> SLASettings:
    """Get SLA thresholds configuration."""
    return get_config().sla


def get_security_thresholds() -> SecuritySettings:
    """Get security thresholds configuration."""
    return get_config().security


def get_forensic_config() -> ForensicSettings:
    """Get forensic context configuration."""
    return get_config().forensic


def get_notification_config() -> NotificationSettings:
    """Get notification configuration."""
    return get_config().notification


def get_rate_limit_config() -> RateLimitSettings:
    """Get rate limit configuration."""
    return get_config().rate_limit


# =============================================================================
# Legacy aliases for backward compatibility
# =============================================================================
SelfHealingConfig = SelfHealingSettings

# Legacy function aliases
get_dlq_settings = get_dlq_config
get_retry_settings = get_retry_config
get_forensic_settings = get_forensic_config
get_notification_settings = get_notification_config
get_rate_limit_settings = get_rate_limit_config
