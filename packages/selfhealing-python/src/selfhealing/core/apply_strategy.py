"""
Configuration Apply Strategy.

Defines how configuration changes are applied to the running system.

Strategies:
- IMMEDIATE: Apply changes right away
- DELAYED: Apply changes after N seconds (cancellable)
- GRACEFUL: Wait for in-progress operations to complete, then apply
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ApplyStrategy(Enum):
    """Configuration apply strategy."""

    IMMEDIATE = "immediate"  # Apply right now
    DELAYED = "delayed"  # Apply after N seconds (cancellable)
    GRACEFUL = "graceful"  # Wait for in-progress ops, then apply


@dataclass
class ApplyOptions:
    """Options for applying configuration changes."""

    strategy: ApplyStrategy = ApplyStrategy.IMMEDIATE
    delay_seconds: int = 0  # For DELAYED strategy
    grace_timeout_seconds: int = 60  # Max wait for GRACEFUL strategy

    def __post_init__(self):
        """Validate options."""
        if self.strategy == ApplyStrategy.DELAYED and self.delay_seconds <= 0:
            raise ValueError("delay_seconds must be > 0 for DELAYED strategy")
        if self.grace_timeout_seconds <= 0:
            raise ValueError("grace_timeout_seconds must be > 0")


# =============================================================================
# Default strategies per config type
# =============================================================================

# Config types where changes have no operational impact
SAFE_IMMEDIATE_CONFIGS = frozenset([
    "sla",
    "metrics",
    "notification",
    "forensic",
])

# Config types that control traffic/protection - need care
CRITICAL_CONFIGS = frozenset([
    "circuit_breaker",
    "rate_limit",
    "security",
    "idempotency",
])

# Config types that affect processing
PROCESSING_CONFIGS = frozenset([
    "retry",
    "dlq",
])


@dataclass
class DefaultApplyConfig:
    """Default apply configuration for a config type."""

    strategy: ApplyStrategy
    delay_seconds: int = 0
    grace_timeout_seconds: int = 60
    warning_message: Optional[str] = None


# Default apply strategy per config type
DEFAULT_APPLY_STRATEGIES: dict[str, DefaultApplyConfig] = {
    # Safe - immediate by default
    "sla": DefaultApplyConfig(
        strategy=ApplyStrategy.IMMEDIATE,
    ),
    "metrics": DefaultApplyConfig(
        strategy=ApplyStrategy.IMMEDIATE,
    ),
    "notification": DefaultApplyConfig(
        strategy=ApplyStrategy.IMMEDIATE,
    ),
    "forensic": DefaultApplyConfig(
        strategy=ApplyStrategy.IMMEDIATE,
    ),
    # Traffic control - immediate but with warning
    "rate_limit": DefaultApplyConfig(
        strategy=ApplyStrategy.IMMEDIATE,
        warning_message="Rate limit changes take effect immediately for new requests",
    ),
    # Processing - delayed to protect in-flight operations
    "retry": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=10,
    ),
    "dlq": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=10,
    ),
    # Critical - delayed with longer window for cancellation
    "circuit_breaker": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=30,
        warning_message="Circuit breaker protects system stability. Change with caution.",
    ),
    "idempotency": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=30,
        warning_message="Idempotency prevents duplicate transactions. Change with caution.",
    ),
    "security": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=60,
        warning_message="Security settings are highly sensitive. Ensure you have reviewed the changes.",
    ),
    # Error Budget - delayed to prevent alert storm from threshold changes
    "error_budget": DefaultApplyConfig(
        strategy=ApplyStrategy.DELAYED,
        delay_seconds=30,
        warning_message="Error budget threshold changes can trigger immediate state transitions and alerts.",
    ),
}


def get_default_apply_config(config_type: str) -> DefaultApplyConfig:
    """Get default apply configuration for a config type."""
    return DEFAULT_APPLY_STRATEGIES.get(
        config_type,
        DefaultApplyConfig(strategy=ApplyStrategy.IMMEDIATE),
    )


def get_effective_apply_options(
    config_type: str,
    strategy: Optional[str] = None,
    delay_seconds: Optional[int] = None,
    grace_timeout_seconds: Optional[int] = None,
) -> ApplyOptions:
    """
    Get effective apply options, merging user overrides with defaults.

    Args:
        config_type: The configuration type (e.g., "circuit_breaker")
        strategy: User-specified strategy (overrides default)
        delay_seconds: User-specified delay (overrides default)
        grace_timeout_seconds: User-specified grace timeout

    Returns:
        ApplyOptions with effective values
    """
    default = get_default_apply_config(config_type)

    # Determine effective strategy
    effective_strategy = (
        ApplyStrategy(strategy) if strategy else default.strategy
    )

    # Determine effective delay
    if effective_strategy == ApplyStrategy.DELAYED:
        effective_delay = delay_seconds if delay_seconds is not None else default.delay_seconds
        # Ensure at least 1 second for delayed
        if effective_delay <= 0:
            effective_delay = default.delay_seconds or 10
    else:
        effective_delay = 0

    # Determine effective grace timeout
    effective_grace = (
        grace_timeout_seconds if grace_timeout_seconds is not None
        else default.grace_timeout_seconds
    )

    return ApplyOptions(
        strategy=effective_strategy,
        delay_seconds=effective_delay,
        grace_timeout_seconds=effective_grace,
    )
