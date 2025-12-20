# Chaos Engineering (자율 카오스 엔진)

> 이 문서는 시스템의 Continuous Resilience Validation Engine을 설명합니다.

## 📋 목차

1. [개요](#1-개요)
2. [아키텍처](#2-아키텍처)
3. [핵심 컴포넌트](#3-핵심-컴포넌트)
4. [API 레퍼런스](#4-api-레퍼런스)
5. [Celery Beat 스케줄](#5-celery-beat-스케줄)
6. [운영 가이드](#6-운영-가이드)
7. [설정 레퍼런스](#7-설정-레퍼런스)

---

## 1. 개요

### 1.1 목적

자율 카오스 엔진은 45단계 검증을 통과한 Self-Healing 시스템을 **상시 검증**하는 **Continuous Resilience Validation** 체계입니다.

### 1.2 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Error Budget 연동** | 에러 버짓이 20% 미만이면 실험 자동 차단 |
| **Blast Radius Control** | INSTANCE → SERVICE → REGION 단계적 범위 제어 |
| **Kill Switch 필수** | 모든 실험은 즉시 중단 가능 |
| **Audit Trail 완전성** | 모든 실험 이력 영구 기록 |
| **Synthetic 우선** | 실제 트래픽보다 Synthetic 트래픽 우선 적용 |

### 1.3 업계 참조

| 기업 | 도구 | 채택 패턴 |
|------|------|----------|
| Netflix | Chaos Monkey, ChAP | 프로덕션 상시 실행 |
| Google | DiRT | 주간 소규모 + 연간 대규모 |
| Amazon | GameDay, FIS | 에러 버짓 기반 |
| Gremlin | Gremlin Platform | Pre-flight checks 내장 |

---

## 2. 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Chaos Engineering Architecture                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐   │
│  │   Operator   │───►│  Control API │───►│  ChaosSchedulerService   │   │
│  │   (Human)    │    │   (REST)     │    │  - Schedule CRUD         │   │
│  └──────────────┘    └──────────────┘    │  - Kill Switch           │   │
│                                           │  - Approval Workflow     │   │
│                                           └──────────┬───────────────┘   │
│                                                      │                   │
│  ┌───────────────────────────────────────────────────┼───────────────┐   │
│  │                     Pre-flight Checks             ▼               │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐    │   │
│  │  │ SafetyGuard  │    │ BlastRadius  │    │  Error Budget    │    │   │
│  │  │ - Budget ≥20%│    │  Manager     │    │    Service       │    │   │
│  │  │ - No Incident│    │ - Scope Ctrl │    │                  │    │   │
│  │  │ - Kill Switch│    │ - Approval   │    │                  │    │   │
│  │  └──────────────┘    └──────────────┘    └──────────────────┘    │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                      │                   │
│                                                      ▼                   │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                    Experiment Execution                            │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐     │   │
│  │  │  Latency   │ │   5xx      │ │  Packet    │ │  Timeout   │     │   │
│  │  │ Injection  │ │  Errors    │ │   Loss     │ │            │     │   │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘     │   │
│  │  ┌────────────┐                                                   │   │
│  │  │ Resource   │                                                   │   │
│  │  │Exhaustion  │                                                   │   │
│  │  └────────────┘                                                   │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                      │                   │
│                                                      ▼                   │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │                    Post-Experiment Analysis                        │   │
│  │  ┌────────────────┐    ┌────────────────┐    ┌────────────────┐   │   │
│  │  │ ForensicAdvisor│    │ ResilienceReport│   │  Audit Trail   │   │   │
│  │  │   Analysis     │    │   Generator     │   │   Recording    │   │   │
│  │  └────────────────┘    └────────────────┘    └────────────────┘   │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 핵심 컴포넌트

### 3.1 SafetyGuard (안전 가드)

실험 실행 전 **Pre-flight Safety Check**를 수행합니다.

```python
from selfhealing.services.chaos import get_safety_guard

guard = get_safety_guard()
result = guard.check(
    experiment_type="latency_injection",
    blast_radius="service",
)

if result.allowed:
    # 실험 진행
else:
    print(f"Blocked: {result.block_reason}")
```

**검사 항목:**

| 검사 | 기본값 | 설명 |
|------|--------|------|
| Error Budget | ≥ 20% | 에러 버짓 잔여량 확인 |
| Kill Switch | OFF | 글로벌 킬 스위치 상태 |
| Active Incidents | 0 | 활성 장애 건수 |
| Deployment Freeze | OFF | 배포 동결 상태 |
| System Health | Healthy | 시스템 상태 |
| Cooldown | 30분 | 이전 실험 후 쿨다운 |

### 3.2 BlastRadiusManager (폭발 반경 관리자)

실험 영향 범위를 제어합니다.

```python
from selfhealing.services.chaos import get_blast_radius_manager, BlastRadius

manager = get_blast_radius_manager()
result = manager.check_blast_radius(
    blast_radius=BlastRadius.REGION.value,
    target_service="payment",
)

if result.requires_approval:
    print("운영자 승인 필요")
```

**범위 레벨:**

| 레벨 | 설명 | 승인 필요 | 최대 동시 실행 |
|------|------|----------|---------------|
| `INSTANCE` | 단일 인스턴스/Pod | ❌ 자동 | 5개 |
| `SERVICE` | 전체 서비스 | ⚙️ 설정 가능 | 2개 |
| `REGION` | 전체 리전/AZ | ✅ 필수 | 1개 |

### 3.3 ChaosSchedulerService (스케줄러)

실험 예약 및 실행을 관리합니다.

```python
from selfhealing.services.chaos import get_chaos_scheduler

scheduler = get_chaos_scheduler()

# 스케줄 생성
schedule = scheduler.create_schedule(
    experiment_type="latency_injection",
    target_service="payment",
    schedule_type="daily",
    schedule_time="03:00",  # UTC
    blast_radius="instance",
    experiment_config={"latency_ms": 500, "affected_percent": 5.0},
)

# Kill Switch 활성화
scheduler.activate_kill_switch(
    reason="긴급 중단",
    activated_by="ops@company.com"
)
```

### 3.4 Experiment Library (실험 라이브러리)

5종의 핵심 카오스 실험을 제공합니다.

| 실험 | 클래스 | 설명 |
|------|--------|------|
| **지연 주입** | `LatencyInjectionExperiment` | 응답 지연 시뮬레이션 |
| **5xx 에러** | `Error5xxExperiment` | HTTP 5xx 에러 주입 |
| **패킷 유실** | `PacketLossExperiment` | 네트워크 패킷 손실 |
| **타임아웃** | `TimeoutExperiment` | 요청 타임아웃 유발 |
| **리소스 고갈** | `ResourceExhaustionExperiment` | CPU/메모리 부하 |

```python
from selfhealing.services.chaos.experiments import (
    LatencyInjectionExperiment,
    Error5xxExperiment,
    PacketLossExperiment,
    TimeoutExperiment,
    ResourceExhaustionExperiment,
)

# 지연 주입 실험
latency_exp = LatencyInjectionExperiment(
    target_service="payment",
    latency_ms=500,
    latency_variance_ms=100,
    affected_percent=10.0,
)

# 5xx 에러 주입
error_exp = Error5xxExperiment(
    target_service="order",
    error_codes=[500, 502, 503],
    affected_percent=5.0,
)
```

### 3.5 ResilienceReportGenerator (리포트 생성기)

Daily Resilience Report를 생성합니다.

```python
from selfhealing.services.chaos import get_report_generator

generator = get_report_generator()
report = generator.generate_daily_report()

print(f"Grade: {report.grade}")  # A, B, C, D, F
print(f"Total Experiments: {report.total_experiments}")
print(f"SLA Compliance: {report.sla_compliance_percent}%")
```

**등급 기준:**

| 등급 | 조건 | 설명 |
|------|------|------|
| A | 100% 통과 | 모든 실험 성공, SLA 위반 없음 |
| B | ≥ 90% 통과 | 경미한 이슈, SLA 내 복구 |
| C | ≥ 70% 통과 | 일부 이슈, 복구 지연 |
| D | ≥ 50% 통과 | 다수 실패, 느린 복구 |
| F | < 50% 통과 | 주요 실패, SLA 위반 |

---

## 4. API 레퍼런스

### 4.1 Configuration Endpoints

#### SafetyGuard 설정

```http
GET /api/self-healing/chaos/config/safety-guard/
PATCH /api/self-healing/chaos/config/safety-guard/
```

```json
{
  "error_budget_min_percent": 20.0,
  "error_budget_warning_percent": 50.0,
  "experiment_cooldown_minutes": 30,
  "require_healthy_system": true,
  "require_no_active_incidents": true,
  "fail_safe_on_error": true
}
```

#### BlastRadius 정책

```http
GET /api/self-healing/chaos/config/blast-radius/
PATCH /api/self-healing/chaos/config/blast-radius/
```

```json
{
  "instance_max_concurrent": 5,
  "service_max_concurrent": 2,
  "region_max_concurrent": 1,
  "instance_auto_approve": true,
  "service_auto_approve": false,
  "allowed_hours_start": 2,
  "allowed_hours_end": 6,
  "excluded_services": ["auth", "payment-core"]
}
```

#### Scheduler 설정

```http
GET /api/self-healing/chaos/config/scheduler/
PATCH /api/self-healing/chaos/config/scheduler/
```

```json
{
  "enabled": true,
  "max_concurrent_experiments": 3,
  "default_schedule_hour_start": 2,
  "default_schedule_hour_end": 6
}
```

### 4.2 Schedule Management

#### 스케줄 목록/생성

```http
GET /api/self-healing/chaos/schedules/
POST /api/self-healing/chaos/schedules/
```

```json
{
  "experiment_type": "latency_injection",
  "target_service": "payment",
  "blast_radius": "instance",
  "schedule_type": "daily",
  "schedule_time": "03:00",
  "experiment_config": {
    "latency_ms": 500,
    "affected_percent": 5.0
  },
  "description": "Daily payment service latency test"
}
```

#### 스케줄 승인/거부

```http
POST /api/self-healing/chaos/schedules/{schedule_id}/approve/
```

```json
{
  "action": "approve",  // or "deny"
  "reason": "리전 페일오버 테스트 승인"
}
```

#### 즉시 실행

```http
POST /api/self-healing/chaos/schedules/{schedule_id}/execute/
```

### 4.3 Kill Switch

```http
GET /api/self-healing/chaos/kill-switch/
POST /api/self-healing/chaos/kill-switch/     # 활성화
DELETE /api/self-healing/chaos/kill-switch/   # 비활성화
```

```json
{
  "reason": "긴급 중단 - 프로덕션 이슈 발생",
  "activated_by": "ops@company.com"
}
```

### 4.4 Safety & Blast Radius Check

```http
POST /api/self-healing/chaos/safety-check/
```

```json
{
  "experiment_type": "latency_injection",
  "blast_radius": "service",
  "target_service": "payment"
}
```

**응답:**

```json
{
  "status": "safe",
  "allowed": true,
  "error_budget_remaining": 75.5,
  "checks_passed": ["error_budget", "kill_switch", "system_health"],
  "warnings": []
}
```

### 4.5 Reports

```http
GET /api/self-healing/chaos/reports/
GET /api/self-healing/chaos/reports/{report_id}/
POST /api/self-healing/chaos/reports/generate/
GET /api/self-healing/chaos/reports/grades/
```

### 4.6 Pending Approvals

```http
GET /api/self-healing/chaos/pending-approvals/
```

---

## 5. Celery Beat 스케줄

카오스 엔진은 Celery Beat를 통해 자동 실행됩니다.

| 태스크 | 주기 | 큐 | 설명 |
|--------|------|-----|------|
| `run_scheduled_experiments` | 5분 | chaos | 예약 실험 자동 실행 |
| `generate_daily_resilience_report` | 매일 06:00 UTC | reports | Daily 리포트 생성 |
| `cleanup_expired_approvals` | 1시간 | maintenance | 만료 승인 정리 |
| `check_pending_approvals` | 30분 | maintenance | 승인 대기 알림 |

**Celery 설정 예시:**

```python
# settings.py 또는 celery.py
from selfhealing.tasks.chaos_scheduler import get_beat_schedule_for_celery

app.conf.beat_schedule.update(get_beat_schedule_for_celery())
```

---

## 6. 운영 가이드

### 6.1 긴급 중단 (Kill Switch)

```bash
# Kill Switch 활성화
curl -X POST http://localhost:8000/api/self-healing/chaos/kill-switch/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"reason": "긴급 중단", "activated_by": "ops@company.com"}'

# Kill Switch 해제
curl -X DELETE http://localhost:8000/api/self-healing/chaos/kill-switch/ \
  -H "Authorization: Bearer $TOKEN"
```

### 6.2 고위험 실험 승인 프로세스

1. **스케줄 생성** → REGION 레벨 지정
2. **시스템 자동 검사** → `requires_approval = True` 플래그
3. **운영자 알림** → 30분마다 Pending 알림
4. **수동 승인** → API 또는 대시보드에서 승인
5. **실행** → 다음 스케줄 시점에 실행

### 6.3 에러 버짓 부족 시

에러 버짓이 20% 미만일 경우:

1. 모든 카오스 실험 자동 차단
2. `ChaosSkippedDueToLowBudget` 알림 발생
3. 버짓 회복 시까지 대기

```
Alert: ChaosSkippedDueToLowBudget
- Experiment: latency_injection (sched-abc123)
- Current Budget: 15.2%
- Required: ≥ 20%
- Action: Experiment skipped
```

### 6.4 Daily Resilience Report 확인

```bash
# 최근 리포트 조회
curl http://localhost:8000/api/self-healing/chaos/reports/ \
  -H "Authorization: Bearer $TOKEN"

# 등급 히스토리
curl http://localhost:8000/api/self-healing/chaos/reports/grades/?days=30 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 7. 설정 레퍼런스

### 7.1 SafetyConfig

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `error_budget_min_percent` | float | 20.0 | 최소 에러 버짓 % |
| `error_budget_warning_percent` | float | 50.0 | 경고 발생 버짓 % |
| `experiment_cooldown_minutes` | int | 30 | 실험 간 쿨다운 (분) |
| `require_healthy_system` | bool | true | 시스템 헬스 체크 필수 |
| `require_no_active_incidents` | bool | true | 활성 장애 없음 필수 |
| `require_no_deployment_freeze` | bool | true | 배포 동결 없음 필수 |
| `fail_safe_on_error` | bool | true | 오류 시 차단 (Fail-Safe) |

### 7.2 BlastRadiusPolicy

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `instance_max_concurrent` | int | 5 | INSTANCE 동시 실험 최대 |
| `service_max_concurrent` | int | 2 | SERVICE 동시 실험 최대 |
| `region_max_concurrent` | int | 1 | REGION 동시 실험 최대 |
| `instance_auto_approve` | bool | true | INSTANCE 자동 승인 |
| `service_auto_approve` | bool | false | SERVICE 자동 승인 |
| `region_auto_approve` | bool | false | REGION 자동 승인 (항상 false 권장) |
| `allowed_hours_start` | int | 2 | 허용 시간 시작 (UTC) |
| `allowed_hours_end` | int | 6 | 허용 시간 종료 (UTC) |
| `excluded_services` | list | [] | 제외할 서비스 목록 |
| `excluded_domains` | list | [] | 제외할 도메인 목록 |

### 7.3 SchedulerConfig

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | true | 스케줄러 활성화 |
| `max_concurrent_experiments` | int | 3 | 최대 동시 실험 수 |
| `approval_timeout_hours` | int | 24 | 승인 요청 만료 시간 |

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [11_FORENSIC_ADVISOR.md](11_FORENSIC_ADVISOR.md) | ForensicAdvisor 연동 |
| [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) | Error Budget 시스템 |
| [07_CONTROL_API.md](07_CONTROL_API.md) | Control API 레퍼런스 |
| [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) | 운영자 가이드 |

---

## 관련 코드

| 경로 | 설명 |
|------|------|
| `packages/selfhealing-python/src/selfhealing/services/chaos/` | 카오스 엔진 핵심 모듈 |
| `packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py` | 스케줄러 서비스 |
| `packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py` | SafetyGuard |
| `packages/selfhealing-python/src/selfhealing/services/chaos/blast_radius.py` | BlastRadiusManager |
| `packages/selfhealing-python/src/selfhealing/services/chaos/experiments.py` | 5종 실험 클래스 |
| `packages/selfhealing-python/src/selfhealing/services/chaos/reports.py` | 리포트 생성기 |
| `packages/selfhealing-python/src/selfhealing/api/django/views/chaos.py` | API Views |
| `packages/selfhealing-python/src/selfhealing/tasks/chaos_scheduler.py` | Celery Tasks |

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2025-12-20
- **담당자**: SelfHealing Team
