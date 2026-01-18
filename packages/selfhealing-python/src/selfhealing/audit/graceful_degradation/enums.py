"""
Graceful Degradation Enums and Configurations.

Contains:
- DegradationLevel: Degradation level enum
- CircuitState: Circuit breaker states
- FallbackConfig: Configuration for fallback chain
- CircuitBreakerConfig: Configuration for circuit breaker
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class DegradationLevel(str, Enum):
    """
    Hash chain degradation levels.
    
    Determines available features at each level:
    - NORMAL: Full functionality with Redis
    - DEGRADED: Partial functionality with local fallback
    - EMERGENCY: Minimal functionality, memory-only
    - READONLY: No writes, only reads from cache
    """
    NORMAL = "normal"
    DEGRADED = "degraded"
    EMERGENCY = "emergency"
    READONLY = "readonly"


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class FallbackConfig:
    """Configuration for fallback chain."""
    redis_timeout_seconds: float = 5.0
    replica_timeout_seconds: float = 3.0
    local_file_path: Optional[Path] = None
    memory_max_entries: int = 10000
    key_prefix: str = "selfhealing:"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_requests: int = 3
    success_threshold: int = 2


__all__ = [
    "DegradationLevel",
    "CircuitState",
    "FallbackConfig",
    "CircuitBreakerConfig",
]
