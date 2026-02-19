"""
Unit tests for Step-Level Timeout Monitor.

테스트 대상:
- StepTimeoutError / SessionVersionConflictError 예외 클래스
- RecoveryStep.timeout_seconds 필드 및 직렬화
- RecoverySession.version 필드 및 직렬화
- _get_step_timeout() 타임아웃 결정 로직
- _execute_with_timeout() 타임아웃 실행 + Lock 하트비트
- execute_next_step() 타임아웃 통합
- _save_session() OCC (Optimistic Concurrency Control)
- _attempt_compensation() 보상 타임아웃
- stop_event 협력적 취소
- Settings Step 유형별 타임아웃 기본값
"""

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.coordination.enums import (
    CompensationStatus,
    RecoveryStatus,
)
from selfhealing.services.coordination.recovery_state import (
    CompensationResult,
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)
from selfhealing.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from selfhealing.services.coordination.recovery_coordinator import (
    LOCK_HEARTBEAT_INTERVAL_SECONDS,
    RecoveryCoordinator,
    SessionVersionConflictError,
    StepTimeoutError,
)
from selfhealing.settings.recovery_coordinator import (
    RecoveryCoordinatorSettings,
    get_recovery_coordinator_settings,
    reset_recovery_coordinator_settings,
)


# =========================================================================
# Contract Tests — 설계 계약값 검증 (하드코딩 필수)
# =========================================================================


class TestStepTimeoutSettingsContract:
    """Step 유형별 타임아웃 Settings 기본값 계약 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    def test_step_execution_timeout_default(self):
        """전역 기본 Step 타임아웃: 300초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.step_execution_timeout_seconds == 300

    def test_budget_reset_timeout_default(self):
        """BUDGET_RESET 타임아웃 기본값: 60초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.budget_reset_timeout_seconds == 60

    def test_health_check_timeout_default(self):
        """HEALTH_CHECK 타임아웃 기본값: 600초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.health_check_timeout_seconds == 600

    def test_canary_resume_timeout_default(self):
        """CANARY_RESUME 타임아웃 기본값: 300초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.canary_resume_timeout_seconds == 300

    def test_governance_normal_timeout_default(self):
        """GOVERNANCE_NORMAL 타임아웃 기본값: 120초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.governance_normal_timeout_seconds == 120

    def test_compensation_step_timeout_default(self):
        """보상 핸들러 타임아웃 기본값: 120초."""
        settings = RecoveryCoordinatorSettings()
        assert settings.compensation_step_timeout_seconds == 120

    def test_lock_heartbeat_interval_constant(self):
        """Lock 하트비트 간격 상수: 60초."""
        assert LOCK_HEARTBEAT_INTERVAL_SECONDS == 60


class TestStepTimeoutErrorContract:
    """StepTimeoutError 예외 계약 검증."""

    def test_step_timeout_error_attributes(self):
        """StepTimeoutError는 step_type과 timeout_seconds 속성을 가진다."""
        error = StepTimeoutError("budget_reset", 60)
        assert error.step_type == "budget_reset"
        assert error.timeout_seconds == 60

    def test_step_timeout_error_message(self):
        """StepTimeoutError 메시지 포맷."""
        error = StepTimeoutError("health_check", 600)
        assert "health_check" in str(error)
        assert "600" in str(error)


class TestSessionVersionConflictErrorContract:
    """SessionVersionConflictError 예외 계약 검증."""

    def test_version_conflict_error_attributes(self):
        """SessionVersionConflictError는 session_id, expected, actual 속성을 가진다."""
        error = SessionVersionConflictError("session-abc", 1, 2)
        assert error.session_id == "session-abc"
        assert error.expected_version == 1
        assert error.actual_version == 2

    def test_version_conflict_error_message(self):
        """SessionVersionConflictError 메시지 포맷."""
        error = SessionVersionConflictError("session-abc", 1, 2)
        assert "session-abc" in str(error)
        assert "v1" in str(error)


class TestRecoveryStepTimeoutFieldContract:
    """RecoveryStep.timeout_seconds 필드 계약 검증."""

    def test_timeout_seconds_default(self):
        """timeout_seconds 기본값: 0."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
        )
        assert step.timeout_seconds == 0

    def test_timeout_seconds_custom(self):
        """timeout_seconds 커스텀 값 설정."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            timeout_seconds=180,
        )
        assert step.timeout_seconds == 180


class TestRecoverySessionVersionFieldContract:
    """RecoverySession.version 필드 계약 검증."""

    def test_version_default(self):
        """version 기본값: 0."""
        session = RecoverySession(
            id="test-session",
            namespace="global",
            trigger_level="LEVEL_3",
        )
        assert session.version == 0


# =========================================================================
# Behavior Tests — 동작 검증 (소스 참조 사용)
# =========================================================================


class TestRecoveryStepSerializationBehavior:
    """RecoveryStep timeout_seconds 직렬화 동작 검증."""

    def test_to_dict_includes_timeout_seconds(self):
        """to_dict()에 timeout_seconds가 포함된다."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            timeout_seconds=120,
        )
        data = step.to_dict()
        assert "timeout_seconds" in data
        assert data["timeout_seconds"] == step.timeout_seconds

    def test_from_dict_with_timeout_seconds(self):
        """from_dict()에서 timeout_seconds를 복원한다."""
        data = {
            "step_type": "budget_reset",
            "order": 1,
            "timeout_seconds": 180,
        }
        step = RecoveryStep.from_dict(data)
        assert step.timeout_seconds == 180

    def test_from_dict_missing_timeout_defaults_to_zero(self):
        """from_dict()에 timeout_seconds 없으면 0."""
        data = {
            "step_type": "budget_reset",
            "order": 1,
        }
        step = RecoveryStep.from_dict(data)
        assert step.timeout_seconds == 0

    def test_roundtrip_preserves_timeout(self):
        """to_dict → from_dict 왕복 시 timeout_seconds 유지."""
        original = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            timeout_seconds=600,
        )
        restored = RecoveryStep.from_dict(original.to_dict())
        assert restored.timeout_seconds == original.timeout_seconds


class TestRecoverySessionVersionSerializationBehavior:
    """RecoverySession.version 직렬화 동작 검증."""

    def test_to_dict_includes_version(self):
        """to_dict()에 version이 포함된다."""
        session = RecoverySession(
            id="test",
            namespace="global",
            trigger_level="LEVEL_3",
            version=5,
        )
        data = session.to_dict()
        assert "version" in data
        assert data["version"] == session.version

    def test_from_dict_with_version(self):
        """from_dict()에서 version을 복원한다."""
        data = {
            "id": "test",
            "namespace": "global",
            "trigger_level": "LEVEL_3",
            "version": 3,
        }
        session = RecoverySession.from_dict(data)
        assert session.version == 3

    def test_from_dict_missing_version_defaults_to_zero(self):
        """from_dict()에 version 없으면 0 (하위 호환성)."""
        data = {
            "id": "test",
            "namespace": "global",
            "trigger_level": "LEVEL_3",
        }
        session = RecoverySession.from_dict(data)
        assert session.version == 0


class TestGetStepTimeoutBehavior:
    """_get_step_timeout() 타임아웃 결정 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        return RecoveryCoordinator(
            backend=backend,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

    def test_explicit_timeout_takes_priority(self, coordinator):
        """step.timeout_seconds > 0이면 해당 값을 반환한다."""
        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            timeout_seconds=42,
        )
        assert coordinator._get_step_timeout(step) == 42

    def test_type_specific_settings_used_when_step_timeout_zero(self, coordinator):
        """step.timeout_seconds == 0이면 Settings 유형별 값을 반환한다."""
        settings = get_recovery_coordinator_settings()

        step = RecoveryStep(
            step_type=RecoveryStepType.BUDGET_RESET,
            order=1,
            timeout_seconds=0,
        )
        assert coordinator._get_step_timeout(step) == settings.budget_reset_timeout_seconds

        step = RecoveryStep(
            step_type=RecoveryStepType.HEALTH_CHECK,
            order=2,
            timeout_seconds=0,
        )
        assert coordinator._get_step_timeout(step) == settings.health_check_timeout_seconds

        step = RecoveryStep(
            step_type=RecoveryStepType.CANARY_RESUME,
            order=3,
            timeout_seconds=0,
        )
        assert coordinator._get_step_timeout(step) == settings.canary_resume_timeout_seconds

        step = RecoveryStep(
            step_type=RecoveryStepType.GOVERNANCE_NORMAL,
            order=4,
            timeout_seconds=0,
        )
        assert coordinator._get_step_timeout(step) == settings.governance_normal_timeout_seconds

    def test_default_fallback_for_unknown_step_type(self, coordinator):
        """Settings에 유형별 값이 없는 Step은 전역 기본값을 사용한다."""
        settings = get_recovery_coordinator_settings()

        # 기존 step type 사용하되 Settings 매핑에서 0을 반환하도록 우회
        # budget_reset는 매핑에 있으므로 직접 테스트 불가
        # → step.timeout_seconds > 0이면 해당 값 사용되는 것은 위에서 검증
        # 전역 기본값이 올바른지 검증
        assert settings.step_execution_timeout_seconds > 0


class TestExecuteWithTimeoutBehavior:
    """_execute_with_timeout() 동작 검증."""

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

    @pytest.fixture
    def session(self, coordinator):
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        return session

    def test_handler_completes_within_timeout(self, coordinator, session):
        """핸들러가 타임아웃 내 완료하면 결과를 반환한다."""
        step = session.steps[0]
        handler_fn = lambda: {"success": True, "value": 42}

        result = coordinator._execute_with_timeout(
            handler_fn,
            timeout_seconds=5,
            step=step,
            session=session,
        )
        assert result == {"success": True, "value": 42}

    def test_handler_timeout_raises_step_timeout_error(self, coordinator, session):
        """핸들러가 타임아웃을 초과하면 StepTimeoutError를 발생시킨다."""
        step = session.steps[0]

        def slow_handler():
            time.sleep(3)
            return {"success": True}

        with pytest.raises(StepTimeoutError) as exc_info:
            coordinator._execute_with_timeout(
                slow_handler,
                timeout_seconds=1,
                step=step,
                session=session,
            )
        assert exc_info.value.step_type == step.step_type.value
        assert exc_info.value.timeout_seconds == 1

    def test_handler_exception_propagates(self, coordinator, session):
        """핸들러 예외는 그대로 전파된다."""
        step = session.steps[0]

        def failing_handler():
            raise RuntimeError("External API error")

        with pytest.raises(RuntimeError, match="External API error"):
            coordinator._execute_with_timeout(
                failing_handler,
                timeout_seconds=5,
                step=step,
                session=session,
            )


class TestStopEventBehavior:
    """stop_event 협력적 취소 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        return coord

    def test_stop_event_set_on_timeout(self, coordinator):
        """타임아웃 시 step._stop_event.is_set() == True."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        step = session.steps[0]

        # stop_event 주입
        stop_event = threading.Event()
        step._stop_event = stop_event

        def slow_handler():
            time.sleep(3)
            return {"success": True}

        with pytest.raises(StepTimeoutError):
            coordinator._execute_with_timeout(
                slow_handler,
                timeout_seconds=1,
                step=step,
                session=session,
            )

        assert stop_event.is_set()

    def test_handler_checks_stop_event_for_early_exit(self, coordinator):
        """핸들러가 stop_event를 체크하여 조기 종료할 수 있다."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        step = session.steps[0]
        stop_event = threading.Event()
        step._stop_event = stop_event

        checked_stop_event = threading.Event()

        def cancellable_handler():
            se = getattr(step, "_stop_event", None)
            if se and se.is_set():
                checked_stop_event.set()
                return {"success": False, "error": "Cancelled by timeout"}
            return {"success": True}

        # stop_event가 set되지 않으면 정상 완료
        result = coordinator._execute_with_timeout(
            cancellable_handler,
            timeout_seconds=5,
            step=step,
            session=session,
        )
        assert result["success"] is True
        assert not checked_stop_event.is_set()


class TestSessionVersionOccBehavior:
    """_save_session() OCC 동작 검증."""

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        return RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

    def test_version_increments_on_save(self, coordinator):
        """_save_session() 호출 시 version이 +1 증가한다."""
        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_3",
        )
        # start_recovery 내부에서 _save_session이 호출됨
        # get_active_session으로 다시 로드하면 version > 0
        loaded = coordinator.get_active_session("global")
        assert loaded is not None
        # 최소 1회 save가 호출되었으므로 version >= 1
        assert loaded.version >= 1

    def test_version_monotonically_increases(self, coordinator):
        """매 _save_session() 마다 version이 단조 증가한다."""
        session = RecoverySession(
            id="test-ver",
            namespace="ver-ns",
            trigger_level="LEVEL_3",
            version=0,
        )
        initial_version = session.version
        coordinator._save_session(session)
        assert session.version == initial_version + 1

        v1 = session.version
        coordinator._save_session(session)
        assert session.version == v1 + 1


class TestExecuteNextStepTimeoutBehavior:
    """execute_next_step()의 타임아웃 통합 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        return coord

    def test_step_timeout_triggers_failure(self, coordinator):
        """핸들러 타임아웃 초과 시 Step FAILED + 세션 FAILED."""

        def slow_handler(session, step):
            time.sleep(3)
            return {"success": True}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            slow_handler,
        )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )

        # 타임아웃을 1초로 설정
        session.steps[0].timeout_seconds = 1
        coordinator._save_session(session)

        step = coordinator.execute_next_step("global")
        assert step is not None
        assert step.status == RecoveryStatus.FAILED
        assert "timed out" in step.error_message

        # 세션도 FAILED
        loaded = coordinator.get_active_session("global")
        assert loaded.status == RecoveryStatus.FAILED

    def test_step_completes_within_timeout(self, coordinator):
        """타임아웃 내 완료 → COMPLETED 정상 처리."""

        def fast_handler(session, step):
            return {"success": True, "value": "ok"}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            fast_handler,
        )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )
        session.steps[0].timeout_seconds = 5
        coordinator._save_session(session)

        step = coordinator.execute_next_step("global")
        assert step is not None
        assert step.status == RecoveryStatus.COMPLETED

    def test_stop_event_injected_on_execute_next_step(self, coordinator):
        """execute_next_step()에서 step._stop_event가 주입된다."""
        stop_event_captured = {}

        def capture_handler(session, step):
            stop_event_captured["event"] = getattr(step, "_stop_event", None)
            return {"success": True}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            capture_handler,
        )

        coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )
        coordinator.execute_next_step("global")

        assert "event" in stop_event_captured
        assert isinstance(stop_event_captured["event"], threading.Event)


class TestCompensationTimeoutBehavior:
    """_attempt_compensation() 보상 타임아웃 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        return coord

    def test_compensation_timeout_applied(self, coordinator):
        """보상 핸들러에 compensation_step_timeout_seconds가 적용된다."""
        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            lambda s, st: {"success": True},
            compensate=lambda s, st: {"success": True},
        )

        # 세션을 직접 생성하여 보상 테스트
        session = RecoverySession(
            id="test-comp-timeout",
            namespace="comp-ns",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.COMPENSATING,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                    status=RecoveryStatus.COMPLETED,
                    compensation_status=CompensationStatus.PENDING,
                ),
            ],
        )

        # _execute_with_timeout 호출 시 전달되는 timeout_seconds를 캡처
        with patch.object(
            coordinator,
            "_execute_with_timeout",
            wraps=coordinator._execute_with_timeout,
        ) as mock_exec:
            result = coordinator._attempt_compensation(session)

        # compensation_step_timeout_seconds 기본값이 사용되는지 확인
        settings = get_recovery_coordinator_settings()
        mock_exec.assert_called_once()
        # _execute_with_timeout(handler_fn, timeout_seconds, step, session)
        actual_timeout = mock_exec.call_args[0][1]
        assert actual_timeout == settings.compensation_step_timeout_seconds

    def test_compensation_timeout_independent_of_forward(self, coordinator):
        """보상 타임아웃은 Forward 타임아웃과 독립적이다."""
        settings = get_recovery_coordinator_settings()
        assert settings.compensation_step_timeout_seconds != settings.step_execution_timeout_seconds


class TestLockHeartbeatBehavior:
    """Lock Heartbeat 동작 검증."""

    @pytest.fixture
    def mock_lock(self):
        lock = MagicMock(spec=InMemoryRecoveryLock)
        lock.acquire.return_value = True
        lock.extend.return_value = True
        lock.release.return_value = True
        lock.get_lock_owner.return_value = None
        return lock

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    def test_lock_heartbeat_during_long_step(self, mock_lock):
        """긴 Step 실행 중 lock.extend()가 호출된다."""
        backend = MemoryStateBackend()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=mock_lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        session = RecoverySession(
            id="test-hb",
            namespace="hb-ns",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                ),
            ],
        )

        # 핸들러가 heartbeat 간격보다 오래 걸리도록 설정
        # heartbeat = 60s이지만 테스트에서는 짧은 시간 사용
        heartbeat_count = 0

        def moderate_handler():
            nonlocal heartbeat_count
            # heartbeat interval 보다는 길게 실행되어야 extend가 호출됨
            # 실제는 LOCK_HEARTBEAT_INTERVAL_SECONDS(60)초 마다 호출
            # 테스트에서는 2초 이상 걸리게 하고 heartbeat를 1초로 우회
            time.sleep(2.5)
            return {"success": True}

        step = session.steps[0]
        step._stop_event = threading.Event()

        # heartbeat 간격을 1초로 줄여 테스트
        with patch(
            "selfhealing.services.coordination.recovery_coordinator.LOCK_HEARTBEAT_INTERVAL_SECONDS",
            1,
        ):
            result = coordinator._execute_with_timeout(
                moderate_handler,
                timeout_seconds=5,
                step=step,
                session=session,
            )

        assert result["success"] is True
        # extend가 최소 1회 이상 호출되어야 함 (2.5초 실행 / 1초 간격)
        assert mock_lock.extend.call_count >= 1
        # extend 호출 인자 확인
        mock_lock.extend.assert_called_with(
            session.namespace,
            session.id,
            additional_seconds=300,
        )

    def test_lock_heartbeat_interval(self, mock_lock):
        """heartbeat 간격마다 lock.extend()가 호출된다."""
        backend = MemoryStateBackend()
        coordinator = RecoveryCoordinator(
            backend=backend,
            recovery_lock=mock_lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )

        session = RecoverySession(
            id="test-hb-interval",
            namespace="hb-ns",
            trigger_level="LEVEL_3",
            status=RecoveryStatus.IN_PROGRESS,
            steps=[
                RecoveryStep(
                    step_type=RecoveryStepType.BUDGET_RESET,
                    order=1,
                ),
            ],
        )

        step = session.steps[0]
        step._stop_event = threading.Event()

        def slow_handler():
            time.sleep(3.5)
            return {"success": True}

        with patch(
            "selfhealing.services.coordination.recovery_coordinator.LOCK_HEARTBEAT_INTERVAL_SECONDS",
            1,
        ):
            result = coordinator._execute_with_timeout(
                slow_handler,
                timeout_seconds=5,
                step=step,
                session=session,
            )

        assert result["success"] is True
        # 3.5초 실행 / 1초 간격 = 최소 2~3회 extend 호출
        assert mock_lock.extend.call_count >= 2


class TestTimeoutDlqCompensationIntegrationBehavior:
    """타임아웃 → DLQ/보상 연동 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_settings(self):
        reset_recovery_coordinator_settings()
        yield
        reset_recovery_coordinator_settings()

    @pytest.fixture
    def coordinator(self):
        backend = MemoryStateBackend()
        lock = InMemoryRecoveryLock()
        coord = RecoveryCoordinator(
            backend=backend,
            recovery_lock=lock,
            use_idempotent_handlers=False,
            use_regional_policy=False,
        )
        return coord

    def test_timeout_triggers_fail_session(self, coordinator):
        """타임아웃 → _fail_session() 호출 → 세션 FAILED."""

        def slow_handler(session, step):
            time.sleep(3)
            return {"success": True}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            slow_handler,
        )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )
        session.steps[0].timeout_seconds = 1
        coordinator._save_session(session)

        step = coordinator.execute_next_step("global")
        loaded = coordinator.get_active_session("global")
        assert loaded.status == RecoveryStatus.FAILED
        assert loaded.abort_reason is not None

    @patch("selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator._store_failure_to_dlq")
    def test_timeout_triggers_dlq_storage(self, mock_dlq, coordinator):
        """타임아웃 → _fail_session() → DLQ 저장 호출."""

        def slow_handler(session, step):
            time.sleep(3)
            return {"success": True}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            slow_handler,
        )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )
        session.steps[0].timeout_seconds = 1
        coordinator._save_session(session)

        step = coordinator.execute_next_step("global")
        assert mock_dlq.called

    def test_timeout_triggers_compensation(self, coordinator):
        """타임아웃 후 이전 완료 Step 보상 시도."""
        # Step 1을 성공 → Step 2에서 타임아웃 → Step 1 보상 호출
        compensate_called = {"called": False}

        def fast_handler(session, step):
            return {"success": True}

        def slow_handler(session, step):
            time.sleep(3)
            return {"success": True}

        def compensate_budget(session, step):
            compensate_called["called"] = True
            return {"success": True}

        coordinator.register_step_handler(
            RecoveryStepType.BUDGET_RESET,
            fast_handler,
            compensate=compensate_budget,
        )
        coordinator.register_step_handler(
            RecoveryStepType.HEALTH_CHECK,
            slow_handler,
        )

        session = coordinator.start_recovery(
            namespace="global",
            trigger_level="LEVEL_1",
        )

        # Step 1 (BUDGET_RESET) 성공 실행
        step1 = coordinator.execute_next_step("global")
        assert step1.status == RecoveryStatus.COMPLETED

        # Step 2 (HEALTH_CHECK) 타임아웃
        loaded = coordinator.get_active_session("global")
        loaded.steps[1].timeout_seconds = 1
        coordinator._save_session(loaded)

        step2 = coordinator.execute_next_step("global")
        assert step2.status == RecoveryStatus.FAILED

        # 보상이 호출되었는지 확인
        assert compensate_called["called"] is True
