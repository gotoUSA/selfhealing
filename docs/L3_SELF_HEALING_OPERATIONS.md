# L3 Self-Healing Reliability Layer — Operations Guide

> **Version**: 1.0  
> **Last Updated**: 2025-12-08  
> **Status**: Production Ready  
> **Prerequisite**: Read [L3 Architecture](./L3_SELF_HEALING_ARCHITECTURE.md) first

---

## Table of Contents

1. [Dead Letter Queue (DLQ)](#1-dead-letter-queue-dlq)
2. [DLQ Replay Policy](#2-dlq-replay-policy)
3. [Recovery SLA](#3-recovery-sla)
4. [Escalation & Notifications](#4-escalation--notifications)
5. [Security Violation Handling](#5-security-violation-handling)
6. [Forensic Context](#6-forensic-context)
7. [Observability & Metrics](#7-observability--metrics)
8. [Testing Strategy](#8-testing-strategy)
9. [Operational Runbooks](#9-operational-runbooks)
10. [Configuration Reference](#10-configuration-reference)
11. [Implementation Checklist](#11-implementation-checklist)

---

## 1. Dead Letter Queue (DLQ)

### Purpose

The DLQ is the **central storage for unrecoverable failures**. Every failure that cannot be auto-recovered lands here for human review.

### DLQ Table Schema

```sql
CREATE TABLE failed_operations (
    -- Primary Key
    id                  BIGSERIAL PRIMARY KEY,
    
    -- Domain & Classification
    domain              VARCHAR(50) NOT NULL,      -- 'payment', 'point', 'inventory', 'webhook', 'notification'
    failure_type        VARCHAR(100) NOT NULL,     -- 'PG_TIMEOUT', 'AMOUNT_MISMATCH', etc.
    
    -- Status (State Machine)
    status              VARCHAR(30) DEFAULT 'pending',
                        -- 'pending', 'reviewing', 'replayed', 'resolved', 'rejected', 'expired'
    
    -- Original References
    order_id            BIGINT,
    payment_id          BIGINT,
    user_id             BIGINT,
    
    -- Snapshot Data (for recovery without original records)
    snapshot_data       JSONB NOT NULL DEFAULT '{}',
    
    -- Error Information
    error_code          VARCHAR(100),
    error_message       TEXT,
    
    -- Retry Tracking
    retry_count         INTEGER DEFAULT 0,
    max_retries         INTEGER DEFAULT 3,
    last_retry_at       TIMESTAMP WITH TIME ZONE,
    
    -- Forensic Context
    request_data        JSONB DEFAULT '{}',        -- Original request payload
    response_data       JSONB DEFAULT '{}',        -- External system response
    metadata            JSONB DEFAULT '{}',        -- Additional debug info
    
    -- Resolution
    resolved_at         TIMESTAMP WITH TIME ZONE,
    resolved_by_id      BIGINT,                    -- FK to users
    resolution_type     VARCHAR(30),               -- 'auto_replay', 'manual_fix', 'rejected', 'expired'
    resolution_note     TEXT,
    
    -- Recovery Hints
    next_action_hint    VARCHAR(200),              -- e.g., "Verify payment in PG admin"
    recommended_action  VARCHAR(30),               -- 'replay', 'manual_check', 'escalate', 'archive'
    
    -- Lifecycle
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at          TIMESTAMP WITH TIME ZONE,  -- Auto-archive after retention period
    
    -- Indexes
    CONSTRAINT fk_order FOREIGN KEY (order_id) REFERENCES orders(id),
    CONSTRAINT fk_payment FOREIGN KEY (payment_id) REFERENCES payments(id),
    CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT fk_resolved_by FOREIGN KEY (resolved_by_id) REFERENCES users(id)
);

-- Indexes for common queries
CREATE INDEX idx_failed_ops_status ON failed_operations(status);
CREATE INDEX idx_failed_ops_domain ON failed_operations(domain);
CREATE INDEX idx_failed_ops_created ON failed_operations(created_at);
CREATE INDEX idx_failed_ops_domain_status ON failed_operations(domain, status);
```

### Django Model

```python
class FailedOperation(models.Model):
    """Dead Letter Queue for unrecoverable failures"""
    
    class Domain(models.TextChoices):
        PAYMENT = 'payment', 'Payment'
        POINT = 'point', 'Point'
        INVENTORY = 'inventory', 'Inventory'
        WEBHOOK = 'webhook', 'Webhook'
        NOTIFICATION = 'notification', 'Notification'
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending Review'
        REVIEWING = 'reviewing', 'Under Review'
        REPLAYED = 'replayed', 'Replay Queued'
        RESOLVED = 'resolved', 'Resolved'
        REJECTED = 'rejected', 'Rejected (Unrecoverable)'
        EXPIRED = 'expired', 'Retention Expired'
    
    # Classification
    domain = models.CharField(max_length=50, choices=Domain.choices)
    failure_type = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    
    # References
    order = models.ForeignKey('Order', null=True, on_delete=models.SET_NULL)
    payment = models.ForeignKey('Payment', null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey('User', null=True, on_delete=models.SET_NULL)
    
    # Snapshot & Error
    snapshot_data = models.JSONField(default=dict)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    
    # Retry tracking
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=3)
    last_retry_at = models.DateTimeField(null=True)
    
    # Forensic data
    request_data = models.JSONField(default=dict)
    response_data = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    
    # Resolution
    resolved_at = models.DateTimeField(null=True)
    resolved_by = models.ForeignKey('User', null=True, on_delete=models.SET_NULL, related_name='resolved_failures')
    resolution_type = models.CharField(max_length=30, blank=True)
    resolution_note = models.TextField(blank=True)
    
    # Hints
    next_action_hint = models.CharField(max_length=200, blank=True)
    recommended_action = models.CharField(max_length=30, blank=True)
    
    # Lifecycle
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True)
    
    class Meta:
        db_table = 'failed_operations'
        indexes = [
            models.Index(fields=['domain', 'status']),
            models.Index(fields=['created_at']),
        ]
    
    def mark_as_resolved(self, resolved_by, note="", resolution_type="manual_fix"):
        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        self.resolved_by = resolved_by
        self.resolution_type = resolution_type
        self.resolution_note = note
        self.save()
    
    def queue_for_replay(self):
        if self.retry_count >= 2:
            raise ValueError("Maximum replay attempts (2) exceeded")
        self.status = self.Status.REPLAYED
        self.retry_count += 1
        self.last_retry_at = timezone.now()
        self.save()
```

---

## 2. DLQ Replay Policy

### Replay Types

| Type | Description | Trigger | Max Attempts |
|------|-------------|---------|--------------|
| **Manual Replay** | Operator selects individual items | Admin UI button | 2 |
| **Batch Replay** | Operator selects multiple items by filter | Admin bulk action | 2 |
| **Conditional Replay** | Auto-replay when external system recovers | CB close event | 1 |

### Replay Flow

```
┌─────────────────┐
│  PENDING_REVIEW │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Success     ┌──────────┐
│     REPLAYED    │ ───────────────► │ RESOLVED │
│  (Re-queued)    │                  └──────────┘
└────────┬────────┘
         │ Failure
         ▼
┌─────────────────┐
│  retry_count++  │
└────────┬────────┘
         │
         ├── retry_count < 2 ───► Back to PENDING_REVIEW
         │
         └── retry_count >= 2 ──► REJECTED (Permanent Archive)
```

### Replay Implementation

```python
@shared_task(name="replay_failed_operation")
def replay_failed_operation(failed_op_id: int) -> dict:
    """Replay a single DLQ item"""
    
    failed_op = FailedOperation.objects.select_for_update().get(id=failed_op_id)
    
    # Check replay eligibility
    if failed_op.retry_count >= 2:
        failed_op.status = FailedOperation.Status.REJECTED
        failed_op.resolution_note = "Maximum replay attempts exceeded"
        failed_op.save()
        return {"success": False, "reason": "max_replays_exceeded"}
    
    # Mark as replaying
    failed_op.queue_for_replay()
    
    # Route to appropriate handler
    handler = get_replay_handler(failed_op.domain)
    
    try:
        result = handler.replay(failed_op)
        
        if result.success:
            failed_op.mark_as_resolved(
                resolved_by=None,  # System
                resolution_type="auto_replay"
            )
            return {"success": True, "result": result.data}
        else:
            failed_op.status = FailedOperation.Status.PENDING
            failed_op.save()
            return {"success": False, "reason": result.error}
            
    except Exception as e:
        failed_op.status = FailedOperation.Status.PENDING
        failed_op.error_message = str(e)
        failed_op.save()
        return {"success": False, "reason": str(e)}
```

### Batch Replay

```python
@shared_task(name="batch_replay_by_failure_type")
def batch_replay_by_failure_type(
    failure_type: str,
    max_items: int = 100
) -> dict:
    """Replay all pending items of a specific failure type"""
    
    pending_items = FailedOperation.objects.filter(
        failure_type=failure_type,
        status=FailedOperation.Status.PENDING,
        retry_count__lt=2
    )[:max_items]
    
    results = {"total": 0, "success": 0, "failed": 0}
    
    for item in pending_items:
        results["total"] += 1
        task_result = replay_failed_operation.delay(item.id)
        # Note: Results tracked asynchronously
    
    return results
```

---

## 3. Recovery SLA

### SLA by Domain and Severity

| Domain | Severity | Auto-Retry Window | Human Review SLA | Escalation Trigger |
|--------|----------|-------------------|------------------|-------------------|
| **Payment** | Critical | 5 min (3 attempts) | 1 hour | 30 min no response |
| **Payment** | High | 5 min (3 attempts) | 4 hours | 2 hours no response |
| **Point** | High | 10 min (3 attempts) | 4 hours | 2 hours no response |
| **Inventory** | High | 2 min (2 attempts) | 2 hours | 1 hour no response |
| **Webhook** | Medium | 15 min (5 attempts) | 8 hours | 4 hours no response |
| **Notification** | Low | 1 hour (3 attempts) | 24 hours | None |

### SLA Monitoring

```python
# Scheduled task: Check SLA violations
@shared_task(name="check_sla_violations")
def check_sla_violations():
    """Check for DLQ items exceeding SLA and escalate"""
    
    now = timezone.now()
    
    SLA_THRESHOLDS = {
        'payment': timedelta(hours=1),
        'point': timedelta(hours=4),
        'inventory': timedelta(hours=2),
        'webhook': timedelta(hours=8),
        'notification': timedelta(hours=24),
    }
    
    violations = []
    
    for domain, threshold in SLA_THRESHOLDS.items():
        overdue_items = FailedOperation.objects.filter(
            domain=domain,
            status=FailedOperation.Status.PENDING,
            created_at__lt=now - threshold
        )
        
        for item in overdue_items:
            violations.append(item)
            escalate_sla_violation(item)
    
    return {"violations_count": len(violations)}
```

---

## 4. Escalation & Notifications

### Notification Channels by Severity

| Severity | Channels | Recipients |
|----------|----------|------------|
| **Critical** | Slack + Email + SMS + PagerDuty | On-call engineer, CTO |
| **High** | Slack + Email | Operations team |
| **Medium** | Slack | Development team |
| **Low** | Email (digest) | Development team (daily) |

### Severity Mapping

```python
SEVERITY_BY_FAILURE_TYPE = {
    # Critical - Immediate attention required
    'AMOUNT_MISMATCH_PG_RESPONSE': 'critical',
    'AMOUNT_MISMATCH_WEBHOOK': 'critical',
    'SECURITY_SIGNATURE_INVALID': 'critical',
    'NEGATIVE_BALANCE_DETECTED': 'critical',
    
    # High - Within hours
    'PG_TIMEOUT_MAX_RETRIES': 'high',
    'DB_SAVE_AFTER_PG_SUCCESS': 'high',
    'DUPLICATE_DEDUCTION': 'high',
    'STOCK_NEGATIVE': 'high',
    
    # Medium - Same day
    'WEBHOOK_ORDER_NOT_FOUND': 'medium',
    'STOCK_MISMATCH': 'medium',
    
    # Low - Best effort
    'SMTP_TIMEOUT': 'low',
    'FCM_ERROR': 'low',
}
```

### Notification Implementation

```python
def send_failure_notification(failed_op: FailedOperation):
    """Send notification based on severity"""
    
    severity = SEVERITY_BY_FAILURE_TYPE.get(
        failed_op.failure_type, 
        'medium'
    )
    
    message = format_failure_message(failed_op)
    
    if severity == 'critical':
        send_slack_alert(message, channel="#critical-alerts")
        send_email_alert(message, recipients=get_oncall_emails())
        send_sms_alert(message, recipients=get_oncall_phones())
        trigger_pagerduty(failed_op)
        
    elif severity == 'high':
        send_slack_alert(message, channel="#ops-alerts")
        send_email_alert(message, recipients=get_ops_emails())
        
    elif severity == 'medium':
        send_slack_alert(message, channel="#dev-alerts")
        
    else:  # low
        queue_for_daily_digest(failed_op)
```

### Alert Message Format

```python
def format_failure_message(failed_op: FailedOperation) -> str:
    return f"""
🚨 *Self-Healing Alert*

*Domain*: {failed_op.domain}
*Failure Type*: {failed_op.failure_type}
*Status*: {failed_op.status}

*Order ID*: {failed_op.order_id}
*User ID*: {failed_op.user_id}
*Amount*: {failed_op.snapshot_data.get('amount', 'N/A')}

*Error*: {failed_op.error_message[:200]}

*Retry Count*: {failed_op.retry_count}/{failed_op.max_retries}
*Created*: {failed_op.created_at}

*Recommended Action*: {failed_op.next_action_hint}

<Admin Link: {get_admin_url(failed_op)}>
"""
```

---

## 5. Security Violation Handling

### Security Violations NEVER Self-Heal

Security violations are **immediately blocked** and routed to the security team.

### Security Violation Types

| Type | Detection | Immediate Action |
|------|-----------|------------------|
| `WEBHOOK_SIGNATURE_INVALID` | HMAC mismatch | Block, log source IP |
| `PAYMENT_AMOUNT_TAMPERED` | Request != Response | Block, freeze order |
| `TOKEN_FORGED` | Invalid signature | Invalidate all sessions |
| `UNAUTHORIZED_ACCESS` | Role/permission violation | Log, block |
| `RATE_LIMIT_ABUSE` | Excessive requests | Temporary ban |

### Security Incident Model

```python
class SecurityIncident(models.Model):
    """Separate table for security violations - never in main DLQ"""
    
    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Critical'
        HIGH = 'high', 'High'
        MEDIUM = 'medium', 'Medium'
    
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        INVESTIGATING = 'investigating', 'Investigating'
        RESOLVED = 'resolved', 'Resolved'
        FALSE_POSITIVE = 'false_positive', 'False Positive'
    
    incident_type = models.CharField(max_length=100)
    severity = models.CharField(max_length=20, choices=Severity.choices)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)
    
    # Source
    source_ip = models.GenericIPAddressField(null=True)
    user_agent = models.TextField(blank=True)
    user = models.ForeignKey('User', null=True, on_delete=models.SET_NULL)
    
    # Details
    description = models.TextField()
    raw_request = models.JSONField(default=dict)
    
    # Response
    action_taken = models.TextField(blank=True)  # e.g., "Session invalidated"
    investigated_by = models.ForeignKey('User', null=True, on_delete=models.SET_NULL, related_name='investigations')
    resolved_at = models.DateTimeField(null=True)
    
    # Timestamps
    detected_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'security_incidents'
```

### Security Response Flow

```python
def handle_security_violation(
    incident_type: str,
    request,
    user=None,
    details: dict = None
):
    """Handle security violation - never retry"""
    
    # 1. Create incident record
    incident = SecurityIncident.objects.create(
        incident_type=incident_type,
        severity=get_severity(incident_type),
        source_ip=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        user=user,
        description=details.get('description', ''),
        raw_request=sanitize_request_data(request),
    )
    
    # 2. Immediate protective action
    if incident_type == 'TOKEN_FORGED' and user:
        invalidate_all_user_sessions(user)
        incident.action_taken = "All user sessions invalidated"
    
    elif incident_type == 'WEBHOOK_SIGNATURE_INVALID':
        log_suspicious_ip(get_client_ip(request))
        incident.action_taken = "IP logged for monitoring"
    
    elif incident_type == 'RATE_LIMIT_ABUSE':
        temporary_ip_ban(get_client_ip(request), duration=timedelta(hours=1))
        incident.action_taken = "IP temporarily banned for 1 hour"
    
    incident.save()
    
    # 3. Alert security team
    send_security_alert(incident)
    
    return incident
```

---

## 6. Forensic Context

### What to Capture

Every DLQ entry should contain enough information to:
1. **Understand** what happened
2. **Reproduce** the issue if needed
3. **Recover** without accessing original records
4. **Audit** for compliance

### Forensic Context Structure

```python
metadata = {
    # Timing
    "request_timestamp": "2025-12-08T10:30:00.123Z",
    "response_timestamp": "2025-12-08T10:30:05.456Z",
    "latency_ms": 5333,
    
    # Retry History
    "retry_history": [
        {
            "attempt": 1,
            "error_code": "TIMEOUT",
            "error_message": "Connection timed out after 30s",
            "attempted_at": "2025-12-08T10:30:05Z",
            "backoff_seconds": 4
        },
        {
            "attempt": 2,
            "error_code": "TIMEOUT",
            "error_message": "Connection timed out after 30s",
            "attempted_at": "2025-12-08T10:30:10Z",
            "backoff_seconds": 16
        }
    ],
    
    # State Snapshots
    "state_before": {
        "order_status": "pending",
        "payment_status": "ready",
        "user_points": 5000,
        "product_stock": 10
    },
    "state_after": {
        "order_status": "pending",  # No change due to failure
        "payment_status": "failed",
        "user_points": 5000,
        "product_stock": 10
    },
    
    # Request Context
    "client_ip": "203.0.113.50",
    "user_agent": "Mozilla/5.0...",
    "session_id": "sess_abc123",
    
    # Task Context
    "task_name": "process_payment_confirmation",
    "task_id": "task-uuid-here",
    "queue_name": "payment_processing",
    "worker_id": "worker-01",
    
    # External System
    "external_request_id": "toss_req_12345",
    "external_response_code": 500,
    "external_response_body": "{\"error\": \"Internal server error\"}"
}
```

### Snapshot Data Structure

```python
snapshot_data = {
    # Core identifiers
    "order_id": 12345,
    "order_number": "ORD-2025-12345",
    "payment_id": 67890,
    "payment_key": "toss_pay_abc123",
    
    # Financial data
    "amount": 50000,
    "points_used": 1000,
    "final_amount": 49000,
    
    # User data
    "user_id": 100,
    "user_email": "user@example.com",
    
    # Product data (for inventory)
    "items": [
        {"product_id": 1, "quantity": 2, "price": 25000}
    ]
}
```

---

## 7. Observability & Metrics

### Key Metrics

| Metric | Description | Alert Threshold | Dashboard |
|--------|-------------|-----------------|-----------|
| `dlq.pending_count` | Total pending DLQ items | > 10 | Main |
| `dlq.pending_by_domain` | Pending items per domain | > 5 per domain | Main |
| `dlq.growth_rate` | New items per minute | > 5/min | Main |
| `retry.success_rate` | Successful auto-retries | < 70% | Retry |
| `retry.mean_attempts` | Average attempts before success | > 2.5 | Retry |
| `recovery.mean_time_seconds` | Time from failure to resolution | > 1800 (30min) | Recovery |
| `circuit_breaker.state` | Current CB state per service | open | Circuit |
| `circuit_breaker.open_duration_seconds` | How long CB has been open | > 300 (5min) | Circuit |
| `sla.breach_count` | Items exceeding review SLA | > 0 | SLA |
| `human_review.queue_time_seconds` | Time items wait for review | > 3600 (1hr) | Review |

### Prometheus Metrics Example

```python
from prometheus_client import Counter, Gauge, Histogram

# DLQ Metrics
dlq_items_total = Counter(
    'dlq_items_total',
    'Total DLQ items created',
    ['domain', 'failure_type']
)

dlq_pending_gauge = Gauge(
    'dlq_pending_count',
    'Current pending DLQ items',
    ['domain']
)

# Retry Metrics
retry_attempts = Histogram(
    'retry_attempts_total',
    'Number of retry attempts before resolution',
    ['domain'],
    buckets=[1, 2, 3, 4, 5]
)

retry_success_rate = Gauge(
    'retry_success_rate',
    'Percentage of successful retries',
    ['domain']
)

# Recovery Metrics
recovery_time_seconds = Histogram(
    'recovery_time_seconds',
    'Time from failure to resolution',
    ['domain', 'resolution_type'],
    buckets=[60, 300, 900, 1800, 3600, 7200]
)

# Circuit Breaker Metrics
circuit_breaker_state = Gauge(
    'circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open, 2=half-open)',
    ['service']
)
```

### Grafana Dashboard Panels

```yaml
# Dashboard Configuration
panels:
  - title: "DLQ Pending Items"
    type: gauge
    query: dlq_pending_count
    thresholds:
      - value: 0
        color: green
      - value: 5
        color: yellow
      - value: 10
        color: red

  - title: "Retry Success Rate"
    type: graph
    query: rate(retry_success_total[5m]) / rate(retry_attempts_total[5m])
    
  - title: "Recovery Time (P95)"
    type: stat
    query: histogram_quantile(0.95, recovery_time_seconds)
    
  - title: "Circuit Breaker Status"
    type: table
    query: circuit_breaker_state
```

---

## 8. Testing Strategy

### Test Categories

| Category | Focus | Tools |
|----------|-------|-------|
| **Unit Tests** | Policy logic, backoff calculation | pytest |
| **Integration Tests** | Full DLQ flow, replay | pytest + TestClient |
| **Contract Tests** | External API responses | pytest + responses |
| **Chaos Tests** | Fault injection | pytest + custom fixtures |

### Unit Test Examples

```python
# tests/test_self_healing_policy.py

class TestFailureClassification:
    """Test failure type → policy mapping"""
    
    def test_pg_timeout_is_retryable(self):
        policy = get_policy_for_failure("PG_TIMEOUT")
        assert policy.is_retryable is True
        assert policy.max_retries == 3
    
    def test_invalid_card_is_permanent(self):
        policy = get_policy_for_failure("INVALID_CARD")
        assert policy.is_retryable is False
        assert policy.action == "permanent_fail"
    
    def test_amount_mismatch_requires_approval(self):
        policy = get_policy_for_failure("AMOUNT_MISMATCH_PG_RESPONSE")
        assert policy.is_retryable is False
        assert policy.action == "human_approval"
        assert policy.severity == "critical"


class TestBackoffCalculation:
    """Test exponential backoff with jitter"""
    
    def test_backoff_sequence(self):
        delays = [calculate_backoff(attempt) for attempt in range(1, 5)]
        # Base 4: 4, 16, 64, 180 (capped)
        assert delays[0] >= 3 and delays[0] <= 5  # 4 ± 25%
        assert delays[1] >= 12 and delays[1] <= 20
        assert delays[2] >= 48 and delays[2] <= 80
        assert delays[3] == 180  # Max cap
    
    def test_jitter_distribution(self):
        """Jitter should spread retries"""
        delays = [calculate_backoff(1) for _ in range(100)]
        assert min(delays) >= 3
        assert max(delays) <= 5
        assert len(set(delays)) > 10  # Good distribution


class TestIdempotencyCheck:
    """Test idempotency key validation"""
    
    def test_duplicate_payment_detected(self):
        # First attempt
        result1 = process_payment(order_id=1, amount=1000)
        assert result1.was_duplicate is False
        
        # Retry with same key
        result2 = process_payment(order_id=1, amount=1000)
        assert result2.was_duplicate is True
        assert result2.payment_id == result1.payment_id
```

### Integration Test Examples

```python
# tests/test_dlq_integration.py

@pytest.mark.django_db(transaction=True)
class TestDLQFlow:
    """Test complete DLQ workflow"""
    
    def test_failure_creates_dlq_entry(self, order, mock_pg_timeout):
        """Failure after max retries creates DLQ entry"""
        # Act
        with pytest.raises(MaxRetriesExceeded):
            process_payment_with_retry(order.id)
        
        # Assert
        dlq_entry = FailedOperation.objects.get(order_id=order.id)
        assert dlq_entry.failure_type == "PG_TIMEOUT"
        assert dlq_entry.retry_count == 3
        assert dlq_entry.status == "pending"
    
    def test_dlq_replay_success(self, dlq_entry, mock_pg_success):
        """Successful replay resolves DLQ entry"""
        # Act
        result = replay_failed_operation(dlq_entry.id)
        
        # Assert
        dlq_entry.refresh_from_db()
        assert result["success"] is True
        assert dlq_entry.status == "resolved"
        assert dlq_entry.resolution_type == "auto_replay"
    
    def test_dlq_replay_max_attempts(self, dlq_entry_with_2_replays):
        """Third replay attempt is rejected"""
        # Act
        result = replay_failed_operation(dlq_entry_with_2_replays.id)
        
        # Assert
        assert result["success"] is False
        assert result["reason"] == "max_replays_exceeded"
        dlq_entry_with_2_replays.refresh_from_db()
        assert dlq_entry_with_2_replays.status == "rejected"


@pytest.mark.django_db
class TestCircuitBreaker:
    """Test circuit breaker behavior"""
    
    def test_manual_open_blocks_requests(self):
        # Open circuit
        CircuitBreaker.force_open("toss_payment", reason="Test")
        
        # Assert blocked
        assert CircuitBreaker.should_allow("toss_payment") is False
    
    def test_manual_close_allows_requests(self):
        CircuitBreaker.force_open("toss_payment", reason="Test")
        CircuitBreaker.force_close("toss_payment", reason="Test complete")
        
        assert CircuitBreaker.should_allow("toss_payment") is True
```

### Chaos Engineering Tests

```python
# tests/test_chaos.py

@pytest.mark.chaos
class TestFaultTolerance:
    """Chaos engineering tests for self-healing"""
    
    def test_random_pg_failures(self, order_factory):
        """System handles random PG failures gracefully"""
        orders = [order_factory() for _ in range(100)]
        
        with RandomFailureInjector(failure_rate=0.3):
            results = [
                process_payment_with_retry(order.id)
                for order in orders
            ]
        
        # At least 70% should eventually succeed
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 70
        
        # All failures should be in DLQ
        failed_order_ids = [
            o.id for o, r in zip(orders, results) if not r.success
        ]
        dlq_count = FailedOperation.objects.filter(
            order_id__in=failed_order_ids
        ).count()
        assert dlq_count == len(failed_order_ids)
    
    def test_network_latency_handling(self, order):
        """System handles high latency without timeout cascade"""
        with LatencyInjector(min_ms=100, max_ms=2000):
            start = time.time()
            result = process_payment_with_retry(order.id)
            elapsed = time.time() - start
        
        # Should complete within SLA even with retries
        assert elapsed < 300  # 5 minutes max
```

---

## 9. Operational Runbooks

### Runbook: High DLQ Pending Count

**Trigger**: `dlq.pending_count > 10`

**Steps**:
1. Check DLQ dashboard for failure type distribution
2. Identify if single failure type dominates
3. If PG-related:
   - Check PG status page
   - Consider opening Circuit Breaker
4. If internal error:
   - Check error logs for stack traces
   - Notify development team
5. For batch issues:
   - Use batch replay after root cause fixed

### Runbook: Circuit Breaker Opened

**Trigger**: Operator opened CB or auto-triggered

**Steps**:
1. Confirm external system is actually down
2. Communicate to stakeholders (Slack announcement)
3. Monitor DLQ growth rate
4. When external system recovers:
   - Close Circuit Breaker
   - Monitor first few requests
   - If stable, trigger conditional replay

### Runbook: SLA Breach

**Trigger**: Items pending > SLA threshold

**Steps**:
1. Check if reviewer is assigned
2. If unassigned:
   - Assign to available ops team member
   - Escalate if no one available
3. If assigned but stale:
   - Contact assigned reviewer
   - Reassign if unresponsive
4. Document resolution in `resolution_note`

### Runbook: Security Incident

**Trigger**: `SecurityIncident` created

**Steps**:
1. **Do not** attempt to replay or auto-recover
2. Review incident details in Security Admin
3. Check if pattern indicates attack:
   - Multiple incidents from same IP?
   - Same user involved?
4. Apply protective measures:
   - IP ban if needed
   - Account suspension if compromised
5. Document findings and actions taken
6. Update security rules if new pattern identified

---

## 10. Configuration Reference

### Full Configuration

```python
# settings/components/self_healing.py

SELF_HEALING = {
    # Retry Configuration
    "RETRY": {
        "MAX_ATTEMPTS": int(os.environ.get("SH_RETRY_MAX_ATTEMPTS", 3)),
        "BACKOFF_BASE": int(os.environ.get("SH_BACKOFF_BASE", 4)),
        "BACKOFF_MAX": int(os.environ.get("SH_BACKOFF_MAX", 180)),
        "JITTER_PERCENT": int(os.environ.get("SH_JITTER_PERCENT", 25)),
    },
    
    # Per-Domain Overrides
    "DOMAIN_CONFIG": {
        "payment": {
            "max_attempts": 3,
            "backoff_base": 4,
            "sla_seconds": 3600,
        },
        "webhook": {
            "max_attempts": 5,
            "backoff_base": 2,
            "sla_seconds": 28800,
        },
        "notification": {
            "max_attempts": 3,
            "backoff_base": 60,
            "sla_seconds": 86400,
        },
    },
    
    # DLQ Configuration
    "DLQ": {
        "ENABLED": os.environ.get("SH_DLQ_ENABLED", "true").lower() == "true",
        "RETENTION_DAYS": int(os.environ.get("SH_DLQ_RETENTION_DAYS", 30)),
        "MAX_REPLAY_ATTEMPTS": int(os.environ.get("SH_MAX_REPLAY", 2)),
    },
    
    # Circuit Breaker
    "CIRCUIT_BREAKER": {
        "ENABLED": os.environ.get("SH_CB_ENABLED", "false").lower() == "true",
        "FAILURE_THRESHOLD": int(os.environ.get("SH_CB_FAILURE_THRESHOLD", 5)),
        "RECOVERY_TIMEOUT": int(os.environ.get("SH_CB_RECOVERY_TIMEOUT", 60)),
        "SUCCESS_THRESHOLD": int(os.environ.get("SH_CB_SUCCESS_THRESHOLD", 2)),
    },
    
    # Notifications
    "NOTIFICATIONS": {
        "SLACK_WEBHOOK_URL": os.environ.get("SLACK_ALERTS_WEBHOOK"),
        "CRITICAL_CHANNEL": "#critical-alerts",
        "HIGH_CHANNEL": "#ops-alerts",
        "MEDIUM_CHANNEL": "#dev-alerts",
        "EMAIL_RECIPIENTS": {
            "critical": ["oncall@company.com", "cto@company.com"],
            "high": ["ops@company.com"],
        },
        "SMS_RECIPIENTS": {
            "critical": ["+821012345678"],
        },
    },
    
    # SLA Configuration
    "SLA": {
        "CHECK_INTERVAL_SECONDS": 300,  # Check every 5 minutes
        "ESCALATION_ENABLED": True,
    },
}
```

### Environment Variables

```bash
# .env.production

# Retry
SH_RETRY_MAX_ATTEMPTS=3
SH_BACKOFF_BASE=4
SH_BACKOFF_MAX=180
SH_JITTER_PERCENT=25

# DLQ
SH_DLQ_ENABLED=true
SH_DLQ_RETENTION_DAYS=30
SH_MAX_REPLAY=2

# Circuit Breaker
SH_CB_ENABLED=false
SH_CB_FAILURE_THRESHOLD=5
SH_CB_RECOVERY_TIMEOUT=60
SH_CB_SUCCESS_THRESHOLD=2

# Notifications
SLACK_ALERTS_WEBHOOK=https://hooks.slack.com/services/xxx
```

---

## 11. Implementation Checklist

### Phase 1: Core Infrastructure (Week 1-2)

- [ ] Create `FailedOperation` model and migration
- [ ] Create `SecurityIncident` model and migration
- [ ] Implement `FailureClassifier` service
- [ ] Implement `BackoffCalculator` utility
- [ ] Write unit tests for classification and backoff

### Phase 2: Retry Logic (Week 2-3)

- [ ] Implement retry decorator with backoff
- [ ] Add idempotency key checking
- [ ] Implement DLQ write on max retries
- [ ] Add forensic context capture
- [ ] Write integration tests

### Phase 3: Recovery Features (Week 3-4)

- [ ] Implement manual replay from Admin
- [ ] Implement batch replay task
- [ ] Add SLA checking scheduled task
- [ ] Implement notification channels
- [ ] Write chaos tests

### Phase 4: Circuit Breaker (Week 4-5)

- [ ] Create `CircuitBreakerState` model
- [ ] Implement toggle-based CB
- [ ] Add admin controls for force open/close
- [ ] Add conditional replay on CB close
- [ ] Write CB integration tests

### Phase 5: Observability (Week 5-6)

- [ ] Add Prometheus metrics
- [ ] Create Grafana dashboards
- [ ] Set up alerting rules
- [ ] Document runbooks
- [ ] Conduct chaos engineering session

### Phase 6: Security Hardening (Week 6)

- [ ] Implement security violation handling
- [ ] Add security incident notifications
- [ ] Review and harden all sensitive paths
- [ ] Conduct security review

---

## Related Documents

- [L3 Self-Healing Architecture](./L3_SELF_HEALING_ARCHITECTURE.md)
- [Celery Retry Guide](./CELERY_RETRY_GUIDE.md)
- [Production Checklist](./PRODUCTION_CHECKLIST.md)

---

*This document covers operations. For architecture, concepts, and decision tables, see the [Architecture Guide](./L3_SELF_HEALING_ARCHITECTURE.md).*
