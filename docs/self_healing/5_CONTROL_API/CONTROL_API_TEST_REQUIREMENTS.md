# Self-Healing Control API — Test Requirements Specification

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active
> **Part of**: Self-Healing Control API Suite

---

## Table of Contents

1. [Overview](#1-overview)
2. [Action Execution Tests](#2-action-execution-tests)
3. [Environment Constraint Tests](#3-environment-constraint-tests)
4. [TTL Management Tests](#4-ttl-management-tests)
5. [Authorization Tests](#5-authorization-tests)
6. [Risk Classification Tests](#6-risk-classification-tests)
7. [Validation Tests](#7-validation-tests)
8. [Audit Trail Tests](#8-audit-trail-tests)
9. [Integration Tests](#9-integration-tests)
10. [Conditional Replay Tests](#10-conditional-replay-tests)

---

## 1. Overview

### Purpose

This document specifies test requirements for the Self-Healing Control API, ensuring all governance, security, and execution behaviors are validated.

### Test Categories

| Category | Purpose | Priority |
|----------|---------|----------|
| Action Execution | Validate each action type | 🔴 HIGH |
| Environment Constraints | Validate env-specific rules | 🔴 HIGH |
| TTL Management | Validate expiration behavior | 🔴 HIGH |
| Authorization | Validate role-based access | 🔴 HIGH |
| Risk Classification | Validate risk assessment | 🟡 MEDIUM |
| Validation | Validate input validation | 🟡 MEDIUM |
| Audit Trail | Validate audit logging | 🟡 MEDIUM |
| Integration | Validate service integration | 🔴 HIGH |
| Conditional Replay | Validate replay on state change | 🔴 HIGH |

---

## 2. Action Execution Tests

### Purpose

Validate that each action type executes correctly and produces expected state changes.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| ACT-001 | `test_allow_action_enables_operations` | Execute `allow` action | Service state → `allow`, operations proceed |
| ACT-002 | `test_block_action_disables_operations` | Execute `block` action | Service state → `block`, operations fail-fast |
| ACT-003 | `test_override_action_bypasses_rules` | Execute `override` action | Policies bypassed for TTL duration |
| ACT-004 | `test_reset_action_reverts_to_default` | Execute `reset` action | All overrides cleared, default state |
| ACT-005 | `test_inject_failure_simulates_failures` | Execute `inject_failure` in chaos | Controlled failures at specified rate |
| ACT-006 | `test_allow_maps_to_circuit_breaker_close` | Execute `allow` | CircuitBreaker → CLOSED |
| ACT-007 | `test_block_maps_to_circuit_breaker_open` | Execute `block` | CircuitBreaker → OPEN |

### Required Test Implementation

```python
# tests/integration/control_api/test_action_execution.py

class TestActionExecution:
    """Tests for Control API action execution."""

    def test_allow_action_enables_operations(self):
        """
        Purpose:
            Verify that allow action enables service operations.

        Scenario:
            1. Service is in blocked state
            2. Execute allow action
            3. Verify operations can proceed

        Expected:
            - Service state changes to allow
            - CircuitBreaker state is CLOSED
            - Pending operations can execute
        """
        pass

    def test_block_action_disables_operations(self):
        """
        Purpose:
            Verify that block action disables service operations.

        Scenario:
            1. Service is in normal state
            2. Execute block action
            3. Attempt operation

        Expected:
            - Service state changes to block
            - CircuitBreaker state is OPEN
            - Operations fail immediately
        """
        pass

    def test_override_action_bypasses_rules(self):
        """
        Purpose:
            Verify that override temporarily bypasses policies.

        Scenario:
            1. Policy normally blocks operation
            2. Execute override with TTL
            3. Attempt blocked operation

        Expected:
            - Operation succeeds during TTL
            - Audit records override usage
        """
        pass
```

---

## 3. Environment Constraint Tests

### Purpose

Validate that environment-specific rules are enforced correctly.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| ENV-001 | `test_inject_failure_forbidden_in_ops` | `inject_failure` in ops | Rejection with error |
| ENV-002 | `test_inject_failure_allowed_in_chaos` | `inject_failure` in chaos | Success |
| ENV-003 | `test_inject_failure_allowed_in_test` | `inject_failure` in test | Success |
| ENV-004 | `test_override_requires_ttl_in_ops` | `override` without TTL in ops | Rejection |
| ENV-005 | `test_override_ttl_optional_in_test` | `override` without TTL in test | Success |
| ENV-006 | `test_ttl_max_limit_in_ops` | TTL > 60 min in ops | Rejection |
| ENV-007 | `test_all_actions_allowed_in_test` | Any action in test | Success |

### Required Test Implementation

```python
# tests/integration/control_api/test_environment_constraints.py

class TestEnvironmentConstraints:
    """Tests for environment-specific restrictions."""

    def test_inject_failure_forbidden_in_ops(self):
        """
        Purpose:
            Verify inject_failure is forbidden in ops environment.

        Scenario:
            1. Attempt inject_failure with environment=ops
            2. Verify rejection

        Expected:
            - Status: rejected
            - Error code: ACTION_FORBIDDEN_IN_ENVIRONMENT
            - No state change
        """
        request = ControlRequest(
            service_name="payment",
            action="inject_failure",
            environment="ops",
            reason="test",
            ttl_minutes=5
        )

        response = control_api_handler.handle(request)

        assert response.status == "rejected"
        assert response.error_code == "ACTION_FORBIDDEN_IN_ENVIRONMENT"

    def test_override_requires_ttl_in_ops(self):
        """
        Purpose:
            Verify override requires TTL in ops environment.

        Scenario:
            1. Attempt override without TTL in ops
            2. Verify rejection

        Expected:
            - Status: rejected
            - Error code: TTL_REQUIRED_FOR_OPS_OVERRIDE
        """
        request = ControlRequest(
            service_name="payment",
            action="override",
            environment="ops",
            reason="test"
            # No ttl_minutes
        )

        response = control_api_handler.handle(request)

        assert response.status == "rejected"
        assert response.error_code == "TTL_REQUIRED_FOR_OPS_OVERRIDE"
```

---

## 4. TTL Management Tests

### Purpose

Validate TTL lifecycle including creation, expiration, and cleanup.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| TTL-001 | `test_ttl_expiration_reverts_state` | TTL expires | State reverts to default |
| TTL-002 | `test_ttl_expiration_sends_notification` | TTL expires | Notification sent |
| TTL-003 | `test_ttl_expiration_records_audit` | TTL expires | Audit entry created |
| TTL-004 | `test_ttl_extension_within_limits` | Extend TTL within max | Extension applied |
| TTL-005 | `test_ttl_extension_exceeds_max` | Extend TTL beyond max | Rejection |
| TTL-006 | `test_repeated_extension_requires_approval` | Second extension | Approval required |
| TTL-007 | `test_expiration_task_runs_periodically` | Celery task | Expired items processed |

### Required Test Implementation

```python
# tests/integration/control_api/test_ttl_management.py

from datetime import timedelta
from django.utils import timezone

class TestTTLManagement:
    """Tests for TTL lifecycle management."""

    def test_ttl_expiration_reverts_state(self):
        """
        Purpose:
            Verify that TTL expiration reverts service to default state.

        Scenario:
            1. Create block with 1 minute TTL
            2. Backdate created_at to simulate time passing
            3. Run expiration task
            4. Verify state reverted

        Expected:
            - Service state → default
            - Notification sent
            - Audit recorded
        """
        # Create block
        response = control_api_handler.handle(ControlRequest(
            service_name="payment",
            action="block",
            environment="ops",
            reason="test",
            ttl_minutes=1
        ))

        # Backdate expiration
        service_state = ServiceState.objects.get(service_name="payment")
        service_state.expires_at = timezone.now() - timedelta(minutes=1)
        service_state.save()

        # Run expiration task
        result = expire_control_api_overrides()

        # Verify
        service_state.refresh_from_db()
        assert service_state.state == "default"
        assert result["expired_count"] == 1

    def test_ttl_max_limit_enforced(self):
        """
        Purpose:
            Verify TTL max limit (60 min) is enforced in ops.

        Scenario:
            1. Attempt override with 120 min TTL in ops
            2. Verify rejection

        Expected:
            - Status: rejected
            - Error: TTL_EXCEEDS_POLICY_LIMIT
        """
        response = control_api_handler.handle(ControlRequest(
            service_name="payment",
            action="override",
            environment="ops",
            reason="test",
            ttl_minutes=120
        ))

        assert response.status == "rejected"
        assert response.error_code == "TTL_EXCEEDS_POLICY_LIMIT"
```

---

## 5. Authorization Tests

### Purpose

Validate role-based access control for different actions.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| AUTH-001 | `test_developer_can_use_test_api` | Developer in test | Success |
| AUTH-002 | `test_developer_cannot_use_ops_api` | Developer in ops | Rejection |
| AUTH-003 | `test_ops_engineer_can_use_ops_api` | Ops Engineer in ops | Success |
| AUTH-004 | `test_ops_commander_can_approve_critical` | CRITICAL action | Approval granted |
| AUTH-005 | `test_unauthorized_user_rejected` | No role | 401 Unauthorized |
| AUTH-006 | `test_expired_token_rejected` | Expired token | 401 Unauthorized |
| AUTH-007 | `test_insufficient_role_rejected` | Wrong role | 403 Forbidden |

### Required Test Implementation

```python
# tests/integration/control_api/test_authorization.py

class TestAuthorization:
    """Tests for role-based access control."""

    def test_developer_cannot_use_ops_api(self):
        """
        Purpose:
            Verify developers cannot use ops environment.

        Scenario:
            1. Authenticate as developer
            2. Attempt action in ops environment
            3. Verify rejection

        Expected:
            - Status: rejected
            - Error code: FORBIDDEN
        """
        developer = UserFactory(role="developer")

        response = control_api_handler.handle(
            ControlRequest(
                service_name="payment",
                action="allow",
                environment="ops",
                reason="test"
            ),
            actor=developer
        )

        assert response.status == "rejected"
        assert response.error_code == "FORBIDDEN"

    def test_critical_action_requires_approval(self):
        """
        Purpose:
            Verify CRITICAL risk actions require approval.

        Scenario:
            1. Execute action that triggers CRITICAL risk
            2. Verify pending_approval status
            3. Approve and verify execution

        Expected:
            - Initial status: pending_approval
            - After approval: success
        """
        pass
```

---

## 6. Risk Classification Tests

### Purpose

Validate that risk levels are correctly assessed and appropriate controls applied.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| RISK-001 | `test_allow_in_test_is_info_risk` | `allow` in test | INFO risk, auto-accept |
| RISK-002 | `test_block_in_ops_is_high_risk` | `block` in ops | HIGH risk, needs evidence |
| RISK-003 | `test_override_in_ops_is_critical_risk` | `override` in ops | CRITICAL risk, needs approval |
| RISK-004 | `test_inject_in_chaos_is_high_risk` | `inject_failure` in chaos | HIGH risk |
| RISK-005 | `test_critical_risk_requires_approval` | CRITICAL action | pending_approval |
| RISK-006 | `test_ai_cannot_execute_critical` | AI attempts CRITICAL | Rejection |

---

## 7. Validation Tests

### Purpose

Validate input validation rules.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| VAL-001 | `test_missing_service_name_rejected` | No service_name | 400 + MISSING_REQUIRED_FIELD |
| VAL-002 | `test_missing_action_rejected` | No action | 400 + MISSING_REQUIRED_FIELD |
| VAL-003 | `test_missing_reason_rejected` | No reason | 400 + MISSING_REASON |
| VAL-004 | `test_invalid_action_rejected` | Invalid action | 400 + INVALID_ACTION |
| VAL-005 | `test_invalid_environment_rejected` | Invalid environment | 400 + INVALID_ENVIRONMENT |
| VAL-006 | `test_nested_metadata_rejected` | Nested metadata | 400 + METADATA_NESTING_FORBIDDEN |
| VAL-007 | `test_oversized_metadata_rejected` | Metadata > 10KB | 400 + METADATA_TOO_LARGE |
| VAL-008 | `test_nonexistent_service_rejected` | Unknown service | 404 + SERVICE_NOT_FOUND |

### Required Test Implementation

```python
# tests/unit/control_api/test_validation.py

class TestValidation:
    """Tests for request validation."""

    def test_missing_reason_rejected(self):
        """
        Purpose:
            Verify that reason is required for all actions.

        Scenario:
            1. Submit request without reason
            2. Verify rejection

        Expected:
            - Status: rejected
            - Error code: MISSING_REASON
        """
        request = ControlRequest(
            service_name="payment",
            action="allow",
            environment="test"
            # No reason
        )

        result = validate_control_request(request)

        assert result.valid is False
        assert any(e.field == "reason" for e in result.errors)

    def test_nested_metadata_rejected(self):
        """
        Purpose:
            Verify nested objects in metadata are rejected.

        Scenario:
            1. Submit request with nested metadata
            2. Verify rejection

        Expected:
            - Status: rejected
            - Error code: METADATA_NESTING_FORBIDDEN
        """
        request = ControlRequest(
            service_name="payment",
            action="allow",
            environment="test",
            reason="test",
            metadata={
                "nested": {
                    "object": "not allowed"
                }
            }
        )

        result = validate_control_request(request)

        assert result.valid is False
```

---

## 8. Audit Trail Tests

### Purpose

Validate that all actions are properly audited.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| AUD-001 | `test_successful_action_audited` | Action succeeds | Audit entry created |
| AUD-002 | `test_rejected_action_audited` | Action rejected | Audit entry with rejection |
| AUD-003 | `test_ttl_expiration_audited` | TTL expires | Audit entry for expiration |
| AUD-004 | `test_audit_contains_who` | Any action | `actor` field populated |
| AUD-005 | `test_audit_contains_why` | Any action | `reason` field populated |
| AUD-006 | `test_audit_contains_previous_state` | State change | `previous_state` recorded |
| AUD-007 | `test_audit_immutable` | Attempt modification | Modification rejected |
| AUD-008 | `test_ai_suggestion_flagged` | AI suggests action | Flagged as non-executed |

---

## 9. Integration Tests

### Purpose

Validate integration with existing self-healing services.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| INT-001 | `test_allow_calls_circuit_breaker_close` | `allow` action | CircuitBreakerService.force_close() |
| INT-002 | `test_block_calls_circuit_breaker_open` | `block` action | CircuitBreakerService.force_open() |
| INT-003 | `test_allow_triggers_conditional_replay` | `allow` with flag | DLQ entries replayed |
| INT-004 | `test_state_change_emits_metrics` | Any state change | Prometheus metrics updated |
| INT-005 | `test_block_suspends_dlq_replay` | `block` active | No replay attempts |
| INT-006 | `test_inject_activates_chaos_service` | `inject_failure` | ChaosService activated |

### Required Test Implementation

```python
# tests/integration/control_api/test_service_integration.py

class TestServiceIntegration:
    """Tests for integration with existing services."""

    @patch.object(CircuitBreakerService, 'force_close')
    def test_allow_calls_circuit_breaker_close(self, mock_close):
        """
        Purpose:
            Verify allow action calls CircuitBreakerService.force_close.

        Expected:
            - force_close called with correct parameters
        """
        control_api_handler.handle(ControlRequest(
            service_name="payment",
            action="allow",
            environment="ops",
            reason="test recovery"
        ))

        mock_close.assert_called_once_with(
            service_name="payment",
            reason="test recovery",
            controlled_by=ANY
        )

    def test_allow_triggers_conditional_replay(self):
        """
        Purpose:
            Verify allow action with trigger_replay triggers DLQ replay.

        Scenario:
            1. Create DLQ entries for service
            2. Block service
            3. Allow service with trigger_replay=True
            4. Verify entries replayed

        Expected:
            - Pending DLQ entries processed
            - Resolved entries marked resolved
        """
        # Create DLQ entries
        dlq_entry = FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            status=FailedOperation.Status.PENDING,
            metadata={"service": "payment"}
        )

        # Allow with replay
        control_api_handler.handle(ControlRequest(
            service_name="payment",
            action="allow",
            environment="ops",
            reason="recovery",
            metadata={"trigger_replay": True}
        ))

        # Verify replay
        dlq_entry.refresh_from_db()
        assert dlq_entry.status in [
            FailedOperation.Status.RESOLVED,
            FailedOperation.Status.REQUIRES_REVIEW
        ]
```

---

## 10. Conditional Replay Tests

### Purpose

Validate conditional replay behavior on state changes.

### Test Cases

| Test ID | Test Name | Scenario | Expected |
|---------|-----------|----------|----------|
| CRP-001 | `test_replay_on_block_to_allow` | State: block → allow | Pending entries replayed |
| CRP-002 | `test_replay_failure_escalates` | Replay fails | Entry → REQUIRES_REVIEW |
| CRP-003 | `test_replay_success_resolves` | Replay succeeds | Entry → RESOLVED |
| CRP-004 | `test_security_violation_not_replayed` | Security entry | Entry skipped |
| CRP-005 | `test_replay_respects_max_attempts` | Entry with 2 replays | Entry skipped |
| CRP-006 | `test_state_stays_allow_on_replay_failure` | Replay fails | State still `allow` |

### Required Test Implementation

```python
# tests/integration/control_api/test_conditional_replay.py

class TestConditionalReplay:
    """Tests for conditional replay on state changes."""

    def test_replay_failure_does_not_revert_state(self):
        """
        Purpose:
            Verify that replay failure does not revert allow state.

        Scenario:
            1. Block service
            2. Create pending DLQ entry
            3. Allow service with failing replay handler
            4. Verify state stays allow

        Expected:
            - State remains allow
            - DLQ entry escalated to REQUIRES_REVIEW
            - Circuit breaker stays CLOSED

        Risk Covered:
            - R-017: Accidental state revert on replay failure
        """
        # Block service
        control_api_handler.handle(ControlRequest(
            service_name="payment",
            action="block",
            environment="ops",
            reason="test"
        ))

        # Create failing entry
        dlq_entry = FailedOperation.objects.create(
            domain="payment",
            failure_type="UNRECOVERABLE",
            status=FailedOperation.Status.PENDING,
            metadata={"service": "payment"}
        )

        # Allow with replay (will fail)
        with patch('replay_service.replay_single', side_effect=Exception("fail")):
            control_api_handler.handle(ControlRequest(
                service_name="payment",
                action="allow",
                environment="ops",
                reason="recovery",
                metadata={"trigger_replay": True}
            ))

        # Verify state unchanged
        service_state = ServiceState.objects.get(service_name="payment")
        assert service_state.state == "allow"

        # Verify entry escalated
        dlq_entry.refresh_from_db()
        assert dlq_entry.status == FailedOperation.Status.REQUIRES_REVIEW
```

---

## Summary

### Test Coverage Matrix

| Category | Test Count | Priority |
|----------|------------|----------|
| Action Execution | 7 | 🔴 HIGH |
| Environment Constraints | 7 | 🔴 HIGH |
| TTL Management | 7 | 🔴 HIGH |
| Authorization | 7 | 🔴 HIGH |
| Risk Classification | 6 | 🟡 MEDIUM |
| Validation | 8 | 🟡 MEDIUM |
| Audit Trail | 8 | 🟡 MEDIUM |
| Integration | 6 | 🔴 HIGH |
| Conditional Replay | 6 | 🔴 HIGH |
| **Total** | **62** | |

### Test File Structure

```
shopping/tests/
├── unit/
│   └── control_api/
│       ├── test_validation.py
│       ├── test_risk_classification.py
│       └── test_audit_schema.py
│
├── integration/
│   └── control_api/
│       ├── test_action_execution.py
│       ├── test_environment_constraints.py
│       ├── test_ttl_management.py
│       ├── test_authorization.py
│       ├── test_service_integration.py
│       └── test_conditional_replay.py
│
└── e2e/
    └── control_api/
        └── test_full_lifecycle.py
```

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) | API specification |
| [CONTROL_API_SECURITY_GOVERNANCE.md](./CONTROL_API_SECURITY_GOVERNANCE.md) | Security rules |
| [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) | Execution behavior |
| [Test Strategy](../2_STRATEGY/SELF_HEALING_TEST_STRATEGY.md) | Overall test strategy |
