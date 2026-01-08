# Error Budget 관리 및 배포 동결 권고 시스템

> **핵심 원칙**: "시스템은 조언하고, 결정은 사람이 한다."

## 📋 목차

1. [개요](#1-개요)
2. [Error Budget 계산](#2-error-budget-계산)
3. [배포 정책 어드바이저](#3-배포-정책-어드바이저)
4. [결정 기록 (Audit Trail)](#4-결정-기록-audit-trail)
5. [API 레퍼런스](#5-api-레퍼런스)
6. [메트릭 및 모니터링](#6-메트릭-및-모니터링)
7. [OpenTelemetry 연동](#7-opentelemetry-연동)
8. [운영 가이드](#8-운영-가이드)
9. [Error Budget Gate (자동화 제어)](#9-error-budget-gate-자동화-제어)
   - [9.7 Fail-Open Rate Limiting](#97-fail-open-rate-limiting-최소한의-제약이-있는-방임)
   - [9.9 Circuit Breaker](#99-circuit-breaker-빠른-실패)
   - [9.10 Alert Manager](#910-alert-manager-운영-알림)
   - [9.11 Health Endpoint](#911-health-endpoint-상태-조회-api)
   - [9.12 Grafana Dashboard](#912-grafana-dashboard)
10. [동적 설정 (Runtime Configuration)](#10-동적-설정-runtime-configuration)
11. [Fail-Safe Self-Reporting](#11-fail-safe-self-reporting-침묵하는-장애-방지)
12. [고급 관측성 기능](#12-고급-관측성-기능)
13. [Reconciliation (Shadow Budget)](#13-reconciliation-shadow-budget)

---

## 1. 개요

### 1.1 Error Budget이란?

Error Budget은 SLO(Service Level Objective)에서 허용하는 오류의 양입니다. 예를 들어:

- **SLO 99.9% 가용성** = 0.1% Error Budget
- 30일 기준: **43.2분**의 장애 시간 허용

```
┌──────────────────────────────────────────────────────────────┐
│                     Error Budget 개념도                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   SLO Target: 99.9%  ═══════════════════════════════════▶   │
│                      ┌─────────────────────────────────┐    │
│   Budget Total:      │███████████████████████████████│    │
│   43.2 min/month     │      100% (43.2 min)          │    │
│                      └─────────────────────────────────┘    │
│                                                              │
│   Budget Consumed:   ┌─────────────────────────────────┐    │
│   (에러/장애 발생)    │████████░░░░░░░░░░░░░░░░░░░░░│    │
│                      │  25% consumed │ 75% remaining  │    │
│                      └─────────────────────────────────┘    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 시스템 목적

| 목적 | 설명 |
|------|------|
| **버짓 가시화** | Error Budget 잔여량을 실시간으로 계산 및 표시 |
| **배포 위험 평가** | 버짓 상태에 따른 배포 권고 제공 |
| **Governance 기록** | 동결/무시 결정을 Audit Trail에 기록 |
| **조기 경보** | Burn Rate 기반 급속 소진 감지 |

### 1.3 Fail-Safe 설계

> **핵심 원칙: Fail-Open**

Error Budget 시스템 자체가 장애를 일으킬 경우, **배포를 막는 것보다 허용하는 것이 더 안전합니다.**

```
┌──────────────────────────────────────────────────────────────┐
│                 Fail-Safe Decision Flow                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Error Budget 시스템 장애 발생                              │
│              │                                               │
│              ▼                                               │
│   ┌───────────────────────────────┐                          │
│   │ Fail-Safe Response 반환       │                          │
│   │                               │                          │
│   │ • status: "degraded"          │                          │
│   │ • verdict: PROCEED            │ ◀── 기본 허용            │
│   │ • can_deploy: true            │                          │
│   │ • failsafe_applied: true      │                          │
│   └───────────────────────────────┘                          │
│              │                                               │
│              ▼                                               │
│   CI/CD 파이프라인 계속 동작                                  │
│   (⚠️ 경고 표시, 수동 확인 권장)                             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

| 장애 시나리오 | 동작 | 근거 |
|--------------|------|------|
| DB 연결 실패 | 기본값 PROCEED | CI/CD 중단 방지 |
| Redis 타임아웃 | 기본값 PROCEED | 가용성 우선 |
| SLO 조회 실패 | Budget 100% 가정 | 안전한 기본값 |

**응답 예시 (장애 시):**
```json
{
  "status": "degraded",
  "data": {
    "verdict": {
      "status": "proceed",
      "can_deploy": true
    },
    "message": "⚠️ Error Budget 시스템 일시적 오류. 기본값 PROCEED 적용됨."
  },
  "degraded_mode": true,
  "failsafe_applied": true
}
```

### 1.4 아키텍처

```
┌────────────────────────────────────────────────────────────────────┐
│                    Error Budget Management System                   │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   ┌───────────────────┐      ┌───────────────────┐                │
│   │ ErrorBudget       │      │ DeploymentPolicy  │                │
│   │ Calculator        │─────▶│ Advisor           │                │
│   │                   │      │                   │                │
│   │ - SLO 기반 계산   │      │ - 상태 판정       │                │
│   │ - Burn Rate 산출  │      │ - 권고 메시지     │                │
│   └───────────────────┘      └─────────┬─────────┘                │
│                                        │                           │
│                                        ▼                           │
│                          ┌───────────────────┐                     │
│                          │ FreezeDecision    │                     │
│                          │ Recorder          │                     │
│                          │                   │                     │
│                          │ - Audit Trail     │                     │
│                          │ - Override 관리   │                     │
│                          └─────────┬─────────┘                     │
│                                    │                               │
│          ┌─────────────────────────┼─────────────────────────┐    │
│          ▼                         ▼                         ▼    │
│   ┌─────────────┐         ┌─────────────┐         ┌─────────────┐ │
│   │ Prometheus  │         │OpenTelemetry│         │   REST API  │ │
│   │  Metrics    │         │   Events    │         │  Endpoints  │ │
│   └─────────────┘         └─────────────┘         └─────────────┘ │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. Error Budget 계산

### 2.1 계산 로직

```python
from selfhealing.services.error_budget_service import ErrorBudgetCalculator

calculator = ErrorBudgetCalculator()
status = calculator.calculate_budget_status(slo_name="availability")


print(f"SLO: {status.slo_target * 100}%")
print(f"Budget Total: {status.budget_total_minutes:.1f} min/month")
print(f"Budget Remaining: {status.budget_remaining_percent:.1f}%")
print(f"Burn Rate (1h): {status.burn_rate_1h:.2f}x")
```

### 2.2 Burn Rate

Burn Rate는 Error Budget이 소진되는 속도입니다.

| Burn Rate | 의미 | 소진 속도 |
|-----------|------|-----------|
| **1.0x** | 정상 | 30일 후 100% 소진 |
| **3.0x** | 느린 경고 | 10일 후 100% 소진 |
| **6.0x** | 빠른 경고 | 5일 후 100% 소진 |
| **14.4x** | 위험 | 2일 후 100% 소진 |

```
Burn Rate 계산:
                실제 에러율
Burn Rate = ─────────────────
              허용 에러율 (1 - SLO)
```

### 2.3 Multi-Window Burn Rate Alerting

Google SRE Workbook에서 권장하는 방식:

| 윈도우 | Burn Rate 임계값 | Budget 소진 | 조치 |
|--------|------------------|-------------|------|
| **1시간** | > 14.4x | 2% | 🔴 즉시 대응 (Page) |
| **6시간** | > 6.0x | 5% | 🟠 경고 (Ticket) |
| **3일** | > 1.0x | 10% | 🟡 주간 리뷰 |

---

## 3. 배포 정책 어드바이저

### 3.1 판정 상태

| 상태 | Budget 잔여 | 권고 |
|------|-------------|------|
| 🟢 **PROCEED** | ≥ 75% | 정상 배포 가능 |
| 🟡 **CAUTION** | 50-75% | 주의하여 배포 |
| 🟠 **WARNING** | 20-50% | 신규 기능 배포 자제 |
| 🔴 **FREEZE_RECOMMENDED** | < 20% | 긴급 패치 외 배포 중단 권고 |

```python
from selfhealing.services.error_budget_service import DeploymentPolicyAdvisor

advisor = DeploymentPolicyAdvisor()
verdict = advisor.get_deployment_verdict()

print(f"Status: {verdict.status.value}")
print(f"Message: {verdict.message}")
print(f"Can Deploy: {verdict.can_deploy}")
print(f"Allowed Types: {verdict.allowed_deployment_types}")
```

### 3.2 상태별 허용 배포 유형

| 상태 | feature | enhancement | refactor | hotfix | security_patch | rollback |
|------|---------|-------------|----------|--------|----------------|----------|
| PROCEED | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| CAUTION | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| WARNING | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| FREEZE | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |

### 3.3 Fast Burn 트리거

Fast Burn Rate (1시간 > 14.4x) 감지 시, Budget 잔여량과 관계없이 즉시 **FREEZE_RECOMMENDED** 상태로 전환됩니다.

---

## 4. 결정 기록 (Audit Trail)

### 4.1 결정 유형

| 결정 유형 | 설명 |
|-----------|------|
| `freeze_acknowledged` | 운영자가 동결 권고를 확인하고 동결 확정 |
| `override_approved` | 운영자가 동결을 무시하고 배포 진행 승인 |
| `freeze_lifted` | 동결 해제 (Budget 회복 또는 상황 종료) |

### 4.2 Override 유형

| 유형 | 설명 | 유효 기간 |
|------|------|-----------|
| `hotfix` | 긴급 버그 수정 | 기본 4시간 |
| `security_patch` | 보안 패치 | 기본 4시간 |
| `executive_approval` | 경영진 승인 | 기본 4시간 |
| `rollback` | 롤백 배포 | 기본 4시간 |

### 4.3 기록 예시

```python
from selfhealing.services.error_budget_service import (
    ErrorBudgetService,
    OverrideType,
)

service = ErrorBudgetService()

# 동결 확정
service.acknowledge_freeze(
    decided_by="ops_lead",
    justification="Error budget critical, pausing all deployments"
)

# Override 승인 (긴급 보안 패치)
service.approve_override(
    decided_by="cto",
    justification="Critical CVE-2024-XXXX security patch",
    override_type=OverrideType.SECURITY_PATCH,
    deployment_name="auth-service v2.1.0",
    expires_hours=2,
)

# 동결 해제
service.lift_freeze(
    decided_by="ops_lead",
    justification="Situation resolved, budget recovered to 65%"
)
```

---

## 5. API 레퍼런스

### 5.1 Error Budget API

#### GET /api/self-healing/error-budget/status/

현재 Error Budget 상태 조회.

**Query Parameters:**
- `slo_name` (optional): SLO 이름 (default: "availability")

**Response:**
```json
{
  "status": "success",
  "data": {
    "slo": {
      "name": "availability",
      "target": 0.999,
      "target_percentage": "99.90%",
      "window_days": 30
    },
    "budget": {
      "total_minutes": 43.2,
      "consumed_minutes": 15.3,
      "remaining_minutes": 27.9,
      "remaining_percent": 64.58
    },
    "burn_rate": {
      "rate_1h": 2.1,
      "rate_6h": 1.8,
      "is_fast_burn": false,
      "is_slow_burn": false
    },
    "health": {
      "is_healthy": false,
      "is_critical": false
    }
  }
}
```

#### GET /api/self-healing/error-budget/history/

결정 이력 조회.

**Query Parameters:**
- `limit` (optional): 최대 조회 건수 (default: 50)
- `decision_type` (optional): 결정 유형 필터

---

### 5.2 Deployment Policy API

#### GET /api/self-healing/deployment-policy/verdict/

배포 가능 여부 판정.

**Response:**
```json
{
  "status": "success",
  "data": {
    "verdict": {
      "status": "warning",
      "can_deploy": false,
      "requires_override": false,
      "has_active_override": false
    },
    "message": "🟠 Error Budget 경고 수준입니다. 신규 기능 배포를 자제해주세요.",
    "recommendation": "배포 동결을 고려하고, 기존 이슈 해결에 집중하세요.",
    "reasons": [
      "Error Budget 잔여량 경고: 35.2% (임계값: 50%)"
    ],
    "allowed_deployment_types": ["hotfix", "security_patch", "rollback"]
  }
}
```

#### POST /api/self-healing/deployment-policy/acknowledge/

배포 동결 확정.

**Request:**
```json
{
  "justification": "Error budget critical, pausing all deployments"
}
```

#### POST /api/self-healing/deployment-policy/override/

동결 무시 승인.

**Request:**
```json
{
  "justification": "Critical security patch for CVE-2024-XXXX",
  "override_type": "security_patch",
  "deployment_id": "deploy-abc123",
  "deployment_name": "payment-service v1.2.3",
  "expires_hours": 4
}
```

#### POST /api/self-healing/deployment-policy/lift/

동결 해제.

**Request:**
```json
{
  "justification": "Error budget recovered to healthy level"
}
```

#### GET /api/self-healing/deployment-policy/active-override/

활성 Override 조회.

---

## 6. 메트릭 및 모니터링

### 6.1 Prometheus 메트릭

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `error_budget_remaining_percent` | Gauge | Error Budget 잔여량 (%) |
| `error_budget_remaining_minutes` | Gauge | Error Budget 잔여량 (분) |
| `error_budget_burn_rate_1h` | Gauge | 1시간 Burn Rate |
| `error_budget_burn_rate_6h` | Gauge | 6시간 Burn Rate |
| `deployment_freeze_status` | Gauge | 동결 상태 (0-3) |
| `freeze_decision_total` | Counter | 동결 결정 횟수 |
| `deployment_active_override` | Gauge | 활성 Override 여부 |

### 6.2 Alerting Rules

```yaml
groups:
  - name: error_budget
    rules:
      - alert: ErrorBudgetCritical
        expr: error_budget_remaining_percent < 20
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "🔴 Error Budget 위험"
          description: "Error Budget 잔여량이 {{ $value }}%입니다. 배포 동결을 권고합니다."

      - alert: ErrorBudgetFastBurn
        expr: error_budget_burn_rate_1h > 14.4
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "🔥 Error Budget 급속 소진"
          description: "1시간 Burn Rate가 {{ $value }}입니다. 즉시 조치가 필요합니다."
```

### 6.3 Grafana Dashboard

Error Budget 대시보드는 다음 패널을 포함합니다:

1. **Budget 게이지**: 현재 잔여량 (%)
2. **Burn Rate 차트**: 1h/6h Burn Rate 추이
3. **동결 상태 표시**: 현재 배포 정책 상태
4. **결정 이력 테이블**: 최근 동결/해제 기록

---

## 7. OpenTelemetry 연동

### 7.1 이벤트 타입

| 이벤트 | 설명 |
|--------|------|
| `selfhealing.error_budget.low` | Budget < 50% |
| `selfhealing.error_budget.critical` | Budget < 20% |
| `selfhealing.error_budget.exhausted` | Budget ≤ 0% |
| `selfhealing.error_budget.recovered` | 정상 수준 회복 |
| `selfhealing.error_budget.burn_rate_fast` | Fast Burn 감지 |
| `selfhealing.deployment.freeze_recommended` | 동결 권고 |
| `selfhealing.deployment.freeze_acknowledged` | 동결 확정 |
| `selfhealing.deployment.override_approved` | Override 승인 |
| `selfhealing.deployment.freeze_lifted` | 동결 해제 |

### 7.2 이벤트 속성

```python
EventAttribute.ERROR_BUDGET_REMAINING_PERCENT  # 잔여량 %
EventAttribute.ERROR_BUDGET_REMAINING_MINUTES  # 잔여량 분
EventAttribute.BURN_RATE_1H                    # 1시간 Burn Rate
EventAttribute.BURN_RATE_6H                    # 6시간 Burn Rate
EventAttribute.FREEZE_STATUS                   # 동결 상태
EventAttribute.FREEZE_DECIDED_BY               # 결정자
EventAttribute.FREEZE_JUSTIFICATION            # 결정 사유
```

---

## 8. 운영 가이드

### 8.1 일일 점검

```bash
# Error Budget 상태 확인
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/error-budget/status/

# 배포 가능 여부 확인
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/deployment-policy/verdict/
```

### 8.2 동결 대응 플로우

```
Budget < 20% 감지
       │
       ▼
┌─────────────────────┐
│ Freeze 권고 확인    │
│ (Dashboard/Alert)   │
└─────────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌───────┐   ┌───────────┐
│ 확정  │   │  Override │
│       │   │  (긴급시) │
└───┬───┘   └─────┬─────┘
    │             │
    ▼             ▼
┌───────────┐ ┌─────────────────┐
│ 배포 중지 │ │ 사유 기록 후    │
│ 안정화    │ │ 배포 진행       │
└───────────┘ └─────────────────┘
    │             │
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 상황 해결   │
    │ Budget 회복 │
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ Freeze 해제 │
    │ (사유 기록) │
    └─────────────┘
```

### 8.3 CI/CD 연동 예시

```yaml
# GitHub Actions 예시
- name: Check Deployment Policy
  run: |
    VERDICT=$(curl -s -H "Authorization: Bearer $TOKEN" \
      $API_URL/api/self-healing/deployment-policy/verdict/)

    STATUS=$(echo $VERDICT | jq -r '.data.verdict.status')
    CAN_DEPLOY=$(echo $VERDICT | jq -r '.data.verdict.can_deploy')

    if [ "$CAN_DEPLOY" = "false" ]; then
      echo "⚠️ Deployment not recommended: $STATUS"
      echo "Message: $(echo $VERDICT | jq -r '.data.message')"

      # 권고일 뿐, 강제 차단하지 않음
      # exit 1  # 필요시 주석 해제
    fi
```

---

## 관련 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 런타임 설정 (Error Budget 임계값 동적 변경)
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드

---

## 9. Error Budget Gate (자동화 제어)

> **핵심 원칙**: "시스템이 '대신 하는 것'이 아니라 '멈추는 것'" - Error Budget이 위험 수준일 때 자동화를 차단하고 수동 모드를 강제합니다.

### 9.1 개요

**Error Budget Gate**는 Error Budget 잔량에 따라 모든 자동화 기능을 중앙에서 제어하는 게이트입니다.

Error Budget이 임계값(기본 10%) 미만으로 떨어지면:
- ❌ Chaos Engineering 자동 실행 차단
- ❌ DLQ Replay 자동 재시도 차단
- ⚠️ 운영자에게 수동 확인 요청

```
┌──────────────────────────────────────────────────────────────┐
│                    Error Budget Gate 개념도                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Error Budget 상태          Gate 상태                       │
│   ─────────────────          ─────────                       │
│   ███████████ 75%     →     🟢 OPEN (자동화 허용)            │
│   ██████░░░░░ 50%     →     🟢 OPEN (자동화 허용)            │
│   ███░░░░░░░░ 20%     →     🟡 WARNING (경고, 허용)          │
│   █░░░░░░░░░░ 10%     →     🔴 BLOCKED (자동화 차단!)        │
│   ░░░░░░░░░░░  0%     →     🔴 BLOCKED (수동 모드 강제)      │
│                                                              │
│   ⚠️ Gate 오류 시     →     🟢 FAIL_OPEN (안전하게 허용)     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 9.2 설정

```python
from selfhealing.services.error_budget_gate import ErrorBudgetGateConfig

# 기본 설정
config = ErrorBudgetGateConfig(
    enabled=True,                      # Gate 활성화
    critical_threshold_percent=10.0,   # 차단 임계값 (10% 미만 시 차단)
    warning_threshold_percent=20.0,    # 경고 임계값 (20% 미만 시 경고)
    fail_open=True,                    # Gate 오류 시 허용 (기본: True)
    cache_ttl_seconds=30,              # 캐시 TTL (API 호출 최소화)
)
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `enabled` | `True` | Gate 활성화 여부 |
| `critical_threshold_percent` | `10.0` | 이 값 미만 시 자동화 차단 |
| `warning_threshold_percent` | `20.0` | 이 값 미만 시 경고 발생 |
| `fail_open` | `True` | Gate 오류 시 허용 (False면 차단) |
| `cache_ttl_seconds` | `30` | Error Budget 캐시 시간 |

### 9.3 사용법

#### 간편 함수

```python
from selfhealing.services.error_budget_gate import (
    is_automation_allowed,
    check_automation_allowed,
    require_automation_allowed,
)

# 1. 단순 확인 (bool 반환)
if is_automation_allowed():
    run_automation()
else:
    log.info("Manual mode enforced")

# 2. 상세 결과 확인
result = check_automation_allowed()
if result.allowed:
    run_automation()
else:
    log.warning(f"Blocked: {result.reason}")
    log.info(f"Budget remaining: {result.budget_percent}%")

# 3. 예외 발생 방식
try:
    require_automation_allowed(action="chaos_execution")
    run_automation()
except AutomationBlockedError as e:
    log.warning(f"Automation blocked: {e.reason}")
```

#### 데코레이터

```python
from selfhealing.services.error_budget_gate import automation_gate

@automation_gate(action="dlq_replay")  # 함수 시작 전 자동 체크
def replay_dlq_entry(entry_id: str):
    # Error Budget 부족 시 AutomationBlockedError 발생
    ...
```

### 9.4 통합 포인트

#### Chaos Scheduler

```python
# packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py

class ChaosScheduler:
    async def execute_now(self, rule_id: str, ...) -> ExecutionResult:
        # Gate 체크 - 예산 부족 시 차단
        gate_result = check_automation_allowed()
        if not gate_result.allowed:
            return ExecutionResult(
                status="blocked",
                message=f"Error Budget Gate: {gate_result.reason}"
            )

        # 정상 실행
        return await self._execute_injection(...)
```

#### DLQ Replay

```python
# packages/selfhealing-python/src/selfhealing/adapters/celery/tasks.py

@app.task
def replay_single_dlq_entry(entry_id: str, ...):
    gate_result = check_automation_allowed()
    if not gate_result.allowed:
        return {
            "status": "blocked",
            "manual_mode_enforced": True,
            "reason": gate_result.reason,
        }

    # 정상 재시도 로직
    ...
```

### 9.5 Gate 상태 종류

| 상태 | 설명 | 자동화 |
|------|------|--------|
| `OPEN` | Error Budget 충분 | ✅ 허용 |
| `WARNING` | 예산 경고 수준 (20% 미만) | ✅ 허용 (경고 로그) |
| `BLOCKED` | 예산 위험 수준 (10% 미만) | ❌ 차단 |
| `FAIL_OPEN` | Gate 오류 발생 | ✅ 허용 (안전 모드) |
| `DISABLED` | Gate 비활성화 | ✅ 허용 |

### 9.6 Fail-Open 설계

**왜 Fail-Open인가?**

Error Budget Gate 자체에 오류가 발생했을 때:
- ❌ **Fail-Close**: 모든 자동화 차단 → 배포/운영 마비
- ✅ **Fail-Open**: 자동화 허용 → 기존 동작 유지

### 9.7 Fail-Open Rate Limiting (최소한의 제약이 있는 방임)

> **핵심 원칙**: "완전한 방임보다 '제한된 방임'이 폭주를 막는 최후의 보루"

Redis/DB가 장애 상태일 때 무조건 `allowed=True`를 주는 것이 아니라,
**메모리 기반 Rate Limit**을 적용하여 무한 루프나 폭주를 방지합니다.

```
┌──────────────────────────────────────────────────────────────┐
│               Fail-Open Rate Limiting 개념도                  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Error Budget 조회 실패 (Redis/DB 장애)                      │
│              │                                               │
│              ▼                                               │
│   ┌───────────────────────────────┐                          │
│   │ Rate Limit 체크 (메모리 기반) │ ◀── 외부 의존성 없음      │
│   │                               │                          │
│   │ 분당 10회 이내?               │                          │
│   └───────────────────────────────┘                          │
│          │ Yes              │ No                             │
│          ▼                  ▼                                │
│   🟢 FAIL_OPEN         🔴 FAIL_OPEN_RATE_LIMITED             │
│   (자동화 허용)        (자동화 차단)                          │
│   remaining: 9          remaining: 0                          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### 설정

```python
config = ErrorBudgetGateConfig(
    fail_open=True,
    fail_open_rate_limit_enabled=True,        # Rate Limit 활성화 (기본: True)
    fail_open_rate_limit_per_minute=10,       # 분당 최대 10회 (기본: 10)
    fail_open_rate_limit_window_seconds=60,   # 슬라이딩 윈도우 60초 (기본: 60)
)
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `fail_open_rate_limit_enabled` | `True` | Rate Limit 적용 여부 |
| `fail_open_rate_limit_per_minute` | `10` | 분당 최대 허용 횟수 |
| `fail_open_rate_limit_window_seconds` | `60` | 슬라이딩 윈도우 크기 (초) |

#### API로 동적 변경

```bash
# Rate Limit 설정 변경
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "fail_open_rate_limit_per_minute": 5,
    "fail_open_rate_limit_window_seconds": 30
  }' \
  $API_URL/api/self-healing/config/error-budget-gate/
```

#### Rate Limiter 상태 조회

```python
gate = get_error_budget_gate()
status = gate.get_rate_limiter_status()
# {
#     "enabled": True,
#     "current_count": 3,
#     "max_requests": 10,
#     "window_seconds": 60,
#     "remaining": 7
# }
```

#### 응답 예시 (Rate Limit 초과 시)

```json
{
    "allowed": false,
    "status": "fail_open_rate_limited",
    "reason": "Error budget retrieval failed and rate limit exceeded (10/min)",
    "recommendation": "에러 예산 조회 실패 상황에서 Rate Limit을 초과했습니다...",
    "fail_open_triggered": true,
    "rate_limit_remaining": 0,
    "rate_limit_reset_at": "2024-01-15T10:30:00+00:00"
}
```

### 9.8 메트릭

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `selfhealing_error_budget_gate_checks_total` | Counter | Gate 체크 횟수 |
| `selfhealing_error_budget_gate_blocked_total` | Counter | 차단된 자동화 횟수 |
| `selfhealing_error_budget_gate_status` | Gauge | 현재 Gate 상태 |
| `selfhealing_error_budget_gate_latency_seconds` | Histogram | Gate 체크 지연 시간 |
| `selfhealing_error_budget_gate_rate_limited_total` | Counter | Rate Limit 초과 횟수 |
| `selfhealing_error_budget_gate_circuit_breaker_state` | Gauge | Circuit Breaker 상태 (0=closed, 1=open, 2=half_open) |
| `selfhealing_error_budget_gate_alerts_total` | Counter | 발송된 알림 횟수 |

### 9.9 Circuit Breaker (빠른 실패)

> **핵심 원칙**: "반복적인 실패에 대해 빠르게 실패하여 시스템 부하를 줄임"

Error Budget Gate가 연속적으로 오류가 발생할 때, 매번 느린 타임아웃을 기다리는 대신 **즉시 Fail-Open**으로 응답합니다.

```
┌──────────────────────────────────────────────────────────────┐
│                 Circuit Breaker 상태 전이                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────┐  failure >= 3   ┌─────────┐                    │
│   │ CLOSED  │ ──────────────▶ │  OPEN   │                    │
│   │ (정상)  │                 │ (차단)  │                    │
│   └─────────┘                 └────┬────┘                    │
│        ▲                           │                         │
│        │ success                   │ recovery_timeout        │
│        │                           ▼                         │
│   ┌─────────┐                 ┌─────────┐                    │
│   │         │ ◀────────────── │HALF_OPEN│                    │
│   └─────────┘                 │ (시험)  │                    │
│                               └─────────┘                    │
│                                                              │
│   CLOSED:    정상 동작, Error Budget 조회                    │
│   OPEN:      즉시 Fail-Open 반환 (조회 안함)                 │
│   HALF_OPEN: 1회 시험 조회, 성공 시 CLOSED                   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### 설정

```python
config = ErrorBudgetGateConfig(
    circuit_breaker_enabled=True,           # Circuit Breaker 활성화
    circuit_breaker_failure_threshold=3,    # 연속 실패 N회 시 Open
    circuit_breaker_recovery_timeout=60,    # Open 상태 유지 시간 (초)
)
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `circuit_breaker_enabled` | `True` | Circuit Breaker 활성화 여부 |
| `circuit_breaker_failure_threshold` | `3` | Open 전환을 위한 연속 실패 횟수 |
| `circuit_breaker_recovery_timeout` | `60` | Open에서 Half-Open까지 대기 시간 (초) |

#### 상태 조회

```python
gate = get_error_budget_gate()
status = gate.get_circuit_breaker_status()
# {
#     "enabled": True,
#     "state": "closed",  # "closed", "open", "half_open"
#     "failure_count": 0,
#     "failure_threshold": 3,
#     "recovery_timeout_seconds": 60,
#     "last_failure_at": None,
#     "open_until": None
# }
```

#### 동작 예시

```
Request 1: Error Budget 조회 성공 → CLOSED 유지
Request 2: Error Budget 조회 실패 → failure_count=1, CLOSED
Request 3: Error Budget 조회 실패 → failure_count=2, CLOSED
Request 4: Error Budget 조회 실패 → failure_count=3, CLOSED → OPEN 전환

Request 5-100: 즉시 Fail-Open 반환 (조회 안함, 빠른 응답)

60초 후...
Request 101: HALF_OPEN, 1회 시험 조회
  - 성공 → CLOSED, failure_count=0
  - 실패 → OPEN 재진입, 60초 대기
```

### 9.10 Alert Manager (운영 알림)

> **핵심 원칙**: "Fail-Safe가 발동하면 반드시 알림을 발송하여 '침묵하는 장애'를 방지"

Fail-Open, Rate Limit, Circuit Breaker 상태 변화 시 자동으로 알림을 발송합니다.

```
┌──────────────────────────────────────────────────────────────┐
│                   Alert Manager 알림 종류                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   🔶 fail_open                                               │
│      "Error Budget 조회 실패, Fail-Open 모드 전환"           │
│      심각도: warning                                         │
│                                                              │
│   🔴 rate_limited                                            │
│      "Rate Limit 초과로 자동화 차단"                         │
│      심각도: critical                                        │
│                                                              │
│   ⚡ circuit_open                                            │
│      "연속 실패로 Circuit Breaker Open"                      │
│      심각도: warning                                         │
│                                                              │
│   ✅ circuit_recovered                                       │
│      "Circuit Breaker가 정상으로 복구됨"                     │
│      심각도: info                                            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### 설정

```python
config = ErrorBudgetGateConfig(
    alert_on_fail_open=True,          # Fail-Open 시 알림
    alert_cooldown_seconds=300,       # 같은 타입 알림 재발송 대기 (5분)
)
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `alert_on_fail_open` | `True` | Fail-Open/Rate Limit/Circuit Open 시 알림 발송 |
| `alert_cooldown_seconds` | `300` | 동일 타입 알림 재발송 쿨다운 (초) |

#### 알림 상태 조회

```python
gate = get_error_budget_gate()
status = gate.get_alert_manager_status()
# {
#     "enabled": True,
#     "cooldown_seconds": 300,
#     "alert_counts": {
#         "fail_open": 2,
#         "rate_limited": 0,
#         "circuit_open": 1,
#         "circuit_recovered": 1
#     },
#     "last_alerts": {
#         "fail_open": "2024-01-15T10:30:00+00:00",
#         "circuit_open": "2024-01-15T10:25:00+00:00"
#     }
# }
```

#### 알림 예시 (콘솔 로그)

```
[GateAlert] 🔶 Error Budget Gate: Fail-Open 발동: Error Budget 조회에 실패하여 Fail-Open 모드로 전환되었습니다.
• 사유: Error budget service unavailable
• Rate Limit 잔여: 9
• 조치: Error Budget 서비스 상태를 확인하세요.
```

### 9.11 Health Endpoint (상태 조회 API)

Gate의 전체 상태를 한 번에 조회할 수 있는 API를 제공합니다.

#### GET /api/self-healing/health/gate/

```bash
curl -H "Authorization: Bearer $TOKEN" $API_URL/api/self-healing/health/gate/
```

**응답 예시:**

```json
{
    "status": "healthy",
    "enabled": true,
    "gate_status": "open",
    "error_budget_percent": 75.0,
    "thresholds": {
        "critical_percent": 10.0,
        "warning_percent": 20.0
    },
    "rate_limiter": {
        "enabled": true,
        "current_count": 0,
        "max_requests": 10,
        "remaining": 10
    },
    "circuit_breaker": {
        "enabled": true,
        "state": "closed",
        "failure_count": 0,
        "failure_threshold": 3
    },
    "alerts": {
        "enabled": true,
        "cooldown_seconds": 300,
        "total_sent": 5
    },
    "checked_at": "2024-01-15T10:30:00+00:00"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | `healthy`, `degraded`, `unknown` |
| `gate_status` | string | Gate 현재 상태 |
| `error_budget_percent` | float | Error Budget 잔량 |
| `rate_limiter` | object | Rate Limiter 상태 |
| `circuit_breaker` | object | Circuit Breaker 상태 |
| `alerts` | object | Alert Manager 상태 |

#### GET /api/self-healing/config/gate/

Gate 설정 조회:

```json
{
    "enabled": true,
    "critical_threshold_percent": 10.0,
    "warning_threshold_percent": 20.0,
    "fail_open": true,
    "fail_open_rate_limit_enabled": true,
    "fail_open_rate_limit_per_minute": 10,
    "circuit_breaker_enabled": true,
    "circuit_breaker_failure_threshold": 3,
    "circuit_breaker_recovery_timeout": 60,
    "alert_on_fail_open": true,
    "alert_cooldown_seconds": 300
}
```

#### PUT /api/self-healing/config/gate/

Gate 설정 동적 변경:

```bash
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "circuit_breaker_failure_threshold": 5,
    "alert_cooldown_seconds": 600
  }' \
  $API_URL/api/self-healing/config/gate/
```

#### POST /api/self-healing/gate/reset/

긴급 상황에서 컴포넌트 리셋:

```bash
# 전체 리셋
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"components": ["all"]}' \
  $API_URL/api/self-healing/gate/reset/

# 특정 컴포넌트만 리셋
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"components": ["circuit_breaker", "rate_limiter"]}' \
  $API_URL/api/self-healing/gate/reset/
```

### 9.12 Grafana Dashboard

Error Budget Gate 전용 Grafana 대시보드를 제공합니다.

**위치**: `docker/grafana/dashboards/error_budget_gate.json`

```
┌──────────────────────────────────────────────────────────────┐
│              Error Budget Gate Dashboard                      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│   │ Gate Status │ │ Budget %    │ │ Rate Limit  │            │
│   │    OPEN     │ │    75%      │ │   8/10      │            │
│   └─────────────┘ └─────────────┘ └─────────────┘            │
│                                                              │
│   ┌─────────────┐ ┌─────────────────────────────────────┐    │
│   │ Circuit     │ │         Gate Activity               │    │
│   │ Breaker     │ │   ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄       │    │
│   │  CLOSED     │ │   ████████████████████████████       │    │
│   └─────────────┘ └─────────────────────────────────────┘    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**패널 구성:**

| 패널 | 쿼리 | 설명 |
|------|------|------|
| Gate Status | `selfhealing_error_budget_gate_status` | 현재 Gate 상태 |
| Error Budget | `selfhealing_error_budget_remaining_percent` | Budget 잔량 게이지 |
| Rate Limit Remaining | `10 - rate(...)` | Rate Limit 잔여 횟수 |
| Circuit Breaker State | `selfhealing_...circuit_breaker_state` | CB 상태 표시 |
| Gate Checks | `rate(selfhealing_...checks_total[5m])` | 분당 체크 횟수 |
| Gate Blocked | `rate(selfhealing_...blocked_total[5m])` | 분당 차단 횟수 |

**대시보드 설정:**

```bash
# Grafana에 대시보드 임포트
docker cp docker/grafana/dashboards/error_budget_gate.json grafana:/etc/grafana/provisioning/dashboards/
```

---

## 10. 동적 설정 (Runtime Configuration)

### 10.1 API를 통한 임계값 변경

Error Budget 및 Burn Rate 임계값은 **서버 재시작 없이 API로 동적 변경**이 가능합니다.

#### GET /api/self-healing/config/error-budget/

현재 Error Budget 설정 조회:

```json
{
  "status": "success",
  "config": {
    "threshold_healthy": 75.0,
    "threshold_caution": 50.0,
    "threshold_warning": 20.0,
    "threshold_critical": 0.0,
    "burn_rate_fast_critical": 14.4,
    "burn_rate_fast_warning": 6.0,
    "burn_rate_slow_warning": 3.0,
    "burn_rate_slow_info": 1.0,
    "failsafe_alert_enabled": true,
    "failsafe_cooldown_seconds": 300
  }
}
```

#### PUT /api/self-healing/config/error-budget/

임계값 동적 변경:

```bash
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "threshold_warning": 25.0,
    "burn_rate_fast_critical": 12.0
  }' \
  $API_URL/api/self-healing/config/error-budget/
```

### 10.2 설정 항목

| 설정 | 기본값 | 범위 | 설명 |
|------|--------|------|------|
| `threshold_healthy` | 75.0 | 50-100% | 정상 상태 임계값 |
| `threshold_caution` | 50.0 | 20-80% | 주의 상태 임계값 |
| `threshold_warning` | 20.0 | 5-50% | 경고 상태 임계값 |
| `threshold_critical` | 0.0 | 0-20% | 위험 상태 임계값 |
| `burn_rate_fast_critical` | 14.4 | 10-50x | 빠른 소진 위험 임계값 |
| `burn_rate_fast_warning` | 6.0 | 3-15x | 빠른 소진 경고 임계값 |
| `burn_rate_slow_warning` | 3.0 | 1-10x | 느린 소진 경고 임계값 |
| `burn_rate_slow_info` | 1.0 | 0.5-3x | 정상 소진율 임계값 |
| `failsafe_alert_enabled` | true | bool | Fail-Safe 발동 시 알림 발송 |
| `failsafe_cooldown_seconds` | 300 | 60-3600 | 연속 알림 방지 쿨다운 (초) |

---

## 11. Fail-Safe Self-Reporting (침묵하는 장애 방지)

### 11.1 문제점

Fail-Safe가 발동되면 시스템은 안전하게 동작하지만, 운영팀이 이를 인지하지 못할 수 있습니다.

```
❌ 나쁜 예: 침묵하는 장애
┌──────────────────────────────────────────────────────────┐
│  Error Budget DB 연결 실패                               │
│              │                                           │
│              ▼                                           │
│  Fail-Safe 응답 반환 (PROCEED)                          │
│              │                                           │
│              ▼                                           │
│  배포 계속 진행... (아무도 모름)                         │
│              │                                           │
│              ▼                                           │
│  문제 축적... 나중에 큰 장애로 발전                      │
└──────────────────────────────────────────────────────────┘
```

### 11.2 Self-Reporting 패턴

Fail-Safe 발동 즉시 **3가지 채널로 알림**을 발송합니다:

```
✅ 좋은 예: Self-Reporting Fail-Safe
┌──────────────────────────────────────────────────────────┐
│  Error Budget DB 연결 실패                               │
│              │                                           │
│              ▼                                           │
│  ┌─────────────────────────────────────────────────────┐│
│  │         Fail-Safe 발동 + Self-Reporting             ││
│  │                                                      ││
│  │  1. 로그: CRITICAL 레벨 기록                        ││
│  │  2. 알림: Slack/PagerDuty 즉시 발송                 ││
│  │  3. 메트릭: Prometheus Counter/Gauge 증가           ││
│  └─────────────────────────────────────────────────────┘│
│              │                                           │
│              ▼                                           │
│  배포 계속 진행 (PROCEED) + 운영팀 즉시 인지             │
└──────────────────────────────────────────────────────────┘
```

### 11.3 Prometheus 알림 규칙

```yaml
# Fail-Safe 발동 알림 (즉시)
- alert: FailSafeTriggered
  expr: increase(selfhealing_failsafe_triggered_total[5m]) > 0
  for: 0m
  labels:
    severity: critical
    team: ops
  annotations:
    summary: "🚨 Self-Healing Fail-Safe mode activated"
    description: "Component {{ $labels.component }} has failed. Deployments proceeding but system needs immediate attention."

# Fail-Safe 모드 지속 알림 (2분 이상)
- alert: FailSafeModeActive
  expr: selfhealing_failsafe_mode_active == 1
  for: 2m
  labels:
    severity: critical
    team: ops
  annotations:
    summary: "🚨 Self-Healing in degraded mode"
    description: "Error Budget recommendations unavailable. Investigate immediately."
```

### 10.4 업계 표준 참고

| 회사 | 패턴 | 동작 |
|------|------|------|
| **Google SRE** | "Silent failures are the worst failures" | 모든 Fallback에 메트릭 |
| **Netflix** | Fallback activation = immediate alert | Circuit Breaker Fallback 시 알림 |
| **Uber** | Self-healing with visibility | 자동 복구도 P2 인시던트 생성 |

---

## 12. 고급 관측성 기능

### 12.1 Heartbeat (Dead Man's Snitch)

Error Budget 시스템이 정상 동작 중인지 확인하기 위해 주기적인 heartbeat를 발송합니다.

```
┌──────────────────────────────────────────────────────────┐
│                Dead Man's Snitch 패턴                    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   Error Budget 서비스                                    │
│          │                                               │
│          ▼  (매 60초)                                    │
│   ┌─────────────────────────────────────────────────────┐│
│   │         emit_heartbeat()                            ││
│   │                                                      ││
│   │  selfhealing_heartbeat_timestamp = time()           ││
│   │  selfhealing_heartbeat_count++                      ││
│   └─────────────────────────────────────────────────────┘│
│          │                                               │
│          ▼                                               │
│   Prometheus 모니터링                                    │
│          │                                               │
│          ▼                                               │
│   time() - heartbeat_timestamp > 120초?                 │
│          │                                               │
│    YES   ▼                                               │
│   🚨 SelfHealingServiceDead 알림 발송                    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**Prometheus 알림 규칙:**

```yaml
# 서비스 사망 감지
- alert: SelfHealingServiceDead
  expr: time() - selfhealing_heartbeat_timestamp_seconds > 120
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "🔴 Self-Healing service is DEAD"
    description: "No heartbeat for 2+ minutes. Service may have crashed."

# Heartbeat 메트릭 부재
- alert: SelfHealingHeartbeatMissing
  expr: absent(selfhealing_heartbeat_timestamp_seconds) == 1
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "🔴 Self-Healing heartbeat metric missing"
```

**API 설정:**

```bash
# Heartbeat 주기 변경 (30초)
curl -X PATCH /api/v1/selfhealing/config/error-budget/ \
  -H "Content-Type: application/json" \
  -d '{
    "heartbeat_enabled": true,
    "heartbeat_interval_seconds": 30,
    "heartbeat_timeout_seconds": 90
  }'
```

### 12.2 복구 완료 알림 (Recovery Notification)

Fail-Safe 모드에서 정상으로 복구되었을 때 적극적으로 알림을 발송합니다.

```
❌ 나쁜 예: 침묵하는 복구
┌───────────────────────────────────────────────────────────┐
│  Fail-Safe 모드 → 자동 복구 → (알림 없음)                 │
│                                                           │
│  운영자: "언제 복구됐지? 아직 장애 중인가?"              │
└───────────────────────────────────────────────────────────┘

✅ 좋은 예: Recovery Notification
┌───────────────────────────────────────────────────────────┐
│  Fail-Safe 모드 → 자동 복구                               │
│              │                                            │
│              ▼                                            │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  alert_failsafe_recovered()                          │ │
│  │                                                       │ │
│  │  Title: ✅ RECOVERED: error_budget                   │ │
│  │  Downtime: 5분 32초                                  │ │
│  │  Severity: INFO                                       │ │
│  └─────────────────────────────────────────────────────┘ │
│              │                                            │
│              ▼                                            │
│  운영자: "5분만에 자동 복구됐구나, OK"                   │
└───────────────────────────────────────────────────────────┘
```

**API 설정:**

```bash
curl -X PATCH /api/v1/selfhealing/config/error-budget/ \
  -d '{
    "recovery_alert_enabled": true,
    "recovery_alert_include_downtime": true
  }'
```

### 12.3 Override 에스컬레이션

Error Budget이 부족한 상태에서 배포 Override를 승인하면 상위 채널에 알림을 발송합니다.

```
┌──────────────────────────────────────────────────────────┐
│              Override 에스컬레이션 흐름                   │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   Error Budget: 5% (위험)                                │
│          │                                               │
│          ▼                                               │
│   개발자: "긴급 보안 패치 배포 필요"                     │
│          │                                               │
│          ▼                                               │
│   record_override_approved(type=SECURITY_PATCH)          │
│          │                                               │
│          ▼                                               │
│   ┌─────────────────────────────────────────────────────┐│
│   │  에스컬레이션 알림 발송                              ││
│   │                                                      ││
│   │  채널: #governance                                   ││
│   │  멘션: @cto @security                                ││
│   │  내용: Override 승인 - 감사 로그 기록됨             ││
│   └─────────────────────────────────────────────────────┘│
│          │                                               │
│          ▼                                               │
│   배포 진행 + 거버넌스 추적                              │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**API 설정:**

```bash
curl -X PATCH /api/v1/selfhealing/config/error-budget/ \
  -d '{
    "escalation_enabled": true,
    "escalation_channel": "#governance",
    "escalation_mention": "@cto @security"
  }'
```

**Prometheus 알림 규칙:**

```yaml
# Override 발생 알림
- alert: OverrideEscalation
  expr: increase(selfhealing_override_escalation_total[1h]) > 0
  for: 0m
  labels:
    severity: warning
  annotations:
    summary: "⚠️ Deployment override escalation"
    description: "Override type '{{ $labels.override_type }}' approved despite low budget"

# 과도한 Override 알림
- alert: OverrideEscalationHigh
  expr: increase(selfhealing_override_escalation_total[24h]) > 5
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "🚨 Excessive deployment overrides"
    description: "More than 5 overrides in 24h indicates process issues"
```

### 12.4 Celery Beat 스케줄 설정

Heartbeat Task를 Celery Beat에 등록합니다:

```python
# myproject/celery.py 또는 settings.py

CELERY_BEAT_SCHEDULE = {
    # ... 기존 스케줄 ...

    # Heartbeat (Dead Man's Snitch)
    'emit-selfhealing-heartbeat': {
        'task': 'selfhealing.adapters.celery.tasks.emit_selfhealing_heartbeat',
        'schedule': 60.0,  # heartbeat_interval_seconds와 동일하게
    },
}
```

---

## 13. Reconciliation (Shadow Budget)

### 13.1 개요

Fail-Safe(Fail-Open) 설계로 인해 Error Budget Gate가 일시적으로 비활성화될 때, 그 기간 동안 발생한 에러가 Budget에 반영되지 않을 수 있습니다. **Reconciliation**은 이러한 "놓친 에러"를 추후에 조정할 수 있게 해주는 시스템입니다.

> **핵심 원칙: "시스템은 계산하고, 반영은 사람이 결정한다."**

```
┌──────────────────────────────────────────────────────────────────────┐
│                   Shadow Budget Reconciliation 개념                    │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  Fail-Safe 구간                                              │   │
│   │  ════════════════════════                                    │   │
│   │  시작: 10:00:00        종료: 10:15:00                        │   │
│   │  지속시간: 15분                                              │   │
│   │  원인: Redis connection timeout                              │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                            │                                         │
│                            ▼                                         │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  Shadow Budget 계산                                          │   │
│   │  ────────────────────                                        │   │
│   │  • Prometheus에서 해당 구간 에러 조회                        │   │
│   │  • 추정 에러: 45건 (3.0 errors/min × 15min)                  │   │
│   │  • 데이터 소스: prometheus                                   │   │
│   │  • 신뢰도: 95%                                               │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                            │                                         │
│                            ▼                                         │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  운영자 검토 대기                                            │   │
│   │  ────────────────────                                        │   │
│   │  상태: PENDING_REVIEW                                        │   │
│   │  옵션: [✓ Approve] [✗ Reject]                                │   │
│   │                                                              │   │
│   │  ⚠️ 자동 반영 없음 - 사람의 결정 필요                       │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                            │                                         │
│            ┌───────────────┴───────────────┐                        │
│            ▼                               ▼                        │
│   ┌─────────────────────┐       ┌─────────────────────┐             │
│   │ Approve             │       │ Reject              │             │
│   │ ─────────           │       │ ────────            │             │
│   │ Budget 조정         │       │ Shadow 폐기         │             │
│   │ (최대 10%/cycle)    │       │ 사유 기록           │             │
│   │ Audit 기록          │       │ 분석에서 제외       │             │
│   └─────────────────────┘       └─────────────────────┘             │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 13.2 왜 자동 반영하지 않는가?

| 접근 방식 | 자동 Reconciliation | **Shadow Budget (권장)** |
|-----------|---------------------|--------------------------|
| 동작 | 시스템이 자동 조정 | 시스템이 계산, 사람이 결정 |
| 위험 | Budget Shock, 급격한 감소 | 점진적 조정, 예측 가능 |
| 투명성 | 블랙박스 | 완전한 감사 추적 |
| 취소 | 어려움 | 승인 전 검토 가능 |
| 적합성 | 단순 시스템 | 거버넌스 중요 환경 |

**Budget Shock 방지:**
- `ApplyMode.CAPPED`: 1회 조정 시 최대 10% 제한
- 대규모 Shadow Budget은 여러 사이클에 걸쳐 분산 적용

### 13.3 핵심 컴포넌트

#### 13.3.1 FailSafePeriodTracker

Fail-Safe 구간을 추적하고 기록합니다:

```python
from shopping.selfhealing.reconciliation import get_period_tracker

tracker = get_period_tracker()

# Fail-Safe 시작 기록
tracker.start_failsafe(
    reason="Redis connection timeout",
    metadata={"component": "rate_limiter"}
)

# Fail-Safe 종료 기록
tracker.end_failsafe()

# 미계산 구간 조회
unprocessed = tracker.get_unprocessed_periods()
```

#### 13.3.2 ShadowBudgetCalculator

놓친 에러를 추정합니다:

```python
from shopping.selfhealing.reconciliation import ShadowBudgetCalculator

calculator = ShadowBudgetCalculator(historical_error_rate=2.5)

# Shadow Budget 계산
shadow = calculator.calculate_shadow_budget(failsafe_period)
print(f"추정 에러: {shadow.estimated_errors}")
print(f"데이터 소스: {shadow.data_source}")
print(f"신뢰도: {shadow.confidence_score}%")
```

#### 13.3.3 ReconciliationConfig

조정 정책을 설정합니다:

```python
from shopping.selfhealing.reconciliation import ReconciliationConfig, ApplyMode

config = ReconciliationConfig(
    enabled=True,
    apply_mode=ApplyMode.CAPPED,           # 최대 10% 제한
    max_adjustment_percent=10.0,            # 1회 최대 조정률
    require_approval=True,                  # 운영자 승인 필요
    auto_exclude_short_periods=True,        # 60초 미만 자동 제외
    min_period_seconds=60                   # 최소 추적 기간
)
```

#### 13.3.4 ErrorBudgetReconciliationService

전체 Reconciliation 워크플로우를 관리합니다:

```python
from shopping.selfhealing.reconciliation import get_reconciliation_service

service = get_reconciliation_service()

# Shadow Budget 계산 및 저장
shadow = service.calculate_and_store_shadow_budget(period_id)

# 운영자 승인
service.approve_shadow_budget(
    calculation_id=shadow.calculation_id,
    approved_by="admin@example.com",
    apply_percent=100.0  # 전체 반영
)

# 또는 거부
service.reject_shadow_budget(
    calculation_id=shadow.calculation_id,
    rejected_by="admin@example.com",
    reason="테스트 환경 에러, 제외 대상"
)
```

### 13.4 API 레퍼런스

#### 13.4.1 상태 조회

```bash
GET /api/self-healing/reconciliation/status/
```

**응답:**
```json
{
    "enabled": true,
    "pending_periods": 3,
    "pending_shadow_budgets": 2,
    "last_processed_at": "2025-01-15T10:30:00Z",
    "config": {
        "apply_mode": "CAPPED",
        "max_adjustment_percent": 10.0,
        "require_approval": true,
        "auto_exclude_short_periods": true
    }
}
```

#### 13.4.2 Fail-Safe 구간 목록

```bash
GET /api/self-healing/reconciliation/failsafe-periods/
```

**응답:**
```json
{
    "periods": [
        {
            "period_id": "period_abc123",
            "start_time": "2025-01-15T10:00:00Z",
            "end_time": "2025-01-15T10:15:00Z",
            "duration_seconds": 900,
            "reason": "Redis connection timeout",
            "processed": false,
            "excluded": false
        }
    ]
}
```

#### 13.4.3 Shadow Budget 계산

```bash
POST /api/self-healing/reconciliation/shadow-budgets/
Content-Type: application/json

{
    "period_id": "period_abc123"
}
```

**응답:**
```json
{
    "calculation_id": "shadow_xyz789",
    "period_id": "period_abc123",
    "estimated_errors": 45,
    "data_source": "prometheus",
    "confidence_score": 95,
    "status": "PENDING_REVIEW",
    "created_at": "2025-01-15T11:00:00Z"
}
```

#### 13.4.4 Shadow Budget 승인

```bash
POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/approve/
Content-Type: application/json

{
    "approved_by": "admin@example.com",
    "apply_percent": 100.0
}
```

**응답:**
```json
{
    "calculation_id": "shadow_xyz789",
    "status": "APPROVED",
    "applied_errors": 45,
    "applied_at": "2025-01-15T11:05:00Z",
    "approved_by": "admin@example.com"
}
```

#### 13.4.5 Shadow Budget 거부

```bash
POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/reject/
Content-Type: application/json

{
    "rejected_by": "admin@example.com",
    "reason": "테스트 환경 에러, 프로덕션 버짓에서 제외"
}
```

#### 13.4.6 Excluded Period 관리

**제외 추가:**
```bash
POST /api/self-healing/reconciliation/excluded-periods/
Content-Type: application/json

{
    "period_id": "period_abc123",
    "reason": "계획된 점검 시간",
    "excluded_by": "admin@example.com"
}
```

**제외 취소:**
```bash
DELETE /api/self-healing/reconciliation/excluded-periods/{exclusion_id}/
```

#### 13.4.7 설정 조회/변경

```bash
# 조회
GET /api/self-healing/reconciliation/config/

# 변경
PUT /api/self-healing/reconciliation/config/
Content-Type: application/json

{
    "enabled": true,
    "apply_mode": "CAPPED",
    "max_adjustment_percent": 15.0,
    "require_approval": true,
    "auto_exclude_short_periods": true,
    "min_period_seconds": 60
}
```

### 13.5 운영 워크플로우

```
┌──────────────────────────────────────────────────────────────────────┐
│                    일일 Reconciliation 워크플로우                      │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [1] 대시보드 확인 ───────────────────────────────────────────────── │
│      │                                                               │
│      ├─ Pending Periods: 3                                           │
│      ├─ Pending Shadow Budgets: 2                                    │
│      └─ Last Processed: 2시간 전                                     │
│                                                                      │
│  [2] Fail-Safe 구간 검토 ─────────────────────────────────────────── │
│      │                                                               │
│      ├─ 구간 1: Redis timeout (15분) → Shadow 계산 요청             │
│      ├─ 구간 2: 점검 시간 (30분) → Exclude 처리                      │
│      └─ 구간 3: 네트워크 장애 (5분) → 자동 제외됨 (<60초 아님)      │
│                                                                      │
│  [3] Shadow Budget 검토 ──────────────────────────────────────────── │
│      │                                                               │
│      ├─ Shadow 1: 45 errors, 95% 신뢰도 → Approve                   │
│      └─ Shadow 2: 120 errors, 70% 신뢰도 → 추가 검토 필요           │
│                                                                      │
│  [4] 승인 결과 ───────────────────────────────────────────────────── │
│      │                                                               │
│      └─ Budget 조정: -4.5% (45 errors → 10% cap 적용)               │
│         나머지: 다음 사이클에서 처리                                 │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 13.6 메트릭 및 알림

**Prometheus 메트릭:**

```python
# 카운터
selfhealing_reconciliation_periods_total       # 추적된 Fail-Safe 구간 수
selfhealing_reconciliation_shadows_calculated  # 계산된 Shadow Budget 수
selfhealing_reconciliation_approved_total      # 승인된 조정 수
selfhealing_reconciliation_rejected_total      # 거부된 조정 수

# 게이지
selfhealing_reconciliation_pending_periods     # 대기 중인 구간 수
selfhealing_reconciliation_pending_shadows     # 대기 중인 Shadow 수
selfhealing_reconciliation_total_shadow_errors # 미반영 에러 총합
```

**알림 규칙:**

```yaml
# 미처리 구간 누적 알림
- alert: ReconciliationBacklog
  expr: selfhealing_reconciliation_pending_periods > 10
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "Reconciliation 백로그 누적"
    description: "{{ $value }}개의 Fail-Safe 구간이 미처리 상태입니다."

# 대규모 Shadow Budget 알림
- alert: LargeShadowBudget
  expr: selfhealing_reconciliation_total_shadow_errors > 1000
  for: 0m
  labels:
    severity: critical
  annotations:
    summary: "대규모 Shadow Budget 감지"
    description: "{{ $value }}개의 미반영 에러가 대기 중입니다. 검토가 필요합니다."
```

### 13.7 설정 권장사항

| 환경 | `apply_mode` | `max_adjustment_percent` | `require_approval` |
|------|--------------|--------------------------|-------------------|
| 개발 | `FULL` | 100% | `false` |
| 스테이징 | `CAPPED` | 20% | `true` |
| 프로덕션 | `CAPPED` | 10% | `true` |

### 13.8 문제 해결

**Q: Shadow Budget 계산 결과가 0인 경우?**
- Prometheus에 해당 시간대 데이터가 없을 수 있음
- `data_source`가 `fallback`인 경우 historical rate 확인
- 해당 구간이 실제로 에러가 없었던 경우

**Q: 너무 많은 Pending 구간이 쌓이는 경우?**
- `auto_exclude_short_periods=True`로 짧은 구간 자동 제외
- 정기 점검 시간은 미리 Exclude 등록
- 자동화 스크립트로 주기적 처리

**Q: CAPPED 모드에서 조정이 분산되는 이유?**
- Budget Shock 방지를 위한 설계
- 대규모 조정은 여러 사이클에 걸쳐 적용됨
- 예: 50% 조정 필요 시 → 5회 × 10%로 분산
