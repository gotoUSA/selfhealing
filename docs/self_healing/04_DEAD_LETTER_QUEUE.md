# Dead Letter Queue (DLQ)

> 이 문서는 Self-Healing 시스템의 Dead Letter Queue 구현을 상세히 설명합니다.

## 📋 목차

1. [개념 및 목적](#1-개념-및-목적)
2. [라이프사이클](#2-라이프사이클)
3. [도메인 분류](#3-도메인-분류)
4. [데이터 모델](#4-데이터-모델)
5. [서비스 API](#5-서비스-api)
6. [SLA 관리](#6-sla-관리)
7. [Forensic Context](#7-forensic-context)
8. [사용 예시](#8-사용-예시)

---

## 1. 개념 및 목적

### 1.1 Dead Letter Queue란?

Dead Letter Queue(DLQ)는 **처리할 수 없는 메시지/작업을 보관하는 별도의 큐**입니다.

재시도 불가능하거나 최대 재시도 횟수를 초과한 작업을 DLQ에 저장하여:

- **데이터 손실 방지**: 실패한 작업을 영구 보존
- **수동 복구 지원**: 운영자가 검토 후 재실행 가능
- **디버깅 지원**: 전체 컨텍스트(요청/응답/상태)를 저장
- **SLA 모니터링**: 도메인별 복구 시간 추적

### 1.2 DLQ로 라우팅되는 경우

```
┌─────────────────────────────────────────────────────────────┐
│                    DLQ 라우팅 조건                           │
├─────────────────────────────────────────────────────────────┤
│  1. 최대 재시도 횟수 초과 (max_retries_exceeded)             │
│  2. 재시도 불가능한 오류 (non_retryable_error)              │
│     - AMOUNT_MISMATCH: 금액 불일치                          │
│     - SECURITY_SIGNATURE_INVALID: 서명 위변조               │
│     - DUPLICATE_PAYMENT: 중복 결제                          │
│  3. SLA 타임아웃 (sla_timeout)                              │
│  4. Circuit Breaker 차단 중 발생 (circuit_breaker_open)     │
│  5. 수동 중단 (manual_abort)                                │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 라이프사이클

### 2.1 상태 정의

```python
class Status(models.TextChoices):
    PENDING = "pending", "검토 대기"
    REVIEWING = "reviewing", "검토 중"
    REPLAYED = "replayed", "재실행 대기"
    REQUIRES_REVIEW = "requires_review", "수동 검토 필요"
    RESOLVED = "resolved", "해결됨"
    REJECTED = "rejected", "복구 불가"
    ARCHIVED = "archived", "보관됨"
    EXPIRED = "expired", "보관 기간 만료"
```

### 2.2 상태 전환 다이어그램

```
                            [새 실패 발생]
                                  │
                                  ▼
                           ┌───────────┐
                           │  PENDING  │◄────────────────────────────┐
                           └─────┬─────┘                             │
                                 │                                   │
              ┌──────────────────┼──────────────────┐               │
              │                  │                  │               │
              ▼                  ▼                  ▼               │
       ┌───────────┐      ┌───────────┐     ┌─────────────┐        │
       │ REVIEWING │      │ REPLAYED  │     │ REQUIRES_   │        │
       └─────┬─────┘      └─────┬─────┘     │   REVIEW    │        │
             │                  │           └──────┬──────┘        │
             │                  │                  │               │
             │        ┌────────┤                  │               │
             │        │        │                  │               │
             ▼        ▼        │                  ▼               │
       ┌───────────────┐       │           ┌───────────┐          │
       │   RESOLVED    │◄──────┤           │  REJECTED │          │
       └───────────────┘       │           └───────────┘          │
                               │                                   │
                               │ (재실행 실패)                      │
                               └───────────────────────────────────┘
                               
       ┌───────────────┐       ┌───────────┐
       │   ARCHIVED    │       │  EXPIRED  │
       └───────────────┘       └───────────┘
            (수동 보관)         (retention 만료)
```

### 2.3 상태 전환 규칙

| 현재 상태 | 가능한 전환 | 트리거 |
|-----------|-------------|--------|
| PENDING | REVIEWING | 운영자가 검토 시작 |
| PENDING | REPLAYED | 재실행 요청 |
| PENDING | REQUIRES_REVIEW | AI/시스템이 수동 검토 필요 판단 |
| REVIEWING | RESOLVED | 검토 후 해결 |
| REVIEWING | REJECTED | 복구 불가 판정 |
| REVIEWING | REPLAYED | 검토 후 재실행 결정 |
| REPLAYED | RESOLVED | 재실행 성공 |
| REPLAYED | PENDING | 재실행 실패 (재시도 가능) |
| REPLAYED | REJECTED | 재실행 실패 (재시도 불가) |
| REQUIRES_REVIEW | REVIEWING | 운영자가 검토 시작 |
| RESOLVED | ARCHIVED | 보관 처리 |
| Any | EXPIRED | retention 기간 만료 |

---

## 3. 도메인 분류

### 3.1 지원 도메인

```python
class Domain(models.TextChoices):
    PAYMENT = "payment", "결제"
    POINT = "point", "포인트"
    INVENTORY = "inventory", "재고"
    WEBHOOK = "webhook", "웹훅"
    NOTIFICATION = "notification", "알림"
```

### 3.2 도메인별 SLA (복구 시간 목표)

| 도메인 | SLA (시간) | 설명 |
|--------|-----------|------|
| Payment | 1시간 | 결제 관련 (가장 긴급) |
| Inventory | 2시간 | 재고 복원 |
| Point | 4시간 | 포인트 적립/취소 |
| Webhook | 8시간 | 외부 알림 |
| Notification | 24시간 | 이메일/SMS |

### 3.3 실패 유형 예시 (도메인 중립)

```python
# 일반적인 실패 유형 (모든 도메인에서 사용 가능)
GENERIC_FAILURE_TYPES = [
    "TIMEOUT",                 # 응답 시간 초과
    "CONNECTION_ERROR",        # 연결 실패
    "MAX_RETRIES_EXCEEDED",    # 최대 재시도 초과
    "VALIDATION_ERROR",        # 데이터 검증 실패 (Non-retryable)
    "SECURITY_VIOLATION",      # 보안 위반 (Non-retryable)
    "RATE_LIMITED",            # 요청 제한 초과
    "EXTERNAL_SERVICE_ERROR",  # 외부 서비스 오류
]

# 예시: 실패 유형은 도메인별로 자유롭게 정의 가능
# - 결제: PG_TIMEOUT, AMOUNT_MISMATCH, DUPLICATE_PAYMENT
# - 재고: STOCK_INSUFFICIENT, LOCK_TIMEOUT
# - 포인트: INSUFFICIENT_POINTS, CALCULATION_ERROR
# - 웹훅: DELIVERY_FAILED, ENDPOINT_UNREACHABLE
```

---

## 4. 데이터 모델

### 4.1 FailedOperation 모델

범용 DLQ 모델로, 모든 도메인의 실패를 저장합니다. **도메인 중립 설계**로 FK 대신 entity_type/entity_id를 사용합니다.

```python
# shopping/models/failed_operation.py

class FailedOperation(models.Model):
    """
    Dead Letter Queue for unrecoverable failures.
    
    설계 원칙:
    - FK 대신 entity_type/entity_id로 느슨한 결합
    - 어떤 비즈니스 도메인에서도 사용 가능
    """
    
    # ========================================
    # Domain & Classification
    # ========================================
    domain = models.CharField(
        max_length=50,
        choices=Domain.choices,
        db_index=True,
    )
    
    failure_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="예: TIMEOUT, VALIDATION_ERROR",
    )
    
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    
    # ========================================
    # Generic Entity Reference (도메인 중립)
    # ========================================
    entity_type = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="엔티티 타입 (예: 'order', 'subscription', 'user')",
    )
    
    entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="엔티티 ID",
    )
    
    entity_refs = models.JSONField(
        default=dict,
        blank=True,
        help_text="추가 엔티티 참조 (예: {'user_id': 123, 'tenant_id': 'abc'})",
    )
    
    user_id = models.PositiveIntegerField(
        null=True, blank=True,
        db_index=True,
    )
    
    # ========================================
    # Snapshot Data (for recovery)
    # ========================================
    snapshot_data = models.JSONField(
        default=dict,
        help_text="원본 데이터 없이도 복구 가능한 스냅샷",
    )
    
    # ========================================
    # Error Information
    # ========================================
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    
    # ========================================
    # Retry Tracking
    # ========================================
    retry_count = models.PositiveIntegerField(
        default=0,
        help_text="DLQ에서 재실행 시도 횟수",
    )
    
    max_retries = models.PositiveIntegerField(
        default=2,
        help_text="DLQ 내 최대 재실행 횟수",
    )
    
    last_retry_at = models.DateTimeField(null=True, blank=True)
    
    # ========================================
    # Forensic Context
    # ========================================
    request_data = models.JSONField(default=dict, blank=True)
    response_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="타이밍, 재시도 이력, 상태 스냅샷",
    )
    
    # ========================================
    # Resolution
    # ========================================
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="resolved_operations",
    )
    resolution_type = models.CharField(
        max_length=30,
        choices=ResolutionType.choices,
        blank=True,
    )
    resolution_note = models.TextField(blank=True)
    
    # ========================================
    # Recommendations
    # ========================================
    next_action_hint = models.TextField(
        blank=True,
        help_text="운영자를 위한 다음 행동 제안",
    )
    
    recommended_action = models.CharField(
        max_length=30,
        choices=RecommendedAction.choices,
        blank=True,
    )
    
    # ========================================
    # Timestamps
    # ========================================
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="보관 만료일 (기본: 30일)",
    )
```

### 4.2 FailedExternalRequest 모델

외부 API 요청 전용 DLQ로, **도메인 중립적 설계**를 따릅니다.

```python
# shopping/models/failed_external_request.py

class FailedExternalRequest(models.Model):
    """
    외부 요청 전용 DLQ (도메인 중립)
    
    FK 대신 entity_type/entity_id를 사용하여 느슨한 결합 유지
    """
    
    # 도메인 (어떤 종류의 요청인지)
    domain = models.CharField(choices=DOMAIN_CHOICES)
    
    # Generic Entity Reference (도메인 중립)
    entity_type = models.CharField(max_length=100, blank=True)
    entity_id = models.CharField(max_length=100, blank=True)
    entity_refs = models.JSONField(default=dict)  # 추가 참조
    user_id = models.PositiveIntegerField(null=True)
    
    # 외부 요청 식별자
    external_request_id = models.CharField(max_length=200)
    external_transaction_id = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=0)
    
    # 실패 정보
    failure_type = models.CharField(choices=FAILURE_TYPE_CHOICES)
    error_code = models.CharField(max_length=100)
    error_message = models.TextField()
    
    # 처리 상태
    status = models.CharField(choices=STATUS_CHOICES)
    
    # 원본 데이터 (디버깅용)
    request_data = models.JSONField()
    response_data = models.JSONField()
    metadata = models.JSONField()
```

---

## 5. 서비스 API

### 5.1 DLQService

```python
from shopping.services.self_healing import get_dlq_service

service = get_dlq_service()
```

#### 핵심 메서드

##### `store_failure()` - DLQ에 실패 저장 (도메인 중립)

```python
result = service.store_failure(
    domain="payment",
    failure_type="TIMEOUT",
    entity_type="order",
    entity_id="12345",
    user_id=user.id,  # FK 대신 ID 사용
    entity_refs={"tenant_id": "acme"},  # 추가 참조
    error_code="TIMEOUT",
    error_message="외부 API 응답 시간 초과",
    snapshot_data={
        "entity_type": "order",
        "entity_id": 12345,
        "amount": 50000,
        "status": "pending",
    },
    request_data={
        "url": "https://api.example.com/...",
        "method": "POST",
        "body": {...},
    },
    response_data={
        "status_code": 504,
        "body": {...},
    },
    metadata={
        "latency_ms": 30000,
        "retry_count": 3,
    },
    recommended_action="replay",
    next_action_hint="외부 서비스 상태 확인 후 재실행 권장",
)

if result.success:
    print(f"DLQ 저장 완료: ID={result.dlq_id}")
```

##### `get_pending()` - 대기 중인 항목 조회

```python
# 도메인별 조회
items = service.get_pending(domain="payment", limit=100)

# 전체 조회
all_items = service.get_pending(limit=100)

# 필터링
items = service.get_pending(
    domain="payment",
    failure_type="PG_TIMEOUT",
    since=timezone.now() - timedelta(hours=24),
)
```

##### `update_status()` - 상태 업데이트

```python
result = service.update_status(
    dlq_id=123,
    status="reviewing",
    reviewer=admin_user,
)
```

##### `mark_resolved()` - 해결 완료 처리

```python
result = service.mark_resolved(
    dlq_id=123,
    resolution_type="auto_replay",
    resolved_by=admin_user,
    note="재실행 성공",
)
```

##### `mark_rejected()` - 복구 불가 처리

```python
result = service.mark_rejected(
    dlq_id=123,
    resolved_by=admin_user,
    note="중복 결제로 확인됨, 수동 환불 처리 완료",
)
```

##### `get_sla_breaches()` - SLA 위반 항목 조회

```python
# SLA를 초과한 PENDING 항목 조회
breaches = service.get_sla_breaches()

for item in breaches:
    print(f"SLA Breach: {item.domain} - {item.failure_type}")
    print(f"  대기 시간: {item.pending_duration}")
    print(f"  SLA: {item.sla_threshold}")
```

---

## 6. SLA 관리

### 6.1 SLA 설정

```python
# settings.py
SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
        "DEFAULT_HOURS": 24,
    },
}
```

### 6.2 SLA 체크 태스크

```python
# shopping/tasks/self_healing_tasks.py

@shared_task(name="shopping.tasks.check_and_report_sla_breaches")
def check_and_report_sla_breaches() -> dict:
    """
    SLA 위반 항목 감지 및 알림.
    매 10분마다 실행 권장.
    """
    service = get_dlq_service()
    breaches = service.get_sla_breaches()
    
    if breaches:
        # Slack 알림 발송
        send_slack_alert(
            channel="#ops-alerts",
            message=f"⚠️ SLA 위반 항목 {len(breaches)}건 감지",
            items=breaches,
        )
    
    return {"breach_count": len(breaches)}
```

### 6.3 SLA 대시보드 쿼리

```python
# 도메인별 SLA 현황
def get_sla_status_by_domain():
    from shopping.models.failed_operation import FailedOperation
    from django.db.models import Count, Avg, F
    from django.db.models.functions import Now
    
    return FailedOperation.objects.filter(
        status="pending"
    ).values("domain").annotate(
        pending_count=Count("id"),
        avg_wait_hours=Avg(
            (Now() - F("created_at")).total_seconds() / 3600
        ),
    )
```

---

## 7. Forensic Context

### 7.1 Forensic Context란?

실패 분석 및 복구를 위한 **완전한 컨텍스트**를 캡처합니다.

```python
@dataclass
class ForensicContext:
    # 타이밍
    request_timestamp: str = ""
    response_timestamp: str = ""
    latency_ms: int = 0
    
    # 재시도 이력
    retry_history: list[RetryAttempt] = field(default_factory=list)
    
    # 상태 스냅샷
    state_before: StateSnapshot | None = None
    state_after: StateSnapshot | None = None
    
    # 요청 컨텍스트
    client_ip: str = ""
    user_agent: str = ""
    session_id: str = ""
    
    # 태스크 컨텍스트
    task_name: str = ""
    task_id: str = ""
    queue_name: str = ""
    worker_id: str = ""
    
    # 외부 시스템
    external_request_id: str = ""
    external_response_code: int | None = None
    external_response_body: str = ""
```

### 7.2 Forensic Context 사용 (도메인 중립)

```python
from selfhealing.services.forensic_context import (
    ForensicContextBuilder,
    capture_forensic_context,
    create_snapshot_data,
)

# 작업 처리 중 컨텍스트 캡처 (도메인 중립)
builder = ForensicContextBuilder()
builder.start_timing()
builder.with_request(request)

# 작업 전 상태 (generic dict 사용)
builder.with_state_before(
    entity_status="pending",
    user_id=user.id,
    amount=50000,
)

try:
    result = call_external_api(data)
    builder.with_external_response(
        request_id=result.request_id,
        response_code=result.status_code,
        response_body=result.body,
    )
except Exception as e:
    ctx = builder.build()
    ctx.add_retry_attempt(
        attempt=1,
        error_code=getattr(e, 'code', 'UNKNOWN'),
        error_message=str(e),
    )
    raise

# 작업 후 상태 (generic dict 사용)
builder.with_state_after(
    entity_status="completed",
    result_code="SUCCESS",
)

# DLQ 저장 시 컨텍스트 포함
ctx = builder.build()
service.store_failure(
    ...
    metadata=ctx.to_metadata(),
)
```

### 7.3 저장되는 metadata 구조 (도메인 중립)

```json
{
    "request_timestamp": "2025-12-20T10:00:00Z",
    "response_timestamp": "2025-12-20T10:00:30Z",
    "latency_ms": 30000,
    "retry_history": [
        {
            "attempt": 1,
            "error_code": "TIMEOUT",
            "error_message": "Connection timed out",
            "attempted_at": "2025-12-20T10:00:05Z",
            "backoff_seconds": 4
        },
        {
            "attempt": 2,
            "error_code": "TIMEOUT",
            "error_message": "Connection timed out",
            "attempted_at": "2025-12-20T10:00:20Z",
            "backoff_seconds": 16
        }
    ],
    "state_before": {
        "entity_status": "pending",
        "amount": 50000
    },
    "state_after": {
        "entity_status": "failed",
        "error_code": "TIMEOUT"
    },
    "client_ip": "203.0.113.50",
    "task_id": "abc123-def456",
    "external_request_id": "ext_req_xyz789"
}
```

---

## 8. 사용 예시 (도메인 중립)

### 8.1 외부 API 서비스에서 DLQ 저장

```python
class ExternalAPIRecoveryHandler:
    """도메인 중립적인 외부 API 복구 핸들러"""
    
    def __init__(self, domain: str = "external_api"):
        self.domain = domain
        self.dlq_service = get_dlq_service()
        self.retry_handler = RetryHandler(domain=domain)
    
    def process_request(self, entity_type: str, entity_id: str, request_data: dict):
        try:
            result = self.retry_handler.execute(
                self._call_external_api,
                request_data,
            )
            return result
            
        except MaxRetriesExceededError as e:
            # DLQ에 저장 (도메인 중립)
            self.dlq_service.store_failure(
                domain=self.domain,
                failure_type="MAX_RETRIES_EXCEEDED",
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=request_data.get("user_id"),
                error_code=e.last_error.code if e.last_error else "",
                error_message=str(e),
                snapshot_data={
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "request_data": request_data,
                },
                recommended_action="replay",
                next_action_hint="외부 서비스 상태 확인 후 재실행 권장",
            )
            raise
```

### 8.2 Admin에서 DLQ 조회 및 처리

```python
# Django Admin
class FailedOperationAdmin(admin.ModelAdmin):
    list_display = ["id", "domain", "failure_type", "status", "created_at"]
    list_filter = ["domain", "status", "failure_type"]
    search_fields = ["entity_id", "error_message"]
    
    actions = ["mark_as_reviewing", "trigger_replay", "mark_as_rejected"]
    
    @admin.action(description="검토 시작")
    def mark_as_reviewing(self, request, queryset):
        service = get_dlq_service()
        for item in queryset:
            service.update_status(item.id, "reviewing", reviewer=request.user)
    
    @admin.action(description="재실행")
    def trigger_replay(self, request, queryset):
        service = get_replay_service()
        for item in queryset:
            service.replay_single(item.id, triggered_by=request.user)
```

### 8.3 Grafana 대시보드용 쿼리

```sql
-- 도메인별 PENDING 항목 수
SELECT 
    domain,
    COUNT(*) as pending_count,
    AVG(EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600) as avg_wait_hours
FROM shopping_failed_operation
WHERE status = 'pending'
GROUP BY domain;

-- SLA 위반 항목
SELECT 
    id, domain, failure_type, created_at,
    EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600 as wait_hours
FROM shopping_failed_operation
WHERE status = 'pending'
  AND (
    (domain = 'payment' AND created_at < NOW() - INTERVAL '1 hour')
    OR (domain = 'point' AND created_at < NOW() - INTERVAL '4 hours')
    OR (domain = 'inventory' AND created_at < NOW() - INTERVAL '2 hours')
  );
```

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2025-12-20
