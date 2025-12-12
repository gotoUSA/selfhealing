"""
In-Memory Repository Adapters

Provides in-memory implementations of repository interfaces for:
- Testing without database
- Standalone (non-framework) usage
- Development and prototyping
"""

from selfhealing.adapters.memory.failed_operation import InMemoryFailedOperationRepository
from selfhealing.adapters.memory.circuit_breaker import InMemoryCircuitBreakerStateRepository
from selfhealing.adapters.memory.security_incident import InMemorySecurityIncidentRepository

__all__ = [
    "InMemoryFailedOperationRepository",
    "InMemoryCircuitBreakerStateRepository",
    "InMemorySecurityIncidentRepository",
]
