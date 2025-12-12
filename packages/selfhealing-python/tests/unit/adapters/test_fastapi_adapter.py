"""
Unit tests for FastAPI adapter.

Tests:
- SelfHealingMiddleware
- ShutdownMiddleware
- Dependencies
- Routes
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone


class TestSelfHealingMiddleware:
    """Tests for SelfHealingMiddleware."""

    @pytest.fixture
    def mock_app(self):
        """Create mock ASGI app."""

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"message": "ok"}',
                }
            )

        return app

    @pytest.fixture
    def mock_tracker(self):
        """Create mock request tracker."""
        tracker = Mock()
        tracker.start_request = Mock(return_value=Mock())
        tracker.end_request = Mock()
        return tracker

    @pytest.fixture
    def mock_shutdown_coordinator(self):
        """Create mock shutdown coordinator."""
        coordinator = Mock()
        coordinator.is_accepting_requests = Mock(return_value=True)
        coordinator.get_stats = Mock(
            return_value=Mock(
                phase=Mock(value="running"),
                in_flight_count=0,
                remaining_drain_time=None,
            )
        )
        return coordinator

    @pytest.mark.asyncio
    async def test_middleware_passes_through_non_http(self, mock_app):
        """Test that non-HTTP requests pass through unchanged."""
        from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

        middleware = SelfHealingMiddleware(mock_app)

        scope = {"type": "websocket", "path": "/ws"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Should pass through to app
        send.assert_called()

    @pytest.mark.asyncio
    async def test_middleware_excludes_health_paths(self, mock_app, mock_tracker):
        """Test that health check paths are excluded from tracking."""
        from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

        middleware = SelfHealingMiddleware(
            mock_app,
            request_tracker=mock_tracker,
            exclude_paths=["/health", "/metrics"],
        )

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
        }
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Tracker should not be called for excluded paths
        mock_tracker.start_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_tracks_requests(self, mock_app, mock_tracker):
        """Test that requests are tracked."""
        from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

        middleware = SelfHealingMiddleware(
            mock_app,
            request_tracker=mock_tracker,
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/payments",
            "headers": [(b"x-request-id", b"test-123")],
        }
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Response should be sent
        assert send.call_count >= 2  # start and body

    @pytest.mark.asyncio
    async def test_middleware_rejects_during_shutdown(self, mock_app, mock_shutdown_coordinator):
        """Test that requests are rejected during shutdown."""
        from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

        mock_shutdown_coordinator.is_accepting_requests.return_value = False

        middleware = SelfHealingMiddleware(
            mock_app,
            shutdown_coordinator=mock_shutdown_coordinator,
        )

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/data",
            "headers": [],
        }
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Should send 503 response
        start_call = send.call_args_list[0]
        assert start_call[0][0]["status"] == 503

    @pytest.mark.asyncio
    async def test_middleware_adds_request_id_header(self, mock_app):
        """Test that X-Request-ID is added to response."""
        from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

        middleware = SelfHealingMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [(b"x-request-id", b"custom-id-123")],
        }
        receive = AsyncMock()
        captured_headers = []

        async def capture_send(message):
            if message["type"] == "http.response.start":
                captured_headers.extend(message.get("headers", []))

        await middleware(scope, receive, capture_send)

        # Check that x-request-id is in response headers
        header_names = [h[0] for h in captured_headers]
        assert b"x-request-id" in header_names


class TestShutdownMiddleware:
    """Tests for ShutdownMiddleware."""

    @pytest.fixture
    def mock_app(self):
        """Create mock ASGI app."""

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"ok",
                }
            )

        return app

    @pytest.fixture
    def mock_coordinator(self):
        """Create mock shutdown coordinator."""
        from selfhealing.core.shutdown_coordinator import ShutdownPhase

        coordinator = Mock()
        coordinator.is_accepting_requests = Mock(return_value=True)
        coordinator.get_stats = Mock(
            return_value=Mock(
                phase=ShutdownPhase.RUNNING,
                in_flight_count=0,
                remaining_drain_time=None,
            )
        )
        return coordinator

    @pytest.mark.asyncio
    async def test_allows_requests_when_running(self, mock_app, mock_coordinator):
        """Test that requests pass through when running normally."""
        from selfhealing.adapters.fastapi.middleware import ShutdownMiddleware

        middleware = ShutdownMiddleware(mock_app, mock_coordinator)

        scope = {"type": "http", "method": "GET", "path": "/api/test"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        assert send.call_count >= 2

    @pytest.mark.asyncio
    async def test_rejects_requests_during_shutdown(self, mock_app, mock_coordinator):
        """Test that requests are rejected during shutdown."""
        from selfhealing.adapters.fastapi.middleware import ShutdownMiddleware
        from selfhealing.core.shutdown_coordinator import ShutdownPhase

        mock_coordinator.is_accepting_requests.return_value = False
        mock_coordinator.get_stats.return_value.phase = ShutdownPhase.DRAINING

        middleware = ShutdownMiddleware(mock_app, mock_coordinator)

        scope = {"type": "http", "method": "GET", "path": "/api/test"}
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Should return 503
        start_call = send.call_args_list[0]
        assert start_call[0][0]["status"] == 503


class TestDependencies:
    """Tests for FastAPI dependencies."""

    def test_get_factory_returns_registry(self):
        """Test that get_factory returns ProviderRegistry."""
        from selfhealing.adapters.fastapi.dependencies import get_factory
        from selfhealing.factory import ProviderRegistry

        factory = get_factory()
        assert factory is ProviderRegistry

    def test_get_request_tracker_raises_when_not_configured(self):
        """Test that get_request_tracker raises when not configured."""
        from selfhealing.adapters.fastapi import dependencies

        # Reset global state
        dependencies._request_tracker = None
        dependencies._configured = False

        with pytest.raises(RuntimeError, match="not configured"):
            dependencies.get_request_tracker()

    def test_get_shutdown_coordinator_raises_when_not_configured(self):
        """Test that get_shutdown_coordinator raises when not configured."""
        from selfhealing.adapters.fastapi import dependencies

        # Reset global state
        dependencies._shutdown_coordinator = None
        dependencies._configured = False

        with pytest.raises(RuntimeError, match="not configured"):
            dependencies.get_shutdown_coordinator()


class TestRoutes:
    """Tests for FastAPI routes."""

    def test_create_selfhealing_router_returns_router(self):
        """Test that create_selfhealing_router returns a FastAPI router."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_selfhealing_router

        router = create_selfhealing_router()

        # Should be an APIRouter
        from fastapi import APIRouter

        assert isinstance(router, APIRouter)

    def test_router_has_health_routes(self):
        """Test that router includes health check routes."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_selfhealing_router

        router = create_selfhealing_router(include_health=True)

        # Get route paths
        route_paths = [route.path for route in router.routes]

        assert "/health" in route_paths
        assert "/ready" in route_paths

    def test_router_has_circuit_breaker_routes(self):
        """Test that router includes circuit breaker routes."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_selfhealing_router

        router = create_selfhealing_router(include_circuit_breaker=True)

        route_paths = [route.path for route in router.routes]

        assert "/circuit-breakers" in route_paths
        assert "/circuit-breakers/{service_name}" in route_paths

    def test_router_has_dlq_routes(self):
        """Test that router includes DLQ routes."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_selfhealing_router

        router = create_selfhealing_router(include_dlq=True)

        route_paths = [route.path for route in router.routes]

        assert "/dlq" in route_paths
        assert "/dlq/{operation_id}" in route_paths

    def test_router_can_exclude_features(self):
        """Test that router features can be excluded."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_selfhealing_router

        router = create_selfhealing_router(
            include_circuit_breaker=False,
            include_dlq=False,
            include_shutdown=False,
            include_health=True,
        )

        route_paths = [route.path for route in router.routes]

        # Only health routes should be present
        assert "/health" in route_paths
        assert "/circuit-breakers" not in route_paths
        assert "/dlq" not in route_paths
        assert "/shutdown/status" not in route_paths

    def test_create_health_router(self):
        """Test create_health_router utility."""
        pytest.importorskip("fastapi")

        from selfhealing.adapters.fastapi.routes import create_health_router

        router = create_health_router()

        route_paths = [route.path for route in router.routes]

        assert "/health" in route_paths
        assert "/ready" in route_paths


class TestIntegration:
    """Integration tests with actual FastAPI app."""

    @pytest.fixture
    def app(self):
        """Create test FastAPI app."""
        fastapi = pytest.importorskip("fastapi")

        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import create_selfhealing_router

        app = FastAPI()
        app.include_router(create_selfhealing_router(), prefix="/api/selfhealing")

        @app.get("/")
        async def root():
            return {"message": "Hello"}

        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        starlette = pytest.importorskip("starlette")
        from starlette.testclient import TestClient

        return TestClient(app)

    def test_health_endpoint(self, client):
        """Test health endpoint returns 200."""
        response = client.get("/api/selfhealing/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert "timestamp" in data

    def test_ready_endpoint(self, client):
        """Test ready endpoint returns 200."""
        response = client.get("/api/selfhealing/ready")
        assert response.status_code == 200

    def test_circuit_breakers_list_endpoint(self, client):
        """Test circuit breakers list endpoint."""
        response = client.get("/api/selfhealing/circuit-breakers")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_dlq_list_endpoint(self, client):
        """Test DLQ list endpoint."""
        response = client.get("/api/selfhealing/dlq")
        assert response.status_code == 200

        data = response.json()
        assert "items" in data
        assert "total" in data


class TestConfigureFastAPI:
    """Tests for configure_fastapi_selfhealing."""

    def test_configure_returns_tracker_and_coordinator(self):
        """Test that configure_fastapi_selfhealing returns correct objects."""
        fastapi = pytest.importorskip("fastapi")

        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import (
            configure_fastapi_selfhealing,
        )
        from selfhealing.core.shutdown_coordinator import (
            RequestTracker,
            GracefulShutdownCoordinator,
        )
        from selfhealing.adapters.fastapi import dependencies

        # Reset global state
        dependencies._request_tracker = None
        dependencies._shutdown_coordinator = None
        dependencies._configured = False

        app = FastAPI()
        tracker, coordinator = configure_fastapi_selfhealing(
            app,
            drain_timeout=30.0,
            enable_middleware=False,  # Don't add middleware in test
        )

        assert isinstance(tracker, RequestTracker)
        assert isinstance(coordinator, GracefulShutdownCoordinator)
        assert dependencies._configured is True

    def test_configure_sets_global_state(self):
        """Test that configuration sets module-level globals."""
        fastapi = pytest.importorskip("fastapi")

        from fastapi import FastAPI
        from selfhealing.adapters.fastapi import (
            configure_fastapi_selfhealing,
            get_request_tracker,
            get_shutdown_coordinator,
        )
        from selfhealing.adapters.fastapi import dependencies

        # Reset global state
        dependencies._request_tracker = None
        dependencies._shutdown_coordinator = None
        dependencies._configured = False

        app = FastAPI()
        configure_fastapi_selfhealing(app, enable_middleware=False)

        # Should not raise
        tracker = get_request_tracker()
        coordinator = get_shutdown_coordinator()

        assert tracker is not None
        assert coordinator is not None
