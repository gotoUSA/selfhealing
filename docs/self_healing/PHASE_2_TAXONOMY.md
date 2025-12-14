# Phase 2: Core Taxonomy 중립화

> **목표**: 코어 분류 체계(enum, DTO, metrics, config)에서 쇼핑 도메인 용어 제거
>
> **핵심**: 코어는 도메인에 무관하게 동작, 어댑터에서 도메인별 값 주입

---

## 상태

| 항목 | 상태 |
|------|------|
| 전체 진행 | ⬜ 미시작 |
| 예상 소요 | 1시간 |
| 위험도 | 중간 |

---

## 2-1. ViolationType Enum 도메인 특화 상수 제거

### 현재 상태 (security_violation_service.py)

```python
class ViolationType(str, Enum):
    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"  # ❌ 쇼핑 특화
    PAYMENT_AMOUNT_TAMPERED = "payment_amount_tampered"      # ❌ 쇼핑 특화
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
```

### 목표 상태

```python
class ViolationType(str, Enum):
    SIGNATURE_INVALID = "signature_invalid"      # 중립화
    DATA_TAMPERED = "data_tampered"              # 중립화
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
```

### 작업 체크리스트

- [ ] `ViolationType.WEBHOOK_SIGNATURE_INVALID` → `SIGNATURE_INVALID`
- [ ] `ViolationType.PAYMENT_AMOUNT_TAMPERED` → `DATA_TAMPERED`
- [ ] `SEVERITY_BY_VIOLATION_TYPE` 매핑 업데이트
- [ ] `_take_protective_action()` 분기 조건 업데이트
- [ ] 비즈니스 메시지 중립화: `"Payment blocked, order frozen"` → `"Request blocked, entity frozen for investigation"`

---

## 2-2. FailedOperationDomain Enum 고정 목록 제거

### 현재 상태 (repositories.py)

```python
class FailedOperationDomain(str, Enum):
    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    NOTIFICATION = "notification"
    DATA_SYNC = "data_sync"
    CUSTOM = "custom"

    # Legacy aliases (deprecated) ← 제거 대상
    PAYMENT = "external_service"
    POINT = "internal_process"
    INVENTORY = "internal_process"
    WEBHOOK = "external_service"
```

### 목표 상태

```python
class FailedOperationDomain(str, Enum):
    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    NOTIFICATION = "notification"
    DATA_SYNC = "data_sync"
    CUSTOM = "custom"
    # Legacy aliases 완전 제거
```

### 작업 체크리스트

- [ ] `repositories.py`: `PAYMENT`, `POINT`, `INVENTORY`, `WEBHOOK` alias 제거
- [ ] `types.py`: `FailureType.PAYMENT`, `INVENTORY` alias 제거
- [ ] `types.py`: `DomainType.PAYMENT`, `ORDER`, `INVENTORY`, `SHIPPING`, `USER` alias 제거
- [ ] 사용처 검색 및 마이그레이션

---

## 2-3. DTO 필드 `order_id`, `payment_id` 처리

### 현재 상태 (repositories.py)

```python
@dataclass
class FailedOperationData:
    entity_refs: dict[str, int] = field(default_factory=dict)
    
    @property
    def order_id(self) -> Optional[int]:           # ❌ 제거 대상
        """@deprecated: use entity_refs.get('order_id')"""
        return self.entity_refs.get("order_id")

    @property
    def payment_id(self) -> Optional[int]:         # ❌ 제거 대상
        """@deprecated: use entity_refs.get('payment_id')"""
        return self.entity_refs.get("payment_id")
```

### 목표 상태

```python
@dataclass
class FailedOperationData:
    entity_refs: dict[str, int] = field(default_factory=dict)
    # order_id, payment_id property 완전 제거
    # 어댑터에서 필요시 자체 래퍼 정의
```

### 작업 체크리스트

- [ ] `FailedOperationData`: `order_id`, `payment_id` property 제거
- [ ] `SecurityIncidentData`: `order_id`, `payment_id` property 제거
- [ ] `FailedOperationRepository.create()`: `order_id`, `payment_id` 파라미터 → `entity_refs` 변경
- [ ] `SecurityIncidentRepository.create()`: `order_id`, `payment_id` 파라미터 → `entity_refs` 변경
- [ ] `SecurityViolationService.handle_violation()`: `order_id`, `payment_id` 파라미터 → `entity_refs` 변경
- [ ] FastAPI routes 업데이트
- [ ] SQLAlchemy adapters 업데이트

---

## 2-4. Metrics DOMAINS 라벨 고정값 제거

### 현재 상태 (prometheus.py)

```python
DOMAINS: list = [
    "payment",
    "point",
    "inventory",
    "webhook",
    "notification",
]
```

### 목표 상태

```python
# 코어: 고정 목록 없음, 동적 등록
_registered_domains: set[str] = set()

def register_domain(domain: str) -> None:
    """도메인을 메트릭 라벨로 등록"""
    _registered_domains.add(domain)

def get_domains() -> list[str]:
    """등록된 도메인 목록 반환"""
    return sorted(_registered_domains)

# 어댑터 초기화 시
# register_domain("payment")
# register_domain("order")
```

### 작업 체크리스트

- [ ] `DOMAINS` 상수 제거
- [ ] `register_domain()`, `get_domains()` 함수 추가
- [ ] 메트릭 수집 코드에서 동적 도메인 사용

---

## 2-5. SLA Config 필드명 중립화

### 현재 상태 (config.py)

```python
@dataclass
class SLAConfig:
    default_hours: int = 24
    thresholds_by_domain: dict[str, int] = field(default_factory=dict)

    @property
    def payment_hours(self) -> int:      # ❌ 제거 대상
        return self.thresholds_by_domain.get("payment", self.default_hours)

    @property
    def point_hours(self) -> int:        # ❌ 제거 대상
        ...
```

### 목표 상태

```python
@dataclass
class SLAConfig:
    default_hours: int = 24
    thresholds_by_domain: dict[str, int] = field(default_factory=dict)
    
    # 도메인별 property 완전 제거
    # 사용: config.get_threshold("payment") 또는 config.thresholds_by_domain["payment"]
```

### 작업 체크리스트

- [ ] `SLAConfig`: `payment_hours`, `point_hours`, `inventory_hours`, `webhook_hours`, `notification_hours` property 제거
- [ ] `IdempotencyConfig`: `payment_cache_ttl`, `webhook_cache_ttl` → `cache_ttl_by_domain: dict` 변경

---

## 2-6. StateSnapshot/ForensicContext 쇼핑 property 제거

### 현재 상태 (forensic.py)

```python
@dataclass
class StateSnapshot:
    states: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def order_status(self) -> Optional[str]:      # ❌ 제거 대상
        return self.states.get("order_status")
    
    @property
    def payment_status(self) -> Optional[str]:    # ❌ 제거 대상
        return self.states.get("payment_status")
```

### 목표 상태

```python
@dataclass
class StateSnapshot:
    states: Dict[str, Any] = field(default_factory=dict)
    
    # 쇼핑 property 완전 제거
    # 사용: snapshot.get_state("order_status")
```

### 작업 체크리스트

- [ ] `StateSnapshot`: `order_status`, `payment_status`, `user_points`, `product_stock` property/setter 제거
- [ ] `ForensicContext.capture_state_before/after()`: 쇼핑 파라미터 제거, `**extra`만 사용

---

## 2-7. IdempotencyService 도메인 특화 제거

### 현재 상태 (idempotency_service.py)

```python
class IdempotencyDomain(Enum):
    PAYMENT = "payment"       # ❌ 쇼핑 특화
    WEBHOOK = "webhook"       # ❌ 쇼핑 특화
    POINT = "point"           # ❌ 쇼핑 특화
    INVENTORY = "inventory"   # ❌ 쇼핑 특화
    NOTIFICATION = "notification"
```

### 목표 상태

**옵션 A**: Enum 제거, 문자열 기반
```python
# IdempotencyDomain enum 제거
# 사용: IdempotencyKey(domain="payment", key="...")
```

**옵션 B**: 도메인 중립 Enum
```python
class IdempotencyDomain(Enum):
    EXTERNAL_CALL = "external_call"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    CUSTOM = "custom"
```

### 작업 체크리스트

- [ ] `IdempotencyDomain` enum 중립화 또는 제거
- [ ] `IdempotencyKey.for_payment()`, `for_webhook()` 등 → 범용 `IdempotencyKey.create(domain, **components)` 변경
- [ ] `IdempotencyService.check_payment()`, `check_webhook()` 등 → 범용 `check(key, lookup_fn)` 변경
- [ ] shopping import fallback 제거

---

## 마이그레이션 호환성 메모

### Enum 값 매핑

| 기존 값 (쇼핑 특화) | 신규 값 (도메인 중립) | 비고 |
|---------------------|----------------------|------|
| `WEBHOOK_SIGNATURE_INVALID` | `SIGNATURE_INVALID` | 어댑터에서 context 추가 |
| `PAYMENT_AMOUNT_TAMPERED` | `DATA_TAMPERED` | 어댑터에서 context 추가 |
| `PAYMENT` | 어댑터에서 정의 | 코어에서 제거 |
| `POINT` | 어댑터에서 정의 | 코어에서 제거 |
| `INVENTORY` | 어댑터에서 정의 | 코어에서 제거 |
| `WEBHOOK` | 어댑터에서 정의 | 코어에서 제거 |

### DTO 필드 매핑

| 기존 접근 | 신규 접근 |
|----------|-----------|
| `data.order_id` | `data.entity_refs.get("order_id")` |
| `data.payment_id` | `data.entity_refs.get("payment_id")` |

---

## 검증

```bash
cd packages/selfhealing-python

# Enum 쇼핑 값 검사
grep -rn --include="*.py" \
  "PAYMENT\|WEBHOOK\|INVENTORY\|POINT\|ORDER\|SHIPPING" \
  src/selfhealing/ \
  | grep -v adapters \
  | grep -v "#"
# 목표: 0건

# Property 쇼핑 용어 검사
grep -rn --include="*.py" \
  "def order_id\|def payment_id\|def payment_hours\|def point_hours" \
  src/selfhealing/
# 목표: 0건

# DOMAINS 상수 검사
grep -rn --include="*.py" "DOMAINS.*=.*\[" src/selfhealing/
# 목표: 0건 또는 동적 등록 함수만 존재
```

---

## 완료 조건

- [ ] `SecurityIncidentType`/`ViolationType`에 `PAYMENT_*`, `WEBHOOK_*` 없음
- [ ] `FailedOperationDomain`/`DomainType`/`FailureType`에 쇼핑 alias 없음
- [ ] DTO에 `order_id`, `payment_id` property 없음
- [ ] Repository interface에 `order_id`, `payment_id` 파라미터 없음
- [ ] Metrics에 고정 DOMAINS 목록 없음
- [ ] SLA config에 `payment_hours` 등 property 없음
- [ ] StateSnapshot에 쇼핑 property 없음
- [ ] IdempotencyDomain에 쇼핑 값 없음
- [ ] IdempotencyService에 도메인 특화 메서드 없음

---

*문서 생성일: 2025-12-14*
