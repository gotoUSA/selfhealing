# 결제 시스템 보완 작업 문서

> 작성일: 2025-12-07
> 작성자: GitHub Copilot
> 상태: **작업 예정**

---

## 📋 개요

결제 시스템 전체 흐름을 분석한 결과, 결제 실패/취소 시 보상 체계(Compensation)가 일부 누락되어 있거나 불완전한 부분이 발견되었습니다. 이 문서는 발견된 문제점과 해결 방안을 정리합니다.

---

## 🔍 현재 결제 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           결제 프로세스 흐름                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. 주문 생성 (OrderService.create_order_from_cart)                     │
│     └─> status: "pending"                                               │
│     └─> 재고 차감 (stock -= quantity)                                   │
│     └─> 포인트 차감 (used_points)                                       │
│                                                                         │
│  2. 하이브리드 방식 (OrderService.create_order_hybrid)                   │
│     └─> status: "pending" → 비동기 처리 후 "confirmed"                   │
│     └─> 재고 차감은 비동기 태스크에서 처리                                │
│                                                                         │
│  3. 결제 요청 (PaymentService.create_payment)                           │
│     └─> Payment status: "ready"                                         │
│                                                                         │
│  4. Toss 결제창 → 결제 승인 (PaymentService.confirm_payment_async)       │
│     └─> Celery chain: call_toss_confirm_api → finalize_payment_confirm  │
│     └─> 성공 시: Payment "done", Order "paid", sold_count++             │
│     └─> 실패 시: rollback_payment_failure 태스크 트리거                  │
│                                                                         │
│  5. 웹훅 수신 (TossWebhookService)                                      │
│     └─> PAYMENT.DONE: 결제 완료 처리                                    │
│     └─> PAYMENT.CANCELED: 결제 취소 처리                                │
│     └─> PAYMENT.FAILED: 결제 실패 처리 ⚠️ (롤백 누락)                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## ⚠️ 발견된 문제점 목록

### 문제 1: PAYMENT.FAILED 웹훅에서 재고 롤백 누락 (심각도: 🔴 High)

**파일**: `shopping/services/toss_webhook_service.py`
**함수**: `handle_payment_failed()`

**현재 동작**:
- `PAYMENT.FAILED` 웹훅 수신 시 Payment 상태만 `aborted`로 변경
- Order 상태는 `confirmed`로 유지됨
- 재고는 차감된 상태로 유지됨
- 사용한 포인트도 차감된 상태로 유지됨

**영향**:
- 결제 실패 후에도 재고가 잠겨있어 다른 사용자가 구매 불가
- 사용자 포인트가 복구되지 않음
- `detect_orphaned_orders` 태스크가 10분마다 감지하지만, 그 사이 재고 불일치 발생

**현재 코드**:
```python
@staticmethod
def handle_payment_failed(event_data: dict[str, Any]) -> None:
    order_id = event_data.get("orderId")
    fail_reason = event_data.get("failReason", "")

    # ... Payment 조회 ...

    # Payment 실패 처리만 수행
    payment.mark_as_failed(fail_reason)

    # 웹훅 로그
    PaymentLog.objects.create(...)

    # ❌ 재고 롤백 없음
    # ❌ 포인트 환불 없음
    # ❌ Order 상태 변경 없음
```

**해결 방안**:
```python
@staticmethod
def handle_payment_failed(event_data: dict[str, Any]) -> None:
    order_id = event_data.get("orderId")
    fail_reason = event_data.get("failReason", "")

    # ... Payment 조회 ...

    payment.mark_as_failed(fail_reason)

    # ✅ 롤백 태스크 트리거 추가
    from ..tasks.payment_tasks import rollback_payment_failure
    order = payment.order
    if order.status in ["pending", "confirmed"]:
        rollback_payment_failure.delay(order.id, fail_reason)

    PaymentLog.objects.create(...)
```

---

### 문제 2: handle_payment_canceled에서 confirmed 상태 미처리 (심각도: 🟡 Medium)

**파일**: `shopping/services/toss_webhook_service.py`
**함수**: `handle_payment_canceled()`

**현재 동작**:
- `paid`, `preparing` 상태에서만 재고 복구
- `confirmed` 상태에서 취소 웹훅이 오면 재고가 복구되지 않음

**영향**:
- `confirmed` 상태에서는 이미 재고가 차감되어 있음
- 하지만 취소 시 재고가 복구되지 않음

**현재 코드**:
```python
# 재고 복구 (paid 상태였던 경우만)
if order.status in ["paid", "preparing"]:
    for order_item in order.order_items.all():
        # ... 재고 복구 ...
```

**해결 방안**:
```python
# 재고 복구 (재고가 차감된 상태들)
if order.status in ["paid", "preparing", "confirmed"]:
    for order_item in order.order_items.all():
        if order_item.product:
            if order.status in ["paid", "preparing"]:
                # paid/preparing: 재고 복구 + sold_count 차감
                # ... 기존 로직 ...
            else:
                # confirmed: 재고만 복구 (sold_count는 아직 증가 안함)
                Product.objects.filter(pk=order_item.product.pk).update(
                    stock=F("stock") + order_item.quantity,
                )
```

---

### 문제 3: sold_count 음수 방지 미적용 (심각도: 🟡 Medium)

**파일**: `shopping/services/order_service.py`
**함수**: `cancel_order()`

**현재 동작**:
- `payment_service.cancel_payment()`에서는 `Greatest()` 함수로 음수 방지 적용
- `order_service.cancel_order()`에서는 음수 방지가 없음

**영향**:
- `sold_count`가 음수가 되면 데이터 무결성 위반
- 상품 통계 집계 시 오류 발생 가능

**현재 코드**:
```python
if order.status == "paid":
    Product.objects.filter(pk=item.product.pk).update(
        stock=F("stock") + item.quantity,
        sold_count=F("sold_count") - item.quantity,  # ❌ 음수 가능
    )
```

**해결 방안**:
```python
from django.db.models.functions import Greatest

if order.status == "paid":
    Product.objects.filter(pk=item.product.pk).update(
        stock=F("stock") + item.quantity,
        sold_count=Greatest(F("sold_count") - item.quantity, 0),  # ✅ 음수 방지
    )
```

---

### 문제 4: 주문 실패 시 장바구니 복구 누락 (심각도: 🟡 Medium)

**파일**: `shopping/services/order_service.py` 및 `shopping/tasks/order_tasks.py`

**현재 동작**:
1. `create_order_hybrid()`에서 장바구니를 즉시 비활성화 (`is_active = False`)
2. 비동기 태스크 `process_order_heavy_tasks`에서 주문 처리
3. 재고 부족 등으로 주문이 `failed` 상태가 되면
4. 장바구니는 비활성화 상태로 유지됨 (복구 안됨)

**영향**:
- 사용자가 장바구니에 담은 상품이 사라짐
- 다시 장바구니에 담아야 하는 불편함

**현재 코드** (order_tasks.py):
```python
# 재고 부족 시
order.status = "failed"
order.failure_reason = f"재고 부족: {product.name}..."
order.save(update_fields=["status", "failure_reason", "updated_at"])

return {
    "status": "failed",
    "reason": "insufficient_stock",
    # ❌ 장바구니 복구 없음
}
```

**해결 방안**:
```python
# 재고 부족 시
order.status = "failed"
order.failure_reason = f"재고 부족: {product.name}..."
order.save(update_fields=["status", "failure_reason", "updated_at"])

# ✅ 장바구니 복구
Cart.objects.filter(pk=cart_id).update(is_active=True)
logger.info(f"장바구니 복구: cart_id={cart_id}")

return {
    "status": "failed",
    "reason": "insufficient_stock",
}
```

**추가로** 포인트 사용 실패 시에도 장바구니 복구 필요:
```python
if not result["success"]:
    # ... 재고 복구 ...

    order.status = "failed"
    order.failure_reason = f"포인트 사용 실패: {result['message']}"
    order.save(update_fields=["status", "failure_reason", "updated_at"])

    # ✅ 장바구니 복구
    Cart.objects.filter(pk=cart_id).update(is_active=True)

    return {...}
```

---

### 문제 5: 포인트 전액 결제 시 Toss API 호출 (심각도: 🟢 Low)

**파일**: `shopping/serializers/payment_serializers.py`

**현재 동작**:
- `final_amount = 0` (포인트 전액 결제)여도 Toss 결제창으로 진행
- Toss API는 0원 결제를 지원하지 않을 수 있음

**영향**:
- 포인트 전액 결제 시 에러 발생 가능
- 사용자 경험 저하

**해결 방안 (2가지 옵션)**:

**옵션 A**: 포인트 전액 결제 시 별도 처리 경로
```python
# PaymentRequestSerializer.validate_order_id()
if order.final_amount == 0:
    # 포인트 전액 결제: 별도 처리 필요
    raise serializers.ValidationError(
        "포인트 전액 결제입니다. /api/payments/points-only/ 엔드포인트를 사용해주세요."
    )
```

**옵션 B**: View에서 분기 처리
```python
# PaymentConfirmView
if payment.amount == 0:
    # 포인트 전액 결제: Toss API 호출 없이 바로 완료 처리
    return PaymentService.complete_points_only_payment(payment, user)
```

---

### 문제 6: 장바구니-재고 Race Condition 알림 부재 (심각도: 🟢 Low)

**파일**: 여러 파일

**현재 동작**:
- 장바구니에 상품을 담은 후 결제 전에 재고가 소진되면
- 주문 생성 시점에서야 재고 부족 에러 발생
- 사용자에게 사전 알림 없음

**영향**:
- 결제 직전에 재고 부족 에러를 경험하는 UX 저하

**해결 방안** (선택적):
1. 장바구니 페이지 로드 시 실시간 재고 확인 API 호출
2. 결제 버튼 클릭 전 재고 사전 검증
3. WebSocket으로 재고 변동 실시간 알림 (고급)

---

## 📝 작업 체크리스트

| # | 문제 | 심각도 | 파일 | 상태 |
|---|------|--------|------|------|
| 1 | PAYMENT.FAILED 웹훅 롤백 누락 | 🔴 High | `toss_webhook_service.py` | ⬜ 미완료 |
| 2 | handle_payment_canceled confirmed 미처리 | 🟡 Medium | `toss_webhook_service.py` | ⬜ 미완료 |
| 3 | sold_count 음수 방지 미적용 | 🟡 Medium | `order_service.py` | ✅ 완료 (2025-12-07) |
| 4 | 주문 실패 시 장바구니 복구 누락 | 🟡 Medium | `order_tasks.py` | ✅ 완료 (2025-12-07) |
| 5 | 포인트 전액 결제 처리 | 🟢 Low | `payment_serializers.py` | ⬜ 미완료 |
| 6 | 장바구니-재고 Race Condition 알림 | 🟢 Low | 여러 파일 | ⬜ 미완료 |

---

## 🔄 관련 테스트 케이스

각 수정 사항에 대해 다음 테스트가 필요합니다:

### 문제 1 테스트
```python
def test_payment_failed_webhook_triggers_rollback():
    """PAYMENT.FAILED 웹훅 수신 시 롤백 태스크가 트리거되는지 확인"""

def test_payment_failed_webhook_restores_stock():
    """PAYMENT.FAILED 웹훅 후 재고가 복구되는지 확인"""

def test_payment_failed_webhook_refunds_points():
    """PAYMENT.FAILED 웹훅 후 포인트가 환불되는지 확인"""
```

### 문제 2 테스트
```python
def test_payment_canceled_webhook_restores_confirmed_order_stock():
    """confirmed 상태 주문의 취소 웹훅 시 재고 복구 확인"""
```

### 문제 3 테스트
```python
def test_cancel_order_sold_count_not_negative():
    """주문 취소 시 sold_count가 음수가 되지 않는지 확인"""
```

### 문제 4 테스트
```python
def test_order_failed_restores_cart():
    """주문 실패 시 장바구니가 복구되는지 확인"""
```

---

## 📚 참고 자료

- 기존 수정 내역: `order_service.py`에서 confirmed 상태 재고 복구 추가 (어제 작업)
- 관련 태스크: `detect_orphaned_orders` - 불일치 상태 감지 (10분 주기)
- 관련 문서: `docs/CELERY_RETRY_GUIDE.md`

---

## ✅ 완료 시 검증 항목

1. [ ] 모든 단위 테스트 통과
2. [ ] 통합 테스트 통과 (Postman Collections)
3. [ ] `t1_payment_failrollback_stockrestore.json` 테스트 통과
4. [ ] `t1_order_cancel_stockrestore.json` 테스트 통과
5. [ ] 수동 테스트: 결제 실패 시나리오
6. [ ] 수동 테스트: 주문 취소 시나리오
7. [ ] 로그 확인: 롤백 처리 로그 정상 출력

---

## 🚀 작업 순서 권장

1. **문제 1** (PAYMENT.FAILED 롤백) - 가장 심각, 먼저 수정
2. **문제 2** (confirmed 상태 취소) - 관련 파일이 같으므로 함께 수정
3. **문제 3** (sold_count 음수 방지) - 간단한 수정
4. **문제 4** (장바구니 복구) - 사용자 경험 개선
5. **문제 5, 6** - 선택적 개선 (우선순위 낮음)
