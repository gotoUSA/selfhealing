"""
Service Factory for Self-Healing Components.

DEPRECATED: This module is kept for backward compatibility.
Import from selfhealing.services.factory package instead.

This module re-exports all components from the factory package:
    - from selfhealing.services.factory import ServiceFactory
    - from selfhealing.services.factory import ProviderRegistry
    - etc.

For new code, use:
    from selfhealing.services.factory import (
        ServiceFactory,
        FrameworkType,
        ProviderRegistry,
        create_dlq_service,
        ...
    )
"""

# Re-export everything from the factory package for backward compatibility
from selfhealing.services.factory import (
    # Framework types and ServiceFactory
    FrameworkType,
    ServiceFactory,
    get_service_factory,
    configure_service_factory,
    reset_service_factory,
    # Provider Registry
    ProviderRegistry,
    # Repository factories
    create_failed_operation_repository,
    create_circuit_breaker_repository,
    create_security_incident_repository,
    # Service factories
    create_dlq_service,
    create_replay_service,
    create_circuit_breaker_service,
    create_security_violation_service,
    # Singleton accessors
    get_dlq_service_with_di,
    get_replay_service_with_di,
    get_circuit_breaker_service_with_di,
    get_security_violation_service_with_di,
    reset_service_singletons,
)
