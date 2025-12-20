# Integration Test Mock Conversion Guide

## 개요

이 문서는 `tests/self_healing/integration/` 폴더의 모든 테스트를 **Mock 기반**으로 변환하는 방법을 설명합니다.

**목적:**
- DB 의존성 제거로 병렬 실행(`pytest-xdist -n6`) 가능
- selfhealing 패키지의 독립적 테스트 환경 구축
- 테스트 속도 향상 (DB I/O 제거)

---

## 변환 완료 파일 (75 tests)

| 파일 | 테스트 수 | 상태 |
|------|-----------|------|
| `test_dlq_storage_and_replay.py` | 45 | ✅ 완료 |
| `test_circuit_breaker.py` | 10 | ✅ 완료 |
| `test_forensic_replay.py` | 8 | ✅ 완료 |
| `test_audit_accountability.py` | 12 | ✅ 완료 |

---

## 변환 대상 파일 (68 django_db markers)

| 파일 | 마커 수 | 복잡도 | 권장 처리 |
|------|---------|--------|-----------|
| `test_control_api.py` | 17 | 높음 | Skip 또는 Django 앱 통합으로 유지 |
| `test_cold_start_recovery.py` | 7 | 중간 | 변환 가능 |
| `test_l3_self_healing.py` | 6 | 중간 | 변환 가능 |
| `test_multi_tenancy_isolation.py` | 5 | 높음 | Skip (이미 처리됨) |
| `test_redis_failure_scenarios.py` | 5 | 중간 | Skip (인프라 테스트) |
| `test_time_based_behaviors.py` | 4 | 중간 | 변환 가능 |
| `test_observability_metrics.py` | 3 | 낮음 | 변환 가능 |
| `test_redis_fallback.py` | 1 | 낮음 | Skip (인프라 테스트) |
| `test_circuit_breaker_distributed.py` | 1 | 낮음 | Skip (인프라 테스트) |
| `test_security_dlq_separation.py` | 1 | 낮음 | 변환 가능 |
| `test_db_connection_recovery.py` | 1 | 낮음 | Skip (DB 테스트) |

---

## 변환 패턴

### 1. Import 변경

**Before:**
```python
from django.utils import timezone
from shopping.models.failed_operation import FailedOperation
from shopping.tests.factories import UserFactory, OrderFactory, PaymentFactory
```

**After:**
```python
from selfhealing.core.timezone import now
from .conftest import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    MockUser,
    MockOrder,
    MockPayment,
)
```

### 2. Django DB 마커 제거

**Before:**
```python
@pytest.mark.django_db(transaction=True)
class TestSomething:
    ...
```

**After:**
```python
class TestSomething:
    ...
```

### 3. Factory → Mock 객체

**Before:**
```python
user = UserFactory(is_staff=True)
order = OrderFactory(user=user)
payment = PaymentFactory(order=order, status="failed")
```

**After:**
```python
user = MockUser(is_staff=True)
order = MockOrder(user=user)
payment = MockPayment(order=order, status="failed")
```

### 4. ORM 쿼리 → Repository 메서드

**Before:**
```python
FailedOperation.objects.create(
    domain="payment",
    failure_type="PG_TIMEOUT",
    ...
)

entries = FailedOperation.objects.filter(status="pending")
```

**After:**
```python
repository = InMemoryFailedOperationRepository()

entry = repository.create(
    domain="payment",
    failure_type="PG_TIMEOUT",
    ...
)

entries = repository.find_by_status("pending")
```

### 5. CircuitBreakerState ORM → Repository

**Before:**
```python
state = CircuitBreakerState.objects.get(service_name=service_name)
state.state = "open"
state.save()
```

**After:**
```python
repository = InMemoryCircuitBreakerStateRepository()
state = repository.get_by_service_name(service_name)
repository.update_state(service_name, state="open")
```

### 6. Celery Task Mock

**Before:**
```python
@patch("shopping.tasks.self_healing_tasks.conditional_replay_on_circuit_close.delay")
def test_something(self, mock_task):
    ...
    mock_task.assert_called_once_with(service_name=service_name)
```

**After:**
```python
def test_something(self):
    mock_queue = MagicMock()
    mock_queue.enqueue.return_value = "task-123"
    mock_registry = MagicMock()
    mock_registry.get_queue.return_value = mock_queue

    with patch("selfhealing.factory.ProviderRegistry", mock_registry):
        ...
        mock_queue.enqueue.assert_called_once()
```

### 7. Replay Handler 등록

**Before:**
```python
replay_service.register_handler("payment", mock_handler)
```

**After:**
```python
from selfhealing.services.replay_service import _replay_handlers

with patch.dict(_replay_handlers, {"payment": mock_handler}, clear=True):
    ...
```

---

## conftest.py에서 사용 가능한 클래스

### In-Memory Repositories

```python
class InMemoryFailedOperationRepository:
    """FailedOperation 저장소 - 모든 CRUD 메서드 지원"""
    
    def create(...) -> FailedOperationData
    def get_by_id(id) -> Optional[FailedOperationData]
    def get_pending_by_domain(domain, limit) -> list
    def find_by_status(status, domain, failure_type) -> list
    def find_replayable(max_retries, domain) -> list
    def update_status(id, status, ...) -> bool
    def mark_as_resolved(id, resolution_type, ...) -> bool
    def try_acquire_for_replay(id, max_retries) -> Optional[FailedOperationData]
    def complete_replay(id, success, ...) -> bool
    def get_pending_by_failure_types(failure_types, max_retry_count) -> list
    def mark_as_requires_review(id, note) -> bool
    def archive_old_resolved(older_than, batch_size) -> int


class InMemoryCircuitBreakerStateRepository:
    """CircuitBreakerState 저장소"""
    
    def get_or_create(service_name) -> CircuitBreakerStateData
    def get_by_service_name(service_name) -> Optional[CircuitBreakerStateData]
    def update_state(service_name, state, failure_count, ...) -> bool
    def increment_failure(service_name) -> int
    def increment_success(service_name) -> int
    def reset_counts(service_name) -> bool
    def atomic_force_open(service_name, reason, ...) -> tuple
    def atomic_force_close(service_name, reason, ...) -> tuple
    def atomic_reset(service_name, reason, ...) -> tuple
```

### Mock 객체

```python
@dataclass
class MockUser:
    id: int
    username: str = "testuser"
    email: str = "test@example.com"
    is_staff: bool = False
    is_superuser: bool = False


@dataclass
class MockOrder:
    id: int
    user: Any = None
    status: str = "confirmed"
    total_amount: Decimal = Decimal("10000")


@dataclass
class MockPayment:
    id: int
    order: Any = None
    status: str = "in_progress"
    amount: Decimal = Decimal("10000")
    payment_key: str = "pay_xxx"
```

### Audit & Cost Trackers

```python
class MockAuditLogRepository:
    """감사 로그 저장소"""
    
    def log_circuit_breaker_action(...)
    def log(entry: AuditEntry)
    def find_by_action(action_type, service_name) -> list
    def find_by_dlq_id(dlq_id) -> list


class MockCostTracker:
    """비용 추적기"""
    
    def get_cost(dlq_id) -> float
    def set_cost(dlq_id, cost)
    def is_high_cost(dlq_id, threshold) -> bool
```

---

## 파일별 변환 체크리스트

### test_control_api.py (17 markers)

⚠️ **권장: Skip 처리**

REST API 테스트로 Django Test Client에 강하게 의존합니다. selfhealing 패키지 판매 시 구매자가 자체 API 통합 테스트를 작성해야 합니다.

```python
# 파일 상단에 추가
pytestmark = pytest.mark.skip(reason="Django REST API integration - not part of selfhealing package tests")
```

### test_cold_start_recovery.py (7 markers)

**변환 가능** - 핵심 로직은 selfhealing 패키지에 포함됨

1. `ColdStartSimulator` 클래스는 이미 mock 기반
2. `CircuitBreakerState.objects` → `InMemoryCircuitBreakerStateRepository`
3. `FailedExternalRequest.objects` → `InMemoryFailedOperationRepository`
4. `UserFactory/OrderFactory/PaymentFactory` → `MockUser/MockOrder/MockPayment`

### test_l3_self_healing.py (6 markers)

**변환 가능** - Self-healing 핵심 테스트

1. `CeleryPaymentRecovery` → Mock recovery handler
2. Backoff 계산 테스트 → 이미 단위 테스트 성격
3. DLQ 이동 테스트 → `InMemoryFailedOperationRepository` 사용

### test_time_based_behaviors.py (4 markers)

**변환 가능**

1. `timezone.now()` → `selfhealing.core.timezone.now()`
2. 시간 조작 → `freezegun` 또는 직접 mock

### test_observability_metrics.py (3 markers)

**변환 가능**

1. `MockMetrics` 클래스가 이미 conftest.py에 있음
2. 메트릭 수집 테스트만 하면 됨

### test_security_dlq_separation.py (1 marker)

**변환 가능**

1. 간단한 DLQ 분리 테스트
2. `InMemoryFailedOperationRepository` 사용

### Redis 관련 파일들

⚠️ **권장: Skip 처리**

인프라 테스트로 selfhealing 패키지와 무관합니다.

```python
# test_redis_failure_scenarios.py
# test_redis_fallback.py
# test_circuit_breaker_distributed.py

pytestmark = pytest.mark.skip(reason="Infrastructure tests - require Redis")
```

### test_db_connection_recovery.py

⚠️ **권장: Skip 처리**

DB 연결 복구 테스트로 인프라에 의존합니다.

```python
pytestmark = pytest.mark.skip(reason="DB infrastructure test")
```

---

## 실행 확인

변환 후 테스트 실행:

```bash
# 단일 프로세스 실행
pytest tests/self_healing/integration/ -n0 --no-cov -q

# 병렬 실행 (목표)
pytest tests/self_healing/integration/ -n6 --no-cov -q

# 특정 파일만 실행
pytest tests/self_healing/integration/test_circuit_breaker.py -n0 --no-cov -v
```

---

## 트러블슈팅

### 1. AttributeError: 'str' object has no attribute 'state'

`CircuitBreakerService.get_state()`는 문자열을 반환합니다.

```python
# Wrong
assert state.state == "closed"

# Correct
assert state == "closed"
```

### 2. TypeError: unexpected keyword argument 'opened_at'

`InMemoryCircuitBreakerStateRepository.update_state()`에 파라미터가 추가되었는지 확인하세요.

### 3. ProviderRegistry patch 실패

모듈 내부가 아닌 `selfhealing.factory`를 패치하세요.

```python
# Wrong
with patch("selfhealing.services.circuit_breaker.manual_control.ProviderRegistry"):

# Correct
with patch("selfhealing.factory.ProviderRegistry", mock_registry):
```

### 4. service_failure_type_map 누락

`replay_on_circuit_close()` 호출 시 필수 파라미터입니다.

```python
replay_service.replay_on_circuit_close(
    service_name="toss_payment",
    max_items=10,
    service_failure_type_map={"toss_payment": ["PG_TIMEOUT"]},  # 필수!
)
```

---

## 완료 기준

- [ ] 모든 `@pytest.mark.django_db` 제거 또는 skip 처리
- [ ] `pytest tests/self_healing/integration/ -n6 --no-cov` 성공
- [ ] DB 없이 테스트 실행 가능
- [ ] 모든 테스트 1초 이내 완료

---

*Last Updated: 2025-12-19*
