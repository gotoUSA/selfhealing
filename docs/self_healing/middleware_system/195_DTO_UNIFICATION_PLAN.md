# 195. DTO 중복 정의 통합 계획

> **문서 버전**: 1.1.0
> **최종 수정일**: 2026-02-07
> **상태**: ✅ 구현 완료
> **작성 근거**:
> - `selfhealing/core/types.py` (Line 49-87: FailedOperationData, CircuitBreakerStateData — **삭제됨**)
> - `selfhealing/interfaces/repositories.py` (Line 103-214: FailedOperationData, CircuitBreakerStateData — **정규 소스**)
> - `selfhealing/core/__init__.py` (Line 126-136: re-export — **interfaces로 리다이렉트 완료**)
> - `selfhealing/__init__.py` (Line 11-16: 공개 API — 변경 없음)
> - `tests/conftest.py` (Line 367-393: fixture — **interfaces 버전으로 전환 완료**)
> - `tests/unit/utils/test_types.py` (Line 55-100: 단위 테스트 — **interfaces 버전 기준으로 교체 완료**)
> - `tests/factories/data_factory.py` (Line 18-26: import — **통합 완료**)
> **선행 조건**: 194번 데드코드 제거 완료
> **우선순위**: 🟡 P1

---

## 1. 개요

`FailedOperationData`, `CircuitBreakerStateData` 두 DTO가 **`core/types.py`와 `interfaces/repositories.py` 2곳에 중복 정의**되어 있습니다.
두 버전은 필드 구성, 기본값, 프로퍼티 유무가 모두 다르며, 이로 인해 테스트와 프로덕션 코드가 **서로 다른 DTO를 사용**하는 비일관적 상태입니다.

---

## 2. 현황 분석

### 2.1 `FailedOperationData` 이중 정의

#### `core/types.py` 버전 (Line 66-80)

```python
@dataclass
class FailedOperationData:
    id: int
    domain: str
    failure_type: str
    status: str
    created_at: datetime
    context: dict[str, Any] = field(default_factory=dict)  # ← 고유 필드
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 3          # ← interfaces는 2
    last_retry_at: datetime | None = None
    next_retry_at: datetime | None = None
    resolved_at: datetime | None = None
    updated_at: datetime | None = None
```

**사용처** (총 6곳):
- `tests/conftest.py` Line 367: `sample_failed_operation_data` fixture
- `tests/unit/utils/test_types.py` Line 55, 72: 단위 테스트
- `tests/factories/data_factory.py` Line 18: import (하지만 실제 사용하지 않음)
- `core/__init__.py` Line 133: re-export
- `__init__.py` Line 11: 공개 API 노출 안 함

#### `interfaces/repositories.py` 버전 (Line 100-175)

```python
@dataclass
class FailedOperationData:
    id: int
    domain: str
    failure_type: str
    status: str
    entity_type: str | None = None       # ← 고유
    entity_id: str | None = None         # ← 고유
    entity_refs: dict[str, Any] = field(default_factory=dict)
    user_id: int | None = None           # ← 고유
    snapshot_data: dict[str, Any] = field(default_factory=dict)  # ← 고유
    error_code: str = ""                 # ← 고유
    error_message: str = ""
    retry_count: int = 0
    max_retries: int = 2                 # ← core는 3
    last_retry_at: datetime | None = None
    request_data: dict[str, Any] = field(default_factory=dict)   # ← 고유
    response_data: dict[str, Any] = field(default_factory=dict)  # ← 고유
    metadata: dict[str, Any] = field(default_factory=dict)       # ← 고유
    resolved_at: datetime | None = None
    resolved_by_id: int | None = None    # ← 고유
    resolution_type: str = ""            # ← 고유
    resolution_note: str = ""            # ← 고유
    next_action_hint: str = ""           # ← 고유
    recommended_action: str = ""         # ← 고유
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None   # ← 고유

    @property
    def is_pending(self) -> bool: ...    # ← 프로퍼티
    @property
    def is_resolved(self) -> bool: ...   # ← 프로퍼티
    @property
    def can_retry(self) -> bool: ...     # ← 프로퍼티
```

**사용처**: 프로덕션 코드 전반 (어댑터, 서비스, 리포지토리 구현체). `data_factory.py` Line 26에서도 import.

#### 필드 차이 요약

| 비교 항목 | `core/types.py` | `interfaces/repositories.py` |
|-----------|----------------|------------------------------|
| 총 필드 수 | 14개 | 27개 + 프로퍼티 3개 |
| `max_retries` 기본값 | **3** | **2** |
| `created_at` | 필수 (required) | 선택 (Optional) |
| `context` 필드 | ✅ 있음 | ❌ 없음 |
| `entity_type/entity_id` | ❌ 없음 | ✅ 있음 |
| `snapshot_data` | ❌ 없음 | ✅ 있음 |
| `error_code` | ❌ 없음 | ✅ 있음 |
| `request_data/response_data` | ❌ 없음 | ✅ 있음 |
| `metadata` | ❌ 없음 | ✅ 있음 |
| `resolution_*` 필드들 | ❌ 없음 | ✅ 있음 |
| `expires_at` | ❌ 없음 | ✅ 있음 |
| 프로퍼티 (`is_pending`, `can_retry`) | ❌ 없음 | ✅ 있음 |

---

### 2.2 `CircuitBreakerStateData` 이중 정의

#### `core/types.py` 버전 (Line 83-98)

```python
@dataclass
class CircuitBreakerStateData:
    service_name: str
    state: str
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None      # ← 고유
    opened_at: datetime | None = None
    half_opened_at: datetime | None = None        # ← 고유
    failure_threshold: int = 5                    # ← 고유
    recovery_timeout: int = 60                    # ← 고유
    half_open_max_calls: int = 3                  # ← 고유
    manually_controlled: bool = False
    controlled_by_id: int | None = None
    control_reason: str = ""
    manual_override_expires_at: datetime | None = None
    half_open_request_count: int = 0
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

**사용처** (총 4곳):
- `tests/conftest.py` Line 385: `sample_circuit_breaker_data` fixture
- `tests/unit/utils/test_types.py` Line 96: 단위 테스트
- `tests/factories/data_factory.py` Line 19: import
- `core/__init__.py` Line 134: re-export

#### `interfaces/repositories.py` 버전 (Line 180-220)

```python
@dataclass
class CircuitBreakerStateData:
    service_name: str
    id: int | None = None
    state: str = CircuitBreakerStateEnum.CLOSED.value
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: datetime | None = None
    opened_at: datetime | None = None
    manually_controlled: bool = False
    controlled_by_id: int | None = None
    control_reason: str = ""
    manual_override_expires_at: datetime | None = None
    half_open_request_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_open(self) -> bool: ...       # ← 프로퍼티
    @property
    def is_closed(self) -> bool: ...     # ← 프로퍼티
    @property
    def is_half_open(self) -> bool: ...  # ← 프로퍼티
```

**사용처**: 프로덕션 코드 전반 (어댑터, 리포지토리, CircuitBreaker 서비스).

#### 필드 차이 요약

| 비교 항목 | `core/types.py` | `interfaces/repositories.py` |
|-----------|----------------|------------------------------|
| `state` 기본값 | 없음 (required) | `"closed"` |
| `last_success_at` | ✅ 있음 | ❌ 없음 |
| `half_opened_at` | ✅ 있음 | ❌ 없음 |
| `failure_threshold` | ✅ 있음 (5) | ❌ 없음 (Config 소관) |
| `recovery_timeout` | ✅ 있음 (60) | ❌ 없음 (Config 소관) |
| `half_open_max_calls` | ✅ 있음 (3) | ❌ 없음 (Config 소관) |
| 프로퍼티 | ❌ 없음 | ✅ `is_open`, `is_closed`, `is_half_open` |

---

## 3. 통합 방향

### 3.1 정규 소스(canonical source): `interfaces/repositories.py`

**선택 근거**:
1. 프로덕션 코드의 유일한 소비 대상
2. 모든 어댑터(Redis, Memory, Django)가 이 버전을 사용
3. 리포지토리 ABC와 같은 모듈에 위치하여 계약 일관성 보장
4. 프로퍼티(`is_pending`, `can_retry`, `is_open` 등) 보유

### 3.2 `core/types.py` 고유 필드 처리

| `core/types.py` 고유 필드 | 처리 방침 |
|---------------------------|----------|
| `FailedOperationData.context` | `metadata` 필드로 대체 가능. 테스트에서 `context={"order_id": 123}` → `metadata={"order_id": 123}` |
| `FailedOperationData.next_retry_at` | `interfaces` 버전에 없음. 테스트에서만 사용 → 삭제 |
| `CircuitBreakerStateData.last_success_at` | `interfaces` 버전에 없음. 테스트에서만 사용 → 삭제 |
| `CircuitBreakerStateData.half_opened_at` | `interfaces` 버전에 없음. 테스트에서만 사용 → 삭제 |
| `CircuitBreakerStateData.failure_threshold` | Config 클래스 소관. DTO에서 삭제 |
| `CircuitBreakerStateData.recovery_timeout` | Config 클래스 소관. DTO에서 삭제 |
| `CircuitBreakerStateData.half_open_max_calls` | Config 클래스 소관. DTO에서 삭제 |

---

## 4. 실행 계획

### Phase 1: `core/types.py`에서 중복 DTO 삭제

**삭제 대상** (194번 데드코드 제거 후 남은 것):
- `FailedOperationData` 클래스 (Line 66-80)
- `CircuitBreakerStateData` 클래스 (Line 83-98)

삭제 후 `core/types.py`에 **보존되는 코드**:
- `FailureType(str, Enum)` — 고유 정의, 사용처 다수
- `OperationStatus(str, Enum)` — 11건 사용, 별도 검토 필요 (아래 5절 참조)
- `CircuitState(str, Enum)` — 196번 문서에서 별도 다룸
- `RetryContext(TypedDict)` — 고유 정의
- `MetricsSnapshot` — 고유 정의

### Phase 2: re-export 리다이렉트

`core/__init__.py` (Line 133-140)의 re-export를 `interfaces`로 변경:

```python
# 변경 전 (core/__init__.py)
from selfhealing.core.types import (
    CircuitBreakerStateData,
    FailedOperationData,
    ...
)

# 변경 후
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    FailedOperationData,
)
```

### Phase 3: 테스트 코드 수정

#### 3-1. `tests/conftest.py` (Line 367-393)

```python
# 변경 전
from selfhealing.core.types import FailedOperationData
return FailedOperationData(
    id=1, domain="order", failure_type="network", status="pending",
    created_at=datetime.now(),
    context={"order_id": 123, "amount": 10000},   # ← context 필드
    max_retries=3,                                  # ← 기본값 3
)

# 변경 후
from selfhealing.interfaces.repositories import FailedOperationData
return FailedOperationData(
    id=1, domain="order", failure_type="network", status="pending",
    created_at=datetime.now(),
    metadata={"order_id": 123, "amount": 10000},   # ← metadata로 변경
    max_retries=3,                                  # ← 명시적 3 (기본값 2와 다름)
)
```

#### 3-2. `tests/unit/utils/test_types.py` (Line 55-100)

이 테스트 파일은 **`core/types.py`의 DTO를 직접 테스트**합니다. DTO 삭제 후:

```python
# 삭제: TestFailedOperationData 클래스 (Line 55-90)
#   - test_create_with_required_fields: context 필드, max_retries==3 검증
#   - test_create_with_all_fields: context 필드 사용

# 삭제: TestCircuitBreakerStateData 클래스 (Line 93-106)
#   - test_create_with_defaults: failure_threshold==5, recovery_timeout==60 검증
```

**대안**: `interfaces/repositories.py` DTO에 대한 테스트로 교체하거나, 이미 어댑터 테스트에서 간접 검증되고 있으므로 삭제.

#### 3-3. `tests/factories/data_factory.py` (Line 18-26)

```python
# 변경 전
from selfhealing.core.types import (
    CircuitBreakerStateData,
    FailureType,
    OperationStatus,
    CircuitState,
)
from selfhealing.interfaces import FailedOperationData

# 변경 후
from selfhealing.core.types import (
    FailureType,
    OperationStatus,
    CircuitState,
)
from selfhealing.interfaces.repositories import (
    FailedOperationData,
    CircuitBreakerStateData,
)
```

`MockCircuitBreakerStateData` (Line 33-53)는 `Optional` 타이핑 사용 → 197번 문서에서 처리.

---

## 5. `OperationStatus` 검토

`core/types.py`의 `OperationStatus(str, Enum)` (사용처 11건):

```python
class OperationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    MANUAL_REVIEW = "manual_review"
```

**vs** `interfaces/repositories.py`의 `FailedOperationStatus(str, Enum)` (사용처 106건):

```python
class FailedOperationStatus(str, Enum):
    PENDING = "pending"
    REVIEWING = "reviewing"
    REPLAYED = "replayed"
    REQUIRES_REVIEW = "requires_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    EXPIRED = "expired"
```

**이름도 상태값도 완전히 다릅니다.** 두 Enum은 같은 것을 다르게 정의한 것이 아니라, 설계 의도 자체가 다를 수 있습니다.

| 비교 | `OperationStatus` | `FailedOperationStatus` |
|------|-------------------|------------------------|
| `PROCESSING` | ✅ | ❌ |
| `COMPLETED` | ✅ | ❌ |
| `FAILED` | ✅ | ❌ |
| `MANUAL_REVIEW` | ✅ | ❌ |
| `REVIEWING` | ❌ | ✅ |
| `REPLAYED` | ❌ | ✅ |
| `REQUIRES_REVIEW` | ❌ | ✅ |
| `RESOLVED` | ❌ | ✅ |
| `REJECTED` | ❌ | ✅ |
| `ARCHIVED` | ❌ | ✅ |

**판정**: `OperationStatus`는 11건 중 대부분이 re-export와 테스트. 프로덕션 코드는 `FailedOperationStatus`를 사용. `OperationStatus`는 데드코드에 준하는 상태이나, 통합 시 별도 마이그레이션 검토 필요.

---

## 6. 영향 범위

| 카테고리 | 수정 파일 수 | 상세 |
|---------|------------|------|
| 소스 코드 | 2개 | `core/types.py` (DTO 삭제), `core/__init__.py` (re-export 변경) |
| 테스트 코드 | 3개 | `conftest.py`, `test_types.py`, `data_factory.py` |
| 프로덕션 코드 | 0개 | `interfaces` 버전을 이미 사용 중 |

---

## 7. 리스크 평가

| 리스크 | 수준 | 대응 |
|--------|------|------|
| `core.types.FailedOperationData` 직접 import 깨짐 | 🟡 중간 | `core/__init__.py` re-export로 하위호환 유지 |
| `context` → `metadata` 필드명 변경 | 🟡 중간 | 테스트 코드만 해당, 3곳 수정 |
| `max_retries` 기본값 차이 (3→2) | 🟡 중간 | 기존 테스트에서 명시적으로 `max_retries=3` 지정 |
| `failure_threshold` 등 Config 필드 누락 | 🟢 낮음 | Config 클래스에서 관리, DTO 소관 아님 |
| `is_open` 등 프로퍼티 부재하던 코드 | 🟢 긍정적 | 통합 후 프로퍼티 사용 가능해짐 |

---

## 8. 검증 절차

```bash
# 1. core/types.py에서 DTO가 삭제되었는지 확인
grep -n "class FailedOperationData" packages/selfhealing-python/src/selfhealing/core/types.py
grep -n "class CircuitBreakerStateData" packages/selfhealing-python/src/selfhealing/core/types.py

# 2. re-export가 interfaces를 가리키는지 확인
grep -n "FailedOperationData\|CircuitBreakerStateData" packages/selfhealing-python/src/selfhealing/core/__init__.py

# 3. 테스트 실행
cd packages/selfhealing-python && python -m pytest tests/unit/utils/test_types.py -v
cd packages/selfhealing-python && python -m pytest tests/ -x --tb=short

# 4. import 정합성 확인
python -c "from selfhealing.core import FailedOperationData; print(FailedOperationData.__module__)"
# 예상 출력: selfhealing.interfaces.repositories
```

---

## 9. 구현 결과 (2026-02-07)

### 9.1 실행 요약

| Phase | 작업 내용 | 상태 |
|-------|----------|------|
| Phase 1 | `core/types.py`에서 `FailedOperationData`(14필드), `CircuitBreakerStateData`(20필드) 삭제 | ✅ 완료 |
| Phase 2 | `core/__init__.py` re-export를 `interfaces.repositories`로 리다이렉트 | ✅ 완료 |
| Phase 3-1 | `tests/conftest.py` fixture: `context` → `metadata`, import 경로 전환 | ✅ 완료 |
| Phase 3-2 | `tests/unit/utils/test_types.py`: interfaces 버전 기준 테스트로 교체, 프로퍼티 테스트 추가 | ✅ 완료 |
| Phase 3-3 | `tests/factories/data_factory.py`: import 통합 (`CircuitBreakerStateData`도 interfaces에서 import) | ✅ 완료 |

### 9.2 검증 결과

```
# 1. core/types.py에서 DTO 검색 → 결과 없음 (삭제 확인)
$ grep -n "class FailedOperationData\|class CircuitBreakerStateData" src/selfhealing/core/types.py
(exit code 1 - no matches)

# 2. re-export 경로 확인
$ from selfhealing.core import FailedOperationData → __module__ = "selfhealing.interfaces.repositories"
$ from selfhealing.core import CircuitBreakerStateData → __module__ = "selfhealing.interfaces.repositories"

# 3. 테스트 결과
$ python -m pytest tests/unit/utils/test_types.py -v
10 passed in 0.54s
```

### 9.3 수정 파일 목록

| 파일 | 변경 유형 |
|------|----------|
| `packages/selfhealing-python/src/selfhealing/core/types.py` | DTO 2개 클래스 삭제 (45줄 제거) |
| `packages/selfhealing-python/src/selfhealing/core/__init__.py` | re-export 경로 변경 (`core.types` → `interfaces.repositories`) |
| `packages/selfhealing-python/tests/conftest.py` | import 경로 + `context` → `metadata` 필드명 변경 |
| `packages/selfhealing-python/tests/unit/utils/test_types.py` | interfaces 버전 기준 테스트로 전면 교체 + 프로퍼티 테스트 추가 |
| `packages/selfhealing-python/tests/factories/data_factory.py` | 하이브리드 import 제거, interfaces.repositories로 통합 |
