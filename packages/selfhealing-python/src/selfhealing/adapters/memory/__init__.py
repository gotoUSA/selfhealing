"""
In-Memory Repository Adapters

Provides in-memory implementations of repository interfaces for:
- Testing without database
- Standalone (non-framework) usage
- Development and prototyping
"""

from .repositories import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    InMemorySecurityIncidentRepository,
)

__all__ = [
    "InMemoryFailedOperationRepository",
    "InMemoryCircuitBreakerStateRepository",
    "InMemorySecurityIncidentRepository",
]
