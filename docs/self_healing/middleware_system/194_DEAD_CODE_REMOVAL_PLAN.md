# 194. 데드코드 제거 계획

> **문서 버전**: 1.2.0
> **최종 수정일**: 2026-02-10
> **구현 완료일**: 2026-02-07 (Phase 1), 2026-02-10 (Phase 2)
> **상태**: ✅ 완료
> **작성 근거**:
> - `selfhealing/core/constants.py` (Line 67-100)
> - `selfhealing/core/types.py` (Line 100-120: SecurityIncidentData)
> - `selfhealing/core/types.py` (Line 54-63: DomainType)
> - `selfhealing/core/types.py` (Line 12-27: FailureType — 프로덕션 0건)
> - `selfhealing/core/types.py` (Line 28-36: OperationStatus — 프로덕션 0건)
> - `selfhealing/core/types.py` (Line 39-49: RetryContext — 프로덕션 0건, 테스트 0건)
> - `selfhealing/core/types.py` (Line 51-62: MetricsSnapshot — 프로덕션 0건, 테스트 0건)
> - `selfhealing/interfaces/repositories.py` (Line 49-57: FailedOperationStatus 106 usages)
> - `selfhealing/interfaces/repositories.py` (Line 60-63: CircuitBreakerStateEnum 74 usages)
> **우선순위**: 🔴 P0 (제로 리스크, 즉시 실행 가능)

---

## 1. 개요

코드 일관성 조사 결과, 어디서도 import 되지 않는 데드코드(dead code)가 확인되었습니다.
이 문서는 **사용처가 0인 코드**만을 대상으로 하며, **`list_code_usages` 결과를 근거로** 작성되었습니다.

---

## 2. 제거 대상 목록

### 2.1 `core/constants.py` — `FailedOperationStatus` (사용처: 0)

**파일 위치**: `selfhealing/core/constants.py` Line 67-83

```python
class FailedOperationStatus:
    """Failed operation status constants."""

    PENDING = "pending"
    RETRYING = "retrying"           # ← interfaces 버전에는 없는 값
    RESOLVED = "resolved"
    PERMANENTLY_FAILED = "permanently_failed"  # ← interfaces 버전에는 없는 값
    EXPIRED = "expired"

    CHOICES = [
        (PENDING, "Pending - Awaiting retry"),
        (RETRYING, "Retrying - In progress"),
        (RESOLVED, "Resolved - Successfully completed"),
        (PERMANENTLY_FAILED, "Permanently Failed - Max retries exceeded"),
        (EXPIRED, "Expired - TTL exceeded"),
    ]
```

**근거**: `list_code_usages("FailedOperationStatus", core/constants.py)` → **0건**

**대조**: `interfaces/repositories.py`의 `FailedOperationStatus(str, Enum)` → **106건** 사용 중

| 비교 항목 | `core/constants.py` (데드) | `interfaces/repositories.py` (활성) |
|-----------|---------------------------|--------------------------------------|
| 구현 방식 | 평문 클래스 | `str, Enum` |
| 상태값 | `PENDING, RETRYING, RESOLVED, PERMANENTLY_FAILED, EXPIRED` | `PENDING, REVIEWING, REPLAYED, REQUIRES_REVIEW, RESOLVED, REJECTED, ARCHIVED, EXPIRED` |
| 사용처 | 0건 | 106건 |

---

### 2.2 `core/constants.py` — `CircuitBreakerState` (사용처: 0)

**파일 위치**: `selfhealing/core/constants.py` Line 86-98

```python
class CircuitBreakerState:
    """Circuit breaker state constants."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    CHOICES = [
        (CLOSED, "Closed - Normal operation"),
        (OPEN, "Open - Blocking requests"),
        (HALF_OPEN, "Half Open - Testing recovery"),
    ]
```

**근거**: `list_code_usages("CircuitBreakerState", core/constants.py)` → **0건**

**대조**: 동일한 값을 가진 활성 정의들:

| 위치 | 사용처 수 |
|------|----------|
| `services/circuit_breaker/config.py` `CircuitState` (plain class) | 117건 |
| `interfaces/repositories.py` `CircuitBreakerStateEnum(str, Enum)` | 74건 |
| `audit/graceful_degradation/enums.py` `CircuitState(str, Enum)` | 48건 |
| `audit/resilience/circuit_breaker.py` `CircuitState(Enum)` | 64건 |

---

### 2.3 `core/types.py` — `SecurityIncidentData` (프로덕션 사용처: 0)

**파일 위치**: `selfhealing/core/types.py` Line 100-113

```python
@dataclass
class SecurityIncidentData:
    """Data transfer object for security incidents (domain-neutral)."""

    id: int
    incident_type: str
    severity: str
    source_ip: str | None = None
    user_id: int | None = None
    entity_refs: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    is_resolved: bool = False
```

**근거**: `list_code_usages("SecurityIncidentData")` 결과, `core/types.py` 버전은 프로덕션 코드에서 0건 import. `core/__init__.py`에서 re-export만 존재.

**대조**: `interfaces/repositories.py`의 `SecurityIncidentData` (Line 237-280)에는 `status`, `user_agent`, `investigation_notes`, `investigated_at`, `detected_by` 등 추가 필드 보유 — 실제 어댑터 코드에서 사용.

| 필드 비교 | `core/types.py` (데드) | `interfaces/repositories.py` (활성) |
|-----------|----------------------|--------------------------------------|
| `status` | ❌ 없음 | ✅ 있음 |
| `user_agent` | ❌ 없음 | ✅ 있음 |
| `investigation_notes` | ❌ 없음 | ✅ 있음 |
| `investigated_at` | ❌ 없음 | ✅ 있음 |
| `detected_by` | ❌ 없음 | ✅ 있음 |
| `context` | ✅ 있음 | ❌ 없음 |
| `is_resolved` | ✅ 있음 (bool) | ❌ 없음 (status로 대체) |

---

### 2.4 `core/types.py` — `DomainType` (사용처: 0)

**파일 위치**: `selfhealing/core/types.py` Line 54-63

```python
class DomainType(str, Enum):
    """Business domains that can be protected by self-healing (domain-neutral)."""

    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    NOTIFICATION = "notification"
    DATA_SYNC = "data_sync"
    GENERAL = "general"
```

**근거**: `list_code_usages("DomainType")` 결과, `core/__init__.py` re-export 외 실제 사용 0건.

**대조**: `interfaces/repositories.py`의 `FailedOperationDomain(str, Enum)` (Line 29-44)이 동일 역할이며 `CUSTOM` 확장 포인트 포함.

---

## 3. 실행 계획

### Phase 1: 데드코드 삭제

| 순서 | 대상 | 파일 | 행동 |
|------|------|------|------|
| 1 | `FailedOperationStatus` | `core/constants.py` | 클래스 전체 삭제 |
| 2 | `CircuitBreakerState` | `core/constants.py` | 클래스 전체 삭제 |
| 3 | `SecurityIncidentData` | `core/types.py` | 클래스 전체 삭제 |
| 4 | `DomainType` | `core/types.py` | 클래스 전체 삭제 |

### Phase 2: re-export 정리

삭제된 심볼의 re-export를 제거합니다.

| 파일 | 삭제할 re-export |
|------|-----------------|
| `core/__init__.py` (Line 133-140) | `DomainType`, `SecurityIncidentData` |
| `__init__.py` (공개 API) | 현재 `SecurityIncidentData`, `DomainType` export 없음 → 변경 불필요 |

### Phase 3: 검증

```bash
# 1. grep으로 잔여 참조 확인
grep -rn "from selfhealing.core.constants import.*FailedOperationStatus" packages/selfhealing-python/
grep -rn "from selfhealing.core.constants import.*CircuitBreakerState" packages/selfhealing-python/
grep -rn "DomainType" packages/selfhealing-python/src/
grep -rn "core.types.*SecurityIncidentData" packages/selfhealing-python/

# 2. 테스트 실행
cd packages/selfhealing-python && python -m pytest tests/ -x --tb=short
```

---

## 4. 영향 범위

| 항목 | 영향도 |
|------|--------|
| 프로덕션 코드 | ⚪ 없음 (사용처 0건) |
| 테스트 코드 | ⚪ 없음 (직접 import 없음) |
| 공개 API (`__init__.py`) | ⚪ 없음 (해당 심볼 미노출) |
| 하위 호환성 | 🟡 `core/__init__.py` re-export 제거 필요 (`DomainType`, `SecurityIncidentData`) |

---

## 5. `core/constants.py` 잔존 코드 (활성)

삭제 **대상 아님** — 사용 중인 코드:

| 클래스 | 사용처 수 | 주요 소비자 |
|--------|----------|------------|
| `ControlAPIActions` | 33건 | `api/django/views/circuit_breaker.py`, `services/control_api_service.py` |
| `ControlAPIEnvironments` | 활성 | `services/control_api_service.py` |
| `RiskLevels` | 활성 | `services/control_api_service.py` |

삭제 후 `core/constants.py`에는 `ControlAPIActions`, `ControlAPIEnvironments`, `RiskLevels`만 남습니다.

---

## 6. 리스크 평가

| 리스크 | 수준 | 대응 |
|--------|------|------|
| 런타임 에러 | ⚪ 제로 | 사용처 0건이므로 import 실패 불가 |
| 테스트 실패 | ⚪ 제로 | 직접 참조하는 테스트 없음 |
| re-export 깨짐 | 🟡 낮음 | `core/__init__.py`에서 `DomainType`, `SecurityIncidentData` re-export 제거 필요 |

**결론**: 제로 리스크 작업으로, 다른 리팩토링의 선행 작업으로 즉시 실행 가능합니다.

---

## 7. 구현 결과

> **구현일**: 2026-02-07

### 수행된 변경

| 순서 | 대상 | 파일 | 결과 |
|------|------|------|------|
| 1 | `FailedOperationStatus` | `core/constants.py` | ✅ 클래스 전체 삭제 |
| 2 | `CircuitBreakerState` | `core/constants.py` | ✅ 클래스 전체 삭제 |
| 3 | `SecurityIncidentData` | `core/types.py` | ✅ 클래스 전체 삭제 |
| 4 | `DomainType` | `core/types.py` | ✅ 클래스 전체 삭제 |
| 5 | re-export 정리 | `core/__init__.py` | ✅ `DomainType`, `SecurityIncidentData` import 및 `__all__` 제거 |

### 검증 결과

| 검증 항목 | 결과 |
|-----------|------|
| `list_code_usages` 사전 확인 | ✅ 4개 심볼 모두 프로덕션 사용처 0건 확인 |
| grep 잔여 참조 확인 | ✅ src 내 잔여 import 0건 |
| core 단위 테스트 (257건) | ✅ 전체 통과 |
| 전체 테스트 (4,009건) | ✅ 4,008 passed, 1 skipped (1 failed는 기존 실패, 변경과 무관) |

---

## 8. Phase 2: `core/types.py` 잔존 데드코드 제거

> **발견일**: 2026-02-10
> **구현일**: 2026-02-10

Phase 1에서 `DomainType`, `SecurityIncidentData`를 제거했으나, 같은 파일의 나머지 4개 심볼도 동일한 패턴(프로덕션 사용처 0건, re-export만 존재)임이 확인되었습니다.

### 8.1 `core/types.py` — `FailureType` (프로덕션 사용처: 0)

**파일 위치**: `selfhealing/core/types.py` Line 12-27

```python
class FailureType(str, Enum):
    NETWORK = "network"
    DATABASE = "database"
    TIMEOUT = "timeout"
    VALIDATION = "validation"
    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    DATA_INTEGRITY = "data_integrity"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"
```

**근거**: `list_code_usages("FailureType")` → 프로덕션 src/ 내 실제 사용 **0건** (re-export만)

**연결 불가 사유**: 프로덕션 코드에서 `failure_type: str`로 자유형 값 사용 (`"PG_TIMEOUT"`, `"throttle_rejected"`, `"AMOUNT_MISMATCH"` 등). Enum 값과 불일치하며, `interfaces/repositories.py`에도 대응 Enum 없음 — 의도적으로 `str`로 설계됨.

### 8.2 `core/types.py` — `OperationStatus` (프로덕션 사용처: 0)

**파일 위치**: `selfhealing/core/types.py` Line 28-36

```python
class OperationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    MANUAL_REVIEW = "manual_review"
```

**근거**: `list_code_usages("OperationStatus")` → 프로덕션 src/ 내 실제 사용 **0건** (re-export만)

**대조**: `interfaces/repositories.py`의 `FailedOperationStatus(str, Enum)` → **106건** 사용 중

| 비교 항목 | `core/types.py` (데드) | `interfaces/repositories.py` (활성) |
|-----------|----------------------|--------------------------------------|
| 상태값 | `PENDING, PROCESSING, COMPLETED, FAILED, EXPIRED, MANUAL_REVIEW` | `PENDING, REVIEWING, REPLAYED, REQUIRES_REVIEW, RESOLVED, REJECTED, ARCHIVED, EXPIRED` |
| 프로덕션 사용처 | **0건** | **106건** |

**연결 불가 사유**: 상태값이 완전히 다른 상태 모델. `FailedOperationStatus`로 대체 완료.

### 8.3 `core/types.py` — `RetryContext` (프로덕션 사용처: 0, 테스트 0건)

**파일 위치**: `selfhealing/core/types.py` Line 39-49

```python
class RetryContext(TypedDict, total=False):
    attempt: int
    max_attempts: int
    delay: float
    last_error: str
    operation_id: str
    domain: str
```

**근거**: `list_code_usages("RetryContext")` → re-export 2건 + 정의 1건 = **프로덕션 사용 0건, 테스트 0건**

**연결 불가 사유**: `services/retry_handler/models.py`의 `RetryConfig` + `RetryResult`가 동일 역할을 이미 수행. `handler.py`의 `context: dict | None`은 자유형 dict이며 TypedDict 미사용.

### 8.4 `core/types.py` — `MetricsSnapshot` (프로덕션 사용처: 0, 테스트 0건)

**파일 위치**: `selfhealing/core/types.py` Line 51-62

```python
@dataclass
class MetricsSnapshot:
    timestamp: datetime
    circuit_breakers_open: int = 0
    circuit_breakers_half_open: int = 0
    dlq_pending_count: int = 0
    dlq_processing_count: int = 0
    dlq_failed_count: int = 0
    replay_success_rate: float = 0.0
    total_retries: int = 0
    successful_retries: int = 0
```

**근거**: `list_code_usages("MetricsSnapshot")` → re-export 2건 + 정의 1건 = **프로덕션 사용 0건, 테스트 0건**

**연결 불가 사유**: 실제 메트릭 시스템은 Prometheus label 기반으로 **도메인별 분리** 수집 (`dlq_pending_gauge.labels(domain=domain).set(count)`). `MetricsSnapshot`은 도메인 구분 없는 전역 카운트 설계로 아키텍처 불일치. 활성 대체: `interfaces/statistics.py`의 `StatusCounts`, `CircuitBreakerSummary`, `DashboardSummary`.

### 8.5 수행된 변경

| 순서 | 대상 | 파일 | 결과 |
|------|------|------|------|
| 1 | `FailureType` | `core/types.py` | ✅ 클래스 삭제 |
| 2 | `OperationStatus` | `core/types.py` | ✅ 클래스 삭제 |
| 3 | `RetryContext` | `core/types.py` | ✅ 클래스 삭제 |
| 4 | `MetricsSnapshot` | `core/types.py` | ✅ 클래스 삭제 |
| 5 | re-export 정리 | `core/__init__.py` | ✅ 4개 심볼 import 및 `__all__` 제거 |
| 6 | re-export 정리 | `selfhealing/__init__.py` | ✅ `FailureType`, `OperationStatus` import 및 `__all__` 제거 |
| 7 | 미사용 import | `tests/factories/data_factory.py` | ✅ `FailureType`, `OperationStatus` import 삭제 |
| 8 | 데드 테스트 | `tests/unit/utils/test_types.py` | ✅ `TestFailureType`, `TestOperationStatus` 클래스 삭제 |

### 8.6 검증 결과

| 검증 항목 | 결과 |
|-----------|------|
| `list_code_usages` 사전 확인 | ✅ 4개 심볼 모두 프로덕션 사용처 0건 |
| grep 잔여 참조 확인 | ✅ src 내 잔여 import 0건 |
| import 검증 (활성 심볼) | ✅ `CircuitBreakerStateEnum`, `FailedOperationStatus`, `FailedOperationData` 정상 |
| import 검증 (`core/__init__`) | ✅ `CircuitState`, `FailedOperationData`, `CircuitBreakerStateData` 정상 |
| import 검증 (`selfhealing/__init__`) | ✅ `CircuitState` 정상 |
| import 검증 (`data_factory`) | ✅ `TestDataFactory` 정상 |
| test_types.py (7건) | ✅ 전체 통과 |
