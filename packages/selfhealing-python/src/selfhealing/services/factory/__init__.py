"""
Factory Package for Self-Healing Components.

Provides centralized creation of self-healing services with proper
dependency injection. This factory pattern allows:

1. Easy testing with mock repositories
2. Consistent service instantiation
3. Future-proof for package extraction
4. Pluggable provider architecture

Key Components:
    - ServiceFactory: Framework-aware service creation
    - ProviderRegistry: Register and retrieve pluggable adapters
    - Repository Factory Functions: Create repository adapters
    - Service Factory Functions: Create service instances with DI
    - Singleton Accessors: Manage service lifecycle
"""

# Framework types and ServiceFactory
from .base import (
    FrameworkType,
    ServiceFactory,
    StorageMode,
    configure_service_factory,
    get_service_factory,
    reset_service_factory,
)

# Provider Registry for pluggable architecture
from .registry import ProviderRegistry

# Repository factory functions
from .repository import (
    create_circuit_breaker_repository,
    create_failed_operation_repository,
    create_security_incident_repository,
)

# Service factory functions
from .service import (
    create_circuit_breaker_service,
    create_dlq_service,
    create_replay_service,
    create_security_violation_service,
)

# Singleton accessors
from .singleton import (
    get_circuit_breaker_service_with_di,
    get_dlq_service_with_di,
    get_replay_service_with_di,
    get_security_violation_service_with_di,
    reset_service_singletons,
)

__all__ = [
    # Framework types and ServiceFactory
    "FrameworkType",
    "StorageMode",
    "ServiceFactory",
    "get_service_factory",
    "configure_service_factory",
    "reset_service_factory",
    # Provider Registry
    "ProviderRegistry",
    # Repository factories
    "create_failed_operation_repository",
    "create_circuit_breaker_repository",
    "create_security_incident_repository",
    # Service factories
    "create_dlq_service",
    "create_replay_service",
    "create_circuit_breaker_service",
    "create_security_violation_service",
    # Singleton accessors
    "get_dlq_service_with_di",
    "get_replay_service_with_di",
    "get_circuit_breaker_service_with_di",
    "get_security_violation_service_with_di",
    "reset_service_singletons",
]
