"""
L2 Storage API Common Utilities.

Shared utility functions for L2 storage API views.
"""

import structlog

logger = structlog.get_logger()


def get_layered_repository():
    """Get LayeredCircuitBreakerStateRepository if available."""
    try:
        from selfhealing.services.factory.base import get_service_factory

        factory = get_service_factory()
        repo = factory.get_circuit_breaker_state_repository()

        # Check if it's a LayeredRepository
        from selfhealing.adapters.memory.circuit_breaker import (
            LayeredCircuitBreakerStateRepository,
        )

        if isinstance(repo, LayeredCircuitBreakerStateRepository):
            return repo
        return None
    except Exception as e:
        logger.warning(
            "l2_storage_api.failed_get_layered_repository",
            error=e,
        )
        return None


def get_shadow_logger():
    """Get ShadowLogger instance."""
    try:
        from selfhealing.adapters.memory.circuit_breaker import get_shadow_logger

        return get_shadow_logger()
    except Exception as e:
        logger.warning(
            "l2_storage_api.failed_get_shadow_logger",
            error=e,
        )
        return None
