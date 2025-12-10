"""
Web Framework Interface for Self-Healing System

Abstract interface for HTTP routing and request/response handling.
Enables framework migration (Django REST -> FastAPI, Flask, etc.).

Design Principles:
1. Pure Python - no framework dependencies
2. Dataclasses for immutable request/response contexts
3. ABC for framework adapter contracts
4. OpenAPI/documentation support

Reference: docs/PLUGGABLE_ARCHITECTURE.md Section 3.4
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Type, TypeVar

T = TypeVar("T")


# ============================================================================
# Enums
# ============================================================================


class HttpMethod(str, Enum):
    """HTTP methods"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ContentType(str, Enum):
    """Common content types"""

    JSON = "application/json"
    FORM = "application/x-www-form-urlencoded"
    MULTIPART = "multipart/form-data"
    TEXT = "text/plain"
    HTML = "text/html"
    XML = "application/xml"


# ============================================================================
# Data Transfer Objects (DTOs)
# ============================================================================


@dataclass
class RequestContext:
    """
    Framework-independent request context.

    Adapters convert framework-specific requests to this format,
    enabling handlers to work with any web framework.

    Attributes:
        method: HTTP method
        path: Request path (without query string)
        headers: Request headers (case-insensitive keys)
        query_params: Query string parameters
        path_params: URL path parameters (e.g., {id})
        body: Raw request body bytes
        json_body: Parsed JSON body (if applicable)
        user: Authenticated user object (framework-specific)
        is_authenticated: Whether user is authenticated
        client_ip: Client IP address
        user_agent: User-Agent header value
        request_id: Unique request identifier for tracing
        content_type: Content-Type header value
    """

    method: HttpMethod
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, Any] = field(default_factory=dict)
    path_params: dict[str, Any] = field(default_factory=dict)
    body: Optional[bytes] = None
    json_body: Optional[dict] = None
    user: Optional[Any] = None
    is_authenticated: bool = False

    # Request metadata
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    content_type: Optional[str] = None

    def get_header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get header value (case-insensitive).

        Args:
            name: Header name
            default: Default value if not found

        Returns:
            Header value or default
        """
        # Headers are case-insensitive per HTTP spec
        name_lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == name_lower:
                return value
        return default

    def get_query(self, name: str, default: Any = None) -> Any:
        """
        Get query parameter value.

        Args:
            name: Parameter name
            default: Default value if not found

        Returns:
            Parameter value or default
        """
        return self.query_params.get(name, default)

    def get_path_param(self, name: str, default: Any = None) -> Any:
        """
        Get path parameter value.

        Args:
            name: Parameter name
            default: Default value if not found

        Returns:
            Parameter value or default
        """
        return self.path_params.get(name, default)

    @property
    def is_json(self) -> bool:
        """Check if request has JSON content."""
        content_type = self.content_type or self.get_header("Content-Type", "")
        return "application/json" in content_type.lower()


@dataclass
class ResponseContext:
    """
    Framework-independent response context.

    Handlers return this, adapters convert to framework responses.

    Attributes:
        status_code: HTTP status code
        body: Response body (will be JSON-encoded if dict/list)
        headers: Response headers
        content_type: Content-Type header
    """

    status_code: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = ContentType.JSON.value

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @classmethod
    def json(
        cls,
        data: Any,
        status_code: int = 200,
        headers: Optional[dict[str, str]] = None,
    ) -> "ResponseContext":
        """
        Create JSON response.

        Args:
            data: Data to JSON-encode
            status_code: HTTP status code
            headers: Additional headers

        Returns:
            ResponseContext with JSON content
        """
        return cls(
            status_code=status_code,
            body=data,
            headers=headers or {},
            content_type=ContentType.JSON.value,
        )

    @classmethod
    def error(
        cls,
        message: str,
        status_code: int = 400,
        error_code: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> "ResponseContext":
        """
        Create error response.

        Args:
            message: Error message
            status_code: HTTP status code
            error_code: Machine-readable error code
            details: Additional error details

        Returns:
            ResponseContext with error body
        """
        body = {
            "success": False,
            "error": message,
        }
        if error_code:
            body["error_code"] = error_code
        if details:
            body["details"] = details

        return cls(status_code=status_code, body=body)

    @classmethod
    def success(
        cls,
        data: Any = None,
        message: Optional[str] = None,
        status_code: int = 200,
    ) -> "ResponseContext":
        """
        Create success response.

        Args:
            data: Response data
            message: Success message
            status_code: HTTP status code

        Returns:
            ResponseContext with success body
        """
        body = {"success": True}
        if data is not None:
            body["data"] = data
        if message:
            body["message"] = message

        return cls(status_code=status_code, body=body)

    @classmethod
    def created(cls, data: Any, location: Optional[str] = None) -> "ResponseContext":
        """
        Create 201 Created response.

        Args:
            data: Created resource data
            location: Location header value (URL of new resource)

        Returns:
            ResponseContext with 201 status
        """
        headers = {}
        if location:
            headers["Location"] = location
        return cls(status_code=201, body=data, headers=headers)

    @classmethod
    def no_content(cls) -> "ResponseContext":
        """
        Create 204 No Content response.

        Returns:
            ResponseContext with 204 status and no body
        """
        return cls(status_code=204, body=None)

    @classmethod
    def not_found(cls, message: str = "Resource not found") -> "ResponseContext":
        """
        Create 404 Not Found response.

        Args:
            message: Error message

        Returns:
            ResponseContext with 404 status
        """
        return cls.error(message, status_code=404, error_code="NOT_FOUND")

    @classmethod
    def unauthorized(cls, message: str = "Authentication required") -> "ResponseContext":
        """
        Create 401 Unauthorized response.

        Args:
            message: Error message

        Returns:
            ResponseContext with 401 status
        """
        return cls.error(message, status_code=401, error_code="UNAUTHORIZED")

    @classmethod
    def forbidden(cls, message: str = "Permission denied") -> "ResponseContext":
        """
        Create 403 Forbidden response.

        Args:
            message: Error message

        Returns:
            ResponseContext with 403 status
        """
        return cls.error(message, status_code=403, error_code="FORBIDDEN")

    @classmethod
    def bad_request(
        cls,
        message: str = "Bad request",
        errors: Optional[dict] = None,
    ) -> "ResponseContext":
        """
        Create 400 Bad Request response.

        Args:
            message: Error message
            errors: Validation errors dict

        Returns:
            ResponseContext with 400 status
        """
        return cls.error(
            message,
            status_code=400,
            error_code="BAD_REQUEST",
            details=errors,
        )

    @classmethod
    def server_error(cls, message: str = "Internal server error") -> "ResponseContext":
        """
        Create 500 Internal Server Error response.

        Args:
            message: Error message

        Returns:
            ResponseContext with 500 status
        """
        return cls.error(message, status_code=500, error_code="INTERNAL_ERROR")

    @classmethod
    def redirect(
        cls,
        url: str,
        permanent: bool = False,
    ) -> "ResponseContext":
        """
        Create redirect response.

        Args:
            url: Redirect URL
            permanent: If True, use 301, else 302

        Returns:
            ResponseContext with redirect
        """
        status = 301 if permanent else 302
        return cls(
            status_code=status,
            body=None,
            headers={"Location": url},
        )


# Type alias for handler functions
HandlerFunc = Callable[[RequestContext], ResponseContext]


# ============================================================================
# Exceptions
# ============================================================================


class WebFrameworkError(Exception):
    """Base exception for web framework errors."""

    pass


class RouteNotFoundError(WebFrameworkError):
    """Raised when a route is not found."""

    pass


class AuthenticationError(WebFrameworkError):
    """Raised when authentication fails."""

    pass


class PermissionDeniedError(WebFrameworkError):
    """Raised when permission check fails."""

    pass


# ============================================================================
# Web Framework Interface
# ============================================================================


class WebFrameworkInterface(ABC):
    """
    Abstract interface for web framework adapters.

    This interface defines the contract for HTTP framework
    adapters, enabling the self-healing system to work with
    different web frameworks interchangeably.

    Implementations:
        - DjangoRESTAdapter (current)
        - FastAPIAdapter (planned)
        - FlaskAdapter (planned)

    Example:
        >>> framework = ProviderRegistry.get_framework()
        >>> router = framework.create_router(prefix="/api/v1")
        >>>
        >>> def handle_status(request: RequestContext) -> ResponseContext:
        ...     return ResponseContext.json({"status": "healthy"})
        >>>
        >>> framework.add_route(
        ...     router=router,
        ...     path="/health",
        ...     method=HttpMethod.GET,
        ...     handler=handle_status,
        ...     auth_required=False,
        ... )
    """

    @property
    @abstractmethod
    def framework_name(self) -> str:
        """
        Return the framework name.

        Returns:
            Framework identifier (e.g., 'django', 'fastapi', 'flask')
        """
        pass

    # =========================================================================
    # Routing
    # =========================================================================

    @abstractmethod
    def create_router(
        self,
        prefix: str = "",
        tags: Optional[list[str]] = None,
    ) -> Any:
        """
        Create a router/blueprint for grouping routes.

        Args:
            prefix: URL prefix for all routes in this router
            tags: OpenAPI tags for documentation

        Returns:
            Framework-specific router object

        Example:
            >>> router = framework.create_router(
            ...     prefix="/api/v1/payments",
            ...     tags=["payments"],
            ... )
        """
        pass

    @abstractmethod
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
        deprecated: bool = False,
    ) -> None:
        """
        Add a route to the router.

        Args:
            router: Router from create_router
            path: URL path (can include path parameters like {id})
            method: HTTP method
            handler: Handler function (RequestContext -> ResponseContext)
            response_model: Pydantic/Serializer model for response
            summary: OpenAPI summary (short description)
            description: OpenAPI description (detailed)
            auth_required: Require authenticated user
            permissions: Required permission codes
            deprecated: Mark route as deprecated

        Example:
            >>> framework.add_route(
            ...     router=router,
            ...     path="/{id}",
            ...     method=HttpMethod.GET,
            ...     handler=get_payment,
            ...     summary="Get payment by ID",
            ...     permissions=["payments.view"],
            ... )
        """
        pass

    @abstractmethod
    def include_router(
        self,
        parent: Any,
        child: Any,
        prefix: str = "",
    ) -> None:
        """
        Include a child router in parent.

        Args:
            parent: Parent router or application
            child: Child router to include
            prefix: Additional URL prefix

        Example:
            >>> app = framework.create_app()
            >>> framework.include_router(app, payment_router, prefix="/payments")
        """
        pass

    # =========================================================================
    # Request/Response Conversion
    # =========================================================================

    @abstractmethod
    def to_request_context(self, request: Any) -> RequestContext:
        """
        Convert framework request to RequestContext.

        Args:
            request: Framework-specific request object

        Returns:
            Normalized RequestContext
        """
        pass

    @abstractmethod
    def from_response_context(self, response: ResponseContext) -> Any:
        """
        Convert ResponseContext to framework response.

        Args:
            response: Framework-independent ResponseContext

        Returns:
            Framework-specific response object
        """
        pass

    def wrap_handler(self, handler: HandlerFunc) -> Callable:
        """
        Wrap a handler function for the framework.

        Converts between framework request/response and our contexts.

        Args:
            handler: Handler using RequestContext/ResponseContext

        Returns:
            Framework-compatible handler function
        """

        def wrapped(request: Any) -> Any:
            request_ctx = self.to_request_context(request)
            response_ctx = handler(request_ctx)
            return self.from_response_context(response_ctx)

        return wrapped

    # =========================================================================
    # Middleware
    # =========================================================================

    @abstractmethod
    def add_middleware(
        self,
        app: Any,
        middleware_class: Type,
        **options,
    ) -> None:
        """
        Add middleware to application.

        Args:
            app: Application instance
            middleware_class: Middleware class
            **options: Middleware configuration options

        Example:
            >>> framework.add_middleware(
            ...     app,
            ...     CORSMiddleware,
            ...     allow_origins=["*"],
            ... )
        """
        pass

    def add_exception_handler(
        self,
        app: Any,
        exception_class: Type[Exception],
        handler: Callable[[Any, Exception], ResponseContext],
    ) -> None:
        """
        Add exception handler to application.

        Args:
            app: Application instance
            exception_class: Exception type to handle
            handler: Handler function (request, exception) -> ResponseContext
        """
        pass

    # =========================================================================
    # Authentication
    # =========================================================================

    @abstractmethod
    def get_current_user(self, request: Any) -> Optional[Any]:
        """
        Get authenticated user from request.

        Args:
            request: Framework-specific request

        Returns:
            User object or None if not authenticated
        """
        pass

    @abstractmethod
    def require_auth(self) -> Callable:
        """
        Get authentication dependency/decorator.

        Returns:
            Callable that enforces authentication

        Usage depends on framework:
            - Django: Permission class
            - FastAPI: Dependency
            - Flask: Decorator
        """
        pass

    @abstractmethod
    def require_permissions(self, permissions: list[str]) -> Callable:
        """
        Get permission checking dependency/decorator.

        Args:
            permissions: Required permission codes

        Returns:
            Callable that enforces permissions
        """
        pass

    def check_permission(
        self,
        user: Any,
        permission: str,
    ) -> bool:
        """
        Check if user has a specific permission.

        Args:
            user: User object
            permission: Permission code to check

        Returns:
            True if user has permission
        """
        if user is None:
            return False

        # Default implementation - override for framework-specific
        if hasattr(user, "has_perm"):
            return user.has_perm(permission)
        return False

    # =========================================================================
    # OpenAPI/Documentation
    # =========================================================================

    @abstractmethod
    def get_openapi_schema(self, app: Any) -> dict:
        """
        Get OpenAPI schema for the application.

        Args:
            app: Application instance

        Returns:
            OpenAPI 3.0 schema dictionary
        """
        pass

    def get_routes(self, app: Any) -> list[dict]:
        """
        Get list of registered routes.

        Args:
            app: Application instance

        Returns:
            List of route info dicts with path, method, name
        """
        return []

    # =========================================================================
    # Utilities
    # =========================================================================

    def create_app(self, **options) -> Any:
        """
        Create a new application instance.

        Args:
            **options: Framework-specific options

        Returns:
            Application instance
        """
        raise NotImplementedError(f"{self.framework_name} adapter does not support create_app")

    def run_server(
        self,
        app: Any,
        host: str = "0.0.0.0",
        port: int = 8000,
        debug: bool = False,
    ) -> None:
        """
        Run development server.

        Args:
            app: Application instance
            host: Host to bind
            port: Port to listen on
            debug: Enable debug mode

        Note:
            For development only. Use proper WSGI/ASGI server in production.
        """
        raise NotImplementedError(f"{self.framework_name} adapter does not support run_server")
