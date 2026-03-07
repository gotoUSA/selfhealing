"""
Tests for ReplayService.
DLQ 재생 서비스(replay_service.py)의 단위 테스트.
거버넌스 체크, 단건/배치 재생, 핸들러 레지스트리 등을 검증합니다.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.governance.checks import GovernanceCheckResult
from selfhealing.services.replay_service import (
    BatchReplayResult,
    DefaultReplayHandler,
    ReplayHandler,
    ReplayResult,
    ReplayService,
    _replay_handlers,
    get_replay_handler,
    register_replay_handler,
)

# =============================================================================
# Fixtures
# =============================================================================


@dataclass
class FakeFailedOperationData:
    """테스트용 FailedOperationData 대체 데이터클래스."""

    id: int
    domain: str = "payment"
    status: str = "pending"
    failure_type: str = "PG_TIMEOUT"
    retry_count: int = 0
    error_code: str = ""
    error_message: str = ""
    snapshot_data: dict = None
    request_data: dict = None
    response_data: dict = None
    metadata: dict = None

    def __post_init__(self):
        self.snapshot_data = self.snapshot_data or {}
        self.request_data = self.request_data or {}
        self.response_data = self.response_data or {}
        self.metadata = self.metadata or {}


class FakeReplayHandler(ReplayHandler):
    """테스트용 ReplayHandler 구현체."""

    def __init__(self, domain_name: str, success: bool = True):
        self._domain = domain_name
        self._success = success

    @property
    def domain(self) -> str:
        return self._domain

    def can_replay(self, failed_op) -> tuple[bool, str]:
        return True, ""

    def replay(self, failed_op) -> ReplayResult:
        if self._success:
            return ReplayResult.succeeded(failed_op.id, "Replayed OK")
        return ReplayResult.failed(failed_op.id, "Handler says no")


@pytest.fixture(autouse=True)
def _clear_handler_registry():
    """각 테스트 전후로 핸들러 레지스트리를 초기화."""
    _replay_handlers.clear()
    yield
    _replay_handlers.clear()


@pytest.fixture
def mock_repository():
    """Mock FailedOperationRepository를 생성."""
    repo = MagicMock()
    repo.try_acquire_for_replay.return_value = FakeFailedOperationData(id=1)
    repo.get_by_id.return_value = FakeFailedOperationData(id=1)
    repo.complete_replay.return_value = None
    repo.get_pending_entries.return_value = []
    repo.get_pending_by_failure_types.return_value = []
    return repo


# =============================================================================
# ReplayResult Tests
# =============================================================================


class TestReplayResult:
    """ReplayResult 데이터클래스 팩토리 메서드 테스트."""

    def test_succeeded_factory(self):
        """Succeeded factory
        성공 팩토리가 올바른 값을 반환하는지 확인.
        """
        result = ReplayResult.succeeded(1, "OK", data={"key": "value"})
        assert result.success is True
        assert result.dlq_id == 1
        assert result.message == "OK"
        assert result.data == {"key": "value"}

    def test_failed_factory(self):
        """Failed factory
        실패 팩토리가 올바른 값을 반환하는지 확인.
        """
        result = ReplayResult.failed(2, "timeout")
        assert result.success is False
        assert result.dlq_id == 2
        assert result.error == "timeout"

    def test_blocked_factory(self):
        """Blocked factory
        차단 팩토리가 거버넌스 정보를 포함하는지 확인.
        """
        governance = MagicMock(spec=GovernanceCheckResult)
        governance.block_message = "Kill Switch active"
        governance.block_reason = MagicMock()
        governance.block_reason.value = "kill_switch"

        result = ReplayResult.blocked(3, governance)
        assert result.success is False
        assert result.data["blocked"] is True
        assert result.data["block_reason"] == "kill_switch"


# =============================================================================
# BatchReplayResult Tests
# =============================================================================


class TestBatchReplayResult:
    """BatchReplayResult 데이터클래스 테스트."""

    def test_default_values(self):
        """Default values
        기본값이 올바르게 초기화되는지 확인.
        """
        result = BatchReplayResult()
        assert result.total == 0
        assert result.success_count == 0
        assert result.failed_count == 0
        assert result.governance_blocked is False

    def test_priority_metadata(self):
        """Priority metadata
        우선순위 기반 재생 정보가 올바르게 설정되는지 확인.
        """
        result = BatchReplayResult(
            priority_used=True,
            domains_processed=["payment", "notification"],
        )
        assert result.priority_used is True
        assert result.domains_processed == ["payment", "notification"]


# =============================================================================
# DefaultReplayHandler Tests
# =============================================================================


class TestDefaultReplayHandler:
    """DefaultReplayHandler 테스트."""

    def test_can_replay_returns_false(self):
        """Can replay returns false
        기본 핸들러는 항상 can_replay=False를 반환하는지 확인.
        """
        handler = DefaultReplayHandler("unknown")
        can, reason = handler.can_replay(FakeFailedOperationData(id=1))
        assert can is False
        assert "unknown" in reason

    def test_replay_returns_failed(self):
        """Replay returns failed
        기본 핸들러의 replay가 실패 결과를 반환하는지 확인.
        """
        handler = DefaultReplayHandler("unknown")
        result = handler.replay(FakeFailedOperationData(id=1))
        assert result.success is False
        assert "register" in result.error.lower()

    def test_domain_property(self):
        """Domain property
        domain 프로퍼티가 올바른 값을 반환하는지 확인.
        """
        handler = DefaultReplayHandler("payment")
        assert handler.domain == "payment"


# =============================================================================
# Handler Registry Tests
# =============================================================================


class TestHandlerRegistry:
    """핸들러 레지스트리 테스트."""

    def test_register_and_get(self):
        """Register and get
        핸들러 등록 후 조회가 올바르게 동작하는지 확인.
        """
        handler = FakeReplayHandler("payment")
        register_replay_handler(handler)
        retrieved = get_replay_handler("payment")
        assert retrieved is handler

    def test_get_unregistered_returns_default(self):
        """Get unregistered returns default
        등록되지 않은 도메인 조회 시 DefaultReplayHandler가 반환되는지 확인.
        """
        handler = get_replay_handler("nonexistent")
        assert isinstance(handler, DefaultReplayHandler)

    def test_overwrite_handler(self):
        """Overwrite handler
        같은 도메인으로 재등록하면 덮어쓰기 되는지 확인.
        """
        handler1 = FakeReplayHandler("payment", success=True)
        handler2 = FakeReplayHandler("payment", success=False)
        register_replay_handler(handler1)
        register_replay_handler(handler2)
        assert get_replay_handler("payment") is handler2


# =============================================================================
# ReplayService Tests
# =============================================================================


class TestReplayServiceReplaySingle:
    """ReplayService.replay_single 테스트."""

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_successful_replay(self, mock_audit, mock_gov, mock_repository):
        """Successful replay
        거버넌스 통과 + 핸들러 성공 시 ReplayResult.success=True인지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        handler = FakeReplayHandler("payment", success=True)
        register_replay_handler(handler)

        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=1)

        assert result.success is True
        mock_repository.complete_replay.assert_called_once()

    @patch("selfhealing.services.replay_service.check_all_governance")
    def test_governance_blocked(self, mock_gov, mock_repository):
        """Governance blocked
        거버넌스 차단 시 replay_single이 blocked 결과를 반환하는지 확인.
        """
        mock_gov.return_value = MagicMock(
            allowed=False,
            block_message="Kill Switch",
            block_reason=MagicMock(value="kill_switch"),
        )
        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=1)

        assert result.success is False
        assert result.data["blocked"] is True

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_entry_not_found(self, mock_audit, mock_gov, mock_repository):
        """Entry not found
        DLQ 엔트리를 찾을 수 없을 때 적절한 에러 메시지를 반환하는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        mock_repository.try_acquire_for_replay.return_value = None
        mock_repository.get_by_id.return_value = None

        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=999)

        assert result.success is False
        assert "not found" in result.error

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_max_replays_exceeded(self, mock_audit, mock_gov, mock_repository):
        """Max replays exceeded
        최대 재시도 횟수 초과 시 적절한 에러 메시지를 반환하는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        mock_repository.try_acquire_for_replay.return_value = None
        mock_repository.get_by_id.return_value = FakeFailedOperationData(id=1, status="pending")

        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=1)

        assert result.success is False
        assert "max_replays_exceeded" in result.error

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_handler_crash_escalates(self, mock_audit, mock_gov, mock_repository):
        """Handler crash escalates
        핸들러가 예외를 발생시키면 에러가 적절히 처리되는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)

        class CrashHandler(ReplayHandler):
            @property
            def domain(self):
                return "payment"

            def can_replay(self, failed_op):
                return True, ""

            def replay(self, failed_op):
                raise RuntimeError("Handler exploded")

        register_replay_handler(CrashHandler())

        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=1)

        assert result.success is False
        assert "internal_error" in result.error
        mock_repository.complete_replay.assert_called_once()

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_non_pending_entry_rejected(self, mock_audit, mock_gov, mock_repository):
        """Non-pending entry rejected
        pending 상태가 아닌 엔트리는 재생이 거부되는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        mock_repository.try_acquire_for_replay.return_value = None
        mock_repository.get_by_id.return_value = FakeFailedOperationData(id=1, status="resolved")

        service = ReplayService(repository=mock_repository)
        result = service.replay_single(dlq_id=1)

        assert result.success is False
        assert "resolved" in result.error


class TestReplayServiceReplayBatch:
    """ReplayService.replay_batch 테스트."""

    @patch("selfhealing.services.replay_service.check_all_governance")
    def test_governance_blocked_batch(self, mock_gov, mock_repository):
        """Governance blocked batch
        거버넌스 차단 시 배치 재생이 차단되는지 확인.
        """
        mock_gov.return_value = MagicMock(
            allowed=False,
            block_message="Emergency Level",
        )
        service = ReplayService(repository=mock_repository)
        result = service.replay_batch(domain="payment")

        assert result.governance_blocked is True
        assert result.total == 0

    @patch("selfhealing.services.replay_service.check_all_governance")
    def test_empty_batch(self, mock_gov, mock_repository):
        """Empty batch
        대상 엔트리가 없을 때 빈 결과를 반환하는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        mock_repository.get_pending_entries.return_value = []

        service = ReplayService(repository=mock_repository)
        result = service.replay_batch(domain="payment")

        assert result.total == 0
        assert result.success_count == 0


class TestReplayServiceReplayOnCircuitClose:
    """ReplayService.replay_on_circuit_close 테스트."""

    @patch("selfhealing.services.replay_service.check_all_governance")
    def test_no_failure_types_mapped(self, mock_gov, mock_repository):
        """No failure types mapped
        서비스에 매핑된 failure_type이 없으면 빈 결과를 반환하는지 확인.
        """
        service = ReplayService(repository=mock_repository)
        result = service.replay_on_circuit_close(service_name="unknown_service")

        assert result.total == 0

    @patch("selfhealing.services.replay_service.check_all_governance")
    @patch("selfhealing.services.replay_service.log_dlq_replay_audit")
    def test_with_failure_type_map(self, mock_audit, mock_gov, mock_repository):
        """With failure type map
        커스텀 매핑을 통해 적절한 엔트리가 재생되는지 확인.
        """
        mock_gov.return_value = MagicMock(allowed=True)
        entry = FakeFailedOperationData(id=10, domain="payment")
        mock_repository.get_pending_by_failure_types.return_value = [entry]
        mock_repository.try_acquire_for_replay.return_value = entry

        handler = FakeReplayHandler("payment", success=True)
        register_replay_handler(handler)

        service = ReplayService(repository=mock_repository)
        result = service.replay_on_circuit_close(
            service_name="pg",
            service_failure_type_map={"pg": ["PG_TIMEOUT"]},
        )

        assert result.total == 1
