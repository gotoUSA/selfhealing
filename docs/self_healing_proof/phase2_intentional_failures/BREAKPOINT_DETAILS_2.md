# Breakpoint 상세 분석: BP-26 ~ BP-30

> **문서 목적**: Circuit Breaker 연동, Race Window 극대화, ForensicContext 검증, 포인트 고아 상태, 캐시 무효화에 대한 상세 분석

---

## BP-26: Circuit Breaker → DLQ Replay 단절

### 코드 위치

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py`

```python
class CircuitBreakerService(ProtectionMixin, ManualControlMixin):
    """
    Circuit Breaker Service.
    
    토글 기반 수동 제어. force_open/force_close로 조작.
    """
    
    def should_allow(self, service_name: str) -> bool:
        """요청 허용 여부 확인"""
        if not self.is_enabled:
            return True
        
        state = self.get_or_create_state(service_name)
        return state.state != CircuitState.OPEN.value
```

**파일**: `shopping/tasks/dlq_replay_tasks.py`

```python
@shared_task(...)
def replay_on_circuit_breaker_close(
    self,
    service_name: str,
    max_items: int = 50,
) -> dict:
    """
    CB 클로즈 시 DLQ 재생
    
    이 태스크가 force_close(trigger_replay=True) 시 호출되어야 함
    """
    from selfhealing.services import get_replay_service

    service = get_replay_service()
    # ...
```

### 파괴 메커니즘: 연동 단절 의심

```
[예상 흐름]
force_close(service_name="toss_payment", trigger_replay=True)
                    ↓
              CB 상태: OPEN → CLOSED
                    ↓
              replay_on_circuit_breaker_close.delay("toss_payment")
                    ↓
              DLQ 조회 (failure_type in ["PG_TIMEOUT", "PG_CONNECTION_ERROR"])
                    ↓
              각 엔트리 재생

[의심되는 실제 흐름]
force_close(service_name="toss_payment", trigger_replay=True)
                    ↓
              CB 상태: OPEN → CLOSED
                    ↓
              ??? replay 태스크 호출 코드 없음 ???
                    ↓
              DLQ 상태 그대로 유지
```

### ManualControlMixin 분석 필요

```python
# selfhealing/services/circuit_breaker/manual_control.py (추정)
class ManualControlMixin:
    def force_close(
        self,
        service_name: str,
        reason: str,
        controlled_by: Any = None,
        trigger_replay: bool = False,
    ) -> CircuitBreakerResult:
        """CB 강제 클로즈"""
        # 상태 변경
        self.repository.update_state(service_name, CircuitState.CLOSED)
        
        # trigger_replay 처리 코드가 있는가?
        if trigger_replay:
            # 이 코드가 실제로 존재하는지 검증 필요
            from shopping.tasks.dlq_replay_tasks import replay_on_circuit_breaker_close
            replay_on_circuit_breaker_close.delay(service_name)
```

### 검증 포인트

```python
def verify_bp26():
    from selfhealing.services import get_circuit_breaker_service
    from shopping.models.failed_operation import FailedOperation
    
    cb = get_circuit_breaker_service()
    
    # 1. DLQ에 PG_TIMEOUT 엔트리 축적
    for i in range(3):
        FailedOperation.objects.create(
            domain="payment",
            failure_type="PG_TIMEOUT",
            status="pending",
        )
    
    # 2. CB 강제 오픈
    cb.force_open("toss_payment", reason="Test")
    
    # 3. CB 강제 클로즈 (replay 트리거)
    result = cb.force_close("toss_payment", reason="Test", trigger_replay=True)
    
    # 4. 잠시 대기 (Celery 태스크 실행)
    import time
    time.sleep(5)
    
    # 5. DLQ 상태 확인
    pending_count = FailedOperation.objects.filter(
        failure_type="PG_TIMEOUT",
        status="pending"
    ).count()
    
    replayed_count = FailedOperation.objects.filter(
        failure_type="PG_TIMEOUT",
        status__in=["replayed", "resolved"]
    ).count()
    
    # 정상: pending → replayed/resolved
    assert replayed_count > 0, "CB close should trigger DLQ replay"
    
    # 연동 단절: 모두 pending 유지
    if pending_count == 3:
        raise AssertionError("BP-26: CB↔DLQ 연동 단절 확인됨!")
```

### 서비스-실패유형 매핑

```python
# shopping/services/self_healing/replay_handlers.py

SHOPPING_SERVICE_FAILURE_TYPES: dict[str, list[str]] = {
    "toss_payment": ["PG_TIMEOUT", "PG_CONNECTION_ERROR", "PG_500_ERROR"],
    "notification": ["SMTP_TIMEOUT", "FCM_ERROR"],
}
```

이 매핑이 실제로 사용되는지 확인 필요.

---

## BP-27: Race Window 극대화 (Confirm + Cancel 교차)

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
@staticmethod
@transaction.atomic
def confirm_payment_sync(payment, payment_key, order_id, amount, user):
    # [BP-27 주입 지점 1] 락 획득 전 지연
    inject_confirm_race_delay(payment_id=payment.pk)
    
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    
    if payment.is_paid:
        raise PaymentConfirmError("이미 완료된 결제입니다.")
    
    # ... Toss API 호출 ...
    
    # [BP-27 주입 지점 2] 커밋 전 지연
    inject_payment_confirm_delay(payment_id=payment.id, order_id=order.id)
    
    payment.mark_as_paid(payment_data)
```

```python
@staticmethod
@transaction.atomic
def cancel_payment(payment_id, user, cancel_reason):
    # [BP-27 주입 지점 3] 락 획득 전 지연
    inject_cancel_race_delay(payment_id=payment_id)
    
    payment = Payment.objects.select_for_update().get(id=payment_id)
    
    if payment.is_canceled:
        raise PaymentCancelError("이미 취소된 결제입니다.")
```

### 파괴 메커니즘: 상태 불일치 유도

```
[시나리오 A: 락 직렬화 성공]
Confirm ──[DELAY]──select_for_update()──[락 획득]──처리──커밋
Cancel  ──[DELAY]──select_for_update()──[락 대기]──────────[락 획득]──is_paid=True──실패

[시나리오 B: 불가능한 상태 (이론적)]
Confirm ──[락 획득]──Toss API──[DELAY 3초]──────────────────mark_as_paid()
Cancel  ────────────────────[락 대기]──[획득]──is_paid=False?──취소 진행?

문제: 같은 트랜잭션 내에서 락을 해제하지 않으므로 B는 불가능.
     하지만 여러 마이크로서비스로 분리되면?
```

### 극한 시나리오: 트랜잭션 경계 공격

```python
# 만약 락 없이 상태 체크 후 지연이 있다면?
def confirm_payment_sync(...):
    payment = Payment.objects.get(pk=payment.pk)  # 락 없음!
    
    if payment.is_paid:  # False로 확인
        raise ...
    
    # [CHAOS] 여기서 3초 지연
    time.sleep(3)
    
    # 이 사이에 다른 요청이 먼저 완료할 수 있음!
    
    payment.mark_as_paid(...)  # 중복 처리 가능!
```

현재 코드는 `select_for_update()`로 보호되어 있음. 검증 목적:
- 락이 실제로 작동하는지 확인
- 락 대기 시간이 타임아웃되면?

### 트리거 조건

```python
# shopping/chaos/decorators.py

def inject_confirm_race_delay(payment_id: int) -> None:
    """BP-27: Confirm 경로 지연"""
    if not chaos_config.is_chaos_active("race_amplification"):
        return
    
    delay_ms = chaos_config.race_delay_ms
    logger.warning(f"[CHAOS] BP-27 confirm delay: {delay_ms}ms")
    time.sleep(delay_ms / 1000)

def inject_cancel_race_delay(payment_id: int) -> None:
    """BP-27: Cancel 경로 지연"""
    # 동일한 로직
```

### 검증 포인트

```python
import asyncio
import aiohttp

async def verify_bp27():
    async with aiohttp.ClientSession() as session:
        # 결제 생성
        payment_id = await create_payment(session)
        
        # 동시 요청
        confirm_task = session.post(f"/api/payments/{payment_id}/confirm/", ...)
        cancel_task = session.post(f"/api/payments/{payment_id}/cancel/", ...)
        
        results = await asyncio.gather(confirm_task, cancel_task, return_exceptions=True)
    
    # 검증
    payment = Payment.objects.get(id=payment_id)
    
    # 정상: 하나만 성공
    assert not (payment.is_paid and payment.is_canceled), \
        "CRITICAL: Both confirm and cancel succeeded!"
    
    # 하나는 성공, 하나는 4xx
    success_count = sum(1 for r in results if r.status == 200)
    assert success_count == 1, "Exactly one should succeed"
```

---

## BP-28: ForensicContext 필드 누락

### 코드 위치

**파일**: `shopping/services/self_healing/forensic_context.py`

```python
@dataclass
class ForensicContext:
    """실패 작업의 포렌식 컨텍스트"""
    
    # Timing (기본값: 빈 문자열)
    request_timestamp: str = ""
    response_timestamp: str = ""
    latency_ms: int = 0

    # Retry History (기본값: 빈 리스트)
    retry_history: list[RetryAttempt] = field(default_factory=list)

    # State Snapshots (기본값: None)
    state_before: StateSnapshot | None = None
    state_after: StateSnapshot | None = None

    # Request Context (기본값: 빈 문자열)
    client_ip: str = ""
    user_agent: str = ""
    
    # Task Context (기본값: 빈 문자열)
    task_name: str = ""
    task_id: str = ""
```

### 파괴 메커니즘: 기본값으로 남는 필드

```python
# 이상적인 DLQ 저장
dlq_service.store_failure(
    domain="payment",
    failure_type="PG_TIMEOUT",
    forensic_context=ForensicContext(
        request_timestamp="2025-12-18T10:00:00Z",
        response_timestamp="2025-12-18T10:00:30Z",
        latency_ms=30000,
        retry_history=[
            RetryAttempt(attempt=1, error_code="NETWORK_ERROR", ...),
            RetryAttempt(attempt=2, error_code="NETWORK_ERROR", ...),
        ],
        state_before=StateSnapshot(payment_status="ready"),
        state_after=StateSnapshot(payment_status="aborted"),
    )
)

# 현실적인 DLQ 저장 (필드 누락)
dlq_service.store_failure(
    domain="payment",
    failure_type="PG_TIMEOUT",
    forensic_context=ForensicContext()  # 모든 필드 기본값!
)
```

### 문제점

| 필드 | 용도 | 누락 시 영향 |
|------|------|-------------|
| retry_history | 재시도 패턴 분석 | 디버깅 불가 |
| state_before/after | 상태 변화 추적 | 복구 방향 불명 |
| latency_ms | 성능 문제 식별 | 원인 분석 불가 |
| task_id | 태스크 추적 | Celery 로그 연결 불가 |

### 검증 포인트

```python
def verify_bp28():
    # 1. 실패 유도 (BP-21, BP-22, BP-23 등)
    
    # 2. DLQ 엔트리 조회
    failed_op = FailedOperation.objects.latest("created_at")
    ctx = failed_op.forensic_context
    
    # 3. 필수 필드 존재 확인
    required_fields = [
        "request_timestamp",
        "retry_history",
        "state_before",
    ]
    
    missing_fields = []
    for field in required_fields:
        value = ctx.get(field)
        if value is None or value == "" or value == []:
            missing_fields.append(field)
    
    if missing_fields:
        raise AssertionError(
            f"BP-28: ForensicContext missing fields: {missing_fields}"
        )
```

### ForensicContextBuilder 필요성

```python
# 권장 패턴
class ForensicContextBuilder:
    """포렌식 컨텍스트 빌더"""
    
    @classmethod
    def from_payment_failure(
        cls,
        payment: Payment,
        error: Exception,
        retry_count: int,
        pg_response: dict | None = None,
    ) -> ForensicContext:
        return ForensicContext(
            request_timestamp=timezone.now().isoformat(),
            latency_ms=...,
            retry_history=[...],
            state_before=StateSnapshot(
                payment_status=payment.status,
                order_status=payment.order.status,
            ),
            # ...
        )
```

---

## BP-29: 포인트 적립 부분 실패 (Point Accumulation Orphan)

### 코드 위치

**파일**: `shopping/tasks/payment_tasks.py`

```python
def finalize_payment_confirm(self, toss_response, payment_id, user_id):
    # ... 결제 처리 ...
    
    logger.info(f"결제 최종 처리 완료: payment_id={payment_id}")

    # 6. 포인트 적립은 별도 태스크로 (비동기)
    from .point_tasks import add_points_after_payment

    if order.final_amount > 0:
        add_points_after_payment.delay(user_id, order.id)  # ← 비동기 분리!
```

**파일**: `shopping/tasks/point_tasks.py` (추정)

```python
@shared_task(...)
def add_points_after_payment(user_id: int, order_id: int) -> dict:
    """결제 후 포인트 적립"""
    # ... 포인트 적립 로직 ...
```

### 파괴 메커니즘: 성공 후 부수 작업 실패

```
[정상 흐름]
finalize_payment_confirm (성공)
            ↓
      Payment.is_paid = True
      Order.status = "paid"
            ↓
      add_points_after_payment.delay()
            ↓
      User.points += earned_points

[BP-29 파괴 흐름]
finalize_payment_confirm (성공)
            ↓
      Payment.is_paid = True
      Order.status = "paid"
            ↓
      add_points_after_payment.delay()
            ↓
      [CHAOS] 태스크 실패
            ↓
      재시도 소진
            ↓
      ??? DLQ ???
            ↓
      User.points 미증가 (고아 상태)
```

### Phase 1 vs Phase 2

| 차원 | Phase 1 | Phase 2 (BP-29) |
|------|---------|-----------------|
| **실패 위치** | 트랜잭션 내부 | 트랜잭션 외부 (비동기) |
| **롤백 가능** | 예 | **아니오** |
| **상태** | 일관 | **불일치** |
| **복구 방법** | 자동 | **수동 보정** |

### 고아 상태 정의

| 시스템 | 상태 | 문제 |
|--------|------|------|
| Payment | `is_paid=True` | 결제 완료 |
| Order | `status=paid` | 주문 완료 |
| User.points | 미증가 | **포인트 누락** |

고객 관점: "결제했는데 포인트가 안 쌓였어요!"

### 트리거 조건

```python
# shopping/tasks/point_tasks.py

@shared_task(bind=True, max_retries=3)
def add_points_after_payment(self, user_id: int, order_id: int) -> dict:
    # [BP-29 주입 지점]
    if os.getenv("CHAOS_PHASE2_MODE") == "point_orphan":
        raise Exception("[CHAOS] Simulated point accumulation failure")
    
    # ... 정상 로직 ...
```

### 검증 포인트

```python
def verify_bp29():
    # 1. 결제 성공 (포인트 적립 태스크 실패 주입)
    # 2. Celery 태스크 재시도 소진 대기
    
    # 3. 상태 확인
    order = Order.objects.get(id=order_id)
    user = order.user
    
    assert order.status == "paid", "Order should be paid"
    
    # 4. 포인트 확인
    expected_points = int(order.total_amount * user.get_earn_rate() / 100)
    
    # 포인트가 적립되지 않았으면 고아 상태
    if order.earned_points == 0:
        # DLQ에 기록되어야 함
        failed_op = FailedOperation.objects.filter(
            domain="point",
            order_id=order_id
        ).first()
        
        assert failed_op is not None, \
            "BP-29: Point orphan should be recorded in DLQ"
```

### 보상 트랜잭션 필요성

```python
# 포인트 고아 상태 감지 및 보상
def detect_point_orphans():
    """포인트 적립 누락 주문 감지"""
    orphans = Order.objects.filter(
        status="paid",
        earned_points=0,
        created_at__lt=timezone.now() - timedelta(hours=1)
    )
    
    for order in orphans:
        # DLQ에 없으면 누락
        if not FailedOperation.objects.filter(order_id=order.id, domain="point").exists():
            logger.error(f"Point orphan detected: order_id={order.id}")
            # 보상 적립 또는 알림
```

---

## BP-30: 캐시 무효화 후 DB 방어 검증

### 코드 위치

**파일**: `shopping/services/toss_webhook_service.py`

```python
@staticmethod
def is_webhook_duplicate(order_id: str, event_type: str) -> bool:
    """Redis에서 웹훅 중복 여부 확인 (TTL 60초)"""
    cache_key = TossWebhookService._get_webhook_cache_key(order_id, event_type)
    return cache.get(cache_key) is not None  # ← Redis 캐시

@staticmethod
@transaction.atomic
def handle_payment_done(event_data: dict[str, Any]) -> None:
    order_id = event_data.get("orderId")

    # 1차 방어: Redis
    if TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.DONE"):
        logger.info(f"Webhook duplicate blocked by Redis: {order_id}")
        return

    # 2. Redis 마킹
    TossWebhookService.mark_webhook_processed(order_id, "PAYMENT.DONE")

    # 3. Payment 조회
    payment = Payment.objects.select_for_update().get(toss_order_id=order_id)

    # 2차 방어: DB
    if payment.is_paid:
        logger.info(f"Payment already processed: {order_id}")
        return  # ← 이 경로!
```

### 파괴 메커니즘: 캐시 미스 후 DB 방어

```
[정상 흐름]
Webhook 1 → is_webhook_duplicate() → False (캐시 미스)
                  ↓
            mark_webhook_processed() (캐시 저장)
                  ↓
            handle_payment_done() → is_paid=True

Webhook 2 (중복) → is_webhook_duplicate() → True (캐시 히트)
                  ↓
            return (무시) ✓

[BP-30 파괴 흐름: 캐시 실패]
Redis FLUSHDB (캐시 전체 삭제)
                  ↓
Webhook 2 (중복) → is_webhook_duplicate() → False (캐시 미스!)
                  ↓
            mark_webhook_processed() (새로 저장)
                  ↓
            payment.is_paid 확인 → True
                  ↓
            return (무시) ✓  ← 2차 방어 성공
```

### 검증 목적

캐시 장애 시에도 시스템이 일관성을 유지하는지 확인.

### 트리거 방법

```bash
# 1. 웹훅 처리 (캐시 저장됨)
curl -X POST http://localhost:8000/api/webhooks/toss/ \
  -d '{"eventType": "PAYMENT.DONE", "data": {"orderId": "ORDER_123"}}'

# 2. Redis 캐시 삭제
redis-cli FLUSHDB

# 3. 동일 웹훅 재전송
curl -X POST http://localhost:8000/api/webhooks/toss/ \
  -d '{"eventType": "PAYMENT.DONE", "data": {"orderId": "ORDER_123"}}'

# 4. 로그에서 "Payment already processed" 확인
```

### 검증 포인트

```python
def verify_bp30():
    # 1. 결제 완료 (웹훅 처리됨)
    payment = Payment.objects.get(toss_order_id="ORDER_123")
    assert payment.is_paid == True
    
    # 2. Redis 캐시 삭제
    from django.core.cache import cache
    cache.clear()  # 또는 특정 키만 삭제
    
    # 3. 동일 웹훅 재전송
    response = client.post("/api/webhooks/toss/", ...)
    
    # 4. 200 OK 반환 (에러 아님)
    assert response.status_code == 200
    
    # 5. 중복 처리 안 됨 확인
    # (sold_count 변화 없음, 포인트 중복 적립 없음)
    payment.refresh_from_db()
    # 상태 변화 없어야 함
```

### 2차 방어 실패 시나리오 (이론적)

```python
# 만약 is_paid 체크 없이 처리한다면?
def handle_payment_done_unsafe(event_data):
    if is_webhook_duplicate(...):
        return
    
    mark_webhook_processed(...)
    
    payment = Payment.objects.get(...)
    
    # is_paid 체크 없음!
    payment.mark_as_paid(event_data)  # 중복 처리!
    
    for item in order.order_items.all():
        Product.objects.filter(...).update(sold_count=F("sold_count") + 1)  # 중복 증가!
```

현재 코드는 `is_paid` 체크로 보호됨. BP-30은 이 방어가 작동하는지 확인.

---

## 요약: Phase 2 Breakpoint 전체 목록

| BP | 이름 | 위험도 | 핵심 검증 |
|----|------|--------|----------|
| BP-21 | Orphaned PG | CRITICAL | DLQ에 pg_response 캡처 |
| BP-22 | Rollback Failure | HIGH | 2차 DLQ 생성 |
| BP-23 | Silent Task Failure | CRITICAL | Celery 실패 → DLQ 라우팅 |
| BP-24 | Webhook Order | LOW | 순서 역전 방어 |
| BP-25 | TTL Boundary | MEDIUM | 멱등성 2차 방어 |
| BP-26 | CB↔DLQ | HIGH | 연동 작동 확인 |
| BP-27 | Race Window | MEDIUM | 락 직렬화 확인 |
| BP-28 | ForensicContext | MEDIUM | 필드 완전성 |
| BP-29 | Point Orphan | MEDIUM | 비동기 실패 DLQ |
| BP-30 | Cache Miss | LOW | DB 2차 방어 |

---

**다음 문서**: [IMPLEMENTATION_GUIDE.md](./IMPLEMENTATION_GUIDE.md)
