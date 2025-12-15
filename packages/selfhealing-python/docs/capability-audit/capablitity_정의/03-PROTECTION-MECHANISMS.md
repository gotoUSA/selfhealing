# Protection Mechanisms

This document details the protective capabilities that prevent cascade failures and system overload.

---

## Capability 6: Circuit Breaker

### 6.1 Purpose

Implements the Circuit Breaker pattern to prevent requests to failing external services, allowing them time to recover while protecting the calling system from timeouts and resource exhaustion.

### 6.2 Circuit States

```
┌──────────────────────────────────────────────────────────────┐
│                    Circuit Breaker States                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   CLOSED ◄────────────────────────────────────► OPEN        │
│     │         failure_count >= threshold            │        │
│     │                                               │        │
│     │         success_count >= success_threshold    │        │
│     │                     or                        │        │
│     │            manual force_close                 │        │
│     │                                               │        │
│     │                                               ▼        │
│     │                                          HALF_OPEN     │
│     │                                               │        │
│     │◄──────────────────────────────────────────────┘        │
│                  (recovery test succeeds)                    │
│                                                              │
│   Legend:                                                    │
│   CLOSED = Normal operation, requests pass through           │
│   OPEN = Blocking requests, service considered down          │
│   HALF_OPEN = Testing recovery, limited requests allowed     │
└──────────────────────────────────────────────────────────────┘
```

### 6.3 Trigger Conditions

| Transition | Condition |
|------------|-----------|
| CLOSED → OPEN | `failure_count >= failure_threshold` (default: 5) |
| OPEN → HALF_OPEN | `recovery_timeout` elapsed (default: 60s) |
| HALF_OPEN → CLOSED | `success_count >= success_threshold` (default: 2) |
| HALF_OPEN → OPEN | Any failure during half-open testing |
| Any → OPEN | Manual `force_open()` by operator |
| Any → CLOSED | Manual `force_close()` by operator |

### 6.4 Behavioral Flow

```python
def should_allow(service_name: str) -> bool:
    state = get_or_create_state(service_name)
    
    if state.state == "closed":
        return True
    
    if state.state == "open":
        # Check recovery timeout
        if elapsed >= recovery_timeout:
            transition_to_half_open(service_name)
            return True
        return False
    
    # half_open: allow limited requests
    return True
```

### 6.5 Manual Control Operations

The system provides full manual control for operators:

| Operation | Method | Effect |
|-----------|--------|--------|
| Force Open | `force_open(service_name, reason)` | Block all requests |
| Force Close | `force_close(service_name, reason, trigger_replay)` | Allow requests, optionally replay DLQ |
| Reset | `reset(service_name)` | Clear counters, return to CLOSED |
| Extend TTL | `extend_manual_override(service_name, minutes)` | Extend manual control duration |

### 6.6 Manual Override TTL

Manual overrides have a configurable Time-To-Live to prevent "forgotten" blocks:

```python
# Default: 90 minutes
manual_override_ttl_minutes: int = 90

# When expired:
# OPEN → HALF_OPEN (gradual recovery testing)
# Reason updated: "original reason [EXPIRED]"
```

### 6.7 Atomic Operations

All state transitions use atomic repository operations to prevent race conditions:

```python
# Atomic force open
success, previous_state, new_state = repository.atomic_force_open(
    service_name=service_name,
    reason=reason,
    controlled_by_id=user_id,
    ttl_minutes=90,
)

# Atomic force close  
success, previous_state, new_state = repository.atomic_force_close(
    service_name=service_name,
    reason=reason,
    controlled_by_id=user_id,
)
```

### 6.8 Configuration

```python
@dataclass
class CircuitBreakerConfig:
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    success_threshold: int = 2
    half_open_max_calls: int = 3
    half_open_request_limit: int = 10
    manual_override_ttl_minutes: int = 90
    max_pending_duration_hours: int = 4  # SLA
    max_retry_lifetime_hours: int = 24
```

### 6.9 Code References

| Component | Location |
|-----------|----------|
| Service | `services/circuit_breaker/service.py` |
| Manual Control | `services/circuit_breaker/manual_control.py` |
| Protection Mix-in | `services/circuit_breaker/protection.py` |
| Configuration | `services/circuit_breaker/config.py` |

---

## Capability 7: Rate Limit Cascade Detection

### 7.1 Purpose

Detects when an external service is returning excessive 429 (Too Many Requests) responses and automatically opens the circuit breaker to prevent a "storm" of rate-limited requests.

### 7.2 Trigger Conditions

```python
# When 429 count in window exceeds threshold
if rate_limit_count >= rate_limit_cascade_threshold:
    # Auto-open circuit breaker
    force_open(
        service_name,
        reason=f"Rate limit cascade detected ({count} 429s in {window}s)"
    )
```

### 7.3 Configuration

```python
# Default settings
rate_limit_cascade_threshold: int = 10  # 429 responses
rate_limit_cascade_window_seconds: int = 60  # window
```

### 7.4 Behavioral Flow

```
┌─────────────────────────────────────────┐
│ Receive 429 Response                    │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ record_rate_limit_response(service)     │
│ tracker.record_rate_limit(service)      │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ count = get_rate_limit_count(           │
│   service, window_seconds               │
│ )                                       │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┴─────────────┐
    │ count >= threshold?   │
    │                       │
    ▼ Yes                   ▼ No
┌────────────────┐    ┌──────────────┐
│ Auto-open CB   │    │ Continue     │
│ Increment      │    │ monitoring   │
│ backoff level  │    └──────────────┘
└────────────────┘
```

### 7.5 Code References

| Component | Location |
|-----------|----------|
| Protection Mixin | `services/circuit_breaker/protection.py::record_rate_limit_response` |
| Rate Limit Tracker | `services/circuit_breaker/rate_limit_tracker.py` |

---

## Capability 8: Self-DDoS Prevention

### 8.1 Purpose

Prevents the system from overwhelming external services with retry requests after a failure, which could prevent the external service from recovering.

### 8.2 Problem Statement

Without Self-DDoS prevention:
```
Service fails → 100 workers retry simultaneously → Service overwhelmed → Never recovers
```

With Self-DDoS prevention:
```
Service fails → Global cooldown set → All workers wait → Service recovers → Gradual resume
```

### 8.3 Trigger Conditions

```python
# Self-DDoS detection
if request_count > self_ddos_request_threshold:
    # Suggest backoff but don't block
    suggested_backoff = calculate_adaptive_backoff(service)
    return True, suggested_backoff  # Allow with delay suggestion
```

### 8.4 Configuration

```python
# Default settings
self_ddos_protection_enabled: bool = True
self_ddos_request_threshold: int = 100  # requests
self_ddos_window_seconds: int = 10  # window
self_ddos_backoff_multiplier: float = 2.0
```

### 8.5 Adaptive Backoff Calculation

```python
def calculate_adaptive_backoff(service_name) -> float:
    backoff_level = tracker.get_backoff_level(service_name)
    
    # Exponential backoff with cap
    base_backoff = 1.0
    max_backoff = 60.0
    
    backoff = min(
        base_backoff * (multiplier ** backoff_level),
        max_backoff
    )
    
    # Add jitter (±25%) to prevent thundering herd
    jitter = backoff * 0.25 * (random() * 2 - 1)
    
    return max(0.1, backoff + jitter)
```

### 8.6 Code References

| Component | Location |
|-----------|----------|
| Protection Mixin | `services/circuit_breaker/protection.py` |
| Rate Limit Tracker | `services/circuit_breaker/rate_limit_tracker.py` |

---

## Capability 9: Rate Limit Coordinator

### 9.1 Purpose

Provides distributed coordination of rate limiting across multiple workers/processes, ensuring that global cooldown states are shared via a common storage backend.

### 9.2 Design Philosophy

```
"어떤 고객 환경이든 100% Self-DDoS 차단"
"100% Self-DDoS prevention regardless of customer infrastructure"

Fallback chain:
1. Redis (fastest, if available)
2. Database (100% compatible, guaranteed available)
3. In-Memory (single process, for testing)
```

### 9.3 Storage Backends

| Backend | Type | Use Case |
|---------|------|----------|
| `RedisRateLimitStorage` | Distributed | Production with Redis |
| `DatabaseRateLimitStorage` | Distributed | Production without Redis |
| `InMemoryRateLimitStorage` | Local | Testing, single process |

### 9.4 Behavioral Flow

```python
# Before making external request
result = coordinator.wait_if_needed("payment_api")
if result.waited:
    logger.info(f"Waited {result.wait_time}s for cooldown")

# After receiving 429
coordinator.on_rate_limited(
    key="payment_api",
    retry_after=response.headers.get("Retry-After"),
)

# After successful request
coordinator.on_success("payment_api")
```

### 9.5 Rate Limit State

```python
@dataclass
class RateLimitState:
    key: str                    # e.g., "payment_api"
    cooldown_until: float       # Unix timestamp
    consecutive_429s: int       # For exponential backoff
    last_updated: float         # Last state update
    
    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until
    
    @property
    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooldown_until - time.time())
```

### 9.6 Configuration

```python
@dataclass
class RateLimitCoordinatorConfig:
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter_percent: float = 30.0
    default_retry_after: float = 5.0
    backoff_multiplier: float = 2.0
```

### 9.7 Code References

| Component | Location |
|-----------|----------|
| Coordinator | `services/rate_limit_coordinator.py` |
| Storage Interface | `interfaces/rate_limit_storage.py` |
| Redis Adapter | `adapters/rate_limit/redis_adapter.py` |
| DB Adapter | `adapters/rate_limit/database_adapter.py` |
| Memory Adapter | `adapters/rate_limit/memory_adapter.py` |

---

## Capability 10: Connection Pool Monitoring

### 10.1 Purpose

Monitors database connection pool health to detect exhaustion, leaks, and degradation before they cause outages.

### 10.2 Pool Health States

```python
class PoolHealthStatus(str, Enum):
    HEALTHY = "healthy"           # Normal operation
    WARNING = "warning"           # 70%+ usage
    CRITICAL = "critical"         # 90%+ usage
    EXHAUSTED = "exhausted"       # No available connections
    LEAK_SUSPECTED = "leak_suspected"  # Connections held too long
```

### 10.3 Metrics Tracked

```python
@dataclass
class PoolStats:
    pool_name: str
    max_connections: int
    active_connections: int
    available_connections: int
    waiting_requests: int = 0
    
    @property
    def usage_percent(self) -> float:
        return (active_connections / max_connections) * 100
    
    @property
    def is_exhausted(self) -> bool:
        return available_connections == 0 and waiting_requests > 0
```

### 10.4 Leak Detection

```python
# Connections held longer than threshold are suspected leaks
leak_threshold_seconds: float = 300.0  # 5 minutes

def detect_leaks() -> LeakReport:
    suspected = []
    for conn_id, info in active_connections.items():
        if info.acquired_at < threshold_time:
            suspected.append(info)
    return LeakReport(suspected_leaks=suspected)
```

### 10.5 Code References

| Component | Location |
|-----------|----------|
| Pool Monitor | `core/pool_monitor.py` |
| Pool Watchdog | `core/pool_watchdog.py` |

---

## Capability 11: Connection Pool Watchdog

### 11.1 Purpose

Takes automatic recovery actions when pool issues are detected.

### 11.2 Recovery Actions

```python
class RecoveryAction(str, Enum):
    NONE = "none"
    ALERT_ONLY = "alert_only"
    CLOSE_LEAKED = "close_leaked"
    EXPAND_POOL = "expand_pool"
    CIRCUIT_BREAK = "circuit_break"
```

### 11.3 Recovery Flow

```
┌─────────────────────────────────────────┐
│ check_and_recover()                     │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ status, stats = monitor.check_health()  │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┼─────────────────────┐
    ▼         ▼                     ▼
┌────────┐ ┌──────────┐ ┌───────────────────┐
│HEALTHY │ │LEAK      │ │EXHAUSTED          │
│No-op   │ │Close     │ │Expand or          │
│        │ │leaked    │ │Circuit Break      │
└────────┘ └──────────┘ └───────────────────┘
```

### 11.4 Configuration

```python
PoolWatchdog(
    monitor=pool_monitor,
    recovery_handler=my_handler,
    alert_callback=send_alert,
    auto_close_leaked=True,      # Auto-close leaked connections
    auto_expand=False,           # Auto-expand pool
    max_expansion=10,            # Max additional connections
)
```

### 11.5 Code References

| Component | Location |
|-----------|----------|
| Pool Watchdog | `core/pool_watchdog.py` |
| Recovery Handler | `core/pool_watchdog.py::PoolRecoveryHandler` |
