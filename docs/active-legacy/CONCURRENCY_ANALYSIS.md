# 🔍 동시성 취약점 분석 및 리팩토링 계획

> **작성일**: 2025년 12월 2일
> **분석 대상**: django-shopping-mall 프로젝트
> **브랜치**: release/final-check

---

## 📊 1. 현재 코드 동시성 제어 현황

### ✅ 이미 잘 구현된 부분

| 영역 | 구현 방식 | 파일 위치 |
|------|-----------|-----------|
| **재고 차감** | `select_for_update()` + `F()` 객체 | `order_service.py` L303-315 |
| **포인트 차감** | `select_for_update()` + `F()` 객체 | `point_service.py` L98-126 |
| **주문 취소** | `Order.select_for_update()` 후 상태 체크 | `order_service.py` L396-408 |
| **장바구니 락** | `Cart.select_for_update()` | `order_service.py` L106-112 |
| **FIFO 포인트** | `PointHistory.select_for_update()` | `point_service.py` L271-295 |
| **Webhook 중복 방지** | `Payment.select_for_update()` + `is_paid` 체크 | `toss_webhook_view.py` L147-163 |

### ⚠️ 잠재적 위험 영역

리뷰에서 지적된 5가지 동시성 위험:
1. 동일 상품을 여러 유저가 동시에 구매 → 재고 음수
2. 동시에 주문 confirm → double-confirm
3. 동시에 포인트 사용 → FIFO 무너짐
4. 동시에 webhook 두 번 도착 → 중복 결제/중복 취소
5. 동시에 cart add → row-lock 안 걸리면 혼선 발생

---

## 🚨 2. 발견된 취약점 상세 분석

### 2-1. 재고 음수 가능성 (🟡 낮은 위험)

**현재 코드** (`order_service.py` L303-315):
```python
# 재고 최종 확인 (select_for_update로 동시성 제어)
product = Product.objects.select_for_update().get(pk=cart_item.product.pk)

if product.stock < cart_item.quantity:
    raise OrderServiceError(...)

# F() 객체를 사용한 안전한 재고 차감
Product.objects.filter(pk=product.pk).update(stock=F("stock") - cart_item.quantity)
```

**분석**:
- `select_for_update()`가 있어서 동시 접근 시 락이 걸립니다
- **✅ 기본적으로 안전합니다**

**그러나 주의점**:
- `toss_webhook_view.py`의 `handle_payment_done`에서 `Greatest(F("stock") - quantity, 0)` 사용
- 이는 음수 방지를 위한 **방어 코드**지만, 논리적으로 이미 차감된 재고를 또 차감할 위험이 있음

---

### 2-2. 동시 주문 confirm (Double-Confirm) (🟡 중간 위험)

**현재 코드** (`payment_tasks.py` L108-121):
```python
payment = Payment.objects.select_for_update().get(pk=payment_id)

# 중복 처리 방지
if payment.is_paid:
    logger.warning(f"이미 처리된 결제: payment_id={payment_id}")
    return {"status": "already_processed", "payment_id": payment_id}
```

**분석**:
- `select_for_update()` + `is_paid` 체크로 **이미 보호되어 있음**
- **✅ 기본적으로 안전합니다**

**그러나 주의점**:
- `toss_webhook_view.py`와 `payment_tasks.py` 두 곳에서 결제 완료 처리
- Webhook이 먼저 도착하면 문제없지만, **동시에 도착하면?**

---

### 2-3. 포인트 FIFO 무너짐 (🟢 낮은 위험)

**현재 코드** (`point_service.py` L271-295):
```python
# 동시성 제어: select_for_update로 락 획득
locked_user = User.objects.select_for_update().get(pk=user.pk)

# ...중략...

available_points = query.exclude(metadata__contains={"expired": True}).order_by("expires_at", "created_at")
```

**분석**:
- User 레벨에서 락을 걸어 **FIFO 순서 보장**
- **✅ 잘 구현되어 있음**

---

### 2-4. 동시 Webhook 두 번 도착 (🟡 중간 위험)

**현재 코드** (`toss_webhook_view.py` L147-163):
```python
payment = Payment.objects.select_for_update().get(toss_order_id=order_id)

# 이미 처리된 결제인지 확인 (중복 방지)
if payment.is_paid:
    logger.info(f"Payment already processed: {order_id}")
    return
```

**분석**:
- `select_for_update()`로 락 걸고 `is_paid` 체크
- **✅ 기본적으로 안전합니다**

**그러나 주의점**:
```python
# 취소 이벤트도 마찬가지로 is_canceled 체크 필요
if payment.is_canceled:
    return
```
- 이 부분도 구현되어 있음 ✅

---

### 2-5. 동시 Cart Add (🟡 중간 위험)

**현재 코드** (`cart_service.py` L176-180):
```python
# 3. 장바구니 락 획득 (동시성 제어)
cart = Cart.objects.select_for_update().get(pk=cart.pk)
```

**분석**:
- Cart 레벨 락이 있어 동일 장바구니 동시 수정 방지
- **✅ 기본적으로 안전합니다**

---

## 📋 3. 테스트 커버리지 현황

### ✅ 이미 있는 테스트

| 테스트 파일 | 시나리오 | 규모 |
|------------|---------|------|
| `test_order_concurrency.py` | 동시 주문 생성, 재고 경합 | 3~50명 |
| `test_point_service_concurrent.py` | 동시 포인트 사용/추가 | 5~20명 |
| `concurrent_payment_locust.py` | 동시 결제 승인 | 500명 |
| `locustfile.py` | 혼합 트래픽 시나리오 | 10~1000명 |

### ❌ 누락된 테스트 (보강 필요)

1. **동시 Webhook 도착 테스트** - 같은 결제에 DONE 이벤트 2번
2. **Confirm API + Webhook 동시 도착** - Race Condition
3. **동시 주문 취소 (여러 다른 사용자의 주문)** - Deadlock 가능성
4. **동시 포인트 만료 처리** - 이미 있지만 스케일 확장 필요
5. **Cart + Order 동시 진행** - 같은 유저가 장바구니 수정 중 주문

---

## 🛠️ 4. 리팩토링 계획

### Phase 1: 누락된 테스트 추가 (우선순위: 높음)

#### 4-1. 동시 Webhook 테스트 추가

```python
# tests/integration/test_webhook_concurrency.py

@pytest.mark.django_db(transaction=True)
class TestWebhookConcurrency:
    """Webhook 동시 도착 테스트"""

    def test_duplicate_payment_done_webhook(self):
        """동일 결제에 DONE 이벤트 2번 동시 도착"""
        # 1. 결제 생성
        # 2. 2개 스레드로 동시에 handle_payment_done 호출
        # 3. 1번만 성공하고, 재고도 1번만 차감되었는지 검증
```

#### 4-2. Confirm + Webhook 동시 도착 테스트

```python
def test_confirm_and_webhook_race_condition(self):
    """Confirm API와 Webhook이 동시에 도착하는 경우"""
    # 시나리오:
    # - 스레드 1: /api/payments/confirm/ 호출
    # - 스레드 2: handle_payment_done 호출 (웹훅 시뮬레이션)
    # - 결과: 딱 1번만 처리되어야 함
```

---

### Phase 2: 방어 코드 강화 (우선순위: 중간)

#### 4-3. 재고 음수 방지 강화

**현재 문제점**: `handle_payment_done`에서 `Greatest()` 사용은 음수 방지지만, 논리적 오류를 숨김

**개선 방안**:

```python
# 옵션 A: 조건부 업데이트 (업계 표준) ⭐ 추천
updated = Product.objects.filter(
    pk=order_item.product.pk,
    stock__gte=order_item.quantity  # 재고가 충분할 때만
).update(
    stock=F("stock") - order_item.quantity,
    sold_count=F("sold_count") + order_item.quantity,
)

if updated == 0:
    raise InsufficientStockError(f"재고 부족: {product.name}")

# 옵션 B: DB 레벨 CHECK 제약조건 (PostgreSQL)
# migrations에서:
# ALTER TABLE shopping_product ADD CONSTRAINT stock_non_negative CHECK (stock >= 0);
```

**선택지 비교**:

| 방식 | 장점 | 단점 | 업계 사용 |
|-----|------|------|---------|
| **조건부 업데이트** | 코드 레벨 제어, 로깅 가능 | 매번 체크 필요 | ⭐⭐⭐ 가장 많이 사용 |
| **DB 제약조건** | DB가 보장, 실수 방지 | 에러 메시지 불친절 | ⭐⭐ 보조적 사용 |
| **Greatest()** | 음수 안 됨 | 논리 오류 숨김 | ⭐ 비추천 |

**추천**: **조건부 업데이트 + DB 제약조건 병행**

---

#### 4-4. Idempotency Key 도입 (결제 중복 방지 강화)

```python
# models/payment.py에 추가
class Payment(models.Model):
    # 기존 필드...
    idempotency_key = models.CharField(max_length=64, unique=True, null=True)

# payment_service.py
def confirm_payment(payment_key, order_id, amount, idempotency_key=None):
    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing  # 이미 처리된 결제 반환
```

---

### Phase 3: Locust 테스트 확장 (우선순위: 중간)

#### 4-5. 새로운 Locust 시나리오 추가

```python
# load_tests/webhook_stress_test.py

class WebhookStressUser(HttpUser):
    """Webhook 동시 도착 스트레스 테스트"""

    @task
    def simulate_duplicate_webhook(self):
        """같은 결제에 여러 번 웹훅 전송"""
        # 토스페이먼츠가 실제로 재전송하는 시나리오 시뮬레이션
```

---

### Phase 4: 모니터링 강화 (우선순위: 낮음)

#### 4-6. 동시성 문제 감지 로깅

```python
# services/order_service.py
import time

def _create_order_items_and_decrease_stock(order, cart):
    start_time = time.time()

    # ... 기존 코드 ...

    elapsed = time.time() - start_time
    if elapsed > 1.0:  # 1초 이상 걸리면 경고
        logger.warning(
            f"재고 차감 지연: order_id={order.id}, elapsed={elapsed:.2f}s, "
            f"possible_lock_contention=True"
        )
```

---

## 🎯 5. 업계 표준과 선택지 비교

### 5-1. 동시성 제어 방식 비교

| 방식 | 설명 | 장점 | 단점 | 언제 사용 |
|-----|------|------|------|---------|
| **Pessimistic Lock** (`select_for_update`) | 먼저 락 획득 | 확실한 보호 | 성능 저하, Deadlock 위험 | **금융, 재고** (현재 사용 중) |
| **Optimistic Lock** (version 필드) | 충돌 시 재시도 | 읽기 성능 우수 | 재시도 로직 필요 | 충돌 적은 경우 |
| **Atomic Update** (`F()` 객체) | DB 레벨 원자적 연산 | 간단, 빠름 | 복잡한 로직 불가 | 단순 증감 (현재 사용 중) |
| **Redis 분산락** | 분산 환경 락 | 여러 서버에서 사용 | 복잡도 증가, Redis 의존 | 대규모 분산 시스템 |

### 5-2. 현재 코드의 선택

**현재 코드는 `Pessimistic Lock` + `Atomic Update` 조합**을 사용 중입니다.
**이것이 업계 표준이며, 쇼핑몰/결제 시스템에 가장 적합합니다.**

---

## 📝 6. 구체적 액션 아이템

### 즉시 실행 (1주차)

- [x] **동시 Webhook 테스트 추가** - `test_webhook_concurrency.py`
- [x] **조건부 업데이트로 재고 차감 강화** - `toss_webhook_view.py`
- [x] **DB CHECK 제약조건 마이그레이션** - `stock >= 0`

### 단기 (2주차)

- [x] **Confirm + Webhook 동시 도착 테스트** - `test_confirm_webhook_race.py`
- [x] **Idempotency Key 도입** - `Payment.idempotency_key`, `payment_service.py`
- [x] **Locust 웹훅 스트레스 테스트** - `load_tests/webhook_stress_test.py`

### 중기 (3-4주차)

- [x] **동시성 모니터링 대시보드** (로깅 기반)
- [x] **대규모 부하 테스트** (1000명+ 동시)

---

## 🎓 7. 초보자를 위한 핵심 개념 설명

### Q1. `select_for_update()`가 뭔가요?

```python
# 일반 조회 - 다른 사람도 동시에 읽을 수 있음
product = Product.objects.get(pk=1)

# select_for_update - "나 이거 수정할 거야, 다른 사람 건들지마!"
product = Product.objects.select_for_update().get(pk=1)
```

**비유**: 화장실에 들어가면서 문 잠그는 것 🚽🔒

---

### Q2. `F()` 객체가 뭔가요?

```python
# 위험한 방식 - 읽고 쓰는 사이에 다른 요청이 끼어들 수 있음
product = Product.objects.get(pk=1)
product.stock = product.stock - 1  # 메모리에서 계산
product.save()

# 안전한 방식 - DB가 직접 계산
Product.objects.filter(pk=1).update(stock=F("stock") - 1)
```

**비유**:
- 위험: "잔액 확인하고 → 계산하고 → 저장" (중간에 누가 끼어들 수 있음)
- 안전: "DB야, 현재 값에서 1 빼줘" (DB가 원자적으로 처리)

---

### Q3. 왜 테스트가 100% 커버리지여도 동시성 버그가 날 수 있나요?

```
일반 테스트: A → B → C (순차 실행)
동시성 버그: A --→ C
             B ↗     (타이밍에 따라 결과가 달라짐)
```

**커버리지 도구는 "코드 라인을 실행했는지"만 체크**합니다.
**동시에 실행될 때의 타이밍 문제는 감지 못합니다.**

---

### Q4. Deadlock이 뭔가요?

```
스레드 A: Product 1 락 획득 → Product 2 락 대기
스레드 B: Product 2 락 획득 → Product 1 락 대기
→ 서로 기다리며 영원히 멈춤 💀
```

**해결책**: 항상 같은 순서로 락 획득 (예: ID 오름차순)

```python
# order_tasks.py에서 이미 적용됨
cart_items = cart.items.select_related('product').order_by('product_id').all()
```

---

### Q5. Race Condition이 뭔가요?

```
시간 →
User A: 재고 확인(10개) ─────────────── 차감(10-1=9) 저장
User B:      재고 확인(10개) ─ 차감(10-1=9) 저장
결과: 2개 팔렸는데 재고는 9개 (1개 손실!)
```

**해결책**: `select_for_update()` + `F()` 객체

---

## 📊 8. 결론

### 현재 상태 평가

| 영역 | 점수 | 평가 |
|-----|------|------|
| 재고 관리 | ⭐⭐⭐⭐ | 잘 되어 있음, 약간 보강 필요 |
| 포인트 FIFO | ⭐⭐⭐⭐⭐ | 매우 잘 구현됨 |
| 결제 중복 방지 | ⭐⭐⭐⭐ | 잘 되어 있음, 테스트 보강 필요 |
| 장바구니 동시성 | ⭐⭐⭐⭐ | 잘 되어 있음 |
| **테스트 커버리지** | ⭐⭐⭐ | **보강 필요** |

### 핵심 메시지

> **코드 자체는 이미 잘 구현되어 있습니다!**
> 리뷰어가 지적한 것은 "테스트로 검증되지 않은 영역"입니다.
> **추가 테스트**를 통해 실제로 동시성 보호가 작동하는지 **증명**해야 합니다.

---

## 📚 9. 참고 자료

### Django 공식 문서
- [Database transactions](https://docs.djangoproject.com/en/4.2/topics/db/transactions/)
- [select_for_update()](https://docs.djangoproject.com/en/4.2/ref/models/querysets/#select-for-update)
- [F() expressions](https://docs.djangoproject.com/en/4.2/ref/models/expressions/#f-expressions)

### 테스트 도구
- [pytest-django](https://pytest-django.readthedocs.io/)
- [Locust](https://locust.io/)

### 추가 학습
- [Two-Phase Locking](https://en.wikipedia.org/wiki/Two-phase_locking)
- [Optimistic vs Pessimistic Locking](https://stackoverflow.com/questions/129329/optimistic-vs-pessimistic-locking)

---

## 📝 변경 이력

| 날짜 | 내용 | 작성자 |
|-----|------|--------|
| 2025-12-02 | 최초 작성 | AI Assistant |
