# Self-Healing Control API — Execution Specification

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active
> **Part of**: Self-Healing Control API Suite

---

## Table of Contents

1. [Overview](#1-overview)
2. [Execution Lifecycle](#2-execution-lifecycle)
3. [Action Execution Details](#3-action-execution-details)
4. [TTL Management](#4-ttl-management)
5. [State Transitions](#5-state-transitions)
6. [Conditional Replay](#6-conditional-replay)
7. [Validation & Enforcement](#7-validation--enforcement)
8. [Integration with Existing Services](#8-integration-with-existing-services)
9. [Error Handling](#9-error-handling)
10. [Metrics & Observability](#10-metrics--observability)
11. [Implementation Reference](#11-implementation-reference)
12. [Related Documents](#12-related-documents)

---

## 1. Overview

### Purpose

This document defines **HOW** the Self-Healing Control API actions execute, including:

- Execution lifecycle and state transitions
- TTL (Time-To-Live) management and expiration
- Validation and enforcement rules
- Integration with existing self-healing services
- Error handling and recovery patterns

### Position in Document Suite

| Document | Role |
|----------|------|
| [Control API Interface](./CONTROL_API_INTERFACE.md) | **What** = Contract |
| **This Document** | **How** = Execution |
| [Security Governance](./CONTROL_API_SECURITY_GOVERNANCE.md) | **Who/Why** = Authority |

---

## 2. Execution Lifecycle

### Request Processing Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Request Processing Flow                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ Receive  │───►│ Validate │───►│ Authorize│───►│ Execute  │          │
│  │ Request  │    │ Fields   │    │ Action   │    │ Action   │          │
│  └──────────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘          │
│                       │               │               │                 │
│                       ▼               ▼               ▼                 │
│                  ┌─────────┐    ┌─────────┐    ┌─────────┐             │
│                  │ Reject  │    │ Reject  │    │ Record  │             │
│                  │ Invalid │    │ Unauth  │    │ Audit   │             │
│                  └─────────┘    └─────────┘    └────┬────┘             │
│                                                     │                   │
│                                                     ▼                   │
│                                              ┌─────────────┐            │
│                                              │   Return    │            │
│                                              │  Response   │            │
│                                              └─────────────┘            │
└─────────────────────────────────────────────────────────────────────────┘
```

### Lifecycle Stages

| Stage | Description | Failure Behavior |
|-------|-------------|------------------|
| **Receive** | Accept HTTP request | 400 if malformed |
| **Validate** | Check required fields and constraints | 400 with validation errors |
| **Authorize** | Verify role and permissions | 401/403 with reason |
| **Execute** | Apply action to target service | 500 with rollback |
| **Audit** | Record action in audit log | Best-effort (never blocks) |
| **Respond** | Return result to caller | N/A |

### Idempotency

```yaml
Idempotency Guarantee:
  - Same request_id returns cached response
  - Duplicate requests within 5 minutes are idempotent
  - State changes are atomic (all-or-nothing)

Implementation:
  - request_id stored in cache with TTL
  - Database transaction for state changes
  - Rollback on partial failure
```

---

## 3. Action Execution Details

### `allow` Action

**Purpose**: Enable normal operations for a service

```python
def execute_allow(request: ControlRequest) -> ControlResponse:
    """
    Enable operations for the specified service.
    
    Equivalent to Circuit Breaker: force_close (CB → CLOSED state)
    
    Effects:
        - Service state → ALLOW
        - Pending operations → Resume processing
        - New requests → Pass through
        - Circuit Breaker → CLOSED
    """
    # 1. Update service state
    service_state = ServiceState.objects.get(service_name=request.service_name)
    service_state.state = "allow"
    service_state.controlled_by = request.actor
    service_state.control_reason = request.reason
    service_state.save()
    
    # 2. Update Circuit Breaker if applicable
    circuit_breaker_service.force_close(
        service_name=request.service_name,
        reason=request.reason,
        controlled_by=request.actor
    )
    
    # 3. Trigger conditional replay if configured
    if request.metadata.get("trigger_replay", False):
        trigger_conditional_replay(request.service_name)
    
    # 4. Record audit
    record_audit(request, "allow", success=True)
    
    return ControlResponse(
        status="success",
        action_applied="allow",
        system_state="allow"
    )
```

### `block` Action

**Purpose**: Block all operations for a service

```python
def execute_block(request: ControlRequest) -> ControlResponse:
    """
    Block operations for the specified service.
    
    Equivalent to Circuit Breaker: force_open (CB → OPEN state)
    
    Effects:
        - Service state → BLOCK
        - New requests → Fail-fast with error
        - Pending operations → Pause (not cancelled)
        - DLQ replay → Suspended
        - Auto-recovery → Disabled
    """
    # 1. Update service state
    service_state = ServiceState.objects.get(service_name=request.service_name)
    previous_state = service_state.state
    service_state.state = "block"
    service_state.controlled_by = request.actor
    service_state.control_reason = request.reason
    
    # 2. Set TTL if provided
    if request.ttl_minutes:
        service_state.expires_at = timezone.now() + timedelta(minutes=request.ttl_minutes)
    else:
        # Default TTL for block in ops: 90 minutes
        if request.environment == "ops":
            service_state.expires_at = timezone.now() + timedelta(minutes=90)
    
    service_state.save()
    
    # 3. Update Circuit Breaker
    circuit_breaker_service.force_open(
        service_name=request.service_name,
        reason=request.reason,
        controlled_by=request.actor,
        ttl_minutes=request.ttl_minutes or 90
    )
    
    # 4. Record audit with previous state
    record_audit(request, "block", success=True, previous_state=previous_state)
    
    return ControlResponse(
        status="success",
        action_applied="block",
        system_state="block",
        effective_until=service_state.expires_at.isoformat()
    )
```

### `override` Action

**Purpose**: Temporarily bypass normal rules under governance

```python
def execute_override(request: ControlRequest) -> ControlResponse:
    """
    Temporarily override normal policies.
    
    Effects:
        - Specific policies → Suspended for TTL duration
        - Normal validation → Bypassed (with governance)
        - Audit → Enhanced logging
    
    Requirements:
        - TTL required in ops environment
        - Reason required
        - Evidence recommended
    """
    # 1. Validate TTL for ops
    if request.environment == "ops" and not request.ttl_minutes:
        raise ValidationError("TTL required for override in ops environment")
    
    if request.environment == "ops" and request.ttl_minutes > 60:
        raise ValidationError("TTL must not exceed 60 minutes in ops")
    
    # 2. Check if CRITICAL risk requires approval
    risk_level = assess_risk(request)
    if risk_level == "CRITICAL":
        approval = check_approval(request)
        if not approval.approved:
            return ControlResponse(
                status="pending_approval",
                request_id=request.request_id,
                approvers=approval.required_approvers
            )
    
    # 3. Apply override
    override_policy = OverridePolicy.objects.create(
        service_name=request.service_name,
        override_type=request.metadata.get("override_type", "general"),
        reason=request.reason,
        controlled_by=request.actor,
        expires_at=timezone.now() + timedelta(minutes=request.ttl_minutes),
        environment=request.environment
    )
    
    # 4. Collect evidence
    evidence = collect_evidence(request.service_name)
    
    # 5. Classify reason (AI-assisted)
    reason_classification = classify_reason(request.reason)
    
    # 6. Record enhanced audit
    record_audit(
        request, 
        "override", 
        success=True,
        evidence=evidence,
        reason_classification=reason_classification
    )
    
    return ControlResponse(
        status="success",
        action_applied="override",
        system_state="allow",  # Override allows operations
        effective_until=override_policy.expires_at.isoformat(),
        reason_classification=reason_classification,
        evidence=evidence
    )
```

### `reset` Action

**Purpose**: Revert service to default configuration

```python
def execute_reset(request: ControlRequest) -> ControlResponse:
    """
    Reset service to default state.
    
    Effects:
        - All manual overrides → Cleared
        - Service state → Default policy
        - Circuit Breaker → Auto mode (if enabled)
        - Active TTLs → Cancelled
    """
    # 1. Clear overrides
    OverridePolicy.objects.filter(
        service_name=request.service_name,
        expires_at__gt=timezone.now()
    ).update(
        expires_at=timezone.now(),
        cancelled_by=request.actor,
        cancelled_reason="Manual reset"
    )
    
    # 2. Reset service state
    service_state = ServiceState.objects.get(service_name=request.service_name)
    previous_state = service_state.state
    service_state.state = "default"
    service_state.controlled_by = None
    service_state.control_reason = None
    service_state.expires_at = None
    service_state.save()
    
    # 3. Reset Circuit Breaker
    circuit_breaker_service.reset(
        service_name=request.service_name,
        reason=request.reason
    )
    
    # 4. Record audit
    record_audit(request, "reset", success=True, previous_state=previous_state)
    
    return ControlResponse(
        status="success",
        action_applied="reset",
        system_state="default"
    )
```

### `inject_failure` Action

**Purpose**: Simulate failures for testing resilience

```python
def execute_inject_failure(request: ControlRequest) -> ControlResponse:
    """
    Inject controlled failures for chaos testing.
    
    Effects:
        - Specified operations → Fail at configured rate
        - Failure patterns → Applied (timeout, error, latency)
        - Metrics → Tagged as chaos
    
    Restrictions:
        - FORBIDDEN in ops environment
        - TTL recommended
    """
    # 1. Block in ops environment
    if request.environment == "ops":
        raise ForbiddenError("inject_failure is forbidden in ops environment")
    
    # 2. Parse failure configuration
    failure_config = FailureConfig(
        failure_rate=request.metadata.get("failure_rate", 0.3),
        failure_type=request.metadata.get("failure_type", "error"),
        affected_operations=request.metadata.get("affected_operations", []),
        latency_ms=request.metadata.get("latency_ms", 0)
    )
    
    # 3. Register failure injection
    injection = FailureInjection.objects.create(
        service_name=request.service_name,
        config=failure_config.to_dict(),
        controlled_by=request.actor,
        reason=request.reason,
        expires_at=timezone.now() + timedelta(minutes=request.ttl_minutes or 10),
        environment=request.environment
    )
    
    # 4. Activate injection
    chaos_service.activate_injection(injection)
    
    # 5. Record audit
    record_audit(request, "inject_failure", success=True)
    
    return ControlResponse(
        status="success",
        action_applied="inject_failure",
        system_state="chaos_active",
        effective_until=injection.expires_at.isoformat()
    )
```

---

## 4. TTL Management

### TTL Configuration

| Environment | Action | Default TTL | Max TTL |
|-------------|--------|-------------|---------|
| `test` | Any | No expiry | Unlimited |
| `chaos` | `inject_failure` | 10 min | 60 min |
| `chaos` | `override` | 30 min | 120 min |
| `ops` | `block` | 90 min | 180 min |
| `ops` | `override` | **Required** | 60 min |

### TTL Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           TTL Lifecycle                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Action Created                                                         │
│        │                                                                 │
│        ▼                                                                 │
│   ┌─────────────┐                                                       │
│   │ TTL Active  │◄─────────────────────────────┐                        │
│   │   (N min)   │                              │                        │
│   └──────┬──────┘                              │                        │
│          │                                     │                        │
│   ┌──────┴──────┬──────────────┐              │                        │
│   │             │              │              │                        │
│   ▼             ▼              ▼              │                        │
│ Manual       TTL           Extension       (if approved)               │
│ Cancel     Expires         Requested                                   │
│   │             │              │                                        │
│   ▼             ▼              ▼                                        │
│ ┌─────────┐ ┌─────────┐  ┌──────────┐                                  │
│ │ Cleanup │ │Auto-Revert│ │ Approval │──────────────────────┘          │
│ │ + Audit │ │ + Alert  │ │ Required │                                  │
│ └─────────┘ └─────────┘  └──────────┘                                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### TTL Expiration Task

```python
@shared_task(name="expire_control_api_overrides")
def expire_control_api_overrides():
    """
    Periodic task to expire TTL-based overrides.
    
    Runs every 5 minutes.
    
    Actions:
        1. Find expired overrides
        2. Revert to previous/default state
        3. Send notification
        4. Record audit
    """
    now = timezone.now()
    
    # Find expired service states
    expired_states = ServiceState.objects.filter(
        expires_at__lte=now,
        state__in=["block", "override"]
    )
    
    results = {"expired_count": 0, "services": []}
    
    for state in expired_states:
        # Record previous state
        previous_state = state.state
        
        # Revert to default
        state.state = "default"
        state.expires_at = None
        state.control_reason = f"Auto-expired from {previous_state}"
        state.save()
        
        # Notify operators
        send_notification(
            channel="ops",
            message=f"Control override expired for {state.service_name}. "
                    f"State reverted from {previous_state} to default."
        )
        
        # Record audit
        record_audit_expiration(state, previous_state)
        
        results["expired_count"] += 1
        results["services"].append(state.service_name)
    
    # Also expire failure injections
    expired_injections = FailureInjection.objects.filter(
        expires_at__lte=now,
        is_active=True
    )
    
    for injection in expired_injections:
        chaos_service.deactivate_injection(injection)
        injection.is_active = False
        injection.save()
    
    return results
```

### TTL Extension

```python
def extend_ttl(request_id: str, additional_minutes: int, approver: User) -> ControlResponse:
    """
    Extend TTL for an active override.
    
    Requirements:
        - Original actor or higher authority
        - Additional minutes within max limits
        - Reason for extension
        - Approval for CRITICAL extensions
    """
    # 1. Find active override
    override = ServiceState.objects.get(
        request_id=request_id,
        expires_at__gt=timezone.now()
    )
    
    # 2. Check extension limits
    new_expiry = override.expires_at + timedelta(minutes=additional_minutes)
    total_duration = (new_expiry - override.created_at).total_seconds() / 60
    
    if override.environment == "ops" and total_duration > 120:
        raise ValidationError("Total override duration cannot exceed 120 minutes in ops")
    
    # 3. Require approval for repeated extensions
    extension_count = override.extension_count or 0
    if extension_count >= 1:
        approval = require_approval(override, approver)
        if not approval.approved:
            raise ApprovalRequired("Repeated extension requires approval")
    
    # 4. Apply extension
    override.expires_at = new_expiry
    override.extension_count = extension_count + 1
    override.save()
    
    # 5. Record audit
    record_audit_extension(override, additional_minutes, approver)
    
    return ControlResponse(
        status="success",
        action_applied="extend_ttl",
        effective_until=new_expiry.isoformat()
    )
```

---

## 5. State Transitions

### Service State Machine

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Service State Machine                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                          ┌─────────────┐                                │
│                          │   DEFAULT   │                                │
│                          │  (Normal)   │                                │
│                          └──────┬──────┘                                │
│                                 │                                        │
│           ┌─────────────────────┼─────────────────────┐                 │
│           │                     │                     │                 │
│           ▼                     ▼                     ▼                 │
│    ┌─────────────┐       ┌─────────────┐       ┌─────────────┐         │
│    │    ALLOW    │       │    BLOCK    │       │  OVERRIDE   │         │
│    │ (Ops permit)│       │ (Ops block) │       │ (Temp bypass)│        │
│    └──────┬──────┘       └──────┬──────┘       └──────┬──────┘         │
│           │                     │                     │                 │
│           └─────────┬───────────┴──────────┬──────────┘                 │
│                     │                      │                            │
│                     ▼                      ▼                            │
│              ┌─────────────┐        ┌─────────────┐                    │
│              │    RESET    │        │ CHAOS_ACTIVE│                    │
│              │ (→ Default) │        │(Inject mode)│                    │
│              └─────────────┘        └─────────────┘                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### State Definitions

| State | Description | Operations | Transitions To |
|-------|-------------|------------|----------------|
| `default` | Normal policy-driven mode | Normal flow | `allow`, `block`, `override`, `chaos_active` |
| `allow` | Explicitly enabled by operator | All allowed | `block`, `default`, `override` |
| `block` | Explicitly blocked by operator | All fail-fast | `allow`, `default` |
| `override` | Temporary policy bypass | Bypass active | `default` (on expiry), `reset` |
| `chaos_active` | Failure injection active | Controlled failures | `default` (on expiry) |

### Mapping to Circuit Breaker

| Control API State | Circuit Breaker State | Effect |
|-------------------|-----------------------|--------|
| `default` | AUTO | Policy-driven open/close |
| `allow` | CLOSED (forced) | All requests pass |
| `block` | OPEN (forced) | All requests fail |
| `override` | CLOSED with bypass | Requests pass, policies bypassed |
| `chaos_active` | N/A | Failure injection active |

---

## 6. Conditional Replay

### Trigger Conditions

Conditional replay is triggered when:

1. Service transitions from `block` to `allow`
2. Override expires and state returns to `default`
3. Chaos experiment ends successfully

### Conditional Replay on State Change

```python
def trigger_conditional_replay(
    service_name: str,
    escalate_failures: bool = True
) -> dict:
    """
    Replay backlogged DLQ entries when service becomes available.
    
    Args:
        service_name: Target service
        escalate_failures: If True, escalate replay failures to REQUIRES_REVIEW
    
    Returns:
        dict with replay statistics
    """
    # 1. Find pending DLQ entries for this service
    pending_entries = FailedOperation.objects.filter(
        metadata__service=service_name,
        status=FailedOperation.Status.PENDING,
        retry_count__lt=2  # Respect max replay attempts
    )
    
    results = {
        "total": pending_entries.count(),
        "success": 0,
        "failed": 0,
        "escalated": 0
    }
    
    # 2. Process each entry
    for entry in pending_entries:
        try:
            replay_result = replay_single(entry.id)
            
            if replay_result["success"]:
                entry.status = FailedOperation.Status.RESOLVED
                entry.resolution_type = "conditional_replay"
                results["success"] += 1
            else:
                if escalate_failures:
                    # Failure after state change = needs investigation
                    entry.status = FailedOperation.Status.REQUIRES_REVIEW
                    entry.resolution_note = (
                        f"Conditional replay failed after state change: "
                        f"{replay_result['error']}"
                    )
                    results["escalated"] += 1
                else:
                    entry.retry_count += 1
                    results["failed"] += 1
            
            entry.save()
            
        except Exception as e:
            # Handler crash = immediate escalation
            entry.status = FailedOperation.Status.REQUIRES_REVIEW
            entry.resolution_note = f"Replay handler crashed: {type(e).__name__}: {str(e)}"
            entry.save()
            results["escalated"] += 1
    
    # 3. Record metrics
    record_replay_metrics(service_name, results)
    
    return results
```

### Critical Behavior Guarantees

| Scenario | Guaranteed Behavior | Rationale |
|----------|---------------------|-----------|
| Replay failure after `allow` | State stays `allow` | Don't auto-revert on DLQ issues |
| Replay failure with escalation | Entry → `REQUIRES_REVIEW` | Failed after intervention = needs review |
| Security violation replay | **Blocked**, never auto-retry | Security never self-heals |
| Successful replay | Entry → `RESOLVED` | Normal success flow |

---

## 7. Validation & Enforcement

### Request Validation Pipeline

```python
def validate_control_request(request: ControlRequest) -> ValidationResult:
    """
    Complete validation pipeline for control requests.
    """
    errors = []
    
    # 1. Required fields
    if not request.service_name:
        errors.append(ValidationError("service_name", "required"))
    if not request.action:
        errors.append(ValidationError("action", "required"))
    if not request.reason:
        errors.append(ValidationError("reason", "required"))
    if not request.environment:
        errors.append(ValidationError("environment", "required"))
    
    # 2. Enum validation
    if request.action not in VALID_ACTIONS:
        errors.append(ValidationError("action", f"must be one of {VALID_ACTIONS}"))
    if request.environment not in VALID_ENVIRONMENTS:
        errors.append(ValidationError("environment", f"must be one of {VALID_ENVIRONMENTS}"))
    
    # 3. Environment-specific rules
    if request.environment == "ops":
        # inject_failure forbidden
        if request.action == "inject_failure":
            errors.append(ValidationError(
                "action", 
                "inject_failure is forbidden in ops environment"
            ))
        
        # TTL required for override
        if request.action == "override" and not request.ttl_minutes:
            errors.append(ValidationError(
                "ttl_minutes",
                "required for override in ops environment"
            ))
        
        # TTL max limit
        if request.ttl_minutes and request.ttl_minutes > 60:
            errors.append(ValidationError(
                "ttl_minutes",
                "must not exceed 60 minutes in ops environment"
            ))
    
    # 4. Metadata validation
    if request.metadata:
        if has_nested_objects(request.metadata):
            errors.append(ValidationError(
                "metadata",
                "nested objects are not allowed"
            ))
        if has_binary_data(request.metadata):
            errors.append(ValidationError(
                "metadata",
                "binary data is not allowed"
            ))
        if len(json.dumps(request.metadata)) > 10240:
            errors.append(ValidationError(
                "metadata",
                "size must not exceed 10KB"
            ))
    
    # 5. Service existence
    if not service_exists(request.service_name):
        errors.append(ValidationError(
            "service_name",
            f"service '{request.service_name}' not found"
        ))
    
    if errors:
        return ValidationResult(valid=False, errors=errors)
    
    return ValidationResult(valid=True)
```

### Enforcement Rules

| Rule | Enforcement Point | Violation Response |
|------|-------------------|-------------------|
| Reason required | Validation | 400 + MISSING_REASON |
| TTL for ops override | Validation | 400 + TTL_REQUIRED |
| TTL max limit | Validation | 400 + TTL_EXCEEDS_LIMIT |
| inject forbidden in ops | Validation | 403 + ACTION_FORBIDDEN |
| Role authorization | Authorization | 403 + FORBIDDEN |
| CRITICAL approval | Authorization | 403 + APPROVAL_REQUIRED |

---

## 8. Integration with Existing Services

### Service Integration Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Control API Service Integration                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐                                                    │
│  │  Control API    │                                                    │
│  │    Handler      │                                                    │
│  └────────┬────────┘                                                    │
│           │                                                              │
│   ┌───────┴───────┬──────────────┬──────────────┬──────────────┐       │
│   │               │              │              │              │       │
│   ▼               ▼              ▼              ▼              ▼       │
│ ┌─────────┐ ┌─────────┐  ┌─────────────┐ ┌──────────┐ ┌─────────┐     │
│ │Circuit  │ │  DLQ    │  │   Replay    │ │  Chaos   │ │ Metrics │     │
│ │Breaker  │ │ Service │  │   Service   │ │ Service  │ │ Service │     │
│ │Service  │ │         │  │             │ │          │ │         │     │
│ └─────────┘ └─────────┘  └─────────────┘ └──────────┘ └─────────┘     │
│                                                                          │
│  Existing Self-Healing Services                                         │
│  (shopping/services/self_healing/)                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### CircuitBreakerService Integration

```python
# Control API uses existing CircuitBreakerService
from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService

class ControlAPIHandler:
    def __init__(self):
        self.circuit_breaker = CircuitBreakerService()
    
    def handle_allow(self, request: ControlRequest) -> ControlResponse:
        # Map to existing force_close
        result = self.circuit_breaker.force_close(
            service_name=request.service_name,
            reason=request.reason,
            controlled_by=request.actor
        )
        return ControlResponse(status="success", ...)
    
    def handle_block(self, request: ControlRequest) -> ControlResponse:
        # Map to existing force_open
        result = self.circuit_breaker.force_open(
            service_name=request.service_name,
            reason=request.reason,
            controlled_by=request.actor,
            ttl_minutes=request.ttl_minutes
        )
        return ControlResponse(status="success", ...)
```

### DLQService Integration

```python
from shopping.services.self_healing.dlq_service import DLQService
from shopping.services.self_healing.replay_service import ReplayService

class ControlAPIHandler:
    def handle_conditional_replay(self, service_name: str):
        # Get pending entries
        pending = DLQService.get_pending_entries(
            domain=None,  # All domains
            service=service_name
        )
        
        # Trigger replay
        for entry in pending:
            ReplayService.replay_single(entry.id)
```

### Terminology Mapping

| Control API | CircuitBreakerService | DLQService |
|-------------|----------------------|------------|
| `allow` | `force_close()` | N/A |
| `block` | `force_open()` | N/A |
| `reset` | `reset()` | N/A |
| Conditional Replay | N/A | `replay_single()` |
| `inject_failure` | N/A | N/A (Chaos Service) |

---

## 9. Error Handling

### Error Categories

| Category | HTTP Status | Recovery |
|----------|-------------|----------|
| Validation | 400 | Fix request |
| Authentication | 401 | Re-authenticate |
| Authorization | 403 | Request permission |
| Not Found | 404 | Check service name |
| Conflict | 409 | Wait and retry |
| Internal | 500 | Contact ops |

### Error Response Format

```json
{
  "status": "error",
  "error_code": "TTL_EXCEEDS_POLICY_LIMIT",
  "error_message": "TTL must not exceed 60 minutes in ops environment",
  "error_details": {
    "field": "ttl_minutes",
    "value": 120,
    "constraint": "max: 60",
    "environment": "ops"
  },
  "retry_available": true,
  "next_action_suggestion": "Reduce TTL to 60 minutes or less"
}
```

### Transaction Rollback

```python
@transaction.atomic
def execute_action(request: ControlRequest) -> ControlResponse:
    """
    Execute action with transaction safety.
    
    All-or-nothing: If any step fails, all changes are rolled back.
    """
    try:
        # Create savepoint
        sid = transaction.savepoint()
        
        # Execute action
        result = _execute_action_impl(request)
        
        # Commit on success
        transaction.savepoint_commit(sid)
        
        return result
        
    except Exception as e:
        # Rollback on failure
        transaction.savepoint_rollback(sid)
        
        # Record failed attempt in audit (separate transaction)
        record_audit_failure(request, str(e))
        
        raise
```

---

## 10. Metrics & Observability

### Control API Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# Request metrics
control_api_requests_total = Counter(
    'control_api_requests_total',
    'Total Control API requests',
    ['action', 'environment', 'status']
)

control_api_request_duration = Histogram(
    'control_api_request_duration_seconds',
    'Control API request duration',
    ['action', 'environment'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0]
)

# State metrics
control_api_active_overrides = Gauge(
    'control_api_active_overrides',
    'Number of active overrides',
    ['service', 'environment']
)

control_api_active_blocks = Gauge(
    'control_api_active_blocks',
    'Number of active blocks',
    ['service']
)

# TTL metrics
control_api_ttl_expirations_total = Counter(
    'control_api_ttl_expirations_total',
    'Total TTL expirations',
    ['service', 'action']
)

# Replay metrics
control_api_conditional_replays_total = Counter(
    'control_api_conditional_replays_total',
    'Total conditional replays triggered',
    ['service', 'result']
)
```

### Alerting Rules

```yaml
groups:
  - name: control_api_alerts
    rules:
      - alert: ControlAPIHighBlockCount
        expr: control_api_active_blocks > 3
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High number of blocked services"
          
      - alert: ControlAPIOverrideLongDuration
        expr: control_api_override_duration_seconds > 3600
        for: 1m
        labels:
          severity: high
        annotations:
          summary: "Override active for more than 1 hour"
          
      - alert: ControlAPIReplayFailureHigh
        expr: |
          rate(control_api_conditional_replays_total{result="failed"}[5m])
          / rate(control_api_conditional_replays_total[5m]) > 0.5
        for: 5m
        labels:
          severity: high
        annotations:
          summary: "High conditional replay failure rate"
```

---

## 11. Implementation Reference

### Directory Structure

```
shopping/services/control_api/
├── __init__.py
├── handler.py              # Main request handler
├── validators.py           # Request validation
├── executors/
│   ├── __init__.py
│   ├── allow_executor.py
│   ├── block_executor.py
│   ├── override_executor.py
│   ├── reset_executor.py
│   └── inject_executor.py
├── models.py               # ServiceState, OverridePolicy, FailureInjection
├── tasks.py                # Celery tasks (TTL expiration, etc.)
├── metrics.py              # Prometheus metrics
└── tests/
    ├── __init__.py
    ├── test_handler.py
    ├── test_validators.py
    └── test_executors.py
```

### API Endpoints

```python
# shopping/api/control_api.py

from rest_framework.views import APIView
from rest_framework.response import Response

class ControlAPIView(APIView):
    """
    Self-Healing Control API endpoint.
    
    POST /api/v1/control/
    """
    
    def post(self, request):
        # Parse request
        control_request = ControlRequest.from_dict(request.data)
        
        # Validate
        validation_result = validate_control_request(control_request)
        if not validation_result.valid:
            return Response(
                {"status": "rejected", "errors": validation_result.errors},
                status=400
            )
        
        # Authorize
        auth_result = authorize_request(control_request, request.user)
        if not auth_result.authorized:
            return Response(
                {"status": "rejected", "error": auth_result.reason},
                status=403
            )
        
        # Execute
        handler = ControlAPIHandler()
        result = handler.handle(control_request)
        
        return Response(result.to_dict())
```

### Configuration

```python
# settings/components/control_api.py

CONTROL_API = {
    # TTL Limits
    "TTL": {
        "OPS_OVERRIDE_MAX_MINUTES": 60,
        "OPS_BLOCK_DEFAULT_MINUTES": 90,
        "CHAOS_INJECT_DEFAULT_MINUTES": 10,
        "CHAOS_OVERRIDE_MAX_MINUTES": 120,
    },
    
    # Expiration Task
    "EXPIRATION": {
        "CHECK_INTERVAL_SECONDS": 300,  # 5 minutes
    },
    
    # Conditional Replay
    "REPLAY": {
        "ESCALATE_FAILURES": True,
        "MAX_BATCH_SIZE": 100,
    },
    
    # Validation
    "VALIDATION": {
        "METADATA_MAX_SIZE_KB": 10,
        "ALLOW_NESTED_METADATA": False,
    },
}
```

---

## 12. Related Documents

| Document | Role | Location |
|----------|------|----------|
| **Control API Interface** | What = Contract | [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) |
| **Security Governance** | Who/Why = Authority | [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) |
| **Architecture** | System overview | [../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md](../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md) |
| **Operations** | DLQ, Replay, Metrics | [../0_OVERVIEW/SELF_HEALING_OPERATIONS.md](../0_OVERVIEW/SELF_HEALING_OPERATIONS.md) |

---

## Summary

This document defines **HOW** the Control API executes:

| Aspect | Implementation |
|--------|----------------|
| **Execution Flow** | Receive → Validate → Authorize → Execute → Audit |
| **State Management** | Service state machine with transitions |
| **TTL Lifecycle** | Creation → Active → Expiration → Cleanup |
| **Integration** | Leverages existing self-healing services |
| **Error Handling** | Transaction-safe with rollback |
| **Observability** | Prometheus metrics + alerting rules |

---

*This document defines execution behavior. For API interface, see [Interface](./CONTROL_API_INTERFACE.md). For security and authorization, see [Governance](./CONTROL_API_SECURITY_GOVERNANCE.md).*
