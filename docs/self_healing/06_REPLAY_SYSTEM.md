# Replay System

> 이 문서는 Self-Healing 시스템의 DLQ Replay(재실행) 기능을 상세히 설명합니다.

## 📋 목차

1. [개념 및 목적](#1-개념-및-목적)
2. [Replay 유형](#2-replay-유형)
3. [ReplayHandler](#3-replayhandler)
4. [ReplayService](#4-replayservice)
5. [도메인별 Replay Handler](#5-도메인별-replay-handler)
6. [Conditional Replay](#6-conditional-replay)
7. [사용 예시](#7-사용-예시)

---

## 1. 개념 및 목적

### 1.1 Replay란?

DLQ에 저장된 **실패한 작업을 다시 실행**하는 것입니다.

```
┌─────────────────────────────────────────────────────────────┐
│                       Replay Flow                            │
│                                                              │
│   [DLQ Entry]  →  [Replay Request]  →  [Execute]  →  결과   │
│   (PENDING)       (운영자/자동)        (재실행)      ├─성공→ RESOLVED
│                                                      └─실패→ PENDING/REJECTED
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Retry vs Replay

| 구분 | Retry | Replay |
|------|-------|--------|
| **시점** | 실패 즉시 | DLQ 저장 후 |
| **트리거** | 자동 | 수동/자동 |
| **횟수** | 제한적 (3-5회) | 별도 제한 (2회) |
| **대상** | 재시도 가능 오류 | 모든 DLQ 항목 |
| **컨텍스트** | 원본 컨텍스트 | 저장된 스냅샷 |

### 1.3 왜 Replay가 필요한가?

1. **외부 서비스 복구 후**: 외부 API 장애가 해결된 후 실패한 작업 재실행
2. **버그 수정 후**: 코드 버그로 실패한 작업을 수정 후 재실행
3. **수동 검토 후**: 운영자가 검토 후 안전하다고 판단한 항목 재실행
4. **Circuit Breaker Close 시**: 서비스 복구 후 자동 재실행

---

## 2. Replay 유형

### 2.1 Manual Replay (수동)

운영자가 개별 DLQ 항목을 선택하여 재실행합니다.

```python
# Admin에서 개별 항목 재실행
service = get_replay_service()
result = service.replay_single(
    dlq_id=123,
    triggered_by=admin_user,
)
```

### 2.2 Batch Replay (일괄)

필터 조건에 맞는 다수의 DLQ 항목을 일괄 재실행합니다.

```python
# 도메인별 일괄 재실행
result = service.batch_replay(
    domain="payment",
    failure_type="PG_TIMEOUT",
    max_items=100,
    triggered_by=admin_user,
)

print(f"Total: {result.total}, Success: {result.success_count}")
```

### 2.3 Conditional Replay (조건부)

Circuit Breaker가 Close될 때 자동으로 관련 DLQ 항목을 재실행합니다.

```python
# Circuit Breaker Close 시 자동 호출
result = service.replay_on_circuit_close(
    service_name="toss_payment",
    max_items=50,
)
```

---

## 3. ReplayHandler

### 3.1 추상 클래스

```python
from abc import ABC, abstractmethod

class ReplayHandler(ABC):
    """도메인별 Replay 핸들러 추상 클래스"""
    
    @property
    @abstractmethod
    def domain(self) -> str:
        """이 핸들러가 처리하는 도메인"""
        pass
    
    @abstractmethod
    def can_replay(self, failed_op: FailedOperation) -> tuple[bool, str]:
        """
        Replay 가능 여부 판단.
        
        Returns:
            (can_replay: bool, reason: str)
        """
        pass
    
    @abstractmethod
    def replay(self, failed_op: FailedOperation) -> ReplayResult:
        """
        실제 Replay 실행.
        
        Returns:
            ReplayResult with success/failure status
        """
        pass
```

### 3.2 ReplayResult

```python
@dataclass
class ReplayResult:
    success: bool
    dlq_id: int
    message: str = ""
    error: str | None = None
    data: dict | None = None
    
    @classmethod
    def succeeded(cls, dlq_id: int, message: str = "", data: dict = None):
        return cls(success=True, dlq_id=dlq_id, message=message, data=data)
    
    @classmethod
    def failed(cls, dlq_id: int, error: str):
        return cls(success=False, dlq_id=dlq_id, error=error)
```

### 3.3 BatchReplayResult

```python
@dataclass
class BatchReplayResult:
    total: int = 0              # 전체 시도 수
    success_count: int = 0      # 성공 수
    failed_count: int = 0       # 실패 수
    skipped_count: int = 0      # 스킵 수 (can_replay=False)
    results: list[ReplayResult] = None
```

---

## 4. ReplayService

### 4.1 클래스 개요

```python
from selfhealing.services import get_replay_service

service = get_replay_service()
```

### 4.2 핵심 메서드

#### `replay_single(dlq_id, triggered_by=None) -> ReplayResult`

```python
# 개별 DLQ 항목 재실행
result = service.replay_single(
    dlq_id=123,
    triggered_by=request.user,
)

if result.success:
    print(f"재실행 성공: {result.message}")
else:
    print(f"재실행 실패: {result.error}")
```

#### `batch_replay(...) -> BatchReplayResult`

```python
# 일괄 재실행
result = service.batch_replay(
    domain="payment",              # 필수: 도메인
    failure_type="PG_TIMEOUT",     # 선택: 실패 유형 필터
    status="pending",              # 선택: 상태 필터
    max_items=100,                 # 최대 처리 수
    triggered_by=admin_user,       # 트리거한 사용자
    dry_run=False,                 # True면 실제 실행 안 함
)

print(f"""
Batch Replay 결과:
- 전체: {result.total}
- 성공: {result.success_count}
- 실패: {result.failed_count}
- 스킵: {result.skipped_count}
""")
```

#### `replay_on_circuit_close(service_name, max_items=50) -> BatchReplayResult`

```python
# Circuit Breaker Close 시 자동 호출
result = service.replay_on_circuit_close(
    service_name="toss_payment",
    max_items=50,
)
```

#### `get_replayable_items(domain, limit=100) -> List[FailedOperation]`

```python
# 재실행 가능한 항목 조회
items = service.get_replayable_items(
    domain="payment",
    limit=100,
)

for item in items:
    can_replay, reason = service.can_replay(item)
    if can_replay:
        print(f"재실행 가능: {item.id}")
    else:
        print(f"재실행 불가: {item.id} - {reason}")
```

---

## 5. 도메인별 Replay Handler

### 5.1 PaymentReplayHandler

```python
class PaymentReplayHandler(ReplayHandler):
    """결제 도메인 Replay 핸들러 (도메인 중립 패턴 사용)"""
    
    @property
    def domain(self) -> str:
        return "payment"
    
    def can_replay(self, failed_op: FailedOperation) -> tuple[bool, str]:
        # entity_refs에서 관련 ID 조회
        entity_refs = failed_op.entity_refs or {}
        
        # 이미 완료된 결제인지 확인 (외부 조회 필요시)
        if self._is_already_completed(failed_op.entity_id, entity_refs):
            return False, "Operation is already completed"
        
        # 취소된 관련 엔티티 확인
        if self._is_related_entity_cancelled(entity_refs):
            return False, "Related entity is cancelled"
        
        # 재실행 불가능한 실패 유형
        non_replayable = [
            "AMOUNT_MISMATCH",
            "SECURITY_SIGNATURE_INVALID", 
            "DUPLICATE_OPERATION",
        ]
        if failed_op.failure_type in non_replayable:
            return False, f"Failure type {failed_op.failure_type} cannot be replayed"
        
        return True, ""
    
    def replay(self, failed_op: FailedOperation) -> ReplayResult:
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)
        
        try:
            # 스냅샷 데이터와 entity_refs에서 복구
            entity_id = failed_op.entity_id
            entity_refs = failed_op.entity_refs or {}
            snapshot = failed_op.snapshot_data or {}
            
            # 도메인별 RecoveryHandler를 통해 재시도 스케줄
            recovery = get_recovery_handler(self.domain)
            task_id = recovery.schedule_retry(
                entity_id=entity_id,
                entity_refs=entity_refs,
                snapshot_data=snapshot,
                attempt=0,  # 새 시도
            )
            
            return ReplayResult.succeeded(
                failed_op.id, 
                f"Scheduled retry with task_id={task_id}",
                data={"task_id": task_id}
            )
            
        except Exception as e:
            return ReplayResult.failed(failed_op.id, str(e))
```

### 5.2 PointReplayHandler

```python
class PointReplayHandler(ReplayHandler):
    """포인트 도메인 Replay 핸들러 (도메인 중립 패턴 사용)"""
    
    @property
    def domain(self) -> str:
        return "point"
    
    def can_replay(self, failed_op: FailedOperation) -> tuple[bool, str]:
        # entity_refs에서 user_id 조회
        entity_refs = failed_op.entity_refs or {}
        user_id = entity_refs.get("user_id") or failed_op.snapshot_data.get("user_id")
        
        if not user_id:
            return False, "User ID not found in entity_refs or snapshot"
        
        # 이미 처리된 포인트 적립
        if self._is_point_already_applied(failed_op):
            return False, "Point already applied"
        
        return True, ""
    
    def replay(self, failed_op: FailedOperation) -> ReplayResult:
        can_replay, reason = self.can_replay(failed_op)
        if not can_replay:
            return ReplayResult.failed(failed_op.id, reason)
        
        try:
            # 도메인별 서비스 주입 (예시: 어댑터 패턴)
            point_service = get_point_service()  # 의존성 주입
            
            # entity_refs와 스냅샷에서 포인트 정보 복구
            entity_refs = failed_op.entity_refs or {}
            snapshot = failed_op.snapshot_data or {}
            
            user_id = entity_refs.get("user_id") or snapshot.get("user_id")
            amount = snapshot.get("amount", 0)
            description = snapshot.get("description", "")
            
            point_service.add_point(
                user_id=user_id,
                amount=amount,
                description=description,
            )
            
            return ReplayResult.succeeded(
                failed_op.id,
                f"Point {amount} added successfully for user {user_id}"
            )
            
        except Exception as e:
            return ReplayResult.failed(failed_op.id, str(e))
```

### 5.3 핸들러 등록

```python
# selfhealing/services/replay_service.py

# 도메인별 핸들러 레지스트리
_replay_handlers: dict[str, ReplayHandler] = {}

def register_replay_handler(handler: ReplayHandler):
    """Replay 핸들러 등록"""
    _replay_handlers[handler.domain] = handler

def get_replay_handler(domain: str) -> ReplayHandler | None:
    """도메인별 핸들러 조회"""
    return _replay_handlers.get(domain)

# 핸들러 자동 등록
register_replay_handler(PaymentReplayHandler())
register_replay_handler(PointReplayHandler())
register_replay_handler(InventoryReplayHandler())
register_replay_handler(WebhookReplayHandler())
register_replay_handler(NotificationReplayHandler())
```

---

## 6. Conditional Replay

### 6.1 Circuit Breaker 연동

Circuit Breaker가 Close될 때 자동으로 관련 DLQ 항목을 재실행합니다.

```python
# CircuitBreakerService.force_close() 내부
def force_close(
    self,
    service_name: str,
    reason: str = "",
    controlled_by: User = None,
    trigger_replay: bool = False,
) -> CircuitBreakerResult:
    """Circuit Breaker 강제 Close"""
    
    # ... 상태 변경 로직 ...
    
    if trigger_replay:
        # Celery 태스크로 비동기 실행
        from selfhealing.tasks.replay import conditional_replay_on_circuit_close
        conditional_replay_on_circuit_close.delay(
            service_name=service_name,
            max_items=50,
        )
    
    return result
```

### 6.2 Celery 태스크

```python
# selfhealing/tasks/replay.py

@shared_task(
    name="selfhealing.tasks.conditional_replay_on_circuit_close",
    queue="dlq_processing",
    max_retries=0,
    time_limit=300,
)
def conditional_replay_on_circuit_close(
    service_name: str, 
    max_items: int = 50
) -> dict:
    """
    Circuit Breaker Close 시 조건부 Replay.
    
    해당 서비스 관련 DLQ 항목 중 재실행 가능한 것만 처리.
    """
    service = get_replay_service()
    
    result = service.replay_on_circuit_close(
        service_name=service_name,
        max_items=max_items,
    )
    
    return {
        "service_name": service_name,
        "total": result.total,
        "success_count": result.success_count,
        "failed_count": result.failed_count,
    }
```

### 6.3 서비스 이름과 DLQ 매칭

```python
# 서비스 이름 → 도메인 + failure_type 매핑 (도메인 중립 설정)
SERVICE_TO_DLQ_FILTER = {
    # 외부 결제 서비스 (PG사)
    "external_payment_gateway": {
        "domain": "payment",
        "failure_types": [
            "EXTERNAL_TIMEOUT",
            "CONNECTION_ERROR",
            "CIRCUIT_BREAKER_OPEN",
        ],
    },
    # 재고 관리 서비스
    "inventory_service": {
        "domain": "inventory",
        "failure_types": [
            "RESOURCE_LOCK_TIMEOUT",
            "SERVICE_UNAVAILABLE",
        ],
    },
    # 알림 서비스
    "notification_service": {
        "domain": "notification",
        "failure_types": [
            "DELIVERY_FAILED",
            "RATE_LIMITED",
        ],
    },
}

def replay_on_circuit_close(self, service_name: str, max_items: int = 50):
    """서비스별 관련 DLQ 항목 재실행"""
    
    filter_config = SERVICE_TO_DLQ_FILTER.get(service_name)
    if not filter_config:
        return BatchReplayResult()
    
    # 관련 DLQ 항목 조회
    items = FailedOperation.objects.filter(
        domain=filter_config["domain"],
        failure_type__in=filter_config["failure_types"],
        status="pending",
    )[:max_items]
    
    # 일괄 재실행
    return self._batch_replay_items(items)
```

---

## 7. 사용 예시

### 7.1 Admin에서 수동 Replay

```python
# Django Admin Action
@admin.action(description="선택 항목 재실행")
def replay_selected(modeladmin, request, queryset):
    service = get_replay_service()
    
    success = 0
    failed = 0
    
    for item in queryset:
        result = service.replay_single(
            dlq_id=item.id,
            triggered_by=request.user,
        )
        if result.success:
            success += 1
        else:
            failed += 1
    
    messages.success(request, f"성공: {success}, 실패: {failed}")
```

### 7.2 API에서 Replay 트리거

```python
# POST /api/self-healing/dlq/replay/
class DLQReplayView(APIView):
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        serializer = DLQReplaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        service = get_replay_service()
        
        if serializer.validated_data.get("dlq_id"):
            # 개별 재실행
            result = service.replay_single(
                dlq_id=serializer.validated_data["dlq_id"],
                triggered_by=request.user,
            )
            return Response({
                "success": result.success,
                "message": result.message or result.error,
            })
        else:
            # 일괄 재실행
            result = service.batch_replay(
                domain=serializer.validated_data["domain"],
                failure_type=serializer.validated_data.get("failure_type"),
                max_items=serializer.validated_data.get("max_items", 100),
                triggered_by=request.user,
            )
            return Response({
                "total": result.total,
                "success_count": result.success_count,
                "failed_count": result.failed_count,
            })
```

### 7.3 스케줄 기반 자동 Replay

```python
# Celery Beat 스케줄
CELERY_BEAT_SCHEDULE = {
    # 매시간 payment 도메인 자동 재실행 시도
    "hourly-payment-replay": {
        "task": "selfhealing.tasks.scheduled_batch_replay",
        "schedule": crontab(minute=0),
        "args": ("payment", 100),
    },
}

@shared_task
def scheduled_batch_replay(domain: str, max_items: int = 100):
    """스케줄 기반 일괄 재실행 (도메인 중립)"""
    
    service = get_replay_service()
    
    # Circuit Breaker가 열려있으면 스킵
    cb_service = get_circuit_breaker_service()
    
    # 도메인에 연관된 서비스들의 Circuit Breaker 상태 확인
    related_services = get_related_services_for_domain(domain)
    for svc_name in related_services:
        if not cb_service.should_allow(svc_name):
            return {"skipped": True, "reason": f"Circuit breaker open for {svc_name}"}
    
    result = service.batch_replay(
        domain=domain,
        max_items=max_items,
    )
    
    return {
        "domain": domain,
        "total": result.total,
        "success": result.success_count,
    }
```

### 7.4 멱등성 보장

```python
class PaymentReplayHandler(ReplayHandler):
    def replay(self, failed_op: FailedOperation) -> ReplayResult:
        # 멱등성 키 생성
        idempotency_key = f"replay:{failed_op.id}:{failed_op.retry_count}"
        
        # 이미 처리된 요청인지 확인
        idempotency_service = get_idempotency_service()
        if idempotency_service.is_duplicate(idempotency_key):
            return ReplayResult.failed(
                failed_op.id, 
                "Already replayed"
            )
        
        try:
            # 실제 재실행
            result = self._do_replay(failed_op)
            
            # 성공 시 멱등성 키 기록
            if result.success:
                idempotency_service.mark_completed(idempotency_key)
            
            return result
            
        except Exception as e:
            return ReplayResult.failed(failed_op.id, str(e))
```

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2025-12-20
