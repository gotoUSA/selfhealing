# Celery Signal Hooks Integration

This document details the Celery signal hooks capability for **non-invasive** self-healing integration.

---

## Capability 36: Celery Signal Hooks (Zero-Code Integration)

### 36.1 Purpose

Provides automatic self-healing integration through Celery's signal system. When installed, **no code changes are required** in the host application's tasks - failures are automatically tracked for:
- Circuit Breaker state management
- DLQ storage
- Forensic context capture
- Metrics recording

### 36.2 Key Benefits

| Benefit | Description |
|---------|-------------|
| **Zero Code Changes** | No modifications to existing Celery tasks required |
| **Plug-and-Play** | Single setup call enables all features |
| **Configurable** | Fine-grained control via environment variables |
| **Non-Invasive** | Works as an external observer, never modifies task behavior |

### 36.3 Signal Handlers

| Signal | Handler | Purpose |
|--------|---------|---------|
| `task_failure` | `on_task_failure` | Records CB failure, stores to DLQ, captures forensics |
| `task_success` | `on_task_success` | Records CB success, triggers recovery replay |
| `task_retry` | `on_task_retry` | Tracks retry attempts for metrics |

### 36.4 Installation

```python
# In your celery.py or app initialization
from selfhealing.adapters.celery import setup_selfhealing_signals

# Simple setup - uses environment variables
setup_selfhealing_signals()

# Or with explicit configuration
setup_selfhealing_signals(
    enabled=True,
    cb_enabled=True,
    dlq_enabled=True,
    task_domain_mapping={
        'myapp.tasks.process_payment': 'payment',
        'myapp.tasks.send_notification': 'notification',
    },
)
```

### 36.5 Configuration via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SELFHEALING_ENABLED` | `true` | Master switch for all hooks |
| `SELFHEALING_CB_ENABLED` | `true` | Enable circuit breaker recording |
| `SELFHEALING_DLQ_ENABLED` | `true` | Enable DLQ storage |
| `SELFHEALING_METRICS_ENABLED` | `true` | Enable metrics recording |
| `SELFHEALING_FORENSICS_ENABLED` | `true` | Enable forensic context capture |
| `SELFHEALING_CB_FAILURE_THRESHOLD` | `5` | Failures before CB opens |
| `SELFHEALING_CB_RECOVERY_TIMEOUT` | `60` | Seconds before half-open |
| `SELFHEALING_CB_SUCCESS_THRESHOLD` | `2` | Successes to close from half-open |
| `SELFHEALING_TASK_DOMAIN_MAPPING` | `{}` | JSON mapping of task names to domains |

### 36.6 Behavioral Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CELERY TASK EXECUTION                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐                                                    │
│  │ Task Starts │                                                    │
│  └──────┬──────┘                                                    │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │              Task Executes (Your Code)                   │        │
│  └─────────────────────────┬───────────────────────────────┘        │
│                            │                                        │
│              ┌─────────────┴─────────────┐                          │
│              ▼                           ▼                          │
│  ┌───────────────────┐       ┌───────────────────┐                  │
│  │    SUCCESS        │       │     FAILURE       │                  │
│  └─────────┬─────────┘       └─────────┬─────────┘                  │
│            │                           │                            │
│            ▼                           ▼                            │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │              SIGNAL HOOKS (Automatic)                    │        │
│  │                                                          │        │
│  │  ┌─────────────────┐    ┌─────────────────────────────┐  │        │
│  │  │ on_task_success │    │      on_task_failure        │  │        │
│  │  │                 │    │                             │  │        │
│  │  │ • CB: record    │    │ • CB: record failure        │  │        │
│  │  │   success       │    │ • DLQ: store if max retries │  │        │
│  │  │ • Metrics:      │    │ • Forensics: capture        │  │        │
│  │  │   success rate  │    │ • Metrics: failure rate     │  │        │
│  │  └─────────────────┘    └─────────────────────────────┘  │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 36.7 Automatic Features via Signal Hooks

| Feature | How It Works | Customer Code Required? |
|---------|--------------|------------------------|
| **Circuit Breaker** | Failures increment counter, opens when threshold reached | ❌ No |
| **DLQ Storage** | Failed tasks automatically stored with context | ❌ No |
| **Forensic Capture** | Exception, args, kwargs captured automatically | ❌ No |
| **Metrics** | Success/failure rates recorded | ❌ No |
| **Replay on Recovery** | CB close triggers automatic DLQ replay | ❌ No |

### 36.8 Domain Resolution

The system automatically determines the domain for failed tasks:

1. **Explicit Mapping** (highest priority)
   ```python
   task_domain_mapping={'myapp.tasks.process_payment': 'payment'}
   ```

2. **Pattern Matching**
   - Task name contains "payment" → domain: `payment`
   - Task name contains "order" → domain: `order`
   - etc.

3. **First Segment** (fallback)
   - `myapp.tasks.process` → domain: `myapp`

### 36.9 Failure Type Classification

Exceptions are automatically classified:

| Exception Pattern | Failure Type |
|-------------------|--------------|
| `TimeoutError`, `timeout` in message | `TIMEOUT` |
| `ConnectionError`, `connection` | `CONNECTION_ERROR` |
| `429`, `rate limit` | `RATE_LIMITED` |
| `401`, `403`, `auth` | `AUTH_ERROR` |
| `502`, `503`, `504` | `EXTERNAL_SERVICE_ERROR` |
| `payment`, `pg`, `toss` | `PAYMENT_ERROR` |
| Other | `UNKNOWN_ERROR` |

### 36.10 Optional Decorator for Fine-Grained Control

For tasks that need explicit configuration:

```python
from selfhealing.adapters.celery import selfhealing_task

@app.task
@selfhealing_task(domain='payment', service_name='toss_payment')
def process_payment(order_id):
    # Your task logic
    pass
```

### 36.11 Disconnecting Hooks

For testing or temporary disabling:

```python
from selfhealing.adapters.celery import disconnect_selfhealing_signals

disconnect_selfhealing_signals()
```

### 36.12 Code References

| Component | Location |
|-----------|----------|
| Signal Hooks | `adapters/celery/signal_hooks.py` |
| Setup Function | `signal_hooks.py::setup_selfhealing_signals` |
| Configuration | `signal_hooks.py::SignalHooksConfig` |
| Decorator | `signal_hooks.py::selfhealing_task` |

---

## What Can vs Cannot Be Done via Signal Hooks

### ✅ Automatic via Signal Hooks (70%)

| Capability | Method |
|------------|--------|
| Circuit Breaker State | `task_failure` / `task_success` signals |
| DLQ Storage | `task_failure` signal + context capture |
| Forensic Context | Exception + args/kwargs from signal |
| Metrics Recording | Success/failure counters |
| Conditional Replay | CB close triggers replay task |
| Security Violation | Exception pattern matching |

### ⚠️ Requires Middleware Config (15%)

| Capability | Method |
|------------|--------|
| Rate Limit Protection | Django/FastAPI middleware |
| Request-Level Metrics | Middleware instrumentation |

### ❌ Requires Explicit Code (15%)

| Capability | Why Signal Hooks Can't Do It |
|------------|------------------------------|
| **Idempotency** | Requires key generation **before** task execution |
| **Retry Orchestration** | Retry decision must be made **inside** task |
| **Custom Backoff** | Celery's retry mechanism is task-controlled |

---

## Integration Comparison

| Integration Method | Code Changes | Features | Best For |
|-------------------|--------------|----------|----------|
| **Signal Hooks** | 1 line setup | CB, DLQ, Forensics, Metrics | Most applications |
| **Decorator** | 1 decorator per task | Same + explicit domain/service | Fine-grained control |
| **Full Integration** | Explicit service calls | All features including idempotency | Maximum control |
