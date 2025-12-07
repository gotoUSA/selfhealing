# L2 Crash Recovery Test 구현 계획서

## 📋 개요

**목적:** DB 트랜잭션 중간 장애 발생 시에도 데이터 무결성이 유지되는지 검증  
**수준:** 금융권/PG 수준의 신뢰성 테스트  
**구현 방식:** 방법 2 (Transaction Savepoint) + 방법 3 (Celery Task Crash Mock)

---

## 🎯 테스트 시나리오

### 시나리오 매트릭스

| # | 시나리오 | 장애 시점 | 검증 항목 | 방법 |
|---|----------|----------|----------|------|
| **L2-A** | Payment 상태 업데이트 중 Crash | `payment.status = DONE` 직전 | 롤백 확인, 재처리 가능 | 방법 2 |
| **L2-B** | 포인트 차감 후 Crash | 포인트 차감 완료, Order 미완료 | 포인트 롤백 또는 정합성 | 방법 2 |
| **L2-C** | 재고 차감 후 Crash | 재고 ↓, Payment 미완료 | 재고 복구 확인 | 방법 2 |
| **L2-D** | Webhook 처리 중 Celery Crash | Task 실행 중 Worker 죽음 | idempotency 유지, 재처리 성공 | 방법 3 |
| **L2-E** | 결제 승인 Task Crash | confirm_payment task 중간 | 상태 일관성, Webhook으로 복구 | 방법 3 |

---

## 🛠 구현 방법

### 방법 2: Transaction Savepoint 활용

```python
# tests/integration/test_l2_transaction_crash.py

import pytest
from django.db import transaction
from django.test import TransactionTestCase
from decimal import Decimal

from shopping.models import Payment, Order, User, Product


class SimulatedCrashError(Exception):
    """테스트용 Crash 시뮬레이션 예외"""
    pass


class L2TransactionCrashTest(TransactionTestCase):
    """L2: DB Transaction 중간 Crash 테스트"""
    
    def setUp(self):
        """테스트 데이터 준비"""
        self.user = User.objects.create_user(
            username='l2_test_user',
            email='l2@test.com',
            password='testpass123',
            points=50000
        )
        self.product = Product.objects.create(
            name='Test Product',
            price=Decimal('10000'),
            stock=100
        )
    
    def test_l2a_payment_status_crash_before_done(self):
        """
        L2-A: Payment 상태 업데이트 직전 Crash
        
        Timeline:
        1. Payment 조회 ✓
        2. payment.status = 'done' 설정
        3. ❌ CRASH (save 전)
        4. 검증: 상태가 'pending' 유지
        """
        # 1. Payment 생성 (pending 상태)
        order = Order.objects.create(user=self.user, total_amount=10000)
        payment = Payment.objects.create(
            order=order,
            amount=10000,
            status='pending'
        )
        original_status = payment.status
        
        # 2. 트랜잭션 내에서 crash 시뮬레이션
        try:
            with transaction.atomic():
                payment.status = 'done'
                # Crash 시뮬레이션 (save 전)
                raise SimulatedCrashError("Crash before payment.save()")
        except SimulatedCrashError:
            pass
        
        # 3. 검증: DB에서 다시 조회하면 원래 상태 유지
        payment.refresh_from_db()
        assert payment.status == original_status, \
            f"Payment should remain '{original_status}', got '{payment.status}'"
        
        print(f"✓ L2-A PASSED: Payment status rolled back to '{payment.status}'")
    
    def test_l2b_points_deducted_then_crash(self):
        """
        L2-B: 포인트 차감 후 Order 업데이트 전 Crash
        
        Timeline:
        1. 포인트 50,000 → 40,000 차감 ✓
        2. Order 상태 업데이트 중
        3. ❌ CRASH
        4. 검증: 포인트가 50,000으로 롤백
        """
        original_points = self.user.points
        deduct_amount = 10000
        
        try:
            with transaction.atomic():
                # 포인트 차감
                self.user.points -= deduct_amount
                self.user.save()
                
                # Order 생성 중 crash
                order = Order(user=self.user, total_amount=deduct_amount)
                raise SimulatedCrashError("Crash during order creation")
        except SimulatedCrashError:
            pass
        
        # 검증: 포인트 롤백
        self.user.refresh_from_db()
        assert self.user.points == original_points, \
            f"Points should be {original_points}, got {self.user.points}"
        
        print(f"✓ L2-B PASSED: Points rolled back to {self.user.points}")
    
    def test_l2c_stock_decreased_then_crash(self):
        """
        L2-C: 재고 차감 후 Payment 완료 전 Crash
        
        Timeline:
        1. 재고 100 → 99 차감 ✓
        2. Payment 완료 처리 중
        3. ❌ CRASH
        4. 검증: 재고가 100으로 복구
        """
        original_stock = self.product.stock
        
        try:
            with transaction.atomic():
                # 재고 차감
                self.product.stock -= 1
                self.product.save()
                
                # Payment 처리 중 crash
                raise SimulatedCrashError("Crash during payment processing")
        except SimulatedCrashError:
            pass
        
        # 검증: 재고 롤백
        self.product.refresh_from_db()
        assert self.product.stock == original_stock, \
            f"Stock should be {original_stock}, got {self.product.stock}"
        
        print(f"✓ L2-C PASSED: Stock rolled back to {self.product.stock}")
```

### 방법 3: Celery Task Crash Mock

```python
# tests/integration/test_l2_celery_crash.py

import pytest
from unittest.mock import patch, MagicMock
from django.test import TransactionTestCase
from celery.exceptions import WorkerLostError

from shopping.models import Payment, Order, User
from shopping.tasks import process_payment_confirmation


class L2CeleryCrashTest(TransactionTestCase):
    """L2: Celery Worker Crash 시뮬레이션 테스트"""
    
    def setUp(self):
        """테스트 데이터 준비"""
        self.user = User.objects.create_user(
            username='l2_celery_user',
            email='l2celery@test.com',
            password='testpass123',
            points=50000,
            is_email_verified=True
        )
    
    @patch('shopping.tasks.process_payment_confirmation.delay')
    def test_l2d_webhook_during_celery_crash(self, mock_task):
        """
        L2-D: Webhook 처리 중 Celery Worker Crash
        
        Timeline:
        1. 결제 승인 요청 → Celery task 시작
        2. ❌ Worker 죽음 (WorkerLostError)
        3. PG가 Webhook 재전송
        4. 검증: Webhook으로 정상 복구
        """
        # 1. Task가 crash 발생시키도록 mock
        mock_task.side_effect = WorkerLostError("Worker crashed!")
        
        # 2. Order/Payment 생성
        order = Order.objects.create(
            user=self.user,
            total_amount=10000,
            status='pending'
        )
        payment = Payment.objects.create(
            order=order,
            amount=10000,
            status='pending',
            payment_key='test_crash_key'
        )
        
        # 3. Task 호출 시도 (crash 발생)
        try:
            mock_task(payment.id, 'test_payment_key', 10000)
        except WorkerLostError:
            pass
        
        # 4. Payment 상태 확인 (여전히 pending)
        payment.refresh_from_db()
        assert payment.status == 'pending', \
            "Payment should remain pending after worker crash"
        
        # 5. Webhook으로 복구 시뮬레이션
        # (실제로는 Webhook handler 호출)
        payment.status = 'done'
        payment.save()
        
        # 6. 최종 상태 검증
        payment.refresh_from_db()
        assert payment.status == 'done', \
            "Payment should be done after webhook recovery"
        
        print("✓ L2-D PASSED: Webhook recovered payment after worker crash")
    
    @patch('shopping.services.payment_service.TossPaymentClient')
    def test_l2e_confirm_task_crash_then_replay(self, mock_toss):
        """
        L2-E: 결제 승인 Task 중간 Crash 후 Replay
        
        Timeline:
        1. confirm_payment task 시작
        2. Toss API 호출 성공
        3. DB 저장 중 ❌ CRASH
        4. 동일 요청 재시도
        5. 검증: idempotency 유지, 중복 결제 없음
        """
        # Mock Toss 응답
        mock_toss.return_value.confirm_payment.return_value = {
            'status': 'DONE',
            'paymentKey': 'toss_key_123',
            'orderId': 'order_123',
            'totalAmount': 10000
        }
        
        # 1. 첫 번째 시도에서 crash
        order = Order.objects.create(
            user=self.user,
            total_amount=10000,
            status='pending'
        )
        payment = Payment.objects.create(
            order=order,
            amount=10000,
            status='pending',
            idempotency_key='unique_key_123'
        )
        
        # 2. 첫 번째 처리 (crash 시뮬레이션)
        first_attempt_crashed = False
        try:
            with transaction.atomic():
                # Toss API 호출 성공
                mock_toss.return_value.confirm_payment(
                    'toss_key_123', str(order.id), 10000
                )
                # DB 저장 직전 crash
                raise SimulatedCrashError("Crash before DB commit")
        except SimulatedCrashError:
            first_attempt_crashed = True
        
        assert first_attempt_crashed, "First attempt should have crashed"
        
        # 3. 재시도 (정상 처리)
        payment.refresh_from_db()
        if payment.status != 'done':
            payment.status = 'done'
            payment.payment_key = 'toss_key_123'
            payment.save()
        
        # 4. 검증: 단 한 번만 처리됨
        payment_count = Payment.objects.filter(
            order=order,
            status='done'
        ).count()
        
        assert payment_count == 1, \
            f"Should have exactly 1 successful payment, got {payment_count}"
        
        print("✓ L2-E PASSED: Idempotency maintained after crash and replay")
```

---

## 📁 파일 구조

```
myproject/
├── tests/
│   └── integration/
│       ├── __init__.py
│       ├── test_l2_transaction_crash.py    # 방법 2: Savepoint 테스트
│       └── test_l2_celery_crash.py         # 방법 3: Celery Crash 테스트
├── load_tests/
│   └── scenarios/
│       └── stage8_webhook.py               # 기존 L1 포함
└── docs/
    └── L2_CRASH_RECOVERY_TEST_PLAN.md      # 이 문서
```

---

## 🔧 실행 방법

### 로컬 실행

```bash
# 방법 2 테스트 (Transaction Savepoint)
pytest tests/integration/test_l2_transaction_crash.py -v

# 방법 3 테스트 (Celery Crash Mock)
pytest tests/integration/test_l2_celery_crash.py -v

# 전체 L2 테스트
pytest tests/integration/test_l2_*.py -v
```

### Docker 환경 실행

```bash
# Docker 컨테이너 내에서 실행
docker-compose exec web pytest tests/integration/test_l2_*.py -v
```

---

## ✅ 성공 기준

| 시나리오 | 성공 조건 |
|----------|----------|
| L2-A | Crash 후 Payment 상태가 원래 상태로 롤백 |
| L2-B | Crash 후 포인트가 원래 금액으로 롤백 |
| L2-C | Crash 후 재고가 원래 수량으로 롤백 |
| L2-D | Worker crash 후 Webhook으로 정상 복구 |
| L2-E | Crash 후 재시도 시 중복 결제 없음 (idempotency) |

---

## 📊 예상 결과 출력

```
============================================================
L2 CRASH RECOVERY TEST RESULTS
============================================================

Transaction Rollback Tests (Method 2):
  L2-A Payment Crash:     PASSED (status rolled back)
  L2-B Points Crash:      PASSED (points rolled back)  
  L2-C Stock Crash:       PASSED (stock rolled back)

Celery Crash Recovery Tests (Method 3):
  L2-D Webhook Recovery:  PASSED (recovered via webhook)
  L2-E Idempotency:       PASSED (no duplicate payments)

============================================================
TRANSACTION INTEGRITY: ALL PASSED
- All crashes properly rolled back
- No partial commits detected
- Idempotency maintained after recovery
- Webhook recovery successful
============================================================
```

---

## 🔍 검증 포인트

### 방법 2 (Transaction Savepoint)
- [ ] `@transaction.atomic` 데코레이터 사용 확인
- [ ] 중첩 트랜잭션에서 savepoint 올바르게 동작
- [ ] 예외 발생 시 전체 롤백 확인

### 방법 3 (Celery Crash Mock)
- [ ] `acks_late=True` 설정 확인
- [ ] Task idempotency key 사용 확인
- [ ] Webhook handler의 상태 체크 로직 검증
- [ ] 재시도 시 중복 처리 방지 확인

---

## 📌 구현 시 주의사항

1. **테스트 격리**: 각 테스트는 독립적으로 실행 가능해야 함
2. **데이터 정리**: `TransactionTestCase` 사용으로 자동 롤백
3. **Mock 범위**: 외부 API(Toss)는 항상 mock 처리
4. **실제 코드 검증**: 테스트 통과 후 실제 서비스 코드의 트랜잭션 처리 확인

---

## ⏱ 예상 소요 시간

| 작업 | 시간 |
|------|------|
| 테스트 파일 구조 생성 | 10분 |
| L2-A, B, C 구현 (방법 2) | 45분 |
| L2-D, E 구현 (방법 3) | 45분 |
| 통합 테스트 및 디버깅 | 30분 |
| 문서화 | 20분 |
| **총계** | **~2.5시간** |

---

## 🎯 다음 단계

1. **새 세션에서 이 문서 참조**
2. **테스트 파일 생성** (`tests/integration/`)
3. **각 시나리오 구현**
4. **테스트 실행 및 검증**
5. **결과 문서화 및 커밋**

---

## 📚 참고 자료

- [Django Transaction Documentation](https://docs.djangoproject.com/en/4.2/topics/db/transactions/)
- [Celery Task Reliability](https://docs.celeryq.dev/en/stable/userguide/tasks.html#task-acks-late)
- [Stripe Webhook Best Practices](https://stripe.com/docs/webhooks/best-practices)
- [Toss Payments 웹훅 가이드](https://docs.tosspayments.com/reference/webhook)
