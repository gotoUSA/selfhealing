"""
DLQ Replay Throttle-Aware 단위 테스트.

테스트 대상: selfhealing.services.dlq.replay_operations.ReplayOperationsMixin
  - replay_throttle_aware()
  - replay_all_throttle_aware()

테스트 시나리오:
1. TTL 만료 엔트리 Replay 차단 (expires_at 과거)
2. 재시도 한도 소진 엔트리 자동 permanently_failed 처리
3. Throttle permit 거부 시 retry_after 반환
4. _execute_replay() 파이프라인 통한 안전한 Replay
5. Replay 성공 시 resolve_entry 호출
6. 배치 Replay에서 Emergency 시 조기 중단
7. get_replayable_entries() 사용 (query() 대신)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from selfhealing.interfaces.repositories import FailedOperationData
from selfhealing.services.dlq.base import DLQServiceBase
from selfhealing.services.dlq.entry_operations import EntryOperationsMixin
from selfhealing.services.dlq.query_operations import QueryOperationsMixin
from selfhealing.services.dlq.replay_operations import ReplayOperationsMixin
from selfhealing.services.dlq.store_operations import StoreOperationsMixin
from selfhealing.services.dlq_models import (
    DLQConfig,
    DLQThrottleReplayResult,
)


class MockDLQService(
    StoreOperationsMixin,
    QueryOperationsMixin,
    ReplayOperationsMixin,
    EntryOperationsMixin,
    DLQServiceBase,
):
    """테스트용 DLQ Service (필요한 Mixin만 포함)."""

    def __init__(self, repository=None, config=None):
        self._repository = repository or MagicMock()
        self.config = config or DLQConfig(enabled=True)

    @property
    def repository(self):
        return self._repository

    @property
    def is_enabled(self):
        return self.config.enabled

    def _log_dlq_audit(self, **kwargs):
        pass


def _make_entry(**overrides) -> FailedOperationData:
    """테스트용 FailedOperationData 팩토리."""
    defaults = {
        "id": 1,
        "domain": "throttle_rejection",
        "failure_type": "throttle_rejected",
        "status": "pending",
        "retry_count": 0,
        "max_retries": 2,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=72),
        "metadata": {},
    }
    defaults.update(overrides)
    return FailedOperationData(**defaults)


class TestReplayThrottleAwareTTLExpiry:
    """TTL 만료 엔트리 Replay 차단 테스트."""

    def test_expired_entry_skips_replay(self):
        """expires_at이 과거인 엔트리는 Replay하지 않는다."""
        mock_repo = MagicMock()
        expired_entry = _make_entry(
            expires_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        mock_repo.get_by_id.return_value = expired_entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is False
        assert "expired" in result.error.lower()
        throttle.check.assert_not_called()

    def test_none_expires_at_proceeds(self):
        """expires_at이 None이면 TTL 검증 통과."""
        mock_repo = MagicMock()
        entry = _make_entry(expires_at=None, retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is True
        throttle.check.assert_called_once()

    def test_future_expires_at_proceeds(self):
        """expires_at이 미래면 정상 Replay 진행."""
        mock_repo = MagicMock()
        entry = _make_entry(
            expires_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
            retry_count=0,
            max_retries=2,
        )
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is True


class TestReplayThrottleAwareRetryGuard:
    """재시도 한도 소진 검증 테스트 (Death Spiral 방지)."""

    def test_exhausted_retries_marked_permanently_failed(self):
        """retry_count >= max_retries인 엔트리는 permanently_failed 처리."""
        mock_repo = MagicMock()
        exhausted_entry = _make_entry(retry_count=2, max_retries=2)
        mock_repo.get_by_id.return_value = exhausted_entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is False
        assert "Max retries exhausted" in result.error
        throttle.check.assert_not_called()
        mock_repo.update_status.assert_called_once()

    def test_retries_available_proceeds(self):
        """retry_count < max_retries이면 정상 진행."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=1, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is True


class TestReplayThrottleAwarePermitCheck:
    """Throttle permit 획득 테스트."""

    def test_throttle_rejection_returns_retry_after(self):
        """Throttle 거부 시 retry_after를 포함한 실패 반환."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=False, remaining=0, reason="capacity_exceeded", reset_at=1.5)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is False
        assert "Throttle rejected" in result.error
        assert result.retry_after == 1.5
        mock_repo.increment_retry_count.assert_called_once_with(entry.id)

    def test_throttle_allowed_proceeds_to_replay(self):
        """Throttle 허용 시 _execute_replay 호출."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        service._execute_replay.assert_called_once_with(entry)
        assert result.success is True


class TestReplayThrottleAwareExecutePipeline:
    """_execute_replay() 파이프라인 보장 테스트."""

    def test_uses_execute_replay_not_direct_executor(self):
        """replay_throttle_aware는 _execute_replay()을 통해 실행한다."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        service._execute_replay.assert_called_once_with(entry)
        assert result.success is True

    def test_execute_replay_failure_increments_retry_count(self):
        """_execute_replay()가 False 반환 시 retry_count 증가."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=False)

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is False
        mock_repo.increment_retry_count.assert_called_once_with(entry.id)

    def test_execute_replay_exception_increments_retry_count(self):
        """_execute_replay() 예외 발생 시 retry_count 증가."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(side_effect=RuntimeError("handler error"))

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is False
        assert "handler error" in result.error
        mock_repo.increment_retry_count.assert_called_once()

    def test_entry_not_found_returns_failure(self):
        """존재하지 않는 entry_id는 실패 반환."""
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()

        result = service.replay_throttle_aware(entry_id=999, throttle=throttle)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_successful_replay_resolves_entry(self):
        """Replay 성공 시 resolve_entry가 호출된다."""
        mock_repo = MagicMock()
        entry = _make_entry(retry_count=0, max_retries=2)
        mock_repo.get_by_id.return_value = entry

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()
        throttle.check.return_value = MagicMock(allowed=True, remaining=50, reason=None, reset_at=0)
        service._execute_replay = MagicMock(return_value=True)
        service.resolve_entry = MagicMock()

        result = service.replay_throttle_aware(entry_id=1, throttle=throttle)

        assert result.success is True
        service.resolve_entry.assert_called_once_with(entry.id, notes="throttle_aware_replay")


class TestReplayAllThrottleAware:
    """배치 Throttle-Aware Replay 테스트."""

    def test_uses_get_replayable_entries(self):
        """query() 대신 get_replayable_entries()를 사용한다."""
        mock_repo = MagicMock()
        mock_repo.find_replayable.return_value = []

        service = MockDLQService(repository=mock_repo)
        throttle = MagicMock()

        result = service.replay_all_throttle_aware(
            throttle=throttle,
            domain="throttle_rejection",
        )

        mock_repo.find_replayable.assert_called_once()
        assert result.total == 0

    def test_emergency_mode_stops_batch(self):
        """Emergency 모드 진입 시 배치 Replay를 중단한다."""
        mock_repo = MagicMock()
        entries = [_make_entry(id=i) for i in range(1, 15)]
        mock_repo.find_replayable.return_value = entries

        service = MockDLQService(repository=mock_repo)
        service.replay_throttle_aware = MagicMock(return_value=DLQThrottleReplayResult(success=True, entry_id=1))

        throttle = MagicMock()
        # 첫 번째 배치(10개) 후 emergency level=1 감지
        throttle.get_stats.return_value = {"emergency": {"level": 1}}

        result = service.replay_all_throttle_aware(
            throttle=throttle,
            batch_size=10,
            max_entries=100,
        )

        # 첫 10개 처리 후 emergency 체크에서 중단
        assert result.early_stop_reason == "emergency_mode_activated"
        assert result.skipped > 0

    def test_batch_replay_counts_success_and_failure(self):
        """성공/실패 카운트가 정확히 집계된다."""
        mock_repo = MagicMock()
        entries = [_make_entry(id=i) for i in range(1, 4)]
        mock_repo.find_replayable.return_value = entries

        service = MockDLQService(repository=mock_repo)
        results_sequence = [
            DLQThrottleReplayResult(success=True, entry_id=1),
            DLQThrottleReplayResult(success=False, entry_id=2, error="failed"),
            DLQThrottleReplayResult(success=True, entry_id=3),
        ]
        service.replay_throttle_aware = MagicMock(side_effect=results_sequence)

        throttle = MagicMock()
        throttle.get_stats.return_value = {"emergency": {"level": 0}}

        result = service.replay_all_throttle_aware(
            throttle=throttle,
            batch_size=10,
        )

        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1
        assert result.skipped == 0
