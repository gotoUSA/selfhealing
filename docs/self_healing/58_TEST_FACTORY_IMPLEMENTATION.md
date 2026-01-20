# 58. Test Factory Implementation Guide

> **의존**: [57_TEST_REFACTORING_PLAN.md](57_TEST_REFACTORING_PLAN.md)  
> **목적**: Factory Pattern 구현 상세 가이드  
> **상태**: ✅ **구현 완료** (2026-01-20)

---

## 1. 디렉토리 구조

### 1.1 계획 vs 실제 구현

**계획된 구조:**
```
packages/selfhealing-python/tests/factories/
├── __init__.py           # 메인 Factory (TestDataFactory)
├── repositories.py       # InMemory Repository 모음
├── redis.py              # MockRedisClient (통합본)
├── builders.py           # 복잡한 객체 Builder (선택사항)
└── constants.py          # 테스트 상수 (선택사항)
```

**실제 구현된 구조:**
```
packages/selfhealing-python/tests/factories/
├── __init__.py           # 모듈 export (15개 심볼)
├── data_factory.py       # TestDataFactory, MockCircuitBreakerStateData, DefaultValues
├── redis.py              # MockRedisClient, MockPipeline, MockDistributedLock
├── repositories.py       # InMemoryCircuitBreakerRepository, InMemoryRateLimitTracker, InMemoryDLQRepository
└── time_helpers.py       # freeze_time, mock_sleep, MockSleep, get_fixed_datetime, make_datetime_range
```

### 1.2 변경 사항

| 계획 | 실제 | 사유 |
|------|------|------|
| `constants.py` | `data_factory.py`의 `DefaultValues` 클래스 | 상수와 Factory를 한 파일에서 관리하는 것이 더 편리 |
| `builders.py` | 미구현 | 현재 복잡한 객체 빌더 필요 없음, 향후 필요시 추가 |
| - | `time_helpers.py` 추가 | Phase 4-5에서 시간 관련 테스트 유틸리티 필요 |

### 1.3 전체 워크스페이스 구조

```
packages/selfhealing-python/tests/
├── factories/
│   ├── __init__.py           # 모듈 export
│   ├── data_factory.py       # TestDataFactory + DefaultValues
│   ├── redis.py              # MockRedisClient
│   ├── repositories.py       # InMemory Repositories
│   └── time_helpers.py       # Time utilities
├── conftest.py               # 글로벌 fixture
└── ... (기존 테스트 파일들)
```

---

## 2. Factory 구현

### 2.1 TestDataFactory (`__init__.py`)

```python
"""
Central Test Data Factory.

모든 테스트 데이터 생성을 한 곳에서 관리.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from unittest.mock import Mock
import itertools


class TestDataFactory:
    """중앙 집중식 테스트 데이터 Factory."""
    
    _id_counter = itertools.count(1)
    
    @classmethod
    def reset(cls):
        """ID 카운터 리셋 (테스트 격리용)."""
        cls._id_counter = itertools.count(1)
    
    @classmethod
    def next_id(cls) -> int:
        """유니크 ID 생성."""
        return next(cls._id_counter)
    
    # =========================================================================
    # CircuitBreaker 관련
    # =========================================================================
    
    @classmethod
    def circuit_breaker_state(
        cls,
        service_name: str = None,
        state: str = "closed",
        failure_count: int = 0,
        success_count: int = 0,
        last_failure_time: datetime = None,
        last_success_time: datetime = None,
        opened_at: datetime = None,
        opened_by_id: int = None,
        opened_reason: str = "",
        manually_controlled: bool = False,
    ) -> "CircuitBreakerStateData":
        """CircuitBreakerStateData 생성."""
        from selfhealing.core.types import CircuitBreakerStateData
        
        return CircuitBreakerStateData(
            service_name=service_name or f"test_service_{cls.next_id()}",
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            last_failure_time=last_failure_time,
            last_success_time=last_success_time,
            opened_at=opened_at,
            opened_by_id=opened_by_id,
            opened_reason=opened_reason,
            manually_controlled=manually_controlled,
        )
    
    @classmethod
    def circuit_breaker_config(
        cls,
        enabled: bool = True,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        timeout_seconds: int = 60,
        half_open_max_calls: int = 3,
    ) -> "CircuitBreakerConfig":
        """CircuitBreakerConfig 생성."""
        from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig
        
        return CircuitBreakerConfig(
            enabled=enabled,
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout_seconds=timeout_seconds,
            half_open_max_calls=half_open_max_calls,
        )
    
    # =========================================================================
    # DLQ / FailedOperation 관련
    # =========================================================================
    
    @classmethod
    def failed_operation(
        cls,
        id: int = None,
        domain: str = "payment",
        failure_type: str = "PG_TIMEOUT",
        status: str = "pending",
        retry_count: int = 0,
        max_retries: int = 3,
        error_message: str = "Test error message",
        error_code: str = "ERR_TEST",
        entity_type: str = "Order",
        entity_id: str = None,
        snapshot_data: Dict[str, Any] = None,
        request_data: Dict[str, Any] = None,
        response_data: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
        created_at: datetime = None,
        resolved_at: datetime = None,
        **kwargs,
    ) -> "FailedOperationData":
        """FailedOperationData 생성."""
        from selfhealing.interfaces import FailedOperationData
        
        _id = id or cls.next_id()
        
        return FailedOperationData(
            id=_id,
            domain=domain,
            failure_type=failure_type,
            status=status,
            retry_count=retry_count,
            max_retries=max_retries,
            error_message=error_message,
            error_code=error_code,
            entity_type=entity_type,
            entity_id=entity_id or f"entity_{_id}",
            snapshot_data=snapshot_data or {},
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
            created_at=created_at or datetime.now(timezone.utc),
            resolved_at=resolved_at,
            **kwargs,
        )
    
    @classmethod
    def failed_operation_list(
        cls,
        count: int = 5,
        domain: str = "payment",
        status: str = "pending",
        **kwargs,
    ) -> List["FailedOperationData"]:
        """여러 FailedOperationData 생성."""
        return [
            cls.failed_operation(domain=domain, status=status, **kwargs)
            for _ in range(count)
        ]
    
    # =========================================================================
    # Mock 버전 (spec 포함)
    # =========================================================================
    
    @classmethod
    def mock_failed_operation(cls, **kwargs) -> Mock:
        """Mock 버전 FailedOperationData (spec 포함)."""
        from selfhealing.interfaces import FailedOperationData
        
        data = cls.failed_operation(**kwargs)
        mock = Mock(spec=FailedOperationData)
        
        for attr in [
            'id', 'domain', 'failure_type', 'status', 'retry_count',
            'max_retries', 'error_message', 'error_code', 'entity_type',
            'entity_id', 'snapshot_data', 'request_data', 'response_data',
            'metadata', 'created_at', 'resolved_at',
        ]:
            setattr(mock, attr, getattr(data, attr, None))
        
        return mock
    
    @classmethod
    def mock_circuit_breaker_state(cls, **kwargs) -> Mock:
        """Mock 버전 CircuitBreakerStateData."""
        from selfhealing.core.types import CircuitBreakerStateData
        
        data = cls.circuit_breaker_state(**kwargs)
        mock = Mock(spec=CircuitBreakerStateData)
        
        for attr in [
            'service_name', 'state', 'failure_count', 'success_count',
            'last_failure_time', 'last_success_time', 'opened_at',
            'opened_by_id', 'opened_reason', 'manually_controlled',
        ]:
            setattr(mock, attr, getattr(data, attr, None))
        
        return mock
    
    # =========================================================================
    # Audit 관련
    # =========================================================================
    
    @classmethod
    def audit_entry(
        cls,
        sequence: int = None,
        event_type: str = "CONFIG_CHANGE",
        actor_id: str = "system",
        payload: Dict[str, Any] = None,
        timestamp: datetime = None,
        previous_hash: str = "GENESIS",
        current_hash: str = None,
    ) -> Dict[str, Any]:
        """Audit log entry 생성."""
        import hashlib
        import json
        
        _seq = sequence or cls.next_id()
        _ts = timestamp or datetime.now(timezone.utc)
        _payload = payload or {"change": "test"}
        
        entry = {
            "sequence": _seq,
            "event_type": event_type,
            "actor_id": actor_id,
            "payload": _payload,
            "timestamp": _ts.isoformat(),
            "integrity": {
                "sequence": _seq,
                "previous_hash": previous_hash,
            }
        }
        
        if current_hash:
            entry["integrity"]["current_hash"] = current_hash
        else:
            # 간단한 해시 생성
            content = json.dumps(entry, sort_keys=True, default=str)
            entry["integrity"]["current_hash"] = hashlib.sha256(
                content.encode()
            ).hexdigest()[:16]
        
        return entry
    
    # =========================================================================
    # User / Actor 관련
    # =========================================================================
    
    @classmethod
    def mock_user(
        cls,
        id: int = None,
        username: str = None,
        is_staff: bool = False,
        is_superuser: bool = False,
    ) -> Mock:
        """Mock Django User."""
        _id = id or cls.next_id()
        mock = Mock()
        mock.id = _id
        mock.pk = _id
        mock.username = username or f"user_{_id}"
        mock.is_staff = is_staff
        mock.is_superuser = is_superuser
        mock.is_authenticated = True
        return mock
    
    @classmethod
    def admin_user(cls, **kwargs) -> Mock:
        """Admin 권한 Mock User."""
        return cls.mock_user(is_staff=True, is_superuser=True, **kwargs)


# Convenience aliases
factory = TestDataFactory
F = TestDataFactory  # 짧은 별칭
```

---

### 2.2 InMemory Repositories (`repositories.py`)

```python
"""
InMemory Repository 구현들.

테스트에서 DB 없이 사용 가능한 Repository.
"""

from typing import Dict, Optional, List
from datetime import datetime, timezone


class InMemoryCircuitBreakerRepository:
    """테스트용 InMemory CB Repository."""
    
    def __init__(self):
        self._states: Dict[str, any] = {}
    
    def get_or_create(self, service_name: str):
        """Get or create state."""
        if service_name not in self._states:
            from tests.factories import TestDataFactory
            self._states[service_name] = TestDataFactory.circuit_breaker_state(
                service_name=service_name
            )
        return self._states[service_name]
    
    def get_state(self, service_name: str):
        """Get state by service name."""
        return self._states.get(service_name)
    
    def increment_failure(self, service_name: str) -> int:
        """Increment failure count."""
        state = self.get_or_create(service_name)
        state.failure_count += 1
        state.last_failure_time = datetime.now(timezone.utc)
        return state.failure_count
    
    def increment_success(self, service_name: str) -> int:
        """Increment success count."""
        state = self.get_or_create(service_name)
        state.success_count += 1
        state.last_success_time = datetime.now(timezone.utc)
        return state.success_count
    
    def atomic_force_open(
        self,
        service_name: str,
        reason: str,
        controlled_by_id: int,
        ttl_minutes: int = 60,
    ):
        """Force open circuit."""
        state = self.get_or_create(service_name)
        previous = state.state
        state.state = "open"
        state.opened_reason = reason
        state.opened_by_id = controlled_by_id
        state.opened_at = datetime.now(timezone.utc)
        state.manually_controlled = True
        return (True, previous, "open")
    
    def atomic_force_close(
        self,
        service_name: str,
        reason: str,
        controlled_by_id: int,
    ):
        """Force close circuit."""
        state = self.get_or_create(service_name)
        previous = state.state
        state.state = "closed"
        state.manually_controlled = False
        return (True, previous, "closed")
    
    def reset(self):
        """Reset all states."""
        self._states.clear()


class InMemoryDLQRepository:
    """테스트용 InMemory DLQ Repository."""
    
    def __init__(self):
        self._entries: Dict[int, any] = {}
        self._id_counter = 1
    
    def create(self, **kwargs):
        """Create new entry."""
        from tests.factories import TestDataFactory
        entry = TestDataFactory.failed_operation(id=self._id_counter, **kwargs)
        self._entries[entry.id] = entry
        self._id_counter += 1
        return entry
    
    def get_by_id(self, id: int):
        """Get entry by ID."""
        return self._entries.get(id)
    
    def get_pending_by_domain(self, domain: str) -> List:
        """Get pending entries by domain."""
        return [
            e for e in self._entries.values()
            if e.domain == domain and e.status == "pending"
        ]
    
    def increment_retry_count(self, id: int) -> bool:
        """Increment retry count."""
        entry = self.get_by_id(id)
        if entry:
            entry.retry_count += 1
            return True
        return False
    
    def mark_as_resolved(
        self,
        id: int,
        resolution_type: str = "manual",
        resolution_note: str = "",
    ) -> bool:
        """Mark entry as resolved."""
        entry = self.get_by_id(id)
        if entry:
            entry.status = "resolved"
            entry.resolved_at = datetime.now(timezone.utc)
            return True
        return False
    
    def update_status(
        self,
        id: int,
        status: str,
        resolution_type: str = None,
        resolution_note: str = None,
        resolved_by_id: int = None,
    ) -> bool:
        """Update entry status."""
        entry = self.get_by_id(id)
        if entry:
            entry.status = status
            if status == "resolved":
                entry.resolved_at = datetime.now(timezone.utc)
            return True
        return False
    
    def reset(self):
        """Reset all entries."""
        self._entries.clear()
        self._id_counter = 1
```

---

### 2.3 MockRedisClient 통합 (`redis.py`)

```python
"""
Unified MockRedisClient.

모든 테스트에서 사용하는 단일 Mock Redis 구현.
"""

import threading
from typing import Any, Dict, List, Optional
import fnmatch


class MockRedisClient:
    """
    통합 Mock Redis Client.
    
    기존 위치:
    - tests/unit/audit/hash_chain_core/conftest.py
    - tests/unit/audit/graceful_degradation/conftest.py
    
    이제 이 단일 구현 사용.
    """
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
    
    def set_should_fail(self, should_fail: bool) -> None:
        """Set failure mode for testing error scenarios."""
        self._should_fail = should_fail
    
    def _check_fail(self):
        """Raise if failure mode is enabled."""
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
    
    # =========================================================================
    # String Operations
    # =========================================================================
    
    def get(self, key: str) -> Optional[bytes]:
        self._check_fail()
        value = self._data.get(key)
        if value is not None:
            return str(value).encode() if not isinstance(value, bytes) else value
        return None
    
    def set(
        self,
        key: str,
        value: Any,
        nx: bool = False,
        ex: int = None,
        px: int = None,
    ) -> bool:
        self._check_fail()
        with self._lock:
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True
    
    def delete(self, *keys: str) -> int:
        self._check_fail()
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
            if key in self._hashes:
                del self._hashes[key]
                count += 1
        return count
    
    def keys(self, pattern: str) -> List[bytes]:
        self._check_fail()
        return [k.encode() for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]
    
    def incr(self, key: str) -> int:
        self._check_fail()
        with self._lock:
            current = int(self._data.get(key, 0))
            new_value = current + 1
            self._data[key] = new_value
            return new_value
    
    def expire(self, key: str, seconds: int) -> int:
        self._check_fail()
        return 1 if key in self._data or key in self._hashes else 0
    
    # =========================================================================
    # Hash Operations
    # =========================================================================
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        self._check_fail()
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        if value is not None:
            return str(value).encode()
        return None
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs) -> int:
        self._check_fail()
        if mapping is None:
            mapping = kwargs
        with self._lock:
            if key not in self._hashes:
                self._hashes[key] = {}
            self._hashes[key].update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        self._check_fail()
        hash_data = self._hashes.get(key, {})
        return {k.encode(): str(v).encode() for k, v in hash_data.items()}
    
    def hdel(self, key: str, *fields: str) -> int:
        self._check_fail()
        if key not in self._hashes:
            return 0
        count = 0
        for field in fields:
            if field in self._hashes[key]:
                del self._hashes[key][field]
                count += 1
        return count
    
    # =========================================================================
    # List Operations
    # =========================================================================
    
    def lpush(self, key: str, *values: str) -> int:
        self._check_fail()
        with self._lock:
            if key not in self._lists:
                self._lists[key] = []
            for v in values:
                self._lists[key].insert(0, str(v))
            return len(self._lists[key])
    
    def rpush(self, key: str, *values: str) -> int:
        self._check_fail()
        with self._lock:
            if key not in self._lists:
                self._lists[key] = []
            for v in values:
                self._lists[key].append(str(v))
            return len(self._lists[key])
    
    def lrange(self, key: str, start: int, end: int) -> List[bytes]:
        self._check_fail()
        lst = self._lists.get(key, [])
        if end == -1:
            end = len(lst)
        else:
            end += 1
        return [v.encode() for v in lst[start:end]]
    
    def llen(self, key: str) -> int:
        self._check_fail()
        return len(self._lists.get(key, []))
    
    # =========================================================================
    # Utility
    # =========================================================================
    
    def flushdb(self) -> bool:
        """Clear all data."""
        self._data.clear()
        self._hashes.clear()
        self._lists.clear()
        return True
    
    def ping(self) -> bool:
        self._check_fail()
        return True
```

---

### 2.4 상수 정의 (`constants.py`)

```python
"""
테스트 상수 정의.

하드코딩 제거를 위한 중앙 집중식 상수 관리.
"""

# =========================================================================
# Domain Names
# =========================================================================

class Domains:
    PAYMENT = "payment"
    ORDER = "order"
    POINT = "point"
    SHIPPING = "shipping"
    NOTIFICATION = "notification"
    WEBHOOK = "webhook"


# =========================================================================
# Service Names
# =========================================================================

class Services:
    PAYMENT_GATEWAY = "payment-gateway"
    EXTERNAL_API = "external-api"
    TOSS_PAYMENTS = "toss-payments"
    NOTIFICATION = "notification-service"
    DEFAULT = "test-service"


# =========================================================================
# Failure Types
# =========================================================================

class FailureTypes:
    PG_TIMEOUT = "PG_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# =========================================================================
# Status Values
# =========================================================================

class Status:
    PENDING = "pending"
    RESOLVED = "resolved"
    ARCHIVED = "archived"
    FAILED = "failed"


class CircuitState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
```

---

## 3. 사용 예제

### 3.1 Before (현재 방식)

```python
# test_entry_operations.py (현재)
def make_mock_entry(
    id: int = 1,
    domain: str = "payment",
    failure_type: str = "PG_TIMEOUT",
    status: str = "pending",
    retry_count: int = 0,
    max_retries: int = 3,
) -> Mock:
    entry = Mock(spec=FailedOperationData)
    entry.id = id
    entry.domain = domain
    entry.failure_type = failure_type
    entry.status = status
    entry.retry_count = retry_count
    entry.max_retries = max_retries
    entry.created_at = datetime.now(timezone.utc)
    entry.resolved_at = None
    entry.error_code = "TIMEOUT"
    entry.error_message = "Connection timed out"
    entry.snapshot_data = {"order_id": "order-123"}
    entry.request_data = {"method": "POST"}
    entry.response_data = {"status_code": 500}
    entry.metadata = {}
    return entry


class TestRetryEntry:
    def test_retry_entry_success(self):
        mock_entry = make_mock_entry(id=1, status="pending", retry_count=1)
        # ...
```

### 3.2 After (Factory 사용)

```python
# test_entry_operations.py (개선)
from tests.factories import TestDataFactory as F
from tests.factories.constants import Domains, FailureTypes, Status


class TestRetryEntry:
    def test_retry_entry_success(self):
        mock_entry = F.mock_failed_operation(
            status=Status.PENDING,
            retry_count=1,
        )
        # ...
    
    def test_retry_entry_payment_timeout(self):
        mock_entry = F.mock_failed_operation(
            domain=Domains.PAYMENT,
            failure_type=FailureTypes.PG_TIMEOUT,
        )
        # ...
```

### 3.3 Fixture 통합

```python
# conftest.py (개선)
import pytest
from tests.factories import TestDataFactory
from tests.factories.repositories import (
    InMemoryCircuitBreakerRepository,
    InMemoryDLQRepository,
)


@pytest.fixture(autouse=True)
def reset_factory():
    """각 테스트 전 Factory ID 카운터 리셋."""
    TestDataFactory.reset()
    yield


@pytest.fixture
def cb_repository():
    """InMemory CB Repository."""
    repo = InMemoryCircuitBreakerRepository()
    yield repo
    repo.reset()


@pytest.fixture
def dlq_repository():
    """InMemory DLQ Repository."""
    repo = InMemoryDLQRepository()
    yield repo
    repo.reset()


@pytest.fixture
def factory():
    """TestDataFactory 접근용."""
    return TestDataFactory
```

---

## 4. 마이그레이션 가이드

### Step 1: Factory import 추가

```python
# 파일 상단에 추가
from tests.factories import TestDataFactory as F
from tests.factories.constants import Domains, Status
```

### Step 2: 로컬 Mock 함수 제거

```python
# 제거 대상
def make_mock_entry(...):  # 삭제
    ...

@dataclass
class MockCircuitBreakerStateData:  # 삭제
    ...
```

### Step 3: Factory 메서드로 교체

```python
# Before
mock_entry = make_mock_entry(id=1, domain="payment")

# After
mock_entry = F.mock_failed_operation(domain=Domains.PAYMENT)
```

---

## 5. 구현 현황 (2026-01-20)

### 5.1 완료된 작업

| 항목 | 상태 | 설명 |
|------|------|------|
| `data_factory.py` | ✅ 완료 | `TestDataFactory`, `MockCircuitBreakerStateData`, `DefaultValues` |
| `redis.py` | ✅ 완료 | `MockRedisClient`, `MockPipeline`, `MockDistributedLock` |
| `repositories.py` | ✅ 완료 | `InMemoryCircuitBreakerRepository`, `InMemoryRateLimitTracker`, `InMemoryDLQRepository` |
| `time_helpers.py` | ✅ 완료 | `freeze_time`, `mock_sleep`, `MockSleep`, `get_fixed_datetime`, `make_datetime_range` |
| `__init__.py` | ✅ 완료 | 15개 심볼 export |

### 5.2 리팩토링된 테스트 파일

| 파일 | 적용된 Factory |
|------|----------------|
| `tests/services/circuit_breaker/test_service.py` | `MockCircuitBreakerStateData`, `InMemoryCircuitBreakerRepository` |
| `tests/services/circuit_breaker/test_protection.py` | `MockCircuitBreakerStateData`, `InMemoryCircuitBreakerRepository`, `InMemoryRateLimitTracker` |
| `tests/services/circuit_breaker/test_manual_control.py` | `MockCircuitBreakerStateData`, `InMemoryCircuitBreakerRepository` |
| `tests/services/circuit_breaker/test_convenience.py` | `MockCircuitBreakerStateData`, `InMemoryCircuitBreakerRepository` |
| `tests/services/dlq/test_entry_operations.py` | `TestDataFactory.mock_failed_operation` |
| `tests/services/dlq/test_list_operations.py` | `TestDataFactory.mock_failed_operation` |
| `tests/unit/audit/hash_chain_core/conftest.py` | `MockRedisClient` (factories에서 import) |
| `tests/unit/audit/graceful_degradation/conftest.py` | `MockRedisClient`, `MockDistributedLock` (factories에서 import) |

### 5.3 테스트 결과

```
Audit (hash_chain_core + graceful_degradation): 80 passed
```

### 5.4 미구현 항목

| 항목 | 사유 | 향후 계획 |
|------|------|----------|
| `builders.py` | 현재 복잡한 객체 빌더 필요 없음 | 필요시 추가 |
| `constants.py` (별도 파일) | `DefaultValues`로 충분 | 필요시 분리 |

### 5.5 사용 예시

```python
# 기본 사용
from tests.factories import TestDataFactory, MockRedisClient
from tests.factories import InMemoryCircuitBreakerRepository

# CB 상태 생성
state = TestDataFactory.circuit_breaker_state(service_name="payment-api")

# Mock 객체 생성
mock_entry = TestDataFactory.mock_failed_operation(domain="order", status="pending")

# Redis Mock
redis = MockRedisClient()
redis.set("key", "value")

# Repository
repo = InMemoryCircuitBreakerRepository()
state = repo.get_or_create("test_service")

# Time Helpers
from tests.factories import freeze_time, mock_sleep

with freeze_time("2026-01-20 12:00:00"):
    # datetime.now()가 고정됨
    pass

with mock_sleep() as sleep_mock:
    time.sleep(10)  # 즉시 반환
    assert sleep_mock.total_slept == 10
```
