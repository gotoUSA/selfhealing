"""
FastAPI Adapter for Self-Healing System

Provides FastAPI-specific implementations for:
- Middleware for request tracking and graceful shutdown
- Dependency injection integration
- Admin routes for monitoring and management

Usage:
    from fastapi import FastAPI
    from selfhealing.adapters.fastapi import (
        SelfHealingMiddleware,
        get_circuit_breaker_service,
        get_dlq_service,
        create_selfhealing_router,
    )

    app = FastAPI()

    # Add middleware
    app.add_middleware(SelfHealingMiddleware)

    # Include routes
    app.include_router(create_selfhealing_router(), prefix="/api/selfhealing")

    # Use in routes
    @app.get("/payments/{payment_id}")
    async def get_payment(
        payment_id: int,
        cb_service = Depends(get_circuit_breaker_service)
    ):
        ...
"""

from .middleware import SelfHealingMiddleware, ShutdownMiddleware
from .dependencies import (
    get_factory,
    get_circuit_breaker_service,
    get_dlq_service,
    get_replay_service,
    get_request_tracker,
    get_shutdown_coordinator,
    configure_fastapi_selfhealing,
)
from .routes import create_selfhealing_router

__all__ = [
    # Middleware
    "SelfHealingMiddleware",
    "ShutdownMiddleware",
    # Dependencies
    "get_factory",
    "get_circuit_breaker_service",
    "get_dlq_service",
    "get_replay_service",
    "get_request_tracker",
    "get_shutdown_coordinator",
    "configure_fastapi_selfhealing",
    # Routes
    "create_selfhealing_router",
]
