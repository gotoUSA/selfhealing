"""
DLQ Replay Prometheus 메트릭 기록(wiring) 단위 테스트.

테스트 대상:
  - adaptive_dlq_replay.py: store/replay 메서드 내 메트릭 기록
  - replay_operations.py: TTL 만료 메트릭 기록
  - store_operations.py: Fallback 채널 메트릭 기록

테스트 시나리오:
1. Hedging 스킵 시 hedged_skipped_total 메트릭 증가
2. non_essential 스킵 시 sampled_out_total 메트릭 증가
3. sampling_rate 스킵 시 sampled_out_total 메트릭 증가
4. DLQ 저장 성공 시 dlq_stored_total 메트릭 증가
5. permanently_failed 시 permanently_failed_total 메트릭 증가
6. Replay 성공/실패 시 recovery_replay_total 메트릭 증가
7. Adaptive interval 변경 시 adaptive_interval_ms 메트릭 set
8. TTL 만료 시 ttl_expired_total 메트릭 증가
9. Fallback 채널별(LMDB/JSONL/stderr) fallback_total 메트릭 증가
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from selfhealing.services.throttle.config import ThrottleConfig


def _make_throttle(**overrides):
    """테스트용 AdaptiveThrottle 생성 헬퍼."""
    from selfhealing.services.throttle.adaptive import AdaptiveThrottle

    defaults = {
        "initial_limit": 100,
        "sample_interval_ms": 0,
    }
    defaults.update(overrides)
    config = ThrottleConfig(**defaults)
    return AdaptiveThrottle(config)


class TestStoreMetricsWiring:
    """store_throttle_rejection_to_dlq() 메트릭 기록 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_rejection_hedged_skipped_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_hedged_skip_increments_hedged_skipped_metric(self, mock_metric):
        """hedged=True 스킵 시 hedged_skipped_total 메트릭이 증가한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        throttle.store_throttle_rejection_to_dlq(
            {"domain": "test", "hedged": True},
            "capacity_exceeded",
        )

        mock_metric.labels.assert_called_once_with(domain="test")
        mock_metric.labels.return_value.inc.assert_called_once()

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_rejection_sampled_out_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_non_essential_skip_increments_sampled_out_metric(self, mock_metric):
        """non_essential 티어 스킵 시 sampled_out_total 메트릭이 증가한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        throttle.store_throttle_rejection_to_dlq(
            {"domain": "log", "tier_id": "non_essential"},
            "capacity_exceeded",
        )

        mock_metric.labels.assert_called_once_with(tier_id="non_essential", reason="non_essential")
        mock_metric.labels.return_value.inc.assert_called_once()

    @patch("selfhealing.services.throttle.adaptive_dlq_replay.random")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_rejection_sampled_out_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_sampling_rate_skip_increments_sampled_out_metric(self, mock_metric, mock_random):
        """standard 티어 sampling_rate 초과 시 sampled_out_total 메트릭이 증가한다."""
        mock_random.random.return_value = 0.99  # > sampling_rate 0.5

        throttle = _make_throttle(initial_limit=100, dlq_store_sampling_rate=0.5)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        throttle.store_throttle_rejection_to_dlq(
            {"domain": "test", "tier_id": "standard"},
            "capacity_exceeded",
        )

        mock_metric.labels.assert_called_once_with(tier_id="standard", reason="sampling_rate")

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_rejection_dlq_stored_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_successful_store_increments_stored_metric(self, mock_metric):
        """DLQ 저장 성공 시 dlq_stored_total 메트릭이 증가한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        throttle.store_throttle_rejection_to_dlq(
            {"domain": "payment", "tier_id": "critical"},
            "full_stop",
        )

        mock_metric.labels.assert_called_once_with(reason="full_stop", domain="payment")
        mock_metric.labels.return_value.inc.assert_called_once()


class TestReplayMetricsWiring:
    """_execute_dlq_replay_on_recovery() 메트릭 기록 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_replay_permanently_failed_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_permanently_failed_increments_metric(self, mock_metric):
        """can_retry=False 엔트리 처리 시 permanently_failed_total 메트릭이 증가한다."""
        from selfhealing.interfaces.repositories import FailedOperationData

        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()

        exhausted_entry = FailedOperationData(
            id=1,
            domain="throttle_rejection",
            failure_type="throttle_rejected",
            status="pending",
            retry_count=2,
            max_retries=2,
        )
        mock_dlq.get_replayable_entries.return_value = [exhausted_entry]
        throttle._dlq_service = mock_dlq

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        mock_metric.labels.assert_called_with(domain="throttle_rejection")
        mock_metric.labels.return_value.inc.assert_called()

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_recovery_replay_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_replay_success_increments_succeeded_metric(self, mock_metric):
        """Replay 성공 시 recovery_replay_total(result=succeeded) 메트릭이 증가한다."""
        from selfhealing.interfaces.repositories import FailedOperationData
        from selfhealing.services.dlq_models import DLQThrottleReplayResult

        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()

        entry = FailedOperationData(
            id=1,
            domain="throttle_rejection",
            failure_type="throttle_rejected",
            status="pending",
            retry_count=0,
            max_retries=2,
        )
        mock_dlq.get_replayable_entries.return_value = [entry]
        mock_dlq.replay_throttle_aware.return_value = DLQThrottleReplayResult(success=True, entry_id=1)
        throttle._dlq_service = mock_dlq

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        mock_metric.labels.assert_called_with(domain="throttle_rejection", result="succeeded")

    @patch("selfhealing.services.throttle.adaptive_dlq_replay._throttle_recovery_replay_total")
    @patch("selfhealing.services.throttle.adaptive_dlq_replay._DLQ_METRICS_AVAILABLE", True)
    def test_replay_failure_increments_failed_metric(self, mock_metric):
        """Replay 실패 시 recovery_replay_total(result=failed) 메트릭이 증가한다."""
        from selfhealing.interfaces.repositories import FailedOperationData
        from selfhealing.services.dlq_models import DLQThrottleReplayResult

        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()

        entry = FailedOperationData(
            id=1,
            domain="throttle_rejection",
            failure_type="throttle_rejected",
            status="pending",
            retry_count=0,
            max_retries=2,
        )
        mock_dlq.get_replayable_entries.return_value = [entry]
        mock_dlq.replay_throttle_aware.return_value = DLQThrottleReplayResult(success=False, error="handler failed")
        throttle._dlq_service = mock_dlq

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        mock_metric.labels.assert_called_with(domain="throttle_rejection", result="failed")


class TestTtlExpiredMetricWiring:
    """replay_throttle_aware() TTL 만료 메트릭 기록 테스트."""

    @patch("selfhealing.services.metrics.definitions.throttle_replay_ttl_expired_total")
    def test_ttl_expired_increments_metric(self, mock_metric):
        """TTL 만료 엔트리 처리 시 ttl_expired_total 메트릭이 증가한다."""
        from datetime import datetime, timedelta, timezone
        from selfhealing.interfaces.repositories import FailedOperationData
        from selfhealing.services.dlq.replay_operations import ReplayOperationsMixin
        from selfhealing.services.dlq.query_operations import QueryOperationsMixin

        class TestDLQ(ReplayOperationsMixin, QueryOperationsMixin):
            pass

        expired_entry = FailedOperationData(
            id=1,
            domain="throttle_rejection",
            failure_type="throttle_rejected",
            status="pending",
            retry_count=0,
            max_retries=2,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        dlq = TestDLQ()
        dlq.repository = MagicMock()
        dlq.repository.get_by_id.return_value = expired_entry

        mock_throttle = MagicMock()
        result = dlq.replay_throttle_aware(entry_id=1, throttle=mock_throttle)

        assert result.success is False
        assert "expired" in result.error.lower()
        mock_metric.labels.assert_called_once_with(domain="throttle_rejection")
        mock_metric.labels.return_value.inc.assert_called_once()


class TestFallbackMetricWiring:
    """_write_to_local_fallback() Fallback 채널 메트릭 기록 테스트."""

    @patch("selfhealing.services.metrics.definitions.throttle_dlq_fallback_total")
    @patch("selfhealing.audit.persistence.disk_buffer.DiskBufferAdapter")
    def test_lmdb_fallback_increments_disk_persistent_buffer_metric(self, mock_adapter_cls, mock_metric):
        """LMDB 1차 Fallback 시 channel=disk_persistent_buffer 메트릭이 증가한다."""
        from selfhealing.services.dlq.store_operations import StoreOperationsMixin

        class TestStore(StoreOperationsMixin):
            pass

        store = TestStore()
        store.repository = MagicMock()

        mock_buffer = MagicMock()
        mock_adapter_cls.get_instance.return_value = mock_buffer

        result = store._write_to_local_fallback({"domain": "test"}, "db_error")

        assert result == "disk_persistent_buffer://dlq_fallback"
        mock_metric.labels.assert_called_with(channel="disk_persistent_buffer")
        mock_metric.labels.return_value.inc.assert_called()
