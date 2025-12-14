# Phase 0.5: 도메인 순수성 감사 및 Legacy Residue Purge

> **목표**: 1차 분리 이후에도 코어 패키지에 잔존하는 "deprecated 처리되었으나 여전히 존재·노출되는 쇼핑 도메인 개념"을 완전히 제거하거나 어댑터 레이어로 격리한다.
>
> 이 단계는 **기능 변경이 아닌 계약 정화(contract sanitation)** 작업이다.

---

## 감사 일시

- **1차 감사**: 2025-12-14
- **2차 감사 (전체)**: 2025-12-14

---

## 감사 요약

| 범주 | 항목 수 | 설명 |
|------|---------|------|
| ❌ **즉시 수정 필요** | 29개 | 코어 패키지에 쇼핑 도메인 잔재 존재 |
| ⚠️ **분리 위험** | 20개 | deprecated이나 여전히 접근 가능 (Phase 1에서 처리) |
| ✅ **분리 안전** | 5개 | 도메인 중립적 구현 확인 |
| ℹ️ **테스트 코드** | 다수 | `tests/` 디렉토리 - 코어 범위 외 |

---

## 감사 범위

- **포함**: `packages/selfhealing-python/src/selfhealing/` (코어 소스)
- **제외**: `packages/selfhealing-python/tests/` (테스트 코드 - 실제 시스템 영향 없음)
- **주의**: 테스트 코드에 `toss` 언급 있으나, 테스트 시나리오로서 허용

---

## 감사 대상 파일

```
packages/selfhealing-python/src/selfhealing/
├── interfaces/
│   ├── repositories.py          ❌ enum alias, property, 파라미터
│   └── payment_provider.py      ❌ 결제 전용 인터페이스 전체
├── core/
│   ├── config.py                ❌ SLAConfig property
│   ├── forensic.py              ❌ create_shopping_snapshot_data, StateSnapshot property
│   └── types.py                 ❌ FailureType, DomainType enum alias
├── services/
│   ├── idempotency_service.py   ❌ IdempotencyDomain, check_payment 등
│   ├── security_violation_service.py ❌ ViolationType, order_id/payment_id
│   └── factory/registry.py      ❌ _default_payment = "toss"
├── metrics/
│   └── prometheus.py            ❌ DOMAINS 상수
├── api/django/views/
│   ├── dashboard.py             ❌ from shopping.models import
│   ├── health.py                ❌ from shopping.models import
│   ├── circuit_breaker.py       ❌ from shopping.models import
│   └── dlq.py                   ❌ from shopping.models import
└── adapters/
    ├── fastapi/routes.py        ❌ order_id, payment_id
    └── sqlalchemy/              ❌ order_id, payment_id
```

---

## ❌ 0.5-A: Legacy Enum Alias (즉시 제거 필요)

### 문제점
deprecated 주석이 있으나 enum 멤버로 여전히 존재하여 코어 import 시 쇼핑 도메인 노출.

### 발견 항목

#### repositories.py - FailedOperationDomain
| 라인 | 코드 | 상태 |
|------|------|------|
| 50 | `PAYMENT = "external_service"  # @deprecated` | ⬜ 미제거 |
| 51 | `POINT = "internal_process"  # @deprecated` | ⬜ 미제거 |
| 52 | `INVENTORY = "internal_process"  # @deprecated` | ⬜ 미제거 |
| 53 | `WEBHOOK = "external_service"  # @deprecated` | ⬜ 미제거 |

#### repositories.py - SecurityIncidentType
| 라인 | 코드 | 상태 |
|------|------|------|
| 91 | `WEBHOOK_SIGNATURE_INVALID = "signature_invalid"  # @deprecated` | ⬜ 미제거 |
| 92 | `PAYMENT_AMOUNT_TAMPERED = "data_tampered"  # @deprecated` | ⬜ 미제거 |

#### types.py - FailureType
| 라인 | 코드 | 상태 |
|------|------|------|
| 29 | `PAYMENT = "external_service"  # @deprecated` | ⬜ 미제거 |
| 30 | `INVENTORY = "internal_process"  # @deprecated` | ⬜ 미제거 |

#### types.py - DomainType
| 라인 | 코드 | 상태 |
|------|------|------|
| 63 | `PAYMENT = "external_service"  # @deprecated` | ⬜ 미제거 |
| 64 | `ORDER = "external_service"  # @deprecated` | ⬜ 미제거 |
| 65 | `INVENTORY = "internal_process"  # @deprecated` | ⬜ 미제거 |
| 66 | `SHIPPING = "external_service"  # @deprecated` | ⬜ 미제거 |
| 67 | `USER = "internal_process"  # @deprecated` | ⬜ 미제거 |

### 처리 방안
- [ ] 모든 deprecated enum alias 완전 삭제
- [ ] 쇼핑 어댑터에서 필요시 자체 enum 정의

---

## ❌ 0.5-B: Legacy Property Accessor (즉시 제거 필요)

### 문제점
`entity_refs` 또는 `thresholds_by_domain` 기반으로 일반화되어 있으나, 편의 property가 쇼핑 용어를 코어에 노출.

### 발견 항목

#### repositories.py - FailedOperationData
| 라인 | 코드 | 상태 |
|------|------|------|
| 144-146 | `order_id` property | ⬜ 미제거 |
| 149-151 | `payment_id` property | ⬜ 미제거 |

#### repositories.py - SecurityIncidentData
| 라인 | 코드 | 상태 |
|------|------|------|
| 283-285 | `order_id` property | ⬜ 미제거 |
| 288-290 | `payment_id` property | ⬜ 미제거 |

#### config.py - SLAConfig
| 라인 | 코드 | 상태 |
|------|------|------|
| 99-102 | `payment_hours` property | ⬜ 미제거 |
| 105-107 | `point_hours` property | ⬜ 미제거 |
| 110-112 | `inventory_hours` property | ⬜ 미제거 |
| 115-117 | `webhook_hours` property | ⬜ 미제거 |
| 120-122 | `notification_hours` property | ⬜ 미제거 |

#### config.py - IdempotencyConfig
| 라인 | 코드 | 상태 |
|------|------|------|
| 141 | `payment_cache_ttl: int = 300` | ⬜ 미중립화 |
| 142 | `webhook_cache_ttl: int = 60` | ⬜ 미중립화 |

#### forensic.py - StateSnapshot
| 라인 | 코드 | 상태 |
|------|------|------|
| 104-112 | `order_status` property + setter | ⬜ 미제거 |
| 114-121 | `payment_status` property + setter | ⬜ 미제거 |
| 124-131 | `user_points` property + setter | ⬜ 미제거 |
| 134-140 | `product_stock` property + setter | ⬜ 미제거 |

### 처리 방안
- [ ] 코어에서 모든 쇼핑 용어 property 제거
- [ ] 필요시 어댑터 레이어에서만 제공

---

## ❌ 0.5-C: Legacy Helper/Snapshot 함수 (즉시 제거 필요)

### 문제점
deprecated 주석이 있으나 함수가 코어에 여전히 존재.

### 발견 항목

#### forensic.py - create_shopping_snapshot_data
| 라인 | 파라미터 | 상태 |
|------|----------|------|
| 575-644 | 함수 전체 | ⬜ 미제거 |
| 576 | `order_id: Optional[int]` | ⬜ |
| 583 | `payment_id: Optional[int]` | ⬜ |
| 585 | `toss_order_id: Optional[str]` | ⬜ |
| 584 | `payment_key: Optional[str]` | ⬜ |

#### forensic.py - ForensicContext.capture_state_before/after
| 라인 | 파라미터 | 상태 |
|------|----------|------|
| 245-270 | `order_status`, `payment_status`, `user_points`, `product_stocks` | ⬜ 미제거 |

### 처리 방안
- [ ] `create_shopping_snapshot_data()` 함수 완전 제거
- [ ] `capture_state_*` 메서드의 쇼핑 파라미터를 `**extra`로 대체

---

## ❌ 0.5-D: Payment/PG 전용 인터페이스 (격리 필요)

### 문제점
Self-Healing 코어에 결제 전용 인터페이스가 존재. 결제가 없는 시스템에서 무의미.

### 발견 항목

#### payment_provider.py (전체 파일)
| 라인 | 클래스/함수 | 상태 |
|------|-------------|------|
| 28-53 | `PaymentConfirmResult` | ⬜ 미이동 |
| 55-79 | `PaymentCancelResult` | ⬜ 미이동 |
| 81-97 | `WebhookVerifyResult` | ⬜ 미이동 |
| 99-152 | `PaymentStatusResult` | ⬜ 미이동 |
| 154-295 | `PaymentProviderInterface` | ⬜ 미이동 |
| 77 | `# Toss-specific` 주석 | ⬜ |
| 173 | `TossPaymentAdapter (current)` 언급 | ⬜ |

#### adapters/__init__.py (Export 문제)
| 라인 | 코드 | 상태 |
|------|------|------|
| 15 | `TossPaymentAdapter (Toss Payments - Korean PG)` docstring | ⬜ |
| 33 | `TossPaymentAdapter` import | ⬜ |
| 59 | `TossPaymentAdapter` import | ⬜ |
| 90 | `"TossPaymentAdapter"` __all__ | ⬜ |

#### adapters/payments/__init__.py
| 라인 | 코드 | 상태 |
|------|------|------|
| 8 | `TossPaymentAdapter: Toss Payments` docstring | ⬜ |

### 처리 방안
**옵션 A**: `TossPaymentAdapter`를 별도 패키지(`selfhealing-shopping`)로 분리
**옵션 B**: adapters/payments/ 자체를 optional extras로 변경

- [ ] 옵션 선택 후 적용

---

## ❌ 0.5-E: Default Provider/Vendor 값 (즉시 제거 필요)

### 문제점
코어에 특정 벤더(Toss) 또는 쇼핑 도메인 목록이 하드코딩됨.

### 발견 항목

#### registry.py
| 라인 | 코드 | 상태 |
|------|------|------|
| 61 | `_default_payment: str = "toss"` | ⬜ 미제거 |

#### prometheus.py
| 라인 | 코드 | 상태 |
|------|------|------|
| 32-38 | `DOMAINS = ["payment", "point", "inventory", "webhook", "notification"]` | ⬜ 미동적화 |

#### idempotency_service.py
| 라인 | 코드 | 상태 |
|------|------|------|
| 8 | `Payment: toss_order_id + payment_key` 주석 | ⬜ |
| 97, 410 | `payment_key: Toss payment key` docstring | ⬜ |

### 처리 방안
- [ ] `_default_payment` 기본값 제거 또는 `None`으로 변경
- [ ] `DOMAINS` 상수를 동적 등록 방식으로 변경
- [ ] Toss 언급 docstring 중립화

---

## ❌ 추가 발견: IdempotencyService 도메인 특화 (즉시 제거 필요)

### 문제점
코어 서비스가 쇼핑 도메인 전용 메서드와 enum을 직접 제공.

### 발견 항목

#### idempotency_service.py - IdempotencyDomain Enum
| 라인 | 코드 | 상태 |
|------|------|------|
| 39-46 | `PAYMENT`, `WEBHOOK`, `POINT`, `INVENTORY`, `NOTIFICATION` | ⬜ 미제거 |

#### idempotency_service.py - IdempotencyKey Factory Methods
| 라인 | 메서드 | 상태 |
|------|--------|------|
| 73-89 | `for_payment(order_id, amount)` | ⬜ 미제거 |
| 91-114 | `for_payment_confirm(payment_key, order_id, amount)` | ⬜ 미제거 |
| 116-130 | `for_webhook(event_id)` | ⬜ 미제거 |
| 132-156 | `for_point_operation(order_id, point_type, amount)` | ⬜ 미제거 |
| 158-175 | `for_inventory(order_item_id, action)` | ⬜ 미제거 |

#### idempotency_service.py - Domain-Specific Check Methods
| 라인 | 메서드 | 상태 |
|------|--------|------|
| 322-394 | `check_payment(order_id, amount)` | ⬜ 미제거 |
| 396-478 | `check_payment_confirm(payment_key, order_id, amount)` | ⬜ 미제거 |
| 480-548 | `check_webhook(event_id)` | ⬜ 미제거 |
| 550-620 | `check_point_operation(order_id, point_type, amount)` | ⬜ 미제거 |

#### idempotency_service.py - from shopping import
| 라인 | 코드 | 상태 |
|------|------|------|
| 348 | `from shopping.models.payment import Payment` | ⬜ |
| 426 | `from shopping.models.payment import Payment` | ⬜ |
| 496 | `from shopping.models.webhook_event import WebhookEvent` | ⬜ |
| 566 | `from shopping.models.point import PointHistory` | ⬜ |

### 처리 방안
- [ ] `IdempotencyDomain` enum을 도메인 중립적으로 변경 또는 제거
- [ ] 도메인 특화 factory methods를 어댑터로 이동
- [ ] 도메인 특화 check methods를 범용 `check(key, lookup_fn)` 형태로 변경
- [ ] shopping import를 어댑터로 이동

---

## ❌ 추가 발견: SecurityViolationService 도메인 특화 (즉시 제거 필요)

### 문제점
보안 위협 분류 체계가 쇼핑 도메인 언어를 전제.

### 발견 항목

#### security_violation_service.py - ViolationType Enum
| 라인 | 코드 | 상태 |
|------|------|------|
| 44 | `WEBHOOK_SIGNATURE_INVALID` | ⬜ 미중립화 |
| 45 | `PAYMENT_AMOUNT_TAMPERED` | ⬜ 미중립화 |

#### security_violation_service.py - SEVERITY_BY_VIOLATION_TYPE
| 라인 | 코드 | 상태 |
|------|------|------|
| 60-68 | 쇼핑 ViolationType 매핑 포함 | ⬜ |

#### security_violation_service.py - handle_violation 파라미터
| 라인 | 파라미터 | 상태 |
|------|----------|------|
| 215 | `order_id: Optional[int]` | ⬜ 미제거 |
| 216 | `payment_id: Optional[int]` | ⬜ 미제거 |

#### security_violation_service.py - 비즈니스 메시지
| 라인 | 코드 | 상태 |
|------|------|------|
| 340 | `"Payment blocked, order frozen for investigation"` | ⬜ 미중립화 |

### 처리 방안
- [ ] `ViolationType` 쇼핑 값을 중립적 이름으로 변경
- [ ] `order_id`, `payment_id` 파라미터를 `entity_refs: dict`로 변경
- [ ] 비즈니스 메시지 중립화

---

## ❌ 추가 발견: Repository Interface 파라미터 (즉시 제거 필요)

### 문제점
Repository 인터페이스가 `order_id`, `payment_id`를 명시적 파라미터로 정의.

### 발견 항목

#### repositories.py - FailedOperationRepository.create()
| 라인 | 파라미터 | 상태 |
|------|----------|------|
| 331 | `order_id: Optional[int] = None` | ⬜ 미제거 |
| 332 | `payment_id: Optional[int] = None` | ⬜ 미제거 |

#### repositories.py - SecurityIncidentRepository.create()
| 라인 | 파라미터 | 상태 |
|------|----------|------|
| 795 | `order_id: Optional[int] = None` | ⬜ 미제거 |
| 796 | `payment_id: Optional[int] = None` | ⬜ 미제거 |

### 처리 방안
- [ ] `order_id`, `payment_id` 파라미터 제거
- [ ] `entity_refs: dict[str, int] = None` 파라미터로 대체

---

## ❌ 추가 발견: Adapter 레이어 잔재

### FastAPI Routes
| 파일 | 라인 | 잔재 |
|------|------|------|
| fastapi/routes.py | 102-103 | Request model에 `order_id`, `payment_id` 필드 |
| fastapi/routes.py | 358-359, 404-405 | Response에 `order_id`, `payment_id` 사용 |

### SQLAlchemy Adapters
| 파일 | 라인 | 잔재 |
|------|------|------|
| sqlalchemy/security_incident.py | 40-41, 59-60, 74-75 | `order_id`, `payment_id` 필드/파라미터 |

### 처리 방안
- [ ] FastAPI routes: `entity_refs` 기반으로 변경
- [ ] SQLAlchemy: `entity_refs` JSONB 컬럼으로 변경 또는 범용 참조 테이블

---

## ❌ 추가 발견: from shopping import (Phase 1 관련)

### 문제점
코어 패키지에서 쇼핑 앱 직접 import 시 런타임 장애.

### 발견 항목 (11건)

| 파일 | 라인 | import |
|------|------|--------|
| services/__init__.py | 8 | `from shopping.services.self_healing import CircuitBreakerService` |
| services/circuit_breaker/manual_control.py | 286 | `from shopping.models.failed_payment import CircuitBreakerState` |
| services/idempotency_service.py | 348, 426, 496, 566 | `from shopping.models.*` |
| config.py | 85-88 | `from shopping.services.self_healing.config import ...` |
| adapters/django_repos/failed_operation.py | 33 | `from shopping.models.failed_operation import FailedOperation` |
| adapters/django_repos/circuit_breaker.py | 33 | `from shopping.models.failed_payment import CircuitBreakerState` |
| adapters/django_repos/security_incident.py | 34 | `from shopping.models.security_incident import SecurityIncident` |
| adapters/django_repos/rate_limit.py | 28 | `from shopping.models.rate_limit_state import RateLimitState` |
| adapters/django/repositories.py | 43, 349, 724 | `from shopping.models.*` |
| api/django/stress_views.py | 309 | `FROM shopping_product` SQL 쿼리 |

### 처리 방안
- [ ] **Phase 1에서 처리**: 모든 shopping import를 try-except fallback 또는 어댑터 주입 방식으로 변경

---

## ✅ 분리 안전 (확인 완료)

| 항목 | 파일 | 상태 |
|------|------|------|
| 핵심 Circuit Breaker 로직 | `services/circuit_breaker/` | ✅ `service_name: str` 기반, 도메인 중립 |
| Backoff/Retry 계산 | `core/backoff.py`, `services/retry_handler.py` | ✅ 순수 수학적 로직 |
| 시간 제공자 | `core/timezone.py`, `core/time_provider.py` | ✅ 순수 시간 처리 유틸리티 |
| ReplayService 핸들러 등록 패턴 | `services/replay_service.py` | ✅ 어댑터 주입 방식 |
| DTO entity_refs 구조 | `interfaces/repositories.py` | ✅ `entity_refs: dict[str, int]` 도메인 중립 |

---

## 이식 시뮬레이션: IoT 데이터 수집 시스템

| 항목 | 결과 | 설명 |
|------|------|------|
| import 성공 | ❌ 실패 | `adapters/django_repos/`가 `shopping.models.*` import |
| 메트릭 의미 | ⚠️ 왜곡 | `DOMAINS = ["payment", ...]` 라벨이 IoT와 무관 |
| 보안 위반 분류 | ❌ 의미 없음 | `PAYMENT_AMOUNT_TAMPERED`가 센서 데이터와 무관 |
| DLQ 기록 | ⚠️ 혼란 | `order_id`, `payment_id` 파라미터가 무의미 |
| Idempotency 체크 | ❌ 사용 불가 | `check_payment()`, `check_webhook()` 함수가 무관 |

---

## Phase 0.5 완료 조건

- [ ] deprecated 쇼핑 enum alias 0개
- [ ] 쇼핑 용어 property accessor 0개
- [ ] 쇼핑 전용 helper / snapshot 함수 0개
- [ ] Payment/PG 인터페이스 코어에서 제거
- [ ] default vendor / provider 하드코딩 0개
- [ ] IdempotencyDomain 쇼핑 값 0개
- [ ] ViolationType 쇼핑 값 0개
- [ ] Repository create() 파라미터에서 order_id/payment_id 0개

---

## 검증 체크포인트

```bash
# Legacy 쇼핑 도메인 노출 검사 (코어 기준)
cd packages/selfhealing-python

# Enum alias 검사
grep -rn --include="*.py" \
  "PAYMENT\|WEBHOOK\|INVENTORY\|POINT\|ORDER\|SHIPPING" \
  src/selfhealing/ \
  | grep -v adapters \
  | grep -v tests \
  | grep -v "#"

# Property accessor 검사
grep -rn --include="*.py" \
  "def order_id\|def payment_id\|def payment_hours\|def point_hours" \
  src/selfhealing/

# Default vendor 검사
grep -rn --include="*.py" \
  "_default_payment\|toss" \
  src/selfhealing/ \
  | grep -v "#" \
  | grep -v docstring

# 목표 결과: 각 검사 0건
```

---

*문서 생성일: 2025-12-14*
*마지막 수정: 2025-12-14 (2차 전체 감사 결과 추가)*
