# OpenTelemetry Adapter

This document details the optional OpenTelemetry adapter for exporting self-healing signals to external APM platforms.

---

## Capability 30: OpenTelemetry Integration

### 30.1 Purpose

Provides **optional** export of self-healing decision events to external APM platforms (Datadog, New Relic, Elastic, CloudWatch) via OpenTelemetry protocol.

**IMPORTANT:**
- This is an **optional extension**, NOT a replacement for Prometheus
- Prometheus/Grafana integration remains the primary observability layer
- Core system works identically without OpenTelemetry installed

### 30.2 Non-Goals (Explicitly NOT Implemented)

| ❌ Non-Goal | Rationale |
|-------------|-----------|
| Automatic request tracing | Too verbose, performance impact |
| HTTP/DB/framework instrumentation | Handled by dedicated APM agents |
| Per-request span creation | Self-healing operates at decision level |
| Deep framework hooks | Invasive, maintenance burden |
| Hot-path logging | Performance concern |
| Prometheus replacement | Prometheus is primary metrics layer |
| Mandatory OTel dependency | Must remain optional |

### 30.3 What Is Exported

Only meaningful self-healing lifecycle signals:

| Signal Category | Events |
|----------------|--------|
| Circuit Breaker | State transitions (OPEN, HALF_OPEN, CLOSED) |
| Retry | Attempts, exhaustion, success after retry |
| DLQ | Enqueue, replay start, replay success/failure, abort |
| Rate Limit | Triggered, cascade detected, self-DDoS detected |
| SLO | Threshold approaching, breached, recovered |
| Policy | Evaluation outcomes, auto-heal allowed/blocked |
| Manual Control | Manual overrides, operator interventions |

### 30.4 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Self-Healing Engine                              │
├──────────────────┬──────────────────┬───────────────────────────────┤
│ Circuit Breaker  │ DLQ Service      │ Retry Handler                 │
│ Service          │                  │                               │
└────────┬─────────┴────────┬─────────┴───────────────┬───────────────┘
         │                  │                         │
         ▼                  ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  OpenTelemetry Adapter                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │ Event       │  │ Decision    │  │ Config      │                  │
│  │ Emitter     │  │ Span Mgr    │  │ Module      │                  │
│  └──────┬──────┘  └──────┬──────┘  └─────────────┘                  │
│         │                │                                           │
│         ▼                ▼                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  OpenTelemetry SDK (optional)  OR  NO-OP Fallback           │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │  External APM Platforms       │
                    │  (Datadog, New Relic, etc.)   │
                    └───────────────────────────────┘
```

### 30.5 Configuration

#### 30.5.1 Configuration Model

```python
@dataclass(frozen=True)
class OpenTelemetryConfig:
    # Core Settings
    enabled: bool = False                    # Master switch, default OFF
    service_name: str = "selfhealing-service"
    environment: str = "development"
    
    # Feature Toggles
    decision_span_enabled: bool = True       # Coarse-grained spans
    event_export_enabled: bool = True        # Structured events
    
    # Export Configuration
    endpoint: Optional[str] = None           # OTLP endpoint
    export_timeout_seconds: int = 30
    
    # Event Filtering
    export_circuit_breaker_events: bool = True
    export_retry_events: bool = True
    export_dlq_events: bool = True
    export_rate_limit_events: bool = True
    export_slo_events: bool = True
    export_policy_events: bool = True
```

#### 30.5.2 Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SELFHEALING_OTEL_ENABLED` | Enable OpenTelemetry export | `false` |
| `SELFHEALING_OTEL_SERVICE_NAME` | Service identifier | `selfhealing-service` |
| `SELFHEALING_OTEL_ENVIRONMENT` | Environment name | `development` |
| `SELFHEALING_OTEL_ENDPOINT` | OTLP exporter endpoint | SDK default |
| `SELFHEALING_OTEL_DECISION_SPAN_ENABLED` | Enable decision spans | `true` |
| `SELFHEALING_OTEL_EVENT_EXPORT_ENABLED` | Enable events | `true` |

#### 30.5.3 Django Settings

```python
# settings.py
SELFHEALING_OPENTELEMETRY = {
    "enabled": True,
    "service_name": "my-payment-service",
    "environment": "production",
    "endpoint": "http://otel-collector:4317",
    "decision_span_enabled": True,
    "export_circuit_breaker_events": True,
    "export_dlq_events": True,
}
```

### 30.6 Graceful Degradation

The adapter uses lazy imports and feature detection:

```python
# OpenTelemetry SDK detection
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    # All operations become NO-OP
```

**Behavior when OpenTelemetry is not installed:**
- No `ImportError` raised
- All API calls are safe (NO-OP)
- Zero performance overhead
- System functions normally

### 30.7 Event Types

```python
class SelfHealingEventType(str, Enum):
    # Circuit Breaker
    CIRCUIT_BREAKER_OPENED = "selfhealing.circuit_breaker.opened"
    CIRCUIT_BREAKER_CLOSED = "selfhealing.circuit_breaker.closed"
    CIRCUIT_BREAKER_HALF_OPENED = "selfhealing.circuit_breaker.half_opened"
    CIRCUIT_BREAKER_MANUAL_OVERRIDE = "selfhealing.circuit_breaker.manual_override"
    
    # Retry
    RETRY_ATTEMPT = "selfhealing.retry.attempt"
    RETRY_EXHAUSTED = "selfhealing.retry.exhausted"
    RETRY_SUCCESS = "selfhealing.retry.success"
    
    # DLQ
    DLQ_ENQUEUED = "selfhealing.dlq.enqueued"
    DLQ_REPLAY_STARTED = "selfhealing.dlq.replay_started"
    DLQ_REPLAY_SUCCESS = "selfhealing.dlq.replay_success"
    DLQ_REPLAY_FAILED = "selfhealing.dlq.replay_failed"
    DLQ_REPLAY_ABORTED = "selfhealing.dlq.replay_aborted"
    
    # Rate Limit
    RATE_LIMIT_TRIGGERED = "selfhealing.rate_limit.triggered"
    RATE_LIMIT_CASCADE_DETECTED = "selfhealing.rate_limit.cascade_detected"
    SELF_DDOS_DETECTED = "selfhealing.rate_limit.self_ddos_detected"
    
    # SLO
    SLO_THRESHOLD_APPROACHING = "selfhealing.slo.threshold_approaching"
    SLO_BREACHED = "selfhealing.slo.breached"
    SLO_RECOVERED = "selfhealing.slo.recovered"
    
    # Policy
    POLICY_EVALUATED = "selfhealing.policy.evaluated"
    POLICY_AUTO_HEAL_ALLOWED = "selfhealing.policy.auto_heal_allowed"
    POLICY_AUTO_HEAL_BLOCKED = "selfhealing.policy.auto_heal_blocked"
```

### 30.8 Decision Spans

Decision spans represent **coarse-grained decision cycles** (seconds to minutes), NOT individual requests.

#### 30.8.1 Decision Types

```python
class DecisionType(str, Enum):
    POLICY_EVALUATION = "policy_evaluation"
    MANUAL_OVERRIDE = "manual_override"
    CIRCUIT_BREAKER_RECOVERY = "circuit_breaker_recovery"
    DLQ_BATCH_REPLAY = "dlq_batch_replay"
    SLO_BREACH_RESPONSE = "slo_breach_response"
    RATE_LIMIT_COOLDOWN = "rate_limit_cooldown"
```

#### 30.8.2 Span Lifecycle

**CRITICAL:** Span boundaries are explicitly triggered by the self-healing engine. The adapter does NOT autonomously decide when cycles start/end.

```python
# Span context tracks the decision cycle
@dataclass
class DecisionSpanContext:
    span_id: str
    decision_type: str
    started_at: datetime
    is_active: bool = True
    domain: Optional[str] = None
    
    def add_event(self, name: str, attributes: Dict) -> None: ...
    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_outcome(self, outcome: str, attributes: Dict) -> None: ...
    def get_duration_ms(self) -> int: ...
```

### 30.9 Usage Examples

#### 30.9.1 Enable via Configuration

```python
from selfhealing.adapters.observability import (
    OpenTelemetryConfig,
    get_opentelemetry_adapter,
)

# Initialize with explicit configuration
config = OpenTelemetryConfig(
    enabled=True,
    service_name="payment-service",
    environment="production",
    endpoint="http://otel-collector:4317",
)

adapter = get_opentelemetry_adapter(config)
```

#### 30.9.2 Emit Circuit Breaker Event

```python
from selfhealing.adapters.observability import get_opentelemetry_adapter

adapter = get_opentelemetry_adapter()

adapter.emit_circuit_breaker_transition(
    service_name="payment_api",
    from_state="closed",
    to_state="open",
    reason="failure_threshold_exceeded",
    manually_controlled=False,
    failure_count=5,
)
```

#### 30.9.3 Use Decision Spans

```python
from selfhealing.adapters.observability import (
    start_decision_span,
    end_decision_span,
)
from selfhealing.adapters.observability.opentelemetry import (
    DecisionType,
    DecisionOutcome,
)

# Start a policy evaluation cycle
span_ctx = start_decision_span(
    decision_type=DecisionType.POLICY_EVALUATION,
    attributes={"target_service": "payment_api"},
    domain="payment",
)

try:
    # Perform policy evaluation
    span_ctx.add_event("policy_rule_checked", {"rule": "max_retries"})
    
    # Simulate decision
    if should_auto_heal:
        span_ctx.set_outcome(DecisionOutcome.AUTO_HEALED)
    else:
        span_ctx.set_outcome(DecisionOutcome.MANUAL_INTERVENTION)
        
finally:
    end_decision_span(span_ctx)
```

#### 30.9.4 Convenience Event Emission

```python
from selfhealing.adapters.observability import emit_selfhealing_event
from selfhealing.adapters.observability.opentelemetry import (
    SelfHealingEventType,
    EventAttribute,
)

# Emit using convenience function (uses global adapter)
emit_selfhealing_event(
    SelfHealingEventType.DLQ_ENQUEUED,
    attributes={
        EventAttribute.DLQ_FAILURE_TYPE: "PG_TIMEOUT",
        EventAttribute.DLQ_ID: 12345,
    },
    domain="payment",
)
```

### 30.10 Data Safety

The adapter enforces strict data safety constraints:

| ✅ Safe to Export | ❌ Never Export |
|-------------------|-----------------|
| Service names | Request payloads |
| State transitions | HTTP headers |
| Decision outcomes | PII (names, emails) |
| Error types (codes) | Customer identifiers |
| Retry counts | Session tokens |
| Domain identifiers | API keys |

### 30.11 Decision-Trace Philosophy

The adapter supports the system's core observability philosophy:

> **No logs or telemetry when healthy and stable.**

Events are emitted ONLY when:
- Thresholds are approached (warning level)
- Decisions are evaluated (policy checks)
- Automatic actions are triggered
- Automatic actions are blocked
- Manual intervention occurs

This enables external observers to answer:
> "Why did the system take this action at this time?"

### 30.12 Module Structure

```
selfhealing/adapters/observability/
├── __init__.py                  # Re-exports
└── opentelemetry/
    ├── __init__.py              # Public API
    ├── config.py                # Configuration model
    ├── adapter.py               # Main adapter implementation
    ├── events.py                # Event types and emission
    ├── spans.py                 # Decision span management
    └── noop.py                  # NO-OP fallback implementations
```

### 30.13 Code References

| Component | Location |
|-----------|----------|
| Configuration | `adapters/observability/opentelemetry/config.py` |
| Main Adapter | `adapters/observability/opentelemetry/adapter.py` |
| Event Types | `adapters/observability/opentelemetry/events.py` |
| Decision Spans | `adapters/observability/opentelemetry/spans.py` |
| NO-OP Fallback | `adapters/observability/opentelemetry/noop.py` |

---

## Related Capabilities

- **Capability 24: Prometheus Metrics** - Primary metrics layer (unchanged)
- **Capability 25: Forensic Context Capture** - Debugging context
- **Capability 27: Audit Trail** - Control action recording
