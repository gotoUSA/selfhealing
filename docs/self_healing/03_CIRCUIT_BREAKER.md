# Circuit Breaker (서킷 브레이커)

> 이 문서는 Self-Healing 시스템의 Circuit Breaker 패턴 구현을 상세히 설명합니다.

## 📋 목차

1. [개념 및 목적](#1-개념-및-목적)
2. [상태 머신](#2-상태-머신)
3. [동작 모드](#3-동작-모드)
4. [서비스 API](#4-서비스-api)
5. [설정](#5-설정)
6. [보호 기능](#6-보호-기능)
7. [데이터 모델](#7-데이터-모델)
8. [사용 예시](#8-사용-예시)
9. [Celery 태스크](#9-celery-태스크)

---

## 1. 개념 및 목적

### 1.1 Circuit Breaker 패턴이란?

Circuit Breaker는 분산 시스템에서 **연쇄 장애(Cascading Failure)**를 방지하기 위한 패턴입니다.
외부 서비스가 응답하지 않을 때 계속 요청을 보내면:

- 리소스(스레드, 커넥션) 고갈
- 응답 시간 급증
- 전체 시스템 마비

Circuit Breaker는 전기 회로의 차단기처럼 동작하여, 장애가 감지되면 회로를 **열어(Open)** 요청을 빠르게 실패시킵니다.

### 1.2 왜 Toggle 기반인가?

현재 구현은 **기본적으로 비활성화**되어 있습니다:

```python
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": False,  # 기본 OFF
        ...
    }
}
```

**이유:**
- 평상시에는 Circuit Breaker 체크 오버헤드가 없음
- PG 장애 감지 시 운영자가 수동으로 `force_open()` 호출
- 복구 확인 후 `force_close()` 호출
- 자동 모드도 지원 (`ENABLED: True` 설정 시)

---

## 2. 상태 머신

### 2.1 상태 정의

```python
class CircuitState(str, Enum):
    CLOSED = "closed"       # 정상 (요청 허용)
    OPEN = "open"           # 차단 (요청 거부)
    HALF_OPEN = "half_open" # 테스트 (제한적 허용)
```

### 2.2 상태 전환 다이어그램

```
                              ┌──────────────────────────┐
                              │                          │
                              ▼                          │
    ┌─────────────────────────────────────────────┐      │
    │                   CLOSED                     │      │
    │  ─────────────────────────────────────────   │      │
    │  • 모든 요청 허용                            │      │
    │  • failure_count 추적                       │      │
    │  • 성공 시 failure_count 리셋               │      │
    └───────────────────┬─────────────────────────┘      │
                        │                                 │
                        │ failure_count >= failure_threshold (5)
                        │ 또는 manual force_open()
                        │                                 │
                        ▼                                 │
    ┌─────────────────────────────────────────────┐      │
    │                    OPEN                      │      │
    │  ─────────────────────────────────────────   │      │
    │  • 모든 요청 즉시 거부 (Fast Fail)          │      │
    │  • opened_at 타임스탬프 기록                │      │
    │  • recovery_timeout 대기                    │      │
    └───────────────────┬─────────────────────────┘      │
                        │                                 │
                        │ recovery_timeout (60초) 경과
                        │ 또는 주기적 태스크에서 전환
                        │                                 │
                        ▼                                 │
    ┌─────────────────────────────────────────────┐      │
    │                 HALF_OPEN                    │      │
    │  ─────────────────────────────────────────   │      │
    │  • 제한된 요청만 허용                        │      │
    │  • 성공/실패 추적                           │      │
    │  • success_count >= success_threshold → CLOSED     │
    │  • 실패 발생 → OPEN                         │───────┘
    └───────────────────┬─────────────────────────┘
                        │
                        │ success_count >= success_threshold (2)
                        │
                        ▼
                    [CLOSED]
```

### 2.3 전환 조건 요약

| 현재 상태 | 조건 | 다음 상태 |
|-----------|------|-----------|
| CLOSED | `failure_count >= 5` | OPEN |
| CLOSED | `force_open()` 호출 | OPEN |
| OPEN | `recovery_timeout (60s)` 경과 | HALF_OPEN |
| OPEN | `force_close()` 호출 | CLOSED |
| HALF_OPEN | 요청 성공 (`success_count >= 2`) | CLOSED |
| HALF_OPEN | 요청 실패 | OPEN |

---

## 3. 동작 모드

### 3.1 자동 모드 (Automatic)

설정에서 `ENABLED: True`일 때 활성화됩니다.

```python
# settings.py
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "RECOVERY_TIMEOUT": 60,
        "SUCCESS_THRESHOLD": 2,
    }
}
```

**동작:**
1. 외부 API 호출 실패 시 `record_failure()` 호출
2. `failure_count`가 임계값(5) 도달 시 자동으로 OPEN
3. 성공 시 `record_success()` 호출하여 카운터 리셋
4. HALF_OPEN에서 충분한 성공 시 자동으로 CLOSED

### 3.2 수동 모드 (Manual/Toggle)

설정에서 `ENABLED: False`(기본값)일 때 사용합니다.

```python
# 운영자가 PG 장애 감지 시
service.force_open(
    service_name="toss_payment",
    reason="PG 장애 감지 - 수동 차단",
    controlled_by=admin_user,
)

# PG 복구 확인 후
service.force_close(
    service_name="toss_payment",
    reason="PG 복구 확인",
    controlled_by=admin_user,
    trigger_replay=True,  # DLQ 자동 재실행
)
```

---

## 4. 서비스 API

### 4.1 CircuitBreakerService

```python
from selfhealing.services import get_circuit_breaker_service

service = get_circuit_breaker_service()
```

#### 핵심 메서드

##### `should_allow(service_name: str) -> bool`
```python
# 요청 허용 여부 확인
if service.should_allow("toss_payment"):
    # 외부 API 호출 진행
    result = call_toss_api()
else:
    # Fast Fail - 즉시 에러 반환
    raise CircuitBreakerOpenError("toss_payment is blocked")
```

##### `force_open(service_name, reason, controlled_by=None) -> CircuitBreakerResult`
```python
# 서비스 강제 차단
result = service.force_open(
    service_name="toss_payment",
    reason="정기 점검",
    controlled_by=admin_user,
)

if result.success:
    print(f"Circuit opened: {result.state}")
```

##### `force_close(service_name, reason, controlled_by=None, trigger_replay=False) -> CircuitBreakerResult`
```python
# 서비스 차단 해제
result = service.force_close(
    service_name="toss_payment",
    reason="점검 완료",
    controlled_by=admin_user,
    trigger_replay=True,  # DLQ 자동 재실행 트리거
)
```

##### `record_failure(service_name: str) -> None`
```python
# 자동 모드에서 실패 기록
try:
    response = call_external_api()
except ExternalAPIError:
    service.record_failure("external_api")
    raise
```

##### `record_success(service_name: str) -> None`
```python
# 자동 모드에서 성공 기록
response = call_external_api()
service.record_success("external_api")
return response
```

##### `get_state(service_name: str) -> str`
```python
# 현재 상태 조회
state = service.get_state("toss_payment")
# Returns: "closed", "open", or "half_open"
```

##### `get_all_states() -> List[Dict]`
```python
# 모든 서비스 상태 조회
states = service.get_all_states()
for s in states:
    print(f"{s['service_name']}: {s['state']}")
```

---

## 5. 설정

### 5.1 설정 구조

```python
# settings.py
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        # 기본 동작
        "ENABLED": False,           # Circuit Breaker 활성화 여부
        "FAILURE_THRESHOLD": 5,     # OPEN 전환까지 연속 실패 횟수
        "RECOVERY_TIMEOUT": 60,     # OPEN → HALF_OPEN 전환 대기 시간(초)
        "SUCCESS_THRESHOLD": 2,     # HALF_OPEN → CLOSED 전환까지 성공 횟수
        
        # 고급 설정
        "HALF_OPEN_REQUEST_LIMIT": 10,  # HALF_OPEN에서 허용할 최대 요청 수
    },
    "GOVERNANCE": {
        "MANUAL_OVERRIDE_TTL_MINUTES": 90,  # 수동 제어 자동 만료 시간
        "MAX_PENDING_DURATION_HOURS": 4,    # DLQ 대기 SLA
        "MAX_RETRY_LIFETIME_HOURS": 24,     # 최대 재시도 기간
    },
}
```

### 5.2 CircuitBreakerConfig

```python
from selfhealing.services.circuit_breaker import CircuitBreakerConfig

# 기본값으로 생성
config = CircuitBreakerConfig()

# 또는 설정에서 로드
config = CircuitBreakerConfig.from_settings()

# 또는 직접 지정
config = CircuitBreakerConfig(
    enabled=True,
    failure_threshold=5,
    recovery_timeout=60,
    success_threshold=2,
)
```

---

## 6. 보호 기능

### 6.1 Rate Limit Cascade Detection

PG에서 429 (Rate Limit) 응답이 폭증하면 자동으로 Circuit Breaker를 Open합니다.

```python
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "RATE_LIMIT_CASCADE_THRESHOLD": 10,     # 윈도우 내 429 응답 임계값
        "RATE_LIMIT_CASCADE_WINDOW_SECONDS": 60, # 감지 윈도우 (초)
    }
}
```

**동작:**
1. 429 응답 감지 시 카운터 증가
2. 60초 내 10회 이상 429 발생 시 CB 자동 Open
3. 이후 요청은 PG에 도달하지 않고 즉시 실패
4. Rate Limit 복구 후 HALF_OPEN → CLOSED 전환

### 6.2 Self-DDoS Protection

재시도 폭증으로 인한 자체 DDoS를 방지합니다.

```python
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "SELF_DDOS_PROTECTION_ENABLED": True,
        "SELF_DDOS_REQUEST_THRESHOLD": 100,  # 윈도우 내 요청 임계값
        "SELF_DDOS_WINDOW_SECONDS": 10,      # 감지 윈도우 (초)
        "SELF_DDOS_BACKOFF_MULTIPLIER": 2.0, # 백오프 배수
    }
}
```

**동작:**
1. 10초 내 100건 이상 요청 감지
2. 재시도 간격에 2x 백오프 적용
3. 요청 속도 자동 조절

### 6.3 Manual Override TTL

수동 제어가 자동으로 만료되도록 TTL을 설정합니다.

```python
SELF_HEALING = {
    "GOVERNANCE": {
        "MANUAL_OVERRIDE_TTL_MINUTES": 90,  # 90분 후 자동 만료
    }
}
```

**동작:**
1. `force_open()` 호출 시 `manual_override_expires_at` 설정
2. 90분 후 수동 제어 자동 해제
3. 시스템이 자동 모드로 복귀

---

## 7. 데이터 모델

### 7.1 CircuitBreakerState 모델

```python
# shopping/models/failed_external_request.py

class CircuitBreakerState(models.Model):
    """Circuit Breaker 상태 저장"""
    
    class State(models.TextChoices):
        CLOSED = "closed", "정상 (Closed)"
        OPEN = "open", "차단 (Open)"
        HALF_OPEN = "half_open", "테스트 중 (Half-Open)"
    
    # 기본 정보
    service_name = models.CharField(max_length=100, unique=True)
    state = models.CharField(
        max_length=20, 
        choices=State.choices, 
        default=State.CLOSED
    )
    
    # 카운터
    failure_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    
    # 타임스탬프
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    half_opened_at = models.DateTimeField(null=True, blank=True)
    
    # 설정 오버라이드
    failure_threshold = models.PositiveIntegerField(default=5)
    recovery_timeout = models.PositiveIntegerField(default=60)
    half_open_max_calls = models.PositiveIntegerField(default=3)
    
    # 수동 제어
    manually_controlled = models.BooleanField(default=False)
    controlled_by = models.ForeignKey(
        "User", 
        on_delete=models.SET_NULL, 
        null=True, blank=True
    )
    control_reason = models.TextField(blank=True)
    manual_override_expires_at = models.DateTimeField(null=True, blank=True)
    
    # 감사
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "shopping_circuit_breaker_state"
```

### 7.2 DTO (Data Transfer Object)

```python
# selfhealing/core/types.py

@dataclass
class CircuitBreakerStateData:
    """Circuit Breaker 상태 DTO"""
    
    service_name: str
    state: str  # 'closed', 'open', 'half_open'
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    half_opened_at: Optional[datetime] = None
    failure_threshold: int = 5
    recovery_timeout: int = 60
    half_open_max_calls: int = 3
    manually_controlled: bool = False
    controlled_by_id: Optional[int] = None
    control_reason: str = ""
    manual_override_expires_at: Optional[datetime] = None
    half_open_request_count: int = 0
    id: Optional[int] = None
    created_at: Optional[datetime] = None
```

---

## 8. 사용 예시

### 8.1 결제 서비스에서 사용

```python
from selfhealing.services import get_circuit_breaker_service
from selfhealing.core.types import CircuitState

class PaymentService:
    def __init__(self):
        self.circuit_breaker = get_circuit_breaker_service()
    
    def process_payment(self, order_id: str, amount: int) -> PaymentResult:
        service_name = "toss_payment"
        
        # 1. Circuit Breaker 체크
        if not self.circuit_breaker.should_allow(service_name):
            raise PaymentBlockedError(
                "결제 서비스가 일시적으로 차단되었습니다."
            )
        
        try:
            # 2. 외부 API 호출
            result = self._call_toss_api(order_id, amount)
            
            # 3. 성공 기록
            self.circuit_breaker.record_success(service_name)
            return result
            
        except TossAPIError as e:
            # 4. 실패 기록
            self.circuit_breaker.record_failure(service_name)
            
            # 재시도 가능 여부에 따라 처리
            if e.is_retryable:
                raise RetryablePaymentError(str(e))
            else:
                raise NonRetryablePaymentError(str(e))
```

### 8.2 Admin에서 수동 제어

```python
# Django Admin Action
from selfhealing.services import get_circuit_breaker_service

@admin.action(description="선택한 서비스 차단")
def block_services(modeladmin, request, queryset):
    service = get_circuit_breaker_service()
    
    for obj in queryset:
        result = service.force_open(
            service_name=obj.service_name,
            reason=f"Admin에서 수동 차단 (by {request.user.username})",
            controlled_by=request.user,
        )
        if result.success:
            messages.success(request, f"{obj.service_name} 차단됨")
        else:
            messages.error(request, f"{obj.service_name} 차단 실패: {result.error}")
```

### 8.3 REST API에서 제어

```python
# POST /api/self-healing/block/toss_payment/
{
    "reason": "PG 정기 점검",
    "ttl_minutes": 60
}

# Response
{
    "status": "success",
    "action_applied": "block",
    "system_state": "open",
    "effective_until": "2025-12-20T15:00:00Z"
}
```

---

## 9. Celery 태스크

### 9.1 주기적 상태 전환 체크

```python
# shopping/tasks/self_healing_tasks.py

@shared_task(
    name="shopping.tasks.self_healing_tasks.check_circuit_breaker_recovery",
    queue="maintenance",
)
def check_circuit_breaker_recovery() -> dict:
    """
    OPEN 상태의 Circuit Breaker를 HALF_OPEN으로 전환.
    매 1분마다 실행 권장.
    """
    # OPEN 상태이고 recovery_timeout 경과한 CB 찾기
    open_circuits = CircuitBreakerState.objects.filter(
        state="open",
        opened_at__isnull=False,
        manually_controlled=False,
    )
    
    transitioned = []
    for circuit in open_circuits:
        elapsed = (now - circuit.opened_at).total_seconds()
        if elapsed >= recovery_timeout:
            circuit.state = "half_open"
            circuit.success_count = 0
            circuit.save()
            transitioned.append(circuit.service_name)
    
    return {"transitioned": transitioned}
```

### 9.2 강제 Open/Close 태스크

```python
@shared_task(
    name="shopping.tasks.self_healing_tasks.force_open_circuit_breaker",
    queue="critical",
)
def force_open_circuit_breaker(
    service_name: str,
    reason: str = "",
    user_id: int | None = None,
) -> dict:
    """Circuit Breaker 강제 Open (비동기)"""
    service = get_circuit_breaker_service()
    result = service.force_open(
        service_name=service_name,
        reason=reason,
        controlled_by=User.objects.get(id=user_id) if user_id else None,
    )
    return {"success": result.success, "state": result.state}


@shared_task(
    name="shopping.tasks.self_healing_tasks.force_close_circuit_breaker",
    queue="critical",
)
def force_close_circuit_breaker(
    service_name: str,
    reason: str = "",
    user_id: int | None = None,
    trigger_replay: bool = False,
) -> dict:
    """Circuit Breaker 강제 Close (비동기)"""
    service = get_circuit_breaker_service()
    result = service.force_close(
        service_name=service_name,
        reason=reason,
        controlled_by=User.objects.get(id=user_id) if user_id else None,
        trigger_replay=trigger_replay,
    )
    return {"success": result.success, "state": result.state}
```

### 9.3 Celery Beat 스케줄

```python
# myproject/celery.py

CELERY_BEAT_SCHEDULE = {
    "check-circuit-breaker-recovery": {
        "task": "shopping.tasks.self_healing_tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,  # 매 1분
    },
}
```

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2025-12-20
