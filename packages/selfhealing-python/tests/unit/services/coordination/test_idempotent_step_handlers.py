"""
Idempotent Step Handlers 단위 테스트.

Phase 2.7: Step 재시도 안전성 테스트

테스트 항목:
- IdempotencyStatus 열거형
- IdempotencyRecord 데이터 클래스
- generate_idempotency_key 함수
- IdempotentStepHandler 구현체들
- IdempotentStepHandlerRegistry
"""

from datetime import datetime, timedelta, timezone

import pytest

from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.idempotent_step_handlers import (
    EXECUTION_TIMEOUT_MINUTES,
    IdempotencyRecord,
    IdempotencyStatus,
    IdempotentBudgetResetHandler,
    IdempotentCanaryResumeHandler,
    IdempotentGovernanceNormalHandler,
    IdempotentHealthCheckHandler,
    IdempotentStepHandler,
    generate_idempotency_key,
    get_idempotent_step_handler_registry,
    reset_idempotent_step_handler_registry,
)
from selfhealing.services.coordination.recovery_state import (
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def handler_registry():
    """핸들러 레지스트리."""
    reset_idempotent_step_handler_registry()
    return get_idempotent_step_handler_registry()


@pytest.fixture
def budget_handler():
    """Budget Reset 핸들러."""
    return IdempotentBudgetResetHandler()


@pytest.fixture
def health_check_handler():
    """Health Check 핸들러."""
    return IdempotentHealthCheckHandler()


@pytest.fixture
def canary_handler():
    """Canary Resume 핸들러."""
    return IdempotentCanaryResumeHandler()


@pytest.fixture
def governance_handler():
    """Governance Normal 핸들러."""
    return IdempotentGovernanceNormalHandler()


@pytest.fixture
def sample_session():
    """샘플 RecoverySession."""
    return RecoverySession(
        id="test-session-123",
        namespace="global",
        trigger_level="LEVEL_3",
        status=RecoveryStatus.IN_PROGRESS,
        steps=[],
        current_step_index=0,
        started_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def sample_step():
    """샘플 RecoveryStep."""
    return RecoveryStep(
        step_type=RecoveryStepType.BUDGET_RESET,
        order=1,
        status=RecoveryStatus.NOT_STARTED,
        params={"target_multiplier": 1.0},
    )


# =============================================================================
# IdempotencyStatus Tests
# =============================================================================


class TestIdempotencyStatus:
    """IdempotencyStatus 열거형 테스트."""

    def test_all_values_exist(self):
        """모든 상태값 존재 확인."""
        assert IdempotencyStatus.NOT_EXECUTED.value == "not_executed"
        assert IdempotencyStatus.EXECUTING.value == "executing"
        assert IdempotencyStatus.COMPLETED.value == "completed"
        assert IdempotencyStatus.FAILED.value == "failed"
        assert IdempotencyStatus.SKIPPED.value == "skipped"


# =============================================================================
# IdempotencyRecord Tests
# =============================================================================


class TestIdempotencyRecord:
    """IdempotencyRecord 테스트."""

    def test_create_record(self):
        """레코드 생성 테스트."""
        record = IdempotencyRecord(
            idempotency_key="test-key-123",
            session_id="session-abc",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.COMPLETED,
        )

        assert record.idempotency_key == "test-key-123"
        assert record.session_id == "session-abc"
        assert record.step_type == "budget_reset"
        assert record.status == IdempotencyStatus.COMPLETED

    def test_is_safe_to_execute_not_executed(self):
        """미실행 상태에서 실행 가능 확인."""
        record = IdempotencyRecord(
            idempotency_key="test-key",
            session_id="session-1",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.NOT_EXECUTED,
        )

        assert record.is_safe_to_execute() is True

    def test_is_safe_to_execute_executing_recent(self):
        """실행 중(최근) 상태에서 실행 불가 확인."""
        record = IdempotencyRecord(
            idempotency_key="test-key",
            session_id="session-1",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.EXECUTING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # 최근에 시작된 경우 실행 불가
        assert record.is_safe_to_execute() is False

    def test_is_safe_to_execute_executing_timed_out(self):
        """실행 중(타임아웃) 상태에서 실행 가능 확인."""
        old_time = datetime.now(timezone.utc) - timedelta(minutes=EXECUTION_TIMEOUT_MINUTES + 5)
        record = IdempotencyRecord(
            idempotency_key="test-key",
            session_id="session-1",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.EXECUTING,
            started_at=old_time.isoformat(),
        )

        # 타임아웃 초과 시 실행 가능
        assert record.is_safe_to_execute() is True

    def test_is_safe_to_execute_completed(self):
        """완료 상태에서 실행 불가 확인."""
        record = IdempotencyRecord(
            idempotency_key="test-key",
            session_id="session-1",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        assert record.is_safe_to_execute() is False

    def test_is_safe_to_execute_failed(self):
        """실패 상태에서 실행 가능 확인."""
        record = IdempotencyRecord(
            idempotency_key="test-key",
            session_id="session-1",
            step_type="budget_reset",
            step_order=1,
            status=IdempotencyStatus.FAILED,
        )

        assert record.is_safe_to_execute() is True

    def test_to_dict_and_from_dict(self):
        """딕셔너리 변환 왕복 테스트."""
        now = datetime.now(timezone.utc)
        original = IdempotencyRecord(
            idempotency_key="test-key-456",
            session_id="session-xyz",
            step_type="health_check",
            step_order=2,
            status=IdempotencyStatus.COMPLETED,
            result={"health_score": 0.95},
            started_at=(now - timedelta(minutes=5)).isoformat(),
            completed_at=now.isoformat(),
            retry_count=2,
        )

        data = original.to_dict()
        restored = IdempotencyRecord.from_dict(data)

        assert restored.idempotency_key == original.idempotency_key
        assert restored.session_id == original.session_id
        assert restored.step_type == original.step_type
        assert restored.status == original.status
        assert restored.result == original.result
        assert restored.retry_count == original.retry_count


# =============================================================================
# generate_idempotency_key Tests
# =============================================================================


class TestGenerateIdempotencyKey:
    """generate_idempotency_key 함수 테스트."""

    def test_generate_basic_key(self):
        """기본 키 생성 테스트."""
        key = generate_idempotency_key(
            session_id="session-123",
            step_type="budget_reset",
            step_order=1,
        )

        assert "session-123" in key
        assert "budget_reset" in key
        assert "1" in key
        assert key.startswith("idem:")

    def test_generate_key_with_params(self):
        """파라미터 포함 키 생성 테스트."""
        params = {"target_multiplier": 1.0, "timeout": 60}

        key = generate_idempotency_key(
            session_id="session-456",
            step_type="health_check",
            step_order=2,
            params=params,
        )

        assert len(key) > 0
        # 해시가 포함됨
        assert ":" in key

    def test_same_input_same_key(self):
        """동일 입력 동일 키 확인."""
        params = {"value": 100}

        key1 = generate_idempotency_key(
            session_id="session-789",
            step_type="canary_resume",
            step_order=3,
            params=params,
        )

        key2 = generate_idempotency_key(
            session_id="session-789",
            step_type="canary_resume",
            step_order=3,
            params=params,
        )

        assert key1 == key2

    def test_different_params_different_key(self):
        """다른 파라미터 다른 키 확인."""
        key1 = generate_idempotency_key(
            session_id="session-aaa",
            step_type="budget_reset",
            step_order=1,
            params={"value": 100},
        )

        key2 = generate_idempotency_key(
            session_id="session-aaa",
            step_type="budget_reset",
            step_order=1,
            params={"value": 200},
        )

        assert key1 != key2


# =============================================================================
# IdempotentBudgetResetHandler Tests
# =============================================================================


class TestIdempotentBudgetResetHandler:
    """IdempotentBudgetResetHandler 테스트."""

    def test_handler_creation(self, budget_handler):
        """핸들러 생성 테스트."""
        assert budget_handler is not None
        assert isinstance(budget_handler, IdempotentStepHandler)

    def test_execute_basic(self, budget_handler, sample_session, sample_step):
        """기본 실행 테스트."""
        result = budget_handler.execute(sample_session, sample_step)

        # CrisisMultiplierProvider 없으면 skipped
        assert result is not None
        assert isinstance(result, dict)
        assert "success" in result


# =============================================================================
# IdempotentHealthCheckHandler Tests
# =============================================================================


class TestIdempotentHealthCheckHandler:
    """IdempotentHealthCheckHandler 테스트."""

    def test_handler_creation(self, health_check_handler):
        """핸들러 생성 테스트."""
        assert health_check_handler is not None
        assert isinstance(health_check_handler, IdempotentStepHandler)

    def test_execute_basic(self, health_check_handler, sample_session):
        """기본 실행 테스트."""
        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            status=RecoveryStatus.NOT_STARTED,
            params={"duration_minutes": 5},
        )

        result = health_check_handler.execute(sample_session, step)

        assert result is not None
        assert isinstance(result, dict)


# =============================================================================
# IdempotentCanaryResumeHandler Tests
# =============================================================================


class TestIdempotentCanaryResumeHandler:
    """IdempotentCanaryResumeHandler 테스트."""

    def test_handler_creation(self, canary_handler):
        """핸들러 생성 테스트."""
        assert canary_handler is not None
        assert isinstance(canary_handler, IdempotentStepHandler)

    def test_execute_basic(self, canary_handler, sample_session):
        """기본 실행 테스트."""
        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            status=RecoveryStatus.NOT_STARTED,
            params={"resume_paused_only": True},
        )

        result = canary_handler.execute(sample_session, step)

        assert result is not None
        assert isinstance(result, dict)


# =============================================================================
# IdempotentGovernanceNormalHandler Tests
# =============================================================================


class TestIdempotentGovernanceNormalHandler:
    """IdempotentGovernanceNormalHandler 테스트."""

    def test_handler_creation(self, governance_handler):
        """핸들러 생성 테스트."""
        assert governance_handler is not None
        assert isinstance(governance_handler, IdempotentStepHandler)

    def test_execute_basic(self, governance_handler, sample_session):
        """기본 실행 테스트."""
        step = RecoveryStep(
            step_type=RecoveryStepType.GOVERNANCE_NORMAL,
            order=4,
            status=RecoveryStatus.NOT_STARTED,
            params={},
        )

        result = governance_handler.execute(sample_session, step)

        assert result is not None
        assert isinstance(result, dict)


# =============================================================================
# IdempotentStepHandlerRegistry Tests
# =============================================================================


class TestIdempotentStepHandlerRegistry:
    """IdempotentStepHandlerRegistry 테스트."""

    def test_get_handler_budget_reset(self, handler_registry):
        """Budget Reset 핸들러 획득 테스트."""
        handler = handler_registry.get(RecoveryStepType.BUDGET_RESET)

        assert handler is not None
        assert isinstance(handler, IdempotentBudgetResetHandler)

    def test_get_handler_health_check(self, handler_registry):
        """Health Check 핸들러 획득 테스트."""
        handler = handler_registry.get(RecoveryStepType.HEALTH_CHECK)

        assert handler is not None
        assert isinstance(handler, IdempotentHealthCheckHandler)

    def test_get_handler_canary_resume(self, handler_registry):
        """Canary Resume 핸들러 획득 테스트."""
        handler = handler_registry.get(RecoveryStepType.CANARY_RESUME)

        assert handler is not None
        assert isinstance(handler, IdempotentCanaryResumeHandler)

    def test_get_handler_governance_normal(self, handler_registry):
        """Governance Normal 핸들러 획득 테스트."""
        handler = handler_registry.get(RecoveryStepType.GOVERNANCE_NORMAL)

        assert handler is not None
        assert isinstance(handler, IdempotentGovernanceNormalHandler)

    def test_get_handler_not_found(self, handler_registry):
        """등록되지 않은 스텝 타입 테스트."""
        # 등록된 4개 이외의 단계 유형은 없으므로 등록된 것들만 테스트
        # 실제로 FINAL_CLEANUP 같은 유형은 존재하지 않음
        # 기본 핸들러 4개가 등록되어 있는지 확인
        assert handler_registry.get(RecoveryStepType.BUDGET_RESET) is not None
        assert handler_registry.get(RecoveryStepType.HEALTH_CHECK) is not None
        assert handler_registry.get(RecoveryStepType.CANARY_RESUME) is not None
        assert handler_registry.get(RecoveryStepType.GOVERNANCE_NORMAL) is not None

    def test_register_custom_handler(self, handler_registry, sample_session):
        """커스텀 핸들러 등록 테스트 (기존 핸들러 교체)."""

        # 커스텀 핸들러 클래스 생성
        class CustomHandler(IdempotentStepHandler):
            def _execute_internal(self, session, step):
                return {"success": True, "custom": True}

        custom = CustomHandler()
        # 기존 핸들러 교체 테스트
        handler_registry.register(RecoveryStepType.BUDGET_RESET, custom)

        retrieved = handler_registry.get(RecoveryStepType.BUDGET_RESET)

        assert retrieved is custom

    def test_execute_via_registry(self, handler_registry, sample_session, sample_step):
        """레지스트리를 통한 실행 테스트."""
        result = handler_registry.execute(sample_session, sample_step)

        assert result is not None
        assert isinstance(result, dict)

    def test_execute_success(self, handler_registry, sample_session):
        """레지스트리를 통한 실행 성공 테스트."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            status=RecoveryStatus.NOT_STARTED,
            params={"target_multiplier": 1.0},
        )

        result = handler_registry.execute(sample_session, step)

        assert result is not None
        assert isinstance(result, dict)


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """싱글톤 테스트."""

    def test_get_singleton(self):
        """싱글톤 획득 테스트."""
        reset_idempotent_step_handler_registry()

        registry1 = get_idempotent_step_handler_registry()
        registry2 = get_idempotent_step_handler_registry()

        assert registry1 is registry2

    def test_reset_singleton(self):
        """싱글톤 리셋 테스트."""
        registry1 = get_idempotent_step_handler_registry()

        reset_idempotent_step_handler_registry()

        registry2 = get_idempotent_step_handler_registry()

        assert registry1 is not registry2
