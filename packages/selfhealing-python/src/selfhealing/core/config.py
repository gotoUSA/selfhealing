"""
Configuration management for the self-healing system.

This module provides a framework-agnostic configuration system
that can be populated from various sources (Django settings, env vars, etc.)
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Any, Optional, List


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breakers."""

    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    success_threshold: int = 2
    half_open_max_calls: int = 3
    half_open_request_limit: int = 10
    excluded_exceptions: List[str] = field(default_factory=list)

    # Rate limit cascade detection
    rate_limit_cascade_threshold: int = 10
    rate_limit_cascade_window_seconds: int = 60

    # Self-DDoS protection
    self_ddos_protection_enabled: bool = True
    self_ddos_request_threshold: int = 100
    self_ddos_window_seconds: int = 10
    self_ddos_backoff_multiplier: float = 2.0


@dataclass
class DLQConfig:
    """Configuration for Dead Letter Queue."""

    enabled: bool = True
    max_retries: int = 3
    retry_delay: int = 60  # seconds
    expiry_hours: int = 72
    retention_days: int = 30
    batch_size: int = 10
    max_replay_attempts: int = 2


@dataclass
class RetryConfig:
    """Configuration for retry mechanisms."""

    max_attempts: int = 3
    backoff_strategy: str = "exponential"
    backoff_base: int = 4  # Base for exponential (4^n seconds)
    base_delay: float = 1.0
    max_delay: float = 300.0
    min_delay: int = 1
    jitter: bool = True
    jitter_percent: int = 25


@dataclass
class SLAConfig:
    """
    SLA thresholds configuration (domain-neutral).

    Uses a dictionary-based approach for domain-specific thresholds,
    allowing adapters to configure application-specific domains.

    Note: This dataclass is no longer frozen to support mutable thresholds_by_domain.
    Use with care and avoid modifying after initialization in production.
    """

    # Default threshold for unregistered domains
    default_hours: int = 24

    # Domain-specific thresholds (configured by adapters)
    # Example: {"payment": 1, "order": 2, "notification": 24}
    thresholds_by_domain: dict[str, int] = field(default_factory=dict)

    def get_threshold(self, domain: str) -> timedelta:
        """Get the SLA threshold for a domain."""
        hours = self.thresholds_by_domain.get(domain.lower(), self.default_hours)
        return timedelta(hours=hours)

    def get_all_thresholds(self) -> dict[str, timedelta]:
        """Get all configured SLA thresholds as a dictionary."""
        result = {domain: timedelta(hours=hours) for domain, hours in self.thresholds_by_domain.items()}
        # Add default if no domains configured
        if not result:
            result["default"] = timedelta(hours=self.default_hours)
        return result


@dataclass
class SLODefinition:
    """
    Single SLO definition for runtime configuration.

    API를 통해 동적으로 생성/수정/삭제 가능한 SLO 정의.
    """

    name: str
    sli_type: str = "availability"  # availability, latency_p99, latency_p95, latency_p50, error_rate, throughput
    target: float = 0.999  # 목표값 (예: 0.999 = 99.9%)
    window_days: int = 30  # 측정 윈도우 (일)
    description: str = ""
    service_name: str = ""
    domain: str = ""

    # 알림 임계값
    warning_threshold: Optional[float] = None  # 경고 임계값
    critical_threshold: Optional[float] = None  # 위험 임계값

    # Burn rate 임계값 (SLO별 커스텀 가능)
    fast_burn_rate: float = 14.4  # 1시간에 2% 소진 시 위험
    slow_burn_rate: float = 3.0  # 6시간에 5% 소진 시 경고


@dataclass
class SLOConfigRuntime:
    """
    Runtime SLO configuration (API로 동적 변경 가능).

    코드에서 정의한 SLOConfig(slo.py)와 별도로,
    API를 통해 런타임에 SLO를 추가/수정/삭제할 수 있습니다.
    """

    # 기본 윈도우 (SLO 생성 시 기본값)
    default_window_days: int = 30

    # 기본 타겟 (SLO 생성 시 기본값)
    default_target: float = 0.999

    # 기본 burn rate 임계값
    default_fast_burn_rate: float = 14.4
    default_slow_burn_rate: float = 3.0

    # SLO 정의 목록 (런타임에 동적으로 관리)
    # 각 항목은 SLODefinition을 dict로 직렬화한 형태
    slos: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RateLimitConfig:
    """Configuration for rate limit coordination."""

    base_delay: float = 1.0  # Base delay in seconds
    max_delay: float = 60.0  # Maximum delay cap
    jitter_percent: float = 30.0  # ±30% random jitter
    default_retry_after: float = 5.0  # Default if no Retry-After header
    backoff_multiplier: float = 2.0  # Cooldown multiplier for consecutive 429s


@dataclass
class IdempotencyConfig:
    """Configuration for idempotency service."""

    default_cache_ttl: int = 60
    extended_cache_ttl: int = 300  # For operations requiring longer TTL
    short_cache_ttl: int = 60  # For short-lived operations
    clock_skew_tolerance_seconds: float = 5.0  # Stage 23: Clock skew tolerance


@dataclass
class SecurityConfig:
    """Security-related thresholds and timeouts."""

    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 100
    temporary_ban_hours: int = 1
    permanent_ban_threshold: int = 5
    suspicious_ip_cache_timeout: int = 86400
    injection_ban_hours: int = 24
    failed_login_threshold: int = 5
    suspicious_ip_cache_prefix: str = "security:suspicious_ip:"
    banned_ip_cache_prefix: str = "security:banned_ip:"


@dataclass
class ForensicConfig:
    """Forensic context truncation limits."""

    error_message_max_length: int = 500
    response_body_max_length: int = 5000
    user_agent_max_length: int = 500


@dataclass
class MetricsConfig:
    """Configuration for metrics collection."""

    enabled: bool = True
    prefix: str = "selfhealing"
    collection_interval: int = 60  # seconds
    export_prometheus: bool = True


@dataclass
class NotificationConfig:
    """Configuration for notifications and alerts."""

    enabled: bool = True
    channels: List[str] = field(default_factory=lambda: ["email"])
    critical_threshold: int = 10
    warning_threshold: int = 5

    # Message limits
    slack_block_text_limit: int = 3000
    description_max_length: int = 500
    action_taken_max_length: int = 200
    title_max_length: int = 150
    notification_timeout_seconds: int = 10

    # Slack channels
    critical_channel: str = "#critical-alerts"
    high_channel: str = "#ops-alerts"
    medium_channel: str = "#dev-alerts"


@dataclass
class ErrorBudgetConfig:
    """
    Configuration for Error Budget thresholds.

    Error Budget 임계값 설정 (API로 동적 변경 가능).
    Google SRE 권장 임계값을 기본값으로 사용합니다.
    """

    # Error Budget 임계값 (%)
    threshold_healthy: float = 75.0  # 75% 이상: 정상
    threshold_caution: float = 50.0  # 50-75%: 주의
    threshold_warning: float = 20.0  # 20-50%: 경고
    threshold_critical: float = 0.0  # 20% 미만: 동결 권고

    # Burn Rate 임계값 (Google SRE 권장)
    burn_rate_fast_critical: float = 14.4  # 1시간에 2% 소진 -> 즉시 대응
    burn_rate_fast_warning: float = 6.0  # 1시간에 ~0.8% 소진
    burn_rate_slow_warning: float = 3.0  # 6시간에 5% 소진
    burn_rate_slow_info: float = 1.0  # 정상 소진율

    # Fail-Safe 설정
    failsafe_alert_enabled: bool = True  # Fail-Safe 발동 시 알림 발송
    failsafe_cooldown_seconds: int = 300  # 연속 알림 방지 (5분)

    # =========================================================================
    # Heartbeat (Dead Man's Snitch) 설정
    # =========================================================================
    heartbeat_enabled: bool = True  # Heartbeat 활성화
    heartbeat_interval_seconds: int = 60  # Heartbeat 주기 (기본: 1분)
    heartbeat_timeout_seconds: int = 120  # 이 시간 초과 시 Dead 판정 (기본: 2분)

    # =========================================================================
    # 복구 알림 (Recovery Notification) 설정
    # =========================================================================
    recovery_alert_enabled: bool = True  # 복구 시 알림 발송
    recovery_alert_include_downtime: bool = True  # 다운타임 정보 포함

    # =========================================================================
    # Override 에스컬레이션 설정
    # =========================================================================
    escalation_enabled: bool = True  # Override 에스컬레이션 활성화
    escalation_channel: str = "#governance"  # 에스컬레이션 채널
    escalation_mention: str = "@cto @security"  # 멘션 대상


@dataclass
class SelfHealingConfig:
    """
    Main configuration for the self-healing system.

    This can be instantiated directly or populated from external sources
    like Django settings or environment variables.
    """

    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    dlq: DLQConfig = field(default_factory=DLQConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    sla: SLAConfig = field(default_factory=SLAConfig)
    idempotency: IdempotencyConfig = field(default_factory=IdempotencyConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    forensic: ForensicConfig = field(default_factory=ForensicConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)

    # Domain-specific overrides
    domain_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Feature flags
    auto_replay_enabled: bool = True
    security_monitoring_enabled: bool = True
    debug_mode: bool = False

    # Site configuration
    site_url: str = "http://localhost:8000"

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "SelfHealingConfig":
        """
        Create configuration from a dictionary.

        Args:
            config_dict: Dictionary with configuration values

        Returns:
            SelfHealingConfig instance
        """
        circuit_breaker = CircuitBreakerConfig(**config_dict.get("circuit_breaker", {}))
        dlq = DLQConfig(**config_dict.get("dlq", {}))
        retry = RetryConfig(**config_dict.get("retry", {}))
        sla = SLAConfig(**config_dict.get("sla", {}))
        idempotency = IdempotencyConfig(**config_dict.get("idempotency", {}))
        security = SecurityConfig(**config_dict.get("security", {}))
        forensic = ForensicConfig(**config_dict.get("forensic", {}))
        metrics = MetricsConfig(**config_dict.get("metrics", {}))
        notification = NotificationConfig(**config_dict.get("notification", {}))
        rate_limit = RateLimitConfig(**config_dict.get("rate_limit", {}))

        return cls(
            circuit_breaker=circuit_breaker,
            dlq=dlq,
            retry=retry,
            sla=sla,
            idempotency=idempotency,
            security=security,
            forensic=forensic,
            metrics=metrics,
            notification=notification,
            rate_limit=rate_limit,
            domain_configs=config_dict.get("domain_configs", {}),
            auto_replay_enabled=config_dict.get("auto_replay_enabled", True),
            security_monitoring_enabled=config_dict.get("security_monitoring_enabled", True),
            debug_mode=config_dict.get("debug_mode", False),
        )

    def get_domain_config(self, domain: str, config_type: str) -> Optional[Dict[str, Any]]:
        """
        Get domain-specific configuration override.

        Args:
            domain: The domain name (e.g., 'payment', 'order')
            config_type: Type of config (e.g., 'circuit_breaker', 'retry')

        Returns:
            Domain-specific config dict or None if not defined
        """
        domain_config = self.domain_configs.get(domain, {})
        return domain_config.get(config_type)

    def get_circuit_breaker_config(self, domain: Optional[str] = None) -> CircuitBreakerConfig:
        """Get circuit breaker config, optionally with domain overrides."""
        if domain:
            override = self.get_domain_config(domain, "circuit_breaker")
            if override:
                return CircuitBreakerConfig(
                    enabled=override.get("enabled", self.circuit_breaker.enabled),
                    failure_threshold=override.get("failure_threshold", self.circuit_breaker.failure_threshold),
                    recovery_timeout=override.get("recovery_timeout", self.circuit_breaker.recovery_timeout),
                    success_threshold=override.get("success_threshold", self.circuit_breaker.success_threshold),
                    half_open_max_calls=override.get("half_open_max_calls", self.circuit_breaker.half_open_max_calls),
                )
        return self.circuit_breaker

    def get_retry_config(self, domain: Optional[str] = None) -> RetryConfig:
        """Get retry config, optionally with domain overrides."""
        if domain:
            override = self.get_domain_config(domain, "retry")
            if override:
                return RetryConfig(
                    max_attempts=override.get("max_attempts", self.retry.max_attempts),
                    backoff_strategy=override.get("backoff_strategy", self.retry.backoff_strategy),
                    backoff_base=override.get("backoff_base", self.retry.backoff_base),
                    base_delay=override.get("base_delay", self.retry.base_delay),
                    max_delay=override.get("max_delay", self.retry.max_delay),
                    jitter=override.get("jitter", self.retry.jitter),
                    jitter_percent=override.get("jitter_percent", self.retry.jitter_percent),
                )
        return self.retry

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "circuit_breaker": {
                "enabled": self.circuit_breaker.enabled,
                "failure_threshold": self.circuit_breaker.failure_threshold,
                "recovery_timeout": self.circuit_breaker.recovery_timeout,
                "success_threshold": self.circuit_breaker.success_threshold,
                "half_open_max_calls": self.circuit_breaker.half_open_max_calls,
            },
            "dlq": {
                "enabled": self.dlq.enabled,
                "max_retries": self.dlq.max_retries,
                "retry_delay": self.dlq.retry_delay,
                "expiry_hours": self.dlq.expiry_hours,
                "batch_size": self.dlq.batch_size,
            },
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "backoff_strategy": self.retry.backoff_strategy,
                "backoff_base": self.retry.backoff_base,
                "base_delay": self.retry.base_delay,
                "max_delay": self.retry.max_delay,
                "jitter": self.retry.jitter,
                "jitter_percent": self.retry.jitter_percent,
            },
            "sla": {
                "thresholds_by_domain": self.sla.thresholds_by_domain,
                "default_hours": self.sla.default_hours,
            },
            "metrics": {
                "enabled": self.metrics.enabled,
                "prefix": self.metrics.prefix,
                "collection_interval": self.metrics.collection_interval,
            },
            "notification": {
                "enabled": self.notification.enabled,
                "channels": self.notification.channels,
            },
            "rate_limit": {
                "base_delay": self.rate_limit.base_delay,
                "max_delay": self.rate_limit.max_delay,
                "jitter_percent": self.rate_limit.jitter_percent,
                "default_retry_after": self.rate_limit.default_retry_after,
                "backoff_multiplier": self.rate_limit.backoff_multiplier,
            },
            "domain_configs": self.domain_configs,
            "auto_replay_enabled": self.auto_replay_enabled,
            "security_monitoring_enabled": self.security_monitoring_enabled,
            "debug_mode": self.debug_mode,
        }


# Global configuration instance (can be set by adapters)
_config: Optional[SelfHealingConfig] = None


def get_config() -> SelfHealingConfig:
    """Get the current configuration, creating a default if none exists."""
    global _config
    if _config is None:
        _config = SelfHealingConfig()
    return _config


def set_config(config: Optional[SelfHealingConfig]) -> None:
    """Set the global configuration."""
    global _config
    _config = config


def reload_config() -> SelfHealingConfig:
    """Force reload of configuration (resets to defaults)."""
    global _config
    _config = SelfHealingConfig()
    return _config


def configure(**kwargs) -> SelfHealingConfig:
    """
    Configure the self-healing system with the given parameters.

    This is a convenience function for simple configuration.

    Args:
        **kwargs: Configuration parameters

    Returns:
        The configured SelfHealingConfig instance
    """
    global _config
    _config = SelfHealingConfig.from_dict(kwargs)
    return _config


# Convenience getters for sub-configurations
def get_circuit_breaker_settings() -> CircuitBreakerConfig:
    """Get circuit breaker configuration."""
    return get_config().circuit_breaker


def get_dlq_settings() -> DLQConfig:
    """Get DLQ configuration."""
    return get_config().dlq


def get_retry_settings() -> RetryConfig:
    """Get retry configuration."""
    return get_config().retry


def get_sla_thresholds() -> SLAConfig:
    """Get SLA thresholds configuration."""
    return get_config().sla


def get_security_thresholds() -> SecurityConfig:
    """Get security thresholds configuration."""
    return get_config().security


def get_forensic_settings() -> ForensicConfig:
    """Get forensic context configuration."""
    return get_config().forensic


def get_notification_settings() -> NotificationConfig:
    """Get notification configuration."""
    return get_config().notification


def get_rate_limit_settings() -> RateLimitConfig:
    """Get rate limit configuration."""
    return get_config().rate_limit
