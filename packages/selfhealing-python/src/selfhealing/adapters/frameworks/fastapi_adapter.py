"""
FastAPI Framework Adapter for the self-healing system.

Implements WebFrameworkInterface using FastAPI as the web framework.
Supports async handlers, dependency injection, and automatic OpenAPI docs.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional, Type, TypeVar

from selfhealing.interfaces.web_framework import (
    WebFrameworkInterface,
    RequestContext,
    ResponseContext,
    HttpMethod,
    HandlerFunc,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FastAPIAdapter(WebFrameworkInterface):
    """
    FastAPI implementation of WebFrameworkInterface.

    Uses FastAPI's APIRouter for routing and Pydantic for validation.
    Supports both sync and async handlers.

    Usage:
        from fastapi import FastAPI
        from selfhealing.adapters.frameworks import FastAPIAdapter

        app = FastAPI()
        adapter = FastAPIAdapter()

        router = adapter.create_router(prefix="/api/v1", tags=["payments"])

        def handle_payment(ctx: RequestContext) -> ResponseContext:
            payment_id = ctx.path_params.get("id")
            return ResponseContext.json({"id": payment_id})

        adapter.add_route(
            router,
            path="/payments/{id}",
            method=HttpMethod.GET,
            handler=handle_payment,
        )

        app.include_router(router)
    """

    def __init__(self):
        """Initialize the FastAPI adapter."""
        self._fastapi = None
        self._pydantic = None

    @property
    def fastapi(self):
        """Get FastAPI module."""
        if self._fastapi is None:
            try:
                import fastapi

                self._fastapi = fastapi
            except ImportError:
                raise ImportError("fastapi is required for FastAPIAdapter. " "Install it with: pip install fastapi")
        return self._fastapi

    @property
    def framework_name(self) -> str:
        """Return the framework name."""
        return "fastapi"

    # =========================================================================
    # Routing
    # =========================================================================

    def create_router(
        self,
        prefix: str = "",
        tags: Optional[list[str]] = None,
    ) -> Any:
        """
        Create a FastAPI APIRouter.

        Args:
            prefix: URL prefix for all routes
            tags: OpenAPI tags for documentation

        Returns:
            fastapi.APIRouter instance
        """
        return self.fastapi.APIRouter(
            prefix=prefix,
            tags=tags or [],
        )

    def add_route(
        self,
        router: Any,
        path: str,
        method: HttpMethod,
        handler: HandlerFunc,
        response_model: Optional[Type] = None,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        auth_required: bool = True,
        permissions: Optional[list[str]] = None,
    ) -> None:
        """
        Add a route to the FastAPI router.

        Args:
            router: APIRouter from create_router
            path: URL path with path parameters (e.g., "/items/{item_id}")
            method: HTTP method
            handler: Framework-independent handler function
            response_model: Optional Pydantic model for response validation
            summary: OpenAPI summary
            description: OpenAPI description
            auth_required: Whether authentication is required
            permissions: Required permission codes
        """
        # Create wrapped handler
        wrapped = self._create_fastapi_handler(handler, auth_required, permissions)

        # Build route options
        route_options = {
            "summary": summary,
            "description": description,
        }

        if response_model:
            route_options["response_model"] = response_model

        # Add route based on method
        method_map = {
            HttpMethod.GET: router.get,
            HttpMethod.POST: router.post,
            HttpMethod.PUT: router.put,
            HttpMethod.PATCH: router.patch,
            HttpMethod.DELETE: router.delete,
            HttpMethod.HEAD: router.head,
            HttpMethod.OPTIONS: router.options,
        }

        route_decorator = method_map.get(method)
        if route_decorator:
            route_decorator(path, **route_options)(wrapped)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

    def _create_fastapi_handler(
        self,
        handler: HandlerFunc,
        auth_required: bool,
        permissions: Optional[list[str]],
    ) -> Callable:
        """
        Create a FastAPI-compatible handler from a framework-independent handler.

        Args:
            handler: Framework-independent handler
            auth_required: Whether authentication is required
            permissions: Required permission codes

        Returns:
            FastAPI-compatible async handler
        """
        from fastapi import Request, Response, HTTPException, Depends

        async def fastapi_handler(
            request: Request,
        ) -> Response:
            try:
                # Convert request to context
                ctx = await self._to_request_context_async(request)

                # Check authentication if required
                if auth_required and not ctx.is_authenticated:
                    raise HTTPException(status_code=401, detail="Not authenticated")

                # Check permissions if required
                if permissions and ctx.user:
                    user_perms = getattr(ctx.user, "permissions", set())
                    if not all(p in user_perms for p in permissions):
                        raise HTTPException(status_code=403, detail="Insufficient permissions")

                # Call the handler
                response_ctx = handler(ctx)

                # Convert response
                return self.from_response_context(response_ctx)

            except HTTPException:
                raise
            except Exception as e:
                logger.exception(f"[FastAPI] Handler error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        return fastapi_handler

    def include_router(
        self,
        parent: Any,
        child: Any,
        prefix: str = "",
    ) -> None:
        """
        Include a child router in parent application or router.

        Args:
            parent: FastAPI app or APIRouter
            child: APIRouter to include
            prefix: Additional URL prefix
        """
        parent.include_router(child, prefix=prefix)

    # =========================================================================
    # Request/Response Conversion
    # =========================================================================

    def to_request_context(self, request: Any) -> RequestContext:
        """
        Convert FastAPI Request to RequestContext (sync version).

        Note: For async body reading, use _to_request_context_async.
        """
        from fastapi import Request

        if not isinstance(request, Request):
            raise TypeError(f"Expected fastapi.Request, got {type(request)}")

        return RequestContext(
            method=HttpMethod(request.method),
            path=str(request.url.path),
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            path_params=dict(request.path_params),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_id=request.headers.get("x-request-id"),
            user=getattr(request.state, "user", None),
            is_authenticated=getattr(request.state, "is_authenticated", False),
        )

    async def _to_request_context_async(self, request: Any) -> RequestContext:
        """
        Convert FastAPI Request to RequestContext (async version).

        Reads body asynchronously.
        """
        from fastapi import Request

        if not isinstance(request, Request):
            raise TypeError(f"Expected fastapi.Request, got {type(request)}")

        # Read body
        body = await request.body()
        json_body = None

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type and body:
            try:
                json_body = await request.json()
            except Exception:
                pass

        return RequestContext(
            method=HttpMethod(request.method),
            path=str(request.url.path),
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            path_params=dict(request.path_params),
            body=body,
            json_body=json_body,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_id=request.headers.get("x-request-id"),
            user=getattr(request.state, "user", None),
            is_authenticated=getattr(request.state, "is_authenticated", False),
        )

    def from_response_context(self, response: ResponseContext) -> Any:
        """
        Convert ResponseContext to FastAPI Response.

        Args:
            response: Framework-independent ResponseContext

        Returns:
            fastapi.responses.JSONResponse or Response
        """
        from fastapi.responses import JSONResponse, Response

        if response.body is None:
            return Response(
                status_code=response.status_code,
                headers=response.headers or None,
            )

        return JSONResponse(
            content=response.body,
            status_code=response.status_code,
            headers=response.headers or None,
        )

    # =========================================================================
    # Middleware
    # =========================================================================

    def add_middleware(
        self,
        app: Any,
        middleware_class: Type,
        **options,
    ) -> None:
        """
        Add middleware to FastAPI application.

        Args:
            app: FastAPI application instance
            middleware_class: Middleware class (Starlette-compatible)
            options: Middleware configuration
        """
        app.add_middleware(middleware_class, **options)

    # =========================================================================
    # Authentication
    # =========================================================================

    def get_current_user(self, request: Any) -> Optional[Any]:
        """
        Get authenticated user from FastAPI request.

        Args:
            request: FastAPI Request

        Returns:
            User object or None
        """
        return getattr(request.state, "user", None)

    def require_auth(self) -> Callable:
        """
        Get FastAPI dependency for authentication.

        Returns:
            Dependency that raises HTTPException if not authenticated
        """
        from fastapi import Request, HTTPException, Depends

        async def auth_dependency(request: Request):
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="Not authenticated")
            return user

        return Depends(auth_dependency)

    def require_permissions(self, permissions: list[str]) -> Callable:
        """
        Get FastAPI dependency for permission checking.

        Args:
            permissions: Required permission codes

        Returns:
            Dependency that raises HTTPException if permissions not met
        """
        from fastapi import Request, HTTPException, Depends

        async def permission_dependency(request: Request):
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="Not authenticated")

            user_perms = getattr(user, "permissions", set())
            missing = [p for p in permissions if p not in user_perms]
            if missing:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing permissions: {', '.join(missing)}",
                )
            return user

        return Depends(permission_dependency)

    # =========================================================================
    # OpenAPI/Documentation
    # =========================================================================

    def get_openapi_schema(self, app: Any) -> dict:
        """
        Get OpenAPI schema from FastAPI application.

        Returns:
            OpenAPI 3.0 schema dictionary
        """
        return app.openapi()

    # =========================================================================
    # Additional FastAPI-specific Methods
    # =========================================================================

    def create_app(
        self,
        title: str = "Self-Healing API",
        description: str = "",
        version: str = "1.0.0",
        **kwargs,
    ) -> Any:
        """
        Create a new FastAPI application.

        Args:
            title: API title
            description: API description
            version: API version
            **kwargs: Additional FastAPI constructor arguments

        Returns:
            FastAPI application instance
        """
        return self.fastapi.FastAPI(
            title=title,
            description=description,
            version=version,
            **kwargs,
        )

    def add_exception_handler(
        self,
        app: Any,
        exception_class: Type[Exception],
        handler: Callable,
    ) -> None:
        """
        Add custom exception handler to FastAPI app.

        Args:
            app: FastAPI application
            exception_class: Exception type to handle
            handler: Exception handler function
        """
        app.add_exception_handler(exception_class, handler)

    def add_cors_middleware(
        self,
        app: Any,
        allow_origins: list[str] = None,
        allow_methods: list[str] = None,
        allow_headers: list[str] = None,
        allow_credentials: bool = True,
    ) -> None:
        """
        Add CORS middleware to FastAPI app.

        Args:
            app: FastAPI application
            allow_origins: Allowed origins (default: ["*"])
            allow_methods: Allowed methods (default: ["*"])
            allow_headers: Allowed headers (default: ["*"])
            allow_credentials: Allow credentials
        """
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins or ["*"],
            allow_credentials=allow_credentials,
            allow_methods=allow_methods or ["*"],
            allow_headers=allow_headers or ["*"],
        )
