# Phase 3: Replay/비즈니스 로직 격리

> **목표**: 코어 패키지에서 쇼핑 도메인 전용 ReplayHandler와 비즈니스 판정 로직 제거
>
> **핵심**: 코어는 추상 인터페이스만 정의, 어댑터에서 구체 핸들러 주입

---

## 상태

| 항목 | 상태 |
|------|------|
| 전체 진행 | ✅ 완료 |
| 완료 일시 | 2025-12-14 |

---

## 3-1. 도메인별 ReplayHandler 코어에서 제거

### 작업 내역

| # | 작업 | 상태 |
|---|------|------|
| 1 | `PaymentReplayHandler` 코어에서 제거 | ✅ 완료 |
| 2 | `PointReplayHandler` 코어에서 제거 | ✅ 완료 |
| 3 | `WebhookReplayHandler` 코어에서 제거 | ✅ 완료 |
| 4 | `shopping/services/self_healing/replay_handlers.py` 생성 | ✅ 완료 |

### 결과 구조

```
packages/selfhealing-python/
└── src/selfhealing/services/
    └── replay_service.py          ← ReplayHandler 추상 클래스 + DefaultReplayHandler만 존재

shopping/services/self_healing/
└── replay_handlers.py             ← PaymentReplayHandler, PointReplayHandler, WebhookReplayHandler
```

### 코어 replay_service.py 상태

```python
class ReplayHandler(ABC):
    """Abstract base class for domain-specific replay handlers."""
    
    @abstractmethod
    def can_handle(self, failed_operation: FailedOperationData) -> bool:
        """Check if this handler can handle the given operation."""
        pass
    
    @abstractmethod
    def replay(self, failed_operation: FailedOperationData) -> ReplayResult:
        """Attempt to replay the failed operation."""
        pass


class DefaultReplayHandler(ReplayHandler):
    """Default handler that marks operations as requiring review."""
    
    def can_handle(self, failed_operation: FailedOperationData) -> bool:
        return True  # Catch-all handler
    
    def replay(self, failed_operation: FailedOperationData) -> ReplayResult:
        return ReplayResult(
            success=False,
            status=ReplayStatus.REQUIRES_REVIEW,
            message="No specific handler available, requires manual review",
        )
```

---

## 3-2. 비즈니스 상태 판정 로직 제거

### 작업 내역

| # | 판정 로직 | 상태 |
|---|----------|------|
| 1 | `payment_is_paid` 판정 | ✅ 제거됨 |
| 2 | `order_status == "cancelled"` 판정 | ✅ 제거됨 |
| 3 | `is_order_completed()` 함수 | ✅ 제거됨 |
| 4 | `should_skip_replay()` 비즈니스 조건 | ✅ 제거됨 |

### 검증 결과

```bash
cd packages/selfhealing-python
grep -rn --include="*.py" \
  "payment_is_paid\|order_status.*cancelled\|is_paid\|is_order_completed" \
  src/selfhealing/
# 결과: 0건 ✅
```

---

## 3-3. PG/Vendor API 호출 제거

### 작업 내역

| # | 호출 | 상태 |
|---|------|------|
| 1 | `from shopping.tasks... import call_toss_confirm_api` | ✅ 제거됨 |
| 2 | `call_toss_confirm_api.delay(...)` | ✅ 제거됨 |
| 3 | `TossPaymentService` 직접 호출 | ✅ 제거됨 |

### 검증 결과

```bash
cd packages/selfhealing-python
grep -rn --include="*.py" \
  "call_toss_confirm_api\|\.delay(" \
  src/selfhealing/services/replay_service.py
# 결과: 주석만 존재 (실행 코드 0건) ✅
```

---

## 어댑터 등록 패턴

### 쇼핑 어댑터 초기화 예시

```python
# shopping/services/self_healing/__init__.py

from selfhealing.services.replay_service import ReplayService
from .replay_handlers import (
    PaymentReplayHandler,
    PointReplayHandler,
    WebhookReplayHandler,
)


def register_shopping_handlers():
    """Register shopping domain replay handlers with the core service."""
    replay_service = ReplayService()
    
    replay_service.register_handler(PaymentReplayHandler())
    replay_service.register_handler(PointReplayHandler())
    replay_service.register_handler(WebhookReplayHandler())
```

### 핸들러 우선순위

1. 도메인별 핸들러 (`can_handle() == True` 순서)
2. `DefaultReplayHandler` (catch-all)

---

## Phase 3 검증 체크포인트

```bash
cd packages/selfhealing-python

# Replay Neutrality Test
grep -rn --include="*.py" \
  "is_paid\|order_status.*cancelled\|payment_is_paid\|call_toss_confirm_api" \
  src/selfhealing/

# 목표: 0건

# shopping import 검사 (replay_service.py)
grep -rn "from shopping\|import shopping" src/selfhealing/services/replay_service.py

# 목표: 주석만 또는 0건
```

---

## 완료 조건

- [x] 코어에 `PaymentReplayHandler`, `WebhookReplayHandler` 등 없음 ✅
- [x] 코어에 `is_paid`, `cancelled`, `order.status` 판정 없음 ✅
- [x] 코어에 `call_toss_*`, `*.delay()` 등 외부 호출 없음 ✅
- [x] 쇼핑 어댑터에 도메인 핸들러 이동 완료 ✅
- [x] 핸들러 등록 패턴 동작 확인 ✅

---

## 작업 로그

| 날짜 | 작업 | 결과 |
|------|------|------|
| 2025-12-14 | PaymentReplayHandler 코어에서 제거 | ✅ 완료 |
| 2025-12-14 | PointReplayHandler 코어에서 제거 | ✅ 완료 |
| 2025-12-14 | WebhookReplayHandler 코어에서 제거 | ✅ 완료 |
| 2025-12-14 | shopping/replay_handlers.py 생성 | ✅ 완료 |
| 2025-12-14 | 비즈니스 판정 로직 제거 | ✅ 완료 |
| 2025-12-14 | shopping import/delay 제거 | ✅ 완료 |
| 2025-12-14 | Phase 3 검증 통과 | ✅ 완료 |

---

*문서 생성일: 2025-12-14*
*Phase 완료일: 2025-12-14*
