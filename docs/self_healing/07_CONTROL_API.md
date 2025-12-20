# Control API

> 이 문서는 Self-Healing 시스템의 제어 API를 상세히 설명합니다.

## 📋 목차

1. [개요](#1-개요)
2. [API 엔드포인트](#2-api-엔드포인트)
3. [ControlAPIService](#3-controlapiservice)
4. [요청/응답 형식](#4-요청응답-형식)
5. [위험도 분류](#5-위험도-분류)
6. [인증 및 권한](#6-인증-및-권한)
7. [사용 예시](#7-사용-예시)

---

## 1. 개요

### 1.1 Control API란?

Self-Healing 시스템을 **통합 제어**하기 위한 REST API입니다.

운영자가 다음 작업을 수행할 수 있습니다:
- Circuit Breaker 수동 제어 (Block/Allow/Reset)
- DLQ 조회 및 재실행
- 시스템 상태 조회
- 메트릭 조회

### 1.2 설계 원칙

| 원칙 | 설명 |
|------|------|
| **통합** | 모든 제어 기능을 단일 API로 통합 |
| **감사** | 모든 액션에 대한 로깅 및 추적 |
| **가역성** | 실행한 액션을 되돌릴 수 있음 |
| **거버넌스** | 위험도에 따른 접근 제어 |

### 1.3 API 경로 요약

```
/api/self-healing/
├── control/                 # 통합 제어
├── status/                  # 전체 상태
├── status/{service_name}/   # 서비스별 상태
├── health/                  # 헬스 체크
├── health/ping/             # 간단한 ping
├── metrics/                 # 메트릭 조회
├── block/{service_name}/    # 서비스 차단
├── allow/{service_name}/    # 서비스 허용
├── reset/{service_name}/    # 서비스 리셋
├── dlq/list/                # DLQ 목록
├── dlq/replay/              # DLQ 재실행
│
└── chaos/                   # Chaos Engineering API
    ├── config/
    │   ├── safety-guard/    # SafetyGuard 설정 (GET/PATCH)
    │   ├── blast-radius/    # Blast Radius 정책 (GET/PATCH)
    │   ├── scheduler/       # Scheduler 설정 (GET/PATCH)
    │   └── reports/         # Report 설정 (GET/PATCH)
    ├── schedules/           # 예약 실험 CRUD (GET/POST)
    │   ├── {id}/            # 상세 조회/수정/삭제 (GET/PATCH/DELETE)
    │   ├── {id}/approve/    # 승인/거부 (POST)
    │   └── {id}/execute/    # 즉시 실행 (POST)
    ├── kill-switch/         # Kill Switch 제어 (GET/POST)
    ├── kill-all/            # 전체 실험 중단 (POST)
    ├── safety-check/        # 안전 검사 실행 (GET)
    ├── blast-radius/check/  # Blast Radius 검사 (POST)
    ├── reports/             # 리포트 목록 (GET)
    │   ├── generate/        # 리포트 생성 (POST)
    │   └── {id}/            # 리포트 상세 (GET)
    ├── grade-history/       # 등급 히스토리 (GET)
    ├── pending-approvals/   # 승인 대기 목록 (GET)
    └── safety/              # 안전장치 설정
        ├── stop-conditions/ # Stop Conditions (GET/PATCH)
        ├── ttl/             # TTL 설정 (GET/PATCH)
        └── dry-run/         # Dry Run 설정 (GET/PATCH)
```

---

## 2. API 엔드포인트

### 2.1 통합 제어 API

#### `POST /api/self-healing/control/`

모든 제어 액션을 단일 엔드포인트로 처리합니다.

**Request:**
```json
{
    "service_name": "toss_payment",
    "action": "block",
    "reason": "PG 정기 점검",
    "environment": "ops",
    "ttl_minutes": 60,
    "metadata": {
        "ticket_id": "OPS-12345"
    }
}
```

**Response:**
```json
{
    "status": "success",
    "action_applied": "block",
    "system_state": "open",
    "effective_until": "2025-12-20T15:00:00Z",
    "reason_classification": "maintenance-window",
    "correlation_id": "abc123-def456",
    "risk_level": "high",
    "evidence": {
        "previous_state": "closed",
        "failure_count": 0
    }
}
```

**지원 액션:**

| 액션 | 설명 |
|------|------|
| `block` | 서비스 차단 (Circuit Breaker Open) |
| `allow` | 서비스 허용 (Circuit Breaker Close) |
| `reset` | 서비스 리셋 (카운터 초기화) |
| `override` | 설정 임시 오버라이드 |
| `inject_failure` | 테스트용 실패 주입 |
| `inject_success` | 테스트용 성공 주입 |

### 2.2 상태 조회 API

#### `GET /api/self-healing/status/`

전체 시스템 상태를 조회합니다.

**Response:**
```json
{
    "circuit_breaker": {
        "enabled": true,
        "services": [
            {
                "service_name": "toss_payment",
                "state": "closed",
                "failure_count": 0,
                "manually_controlled": false
            },
            {
                "service_name": "inventory_service",
                "state": "open",
                "failure_count": 5,
                "manually_controlled": true,
                "control_reason": "정기 점검"
            }
        ]
    },
    "dlq": {
        "pending_count": 15,
        "by_domain": {
            "payment": 10,
            "point": 3,
            "inventory": 2
        }
    },
    "timestamp": "2025-12-20T14:00:00Z"
}
```

#### `GET /api/self-healing/status/{service_name}/`

특정 서비스의 상태를 조회합니다.

**Response:**
```json
{
    "service_name": "toss_payment",
    "state": "closed",
    "failure_count": 2,
    "success_count": 0,
    "last_failure_at": "2025-12-20T13:55:00Z",
    "opened_at": null,
    "manually_controlled": false,
    "control_reason": "",
    "failure_threshold": 5,
    "recovery_timeout": 60
}
```

### 2.3 서비스 제어 API (개별)

#### `POST /api/self-healing/block/{service_name}/`

서비스를 차단합니다 (Circuit Breaker Open).

**Request:**
```json
{
    "reason": "PG 장애 감지",
    "ttl_minutes": 30
}
```

**Response:**
```json
{
    "status": "success",
    "action": "block",
    "service_name": "toss_payment",
    "previous_state": "closed",
    "current_state": "open",
    "effective_until": "2025-12-20T14:30:00Z"
}
```

#### `POST /api/self-healing/allow/{service_name}/`

서비스 차단을 해제합니다 (Circuit Breaker Close).

**Request:**
```json
{
    "reason": "PG 복구 확인",
    "trigger_replay": true
}
```

**Response:**
```json
{
    "status": "success",
    "action": "allow",
    "service_name": "toss_payment",
    "previous_state": "open",
    "current_state": "closed",
    "replay_triggered": true
}
```

#### `POST /api/self-healing/reset/{service_name}/`

서비스 상태를 초기화합니다.

**Request:**
```json
{
    "reason": "카운터 리셋"
}
```

### 2.4 DLQ API

#### `GET /api/self-healing/dlq/list/`

DLQ 목록을 조회합니다.

**Query Parameters:**

| 파라미터 | 타입 | 설명 |
|----------|------|------|
| `domain` | string | 도메인 필터 |
| `status` | string | 상태 필터 |
| `failure_type` | string | 실패 유형 필터 |
| `limit` | int | 최대 조회 수 (기본: 100) |
| `offset` | int | 오프셋 |

**Response:**
```json
{
    "count": 15,
    "items": [
        {
            "id": 123,
            "domain": "payment",
            "failure_type": "PG_TIMEOUT",
            "status": "pending",
            "entity_type": "order",
            "entity_id": "12345",
            "error_message": "Connection timed out",
            "created_at": "2025-12-20T13:00:00Z",
            "retry_count": 0,
            "can_replay": true
        }
    ]
}
```

#### `POST /api/self-healing/dlq/replay/`

DLQ 항목을 재실행합니다.

**Request (개별):**
```json
{
    "dlq_id": 123
}
```

**Request (일괄):**
```json
{
    "domain": "payment",
    "failure_type": "PG_TIMEOUT",
    "max_items": 50
}
```

**Response:**
```json
{
    "total": 10,
    "success_count": 8,
    "failed_count": 2,
    "skipped_count": 0
}
```

### 2.5 헬스 체크 API

#### `GET /api/self-healing/health/`

Self-Healing 시스템 상태를 확인합니다.

**Response:**
```json
{
    "status": "healthy",
    "circuit_breaker_enabled": true,
    "services_count": 3,
    "timestamp": "2025-12-20T14:00:00Z"
}
```

#### `GET /api/self-healing/health/ping/`

간단한 ping 응답.

**Response:**
```json
{
    "pong": true,
    "timestamp": "2025-12-20T14:00:00Z"
}
```

### 2.6 메트릭 API

#### `GET /api/self-healing/metrics/`

Prometheus 메트릭을 조회합니다.

**Response:**
```json
{
    "dlq_pending_total": 15,
    "dlq_by_domain": {
        "payment": 10,
        "point": 3,
        "inventory": 2
    },
    "circuit_breaker_states": {
        "closed": 2,
        "open": 1,
        "half_open": 0
    },
    "retry_success_rate": 0.85,
    "avg_resolution_time_hours": 2.5
}
```

---

## 3. ControlAPIService

### 3.1 클래스 구조

```python
from shopping.services.self_healing import get_control_api_service

service = get_control_api_service()
```

### 3.2 핵심 메서드

```python
class ControlAPIService:
    """Self-Healing Control API 비즈니스 로직"""
    
    def execute_control(self, request: ControlRequest) -> ControlResponse:
        """통합 제어 명령 실행"""
        ...
    
    def get_status(self) -> dict:
        """전체 시스템 상태 조회"""
        ...
    
    def get_service_status(self, service_name: str) -> dict:
        """서비스별 상태 조회"""
        ...
    
    def block_service(
        self, 
        service_name: str, 
        reason: str, 
        ttl_minutes: int = None,
        controlled_by: User = None,
    ) -> ControlResponse:
        """서비스 차단"""
        ...
    
    def allow_service(
        self,
        service_name: str,
        reason: str,
        trigger_replay: bool = False,
        controlled_by: User = None,
    ) -> ControlResponse:
        """서비스 허용"""
        ...
    
    def reset_service(
        self,
        service_name: str,
        reason: str,
        controlled_by: User = None,
    ) -> ControlResponse:
        """서비스 리셋"""
        ...
```

### 3.3 ControlRequest / ControlResponse

```python
@dataclass
class ControlRequest:
    service_name: str
    action: str                      # block, allow, reset, override, etc.
    reason: str
    environment: str = "ops"         # test, chaos, ops
    ttl_minutes: int | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = field(default_factory=dict)
    actor: str = "system"
    actor_role: str = "automation"

@dataclass
class ControlResponse:
    status: str                      # success, error
    action_applied: str
    system_state: str = ""           # 현재 CB 상태
    effective_until: str | None = None
    reason_classification: str = ""
    evidence: dict = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    error_code: str = ""
    error_message: str = ""
    risk_level: str = ""
```

---

## 4. 요청/응답 형식

### 4.1 Reason Classification

시스템이 `reason` 텍스트를 분석하여 자동 분류합니다.

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

**분류 키워드:**

| 분류 | 키워드 |
|------|--------|
| `maintenance-window` | maintenance, scheduled, upgrade, deploy |
| `sla-breach-mitigation` | sla, breach, violation, threshold |
| `chaos-experiment` | chaos, experiment, resilience |
| `recovery-procedure` | recovery, recovered, restored, fixed |
| `security-incident` | security, attack, ddos, vulnerability |
| `external-dependency-failure` | external, pg, payment gateway, timeout |
| `internal-service-error` | internal, service, error, bug |

### 4.2 Environment 타입

```python
class ControlAPIEnvironments(str, Enum):
    TEST = "test"    # 테스트 환경
    CHAOS = "chaos"  # 카오스 엔지니어링
    OPS = "ops"      # 운영 환경
```

---

## 5. 위험도 분류

### 5.1 Risk Level Matrix

환경과 액션에 따라 위험도가 결정됩니다.

```python
class RiskLevels(str, Enum):
    INFO = "info"           # 정보성
    WARNING = "warning"     # 주의
    HIGH = "high"           # 높음
    CRITICAL = "critical"   # 심각
    FORBIDDEN = "forbidden" # 금지
```

### 5.2 Risk Matrix

| 액션 | TEST | CHAOS | OPS |
|------|------|-------|-----|
| `allow` | INFO | INFO | WARNING |
| `block` | INFO | WARNING | **HIGH** |
| `reset` | INFO | WARNING | WARNING |
| `override` | WARNING | HIGH | **CRITICAL** |
| `inject_failure` | INFO | HIGH | **FORBIDDEN** |
| `inject_success` | INFO | INFO | **FORBIDDEN** |

### 5.3 위험도별 처리

```python
def assess_and_apply_governance(request: ControlRequest) -> ControlResponse:
    risk_level = assess_risk_level(request.action, request.environment)
    
    if risk_level == RiskLevels.FORBIDDEN:
        return ControlResponse(
            status="error",
            error_code="FORBIDDEN_ACTION",
            error_message=f"Action '{request.action}' is forbidden in {request.environment}",
        )
    
    if risk_level == RiskLevels.CRITICAL:
        # 추가 확인 필요
        if not request.metadata.get("confirmed"):
            return ControlResponse(
                status="error",
                error_code="CONFIRMATION_REQUIRED",
                error_message="Critical action requires explicit confirmation",
            )
    
    # 위험도에 따른 로깅
    if risk_level in [RiskLevels.HIGH, RiskLevels.CRITICAL]:
        logger.warning(
            f"[ControlAPI] High-risk action: {request.action} "
            f"by {request.actor} on {request.service_name}"
        )
        # Slack 알림 발송
        send_ops_alert(request, risk_level)
    
    return execute_action(request)
```

---

## 6. 인증 및 권한

### 6.1 인증 방식

```python
# views.py
from rest_framework.permissions import IsAdminUser

class SelfHealingControlView(APIView):
    """
    Self-Healing Control API.
    
    관리자만 접근 가능.
    """
    permission_classes = [IsAdminUser]
    
    def post(self, request, *args, **kwargs):
        ...
```

### 6.2 역할별 권한

| 역할 | 허용 액션 |
|------|-----------|
| `viewer` | 상태 조회만 |
| `operator` | block, allow, reset |
| `admin` | 모든 액션 |
| `automation` | 특정 액션만 (스크립트용) |

### 6.3 감사 로깅

모든 제어 액션은 로깅됩니다.

```python
def log_control_action(request: ControlRequest, response: ControlResponse):
    """제어 액션 감사 로그 기록"""
    logger.info(
        f"[ControlAPI] Action executed: "
        f"action={request.action}, "
        f"service={request.service_name}, "
        f"actor={request.actor}, "
        f"status={response.status}, "
        f"correlation_id={response.correlation_id}"
    )
    
    # DB에도 기록
    ControlActionLog.objects.create(
        action=request.action,
        service_name=request.service_name,
        actor=request.actor,
        reason=request.reason,
        reason_classification=response.reason_classification,
        risk_level=response.risk_level,
        status=response.status,
        correlation_id=response.correlation_id,
    )
```

---

## 7. 사용 예시

### 7.1 cURL로 서비스 차단

```bash
# 서비스 차단
curl -X POST http://localhost:8000/api/self-healing/block/toss_payment/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "PG 정기 점검",
    "ttl_minutes": 60
  }'
```

### 7.2 cURL로 DLQ 재실행

```bash
# DLQ 일괄 재실행
curl -X POST http://localhost:8000/api/self-healing/dlq/replay/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "payment",
    "failure_type": "PG_TIMEOUT",
    "max_items": 50
  }'
```

### 7.3 Python 클라이언트

```python
import requests

class SelfHealingClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }
    
    def block_service(self, service_name: str, reason: str, ttl_minutes: int = None):
        return requests.post(
            f"{self.base_url}/api/self-healing/block/{service_name}/",
            headers=self.headers,
            json={"reason": reason, "ttl_minutes": ttl_minutes},
        ).json()
    
    def allow_service(self, service_name: str, reason: str, trigger_replay: bool = False):
        return requests.post(
            f"{self.base_url}/api/self-healing/allow/{service_name}/",
            headers=self.headers,
            json={"reason": reason, "trigger_replay": trigger_replay},
        ).json()
    
    def get_status(self):
        return requests.get(
            f"{self.base_url}/api/self-healing/status/",
            headers=self.headers,
        ).json()

# 사용
client = SelfHealingClient("http://localhost:8000", "YOUR_TOKEN")
client.block_service("toss_payment", "점검", ttl_minutes=30)
```

### 7.4 Postman Collection

```json
{
    "info": {
        "name": "Self-Healing API",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "item": [
        {
            "name": "Block Service",
            "request": {
                "method": "POST",
                "url": "{{base_url}}/api/self-healing/block/{{service_name}}/",
                "header": [
                    {"key": "Authorization", "value": "Token {{token}}"}
                ],
                "body": {
                    "mode": "raw",
                    "raw": "{\"reason\": \"테스트 차단\", \"ttl_minutes\": 30}"
                }
            }
        }
    ]
}
```

---

## 8. Chaos Engineering API

> Chaos Engineering 전용 API는 별도 모듈로 분리되어 있습니다.
> 상세 내용은 [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md)를 참조하세요.

### 8.1 주요 엔드포인트 요약

| 카테고리 | 엔드포인트 | 메서드 | 설명 |
|----------|-----------|--------|------|
| **설정** | `/chaos/config/safety-guard/` | GET, PATCH | SafetyGuard 설정 |
| **설정** | `/chaos/config/blast-radius/` | GET, PATCH | Blast Radius 정책 |
| **설정** | `/chaos/config/scheduler/` | GET, PATCH | Scheduler 설정 |
| **스케줄** | `/chaos/schedules/` | GET, POST | 예약 실험 목록/생성 |
| **스케줄** | `/chaos/schedules/{id}/approve/` | POST | 실험 승인/거부 |
| **스케줄** | `/chaos/schedules/{id}/execute/` | POST | 즉시 실행 |
| **Kill Switch** | `/chaos/kill-switch/` | GET, POST | Kill Switch 상태/제어 |
| **Kill All** | `/chaos/kill-all/` | POST | 모든 실험 즉시 중단 |
| **안전검사** | `/chaos/safety-check/` | GET | 안전 검사 실행 |
| **리포트** | `/chaos/reports/generate/` | POST | 리포트 생성 |
| **안전장치** | `/chaos/safety/stop-conditions/` | GET, PATCH | Stop Conditions 설정 |
| **안전장치** | `/chaos/safety/ttl/` | GET, PATCH | TTL 설정 |
| **안전장치** | `/chaos/safety/dry-run/` | GET, PATCH | Dry Run 설정 |

### 8.2 안전장치 API 예시

#### Stop Conditions 조회

```bash
curl -X GET /api/self-healing/chaos/safety/stop-conditions/ \
  -H "Authorization: Token <token>"
```

```json
{
  "status": "success",
  "data": {
    "max_error_rate_percent": 5.0,
    "max_latency_p99_ms": 2000,
    "max_latency_p95_ms": 1000,
    "min_error_budget_percent": 10.0,
    "check_interval_seconds": 10,
    "consecutive_breaches_required": 2,
    "enabled": true
  }
}
```

#### Kill All - 전체 실험 중단

```bash
curl -X POST /api/self-healing/chaos/kill-all/ \
  -H "Authorization: Token <token>" \
  -H "Content-Type: application/json" \
  -d '{"reason": "긴급 장애 발생"}'
```

```json
{
  "status": "success",
  "message": "All chaos experiments killed",
  "killed_count": 3,
  "affected_experiments": ["exp-001", "exp-002", "exp-003"],
  "reason": "긴급 장애 발생",
  "killed_by": "admin@example.com",
  "killed_at": "2025-12-21T10:30:00Z"
}
```

---

## 버전 정보

- **현재 버전**: 1.1.0
- **마지막 업데이트**: 2025-12-21
- **변경 내역**:
  - v1.1.0: Chaos Engineering API 섹션 추가
  - v1.0.0: 초기 버전
