"""
API Tiering System for Criticality-Based Load Shedding.

Provides tier-based rate limiting where different APIs get different
treatment during emergency mode. Critical APIs get priority access
while non-essential APIs are shed first.

Tier Hierarchy:
- Tier 1 (Critical): Self-healing actions, payment APIs - 50% allowed in emergency
- Tier 2 (Standard): Config changes, DLQ replay - 10% allowed in emergency
- Tier 3 (Non-Essential): Dashboard, metrics - Blocked in emergency

This package has been refactored from a single 1,700-line file into:
- enums.py: TierFallbackReason, PatternType, OverrideIdentifierType
- models.py: TierResult, TierDefinition, TierMapping, TierOverride
- defaults.py: Default tier templates and static critical paths
- circuit_breaker.py: TieringCircuitBreaker
- validator.py: ValidationResult, TierConfigValidator
- registry.py: TierRegistry
- middleware.py: TieringMiddleware
"""

from __future__ import annotations

# Circuit Breaker
from .circuit_breaker import (
    TieringCircuitBreaker,
    get_tiering_circuit_breaker,
)

# Defaults
from .defaults import (
    DEFAULT_TIER_DEFINITIONS,
    DEFAULT_TIER_MAPPINGS,
    DEFAULT_TIER_OVERRIDES,
    STATIC_CRITICAL_PATHS,
    STATIC_CRITICAL_PREFIXES,
)

# Enums
from .enums import (
    OverrideIdentifierType,
    TierMatchType,
    TierFallbackReason,
)

# Middleware
from .middleware import TieringMiddleware

# Models
from .models import (
    TierDefinition,
    TierMapping,
    TierOverride,
    TierResult,
)

# Registry
from .registry import (
    TierRegistry,
    get_tier_registry,
)

# Validator
from .validator import (
    TierConfigValidator,
    TierValidationResult,
)

__all__ = [
    # Enums
    "TierFallbackReason",
    "TierMatchType",
    "OverrideIdentifierType",
    # Models
    "TierResult",
    "TierDefinition",
    "TierMapping",
    "TierOverride",
    # Defaults
    "STATIC_CRITICAL_PATHS",
    "STATIC_CRITICAL_PREFIXES",
    "DEFAULT_TIER_DEFINITIONS",
    "DEFAULT_TIER_MAPPINGS",
    "DEFAULT_TIER_OVERRIDES",
    # Circuit Breaker
    "TieringCircuitBreaker",
    "get_tiering_circuit_breaker",
    # Validator
    "TierValidationResult",
    "TierConfigValidator",
    # Registry
    "TierRegistry",
    "get_tier_registry",
    # Middleware
    "TieringMiddleware",
]
