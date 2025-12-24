"""
Circuit Breaker Configuration and Types

Contains configuration dataclass, state constants, and result types
for circuit breaker operations.
"""

from __future__ import annotations

from dataclasses import dataclass

from selfhealing.core.config import get_config


# =============================================================================
# Circuit Breaker State Enum
# =============================================================================


class CircuitState:
    """Circuit breaker state constants."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker operations."""

    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    success_threshold: int = 2
    # Governance parameters
    manual_override_ttl_minutes: int = 90  # Default 90 min, max recommended 180
    half_open_request_limit: int = 10  # Max requests allowed in half-open state
    max_pending_duration_hours: int = 4  # SLA for pending DLQ items
    max_retry_lifetime_hours: int = 24  # Max time to attempt retries

    # Rate limit cascade detection settings
    rate_limit_cascade_threshold: int = 10  # Number of 429s in window to trigger CB
    rate_limit_cascade_window_seconds: int = 60  # Time window for cascade detection

    # Self-DDoS protection settings
    self_ddos_protection_enabled: bool = True
    self_ddos_request_threshold: int = 100  # Max requests in window
    self_ddos_window_seconds: int = 10  # Time window for self-DDoS detection
    self_ddos_backoff_multiplier: float = 2.0  # Exponential backoff multiplier

    @classmethod
    def from_settings(cls) -> "CircuitBreakerConfig":
        """Load configuration from RuntimeConfigManager (preferred) or core config."""
        # Try RuntimeConfigManager first (runtime-configurable)
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            runtime_config = manager.get_circuit_breaker_config()
            
            return cls(
                enabled=runtime_config.get("enabled", True),
                failure_threshold=runtime_config.get("failure_threshold", 5),
                recovery_timeout=runtime_config.get("recovery_timeout", 60),
                success_threshold=runtime_config.get("success_threshold", 2),
                manual_override_ttl_minutes=runtime_config.get("manual_override_ttl_minutes", 90),
                half_open_request_limit=runtime_config.get("half_open_request_limit", 10),
                max_pending_duration_hours=runtime_config.get("max_pending_duration_hours", 4),
                max_retry_lifetime_hours=runtime_config.get("max_retry_lifetime_hours", 24),
                rate_limit_cascade_threshold=runtime_config.get("rate_limit_cascade_threshold", 10),
                rate_limit_cascade_window_seconds=runtime_config.get("rate_limit_cascade_window_seconds", 60),
                self_ddos_protection_enabled=runtime_config.get("self_ddos_protection_enabled", True),
                self_ddos_request_threshold=runtime_config.get("self_ddos_request_threshold", 100),
                self_ddos_window_seconds=runtime_config.get("self_ddos_window_seconds", 10),
                self_ddos_backoff_multiplier=runtime_config.get("self_ddos_backoff_multiplier", 2.0),
            )
        except Exception:
            pass  # Fall through to static config
        
        # Fallback to static core config
        cb_settings = get_config().circuit_breaker
        return cls(
            enabled=cb_settings.enabled,
            failure_threshold=cb_settings.failure_threshold,
            recovery_timeout=cb_settings.recovery_timeout,
            success_threshold=cb_settings.success_threshold,
            manual_override_ttl_minutes=getattr(cb_settings, "manual_override_ttl_minutes", 90),
            half_open_request_limit=getattr(cb_settings, "half_open_request_limit", 10),
            max_pending_duration_hours=getattr(cb_settings, "max_pending_duration_hours", 4),
            max_retry_lifetime_hours=getattr(cb_settings, "max_retry_lifetime_hours", 24),
            rate_limit_cascade_threshold=cb_settings.rate_limit_cascade_threshold,
            rate_limit_cascade_window_seconds=cb_settings.rate_limit_cascade_window_seconds,
            self_ddos_protection_enabled=cb_settings.self_ddos_protection_enabled,
            self_ddos_request_threshold=cb_settings.self_ddos_request_threshold,
            self_ddos_window_seconds=cb_settings.self_ddos_window_seconds,
            self_ddos_backoff_multiplier=cb_settings.self_ddos_backoff_multiplier,
        )


# =============================================================================
# Circuit Breaker Result
# =============================================================================


@dataclass
class CircuitBreakerResult:
    """Result of a circuit breaker operation."""

    success: bool
    service_name: str
    previous_state: str = ""
    new_state: str = ""
    message: str = ""
    error: str | None = None

    @classmethod
    def succeeded(
        cls,
        service_name: str,
        previous_state: str,
        new_state: str,
        message: str = "",
    ) -> "CircuitBreakerResult":
        """Factory for successful operation."""
        return cls(
            success=True,
            service_name=service_name,
            previous_state=previous_state,
            new_state=new_state,
            message=message,
        )

    @classmethod
    def failed(cls, service_name: str, error: str) -> "CircuitBreakerResult":
        """Factory for failed operation."""
        return cls(
            success=False,
            service_name=service_name,
            error=error,
        )
