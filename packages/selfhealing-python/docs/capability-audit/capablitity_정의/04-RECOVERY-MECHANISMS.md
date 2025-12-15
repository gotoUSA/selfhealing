# Recovery Mechanisms

This document details the recovery and fallback capabilities that enable graceful degradation and eventual consistency.

---

## Capability 12: Fallback Strategies

### 12.1 Purpose

Provides graceful degradation strategies when primary operations fail, allowing the system to continue functioning with reduced capabilities rather than failing entirely.

### 12.2 Fallback Modes

```python
class FallbackMode(str, Enum):
    FAIL_FAST = "fail_fast"           # Immediate failure
    USE_CACHE = "use_cache"           # Return cached value
    USE_DEFAULT = "use_default"       # Return default value
    DEGRADE_GRACEFULLY = "degrade"    # Reduced functionality
    RETRY_ALTERNATIVE = "retry_alt"   # Try alternate path
```

### 12.3 Fallback Strategies Available

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `SimpleFallback` | Try fallback_fn then default_value | Basic retry |
| `PartitionAwareFallback` | Adapts based on which connections are up | Partial outages |
| `CacheFirstFallback` | Cache → DB fallback chain | Read-heavy operations |

### 12.4 Partition-Aware Fallback

The system can detect partial network partitions and adapt:

```python
@dataclass
class PartitionState:
    db_available: bool = True
    cache_available: bool = True
    external_apis: Dict[str, bool] = field(default_factory=dict)
    
    @property
    def is_partial_partition(self) -> bool:
        """True if some but not all connections are down"""
        statuses = [self.db_available, self.cache_available] + ...
        return any(statuses) and not all(statuses)
```

### 12.5 Fallback Flow

```
┌─────────────────────────────────────────┐
│ Execute primary_fn()                    │
└─────────────┬───────────────────────────┘
              │ Exception
              ▼
┌─────────────────────────────────────────┐
│ 1. Try explicit fallback_fn             │
│    (if provided)                        │
└─────────────┬───────────────────────────┘
              │ Also failed
              ▼
┌─────────────────────────────────────────┐
│ 2. Check partition state                │
│    Cache down + DB up → DB fallback     │
│    DB down + Cache up → Cache fallback  │
└─────────────┬───────────────────────────┘
              │ Also failed
              ▼
┌─────────────────────────────────────────┐
│ 3. Use default_value                    │
│    (if provided)                        │
└─────────────┬───────────────────────────┘
              │ No default
              ▼
┌─────────────────────────────────────────┐
│ 4. Return FAIL_FAST result              │
└─────────────────────────────────────────┘
```

### 12.6 Result Object

```python
@dataclass
class FallbackResult(Generic[T]):
    value: Optional[T]
    used_fallback: bool
    fallback_mode: Optional[FallbackMode] = None
    original_error: Optional[str] = None
    
    @property
    def success(self) -> bool:
        return self.value is not None or (
            self.used_fallback and 
            self.fallback_mode != FallbackMode.FAIL_FAST
        )
```

### 12.7 Code References

| Component | Location |
|-----------|----------|
| Fallback Strategies | `core/fallback_strategy.py` |
| Partition State | `core/connection_health.py::PartitionState` |

---

## Capability 13: Connection Health Monitoring

### 13.1 Purpose

Tracks health of different connection types independently, enabling targeted recovery strategies based on which specific connections are failing.

### 13.2 Connection Types Monitored

```python
class ConnectionType(str, Enum):
    DATABASE = "database"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
    MESSAGE_QUEUE = "message_queue"
```

### 13.3 Health Statuses

```python
class ConnectionStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"      # Some failures, not critical
    UNHEALTHY = "unhealthy"    # Consecutive failures >= threshold
    UNKNOWN = "unknown"        # Not yet checked
```

### 13.4 Health Check Registration

```python
monitor = DefaultConnectionHealthMonitor(failure_threshold=3)

# Register health checks
monitor.register_health_check(
    connection_type=ConnectionType.DATABASE,
    name="primary_db",
    check_fn=lambda: db.ping() is True
)

monitor.register_health_check(
    connection_type=ConnectionType.CACHE,
    name="redis",
    check_fn=lambda: redis.ping() == "PONG"
)
```

### 13.5 Health Data Captured

```python
@dataclass
class ConnectionHealth:
    connection_type: ConnectionType
    name: str
    status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    consecutive_failures: int = 0
    error_message: str = ""
    latency_ms: Optional[float] = None
```

### 13.6 Code References

| Component | Location |
|-----------|----------|
| Health Monitor | `core/connection_health.py` |
| Partition State | `core/connection_health.py::PartitionState` |

---

## Capability 14: Graceful Shutdown Coordination

### 14.1 Purpose

Manages graceful shutdown with in-flight request handling, ensuring operations complete before the process terminates.

### 14.2 Shutdown Phases

```python
class ShutdownPhase(str, Enum):
    RUNNING = "running"         # Normal operation
    DRAINING = "draining"       # Reject new, complete existing
    TERMINATING = "terminating" # Force shutdown pending
    TERMINATED = "terminated"   # Shutdown complete
```

### 14.3 Request Tracking

```python
# On request start
tracker.start_request(
    request_id="req-123",
    endpoint="/api/payment",
    method="POST"
)

# On request end
tracker.end_request("req-123", success=True)

# During shutdown
pending = tracker.get_pending_requests()
```

### 14.4 Shutdown Flow

```
┌─────────────────────────────────────────┐
│ Signal Received (SIGTERM/SIGINT)        │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Phase: DRAINING                         │
│ - Reject new requests                   │
│ - Wait for in-flight to complete        │
│ - Max wait: drain_timeout               │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┴─────────────┐
    ▼                       ▼
┌──────────────┐    ┌─────────────────────┐
│ All drained  │    │ Timeout reached     │
│              │    │ Pending requests:   │
│              │    │ → Abort             │
│              │    │ → Alert             │
└──────┬───────┘    └──────────┬──────────┘
       │                       │
       ▼                       ▼
┌─────────────────────────────────────────┐
│ Phase: TERMINATED                       │
│ handler.on_drain_complete() or          │
│ handler.on_force_shutdown(pending)      │
└─────────────────────────────────────────┘
```

### 14.5 Tracked Request Data

```python
@dataclass
class TrackedRequest:
    request_id: str
    started_at: datetime
    endpoint: str = ""
    method: str = ""
    state: RequestState = RequestState.IN_PROGRESS
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def duration_seconds(self) -> float:
        return (now() - started_at).total_seconds()
```

### 14.6 Code References

| Component | Location |
|-----------|----------|
| Coordinator | `core/shutdown_coordinator.py` |
| Request Tracker | `core/shutdown_coordinator.py::RequestTracker` |

---

## Capability 15: TLS/SSL Error Handling

### 15.1 Purpose

Classifies and handles TLS-related failures, distinguishing between retryable transient errors and non-retryable certificate issues.

### 15.2 TLS Error Types

```python
class TLSErrorType(str, Enum):
    CERTIFICATE_EXPIRED = "cert_expired"
    CERTIFICATE_NOT_YET_VALID = "cert_not_yet_valid"
    CERTIFICATE_REVOKED = "cert_revoked"
    CERTIFICATE_HOSTNAME_MISMATCH = "cert_hostname_mismatch"
    CERTIFICATE_SELF_SIGNED = "cert_self_signed"
    CERTIFICATE_CHAIN_INVALID = "cert_chain_invalid"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    HANDSHAKE_FAILURE = "handshake_failure"
    PROTOCOL_VERSION_MISMATCH = "protocol_mismatch"
    CONNECTION_RESET = "connection_reset"
    UNKNOWN = "unknown"
```

### 15.3 Severity Classification

```python
class TLSErrorSeverity(str, Enum):
    CRITICAL = "critical"  # Immediate alert (cert expired)
    HIGH = "high"          # Urgent action needed
    MEDIUM = "medium"      # Monitor
    LOW = "low"            # Transient, retry may resolve
```

### 15.4 Error Classification

```python
@dataclass
class TLSErrorInfo:
    error_type: TLSErrorType
    severity: TLSErrorSeverity
    endpoint: str
    error_message: str
    is_retryable: bool
    detected_at: datetime
    certificate_expiry: Optional[datetime] = None
    days_until_expiry: Optional[int] = None
    recommended_action: str = ""
```

### 15.5 Retryable vs Non-Retryable

| Error Type | Retryable | Action |
|------------|-----------|--------|
| HANDSHAKE_TIMEOUT | Yes | Retry with backoff |
| CONNECTION_RESET | Yes | Retry with backoff |
| CERTIFICATE_EXPIRED | No | Alert, renew cert |
| CERTIFICATE_REVOKED | No | Alert, obtain new cert |
| HOSTNAME_MISMATCH | No | Check URL and SAN |

### 15.6 Code References

| Component | Location |
|-----------|----------|
| TLS Handler | `core/tls_handler.py` |
| Error Classifier | `core/tls_handler.py::TLSErrorClassifier` |

---

## Capability 16: Certificate Expiry Monitoring

### 16.1 Purpose

Proactively monitors certificate expiration dates to alert before expiry, preventing TLS failures in production.

### 16.2 Certificate Status

```python
class CertificateStatus(str, Enum):
    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"  # < 30 days
    CRITICAL = "critical"             # < 7 days
    EXPIRED = "expired"
```

### 16.3 Certificate Info

```python
@dataclass
class CertificateInfo:
    endpoint: str
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    status: CertificateStatus
    days_remaining: int
    checked_at: datetime
    
    @property
    def is_valid(self) -> bool:
        return self.status != CertificateStatus.EXPIRED
    
    @property
    def needs_attention(self) -> bool:
        return self.status in {EXPIRING_SOON, CRITICAL}
    
    @property
    def is_urgent(self) -> bool:
        return self.status in {CRITICAL, EXPIRED}
```

### 16.4 Configuration

```python
CertificateExpiryMonitor(
    warning_days=30,     # Start warning
    critical_days=7,     # Critical alert
    alert_callback=send_cert_alert
)
```

### 16.5 Code References

| Component | Location |
|-----------|----------|
| Cert Monitor | `core/cert_monitor.py` |
