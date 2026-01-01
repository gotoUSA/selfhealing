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
from selfhealing.services import get_control_api_service

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

## 9. 보안 및 데이터 보호

### 9.1 권한 설계

Self-Healing API는 역할 기반 접근 제어(RBAC)를 적용합니다.

| 구분 | 권한 설정 | 설명 |
|------|----------|------|
| **설정/제어 (POST/PUT)** | `IsAdminUser` | 사고 방지를 위한 엄격한 통제 |
| **로그/통계 조회 (GET)** | `IsAuthenticated` | 운영의 투명성 확보 |
| **데이터 수정** | API 없음 | 조작 불가능성(Immutability) 보장 |

### 9.2 민감 정보 마스킹 (Log Masking)

모든 로그와 API 응답에서 민감 정보가 자동으로 마스킹됩니다.

**마스킹 대상:**

```python
# 필드 기반 마스킹
sensitive_fields = [
    "password", "secret", "token", "api_key",
    "authorization", "credential", "private_key",
    "card_number", "cvv", "connection_string",
]

# 패턴 기반 마스킹 (정규식)
internal_ip_patterns = [
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}",        # 10.0.0.0/8
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}",  # 172.16.0.0/12
    r"192\.168\.\d{1,3}\.\d{1,3}",           # 192.168.0.0/16
]

server_path_patterns = [
    r"/home/[^/]+",                # Unix 홈 디렉토리
    r"/var/[^/]+/[^/]+",          # Var 서브디렉토리
    r"[A-Z]:\\Users\\[^\\]+",     # Windows 사용자 경로
]
```

**마스킹 결과 예시:**

```json
// Before
{"ip": "10.0.5.123", "path": "/home/deploy/app", "token": "secret123"}

// After
{"ip": "[INTERNAL_IP]", "path": "[SERVER_PATH]", "token": "[REDACTED]"}
```

### 9.3 액세스 로깅 (Access Logging)

민감 엔드포인트에 대한 모든 접근이 기록됩니다.

**로깅 대상 엔드포인트:**

| 엔드포인트 | 민감도 | 이유 |
|-----------|--------|------|
| `/api/self-healing/audit/` | 🔴 높음 | 시스템 제어 기록 |
| `/api/self-healing/config/*` | 🔴 높음 | 시스템 구성 정보 |
| `/api/self-healing/chaos/schedules/*` | 🔴 높음 | 취약점 실험 정보 |
| `/api/self-healing/chaos/config/*` | 🔴 높음 | 카오스 설정 정보 |

**로그 형식:**

```json
{
  "timestamp": "2025-12-21T10:30:00Z",
  "user": "admin",
  "method": "GET",
  "path": "/api/self-healing/audit/",
  "source_ip": "10.0.xxx.xxx",
  "user_agent": "Mozilla/5.0...",
  "status_code": 200,
  "response_time_ms": 45.2
}
```

**미들웨어 설정:**

```python
# settings.py
MIDDLEWARE = [
    ...
    'selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware',
    ...
]
```

### 9.4 조회 성능 보호 (Read Cache)

장애 상황 시 대량 조회로 인한 DB 부하를 방지하기 위해 Redis 캐싱을 사용합니다.

**캐시 전략:**

| 데이터 | TTL | 설명 |
|--------|-----|------|
| Dashboard Summary | 30초 | 전체 요약 데이터 |
| Status Counts | 15초 | 상태별 카운트 |
| Recent Activity | 60초 | 24h/7d 활동 통계 |

**캐시 바이패스:**

```python
# 강제로 최신 데이터 조회
service = get_dashboard_service()
summary = service.get_summary(skip_cache=True)
```

**캐시 무효화:**

```python
from selfhealing.services.dashboard_service import invalidate_dashboard_cache

# 중요 상태 변경 시 캐시 무효화
invalidate_dashboard_cache()
```

### 9.5 데이터 불변성 (Immutability)

감사 로그는 수정할 수 없습니다:

- ✅ 로그 **조회** API만 존재 (GET)
- ❌ 로그 **수정** API 없음 (POST/PUT/DELETE)
- ✅ 모든 변경은 새 레코드로 추가
- ✅ 삭제는 아카이브 처리만 가능

### 9.6 Fail-Safe vs Fail-Secure 정책

보안 기능 실패 시 처리 방식입니다.

| 실패 유형 | 적용 전략 | 기술적 구현 상세 | 비즈니스 가치 |
|----------|----------|-----------------|--------------|
| **마스킹 실패** | 🔒 Fail-Secure | 원본 차단 및 고정 텍스트 `[MASKING_ERROR: SENSITIVE_DATA_HIDDEN]` 반환 | 데이터 유출 사고 0% 보장 |
| **권한 검증 실패** | 🔒 Fail-Secure | 즉시 403 Forbidden 반환 | 비인가 접근 원천 차단 |
| **액세스 로깅 실패** | 🟢 Fail-Open + Fallback | 메인 로그 실패 시 stdout에 `[FALLBACK_AUDIT_LOG]` 기록 후 진행 | 운영 연속성 + 최소한의 추적성 유지 |
| **캐시 실패** | 🟢 Fail-Open | DB 직접 조회로 전환 및 캐시 상태 경고 발생 | 성능 저하 시에도 서비스 가동 유지 |

**Fail-Secure 권한 클래스 사용:**

```python
from selfhealing.api.django.middleware import (
    FailSecureIsAuthenticated,
    FailSecureIsAdminUser,
)

class MySensitiveView(APIView):
    # 표준 DRF 대신 Fail-Secure 버전 사용
    permission_classes = [FailSecureIsAuthenticated, FailSecureIsAdminUser]
```

**마스킹 실패 시 응답:**

```python
# 마스킹 오류 발생 시 원본 데이터 대신 고정 문자열 반환
"[MASKING_ERROR: SENSITIVE_DATA_HIDDEN]"
```

**Fallback 로깅 출력 예시:**

```bash
# 메인 로깅 실패 시 stdout으로 기록
[FALLBACK_AUDIT_LOG] {"_fallback": true, "_reason": "primary_logging_failed", "user": "admin", "path": "/api/self-healing/config/", ...}
```

### 9.7 재인증 요구사항 (Reauthentication)

중요 설정 변경 시 추가 인증을 요구하는 훅 기반 시스템입니다.

**설계 원칙:**
- **벤더 중립**: 특정 인증 시스템(OAuth, SAML, JWT)에 종속되지 않음
- **확장 가능**: `ReauthenticationProvider` 인터페이스 구현으로 커스텀 가능
- **PCI-DSS 준수**: 유휴 시간 및 세션 제한 지원

**데코레이터 사용:**

```python
from selfhealing.api.django.reauthentication import requires_reauthentication

@requires_reauthentication(
    max_idle_minutes=15,      # 15분 이상 유휴 시 재인증
    max_session_minutes=60,   # 60분 이상 세션 시 재인증
)
def update_config(request, ...):
    # 민감한 설정 변경 로직
    ...
```

**DRF Permission 클래스 사용:**

```python
from selfhealing.api.django.reauthentication import RequiresReauthenticationPermission

class ConfigUpdateView(APIView):
    permission_classes = [IsAuthenticated, RequiresReauthenticationPermission]
```

**커스텀 Provider 구현:**

```python
from selfhealing.api.django.reauthentication import ReauthenticationProvider

class MyOAuthReauthProvider(ReauthenticationProvider):
    """기업별 OAuth 시스템 연동 예시"""

    def check_reauthentication_required(self, request, config):
        # 기업의 OAuth 토큰 검증 로직
        token = self._get_oauth_token(request)
        issued_at = self._decode_token_time(token)

        # 세션 시간 확인
        session_age = (datetime.now() - issued_at).total_seconds() / 60
        if session_age > config.max_session_minutes:
            return True

        return False

    def get_reauthentication_response(self, request, config):
        return JsonResponse({
            'error': 'reauthentication_required',
            'oauth_url': '/oauth/reauthorize/',
        }, status=403)
```

**설정 (settings.py):**

```python
# 커스텀 Provider 등록
SELFHEALING_REAUTH_PROVIDER = 'myapp.auth.MyOAuthReauthProvider'

# 또는 설정 기반 (RequiresReauthenticationPermission 사용 시)
SELFHEALING_REAUTH_MAX_IDLE_MINUTES = 15
SELFHEALING_REAUTH_MAX_SESSION_MINUTES = 60
SELFHEALING_REAUTH_ENABLED = True
```

---

## 10. 상세 API 엔드포인트 테이블

### 10.1 Runtime Config API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/config/` | GET/PATCH | 전체 설정 조회/수정 |
| `/config/reset/` | POST | 설정 초기화 |
| `/config/pending/` | GET | 대기 중 변경 목록 |
| `/config/pending/<id>/cancel/` | DELETE | 대기 변경 취소 |
| `/config/circuit-breaker/` | GET/PATCH | CB 설정 |
| `/config/dlq/` | GET/PATCH | DLQ 설정 |
| `/config/retry/` | GET/PATCH | 재시도 설정 |
| `/config/sla/` | GET/PATCH | SLA 설정 |
| `/config/slo/` | GET/PATCH | SLO 설정 |
| `/config/rate-limit/` | GET/PATCH | Rate Limit 설정 |
| `/config/security/` | GET/PATCH | 보안 설정 |
| `/config/idempotency/` | GET/PATCH | 멱등성 설정 |
| `/config/notification/` | GET/PATCH | 알림 설정 |
| `/config/forensic/` | GET/PATCH | 포렌식 설정 |
| `/config/logging/` | GET/PATCH | 로깅 설정 |
| `/config/metrics/` | GET/PATCH | 메트릭 설정 |
| `/config/error-budget/` | GET/PATCH | Error Budget 설정 |
| `/config/gate/` | GET/PATCH | Gate 설정 |
| `/config/governance/` | GET/PATCH | 거버넌스 설정 |
| `/config/drift-thresholds/` | GET/PATCH | 드리프트 임계치 |
| `/config/l2-storage/` | GET/PATCH | L2 저장소 설정 |

### 10.2 Config History & Rollback API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/config/<type>/history/` | GET | 설정 변경 이력 |
| `/config/<type>/history/<ver>/` | GET | 특정 버전 상세 |
| `/config/<type>/rollback/` | POST | 설정 롤백 |
| `/config/<type>/compare/` | GET | 버전 비교 |

### 10.3 Emergency Mode API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/emergency/status/` | GET | 비상 모드 상태 |
| `/emergency/trigger/` | POST | 비상 모드 활성화 |
| `/emergency/release/` | POST | 비상 모드 해제 |
| `/emergency/gradual-recovery/` | POST | 점진적 복구 시작 |
| `/emergency/stop-recovery/` | POST | 복구 중단 |
| `/emergency/history/` | GET | 비상 모드 이력 |
| `/emergency/config/` | GET/PATCH | 비상 모드 설정 |
| `/emergency/levels/` | GET | 비상 레벨 정의 |

### 10.4 Auto-Tuning API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/auto-tuning/status/` | GET | Auto-Tuning 상태 |
| `/auto-tuning/enable/` | POST | 활성화 |
| `/auto-tuning/disable/` | POST | 비활성화 |
| `/auto-tuning/<module>/enable/` | POST | 모듈 활성화 |
| `/auto-tuning/<module>/disable/` | POST | 모듈 비활성화 |
| `/auto-tuning/bounds/` | GET/PATCH | 조정 범위 |
| `/auto-tuning/history/` | GET | 조정 이력 |
| `/auto-tuning/override/` | POST/DELETE | 수동 오버라이드 |
| `/auto-tuning/metrics/` | GET | 조정 메트릭 |

### 10.5 Error Budget API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/error-budget/status/` | GET | Error Budget 상태 |
| `/error-budget/history/` | GET | 사용 이력 |
| `/error-budget/record/` | POST | 에러 기록 (테스트) |
| `/error-budget/exhaust/` | POST | 예산 소진 (테스트) |
| `/error-budget/reset-simulation/` | POST | 시뮬레이션 초기화 |
| `/gate/reset/` | POST | Gate 초기화 |

### 10.6 Deployment Policy API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/deployment-policy/verdict/` | GET | 배포 허용 여부 |
| `/deployment-policy/acknowledge/` | POST | 동결 확인 |
| `/deployment-policy/override/` | POST | 동결 오버라이드 |
| `/deployment-policy/lift/` | POST | 동결 해제 |
| `/deployment-policy/active-override/` | GET | 활성 오버라이드 |

### 10.7 Reconciliation API (Shadow Budget)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/reconciliation/status/` | GET | 조정 상태 |
| `/reconciliation/failsafe-periods/` | GET | Failsafe 기간 |
| `/reconciliation/shadow-budgets/` | GET | Shadow Budget 목록 |
| `/reconciliation/shadow-budgets/<id>/` | GET | Shadow Budget 상세 |
| `/reconciliation/shadow-budgets/<id>/approve/` | POST | 승인 |
| `/reconciliation/shadow-budgets/<id>/reject/` | POST | 거부 |
| `/reconciliation/excluded-periods/` | GET/POST | 제외 기간 |
| `/reconciliation/excluded-periods/<id>/` | DELETE | 제외 기간 삭제 |
| `/reconciliation/config/` | GET/PATCH | 조정 설정 |

### 10.8 Governance API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/governance/reconcile/` | POST | 정합성 조정 |
| `/governance/mode/` | GET/POST | 모드 전환 |
| `/governance/status/` | GET | RBAC 상태 |
| `/governance/approval-requests/` | GET | 승인 요청 목록 |
| `/governance/approval-requests/<id>/approve/` | POST | 승인 |
| `/governance/approval-requests/<id>/reject/` | POST | 거부 |

### 10.9 L2 Storage API

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/l2-storage/config/` | GET/PATCH | L2 저장소 설정 |
| `/l2-storage/status/` | GET | 상태 조회 |
| `/l2-storage/health/` | GET | 헬스 체크 |
| `/l2-storage/shadow-log/` | GET | Shadow Log 목록 |
| `/l2-storage/shadow-log/stats/` | GET | Shadow Log 통계 |
| `/l2-storage/shadow-log/clear/` | POST | Shadow Log 삭제 |
| `/l2-storage/sync/from-l2/` | POST | L2 → L1 동기화 |
| `/l2-storage/sync/to-l2/` | POST | L1 → L2 동기화 |
| `/l2-storage/drift/stats/` | GET | 드리프트 통계 |
| `/l2-storage/drift/reconcile/` | POST | 드리프트 조정 |

### 10.10 DNA API (Enterprise Features)

#### FinOps DNA
| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/dna/finops/budget/` | GET | 예산 목록 |
| `/dna/finops/budget/<stage>/` | GET/PATCH | Stage 예산 |
| `/dna/finops/cost/` | GET | 비용 조회 |
| `/dna/finops/report/` | GET | 비용 리포트 |
| `/dna/finops/alerts/` | GET | 비용 알림 |

#### Learning DNA
| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/dna/learning/session/<action>/` | POST | 학습 세션 제어 |
| `/dna/learning/patterns/` | GET | 패턴 조회 |
| `/dna/learning/suggestions/` | GET | 제안 목록 |
| `/dna/learning/suggestions/<id>/apply/` | POST | 제안 적용 |
| `/dna/learning/metrics/` | GET | 학습 메트릭 |

#### Rollback DNA
| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/dna/rollback/policy/<stage>/` | GET/PATCH | 롤백 정책 |
| `/dna/rollback/request/` | POST | 롤백 요청 |
| `/dna/rollback/request/<id>/` | GET | 요청 상세 |
| `/dna/rollback/request/<id>/execute/` | POST | 롤백 실행 |
| `/dna/rollback/history/` | GET | 롤백 이력 |

#### Blast Radius DNA
| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/dna/blast-radius/policy/<stage>/` | GET/PATCH | 정책 |
| `/dna/blast-radius/dependency/` | POST | 종속성 추가 |
| `/dna/blast-radius/assessment/` | POST | 영향 평가 |
| `/dna/blast-radius/isolation/` | GET/POST | 격리 목록/실행 |
| `/dna/blast-radius/graph/` | GET | 종속성 그래프 |

#### Compliance DNA
| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/dna/compliance/standards/` | GET | 표준 목록 |
| `/dna/compliance/check/<stage>/` | POST | 준수 검사 |
| `/dna/compliance/violations/` | GET | 위반 목록 |
| `/dna/compliance/violations/<id>/resolve/` | POST | 위반 해결 |
| `/dna/compliance/reports/` | GET | 준수 리포트 |

### 10.11 X-Test Mode API

> Chaos 테스트용 API (X-Test-Mode 헤더 필수)

| 엔드포인트 | 메서드 | 용도 |
|-----------|--------|------|
| `/xtest/inject-cb-failure/` | POST | CB 실패 주입 |
| `/xtest/reset-cb/` | POST | CB 리셋 |
| `/xtest/cb-status/` | GET | CB 상세 상태 |
| `/xtest/inject-error-budget/` | POST | Error Budget 주입 |
| `/xtest/snapshot/` | GET | 시스템 스냅샷 |
| `/xtest/healing-timeline/` | GET | 치유 타임라인 |
| `/xtest/blast-radius-test/` | POST | 폭발 반경 테스트 |
| `/xtest/generate-postmortem/` | POST | 포스트모템 생성 |

---

## 11. Serializers (API 직렬화기)

> REST API 요청/응답 직렬화 및 검증

### 11.1 Core Serializers

**경로**: `selfhealing.api.django.serializers_legacy`

| Serializer | 용도 |
|-----------|------|
| `ControlRequestSerializer` | 서비스 제어 요청 |
| `DLQReplayRequestSerializer` | DLQ 리플레이 요청 |
| `EvidenceSerializer` | Forensic 증거 데이터 |
| `ControlResponseSerializer` | 제어 API 응답 |
| `ControlErrorResponseSerializer` | 제어 API 에러 응답 |
| `ServiceStateSerializer` | 서비스 상태 |
| `ControlStatusResponseSerializer` | 상태 조회 응답 |
| `AuditLogSerializer` | Audit 로그 항목 |
| `AuditLogListResponseSerializer` | Audit 로그 목록 |
| `ServiceMetricsSerializer` | 서비스 메트릭 |
| `MetricsResponseSerializer` | 메트릭 응답 |
| `HealthCheckResponseSerializer` | 헬스체크 응답 |
| `DLQReplayResponseSerializer` | DLQ 리플레이 응답 |

### 11.2 Config Serializers

**경로**: `selfhealing.api.django.serializers.config`

| Serializer | 용도 |
|-----------|------|
| `ApplyStrategyMixin` | 적용 전략 공통 Mixin |
| `CircuitBreakerConfigSerializer` | CB 설정 |
| `DLQConfigSerializer` | DLQ 설정 |
| `RetryConfigSerializer` | 재시도 설정 |
| `SLAConfigSerializer` | SLA 설정 |
| `SLOConfigSerializer` | SLO 설정 |
| `RateLimitConfigSerializer` | Rate Limit 설정 |
| `SecurityConfigSerializer` | 보안 설정 |
| `NotificationConfigSerializer` | 알림 설정 |
| `ForensicConfigSerializer` | 포렌식 설정 |
| `MetricsConfigSerializer` | 메트릭 설정 |
| `ErrorBudgetConfigSerializer` | Error Budget 설정 |
| `LoggingConfigSerializer` | 로깅 설정 |
| `L2StorageConfigSerializer` | L2 저장소 설정 |

### 11.3 Chaos Serializers

**경로**: `selfhealing.api.django.serializers.chaos`

| 카테고리 | Serializer | 용도 |
|---------|-----------|------|
| 설정 | `SafetyGuardConfigSerializer` | 안전 장치 설정 |
| 설정 | `BlastRadiusPolicySerializer` | 폭발 반경 정책 |
| 설정 | `SchedulerConfigSerializer` | 스케줄러 설정 |
| 설정 | `TTLConfigSerializer` | TTL 자동만료 설정 |
| 실험 | `ExperimentConfigSerializer` | 실험 설정 |
| 실험 | `ScheduledExperimentSerializer` | 스케줄 실험 요청 |
| 승인 | `ApprovalRequestSerializer` | 승인 요청 |
| 승인 | `ApprovalActionSerializer` | 승인/거부 액션 |
| Kill Switch | `KillSwitchSerializer` | Kill Switch 액션 |
| 안전검사 | `SafetyCheckResultSerializer` | 안전 검사 결과 |
| 안전검사 | `BlastRadiusCheckResultSerializer` | 폭발 반경 검사 결과 |

---

## 버전 정보

- **현재 버전**: 1.5.0
- **마지막 업데이트**: 2026-01-01
- **변경 내역**:
  - v1.5.0: 상세 API 엔드포인트 테이블 추가, Serializers 섹션 추가
  - v1.4.0: 재인증 훅 시스템 추가, Fallback 로깅, 마스킹 에러 문자열 개선
  - v1.3.0: Fail-Safe/Fail-Secure 정책 섹션 추가
  - v1.2.0: 보안 및 데이터 보호 섹션 추가 (권한, 마스킹, 액세스 로깅, 캐싱)
  - v1.1.0: Chaos Engineering API 섹션 추가
  - v1.0.0: 초기 버전
