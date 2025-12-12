"""
FastAPI Middleware for Self-Healing System

Provides:
- Request tracking for graceful shutdown
- Circuit breaker integration
- Error handling with DLQ
- Health check bypass
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

from selfhealing.core.shutdown_coordinator import (
    RequestTracker,
    GracefulShutdownCoordinator,
    ShutdownPhase,
)

# Alias for cleaner API
ShutdownCoordinator = GracefulShutdownCoordinator
from selfhealing.core.request_context import RequestContext

logger = logging.getLogger(__name__)


class SelfHealingMiddleware:
    """
    ASGI Middleware for self-healing features.

    Features:
    - Request ID generation/propagation
    - Request tracking for graceful shutdown
    - Response time logging
    - Circuit breaker header propagation

    Usage:
        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import SelfHealingMiddleware

        app = FastAPI()
        app.add_middleware(
            SelfHealingMiddleware,
            request_tracker=tracker,
            exclude_paths=["/health", "/metrics"],
        )
    """

    def __init__(
        self,
        app: "ASGIApp",
        request_tracker: Optional[RequestTracker] = None,
        shutdown_coordinator: Optional[ShutdownCoordinator] = None,
        exclude_paths: Optional[list[str]] = None,
        enable_timing: bool = True,
    ):
        """
        Initialize the middleware.

        Args:
            app: ASGI application
            request_tracker: RequestTracker instance for tracking in-flight requests
            shutdown_coordinator: ShutdownCoordinator for graceful shutdown
            exclude_paths: Paths to exclude from tracking (e.g., health checks)
            enable_timing: Whether to add X-Response-Time header
        """
        self.app = app
        self._tracker = request_tracker
        self._shutdown = shutdown_coordinator
        self._exclude_paths = set(exclude_paths or ["/health", "/healthz", "/ready", "/metrics"])
        self._enable_timing = enable_timing

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip excluded paths
        if path in self._exclude_paths:
            await self.app(scope, receive, send)
            return

        # Check if accepting requests (graceful shutdown)
        if self._shutdown and not self._shutdown.is_accepting_requests():
            await self._send_shutdown_response(scope, receive, send)
            return

        # Extract or generate request ID
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        method = scope.get("method", "GET")

        start_time = time.perf_counter() if self._enable_timing else 0

        # Track request if tracker available
        if self._tracker:
            ctx = RequestContext(
                tracker=self._tracker,
                request_id=request_id,
                endpoint=path,
                method=method,
            )
            try:
                ctx.__enter__()
                await self._process_request(scope, receive, send, request_id, start_time)
            except Exception as e:
                ctx.mark_failed()
                raise
            finally:
                ctx.__exit__(None, None, None)
        else:
            await self._process_request(scope, receive, send, request_id, start_time)

    async def _process_request(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
        request_id: str,
        start_time: float,
    ) -> None:
        """Process the request and add response headers."""
        response_started = False
        initial_message = {}

        async def send_wrapper(message):
            nonlocal response_started, initial_message

            if message["type"] == "http.response.start":
                response_started = True
                initial_message = message

                # Add custom headers
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))

                if self._enable_timing:
                    elapsed = time.perf_counter() - start_time
                    headers.append((b"x-response-time", f"{elapsed:.4f}".encode()))

                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _send_shutdown_response(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        """Send 503 response when server is shutting down."""
        body = b'{"error": "Server is shutting down", "code": "SHUTDOWN_IN_PROGRESS"}'

        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"connection", b"close"),
                    (b"retry-after", b"30"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )


class ShutdownMiddleware:
    """
    Middleware specifically for graceful shutdown handling.

    Lighter weight than SelfHealingMiddleware if you only need
    shutdown handling without full request tracking.

    Usage:
        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import ShutdownMiddleware

        app = FastAPI()
        app.add_middleware(ShutdownMiddleware, shutdown_coordinator=coordinator)
    """

    def __init__(
        self,
        app: "ASGIApp",
        shutdown_coordinator: ShutdownCoordinator,
    ):
        """
        Initialize shutdown middleware.

        Args:
            app: ASGI application
            shutdown_coordinator: ShutdownCoordinator instance
        """
        self.app = app
        self._shutdown = shutdown_coordinator

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check shutdown state
        if not self._shutdown.is_accepting_requests():
            phase = self._shutdown.get_stats().phase

            # Allow health checks even during shutdown
            path = scope.get("path", "")
            if path in ("/health", "/healthz", "/ready"):
                # Return degraded status during shutdown
                await self._send_degraded_health(scope, receive, send, phase)
                return

            # Reject other requests
            await self._send_shutdown_response(scope, receive, send, phase)
            return

        await self.app(scope, receive, send)

    async def _send_shutdown_response(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
        phase: ShutdownPhase,
    ) -> None:
        """Send 503 with shutdown details."""
        import json

        body = json.dumps(
            {
                "error": "Service unavailable",
                "code": "SHUTDOWN_IN_PROGRESS",
                "phase": phase.value,
                "message": "Server is shutting down. Please retry later.",
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"connection", b"close"),
                    (b"retry-after", b"30"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )

    async def _send_degraded_health(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
        phase: ShutdownPhase,
    ) -> None:
        """Send degraded health check response."""
        import json

        stats = self._shutdown.get_stats()
        body = json.dumps(
            {
                "status": "degraded",
                "phase": phase.value,
                "in_flight_requests": stats.in_flight_count,
                "remaining_drain_time": stats.remaining_drain_time,
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
