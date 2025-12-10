"""
Web Framework Interface for the self-healing system.

This module defines the abstract interface for web framework adapters,
allowing framework-independent HTTP handling (Django, FastAPI, Flask, etc.)
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Type, TypeVar
from dataclasses import dataclass, field
from enum import Enum

T = TypeVar("T")


class HttpMethod(str, Enum):
    """HTTP methods"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


@dataclass
class RequestContext:
    """
    Framework-independent request context.

    Adapters convert framework-specific requests to this format.
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


@dataclass
class ResponseContext:
    """
    Framework-independent response context.

    Handlers return this, adapters convert to framework responses.
    """

    status_code: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, status_code: int = 200) -> "ResponseContext":
        """Create JSON response"""
        return cls(status_code=status_code, body=data)

    @classmethod
    def error(cls, message: str, status_code: int = 400, code: Optional[str] = None) -> "ResponseContext":
        """Create error response"""
        error_body = {"error": message, "success": False}
        if code:
            error_body["code"] = code
        return cls(status_code=status_code, body=error_body)

    @classmethod
    def created(cls, data: Any, location: Optional[str] = None) -> "ResponseContext":
        """Create 201 Created response"""
        headers = {"Location": location} if location else {}
        return cls(status_code=201, body=data, headers=headers)

    @classmethod
    def no_content(cls) -> "ResponseContext":
        """Create 204 No Content response"""
        return cls(status_code=204, body=None)

    @classmethod
    def not_found(cls, message: str = "Not found") -> "ResponseContext":
        """Create 404 Not Found response"""
        return cls.error(message, status_code=404, code="NOT_FOUND")

    @classmethod
    def unauthorized(cls, message: str = "Unauthorized") -> "ResponseContext":
        """Create 401 Unauthorized response"""
        return cls.error(message, status_code=401, code="UNAUTHORIZED")

    @classmethod
    def forbidden(cls, message: str = "Forbidden") -> "ResponseContext":
        """Create 403 Forbidden response"""
        return cls.error(message, status_code=403, code="FORBIDDEN")

    @classmethod
    def internal_error(cls, message: str = "Internal server error") -> "ResponseContext":
        """Create 500 Internal Server Error response"""
        return cls.error(message, status_code=500, code="INTERNAL_ERROR")


# Type alias for handler functions
HandlerFunc = Callable[[RequestContext], ResponseContext]


class WebFrameworkInterface(ABC):
    """
    Abstract interface for web framework adapters.

    Implementations:
        - DjangoRESTAdapter (current)
        - FastAPIAdapter (planned)
        - FlaskAdapter (planned)
    """

    @property
    @abstractmethod
    def framework_name(self) -> str:
        """Return the framework name (e.g., 'django', 'fastapi')"""
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
            prefix: URL prefix for all routes
            tags: OpenAPI tags for documentation

        Returns:
            Framework-specific router object
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
    ) -> None:
        """
        Add a route to the router.

        Args:
            router: Router from create_router
            path: URL path (can include path parameters like {id})
            method: HTTP method
            handler: Handler function (RequestContext -> ResponseContext)
            response_model: Pydantic/Serializer model for response
            summary: OpenAPI summary
            description: OpenAPI description
            auth_required: Require authenticated user
            permissions: Required permission codes
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
            parent: Parent router or app
            child: Child router to include
            prefix: Additional URL prefix
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
            options: Middleware configuration
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

    # =========================================================================
    # OpenAPI/Documentation
    # =========================================================================

    @abstractmethod
    def get_openapi_schema(self, app: Any) -> dict:
        """
        Get OpenAPI schema for the application.

        Returns:
            OpenAPI 3.0 schema dictionary
        """
        pass

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def wrap_handler(self, handler: HandlerFunc) -> Callable:
        """
        Wrap a framework-independent handler for use with the framework.

        Default implementation - subclasses may override.

        Args:
            handler: Framework-independent handler

        Returns:
            Framework-compatible handler
        """

        def wrapped(request: Any) -> Any:
            ctx = self.to_request_context(request)
            response = handler(ctx)
            return self.from_response_context(response)

        return wrapped
