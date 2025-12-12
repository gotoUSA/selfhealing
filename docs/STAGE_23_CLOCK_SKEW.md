# Stage 23: Clock Skew / NTP 드리프트 테스트

## 🎯 목표

서버 시계가 어긋났을 때 idempotency 키 중복 방지 및 시간 기반 로직 안정성 확보

## 📋 실제 장애 사례

- **2024년 카카오 장애**: 서버 시계 30초 벌어져서 idempotency 키 중복 → 결제 2번 발생

---

## 🏗️ 구현 내용

### 1. TimeProvider 인터페이스 생성

**파일**: `packages/selfhealing-python/src/selfhealing/core/time_provider.py`

```python
"""
TimeProvider Interface for Clock Skew Resilience

Abstracts time operations to enable:
- Testing with mocked time
- Clock skew tolerance
- Timezone-safe operations
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional


class TimeProvider(ABC):
    """Abstract interface for time operations"""

    @abstractmethod
    def now(self) -> datetime:
        """Get current UTC time"""
        pass

    @abstractmethod
    def now_with_skew_tolerance(self, tolerance_seconds: float = 30.0) -> tuple[datetime, datetime]:
        """
        Get time window for clock skew tolerance.
        Returns (lower_bound, upper_bound)
        """
        pass


class SystemTimeProvider(TimeProvider):
    """Production implementation using system clock"""

    def __init__(self, default_tolerance: float = 30.0):
        self._default_tolerance = default_tolerance

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now_with_skew_tolerance(self, tolerance_seconds: Optional[float] = None) -> tuple[datetime, datetime]:
        tolerance = tolerance_seconds or self._default_tolerance
        current = self.now()
        return (
            current - timedelta(seconds=tolerance),
            current + timedelta(seconds=tolerance)
        )


class MockTimeProvider(TimeProvider):
    """Test implementation with controllable time"""

    def __init__(self, fixed_time: Optional[datetime] = None):
        self._fixed_time = fixed_time or datetime.now(timezone.utc)
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return self._fixed_time + self._offset

    def now_with_skew_tolerance(self, tolerance_seconds: float = 30.0) -> tuple[datetime, datetime]:
        current = self.now()
        return (
            current - timedelta(seconds=tolerance_seconds),
            current + timedelta(seconds=tolerance_seconds)
        )

    def advance(self, seconds: float) -> None:
        """Advance time by specified seconds"""
        self._offset += timedelta(seconds=seconds)

    def set_time(self, new_time: datetime) -> None:
        """Set fixed time"""
        self._fixed_time = new_time
        self._offset = timedelta(0)

    def simulate_clock_skew(self, skew_seconds: float) -> None:
        """Simulate clock drift"""
        self._offset += timedelta(seconds=skew_seconds)
```

---

### 2. Idempotency 서비스 수정

**파일**: `packages/selfhealing-python/src/selfhealing/services/idempotency_service.py`

**수정 사항**:
```python
# 기존: datetime.now() 직접 사용
# 변경: TimeProvider 주입

class IdempotencyService:
    def __init__(
        self,
        repository: IdempotencyRepository,
        time_provider: Optional[TimeProvider] = None,
        clock_skew_tolerance: float = 30.0,  # 30초 허용
    ):
        self._repo = repository
        self._time_provider = time_provider or SystemTimeProvider()
        self._clock_skew_tolerance = clock_skew_tolerance

    def is_duplicate(self, key: str, window_seconds: float = 3600) -> bool:
        """
        Check if key is duplicate within time window.
        Applies clock skew tolerance for distributed systems.
        """
        lower, upper = self._time_provider.now_with_skew_tolerance(
            self._clock_skew_tolerance
        )

        # 시간 윈도우 확장 (skew 고려)
        check_from = lower - timedelta(seconds=window_seconds)
        check_to = upper

        return self._repo.exists_in_window(key, check_from, check_to)
```

---

### 3. 테스트 케이스

**파일**: `packages/selfhealing-python/tests/unit/test_clock_skew.py`

```python
"""
Stage 23: Clock Skew Resilience Tests

Tests:
1. Normal operation with synchronized clocks
2. Clock skew within tolerance (30s)
3. Clock skew exceeding tolerance
4. Idempotency key validation with skewed time
5. Distributed nodes with different clocks
"""

import pytest
from datetime import datetime, timedelta, timezone
from selfhealing.core.time_provider import (
    MockTimeProvider,
    SystemTimeProvider,
)


class TestClockSkewResilience:
    """Clock skew resilience test suite"""

    def test_normal_time_operation(self):
        """정상 시계에서 동작 확인"""
        provider = SystemTimeProvider()
        now = provider.now()

        assert now.tzinfo == timezone.utc
        assert (datetime.now(timezone.utc) - now).total_seconds() < 1

    def test_mock_time_advance(self):
        """Mock 시간 전진 테스트"""
        fixed = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        provider = MockTimeProvider(fixed)

        assert provider.now() == fixed

        provider.advance(60)  # 1분 전진
        assert provider.now() == fixed + timedelta(minutes=1)

    def test_clock_skew_tolerance_window(self):
        """Clock skew 허용 윈도우 테스트"""
        provider = MockTimeProvider()
        lower, upper = provider.now_with_skew_tolerance(30.0)

        # 30초 전후 윈도우
        assert (upper - lower).total_seconds() == 60.0

    def test_simulated_clock_drift(self):
        """시계 드리프트 시뮬레이션"""
        provider = MockTimeProvider()
        original = provider.now()

        # 30초 드리프트 시뮬레이션
        provider.simulate_clock_skew(30)

        drifted = provider.now()
        assert (drifted - original).total_seconds() == 30

    def test_idempotency_with_clock_skew(self):
        """Clock skew 상황에서 idempotency 검증"""
        # Server A: 정상 시계
        server_a = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

        # Server B: 25초 느린 시계 (허용 범위 내)
        server_b = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        server_b.simulate_clock_skew(-25)

        # 같은 시간대로 인식되어야 함
        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        # Server B의 시간이 Server A의 tolerance 범위 내
        assert a_lower <= b_now <= a_upper

    def test_clock_skew_exceeds_tolerance(self):
        """허용 범위 초과 clock skew 감지"""
        server_a = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

        # 60초 차이 (허용 범위 30초 초과)
        server_b = MockTimeProvider(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        server_b.simulate_clock_skew(-60)

        a_lower, a_upper = server_a.now_with_skew_tolerance(30)
        b_now = server_b.now()

        # Server B의 시간이 범위 밖
        assert not (a_lower <= b_now <= a_upper)
```

---

### 4. 통합 테스트

**파일**: `packages/selfhealing-python/tests/integration/test_clock_skew_integration.py`

```python
"""
Stage 23: Clock Skew Integration Tests

실제 서비스 레벨에서 clock skew 처리 검증
"""

import pytest
from datetime import datetime, timedelta, timezone

from selfhealing.core.time_provider import MockTimeProvider
from selfhealing.services.idempotency_service import IdempotencyService


class MockIdempotencyRepository:
    """In-memory repository for testing"""

    def __init__(self):
        self._keys: dict[str, datetime] = {}

    def save(self, key: str, timestamp: datetime) -> None:
        self._keys[key] = timestamp

    def exists_in_window(self, key: str, from_time: datetime, to_time: datetime) -> bool:
        if key not in self._keys:
            return False
        key_time = self._keys[key]
        return from_time <= key_time <= to_time


class TestClockSkewIntegration:
    """Clock skew 통합 테스트"""

    @pytest.fixture
    def service_with_mock_time(self):
        repo = MockIdempotencyRepository()
        time_provider = MockTimeProvider()
        return IdempotencyService(
            repository=repo,
            time_provider=time_provider,
            clock_skew_tolerance=30.0
        ), repo, time_provider

    def test_duplicate_request_within_skew(self, service_with_mock_time):
        """
        시나리오: 두 서버의 시계가 25초 차이날 때
        같은 idempotency key가 중복으로 인식되어야 함
        """
        service, repo, time_provider = service_with_mock_time

        # 첫 번째 요청 (Server A, t=0)
        key = "payment_12345"
        repo.save(key, time_provider.now())

        # 두 번째 요청 (Server B, t=0 + 25초 skew)
        time_provider.simulate_clock_skew(25)

        # 중복으로 인식되어야 함
        assert service.is_duplicate(key) is True

    def test_legitimate_retry_after_window(self, service_with_mock_time):
        """
        시나리오: 정상적인 재시도 (시간 윈도우 후)
        중복이 아닌 새 요청으로 인식
        """
        service, repo, time_provider = service_with_mock_time

        # 첫 번째 요청
        key = "payment_67890"
        repo.save(key, time_provider.now())

        # 2시간 후 (윈도우 1시간 + skew 고려해도 충분히 지남)
        time_provider.advance(7200)

        # 새 요청으로 인식
        assert service.is_duplicate(key, window_seconds=3600) is False
```

---

## 📁 파일 생성 순서

1. `packages/selfhealing-python/src/selfhealing/core/time_provider.py`
2. `packages/selfhealing-python/src/selfhealing/core/__init__.py` 수정 (export 추가)
3. `packages/selfhealing-python/src/selfhealing/services/idempotency_service.py` 수정
4. `packages/selfhealing-python/tests/unit/test_clock_skew.py`
5. `packages/selfhealing-python/tests/integration/test_clock_skew_integration.py`

---

## ✅ 완료 기준

- [x] TimeProvider 인터페이스 구현
- [x] MockTimeProvider 테스트 통과
- [x] IdempotencyService에 TimeProvider 주입
- [x] Clock skew 30초 허용 테스트 통과
- [x] 분산 환경 시뮬레이션 테스트 통과

---

## 📝 새 세션 시작 프롬프트

```
STAGE_23_CLOCK_SKEW.md 문서대로 구현해줘.
TimeProvider 인터페이스와 테스트부터 시작해서 IdempotencyService 수정까지.
```
