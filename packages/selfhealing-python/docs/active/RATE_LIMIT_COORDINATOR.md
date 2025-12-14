# Rate Limit Coordinator

**Distributed Self-DDoS Prevention for Multi-Server Environments**

## Overview

The Rate Limit Coordinator prevents Self-DDoS attacks by coordinating retry behavior across all workers in a distributed environment. When one worker receives a 429 (Rate Limited) response, all workers are notified and respect the cooldown period.

## Key Insight

> "Every application has a database."

This means we can guarantee 100% Self-DDoS prevention coverage regardless of customer infrastructure:

- **Redis available** → Use Redis (fastest, ~0.1ms)
- **No Redis** → Use Database (100% compatible, ~1-5ms)
- **Nothing available** → Fall back to In-Memory (single process only)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                 Rate Limit Coordinator                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   RateLimitCoordinator                                          │
│         │                                                       │
│         ▼                                                       │
│   ┌─────────────────────┐                                       │
│   │ RateLimitStorage    │◄──── Interface                        │
│   │ Interface           │                                       │
│   └─────────────────────┘                                       │
│         │                                                       │
│    ┌────┴────┬────────────┐                                     │
│    ▼         ▼            ▼                                     │
│  Redis    Database    InMemory                                  │
│ Adapter   Adapter    Adapter                                    │
│ (fast)   (100% fallback) (single process)                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. RateLimitStorageInterface

Abstract interface for distributed rate limit state storage.

```python
from selfhealing.interfaces import (
    RateLimitStorageInterface,
    RateLimitState,
    RateLimitStorageType,
)
```

**Methods:**
- `get_state(key)` - Get current cooldown state
- `set_cooldown(key, until, ttl)` - Set global cooldown
- `increment_consecutive_429s(key)` - Atomic counter increment
- `reset_consecutive_429s(key)` - Reset on success
- `is_available()` - Check backend availability

### 2. Storage Adapters

#### Redis Adapter (Fastest)
```python
from selfhealing.adapters.rate_limit import RedisRateLimitStorage

storage = RedisRateLimitStorage(redis_client)
```

#### Database Adapter (100% Compatible)
```python
from selfhealing.adapters.rate_limit import DatabaseRateLimitStorage

storage = DatabaseRateLimitStorage()
```

#### InMemory Adapter (Testing/Single Process)
```python
from selfhealing.adapters.rate_limit import InMemoryRateLimitStorage

storage = InMemoryRateLimitStorage()
```

### 3. RateLimitCoordinator

Central coordinator for rate limit management.

```python
from selfhealing.services import (
    RateLimitCoordinator,
    get_rate_limit_coordinator,
)

# Get singleton instance
coordinator = get_rate_limit_coordinator()

# Before making request
coordinator.wait_if_needed("payment_api")

# After receiving 429
coordinator.on_rate_limited("payment_api", retry_after=60)

# After successful request
coordinator.on_success("payment_api")
```

## Usage

### Basic Usage

```python
from selfhealing.services import get_rate_limit_coordinator

coordinator = get_rate_limit_coordinator()

def call_external_api():
    # Wait if rate limited
    coordinator.wait_if_needed("external_api")

    try:
        response = requests.post("https://api.example.com/...")

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", 5))
            coordinator.on_rate_limited("external_api", retry_after)
            raise RateLimitError()

        coordinator.on_success("external_api")
        return response

    except Exception as e:
        raise
```

### Decorator Usage

```python
coordinator = get_rate_limit_coordinator()

@coordinator.rate_limit_aware("payment_api")
def call_payment_api():
    return requests.post("https://payment.example.com/...")
```

### With RetryHandler (Automatic)

The `RetryHandler` now automatically integrates with `RateLimitCoordinator`:

```python
from selfhealing.services import RetryHandler, RetryConfig

handler = RetryHandler(
    config=RetryConfig(
        domain="payment",
        rate_limit_aware=True,  # Default: True
    )
)

# Rate limiting is automatically handled!
result = handler.execute(call_external_api)
```

## Configuration

### Django Settings

```python
# settings.py

SELF_HEALING = {
    "RATE_LIMIT": {
        "BASE_DELAY": 1.0,        # Base delay in seconds
        "MAX_DELAY": 60.0,        # Maximum delay cap
        "JITTER_PERCENT": 30.0,   # ±30% random jitter
        "DEFAULT_RETRY_AFTER": 5.0,  # Default if no header
        "BACKOFF_MULTIPLIER": 2.0,   # Exponential factor
    },
    # Force specific backend (optional)
    "RATE_LIMIT_BACKEND": "auto",  # "redis", "database", "memory", or "auto"
}
```

### Programmatic Configuration

```python
from selfhealing.services import RateLimitCoordinator, RateLimitConfig
from selfhealing.adapters.rate_limit import RedisRateLimitStorage

config = RateLimitConfig(
    base_delay=2.0,
    max_delay=120.0,
    jitter_percent=25.0,
)

storage = RedisRateLimitStorage(my_redis_client)
coordinator = RateLimitCoordinator(storage=storage, config=config)
```

## How It Works

### Sequence Diagram

```
Worker A          Coordinator         Storage          Worker B
   │                  │                  │                 │
   │ ─────request────▶│                  │                 │
   │                  │──get_state()────▶│                 │
   │                  │◀────────────────│                 │
   │                  │  (no cooldown)   │                 │
   │◀───proceed───────│                  │                 │
   │                  │                  │                 │
   │ ─────429─────────│                  │                 │
   │                  │──set_cooldown()─▶│                 │
   │                  │──increment_429()─▶│                 │
   │                  │                  │                 │
   │                  │                  │                 │ ─────request────▶
   │                  │                  │                 │        │
   │                  │                  │◀──get_state()───│        │
   │                  │                  │────────────────▶│        │
   │                  │                  │  (in cooldown)  │        │
   │                  │                  │                 │◀──wait──
   │                  │                  │                 │  (30s)
```

### Backoff Calculation

```
delay = min(base_delay * (multiplier ^ consecutive_429s), max_delay)
delay = delay ± (delay * jitter_percent / 100)

Example (default config):
- 1st 429: 1s * 2^0 = 1s (±0.3s jitter) → 0.7-1.3s
- 2nd 429: 1s * 2^1 = 2s (±0.6s jitter) → 1.4-2.6s
- 3rd 429: 1s * 2^2 = 4s (±1.2s jitter) → 2.8-5.2s
- 4th 429: 1s * 2^3 = 8s (±2.4s jitter) → 5.6-10.4s
- ...capped at max_delay (60s)
```

## Database Schema

For the Database Adapter, the following table is required:

```sql
CREATE TABLE selfhealing_ratelimitstate (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) UNIQUE NOT NULL,
    cooldown_until DOUBLE PRECISION DEFAULT 0,
    consecutive_429s INTEGER DEFAULT 0,
    last_updated DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ratelimit_key ON selfhealing_ratelimitstate(key);
```

Django migration is provided in `selfhealing.adapters.django_repositories`.

## Performance Comparison

| Backend  | Latency | Multi-Server | Additional Infra |
|----------|---------|--------------|------------------|
| Redis    | ~0.1ms  | ✅ Yes        | Redis required   |
| Database | ~1-5ms  | ✅ Yes        | None (DB exists) |
| InMemory | ~0.01ms | ❌ No         | None             |

**Note:** Rate limit queries only occur on 429 responses, not every request. The slight latency difference is negligible.

## Best Practices

### 1. Use Domain-Specific Keys

```python
# Good - specific keys
coordinator.wait_if_needed("toss_payment_api")
coordinator.wait_if_needed("external_shipping_api")

# Avoid - too generic
coordinator.wait_if_needed("api")
```

### 2. Parse Retry-After Header

```python
def handle_429(response):
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        coordinator.on_rate_limited("api", retry_after=float(retry_after))
    else:
        coordinator.on_rate_limited("api")  # Uses default
```

### 3. Enable in Production

Rate limiting is enabled by default in `RetryHandler`. For testing, you can disable:

```python
config = RetryConfig(
    rate_limit_aware=False,  # Disable for testing
)
```

## Troubleshooting

### Storage Not Available

If you see warnings about storage unavailable:

```
[RateLimitStorage] Falling back to in-memory storage.
Self-DDoS prevention will only work within this process!
```

**Solutions:**
1. Check Redis connection
2. Verify database connectivity
3. Accept in-memory fallback for single-process deployments

### High Cooldown Times

If cooldowns are too long:

1. Check consecutive_429s counter
2. Reduce `max_delay` in config
3. Clear state: `coordinator.clear("api_key")`

## Migration from Legacy RateLimitTracker

The new `RateLimitCoordinator` replaces the legacy `RateLimitTracker`:

```python
# Before (legacy - single process only)
from selfhealing.services import RateLimitTracker

# After (new - distributed)
from selfhealing.services import RateLimitCoordinator

# Usage is similar but now works across servers!
```

## References

- [L3 Self-Healing Architecture](./L3_SELF_HEALING_SYSTEM.md)
- [Pluggable Architecture](./PLUGGABLE_ARCHITECTURE.md)
- [Self-Healing Load Test Plan](./SELF_HEALING_LOAD_TEST_PLAN.md)
