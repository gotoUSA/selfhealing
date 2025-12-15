# Observability & Forensics

This document details the observability, metrics, and forensic capabilities that provide visibility into system behavior.

---

## Capability 24: Prometheus Metrics

### 24.1 Purpose

Provides comprehensive Prometheus metrics for monitoring self-healing system health, recovery rates, and operational efficiency.

### 24.2 Metric Categories

#### 24.2.1 DLQ Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_dlq_items_total` | Counter | Total DLQ items created | domain, failure_type |
| `selfhealing_dlq_pending_count` | Gauge | Current pending DLQ items | domain |
| `selfhealing_dlq_items_by_status` | Gauge | DLQ items count by status | status |
| `selfhealing_dlq_created_total` | Counter | Total DLQ items created (rate calc) | domain |

#### 24.2.2 Retry Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_retry_attempts_distribution` | Histogram | Retry attempts before resolution | domain |
| `selfhealing_retry_outcomes_total` | Counter | Retry outcomes by result | domain, outcome |
| `selfhealing_retry_success_rate` | Gauge | Percentage of successful retries | domain |
| `selfhealing_retry_delay_seconds` | Histogram | Retry delay in seconds | domain |

#### 24.2.3 Recovery Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_recovery_time_seconds` | Histogram | Time from failure to resolution | domain, resolution_type |
| `selfhealing_sla_breach_total` | Counter | Total SLA breaches detected | domain |
| `selfhealing_human_review_queue_time_seconds` | Histogram | Time in human review queue | domain |

#### 24.2.4 Circuit Breaker Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_circuit_breaker_state` | Gauge | CB state (0=closed, 1=open, 2=half_open) | service_name |
| `selfhealing_circuit_breaker_failures_total` | Counter | Total CB failures | service_name |
| `selfhealing_circuit_breaker_trips_total` | Counter | Total times CB tripped to open | service_name |
| `selfhealing_circuit_breaker_transitions_total` | Counter | CB state transitions | service_name, from_state, to_state |
| `selfhealing_circuit_breaker_open_duration_seconds` | Histogram | Duration in open state | service_name |

#### 24.2.5 Replay Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_replay_attempts_total` | Counter | Total replay attempts | domain, replay_type |
| `selfhealing_replay_outcomes_total` | Counter | Replay outcomes | domain, outcome |
| `selfhealing_replay_duration_seconds` | Histogram | Replay operation duration | domain |

#### 24.2.6 Security Metrics

| Metric | Type | Description | Labels |
|--------|------|-------------|--------|
| `selfhealing_security_incidents_total` | Counter | Total security incidents | incident_type, severity |

### 24.3 Histogram Buckets

```python
# Recovery time buckets (seconds)
[60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
# = [1min, 5min, 15min, 30min, 1hr, 2hr, 4hr, 8hr, 24hr]

# Retry attempts buckets
[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

# Circuit breaker open duration buckets (seconds)
[60, 300, 600, 1800, 3600, 7200]
# = [1min, 5min, 10min, 30min, 1hr, 2hr]
```

### 24.4 Graceful Degradation

```python
# Metrics work with or without prometheus_client installed
try:
    from prometheus_client import Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # All metric calls become no-ops
```

### 24.5 Usage Examples

```python
metrics = SelfHealingMetrics(prefix="myapp_selfhealing")

# Record DLQ item creation
metrics.record_dlq_item_created(
    domain="payment",
    failure_type="PG_TIMEOUT"
)

# Record retry attempt
metrics.record_retry_attempt(
    domain="payment",
    attempt_count=3,
    outcome="success"
)

# Record recovery time
metrics.record_recovery_time(
    domain="payment",
    resolution_type="auto_replay",
    created_at=failure_time,
    resolved_at=now()
)

# Set circuit breaker state
metrics.set_circuit_breaker_state(
    service_name="payment_api",
    state="open"  # Sets gauge to 1
)
```

### 24.6 Domain Registration

```python
# Register custom domains for metrics tracking
from selfhealing.metrics.prometheus import register_domain

register_domain("my_custom_domain")
```

### 24.7 Code References

| Component | Location |
|-----------|----------|
| Metrics Class | `metrics/prometheus.py::SelfHealingMetrics` |

---

## Capability 25: Forensic Context Capture (Detailed)

### 25.1 Purpose

Provides complete debugging context for post-mortem analysis, enabling informed recovery decisions without accessing original systems.

### 25.2 Context Components

```python
@dataclass
class ForensicContext:
    # Timing
    request_timestamp: str = ""
    response_timestamp: str = ""
    latency_ms: int = 0
    
    # Retry History
    retry_history: List[RetryAttempt] = field(default_factory=list)
    
    # State Snapshots
    state_before: Optional[StateSnapshot] = None
    state_after: Optional[StateSnapshot] = None
    
    # Request Context
    client_ip: str = ""
    user_agent: str = ""
    session_id: str = ""
    
    # Task Context (async workers)
    task_name: str = ""
    task_id: str = ""
    queue_name: str = ""
    worker_id: str = ""
    
    # External System
    external_request_id: str = ""
    external_response_code: Optional[int] = None
    external_response_body: str = ""
    
    # Extension point
    extra: Dict[str, Any] = field(default_factory=dict)
```

### 25.3 Retry Attempt Recording

```python
@dataclass
class RetryAttempt:
    attempt: int
    error_code: str
    error_message: str
    attempted_at: str
    backoff_seconds: int = 0
```

### 25.4 State Snapshots

```python
@dataclass
class StateSnapshot:
    states: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def set_state(self, key: str, value: Any) -> None:
        self.states[key] = value
    
    def get_state(self, key: str, default: Any = None) -> Any:
        return self.states.get(key, default)
```

### 25.5 Usage Pattern

```python
context = ForensicContext()

# Capture timing
context.request_timestamp = now().isoformat()

# Capture state before operation
context.capture_state_before(
    order_status="pending",
    payment_status="awaiting"
)

# After operation fails
context.capture_state_after(
    order_status="pending",
    payment_status="failed"
)

# Add retry attempt
context.add_retry_attempt(
    attempt=1,
    error_code="PG_TIMEOUT",
    error_message="Connection timed out",
    backoff_seconds=4
)

# Serialize for storage
metadata = context.to_metadata(max_response_length=5000)
```

### 25.6 Reconstruction from Storage

```python
# Recreate context from stored metadata
context = ForensicContext.from_metadata(stored_metadata)
```

### 25.7 Code References

| Component | Location |
|-----------|----------|
| Core Forensic | `core/forensic.py` |
| Service Wrapper | `services/forensic_context.py` |

---

## Capability 26: Security Incident Records

### 26.1 Purpose

Maintains a structured record of security incidents for investigation, compliance, and forensic analysis.

### 26.2 Security Incident Data

```python
@dataclass
class SecurityIncidentData:
    # Identity
    id: int
    
    # Classification
    incident_type: str    # signature_invalid, data_tampered, etc.
    severity: str         # critical, high, medium
    status: str           # open, investigating, resolved, false_positive
    
    # Source Information
    source_ip: Optional[str] = None
    user_agent: str = ""
    user_id: Optional[int] = None
    
    # Entity references (domain-neutral)
    entity_refs: dict[str, int] = field(default_factory=dict)
    
    # Details
    description: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    
    # Investigation
    assigned_to_id: Optional[int] = None
    investigation_notes: str = ""
    resolved_at: Optional[datetime] = None
    
    # Lifecycle
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

### 26.3 Investigation States

```python
class SecurityIncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"
```

### 26.4 Code References

| Component | Location |
|-----------|----------|
| Data Type | `interfaces/repositories.py::SecurityIncidentData` |
| Repository | `interfaces/repositories.py::SecurityIncidentRepository` |
| Service | `services/security_violation_service.py` |

---

## Capability 27: Audit Trail

### 27.1 Purpose

Maintains a complete audit trail of all control actions for compliance and post-incident analysis.

### 27.2 Audit Data Captured

For each Control API action:

| Field | Description |
|-------|-------------|
| `request_id` | Unique identifier for the action |
| `correlation_id` | Link to related actions |
| `service_name` | Affected service |
| `action` | Action performed (allow, block, etc.) |
| `reason` | Human-provided reason |
| `reason_classification` | Auto-classified reason category |
| `environment` | Environment (test, chaos, ops) |
| `risk_level` | Assessed risk level |
| `actor` | Who performed the action |
| `actor_role` | Role of the actor |
| `status` | Outcome (success, error) |
| `system_state` | Resulting system state |
| `evidence` | Captured evidence snapshot |
| `timestamp` | When the action occurred |

### 27.3 Evidence Gathering

```python
def _gather_evidence(self, service_name: str) -> dict:
    """Gather current system evidence for audit."""
    cb_state = self.circuit_breaker.get_or_create_state(service_name)
    
    return {
        "circuit_breaker": {
            "state": cb_state.state,
            "failure_count": cb_state.failure_count,
            "success_count": cb_state.success_count,
            "manually_controlled": cb_state.manually_controlled,
            "control_reason": cb_state.control_reason,
        },
        "timestamp": now().isoformat(),
    }
```

### 27.4 Code References

| Component | Location |
|-----------|----------|
| Audit Recording | `services/control_api_service.py::_record_audit` |

---

## Capability 28: DLQ Statistics

### 28.1 Purpose

Provides aggregated statistics about DLQ entries for dashboard and monitoring purposes.

### 28.2 Statistics Available

```python
def get_stats(self) -> dict[str, Any]:
    """Get DLQ statistics."""
    return self.repository.get_statistics()
```

Typical statistics include:
- Total entries by status
- Entries by domain
- Entries by failure type
- Average retry count
- Average resolution time
- SLA breach count

### 28.3 Code References

| Component | Location |
|-----------|----------|
| Statistics | `services/dlq_service.py::get_stats` |
| Repository Method | `interfaces/repositories.py::FailedOperationRepository::get_statistics` |

---

## Capability 29: Circuit Breaker State Visibility

### 29.1 Purpose

Provides comprehensive visibility into circuit breaker states across all monitored services.

### 29.2 State Information

```python
def get_all_states(self) -> list[dict[str, Any]]:
    """Get all circuit breaker states."""
    states = self.repository.get_all_states()
    return [
        {
            "service_name": s.service_name,
            "state": s.state,
            "failure_count": s.failure_count,
            "success_count": s.success_count,
            "last_failure_at": s.last_failure_at,
            "opened_at": s.opened_at,
            "manually_controlled": s.manually_controlled,
            "controlled_by_id": s.controlled_by_id,
            "control_reason": s.control_reason,
        }
        for s in states
    ]
```

### 29.3 Protection Status

```python
def get_protection_status(self, service_name: str) -> dict[str, Any]:
    """Get comprehensive protection status for a service."""
    return {
        "service_name": service_name,
        "circuit_state": self.get_state(service_name),
        "circuit_breaker_enabled": self.is_enabled,
        "rate_limit_cascade": {
            "detected": self.check_rate_limit_cascade(service_name),
            "count_in_window": tracker.get_rate_limit_count(...),
            "threshold": config.rate_limit_cascade_threshold,
            "window_seconds": config.rate_limit_cascade_window_seconds,
        },
        "self_ddos_protection": {
            "enabled": config.self_ddos_protection_enabled,
            "detected": self.is_self_ddos_detected(service_name),
            "request_count_in_window": tracker.get_request_count(...),
            "threshold": config.self_ddos_request_threshold,
            "window_seconds": config.self_ddos_window_seconds,
        },
        "backoff": {
            "current_level": tracker.get_backoff_level(service_name),
            "suggested_delay_seconds": self.calculate_adaptive_backoff(service_name),
        },
    }
```

### 29.4 Code References

| Component | Location |
|-----------|----------|
| State Visibility | `services/circuit_breaker/service.py::get_all_states` |
| Protection Status | `services/circuit_breaker/protection.py::get_protection_status` |
