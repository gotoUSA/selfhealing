# Integration & Adapter-Level Capabilities

This document details the capabilities that exist at the integration layer and the requirements for host applications.

---

## Capability 30: Provider Registry (Dependency Injection)

### 30.1 Purpose

Provides a centralized registry for all pluggable components, enabling runtime registration and lookup of adapters without hard-coded dependencies.

### 30.2 Registrable Provider Types

| Provider Type | Interface | Purpose |
|---------------|-----------|---------|
| Payment | `PaymentProviderInterface` | External payment gateways |
| Cache | `CacheProviderInterface` | Caching backends |
| Task Queue | `TaskQueueInterface` | Async task execution |
| Failed Operation Repo | `FailedOperationRepository` | DLQ persistence |
| Circuit Breaker Repo | `CircuitBreakerStateRepository` | CB state persistence |
| Security Repo | `SecurityIncidentRepository` | Security incident storage |

### 30.3 Registration API

```python
from selfhealing.factory import ProviderRegistry

# Register providers
ProviderRegistry.register_payment("toss", TossPaymentAdapter)
ProviderRegistry.register_cache("redis", RedisAdapter)
ProviderRegistry.register_queue("celery", CeleryAdapter)
ProviderRegistry.register_failed_operation_repo("django", DjangoRepo)
ProviderRegistry.register_circuit_breaker_repo("django", DjangoCBRepo)
ProviderRegistry.register_security_repo("django", DjangoSecurityRepo)
```

### 30.4 Provider Retrieval

```python
# Get default provider
cache = ProviderRegistry.get_cache()  # Uses _default_cache

# Get specific provider
cache = ProviderRegistry.get_cache("redis")

# Singleton support
cache = ProviderRegistry.get_cache("redis", singleton=True)
```

### 30.5 Default Providers

```python
_default_payment: str = "mock"
_default_cache: str = "memory"
_default_queue: str = "sync"
_default_repo: str = "django"
```

### 30.6 Code References

| Component | Location |
|-----------|----------|
| Registry | `factory.py::ProviderRegistry` |

---

## Capability 31: Framework Adapters

### 31.1 Available Adapters

The system provides adapters for multiple frameworks:

```
adapters/
├── cache/          # Cache providers
├── celery/         # Celery task queue
├── django/         # Django ORM integration
├── django_repos/   # Django repository implementations
├── fastapi/        # FastAPI integration
├── frameworks/     # Generic framework adapters
├── memory/         # In-memory implementations
├── payments/       # Payment gateway adapters
├── queues/         # Task queue adapters
├── rate_limit/     # Rate limit storage adapters
└── sqlalchemy/     # SQLAlchemy ORM integration
```

### 31.2 Repository Implementations

| Adapter | ORM | Status |
|---------|-----|--------|
| `DjangoFailedOperationRepository` | Django ORM | Production |
| `DjangoCircuitBreakerStateRepository` | Django ORM | Production |
| `DjangoSecurityIncidentRepository` | Django ORM | Production |
| `SQLAlchemyFailedOperationRepository` | SQLAlchemy | Available |

### 31.3 Cache Adapters

| Adapter | Backend | Status |
|---------|---------|--------|
| Redis Cache Adapter | Redis | Production |
| Memory Cache Adapter | In-memory dict | Testing |

### 31.4 Queue Adapters

| Adapter | Backend | Status |
|---------|---------|--------|
| Celery Queue Adapter | Celery | Production |
| Sync Queue Adapter | Synchronous | Testing |

### 31.5 Rate Limit Storage Adapters

| Adapter | Backend | Use Case |
|---------|---------|----------|
| `RedisRateLimitStorage` | Redis | Distributed, high performance |
| `DatabaseRateLimitStorage` | SQL DB | Distributed, guaranteed available |
| `InMemoryRateLimitStorage` | Memory | Testing, single process |

### 31.6 Code References

| Component | Location |
|-----------|----------|
| Adapters Root | `adapters/` |
| Django Repos | `adapters/django_repos/` |
| Rate Limit | `adapters/rate_limit/` |

---

## Capability 32: Repository Interfaces

### 32.1 Purpose

Defines abstract contracts for data access, enabling the core system to work with any ORM or database.

### 32.2 FailedOperationRepository Interface

```python
class FailedOperationRepository(ABC):
    @abstractmethod
    def create(
        self,
        domain: str,
        failure_type: str,
        error_message: str = "",
        error_code: str = "",
        entity_refs: Optional[dict[str, int]] = None,
        user_id: Optional[int] = None,
        snapshot_data: Optional[dict[str, Any]] = None,
        request_data: Optional[dict[str, Any]] = None,
        response_data: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
        max_retries: int = 2,
        next_action_hint: str = "",
        recommended_action: str = "",
    ) -> FailedOperationData:
        ...

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        ...

    @abstractmethod
    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = "",
        resolution_note: str = "",
        resolved_by_id: Optional[int] = None,
    ) -> bool:
        ...

    @abstractmethod
    def find_by_status(
        self,
        status: str,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        ...

    @abstractmethod
    def find_replayable(
        self,
        max_retries: int,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[FailedOperationData]:
        ...

    @abstractmethod
    def try_acquire_for_replay(
        self,
        id: int,
        max_retries: int,
    ) -> Optional[FailedOperationData]:
        """Atomic acquisition to prevent race conditions."""
        ...

    @abstractmethod
    def complete_replay(
        self,
        id: int,
        success: bool,
        resolution_type: str = "",
        note: str = "",
        error_details: Optional[dict] = None,
    ) -> bool:
        ...
```

### 32.3 CircuitBreakerStateRepository Interface

```python
class CircuitBreakerStateRepository(ABC):
    @abstractmethod
    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        ...

    @abstractmethod
    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: Optional[int] = None,
        success_count: Optional[int] = None,
        opened_at: Optional[datetime] = None,
    ) -> bool:
        ...

    @abstractmethod
    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """Atomic failure count increment."""
        ...

    @abstractmethod
    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """Atomic success count increment."""
        ...

    @abstractmethod
    def atomic_force_open(
        self,
        service_name: str,
        reason: str,
        controlled_by_id: Optional[int] = None,
        ttl_minutes: int = 90,
    ) -> tuple[bool, str, str]:
        """Atomic force open with race condition prevention."""
        ...

    @abstractmethod
    def atomic_force_close(
        self,
        service_name: str,
        reason: str,
        controlled_by_id: Optional[int] = None,
    ) -> tuple[bool, str, str]:
        """Atomic force close with race condition prevention."""
        ...
```

### 32.4 Code References

| Component | Location |
|-----------|----------|
| Interfaces | `interfaces/repositories.py` |

---

## Capability 33: Replay Handler Registration

### 33.1 Purpose

Allows host applications to register domain-specific replay logic that executes when DLQ items are replayed.

### 33.2 Handler Interface

```python
class ReplayHandler(ABC):
    @property
    @abstractmethod
    def domain(self) -> str:
        """Return the domain this handler handles."""
        pass

    @abstractmethod
    def replay(self, failed_op: FailedOperationData) -> ReplayResult:
        """Execute replay for a single failed operation."""
        pass

    @abstractmethod
    def can_replay(self, failed_op: FailedOperationData) -> tuple[bool, str]:
        """Check if the operation can be replayed."""
        pass
```

### 33.3 Registration

```python
from selfhealing.services.replay_service import register_replay_handler

class PaymentReplayHandler(ReplayHandler):
    @property
    def domain(self) -> str:
        return "payment"

    def can_replay(self, failed_op):
        # Check if payment can be retried
        payment = Payment.objects.filter(id=failed_op.payment_id).first()
        if not payment:
            return False, "Payment not found"
        if payment.status == "completed":
            return False, "Already completed"
        return True, ""

    def replay(self, failed_op):
        # Execute payment retry logic
        try:
            payment_service.retry_payment(failed_op.payment_id)
            return ReplayResult.succeeded(failed_op.id, "Payment retried")
        except Exception as e:
            return ReplayResult.failed(failed_op.id, str(e))

register_replay_handler(PaymentReplayHandler())
```

### 33.4 Default Handler

If no handler is registered for a domain, the `DefaultReplayHandler` returns an error:

```python
class DefaultReplayHandler(ReplayHandler):
    def replay(self, failed_op):
        return ReplayResult.failed(
            failed_op.id,
            f"No replay handler registered for domain '{self._domain}'"
        )
```

### 33.5 Code References

| Component | Location |
|-----------|----------|
| Handler ABC | `services/replay_service.py::ReplayHandler` |
| Registration | `services/replay_service.py::register_replay_handler` |

---

## Capability 34: Task Queue Interface

### 34.1 Purpose

Abstracts task queue operations to support multiple backends (Celery, RQ, sync, etc.).

### 34.2 Interface

```python
class TaskQueueInterface(ABC):
    @abstractmethod
    def enqueue(
        self,
        task_name: str,
        args: tuple = (),
        kwargs: dict = None,
        countdown: int = 0,
        eta: datetime = None,
        queue: str = None,
    ) -> str:
        """Enqueue a task and return task ID."""
        pass

    @abstractmethod
    def get_task_status(self, task_id: str) -> str:
        """Get the status of a task."""
        pass
```

### 34.3 Code References

| Component | Location |
|-----------|----------|
| Interface | `interfaces/task_queue.py` |
| Celery Adapter | `adapters/celery/` |
| Sync Adapter | `adapters/queues/` |

---

## Capability 35: Cache Provider Interface

### 35.1 Purpose

Abstracts cache operations for idempotency checking and rate limit state.

### 35.2 Interface

```python
class CacheProviderInterface(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        pass

    @abstractmethod
    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set a value in cache with optional TTL."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        pass
```

### 35.3 Code References

| Component | Location |
|-----------|----------|
| Interface | `interfaces/cache_provider.py` |
| Adapters | `adapters/cache/` |

---

## Host Application Requirements

### What the Host Must Provide

For full functionality, the host application must:

| Requirement | Purpose | Adapters Available |
|-------------|---------|-------------------|
| **Persistent Storage** | DLQ, CB state, security incidents | Django ORM, SQLAlchemy |
| **Cache Backend** | Idempotency, rate limit state | Redis, Memory |
| **Task Queue** | Async replay, conditional replay | Celery, Sync |
| **Configuration** | System behavior tuning | Django settings, Env vars |

### Minimal Setup

At minimum, the host must:

1. Register repository adapters for persistence
2. Configure the self-healing settings
3. Initialize the ProviderRegistry

```python
# Example minimal setup
from selfhealing.factory import ProviderRegistry
from myapp.adapters import (
    MyFailedOperationRepo,
    MyCircuitBreakerRepo,
)

ProviderRegistry.register_failed_operation_repo("default", MyFailedOperationRepo)
ProviderRegistry.register_circuit_breaker_repo("default", MyCircuitBreakerRepo)
```

### Optional Integrations

| Integration | What It Enables |
|-------------|-----------------|
| Redis | Distributed rate limiting, faster cache |
| Celery | Async DLQ replay, background tasks |
| Prometheus | Metrics scraping |
| Slack/PagerDuty | Security notifications |
---

## Optional: OpenTelemetry Adapter

An **optional** OpenTelemetry adapter is available for exporting self-healing signals to external APM platforms (Datadog, New Relic, Elastic, etc.).

**Important:**
- This is **NOT** a required integration
- Prometheus remains the primary metrics layer
- The system functions identically without OpenTelemetry
- Zero overhead when disabled or not installed

For implementation details, see [10-OPENTELEMETRY-ADAPTER.md](../capablitity_정의/10-OPENTELEMETRY-ADAPTER.md).


Decision Record Logging is defined as an evidence mechanism,
not an integration adapter.
See 11-DECISION-RECORD-LOGGING.md for details.

For complete specification, see [11-DECISION-RECORD-LOGGING.md](../capablitity_정의/11-DECISION-RECORD-LOGGING.md).
