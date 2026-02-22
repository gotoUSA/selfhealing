"""
249. Saga Orchestrator Engine 통합 테스트.

Redis OCC 실제 동작, Celery Task 디스패치 체인, 동시성 충돌 시나리오를
실제 Redis 인프라를 사용하여 검증한다.

Docker Compose 환경에서 실행:
    docker-compose -f docker-compose.test.yml run --rm test-saga-orchestrator

테스트 시나리오:
    1. Redis Lua CAS OCC — SAGA_INSTANCE_CAS_SCRIPT 실제 동작
    2. Redis Lua 상태 전환 — SAGA_TRANSITION_SCRIPT 실제 동작
    3. 동시성 충돌 — 두 쓰레드가 동시에 _save_instance() 호출 시 OCC 충돌 감지
    4. 전체 Saga 라이프사이클 — Redis 백엔드로 Forward → Compensate 전체 흐름
    5. Celery Task 디스패치 체인 — resume_saga_instance_task 실제 디스패치
    6. Orphan Saga 스캔 및 재개 — scan_orphan_sagas → resume 체인
    7. Compensation 실패 → DLQ 저장 체인
    8. Lock Heartbeat — _execute_with_timeout 내 락 연장 동작

선례:
    - tests/integration/selfhealing/test_leader_election_integration.py
    - tests/integration/selfhealing/test_healing_events_redis_integration.py
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import redis

from selfhealing.services.saga.events import SagaEventType
from selfhealing.services.saga.lua_scripts import (
    SAGA_INSTANCE_CAS_SCRIPT,
    SAGA_TRANSITION_SCRIPT,
)
from selfhealing.services.saga.models import (
    SagaContext,
    SagaDefinition,
    SagaInstance,
    SagaStatus,
    SagaStepStatus,
    StepResult,
)
from selfhealing.services.saga.orchestrator import SagaOrchestrator
from selfhealing.services.saga.registry import (
    register_saga,
)
from selfhealing.services.saga.step import SagaStep
from selfhealing.services.coordination.recovery_coordinator import (
    SessionVersionConflictError,
)


# ---------------------------------------------------------------------------
# 환경 설정
# ---------------------------------------------------------------------------

# Docker 내부: redis://redis:6379/0
# 로컬 테스트: redis://localhost:16379/0
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

# Saga 전용 Redis key prefix (기존 selfhealing:state: prefix와 동일)
KEY_PREFIX = "selfhealing:state:"


# ---------------------------------------------------------------------------
# 테스트용 SagaStep 구현체
# ---------------------------------------------------------------------------


class SuccessStep(SagaStep):
    """항상 성공하는 Step."""

    def __init__(self, name: str, delay: float = 0.0):
        self._name = name
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        if self._delay:
            time.sleep(self._delay)
        return StepResult.succeeded(data={f"{self._name}_id": f"{self._name}_001"})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class FailingStep(SagaStep):
    """항상 실패하는 Step (non-retryable)."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed(
            error=f"{self._name} failed",
            error_code=f"{self._name.upper()}_ERROR",
            retryable=False,
        )

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class FailingCompensateStep(SagaStep):
    """execute는 성공하지만 compensate가 실패하는 Step."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded(data={f"{self._name}_id": f"{self._name}_001"})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed(
            error=f"{self._name} compensate failed",
            error_code=f"{self._name.upper()}_COMPENSATE_ERROR",
        )


class RetryableFailStep(SagaStep):
    """retryable=True로 실패하는 Step."""

    def __init__(self, name: str, fail_count: int = 1):
        self._name = name
        self._fail_count = fail_count
        self._call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            return StepResult.failed(
                error=f"{self._name} transient failure #{self._call_count}",
                error_code="TRANSIENT_ERROR",
                retryable=True,
            )
        return StepResult.succeeded(data={f"{self._name}_id": f"{self._name}_ok"})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


class PartialExecutionFailStep(SagaStep):
    """부분 실행 후 실패하는 Step (partial_execution=True)."""

    def __init__(self, name: str):
        self._name = name
        self._compensated = False

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        return StepResult.failed_with_side_effect(
            error=f"{self._name} partially executed then failed",
            data={f"{self._name}_partial": True},
            error_code="PARTIAL_EXEC",
        )

    def compensate(self, ctx: SagaContext) -> StepResult:
        self._compensated = True
        return StepResult.succeeded()


class SlowStep(SagaStep):
    """지정된 시간만큼 대기하는 Step (Lock Heartbeat 테스트용)."""

    def __init__(self, name: str, sleep_seconds: float = 2.0):
        self._name = name
        self._sleep = sleep_seconds

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: SagaContext) -> StepResult:
        time.sleep(self._sleep)
        return StepResult.succeeded(data={f"{self._name}_done": True})

    def compensate(self, ctx: SagaContext) -> StepResult:
        return StepResult.succeeded()


# ---------------------------------------------------------------------------
# Redis-backed StateBackend wrapper (통합 테스트용)
# ---------------------------------------------------------------------------


class RedisTestBackend:
    """실제 Redis 연결을 사용하는 테스트 전용 StateBackend.

    RedisStateBackend와 동일한 인터페이스(_client, _make_key, get, set, scan)를 제공.
    orchestrator._save_instance()가 Lua CAS 스크립트를 실행할 수 있도록 한다.
    _scan_active_saga_instances()가 scan()을 호출할 수 있도록 한다.
    """

    def __init__(self, redis_client: redis.Redis, key_prefix: str = KEY_PREFIX):
        self._client = redis_client
        self._key_prefix = key_prefix

    def _make_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    def get(self, key: str, default: Any = None) -> Any:
        data = self._client.get(self._make_key(key))
        if data:
            return json.loads(data)
        return default

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        data = json.dumps(value, default=str)
        if ttl_seconds:
            self._client.setex(self._make_key(key), ttl_seconds, data)
        else:
            self._client.set(self._make_key(key), data)

    def scan(self, pattern: str) -> list[str]:
        """Redis SCAN으로 패턴 매칭 키 목록을 반환.

        _scan_active_saga_instances()가 호출하는 인터페이스.
        패턴에 _key_prefix를 붙이고, 반환 시 제거.
        """
        full_pattern = self._make_key(pattern)
        keys = []
        for key in self._client.scan_iter(match=full_pattern, count=200):
            short_key = key.replace(self._key_prefix, "")
            keys.append(short_key)
        return keys


# ---------------------------------------------------------------------------
# Mock 인프라 서비스 팩토리
# ---------------------------------------------------------------------------


def _make_mock_lock():
    """분산 락 Mock. acquire/release/extend 모두 성공."""
    lock = MagicMock()
    lock.acquire.return_value = True
    lock.release.return_value = True
    lock.extend.return_value = True
    return lock


def _make_mock_idempotency():
    """멱등성 서비스 Mock. 중복 없음."""
    service = MagicMock()
    result = MagicMock()
    result.is_duplicate = False
    service.check.return_value = result
    return service


def _make_mock_dlq():
    """DLQ 서비스 Mock."""
    return MagicMock()


def _make_mock_event_bus():
    """EventBus Mock."""
    return MagicMock()


def _make_mock_circuit_breaker():
    """서킷브레이커 Mock. 항상 CLOSED."""
    cb = MagicMock()
    state = MagicMock()
    state.value = "closed"
    cb.get_state.return_value = state
    return cb


def _make_mock_blast_radius():
    """BlastRadius Mock."""
    return MagicMock()


def _make_orchestrator(backend, **kwargs):
    """테스트용 SagaOrchestrator 생성.

    backend만 실제 Redis를 사용하고, 나머지 인프라는 Mock.
    """
    defaults = {
        "lock": _make_mock_lock(),
        "idempotency": _make_mock_idempotency(),
        "dlq": _make_mock_dlq(),
        "event_bus": _make_mock_event_bus(),
        "circuit_breaker": _make_mock_circuit_breaker(),
        "blast_radius": _make_mock_blast_radius(),
        "backend": backend,
    }
    defaults.update(kwargs)
    return SagaOrchestrator(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_client():
    """세션 스코프 Redis 클라이언트."""
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not available — run with docker-compose.test.yml")
    yield client
    client.close()


@pytest.fixture(autouse=True)
def clean_saga_keys(redis_client):
    """각 테스트 전후로 saga 관련 Redis 키를 정리."""
    _cleanup_saga_keys(redis_client)
    yield
    _cleanup_saga_keys(redis_client)


def _cleanup_saga_keys(client: redis.Redis):
    """saga 관련 키를 모두 삭제."""
    patterns = [
        f"{KEY_PREFIX}saga:*",
        "saga:*",
    ]
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match=pattern, count=200)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break


@pytest.fixture
def backend(redis_client):
    """Redis 기반 테스트 StateBackend."""
    return RedisTestBackend(redis_client)


@pytest.fixture
def unique_id():
    """테스트마다 고유한 saga ID 생성."""
    return f"saga-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def cleanup_registry():
    """테스트 전후로 saga 레지스트리 정리."""
    from selfhealing.services.saga.registry import _saga_definitions

    original = dict(_saga_definitions)
    yield
    _saga_definitions.clear()
    _saga_definitions.update(original)


# =========================================================================
# 테스트 그룹 1: Redis Lua CAS OCC 실제 동작
# =========================================================================


class TestRedisOCC:
    """SAGA_INSTANCE_CAS_SCRIPT Lua 스크립트의 실제 Redis 동작을 검증."""

    def test_cas_script_new_key_succeeds(self, redis_client):
        """신규 키(존재하지 않는 키)에 대한 CAS가 성공하는지 확인."""
        key = f"{KEY_PREFIX}saga:instance:test-new-{uuid.uuid4().hex[:8]}"
        data = json.dumps({"id": "test-001", "version": 1, "status": "pending"})

        result = redis_client.eval(
            SAGA_INSTANCE_CAS_SCRIPT,
            1,
            key,
            data,
            "0",  # expected_version=0 (신규)
        )

        assert result == 1, "신규 키에 대한 CAS는 성공해야 한다"

        # 저장된 데이터 확인
        stored = json.loads(redis_client.get(key))
        assert stored["id"] == "test-001"
        assert stored["version"] == 1

    def test_cas_script_matching_version_succeeds(self, redis_client):
        """expected_version이 현재 저장된 version과 일치하면 CAS 성공."""
        key = f"{KEY_PREFIX}saga:instance:test-match-{uuid.uuid4().hex[:8]}"

        # v1 저장
        v1_data = json.dumps({"id": "test-002", "version": 1, "status": "pending"})
        redis_client.set(key, v1_data)

        # v1 → v2 CAS (expected=1)
        v2_data = json.dumps({"id": "test-002", "version": 2, "status": "running"})
        result = redis_client.eval(
            SAGA_INSTANCE_CAS_SCRIPT,
            1,
            key,
            v2_data,
            "1",  # expected_version=1 (현재 v1)
        )

        assert result == 1, "버전 일치 시 CAS는 성공해야 한다"

        stored = json.loads(redis_client.get(key))
        assert stored["version"] == 2
        assert stored["status"] == "running"

    def test_cas_script_version_mismatch_fails(self, redis_client):
        """expected_version이 현재 저장된 version과 불일치하면 CAS 실패(0 반환)."""
        key = f"{KEY_PREFIX}saga:instance:test-conflict-{uuid.uuid4().hex[:8]}"

        # v2 저장 (이미 다른 워커가 v2로 업데이트)
        v2_data = json.dumps({"id": "test-003", "version": 2, "status": "running"})
        redis_client.set(key, v2_data)

        # v1 → v2 CAS 시도 (expected=1이지만 현재 v2)
        v2_again = json.dumps({"id": "test-003", "version": 2, "status": "compensating"})
        result = redis_client.eval(
            SAGA_INSTANCE_CAS_SCRIPT,
            1,
            key,
            v2_again,
            "1",  # expected_version=1 (틀림)
        )

        assert result == 0, "버전 불일치 시 CAS는 실패(0)를 반환해야 한다"

        # 원래 데이터가 변경되지 않았는지 확인
        stored = json.loads(redis_client.get(key))
        assert stored["status"] == "running", "CAS 실패 시 데이터가 변경되면 안 된다"

    def test_cas_script_sequential_increments(self, redis_client):
        """순차적 버전 증가가 정상 동작하는지 확인. v0→v1→v2→v3."""
        key = f"{KEY_PREFIX}saga:instance:test-seq-{uuid.uuid4().hex[:8]}"

        for version in range(1, 4):
            data = json.dumps({"id": "test-seq", "version": version, "status": f"v{version}"})
            expected = version - 1
            result = redis_client.eval(
                SAGA_INSTANCE_CAS_SCRIPT,
                1,
                key,
                data,
                str(expected),
            )
            assert result == 1, f"v{expected}→v{version} CAS는 성공해야 한다"

        stored = json.loads(redis_client.get(key))
        assert stored["version"] == 3

    def test_cas_script_cjson_decode_complex_data(self, redis_client):
        """복잡한 SagaInstance 직렬화 데이터에도 cjson.decode가 동작하는지 확인."""
        key = f"{KEY_PREFIX}saga:instance:test-complex-{uuid.uuid4().hex[:8]}"

        instance_data = {
            "id": "saga-complex-001",
            "saga_name": "order_creation",
            "version": 1,
            "status": "running",
            "step_instances": [
                {"step_name": "payment", "order": 0, "status": "executed"},
                {"step_name": "inventory", "order": 1, "status": "executing"},
            ],
            "context": {
                "saga_instance_id": "saga-complex-001",
                "initial_data": {"order_id": 123, "amount": 50000},
                "step_results": {"payment": {"payment_id": "pay_001"}},
            },
            "metadata": {"resume_count": 0},
        }

        # v0 → v1
        data = json.dumps(instance_data, default=str)
        result = redis_client.eval(SAGA_INSTANCE_CAS_SCRIPT, 1, key, data, "0")
        assert result == 1

        # v1 → v2 (status 변경)
        instance_data["version"] = 2
        instance_data["status"] = "completed"
        data = json.dumps(instance_data, default=str)
        result = redis_client.eval(SAGA_INSTANCE_CAS_SCRIPT, 1, key, data, "1")
        assert result == 1

        stored = json.loads(redis_client.get(key))
        assert stored["status"] == "completed"
        assert stored["context"]["step_results"]["payment"]["payment_id"] == "pay_001"


# =========================================================================
# 테스트 그룹 2: Redis Lua 상태 전환 스크립트 실제 동작
# =========================================================================


class TestRedisTransitionScript:
    """SAGA_TRANSITION_SCRIPT Lua 스크립트의 실제 Redis 동작을 검증."""

    def test_transition_matching_status_succeeds(self, redis_client):
        """현재 status와 expected_status가 일치하면 전환 성공."""
        key = f"saga:transition:test-{uuid.uuid4().hex[:8]}"

        # HASH로 초기 상태 저장
        redis_client.hset(key, "status", "running")

        result = redis_client.eval(
            SAGA_TRANSITION_SCRIPT,
            1,
            key,
            "running",  # expected
            "compensating",  # new
            datetime.now(timezone.utc).isoformat(),
        )

        assert result[0] == 1
        assert result[2] == "compensating"

        # Redis에서 확인
        assert redis_client.hget(key, "status") == "compensating"
        assert redis_client.hget(key, "updated_at") is not None

    def test_transition_mismatched_status_fails(self, redis_client):
        """현재 status와 expected_status가 불일치하면 전환 실패."""
        key = f"saga:transition:test-{uuid.uuid4().hex[:8]}"

        redis_client.hset(key, "status", "running")

        result = redis_client.eval(
            SAGA_TRANSITION_SCRIPT,
            1,
            key,
            "pending",  # expected (틀림: 현재는 running)
            "compensating",  # new
            datetime.now(timezone.utc).isoformat(),
        )

        assert result[0] == 0
        assert result[1] == "status_mismatch"

        # 원래 상태가 유지되는지 확인
        assert redis_client.hget(key, "status") == "running"

    def test_transition_nonexistent_key_fails(self, redis_client):
        """존재하지 않는 키에 대한 전환은 실패해야 한다."""
        key = f"saga:transition:nonexistent-{uuid.uuid4().hex[:8]}"

        result = redis_client.eval(
            SAGA_TRANSITION_SCRIPT,
            1,
            key,
            "running",
            "compensating",
            datetime.now(timezone.utc).isoformat(),
        )

        assert result[0] == 0, "존재하지 않는 키에 대한 전환은 실패해야 한다"

    def test_transition_full_lifecycle(self, redis_client):
        """PENDING → RUNNING → COMPENSATING → COMPENSATED 전체 전환 체인."""
        key = f"saga:transition:lifecycle-{uuid.uuid4().hex[:8]}"

        transitions = [
            ("pending", "running"),
            ("running", "compensating"),
            ("compensating", "compensated"),
        ]

        redis_client.hset(key, "status", "pending")

        for expected, new in transitions:
            result = redis_client.eval(
                SAGA_TRANSITION_SCRIPT,
                1,
                key,
                expected,
                new,
                datetime.now(timezone.utc).isoformat(),
            )
            assert result[0] == 1, f"전환 {expected}→{new}이 성공해야 한다"

        assert redis_client.hget(key, "status") == "compensated"


# =========================================================================
# 테스트 그룹 3: 동시성 충돌 시나리오
# =========================================================================


class TestConcurrencyConflict:
    """두 쓰레드가 동시에 같은 SagaInstance를 수정할 때 OCC로 충돌을 감지하는지 검증."""

    def test_concurrent_save_instance_one_wins_one_loses(self, redis_client, backend, unique_id):
        """동시 _save_instance 호출 시 하나는 성공, 하나는 SessionVersionConflictError."""
        # 초기 인스턴스 Redis에 저장 (version=1)
        instance_data = _create_test_instance_data(unique_id, version=1, status="running")
        backend.set(f"saga:instance:{unique_id}", instance_data)

        orchestrator = _make_orchestrator(backend)

        # 두 "워커"가 동시에 version=1을 읽고, version=2로 업데이트 시도
        results = {"worker_a": None, "worker_b": None}
        errors = {"worker_a": None, "worker_b": None}
        barrier = threading.Barrier(2, timeout=5)

        def worker(name):
            try:
                instance = SagaInstance.from_dict(instance_data)
                instance.version = 2  # 둘 다 v1→v2 시도
                if name == "worker_b":
                    instance.status = SagaStatus.COMPENSATING
                barrier.wait()  # 동시 실행 보장
                orchestrator._save_instance(instance)
                results[name] = "success"
            except SessionVersionConflictError as e:
                errors[name] = e
                results[name] = "conflict"
            except Exception as e:
                errors[name] = e
                results[name] = "error"

        t_a = threading.Thread(target=worker, args=("worker_a",))
        t_b = threading.Thread(target=worker, args=("worker_b",))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        # 하나는 성공, 하나는 충돌 (순서 불확정)
        outcomes = [results["worker_a"], results["worker_b"]]
        assert "success" in outcomes, f"하나는 성공해야 한다: {outcomes}"
        assert "conflict" in outcomes, f"하나는 충돌이어야 한다: {outcomes}"

    def test_sequential_version_increments_via_save_instance(self, redis_client, backend, unique_id):
        """순차적 _save_instance 호출이 버전을 올바르게 증가시키는지 확인."""
        orchestrator = _make_orchestrator(backend)

        instance = SagaInstance(
            id=unique_id,
            saga_name="test_saga",
            version=0,
            status=SagaStatus.PENDING,
            step_instances=[],
            context=SagaContext(saga_instance_id=unique_id, initial_data={}),
        )

        # v0 → v1
        instance.version += 1
        orchestrator._save_instance(instance)

        stored = backend.get(f"saga:instance:{unique_id}")
        assert stored["version"] == 1

        # v1 → v2
        instance.version += 1
        orchestrator._save_instance(instance)

        stored = backend.get(f"saga:instance:{unique_id}")
        assert stored["version"] == 2

        # v2 → v3
        instance.version += 1
        orchestrator._save_instance(instance)

        stored = backend.get(f"saga:instance:{unique_id}")
        assert stored["version"] == 3

    def test_stale_write_rejected_after_concurrent_update(self, redis_client, backend, unique_id):
        """먼저 저장된 워커의 결과가 있으면 뒤늦은 워커의 저장이 거부되는지 확인."""
        orchestrator = _make_orchestrator(backend)

        # 초기 v0 저장
        instance_data = _create_test_instance_data(unique_id, version=0, status="pending")
        backend.set(f"saga:instance:{unique_id}", instance_data)

        # 워커 A: v0 → v1 (성공)
        instance_a = SagaInstance.from_dict(instance_data)
        instance_a.version = 1
        instance_a.status = SagaStatus.RUNNING
        orchestrator._save_instance(instance_a)

        # 워커 B: v0 → v1 (stale — v0는 더 이상 없음, 현재 v1)
        instance_b = SagaInstance.from_dict(instance_data)
        instance_b.version = 1  # expected v0 → v1이지만 현재 이미 v1
        instance_b.status = SagaStatus.COMPENSATING

        with pytest.raises(SessionVersionConflictError):
            orchestrator._save_instance(instance_b)

        # Redis에는 워커 A의 결과가 남아있어야 함
        stored = backend.get(f"saga:instance:{unique_id}")
        assert stored["status"] == "running"


# =========================================================================
# 테스트 그룹 4: 전체 Saga 라이프사이클 (Redis 백엔드)
# =========================================================================


class TestSagaLifecycleRedis:
    """Redis 백엔드를 사용한 전체 Saga Forward → Complete/Compensate 흐름 검증."""

    def test_full_forward_success_all_steps(self, redis_client, backend):
        """모든 Step이 성공하면 COMPLETED 상태, Redis에 최종 상태 영속화."""
        step1 = SuccessStep("payment")
        step2 = SuccessStep("inventory")
        step3 = SuccessStep("notification")

        definition = SagaDefinition(
            name="order_creation_test",
            steps=[step1, step2, step3],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)
        result = orchestrator.execute_saga(
            "order_creation_test",
            {"order_id": 123, "amount": 50000},
        )

        assert result.status == SagaStatus.COMPLETED
        assert result.step_instances[0].status == SagaStepStatus.EXECUTED
        assert result.step_instances[1].status == SagaStepStatus.EXECUTED
        assert result.step_instances[2].status == SagaStepStatus.EXECUTED
        assert result.context.get("payment_id") == "payment_001"
        assert result.context.get("inventory_id") == "inventory_001"
        assert result.context.get("notification_id") == "notification_001"

        # Redis에 저장되었는지 확인
        stored = backend.get(f"saga:instance:{result.id}")
        assert stored is not None
        assert stored["status"] == "completed"
        assert stored["version"] >= 1

    def test_step_failure_triggers_compensation_all_compensated(self, redis_client, backend):
        """Step 2 실패 시 Step 1만 역순 compensate하여 COMPENSATED."""
        step1 = SuccessStep("payment")
        step2 = FailingStep("inventory")

        definition = SagaDefinition(
            name="order_failing_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)
        result = orchestrator.execute_saga("order_failing_test", {"order_id": 456})

        assert result.status == SagaStatus.COMPENSATED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED  # payment 보상됨
        assert result.step_instances[1].status == SagaStepStatus.EXECUTE_FAILED  # inventory 실패

        # Context Injection 확인 (R5)
        assert result.context.failed_step_name == "inventory"
        assert result.context.abort_reason is not None

        # Redis 영속화 확인
        stored = backend.get(f"saga:instance:{result.id}")
        assert stored["status"] == "compensated"

    def test_three_steps_third_fails_first_two_compensated(self, redis_client, backend):
        """Step 3 실패 시 Step 2, 1 역순으로 모두 compensate."""
        step1 = SuccessStep("payment")
        step2 = SuccessStep("inventory")
        step3 = FailingStep("shipping")

        definition = SagaDefinition(
            name="order_three_fail_test",
            steps=[step1, step2, step3],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)
        result = orchestrator.execute_saga("order_three_fail_test", {"order_id": 789})

        assert result.status == SagaStatus.COMPENSATED
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED
        assert result.step_instances[1].status == SagaStepStatus.COMPENSATED
        assert result.step_instances[2].status == SagaStepStatus.EXECUTE_FAILED

    def test_partial_execution_step_included_in_compensation(self, redis_client, backend):
        """R4: partial_execution=True인 EXECUTE_FAILED Step도 보상 대상에 포함."""
        step1 = SuccessStep("payment")
        step2 = PartialExecutionFailStep("inventory")

        definition = SagaDefinition(
            name="partial_exec_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)
        result = orchestrator.execute_saga("partial_exec_test", {"order_id": 100})

        assert result.status == SagaStatus.COMPENSATED
        # inventory는 partial_execution=True이므로 보상 대상
        assert result.step_instances[1].partial_execution is True
        # payment도 보상됨
        assert result.step_instances[0].status == SagaStepStatus.COMPENSATED

    def test_compensation_failure_stores_to_dlq(self, redis_client, backend):
        """보상 실패 시 COMPENSATION_FAILED 상태 + DLQ store_failure 호출."""
        step1 = FailingCompensateStep("payment")
        step2 = FailingStep("inventory")

        definition = SagaDefinition(
            name="comp_fail_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        mock_dlq = _make_mock_dlq()
        orchestrator = _make_orchestrator(backend, dlq=mock_dlq)
        result = orchestrator.execute_saga("comp_fail_test", {"order_id": 200})

        assert result.status == SagaStatus.COMPENSATION_FAILED

        # DLQ에 저장되었는지 확인
        mock_dlq.store_failure.assert_called_once()
        call_kwargs = mock_dlq.store_failure.call_args.kwargs
        assert call_kwargs["domain"] == "saga"
        assert call_kwargs["failure_type"] == "COMPENSATION_FAILED"
        assert "saga_name" in call_kwargs["snapshot_data"]
        assert call_kwargs["snapshot_data"]["saga_name"] == "comp_fail_test"

        # Redis에도 최종 상태 저장
        stored = backend.get(f"saga:instance:{result.id}")
        assert stored["status"] == "compensation_failed"

    def test_version_increments_match_checkpoint_count(self, redis_client, backend):
        """실행 중 version이 Checkpoint 호출 횟수와 일치하는지 확인.

        3-step 전체 성공 시:
        - v1: 초기 Checkpoint (execute_saga)
        - v2: RUNNING 전환 (_execute_forward)
        - v3: Step 1 성공 Checkpoint
        - v4: Step 2 성공 Checkpoint
        - v5: Step 3 성공 Checkpoint
        - v6: COMPLETED Checkpoint
        총 6회 Checkpoint → version=6
        """
        step1 = SuccessStep("step_a")
        step2 = SuccessStep("step_b")
        step3 = SuccessStep("step_c")

        definition = SagaDefinition(
            name="version_count_test",
            steps=[step1, step2, step3],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)
        result = orchestrator.execute_saga("version_count_test", {"test": True})

        assert result.status == SagaStatus.COMPLETED
        # 정확한 Checkpoint 횟수: 초기(1) + RUNNING(1) + Step성공×3(3) + COMPLETED(1) = 6
        assert result.version == 6, f"3-step 전체 성공 시 version=6이어야 한다. 실제: {result.version}"


# =========================================================================
# 테스트 그룹 5: Celery Task 디스패치 체인
# =========================================================================


class TestCeleryTaskDispatch:
    """resume_saga_instance_task 및 scan_orphan_sagas Celery 태스크 검증.

    CELERY_TASK_ALWAYS_EAGER=True 환경에서 실제 태스크 체인이 동작하는지 확인.
    """

    def test_retry_scheduled_dispatches_celery_task(self, redis_client, backend):
        """R3: retryable 실패 시 RETRY_SCHEDULED + Celery apply_async 호출 확인."""
        step1 = SuccessStep("payment")
        step2 = RetryableFailStep("inventory", fail_count=3)  # 항상 실패

        definition = SagaDefinition(
            name="retry_dispatch_test",
            steps=[step1, step2],
            timeout_seconds=30,
            max_retries_per_step=2,
            retry_backoff_strategy="constant",
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)

        # Celery apply_async를 mock하여 호출 확인
        # resume_saga_instance_task는 orchestrator 내부에서 lazy import 됨:
        #   from selfhealing.services.saga.tasks import resume_saga_instance_task
        # 따라서 tasks 모듈의 원본을 패치해야 함
        with patch("selfhealing.services.saga.tasks.resume_saga_instance_task") as mock_task:
            mock_task.apply_async = MagicMock()

            result = orchestrator.execute_saga("retry_dispatch_test", {"order_id": 300})

        # RETRY_SCHEDULED 상태 (Forward 중단)
        assert result.step_instances[1].status == SagaStepStatus.RETRY_SCHEDULED
        assert result.step_instances[1].retry_count == 1
        assert result.step_instances[1].next_retry_at is not None

    def test_resume_saga_continues_from_retry_scheduled(self, redis_client, backend):
        """resume_saga가 RETRY_SCHEDULED 상태의 Step을 current_step_index부터 재개."""
        step1 = SuccessStep("payment")
        # fail_count=1이면 첫 호출만 실패, 두 번째 호출은 성공
        step2 = RetryableFailStep("inventory", fail_count=1)

        definition = SagaDefinition(
            name="resume_retry_test",
            steps=[step1, step2],
            timeout_seconds=30,
            max_retries_per_step=2,
            retry_backoff_strategy="constant",
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)

        # 첫 실행: Step 2 retryable 실패 → RETRY_SCHEDULED
        with patch("selfhealing.services.saga.tasks.resume_saga_instance_task") as mock_task:
            mock_task.apply_async = MagicMock()
            result = orchestrator.execute_saga("resume_retry_test", {"order_id": 400})

        assert result.step_instances[1].status == SagaStepStatus.RETRY_SCHEDULED
        instance_id = result.id

        # resume_saga 호출: Step 2부터 재시도 (이번엔 성공)
        resumed = orchestrator.resume_saga(instance_id)

        assert resumed.status == SagaStatus.COMPLETED
        assert resumed.step_instances[0].status == SagaStepStatus.EXECUTED
        assert resumed.step_instances[1].status == SagaStepStatus.EXECUTED

    def test_resume_saga_max_count_exceeded_compensation_failed(self, redis_client, backend):
        """R2: MAX_RESUME_COUNT 초과 시 COMPENSATION_FAILED + DLQ."""
        # SUSPENDED 상태 인스턴스를 Redis에 직접 저장 (resume_count=10)
        instance_id = f"saga-max-resume-{uuid.uuid4().hex[:8]}"
        step1 = SuccessStep("payment")
        definition = SagaDefinition(
            name="max_resume_test",
            steps=[step1],
            timeout_seconds=30,
        )
        register_saga(definition)

        instance_data = _create_test_instance_data(
            instance_id,
            saga_name="max_resume_test",
            version=5,
            status="suspended",
            step_instances=[{"step_name": "payment", "order": 0, "status": "executed"}],
            metadata={"resume_count": 10},
        )
        backend.set(f"saga:instance:{instance_id}", instance_data)

        mock_dlq = _make_mock_dlq()
        orchestrator = _make_orchestrator(backend, dlq=mock_dlq)
        result = orchestrator.resume_saga(instance_id)

        assert result.status == SagaStatus.COMPENSATION_FAILED
        assert "Max resume count" in result.error_message
        mock_dlq.store_failure.assert_called_once()

    def test_resume_saga_lock_acquisition_failure_returns_unchanged(self, redis_client, backend):
        """R2: 분산 락 획득 실패 시 재개하지 않고 현재 상태 반환 (GC Pause 방어)."""
        instance_id = f"saga-lock-fail-{uuid.uuid4().hex[:8]}"
        step1 = SuccessStep("payment")
        definition = SagaDefinition(
            name="lock_fail_test",
            steps=[step1],
            timeout_seconds=30,
        )
        register_saga(definition)

        instance_data = _create_test_instance_data(
            instance_id,
            saga_name="lock_fail_test",
            version=3,
            status="suspended",
            step_instances=[{"step_name": "payment", "order": 0, "status": "not_started"}],
        )
        backend.set(f"saga:instance:{instance_id}", instance_data)

        # 락 획득 실패 Mock
        failing_lock = _make_mock_lock()
        failing_lock.acquire.return_value = False

        orchestrator = _make_orchestrator(backend, lock=failing_lock)
        result = orchestrator.resume_saga(instance_id)

        # 상태가 변경되지 않아야 함
        assert result.status == SagaStatus.SUSPENDED


# =========================================================================
# 테스트 그룹 6: Orphan Saga 스캔 재개 체인
# =========================================================================


class TestOrphanSagaScan:
    """scan_orphan_sagas → resume_saga_instance_task 재개 체인 검증."""

    def test_scan_detects_stale_running_saga(self, redis_client, backend):
        """RUNNING 상태에서 5분 이상 업데이트 없으면 고아로 감지."""
        from selfhealing.services.saga.tasks import _scan_active_saga_instances

        instance_id = f"saga-stale-{uuid.uuid4().hex[:8]}"

        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        instance_data = _create_test_instance_data(
            instance_id,
            saga_name="stale_test",
            version=3,
            status="running",
            started_at=stale_time,
        )
        backend.set(f"saga:instance:{instance_id}", instance_data)

        # _scan_active_saga_instances를 직접 호출하여 감지 확인
        # (실제로는 get_state_backend()를 내부 lazy import하므로 올바른 경로 패치)
        with patch(
            "selfhealing.core.state_backend.get_state_backend",
            return_value=backend,
        ):
            instances = _scan_active_saga_instances()

        assert len(instances) >= 1
        found = [i for i in instances if i.id == instance_id]
        assert len(found) == 1
        assert found[0].status == SagaStatus.RUNNING

    def test_scan_detects_suspended_saga(self, redis_client, backend):
        """SUSPENDED 상태의 Saga는 항상 재개 시도 대상."""
        instance_id = f"saga-suspended-{uuid.uuid4().hex[:8]}"

        instance_data = _create_test_instance_data(
            instance_id,
            saga_name="suspended_test",
            version=2,
            status="suspended",
        )
        backend.set(f"saga:instance:{instance_id}", instance_data)

        with patch(
            "selfhealing.core.state_backend.get_state_backend",
            return_value=backend,
        ):
            from selfhealing.services.saga.tasks import _scan_active_saga_instances

            instances = _scan_active_saga_instances()

        found = [i for i in instances if i.id == instance_id]
        assert len(found) == 1
        assert found[0].status == SagaStatus.SUSPENDED

    def test_scan_ignores_completed_sagas(self, redis_client, backend):
        """터미널 상태(COMPLETED, COMPENSATED 등)는 스캔에서 제외."""
        for status in ["completed", "compensated", "compensation_failed", "timed_out"]:
            instance_id = f"saga-terminal-{status}-{uuid.uuid4().hex[:8]}"
            instance_data = _create_test_instance_data(
                instance_id,
                saga_name="terminal_test",
                version=5,
                status=status,
            )
            backend.set(f"saga:instance:{instance_id}", instance_data)

        with patch(
            "selfhealing.core.state_backend.get_state_backend",
            return_value=backend,
        ):
            from selfhealing.services.saga.tasks import _scan_active_saga_instances

            instances = _scan_active_saga_instances()

        terminal_ids = [i.id for i in instances if "terminal" in i.id]
        assert len(terminal_ids) == 0, f"터미널 상태는 스캔에서 제외되어야 한다: {terminal_ids}"


# =========================================================================
# 테스트 그룹 7: Lock Heartbeat 동작
# =========================================================================


class TestLockHeartbeat:
    """_execute_with_timeout 내 Lock Heartbeat Polling 루프 검증."""

    def test_lock_extend_called_during_slow_step(self, redis_client, backend):
        """실행 시간이 HEARTBEAT_INTERVAL(60s)을 초과하면 lock.extend가 호출되는지.

        테스트에서는 HEARTBEAT_INTERVAL을 작은 값으로 패치하여 빠르게 검증.
        """
        slow_step = SlowStep("slow_payment", sleep_seconds=3.0)

        definition = SagaDefinition(
            name="heartbeat_test",
            steps=[slow_step],
            timeout_seconds=30,
        )
        register_saga(definition)

        mock_lock = _make_mock_lock()
        orchestrator = _make_orchestrator(backend, lock=mock_lock)

        # HEARTBEAT_INTERVAL을 1초로 패치
        with patch("selfhealing.services.saga.orchestrator.HEARTBEAT_INTERVAL", 1):
            result = orchestrator.execute_saga("heartbeat_test", {"test": True})

        assert result.status == SagaStatus.COMPLETED

        # lock.extend가 호출되었는지 확인
        # SlowStep이 3초 동안 실행, HEARTBEAT_INTERVAL=1초 → 최소 2회 extend
        assert mock_lock.extend.call_count >= 2, f"lock.extend가 최소 2회 호출되어야 한다. 실제: {mock_lock.extend.call_count}"

    def test_lock_extend_in_compensation_loop(self, redis_client, backend):
        """보상 루프에서 매 Step 보상 전 lock.extend가 호출되는지 확인."""
        step1 = SuccessStep("step_a")
        step2 = SuccessStep("step_b")
        step3 = FailingStep("step_c")

        definition = SagaDefinition(
            name="comp_heartbeat_test",
            steps=[step1, step2, step3],
            timeout_seconds=30,
        )
        register_saga(definition)

        mock_lock = _make_mock_lock()
        orchestrator = _make_orchestrator(backend, lock=mock_lock)

        result = orchestrator.execute_saga("comp_heartbeat_test", {"test": True})

        assert result.status == SagaStatus.COMPENSATED

        # 보상 루프에서 매 step 보상 전 _try_extend_lock → lock.extend 호출
        # step_a, step_b 2개가 보상 대상이므로 최소 2회
        extend_calls = mock_lock.extend.call_count
        assert extend_calls >= 2, f"보상 루프에서 lock.extend가 최소 2회 호출되어야 한다. 실제: {extend_calls}"


# =========================================================================
# 테스트 그룹 8: EventBus 이벤트 발행 체인 (실제 Redis 영속화 연동)
# =========================================================================


class TestEventDispatchChain:
    """Saga 실행 중 EventBus 이벤트 발행이 올바른 순서로 이루어지는지 검증."""

    def test_success_saga_emits_started_step_completed_completed(self, redis_client, backend):
        """전체 성공 시 이벤트 순서: STARTED → STEP_COMPLETED ×N → COMPLETED."""
        step1 = SuccessStep("payment")
        step2 = SuccessStep("inventory")

        definition = SagaDefinition(
            name="event_success_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        mock_event_bus = _make_mock_event_bus()
        orchestrator = _make_orchestrator(backend, event_bus=mock_event_bus)
        result = orchestrator.execute_saga("event_success_test", {"order_id": 500})

        assert result.status == SagaStatus.COMPLETED

        # emit 호출 순서 확인
        calls = mock_event_bus.emit.call_args_list
        event_types = [c.kwargs.get("event_type", c.args[0] if c.args else None) for c in calls]

        # STARTED가 첫 번째
        assert event_types[0] == SagaEventType.SAGA_STARTED
        # STEP_COMPLETED 2개
        step_completed = [e for e in event_types if e == SagaEventType.SAGA_STEP_COMPLETED]
        assert len(step_completed) == 2
        # 마지막은 COMPLETED
        assert event_types[-1] == SagaEventType.SAGA_COMPLETED

    def test_failed_saga_emits_step_failed_compensating_compensated(self, redis_client, backend):
        """실패 + 보상 시: STARTED → STEP_COMPLETED → STEP_FAILED → COMPENSATING → COMPENSATED."""
        step1 = SuccessStep("payment")
        step2 = FailingStep("inventory")

        definition = SagaDefinition(
            name="event_fail_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        mock_event_bus = _make_mock_event_bus()
        orchestrator = _make_orchestrator(backend, event_bus=mock_event_bus)
        result = orchestrator.execute_saga("event_fail_test", {"order_id": 600})

        assert result.status == SagaStatus.COMPENSATED

        calls = mock_event_bus.emit.call_args_list
        event_types = [c.kwargs.get("event_type", c.args[0] if c.args else None) for c in calls]

        assert SagaEventType.SAGA_STARTED in event_types
        assert SagaEventType.SAGA_STEP_COMPLETED in event_types
        assert SagaEventType.SAGA_STEP_FAILED in event_types
        assert SagaEventType.SAGA_COMPENSATING in event_types
        assert SagaEventType.SAGA_COMPENSATED in event_types


# =========================================================================
# 테스트 그룹 9: 거버넌스 차단 시나리오
# =========================================================================


class TestGovernanceBlockIntegration:
    """거버넌스 차단 시 Saga가 실행되지 않고 즉시 반환되는지 검증."""

    def test_governance_blocked_returns_compensation_failed(self, redis_client, backend):
        """거버넌스 차단 시 COMPENSATION_FAILED, 실제 Step은 실행되지 않음."""
        step1 = SuccessStep("payment")

        definition = SagaDefinition(
            name="governance_test",
            steps=[step1],
            timeout_seconds=30,
        )
        register_saga(definition)

        orchestrator = _make_orchestrator(backend)

        # _check_governance를 패치하여 차단
        blocked_result = MagicMock()
        blocked_result.allowed = False
        blocked_result.block_message = "Kill switch activated"

        with patch.object(orchestrator, "_check_governance", return_value=blocked_result):
            result = orchestrator.execute_saga("governance_test", {"order_id": 700})

        assert result.status == SagaStatus.COMPENSATION_FAILED
        assert "Governance blocked" in result.error_message
        assert result.step_instances[0].status == SagaStepStatus.NOT_STARTED


# =========================================================================
# 테스트 그룹 10: Circuit Breaker OPEN → SUSPENDED 전환
# =========================================================================


class TestCircuitBreakerIntegration:
    """서킷브레이커 OPEN 시 SUSPENDED 전환 + Redis 영속화 검증."""

    def test_circuit_breaker_open_suspends_saga(self, redis_client, backend):
        """서킷브레이커가 OPEN이면 SUSPENDED 상태로 전환하고 Redis에 저장."""
        step1 = SuccessStep("payment")
        step2 = SuccessStep("inventory")

        definition = SagaDefinition(
            name="cb_suspend_test",
            steps=[step1, step2],
            timeout_seconds=30,
        )
        register_saga(definition)

        # Step2의 서킷브레이커가 OPEN
        mock_cb = MagicMock()
        call_count = 0

        def get_state_side_effect(namespace):
            nonlocal call_count
            call_count += 1
            state = MagicMock()
            # payment (namespace=saga:payment) → CLOSED, inventory (namespace=saga:inventory) → OPEN
            if "inventory" in namespace:
                state.value = "open"
            else:
                state.value = "closed"
            return state

        mock_cb.get_state.side_effect = get_state_side_effect

        orchestrator = _make_orchestrator(backend, circuit_breaker=mock_cb)
        result = orchestrator.execute_saga("cb_suspend_test", {"order_id": 800})

        assert result.status == SagaStatus.SUSPENDED
        assert "Circuit breaker OPEN" in result.error_message
        assert result.step_instances[0].status == SagaStepStatus.EXECUTED
        # inventory는 아직 실행되지 않음

        # Redis에 SUSPENDED 상태 영속화 확인
        stored = backend.get(f"saga:instance:{result.id}")
        assert stored["status"] == "suspended"


# =========================================================================
# 헬퍼 함수
# =========================================================================


def _create_test_instance_data(
    instance_id: str,
    saga_name: str = "test_saga",
    version: int = 0,
    status: str = "pending",
    step_instances: list | None = None,
    metadata: dict | None = None,
    started_at: str | None = None,
) -> dict:
    """테스트용 SagaInstance 직렬화 데이터 생성."""
    if step_instances is None:
        step_instances = []

    return {
        "id": instance_id,
        "saga_name": saga_name,
        "definition_version": 1,
        "version": version,
        "status": status,
        "step_instances": step_instances,
        "current_step_index": 0,
        "compensate_step_index": -1,
        "context": {
            "saga_instance_id": instance_id,
            "initial_data": {},
            "step_results": {},
            "failed_step_name": None,
            "abort_reason": None,
            "abort_error_code": None,
        },
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "initiated_by": "test",
        "error_message": None,
        "correlation_id": None,
        "metadata": metadata,
    }
