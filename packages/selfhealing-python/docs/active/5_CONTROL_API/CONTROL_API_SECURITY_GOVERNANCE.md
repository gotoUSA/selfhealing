# Self-Healing Control API — Security & Governance Specification

> **Version**: 1.0
> **Created**: 2025-12-09
> **Status**: Active
> **Part of**: Self-Healing Control API Suite

---

## Table of Contents

1. [Overview](#1-overview)
2. [Governance Principles](#2-governance-principles)
3. [Authorization Model](#3-authorization-model)
4. [Risk Classification](#4-risk-classification)
5. [Evidence Requirements](#5-evidence-requirements)
6. [Approval & Escalation](#6-approval--escalation)
7. [Security Enforcement](#7-security-enforcement)
8. [Audit & Traceability](#8-audit--traceability)
9. [Compliance Boundaries](#9-compliance-boundaries)
10. [AI Participation Rules](#10-ai-participation-rules)
11. [Security Incident Handling](#11-security-incident-handling)
12. [Related Documents](#12-related-documents)

---

## 1. Overview

### Purpose

This document establishes **security, authorization, risk classification, audit, and compliance boundaries** for the Self-Healing Control API.

### Scope

- ✅ Who can execute control actions
- ✅ Required evidence for each action
- ✅ Risk classification and allowed environments
- ✅ Approval, escalation, and audit trails

### Position in Document Suite

| Document | Role |
|----------|------|
| [Control API Interface](./CONTROL_API_INTERFACE.md) | **What** = Contract |
| [Control API Execution](./CONTROL_API_EXECUTION.md) | **How** = Execution |
| **This Document** | **Who/Why** = Authority & Risk & Evidence |

> This document serves as the **SINGLE source of governance truth**.
> Compact API specifications do NOT duplicate governance sections.

---

## 2. Governance Principles

### Why Governance Matters

Self-Healing Control affects:

| Impact Area | Risk |
|-------------|------|
| Service Availability | Operations can be blocked |
| System Reliability | Recovery can be disrupted |
| Finance & SLA Exposure | SLA breaches may occur |
| Customer Experience | User-facing impacts |

### Core Principles

| Principle | Meaning |
|-----------|---------|
| **Least Privilege** | Not everyone can break or bypass systems |
| **Justified Control** | Reason documented before any action |
| **Reversibility** | No permanent override without review |
| **Observability** | Evidence enables future prevention |
| **Consistency** | AI and humans follow same rules |

### Governance Guarantee

> **"Power exists, but guarded with clarity and accountability."**

---

## 3. Authorization Model

### Authority Types

| Authority Type | Example Roles | Allowed Actions |
|----------------|---------------|-----------------|
| **Ops Commander** | SRE Lead, Incident Commander | `allow`, `block`, `override`, `reset` |
| **Chaos Engineer** | Reliability Team, Platform Team | `inject_failure` (non-ops only) |
| **Test Automation** | CI/CD Pipeline, QA Bots | `allow`, `block` (test/staging only) |
| **Developer** | Development Team | `allow`, `block` (test only) |
| **Security Team** | Security, Compliance | Read access, Review, Approve |

### Role-Based Execution Matrix

| Role | Test API | Chaos API | Ops API |
|------|----------|-----------|---------|
| **Developer** | ✅ Allowed | ❌ Not allowed | ❌ Not allowed |
| **QA / Reliability Engineer** | ✅ Allowed | ✅ Allowed | ❌ Not allowed |
| **Ops Engineer** | ✅ Allowed | ✅ Allowed | ✅ Allowed |
| **Ops Commander** | ✅ Allowed | ✅ Allowed | ✅ Allowed (CRITICAL) |
| **Security / Compliance** | 📖 Read only | 📖 Read only | ✅ Review / Approve |

> **Security is not an executor** — it is an approver and auditor.

### Permission Formula

```
Authority + Purpose + Evidence = Permission
```

### Critical Restrictions

| Restriction | Rule |
|-------------|------|
| `inject_failure` in Ops | ❌ **CANNOT** be executed in `ops` environment |
| `override` in Ops | Requires TTL + Evidence |
| CRITICAL risk actions | Requires human approval |

---

## 4. Risk Classification

### Risk Levels (NIST-Aligned)

| Level | Impact | Examples | Rules |
|-------|--------|----------|-------|
| **INFO** | No SLA impact | Enable logging, status check | Auto-accept |
| **WARNING** | Minor disruption | Slow response test, minor policy change | Needs reason |
| **HIGH** | Production side-effect | Limit checkout, disable feature | Needs evidence |
| **CRITICAL** | Financial/SLA breach | Override payment, bypass security | Needs approval + TTL |

### Risk Mapping by Action

| Action | Environment | Risk Level |
|--------|-------------|------------|
| `allow` | test | INFO |
| `allow` | chaos | INFO |
| `allow` | ops | WARNING |
| `block` | test | INFO |
| `block` | chaos | WARNING |
| `block` | ops | HIGH |
| `override` | test | WARNING |
| `override` | chaos | HIGH |
| `override` | ops | **CRITICAL** |
| `reset` | any | WARNING |
| `inject_failure` | test | INFO |
| `inject_failure` | chaos | HIGH |
| `inject_failure` | ops | ❌ FORBIDDEN |

### AI Risk Simulation Rules

```yaml
AI Capabilities:
  - May propose: WARNING, HIGH risk actions
  - Cannot autonomously execute: CRITICAL risk actions
  - Human approval: Mandatory for CRITICAL

Principle:
  Risk ≠ action
  Risk = environment + target + timing + capability
```

---

## 5. Evidence Requirements

### Purpose

Evidence is **proof of WHY** control is justified.

### Evidence by Action

| Action | Evidence Required | Example |
|--------|-------------------|---------|
| `allow` | reason | "maintenance complete" |
| `block` | reason | "PG maintenance announced" |
| `override` | reason + incident reference | "incident #88422 - payment SLA breach" |
| `inject_failure` | reason | "chaos ticket CHAOS-123" |
| `override` in ops | reason + correlation + SLA data | "SLA breach 0.8s, correlation_id: xyz" |

### Minimum Evidence Fields

```json
{
  "reason": "string (required)",
  "correlation_id": "uuid (recommended for ops)",
  "last_failure_timestamp": "ISO8601 (recommended)",
  "impact_scope": "test | staging | ops"
}
```

### Evidence for Ops Override

```json
{
  "reason": "external-api-latency-breach",
  "correlation_id": "abcd-1234-efgh",
  "incident_reference": "INC-2025-001234",
  "sla_data": {
    "threshold_ms": 500,
    "actual_ms": 1840,
    "error_rate": 0.12
  },
  "approver": "ops-commander@company.com"
}
```

### No Control Without Context

> Every control action must answer: **"Why is this justified?"**

---

## 6. Approval & Escalation

### Approval Requirements by Risk

| Risk Level | Required Approval |
|------------|-------------------|
| **INFO** | None (auto-accept) |
| **WARNING** | Implicit (logged) |
| **HIGH** | Team authority required |
| **CRITICAL** | Ops Commander / Designated Approver |

### Escalation Flow

```
┌─────────────────┐
│ Request Received│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Assess Risk    │
│     Level       │
└────────┬────────┘
         │
    ┌────┼────┬────────┬─────────┐
    ▼    ▼    ▼        ▼         ▼
 INFO  WARN  HIGH   CRITICAL  FORBIDDEN
    │    │    │        │         │
    ▼    ▼    ▼        ▼         ▼
 Auto  Log   Team   Human     Reject
Accept Only  Auth   Approval
```

### Approval Workflow

```yaml
CRITICAL Approval Process:
  1. Request submitted with full evidence
  2. Notification sent to designated approvers
  3. Approver reviews evidence and risk
  4. Approver approves or rejects with reason
  5. If approved: Action executed with audit trail
  6. If rejected: Requester notified with feedback
  7. All decisions logged for compliance
```

### AI Role in Escalation

| AI Capability | Allowed |
|---------------|---------|
| Suggest escalation | ✅ |
| Force execution | ❌ |
| Approve CRITICAL | ❌ |
| Bypass reason/TTL | ❌ |

> **AI can suggest** but **cannot force** execution or approval.

---

## 7. Security Enforcement

### API Access Requirements by Category

| API Category | Purpose | Allowed Environment | Authentication | Approval |
|--------------|---------|---------------------|----------------|----------|
| **Test API** | Validate reliability | Dev / Stage only | Internal token | No |
| **Chaos API** | Fault injection | Internal network + VPN | Token + Role claim | Conditional |
| **Ops API** | System override | Private network / Bastion | Auth + Role + Reason + TTL | Yes (CRITICAL) |

### Authentication Requirements

```yaml
Required:
  - TLS 1.2+ for all API calls
  - Expired tokens MUST be rejected
  - Token refresh before expiration

Token Claims (should embed):
  - role: "ops_commander" | "reliability_engineer" | "developer"
  - team: "sre" | "platform" | "dev"
  - requester_email: "user@company.com"
  - reason_classification: "planned" | "emergency" | "chaos"
  - correlation_id: "uuid" (recommended)
```

### Actions Requiring Explicit Approval

The following actions **MUST NOT** be executed without approval:

| Action | Reason |
|--------|--------|
| Disable policy enforcement | Risk of uncontrolled behavior |
| Global reset / recovery override | Wide blast radius |
| TTL extension beyond configured duration | Extended exposure |
| Repeated overrides (2+ within TTL window) | Pattern detection |
| System-wide "Fail-Open" mode | Maximum risk |

### Automatic Expiration

```yaml
Rule: CRITICAL overrides MUST expire automatically
Implementation:
  - TTL stored in database
  - Expiration auditable
  - Auto-close task runs every 5 minutes
```

---

## 8. Audit & Traceability

### Audit Trail Requirements

Every control action writes:

| Field | Purpose |
|-------|---------|
| `who` | Accountability - who performed the action |
| `what` | Reproducibility - what action was taken |
| `why` | Decision context - reason provided |
| `when` | Timestamp for timeline |
| `duration` | Risk exposure window (TTL) |
| `previous_state` | Rollback capability |
| `result` | Outcome (accept/reject) |
| `evidence` | Epistemic accountability |

### Audit Record Schema

```json
{
  "audit_id": "audit-2025-12-09-001",
  "timestamp": "2025-12-09T10:30:00Z",
  "actor": {
    "user_id": 123,
    "email": "sre@company.com",
    "role": "ops_commander",
    "ip_address": "10.0.1.50"
  },
  "action": {
    "type": "override",
    "service_name": "payment",
    "environment": "ops",
    "ttl_minutes": 45
  },
  "reason": {
    "provided": "external-api-latency-breach",
    "classification": "external-dependency-failure"
  },
  "previous_state": "allow",
  "new_state": "override",
  "evidence": {
    "latency_avg_ms": 1840,
    "error_rate": 0.12
  },
  "result": "success",
  "expires_at": "2025-12-09T11:15:00Z"
}
```

### Audit Events

| Event | Generates Audit |
|-------|-----------------|
| Action executed | ✅ |
| Action rejected | ✅ |
| TTL expiration | ✅ |
| Manual reset | ✅ |
| AI suggestion | ✅ (flagged as non-executed) |

### Audit Immutability

```yaml
Rules:
  - Audit entries are IMMUTABLE within retention policy
  - No modification after creation
  - Soft-delete only after retention period
  - Compliance with financial audit requirements
```

### Audit vs Logging

| Audit | Logging |
|-------|---------|
| Legal memory | Operational debugging |
| Immutable | Rotatable |
| Compliance-grade | Best-effort |
| Structured schema | Free-form |

> **Audit is not logging** — Audit is **legal memory**.

---

## 9. Compliance Boundaries

### Boundary Rules

| Boundary | Purpose |
|----------|---------|
| No permanent override | Reversibility guarantee |
| No chaos in ops | Production safety |
| TTL required in ops | Exposure limitation |
| Reason mandatory | Abuse prevention |
| Approval for CRITICAL | Governance enforcement |

### Compliance Alignment

| Standard | Relevant Controls | How This Addresses |
|----------|-------------------|-------------------|
| **SOC 2** | CC6.1 (Logical Access) | Role-based access control |
| **SOC 2** | CC7.2 (Monitoring) | Audit trail, evidence |
| **ISO 27001** | A.9.4.1 (Access Control) | Authorization model |
| **ISO 27001** | A.12.4.1 (Logging) | Audit records |
| **NIST SP 800-53** | AU-3 (Audit Content) | Evidence requirements |
| **NIST SP 800-53** | IR-4 (Incident Handling) | Escalation process |
| **PCI-DSS** | 10.x (Logging) | Audit trail for payment |

### Tenant-Specific Policies

```yaml
Requirement:
  Implementation MUST support policy injection by tenant

Variations:
  - Region-specific compliance
  - Industry-specific rules
  - SLA contract requirements
  - Custom approval workflows
```

---

## 10. AI Participation Rules

### AI Capabilities

| Capability | Allowed |
|------------|---------|
| Suggest tests | ✅ |
| Simulate risk | ✅ |
| Auto-reject missing fields | ✅ |
| Classify reasons | ✅ |
| Suggest remediation | ✅ |
| Group related failures | ✅ |
| Execute CRITICAL | ❌ |
| Approve or escalate | ❌ |
| Bypass reason/TTL | ❌ |
| Make irreversible decisions | ❌ |

### AI Role Definition

```yaml
AI is:
  - Advisor
  - Reviewer
  - Risk simulator
  - Pattern recognizer

AI is NOT:
  - Governor
  - Final decision maker
  - Override authority
```

### AI Benefits

| Benefit | Description |
|---------|-------------|
| Reduced alert fatigue | AI groups and classifies |
| Faster time to recovery | AI suggests remediation |
| Lower escalation load | AI handles routine classification |
| Reduced cognitive load | AI provides context |

### Human-AI Collaboration

```
AI does not override governance.
AI supports human decision-making.
Human owns consequence.
```

---

## 11. Security Incident Handling

### Security Violations in Control API

Security violations in the Control API context are handled separately from normal DLQ:

| Violation Type | Severity | Action |
|----------------|----------|--------|
| Unauthorized API access | CRITICAL | Block + Log IP + Alert |
| Invalid token | HIGH | Reject + Log |
| Role violation | HIGH | Reject + Log + Alert |
| Rate limit abuse | MEDIUM | Temporary ban |
| Suspicious patterns | MEDIUM | Log + Review |

### Never Self-Heal Security Violations

```yaml
Rule: Security violations NEVER auto-retry

Response Flow:
  1. Block immediately
  2. Create SecurityIncident record
  3. Take protective action (ban, invalidate)
  4. Send multi-channel notification
  5. Log for security audit
```

### Integration with SecurityViolationService

The Control API integrates with the existing security infrastructure:

- `shopping/services/self_healing/security_violation_service.py`
- `shopping/services/self_healing/security_notification_service.py`

---

## 12. Related Documents

| Document | Role | Location |
|----------|------|----------|
| **Control API Interface** | What = Contract | [CONTROL_API_INTERFACE.md](./CONTROL_API_INTERFACE.md) |
| **Control API Execution** | How = Execution | [CONTROL_API_EXECUTION.md](./CONTROL_API_EXECUTION.md) |
| **Architecture** | System overview | [../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md](../0_OVERVIEW/SELF_HEALING_ARCHITECTURE.md) |
| **Operations** | DLQ, Replay, Metrics | [../0_OVERVIEW/SELF_HEALING_OPERATIONS.md](../0_OVERVIEW/SELF_HEALING_OPERATIONS.md) |
| **Test Requirements** | Test specifications | [../1_REQUIREMENTS/SELF_HEALING_TEST_REQUIREMENTS_SPECIFICATION.md](../1_REQUIREMENTS/SELF_HEALING_TEST_REQUIREMENTS_SPECIFICATION.md) |

---

## Summary

This governance document ensures:

| Guarantee | Implementation |
|-----------|----------------|
| **Who can act** | Role-based authorization |
| **Why they can act** | Evidence requirements |
| **What risk level** | NIST-aligned classification |
| **How it's approved** | Escalation workflow |
| **How it's tracked** | Immutable audit trail |
| **AI boundaries** | Clear participation rules |

> **Governance ensures**: "Power exists, but guarded with clarity and accountability."

---

*This document defines governance. For API interface, see [Interface](./CONTROL_API_INTERFACE.md). For execution behavior, see [Execution](./CONTROL_API_EXECUTION.md).*
