# 57. Test Refactoring Plan: Factory Pattern Migration

> **범위**: `packages/selfhealing-python/tests/` (321개 파일, 4,351개 테스트)  
> **목표**: 하드코딩 제거, Fixture 슬림화, Factory Pattern 도입  
> **예상 기간**: 2-3주

---

## 1. 현재 상태 분석 (코드 근거)

### 1.1 수치 요약

| 항목 | 개수 | 문제점 |
|------|------|--------|
| 테스트 파일 | 321개 | - |
| 테스트 함수 (`def test_`) | 4,351개 | - |
| `@pytest.fixture` 정의 | 265개 | 과도한 fixture, 중복 |
| `Mock()`/`MagicMock()` 직접 생성 | 875회 | Factory 없이 매번 생성 |
| `with patch` / `@patch` 사용 | 627회 | 패치 경로 하드코딩 |
| `conftest.py` 파일 | 11개 (1,188줄) | Mock 클래스 중복 정의 |
| `datetime.now()` 직접 호출 | 156회 | 시간 제어 불가 |
| `time.sleep()` 사용 | 91회 | 테스트 속도 저하 |
| `tempfile` 사용 | 90회 | 정리 누락 가능성 |

### 1.2 주요 문제 패턴

#### 문제 A: MockRedisClient 중복 정의

```
packages/selfhealing-python/tests/unit/audit/hash_chain_core/conftest.py (90줄)
packages/selfhealing-python/tests/unit/audit/graceful_degradation/conftest.py (90줄)
```

**증거 코드** (`hash_chain_core/conftest.py:18-95`):
```python
class MockRedisClient:
    """테스트용 Mock Redis 클라이언트."""
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        # ... 90줄 구현
```

**동일 코드** (`graceful_degradation/conftest.py:18-95`):
```python
class MockRedisClient:
    """Mock Redis client for testing."""
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        # ... 거의 동일한 90줄
```

---

#### 문제 B: 테스트별 Mock 데이터 직접 생성

**증거** (`services/circuit_breaker/test_service.py:18-56`):
```python
@dataclass
class MockCircuitBreakerStateData:
    """Mock circuit breaker state data."""
    service_name: str
    state: str = "closed"
    failure_count: int = 0
    success_count: int = 0
    # ... 각 파일마다 비슷한 클래스 반복

class MockRepository:
    """Mock CircuitBreakerStateRepository."""
    def __init__(self):
        self._states = {}
    # ... 20줄 구현 반복
```

**증거** (`services/dlq/test_entry_operations.py:22-48`):
```python
def make_mock_entry(
    id: int = 1,
    domain: str = "payment",  # 하드코딩
    failure_type: str = "PG_TIMEOUT",  # 하드코딩
    status: str = "pending",
    # ...
) -> Mock:
    entry = Mock(spec=FailedOperationData)
    entry.id = id
    entry.domain = domain
    # ... 매번 수동 설정
```

---

#### 문제 C: 도메인/서비스명 하드코딩

```bash
$ grep -rn "domain.*=.*\"payment" | wc -l
164  # "payment" 도메인 164회 하드코딩

$ grep -rn "service_name.*=.*\"test_service" | wc -l
50+  # "test_service" 50회 이상 하드코딩
```

---

#### 문제 D: time.sleep()으로 인한 테스트 속도 저하

```bash
$ grep -rn "sleep(" packages/selfhealing-python/tests | wc -l
91  # 91곳에서 sleep 사용
```

**증거** (`test_crash_recovery.py`):
```python
time.sleep(0.1)  # 100ms 대기
time.sleep(0.5)  # 500ms 대기
# 테스트당 누적 시 수 초 소요
```

---

## 2. 리팩토링 범위 (이것이 전부인가?)

### 2.1 필수 작업 (Core)

| # | 작업 | 파일 영향 | 우선순위 |
|---|------|----------|----------|
| 1 | **TestDataFactory 생성** | 신규 1개 | P0 |
| 2 | **MockRedisClient 통합** | 2개 conftest 수정 | P0 |
| 3 | **InMemory Repository 통합** | 15+ 파일 | P1 |
| 4 | **make_mock_* 함수 통합** | 10+ 파일 | P1 |
| 5 | **Fixture → Factory 전환** | ~100개 fixture | P2 |

### 2.2 추가 작업 (확장)

| # | 작업 | 이유 | 우선순위 |
|---|------|------|----------|
| 6 | **FreezeTime 도입** | datetime.now() 156회 제어 | P2 |
| 7 | **sleep 제거/모킹** | 91회 sleep → 테스트 속도 | P2 |
| 8 | **Patch 경로 상수화** | 627회 patch 경로 관리 | P3 |
| 9 | **Assertion Helper** | 4,981회 assert 패턴화 | P3 |
| 10 | **Marker 표준화** | tier1, asyncio 등 통일 | P3 |

### 2.3 선택 작업 (Nice-to-have)

| # | 작업 | 이유 |
|---|------|------|
| 11 | **Parameterized Test 확대** | 반복 테스트 케이스 압축 |
| 12 | **Test Docstring 표준화** | Purpose/Scenario/Expected 형식 |
| 13 | **Coverage 갭 분석** | 미테스트 코드 식별 |

---

## 3. 얻는 이득

### 3.1 정량적 이득

| 지표 | 현재 | 목표 | 개선율 |
|------|------|------|--------|
| Mock 생성 코드량 | ~875회 분산 | ~50회 (Factory) | **94% 감소** |
| conftest.py 중복 | 1,188줄 | ~400줄 | **66% 감소** |
| 테스트 수정 시 변경점 | 다수 파일 | Factory 1곳 | **유지보수 90%↓** |
| sleep 대기 시간 | ~91 * 0.2s = 18s | ~0s | **18초 단축** |
| 새 테스트 작성 시간 | Mock 직접 구성 | Factory 호출 1줄 | **80% 단축** |

### 3.2 정성적 이득

1. **일관성**: 모든 테스트가 동일한 Factory 사용 → 데이터 형식 통일
2. **유지보수**: FailedOperationData 필드 추가 시 Factory만 수정
3. **가독성**: 테스트 코드에서 Mock 생성 로직 제거 → 의도 명확
4. **확장성**: 새 도메인 추가 시 Factory에 메서드 1개 추가
5. **속도**: sleep 제거 → CI/CD 파이프라인 시간 단축
6. **신뢰성**: FreezeTime으로 시간 관련 테스트 결정론적

### 3.3 위험 관리

| 위험 | 완화 방안 |
|------|----------|
| 대규모 변경으로 기존 테스트 깨짐 | 점진적 마이그레이션, 기존 테스트 유지 |
| Factory 설계 오류 | 작은 범위(CB)부터 시작, 검증 후 확대 |
| 팀 학습 곡선 | 문서화, 예제 코드 제공 |

---

## 4. 상세 계획

### Phase 1: Foundation (3일)

**Day 1-2: Factory 기반 구축**
```
packages/selfhealing-python/tests/
├── factories/
│   ├── __init__.py          # TestDataFactory
│   ├── repositories.py      # InMemory Repositories
│   ├── redis.py             # MockRedisClient
│   └── builders.py          # Complex object builders
```

**Day 3: 통합 테스트**
- Factory 사용하는 예제 테스트 5개 작성
- CI에서 검증

### Phase 2: Migration - Circuit Breaker (3일)

**대상 파일**:
```
tests/services/circuit_breaker/test_service.py
tests/services/circuit_breaker/test_config.py
tests/services/circuit_breaker/test_protection.py
tests/services/circuit_breaker/test_manual_control.py
... (15개 파일)
```

**작업**:
1. MockCircuitBreakerStateData → Factory 이동
2. MockRepository → InMemory Repository 사용
3. 개별 fixture → Factory fixture 전환

### Phase 3: Migration - DLQ (2일)

**대상 파일**:
```
tests/services/dlq/test_entry_operations.py
tests/services/dlq/test_list_operations.py
... (5개 파일)
```

### Phase 4: Migration - Audit (3일)

**대상 파일**:
```
tests/unit/audit/hash_chain_core/*.py
tests/unit/audit/graceful_degradation/*.py
tests/unit/audit/*.py
```

**특별 작업**:
- MockRedisClient 통합
- 2개 conftest.py 제거

### Phase 5: Optimization (2일)

1. FreezeTime 도입 (`freezegun` 패키지)
2. sleep 제거/모킹
3. 전체 테스트 실행 및 검증

---

## 5. 다음 문서

- [58_TEST_FACTORY_IMPLEMENTATION.md](58_TEST_FACTORY_IMPLEMENTATION.md) - Factory 구현 상세
- [59_TEST_MIGRATION_CHECKLIST.md](59_TEST_MIGRATION_CHECKLIST.md) - 파일별 마이그레이션 체크리스트

---

## 6. 진행 현황

### ✅ Phase 1 완료 (2026-01-20)

**생성된 파일:**
```
packages/selfhealing-python/tests/factories/
├── __init__.py           # 모듈 export
├── data_factory.py       # TestDataFactory, DefaultValues
├── redis.py              # MockRedisClient, MockPipeline, MockDistributedLock
└── repositories.py       # InMemoryCircuitBreakerRepository, InMemoryRateLimitTracker, InMemoryDLQRepository
```

**구현 내용:**
- `TestDataFactory`: 테스트 데이터 생성 Factory 클래스
- `MockCircuitBreakerStateData`: CB 상태 데이터 Mock
- `MockRedisClient`: 통합 Redis Mock (hash_chain_core + graceful_degradation)
- `InMemoryCircuitBreakerRepository`: CB Repository 인메모리 구현
- `InMemoryRateLimitTracker`: Rate Limit Tracker 인메모리 구현
- `InMemoryDLQRepository`: DLQ Repository 인메모리 구현
- `DefaultValues`: 하드코딩 값 중앙 관리 상수 클래스

### ✅ Phase 2 완료 (2026-01-20)

**리팩토링된 파일 (6개):**

| 파일 | 변경 내용 |
|------|----------|
| `test_service.py` | `MockCircuitBreakerStateData`, `MockRepository` 제거 → Factory 사용 |
| `test_protection.py` | `MockCircuitBreakerStateData`, `MockRepository`, `MockRateLimitTracker` 제거 → Factory 사용 |
| `test_manual_control.py` | `MockCircuitBreakerStateData`, `MockRepository` 제거 → Factory 사용 |
| `test_convenience.py` | `MockCircuitBreakerStateData`, `MockRepository` 제거 → Factory 사용 |
| `test_entry_operations.py` | `make_mock_entry` 함수 제거 → `TestDataFactory.mock_failed_operation` 사용 |
| `test_list_operations.py` | `make_mock_entries` 함수 리팩토링 → `TestDataFactory.mock_failed_operation` 사용 |

**테스트 결과:**
- 로컬: 388 passed (CB) + 19 passed (DLQ)
- Docker: 407 passed (전체)

**제거된 중복 코드:**
- `MockCircuitBreakerStateData` 정의 4개 → 1개 (Factory)
- `MockRepository` 정의 4개 → 1개 (`InMemoryCircuitBreakerRepository`)
- `MockRateLimitTracker` 정의 1개 → 1개 (`InMemoryRateLimitTracker`)
- `make_mock_entry` 함수 → `TestDataFactory.mock_failed_operation`

---

## 7. 남은 작업

### Phase 3: Audit Migration (예정)
- `tests/unit/audit/hash_chain_core/conftest.py`의 `MockRedisClient` → `factories.MockRedisClient` 사용
- `tests/unit/audit/graceful_degradation/conftest.py`의 `MockRedisClient` → `factories.MockRedisClient` 사용

### Phase 4-5: Optimization (예정)
- FreezeTime 도입
- sleep 제거/모킹

