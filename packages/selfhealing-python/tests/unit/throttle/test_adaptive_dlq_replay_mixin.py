"""
AdaptiveThrottle DLQ Replay Mixin 단위 테스트.

테스트 대상: selfhealing.services.throttle.adaptive_dlq_replay.ThrottleDLQReplayMixin

테스트 시나리오:
1. Hedging 보조 요청 필터링 (hedged=True → DLQ 저장 스킵)
2. tier_id 기반 샘플링 (critical=100%, standard=sampling_rate, non_essential=스킵)
3. trace_id metadata 보존
4. Recovery 이벤트 수신 시 자동 Replay 트리거
5. recovery_percent 50% 미만 시 Replay 스킵
6. Throttle 건강 상태 확인 (_is_healthy_for_dlq_replay)
7. Adaptive Pacing 간격 계산 (_calculate_adaptive_replay_interval)
8. DLQ 서비스 불가 시 Fail-Open
"""

from unittest.mock import MagicMock, patch

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


class TestHedgingRequestFilter:
    """Hedging 보조 요청 DLQ 저장 필터링 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_hedged_request_skips_dlq_store(self):
        """hedged=True인 보조 요청은 DLQ에 저장하지 않는다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "hedged": True}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_not_called()

    def test_primary_request_stores_to_dlq(self):
        """hedged=False인 원본 요청은 DLQ에 정상 저장한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "hedged": False, "tier_id": "critical"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_called_once()

    def test_missing_hedged_field_defaults_to_store(self):
        """hedged 필드 없으면 기본적으로 DLQ에 저장한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "tier_id": "critical"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_called_once()


class TestTierIdSampling:
    """tier_id 기반 DLQ 저장 샘플링 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_critical_tier_always_stored(self):
        """critical tier는 항상 100% DLQ에 저장한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "payment", "tier_id": "critical"}
        throttle.store_throttle_rejection_to_dlq(context, "full_stop")

        mock_dlq.store_failure.assert_called_once()

    def test_non_essential_tier_never_stored(self):
        """non_essential tier는 DLQ에 저장하지 않는다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "log", "tier_id": "non_essential"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_not_called()

    def test_standard_tier_with_zero_sampling_rate_skips(self):
        """standard tier에서 sampling_rate=0.0이면 저장하지 않는다."""
        throttle = _make_throttle(initial_limit=100, dlq_store_sampling_rate=0.0)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "tier_id": "standard"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_not_called()

    def test_standard_tier_with_full_sampling_rate_stores(self):
        """standard tier에서 sampling_rate=1.0이면 항상 저장한다."""
        throttle = _make_throttle(initial_limit=100, dlq_store_sampling_rate=1.0)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "tier_id": "standard"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        mock_dlq.store_failure.assert_called_once()

    def test_default_tier_is_standard(self):
        """tier_id 미지정 시 기본값 standard로 처리한다."""
        throttle = _make_throttle(initial_limit=100, dlq_store_sampling_rate=1.0)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test"}  # tier_id 없음
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        # standard 기본값이므로 sampling_rate=1.0 → 저장
        mock_dlq.store_failure.assert_called_once()


class TestTraceIdPreservation:
    """trace_id metadata 보존 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_trace_id_included_in_metadata(self):
        """DLQ 저장 시 original_trace_id가 metadata에 포함된다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {
            "domain": "test",
            "trace_id": "abc-123-trace",
            "tier_id": "critical",
        }
        throttle.store_throttle_rejection_to_dlq(context, "full_stop")

        call_kwargs = mock_dlq.store_failure.call_args
        metadata = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
        assert metadata["original_trace_id"] == "abc-123-trace"

    def test_tier_id_included_in_metadata(self):
        """DLQ 저장 시 tier_id가 metadata에 포함된다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "tier_id": "critical"}
        throttle.store_throttle_rejection_to_dlq(context, "capacity_exceeded")

        call_kwargs = mock_dlq.store_failure.call_args
        metadata = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
        assert metadata["tier_id"] == "critical"


class TestThrottleStateInMetadata:
    """Throttle 상태 정보 metadata 포함 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_throttle_state_recorded_in_metadata(self):
        """DLQ 저장 시 throttle_state가 metadata에 포함된다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        context = {"domain": "test", "tier_id": "critical"}
        throttle.store_throttle_rejection_to_dlq(context, "emergency_level_3")

        call_kwargs = mock_dlq.store_failure.call_args
        metadata = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
        throttle_state = metadata["throttle_state"]

        assert "current_limit" in throttle_state
        assert "initial_limit" in throttle_state
        assert throttle_state["initial_limit"] == throttle.config.initial_limit
        assert throttle_state["rejection_reason"] == "emergency_level_3"


class TestRejectionReason:
    """거부 사유 결정 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_full_stop_reason(self):
        """Full Stop 활성화 시 'full_stop' 사유 반환."""
        throttle = _make_throttle(initial_limit=100)
        throttle._full_stop_active = True

        assert throttle.get_rejection_reason() == "full_stop"

    def test_emergency_level_3_reason(self):
        """Emergency Level 3 시 사유에 레벨 포함."""
        throttle = _make_throttle(initial_limit=100)
        throttle._emergency_level = 3

        assert throttle.get_rejection_reason() == "emergency_level_3"

    def test_limit_exhausted_reason(self):
        """limit이 0 이하일 때 'limit_exhausted' 반환."""
        throttle = _make_throttle(initial_limit=100)
        throttle._current_limit = 0

        assert throttle.get_rejection_reason() == "limit_exhausted"

    def test_capacity_exceeded_default_reason(self):
        """기본 상태에서는 'capacity_exceeded' 반환."""
        throttle = _make_throttle(initial_limit=100)

        assert throttle.get_rejection_reason() == "capacity_exceeded"


class TestRecoveryReplayTrigger:
    """Recovery 이벤트 수신 시 DLQ Replay 트리거 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_low_recovery_percent_skips_replay(self):
        """recovery_percent가 replay_min_recovery_percent 미만이면 Replay 스킵."""
        throttle = _make_throttle(initial_limit=100, replay_min_recovery_percent=50.0)
        mock_dlq = MagicMock()
        throttle._dlq_service = mock_dlq

        event_data = {"previous_limit": 10, "new_limit": 30}
        event = MagicMock()
        event.data = event_data

        throttle._on_recovery_trigger_dlq_replay(event)

        mock_dlq.get_replayable_entries.assert_not_called()

    def test_sufficient_recovery_triggers_replay(self):
        """recovery_percent가 충분하면 비동기 Replay 스레드 시작."""
        throttle = _make_throttle(initial_limit=100, replay_min_recovery_percent=50.0)
        mock_dlq = MagicMock()
        mock_dlq.get_replayable_entries.return_value = []
        throttle._dlq_service = mock_dlq

        event_data = {"previous_limit": 10, "new_limit": 80}
        event = MagicMock()
        event.data = event_data

        with patch("threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance

            throttle._on_recovery_trigger_dlq_replay(event)

            mock_thread.assert_called_once()
            mock_thread_instance.start.assert_called_once()

    def test_no_dlq_service_skips_replay(self):
        """DLQ 서비스 없으면 Replay 시도하지 않음."""
        throttle = _make_throttle(initial_limit=100)
        throttle._dlq_service = None

        event = MagicMock()
        event.data = {"previous_limit": 10, "new_limit": 80}

        # 에러 없이 정상 리턴
        throttle._on_recovery_trigger_dlq_replay(event)


class TestIsHealthyForDlqReplay:
    """Replay 계속 가능 여부 판단 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_full_stop_blocks_replay(self):
        """Full Stop 시 Replay 불가."""
        throttle = _make_throttle(initial_limit=100)
        throttle._full_stop_active = True

        assert throttle._is_healthy_for_dlq_replay() is False

    def test_emergency_level_blocks_replay(self):
        """Emergency Level > 0이면 Replay 불가."""
        throttle = _make_throttle(initial_limit=100)
        throttle._emergency_level = 1

        assert throttle._is_healthy_for_dlq_replay() is False

    def test_low_capacity_blocks_replay(self):
        """capacity_ratio 50% 미만이면 Replay 불가."""
        throttle = _make_throttle(initial_limit=100)
        throttle._current_limit = 40  # 40% capacity

        assert throttle._is_healthy_for_dlq_replay() is False

    def test_healthy_state_allows_replay(self):
        """정상 상태에서는 Replay 가능."""
        throttle = _make_throttle(initial_limit=100)
        throttle._current_limit = 80

        assert throttle._is_healthy_for_dlq_replay() is True


class TestAdaptiveReplayInterval:
    """capacity_ratio 기반 동적 Replay 간격 계산 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_high_capacity_returns_base_interval(self):
        """90%+ capacity에서 기본 간격 반환."""
        config = ThrottleConfig(initial_limit=100, replay_interval_ms=100)
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        throttle = AdaptiveThrottle(config)
        throttle._current_limit = 95

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == config.replay_interval_ms

    def test_moderate_capacity_doubles_interval(self):
        """70~90% capacity에서 2배 간격."""
        config = ThrottleConfig(initial_limit=100, replay_interval_ms=100)
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        throttle = AdaptiveThrottle(config)
        throttle._current_limit = 75

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == config.replay_interval_ms * 2

    def test_low_capacity_quadruples_interval(self):
        """50~70% capacity에서 4배 간격."""
        config = ThrottleConfig(initial_limit=100, replay_interval_ms=100)
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        throttle = AdaptiveThrottle(config)
        throttle._current_limit = 55

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == config.replay_interval_ms * 4

    def test_very_low_capacity_10x_interval(self):
        """50% 미만에서 10배 간격."""
        config = ThrottleConfig(initial_limit=100, replay_interval_ms=100)
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        throttle = AdaptiveThrottle(config)
        throttle._current_limit = 30

        interval = throttle._calculate_adaptive_replay_interval()
        assert interval == config.replay_interval_ms * 10


class TestDlqServiceFailOpen:
    """DLQ 서비스 불가 시 Fail-Open 동작 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_store_rejection_noop_without_dlq_service(self):
        """DLQ 서비스 없으면 store_rejection은 아무 동작도 하지 않는다."""
        throttle = _make_throttle(initial_limit=100)
        throttle._dlq_service = None

        # 에러 없이 정상 리턴
        throttle.store_throttle_rejection_to_dlq(
            {"domain": "test", "tier_id": "critical"},
            "full_stop",
        )

    def test_store_failure_exception_handled_gracefully(self):
        """DLQ store_failure 예외 발생 시 로그만 남기고 계속 진행."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        mock_dlq.store_failure.side_effect = RuntimeError("DLQ unavailable")
        throttle._dlq_service = mock_dlq

        # 에러 없이 정상 리턴 (Fail-Open)
        throttle.store_throttle_rejection_to_dlq(
            {"domain": "test", "tier_id": "critical"},
            "full_stop",
        )


class TestExecuteDlqReplayOnRecovery:
    """Recovery 후 DLQ Replay 실행 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def test_no_pending_entries_returns_silently(self):
        """Replay 대상 엔트리가 없으면 조용히 리턴한다."""
        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()
        mock_dlq.get_replayable_entries.return_value = []
        throttle._dlq_service = mock_dlq

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        mock_dlq.get_replayable_entries.assert_called_once()
        mock_dlq.replay_throttle_aware.assert_not_called()

    def test_exhausted_retries_entry_resolved_as_permanently_failed(self):
        """can_retry=False인 엔트리는 permanently_failed로 처리한다."""
        from selfhealing.interfaces.repositories import FailedOperationData

        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()

        exhausted_entry = FailedOperationData(
            id=1,
            domain="throttle_rejection",
            failure_type="throttle_rejected",
            status="pending",
            retry_count=2,
            max_retries=2,  # can_retry = False
        )
        mock_dlq.get_replayable_entries.return_value = [exhausted_entry]
        throttle._dlq_service = mock_dlq

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        mock_dlq.resolve_entry.assert_called_once_with(exhausted_entry.id, notes="permanently_failed")

    def test_healthy_entries_replayed_via_replay_throttle_aware(self):
        """can_retry=True인 엔트리는 replay_throttle_aware로 Replay한다."""
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

        mock_dlq.replay_throttle_aware.assert_called_once_with(entry_id=entry.id, throttle=throttle)

    def test_stops_replay_when_health_degrades(self):
        """Replay 중 Throttle 건강 상태 악화 시 중단한다."""
        from selfhealing.interfaces.repositories import FailedOperationData
        from selfhealing.services.dlq_models import DLQThrottleReplayResult

        throttle = _make_throttle(initial_limit=100)
        mock_dlq = MagicMock()

        entries = [
            FailedOperationData(
                id=i,
                domain="throttle_rejection",
                failure_type="throttle_rejected",
                status="pending",
                retry_count=0,
                max_retries=2,
            )
            for i in range(5)
        ]
        mock_dlq.get_replayable_entries.return_value = entries
        mock_dlq.replay_throttle_aware.return_value = DLQThrottleReplayResult(success=True, entry_id=0)
        throttle._dlq_service = mock_dlq

        # 첫 번째 엔트리 처리 후 Full Stop 활성화
        original_replay = mock_dlq.replay_throttle_aware

        call_count = 0

        def replay_and_degrade(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                throttle._full_stop_active = True
            return DLQThrottleReplayResult(success=True, entry_id=0)

        mock_dlq.replay_throttle_aware.side_effect = replay_and_degrade

        throttle._execute_dlq_replay_on_recovery(recovery_percent=80.0)

        # 2번째부터 건강 확인 실패로 중단 (첫째는 건강 확인 먼저)
        assert mock_dlq.replay_throttle_aware.call_count <= 2
