# Breakpoint 상세 분석: BP-6 ~ BP-9

> **문서 목적**: 포인트 경쟁, Celery 태스크 실패, 멱등성 키 경쟁, Circuit Breaker 경쟁에 대한 상세 분석

---

## BP-6: 포인트 사용 중 잔액 부족 경쟁

### 코드 위치

**파일**: `shopping/services/point_service.py`

```python
@staticmethod
@transaction.atomic
def use_points(
    user: AbstractBaseUser,
    amount: int,
    type: str = "use",
    order: Optional[Order] = None,
    description: str = "",
    metadata: Optional[dict] = None,
) -> bool:
    """
    포인트 차감 (동시성 제어 포함)
    
    [BP-6 주입 지점: 락 획득 전]
    """
    if amount <= 0:
        return False

    # 동시성 제어: select_for_update로 락 획득
    locked_user = User.objects.select_for_update().get(pk=user.pk)

    if locked_user.points < amount:
        logger.warning(f"포인트 부족: user_id={user.id}, required={amount}, available={locked_user.points}")
        return False

    # F() 객체로 안전하게 차감
    User.objects.filter(pk=user.pk).update(points=F("points") - amount)
```

### 실패 메커니즘

```
[포인트 경쟁 시나리오]
User A (포인트: 10,000)

주문 1 (포인트 사용: 8,000) ─────────────────────────────────────>
                            └─ [BP-6 DELAY] ─ select_for_update()
                                              └─ 잔액 10,000 ≥ 8,000 ✓
                                                        └─ 차감 ─ 잔액 2,000

주문 2 (포인트 사용: 5,000) ─────────────────────────────────────────>
                            └─ [BP-6 DELAY] ─ [락 대기] ─────────────────>
                                                         └─ select_for_update()
                                                                    └─ 잔액 2,000 < 5,000 ✗
                                                                              └─ False 반환
```

### FIFO 방식 포인트 사용

**파일**: `shopping/services/point_service.py`

```python
def use_points_fifo(
    self,
    user: AbstractBaseUser,
    amount: int,
    type: str = "use",
    order: Optional[Order] = None,
    description: str = "",
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """
    FIFO 방식 포인트 사용 (만료일 가까운 것부터 사용)
    
    [BP-6 주입 지점: 락 획득 전 또는 포인트 이력 조회 전]
    """
```

### 트리거 조건 예시

```python
def _should_inject_point_race(self) -> bool:
    """
    포인트 경쟁 테스트를 위한 지연 주입
    
    조건:
    1. CHAOS_MODE == "point_race"
    2. 동일 사용자의 동시 요청 감지 (추정)
    """
    if os.getenv("CHAOS_MODE") != "point_race":
        return False
    
    # Redis를 통한 동시 요청 추적
    # user_id별 활성 요청 수 확인
    return random.random() < float(os.getenv("CHAOS_POINT_RACE_PROBABILITY", "0.2"))
```

### 구현 예시

```python
@transaction.atomic
def use_points(user, amount, ...):
    if amount <= 0:
        return False

    # [BP-6] 락 획득 전 지연
    if os.getenv("CHAOS_MODE") == "point_race":
        delay = float(os.getenv("CHAOS_POINT_RACE_DELAY_MS", "200")) / 1000
        if random.random() < 0.3:  # 30% 확률
            logger.debug(f"[CHAOS] Injecting point race delay: {delay}s")
            time.sleep(delay)

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    # ...
```

### 예상 영향

| 시나리오 | 결과 |
|----------|------|
| 동시 주문 A, B (각 5,000P, 잔액 8,000P) | A 성공, B 실패 |
| 동시 주문 A, B (각 3,000P, 잔액 8,000P) | 둘 다 성공 (순차 처리) |
| BP-6 지연 + 락 작동 | 동일 결과 (지연만 추가) |
| 락 비활성화 시 | ⚠️ 음수 포인트 가능 |

### 검증 포인트

Stage5/Stage7에서 확인:
```python
# 포인트 부족 시 주문 실패 → 재고 복구
if not result["success"]:
    # 이미 차감된 재고 복구
    for item in order.order_items.all():
        Product.objects.filter(pk=item.product.pk).update(
            stock=F("stock") + item.quantity
        )
    
    order.status = "failed"
```

---

## BP-7: Celery 태스크 중 예외

### 코드 위치

**파일**: `shopping/tasks/payment_tasks.py`

```python
@shared_task(
    bind=True,
    name="shopping.tasks.payment_tasks.finalize_payment_confirm",
    queue="payment_critical",
    max_retries=5,
    acks_late=True,
)
def finalize_payment_confirm(self, toss_response: dict, payment_id: int, user_id: int) -> dict:
    """
    Toss API 결과를 받아 결제 최종 처리
    
    [BP-7 주입 지점: Toss 성공 후, DB 업데이트 전]
    """
    try:
        with transaction.atomic():
            # 1. Payment 업데이트
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            
            if payment.is_paid:
                return {"status": "already_processed"}
            
            payment.mark_as_paid(toss_response)
            order = payment.order

            # [BP-7 주입 지점]
            # Toss는 성공했지만 DB 업데이트 전 실패
            
            # 2. 재고 차감 (sold_count만)
            for order_item in order.order_items.select_for_update():
                if order_item.product:
                    Product.objects.filter(pk=order_item.product.pk).update(
                        sold_count=F("sold_count") + order_item.quantity
                    )
```

### 실패 메커니즘

```
[정상 흐름]
call_toss_confirm_api ─> Toss API 성공 ─> toss_response
                                              │
                                              v
finalize_payment_confirm ────────────────────────────────────────>
                         └─ Payment.mark_as_paid() ✓
                                   └─ sold_count 증가 ✓
                                            └─ Order.status = 'paid' ✓
                                                      └─ 포인트 적립 태스크 트리거

[BP-7 실패 흐름]
call_toss_confirm_api ─> Toss API 성공 ─> toss_response
                                              │
                                              v
finalize_payment_confirm ────────────────────────────────────────>
                         └─ Payment.mark_as_paid() ✓
                                   └─ [BP-7 EXCEPTION]
                                            │
                                            v
                                   트랜잭션 롤백
                                            │
                                            v
                         ⚠️ Toss는 결제됨, DB는 미처리
                                            │
                                            v
                         Celery 재시도 (max_retries=5)
                                            │
                                            v
                         재시도 성공 시: mark_as_paid() 다시 실행
                         재시도 실패 시: DLQ + detect_orphaned_orders
```

### 불일치 상태 위험

| Toss 상태 | Payment.status | Order.status | 위험 수준 |
|----------|----------------|--------------|----------|
| done | done | paid | 정상 |
| done | in_progress | confirmed | ⚠️ 중간 (재시도 대상) |
| done | in_progress | pending | ⚠️ 위험 (고아 주문) |
| done | ready | pending | 🚨 심각 (불일치) |

### 트리거 조건 예시

```python
def _should_inject_task_failure(self, payment_id: int) -> bool:
    """
    태스크 실패 주입 조건
    
    조건:
    1. CHAOS_MODE == "task_failure"
    2. payment_id % N == 0 (N번째 결제마다)
    """
    if os.getenv("CHAOS_MODE") != "task_failure":
        return False
    
    failure_modulo = int(os.getenv("CHAOS_TASK_FAILURE_MODULO", "10"))
    return payment_id % failure_modulo == 0

class ChaosTaskException(Exception):
    """테스트용 태스크 예외"""
    pass
```

### 구현 예시

```python
@shared_task(...)
def finalize_payment_confirm(self, toss_response, payment_id, user_id):
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            
            if payment.is_paid:
                return {"status": "already_processed"}
            
            payment.mark_as_paid(toss_response)
            order = payment.order

            # [BP-7] sold_count 업데이트 전 예외
            if os.getenv("CHAOS_MODE") == "task_failure":
                failure_modulo = int(os.getenv("CHAOS_TASK_FAILURE_MODULO", "10"))
                if payment_id % failure_modulo == 0:
                    logger.warning(f"[CHAOS] Injecting task failure for payment_id={payment_id}")
                    raise ChaosTaskException("Injected task failure for testing")

            # 정상 처리 계속
            for order_item in order.order_items.select_for_update():
                # ...
```

### 예상 영향

| 영향 범위 | 상태 변화 |
|----------|----------|
| Payment | `in_progress` 유지 (롤백됨) |
| Order | `confirmed` 유지 |
| Celery | 재시도 큐에 추가 |
| DLQ | 재시도 소진 시 엔트리 생성 |
| Circuit Breaker | failure_count 증가 |

### 검증 포인트

Stage5/Stage10에서 확인:
```python
# detect_orphaned_orders 태스크가 불일치 감지
def detect_orphaned_orders(threshold_minutes=10):
    """
    불일치 상태의 주문을 감지하고 자동으로 롤백
    
    불일치 상태: Order=confirmed + Payment=aborted
    """
    orphaned = Order.objects.filter(
        status="confirmed",
        payment__status="aborted",
        updated_at__lt=timezone.now() - timedelta(minutes=threshold_minutes)
    )
    
    for order in orphaned:
        rollback_payment_failure.delay(order.id, "Orphaned order detected")
```

---

## BP-8: Redis 멱등성 키 만료 직전 경쟁

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
# Redis TTL 상수
IDEMPOTENCY_KEY_TTL = 60  # 60초

class PaymentService:
    @staticmethod
    def _get_idempotency_cache_key(key: str) -> str:
        """멱등성 키의 Redis 캐시 키 생성"""
        return f"payment:idempotency:{key}"

    @staticmethod
    def _check_idempotency_key(key: str) -> Payment | None:
        """
        Redis에서 멱등성 키 확인 (TTL 60초)
        
        [BP-8 주입 지점: 캐시 조회 후]
        """
        if not key:
            return None

        cache_key = PaymentService._get_idempotency_cache_key(key)
        payment_id = cache.get(cache_key)

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

### 실패 메커니즘

```
[TTL 경계 경쟁 시나리오]
t=0s   : Request A - idempotency_key="abc123" 도착
t=0.1s : Request A - _check_idempotency_key() → None (캐시 없음)
t=0.2s : Request A - Payment 생성 시작

t=55s  : Request A - Toss API 응답 대기 중 (느린 응답)

t=60s  : TTL 만료! idempotency_key="abc123" Redis에서 삭제

t=61s  : Request B - idempotency_key="abc123" 도착 (클라이언트 재시도)
t=61.1s: Request B - _check_idempotency_key() → None (캐시 만료됨!)
t=61.2s: Request B - Payment 생성 시작 → ⚠️ 중복 Payment!

t=65s  : Request A - Toss API 성공, Payment 완료
t=66s  : Request B - Toss API 성공, Payment 완료 → 🚨 이중 결제!
```

### 현재 구현의 보호 메커니즘

```python
@staticmethod
@transaction.atomic
def create_payment(order: Order, payment_method: str = "card", idempotency_key: str | None = None):
    # 1. Redis 멱등성 키 확인
    existing_payment = PaymentService._check_idempotency_key(idempotency_key)
    if existing_payment:
        return existing_payment  # 기존 결제 반환

    # 2. Order 락으로 동시성 제어
    Order.objects.select_for_update().get(pk=order.pk)

    # 3. 기존 Payment 삭제 (재시도의 경우)
    Payment.objects.filter(order=order).delete()

    # 4. 새 Payment 생성
    payment = Payment.objects.create(...)
    
    # 5. Redis에 멱등성 키 저장
    PaymentService._set_idempotency_key(idempotency_key, payment.id)
```

**Order 락이 2차 방어선**:
- 같은 Order에 대한 중복 Payment 생성 방지
- `Order.objects.select_for_update()` + `Payment.objects.filter(order=order).delete()`

### 잠재적 문제 시나리오

**시나리오 1: 다른 Order로 같은 멱등성 키**
```
Request A: order_id=100, idempotency_key="abc"
Request B: order_id=200, idempotency_key="abc" (같은 키, 다른 주문)

현재 구현: Order 락은 order_id별이므로 다른 락
→ 같은 idempotency_key가 다른 Order에 사용될 수 있음
```

**시나리오 2: Payment.idempotency_key DB 필드 미사용**
```python
# Payment 모델에 idempotency_key 필드가 있지만 unique 제약 없음
idempotency_key = models.CharField(
    max_length=64,
    null=True,
    blank=True,
    db_index=True,  # 인덱스만 있고 unique 아님
)
```

### 트리거 조건 예시

```python
def _simulate_ttl_expiry(self, key: str) -> bool:
    """
    TTL 만료 시뮬레이션
    
    조건:
    1. CHAOS_MODE == "idempotency_ttl"
    2. 특정 확률로 캐시 강제 삭제
    """
    if os.getenv("CHAOS_MODE") != "idempotency_ttl":
        return False
    
    if random.random() < float(os.getenv("CHAOS_TTL_EXPIRY_PROBABILITY", "0.1")):
        cache_key = PaymentService._get_idempotency_cache_key(key)
        cache.delete(cache_key)
        logger.warning(f"[CHAOS] Simulated TTL expiry for key={key}")
        return True
    
    return False
```

### 예상 영향

| 시나리오 | 결과 |
|----------|------|
| TTL 내 재요청 | 기존 Payment 반환 (멱등) |
| TTL 만료 후 재요청 (같은 Order) | Order 락으로 방어, 기존 Payment 삭제 후 재생성 |
| TTL 만료 후 재요청 (다른 Order, 같은 키) | ⚠️ 잠재적 문제 |

### 검증 포인트

Stage7에서 확인:
```python
# 같은 주문에 대한 중복 결제 감지
for order_id, stats in _race_stats["shared_orders"].items():
    if stats["success"] > 1:
        # 같은 주문에 2건 이상 성공 = 심각한 문제
        _race_stats["double_success"] += stats["success"] - 1
```

---

## BP-9: Circuit Breaker 상태 전이 중 경쟁

### 코드 위치

**파일**: `packages/selfhealing-python/` 또는 `shopping/services/self_healing/circuit_breaker_service.py`

```python
# selfhealing 패키지에서 재내보내기
from selfhealing.services.circuit_breaker import (
    CircuitBreakerService,
    CircuitState,
    should_allow_request,
)
```

### 상태 머신

```
        ┌─────────────────────────────────────────────────┐
        │                                                 │
        v                                                 │
    ┌───────┐    failure_count > threshold    ┌───────┐  │
    │ CLOSED │ ─────────────────────────────> │ OPEN  │──┘
    └───────┘                                 └───────┘   recovery_timeout
        ^                                         │
        │                                         │
        │      success_count > threshold          v
        │   ┌────────────────────────────┐   ┌───────────┐
        └───│       reset to CLOSED      │<──│ HALF_OPEN │
            └────────────────────────────┘   └───────────┘
                                                  │
                                                  │ failure
                                                  v
                                             ┌───────┐
                                             │ OPEN  │
                                             └───────┘
```

### 실패 메커니즘

```
[Half-Open 상태 경쟁]
t=0: Circuit OPEN (recovery_timeout 대기 중)
t=30s: recovery_timeout 만료, 상태 → HALF_OPEN

Request A ─────────────────────────────────────────────>
           └─ should_allow_request() [HALF_OPEN]
                        └─ 허용됨 ─ 처리 중...

Request B ─────────────────────────────────────────────>
           └─ should_allow_request() [HALF_OPEN]
                        └─ 허용됨 ─ 처리 중...
                        (HALF_OPEN에서 여러 요청 허용 시)

Request A 성공 ─> record_success() ─> CLOSED로 전이

Request B 실패 ─> record_failure() ─> OPEN으로 전이?

⚠️ 최종 상태가 요청 완료 순서에 따라 달라짐
```

### 핵심 검증 포인트

Stage10에서 확인하는 Self-Healing API:
```python
# Control API 테스트
payload = {
    "service_name": service,
    "action": "allow",  # or "block", "reset", "inject_failure"
    "environment": "test",
    "reason": f"Load test - {request_id}",
}

response = self.client.post("/api/self-healing/control/", json=payload)
```

### 잠재적 문제 시나리오

**시나리오 1: 동시 control 요청**
```
Admin A: POST /control/ {"action": "block", "service": "payment"}
Admin B: POST /control/ {"action": "allow", "service": "payment"}

→ 최종 상태가 요청 순서/처리 순서에 따라 달라짐
→ 감사 로그와 실제 상태 불일치 가능
```

**시나리오 2: 상태 전이 중 요청**
```
t=0: Circuit OPEN
t=1: Admin이 reset 요청 (CLOSED로)
t=1.1: reset 처리 중 (상태 전이 중)
t=1.2: 사용자 요청 도착 → OPEN인가 CLOSED인가?
```

### 트리거 조건 예시

```python
def _should_inject_cb_race(self, service_name: str) -> bool:
    """
    Circuit Breaker 경쟁 테스트
    
    조건:
    1. CHAOS_MODE == "cb_race"
    2. 현재 상태가 HALF_OPEN
    """
    if os.getenv("CHAOS_MODE") != "cb_race":
        return False
    
    # 현재 상태 확인
    current_state = get_circuit_state(service_name)
    return current_state == CircuitState.HALF_OPEN
```

### 구현 예시 (상태 전이 지연)

```python
# Circuit Breaker 상태 변경 시 지연 추가
def transition_to(self, new_state: CircuitState):
    """
    상태 전이 (BP-9 주입 지점)
    """
    old_state = self.current_state
    
    # [BP-9] 상태 전이 지연
    if os.getenv("CHAOS_MODE") == "cb_race":
        delay = float(os.getenv("CHAOS_CB_TRANSITION_DELAY_MS", "100")) / 1000
        logger.debug(f"[CHAOS] Delaying CB transition by {delay}s")
        time.sleep(delay)
    
    self.current_state = new_state
    logger.info(f"Circuit breaker {self.service_name}: {old_state} → {new_state}")
```

### 예상 영향

| 시나리오 | 결과 |
|----------|------|
| HALF_OPEN + 동시 요청 | 여러 요청 허용 후 상태 결정 |
| 동시 control 요청 | 최종 상태 불확정 |
| reset 중 요청 | 일시적 불일치 |

### 검증 포인트

Stage10에서 확인:
```python
# Control API 응답 시간
if response_time > 2000:  # 2초 초과
    print("SLA Status: ✗ Exceeded 2s threshold")

# 거버넌스 규칙 검증
if response.status_code in [400, 403]:
    _self_healing_stats["governance_violations"]["inject_failure_in_ops"] += 1
    response.success()  # 정상적으로 거부됨
```

---

## 요약: Breakpoint 우선순위

| Breakpoint | 구현 복잡도 | 테스트 가치 | 권장 우선순위 |
|------------|------------|------------|--------------|
| BP-1 (결제 지연) | 낮음 | 높음 | ⭐⭐⭐⭐⭐ |
| BP-3 (동시 승인) | 낮음 | 높음 | ⭐⭐⭐⭐⭐ |
| BP-7 (태스크 예외) | 중간 | 높음 | ⭐⭐⭐⭐ |
| BP-4 (취소 충돌) | 낮음 | 중간 | ⭐⭐⭐⭐ |
| BP-5 (부분 실패) | 중간 | 중간 | ⭐⭐⭐ |
| BP-6 (포인트 경쟁) | 낮음 | 중간 | ⭐⭐⭐ |
| BP-2 (금액 불일치) | 낮음 | 낮음 | ⭐⭐ (이미 테스트 코드에서 처리) |
| BP-8 (TTL 경쟁) | 높음 | 중간 | ⭐⭐ |
| BP-9 (CB 경쟁) | 높음 | 중간 | ⭐⭐ |

---

> **작성일**: 2025-12-17  
> **버전**: 1.0.0
