# Phase 1: Intentional Failure Map

> **문서 목적**: Phase 1 테스트(stage4, stage5, stage7, stage10)에서 Self-Healing 레이어를 트리거하기 위해  
> 쇼핑 시스템의 **어디**를, **왜**, **어떻게** 의도적으로 실패시킬 수 있는지 매핑한다.

---

## 목차

1. [대상 테스트 스테이지 개요](#1-대상-테스트-스테이지-개요)
2. [비즈니스 로직 경계 분석](#2-비즈니스-로직-경계-분석)
3. [실패 주입 후보 지점](#3-실패-주입-후보-지점)
4. [스테이지별 매핑](#4-스테이지별-매핑)
5. [실패 신호 관찰 가이드](#5-실패-신호-관찰-가이드)
6. [구현 제약 조건](#6-구현-제약-조건)

---

## 1. 대상 테스트 스테이지 개요

| Stage | 파일 | 핵심 테스트 목표 |
|-------|------|-----------------|
| **stage4_cancel_storm** | `stage4_cancel_storm.py` | 결제 직후 취소 폭주, 동시 confirm+cancel 교차, 재고 복구 검증 |
| **stage5_rollback** | `stage5_rollback.py` | 결제 실패 시 재고/포인트 롤백 검증, 트랜잭션 정합성 |
| **stage7_race_conflict** | `stage7_race_conflict.py` | 동일 order_id에 대한 동시 결제 시도, 중복 결제 방지 검증 |
| **stage10_self_healing** | `stage10_self_healing.py` | Self-Healing Control API 기능/성능 검증, 거버넌스 규칙 검증 |

---

## 2. 비즈니스 로직 경계 분석

### 2.1 주문 생성 경계 (Order Creation Boundary)

| 파일 | 함수 | 공유 상태 |
|------|------|----------|
| `services/order_service.py` | `create_order_from_cart()` | `Order`, `OrderItem`, `Product.stock`, `User.points` |
| `services/order_service.py` | `_create_order_items_and_decrease_stock()` | `Product.stock` (F() 객체로 atomic 감소) |
| `services/order_service.py` | `_process_point_usage()` | `User.points`, `PointHistory` |

**트랜잭션 구조**:
```python
@transaction.atomic
def create_order_from_cart(user, cart, ...):
    cart = Cart.objects.select_for_update().get(pk=cart.pk)  # 장바구니 락
    # 1. Order 생성
    # 2. OrderItem 생성 + 재고 차감 (Product.select_for_update)
    # 3. 포인트 차감 (User.select_for_update)
    # 4. 장바구니 비우기
```

**동시성 제어**: 
- `Cart.select_for_update()` - 동일 사용자 중복 주문 방지
- `Product.select_for_update()` - 재고 oversell 방지

---

### 2.2 결제 생성/승인 경계 (Payment Creation/Confirmation Boundary)

| 파일 | 함수 | 공유 상태 |
|------|------|----------|
| `services/payment_service.py` | `create_payment()` | `Payment`, Redis 멱등성 키 |
| `services/payment_service.py` | `confirm_payment_sync()` | `Payment.status`, `Order.status`, `Product.sold_count` |
| `services/payment_service.py` | `confirm_payment_async()` | `Payment.status` → `in_progress` 전이 |

**트랜잭션 구조 (동기)**:
```python
@transaction.atomic
def confirm_payment_sync(payment, payment_key, order_id, amount, user):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)  # 결제 락
    # 1. 상태 검증 (is_paid, expired, canceled, aborted)
    # 2. Toss API 호출 (외부 의존)
    # 3. Payment.mark_as_paid()
    # 4. sold_count 증가 (Product.select_for_update)
    # 5. Order.status = 'paid'
    # 6. 포인트 적립
```

**멱등성 키**:
- Redis TTL 60초
- 캐시 키: `payment:idempotency:{key}`

---

### 2.3 결제 취소 경계 (Payment Cancellation Boundary)

| 파일 | 함수 | 공유 상태 |
|------|------|----------|
| `services/payment_service.py` | `cancel_payment()` | `Payment.is_canceled`, `Order.status`, `Product.stock`, `Product.sold_count`, `User.points` |

**트랜잭션 구조**:
```python
@transaction.atomic
def cancel_payment(payment_id, user, cancel_reason):
    payment = Payment.objects.select_for_update().get(id=payment_id, order__user=user)  # 결제+주문 락
    order = Order.objects.select_for_update().get(pk=payment.order_id)  # 주문 락
    # 1. 중복 취소 방지 (is_canceled 체크)
    # 2. Toss API 취소 호출
    # 3. Payment.mark_as_canceled()
    # 4. 재고 복구 (stock+, sold_count-)
    # 5. Order.status = 'canceled'
    # 6. 포인트 환불 (used_points)
    # 7. 적립 포인트 차감 (earned_points)
```

---

### 2.4 롤백 경계 (Rollback Boundary)

| 파일 | 함수 | 공유 상태 |
|------|------|----------|
| `tasks/payment_tasks.py` | `rollback_payment_failure()` | `Order.status`, `Product.stock`, `User.points` |
| `services/order_service.py` | `cancel_order()` | `Order.status`, `Product.stock`, `Product.sold_count` |

**롤백 트리거 조건**:
1. `call_toss_confirm_api` 실패 (타임아웃, API 에러)
2. `finalize_payment_confirm` 실패 (DB 에러)
3. `detect_orphaned_orders` 주기적 감지 (confirmed + aborted 불일치)

---

### 2.5 Self-Healing 제어 경계

| 파일 | 함수 | 공유 상태 |
|------|------|----------|
| `services/self_healing/circuit_breaker_service.py` | `should_allow_request()` | Redis 서킷 상태 |
| `services/self_healing/dlq_service.py` | `store_failure()` | `FailedOperation` 테이블 |
| `services/self_healing/idempotency_service.py` | `for_payment_confirm()` | Redis 멱등성 캐시 |

---

## 3. 실패 주입 후보 지점

### BP-1: 결제 승인 중 지연/타임아웃 (Payment Confirm Delay)

**위치**: `services/payment_service.py` → `confirm_payment_sync()` 또는 `utils/toss_payment.py`

**실패 방식**:
- Toss API 호출 전후에 **조건부 지연** 삽입
- 조건: 특정 금액 범위, 특정 사용자 ID, 요청 비율 기반

**왜 현실적인가**:
- PG사 API는 네트워크 지연, 서버 과부하로 응답이 느려질 수 있음
- 실제 프로덕션에서 Toss 응답 타임아웃은 흔한 장애 패턴

**관찰 가능한 신호**:
- `SoftTimeLimitExceeded` 예외
- `rollback_payment_failure` 태스크 트리거
- `FailedOperation` DLQ 엔트리 생성
- Circuit Breaker 상태 전이

**대상 스테이지**: stage4, stage5, stage10

```python
# 예상 주입 위치
class TossPaymentClient:
    def confirm_payment(self, payment_key, order_id, amount):
        # [BREAKPOINT-1] 조건부 지연
        if self._should_inject_delay(order_id, amount):
            time.sleep(INJECTED_DELAY_SECONDS)
        # 실제 API 호출
```

---

### BP-2: 결제 승인 중 금액 불일치 (Amount Mismatch)

**위치**: `services/payment_service.py` → `confirm_payment_sync()`

**실패 방식**:
- 금액 검증 로직에서 **조건부로 검증 실패** 유도
- 이미 stage5에서 잘못된 금액으로 테스트 중

**왜 현실적인가**:
- 프론트엔드/백엔드 간 금액 계산 불일치
- 할인 쿠폰, 포인트 적용 순서 오류
- 동시 가격 변경 race condition

**관찰 가능한 신호**:
- HTTP 400 응답
- `PaymentConfirmError` 예외
- 재고 변화 없음 (롤백 불필요)

**대상 스테이지**: stage5

---

### BP-3: 동시 결제 승인 경쟁 (Concurrent Confirm Race)

**위치**: `services/payment_service.py` → `confirm_payment_sync()` 또는 `confirm_payment_async()`

**실패 방식**:
- `select_for_update()` 락 획득 **전**에 의도적 지연
- 여러 요청이 동시에 락을 획득하려고 경쟁

**왜 현실적인가**:
- 사용자가 결제 버튼을 연속 클릭
- 네트워크 재시도로 중복 요청
- 로드밸런서 뒤의 여러 서버 동시 처리

**관찰 가능한 신호**:
- HTTP 400/409 (이미 처리된 결제)
- `_race_stats["double_success"]` > 0 (심각)
- 멱등성 키 히트 로그

**대상 스테이지**: stage7

```python
# 예상 주입 위치
@transaction.atomic
def confirm_payment_sync(payment, payment_key, order_id, amount, user):
    # [BREAKPOINT-3] 락 획득 전 지연 (race window 확대)
    if self._should_inject_race_delay():
        time.sleep(0.5)  # 500ms race window
    
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    # ...
```

---

### BP-4: 취소 중 동시성 충돌 (Cancel Concurrency Conflict)

**위치**: `services/payment_service.py` → `cancel_payment()`

**실패 방식**:
- 취소 요청 처리 중 **결제 상태 재확인 시점**에 지연
- confirm과 cancel이 동시에 진행되는 상황

**왜 현실적인가**:
- 결제 직후 빠른 취소 요청
- 분산 시스템에서 이벤트 순서 역전

**관찰 가능한 신호**:
- HTTP 400 (취소 불가능한 상태)
- `PaymentCancelError` 예외
- 재고 복구 타이밍 불일치

**대상 스테이지**: stage4

```python
# 예상 주입 위치
@transaction.atomic
def cancel_payment(payment_id, user, cancel_reason):
    payment = Payment.objects.select_for_update().get(id=payment_id, ...)
    
    # [BREAKPOINT-4] 상태 확인 후 지연 (confirm이 끼어들 여지)
    if self._should_inject_cancel_delay():
        time.sleep(0.3)
    
    if payment.is_canceled:  # 이 시점에 상태 변경 가능
        raise PaymentCancelError(...)
```

---

### BP-5: 재고 차감 중 부분 실패 (Partial Stock Deduction Failure)

**위치**: `services/order_service.py` → `_create_order_items_and_decrease_stock()`

**실패 방식**:
- 여러 상품 주문 시 **중간 상품에서 실패**
- 첫 번째 상품은 재고 차감 성공, 두 번째에서 예외

**왜 현실적인가**:
- 동시 주문으로 인한 재고 경쟁
- DB 락 타임아웃
- 네트워크 파티션 중 부분 커밋

**관찰 가능한 신호**:
- 트랜잭션 전체 롤백 (atomic 보장)
- `OrderServiceError` 예외
- DLQ에 partial failure 기록

**대상 스테이지**: stage5

```python
# 예상 주입 위치
def _create_order_items_and_decrease_stock(order, cart):
    for idx, cart_item in enumerate(cart.items.all()):
        product = Product.objects.select_for_update().get(pk=cart_item.product.pk)
        
        # [BREAKPOINT-5] N번째 상품에서 조건부 실패
        if self._should_inject_partial_failure(idx, product.id):
            raise OrderServiceError("Injected partial failure for testing")
        
        Product.objects.filter(pk=product.pk).update(stock=F("stock") - cart_item.quantity)
```

---

### BP-6: 포인트 사용 중 잔액 부족 경쟁 (Point Race Condition)

**위치**: `services/point_service.py` → `use_points()` 또는 `use_points_fifo()`

**실패 방식**:
- `select_for_update()` 락 획득 **직전** 지연
- 동시 주문에서 동일 사용자 포인트 경쟁

**왜 현실적인가**:
- 탭 여러 개로 동시 결제 시도
- 포인트 만료와 사용 타이밍 충돌

**관찰 가능한 신호**:
- "포인트 부족" 에러
- 주문 실패 → 재고 복구
- PointHistory 불일치

**대상 스테이지**: stage5, stage7

```python
# 예상 주입 위치
@transaction.atomic
def use_points(user, amount, ...):
    # [BREAKPOINT-6] 락 획득 전 지연
    if self._should_inject_point_race():
        time.sleep(0.2)
    
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    
    if locked_user.points < amount:
        return False  # 다른 요청이 먼저 차감
```

---

### BP-7: Celery 태스크 중 예외 (Task Exception Mid-Process)

**위치**: `tasks/payment_tasks.py` → `finalize_payment_confirm()`

**실패 방식**:
- Toss API 성공 후, DB 업데이트 **직전** 예외
- Payment는 paid인데 Order는 pending인 불일치 상태

**왜 현실적인가**:
- Worker 프로세스 crash
- DB 연결 끊김
- 메모리 부족

**관찰 가능한 신호**:
- Celery 태스크 재시도
- DLQ 엔트리 생성
- `detect_orphaned_orders` 감지
- Circuit Breaker 오픈

**대상 스테이지**: stage5, stage10

```python
# 예상 주입 위치
@shared_task(...)
def finalize_payment_confirm(toss_response, payment_id, user_id):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        
        if payment.is_paid:
            return {"status": "already_processed"}
        
        payment.mark_as_paid(toss_response)
        
        # [BREAKPOINT-7] 판매량 업데이트 전 예외
        if self._should_inject_task_failure():
            raise Exception("Injected task failure for testing")
        
        for order_item in order.order_items.select_for_update():
            # ...
```

---

### BP-8: Redis 멱등성 키 만료 직전 경쟁

**위치**: `services/payment_service.py` → `_check_idempotency_key()` / `_set_idempotency_key()`

**실패 방식**:
- TTL 만료 직전에 동일 키로 재요청
- 첫 요청 완료 전 TTL 만료

**왜 현실적인가**:
- 긴 처리 시간으로 TTL(60초) 초과
- 클라이언트 재시도 타이밍

**관찰 가능한 신호**:
- 중복 Payment 생성
- 멱등성 검사 miss
- 이중 재고 차감

**대상 스테이지**: stage7

---

### BP-9: Circuit Breaker 상태 전이 중 경쟁

**위치**: `selfhealing` 패키지 또는 `services/self_healing/circuit_breaker_service.py`

**실패 방식**:
- Half-Open 상태에서 동시 요청
- 상태 전이 체크와 업데이트 사이 경쟁

**왜 현실적인가**:
- 복구 시도 중 부하 급증
- 분산 환경에서 상태 동기화 지연

**관찰 가능한 신호**:
- 불필요한 요청 허용/차단
- 상태 플래핑 (Open ↔ Half-Open 반복)
- 불일치 상태 로그

**대상 스테이지**: stage10

---

## 4. 스테이지별 매핑

### Stage 4: Cancel Storm

| Breakpoint | 적용 | 트리거 조건 |
|------------|------|-------------|
| BP-1 (결제 지연) | ✓ | 취소 전 결제 처리 지연으로 race window 확대 |
| BP-4 (취소 충돌) | ✓ | confirm/cancel 동시 진행 |
| BP-8 (멱등성 경쟁) | △ | 빠른 취소로 인한 상태 불일치 |

**기대 검증 포인트**:
- 취소 성공률 30% 이상 유지
- 재고 정합성 (개별 검증 스킵 환경에서도)
- 중복 취소 방지 (409 응답)

---

### Stage 5: Rollback Validation

| Breakpoint | 적용 | 트리거 조건 |
|------------|------|-------------|
| BP-2 (금액 불일치) | ✓ | 잘못된 금액으로 의도적 실패 |
| BP-5 (부분 실패) | ✓ | 다중 상품 주문 중간 실패 |
| BP-6 (포인트 경쟁) | ✓ | 동시 주문으로 포인트 부족 |
| BP-7 (태스크 예외) | ✓ | Toss 성공 후 DB 업데이트 전 실패 |

**기대 검증 포인트**:
- `rollback_failed` = 0 (시스템 버그 없음)
- 재고 증가 불가 (항상 감소 또는 유지)
- 포인트 정합성

---

### Stage 7: Race Conflict

| Breakpoint | 적용 | 트리거 조건 |
|------------|------|-------------|
| BP-3 (동시 승인) | ✓ | 락 획득 전 지연으로 race window 확대 |
| BP-6 (포인트 경쟁) | ✓ | 동일 사용자 동시 결제 |
| BP-8 (멱등성 경쟁) | ✓ | TTL 경계에서 중복 요청 |

**기대 검증 포인트**:
- `double_success` = 0 (중복 결제 없음)
- 단일 주문당 단일 성공
- 멱등성 키 정상 작동

---

### Stage 10: Self-Healing

| Breakpoint | 적용 | 트리거 조건 |
|------------|------|-------------|
| BP-1 (결제 지연) | ✓ | Circuit Breaker 오픈 트리거 |
| BP-7 (태스크 예외) | ✓ | DLQ 엔트리 생성 트리거 |
| BP-9 (CB 경쟁) | ✓ | 상태 전이 정합성 검증 |

**기대 검증 포인트**:
- Control API 응답 시간 < 2초
- 거버넌스 규칙 정확한 거부
- Recovery latency 측정

---

## 5. 실패 신호 관찰 가이드

### 5.1 로그 패턴

```python
# 결제 지연/타임아웃
logger.error(f"Toss API 호출 타임아웃: order_id={order_id}")

# 롤백 트리거
logger.info(f"결제 실패 롤백 시작: order_id={order_id}, reason={fail_reason}")

# Race condition 감지
logger.warning(f"이미 처리된 결제: payment_id={payment_id}")

# Circuit Breaker 상태 전이
logger.info(f"Circuit breaker state changed: {old_state} -> {new_state}")

# DLQ 저장
logger.info(f"[DLQService] Created DLQ entry: id={failed_op.id}")
```

### 5.2 메트릭

| 메트릭 | 정상 범위 | 실패 징후 |
|--------|----------|-----------|
| `payment_confirm_latency_p99` | < 3s | > 5s |
| `rollback_triggered_count` | > 0 (테스트 시) | N/A |
| `double_payment_count` | = 0 | > 0 (심각) |
| `circuit_breaker_open_count` | 변동 | 급증 |
| `dlq_entries_created` | > 0 (테스트 시) | 급증 |

### 5.3 HTTP 응답 코드

| 코드 | 의미 | 대응 |
|------|------|------|
| 200/201 | 성공 | 정상 |
| 202 | 비동기 처리 중 | 상태 폴링 필요 |
| 400 | 비즈니스 에러 (금액 불일치 등) | 롤백 불필요 |
| 409 | 충돌 (이미 처리됨) | 멱등성 정상 작동 |
| 500 | 서버 에러 | DLQ 확인 |

---

## 6. 구현 제약 조건

### 6.1 반드시 지켜야 할 원칙

1. **조건부 실패만 허용**
   - 무조건적인 실패 주입 금지
   - 환경 변수 / 설정 값으로 제어 가능해야 함

2. **재현 가능성**
   - 동일 조건에서 동일 실패 재현 가능
   - 시드 기반 랜덤 또는 결정적 조건

3. **설명 가능성**
   - 왜 이 실패가 현실적인지 문서화
   - 어떤 시스템 약점을 노출하는지 명시

4. **항상 존재, 조건부 트리거**
   - 코드에 실패 로직이 항상 포함
   - 특정 조건(동시성, 타이밍, 패턴)에서만 활성화

### 6.2 금지 사항

- ❌ 테스트 코드 수정
- ❌ Self-Healing 로직 추가/변경
- ❌ 랜덤 실패 (비결정적)
- ❌ 테스트 전용 플래그 (프로덕션 코드 오염)
- ❌ 트랜잭션 경계 변경

### 6.3 허용 구현 패턴

```python
# 패턴 1: 환경 변수 기반 조건
CHAOS_MODE = os.getenv("CHAOS_MODE", "off")
CHAOS_DELAY_PROBABILITY = float(os.getenv("CHAOS_DELAY_PROBABILITY", "0"))

if CHAOS_MODE == "on" and random.random() < CHAOS_DELAY_PROBABILITY:
    time.sleep(INJECTED_DELAY)

# 패턴 2: 요청 속성 기반 조건
def _should_inject_delay(self, order_id, amount):
    # 특정 금액 범위에서만 지연
    return 10000 <= amount <= 20000 and CHAOS_MODE == "on"

# 패턴 3: 동시성 패턴 감지
def _should_inject_race_delay(self):
    # 락 대기 큐 깊이가 일정 수준 이상일 때
    return self._get_lock_queue_depth() > RACE_THRESHOLD
```

---

## 다음 단계

이 문서를 기반으로 다음 작업 수행:

1. **우선순위 결정**: 각 Breakpoint의 구현 복잡도 vs 테스트 가치 평가
2. **구현 명세 작성**: 선택된 Breakpoint별 상세 구현 가이드
3. **검증 계획 수립**: 실패 주입 후 예상 동작 및 검증 방법

---

> **작성일**: 2025-12-17  
> **작성자**: AI Assistant  
> **버전**: 1.0.0
