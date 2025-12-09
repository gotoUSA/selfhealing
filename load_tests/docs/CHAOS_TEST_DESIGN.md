# Payment Load Test Suite — Design Document

> **Version**: 1.1
> **Last Modified**: 2025-12-07
> **Project**: Solo Developer e-commerce Payment System

---

## ⚠️ Scope and Constraints

> This document describes the Payment Load Test design for a **solo developer e-commerce project**.
>
> - **Required Implementation**: Stage 0-5 (Environment Verification → Rollback Verification)
> - **Extension/Reference**: Stage 6-9 (Chaos, Race, Webhook, Soak)
> - **Current Limitation**: Client-side simulation (not actual PG/network fault injection)

### Current Implementation Limitations

| Test | Document Goal | Actual Implementation |
|------|---------------|----------------------|
| Latency Injection | PG Server Delay | Locust Client `time.sleep()` |
| Webhook Test | Real Toss Signature Verification | Direct handler call without signature |
| Race Condition | DB Level Concurrency | Locust concurrent requests (limited) |

> 📌 True Chaos Engineering (Toxiproxy, server-side middleware, etc.) will be considered for future expansion

---

## 📍 Test Stage Overview

### Required Stages (Stage 0-5)

| Stage | Name | Purpose | Users | Duration |
|-------|------|---------|-------|----------|
| **0** | Smoke | Environment/Login/Basic Flow Verification | 5 | 30s |
| **1** | Happy Load | Normal Load Performance Measurement | 50~200 | 3m |
| **2** | Idempotency | Duplicate Payment Prevention Verification | 30 | 2m |
| **3** | Latency | Latency Scenario Behavior Verification | 50 | 3m |
| **4** | Cancel Storm | Post-Payment Cancel Stress | 50 | 2m |
| **5** | Rollback | Stock/Point Recovery Verification | 30 | 3m |

### Extension Stages (Stage 6-9) — Optional

| Stage | Name | Purpose | Notes |
|-------|------|---------|-------|
| **6** | Chaos Random | Random Failure Injection | Client simulation |
| **7** | Race Conflict | Concurrent Payment Collision | Locust concurrency limits |
| **8** | Webhook | Webhook Handler Test | No signature verification |
| **9** | Soak | Long-term Stability | Recommend excluding from CI |

---

## 📁 Directory Structure

```
load_tests/
├── README.md                    # Quick Start Guide
├── config.py                    # Python Configuration
├── locustfile.py                # Main Entry Point
│
├── docs/
│   └── CHAOS_TEST_DESIGN.md     # This Document
│
├── scenarios/                   # Stage-specific Scenarios
│   ├── stage0_smoke.py
│   ├── stage1_happy_load.py
│   ├── stage2_idempotent.py
│   ├── stage3_latency.py
│   ├── stage4_cancel_storm.py
│   ├── stage5_rollback.py
│   ├── stage6_chaos_random.py   # [Extension]
│   ├── stage7_race_conflict.py  # [Extension]
│   ├── stage8_webhook.py        # [Extension]
│   └── stage9_soak.py           # [Extension]
│
├── utils/                       # Common Helpers
│   ├── login_helper.py
│   ├── product_helper.py
│   ├── cart_helper.py
│   └── payment_helper.py
│
├── validators/                  # Data Integrity Validators
│   ├── stock_validator.py
│   ├── point_validator.py
│   └── order_validator.py
│
├── chaos/                       # Fault Simulation (Client-side)
│   └── fault_injector.py
│
├── metrics/                     # Custom Metrics
│   ├── custom_metrics.py
│   └── event_hooks.py
│
├── runners/                     # Execution Scripts
│   ├── config.yaml
│   ├── run_stage.py
│   ├── smoke.sh / smoke.ps1
│   └── full_cycle.sh / full_cycle.ps1
│
├── fixtures/                    # Test Data
│   ├── seeder.py
│   └── cleaner.py
│
└── reports/                     # Result Reports
```

---

## 📑 Stage Details

### Stage 0 — Smoke

```python
# Environment Normal Operation Verification
# Users: 5, Duration: 30s

- Verify login success
- Verify product list retrieval
- Single product order + 1 successful payment
```

### Stage 1 — Happy Load

```python
# Performance measurement under normal load
# Users: 50 → 100 → 200

- Repeat full payment flow
- Measure P95/P99 latency
- Target error rate < 1%
```

### Stage 2 — Idempotency

```python
# Duplicate Payment Prevention Verification

- Request twice with same payment_key
- First request: 200/201, Second: 400/409
- CRITICAL if both return 200
```

### Stage 3 — Latency

```python
# Latency Scenario Behavior Verification
# ⚠️ Simulated with client-side sleep

- Wait 500~3000ms before request
- Verify timeout handling logic
```

### Stage 4 — Cancel Storm

```python
# Post-Payment Cancel Stress

- Cancel within 0.1~1 second after confirm
- Measure cancel success rate
- Verify stock recovery
```

### Stage 5 — Rollback

```python
# Stock/Point Rollback Verification on Failure

1. Save stock_before, point_before
2. Trigger forced payment failure
3. Check stock_after, point_after
4. Verify before == after
```

---

## 📊 Measurement Metrics

| Metric | Description | Target |
|--------|-------------|--------|
| P95 | Excludes top 5% | SLA baseline |
| P99 | Excludes top 1% | Warning threshold |
| Error Rate (4xx) | Client errors | < 5% |
| Error Rate (5xx) | Server errors | < 0.1% |

---

## 🚀 Execution Methods

### Single Stage

```bash
# Smoke Test
python load_tests/runners/run_stage.py stage0_smoke

# Happy Load
python load_tests/runners/run_stage.py stage1_happy
```

### Profiles

```bash
# Quick (Stage 0-2)
python load_tests/runners/run_stage.py --profile quick

# Standard (Stage 0-5)
python load_tests/runners/run_stage.py --profile standard

# List stages
python load_tests/runners/run_stage.py --list
```

---

## ✅ Success Criteria

| Stage | Success Condition |
|-------|-------------------|
| Stage 0 | 100% success, 0 errors |
| Stage 1 | P99 < SLA, Error rate < 1% |
| Stage 2 | 0 duplicate payments |
| Stage 3 | Normal processing after delay |
| Stage 4 | Cancel success rate > 95% |
| Stage 5 | 100% Rollback success |

---

## 📌 Future Expansion Considerations

When introducing true Chaos Engineering:

| Method | Description |
|--------|-------------|
| Toxiproxy | Network-level latency/fault injection |
| Server TEST_MODE | Django middleware fault injection |
| DB Level Lock Test | Separate test scripts |
| Real PG Sandbox | Toss test environment integration |

---

> This document aims for practical load testing for a solo developer project.
> Consider enterprise-grade Chaos Engineering when team/infrastructure scales.
