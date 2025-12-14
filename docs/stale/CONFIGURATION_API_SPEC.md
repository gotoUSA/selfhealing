# Self-Healing Configuration API 설계 문서

> **문서 버전**: 1.0.0
> **최종 수정**: 2025-12-12
> **상태**: 설계 단계 (구현 예정)

---

## 1. 개요

### 1.1 목적
Enterprise 고객이 Self-Healing 시스템의 모든 정책을 **API를 통해 직접 설정**할 수 있도록 합니다.

### 1.2 핵심 원칙
- **Full Control**: 모든 설정값을 고객이 직접 관리
- **Self-Service**: 문서 기반 자율 설정
- **Audit Trail**: 모든 변경 이력 자동 기록
- **Liability Shift**: 고객 설정에 대한 책임은 고객에게

---

## 2. API 엔드포인트 설계

### 2.1 Config API 목록

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/self-healing/config/` | 전체 설정 조회 |
| GET | `/api/self-healing/config/schema/` | 설정 스키마 (필드 설명, 범위, 기본값) |
| GET | `/api/self-healing/config/circuit-breaker/` | Circuit Breaker 설정 조회 |
| PATCH | `/api/self-healing/config/circuit-breaker/` | Circuit Breaker 설정 수정 |
| GET | `/api/self-healing/config/dlq/` | DLQ 설정 조회 |
| PATCH | `/api/self-healing/config/dlq/` | DLQ 설정 수정 |
| GET | `/api/self-healing/config/retry/` | Retry 정책 조회 |
| PATCH | `/api/self-healing/config/retry/` | Retry 정책 수정 |
| GET | `/api/self-healing/config/sla/` | SLA 임계값 조회 |
| PATCH | `/api/self-healing/config/sla/` | SLA 임계값 수정 |
| GET | `/api/self-healing/config/rate-limit/` | Rate Limit 설정 조회 |
| PATCH | `/api/self-healing/config/rate-limit/` | Rate Limit 설정 수정 |
| GET | `/api/self-healing/config/notification/` | 알림 설정 조회 |
| PATCH | `/api/self-healing/config/notification/` | 알림 설정 수정 |
| POST | `/api/self-healing/config/reset/` | 전체 설정 초기화 (기본값) |
| GET | `/api/self-healing/config/audit/` | 설정 변경 이력 조회 |

---

## 3. 설정 스키마 상세

### 3.1 Circuit Breaker 설정

```json
{
  "circuit_breaker": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Circuit Breaker 활성화 여부",
      "warning": null
    },
    "failure_threshold": {
      "type": "integer",
      "default": 5,
      "min": 1,
      "max": 1000,
      "description": "Circuit이 OPEN 상태로 전환되기 위한 연속 실패 횟수",
      "warning": "1~3으로 설정 시 일시적 네트워크 지연에도 Circuit이 열릴 수 있습니다.",
      "recommended": "5~10"
    },
    "recovery_timeout": {
      "type": "integer",
      "unit": "seconds",
      "default": 60,
      "min": 5,
      "max": 3600,
      "description": "OPEN → HALF_OPEN 전환까지 대기 시간",
      "warning": "300초 이상 설정 시 장애 복구가 지연됩니다.",
      "recommended": "30~120"
    },
    "success_threshold": {
      "type": "integer",
      "default": 2,
      "min": 1,
      "max": 100,
      "description": "HALF_OPEN → CLOSED 전환에 필요한 연속 성공 횟수",
      "warning": null,
      "recommended": "2~5"
    },
    "half_open_max_calls": {
      "type": "integer",
      "default": 3,
      "min": 1,
      "max": 100,
      "description": "HALF_OPEN 상태에서 허용되는 최대 테스트 요청 수",
      "warning": null,
      "recommended": "3~10"
    }
  }
}
```

### 3.2 DLQ (Dead Letter Queue) 설정

```json
{
  "dlq": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "DLQ 활성화 여부"
    },
    "max_retries": {
      "type": "integer",
      "default": 3,
      "min": 0,
      "max": 100,
      "description": "자동 재시도 최대 횟수",
      "warning": "0으로 설정 시 자동 재시도가 비활성화됩니다."
    },
    "retry_delay": {
      "type": "integer",
      "unit": "seconds",
      "default": 60,
      "min": 1,
      "max": 86400,
      "description": "재시도 간 대기 시간"
    },
    "expiry_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 72,
      "min": 1,
      "max": 720,
      "description": "DLQ 항목 만료 시간 (이후 자동 FAILED 처리)"
    },
    "retention_days": {
      "type": "integer",
      "unit": "days",
      "default": 30,
      "min": 1,
      "max": 365,
      "description": "해결된 항목 보관 기간"
    },
    "auto_archive_days": {
      "type": "integer",
      "unit": "days",
      "default": 30,
      "min": 7,
      "max": 365,
      "description": "Resolved 항목 자동 Archive 전환 기간"
    },
    "auto_purge_days": {
      "type": "integer",
      "unit": "days",
      "default": 90,
      "min": 30,
      "max": 730,
      "description": "Archived 항목 자동 삭제 기간"
    }
  }
}
```

### 3.3 Retry 정책 설정

```json
{
  "retry": {
    "max_attempts": {
      "type": "integer",
      "default": 3,
      "min": 1,
      "max": 20,
      "description": "최대 재시도 횟수"
    },
    "backoff_strategy": {
      "type": "enum",
      "options": ["fixed", "linear", "exponential", "fibonacci"],
      "default": "exponential",
      "description": "백오프 전략"
    },
    "base_delay": {
      "type": "float",
      "unit": "seconds",
      "default": 1.0,
      "min": 0.1,
      "max": 60.0,
      "description": "기본 대기 시간"
    },
    "max_delay": {
      "type": "float",
      "unit": "seconds",
      "default": 300.0,
      "min": 1.0,
      "max": 3600.0,
      "description": "최대 대기 시간 상한"
    },
    "jitter": {
      "type": "boolean",
      "default": true,
      "description": "랜덤 지터 추가 여부 (Thundering Herd 방지)"
    },
    "jitter_percent": {
      "type": "integer",
      "unit": "percent",
      "default": 25,
      "min": 0,
      "max": 50,
      "description": "지터 범위 (±%)"
    }
  }
}
```

### 3.4 SLA 임계값 설정

```json
{
  "sla": {
    "payment_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 1,
      "min": 1,
      "max": 168,
      "description": "결제 도메인 SLA (이 시간 내 해결 필요)",
      "warning": "결제는 1시간 이내 해결을 권장합니다."
    },
    "point_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 4,
      "min": 1,
      "max": 168,
      "description": "포인트 도메인 SLA"
    },
    "inventory_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 2,
      "min": 1,
      "max": 168,
      "description": "재고 도메인 SLA"
    },
    "webhook_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 8,
      "min": 1,
      "max": 168,
      "description": "웹훅 도메인 SLA"
    },
    "notification_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 24,
      "min": 1,
      "max": 168,
      "description": "알림 도메인 SLA"
    },
    "default_hours": {
      "type": "integer",
      "unit": "hours",
      "default": 24,
      "min": 1,
      "max": 168,
      "description": "기타 도메인 기본 SLA"
    }
  }
}
```

### 3.5 Rate Limit 설정

```json
{
  "rate_limit": {
    "base_delay": {
      "type": "float",
      "unit": "seconds",
      "default": 1.0,
      "min": 0.1,
      "max": 60.0,
      "description": "429 응답 시 기본 대기 시간"
    },
    "max_delay": {
      "type": "float",
      "unit": "seconds",
      "default": 60.0,
      "min": 1.0,
      "max": 600.0,
      "description": "최대 대기 시간"
    },
    "jitter_percent": {
      "type": "float",
      "unit": "percent",
      "default": 30.0,
      "min": 0.0,
      "max": 50.0,
      "description": "지터 범위"
    },
    "backoff_multiplier": {
      "type": "float",
      "default": 2.0,
      "min": 1.1,
      "max": 5.0,
      "description": "연속 429 시 백오프 배수"
    }
  }
}
```

### 3.6 Notification 설정

```json
{
  "notification": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "알림 활성화 여부"
    },
    "channels": {
      "type": "array",
      "items": "string",
      "options": ["email", "slack", "webhook", "sms"],
      "default": ["email"],
      "description": "알림 채널"
    },
    "critical_threshold": {
      "type": "integer",
      "default": 10,
      "min": 1,
      "max": 1000,
      "description": "CRITICAL 알림 트리거 임계값"
    },
    "warning_threshold": {
      "type": "integer",
      "default": 5,
      "min": 1,
      "max": 1000,
      "description": "WARNING 알림 트리거 임계값"
    },
    "slack_webhook_url": {
      "type": "string",
      "format": "url",
      "default": null,
      "description": "Slack Webhook URL (선택)"
    },
    "email_recipients": {
      "type": "array",
      "items": "email",
      "default": [],
      "description": "알림 수신 이메일 목록"
    }
  }
}
```

---

## 4. API 요청/응답 예시

### 4.1 전체 설정 조회

**Request:**
```http
GET /api/self-healing/config/
Authorization: Bearer <token>
```

**Response:**
```json
{
  "circuit_breaker": {
    "enabled": true,
    "failure_threshold": 5,
    "recovery_timeout": 60,
    "success_threshold": 2,
    "half_open_max_calls": 3
  },
  "dlq": {
    "enabled": true,
    "max_retries": 3,
    "retry_delay": 60,
    "expiry_hours": 72,
    "retention_days": 30
  },
  "retry": { ... },
  "sla": { ... },
  "rate_limit": { ... },
  "notification": { ... },
  "_meta": {
    "last_modified_at": "2025-12-12T10:30:00Z",
    "last_modified_by": "admin@company.com"
  }
}
```

### 4.2 설정 수정

**Request:**
```http
PATCH /api/self-healing/config/circuit-breaker/
Authorization: Bearer <token>
Content-Type: application/json

{
  "failure_threshold": 10,
  "recovery_timeout": 120,
  "acknowledged": true
}
```

**Response (성공):**
```json
{
  "status": "success",
  "updated": {
    "failure_threshold": {
      "old": 5,
      "new": 10
    },
    "recovery_timeout": {
      "old": 60,
      "new": 120
    }
  },
  "warnings": [],
  "audit_id": "audit_abc123"
}
```

**Response (경고 있음):**
```json
{
  "status": "warning",
  "updated": {
    "failure_threshold": {
      "old": 5,
      "new": 2
    }
  },
  "warnings": [
    {
      "field": "failure_threshold",
      "message": "1~3으로 설정 시 일시적 네트워크 지연에도 Circuit이 열릴 수 있습니다.",
      "severity": "medium"
    }
  ],
  "audit_id": "audit_def456"
}
```

### 4.3 위험한 설정 변경 시 (acknowledged 필수)

**Request (acknowledged 없음):**
```http
PATCH /api/self-healing/config/circuit-breaker/
{
  "failure_threshold": 1
}
```

**Response (거부):**
```json
{
  "status": "error",
  "code": "ACKNOWLEDGEMENT_REQUIRED",
  "message": "이 설정은 시스템 안정성에 영향을 줄 수 있습니다. 'acknowledged': true를 포함하여 다시 요청하세요.",
  "warnings": [
    {
      "field": "failure_threshold",
      "message": "failure_threshold=1은 단 한 번의 실패로 Circuit이 열립니다. 프로덕션 환경에서 권장하지 않습니다.",
      "severity": "high"
    }
  ],
  "disclaimer": "이 설정 변경으로 인해 발생하는 서비스 중단, 데이터 손실 또는 기타 문제에 대한 책임은 설정을 변경한 고객에게 있습니다."
}
```

### 4.4 감사 로그 조회

**Request:**
```http
GET /api/self-healing/config/audit/?limit=50
Authorization: Bearer <token>
```

**Response:**
```json
{
  "results": [
    {
      "id": "audit_abc123",
      "timestamp": "2025-12-12T10:30:00Z",
      "user": "admin@company.com",
      "ip_address": "203.0.113.50",
      "user_agent": "Mozilla/5.0...",
      "action": "update",
      "category": "circuit_breaker",
      "changes": [
        {
          "field": "failure_threshold",
          "old_value": 5,
          "new_value": 10
        },
        {
          "field": "recovery_timeout",
          "old_value": 60,
          "new_value": 120
        }
      ],
      "acknowledged": true,
      "warnings_shown": []
    },
    {
      "id": "audit_def456",
      "timestamp": "2025-12-11T15:20:00Z",
      "user": "ops@company.com",
      "ip_address": "203.0.113.51",
      "action": "update",
      "category": "dlq",
      "changes": [
        {
          "field": "max_retries",
          "old_value": 3,
          "new_value": 5
        }
      ],
      "acknowledged": true,
      "warnings_shown": []
    }
  ],
  "pagination": {
    "total": 127,
    "page": 1,
    "page_size": 50
  }
}
```

---

## 5. 데이터 모델 설계

### 5.1 TenantConfig 모델

```python
class TenantConfig(models.Model):
    """테넌트별 설정 저장"""

    tenant_id = models.CharField(max_length=100, unique=True, db_index=True)

    # 각 카테고리별 JSON 설정
    circuit_breaker = models.JSONField(default=dict)
    dlq = models.JSONField(default=dict)
    retry = models.JSONField(default=dict)
    sla = models.JSONField(default=dict)
    rate_limit = models.JSONField(default=dict)
    notification = models.JSONField(default=dict)

    # 메타 정보
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_modified_by = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "selfhealing_tenant_config"
```

### 5.2 ConfigAuditLog 모델

```python
class ConfigAuditLog(models.Model):
    """설정 변경 감사 로그"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.CharField(max_length=100, db_index=True)

    # 변경 정보
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user_email = models.EmailField()
    user_id = models.CharField(max_length=100, null=True)
    ip_address = models.GenericIPAddressField(null=True)
    user_agent = models.TextField(null=True)

    # 변경 내용
    action = models.CharField(max_length=20)  # create, update, reset
    category = models.CharField(max_length=50)  # circuit_breaker, dlq, etc.
    changes = models.JSONField()  # [{field, old_value, new_value}, ...]

    # 동의 정보
    acknowledged = models.BooleanField(default=False)
    warnings_shown = models.JSONField(default=list)

    class Meta:
        db_table = "selfhealing_config_audit_log"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["tenant_id", "timestamp"]),
            models.Index(fields=["category", "timestamp"]),
        ]
```

---

## 6. 보안 및 권한

### 6.1 인증 요구사항
- 모든 Config API는 **Bearer Token 인증** 필수
- 토큰에 `config:read` 또는 `config:write` scope 필요

### 6.2 권한 레벨

| 권한 | 읽기 | 쓰기 | 감사로그 조회 | 초기화 |
|------|------|------|---------------|--------|
| `config:read` | ✅ | ❌ | ❌ | ❌ |
| `config:write` | ✅ | ✅ | ✅ | ❌ |
| `config:admin` | ✅ | ✅ | ✅ | ✅ |

### 6.3 Rate Limiting
- Config API: **분당 60회** 제한
- 감사 로그 조회: **분당 30회** 제한

---

## 7. 면책 조항 (Disclaimer)

### 7.1 설정 변경 시 표시할 면책 문구

> **⚠️ 중요 안내**
>
> 이 API를 통해 변경하는 모든 설정은 귀사의 Self-Healing 시스템 동작에 직접적인 영향을 미칩니다.
>
> - **권장 범위를 벗어난 설정**은 시스템 불안정, 성능 저하, 또는 예상치 못한 동작을 유발할 수 있습니다.
> - **설정 변경으로 인해 발생하는 모든 문제**(서비스 중단, 데이터 손실, 비즈니스 손실 등)에 대한 책임은 **설정을 변경한 고객**에게 있습니다.
> - 모든 설정 변경은 **감사 로그에 기록**되며, 변경한 사용자, 시간, IP 주소가 저장됩니다.
>
> `"acknowledged": true`를 포함하여 요청하는 것은 위 내용을 읽고 이해했으며, 설정 변경에 대한 책임을 수락한다는 것을 의미합니다.

### 7.2 서비스 이용약관에 포함할 내용 (법무팀 검토 필요)

```
제 X 조 (설정 변경에 대한 책임)

1. 고객은 본 서비스에서 제공하는 Configuration API를 통해
   시스템 설정을 변경할 수 있습니다.

2. 고객이 Configuration API를 통해 변경한 설정으로 인해 발생하는
   다음의 문제에 대하여 회사는 책임을 지지 않습니다:
   - 서비스 중단 또는 성능 저하
   - 데이터 손실 또는 데이터 무결성 문제
   - 비즈니스 손실 또는 기회비용 손실
   - 보안 취약점 노출

3. 회사는 모든 설정 변경에 대해 감사 로그를 기록하며,
   분쟁 발생 시 이를 증거로 활용할 수 있습니다.

4. 고객은 설정 변경 시 "acknowledged" 플래그를 통해
   본 조항을 읽고 이해했음을 확인합니다.
```

---

## 8. 구현 우선순위

### Phase 1 (필수)
- [ ] TenantConfig, ConfigAuditLog 모델 생성
- [ ] Config GET API (전체, 카테고리별)
- [ ] Config PATCH API (acknowledged 로직 포함)
- [ ] 감사 로그 자동 기록

### Phase 2 (권장)
- [ ] Config Schema API (동적 필드 설명)
- [ ] Config Reset API
- [ ] 감사 로그 조회 API
- [ ] 변경 전후 diff 알림

### Phase 3 (선택)
- [ ] 설정 Import/Export (JSON)
- [ ] 설정 버전 관리 (특정 시점으로 롤백)
- [ ] 설정 변경 Webhook 알림

---

## 9. 참고: 위험 설정 기준

다음 설정값은 `acknowledged: true` 필수:

| 카테고리 | 필드 | 위험 기준 |
|----------|------|-----------|
| circuit_breaker | failure_threshold | < 3 |
| circuit_breaker | recovery_timeout | > 300 |
| circuit_breaker | enabled | false |
| dlq | enabled | false |
| dlq | max_retries | 0 |
| retry | max_attempts | > 10 |
| retry | max_delay | > 600 |
| sla | payment_hours | > 4 |

---

## 10. 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|-----------|
| 1.0.0 | 2025-12-12 | - | 초기 문서 작성 |
