"""
Redis-based Repository Adapters.

Provides Redis implementations for:
- CircuitBreakerStateRepository
- DLQRepository (FailedOperationRepository)

Uses ResilientStorageBackend for zero data loss guarantees.
"""

from selfhealing.adapters.redis.circuit_breaker import (
    RedisCircuitBreakerStateRepository,
)
from selfhealing.adapters.redis.dlq import RedisDLQRepository

__all__ = [
    "RedisCircuitBreakerStateRepository",
    "RedisDLQRepository",
]
