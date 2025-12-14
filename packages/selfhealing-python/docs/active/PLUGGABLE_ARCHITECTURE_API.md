# Pluggable Architecture API Documentation

> **Version**: 1.0.0
> **Last Updated**: 2025-12-10

## Overview

The selfhealing package provides a pluggable architecture that allows you to:
- Swap payment providers (Toss, Stripe, etc.)
- Switch cache backends (Redis, Memcached, In-Memory)
- Replace task queues (Celery, RQ, synchronous for testing)

## Quick Start

```python
from selfhealing.factory import ProviderRegistry

# Get default providers
cache = ProviderRegistry.get_cache()
queue = ProviderRegistry.get_queue()
payment = ProviderRegistry.get_payment()

# Set test defaults for testing
ProviderRegistry.set_defaults(
    payment="mock",
    cache="memory",
    queue="sync",
)
```

## Provider Registry

The `ProviderRegistry` is the central access point for all pluggable components.

### Methods

#### `get_payment(name: str = None, singleton: bool = True)`
Get a payment provider instance.

```python
# Get default payment provider
payment = ProviderRegistry.get_payment()

# Get specific provider
mock_payment = ProviderRegistry.get_payment("mock")

# Get new instance (not singleton)
payment = ProviderRegistry.get_payment(singleton=False)
```

#### `get_cache(name: str = None, singleton: bool = True)`
Get a cache provider instance.

```python
# Get in-memory cache for testing
cache = ProviderRegistry.get_cache("memory")

# Use with TTL
from datetime import timedelta
cache.set("key", "value", ttl=timedelta(hours=1))
```

#### `get_queue(name: str = None, singleton: bool = True)`
Get a task queue instance.

```python
# Get sync queue for testing
queue = ProviderRegistry.get_queue("sync")

# Register and execute tasks
@queue.task(name="process_payment")
def process_payment(order_id: str):
    return f"Processed {order_id}"

# Execute
task_id = queue.enqueue("process_payment", args=("order_123",))
```

#### `set_defaults(payment: str, cache: str, queue: str)`
Set default provider names.

```python
# For testing
ProviderRegistry.set_defaults(
    payment="mock",
    cache="memory",
    queue="sync",
)

# For production
ProviderRegistry.set_defaults(
    payment="toss",
    cache="redis",
    queue="celery",
)
```

#### `health_check_all()`
Run health checks on all providers.

```python
health = ProviderRegistry.health_check_all()
# Returns: {"payment": True, "cache": True, "queue": True}
```

---

## Payment Provider Interface

### Interface Definition

```python
from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
)
```

### Using Mock Payment Adapter

```python
from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter

adapter = MockPaymentAdapter()

# Configure success response
adapter.set_confirm_response(
    success=True,
    payment_key="pk_test_123",
    transaction_id="tx_456",
)

# Configure failure response
adapter.set_confirm_response(
    success=False,
    error_code="INSUFFICIENT_FUNDS",
    error_message="카드 잔액이 부족합니다",
)

# Configure exception
adapter.set_confirm_exception(ConnectionError("Network timeout"))

# Confirm payment
result = adapter.confirm_payment(
    payment_key="pk_test",
    order_id="order_123",
    amount=Decimal("50000"),
)

# Track calls
calls = adapter.get_confirm_calls()
print(f"Confirm called {adapter.confirm_call_count} times")

# Reset adapter
adapter.reset()
```

### Idempotent Payment Example

```python
def confirm_payment_idempotent(
    payment_key: str,
    order_id: str,
    amount: Decimal
) -> PaymentConfirmResult:
    cache = ProviderRegistry.get_cache()
    payment = ProviderRegistry.get_payment()

    # Check idempotency cache
    idem_key = f"payment:idem:{order_id}"
    cached = cache.get(idem_key)
    if cached:
        return PaymentConfirmResult(**cached)

    # Process payment
    result = payment.confirm_payment(
        payment_key=payment_key,
        order_id=order_id,
        amount=amount,
    )

    # Cache result
    if result.success:
        cache.set(idem_key, {
            "success": result.success,
            "transaction_id": result.transaction_id,
        }, ttl=timedelta(days=1))

    return result
```

---

## Cache Provider Interface

### Interface Definition

```python
from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)
```

### Basic Operations

```python
from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

cache = InMemoryCacheAdapter()

# Set/Get
cache.set("key", "value")
value = cache.get("key")  # "value"

# With TTL
cache.set("key", "value", ttl=timedelta(seconds=60))

# Delete
cache.delete("key")

# Exists
if cache.exists("key"):
    print("Key exists")
```

### Atomic Operations

```python
# Counter operations
cache.incr("counter")  # 1
cache.incr("counter", amount=5)  # 6
cache.decr("counter")  # 5

# Thread-safe rate limiting
rate_key = "rate_limit:user:123"
count = cache.incr(rate_key)
if count == 1:
    cache.expire(rate_key, timedelta(minutes=1))
if count > 10:
    raise RateLimitExceeded()
```

### Distributed Locking

```python
# Context manager (recommended)
with cache.get_lock("payment:order_123") as lock:
    # Critical section
    process_payment()

# Manual acquire/release
lock = cache.get_lock("my_lock", timeout=timedelta(seconds=30))
if lock.acquire(blocking=False):
    try:
        process_payment()
    finally:
        lock.release()
else:
    print("Lock already held")
```

### Bulk Operations

```python
# Set multiple
cache.mset({
    "key1": "value1",
    "key2": "value2",
}, ttl=timedelta(hours=1))

# Get multiple
values = cache.mget(["key1", "key2", "key3"])
# {"key1": "value1", "key2": "value2"}
```

---

## Task Queue Interface

### Interface Definition

```python
from selfhealing.interfaces.task_queue import (
    TaskQueueInterface,
    TaskStatus,
    TaskResult,
    TaskOptions,
)
```

### Using Sync Task Adapter (Testing)

```python
from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

queue = SyncTaskAdapter()

# Register task
@queue.task(name="process_order")
def process_order(order_id: str, amount: int):
    return {"order_id": order_id, "processed": True}

# Execute via delay (Celery-compatible)
result = process_order.delay("order_123", 1000)
print(result.get())  # {"order_id": "order_123", "processed": True}

# Execute via enqueue
task_id = queue.enqueue(
    "process_order",
    args=("order_456", 2000),
)

# Get result
result = queue.get_result(task_id)
print(result.status)  # TaskStatus.SUCCESS
print(result.result)  # {"order_id": "order_456", "processed": True}
```

### Task Options

```python
from datetime import datetime, timedelta

options = TaskOptions(
    countdown=30,  # Delay 30 seconds
    retry=True,
    max_retries=5,
    queue="high_priority",
)

task_id = queue.enqueue(
    "process_payment",
    args=("order_123",),
    options=options,
)
```

### Batch Processing

```python
# Enqueue multiple tasks
task_ids = queue.enqueue_many([
    ("process_order", ("order_1",), {}),
    ("process_order", ("order_2",), {}),
    ("process_order", ("order_3",), {}),
])
```

---

## Testing with Mock Adapters

### pytest Fixtures

```python
# conftest.py
import pytest
from selfhealing.factory import ProviderRegistry

@pytest.fixture(autouse=True)
def setup_test_providers():
    """Use mock adapters for all tests."""
    ProviderRegistry.set_defaults(
        payment="mock",
        cache="memory",
        queue="sync",
    )
    ProviderRegistry.clear_instances()
    yield
    ProviderRegistry.clear_instances()

@pytest.fixture
def mock_payment():
    """Provide configured mock payment adapter."""
    from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter
    adapter = MockPaymentAdapter()
    yield adapter
    adapter.reset()
```

### Test Example

```python
def test_payment_flow(mock_payment):
    """Test payment confirmation flow."""
    mock_payment.set_confirm_response(
        success=True,
        transaction_id="tx_test_123",
    )

    result = mock_payment.confirm_payment(
        payment_key="pk_test",
        order_id="order_test",
        amount=Decimal("10000"),
    )

    assert result.success is True
    assert result.transaction_id == "tx_test_123"
    assert mock_payment.confirm_call_count == 1

def test_payment_failure(mock_payment):
    """Test payment failure handling."""
    mock_payment.set_confirm_response(
        success=False,
        error_code="CARD_DECLINED",
    )

    result = mock_payment.confirm_payment(
        payment_key="pk_test",
        order_id="order_test",
        amount=Decimal("10000"),
    )

    assert result.success is False
    assert result.error_code == "CARD_DECLINED"
```

---

## Available Adapters

| Interface | Adapter | Provider Name | Description |
|-----------|---------|---------------|-------------|
| Payment | `MockPaymentAdapter` | `mock` | Testing, configurable responses |
| Cache | `InMemoryCacheAdapter` | `memory` | Testing, single-process |
| Cache | `RedisCacheAdapter` | `redis` | Production, distributed |
| Queue | `SyncTaskAdapter` | `sync` | Testing, immediate execution |
| Queue | `CeleryTaskAdapter` | `celery` | Production, async |

---

## Creating Custom Adapters

### Custom Payment Adapter

```python
from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)

class StripePaymentAdapter(PaymentProviderInterface):
    """Stripe payment provider implementation."""

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
        # Implement Stripe API call
        pass

    # ... implement other methods

# Register with factory
from selfhealing.factory import ProviderRegistry
ProviderRegistry.register_payment("stripe", StripePaymentAdapter)
```

---

## Best Practices

1. **Always use ProviderRegistry** - Don't instantiate adapters directly in production code
2. **Use fixtures for tests** - Let pytest manage adapter lifecycle
3. **Reset mock adapters** - Call `reset()` after tests to ensure clean state
4. **Use idempotency keys** - Prevent duplicate payments
5. **Use distributed locks** - Prevent race conditions in distributed systems
6. **Health checks** - Monitor provider health in production

```python
# Health monitoring example
def check_system_health():
    health = ProviderRegistry.health_check_all()
    if not all(health.values()):
        unhealthy = [k for k, v in health.items() if not v]
        raise SystemUnhealthy(f"Unhealthy providers: {unhealthy}")
```
