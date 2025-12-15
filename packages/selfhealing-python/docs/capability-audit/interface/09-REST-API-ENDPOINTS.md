# REST API Endpoints & Health Probes

This document details the REST API capabilities that were discovered during the re-audit.

---

## Capability 36: Control API REST Endpoints

### 36.1 Purpose

Provides a complete REST API for operational control of the self-healing system via Django REST Framework.

### 36.2 Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/self-healing/control/` | POST | Execute control action |
| `/api/self-healing/status/` | GET | Get all service states |
| `/api/self-healing/status/{service_name}/` | GET | Get specific service state |
| `/api/self-healing/audit/` | GET | Get audit logs |
| `/api/self-healing/allow/{service_name}/` | POST | Quick allow (close CB) |
| `/api/self-healing/block/{service_name}/` | POST | Quick block (open CB) |
| `/api/self-healing/reset/{service_name}/` | POST | Quick reset |

### 36.3 Control Actions

```python
class ControlAPIActions:
    ALLOW = "allow"           # Close circuit breaker
    BLOCK = "block"           # Open circuit breaker
    OVERRIDE = "override"     # Temporarily bypass rules
    RESET = "reset"           # Revert to default
    INJECT_FAILURE = "inject_failure"  # Simulate failures (non-ops)
    INJECT_SUCCESS = "inject_success"  # Record successes (test only)
```

### 36.4 Request/Response

```python
# Request
{
    "service_name": "payment_api",
    "action": "block",
    "reason": "PG maintenance window",
    "environment": "ops",
    "ttl_minutes": 60
}

# Response
{
    "status": "success",
    "action_applied": "block",
    "system_state": "open",
    "effective_until": "2025-12-15T11:00:00Z",
    "reason_classification": "maintenance-window",
    "correlation_id": "uuid"
}
```

### 36.5 Code References

| Component | Location |
|-----------|----------|
| Views | `api/django/views/circuit_breaker.py` |
| Serializers | `api/django/serializers.py` |
| URLs | `api/django/urls.py` |

---

## Capability 37: DLQ Management REST API

### 37.1 Purpose

REST endpoints for Dead Letter Queue management and replay operations.

### 37.2 Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/self-healing/dlq/` | GET | List DLQ entries |
| `/api/self-healing/dlq/{id}/` | GET | Get DLQ entry detail |
| `/api/self-healing/dlq/replay/` | POST | Trigger batch replay |
| `/api/self-healing/dlq/{id}/retry/` | POST | Retry single entry |
| `/api/self-healing/dlq/{id}/resolve/` | POST | Mark as resolved |
| `/api/self-healing/dlq/cleanup/stats/` | GET | Get cleanup statistics |
| `/api/self-healing/dlq/cleanup/archive/` | POST | Archive old resolved entries |
| `/api/self-healing/dlq/cleanup/purge/` | POST | Permanently delete archived |

### 37.3 Batch Replay Request

```python
# POST /api/self-healing/dlq/replay/
{
    "domain": "payment",     # Optional filter
    "batch_size": 50         # Max items to replay
}

# Response
{
    "status": "success",
    "total": 50,
    "success_count": 45,
    "failed_count": 3,
    "skipped_count": 2
}
```

### 37.4 Cleanup Statistics Response

```python
# GET /api/self-healing/dlq/cleanup/stats/
{
    "total": 1250,
    "by_status": {
        "pending": 45,
        "resolved": 1100,
        "archived": 100,
        "rejected": 5
    },
    "resolved_older_than_30_days": 800,
    "archived_older_than_90_days": 50,
    "recommendations": {
        "can_archive": 800,
        "can_purge": 50
    }
}
```

### 37.5 Code References

| Component | Location |
|-----------|----------|
| Views | `api/django/views/dlq.py` |
| Serializers | `api/django/serializers.py` |

---

## Capability 38: Kubernetes Health Probes

### 38.1 Purpose

Provides Kubernetes-compatible health check endpoints for container orchestration.

### 38.2 Available Endpoints

| Endpoint | Method | Purpose | Returns |
|----------|--------|---------|---------|
| `/api/self-healing/health/` | GET | Full health check | Detailed status |
| `/api/self-healing/health/live/` | GET | Liveness probe | 200 if alive |
| `/api/self-healing/health/ready/` | GET | Readiness probe | 200 if ready |
| `/api/self-healing/health/pool/` | GET | Connection pool health | Pool stats |
| `/api/self-healing/health/ping/` | GET | Simple ping | "pong" |

### 38.3 Liveness Probe

```python
class LivenessView(APIView):
    """Returns 200 if the application is running."""
    permission_classes = []  # Public endpoint
    
    def get(self, request):
        return Response({"status": "alive"})
```

**Kubernetes Configuration:**
```yaml
livenessProbe:
  httpGet:
    path: /api/self-healing/health/live/
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
```

### 38.4 Readiness Probe

```python
class ReadinessView(APIView):
    """Returns 200 if ready to serve traffic."""
    
    def get(self, request):
        # Checks all database connections
        for alias in connections:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        return Response({"status": "ready"})
```

**Kubernetes Configuration:**
```yaml
readinessProbe:
  httpGet:
    path: /api/self-healing/health/ready/
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
```

### 38.5 Connection Pool Health

```python
# GET /api/self-healing/health/pool/
{
    "status": "healthy",
    "pool_info": {
        "alias": "default",
        "vendor": "postgresql",
        "is_usable": true
    }
}
```

### 38.6 Code References

| Component | Location |
|-----------|----------|
| Views | `api/django/views/health.py` |

---

## Capability 39: Metrics REST API

### 39.1 Purpose

Provides REST endpoint for self-healing metrics suitable for trend analysis and dashboard integration.

### 39.2 Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/self-healing/metrics/` | GET | Get comprehensive metrics |

### 39.3 Response Format

```python
{
    "timestamp": "2025-12-15T10:00:00Z",
    "dlq": {
        "pending": 45,
        "by_domain": {
            "payment": 20,
            "notification": 15,
            "webhook": 10
        },
        "by_status": {
            "pending": 45,
            "resolved": 1100,
            "rejected": 5
        }
    },
    "circuit_breakers": {
        "total": 5,
        "open": 1,
        "half_open": 0,
        "closed": 4,
        "services": [
            {
                "service_name": "payment_api",
                "state": "open",
                "failure_count": 12
            }
        ]
    },
    "retry": {
        "total_attempts": 500,
        "success_rate": 85.0
    }
}
```

### 39.4 Code References

| Component | Location |
|-----------|----------|
| View | `api/django/views/health.py::SelfHealingMetricsView` |

---

## Capability 40: Dashboard Summary API

### 40.1 Purpose

Provides aggregated data for administrative dashboards.

### 40.2 Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/self-healing/dashboard/` | GET | Get dashboard summary |

### 40.3 Response Format

```python
{
    "summary": {
        "total_dlq_items": 1250,
        "pending_items": 45,
        "circuits_open": 1,
        "sla_breaches_today": 3
    },
    "recent_incidents": [...],
    "top_failure_types": [...],
    "circuit_breaker_states": [...]
}
```

### 40.4 Code References

| Component | Location |
|-----------|----------|
| View | `api/django/views/dashboard.py::DashboardSummaryView` |

---

## Capability 41: FastAPI ASGI Middleware

### 41.1 Purpose

Provides self-healing integration for FastAPI applications via ASGI middleware.

### 41.2 Features

| Feature | Description |
|---------|-------------|
| Request ID Generation | Generates/propagates X-Request-ID header |
| Request Tracking | Tracks in-flight requests for graceful shutdown |
| Shutdown Protection | Returns 503 when shutting down |
| Response Timing | Adds X-Response-Time header |
| Path Exclusion | Skips health/metrics endpoints |

### 41.3 Usage

```python
from fastapi import FastAPI
from selfhealing.adapters.fastapi import SelfHealingMiddleware
from selfhealing.core.shutdown_coordinator import RequestTracker

app = FastAPI()
tracker = RequestTracker()

app.add_middleware(
    SelfHealingMiddleware,
    request_tracker=tracker,
    exclude_paths=["/health", "/metrics"],
    enable_timing=True,
)
```

### 41.4 Graceful Shutdown Response

When the system is draining requests during shutdown:

```python
# Returns 503 Service Unavailable
{
    "error": "Service is shutting down",
    "retry_after": 30
}
```

### 41.5 Code References

| Component | Location |
|-----------|----------|
| Middleware | `adapters/fastapi/middleware.py::SelfHealingMiddleware` |

---

## Capability 42: Service Factory Functions

### 42.1 Purpose

Provides factory functions for creating service instances with dependency injection support.

### 42.2 Available Factories

```python
from selfhealing.services.factory import (
    create_dlq_service,
    create_replay_service,
    create_circuit_breaker_service,
    create_security_violation_service,
)

# With mock repository for testing
mock_repo = Mock(spec=FailedOperationRepository)
dlq_service = create_dlq_service(repository=mock_repo)

# Without injection (uses default adapters)
dlq_service = create_dlq_service()
```

### 42.3 Factory Functions

| Function | Returns | DI Parameter |
|----------|---------|--------------|
| `create_dlq_service()` | DLQService | repository |
| `create_replay_service()` | ReplayService | failed_operation_repository |
| `create_circuit_breaker_service()` | CircuitBreakerService | repository |
| `create_security_violation_service()` | SecurityViolationService | repository |

### 42.4 Code References

| Component | Location |
|-----------|----------|
| Factory | `services/factory/service.py` |

---

## Capability 43: Celery Task Integration

### 43.1 Purpose

Provides Celery tasks for background DLQ processing and replay operations.

### 43.2 Available Tasks

```python
from selfhealing.adapters.celery.tasks import (
    replay_single_dlq_item,
    replay_batch_dlq_items,
    cleanup_expired_dlq_items,
)

# Async replay
replay_single_dlq_item.delay(dlq_id=123)

# Batch replay
replay_batch_dlq_items.delay(domain="payment", batch_size=50)
```

### 43.3 Task Queue Adapter

```python
class CeleryTaskQueueAdapter(TaskQueueInterface):
    def enqueue(self, task_name: str, *args, **kwargs):
        task = self._get_task(task_name)
        return task.delay(*args, **kwargs)
    
    def get_task_status(self, task_id: str):
        result = AsyncResult(task_id)
        return result.status
```

### 43.4 Code References

| Component | Location |
|-----------|----------|
| Tasks | `adapters/celery/tasks.py` |
| Adapter | `adapters/queues/celery_adapter.py` |

---

## Document Navigation

| Document | Focus |
|----------|-------|
| [01-OVERVIEW-SCOPE.md](01-OVERVIEW-SCOPE.md) | Architecture & scope |
| [02-CORE-CAPABILITIES.md](02-CORE-CAPABILITIES.md) | DLQ, Retry, Idempotency |
| [03-PROTECTION-MECHANISMS.md](03-PROTECTION-MECHANISMS.md) | Circuit Breaker, Rate Limiting |
| [04-RECOVERY-MECHANISMS.md](04-RECOVERY-MECHANISMS.md) | Fallback, Shutdown, TLS |
| [05-OPERATIONAL-GOVERNANCE.md](05-OPERATIONAL-GOVERNANCE.md) | Control API, Security |
| [06-OBSERVABILITY-FORENSICS.md](06-OBSERVABILITY-FORENSICS.md) | Metrics, Forensics |
| [07-INTEGRATION-ADAPTERS.md](07-INTEGRATION-ADAPTERS.md) | Adapters, Interfaces |
| [08-SUMMARY-NONGOALS.md](08-SUMMARY-NONGOALS.md) | Summary & Non-Goals |
| **09-REST-API-ENDPOINTS.md** | REST API & Health (This Document) |
