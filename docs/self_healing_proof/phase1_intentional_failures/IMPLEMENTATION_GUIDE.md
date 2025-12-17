# Phase 1 구현 가이드라인

> **문서 목적**: 실패 주입 구현 시 준수해야 할 가이드라인과 체크리스트 제공

---

## 1. 환경 변수 설계

### 1.1 Chaos Mode 환경 변수 체계

```bash
# 마스터 스위치
CHAOS_MODE=off|payment_delay|race_condition|task_failure|partial_failure|point_race|cb_race|idempotency_ttl

# 각 모드별 세부 설정
CHAOS_DELAY_SECONDS=30           # BP-1: 지연 시간 (soft_time_limit 초과)
CHAOS_RACE_DELAY_MS=500          # BP-3: 락 획득 전 지연
CHAOS_TASK_FAILURE_MODULO=10     # BP-7: N번째 결제마다 실패
CHAOS_PARTIAL_FAILURE_INDEX=1    # BP-5: N번째 상품에서 실패
CHAOS_POINT_RACE_DELAY_MS=200    # BP-6: 포인트 락 전 지연
CHAOS_CB_TRANSITION_DELAY_MS=100 # BP-9: 상태 전이 지연

# 확률 기반 설정 (0.0 ~ 1.0)
CHAOS_DELAY_PROBABILITY=0.3      # BP-1: 지연 발생 확률
CHAOS_RACE_PROBABILITY=0.3       # BP-3: 경쟁 지연 확률
CHAOS_POINT_RACE_PROBABILITY=0.2 # BP-6: 포인트 경쟁 확률
```

### 1.2 환경별 기본값

```python
# settings.py 또는 별도 config
CHAOS_CONFIG = {
    "development": {
        "CHAOS_MODE": "off",
    },
    "testing": {
        "CHAOS_MODE": os.getenv("CHAOS_MODE", "off"),
        "CHAOS_DELAY_SECONDS": 30,
        "CHAOS_RACE_DELAY_MS": 500,
        # ...
    },
    "staging": {
        "CHAOS_MODE": "off",  # 명시적 비활성화
    },
    "production": {
        "CHAOS_MODE": "off",  # 절대 활성화 금지
    },
}
```

---

## 2. 코드 구현 패턴

### 2.1 기본 패턴: 조건부 실패 주입

```python
import os
import time
import random
import logging

logger = logging.getLogger(__name__)

class ChaosInjector:
    """중앙 집중식 Chaos 주입 관리자"""
    
    @staticmethod
    def is_enabled(mode: str) -> bool:
        """특정 chaos 모드가 활성화되었는지 확인"""
        current_mode = os.getenv("CHAOS_MODE", "off")
        return current_mode == mode or current_mode == "all"
    
    @staticmethod
    def inject_delay(mode: str, delay_env: str, default_delay: float = 1.0) -> bool:
        """조건부 지연 주입"""
        if not ChaosInjector.is_enabled(mode):
            return False
        
        delay = float(os.getenv(delay_env, str(default_delay)))
        logger.warning(f"[CHAOS] Injecting delay: mode={mode}, delay={delay}s")
        time.sleep(delay)
        return True
    
    @staticmethod
    def should_fail(mode: str, probability_env: str, default_prob: float = 0.0) -> bool:
        """확률적 실패 결정"""
        if not ChaosInjector.is_enabled(mode):
            return False
        
        probability = float(os.getenv(probability_env, str(default_prob)))
        should = random.random() < probability
        
        if should:
            logger.warning(f"[CHAOS] Failure triggered: mode={mode}, prob={probability}")
        
        return should
```

### 2.2 적용 예시: BP-1 (결제 지연)

```python
# shopping/utils/toss_payment.py

from shopping.utils.chaos import ChaosInjector

class TossPaymentClient:
    def confirm_payment(self, payment_key: str, order_id: str, amount: int) -> dict:
        """결제 승인 API 호출"""
        
        # [BP-1] 조건부 지연 주입
        ChaosInjector.inject_delay(
            mode="payment_delay",
            delay_env="CHAOS_DELAY_SECONDS",
            default_delay=30.0
        )
        
        # 실제 API 호출
        response = requests.post(
            f"{self.base_url}/v1/payments/confirm",
            json={
                "paymentKey": payment_key,
                "orderId": order_id,
                "amount": amount,
            },
            headers=self._get_headers(),
            timeout=self.timeout,
        )
        
        return self._handle_response(response)
```

### 2.3 적용 예시: BP-3 (동시 승인 경쟁)

```python
# shopping/services/payment_service.py

from shopping.utils.chaos import ChaosInjector

class PaymentService:
    @staticmethod
    @transaction.atomic
    def confirm_payment_sync(payment, payment_key, order_id, amount, user):
        """결제 승인 처리 (동기 버전)"""
        
        # [BP-3] 락 획득 전 경쟁 지연
        if ChaosInjector.should_fail("race_condition", "CHAOS_RACE_PROBABILITY", 0.3):
            delay_ms = float(os.getenv("CHAOS_RACE_DELAY_MS", "500"))
            time.sleep(delay_ms / 1000)
        
        # 동시성 제어: 결제 객체를 락으로 보호
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        
        # 이미 처리된 결제인지 확인
        if payment.is_paid:
            raise PaymentConfirmError("이미 완료된 결제입니다.")
        
        # ... 나머지 로직
```

### 2.4 적용 예시: BP-7 (태스크 예외)

```python
# shopping/tasks/payment_tasks.py

from shopping.utils.chaos import ChaosInjector

class ChaosTaskException(Exception):
    """Chaos 테스트용 예외"""
    pass

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
            if ChaosInjector.is_enabled("task_failure"):
                modulo = int(os.getenv("CHAOS_TASK_FAILURE_MODULO", "10"))
                if payment_id % modulo == 0:
                    logger.warning(f"[CHAOS] Task failure for payment_id={payment_id}")
                    raise ChaosTaskException("Injected task failure")

            # 정상 처리 계속
            for order_item in order.order_items.select_for_update():
                # ...
```

---

## 3. 테스트 실행 가이드

### 3.1 Stage별 Chaos 설정

```bash
# Stage 4: Cancel Storm
export CHAOS_MODE=payment_delay
export CHAOS_DELAY_SECONDS=2  # 짧은 지연으로 race window 확대
export CHAOS_DELAY_PROBABILITY=0.2

locust -f load_tests/scenarios/stage4_cancel_storm.py \
    --host=http://localhost:8000 \
    --users=50 --spawn-rate=20 --run-time=2m

# Stage 5: Rollback
export CHAOS_MODE=task_failure
export CHAOS_TASK_FAILURE_MODULO=5  # 5번째 결제마다 실패

locust -f load_tests/scenarios/stage5_rollback.py \
    --host=http://localhost:8000 \
    --users=30 --spawn-rate=10 --run-time=3m

# Stage 7: Race Conflict
export CHAOS_MODE=race_condition
export CHAOS_RACE_PROBABILITY=0.5
export CHAOS_RACE_DELAY_MS=500

locust -f load_tests/scenarios/stage7_race_conflict.py \
    --host=http://localhost:8000 \
    --users=50 --spawn-rate=50 --run-time=2m

# Stage 10: Self-Healing
export CHAOS_MODE=payment_delay  # Circuit Breaker 트리거
export CHAOS_DELAY_SECONDS=35    # soft_time_limit 초과

locust -f load_tests/scenarios/stage10_self_healing.py \
    --host=http://localhost:8000 \
    --users=50 --spawn-rate=5 --run-time=3m
```

### 3.2 Docker Compose 환경 설정

```yaml
# docker-compose.test.yml 수정
services:
  web:
    environment:
      - CHAOS_MODE=${CHAOS_MODE:-off}
      - CHAOS_DELAY_SECONDS=${CHAOS_DELAY_SECONDS:-30}
      - CHAOS_RACE_DELAY_MS=${CHAOS_RACE_DELAY_MS:-500}
      - CHAOS_TASK_FAILURE_MODULO=${CHAOS_TASK_FAILURE_MODULO:-10}
      - CHAOS_DELAY_PROBABILITY=${CHAOS_DELAY_PROBABILITY:-0.3}
      - CHAOS_RACE_PROBABILITY=${CHAOS_RACE_PROBABILITY:-0.3}
```

---

## 4. 검증 체크리스트

### 4.1 구현 전 체크리스트

- [ ] 환경 변수가 비활성화 상태에서 정상 동작 확인
- [ ] 기존 테스트 스위트 통과 확인
- [ ] 영향 받는 트랜잭션 경계 파악
- [ ] 롤백 시나리오 문서화

### 4.2 구현 후 체크리스트

- [ ] `CHAOS_MODE=off`에서 성능 영향 없음 확인
- [ ] 로그에 `[CHAOS]` 프리픽스로 식별 가능
- [ ] 예상된 실패 신호 관찰 (DLQ, Circuit Breaker 등)
- [ ] 기존 테스트 여전히 통과

### 4.3 스테이지별 성공 기준

| Stage | 성공 기준 |
|-------|----------|
| Stage 4 | `cancel_success_rate` ≥ 30%, 재고 정합성 유지 |
| Stage 5 | `rollback_failed` = 0, 재고 증가 없음 |
| Stage 7 | `double_success` = 0, 중복 결제 없음 |
| Stage 10 | Control API 응답 < 2초, 거버넌스 규칙 정확한 거부 |

---

## 5. 주의사항 및 금지사항

### 5.1 절대 금지

❌ **프로덕션 환경에서 CHAOS_MODE 활성화**
```python
# settings.py에 추가 권장
if os.getenv("DJANGO_ENV") == "production":
    assert os.getenv("CHAOS_MODE", "off") == "off", \
        "CHAOS_MODE must be 'off' in production!"
```

❌ **테스트 코드 수정**
- 테스트 파일(stage*.py)은 그대로 유지
- 쇼핑 시스템 코드만 수정

❌ **비결정적 실패**
```python
# 나쁜 예: 완전 랜덤
if random.random() < 0.5:  # ❌ 재현 불가

# 좋은 예: 조건 기반 + 환경 변수 제어
if ChaosInjector.is_enabled("mode") and order_id % 10 == 0:  # ✓ 재현 가능
```

### 5.2 권장 사항

✅ **로깅 명확히**
```python
logger.warning(f"[CHAOS] {mode} triggered: {context}")
```

✅ **영향 범위 최소화**
```python
# 필요한 지점에만 최소한의 코드 추가
if ChaosInjector.is_enabled("mode"):
    do_something_minimal()
```

✅ **환경 변수 문서화**
```python
# 각 Chaos 환경 변수에 대한 docstring 또는 주석
# CHAOS_DELAY_SECONDS: BP-1에서 사용, soft_time_limit(25s) 초과 필요
```

---

## 6. 파일 구조 제안

```
shopping/
├── utils/
│   └── chaos.py              # ChaosInjector 클래스
├── services/
│   ├── payment_service.py    # BP-3, BP-4 주입
│   ├── order_service.py      # BP-5 주입
│   └── point_service.py      # BP-6 주입
├── tasks/
│   └── payment_tasks.py      # BP-7 주입
└── utils/
    └── toss_payment.py       # BP-1 주입
```

---

## 7. 다음 단계

1. **ChaosInjector 유틸리티 구현** (`shopping/utils/chaos.py`)
2. **우선순위 높은 BP 구현** (BP-1, BP-3, BP-7)
3. **각 Stage 테스트 실행 및 검증**
4. **나머지 BP 순차 구현**
5. **통합 테스트 및 문서 업데이트**

---

> **작성일**: 2025-12-17  
> **버전**: 1.0.0
