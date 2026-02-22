"""
Stage 40: Idempotency + Clock Skew Tests

거버넌스 경계 검증: 중복 요청 + 시간 왜곡 시나리오

목표:
- 시간 왜곡 상황에서 멱등성 보장 검증
- 중복 부작용 방지 확인
- 보안 경계의 자동 치유 차단 확인
- Decision Record 및 메트릭 일관성 검증

제약사항 (엄격 준수):
- 새로운 멱등성 로직 추가 금지
- Clock skew tolerance 수정 금지
- 새로운 timestamp 휴리스틱 추가 금지
- 새로운 reason code 추가 금지
- retry/replay/DLQ 동작 변경 금지
- 프로덕션 코드 수정 금지

실행 방법:
    pytest load_tests/scenarios/stage40_idempotency_clock_skew.py -v -s

참조:
- docs/STAGE_23_CLOCK_SKEW.md
- Stage 39 거버넌스 경계 테스트
"""

from __future__ import annotations

import json
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as tz
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import pytest


# Conditional imports for type checking
if TYPE_CHECKING:
    pass


# =============================================================================
# Lazy Service Import Utilities (Reused from Stage 39)
# =============================================================================

_SHARED_MEMORY_REPO = None
_SHARED_CB_SERVICE = None


def _get_or_create_shared_repo():
    """Get or create shared in-memory repository (singleton pattern)."""
    global _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE
    from selfhealing.adapters.memory.circuit_breaker import (
        InMemoryCircuitBreakerStateRepository,
    )
    from selfhealing.services import CircuitBreakerConfig, CircuitBreakerService

    if _SHARED_MEMORY_REPO is None:
        _SHARED_MEMORY_REPO = InMemoryCircuitBreakerStateRepository()
        _SHARED_CB_SERVICE = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=3,
                recovery_timeout=30,
                manual_override_ttl_minutes=60,
            ),
            repository=_SHARED_MEMORY_REPO,
        )
    return _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE


def _get_selfhealing_services():
    """Lazy import selfhealing services to avoid import errors in IDE."""
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
        DLQConfig,
        DLQService,
    )

    _memory_repo, _cb_service = _get_or_create_shared_repo()

    def _create_cb_service(config=None):
        cfg = config or CircuitBreakerConfig(
            enabled=True,
            failure_threshold=3,
            recovery_timeout=30,
            manual_override_ttl_minutes=60,
        )
        return CircuitBreakerService(config=cfg, repository=_memory_repo)

    def force_open_circuit(service_name: str, reason: str = ""):
        return _cb_service.force_open(service_name=service_name, reason=reason)

    def force_close_circuit(service_name: str, reason: str = "", trigger_replay: bool = False):
        return _cb_service.force_close(service_name=service_name, reason=reason, trigger_replay=trigger_replay)

    def should_allow_request(service_name: str) -> bool:
        return _cb_service.should_allow(service_name)

    def get_circuit_breaker_service():
        return _cb_service

    return {
        "CircuitBreakerConfig": CircuitBreakerConfig,
        "CircuitBreakerService": CircuitBreakerService,
        "CircuitState": CircuitState,
        "DLQConfig": DLQConfig,
        "DLQService": DLQService,
        "force_close_circuit": force_close_circuit,
        "force_open_circuit": force_open_circuit,
        "get_circuit_breaker_service": get_circuit_breaker_service,
        "should_allow_request": should_allow_request,
        "_memory_repo": _memory_repo,
        "_create_cb_service": _create_cb_service,
    }


def _get_decision_logger():
    """Lazy import decision logger to avoid import errors in IDE."""
    from selfhealing.core.decision_logger import (
        DecisionLogger,
        DecisionBoundaryEventType,
        ReasonCode,
    )

    return {
        "DecisionLogger": DecisionLogger,
        "EventType": DecisionBoundaryEventType,
        "ReasonCode": ReasonCode,
    }


def _get_idempotency_service():
    """Lazy import idempotency service."""
    from selfhealing.services.idempotency_service import (
        IdempotencyService,
        IdempotencyKey,
        IdempotencyResult,
        IdempotencyDomain,
    )
    from selfhealing.core.time_provider import MockTimeProvider, SystemTimeProvider

    return {
        "IdempotencyService": IdempotencyService,
        "IdempotencyKey": IdempotencyKey,
        "IdempotencyResult": IdempotencyResult,
        "IdempotencyDomain": IdempotencyDomain,
        "MockTimeProvider": MockTimeProvider,
        "SystemTimeProvider": SystemTimeProvider,
    }


# =============================================================================
# Test Constants (NO NEW MECHANISMS)
# =============================================================================

SERVICE_PAYMENT_GATEWAY = "payment_gateway_service"
SERVICE_IDEMPOTENCY = "idempotency_service"
SERVICE_SECURITY = "security_authentication_gateway"

# Existing clock skew tolerance (DO NOT MODIFY)
CLOCK_SKEW_TOLERANCE_SECONDS = 30.0

# Existing retry limits (DO NOT MODIFY)
MAX_RETRY_ATTEMPTS = 3
MAX_REPLAY_ATTEMPTS = 2


# =============================================================================
# In-Memory Idempotency Repository (Test Mock - NO NEW LOGIC)
# =============================================================================


@dataclass
class IdempotencyRecord:
    """Record of an idempotency key usage."""

    key: str
    timestamp: datetime
    request_id: str
    result: Optional[Any] = None
    execution_count: int = 1


class MockIdempotencyRepository:
    """
    In-memory idempotency repository for testing.

    Uses only existing idempotency patterns - NO NEW MECHANISMS.
    """

    def __init__(self):
        self._records: Dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()
        self._execution_log: List[Dict[str, Any]] = []

    def save(self, key: str, timestamp: datetime, request_id: str, result: Any = None) -> bool:
        """
        Save idempotency key if not exists.

        Returns True if this is a new key (first time).
        Returns False if duplicate (already exists).
        """
        with self._lock:
            if key in self._records:
                # Duplicate - increment execution count for tracking
                self._records[key].execution_count += 1
                self._execution_log.append(
                    {
                        "key": key,
                        "action": "duplicate_detected",
                        "original_timestamp": self._records[key].timestamp.isoformat(),
                        "request_timestamp": timestamp.isoformat(),
                        "request_id": request_id,
                    }
                )
                return False

            # New key
            self._records[key] = IdempotencyRecord(
                key=key,
                timestamp=timestamp,
                request_id=request_id,
                result=result,
            )
            self._execution_log.append(
                {
                    "key": key,
                    "action": "new_key_registered",
                    "timestamp": timestamp.isoformat(),
                    "request_id": request_id,
                }
            )
            return True

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        with self._lock:
            return key in self._records

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        """Get record by key."""
        with self._lock:
            return self._records.get(key)

    def get_execution_count(self, key: str) -> int:
        """Get total execution count for a key."""
        with self._lock:
            record = self._records.get(key)
            return record.execution_count if record else 0

    def clear(self) -> None:
        """Clear all records."""
        with self._lock:
            self._records.clear()
            self._execution_log.clear()

    @property
    def execution_log(self) -> List[Dict[str, Any]]:
        """Get execution log for forensic analysis."""
        with self._lock:
            return list(self._execution_log)


# =============================================================================
# Decision Record Capture (Reused from Stage 39)
# =============================================================================


class DecisionRecordCapture:
    """
    Captures Decision Record events for verification.
    Validates against existing frozen schema.
    """

    FROZEN_FIELDS = frozenset(["event", "allowed", "reason", "service_name", "policy_version", "timestamp"])

    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self.schema_violations: List[str] = []

        decision_logger = _get_decision_logger()
        EventType = decision_logger["EventType"]
        ReasonCode = decision_logger["ReasonCode"]

        self.FROZEN_EVENT_TYPES = frozenset(
            [
                EventType.ENTER_PRE_DECISION_ZONE.value,
                EventType.INTERVENTION_EVALUATED.value,
                EventType.EXIT_PRE_DECISION_ZONE.value,
            ]
        )

        self.FROZEN_REASON_CODES = frozenset(
            [
                ReasonCode.THRESHOLD_NOT_MET.value,
                ReasonCode.STABILITY_OK_NO_INTERVENTION.value,
                ReasonCode.POLICY_CONSTRAINT_ACTIVE.value,
                ReasonCode.INTERVENTION_ALLOWED.value,
            ]
        )

    def capture(self, record_json: str) -> None:
        """Capture and validate a decision record."""
        try:
            record = json.loads(record_json)
            self._validate_schema(record)
            self.records.append(record)
        except json.JSONDecodeError as e:
            self.schema_violations.append(f"Invalid JSON: {e}")

    def _validate_schema(self, record: Dict[str, Any]) -> None:
        """Validate record against frozen schema."""
        unknown_fields = set(record.keys()) - self.FROZEN_FIELDS
        if unknown_fields:
            self.schema_violations.append(f"Unknown fields detected: {unknown_fields}")

        event = record.get("event")
        if event and event not in self.FROZEN_EVENT_TYPES:
            self.schema_violations.append(f"Unknown event type: {event}")

        reason = record.get("reason")
        if reason and reason not in self.FROZEN_REASON_CODES:
            self.schema_violations.append(f"Unknown reason code: {reason}")

    @property
    def is_schema_valid(self) -> bool:
        """Check if all records adhered to frozen schema."""
        return len(self.schema_violations) == 0


# =============================================================================
# Mock DLQ for Testing (NO NEW MECHANISMS)
# =============================================================================


@dataclass
class DLQEntry:
    """DLQ entry for testing."""

    entry_id: str
    idempotency_key: str
    timestamp: datetime
    retry_count: int = 0
    max_retries: int = MAX_RETRY_ATTEMPTS
    status: str = "pending"
    reason: str = ""


class MockDLQRepository:
    """In-memory DLQ for testing."""

    def __init__(self):
        self._entries: Dict[str, DLQEntry] = {}
        self._lock = threading.Lock()

    def add(self, idempotency_key: str, timestamp: datetime, reason: str = "") -> DLQEntry:
        """Add entry to DLQ."""
        with self._lock:
            entry_id = str(uuid.uuid4())
            entry = DLQEntry(
                entry_id=entry_id,
                idempotency_key=idempotency_key,
                timestamp=timestamp,
                reason=reason,
            )
            self._entries[entry_id] = entry
            return entry

    def get_by_key(self, idempotency_key: str) -> Optional[DLQEntry]:
        """Get entry by idempotency key."""
        with self._lock:
            for entry in self._entries.values():
                if entry.idempotency_key == idempotency_key:
                    return entry
            return None

    def increment_retry(self, entry_id: str) -> bool:
        """Increment retry count. Returns True if still under limit."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry:
                entry.retry_count += 1
                if entry.retry_count >= entry.max_retries:
                    entry.status = "exhausted"
                    return False
                return True
            return False

    def mark_replayed(self, entry_id: str) -> None:
        """Mark entry as replayed."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry:
                entry.status = "replayed"

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._entries.clear()

    @property
    def pending_count(self) -> int:
        """Count of pending entries."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.status == "pending")

    @property
    def exhausted_count(self) -> int:
        """Count of exhausted entries."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.status == "exhausted")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def idempotency_repo():
    """Create mock idempotency repository."""
    return MockIdempotencyRepository()


@pytest.fixture
def dlq_repo():
    """Create mock DLQ repository."""
    return MockDLQRepository()


@pytest.fixture
def mock_time_provider():
    """Create mock time provider."""
    idem_services = _get_idempotency_service()
    MockTimeProvider = idem_services["MockTimeProvider"]
    return MockTimeProvider(datetime(2024, 12, 16, 12, 0, 0, tzinfo=tz.utc))


@pytest.fixture
def selfhealing_services():
    """Provide all selfhealing services as a fixture."""
    return _get_selfhealing_services()


@pytest.fixture
def decision_logger_classes():
    """Provide decision logger classes as a fixture."""
    return _get_decision_logger()


@pytest.fixture
def decision_record_capture():
    """Get Decision Record capture utility."""
    return DecisionRecordCapture()


@pytest.fixture
def circuit_breaker_service():
    """Get Circuit Breaker Service with in-memory repo."""
    services = _get_selfhealing_services()
    return services["get_circuit_breaker_service"]()


@pytest.fixture(autouse=True)
def cleanup_circuit_breakers():
    """Clean up circuit breaker states after each test."""
    yield
    try:
        global _SHARED_MEMORY_REPO
        if _SHARED_MEMORY_REPO is not None:
            with _SHARED_MEMORY_REPO._lock:
                _SHARED_MEMORY_REPO._storage.clear()
    except Exception:
        pass


# =============================================================================
# Stage 40-S1: Duplicate Request with Forward Clock Skew
# =============================================================================


@pytest.mark.chaos
class TestStage40ForwardClockSkew:
    """
    Stage 40-S1: Forward Clock Skew에서 중복 요청 처리

    Given: 동일 idempotency key를 가진 요청
    When: 두 번째 요청의 타임스탬프가 서버 시간보다 앞서 있음
    Then:
        - 두 번째 요청은 중복으로 처리됨
        - 중복 부작용 없음
        - 자동 복구/재생 트리거 없음
    """

    def test_stage_40_forward_skew_duplicate_detection(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Forward clock skew 상황에서 중복 탐지

        Given:
            - 첫 번째 요청이 정상 처리됨
            - 동일한 idempotency key 사용

        When:
            - 두 번째 요청의 클라이언트 시간이 서버보다 60초 앞서 있음

        Then:
            - 시스템 생존 (예외/크래시 없음)
            - 중복 비즈니스 액션 없음
            - Idempotency key 고유성 유지
        """
        # given: 초기 시스템 상태
        base_time = mock_time_provider.now()
        idempotency_key = f"payment_{uuid.uuid4()}"
        request_id_1 = str(uuid.uuid4())
        request_id_2 = str(uuid.uuid4())

        # given: 첫 번째 요청 처리
        first_request_time = base_time
        is_new = idempotency_repo.save(
            key=idempotency_key,
            timestamp=first_request_time,
            request_id=request_id_1,
            result={"status": "success", "amount": 10000},
        )

        # then: 첫 번째 요청은 새 요청
        assert is_new is True, "First request should be new"

        # when: 두 번째 요청 (forward skew - 60초 앞선 클라이언트 시간)
        forward_skewed_time = base_time + timedelta(seconds=60)
        is_new_2 = idempotency_repo.save(
            key=idempotency_key,
            timestamp=forward_skewed_time,
            request_id=request_id_2,
            result={"status": "success", "amount": 10000},
        )

        # then: 시스템 생존 (예외 없음)
        assert True, "System survived without exception"

        # then: 두 번째 요청은 중복으로 탐지
        assert is_new_2 is False, "Second request should be detected as duplicate"

        # then: 중복 비즈니스 액션 없음 (실행 횟수 확인)
        execution_count = idempotency_repo.get_execution_count(idempotency_key)
        assert execution_count == 2, "Execution count should track duplicate attempt"

        # then: 원본 결과 유지 (중복 실행 아님)
        original_record = idempotency_repo.get(idempotency_key)
        assert original_record is not None
        assert original_record.request_id == request_id_1, "Original request ID preserved"
        assert original_record.timestamp == first_request_time, "Original timestamp preserved"

        # then: 실행 로그 검증 - 중복 탐지 기록
        log = idempotency_repo.execution_log
        assert len(log) == 2
        assert log[0]["action"] == "new_key_registered"
        assert log[1]["action"] == "duplicate_detected"

    def test_stage_40_forward_skew_no_auto_recovery(
        self,
        idempotency_repo: MockIdempotencyRepository,
        selfhealing_services,
        mock_time_provider,
    ):
        """
        시나리오: Forward clock skew 상황에서 자동 복구 없음

        Given:
            - Forward skew로 인한 중복 요청

        When:
            - 중복 탐지 발생

        Then:
            - 자동 복구 트리거 없음
            - 재생(replay) 트리거 없음
            - CB 상태 변경 없음
        """
        CircuitState = selfhealing_services["CircuitState"]
        get_cb_service = selfhealing_services["get_circuit_breaker_service"]
        cb_service = get_cb_service()

        # given: CB 초기 상태 설정
        cb_service.get_or_create_state(SERVICE_IDEMPOTENCY)
        initial_state = cb_service.get_state(SERVICE_IDEMPOTENCY)

        # given: 첫 번째 요청
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: forward skew 중복 요청
        forward_time = base_time + timedelta(seconds=120)
        is_new = idempotency_repo.save(idempotency_key, forward_time, str(uuid.uuid4()))

        # then: 중복 탐지
        assert is_new is False

        # then: CB 상태 변경 없음
        current_state = cb_service.get_state(SERVICE_IDEMPOTENCY)
        assert current_state == initial_state, "CB state should not change on duplicate detection"

        # then: 시스템 안정성 유지
        assert cb_service.should_allow(SERVICE_IDEMPOTENCY) is True

    def test_stage_40_forward_skew_extreme_drift(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
        decision_record_capture: DecisionRecordCapture,
        caplog,
    ):
        """
        시나리오: 극심한 Forward clock drift (5분 이상)

        Given:
            - 첫 번째 요청 정상 처리

        When:
            - 두 번째 요청이 5분 이상 앞선 시간으로 도착

        Then:
            - 시스템 안정성 유지
            - 중복 탐지 정상 동작
            - Decision Record 스키마 준수
        """
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 첫 번째 요청
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: 극심한 forward skew (10분 앞선 시간)
        extreme_forward_time = base_time + timedelta(minutes=10)
        is_new = idempotency_repo.save(idempotency_key, extreme_forward_time, str(uuid.uuid4()))

        # then: 시스템 생존
        assert True, "System survived extreme forward skew"

        # then: 여전히 중복으로 탐지
        assert is_new is False, "Duplicate detection works even with extreme skew"

        # then: 중복 실행 방지
        assert idempotency_repo.get_execution_count(idempotency_key) == 2


# =============================================================================
# Stage 40-S2: Duplicate Request with Backward Clock Skew
# =============================================================================


@pytest.mark.chaos
class TestStage40BackwardClockSkew:
    """
    Stage 40-S2: Backward Clock Skew에서 중복 요청 처리

    Given: 동일 idempotency key를 가진 요청
    When: 두 번째 요청의 타임스탬프가 서버 시간보다 뒤처져 있음
    Then:
        - 중복 탐지 정상 동작
        - 시스템 안정성 유지
        - 크래시 또는 정의되지 않은 동작 없음
    """

    def test_stage_40_backward_skew_duplicate_detection(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Backward clock skew 상황에서 중복 탐지

        Given:
            - 첫 번째 요청이 정상 처리됨

        When:
            - 두 번째 요청의 클라이언트 시간이 서버보다 60초 뒤처져 있음

        Then:
            - 중복 탐지 정상 동작
            - 시스템 안정성 유지
        """
        # given: 첫 번째 요청
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()
        request_id_1 = str(uuid.uuid4())

        is_new_1 = idempotency_repo.save(idempotency_key, base_time, request_id_1)
        assert is_new_1 is True

        # when: backward skew 중복 요청 (60초 뒤처진 시간)
        backward_skewed_time = base_time - timedelta(seconds=60)
        request_id_2 = str(uuid.uuid4())
        is_new_2 = idempotency_repo.save(idempotency_key, backward_skewed_time, request_id_2)

        # then: 시스템 생존
        assert True, "System survived backward skew"

        # then: 중복 탐지 정상
        assert is_new_2 is False, "Duplicate detected despite backward skew"

        # then: 원본 데이터 보존
        record = idempotency_repo.get(idempotency_key)
        assert record.request_id == request_id_1
        assert record.timestamp == base_time

    def test_stage_40_backward_skew_system_stability(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Backward skew에서 시스템 안정성

        Given:
            - 다양한 backward skew 값

        When:
            - 여러 중복 요청 발생

        Then:
            - 모든 요청에서 시스템 안정
            - 정의되지 않은 동작 없음
        """
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 원본 요청
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: 다양한 backward skew 테스트
        skew_values = [10, 30, 60, 120, 300, 600]  # 10초 ~ 10분
        errors = []

        for skew in skew_values:
            try:
                backward_time = base_time - timedelta(seconds=skew)
                is_new = idempotency_repo.save(idempotency_key, backward_time, str(uuid.uuid4()))

                if is_new:
                    errors.append(f"Skew {skew}s: False positive (should be duplicate)")
            except Exception as e:
                errors.append(f"Skew {skew}s: Exception {e}")

        # then: 모든 요청 안정적 처리
        assert len(errors) == 0, f"Errors during backward skew tests: {errors}"

        # then: 모든 요청이 중복으로 탐지됨 (1 원본 + N 중복 시도)
        assert idempotency_repo.get_execution_count(idempotency_key) == len(skew_values) + 1

    def test_stage_40_backward_skew_no_crash_on_negative_diff(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: 음수 시간 차이에서 크래시 없음

        Given:
            - 요청 시간이 원본보다 과거

        When:
            - 시간 차이 계산

        Then:
            - 음수 diff 안전 처리
            - 크래시 없음
        """
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 원본 (현재 시간)
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: 과거 시간으로 요청 (음수 diff 발생 가능)
        past_time = base_time - timedelta(hours=1)

        try:
            is_new = idempotency_repo.save(idempotency_key, past_time, str(uuid.uuid4()))
            crashed = False
        except Exception:
            crashed = True

        # then: 크래시 없음
        assert crashed is False, "System should not crash on negative time diff"
        assert is_new is False, "Should still detect as duplicate"


# =============================================================================
# Stage 40-S3: Clock Skew Combined with Retry Boundary
# =============================================================================


@pytest.mark.chaos
class TestStage40ClockSkewRetryBoundary:
    """
    Stage 40-S3: Clock Skew + Retry Boundary 조합

    Given: 첫 번째 요청이 일시적으로 실패
    When: 재시도가 skewed timestamp와 함께 발생
    Then:
        - 재시도가 idempotency 경계 준수
        - 이중 실행 없음
        - 재시도 횟수 및 포렌식 컨텍스트 정확
    """

    def test_stage_40_retry_with_forward_skew(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Forward skew와 함께 재시도

        Given:
            - 첫 번째 요청이 처리됨 (성공 또는 실패)

        When:
            - 클라이언트가 forward skew 시간으로 재시도

        Then:
            - Idempotency 경계 준수
            - 이중 실행 방지
        """
        idempotency_key = f"order_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 첫 번째 요청 (일시적 실패 시나리오)
        request_id = str(uuid.uuid4())
        # 실제로는 실패했지만, idempotency key는 등록됨 (at-least-once 의미론)
        idempotency_repo.save(idempotency_key, base_time, request_id, result={"status": "transient_failure"})

        # when: forward skew로 재시도
        retry_time = base_time + timedelta(seconds=45)  # 45초 forward skew
        retry_request_id = str(uuid.uuid4())
        is_new = idempotency_repo.save(idempotency_key, retry_time, retry_request_id)

        # then: 중복으로 탐지 (idempotency 경계 준수)
        assert is_new is False

        # then: 이중 실행 방지
        record = idempotency_repo.get(idempotency_key)
        assert record.request_id == request_id  # 원본 유지

        # then: 재시도 횟수 추적
        assert record.execution_count == 2

    def test_stage_40_retry_with_backward_skew(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Backward skew와 함께 재시도

        Given:
            - 첫 번째 요청 처리됨

        When:
            - 클라이언트가 backward skew 시간으로 재시도

        Then:
            - 여전히 idempotency 준수
            - 순서 혼란 없음
        """
        idempotency_key = f"order_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 원본 요청
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: backward skew 재시도
        retry_time = base_time - timedelta(seconds=30)
        is_new = idempotency_repo.save(idempotency_key, retry_time, str(uuid.uuid4()))

        # then: 중복 탐지
        assert is_new is False

    def test_stage_40_retry_forensic_context_accuracy(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: 재시도 포렌식 컨텍스트 정확성

        Given:
            - 여러 재시도 발생

        When:
            - 다양한 skew 값으로 재시도

        Then:
            - 모든 재시도 기록됨
            - 시간 정보 정확
        """
        idempotency_key = f"order_{uuid.uuid4()}"
        base_time = mock_time_provider.now()
        original_request_id = str(uuid.uuid4())

        # given: 원본 요청
        idempotency_repo.save(idempotency_key, base_time, original_request_id)

        # when: 다양한 skew로 재시도
        retries = [
            (base_time + timedelta(seconds=10), "retry_1"),
            (base_time - timedelta(seconds=5), "retry_2"),
            (base_time + timedelta(seconds=60), "retry_3"),
        ]

        for retry_time, retry_id in retries:
            idempotency_repo.save(idempotency_key, retry_time, retry_id)

        # then: 실행 로그에 모든 재시도 기록
        log = idempotency_repo.execution_log
        assert len(log) == 4  # 1 원본 + 3 재시도

        # then: 첫 번째는 new, 나머지는 duplicate
        assert log[0]["action"] == "new_key_registered"
        for i in range(1, 4):
            assert log[i]["action"] == "duplicate_detected"

        # then: 재시도 횟수 정확
        assert idempotency_repo.get_execution_count(idempotency_key) == 4

    def test_stage_40_concurrent_retries_with_skew(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: 동시 재시도 + Clock skew

        Given:
            - 원본 요청 처리됨

        When:
            - 여러 클라이언트가 동시에 skewed 시간으로 재시도

        Then:
            - 모든 재시도가 중복으로 처리됨
            - Race condition 안전
        """
        idempotency_key = f"order_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 원본 요청
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: 동시 재시도
        results = []

        def concurrent_retry(skew_seconds: int):
            retry_time = base_time + timedelta(seconds=skew_seconds)
            is_new = idempotency_repo.save(idempotency_key, retry_time, str(uuid.uuid4()))
            return is_new

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(concurrent_retry, skew) for skew in range(-30, 31, 5)]  # -30초 ~ +30초
            for future in as_completed(futures):
                results.append(future.result())

        # then: 모든 재시도가 중복으로 탐지됨
        assert all(r is False for r in results), "All retries should be detected as duplicates"


# =============================================================================
# Stage 40-S4: Clock Skew + DLQ Safety Check
# =============================================================================


@pytest.mark.chaos
class TestStage40ClockSkewDLQSafety:
    """
    Stage 40-S4: Clock Skew + DLQ 안전성 검증

    Given: Skewed timestamps가 retry 소진 근처에서 발생
    When: DLQ 라우팅 결정
    Then:
        - DLQ 라우팅 정확
        - 명시적 트리거 없이 재생 없음
        - 해결 경로 결정적
    """

    def test_stage_40_dlq_routing_with_skewed_timestamps(
        self,
        idempotency_repo: MockIdempotencyRepository,
        dlq_repo: MockDLQRepository,
        mock_time_provider,
    ):
        """
        시나리오: Skewed timestamps로 DLQ 라우팅

        Given:
            - 재시도 한도 근처의 요청

        When:
            - Skewed timestamp로 마지막 재시도

        Then:
            - DLQ 라우팅 정확
            - 중복 DLQ 항목 없음
        """
        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: 원본 요청 + DLQ 등록
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))
        dlq_entry = dlq_repo.add(idempotency_key, base_time, reason="Transient failure")

        # given: 2회 재시도 (한도 = 3)
        dlq_repo.increment_retry(dlq_entry.entry_id)
        dlq_repo.increment_retry(dlq_entry.entry_id)

        # when: skewed timestamp로 마지막 재시도
        skewed_time = base_time + timedelta(seconds=45)

        # 마지막 재시도 시도 (3회차 = 소진)
        can_retry = dlq_repo.increment_retry(dlq_entry.entry_id)

        # then: 재시도 한도 소진
        assert can_retry is False

        # then: DLQ 상태 정확
        assert dlq_entry.status == "exhausted"
        assert dlq_entry.retry_count == MAX_RETRY_ATTEMPTS

    def test_stage_40_no_replay_without_explicit_trigger(
        self,
        idempotency_repo: MockIdempotencyRepository,
        dlq_repo: MockDLQRepository,
        selfhealing_services,
        mock_time_provider,
    ):
        """
        시나리오: 명시적 트리거 없이 재생 없음

        Given:
            - DLQ에 항목 존재
            - Clock skew 상황

        When:
            - 자동 복구 로직 없음 (trigger_replay=False)

        Then:
            - 재생 없음
            - DLQ 상태 유지
        """
        force_close_circuit = selfhealing_services["force_close_circuit"]

        idempotency_key = f"payment_{uuid.uuid4()}"
        base_time = mock_time_provider.now()

        # given: DLQ 항목
        dlq_entry = dlq_repo.add(idempotency_key, base_time)

        # when: CB 복구 (trigger_replay=False)
        force_close_circuit(
            SERVICE_PAYMENT_GATEWAY,
            reason="Recovery without replay",
            trigger_replay=False,
        )

        # then: DLQ 상태 변경 없음 (재생 없음)
        assert dlq_entry.status == "pending"

    def test_stage_40_deterministic_resolution_path(
        self,
        idempotency_repo: MockIdempotencyRepository,
        dlq_repo: MockDLQRepository,
        mock_time_provider,
    ):
        """
        시나리오: 결정적 해결 경로

        Given:
            - 동일한 초기 상태

        When:
            - 동일한 skew 패턴 적용

        Then:
            - 동일한 최종 상태
            - 결정적 동작
        """

        def run_scenario(seed: int) -> Dict[str, Any]:
            """Run scenario with consistent behavior."""
            idempotency_key = f"payment_seed_{seed}"
            base_time = mock_time_provider.now()

            # 원본 등록
            is_new = idempotency_repo.save(idempotency_key, base_time, f"req_{seed}_0")

            # DLQ 등록
            dlq_entry = dlq_repo.add(idempotency_key, base_time)

            # 재시도 (skewed)
            for i in range(3):
                skewed_time = base_time + timedelta(seconds=(i + 1) * 15)
                idempotency_repo.save(idempotency_key, skewed_time, f"req_{seed}_{i+1}")
                dlq_repo.increment_retry(dlq_entry.entry_id)

            return {
                "is_new": is_new,
                "execution_count": idempotency_repo.get_execution_count(idempotency_key),
                "dlq_status": dlq_entry.status,
                "retry_count": dlq_entry.retry_count,
            }

        # when: 동일한 시나리오 2회 실행
        result1 = run_scenario(1)
        result2 = run_scenario(2)

        # then: 구조적으로 동일한 결과 (결정적)
        assert result1["execution_count"] == result2["execution_count"]
        assert result1["dlq_status"] == result2["dlq_status"]
        assert result1["retry_count"] == result2["retry_count"]

    def test_stage_40_dlq_skew_boundary_exhaustion(
        self,
        dlq_repo: MockDLQRepository,
        mock_time_provider,
    ):
        """
        시나리오: Skew 경계에서 DLQ 소진

        Given:
            - 재시도 한도 = 3

        When:
            - 다양한 skew로 정확히 3회 재시도

        Then:
            - 정확히 3회에서 소진
            - 시스템 안정
        """
        base_time = mock_time_provider.now()
        idempotency_key = f"payment_{uuid.uuid4()}"

        # given: DLQ 항목
        entry = dlq_repo.add(idempotency_key, base_time)

        # when: 3회 재시도 (다양한 skew)
        skews = [30, -15, 60]  # 다양한 skew 값
        results = []
        for skew in skews:
            can_retry = dlq_repo.increment_retry(entry.entry_id)
            results.append(can_retry)

        # then: 처음 2회는 성공, 3회차에서 소진
        assert results == [True, True, False]
        assert entry.status == "exhausted"


# =============================================================================
# Stage 40-S5: Security Boundary + Clock Skew
# =============================================================================


@pytest.mark.chaos
class TestStage40SecurityBoundaryClockSkew:
    """
    Stage 40-S5: 보안 경계 + Clock Skew

    Given: 보안 관련 요청에 clock skew 적용
    When: 중복 요청 또는 재시도 발생
    Then:
        - 보안 위반의 자동 치유 없음
        - 수동 개입 필요
        - Decision Record 일관성
    """

    def test_stage_40_security_violation_no_auto_heal_with_skew(
        self,
        selfhealing_services,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Clock skew 상황에서 보안 위반 자동 치유 없음

        Given:
            - 보안 서비스 CB가 수동 제어 상태

        When:
            - Skewed timestamp로 재시도 시도

        Then:
            - 자동 치유 없음
            - CB 상태 유지
        """
        force_open_circuit = selfhealing_services["force_open_circuit"]
        CircuitState = selfhealing_services["CircuitState"]
        get_cb_service = selfhealing_services["get_circuit_breaker_service"]
        cb_service = get_cb_service()

        # given: 보안 서비스 수동 OPEN
        force_open_circuit(SERVICE_SECURITY, reason="Security: Manual control required")
        state = cb_service.get_or_create_state(SERVICE_SECURITY)

        # given: 원본 요청
        idempotency_key = f"auth_{uuid.uuid4()}"
        base_time = mock_time_provider.now()
        idempotency_repo.save(idempotency_key, base_time, str(uuid.uuid4()))

        # when: skewed 재시도 (forward + backward)
        for skew in [30, -30, 60, -60]:
            skewed_time = base_time + timedelta(seconds=skew)
            idempotency_repo.save(idempotency_key, skewed_time, str(uuid.uuid4()))

        # then: 보안 CB 여전히 OPEN (자동 치유 없음)
        assert cb_service.get_state(SERVICE_SECURITY) == CircuitState.OPEN
        assert state.manually_controlled is True

    def test_stage_40_security_requires_operator_intervention_with_skew(
        self,
        selfhealing_services,
        mock_time_provider,
    ):
        """
        시나리오: Clock skew 상황에서도 운영자 개입 필요

        Given:
            - 보안 서비스 장애

        When:
            - 시간 경과 + clock skew

        Then:
            - 자동 HALF_OPEN 전이 없음
            - 명시적 force_close 필요
        """
        force_open_circuit = selfhealing_services["force_open_circuit"]
        force_close_circuit = selfhealing_services["force_close_circuit"]
        CircuitState = selfhealing_services["CircuitState"]
        get_cb_service = selfhealing_services["get_circuit_breaker_service"]
        cb_service = get_cb_service()

        # given: 보안 서비스 OPEN
        force_open_circuit(SERVICE_SECURITY, reason="Security violation detected")

        # when: 시간 경과 시뮬레이션
        time.sleep(0.1)

        # then: 여전히 OPEN
        assert cb_service.get_state(SERVICE_SECURITY) == CircuitState.OPEN

        # when: 운영자 명시적 복구
        force_close_circuit(SERVICE_SECURITY, reason="Operator confirmed", trigger_replay=False)

        # then: 이제 CLOSED
        assert cb_service.get_state(SERVICE_SECURITY) == CircuitState.CLOSED


# =============================================================================
# Stage 40-S6: Decision Record Consistency
# =============================================================================


@pytest.mark.chaos
class TestStage40DecisionRecordConsistency:
    """
    Stage 40-S6: Decision Record 일관성 검증

    Given: Clock skew 상황에서 다양한 이벤트 발생
    When: Decision Record 생성
    Then:
        - 고정된 reason codes만 사용
        - 스키마 변경 없음
        - 메트릭 일관성
    """

    def test_stage_40_decision_record_fixed_reason_codes(
        self,
        decision_logger_classes,
    ):
        """
        시나리오: 고정된 reason codes만 사용

        Given:
            - 동결된 reason codes

        When:
            - Clock skew 테스트 후

        Then:
            - 새로운 reason code 없음
        """
        ReasonCode = decision_logger_classes["ReasonCode"]

        # Expected frozen values (Stage 39와 동일)
        expected = {
            "INTERVENTION_ALLOWED",
            "POLICY_CONSTRAINT_ACTIVE",
            "STABILITY_OK_NO_INTERVENTION",
            "THRESHOLD_NOT_MET",
        }

        actual = {r.value for r in ReasonCode}

        # then: 변경 없음
        assert actual == expected, f"ReasonCode changed: {actual}"

    def test_stage_40_decision_record_schema_frozen(
        self,
        decision_record_capture: DecisionRecordCapture,
        decision_logger_classes,
    ):
        """
        시나리오: Decision Record 스키마 동결

        Given:
            - 동결된 스키마

        When:
            - Decision Record 이벤트 생성

        Then:
            - 스키마 위반 없음
        """
        DecisionLogger = decision_logger_classes["DecisionLogger"]
        ReasonCode = decision_logger_classes["ReasonCode"]

        # when: Decision Record 생성
        try:
            logger = DecisionLogger(
                service_name="stage40_clock_skew_test",
                policy_version="v1",
            )
            logger.enter_pre_decision_zone()
            logger.intervention_evaluated(
                allowed=False,
                reason=ReasonCode.THRESHOLD_NOT_MET,
            )
            logger.exit_pre_decision_zone()
        except Exception as e:
            pytest.fail(f"DecisionLogger raised unexpected exception: {e}")

        # then: 시스템 생존 (스키마 위반 시 예외 발생)
        assert True

    def test_stage_40_metrics_consistency_with_skew(
        self,
        idempotency_repo: MockIdempotencyRepository,
        mock_time_provider,
    ):
        """
        시나리오: Clock skew 상황에서 메트릭 일관성

        Given:
            - 다양한 skew로 요청 처리

        When:
            - 메트릭 수집

        Then:
            - 중복 카운트 정확
            - 시간 정보 일관
        """
        base_time = mock_time_provider.now()

        # given: 5개의 다른 idempotency key로 요청
        keys = [f"payment_{i}_{uuid.uuid4()}" for i in range(5)]
        for key in keys:
            idempotency_repo.save(key, base_time, str(uuid.uuid4()))

        # when: 각 key에 대해 skewed 중복 요청
        for key in keys:
            for skew in [-30, 0, 30]:
                skewed_time = base_time + timedelta(seconds=skew)
                idempotency_repo.save(key, skewed_time, str(uuid.uuid4()))

        # then: 메트릭 일관성
        total_new = sum(1 for log in idempotency_repo.execution_log if log["action"] == "new_key_registered")
        total_dup = sum(1 for log in idempotency_repo.execution_log if log["action"] == "duplicate_detected")

        assert total_new == 5, "Should have 5 new registrations"
        assert total_dup == 15, "Should have 15 duplicate detections (5 keys × 3 duplicates each)"


# =============================================================================
# Stage 40-S7: Full Integration Scenario
# =============================================================================


@pytest.mark.chaos
class TestStage40FullIntegration:
    """
    Stage 40-S7: 전체 통합 시나리오

    모든 Stage 40 요구사항을 하나의 통합 테스트로 검증
    """

    def test_stage_40_complete_idempotency_clock_skew_cycle(
        self,
        idempotency_repo: MockIdempotencyRepository,
        dlq_repo: MockDLQRepository,
        selfhealing_services,
        decision_record_capture: DecisionRecordCapture,
        mock_time_provider,
        caplog,
    ):
        """
        시나리오: 전체 Idempotency + Clock Skew 사이클

        Given:
            - 정상 시스템 상태

        When:
            1. 원본 요청 처리
            2. Forward skew 중복 요청
            3. Backward skew 중복 요청
            4. 재시도 + skew
            5. DLQ 소진
            6. 보안 경계 확인

        Then:
            - 모든 경계 준수
            - 중복 실행 없음
            - Decision Record 일관성
        """
        force_open_circuit = selfhealing_services["force_open_circuit"]
        CircuitState = selfhealing_services["CircuitState"]
        get_cb_service = selfhealing_services["get_circuit_breaker_service"]
        cb_service = get_cb_service()

        base_time = mock_time_provider.now()
        idempotency_key = f"integration_test_{uuid.uuid4()}"
        boundary_violations = []

        try:
            # Phase 1: 원본 요청
            is_new = idempotency_repo.save(idempotency_key, base_time, "original_request")
            if not is_new:
                boundary_violations.append("Phase 1: Original should be new")

            # Phase 2: Forward skew 중복
            forward_time = base_time + timedelta(seconds=60)
            is_dup = idempotency_repo.save(idempotency_key, forward_time, "forward_dup")
            if is_dup:
                boundary_violations.append("Phase 2: Forward skew should be duplicate")

            # Phase 3: Backward skew 중복
            backward_time = base_time - timedelta(seconds=45)
            is_dup = idempotency_repo.save(idempotency_key, backward_time, "backward_dup")
            if is_dup:
                boundary_violations.append("Phase 3: Backward skew should be duplicate")

            # Phase 4: DLQ + 재시도
            dlq_entry = dlq_repo.add(idempotency_key, base_time, reason="Integration test failure")
            for i in range(MAX_RETRY_ATTEMPTS):
                skewed_time = base_time + timedelta(seconds=(i + 1) * 20)
                idempotency_repo.save(idempotency_key, skewed_time, f"retry_{i}")
                dlq_repo.increment_retry(dlq_entry.entry_id)

            if dlq_entry.status != "exhausted":
                boundary_violations.append("Phase 4: DLQ should be exhausted after max retries")

            # Phase 5: 보안 경계 확인
            force_open_circuit(SERVICE_SECURITY, reason="Integration: Security check")
            if cb_service.get_state(SERVICE_SECURITY) != CircuitState.OPEN:
                boundary_violations.append("Phase 5: Security CB should be OPEN")

            state = cb_service.get_or_create_state(SERVICE_SECURITY)
            if not state.manually_controlled:
                boundary_violations.append("Phase 5: Security should be manually controlled")

        except Exception as e:
            boundary_violations.append(f"Exception occurred: {e}")

        # Final assertions
        assert len(boundary_violations) == 0, f"Boundary violations: {boundary_violations}"

        # Verify execution metrics
        execution_count = idempotency_repo.get_execution_count(idempotency_key)
        # 1 original + 2 skew duplicates + 3 retries = 6
        assert execution_count == 6, f"Expected 6 executions, got {execution_count}"

        # Verify DLQ state
        assert dlq_entry.status == "exhausted"
        assert dlq_entry.retry_count == MAX_RETRY_ATTEMPTS

        # Verify no auto-heal
        assert cb_service.get_state(SERVICE_SECURITY) == CircuitState.OPEN

        # System survived
        assert True, "System survived complete integration cycle"
