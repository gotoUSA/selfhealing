"""
In-Memory Repository Adapters

Provides in-memory implementations of repository interfaces for:
- Testing without database
- Standalone (non-framework) usage
- Development and prototyping

Storage Strategy:
- Memory (Default): 설치 즉시 작동, 테스트/단일 서버
- Layered (L1+L2): Memory + Redis/DB, 분산 환경에서 고성능
"""

from selfhealing.adapters.memory.failed_operation import InMemoryFailedOperationRepository
from selfhealing.adapters.memory.circuit_breaker import (
    InMemoryCircuitBreakerStateRepository,
    LayeredCircuitBreakerStateRepository,
)
from selfhealing.adapters.memory.security_incident import InMemorySecurityIncidentRepository

__all__ = [
    "InMemoryFailedOperationRepository",
    "InMemoryCircuitBreakerStateRepository",
    "LayeredCircuitBreakerStateRepository",
    "InMemorySecurityIncidentRepository",
]
