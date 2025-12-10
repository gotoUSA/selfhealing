# Pluggable Architecture for Self-Healing System

> **Created**: 2025-12-10
> **Status**: Design Phase
> **Goal**: Make Self-Healing components independently replaceable

---

## 📋 Table of Contents

1. [Overview](#1-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Interface Definitions](#3-interface-definitions)
4. [Adapter Implementations](#4-adapter-implementations)
5. [Factory Pattern](#5-factory-pattern)
6. [Package Structure](#6-package-structure)
7. [Implementation Roadmap](#7-implementation-roadmap)
8. [Migration Guide](#8-migration-guide)

---

## 1. Overview

### Problem Statement

The current Self-Healing system is tightly coupled to:
- **Toss Payments** - Korean payment gateway
- **Redis** - Cache and distributed state
- **Celery** - Async task queue
- **Django** - Web framework

This coupling makes it difficult to:
- Switch payment providers (e.g., Stripe, Iamport)
- Use alternative caches (e.g., Memcached, DynamoDB)
- Replace task queues (e.g., RQ, Dramatiq)
- Migrate to FastAPI or other frameworks

### Solution: Pluggable Architecture

Introduce **abstract interfaces** for each external dependency, allowing implementations to be swapped without modifying core business logic.

### Design Principles

1. **Dependency Inversion** - Core depends on abstractions, not concrete implementations
2. **Interface Segregation** - Small, focused interfaces
3. **Factory Pattern** - Centralized component creation
4. **Pure Python Core** - No framework imports in core modules

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Self-Healing Core (Pure Python)                     │
│                                                                          │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐ ┌─────────────────┐   │
│  │ DLQService  │ │ReplayService│ │CircuitBreaker│ │ RetryHandler    │   │
│  └──────┬──────┘ └──────┬──────┘ └──────┬───────┘ └────────┬────────┘   │
│         │               │               │                   │            │
│  ═══════╧═══════════════╧═══════════════╧═══════════════════╧══════════  │
│                              Interfaces (ABC)                            │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐            │
│  │ Repository │ │  Payment   │ │   Cache    │ │ TaskQueue  │            │
│  │ Interface  │ │ Interface  │ │ Interface  │ │ Interface  │            │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘            │
└────────┼──────────────┼──────────────┼──────────────┼────────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            Adapters Layer                                │
├─────────────────┬────────────────┬────────────────┬─────────────────────┤
│   Repository    │    Payment     │     Cache      │     TaskQueue       │
├─────────────────┼────────────────┼────────────────┼─────────────────────┤
│ ✅ Django ORM   │ ✅ Toss        │ ✅ Redis       │ ✅ Celery           │
│ ⬜ SQLAlchemy   │ ⬜ Stripe      │ ⬜ Memcached   │ ⬜ RQ (Redis Queue) │
│ ⬜ MongoDB      │ ⬜ Iamport     │ ⬜ DynamoDB    │ ⬜ Dramatiq         │
│                 │ ⬜ KakaoPay    │ ⬜ In-Memory   │ ⬜ Huey             │
└─────────────────┴────────────────┴────────────────┴─────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                        Web Framework Adapters                            │
├───────────────────────┬───────────────────────┬─────────────────────────┤
│ ✅ Django REST        │ ⬜ FastAPI            │ ⬜ Flask                │
└───────────────────────┴───────────────────────┴─────────────────────────┘

Legend: ✅ Implemented  ⬜ Planned
```

---

## 3. Interface Definitions

### 3.1 Payment Provider Interface

Abstracts payment gateway operations for multi-provider support.

```python
# interfaces/payment_provider.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from decimal import Decimal


@dataclass
class PaymentConfirmResult:
    """Result of payment confirmation attempt"""
    success: bool
    payment_key: Optional[str] = None
    transaction_id: Optional[str] = None
    approved_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class PaymentCancelResult:
    """Result of payment cancellation/refund"""
    success: bool
    cancel_key: Optional[str] = None
    refund_amount: Optional[Decimal] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class WebhookVerifyResult:
    """Result of webhook signature verification"""
    valid: bool
    event_type: Optional[str] = None
    payload: Optional[dict] = None
    error_message: Optional[str] = None


class PaymentProviderInterface(ABC):
    """
    Abstract interface for payment providers.

    Implementations:
        - TossPaymentAdapter (current)
        - StripePaymentAdapter (planned)
        - IamportPaymentAdapter (planned)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'toss', 'stripe')"""
        pass

    @abstractmethod
    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        """
        Confirm/capture a payment.

        Args:
            payment_key: Provider-specific payment identifier
            order_id: Internal order ID
            amount: Payment amount to confirm
            idempotency_key: Optional key for idempotent requests

        Returns:
            PaymentConfirmResult with success status and details
        """
        pass

    @abstractmethod
    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        """
        Cancel or refund a payment.

        Args:
            payment_key: Provider-specific payment identifier
            cancel_reason: Human-readable cancellation reason
            cancel_amount: Partial refund amount (None = full refund)
            idempotency_key: Optional key for idempotent requests

        Returns:
            PaymentCancelResult with refund status and details
        """
        pass

    @abstractmethod
    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        """
        Verify webhook signature for security.

        Args:
            payload: Raw request body bytes
            signature: Signature header value
            timestamp: Optional timestamp for replay protection

        Returns:
            WebhookVerifyResult with validation status
        """
        pass

    @abstractmethod
    def get_payment_status(
        self,
        payment_key: str,
    ) -> dict:
        """
        Query current payment status from provider.

        Args:
            payment_key: Provider-specific payment identifier

        Returns:
            Provider-specific status dictionary
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if payment provider API is reachable.

        Returns:
            True if provider is healthy
        """
        pass
```

### 3.2 Cache Provider Interface

Abstracts cache operations including distributed locking for circuit breakers.

```python
# interfaces/cache_provider.py
from abc import ABC, abstractmethod
from typing import Any, Optional, TypeVar, Generic
from datetime import timedelta
from contextlib import contextmanager

T = TypeVar('T')


class DistributedLock(ABC):
    """
    Distributed lock interface for cross-process synchronization.

    Used by CircuitBreaker for state transitions.
    """

    @abstractmethod
    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, block until lock acquired
            timeout: Max seconds to wait (None = infinite)

        Returns:
            True if lock acquired, False otherwise
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """Release the lock."""
        pass

    @abstractmethod
    def locked(self) -> bool:
        """Check if lock is currently held."""
        pass

    def __enter__(self) -> "DistributedLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class CacheProviderInterface(ABC):
    """
    Abstract interface for cache/state storage.

    Implementations:
        - RedisCacheAdapter (current)
        - MemcachedCacheAdapter (planned)
        - InMemoryCacheAdapter (for testing)
        - DynamoDBCacheAdapter (planned)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'redis', 'memcached')"""
        pass

    # =========================================================================
    # Basic Operations
    # =========================================================================

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """
        Get value by key.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        pass

    @abstractmethod
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """
        Set value with optional TTL.

        Args:
            key: Cache key
            value: Value to cache (must be serializable)
            ttl: Time-to-live (None = no expiration)

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key existed and was deleted
        """
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        pass

    # =========================================================================
    # Atomic Operations (Critical for Circuit Breaker)
    # =========================================================================

    @abstractmethod
    def incr(self, key: str, amount: int = 1) -> int:
        """
        Atomically increment a counter.

        Args:
            key: Counter key
            amount: Increment amount (default 1)

        Returns:
            New counter value after increment

        Note:
            Creates key with value 0 if not exists, then increments.
        """
        pass

    @abstractmethod
    def decr(self, key: str, amount: int = 1) -> int:
        """
        Atomically decrement a counter.

        Args:
            key: Counter key
            amount: Decrement amount (default 1)

        Returns:
            New counter value after decrement
        """
        pass

    @abstractmethod
    def expire(self, key: str, ttl: timedelta) -> bool:
        """
        Set expiration on existing key.

        Args:
            key: Cache key
            ttl: Time-to-live duration

        Returns:
            True if key exists and expiration was set
        """
        pass

    @abstractmethod
    def ttl(self, key: str) -> Optional[int]:
        """
        Get remaining TTL in seconds.

        Returns:
            Seconds until expiration, None if no TTL, -2 if key missing
        """
        pass

    # =========================================================================
    # Distributed Locking
    # =========================================================================

    @abstractmethod
    def get_lock(
        self,
        name: str,
        timeout: timedelta = timedelta(seconds=10),
        blocking_timeout: Optional[float] = None,
    ) -> DistributedLock:
        """
        Get a distributed lock instance.

        Args:
            name: Lock name (should be unique across application)
            timeout: Lock auto-release timeout
            blocking_timeout: Max time to wait when acquiring

        Returns:
            DistributedLock instance

        Example:
            with cache.get_lock("circuit_breaker:payment") as lock:
                # Critical section
                pass
        """
        pass

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    @abstractmethod
    def mget(self, keys: list[str]) -> dict[str, Any]:
        """
        Get multiple values at once.

        Args:
            keys: List of cache keys

        Returns:
            Dict mapping keys to values (missing keys omitted)
        """
        pass

    @abstractmethod
    def mset(
        self,
        mapping: dict[str, Any],
        ttl: Optional[timedelta] = None,
    ) -> bool:
        """
        Set multiple values at once.

        Args:
            mapping: Key-value pairs to set
            ttl: Optional TTL for all keys

        Returns:
            True if successful
        """
        pass

    # =========================================================================
    # Health Check
    # =========================================================================

    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if cache backend is reachable.

        Returns:
            True if healthy
        """
        pass

    @abstractmethod
    def flush_all(self) -> bool:
        """
        Clear all keys (USE WITH CAUTION - mainly for testing).

        Returns:
            True if successful
        """
        pass
```

### 3.3 Task Queue Interface

Abstracts async task execution for background job processing.

```python
# interfaces/task_queue.py
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

F = TypeVar('F', bound=Callable)


class TaskStatus(str, Enum):
    """Task execution status"""
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"


@dataclass
class TaskResult:
    """Result of task execution or status check"""
    task_id: str
    status: TaskStatus
    result: Optional[Any] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    retries: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class TaskOptions:
    """Options for task enqueueing"""
    countdown: Optional[int] = None          # Delay in seconds
    eta: Optional[datetime] = None           # Exact execution time
    expires: Optional[datetime] = None       # Task expiration time
    retry: bool = True                        # Enable auto-retry
    max_retries: int = 3                      # Max retry attempts
    retry_backoff: bool = True                # Exponential backoff
    retry_backoff_max: int = 600              # Max backoff seconds
    queue: Optional[str] = None               # Target queue name
    priority: int = 0                         # Task priority (higher = sooner)


class TaskQueueInterface(ABC):
    """
    Abstract interface for async task queues.

    Implementations:
        - CeleryTaskAdapter (current)
        - RQTaskAdapter (Redis Queue - planned)
        - DramatiqTaskAdapter (planned)
        - SyncTaskAdapter (for testing - synchronous execution)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'celery', 'rq')"""
        pass

    # =========================================================================
    # Task Registration
    # =========================================================================

    @abstractmethod
    def task(
        self,
        name: Optional[str] = None,
        bind: bool = False,
        max_retries: int = 3,
        autoretry_for: tuple[type[Exception], ...] = (),
        retry_backoff: bool = True,
        rate_limit: Optional[str] = None,
    ) -> Callable[[F], F]:
        """
        Decorator to register a function as a task.

        Args:
            name: Task name (default: function name)
            bind: If True, pass task instance as first arg
            max_retries: Maximum retry attempts
            autoretry_for: Exception types to auto-retry
            retry_backoff: Use exponential backoff
            rate_limit: Rate limit (e.g., "10/m" for 10 per minute)

        Returns:
            Decorator function

        Example:
            @task_queue.task(max_retries=5, autoretry_for=(ConnectionError,))
            def process_payment(payment_id: int):
                ...
        """
        pass

    # =========================================================================
    # Task Execution
    # =========================================================================

    @abstractmethod
    def enqueue(
        self,
        task_name: str,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        options: Optional[TaskOptions] = None,
    ) -> str:
        """
        Enqueue a task for async execution.

        Args:
            task_name: Registered task name
            args: Positional arguments
            kwargs: Keyword arguments
            options: Execution options

        Returns:
            Task ID for tracking

        Raises:
            TaskNotFoundError: If task_name not registered
        """
        pass

    @abstractmethod
    def enqueue_many(
        self,
        tasks: list[tuple[str, tuple, dict]],
        options: Optional[TaskOptions] = None,
    ) -> list[str]:
        """
        Enqueue multiple tasks atomically.

        Args:
            tasks: List of (task_name, args, kwargs) tuples
            options: Shared execution options

        Returns:
            List of task IDs
        """
        pass

    # =========================================================================
    # Task Management
    # =========================================================================

    @abstractmethod
    def get_result(
        self,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> TaskResult:
        """
        Get task result (may block if timeout provided).

        Args:
            task_id: Task ID from enqueue
            timeout: Max seconds to wait for completion

        Returns:
            TaskResult with status and result/error
        """
        pass

    @abstractmethod
    def revoke(
        self,
        task_id: str,
        terminate: bool = False,
        signal: str = "SIGTERM",
    ) -> bool:
        """
        Cancel a pending or running task.

        Args:
            task_id: Task ID to cancel
            terminate: If True, terminate running task
            signal: Signal to send if terminating

        Returns:
            True if task was revoked
        """
        pass

    @abstractmethod
    def retry(
        self,
        task_id: str,
        countdown: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """
        Retry a failed task.

        Args:
            task_id: Original task ID
            countdown: Delay before retry
            max_retries: Override max retries

        Returns:
            New task ID
        """
        pass

    # =========================================================================
    # Scheduling
    # =========================================================================

    @abstractmethod
    def schedule_periodic(
        self,
        task_name: str,
        schedule: timedelta,
        args: tuple = (),
        kwargs: Optional[dict] = None,
        name: Optional[str] = None,
    ) -> str:
        """
        Schedule a periodic task.

        Args:
            task_name: Registered task name
            schedule: Execution interval
            args: Positional arguments
            kwargs: Keyword arguments
            name: Unique schedule name

        Returns:
            Schedule ID
        """
        pass

    @abstractmethod
    def unschedule(self, schedule_id: str) -> bool:
        """Remove a periodic schedule."""
        pass

    # =========================================================================
    # Queue Management
    # =========================================================================

    @abstractmethod
    def purge_queue(self, queue_name: str = "default") -> int:
        """
        Remove all pending tasks from a queue.

        Returns:
            Number of tasks purged
        """
        pass

    @abstractmethod
    def queue_length(self, queue_name: str = "default") -> int:
        """Get number of pending tasks in queue."""
        pass

    # =========================================================================
    # Health Check
    # =========================================================================

    @abstractmethod
    def health_check(self) -> bool:
        """Check if task queue backend is reachable."""
        pass
```

### 3.4 Web Framework Interface

Abstracts HTTP routing and request/response handling for framework migration.

```python
# interfaces/web_framework.py
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Type, TypeVar
from dataclasses import dataclass, field
from enum import Enum

T = TypeVar('T')


class HttpMethod(str, Enum):
    """HTTP methods"""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


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
    def error(cls, message: str, status_code: int = 400) -> "ResponseContext":
        """Create error response"""
        return cls(
            status_code=status_code,
            body={"error": message, "success": False}
        )

    @classmethod
    def created(cls, data: Any) -> "ResponseContext":
        """Create 201 Created response"""
        return cls(status_code=201, body=data)

    @classmethod
    def no_content(cls) -> "ResponseContext":
        """Create 204 No Content response"""
        return cls(status_code=204, body=None)


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
```

---

## 4. Adapter Implementations

### 4.1 Current Adapters (Already Implemented)

| Interface | Adapter | Location | Status |
|-----------|---------|----------|--------|
| Repository | `DjangoFailedOperationRepository` | `adapters/django_repositories.py` | ✅ Done |
| Repository | `DjangoCircuitBreakerStateRepository` | `adapters/django_repositories.py` | ✅ Done |
| Repository | `DjangoSecurityIncidentRepository` | `adapters/django_repositories.py` | ✅ Done |

### 4.2 Planned Adapters

#### Payment Adapters

| Provider | Class | Priority | Notes |
|----------|-------|----------|-------|
| Toss Payments | `TossPaymentAdapter` | 🔴 High | Extract from current implementation |
| Stripe | `StripePaymentAdapter` | 🟡 Medium | International payments |
| Iamport | `IamportPaymentAdapter` | 🟢 Low | Korean multi-PG |
| KakaoPay | `KakaoPayAdapter` | 🟢 Low | Korean mobile payments |

#### Cache Adapters

| Provider | Class | Priority | Notes |
|----------|-------|----------|-------|
| Redis | `RedisCacheAdapter` | 🔴 High | Extract from current |
| In-Memory | `InMemoryCacheAdapter` | 🔴 High | For unit testing |
| Memcached | `MemcachedCacheAdapter` | 🟢 Low | Alternative cache |
| DynamoDB | `DynamoDBCacheAdapter` | 🟢 Low | AWS serverless |

#### Task Queue Adapters

| Provider | Class | Priority | Notes |
|----------|-------|----------|-------|
| Celery | `CeleryTaskAdapter` | 🔴 High | Extract from current |
| Sync | `SyncTaskAdapter` | 🔴 High | For testing (immediate execution) |
| RQ | `RQTaskAdapter` | 🟡 Medium | Simpler Redis-based |
| Dramatiq | `DramatiqTaskAdapter` | 🟢 Low | Alternative to Celery |

#### Web Framework Adapters

| Framework | Class | Priority | Notes |
|-----------|-------|----------|-------|
| Django REST | `DjangoRESTAdapter` | 🔴 High | Current framework |
| FastAPI | `FastAPIAdapter` | 🟡 Medium | Modern async framework |
| Flask | `FlaskAdapter` | 🟢 Low | Lightweight option |

---

## 5. Factory Pattern

### 5.1 Extended Factory Design

```python
# factory.py (extended)
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
import logging

if TYPE_CHECKING:
    from .interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )
    from .interfaces.payment_provider import PaymentProviderInterface
    from .interfaces.cache_provider import CacheProviderInterface
    from .interfaces.task_queue import TaskQueueInterface
    from .interfaces.web_framework import WebFrameworkInterface

logger = logging.getLogger(__name__)


# =============================================================================
# Provider Registry
# =============================================================================

class ProviderRegistry:
    """
    Central registry for all pluggable components.

    Allows runtime registration and lookup of adapters.
    """

    _payment_providers: dict[str, type] = {}
    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}
    _web_frameworks: dict[str, type] = {}

    # Default provider names
    _default_payment: str = "toss"
    _default_cache: str = "redis"
    _default_queue: str = "celery"
    _default_framework: str = "django"

    @classmethod
    def register_payment(cls, name: str, provider_class: type) -> None:
        """Register a payment provider adapter."""
        cls._payment_providers[name] = provider_class
        logger.info(f"[Registry] Registered payment provider: {name}")

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None:
        """Register a cache provider adapter."""
        cls._cache_providers[name] = provider_class
        logger.info(f"[Registry] Registered cache provider: {name}")

    @classmethod
    def register_queue(cls, name: str, provider_class: type) -> None:
        """Register a task queue adapter."""
        cls._task_queues[name] = provider_class
        logger.info(f"[Registry] Registered task queue: {name}")

    @classmethod
    def register_framework(cls, name: str, adapter_class: type) -> None:
        """Register a web framework adapter."""
        cls._web_frameworks[name] = adapter_class
        logger.info(f"[Registry] Registered web framework: {name}")

    @classmethod
    def get_payment(cls, name: Optional[str] = None) -> "PaymentProviderInterface":
        """Get payment provider instance."""
        name = name or cls._default_payment
        if name not in cls._payment_providers:
            raise ValueError(f"Unknown payment provider: {name}")
        return cls._payment_providers[name]()

    @classmethod
    def get_cache(cls, name: Optional[str] = None) -> "CacheProviderInterface":
        """Get cache provider instance."""
        name = name or cls._default_cache
        if name not in cls._cache_providers:
            raise ValueError(f"Unknown cache provider: {name}")
        return cls._cache_providers[name]()

    @classmethod
    def get_queue(cls, name: Optional[str] = None) -> "TaskQueueInterface":
        """Get task queue instance."""
        name = name or cls._default_queue
        if name not in cls._task_queues:
            raise ValueError(f"Unknown task queue: {name}")
        return cls._task_queues[name]()

    @classmethod
    def get_framework(cls, name: Optional[str] = None) -> "WebFrameworkInterface":
        """Get web framework adapter instance."""
        name = name or cls._default_framework
        if name not in cls._web_frameworks:
            raise ValueError(f"Unknown web framework: {name}")
        return cls._web_frameworks[name]()

    @classmethod
    def set_defaults(
        cls,
        payment: Optional[str] = None,
        cache: Optional[str] = None,
        queue: Optional[str] = None,
        framework: Optional[str] = None,
    ) -> None:
        """Set default providers."""
        if payment:
            cls._default_payment = payment
        if cache:
            cls._default_cache = cache
        if queue:
            cls._default_queue = queue
        if framework:
            cls._default_framework = framework

    @classmethod
    def list_providers(cls) -> dict[str, list[str]]:
        """List all registered providers."""
        return {
            "payment": list(cls._payment_providers.keys()),
            "cache": list(cls._cache_providers.keys()),
            "queue": list(cls._task_queues.keys()),
            "framework": list(cls._web_frameworks.keys()),
        }


# =============================================================================
# Auto-registration on import
# =============================================================================

def _auto_register_adapters():
    """Auto-register available adapters based on installed packages."""

    # Payment providers
    try:
        from .adapters.payments.toss_adapter import TossPaymentAdapter
        ProviderRegistry.register_payment("toss", TossPaymentAdapter)
    except ImportError:
        pass

    # Cache providers
    try:
        from .adapters.cache.redis_adapter import RedisCacheAdapter
        ProviderRegistry.register_cache("redis", RedisCacheAdapter)
    except ImportError:
        pass

    try:
        from .adapters.cache.memory_adapter import InMemoryCacheAdapter
        ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)
    except ImportError:
        pass

    # Task queues
    try:
        from .adapters.queues.celery_adapter import CeleryTaskAdapter
        ProviderRegistry.register_queue("celery", CeleryTaskAdapter)
    except ImportError:
        pass

    try:
        from .adapters.queues.sync_adapter import SyncTaskAdapter
        ProviderRegistry.register_queue("sync", SyncTaskAdapter)
    except ImportError:
        pass

    # Web frameworks
    try:
        from .adapters.frameworks.django_adapter import DjangoRESTAdapter
        ProviderRegistry.register_framework("django", DjangoRESTAdapter)
    except ImportError:
        pass


# Run auto-registration
_auto_register_adapters()
```

### 5.2 Usage Examples

```python
# Configuration (e.g., in settings.py or startup)
from self_healing.factory import ProviderRegistry

# Set defaults based on environment
if settings.ENVIRONMENT == "production":
    ProviderRegistry.set_defaults(
        payment="toss",
        cache="redis",
        queue="celery",
        framework="django",
    )
elif settings.ENVIRONMENT == "test":
    ProviderRegistry.set_defaults(
        payment="mock",
        cache="memory",
        queue="sync",
        framework="django",
    )

# Usage in services
payment = ProviderRegistry.get_payment()
result = payment.confirm_payment(
    payment_key="pk_xxx",
    order_id="order_123",
    amount=Decimal("50000"),
)

# Or get specific provider
cache = ProviderRegistry.get_cache("redis")
with cache.get_lock("circuit_breaker:payment") as lock:
    # Critical section
    pass
```

---

## 6. Package Structure

### 6.1 Target Directory Layout

```
packages/self_healing/
├── pyproject.toml                    # Package configuration
├── README.md                         # Package documentation
├── CHANGELOG.md                      # Version history
│
├── self_healing/
│   ├── __init__.py                   # Public API exports
│   │
│   ├── core/                         # Pure Python business logic
│   │   ├── __init__.py
│   │   ├── dlq_service.py            # Dead Letter Queue service
│   │   ├── replay_service.py         # DLQ replay service
│   │   ├── circuit_breaker.py        # Circuit breaker logic
│   │   ├── retry_handler.py          # Retry with backoff
│   │   ├── backoff_calculator.py     # Exponential backoff
│   │   ├── idempotency.py            # Idempotency handling
│   │   └── metrics.py                # Metrics collection
│   │
│   ├── interfaces/                   # Abstract interfaces (ABC)
│   │   ├── __init__.py
│   │   ├── repositories.py           # ✅ Data access interfaces
│   │   ├── payment_provider.py       # 🆕 Payment gateway interface
│   │   ├── cache_provider.py         # 🆕 Cache/state interface
│   │   ├── task_queue.py             # 🆕 Async task interface
│   │   └── web_framework.py          # 🆕 HTTP framework interface
│   │
│   ├── adapters/                     # Concrete implementations
│   │   ├── __init__.py
│   │   │
│   │   ├── repositories/             # Database adapters
│   │   │   ├── __init__.py
│   │   │   ├── django_repo.py        # ✅ Django ORM
│   │   │   └── sqlalchemy_repo.py    # 🆕 SQLAlchemy (future)
│   │   │
│   │   ├── payments/                 # Payment provider adapters
│   │   │   ├── __init__.py
│   │   │   ├── toss_adapter.py       # 🆕 Toss Payments
│   │   │   ├── stripe_adapter.py     # 🆕 Stripe (future)
│   │   │   └── mock_adapter.py       # 🆕 Mock for testing
│   │   │
│   │   ├── cache/                    # Cache provider adapters
│   │   │   ├── __init__.py
│   │   │   ├── redis_adapter.py      # 🆕 Redis
│   │   │   └── memory_adapter.py     # 🆕 In-memory (testing)
│   │   │
│   │   ├── queues/                   # Task queue adapters
│   │   │   ├── __init__.py
│   │   │   ├── celery_adapter.py     # 🆕 Celery
│   │   │   └── sync_adapter.py       # 🆕 Sync (testing)
│   │   │
│   │   └── frameworks/               # Web framework adapters
│   │       ├── __init__.py
│   │       ├── django_adapter.py     # 🆕 Django REST Framework
│   │       └── fastapi_adapter.py    # 🆕 FastAPI (future)
│   │
│   ├── config/                       # Configuration
│   │   ├── __init__.py
│   │   └── settings.py               # Centralized settings
│   │
│   └── factory.py                    # ✅ Extended factory + registry
│
└── tests/
    ├── __init__.py
    ├── conftest.py                   # Pytest fixtures
    │
    ├── unit/                         # Unit tests
    │   ├── core/
    │   ├── adapters/
    │   └── interfaces/
    │
    └── integration/                  # Integration tests
        ├── test_payment_adapters.py
        ├── test_cache_adapters.py
        └── test_queue_adapters.py
```

---

## 7. Implementation Roadmap

### Phase 1: Core Interfaces (Week 1)

| Task | Description | Estimate | Priority |
|------|-------------|----------|----------|
| 1.1 | Create `interfaces/payment_provider.py` | 2h | ✅ Done |
| 1.2 | Create `interfaces/cache_provider.py` | 2h | ✅ Done |
| 1.3 | Create `interfaces/task_queue.py` | 2h | ✅ Done |
| 1.4 | Create `interfaces/web_framework.py` | 2h | ✅ Done |
| 1.5 | Update `interfaces/__init__.py` exports | 30m | ✅ Done |

### Phase 2: Essential Adapters (Week 2) ✅ COMPLETED

| Task | Description | Estimate | Status |
|------|-------------|----------|--------|
| 2.1 | Extract `TossPaymentAdapter` from current code | 4h | ✅ Done |
| 2.2 | Create `RedisCacheAdapter` | 3h | ✅ Done |
| 2.3 | Create `InMemoryCacheAdapter` for tests | 2h | ✅ Done |
| 2.4 | Create `CeleryTaskAdapter` | 4h | ✅ Done |
| 2.5 | Create `SyncTaskAdapter` for tests | 2h | ✅ Done |

### Phase 3: Factory Extension (Week 2-3) ✅ COMPLETED

| Task | Description | Estimate | Status |
|------|-------------|----------|--------|
| 3.1 | Implement `ProviderRegistry` | 3h | ✅ Done |
| 3.2 | Add auto-registration logic | 2h | ✅ Done |
| 3.3 | Update existing factory functions | 2h | ✅ Done |
| 3.4 | Add provider health check aggregation | 2h | 🟡 Pending |

### Phase 4: Service Integration (Week 3)

| Task | Description | Estimate | Priority |
|------|-------------|----------|----------|
| 4.1 | Update `DLQService` to use interfaces | 4h | 🔴 High |
| 4.2 | Update `CircuitBreakerService` to use interfaces | 4h | 🔴 High |
| 4.3 | Update `ReplayService` to use interfaces | 3h | 🔴 High |
| 4.4 | Update Celery tasks to use queue interface | 4h | 🔴 High |

### Phase 5: Testing & Documentation (Week 4)

| Task | Description | Estimate | Priority |
|------|-------------|----------|----------|
| 5.1 | Unit tests for all interfaces | 6h | 🔴 High |
| 5.2 | Integration tests for adapter combinations | 8h | 🔴 High |
| 5.3 | Update existing tests to use mock adapters | 4h | 🔴 High |
| 5.4 | API documentation and examples | 4h | 🟡 Medium |

### Phase 6: Additional Adapters (Future)

| Task | Description | Estimate | Priority |
|------|-------------|----------|----------|
| 6.1 | `StripePaymentAdapter` | 6h | 🟢 Low |
| 6.2 | `FastAPIAdapter` | 8h | 🟡 Medium |
| 6.3 | `RQTaskAdapter` | 4h | 🟢 Low |
| 6.4 | `MemcachedCacheAdapter` | 3h | 🟢 Low |

---

## 8. Migration Guide

### 8.1 For Existing Code

When migrating existing code to use the pluggable architecture:

```python
# Before (tightly coupled)
from shopping.services.payment import TossPaymentService

payment_service = TossPaymentService()
result = payment_service.confirm(payment_key, order_id, amount)

# After (loosely coupled)
from self_healing.factory import ProviderRegistry

payment = ProviderRegistry.get_payment()  # Returns configured provider
result = payment.confirm_payment(payment_key, order_id, amount)
```

### 8.2 For Testing

```python
# conftest.py
import pytest
from self_healing.factory import ProviderRegistry
from self_healing.adapters.payments.mock_adapter import MockPaymentAdapter
from self_healing.adapters.cache.memory_adapter import InMemoryCacheAdapter
from self_healing.adapters.queues.sync_adapter import SyncTaskAdapter


@pytest.fixture(autouse=True)
def setup_test_providers():
    """Use mock/test adapters for all tests."""
    ProviderRegistry.register_payment("mock", MockPaymentAdapter)
    ProviderRegistry.register_cache("memory", InMemoryCacheAdapter)
    ProviderRegistry.register_queue("sync", SyncTaskAdapter)

    ProviderRegistry.set_defaults(
        payment="mock",
        cache="memory",
        queue="sync",
    )

    yield

    # Reset after test
    ProviderRegistry.set_defaults(
        payment="toss",
        cache="redis",
        queue="celery",
    )
```

### 8.3 For New Payment Providers

```python
# adapters/payments/stripe_adapter.py
from self_healing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
)
import stripe


class StripePaymentAdapter(PaymentProviderInterface):
    """Stripe payment provider implementation."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.STRIPE_API_KEY
        stripe.api_key = self.api_key

    @property
    def provider_name(self) -> str:
        return "stripe"

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        try:
            intent = stripe.PaymentIntent.confirm(
                payment_key,
                idempotency_key=idempotency_key,
            )
            return PaymentConfirmResult(
                success=intent.status == "succeeded",
                payment_key=intent.id,
                transaction_id=intent.latest_charge,
                raw_response=intent.to_dict(),
            )
        except stripe.error.StripeError as e:
            return PaymentConfirmResult(
                success=False,
                error_code=e.code,
                error_message=str(e),
            )

    # ... implement other methods
```

---

## Appendix A: Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-12-10 | Use ABC for interfaces | Explicit contracts, IDE support, easier testing |
| 2025-12-10 | Dataclasses for DTOs | Immutable, type-safe, no framework dependency |
| 2025-12-10 | Registry pattern for providers | Runtime flexibility, easy testing, plugin support |
| 2025-12-10 | Auto-registration on import | Zero-config for common adapters |

---

## Appendix B: Related Documents

- [SELF_HEALING_EXTRACTION_PLAN.md](./SELF_HEALING_EXTRACTION_PLAN.md) - Original extraction plan
- [SELF_HEALING_EXTRACTION_WORK_PLAN.md](./SELF_HEALING_EXTRACTION_WORK_PLAN.md) - Sprint-level work breakdown
- [L3_SELF_HEALING_SYSTEM.md](./L3_SELF_HEALING_SYSTEM.md) - System overview

---

*Last Updated: 2025-12-10*
