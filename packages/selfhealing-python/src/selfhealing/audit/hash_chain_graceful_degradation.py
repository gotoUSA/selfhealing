"""
Graceful Degradation for Hash Chain Operations.

FACADE MODULE - Re-exports from graceful_degradation/ package.

This file maintains backward compatibility.
The actual implementation has been split into the graceful_degradation/ package:

- enums.py: DegradationLevel, CircuitState, FallbackConfig, CircuitBreakerConfig
- fallback.py: HashChainFallbackChain
- marker.py: DegradedEntryInfo, DegradedEntryMarker
- wal_recovery.py: HashChainWALEntry, HashChainWALRecovery
- degradation_manager.py: HashChainDegradationManager
- circuit_breaker.py: HashChainCircuitBreaker
- manager.py: HashChainGracefulDegradationManager

Migration:
    # Old import (still works)
    from selfhealing.audit.hash_chain_graceful_degradation import (
        HashChainGracefulDegradationManager,
        DegradationLevel,
    )
    
    # New import (preferred)
    from selfhealing.audit.graceful_degradation import (
        HashChainGracefulDegradationManager,
        DegradationLevel,
    )
"""

from __future__ import annotations

# Re-export all from graceful_degradation package
from .graceful_degradation import (
    # Enums and configs
    DegradationLevel,
    CircuitState,
    FallbackConfig,
    CircuitBreakerConfig,
    # Fallback chain
    HashChainFallbackChain,
    # Degraded marker
    DegradedEntryInfo,
    DegradedEntryMarker,
    # WAL recovery
    HashChainWALEntry,
    HashChainWALRecovery,
    # Degradation manager
    HashChainDegradationManager,
    # Circuit breaker
    HashChainCircuitBreaker,
    # Unified manager
    HashChainGracefulDegradationManager,
)


__all__ = [
    # Enums and configs
    "DegradationLevel",
    "CircuitState",
    "FallbackConfig",
    "CircuitBreakerConfig",
    # Fallback chain
    "HashChainFallbackChain",
    # Degraded marker
    "DegradedEntryInfo",
    "DegradedEntryMarker",
    # WAL recovery
    "HashChainWALEntry",
    "HashChainWALRecovery",
    # Degradation manager
    "HashChainDegradationManager",
    # Circuit breaker
    "HashChainCircuitBreaker",
    # Unified manager
    "HashChainGracefulDegradationManager",
]
