# Operational & Governance Capabilities

This document details the operational control, governance, and human-in-the-loop capabilities.

---

## Capability 17: Control API Service

### 17.1 Purpose

Provides a unified, auditable, reversible, and governed control surface to manage reliability behaviors across testing, chaos experimentation, and production operations.

### 17.2 Actions Available

| Action | Effect | Maps To |
|--------|--------|---------|
| `allow` | Enable service operations | CB → CLOSED |
| `block` | Disable service operations | CB → OPEN |
| `override` | Temporarily bypass rules | CB → CLOSED with TTL |
| `reset` | Revert to default config | Clear manual controls |
| `inject_failure` | Simulate failures (non-prod only) | Test mode |
| `inject_success` | Record successes for CB recovery | Test mode |

### 17.3 Environments

```python
class ControlAPIEnvironments:
    TEST = "test"    # CI/CD validation
    CHAOS = "chaos"  # Resilience testing
    OPS = "ops"      # Production control
```

### 17.4 Risk Level Assessment

The system automatically assesses risk based on action + environment:

| Action | Test | Chaos | Ops |
|--------|------|-------|-----|
| `allow` | INFO | INFO | WARNING |
| `block` | INFO | WARNING | HIGH |
| `override` | WARNING | HIGH | CRITICAL |
| `reset` | INFO | WARNING | WARNING |
| `inject_failure` | INFO | HIGH | **FORBIDDEN** |
| `inject_success` | INFO | INFO | **FORBIDDEN** |

### 17.5 Control Request Structure

```python
@dataclass
class ControlRequest:
    service_name: str
    action: str            # allow, block, override, reset, etc.
    reason: str            # Human-provided reason
    environment: str       # test, chaos, ops
    ttl_minutes: int | None = None
    request_id: str        # Auto-generated UUID
    metadata: dict
    actor: str = "system"
    actor_role: str = "automation"
```

### 17.6 Control Response Structure

```python
@dataclass
class ControlResponse:
    status: str              # success, error
    action_applied: str
    system_state: str        # Current state after action
    effective_until: str     # TTL expiration time
    reason_classification: str
    evidence: dict           # Captured evidence
    correlation_id: str
    risk_level: str
    error_code: str = ""
    error_message: str = ""
```

### 17.7 Reason Classification

The system automatically classifies human-provided reasons:

```python
class ReasonClassification(str, Enum):
    EXTERNAL_DEPENDENCY_FAILURE = "external-dependency-failure"
    INTERNAL_SERVICE_ERROR = "internal-service-error"
    MAINTENANCE_WINDOW = "maintenance-window"
    SLA_BREACH_MITIGATION = "sla-breach-mitigation"
    CHAOS_EXPERIMENT = "chaos-experiment"
    MANUAL_INTERVENTION = "manual-intervention"
    RECOVERY_PROCEDURE = "recovery-procedure"
    SECURITY_INCIDENT = "security-incident"
    UNKNOWN = "unknown"
```

### 17.8 Execution Lifecycle

```
┌─────────────────────────────────────────┐
│ 1. Pre-execution Validation             │
│    - Validate request fields            │
│    - Check action allowed in env        │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 2. Risk Assessment                      │
│    - Assess risk level                  │
│    - Block FORBIDDEN actions            │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 3. Execute Action                       │
│    - Dispatch to appropriate handler    │
│    - Gather evidence                    │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 4. Add Metadata                         │
│    - Classify reason                    │
│    - Set correlation ID                 │
│    - Add risk level                     │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 5. Record Audit                         │
│    - Log for compliance                 │
│    - Emit audit trail to stdout         │
└─────────────────────────────────────────┘
```

> **Audit Trail Persistence Boundary:**
> Audit Trail is **not persisted within the application**. Audit records are emitted to stdout only. Durable storage and retrieval of audit data is the responsibility of the deployment infrastructure (e.g., centralized log aggregation systems). This is an intentional design decision, not a missing feature.

### 17.9 Failure Injection (Chaos Engineering Support)

```python
# Configuration mode - sets up injection config
service.execute(ControlRequest(
    service_name="payment",
    action="inject_failure",
    environment="chaos",
    metadata={"failure_rate": 0.5, "duration_minutes": 5}
))

# Trigger CB mode - records failures to trigger CB naturally
service.execute(ControlRequest(
    service_name="payment",
    action="inject_failure",
    environment="test",
    metadata={"trigger_cb_failures": 5}  # Record 5 failures
))
```

### 17.10 Code References

| Component | Location |
|-----------|----------|
| Service | `services/control_api_service.py` |
| Constants | `core/constants.py` |

---

## Capability 18: SLA Breach Detection

### 18.1 Purpose

Detects when DLQ entries have exceeded their Service Level Agreement thresholds, enabling escalation before customer impact becomes severe.

### 18.2 SLA Threshold Configuration

```python
@dataclass
class SLAConfig:
    # Default threshold for unregistered domains
    default_hours: int = 24

    # Domain-specific thresholds
    thresholds_by_domain: dict[str, int] = field(default_factory=dict)

    # Example configuration:
    # {"payment": 1, "order": 2, "notification": 24}

    def get_threshold(self, domain: str) -> timedelta:
        hours = self.thresholds_by_domain.get(domain.lower(), self.default_hours)
        return timedelta(hours=hours)
```

### 18.3 Breach Detection

```python
def get_sla_breached_entries() -> List[FailedOperationData]:
    """Get entries that have breached their SLA."""
    current_time = now()
    sla_config = get_config().sla

    return repository.find_sla_breached(
        current_time=current_time,
        sla_thresholds={
            "payment": sla_config.get_threshold("payment"),
            "point": sla_config.get_threshold("point"),
            ...
        }
    )
```

### 18.4 Code References

| Component | Location |
|-----------|----------|
| SLA Config | `core/config.py::SLAConfig` |
| DLQ Service | `services/dlq_service.py::get_sla_breached_entries` |

---

## Capability 19: Security Violation Handling

### 19.1 Purpose

Handles security violations that should **NEVER** self-heal. Security incidents are immediately blocked and routed to the security team for human investigation.

### 19.2 Security Violation Types

```python
class ViolationType(str, Enum):
    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
```

### 19.3 Severity Mapping

```python
SEVERITY_BY_VIOLATION_TYPE = {
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
    ViolationType.TOKEN_FORGED: Severity.CRITICAL,
    ViolationType.REPLAY_ATTACK: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.INJECTION_ATTEMPT: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ViolationType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
}
```

### 19.4 Handling Flow

```
┌─────────────────────────────────────────┐
│ Security Violation Detected             │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 1. Immediate Block                      │
│    - No retry allowed                   │
│    - No auto-recovery                   │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 2. Create SecurityIncident Record       │
│    - Full forensic context              │
│    - Source IP, User Agent              │
│    - Raw payload                        │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 3. Trigger Notifications                │
│    - Route by severity                  │
│    - Slack, Email, SMS, PagerDuty       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ 4. Apply Protective Actions             │
│    - Temporary IP ban                   │
│    - Account lockout                    │
│    - Rate limit enforcement             │
└─────────────────────────────────────────┘
```

### 19.5 IP Banning

```python
@dataclass
class SecurityConfig:
    temporary_ban_hours: int = 1
    permanent_ban_threshold: int = 5  # violations before permanent
    injection_ban_hours: int = 24
```

### 19.6 Code References

| Component | Location |
|-----------|----------|
| Service | `services/security_violation_service.py` |
| Configuration | `core/config.py::SecurityConfig` |

---

## Capability 20: Security Notifications

### 20.1 Purpose

Routes security notifications to appropriate channels based on incident severity.

### 20.2 Notification Routing

| Severity | Channels |
|----------|----------|
| CRITICAL | Slack + Email + SMS + PagerDuty |
| HIGH | Slack + Email |
| MEDIUM | Slack only |

### 20.3 Channel Configuration

```python
@dataclass
class NotificationConfig:
    # Slack
    slack_webhook_url: str = ""
    slack_critical_channel: str = "#critical-alerts"
    slack_high_channel: str = "#ops-alerts"
    slack_medium_channel: str = "#dev-alerts"

    # Email
    email_critical_recipients: list[str] = []
    email_high_recipients: list[str] = []

    # SMS
    sms_critical_recipients: list[str] = []

    # PagerDuty
    pagerduty_service_key: str = ""
    pagerduty_enabled: bool = False

    # General
    enabled: bool = True
    dry_run: bool = False  # Log only, don't send
```

### 20.4 Message Truncation Limits

```python
SLACK_BLOCK_TEXT_LIMIT = 3000
DESCRIPTION_MAX_LENGTH = 500
ACTION_TAKEN_MAX_LENGTH = 200
TITLE_MAX_LENGTH = 150
```

### 20.5 Code References

| Component | Location |
|-----------|----------|
| Service | `services/security_notification_service.py` |
| Configuration | `core/config.py::NotificationConfig` |

---

## Capability 21: Manual Override TTL Management

### 21.1 Purpose

Ensures manual circuit breaker overrides have a time limit to prevent "forgotten" blocks that could cause extended outages.

### 21.2 TTL Behavior

```
Manual Override Created
       │
       ▼
┌─────────────────────────────────────────┐
│ TTL: 90 minutes (default)               │
│ Extendable: Yes                         │
│ Max recommended: 180 minutes            │
└─────────────┬───────────────────────────┘
              │ TTL expires
              ▼
┌─────────────────────────────────────────┐
│ Automatic Transition                    │
│ OPEN → HALF_OPEN                        │
│ Reason updated: "original [EXPIRED]"    │
│ Gradual recovery testing begins         │
└─────────────────────────────────────────┘
```

### 21.3 TTL Extension

```python
result = service.extend_manual_override(
    service_name="payment",
    additional_minutes=90,
    controlled_by_id=operator_id,
    reason="Still investigating, need more time"
)
```

### 21.4 Periodic Expiration Check

```python
# Called periodically (e.g., via cron/scheduler)
expired_services = service.check_and_expire_manual_overrides()
for service_name in expired_services:
    logger.warning(f"Manual override expired for {service_name}")
```

### 21.5 Code References

| Component | Location |
|-----------|----------|
| TTL Management | `services/circuit_breaker/manual_control.py` |

---

## Capability 22: DLQ Entry Expiration

### 22.1 Purpose

Automatically archives DLQ entries that have exceeded their retention period, preventing indefinite storage growth.

### 22.2 Configuration

```python
@dataclass
class DLQConfig:
    retention_days: int = 30
    expiry_hours: int = 72  # After which status → EXPIRED
```

### 22.3 Expiration Flow

```python
def get_expired_entries() -> List[FailedOperationData]:
    """Get entries past their retention period."""
    return repository.find_expired(current_time=now())
```

### 22.4 Status Transitions for Expired Entries

```
PENDING ──────────────────► EXPIRED
        (after expiry_hours)     │
                                 │
                                 ▼
                            ARCHIVED
                     (after retention_days)
```

### 22.5 Code References

| Component | Location |
|-----------|----------|
| DLQ Service | `services/dlq_service.py` |
| Configuration | `core/config.py::DLQConfig` |

---

## Capability 23: Conditional Replay Trigger

### 23.1 Purpose

Automatically initiates replay of related DLQ entries when a circuit breaker closes (service recovers).

### 23.2 Trigger Mechanism

```python
def force_close(service_name, trigger_replay=True):
    # Close circuit breaker
    result = repository.atomic_force_close(service_name, ...)

    if result.success and trigger_replay:
        _trigger_conditional_replay(service_name)
```

### 23.3 Replay Flow

```python
def _trigger_conditional_replay(service_name):
    queue = ProviderRegistry.get_queue()
    task_id = queue.enqueue(
        "selfhealing.tasks.conditional_replay_on_circuit_close",
        kwargs={"service_name": service_name}
    )
```

### 23.4 Service-to-Failure-Type Mapping

The replay service uses a configurable mapping to determine which DLQ entries are related to a recovered service:

```python
service_failure_type_map = {
    "payment_api": ["PG_TIMEOUT", "PG_CONNECTION_ERROR"],
    "notification_service": ["NOTIFICATION_FAILED"],
    # ... custom mappings
}
```

### 23.5 Escalation on Replay Failure

```python
# When triggered by force_close with trigger_replay=True:
# Any replay failures are escalated to REQUIRES_REVIEW status

if not result.success and escalate_failures:
    repository.mark_as_requires_review(
        entry.id,
        note=f"Conditional replay failed: {result.error}"
    )
```

### 23.6 Code References

| Component | Location |
|-----------|----------|
| Trigger | `services/circuit_breaker/manual_control.py::_trigger_conditional_replay` |
| Replay | `services/replay_service.py::replay_on_circuit_close` |
