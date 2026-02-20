# 통합 테스트 작성 가이드라인

> **적용 범위**: `tests/` 전역 폴더 및 하위 통합 테스트
> **최종 수정일**: 2026-02-20

---

## 0. 통합 테스트의 목적과 필요성 판단

통합 테스트는 **여러 컴포넌트가 조합되었을 때 올바르게 동작하는지** 검증한다.
단위 테스트가 개별 함수/클래스의 동작을 검증한다면, 통합 테스트는 **서비스 간 상호작용, 상태 전파, 워크플로우 전체 흐름**을 검증한다.

### 0.1 통합 테스트가 필요한 경우

통합 테스트 작성 전에 **단위 테스트만으로 충분한지** 먼저 판단한다.

```
구현한 코드가…
├─ 단일 함수/클래스의 독립 동작 → 단위 테스트만 작성
├─ 2개 이상 서비스의 조합 동작 → 통합 테스트 필요
│   예: DLQ 저장 → Circuit Breaker 확인 → Replay 실행
│   예: 설정 변경 → 여러 서비스에 전파 확인
├─ 실제 인프라 연결이 필수인 동작 → 인프라 통합 테스트 필요
│   예: Redis 분산 잠금, DB 트랜잭션, Celery 태스크 체인
│   예: OTel → Tempo/Mimir/Loki 파이프라인
├─ 상태 전이 전체 라이프사이클 → 통합 테스트 필요
│   예: CLOSED → OPEN → HALF_OPEN → CLOSED
└─ 단위 테스트로 이미 충분히 검증됨 → 통합 테스트 불필요
```

### 0.2 테스트 유형 구분

| 항목 | 단위 테스트 | Mock 기반 통합 | 인프라 통합 |
|------|------------|---------------|------------|
| **검증 대상** | 단일 함수/클래스 | 서비스 간 조합 | 실제 인프라 연동 |
| **의존성** | Mock/Stub | InMemory Repository | Docker 서비스 |
| **실행 환경** | 로컬 직접 실행 | 로컬 직접 실행 | `docker-compose.test.yml` |
| **실행 속도** | 빠름 | 보통 | 느림 |
| **병렬 실행** | 항상 가능 | 항상 가능 | 제한적 |

### 0.3 통합 테스트 세부 유형

```
통합 테스트
├─ Mock 기반 통합 (DB 불필요, 로컬 실행)
│   - InMemory Repository 사용
│   - pytest-xdist 병렬 실행 가능
│   - 위치: tests/self_healing/integration/
│
├─ 인프라 의존 통합 (Docker Compose 필수)
│   ├─ DB 통합: requires_db 마커
│   ├─ Redis 통합: requires_redis 마커
│   ├─ Celery 통합: requires_celery 마커
│   ├─ OTel 통합: tests/integration/otel/ (Tempo, Mimir, Loki)
│   └─ Kafka 통합: tests/integration/kafka/
│   - 위치: tests/integration/
│
└─ Django 통합 (DB + Django 설정 필요)
    - django_db 마커 사용
    - 위치: tests/self_healing/django/, shopping/tests/integration/
```

---

## 1. 실행 환경: Docker Compose

### 1.1 docker-compose.test.yml 서비스 구성

통합 테스트에 사용할 수 있는 인프라 서비스:

| 서비스 | 이미지 | 테스트용 포트 | 용도 |
|--------|--------|-------------|------|
| **db** | `postgres:15-alpine` | 15432 | PostgreSQL |
| **redis** | `redis:7-alpine` | 16379 | Redis |
| **kafka** | `confluentinc/cp-kafka:7.5.0` | 19092 | Kafka KRaft |
| **celery-worker** | myproject-test | - | Celery 워커 (다중 큐) |
| **otel-collector** | 커스텀 빌드 | 14317/14318 | OTel Collector |
| **tempo** | `grafana/tempo:2.3.1` | 13200 | 분산 추적 |
| **mimir** | `grafana/mimir:2.11.0` | 19009 | 메트릭 저장소 |
| **loki** | `grafana/loki:2.9.4` | 13100 | 로그 저장소 |

### 1.2 테스트 실행 방법

```bash
# 1. 인프라 시작
docker-compose -f docker-compose.test.yml up -d db redis

# 2. Mock 기반 통합 테스트 (인프라 불필요, 로컬 직접 실행)
pytest tests/self_healing/integration/ --no-cov -n6

# 3. DB 필요 테스트
docker-compose -f docker-compose.test.yml run --rm test-global \
    pytest -m "requires_db" --no-cov

# 4. Redis 필요 테스트
docker-compose -f docker-compose.test.yml run --rm test-global \
    pytest -m "requires_redis" --no-cov

# 5. OTel 파이프라인 테스트
docker-compose -f docker-compose.test.yml run --rm test-otel-all

# 6. Kafka 테스트
docker-compose -f docker-compose.test.yml run --rm test-kafka-event-bus

# 7. 전체 통합 테스트
docker-compose -f docker-compose.test.yml run --rm test-global

# 8. 인프라 정리
docker-compose -f docker-compose.test.yml down
```

### 1.3 인프라 의존성 판단 플로우

새 통합 테스트 작성 시, 필요한 인프라를 판단한다:

```
구현 코드가 사용하는 것은?
├─ selfhealing 서비스 간 조합만 → 인프라 불필요 (InMemory)
├─ Django ORM (Model.objects.*) → DB 필요 (requires_db)
├─ Redis 직접 연결 (redis.Redis, ResilientStorageBackend) → Redis 필요 (requires_redis)
├─ Celery 태스크 (.delay(), .apply_async()) → Celery 필요 (requires_celery)
├─ OTel SDK (trace, metrics, logs export) → OTel 스택 필요
├─ Kafka Producer/Consumer → Kafka 필요
└─ 복합 (예: Redis + Celery) → 복합 마커 + Docker Compose
```

---

## 2. 파일 배치 규칙

### 2.1 전체 테스트 디렉토리 구조

```
tests/                                   # 전역 테스트 폴더 (통합/인프라 테스트 전용)
├── conftest.py                          # 전역 fixtures (Redis, DB, Celery 연결, Auto-skip)
├── factories/                           # Factory/Builder 패턴
│   ├── constants.py                     # 테스트 상수 (RedisTestConfig 등)
│   ├── builders.py                      # Builder 패턴 (CircuitBreakerStateBuilder 등)
│   ├── data_factory.py                  # TestDataFactory
│   └── integration.py                   # IntegrationTestContext
│
├── self_healing/                        # Self-Healing 도메인 테스트
│   ├── integration/                     # Mock 기반 통합 (DB 불필요)
│   │   ├── conftest.py                  # InMemory Repository, Mock 객체
│   │   ├── django/                      # Django 의존 통합 테스트
│   │   └── self_healing/               # 셀프힐링 내부 통합
│   ├── django/                          # Django admin/views 테스트
│   ├── chaos/                           # 카오스 엔지니어링 (인프라 의존)
│   ├── api/                             # Self-healing API 테스트 (Django REST Framework)
│   └── e2e/                             # E2E 테스트
│
├── integration/                         # 인프라 의존 통합 테스트
│   ├── selfhealing/                     # Redis/DB/Celery 통합
│   ├── otel/                            # OTel/Tempo/Mimir/Loki 파이프라인
│   └── kafka/                           # Kafka 이벤트 버스
│
├── hybrid/                              # Celery/타이밍 관련 테스트
├── api/                                 # API 레벨 테스트 (Django 의존)
└── load/                                # 부하 테스트

packages/selfhealing-python/tests/       # Pure Python 패키지 테스트 (Django 불필요)
├── unit/                                # 모든 단위 테스트의 표준 위치
│   ├── services/                        # 서비스 단위 테스트
│   ├── core/                            # 코어 단위 테스트
│   ├── audit/                           # 감사 단위 테스트
│   ├── throttle/                        # 쓰로틀 단위 테스트
│   └── ... (도메인별 하위 폴더)
├── integration/                         # 패키지 내부 통합 테스트
├── chaos/                               # 카오스 테스트 (패키지 수준)
└── factories/                           # 테스트 유틸리티/헬퍼
```

### 2.2 전역 `tests/`는 통합/인프라 테스트 전용

**전역 `tests/` 폴더에 순수 단위 테스트를 배치하지 않는다.**

```
테스트 성격에 따른 배치 판단:
├─ selfhealing 패키지만 import, Django/DB/Redis 불필요
│   → packages/selfhealing-python/tests/unit/ (순수 단위 테스트)
│
├─ Django 설정 필요하지만 실제 DB/Redis 불필요
│   → tests/ 에 배치 가능 (Django가 testpaths에 포함)
│   → 단, 마커 없이도 실행 가능해야 함
│
└─ 실제 인프라(DB/Redis/Celery/OTel/Kafka) 필요
    → tests/ + 적절한 마커 (requires_db, requires_redis 등)
```

### 2.3 `packages/selfhealing-python/tests/` 구조 규칙

- **신규 단위 테스트는 `unit/` 하위에 배치**한다 (도메인별 하위 폴더 구조 사용)
- `unit/` 바깥에 있는 `services/`, `core/`, `audit/`는 레거시 구조로, `unit/` 하위의 동일 이름 폴더와 성격이 같다
- `integration/`, `chaos/`, `factories/`는 성격이 다르므로 독립 유지한다

### 2.4 신규 테스트 파일 배치 기준

| 조건 | 위치 | Docker 필요 |
|------|------|:-----------:|
| selfhealing 패키지 순수 단위 테스트 | `packages/selfhealing-python/tests/unit/` | ❌ |
| selfhealing 패키지 내부 통합 (다중 컴포넌트) | `packages/selfhealing-python/tests/integration/` | ❌ |
| selfhealing 서비스 간 조합 + Mock (DB 불필요) | `tests/self_healing/integration/` | ❌ |
| Django 모델/뷰 포함, 실제 DB 필요 | `tests/self_healing/django/` 또는 `tests/integration/` | ✅ |
| 실제 Redis 연결 필요 | `tests/integration/selfhealing/` | ✅ |
| OTel 파이프라인 검증 | `tests/integration/otel/` | ✅ |
| Kafka 이벤트 버스 | `tests/integration/kafka/` | ✅ |
| Celery 태스크 연동 | `tests/hybrid/` | ✅ |
| 여러 도메인 횡단 E2E | `tests/self_healing/e2e/` | ✅ |

### 2.5 파일 네이밍

```python
# 패턴: test_{도메인}_{검증대상}.py
test_circuit_breaker.py              # CB 워크플로우
test_dlq_storage_and_replay.py       # DLQ 저장/재생
test_cold_start_recovery.py          # Cold Start 복구
test_otel_metrics_pipeline.py        # OTel 메트릭 파이프라인

# ❌ 나쁜 예
test_integration.py                  # 너무 모호
test_misc.py                         # 의미 없음
```

---

## 2. InMemory Repository 패턴

통합 테스트의 기본 전략은 **InMemory Repository**를 사용하여 DB 의존성을 제거하는 것이다.

### 2.1 기존 InMemory Repository 활용

`tests/self_healing/integration/conftest.py`에 이미 정의된 구현체:

| Repository | 클래스 | 용도 |
|------------|--------|------|
| FailedOperationRepository | `InMemoryFailedOperationRepository` | DLQ 엔트리 저장/조회 |
| CircuitBreakerStateRepository | `InMemoryCircuitBreakerStateRepository` | CB 상태 관리 |

```python
# ✅ 기존 InMemory Repository 재사용
from tests.self_healing.integration.conftest import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
)
```

### 2.2 새 InMemory Repository 작성 기준

새로운 Repository 인터페이스가 추가될 때:

1. `selfhealing.interfaces.repositories`에 인터페이스 정의 확인
2. `tests/self_healing/integration/conftest.py`에 InMemory 구현체 추가
3. **모든 인터페이스 메서드를 구현**해야 함 (abstract method 누락 방지)
4. `clear()` 메서드 추가 (테스트 정리용)

```python
# InMemory 구현 패턴
class InMemoryXxxRepository(XxxRepository):
    """
    In-memory implementation of XxxRepository.
    Enables fast, parallel testing without database dependencies.
    """

    def __init__(self):
        self._store: dict[int, XxxData] = {}
        self._next_id = 1

    def create(self, **kwargs) -> XxxData:
        entry = XxxData(id=self._next_id, **kwargs)
        self._store[self._next_id] = entry
        self._next_id += 1
        return entry

    def get_by_id(self, id: int) -> Optional[XxxData]:
        return self._store.get(id)

    def clear(self) -> None:
        """Clear all entries (for test cleanup)."""
        self._store.clear()
        self._next_id = 1
```

---

## 3. Mock 객체 패턴

### 3.1 기존 Mock 객체

`tests/self_healing/integration/conftest.py`에 정의된 dataclass 기반 Mock:

| Mock 클래스 | 대체 대상 | 용도 |
|-------------|----------|------|
| `MockUser` | Django User | 사용자 정보 |
| `MockOrder` | Order 모델 | 주문 데이터 |
| `MockPayment` | Payment 모델 | 결제 데이터 |
| `MockAdminUser` | Staff User | 관리자 권한 |
| `MockTenant` | Tenant 객체 | 멀티테넌시 |
| `MockMetrics` | Prometheus 메트릭 | 관측성 검증 |
| `MockAuditLogRepository` | Audit Log DB | 감사 로그 |
| `MockCostTracker` | 비용 추적기 | 비용 기반 결정 |

### 3.2 새 Mock 객체 작성 규칙

```python
# ✅ dataclass 기반 Mock (권장)
@dataclass
class MockXxx:
    """Mock xxx object for testing."""
    id: int = field(default_factory=lambda: int(time.time() * 1000) % 1000000)
    name: str = "default"
    status: str = "active"

# ✅ 허용: unittest.mock.MagicMock (간단한 의존성)
handler = MagicMock()
handler.handle = MagicMock(return_value={"success": True})

# ❌ 금지: Mock 기반 통합 테스트에서 Django ORM 직접 생성
order = Order.objects.create(...)  # DB 의존성 발생 → InMemory 또는 인프라 통합으로 분류

# ✅ 인프라 통합 테스트에서는 실제 모델 사용 가능 (마커 필수)
@pytest.mark.requires_db
def test_with_real_db():
    ...  # requires_db 마커로 인프라 의존 명시
```

### 3.3 Mock 배치 위치

| 사용 범위 | 위치 |
|-----------|------|
| 1개 테스트 파일 전용 | 해당 테스트 파일 내부 |
| 같은 디렉토리 2+ 파일 공유 | 해당 디렉토리 `conftest.py` |
| 전체 통합 테스트 공유 | `tests/self_healing/integration/conftest.py` |
| Factory/Builder 패턴 | `tests/factories/` |

---

## 4. Fixture 패턴

### 4.1 서비스 Fixture

서비스는 **InMemory Repository를 주입**하여 생성한다.

```python
# conftest.py
@pytest.fixture
def dlq_service(failed_operation_repository):
    """DLQ 서비스 (InMemory Repository 주입)."""
    return DLQService(
        repository=failed_operation_repository,
        config=DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2),
    )

@pytest.fixture
def circuit_breaker_service(circuit_breaker_repository):
    """Circuit Breaker 서비스 (InMemory Repository 주입)."""
    return CircuitBreakerService(repository=circuit_breaker_repository)
```

### 4.2 setup_method 패턴

클래스 기반 테스트에서 매 테스트마다 상태를 초기화한다.

```python
class TestCircuitBreakerWorkflow:
    """CB 워크플로우 통합 테스트."""

    def setup_method(self):
        """테스트마다 새로운 상태로 초기화."""
        self.config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60,
            success_threshold=2,
        )
        self.repository = InMemoryCircuitBreakerStateRepository()
```

### 4.3 인프라 Fixture (전역 conftest.py)

`tests/conftest.py`의 세션 스코프 fixture를 사용한다.

```python
# 이미 정의된 전역 fixture 목록:
# redis_client              - 실제 Redis 연결 (session scope)
# docker_redis_client       - Docker Redis (session scope)
# clean_redis               - 매 테스트 전/후 Redis flush
# docker_db_connection      - Docker PostgreSQL (session scope)
# celery_eager_mode         - Celery 동기 실행 모드
# integration_context       - Redis + DB 통합 컨텍스트
# circuit_breaker_builder   - Builder 패턴
# failed_operation_builder  - Builder 패턴
# test_data                 - TestDataFactory
```

### 4.4 Builder/Factory 활용

```python
# Builder 패턴 사용
def test_cb_recovery(circuit_breaker_builder):
    state = (circuit_breaker_builder
        .payment_service()
        .opened()
        .with_failure_count(5)
        .build())

# DataFactory 사용
def test_dlq_entry(test_data):
    failed_op = test_data.failed_operation(
        domain="payment",
        failure_type="PG_TIMEOUT",
    )
```

---

## 5. 인프라 의존성 마커

### 5.1 마커 사용 규칙

실제 인프라(Redis, DB, Celery, OTel, Kafka)에 연결하는 테스트는 **반드시 마커를 선언**해야 한다.

```python
# ✅ 올바른 마커 사용
@pytest.mark.requires_redis
class TestRedisIntegration:
    """실제 Redis 연결 테스트."""
    ...

@pytest.mark.requires_db
def test_db_transaction():
    """실제 DB 트랜잭션 테스트."""
    ...

# ✅ 복합 마커
@pytest.mark.requires_db
@pytest.mark.requires_redis
class TestFullInfraIntegration:
    ...

# ✅ 파일 단위 마커 (파일 내 모든 테스트에 적용)
pytestmark = [pytest.mark.requires_db, pytest.mark.requires_redis]

# ❌ 마커 없이 인프라 접근 금지
class TestBadExample:
    def test_redis_without_marker(self, redis_client):  # 마커 없음 → 로컬 실행 시 실패
        ...
```

### 5.2 인프라별 마커 및 Docker 서비스

| 인프라 | 마커 | 환경변수 | docker-compose 서비스 | 테스트 포트 |
|--------|------|---------|----------------------|------------|
| PostgreSQL | `requires_db` | `TEST_DB_AVAILABLE=true` | `db` | 15432 |
| Redis | `requires_redis` | `TEST_REDIS_AVAILABLE=true` | `redis` | 16379 |
| Celery | `requires_celery` | - | `celery-worker` | - |
| OTel | (폴더 분리) | - | `otel-collector` | 14317/14318 |
| Tempo | (폴더 분리) | - | `tempo` | 13200 |
| Mimir | (폴더 분리) | - | `mimir` | 19009 |
| Loki | (폴더 분리) | - | `loki` | 13100 |
| Kafka | (폴더 분리) | - | `kafka` | 19092 |

### 5.3 Auto-Skip 메커니즘

`tests/conftest.py`의 `pytest_collection_modifyitems` 훅이 환경변수를 확인하여 자동 skip한다.
또한 `pyproject.toml`의 `addopts`에서 기본적으로 인프라 의존 마커를 제외한다.

```bash
# pyproject.toml 기본 제외 설정:
# -m 'not requires_redis and not requires_db and not e2e and not slow ...'
#
# 따라서 로컬에서 pytest만 실행하면 인프라 불필요 테스트만 실행됨
pytest --no-cov -n6           # Mock 기반 테스트만 실행

# 인프라 포함 실행 시 마커 명시
pytest -m "requires_db" --no-cov  # DB 테스트만
pytest -m "requires_redis" --no-cov  # Redis 테스트만
```

### 5.3 CI 티어 마커

테스트 실행 시간에 따라 CI 파이프라인을 분리한다.

| 마커 | 용도 | 포함 범위 |
|------|------|----------|
| `tier1` | PR 검증 (< 2분) | Mock 기반 통합 테스트 |
| `tier2` | 머지 검증 (< 10분) | 인프라 의존 통합 테스트 |
| `tier3_chaos` | 야간 실행 (30분+) | 카오스 엔지니어링 |
| `tier4_load` | 릴리즈 전 (1시간+) | 부하 테스트 |

---

## 6. 테스트 작성 패턴

### 6.1 테스트 파일 구조

```python
"""
{테스트 도메인} Integration Tests

{테스트 대상 설명}

Test Categories:
    A. {카테고리 1}:
        - {테스트 항목 1}
        - {테스트 항목 2}
    B. {카테고리 2}:
        - ...

Note: All tests use in-memory mock repositories - no DB dependency.
      This enables parallel test execution with pytest-xdist.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.services import ...
from .conftest import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    MockUser,
    ...
)


# =============================================================================
# A. {카테고리 1} Tests
# =============================================================================


class Test{도메인}{검증대상}:
    """
    {테스트 클래스 설명}

    Validates:
    - {검증 항목 1}
    - {검증 항목 2}
    """

    def setup_method(self):
        """Set up test fixtures."""
        ...

    def test_{동작_설명}(self):
        """
        Purpose:
            {테스트 목적}
        Expected:
            - {기대 결과 1}
            - {기대 결과 2}
        """
        ...
```

### 6.2 테스트 독스트링 규칙

통합 테스트는 단위 테스트보다 복잡하므로 **Purpose/Expected 구조**를 권장한다.

```python
# ✅ 좋은 예: 목적과 기대 결과 명시
def test_force_open_creates_audit_log(self, circuit_breaker_service, audit_log_repository):
    """
    Purpose:
        CB force open 시 감사 로그가 생성되는지 검증.
    Expected:
        - AuditEntry가 1개 생성
        - action_type이 "circuit_breaker_action"
        - previous_state와 new_state가 정확히 기록
    """

# ❌ 나쁜 예: 모호한 설명
def test_force_open(self):
    """Force open 테스트."""
```

### 6.3 Django Settings Override

Django 설정이 필요한 통합 테스트에서는 `patch`를 사용한다.

```python
# ✅ patch로 Django settings 오버라이드
SETTINGS = {
    "SELF_HEALING": {
        "CIRCUIT_BREAKER": {
            "ENABLED": True,
            "FAILURE_THRESHOLD": 5,
        },
    }
}

@patch("django.conf.settings.SELF_HEALING", SETTINGS["SELF_HEALING"], create=True)
def test_with_settings(self):
    ...

# 또는 테스트 파일 상단에 상수로 정의
CIRCUIT_BREAKER_SETTINGS = {...}
```

---

## 7. 워크플로우 테스트 패턴

### 7.1 전체 라이프사이클 검증

통합 테스트의 핵심은 **상태 전이 전체 과정**을 검증하는 것이다.

```python
class TestCircuitBreakerLifecycle:
    """CB 상태 전이 전체 라이프사이클."""

    def test_full_lifecycle(self, circuit_breaker_service, circuit_breaker_repository):
        """
        Purpose:
            CLOSED → OPEN → HALF_OPEN → CLOSED 전체 라이프사이클 검증.
        """
        service_name = "payment-api"

        # Phase 1: 실패 누적 → OPEN
        for _ in range(5):
            circuit_breaker_service.record_failure(service_name)
        state = circuit_breaker_repository.get_by_service_name(service_name)
        assert state.state == "open"

        # Phase 2: 타임아웃 후 → HALF_OPEN
        ...

        # Phase 3: 성공 → CLOSED
        ...
```

### 7.2 다중 컴포넌트 상호작용

```python
class TestDLQReplayWithCircuitBreaker:
    """DLQ Replay와 Circuit Breaker 연동."""

    def test_replay_blocked_when_circuit_open(self, dlq_service, circuit_breaker_service):
        """
        Purpose:
            CB가 OPEN 상태일 때 DLQ replay가 차단되는지 검증.
        """
        # CB를 OPEN 상태로 설정
        circuit_breaker_service.force_open("payment-api", reason="test")

        # Replay 시도 → 차단 확인
        ...
```

### 7.3 시뮬레이터 패턴

복잡한 시나리오(Cold Start, Multi-instance 등)에서는 시뮬레이터 클래스를 사용한다.

```python
class ColdStartSimulator:
    """Cold Start 시나리오 시뮬레이션."""

    def __init__(self):
        self._instances = []
        self._config = {...}

    def simulate_startup(self) -> ServiceInstance:
        """새 인스턴스 시작 시뮬레이션."""
        ...

    def simulate_restart(self, instance):
        """인스턴스 재시작 시뮬레이션."""
        ...
```

---

## 8. conftest.py 관리

### 8.1 크기 제한

통합 테스트 conftest.py는 InMemory Repository 구현체로 인해 커질 수 있다.

| 줄 수 | 조치 |
|-------|------|
| ≤ 500줄 | 정상 |
| 500~1000줄 | Mock 클래스를 별도 모듈로 분리 검토 |
| > 1000줄 | **반드시 분리** |

분리 대상 우선순위:
1. **InMemory Repository** → `tests/self_healing/integration/repositories.py`
2. **Mock 데이터 클래스** → `tests/self_healing/integration/mocks.py`
3. **시뮬레이터** → 테스트 파일 내부 또는 별도 모듈

### 8.2 fixture scope 선택

| scope | 통합 테스트 사용 조건 | 예시 |
|-------|---------------------|------|
| `function` (기본) | InMemory Repository, Mock 객체 | `failed_operation_repository` |
| `session` | 실제 인프라 연결 (Redis, DB) | `redis_client`, `docker_db_connection` |
| `class` | 같은 클래스 내 읽기 전용 설정 | `CircuitBreakerConfig` |

---

## 9. 병렬 실행 호환성

### 9.1 필수 조건

Mock 기반 통합 테스트는 `pytest-xdist -n6` 병렬 실행을 지원해야 한다.

```python
# ✅ 병렬 안전: InMemory Repository (각 테스트가 독립 인스턴스)
@pytest.fixture
def failed_operation_repository():
    return InMemoryFailedOperationRepository()  # 매번 새 인스턴스

# ❌ 병렬 위험: 공유 상태
_shared_state = {}  # 워커 간 공유 → 레이스 컨디션
```

### 9.2 격리 보장

- 각 테스트는 **독립적인 fixture 인스턴스**를 사용한다
- 전역 상태(`global`, 모듈 변수)를 수정하지 않는다
- `setup_method`에서 상태를 완전히 초기화한다
- `session` scope fixture는 읽기 전용이거나, 테스트별 cleanup이 보장되어야 한다

---

## 10. 체크리스트

통합 테스트 작성 시 확인:

### 사전 판단
- [ ] **통합 테스트 필요성**: 단위 테스트만으로 충분한지 판단 (§0.1)
- [ ] **인프라 필요성**: 어떤 인프라가 필요한지 확인 (§1.3 플로우 참조)
- [ ] **Docker Compose**: 인프라 필요 시 `docker-compose.test.yml` 서비스 확인 (§1.1)

### 파일 배치
- [ ] **위치**: Mock 기반 → `tests/self_healing/integration/`, 인프라 → `tests/integration/` (§2.2)
- [ ] **네이밍**: `test_{도메인}_{검증대상}.py` 패턴 (§2.3)

### 구현
- [ ] **InMemory Repository**: 기존 구현체 재사용, 없으면 conftest에 추가 (§2 InMemory)
- [ ] **Mock 객체**: dataclass 기반 Mock 사용 (§3)
- [ ] **인프라 마커**: 실제 Redis/DB/Celery 사용 시 마커 부착 (§5)
- [ ] **독스트링**: Purpose/Expected 구조 작성 (§6.2)
- [ ] **파일 독스트링**: Test Categories 목록 포함 (§6.1)
- [ ] **setup_method**: 클래스 기반 테스트 시 매번 상태 초기화

### 검증
- [ ] **로컬 실행**: `pytest tests/self_healing/integration/ --no-cov -n6` 통과
- [ ] **인프라 테스트**: `docker-compose -f docker-compose.test.yml` 로 실행 통과
- [ ] **병렬 실행**: pytest-xdist 호환 확인 (공유 상태 없음) (§9)
- [ ] **conftest 크기**: 1000줄 초과 시 분리 검토 (§8)
- [ ] **cleanup**: 테스트 격리 보장 (InMemory clear / fixture 재생성 / Redis flush)
