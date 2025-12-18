# Breakpoint 상세 분석: BP-21 ~ BP-25

> **문서 목적**: 각 Breakpoint의 구체적인 코드 위치, 파괴 메커니즘, Phase 1 대비 강화된 파괴력을 상세히 기술한다.

---

## BP-21: PG 성공 후 내부 실패 (Orphaned PG Transaction)

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
@staticmethod
@transaction.atomic
def confirm_payment_sync(payment: Payment, payment_key: str, order_id: int, amount: int, user) -> dict:
    # ... 락 획득, 상태 검증 ...
    
    # 1. Toss API 호출 - 여기서 PG 트랜잭션 성공
    payment_data = toss_client.confirm_payment(
        payment_key=payment_key,
        order_id=str(order_id),
        amount=amount,
    )
    logger.info(f"토스페이먼츠 결제 승인 성공: payment_id={payment.id}")
    
    # [BP-21 주입 지점] - PG 성공 후, DB 커밋 전
    try:
        inject_partial_failure_after_pg(payment_id=payment.id, pg_response=payment_data)
    except PartialFailureException as e:
        logger.error(f"[CHAOS] Partial failure injected after PG success")
        raise PaymentConfirmError(str(e))  # ← 여기서 DLQ 라우팅 필요
    
    # 2. Payment 정보 업데이트 (실행 안 됨)
    payment.mark_as_paid(payment_data)
```

### 파괴 메커니즘

```
[정상 흐름]
Client → confirm_payment_sync()
              ↓
         Toss API 호출 ─────→ [PG 시스템: 결제 성공]
              ↓
         payment.mark_as_paid()
              ↓
         DB 트랜잭션 커밋 ──→ [DB: Payment.is_paid=True]
              ↓
         200 OK

[BP-21 파괴 흐름]
Client → confirm_payment_sync()
              ↓
         Toss API 호출 ─────→ [PG 시스템: 결제 성공] ← ⚠️ 실제 결제됨
              ↓
         [CHAOS] PartialFailureException
              ↓
         PaymentConfirmError 발생
              ↓
         DB 트랜잭션 롤백 ──→ [DB: Payment.is_paid=False] ← ⚠️ 불일치!
              ↓
         500 Error (또는 400)
              ↓
         ??? DLQ 생성 여부 불명 ???
```

### Phase 1 vs Phase 2 비교

| 차원 | Phase 1 | Phase 2 (BP-21) |
|------|---------|-----------------|
| **외부 시스템** | 실패 | **성공** |
| **내부 시스템** | 롤백 (일관) | 롤백 (**불일치**) |
| **복구 난이도** | 자동 | **수동 필요** |
| **금전적 영향** | 없음 | **실제 결제됨** |

### 트리거 조건

```python
# shopping/chaos/decorators.py

def inject_partial_failure_after_pg(payment_id: int, pg_response: dict) -> None:
    """
    BP-21: PG 성공 후 내부 실패 주입
    
    조건:
    1. CHAOS_PHASE2_MODE == "orphan_pg"
    2. 확률 또는 결정론적 트리거
    """
    if not chaos_config.is_chaos_active("partial_failure"):
        return
    
    if not chaos_config.should_trigger(chaos_config.partial_failure_probability):
        return
    
    # 포렌식 컨텍스트용 로깅
    logger.warning(
        f"[CHAOS] BP-21 triggered: payment_id={payment_id}, "
        f"pg_response_status={pg_response.get('status')}"
    )
    
    raise PartialFailureException(
        "payment_confirm_post_pg",
        "[CHAOS] Simulated internal failure after PG success"
    )
```

### 예상 결과 상태

| 시스템 | 상태 | 문제 |
|--------|------|------|
| Toss (PG) | `status=DONE`, 결제 완료 | 환불 필요 |
| Payment (DB) | `is_paid=False`, `status=ready` | 불일치 |
| Order (DB) | `status=confirmed` | stuck |
| DLQ | ??? | **검증 대상** |

### 현재 코드의 문제점

```python
# 현재 구현
except PartialFailureException as e:
    logger.error(f"[CHAOS] Partial failure injected")
    raise PaymentConfirmError(str(e))  # ← DLQ 호출 없음!
```

**문제**: `PaymentConfirmError`가 발생해도 DLQ에 저장되지 않음.

**필요한 것**:
```python
except PartialFailureException as e:
    # DLQ 저장 필요
    DLQService().store_failure(
        domain="payment",
        failure_type="PARTIAL_FAILURE_POST_PG",
        forensic_context={
            "pg_response": pg_response,
            "payment_id": payment.id,
        }
    )
    raise PaymentConfirmError(str(e))
```

### 검증 포인트

```python
# 테스트 검증
def verify_bp21():
    # 1. Chaos 모드 활성화 후 결제 시도
    response = client.post("/api/payments/confirm/", ...)
    assert response.status_code in [400, 500]
    
    # 2. DLQ 엔트리 확인
    failed_op = FailedOperation.objects.filter(
        failure_type="PARTIAL_FAILURE_POST_PG"
    ).first()
    
    assert failed_op is not None, "DLQ entry should be created"
    assert "pg_response" in failed_op.forensic_context
    
    # 3. Payment 상태 확인
    payment = Payment.objects.get(id=payment_id)
    assert payment.is_paid == False, "DB should be rolled back"
```

---

## BP-22: 롤백의 롤백 실패 (Rollback Failure)

### 코드 위치

**파일**: `shopping/tasks/payment_tasks.py`

```python
@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.rollback_payment_failure",
    max_retries=3,
    ...
)
def rollback_payment_failure(self, order_id: int, fail_reason: str = "") -> dict:
    logger.info(f"결제 실패 롤백 시작: order_id={order_id}")

    # [BP-22 주입 지점] - 롤백 시작 직후
    try:
        inject_rollback_failure(order_id=order_id)
    except Exception as e:
        logger.error(f"[CHAOS] Rollback failure injected: order_id={order_id}")
        raise self.retry(exc=e, countdown=3)  # ← 재시도 후 소진되면?
    
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            
            # 2. 재고 복구
            for item in order.order_items.select_for_update():
                if item.product:
                    Product.objects.filter(pk=item.product.pk).update(
                        stock=F("stock") + item.quantity
                    )
            # ...
```

### 파괴 메커니즘

```
[정상 흐름]
결제 실패 → rollback_payment_failure.delay()
                    ↓
              재고 복구 성공
                    ↓
              포인트 환불 성공
                    ↓
              Order.status = "payment_failed"

[BP-22 파괴 흐름: 1차 실패]
결제 실패 → rollback_payment_failure.delay()
                    ↓
              [CHAOS] 예외 발생
                    ↓
              Celery 재시도 (countdown=3)
                    ↓
              [CHAOS] 예외 발생 (2회차)
                    ↓
              Celery 재시도 (countdown=3)
                    ↓
              [CHAOS] 예외 발생 (3회차)
                    ↓
              MaxRetriesExceededError
                    ↓
              ??? DLQ 저장 ???
```

### 이중 실패 시나리오

```
┌─────────────────────────────────────────────────────────────────────┐
│                      1차 실패: 결제 승인                              │
│  confirm_payment → Toss Timeout → rollback_payment_failure.delay()  │
└─────────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      2차 실패: 롤백                                   │
│  rollback_payment_failure → 재고 복구 중 예외 → 재시도 소진          │
└─────────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────────┐
│                      결과: 고아 상태                                  │
│  Order.status = "confirmed" (stuck)                                  │
│  Product.stock = 원래 값 - 주문량 (미복구)                            │
│  User.points = 원래 값 - 사용량 (미환불)                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 트리거 조건

```python
# shopping/chaos/decorators.py

def inject_rollback_failure(order_id: int) -> None:
    """
    BP-22: 롤백 실패 주입
    
    조건:
    1. CHAOS_PHASE2_MODE == "rollback_fail"
    2. 특정 order_id 패턴
    """
    if os.getenv("CHAOS_PHASE2_MODE") != "rollback_fail":
        return
    
    # 100% 확률로 실패 (테스트 시)
    logger.warning(f"[CHAOS] BP-22 triggered: order_id={order_id}")
    raise Exception("[CHAOS] Simulated rollback failure")
```

### 현재 코드의 문제점

```python
# 현재 구현
def rollback_payment_failure(self, order_id: int, fail_reason: str = "") -> dict:
    try:
        inject_rollback_failure(order_id=order_id)
    except Exception as e:
        raise self.retry(exc=e, countdown=3)
    # ...
```

**문제**: `self.retry()` 소진 후 `MaxRetriesExceededError`가 발생하면:
- Celery는 태스크를 FAILURE로 마킹
- **DLQ 저장 코드 없음**

**필요한 것**:
```python
@rollback_payment_failure.on_failure.connect
def on_rollback_failure(sender, task_id, exception, args, kwargs, **kw):
    """롤백 태스크 최종 실패 시 DLQ 저장"""
    order_id = args[0] if args else kwargs.get("order_id")
    DLQService().store_failure(
        domain="inventory",
        failure_type="ROLLBACK_FAILURE",
        order_id=order_id,
        error_message=str(exception),
    )
```

### 예상 결과 상태

| 시스템 | 상태 | 문제 |
|--------|------|------|
| Order | `status=confirmed` | stuck |
| Product.stock | 미복구 | 재고 불일치 |
| User.points | 미환불 | 포인트 손실 |
| DLQ | ??? | **검증 대상** |

### 검증 포인트

```python
def verify_bp22():
    # 1. 결제 실패 유도 (BP-21 또는 타임아웃)
    # 2. 롤백 태스크 실패 주입
    # 3. Celery 로그에서 MaxRetriesExceeded 확인
    
    # 4. DLQ 확인
    failed_op = FailedOperation.objects.filter(
        domain="inventory",
        failure_type="ROLLBACK_FAILURE"
    ).first()
    
    assert failed_op is not None, "DLQ entry for rollback failure should exist"
    
    # 5. 상태 불일치 확인
    order = Order.objects.get(id=order_id)
    assert order.status in ["confirmed", "pending"], "Order stuck in non-final state"
```

---

## BP-23: Celery 태스크 무음 실패 (Silent Task Failure)

### 코드 위치

**파일**: `shopping/tasks/payment_tasks.py`

```python
@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.finalize_payment_confirm",
    max_retries=5,
    ...
)
def finalize_payment_confirm(self, toss_response: dict, payment_id: int, user_id: int) -> dict:
    logger.info(f"결제 최종 처리 시작: payment_id={payment_id}")

    # [BP-23 주입 지점]
    try:
        inject_async_task_failure(
            task_name="finalize_payment_confirm",
            task_id=self.request.id if self.request else None
        )
    except AsyncTaskChaosException as e:
        logger.error(f"[CHAOS] Async task failure injected: payment_id={payment_id}")
        raise self.retry(exc=e, countdown=5)  # ← 5회 재시도 후?
```

### 파괴 메커니즘

```
[Celery 재시도 흐름]
call_toss_confirm_api (성공) → finalize_payment_confirm
                                        ↓
                                [CHAOS] 예외 발생
                                        ↓
                                self.retry(countdown=5) [1/5]
                                        ↓
                                [CHAOS] 예외 발생
                                        ↓
                                self.retry(countdown=5) [2/5]
                                        ↓
                                ... 3, 4, 5 ...
                                        ↓
                                MaxRetriesExceededError
                                        ↓
                                Celery: task state = FAILURE
                                        ↓
                                ??? 아무 일도 안 일어남 ???
```

### Phase 1 vs Phase 2

| 차원 | Phase 1 | Phase 2 (BP-23) |
|------|---------|-----------------|
| **실패 위치** | 동기 함수 | 비동기 태스크 |
| **예외 처리** | try-except | Celery 재시도 |
| **DLQ 라우팅** | 명시적 | **암묵적 (누락 가능)** |
| **상태 가시성** | 즉시 | 지연/숨김 |

### 현재 코드의 문제점

```python
# finalize_payment_confirm 끝부분
    except Exception as e:
        logger.error(f"결제 최종 처리 실패: payment_id={payment_id}, error={str(e)}")
        raise self.retry(exc=e)  # ← 재시도 후 소진되면 무음 실패
```

**Celery 기본 동작**:
- `MaxRetriesExceededError` 발생
- 태스크 상태 → FAILURE
- **아무 콜백도 없으면 무음 소멸**

### 필요한 구현

```python
# 옵션 1: on_failure 시그널
@finalize_payment_confirm.on_failure.connect
def on_finalize_failure(sender, task_id, exception, args, kwargs, einfo, **kw):
    """
    태스크 최종 실패 시 DLQ 저장
    
    args: (toss_response, payment_id, user_id)
    """
    toss_response = args[0] if len(args) > 0 else {}
    payment_id = args[1] if len(args) > 1 else kwargs.get("payment_id")
    
    DLQService().store_failure(
        domain="payment",
        failure_type="ASYNC_TASK_FAILURE",
        payment_id=payment_id,
        forensic_context={
            "task_id": task_id,
            "task_name": "finalize_payment_confirm",
            "toss_response": toss_response,
            "exception": str(exception),
        }
    )
    
    # 롤백 태스크 트리거
    rollback_payment_failure.delay(
        order_id=Payment.objects.get(pk=payment_id).order_id,
        fail_reason=f"Task failure after {sender.max_retries} retries"
    )
```

### 예상 결과 상태

| 시스템 | 상태 | 문제 |
|--------|------|------|
| Toss (PG) | 결제 성공 | 환불 필요 |
| Payment (DB) | `status=in_progress` | stuck |
| Order (DB) | `status=confirmed` | stuck |
| Celery | task FAILURE | 로그만 남음 |
| DLQ | **없음** | 데이터 손실 |

### 검증 포인트

```python
def verify_bp23():
    # 1. 비동기 결제 시작
    response = client.post("/api/payments/confirm/async/", ...)
    assert response.json()["status"] == "processing"
    
    # 2. Chaos 모드로 태스크 실패 주입
    # 3. 5회 재시도 대기 (약 25초 + 백오프)
    
    # 4. DLQ 확인 (이게 핵심 검증)
    failed_op = FailedOperation.objects.filter(
        failure_type="ASYNC_TASK_FAILURE"
    ).first()
    
    assert failed_op is not None, "DLQ entry should exist after task exhaustion"
    assert failed_op.forensic_context.get("task_id") is not None
    
    # 5. 롤백 트리거 확인
    order = Order.objects.get(...)
    assert order.status in ["payment_failed", "canceled"]
```

---

## BP-24: 웹훅 순서 역전 (Webhook Order Inversion)

### 코드 위치

**파일**: `shopping/webhooks/toss_webhook_view.py`

```python
@api_view(["POST"])
def toss_webhook(request: Request) -> Response:
    # 1. 서명 검증
    # 2. 데이터 파싱
    
    event_type = serializer.validated_data["eventType"]
    event_data = serializer.validated_data["data"]

    # 3. 이벤트 핸들러 디스패치
    handler = EVENT_HANDLERS.get(event_type)
    # ...
```

**파일**: `shopping/services/toss_webhook_service.py`

```python
@staticmethod
@transaction.atomic
def handle_payment_done(event_data: dict[str, Any]) -> None:
    """결제 완료 이벤트 처리"""
    order_id = event_data.get("orderId")

    # 1. Redis 중복 체크
    if TossWebhookService.is_webhook_duplicate(order_id, "PAYMENT.DONE"):
        return

    # 2. Payment 조회
    try:
        payment = Payment.objects.select_for_update().get(toss_order_id=order_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment not found for order_id: {order_id}")
        return  # ← 무시됨

    # 3. 이미 처리된 결제 확인
    if payment.is_paid:
        return

    # 4. 최종 상태 보호
    if payment.status in ["canceled", "aborted"]:
        logger.info(f"Payment in final state {payment.status}, ignoring DONE event")
        return  # ← 이 경로!
```

### 파괴 메커니즘

```
[정상 흐름]
PG → PAYMENT.DONE 웹훅 → handle_payment_done() → Payment.is_paid = True
            ↓
(사용자 취소)
            ↓
PG → PAYMENT.CANCELED 웹훅 → handle_payment_canceled() → 환불 처리

[BP-24 파괴 흐름: 순서 역전]
PG → PAYMENT.CANCELED 웹훅 (먼저 도착)
            ↓
handle_payment_canceled()
            ↓
Payment.DoesNotExist? 또는 status 불일치?
            ↓
(500ms 후)
            ↓
PG → PAYMENT.DONE 웹훅 (나중 도착)
            ↓
handle_payment_done()
            ↓
payment.status == "canceled" → 무시 (정상 방어)
```

### 시나리오별 분석

**시나리오 A: CANCELED 먼저, Payment 없음**
```
PAYMENT.CANCELED 도착 → Payment.DoesNotExist → 무시 (정상)
PAYMENT.DONE 도착 → Payment 생성/처리 (정상)
```

**시나리오 B: CANCELED 먼저, Payment 있음**
```
PAYMENT.CANCELED 도착 → Payment.status != "done" → 취소 불가? → 에러 또는 무시
PAYMENT.DONE 도착 → Payment 처리 → is_paid = True
                   → 이미 CANCELED 왔으니 상태 불일치?
```

**시나리오 C: 동시 도착**
```
PAYMENT.DONE   ─────────────────────────────→
PAYMENT.CANCELED ─────────────────────────────→
                    ↓ (락 경쟁)
              하나만 성공, 다른 하나는 대기 후 상태 확인
```

### 트리거 방법

```bash
# 웹훅 시뮬레이터로 순서 역전 테스트
# 1. CANCELED 먼저
curl -X POST http://localhost:8000/api/webhooks/toss/ \
  -H "Content-Type: application/json" \
  -H "X-Toss-Webhook-Signature: test_sig" \
  -d '{"eventType": "PAYMENT.CANCELED", "data": {"orderId": "ORDER_123"}}'

# 2. 100ms 후 DONE
sleep 0.1
curl -X POST http://localhost:8000/api/webhooks/toss/ \
  -H "Content-Type: application/json" \
  -H "X-Toss-Webhook-Signature: test_sig" \
  -d '{"eventType": "PAYMENT.DONE", "data": {"orderId": "ORDER_123"}}'
```

### 검증 포인트

```python
def verify_bp24():
    # 1. 결제 생성 (Payment 존재)
    # 2. CANCELED 웹훅 먼저 전송
    # 3. DONE 웹훅 전송
    
    # 4. 최종 상태 확인
    payment = Payment.objects.get(toss_order_id="ORDER_123")
    
    # 방어 성공: 일관된 상태
    assert payment.status in ["done", "canceled"], "Should be in final state"
    
    # 5. WebhookEvent 로그 확인
    events = WebhookEvent.objects.filter(order_id="ORDER_123").order_by("processed_at")
    assert events.count() == 2, "Both events should be logged"
```

---

## BP-25: 멱등성 TTL 경계 공격 (Idempotency TTL Boundary)

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
# Redis TTL 상수
IDEMPOTENCY_KEY_TTL = 60  # 60초

@staticmethod
def _check_idempotency_key(key: str) -> Payment | None:
    """Redis에서 멱등성 키 확인 (TTL 60초)"""
    if not key:
        return None

    cache_key = PaymentService._get_idempotency_cache_key(key)
    payment_id = cache.get(cache_key)  # ← TTL 만료 시 None

    if payment_id:
        try:
            return Payment.objects.get(pk=payment_id)
        except Payment.DoesNotExist:
            cache.delete(cache_key)

    return None

@staticmethod
def _set_idempotency_key(key: str, payment_id: int) -> None:
    """Redis에 멱등성 키 저장 (TTL 60초)"""
    if key:
        cache_key = PaymentService._get_idempotency_cache_key(key)
        cache.set(cache_key, payment_id, timeout=IDEMPOTENCY_KEY_TTL)
```

### 파괴 메커니즘

```
[정상 흐름: TTL 내 중복 요청]
t=0s:  요청 A → _check_idempotency_key → None
                     ↓
              _set_idempotency_key(TTL=60s)
                     ↓
              처리 중...

t=5s:  요청 B (동일 키) → _check_idempotency_key → Payment 반환
                     ↓
              기존 Payment 반환 (중복 방지 ✓)

[BP-25 파괴 흐름: TTL 경계 공격]
t=0s:  요청 A → _check_idempotency_key → None
                     ↓
              _set_idempotency_key(TTL=60s)
                     ↓
              [CHAOS] 65초 지연 주입
                     ↓

t=61s: (TTL 만료)
                     ↓
t=62s: 요청 B (동일 키) → _check_idempotency_key → None (TTL 만료!)
                     ↓
              중복 Payment 생성 시도
                     ↓
              select_for_update() 락 경쟁
                     ↓
              ??? 2차 방어 ???
```

### 2차 방어 메커니즘 분석

```python
@staticmethod
@transaction.atomic
def create_payment(order: Order, payment_method: str = "card", idempotency_key: str | None = None) -> Payment:
    # 1차 방어: Redis TTL (실패)
    existing_payment = PaymentService._check_idempotency_key(idempotency_key)
    if existing_payment:
        return existing_payment

    # 2차 방어: DB 락
    Order.objects.select_for_update().get(pk=order.pk)

    # 기존 Payment 삭제 (재시도 케이스)
    existing_count = Payment.objects.filter(order=order).count()
    if existing_count > 0:
        Payment.objects.filter(order=order).delete()  # ← 덮어쓰기!

    # 새 Payment 생성
    payment = Payment.objects.create(...)
```

**문제**: 2차 방어가 `delete()` 후 `create()`로 구현됨.
- TTL 경계에서 두 요청이 거의 동시에 들어오면?
- 첫 번째 Payment가 삭제되고 두 번째가 생성될 수 있음

### 트리거 조건

```python
def inject_ttl_boundary_delay():
    """
    BP-25: TTL 경계 공격용 지연
    
    멱등성 키 저장 후 65초 지연하여 TTL 만료 유도
    """
    if os.getenv("CHAOS_PHASE2_MODE") != "idempotency_ttl":
        return
    
    delay = float(os.getenv("CHAOS_TTL_DELAY_SECONDS", "65"))
    logger.warning(f"[CHAOS] BP-25: Injecting {delay}s delay for TTL boundary attack")
    time.sleep(delay)
```

### 검증 포인트

```python
def verify_bp25():
    # 1. 첫 번째 요청 (지연 주입)
    # 2. 65초 대기 (또는 Redis TTL 강제 만료)
    # 3. 두 번째 동일 키 요청
    
    # 4. 결과 확인
    payments = Payment.objects.filter(order=order)
    
    # 정상: 하나만 존재
    assert payments.count() == 1, "Should have exactly one payment"
    
    # 비정상 탐지
    if payments.count() > 1:
        # DLQ에 IdempotencyViolation 기록되어야 함
        failed_op = FailedOperation.objects.filter(
            failure_type="IDEMPOTENCY_VIOLATION"
        ).first()
        assert failed_op is not None
```

---

**다음 문서**: [BREAKPOINT_DETAILS_2.md](./BREAKPOINT_DETAILS_2.md)
