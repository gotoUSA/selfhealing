"""
FastAPI Dependency Injection for Self-Healing System

Provides FastAPI-style dependencies for injecting self-healing services.

Usage:
    from fastapi import Depends
    from selfhealing.adapters.fastapi import (
        get_circuit_breaker_service,
        get_dlq_service,
    )

    @app.post("/payments")
    async def create_payment(
        cb_service = Depends(get_circuit_breaker_service),
        dlq_service = Depends(get_dlq_service),
    ):
        ...
"""

from __future__ import annotations

import logging
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from selfhealing.factory import ProviderRegistry
from selfhealing.core.shutdown_coordinator import (
    RequestTracker,
    GracefulShutdownCoordinator as ShutdownCoordinator,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Global State (module-level singletons)
# ============================================================================

_request_tracker: Optional[RequestTracker] = None
_shutdown_coordinator: Optional[ShutdownCoordinator] = None
_configured: bool = False


def configure_fastapi_selfhealing(
    app: "FastAPI",
    drain_timeout: float = 30.0,
    request_timeout: float = 60.0,
    enable_middleware: bool = True,
    exclude_paths: Optional[list[str]] = None,
) -> tuple[RequestTracker, ShutdownCoordinator]:
    """
    Configure self-healing for a FastAPI application.

    This is the recommended way to set up self-healing. It:
    - Creates RequestTracker and ShutdownCoordinator
    - Adds middleware for request tracking
    - Registers shutdown handlers

    Args:
        app: FastAPI application instance
        drain_timeout: Max time to wait for requests to complete during shutdown
        request_timeout: Max time a single request can take
        enable_middleware: Whether to add SelfHealingMiddleware
        exclude_paths: Paths to exclude from tracking

    Returns:
        Tuple of (RequestTracker, ShutdownCoordinator)

    Usage:
        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import configure_fastapi_selfhealing

        app = FastAPI()
        tracker, coordinator = configure_fastapi_selfhealing(app)

        @app.on_event("shutdown")
        async def shutdown():
            await coordinator.initiate_shutdown()
    """
    global _request_tracker, _shutdown_coordinator, _configured

    # Create tracker and coordinator
    _request_tracker = RequestTracker(max_request_age_seconds=request_timeout)
    _shutdown_coordinator = ShutdownCoordinator(
        request_tracker=_request_tracker,
        drain_timeout=drain_timeout,
    )

    # Add middleware if requested
    if enable_middleware:
        from .middleware import SelfHealingMiddleware

        app.add_middleware(
            SelfHealingMiddleware,
            request_tracker=_request_tracker,
            shutdown_coordinator=_shutdown_coordinator,
            exclude_paths=exclude_paths,
        )

    # Register lifespan shutdown handler
    @app.on_event("shutdown")
    async def _selfhealing_shutdown():
        logger.info("[SelfHealing] FastAPI shutdown initiated")
        _shutdown_coordinator.initiate_shutdown()
        _shutdown_coordinator.wait_for_drain()

    _configured = True
    logger.info("[SelfHealing] FastAPI self-healing configured")

    return _request_tracker, _shutdown_coordinator


# ============================================================================
# Dependency Functions
# ============================================================================


def get_factory() -> ProviderRegistry:
    """
    Get the ProviderRegistry for accessing all services.

    Returns:
        ProviderRegistry class (singleton pattern)
    """
    return ProviderRegistry


def get_request_tracker() -> RequestTracker:
    """
    Get the global RequestTracker instance.

    Raises:
        RuntimeError: If configure_fastapi_selfhealing was not called

    Returns:
        RequestTracker instance
    """
    if _request_tracker is None:
        raise RuntimeError("Self-healing not configured. " "Call configure_fastapi_selfhealing(app) first.")
    return _request_tracker


def get_shutdown_coordinator() -> ShutdownCoordinator:
    """
    Get the global ShutdownCoordinator instance.

    Raises:
        RuntimeError: If configure_fastapi_selfhealing was not called

    Returns:
        ShutdownCoordinator instance
    """
    if _shutdown_coordinator is None:
        raise RuntimeError("Self-healing not configured. " "Call configure_fastapi_selfhealing(app) first.")
    return _shutdown_coordinator


def get_circuit_breaker_service():
    """
    FastAPI dependency for CircuitBreakerService.

    Usage:
        @app.get("/status")
        def get_status(cb = Depends(get_circuit_breaker_service)):
            return cb.get_all_states()
    """
    from selfhealing.services.circuit_breaker_service import CircuitBreakerService
    from selfhealing.adapters.memory import InMemoryCircuitBreakerStateRepository

    # Use in-memory repository by default for FastAPI (framework-independent)
    # For Django integration, use the Django-specific dependency
    global _circuit_breaker_repo
    if "_circuit_breaker_repo" not in globals() or _circuit_breaker_repo is None:
        _circuit_breaker_repo = InMemoryCircuitBreakerStateRepository()

    return CircuitBreakerService(repository=_circuit_breaker_repo)


# Global repository instances for singleton pattern
_circuit_breaker_repo = None
_failed_operation_repo = None


def get_dlq_service():
    """
    FastAPI dependency for DLQService.

    Usage:
        @app.post("/retry/{operation_id}")
        def retry_operation(
            operation_id: int,
            dlq = Depends(get_dlq_service)
        ):
            return dlq.retry(operation_id)
    """
    from selfhealing.services.dlq_service import DLQService
    from selfhealing.adapters.memory import InMemoryFailedOperationRepository

    global _failed_operation_repo
    if _failed_operation_repo is None:
        _failed_operation_repo = InMemoryFailedOperationRepository()

    return DLQService(repository=_failed_operation_repo)


def get_replay_service():
    """
    FastAPI dependency for ReplayService.

    Usage:
        @app.post("/replay/batch")
        def replay_batch(dlq = Depends(get_replay_service)):
            return dlq.replay_pending()
    """
    from selfhealing.services.replay_service import ReplayService
    from selfhealing.adapters.memory import InMemoryFailedOperationRepository

    global _failed_operation_repo
    if _failed_operation_repo is None:
        _failed_operation_repo = InMemoryFailedOperationRepository()

    return ReplayService(repository=_failed_operation_repo)


# ============================================================================
# Dependency Factory (for custom configuration)
# ============================================================================


def create_circuit_breaker_dependency(
    repository_name: str = "memory",
) -> Callable:
    """
    Create a customized circuit breaker dependency.

    Args:
        repository_name: Name of repository to use ('memory', 'django', etc.)

    Returns:
        Dependency function for FastAPI

    Usage:
        get_cb = create_circuit_breaker_dependency(repository_name="django")

        @app.get("/status")
        def get_status(cb = Depends(get_cb)):
            ...
    """

    def _dependency():
        from selfhealing.services.circuit_breaker_service import CircuitBreakerService

        repo = ProviderRegistry.get_circuit_breaker_repo(
            name=repository_name,
            singleton=True,
        )
        return CircuitBreakerService(state_repository=repo)

    return _dependency


def create_dlq_dependency(
    repository_name: str = "memory",
) -> Callable:
    """
    Create a customized DLQ dependency.

    Args:
        repository_name: Name of repository to use

    Returns:
        Dependency function for FastAPI
    """

    def _dependency():
        from selfhealing.services.dlq_service import DLQService

        repo = ProviderRegistry.get_failed_operation_repo(
            name=repository_name,
            singleton=True,
        )
        return DLQService(repository=repo)

    return _dependency
