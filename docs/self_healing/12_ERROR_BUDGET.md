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
- [11_FORENSIC_ADVISOR.md](11_FORENSIC_ADVISOR.md) - Forensic Advisor

---

## 9. 동적 설정 (Runtime Configuration)

### 9.1 API를 통한 임계값 변경

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

### 9.2 설정 항목

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

## 10. Fail-Safe Self-Reporting (침묵하는 장애 방지)

### 10.1 문제점

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

### 10.2 Self-Reporting 패턴

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

### 10.3 Prometheus 알림 규칙

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

## 11. 고급 관측성 기능

### 11.1 Heartbeat (Dead Man's Snitch)

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

### 11.2 복구 완료 알림 (Recovery Notification)

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

### 11.3 Override 에스컬레이션

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

### 11.4 Celery Beat 스케줄 설정

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
