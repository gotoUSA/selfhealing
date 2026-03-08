"""
RunbookService Unit Tests.

RunbookService(278)의 단위 테스트:
- 싱글톤 라이프사이클
- handle_event() 파이프라인 (소스 필터, 재귀 깊이, 패턴 매칭)
- execute_runbook() 수동 실행
- _execute_pipeline() 승인 경로 분기 (A: AUTO_APPROVED, B: WAITING)
- cancel_runbook_execution() 우아한 취소
- resume_pipeline() 승인 후 재개
- _acquire/_release_global_semaphore()
- register_subscriptions() / _on_event_received()
- initialize_runbook_system()

Test Categories:
    A. Contract — 상수, 기본값, 설계 사양
    B. Behavior — 파이프라인 동작, 상태 전이, 의존성 상호작용
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.runbook.exceptions import (
    RunbookExecutionError,
    RunbookLockConflictError,
    RunbookNotFoundError,
)
from selfhealing.services.runbook.execution_models import (
    ApprovalDecisionType,
    RunbookExecutionContext,
    RunbookExecutionStatus,
)
from selfhealing.services.runbook.service import (
    MAX_CASCADE_DEPTH,
    SEMAPHORE_KEY,
    SEMAPHORE_TTL_SECONDS,
    RunbookService,
    get_runbook_service,
    initialize_runbook_system,
    reset_runbook_service,
)

# =============================================================================
# Test Helpers
# =============================================================================


@dataclass
class _FakeEvent:
    """테스트용 이벤트 객체."""

    event_type: Any = None
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "test"

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "data": self.data, "source": self.source}


@dataclass
class _FakeRunbook:
    """테스트용 최소 Runbook 객체."""

    id: str = "rb-test-001"
    name: str = "Test Runbook"
    version: int = 1
    risk_level: str = "low"


@dataclass
class _FakeMatchResult:
    """테스트용 MatchResult."""

    runbook_id: str = "rb-test-001"
    confidence: float = 0.9

    def build_trigger_event(
        self, original_event_data: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(original_event_data)
        result.setdefault("trigger_context", {})
        return result


@dataclass
class _FakeSelection:
    """테스트용 MatchSelectionResult."""

    selected: _FakeMatchResult = field(default_factory=_FakeMatchResult)


def _make_ctx(
    execution_id: str = "runbook-test-id",
    runbook_id: str = "rb-test-001",
    status: RunbookExecutionStatus = RunbookExecutionStatus.COMPLETED,
) -> RunbookExecutionContext:
    return RunbookExecutionContext(
        execution_id=execution_id,
        runbook_id=runbook_id,
        namespace="global",
        trigger_event={"manual": True},
        runbook_version=1,
        status=status,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """매 테스트마다 RunbookService 싱글톤 리셋."""
    RunbookService.reset()
    yield
    RunbookService.reset()


# =============================================================================
# A. Contract Tests
# =============================================================================


class TestRunbookServiceConstantsContract:
    """RunbookService 설계 상수 계약 검증."""

    def test_max_cascade_depth_is_three(self):
        """런북 체이닝 최대 깊이 계약값: 3."""
        assert MAX_CASCADE_DEPTH == 3

    def test_semaphore_key_contract(self):
        """글로벌 세마포어 Redis 키 계약값."""
        assert SEMAPHORE_KEY == "selfhealing:runbook:global_semaphore"

    def test_semaphore_ttl_is_one_hour(self):
        """세마포어 TTL 계약값: 3600초(1시간)."""
        assert SEMAPHORE_TTL_SECONDS == 3600


class TestRunbookServiceDefaultContract:
    """RunbookService 초기화 기본값 계약 검증."""

    def test_enabled_default_is_true(self):
        """서비스 초기화 시 _enabled 기본값: True."""
        service = RunbookService()
        assert service._enabled is True

    def test_dependencies_default_to_none(self):
        """초기화 시 모든 lazy 의존성은 None."""
        service = RunbookService()
        assert service._pattern_matcher is None
        assert service._registry is None
        assert service._approval_gate is None
        assert service._executor is None
        assert service._recorder is None
        assert service._event_bus is None


# =============================================================================
# B. Behavior Tests — Singleton & Lifecycle
# =============================================================================


class TestRunbookServiceSingletonBehavior:
    """RunbookService 싱글톤 캐싱/리셋 동작 검증."""

    def test_get_returns_same_instance(self):
        """get_runbook_service()는 동일 인스턴스를 반환."""
        first = get_runbook_service()
        second = get_runbook_service()
        assert first is second

    def test_reset_clears_cached_instance(self):
        """reset 후 새 인스턴스가 생성된다."""
        first = get_runbook_service()
        reset_runbook_service()
        second = get_runbook_service()
        assert first is not second

    def test_concurrent_get_returns_same_instance(self):
        """멀티스레드에서 get_runbook_service()가 동일 인스턴스 반환."""
        results: list[RunbookService] = []

        def worker():
            results.append(get_runbook_service())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


# =============================================================================
# B. Behavior Tests — handle_event()
# =============================================================================


class TestRunbookServiceHandleEventBehavior:
    """handle_event() 이벤트 파이프라인 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    def test_disabled_service_returns_none(self):
        """_enabled=False이면 None 반환."""
        self.service._enabled = False
        event = _FakeEvent()
        assert self.service.handle_event(event) is None

    def test_executor_source_event_skipped(self):
        """source='runbook_executor'인 이벤트는 필터링되어 None 반환."""
        event = _FakeEvent(source="runbook_executor")
        assert self.service.handle_event(event) is None

    def test_max_cascade_depth_exceeded_returns_none(self):
        """cascade_depth가 MAX_CASCADE_DEPTH 이상이면 None 반환."""
        event = _FakeEvent(
            data={"trigger_context": {"cascade_depth": MAX_CASCADE_DEPTH}},
        )

        # 패턴 매처까지 도달하지 않으므로 mock 불필요
        assert self.service.handle_event(event) is None

    def test_cascade_depth_just_below_limit_proceeds(self):
        """cascade_depth가 MAX_CASCADE_DEPTH-1이면 파이프라인 진행."""
        event = _FakeEvent(
            data={"trigger_context": {"cascade_depth": MAX_CASCADE_DEPTH - 1}},
        )

        # Given — 매처가 빈 리스트 반환 → no match
        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = []
        self.service._pattern_matcher = mock_matcher
        self.service._registry = MagicMock()

        # When
        result = self.service.handle_event(event)

        # Then — 패턴 매처까지 도달했음을 확인
        assert result is None
        mock_matcher.evaluate_all.assert_called_once()

    def test_no_pattern_match_returns_none(self):
        """패턴 매칭 결과가 없으면 None 반환."""
        event = _FakeEvent()

        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = []
        self.service._pattern_matcher = mock_matcher
        self.service._registry = MagicMock()

        assert self.service.handle_event(event) is None

    def test_select_runbook_none_returns_none(self):
        """select_runbook()이 None이면 None 반환."""
        event = _FakeEvent()

        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = [MagicMock()]
        mock_matcher.select_runbook.return_value = None
        self.service._pattern_matcher = mock_matcher
        self.service._registry = MagicMock()

        assert self.service.handle_event(event) is None

    def test_runbook_not_in_registry_returns_none(self):
        """선택된 런북이 레지스트리에 없으면 None 반환."""
        event = _FakeEvent()

        selection = _FakeSelection()
        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = [MagicMock()]
        mock_matcher.select_runbook.return_value = selection

        mock_registry = MagicMock()
        mock_registry.get.return_value = None

        self.service._pattern_matcher = mock_matcher
        self.service._registry = mock_registry

        assert self.service.handle_event(event) is None

    @patch.object(RunbookService, "_execute_pipeline")
    def test_successful_match_calls_execute_pipeline(self, mock_pipeline):
        """패턴 매칭 성공 시 _execute_pipeline() 호출."""
        event = _FakeEvent(data={"namespace": "payment"})
        runbook = _FakeRunbook()
        selection = _FakeSelection()

        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = [MagicMock()]
        mock_matcher.select_runbook.return_value = selection

        mock_registry = MagicMock()
        mock_registry.get.return_value = runbook

        self.service._pattern_matcher = mock_matcher
        self.service._registry = mock_registry

        expected_ctx = _make_ctx()
        mock_pipeline.return_value = expected_ctx

        # When
        result = self.service.handle_event(event)

        # Then
        assert result is expected_ctx
        mock_pipeline.assert_called_once()
        call_kwargs = mock_pipeline.call_args
        assert call_kwargs[1]["runbook"] is runbook
        assert call_kwargs[1]["namespace"] == "payment"

    @patch.object(RunbookService, "_execute_pipeline")
    def test_cascade_depth_incremented_in_trigger_event(self, mock_pipeline):
        """handle_event는 cascade_depth를 1 증가시켜 trigger_event에 전파."""
        event = _FakeEvent(
            data={"trigger_context": {"cascade_depth": 1}},
        )
        runbook = _FakeRunbook()
        selection = _FakeSelection()

        mock_matcher = MagicMock()
        mock_matcher.evaluate_all.return_value = [MagicMock()]
        mock_matcher.select_runbook.return_value = selection

        mock_registry = MagicMock()
        mock_registry.get.return_value = runbook

        self.service._pattern_matcher = mock_matcher
        self.service._registry = mock_registry
        mock_pipeline.return_value = _make_ctx()

        # When
        self.service.handle_event(event)

        # Then
        trigger_event = mock_pipeline.call_args[1]["trigger_event"]
        assert trigger_event["trigger_context"]["cascade_depth"] == 2


# =============================================================================
# B. Behavior Tests — execute_runbook()
# =============================================================================


class TestRunbookServiceExecuteRunbookBehavior:
    """execute_runbook() 수동 실행 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    def test_runbook_not_found_raises_error(self):
        """레지스트리에 없는 ID로 호출 시 RunbookNotFoundError."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        self.service._registry = mock_registry

        with pytest.raises(RunbookNotFoundError, match="Runbook not found"):
            self.service.execute_runbook("nonexistent-id")

    @patch.object(RunbookService, "_execute_pipeline")
    def test_found_runbook_calls_pipeline(self, mock_pipeline):
        """레지스트리에 런북 존재 시 _execute_pipeline() 호출."""
        runbook = _FakeRunbook()
        mock_registry = MagicMock()
        mock_registry.get.return_value = runbook
        self.service._registry = mock_registry

        expected_ctx = _make_ctx()
        mock_pipeline.return_value = expected_ctx

        # When
        result = self.service.execute_runbook("rb-test-001", namespace="payment")

        # Then
        assert result is expected_ctx
        mock_pipeline.assert_called_once_with(
            runbook=runbook,
            trigger_event={"manual": True, "runbook_id": "rb-test-001"},
            namespace="payment",
        )

    @patch.object(RunbookService, "_execute_pipeline")
    def test_default_namespace_is_global(self, mock_pipeline):
        """namespace 미지정 시 기본값 'global'."""
        runbook = _FakeRunbook()
        mock_registry = MagicMock()
        mock_registry.get.return_value = runbook
        self.service._registry = mock_registry
        mock_pipeline.return_value = _make_ctx()

        self.service.execute_runbook("rb-test-001")

        call_kwargs = mock_pipeline.call_args
        assert call_kwargs[1]["namespace"] == "global"


# =============================================================================
# B. Behavior Tests — _execute_pipeline()
# =============================================================================


class TestRunbookServiceExecutePipelineBehavior:
    """_execute_pipeline() 승인 경로 분기 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

        self.mock_executor = MagicMock()
        self.mock_approval_gate = MagicMock()
        self.mock_recorder = MagicMock()
        self.service._executor = self.mock_executor
        self.service._approval_gate = self.mock_approval_gate
        self.service._recorder = self.mock_recorder

    @patch.object(RunbookService, "_acquire_global_semaphore", return_value=True)
    @patch.object(RunbookService, "_release_global_semaphore")
    def test_auto_approved_runs_and_records(self, mock_release, mock_acquire):
        """경로 A: AUTO_APPROVED → 실행 + 기록."""
        runbook = _FakeRunbook()
        decision = MagicMock()
        decision.decision_type = ApprovalDecisionType.AUTO_APPROVED
        self.mock_approval_gate.evaluate_approval.return_value = decision

        expected_ctx = _make_ctx()
        self.mock_executor.execute_runbook.return_value = expected_ctx

        # When
        result = self.service._execute_pipeline(
            runbook=runbook,
            trigger_event={"manual": True},
            namespace="global",
        )

        # Then
        assert result is expected_ctx
        self.mock_executor.execute_runbook.assert_called_once()
        self.mock_recorder.record.assert_called_once()
        mock_release.assert_called_once()

    @patch.object(RunbookService, "_acquire_global_semaphore", return_value=True)
    @patch.object(RunbookService, "_release_global_semaphore")
    def test_blocked_decision_sets_failed_status(self, mock_release, mock_acquire):
        """BLOCKED 결정 시 FAILED 상태로 반환."""
        runbook = _FakeRunbook()
        decision = MagicMock()
        decision.decision_type = ApprovalDecisionType.BLOCKED
        decision.block_message = "Governance blocked"
        self.mock_approval_gate.evaluate_approval.return_value = decision

        # When
        result = self.service._execute_pipeline(
            runbook=runbook,
            trigger_event={"manual": True},
            namespace="global",
        )

        # Then
        assert result.status == RunbookExecutionStatus.FAILED
        assert result.abort_reason == "Governance blocked"
        self.mock_executor._save_context.assert_called_once()
        mock_release.assert_called_once()

    @patch.object(RunbookService, "_acquire_global_semaphore", return_value=True)
    @patch.object(RunbookService, "_release_global_semaphore")
    def test_waiting_decision_suspends_pipeline(self, mock_release, mock_acquire):
        """경로 B: WAITING → WAITING_APPROVAL 상태 + 세마포어 미해제."""
        runbook = _FakeRunbook()
        decision = MagicMock()
        decision.decision_type = ApprovalDecisionType.WAITING
        self.mock_approval_gate.evaluate_approval.return_value = decision

        # When
        result = self.service._execute_pipeline(
            runbook=runbook,
            trigger_event={"manual": True},
            namespace="global",
        )

        # Then
        assert result.status == RunbookExecutionStatus.WAITING_APPROVAL
        self.mock_executor._save_context.assert_called_once()
        # WAITING_APPROVAL 시 세마포어 해제하지 않음
        mock_release.assert_not_called()

    @patch.object(RunbookService, "_acquire_global_semaphore", return_value=False)
    @patch("selfhealing.services.dlq.store_to_dlq")
    def test_concurrency_limit_stores_to_dlq(self, mock_store_dlq, mock_acquire):
        """글로벌 동시 실행 제한 초과 시 DLQ에 저장."""
        runbook = _FakeRunbook()

        # When
        result = self.service._execute_pipeline(
            runbook=runbook,
            trigger_event={"manual": True},
            namespace="global",
        )

        # Then
        assert result.status == RunbookExecutionStatus.FAILED
        assert "concurrency limit" in result.abort_reason.lower()
        mock_store_dlq.assert_called_once()

    @patch.object(RunbookService, "_acquire_global_semaphore", return_value=True)
    @patch.object(RunbookService, "_release_global_semaphore")
    def test_lock_conflict_returns_cancelled(self, mock_release, mock_acquire):
        """RunbookLockConflictError 발생 시 CANCELLED 상태 반환."""
        runbook = _FakeRunbook()
        self.mock_approval_gate.evaluate_approval.side_effect = (
            RunbookLockConflictError("lock conflict")
        )

        # When
        result = self.service._execute_pipeline(
            runbook=runbook,
            trigger_event={"manual": True},
            namespace="global",
        )

        # Then
        assert result.status == RunbookExecutionStatus.CANCELLED
        assert "another worker" in result.abort_reason.lower()
        mock_release.assert_called_once()


# =============================================================================
# B. Behavior Tests — resume_pipeline()
# =============================================================================


class TestRunbookServiceResumePipelineBehavior:
    """resume_pipeline() 승인 후 재개 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    @patch.object(RunbookService, "_release_global_semaphore")
    def test_resume_calls_executor_and_records(self, mock_release):
        """resume_pipeline은 executor.resume_execution + recorder.record 호출."""
        mock_executor = MagicMock()
        mock_recorder = MagicMock()
        mock_registry = MagicMock()

        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        mock_executor.resume_execution.return_value = ctx
        mock_registry.get.return_value = _FakeRunbook()

        self.service._executor = mock_executor
        self.service._recorder = mock_recorder
        self.service._registry = mock_registry

        # When
        result = self.service.resume_pipeline("runbook-test-id")

        # Then
        assert result is ctx
        mock_executor.resume_execution.assert_called_once_with("runbook-test-id")
        mock_recorder.record.assert_called_once()
        mock_release.assert_called_once()

    @patch.object(RunbookService, "_release_global_semaphore")
    def test_resume_releases_semaphore_even_on_error(self, mock_release):
        """resume 실행 중 예외 발생해도 세마포어 해제."""
        mock_executor = MagicMock()
        mock_executor.resume_execution.side_effect = RuntimeError("boom")
        self.service._executor = mock_executor

        with pytest.raises(RuntimeError, match="boom"):
            self.service.resume_pipeline("some-id")

        mock_release.assert_called_once()


# =============================================================================
# B. Behavior Tests — cancel_runbook_execution()
# =============================================================================


class TestRunbookServiceCancelBehavior:
    """cancel_runbook_execution() 우아한 취소 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()
        self.mock_executor = MagicMock()
        self.service._executor = self.mock_executor

    def test_execution_not_found_raises_error(self):
        """실행 ID가 없으면 RunbookExecutionError."""
        self.mock_executor._load_context.return_value = None

        with pytest.raises(RunbookExecutionError, match="Execution not found"):
            self.service.cancel_runbook_execution("nonexistent", "admin")

    def test_completed_status_cannot_be_cancelled(self):
        """COMPLETED 상태는 취소 불가."""
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        self.mock_executor._load_context.return_value = ctx

        with pytest.raises(RunbookExecutionError, match="Cannot cancel"):
            self.service.cancel_runbook_execution("runbook-test-id", "admin")

    def test_failed_status_cannot_be_cancelled(self):
        """FAILED 상태는 취소 불가."""
        ctx = _make_ctx(status=RunbookExecutionStatus.FAILED)
        self.mock_executor._load_context.return_value = ctx

        with pytest.raises(RunbookExecutionError, match="Cannot cancel"):
            self.service.cancel_runbook_execution("runbook-test-id", "admin")

    @patch.object(RunbookService, "_release_global_semaphore")
    def test_pending_status_immediately_cancelled(self, mock_release):
        """PENDING 상태는 즉시 CANCELLED로 전환."""
        ctx = _make_ctx(status=RunbookExecutionStatus.PENDING)
        self.mock_executor._load_context.return_value = ctx

        # When
        result = self.service.cancel_runbook_execution(
            "runbook-test-id", "admin", reason="test cancel"
        )

        # Then
        assert result.status == RunbookExecutionStatus.CANCELLED
        assert "admin" in result.abort_reason
        assert "test cancel" in result.abort_reason
        self.mock_executor._save_context.assert_called_once()

    @patch.object(RunbookService, "_release_global_semaphore")
    def test_waiting_approval_immediately_cancelled(self, mock_release):
        """WAITING_APPROVAL 상태는 즉시 CANCELLED + 세마포어 해제."""
        ctx = _make_ctx(status=RunbookExecutionStatus.WAITING_APPROVAL)
        self.mock_executor._load_context.return_value = ctx

        # When
        result = self.service.cancel_runbook_execution("runbook-test-id", "admin")

        # Then
        assert result.status == RunbookExecutionStatus.CANCELLED
        mock_release.assert_called_once()

    def test_executing_status_sets_cancel_flag(self):
        """EXECUTING 상태는 취소 플래그만 설정."""
        ctx = _make_ctx(status=RunbookExecutionStatus.EXECUTING)
        self.mock_executor._load_context.return_value = ctx

        # When
        result = self.service.cancel_runbook_execution(
            "runbook-test-id", "operator", reason="emergency"
        )

        # Then — 상태는 변경하지 않고 변수에 플래그 설정
        assert result.variables["__cancel_requested"] is True
        assert result.variables["__cancelled_by"] == "operator"
        assert result.variables["__cancel_reason"] == "emergency"
        self.mock_executor._save_context.assert_called_once()


# =============================================================================
# B. Behavior Tests — Global Semaphore
# =============================================================================


class TestRunbookServiceSemaphoreBehavior:
    """글로벌 동시 실행 세마포어 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    @patch("selfhealing.core.state_backend.get_state_backend")
    @patch("selfhealing.settings.runbook.get_runbook_settings")
    def test_acquire_returns_true_when_under_limit(self, mock_settings, mock_backend):
        """동시 실행 수가 한도 이하면 True 반환."""
        mock_settings.return_value.max_concurrent_runbooks = 3

        mock_redis = MagicMock()
        mock_redis.incr.return_value = 2
        mock_backend.return_value._client = mock_redis

        assert self.service._acquire_global_semaphore() is True
        mock_redis.incr.assert_called_once_with(SEMAPHORE_KEY)

    @patch("selfhealing.core.state_backend.get_state_backend")
    @patch("selfhealing.settings.runbook.get_runbook_settings")
    def test_acquire_returns_false_when_over_limit(self, mock_settings, mock_backend):
        """동시 실행 수가 한도 초과 시 False + DECR 호출."""
        mock_settings.return_value.max_concurrent_runbooks = 3

        mock_redis = MagicMock()
        mock_redis.incr.return_value = 4
        mock_backend.return_value._client = mock_redis

        assert self.service._acquire_global_semaphore() is False
        mock_redis.decr.assert_called_once_with(SEMAPHORE_KEY)

    @patch("selfhealing.core.state_backend.get_state_backend")
    @patch("selfhealing.settings.runbook.get_runbook_settings")
    def test_acquire_sets_expire_on_first_incr(self, mock_settings, mock_backend):
        """첫 번째 INCR(current==1) 시 TTL 설정."""
        mock_settings.return_value.max_concurrent_runbooks = 3

        mock_redis = MagicMock()
        mock_redis.incr.return_value = 1
        mock_backend.return_value._client = mock_redis

        self.service._acquire_global_semaphore()

        mock_redis.expire.assert_called_once_with(SEMAPHORE_KEY, SEMAPHORE_TTL_SECONDS)

    @patch("selfhealing.core.state_backend.get_state_backend")
    @patch("selfhealing.settings.runbook.get_runbook_settings")
    def test_acquire_no_expire_on_subsequent_incr(self, mock_settings, mock_backend):
        """두 번째 이후 INCR(current>1) 시 expire 호출 안 함."""
        mock_settings.return_value.max_concurrent_runbooks = 3

        mock_redis = MagicMock()
        mock_redis.incr.return_value = 2
        mock_backend.return_value._client = mock_redis

        self.service._acquire_global_semaphore()

        mock_redis.expire.assert_not_called()

    def test_acquire_returns_true_when_no_redis(self):
        """Redis 불가 시 Fail-Open으로 True 반환."""
        with patch(
            "selfhealing.core.state_backend.get_state_backend",
            side_effect=ImportError("no redis"),
        ):
            assert self.service._acquire_global_semaphore() is True

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_release_calls_decr(self, mock_backend):
        """_release_global_semaphore는 DECR 호출."""
        mock_redis = MagicMock()
        mock_backend.return_value._client = mock_redis

        self.service._release_global_semaphore()

        mock_redis.decr.assert_called_once_with(SEMAPHORE_KEY)

    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_release_no_client_does_nothing(self, mock_backend):
        """Redis 클라이언트 없으면 아무 동작 안 함."""
        mock_backend.return_value._client = None
        self.service._release_global_semaphore()  # no error


# =============================================================================
# B. Behavior Tests — register_subscriptions()
# =============================================================================


class TestRunbookServiceSubscriptionBehavior:
    """register_subscriptions() EventBus 구독 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS": "false"})
    def test_env_disabled_skips_subscription(self):
        """환경변수로 구독 비활성화 시 EventBus 접근 안 함."""
        mock_bus = MagicMock()
        self.service._event_bus = mock_bus

        self.service.register_subscriptions()

        mock_bus.subscribe.assert_not_called()

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS": "true"})
    def test_subscribes_to_core_event_types(self):
        """기본 3개 이벤트 타입에 구독 등록."""
        mock_bus = MagicMock()
        self.service._event_bus = mock_bus

        self.service.register_subscriptions()

        assert mock_bus.subscribe.call_count >= 3

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_SUBSCRIBE_EVENTS": "true"})
    def test_import_error_handled_gracefully(self):
        """EventBus import 실패 시 예외 없이 진행."""
        self.service._event_bus = None
        # _get_event_bus()에서 import 실패를 시뮬레이션
        with patch.object(
            RunbookService,
            "_get_event_bus",
            side_effect=ImportError("no bus"),
        ):
            self.service.register_subscriptions()  # no error


# =============================================================================
# B. Behavior Tests — _on_event_received()
# =============================================================================


class TestRunbookServiceOnEventReceivedBehavior:
    """_on_event_received() 이벤트 핸들러 위임 동작 검증."""

    def setup_method(self):
        RunbookService.reset()
        self.service = RunbookService()

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_ASYNC_EXECUTION": "false"})
    @patch.object(RunbookService, "handle_event")
    def test_sync_mode_calls_handle_event_directly(self, mock_handle):
        """동기 모드에서는 handle_event 직접 호출."""
        event = _FakeEvent()
        self.service._on_event_received(event)
        mock_handle.assert_called_once_with(event)

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_ASYNC_EXECUTION": "true"})
    def test_async_mode_dispatches_celery_task(self):
        """비동기 모드에서는 Celery 태스크 디스패치."""
        mock_task = MagicMock()
        event = _FakeEvent()

        with patch.dict(
            "sys.modules",
            {
                "selfhealing.adapters.celery.tasks.runbook": MagicMock(
                    execute_runbook_for_event=mock_task,
                )
            },
        ):
            self.service._on_event_received(event)

        mock_task.delay.assert_called_once()

    @patch.dict(os.environ, {"SELFHEALING_RUNBOOK_ASYNC_EXECUTION": "true"})
    @patch.object(RunbookService, "handle_event")
    def test_async_import_error_falls_back_to_sync(self, mock_handle):
        """Celery import 실패 시 동기 폴백."""
        event = _FakeEvent()

        with patch.dict(
            "sys.modules",
            {
                "selfhealing.adapters.celery.tasks.runbook": None,
            },
        ):
            self.service._on_event_received(event)

        mock_handle.assert_called_once_with(event)


# =============================================================================
# B. Behavior Tests — initialize_runbook_system()
# =============================================================================


class TestInitializeRunbookSystemBehavior:
    """initialize_runbook_system() 전체 초기화 동작 검증."""

    @patch(
        "selfhealing.services.runbook.primitives.register_builtin_primitives",
    )
    @patch(
        "selfhealing.services.runbook.builtins.register_builtin_runbooks",
    )
    @patch.object(RunbookService, "register_subscriptions")
    def test_initialize_registers_builtins_and_subscribes(
        self,
        mock_subscriptions,
        mock_builtins,
        mock_primitives,
    ):
        """초기화 시 빌트인 등록 + 구독."""
        # When
        service = initialize_runbook_system()

        # Then
        assert isinstance(service, RunbookService)
        mock_builtins.assert_called_once()
        mock_primitives.assert_called_once()
        mock_subscriptions.assert_called_once()

    @patch.object(RunbookService, "register_subscriptions")
    def test_initialize_handles_missing_builtins(self, mock_sub):
        """빌트인 모듈 없어도 초기화 성공."""
        with (
            patch(
                "selfhealing.services.runbook.builtins.register_builtin_runbooks",
                side_effect=ImportError,
            ),
            patch(
                "selfhealing.services.runbook.primitives.register_builtin_primitives",
                side_effect=ImportError,
            ),
        ):
            service = initialize_runbook_system()
            assert isinstance(service, RunbookService)
