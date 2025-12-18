# Phase 2 구현 가이드라인

> **문서 목적**: Phase 2 실패 주입 구현 시 준수해야 할 원칙과 제약 조건

---

## 1. 핵심 원칙

### 1.1 불변 규칙

```
┌─────────────────────────────────────────────────────────────────────┐
│                         절대 금지 사항                                │
├─────────────────────────────────────────────────────────────────────┤
│  ❌ Self-Healing 로직 수정                                           │
│  ❌ 테스트 전용 숏컷 추가                                             │
│  ❌ 실패 주입 상시 활성화                                             │
│  ❌ 프로덕션 환경에서 Chaos 모드 활성화                                │
│  ❌ 실패 조건을 하드코딩                                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 필수 요건

| 요건 | 설명 | 검증 방법 |
|------|------|----------|
| **조건부** | 모든 실패는 환경 변수로 제어 | `CHAOS_*` 없으면 작동 안 함 |
| **재현 가능** | 동일 조건에서 동일 결과 | 결정론적 트리거 지원 |
| **설명 가능** | 실패 발생 시 로그로 추적 | `[CHAOS]` 접두사 로그 |
| **격리됨** | 다른 테스트에 영향 없음 | 테스트 간 상태 초기화 |
| **롤백 가능** | 환경 변수 해제 시 즉시 비활성화 | 재배포 불필요 |

### 1.3 Phase 2 vs Phase 1 차이점

| 차원 | Phase 1 | Phase 2 |
|------|---------|---------|
| **목적** | 핸들링 존재 확인 | 핸들링 **작동** 확인 |
| **실패 강도** | 단일 지점 | 다중/중첩 |
| **검증 대상** | 롤백 성공 | DLQ 라우팅, 포렌식 완전성 |
| **가정** | 시스템 통합됨 | 시스템 **분리됨** |

---

## 2. 환경 변수 체계

### 2.1 Phase 2 전용 환경 변수

```bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2 마스터 스위치
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHAOS_PHASE2_ENABLED=false  # 기본값: 비활성화

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 개별 Breakpoint 활성화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHAOS_BP21_ORPHAN_PG=false           # PG 성공 후 내부 실패
CHAOS_BP22_ROLLBACK_FAIL=false       # 롤백 실패
CHAOS_BP23_SILENT_TASK=false         # Celery 무음 실패
CHAOS_BP24_WEBHOOK_ORDER=false       # 웹훅 순서 역전
CHAOS_BP25_TTL_BOUNDARY=false        # 멱등성 TTL 경계
CHAOS_BP26_CB_DLQ_LINK=false         # CB↔DLQ 연동
CHAOS_BP27_RACE_WINDOW=false         # Race 윈도우 극대화
CHAOS_BP28_FORENSIC_EMPTY=false      # ForensicContext 미채움
CHAOS_BP29_POINT_ORPHAN=false        # 포인트 적립 실패
CHAOS_BP30_CACHE_MISS=false          # 캐시 무효화

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파라미터 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHAOS_PHASE2_DELAY_MS=3000           # 기본 지연 시간
CHAOS_PHASE2_PROBABILITY=1.0         # 트리거 확률 (테스트 시 100%)
CHAOS_PHASE2_TTL_DELAY_SEC=65        # TTL 경계 공격용 지연
```

### 2.2 환경별 기본값

```python
# settings/base.py
CHAOS_PHASE2_CONFIG = {
    "enabled": os.getenv("CHAOS_PHASE2_ENABLED", "false").lower() == "true",
    "delay_ms": int(os.getenv("CHAOS_PHASE2_DELAY_MS", "3000")),
    "probability": float(os.getenv("CHAOS_PHASE2_PROBABILITY", "0.0")),
}

# settings/production.py
CHAOS_PHASE2_CONFIG = {
    "enabled": False,  # 절대 활성화 금지
}

# settings/testing.py
CHAOS_PHASE2_CONFIG = {
    "enabled": os.getenv("CHAOS_PHASE2_ENABLED", "false").lower() == "true",
    "delay_ms": int(os.getenv("CHAOS_PHASE2_DELAY_MS", "100")),  # 테스트용 짧은 지연
    "probability": float(os.getenv("CHAOS_PHASE2_PROBABILITY", "1.0")),  # 100%
}
```

---

## 3. 코드 구현 패턴

### 3.1 Phase 2 Chaos 주입기

```python
# shopping/chaos/phase2.py

import os
import time
import random
import logging
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)


class Phase2ChaosInjector:
    """
    Phase 2 전용 Chaos 주입기
    
    Phase 1과 독립적으로 작동.
    더 파괴적인 실패 시나리오 지원.
    """
    
    @staticmethod
    def is_phase2_enabled() -> bool:
        """Phase 2 Chaos 모드 활성화 여부"""
        return os.getenv("CHAOS_PHASE2_ENABLED", "false").lower() == "true"
    
    @staticmethod
    def is_bp_enabled(bp_id: str) -> bool:
        """특정 Breakpoint 활성화 여부"""
        if not Phase2ChaosInjector.is_phase2_enabled():
            return False
        
        env_key = f"CHAOS_{bp_id.upper()}"
        return os.getenv(env_key, "false").lower() == "true"
    
    @staticmethod
    def should_trigger(probability: float = None) -> bool:
        """확률적 트리거 결정"""
        if probability is None:
            probability = float(os.getenv("CHAOS_PHASE2_PROBABILITY", "0.0"))
        return random.random() < probability
    
    @staticmethod
    def inject_delay(bp_id: str, delay_ms: int = None) -> bool:
        """조건부 지연 주입"""
        if not Phase2ChaosInjector.is_bp_enabled(bp_id):
            return False
        
        if not Phase2ChaosInjector.should_trigger():
            return False
        
        if delay_ms is None:
            delay_ms = int(os.getenv("CHAOS_PHASE2_DELAY_MS", "3000"))
        
        logger.warning(f"[CHAOS][Phase2][{bp_id}] Injecting delay: {delay_ms}ms")
        time.sleep(delay_ms / 1000)
        return True
    
    @staticmethod
    def inject_exception(bp_id: str, exception_class: type, message: str) -> None:
        """조건부 예외 주입"""
        if not Phase2ChaosInjector.is_bp_enabled(bp_id):
            return
        
        if not Phase2ChaosInjector.should_trigger():
            return
        
        logger.warning(f"[CHAOS][Phase2][{bp_id}] Injecting exception: {exception_class.__name__}")
        raise exception_class(f"[CHAOS][{bp_id}] {message}")
```

### 3.2 Breakpoint별 주입 함수

```python
# shopping/chaos/phase2_breakpoints.py

from .phase2 import Phase2ChaosInjector
from .decorators import PartialFailureException


def inject_bp21_orphan_pg(payment_id: int, pg_response: dict) -> None:
    """
    BP-21: PG 성공 후 내부 실패
    
    Toss API 호출 성공 후, DB 커밋 전에 예외 발생.
    고아 PG 트랜잭션 생성.
    """
    Phase2ChaosInjector.inject_exception(
        bp_id="BP21_ORPHAN_PG",
        exception_class=PartialFailureException,
        message=f"Simulated internal failure after PG success (payment_id={payment_id})"
    )


def inject_bp22_rollback_fail(order_id: int) -> None:
    """
    BP-22: 롤백 실패
    
    rollback_payment_failure 태스크 내에서 예외 발생.
    재시도 소진 시 2차 DLQ 생성 검증.
    """
    Phase2ChaosInjector.inject_exception(
        bp_id="BP22_ROLLBACK_FAIL",
        exception_class=Exception,
        message=f"Simulated rollback failure (order_id={order_id})"
    )


def inject_bp23_silent_task(task_name: str, task_id: str) -> None:
    """
    BP-23: Celery 무음 실패
    
    비동기 태스크 재시도 소진 유도.
    DLQ 라우팅 누락 탐지.
    """
    from .decorators import AsyncTaskChaosException
    
    Phase2ChaosInjector.inject_exception(
        bp_id="BP23_SILENT_TASK",
        exception_class=AsyncTaskChaosException,
        message=f"Simulated task failure ({task_name}, task_id={task_id})"
    )


def inject_bp25_ttl_delay() -> None:
    """
    BP-25: 멱등성 TTL 경계 공격
    
    Redis TTL(60초) 초과 지연 주입.
    멱등성 2차 방어 검증.
    """
    delay_sec = int(os.getenv("CHAOS_PHASE2_TTL_DELAY_SEC", "65"))
    
    if Phase2ChaosInjector.is_bp_enabled("BP25_TTL_BOUNDARY"):
        logger.warning(f"[CHAOS][Phase2][BP25] Injecting {delay_sec}s delay for TTL boundary")
        time.sleep(delay_sec)


def inject_bp27_race_delay(context: str) -> None:
    """
    BP-27: Race 윈도우 극대화
    
    락 획득 전 지연으로 경쟁 조건 유도.
    """
    Phase2ChaosInjector.inject_delay(
        bp_id="BP27_RACE_WINDOW",
        delay_ms=int(os.getenv("CHAOS_PHASE2_DELAY_MS", "3000"))
    )
```

### 3.3 적용 예시

```python
# shopping/services/payment_service.py

from shopping.chaos.phase2_breakpoints import (
    inject_bp21_orphan_pg,
    inject_bp27_race_delay,
)

class PaymentService:
    @staticmethod
    @transaction.atomic
    def confirm_payment_sync(payment, payment_key, order_id, amount, user):
        # [BP-27] Race 윈도우 확대
        inject_bp27_race_delay("confirm_pre_lock")
        
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        
        # ... 상태 검증 ...
        
        # Toss API 호출
        payment_data = toss_client.confirm_payment(...)
        
        # [BP-21] PG 성공 후 내부 실패
        inject_bp21_orphan_pg(payment_id=payment.id, pg_response=payment_data)
        
        # ... 나머지 처리 ...
```

---

## 4. 테스트 격리

### 4.1 Fixture 설정

```python
# tests/conftest.py

import pytest
import os


@pytest.fixture(autouse=True)
def reset_chaos_phase2():
    """모든 테스트 전후로 Phase 2 Chaos 비활성화"""
    # 테스트 전: 모든 BP 비활성화
    chaos_vars = [k for k in os.environ if k.startswith("CHAOS_")]
    original_values = {k: os.environ.get(k) for k in chaos_vars}
    
    for var in chaos_vars:
        if var in os.environ:
            del os.environ[var]
    
    yield
    
    # 테스트 후: 원래 값 복원
    for var, value in original_values.items():
        if value is not None:
            os.environ[var] = value
        elif var in os.environ:
            del os.environ[var]


@pytest.fixture
def enable_bp21():
    """BP-21 활성화 fixture"""
    os.environ["CHAOS_PHASE2_ENABLED"] = "true"
    os.environ["CHAOS_BP21_ORPHAN_PG"] = "true"
    os.environ["CHAOS_PHASE2_PROBABILITY"] = "1.0"
    yield
    # cleanup은 reset_chaos_phase2가 처리
```

### 4.2 테스트 작성 패턴

```python
# tests/phase2/test_bp21_orphan_pg.py

import pytest
from shopping.models import Payment, FailedOperation


class TestBP21OrphanPG:
    """BP-21: PG 성공 후 내부 실패 테스트"""
    
    @pytest.mark.chaos_phase2
    def test_dlq_created_on_partial_failure(self, enable_bp21, payment_fixture):
        """PG 성공 후 내부 실패 시 DLQ 생성 확인"""
        # Given
        payment = payment_fixture
        
        # When
        with pytest.raises(PaymentConfirmError):
            PaymentService.confirm_payment_sync(
                payment=payment,
                payment_key="test_key",
                order_id=payment.order.id,
                amount=payment.amount,
                user=payment.order.user
            )
        
        # Then
        payment.refresh_from_db()
        assert payment.is_paid == False, "DB should be rolled back"
        
        failed_op = FailedOperation.objects.filter(
            failure_type="PARTIAL_FAILURE_POST_PG"
        ).first()
        
        assert failed_op is not None, "DLQ entry should be created"
        assert "pg_response" in failed_op.forensic_context
    
    @pytest.mark.chaos_phase2
    def test_forensic_context_complete(self, enable_bp21, payment_fixture):
        """ForensicContext 완전성 검증"""
        # ... 테스트 ...
```

---

## 5. 로깅 표준

### 5.1 로그 형식

```
[CHAOS][Phase2][BP-XX] <action>: <details>
```

### 5.2 예시

```python
# 지연 주입
logger.warning("[CHAOS][Phase2][BP-27] Injecting delay: 3000ms")

# 예외 주입
logger.warning("[CHAOS][Phase2][BP-21] Injecting exception: PartialFailureException")

# 트리거 결정
logger.debug("[CHAOS][Phase2][BP-23] Trigger check: probability=1.0, triggered=True")
```

### 5.3 로그 레벨

| 이벤트 | 레벨 | 이유 |
|--------|------|------|
| Chaos 활성화 | WARNING | 비정상 상태 명시 |
| 예외 주입 | WARNING | 의도적 실패 추적 |
| 트리거 결정 | DEBUG | 디버깅용 |
| BP 비활성화로 스킵 | DEBUG | 불필요한 노이즈 방지 |

---

## 6. 검증 체크리스트

### 6.1 구현 완료 체크리스트

| BP | 주입 함수 | 환경 변수 | 테스트 | 로그 |
|----|----------|----------|--------|------|
| BP-21 | ☐ | ☐ | ☐ | ☐ |
| BP-22 | ☐ | ☐ | ☐ | ☐ |
| BP-23 | ☐ | ☐ | ☐ | ☐ |
| BP-24 | ☐ | ☐ | ☐ | ☐ |
| BP-25 | ☐ | ☐ | ☐ | ☐ |
| BP-26 | ☐ | ☐ | ☐ | ☐ |
| BP-27 | ☐ | ☐ | ☐ | ☐ |
| BP-28 | ☐ | ☐ | ☐ | ☐ |
| BP-29 | ☐ | ☐ | ☐ | ☐ |
| BP-30 | ☐ | ☐ | ☐ | ☐ |

### 6.2 Self-Healing 반응 검증

| BP | DLQ 생성 | ForensicContext | CB 상태 | Replay |
|----|---------|-----------------|---------|--------|
| BP-21 | ☐ PARTIAL_FAILURE_POST_PG | ☐ pg_response | N/A | N/A |
| BP-22 | ☐ ROLLBACK_FAILURE | ☐ stock_before/after | N/A | N/A |
| BP-23 | ☐ ASYNC_TASK_FAILURE | ☐ task_id | N/A | N/A |
| BP-26 | N/A | N/A | ☐ state 변경 | ☐ 트리거 |

---

## 7. 금지 사항 상세

### 7.1 Self-Healing 로직 수정 금지

```python
# ❌ 금지: DLQ 서비스 수정
class DLQService:
    def store_failure(self, ...):
        if os.getenv("TEST_MODE"):  # ← 테스트 전용 분기 금지
            return self._store_test(...)
        return self._store_real(...)

# ✅ 허용: 쇼핑 시스템에서 DLQ 호출
class PaymentService:
    def confirm_payment_sync(self, ...):
        try:
            ...
        except PartialFailureException as e:
            # 기존 로직대로 DLQ 호출 (수정 아님, 누락된 호출 추가)
            DLQService().store_failure(...)
            raise
```

### 7.2 테스트 전용 숏컷 금지

```python
# ❌ 금지: 테스트용 바이패스
def should_skip_validation() -> bool:
    return os.getenv("SKIP_VALIDATION") == "true"

# ✅ 허용: 프로덕션에서도 작동하는 조건부 실패
def inject_chaos_failure() -> None:
    if not chaos_config.is_enabled():
        return  # 평상시에는 아무 일도 안 함
    # 실패 주입
```

### 7.3 실패 조건 하드코딩 금지

```python
# ❌ 금지: 하드코딩된 조건
if payment.id == 12345:  # 특정 ID에서만 실패
    raise Exception("Chaos failure")

# ✅ 허용: 환경 변수 기반 조건
if os.getenv("CHAOS_BP21_ORPHAN_PG") == "true":
    if random.random() < float(os.getenv("CHAOS_PHASE2_PROBABILITY", "0")):
        raise Exception("Chaos failure")
```

---

## 8. 결론

Phase 2 구현은:

1. **파괴적**이어야 하지만 **제어 가능**해야 함
2. **시스템 분리 후 취약점**에 집중
3. **DLQ 라우팅 누락**을 탐지하는 것이 핵심 목표
4. Phase 1과 동일한 코드 품질 표준 유지

구현 시작 전 이 문서를 숙지하고, 각 BP 구현 후 체크리스트 검증 필수.
