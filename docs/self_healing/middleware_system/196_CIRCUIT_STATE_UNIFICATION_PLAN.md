# 196. CircuitState 5중 정의 통합 계획

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-07
> **작성 근거**:
> - `selfhealing/core/types.py` (Line 40-47: CircuitState(str, Enum))
> - `selfhealing/services/circuit_breaker/config.py` (Line 20-25: CircuitState plain class)
> - `selfhealing/audit/resilience/circuit_breaker.py` (Line 19-24: CircuitState(Enum))
> - `selfhealing/audit/graceful_degradation/enums.py` (Line 39-44: CircuitState(str, Enum))
> - `tests/factories/constants.py` (Line 61-65: CircuitState plain class)
> - `selfhealing/interfaces/repositories.py` (Line 60-63: CircuitBreakerStateEnum(str, Enum))
> **선행 조건**: 194번 데드코드 제거 완료
> **우선순위**: 🟠 P1

---

## 1. 개요

`CircuitState`가 **5곳에서 독립적으로 정의**되어 있으며, 여기에 `CircuitBreakerStateEnum`까지 합하면 **6개의 회로 차단기 상태 정의**가 존재합니다. 구현 방식(plain class / Enum / str+Enum)도 제각각이어서, 타입 호환성과 비교 연산 동작이 정의마다 다릅니다.

---

## 2. 현황: 6개 정의 상세 비교

### 2.1 정의별 구현 방식과 import 현황

| # | 위치 | 구현 방식 | 외부 import 수 | 내부 사용 | 비고 |
|---|------|----------|:-------------:|---------|------|
| 1 | `core/types.py` L40 | `class CircuitState(str, Enum)` | **6건** | — | 공개 API re-export |
| 2 | `services/circuit_breaker/config.py` L20 | `class CircuitState:` (plain class) | **4건** | 서비스 모듈 내부 | 가장 많은 프로덕션 코드 |
| 3 | `audit/resilience/circuit_breaker.py` L19 | `class CircuitState(Enum)` | **0건** | 20+곳 (같은 파일) | str 미상속 |
| 4 | `audit/graceful_degradation/enums.py` L39 | `class CircuitState(str, Enum)` | **0건** (직접) | 20+곳 (같은 패키지) | 패키지 내부 사용 |
| 5 | `tests/factories/constants.py` L61 | `class CircuitState:` (plain class) | **1건** (re-export) | — | 테스트 전용 |
| 6 | `interfaces/repositories.py` L60 | `class CircuitBreakerStateEnum(str, Enum)` | **74건** | DTO 기본값 | **이름이 다름** |

### 2.2 구현 방식 차이로 인한 행동 차이

```python
# (1) str, Enum — 문자열 비교 가능
from selfhealing.core.types import CircuitState
CircuitState.CLOSED == "closed"  # True ✅

# (2) plain class — 문자열 비교 가능 (값이 문자열)
from selfhealing.services.circuit_breaker.config import CircuitState
CircuitState.CLOSED == "closed"  # True ✅

# (3) Enum (str 미상속) — 문자열 비교 불가!
from selfhealing.audit.resilience.circuit_breaker import CircuitState
CircuitState.CLOSED == "closed"  # False ❌
CircuitState.CLOSED.value == "closed"  # True (value 접근 필요)
```

| 비교 동작 | `str, Enum` | `plain class` | `Enum` (no str) |
|-----------|:-----------:|:------------:|:---------------:|
| `== "closed"` | ✅ True | ✅ True | ❌ False |
| `isinstance(x, str)` | ✅ True | — (비교 불가) | ❌ False |
| JSON 직렬화 | 자동 | 자동 (문자열 값) | `.value` 필요 |
| `match/case` | 문자열 매칭 | 문자열 매칭 | Enum 매칭 |

---

## 3. 정의별 소비자 상세

### 3.1 `core/types.py` → `CircuitState(str, Enum)` — 6건

| 소비 파일 | 라인 | 용도 |
|-----------|-----|------|
| `selfhealing/__init__.py` | L11 | 공개 API re-export |
| `selfhealing/core/__init__.py` | L126 | core 모듈 re-export |
| `adapters/celery/tasks/circuit_breaker.py` | L151 | CB 상태 전환 비교: `state.state != CircuitState.OPEN` |
| `adapters/celery/tasks/circuit_breaker.py` | L388 | 수동 override 만료 비교: `state.state == CircuitState.OPEN` |
| `tests/unit/utils/test_types.py` | L44 | Enum 값 테스트 |
| `tests/factories/data_factory.py` | L18 | 팩토리에서 import (간접 사용) |

**핵심 문제**: Celery task (L151, L388)에서 `CircuitState.OPEN` 값을 리포지토리의 `CircuitBreakerStateData.state` 문자열과 비교. 리포지토리 DTO의 `state` 기본값은 `CircuitBreakerStateEnum.CLOSED.value`로 설정됨 → **다른 Enum에서 생성된 값과 비교**하는 구조.

### 3.2 `services/circuit_breaker/config.py` → `CircuitState` (plain class) — 4건

| 소비 파일 | 라인 | 용도 |
|-----------|-----|------|
| `services/circuit_breaker/__init__.py` | L52 | 서비스 패키지 공개 API |
| `services/circuit_breaker/manual_control.py` | L16 | 수동 제어 시 상태 전이 |
| `services/circuit_breaker_service.py` | L32 | 호환 래퍼 import |
| `services/circuit_breaker/stale_cache_integration.py` | L25 | 캐시 통합에서 CB 상태 확인 |

**이 버전이 핵심 서비스 로직**에서 사용됩니다.

### 3.3 `audit/resilience/circuit_breaker.py` → `CircuitState(Enum)` — 파일 내부 전용

같은 파일 내 20+곳에서 사용. `CircuitBreaker` 클래스가 audit 백엔드(CloudWatch, Elasticsearch 등)에 대한 회로 차단기를 구현. **외부 모듈에서 import하는 곳 0건.**

```python
# circuit_breaker.py 내부 사용 패턴
if self._state.state == CircuitState.CLOSED:  # Enum 비교
    ...
self._transition_to(CircuitState.OPEN)        # Enum 값 전달
```

### 3.4 `audit/graceful_degradation/enums.py` → `CircuitState(str, Enum)` — 패키지 내부 전용

같은 패키지의 `circuit_breaker.py`에서 `from .enums import CircuitState`로 import. 패키지 `__init__.py`에서 re-export는 하지만, **패키지 외부에서 import하는 곳 0건.**

```python
# graceful_degradation/circuit_breaker.py 내부 사용 패턴
from .enums import CircuitBreakerConfig, CircuitState
self._state = CircuitState.CLOSED
if self._state == CircuitState.OPEN:  # str+Enum이므로 문자열 비교도 가능
```

### 3.5 `interfaces/repositories.py` → `CircuitBreakerStateEnum(str, Enum)` — 74건

어댑터(Redis, Memory, Django)와 서비스 코드에서 광범위하게 사용. **이름이 `CircuitState`가 아니라 `CircuitBreakerStateEnum`**이므로 직접적 충돌은 없지만, 동일한 값(`closed`, `open`, `half_open`)을 가진 또 다른 정의.

---

## 4. 통합 방향

### 4.1 통합 전략: 2단계 접근

**Audit 서브시스템은 독립 유지, 나머지 통합.**

#### 이유:
1. Audit 서브시스템(`audit/resilience/`, `audit/graceful_degradation/`)은 **자체 완결형** 회로 차단기 구현으로, 외부에서 import하지 않음
2. 이 모듈들은 **audit 백엔드 보호** 목적으로, 메인 서비스의 CB와 다른 라이프사이클
3. 강제 통합 시 audit 서브시스템의 독립성이 깨질 위험
4. `audit/resilience`의 `CircuitState(Enum)`은 `str` 미상속이므로 통합 시 동작 변경 위험

### 4.2 정규 소스(canonical source) 선정

**`interfaces/repositories.py`의 `CircuitBreakerStateEnum(str, Enum)`을 정규 타입으로 유지.**

| 선정 이유 |
|-----------|
| 1. 이미 74건으로 최다 사용 |
| 2. `str, Enum`이므로 문자열 비교 호환 |
| 3. DTO(`CircuitBreakerStateData.state`)의 기본값 소스 |
| 4. 리포지토리 인터페이스와 동일 모듈 — 계약 일관성 |

### 4.3 이름 변경: `CircuitBreakerStateEnum` → `CircuitState`

현재 이름은 `CircuitBreakerStateEnum`으로 장황합니다. `CircuitState`로 변경하면:
- 기존 소비자 코드와 자연스러운 대체 가능
- 다만 **74곳**의 이름 변경 필요 → 리스크가 있으므로 **별칭(alias) 전략** 사용

```python
# interfaces/repositories.py에 alias 추가
class CircuitState(str, Enum):  # ← 이름 변경
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

# 하위 호환
CircuitBreakerStateEnum = CircuitState  # deprecated alias
```

---

## 5. 실행 계획

### Phase 1: 테스트 전용 데드코드 제거

| 대상 | 파일 | 행동 |
|------|------|------|
| `CircuitState` (plain class) | `tests/factories/constants.py` L61-65 | 삭제 (re-export 외 사용 0건) |
| `DefaultValues.CB_STATE_*` | `tests/factories/constants.py` L100-102 | `CircuitBreakerStateEnum` 사용으로 변경 |
| `CircuitState` re-export | `tests/factories/__init__.py` L31 | 삭제 |

### Phase 2: `core/types.py`의 `CircuitState` 제거

| 대상 | 행동 |
|------|------|
| `core/types.py` L40-47 `CircuitState(str, Enum)` | 삭제 |
| `core/__init__.py` L126 re-export | `interfaces.repositories.CircuitBreakerStateEnum`으로 변경 |
| `__init__.py` L11 공개 API | `interfaces.repositories`에서 import |

### Phase 3: `services/circuit_breaker/config.py`의 `CircuitState` 대체

| 대상 | 변경 |
|------|------|
| `config.py` L20-25 `CircuitState` (plain class) | 삭제 |
| `config.py` | `from selfhealing.interfaces.repositories import CircuitBreakerStateEnum as CircuitState` 추가 |
| `services/circuit_breaker/__init__.py` L52 | re-export 경로 변경 |
| `services/circuit_breaker/manual_control.py` L16 | import 경로 변경 |
| `services/circuit_breaker_service.py` L32 | import 경로 변경 |
| `services/circuit_breaker/stale_cache_integration.py` L25 | import 경로 변경 |

**또는** `config.py`에서 alias로 유지:
```python
# services/circuit_breaker/config.py
from selfhealing.interfaces.repositories import CircuitBreakerStateEnum as CircuitState
```
이 방식은 **소비자 코드 변경 0건**으로 가장 안전합니다.

### Phase 4: Celery task import 변경

| 파일 | 라인 | 변경 |
|------|------|------|
| `adapters/celery/tasks/circuit_breaker.py` | L151 | `from selfhealing.core.types import CircuitState` → `from selfhealing.services.circuit_breaker.config import CircuitState` 또는 `from selfhealing.interfaces.repositories import CircuitBreakerStateEnum as CircuitState` |
| `adapters/celery/tasks/circuit_breaker.py` | L388 | 동일 변경 |

### Phase 5: Audit 서브시스템 — 보류 (독립 유지)

| 모듈 | 상태 | 사유 |
|------|------|------|
| `audit/resilience/circuit_breaker.py` | **변경 안 함** | 파일 내부 전용, `Enum` (no str) — 동작 변경 리스크 |
| `audit/graceful_degradation/enums.py` | **변경 안 함** | 패키지 내부 전용, 자체 `CircuitBreakerConfig`과 밀접 결합 |

---

## 6. 통합 후 최종 상태

| 정의 | 상태 | 위치 |
|------|------|------|
| `CircuitBreakerStateEnum(str, Enum)` | ✅ 정규 소스 | `interfaces/repositories.py` |
| `CircuitState` alias | ✅ 하위 호환 | `core/__init__.py`, `services/circuit_breaker/config.py` |
| `CircuitState(Enum)` | ⚪ 독립 유지 | `audit/resilience/circuit_breaker.py` |
| `CircuitState(str, Enum)` | ⚪ 독립 유지 | `audit/graceful_degradation/enums.py` |
| `CircuitState(str, Enum)` in core/types.py | ❌ 삭제 | — |
| `CircuitState` in tests/factories/constants.py | ❌ 삭제 | — |

---

## 7. 영향 범위

| 카테고리 | 수정 파일 수 | 상세 |
|---------|------------|------|
| 소스 코드 | 5개 | `core/types.py`, `core/__init__.py`, `__init__.py`, `services/circuit_breaker/config.py`, `adapters/celery/tasks/circuit_breaker.py` |
| 테스트 코드 | 3개 | `tests/factories/constants.py`, `tests/factories/__init__.py`, `tests/unit/utils/test_types.py` |
| Audit 코드 | 0개 | 변경 안 함 |

---

## 8. 리스크 평가

| 리스크 | 수준 | 대응 |
|--------|------|------|
| `CircuitState.CLOSED == "closed"` 비교 동작 변경 | 🟢 없음 | `CircuitBreakerStateEnum(str, Enum)`도 `== "closed"` 가능 |
| 서비스 코드에서 `CircuitState` import 깨짐 | 🟢 없음 | `config.py`에 alias 유지 → 소비자 변경 불필요 |
| 순환 import | 🟡 중간 | `interfaces.repositories`는 외부 의존 없음 → 안전 |
| Celery task 동작 변경 | 🟢 없음 | 값(`"closed"`, `"open"`, `"half_open"`)이 동일 |

---

## 9. 검증 절차

```bash
# 1. 남은 core/types.py CircuitState 참조 확인
grep -rn "from selfhealing.core.types import.*CircuitState" packages/selfhealing-python/

# 2. 타입 호환성 확인
python -c "
from selfhealing.interfaces.repositories import CircuitBreakerStateEnum
assert CircuitBreakerStateEnum.CLOSED == 'closed'
assert CircuitBreakerStateEnum.OPEN == 'open'
assert CircuitBreakerStateEnum.HALF_OPEN == 'half_open'
assert isinstance(CircuitBreakerStateEnum.CLOSED, str)
print('All type compatibility checks passed')
"

# 3. 전체 테스트
cd packages/selfhealing-python && python -m pytest tests/ -x --tb=short
```
