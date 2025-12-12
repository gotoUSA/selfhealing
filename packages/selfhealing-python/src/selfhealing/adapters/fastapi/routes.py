"""
FastAPI Routes for Self-Healing Admin API

Provides admin routes for:
- Circuit breaker status and management
- DLQ (Dead Letter Queue) monitoring
- Graceful shutdown control
- Health and readiness checks

Usage:
    from fastapi import FastAPI
    from selfhealing.adapters.fastapi import create_selfhealing_router

    app = FastAPI()
    app.include_router(
        create_selfhealing_router(),
        prefix="/api/selfhealing",
        tags=["selfhealing"],
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, List, Any

logger = logging.getLogger(__name__)


def create_selfhealing_router(
    include_circuit_breaker: bool = True,
    include_dlq: bool = True,
    include_shutdown: bool = True,
    include_health: bool = True,
    auth_dependency: Optional[Any] = None,
):
    """
    Create FastAPI router with self-healing admin endpoints.

    Args:
        include_circuit_breaker: Include circuit breaker routes
        include_dlq: Include DLQ routes
        include_shutdown: Include shutdown control routes
        include_health: Include health check routes
        auth_dependency: Optional FastAPI dependency for authentication

    Returns:
        FastAPI APIRouter

    Usage:
        router = create_selfhealing_router(
            include_shutdown=False,  # Disable shutdown routes in prod
            auth_dependency=Depends(require_admin),
        )
    """
    try:
        from fastapi import APIRouter, Depends, HTTPException, Query, Body
        from pydantic import BaseModel, Field
    except ImportError:
        raise ImportError("FastAPI and Pydantic are required. " "Install with: pip install fastapi pydantic")

    router = APIRouter()

    # Common dependencies
    dependencies = []
    if auth_dependency:
        dependencies.append(auth_dependency)

    # ========================================================================
    # Pydantic Models
    # ========================================================================

    class CircuitBreakerState(BaseModel):
        """Circuit breaker state response."""

        service_name: str
        state: str
        failure_count: int
        success_count: int
        last_failure_at: Optional[datetime] = None
        last_success_at: Optional[datetime] = None
        opened_at: Optional[datetime] = None

    class CircuitBreakerList(BaseModel):
        """List of circuit breaker states."""

        items: List[CircuitBreakerState]
        total: int

    class FailedOperation(BaseModel):
        """Failed operation (DLQ entry)."""

        id: int
        domain: str
        failure_type: str
        status: str
        error_message: str
        retry_count: int
        max_retries: int
        created_at: datetime
        order_id: Optional[int] = None
        payment_id: Optional[int] = None

    class FailedOperationList(BaseModel):
        """List of failed operations."""

        items: List[FailedOperation]
        total: int
        pending_count: int

    class ShutdownStatus(BaseModel):
        """Shutdown status."""

        phase: str
        in_flight_requests: int
        completed_during_drain: int
        aborted_count: int
        remaining_drain_time: Optional[float]
        is_accepting_requests: bool

    class HealthResponse(BaseModel):
        """Health check response."""

        status: str
        timestamp: datetime
        version: str = "1.0.0"
        components: dict = Field(default_factory=dict)

    class RetryRequest(BaseModel):
        """Request to retry a failed operation."""

        operation_id: int

    class RetryBatchRequest(BaseModel):
        """Request to retry multiple operations."""

        operation_ids: List[int] = Field(default_factory=list)
        domain: Optional[str] = None
        max_count: int = Field(default=10, ge=1, le=100)

    class ActionResponse(BaseModel):
        """Generic action response."""

        success: bool
        message: str
        data: Optional[dict] = None

    # ========================================================================
    # Health Routes
    # ========================================================================

    if include_health:

        @router.get(
            "/health",
            response_model=HealthResponse,
            tags=["health"],
            summary="Health check",
        )
        async def health_check():
            """
            Basic health check endpoint.

            Returns current health status of the self-healing system.
            """
            from .dependencies import _shutdown_coordinator, _configured

            components = {}

            # Check shutdown status
            if _configured and _shutdown_coordinator:
                stats = _shutdown_coordinator.get_stats()
                components["shutdown"] = {
                    "status": "healthy" if stats.phase.value == "running" else "degraded",
                    "phase": stats.phase.value,
                }

            return HealthResponse(
                status=(
                    "healthy" if not _configured or components.get("shutdown", {}).get("status") == "healthy" else "degraded"
                ),
                timestamp=datetime.now(timezone.utc),
                components=components,
            )

        @router.get(
            "/ready",
            response_model=HealthResponse,
            tags=["health"],
            summary="Readiness check",
        )
        async def readiness_check():
            """
            Kubernetes readiness probe.

            Returns 503 if the service is not ready to accept traffic.
            """
            from .dependencies import _shutdown_coordinator, _configured

            if _configured and _shutdown_coordinator:
                if not _shutdown_coordinator.is_accepting_requests():
                    raise HTTPException(
                        status_code=503,
                        detail="Service is not accepting requests",
                    )

            return HealthResponse(
                status="ready",
                timestamp=datetime.now(timezone.utc),
            )

    # ========================================================================
    # Circuit Breaker Routes
    # ========================================================================

    if include_circuit_breaker:

        @router.get(
            "/circuit-breakers",
            response_model=CircuitBreakerList,
            tags=["circuit-breaker"],
            summary="List all circuit breakers",
            dependencies=dependencies,
        )
        async def list_circuit_breakers():
            """
            Get status of all circuit breakers.
            """
            from .dependencies import get_circuit_breaker_service

            try:
                service = get_circuit_breaker_service()
                states = service.get_all_states()

                items = [
                    CircuitBreakerState(
                        service_name=s.service_name,
                        state=s.state,
                        failure_count=s.failure_count,
                        success_count=s.success_count,
                        last_failure_at=s.last_failure_at,
                        last_success_at=s.last_success_at,
                        opened_at=s.opened_at,
                    )
                    for s in states
                ]

                return CircuitBreakerList(items=items, total=len(items))
            except Exception as e:
                logger.error(f"Failed to list circuit breakers: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @router.get(
            "/circuit-breakers/{service_name}",
            response_model=CircuitBreakerState,
            tags=["circuit-breaker"],
            summary="Get circuit breaker status",
            dependencies=dependencies,
        )
        async def get_circuit_breaker(service_name: str):
            """
            Get status of a specific circuit breaker.
            """
            from .dependencies import get_circuit_breaker_service

            try:
                service = get_circuit_breaker_service()
                state = service.get_state(service_name)

                if state is None:
                    raise HTTPException(status_code=404, detail="Circuit breaker not found")

                return CircuitBreakerState(
                    service_name=state.service_name,
                    state=state.state,
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    last_failure_at=state.last_failure_at,
                    last_success_at=state.last_success_at,
                    opened_at=state.opened_at,
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get circuit breaker: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @router.post(
            "/circuit-breakers/{service_name}/reset",
            response_model=ActionResponse,
            tags=["circuit-breaker"],
            summary="Reset circuit breaker",
            dependencies=dependencies,
        )
        async def reset_circuit_breaker(service_name: str):
            """
            Reset a circuit breaker to closed state.
            """
            from .dependencies import get_circuit_breaker_service

            try:
                service = get_circuit_breaker_service()
                success = service.reset(service_name)

                return ActionResponse(
                    success=success,
                    message=f"Circuit breaker '{service_name}' reset" if success else "Reset failed",
                )
            except Exception as e:
                logger.error(f"Failed to reset circuit breaker: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # DLQ Routes
    # ========================================================================

    if include_dlq:

        @router.get(
            "/dlq",
            response_model=FailedOperationList,
            tags=["dlq"],
            summary="List failed operations",
            dependencies=dependencies,
        )
        async def list_failed_operations(
            status: Optional[str] = Query(None, description="Filter by status"),
            domain: Optional[str] = Query(None, description="Filter by domain"),
            limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0),
        ):
            """
            List failed operations in the Dead Letter Queue.
            """
            from .dependencies import get_dlq_service

            try:
                service = get_dlq_service()
                operations = service.get_pending_entries(limit=limit)

                # Apply filters
                if status:
                    operations = [op for op in operations if op.status == status]
                if domain:
                    operations = [op for op in operations if op.domain == domain]

                items = [
                    FailedOperation(
                        id=op.id,
                        domain=op.domain,
                        failure_type=op.failure_type,
                        status=op.status,
                        error_message=op.error_message,
                        retry_count=op.retry_count,
                        max_retries=op.max_retries,
                        created_at=op.created_at,
                        order_id=op.order_id,
                        payment_id=op.payment_id,
                    )
                    for op in operations
                ]

                pending_count = len([op for op in operations if op.status == "pending"])

                return FailedOperationList(
                    items=items,
                    total=len(items),
                    pending_count=pending_count,
                )
            except Exception as e:
                logger.error(f"Failed to list DLQ: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @router.get(
            "/dlq/{operation_id}",
            response_model=FailedOperation,
            tags=["dlq"],
            summary="Get failed operation details",
            dependencies=dependencies,
        )
        async def get_failed_operation(operation_id: int):
            """
            Get details of a specific failed operation.
            """
            from .dependencies import get_dlq_service

            try:
                service = get_dlq_service()
                op = service.get_entry_by_id(operation_id)

                if op is None:
                    raise HTTPException(status_code=404, detail="Operation not found")

                return FailedOperation(
                    id=op.id,
                    domain=op.domain,
                    failure_type=op.failure_type,
                    status=op.status,
                    error_message=op.error_message,
                    retry_count=op.retry_count,
                    max_retries=op.max_retries,
                    created_at=op.created_at,
                    order_id=op.order_id,
                    payment_id=op.payment_id,
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to get operation: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @router.post(
            "/dlq/{operation_id}/retry",
            response_model=ActionResponse,
            tags=["dlq"],
            summary="Retry failed operation",
            dependencies=dependencies,
        )
        async def retry_operation(operation_id: int):
            """
            Retry a failed operation.
            """
            from .dependencies import get_replay_service

            try:
                service = get_replay_service()
                result = service.replay_single(operation_id)

                return ActionResponse(
                    success=result.success,
                    message=(
                        f"Operation {operation_id} replay initiated" if result.success else f"Replay failed: {result.error}"
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to retry operation: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @router.post(
            "/dlq/retry-batch",
            response_model=ActionResponse,
            tags=["dlq"],
            summary="Retry multiple operations",
            dependencies=dependencies,
        )
        async def retry_batch(request: RetryBatchRequest):
            """
            Retry multiple failed operations.
            """
            from .dependencies import get_replay_service

            try:
                service = get_replay_service()

                if request.operation_ids:
                    results = [service.replay_single(op_id) for op_id in request.operation_ids]
                    success_count = sum(1 for r in results if r.success)
                else:
                    # Use replay_batch for domain-based replay
                    batch_result = service.replay_batch(
                        domain=request.domain,
                        max_items=request.max_count,
                    )
                    success_count = batch_result.succeeded

                return ActionResponse(
                    success=True,
                    message=f"Retried {success_count} operations",
                    data={"retried_count": success_count},
                )
            except Exception as e:
                logger.error(f"Failed to retry batch: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    # ========================================================================
    # Shutdown Routes
    # ========================================================================

    if include_shutdown:

        @router.get(
            "/shutdown/status",
            response_model=ShutdownStatus,
            tags=["shutdown"],
            summary="Get shutdown status",
            dependencies=dependencies,
        )
        async def get_shutdown_status():
            """
            Get current shutdown status and in-flight request count.
            """
            from .dependencies import get_shutdown_coordinator

            try:
                coordinator = get_shutdown_coordinator()
                stats = coordinator.get_stats()

                return ShutdownStatus(
                    phase=stats.phase.value,
                    in_flight_requests=stats.in_flight_count,
                    completed_during_drain=stats.completed_during_drain,
                    aborted_count=stats.aborted_count,
                    remaining_drain_time=stats.remaining_drain_time,
                    is_accepting_requests=coordinator.is_accepting_requests(),
                )
            except RuntimeError as e:
                # Self-healing not configured
                return ShutdownStatus(
                    phase="running",
                    in_flight_requests=0,
                    completed_during_drain=0,
                    aborted_count=0,
                    remaining_drain_time=None,
                    is_accepting_requests=True,
                )

        @router.post(
            "/shutdown/initiate",
            response_model=ActionResponse,
            tags=["shutdown"],
            summary="Initiate graceful shutdown",
            dependencies=dependencies,
        )
        async def initiate_shutdown():
            """
            Initiate graceful shutdown.

            Warning: This will stop accepting new requests!
            """
            from .dependencies import get_shutdown_coordinator

            try:
                coordinator = get_shutdown_coordinator()
                coordinator.initiate_shutdown()

                return ActionResponse(
                    success=True,
                    message="Shutdown initiated. Draining in-flight requests.",
                )
            except Exception as e:
                logger.error(f"Failed to initiate shutdown: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    return router


# ============================================================================
# Utility Routes (standalone)
# ============================================================================


def create_health_router():
    """
    Create a minimal health check router.

    Lighter than the full selfhealing router.
    """
    try:
        from fastapi import APIRouter
    except ImportError:
        raise ImportError("FastAPI is required. Install with: pip install fastapi")

    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health():
        return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

    @router.get("/ready")
    async def ready():
        return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}

    return router
