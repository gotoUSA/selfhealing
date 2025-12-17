# Breakpoint 상세 분석: BP-1 ~ BP-5

> **문서 목적**: 각 Breakpoint의 구체적인 코드 위치, 실패 메커니즘, 예상 영향을 상세히 기술한다.

---

## BP-1: 결제 승인 중 지연/타임아웃

### 코드 위치

**파일**: `shopping/utils/toss_payment.py` 또는 `shopping/services/payment_service.py`

```python
# shopping/utils/toss_payment.py (예상 구조)
class TossPaymentClient:
    def confirm_payment(self, payment_key: str, order_id: str, amount: int) -> dict:
        """
        [BP-1 주입 지점]
        Toss API 호출 전/후에 조건부 지연 삽입 가능
        """
        # API 호출
        response = requests.post(
            f"{self.base_url}/v1/payments/confirm",
            json={...},
            headers={...},
            timeout=self.timeout,
        )
        return response.json()
```

### 실패 메커니즘

```
[정상 흐름]
Client → PaymentService.confirm_payment_sync() → TossPaymentClient.confirm_payment() → Toss API
                                                           ↓
                                                     200 OK (< 3초)
                                                           ↓
                                              Payment.mark_as_paid() → Order.status = 'paid'

[실패 흐름 - 타임아웃]
Client → PaymentService.confirm_payment_sync() → TossPaymentClient.confirm_payment() → [DELAY 25초+]
                                                           ↓
                                                  SoftTimeLimitExceeded
                                                           ↓
                                              rollback_payment_failure.delay() → Order.status = 'payment_failed'
                                                           ↓
                                              FailedOperation (DLQ) 생성
```

### 트리거 조건 예시

```python
def _should_inject_delay(self, order_id: int, amount: int) -> bool:
    """
    조건부 지연 결정
    
    트리거 조건:
    1. CHAOS_MODE 환경 변수 활성화
    2. 금액이 특정 범위 내 (예: 10,000원 ~ 50,000원)
    3. order_id % 10 == 0 (10% 확률)
    """
    if os.getenv("CHAOS_MODE") != "payment_delay":
        return False
    
    if not (10000 <= amount <= 50000):
        return False
    
    return order_id % 10 == 0

def _get_delay_duration(self) -> float:
    """
    지연 시간 결정 (soft_time_limit 초과하도록)
    
    payment_tasks.py의 soft_time_limit=25 기준
    """
    return float(os.getenv("CHAOS_DELAY_SECONDS", "30"))
```

### 예상 영향

| 영향 범위 | 상태 변화 |
|----------|----------|
| Payment | `in_progress` → `aborted` |
| Order | `confirmed` → `payment_failed` |
| Product.stock | 복구됨 (rollback) |
| User.points | 환불됨 (used_points) |
| FailedOperation | 새 엔트리 생성 |
| Circuit Breaker | failure_count 증가, 임계값 초과 시 OPEN |

### 관찰 로그

```
ERROR shopping.tasks.payment_tasks: Toss API 호출 타임아웃: order_id=12345
INFO shopping.tasks.payment_tasks: 결제 실패 롤백 시작: order_id=12345, reason=결제 처리 시간 초과
INFO shopping.services.self_healing.dlq_service: [DLQService] Created DLQ entry: id=1, domain=payment, failure_type=PG_TIMEOUT
```

### 대상 테스트

- **stage4_cancel_storm**: 취소 전 결제 지연으로 race window 확대
- **stage5_rollback**: 타임아웃으로 인한 롤백 검증
- **stage10_self_healing**: Circuit Breaker 트리거

---

## BP-2: 결제 승인 중 금액 불일치

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
# 현재 구현 (Toss API가 금액 검증)
class TossPaymentClient:
    def confirm_payment(self, payment_key: str, order_id: str, amount: int) -> dict:
        # Toss가 서버에 저장된 금액과 요청 금액 비교
        # 불일치 시 400 에러 반환
```

**현재 테스트 방식** (stage5_rollback.py):
```python
# 의도적으로 잘못된 금액 전송
wrong_amount = int(final_amount) + 10000  # 금액 불일치

response = self.client.post(
    "/api/payments/confirm/",
    json={
        "payment_key": payment_key,
        "order_id": order_id,
        "amount": wrong_amount,  # 잘못된 금액
    },
)
```

### 실패 메커니즘

```
[실패 흐름]
Client → /api/payments/confirm/ (amount: 30000) 
                    ↓
         Order.final_amount = 20000
                    ↓
         금액 불일치 감지 (30000 ≠ 20000)
                    ↓
         HTTP 400 + PaymentConfirmError("금액이 일치하지 않습니다")
                    ↓
         트랜잭션 롤백 없음 (재고는 주문 생성 시 이미 차감됨)
```

### 핵심 검증 포인트

이 실패는 **롤백이 필요 없는 케이스**:
- 재고는 주문 생성 시 이미 차감됨
- 결제 confirm 실패는 재고에 영향 없음
- Order 상태는 `pending` 또는 `confirmed` 유지

Stage5가 검증하는 것:
```python
# 주문 생성 후 재고 스냅샷
stock_after_order = self.stock_validator.snapshot_stock(product_id)

# 결제 실패
# ...

# 재고 확인 - 변화 없어야 함
stock_after_payment_fail = self.stock_validator.get_stock(product_id)

# 검증: stock_after_order == stock_after_payment_fail
```

### 이 Breakpoint의 한계

현재 구현에서는 **시스템 내부에서 금액 불일치를 주입할 필요 없음**:
- 테스트 코드가 이미 잘못된 금액을 전송
- Toss API (또는 Mock)가 검증

**대안적 활용**:
- `final_amount` 계산 로직에 조건부 오류 주입
- 할인/포인트 적용 순서 오류 시뮬레이션

---

## BP-3: 동시 결제 승인 경쟁

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
@staticmethod
@transaction.atomic
def confirm_payment_sync(payment: Payment, payment_key: str, order_id: int, amount: int, user) -> dict:
    """
    [BP-3 주입 지점: 락 획득 전]
    """
    # === 현재 구현 ===
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    
    # 이미 처리된 결제인지 확인
    if payment.is_paid:
        raise PaymentConfirmError("이미 완료된 결제입니다.")
```

### 실패 메커니즘

```
[정상 흐름 - 동시성 보호됨]
Request A ─────────────────────────────────────────────────>
           └─ select_for_update() [락 획득] ─ 처리 ─ 커밋
                                                           
Request B ─────────────────────────────────────────────────>
                              └─ [락 대기] ──────────────── select_for_update()
                                                           └─ is_paid=True → 400

[BP-3 주입 시 - race window 확대]
Request A ─────────────────────────────────────────────────────────>
           └─ [DELAY] ─ select_for_update() [락 획득] ─ 처리 ─ 커밋

Request B ─────────────────────────────────────────────────────────>
           └─ [DELAY] ─ select_for_update() [락 대기 중 A 완료] ────────>
                                                           └─ is_paid=True → 400

[문제 시나리오 - 락 없이 조회 후 지연]
Request A ─────────────────────────────────────────────────────────>
           └─ payment.is_paid 체크 [False] ─ [DELAY] ─ Toss API ─ mark_as_paid

Request B ─────────────────────────────────────────────────────────>
           └─ payment.is_paid 체크 [False] ─ [DELAY] ─ Toss API ─ mark_as_paid
                                                           └─ ⚠️ 중복 결제!
```

### 트리거 조건 예시

```python
def _should_inject_race_delay(self) -> bool:
    """
    Race condition 테스트를 위한 지연 주입
    
    조건:
    1. CHAOS_MODE == "race_condition"
    2. 현재 활성 트랜잭션 수 > 1 (추정)
    """
    if os.getenv("CHAOS_MODE") != "race_condition":
        return False
    
    # 락 대기 큐 깊이 추정 (Redis 등으로 구현)
    # 또는 단순히 확률적으로
    return random.random() < float(os.getenv("CHAOS_RACE_PROBABILITY", "0.3"))

def _get_race_delay(self) -> float:
    """락 획득 전 대기 시간"""
    return float(os.getenv("CHAOS_RACE_DELAY_MS", "500")) / 1000
```

### 구현 위치 옵션

**옵션 1: 락 획득 전 지연**
```python
@transaction.atomic
def confirm_payment_sync(...):
    # [BP-3] 락 획득 전 지연
    if self._should_inject_race_delay():
        time.sleep(self._get_race_delay())
    
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
```

**옵션 2: 멱등성 체크 후 지연**
```python
@transaction.atomic
def confirm_payment_sync(...):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    
    # [BP-3-ALT] 상태 체크 후 지연 (더 위험)
    if payment.is_paid:
        raise PaymentConfirmError(...)
    
    if self._should_inject_race_delay():
        time.sleep(self._get_race_delay())
    
    # Toss API 호출
```

### 예상 결과

| 시나리오 | 결과 |
|----------|------|
| 정상 (락 작동) | 첫 요청 성공, 나머지 400 |
| BP-3 주입 (락 작동) | 동일 (지연만 추가) |
| 락 비활성화 시 | ⚠️ 중복 결제 발생 가능 |

### 검증 포인트

Stage7에서 확인하는 것:
```python
# 동일 주문에 2번 이상 성공하면 심각한 문제!
if prev_success >= 1:
    _race_stats["double_success"] += 1
    response.failure(f"🚨 CRITICAL: Multiple payments on order {order_id}!")
```

---

## BP-4: 취소 중 동시성 충돌

### 코드 위치

**파일**: `shopping/services/payment_service.py`

```python
@staticmethod
@transaction.atomic
def cancel_payment(payment_id: int, user, cancel_reason: str) -> dict:
    """
    [BP-4 주입 지점: 상태 확인 후]
    """
    # 1. Payment 락 획득
    payment = Payment.objects.select_for_update().get(id=payment_id, order__user=user)
    
    # 2. 중복 취소 방지
    if payment.is_canceled:
        raise PaymentCancelError("이미 취소된 결제입니다.")
    
    # [BP-4 주입 지점]
    # 여기서 지연 시 confirm이 끼어들 여지 (단, payment 락 보유 중)
    
    # 3. 취소 가능한 상태인지 확인
    if payment.status != "done":
        raise PaymentCancelError(f"취소할 수 없는 결제 상태입니다: {payment.get_status_display()}")
```

### 실패 메커니즘

```
[Cancel Storm 시나리오]
Confirm 완료 ──────────────────────────────────────────────>
             └─ Payment.status = 'done'
                                    │
                                    v
Cancel 요청 1 ──────────────────────────────────────────────>
              └─ select_for_update() [락] ─ is_canceled=False
                                           └─ [BP-4 DELAY]
                                                          └─ Toss 취소 ─ mark_as_canceled

Cancel 요청 2 ─────────────────────────────────────────────────────>
                               └─ [락 대기] ─────────────────────────>
                                                           └─ is_canceled=True → 400
```

### 핵심 검증 포인트

Stage4 Cancel Storm이 테스트하는 것:
1. **결제 직후 취소**: confirm 후 0.1~1초 내 cancel
2. **연속 취소 시도**: 같은 결제에 3번 연속 취소
3. **재고 복구**: 취소 성공 시 stock 증가

```python
# 취소 성공 시 재고 복구
for order_item in order.order_items.all():
    Product.objects.filter(pk=product.pk).update(
        stock=F("stock") + order_item.quantity,
        sold_count=Greatest(F("sold_count") - order_item.quantity, 0),
    )
```

### 잠재적 문제 지점

현재 구현은 잘 보호되어 있음:
- Payment에 `select_for_update()` 락
- Order에도 별도 락

**문제 발생 가능 시나리오**:
```python
# 만약 락 없이 구현되었다면
def cancel_payment_unsafe(payment_id, user, cancel_reason):
    payment = Payment.objects.get(id=payment_id)  # 락 없음
    
    if payment.is_canceled:
        raise PaymentCancelError(...)
    
    # [RACE WINDOW] 여기서 다른 요청이 취소 완료 가능
    
    toss_client.cancel_payment(...)  # Toss 취소 2번 호출됨
```

---

## BP-5: 재고 차감 중 부분 실패

### 코드 위치

**파일**: `shopping/services/order_service.py`

```python
@staticmethod
def _create_order_items_and_decrease_stock(order: Order, cart: Cart) -> None:
    """
    [BP-5 주입 지점: 반복문 내부]
    """
    for cart_item in cart.items.all():
        # 재고 최종 확인 (select_for_update로 동시성 제어)
        product = Product.objects.select_for_update().get(pk=cart_item.product.pk)

        if product.stock < cart_item.quantity:
            raise OrderServiceError(f"{product.name}의 재고가 부족합니다.")

        # [BP-5 주입 지점]
        # N번째 상품에서 의도적 실패
        
        # F() 객체를 사용한 안전한 재고 차감
        Product.objects.filter(pk=product.pk).update(stock=F("stock") - cart_item.quantity)
```

### 실패 메커니즘

```
[부분 실패 시나리오]
장바구니: [상품A x 1, 상품B x 2, 상품C x 1]

주문 생성 ──────────────────────────────────────────────────>
          └─ 상품A 재고 차감 ✓
                    └─ 상품B 재고 차감 ✓
                              └─ 상품C 재고 차감 [BP-5 실패]
                                        │
                                        v
                              @transaction.atomic 롤백
                                        │
                                        v
                              상품A, 상품B 재고 복구
```

### 트리거 조건 예시

```python
def _should_inject_partial_failure(self, item_index: int, product_id: int) -> bool:
    """
    부분 실패 주입 조건
    
    트리거:
    1. CHAOS_MODE == "partial_failure"
    2. 장바구니 내 N번째 상품 (N > 0)
    3. 특정 상품 ID 범위
    """
    if os.getenv("CHAOS_MODE") != "partial_failure":
        return False
    
    # 첫 번째 상품은 통과, 두 번째 이후에서만 실패
    failure_index = int(os.getenv("CHAOS_PARTIAL_FAILURE_INDEX", "1"))
    
    return item_index >= failure_index
```

### 구현 예시

```python
@staticmethod
def _create_order_items_and_decrease_stock(order: Order, cart: Cart) -> None:
    cart_items = list(cart.items.all())
    
    for idx, cart_item in enumerate(cart_items):
        product = Product.objects.select_for_update().get(pk=cart_item.product.pk)

        if product.stock < cart_item.quantity:
            raise OrderServiceError(f"{product.name}의 재고가 부족합니다.")

        # [BP-5] 부분 실패 주입
        if os.getenv("CHAOS_MODE") == "partial_failure":
            failure_idx = int(os.getenv("CHAOS_PARTIAL_FAILURE_INDEX", "1"))
            if idx >= failure_idx and len(cart_items) > 1:
                logger.warning(f"[CHAOS] Injecting partial failure at item {idx}")
                raise OrderServiceError(f"[CHAOS] Injected partial failure for {product.name}")

        Product.objects.filter(pk=product.pk).update(stock=F("stock") - cart_item.quantity)
```

### 예상 영향

| 영향 범위 | 상태 변화 |
|----------|----------|
| Order | 생성되지 않음 (롤백) |
| Product.stock | 모든 상품 원복 |
| Cart | 유지 (비우기 전 실패) |
| User.points | 차감 안 됨 |

### 검증 포인트

Stage5에서 확인하는 것:
- 부분 실패 시 **전체 트랜잭션 롤백**
- 어떤 상품도 재고 차감 없음
- 포인트 차감 없음

```python
# 트랜잭션 atomic 보장 검증
stock_before_all = {p.id: p.stock for p in products}

try:
    order = OrderService.create_order_from_cart(...)
except OrderServiceError:
    pass

stock_after_all = {p.id: p.stock for p in products}

# 모든 상품 재고 동일해야 함
assert stock_before_all == stock_after_all
```

---

> **다음 문서**: [BP-6 ~ BP-9 상세 분석](./BREAKPOINT_DETAILS_2.md)
