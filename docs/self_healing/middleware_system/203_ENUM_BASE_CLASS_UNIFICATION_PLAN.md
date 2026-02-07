# 203. Enum 베이스 클래스 통일 계획

> **상태**: 📋 계획
> **목적**: Enum 정의 시 `str, Enum` vs plain `Enum` vs `IntEnum` 사용 기준을 수립하고 불일치를 수정한다.

---

## 1. 현황

### 1-1. 세 가지 Enum 패턴 공존

코드베이스 내 60+ Enum 정의가 세 가지 패턴으로 혼재:

#### A. `class X(str, Enum)` — 다수 (주류 패턴)

```python
# core/types.py L13
class FailureType(str, Enum):
    CIRCUIT_BREAKER = "circuit_breaker"
    ...

# core/types.py L29
class OperationStatus(str, Enum):
    PENDING = "pending"
    ...

# services/throttle/registry.py L35
class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    ...

# services/coordination/enums.py L15
class EmergencyScope(str, Enum):
    REGIONAL = "regional"
    ...
```

#### B. `class X(Enum)` — 일부

```python
# services/event_bus.py L57
class EventType(Enum):
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
    ...

# utils/async_logger.py L54
class EventSeverity(Enum):
    CRITICAL = "critical"
    ...

# utils/async_logger.py L90
class WALPolicy(Enum):
    SYNC = "sync"
    ...

# utils/async_logger.py L98
class QueueOverflowPolicy(Enum):
    DROP_OLDEST = "drop_oldest"
    ...

# services/governance.py L56
class OperationMode(Enum):
    STRICT = "strict"
    ...

# services/rollback/models.py L11
class RollbackStrategy(Enum):
    ...

# services/rollback/models.py L21
class RollbackState(Enum):
    ...

# services/learning/models.py L11
class PatternType(Enum):
    ...

# services/learning/models.py L42
class SuggestionPriority(Enum):
    ...

# services/pending_config.py L34
class PendingStatus(Enum):
    ...

# services/event_bus_redis.py L41
class EventChannel(Enum):
    ...

# services/finops/models.py L11
class CostTier(Enum):
    ...

# services/emergency_mode/enums.py L12
class EmergencyLevel(Enum):
    ...

# services/idempotency_service.py L41
class IdempotencyDomain(Enum):
    ...

# services/retry_handler.py L49
class RetryAction(Enum):
    ...

# services/error_budget/exception_weights.py L49
class WeightCombinePolicy(Enum):
    ...
```

#### C. `class X(IntEnum)` — 3건

```python
# services/coordination/redis_key_guard.py L44
class RedisKeyPriority(IntEnum):
    ...

# services/coordination/enums.py L59
class CommandPrecedence(IntEnum):
    ...

# services/coordination/critical_worker.py L41
class CriticalTaskPriority(IntEnum):
    ...
```

---

## 2. 문제점

### 2-1. JSON 직렬화 동작 차이

```python
import json

class A(str, Enum):
    X = "value"

class B(Enum):
    X = "value"

json.dumps(A.X)   # → '"value"'  ✅ 자동 직렬화
json.dumps(B.X)   # → TypeError  ❌ .value 필요
json.dumps(B.X.value)  # → '"value"'
```

`str, Enum`은 JSON 직렬화가 자동이지만, plain `Enum`은 `.value` 접근 필요. 같은 프로젝트 내에서 혼재하면 직렬화 코드 일관성이 깨짐.

### 2-2. 문자열 비교 동작 차이

```python
A.X == "value"  # True  (str, Enum)
B.X == "value"  # False (Enum)
```

`str, Enum`은 문자열 비교가 자연스럽지만, plain `Enum`은 `B.X.value == "value"` 필수.

### 2-3. 동일 레이어의 불일치 예시

| 모듈 | Enum | 베이스 |
|------|------|--------|
| `services/coordination/enums.py` | `EmergencyScope` | `str, Enum` |
| `services/emergency_mode/enums.py` | `EmergencyLevel` | `Enum` |
| `services/governance.py` | `OperationMode` | `Enum` |
| `services/governance_checks.py` | `BlockReason` | `str, Enum` |

같은 `services/` 레이어에서 `str, Enum`과 `Enum`이 무작위로 혼재.

---

## 3. 수정 계획

### 3-1. 확정 규칙

| 조건 | 사용할 베이스 | 사유 |
|------|-------------|------|
| 값이 **문자열**이고 **직렬화/API** 필요 | `str, Enum` | JSON 자동 직렬화, 문자열 비교 |
| 값이 **정수**이고 **순서 비교** 필요 | `IntEnum` | `<`, `>` 비교 지원 |
| 내부 전용, 직렬화 불필요 | `str, Enum` (통일) | 일관성 우선 |

**결론**: 문자열 값을 가진 모든 Enum은 `str, Enum`으로 통일.

### 3-2. 수정 대상 (plain `Enum` → `str, Enum`)

| 파일 | 클래스 | 줄번호 |
|------|--------|--------|
| `services/event_bus.py` | `EventType` | L57 |
| `services/event_bus.py` | `EventPriority` | L143 |
| `utils/async_logger.py` | `EventSeverity` | L54 |
| `utils/async_logger.py` | `WALPolicy` | L90 |
| `utils/async_logger.py` | `QueueOverflowPolicy` | L98 |
| `adapters/resilient/backend.py` | `StorageMode` | L27 |
| `services/governance.py` | `OperationMode` | L56 |
| `services/rollback/models.py` | `RollbackStrategy` | L11 |
| `services/rollback/models.py` | `RollbackState` | L21 |
| `services/learning/models.py` | `PatternType` | L11 |
| `services/learning/models.py` | `SuggestionPriority` | L42 |
| `services/pending_config.py` | `PendingStatus` | L34 |
| `services/event_bus_redis.py` | `EventChannel` | L41 |
| `services/finops/models.py` | `CostTier` | L11 |
| `services/emergency_mode/enums.py` | `EmergencyLevel` | L12 |
| `services/idempotency_service.py` | `IdempotencyDomain` | L41 |
| `services/retry_handler.py` | `RetryAction` | L49 |
| `services/error_budget/exception_weights.py` | `WeightCombinePolicy` | L49 |

### 3-3. IntEnum — 유지

`IntEnum` 3건은 정수 값 + 순서 비교가 필요한 우선순위 체계이므로 현재 상태 유지:
- `RedisKeyPriority(IntEnum)` — 키 우선순위 숫자 비교
- `CommandPrecedence(IntEnum)` — 명령 우선순위 숫자 비교
- `CriticalTaskPriority(IntEnum)` — 태스크 우선순위 숫자 비교

### 3-4. 검증 항목

- [ ] 기존 `.value` 접근 패턴이 `str, Enum` 전환 후에도 호환되는지 확인
- [ ] `json.dumps` 호출부에서 커스텀 인코더 불필요 확인
- [ ] `==` 비교에서 문자열 직접 비교로 전환 가능 여부 확인
