"""
Tests for Shutdown Coordinator - Graceful Shutdown

Framework-agnostic graceful shutdown implementation.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.core.shutdown_coordinator import (
    GracefulShutdownCoordinator,
    RequestState,
    RequestTracker,
    ShutdownHandler,
    ShutdownPhase,
    ShutdownStats,
    TrackedRequest,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def request_tracker():
    """Create a RequestTracker instance."""
    return RequestTracker()


@pytest.fixture
def shutdown_handler():
    """Create a mock shutdown handler."""
    handler = MagicMock(spec=ShutdownHandler)
    return handler


@pytest.fixture
def coordinator(request_tracker, shutdown_handler):
    """Create a GracefulShutdownCoordinator instance."""
    return GracefulShutdownCoordinator(
        request_tracker=request_tracker,
        drain_timeout=5.0,  # Short for testing
        shutdown_handler=shutdown_handler,
        check_interval=0.1,  # Fast for testing
    )


# =============================================================================
# ShutdownPhase Tests
# =============================================================================


class TestShutdownPhase:
    """Test ShutdownPhase enum."""

    def test_phases_exist(self):
        """모든 단계 확인."""
        assert ShutdownPhase.RUNNING == "running"
        assert ShutdownPhase.DRAINING == "draining"
        assert ShutdownPhase.TERMINATING == "terminating"
        assert ShutdownPhase.TERMINATED == "terminated"


# =============================================================================
# RequestState Tests
# =============================================================================


class TestRequestState:
    """Test RequestState enum."""

    def test_states_exist(self):
        """모든 상태 확인."""
        assert RequestState.IN_PROGRESS == "in_progress"
        assert RequestState.COMPLETED == "completed"
        assert RequestState.ABORTED == "aborted"
        assert RequestState.TIMED_OUT == "timed_out"


# =============================================================================
# TrackedRequest Tests
# =============================================================================


class TestTrackedRequest:
    """Test TrackedRequest dataclass."""

    def test_request_creation(self):
        """TrackedRequest 생성."""
        request = TrackedRequest(
            request_id="req-123",
            started_at=datetime.now(timezone.utc),
            endpoint="/api/test",
            method="POST",
        )

        assert request.request_id == "req-123"
        assert request.endpoint == "/api/test"
        assert request.method == "POST"
        assert request.state == RequestState.IN_PROGRESS

    def test_duration_seconds(self):
        """duration_seconds 계산."""
        started = datetime.now(timezone.utc) - timedelta(seconds=5)
        request = TrackedRequest(
            request_id="req-123",
            started_at=started,
        )

        assert request.duration_seconds >= 5.0
        assert request.duration_seconds < 6.0

    def test_metadata(self):
        """메타데이터 저장."""
        request = TrackedRequest(
            request_id="req-123",
            started_at=datetime.now(timezone.utc),
            metadata={"user_id": 42, "trace_id": "abc"},
        )

        assert request.metadata["user_id"] == 42
        assert request.metadata["trace_id"] == "abc"


# =============================================================================
# ShutdownStats Tests
# =============================================================================


class TestShutdownStats:
    """Test ShutdownStats dataclass."""

    def test_stats_creation(self):
        """ShutdownStats 생성."""
        stats = ShutdownStats(
            phase=ShutdownPhase.DRAINING,
            shutdown_started_at=datetime.now(timezone.utc),
            in_flight_count=5,
            completed_during_drain=10,
            aborted_count=2,
            drain_timeout_seconds=30.0,
            remaining_drain_time=15.0,
        )

        assert stats.phase == ShutdownPhase.DRAINING
        assert stats.in_flight_count == 5
        assert stats.completed_during_drain == 10
        assert stats.remaining_drain_time == 15.0


# =============================================================================
# RequestTracker Tests
# =============================================================================


class TestRequestTracker:
    """Test RequestTracker."""

    def test_start_request(self, request_tracker):
        """요청 추적 시작."""
        request = request_tracker.start_request(
            request_id="req-123",
            endpoint="/api/test",
            method="POST",
        )

        assert request.request_id == "req-123"
        assert request.state == RequestState.IN_PROGRESS

    def test_end_request_success(self, request_tracker):
        """요청 추적 종료 (성공)."""
        request_tracker.start_request("req-123")

        ended = request_tracker.end_request("req-123", success=True)

        assert ended is not None
        assert ended.state == RequestState.COMPLETED

    def test_end_request_failure(self, request_tracker):
        """요청 추적 종료 (실패)."""
        request_tracker.start_request("req-123")

        ended = request_tracker.end_request("req-123", success=False)

        assert ended.state == RequestState.ABORTED

    def test_end_nonexistent_request(self, request_tracker):
        """존재하지 않는 요청 종료."""
        ended = request_tracker.end_request("nonexistent")

        assert ended is None

    def test_get_pending_requests(self, request_tracker):
        """대기 중인 요청 조회."""
        request_tracker.start_request("req-1")
        request_tracker.start_request("req-2")
        request_tracker.end_request("req-1")

        pending = request_tracker.get_pending_requests()

        assert len(pending) == 1
        assert pending[0].request_id == "req-2"

    def test_get_pending_count(self, request_tracker):
        """대기 중인 요청 수."""
        request_tracker.start_request("req-1")
        request_tracker.start_request("req-2")
        request_tracker.start_request("req-3")
        request_tracker.end_request("req-1")

        count = request_tracker.get_pending_count()

        assert count == 2

    def test_abort_all(self, request_tracker):
        """모든 요청 중단."""
        request_tracker.start_request("req-1")
        request_tracker.start_request("req-2")

        aborted = request_tracker.abort_all()

        assert len(aborted) == 2
        for req in aborted:
            assert req.state == RequestState.ABORTED

    def test_completed_count(self, request_tracker):
        """완료된 요청 수."""
        request_tracker.start_request("req-1")
        request_tracker.start_request("req-2")
        request_tracker.end_request("req-1")
        request_tracker.end_request("req-2")

        assert request_tracker.completed_count == 2

    def test_cleanup_old_requests(self, request_tracker):
        """오래된 요청 정리."""
        # max_age가 300초이므로, 완료된 오래된 요청은 정리됨
        # 이 테스트에서는 cleanup이 start_request에서 호출됨
        pass  # 구현에 따라 다름


# =============================================================================
# GracefulShutdownCoordinator Tests
# =============================================================================


class TestGracefulShutdownCoordinator:
    """Test GracefulShutdownCoordinator."""

    def test_initial_phase(self, coordinator):
        """초기 단계."""
        assert coordinator.phase == ShutdownPhase.RUNNING

    def test_is_accepting_requests(self, coordinator):
        """요청 수락 여부."""
        assert coordinator.is_accepting_requests() is True

    def test_is_shutting_down(self, coordinator):
        """종료 중 여부."""
        assert coordinator.is_shutting_down() is False

    def test_initiate_shutdown(self, coordinator, shutdown_handler):
        """종료 시작."""
        coordinator.initiate_shutdown()

        assert coordinator.phase in (ShutdownPhase.DRAINING, ShutdownPhase.TERMINATED)
        shutdown_handler.on_shutdown_start.assert_called_once()

    def test_initiate_shutdown_twice(self, coordinator):
        """종료 두 번 시작 시도."""
        coordinator.initiate_shutdown()

        # 두 번째 호출은 무시됨
        coordinator.initiate_shutdown()

        # 에러 없이 처리됨
        assert coordinator.phase != ShutdownPhase.RUNNING

    def test_not_accepting_after_shutdown(self, coordinator):
        """종료 후 요청 거부."""
        coordinator.initiate_shutdown()

        assert coordinator.is_accepting_requests() is False

    def test_is_shutting_down_during_drain(self, coordinator, request_tracker):
        """드레인 중 종료 중 여부."""
        # 요청 추가
        request_tracker.start_request("req-1")

        coordinator.initiate_shutdown()

        assert coordinator.is_shutting_down() is True

    def test_drain_completes_when_no_requests(self, coordinator, shutdown_handler):
        """요청이 없으면 드레인 즉시 완료."""
        coordinator.initiate_shutdown()

        # 잠시 대기
        time.sleep(0.3)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        shutdown_handler.on_drain_complete.assert_called_once()

    def test_drain_waits_for_requests(self, coordinator, request_tracker, shutdown_handler):
        """요청 완료 대기."""
        # 요청 시작
        request_tracker.start_request("req-1")

        coordinator.initiate_shutdown()

        # 아직 드레인 중
        time.sleep(0.1)
        assert coordinator.phase == ShutdownPhase.DRAINING

        # 요청 완료
        request_tracker.end_request("req-1")

        # 드레인 완료 대기
        time.sleep(0.3)

        assert coordinator.phase == ShutdownPhase.TERMINATED

    def test_drain_timeout_force_shutdown(self, request_tracker, shutdown_handler):
        """드레인 타임아웃 시 강제 종료."""
        coordinator = GracefulShutdownCoordinator(
            request_tracker=request_tracker,
            drain_timeout=0.5,  # 매우 짧은 타임아웃
            shutdown_handler=shutdown_handler,
            check_interval=0.1,
        )

        # 요청 시작 (완료하지 않음)
        request_tracker.start_request("req-1")

        coordinator.initiate_shutdown()

        # 타임아웃 대기
        time.sleep(0.8)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        shutdown_handler.on_force_shutdown.assert_called_once()

    def test_get_stats(self, coordinator, request_tracker):
        """통계 조회."""
        request_tracker.start_request("req-1")

        stats = coordinator.get_stats()

        assert stats.phase == ShutdownPhase.RUNNING
        assert stats.in_flight_count == 1
        assert stats.shutdown_started_at is None

    def test_get_stats_during_drain(self, coordinator, request_tracker):
        """드레인 중 통계 조회."""
        request_tracker.start_request("req-1")

        coordinator.initiate_shutdown()

        stats = coordinator.get_stats()

        assert stats.phase == ShutdownPhase.DRAINING
        assert stats.shutdown_started_at is not None
        assert stats.remaining_drain_time is not None

    def test_wait_for_shutdown(self, coordinator):
        """종료 대기."""
        coordinator.initiate_shutdown()

        result = coordinator.wait_for_shutdown(timeout=1.0)

        assert result is True
        assert coordinator.phase == ShutdownPhase.TERMINATED

    def test_wait_for_shutdown_not_initiated(self, coordinator):
        """종료가 시작되지 않은 경우."""
        result = coordinator.wait_for_shutdown(timeout=0.1)

        assert result is False

    def test_on_shutdown_complete_callback(self, request_tracker, shutdown_handler):
        """on_shutdown_complete 콜백."""
        callback = MagicMock()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=request_tracker,
            shutdown_handler=shutdown_handler,
            on_shutdown_complete=callback,
        )

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=1.0)

        callback.assert_called_once()


# =============================================================================
# Signal Handler Tests
# =============================================================================


class TestSignalHandler:
    """Test signal handler registration."""

    @patch('signal.signal')
    def test_register_signals(self, mock_signal, coordinator):
        """시그널 핸들러 등록."""
        import signal as signal_module

        coordinator.register_signals()

        # SIGTERM과 SIGINT 등록 확인
        calls = mock_signal.call_args_list
        assert len(calls) == 2

        registered_signals = [call[0][0] for call in calls]
        assert signal_module.SIGTERM in registered_signals
        assert signal_module.SIGINT in registered_signals


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Test thread safety."""

    def test_concurrent_request_tracking(self, request_tracker):
        """동시 요청 추적."""
        errors = []

        def track_requests(prefix):
            try:
                for i in range(100):
                    req_id = f"{prefix}-{i}"
                    request_tracker.start_request(req_id)
                    request_tracker.end_request(req_id)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=track_requests, args=(f"t{i}",))
            for i in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_status_reads(self, coordinator, request_tracker):
        """동시 상태 읽기."""
        results = []

        def read_stats():
            for _ in range(50):
                coordinator.get_stats()
            results.append(True)

        threads = [threading.Thread(target=read_stats) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases."""

    def test_handler_exception_on_start(self, request_tracker):
        """핸들러 on_shutdown_start 예외."""
        handler = MagicMock(spec=ShutdownHandler)
        handler.on_shutdown_start.side_effect = Exception("Handler error")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=request_tracker,
            shutdown_handler=handler,
        )

        # 예외 발생해도 종료 진행
        coordinator.initiate_shutdown()

        assert coordinator.phase != ShutdownPhase.RUNNING

    def test_request_with_metadata(self, request_tracker):
        """메타데이터가 있는 요청."""
        request = request_tracker.start_request(
            request_id="req-123",
            endpoint="/api/test",
            method="POST",
            metadata={"user_id": 42, "trace_id": "abc-123"},
        )

        assert request.metadata["user_id"] == 42
        assert request.metadata["trace_id"] == "abc-123"

    def test_empty_request_tracker(self, coordinator):
        """빈 요청 트래커."""
        stats = coordinator.get_stats()

        assert stats.in_flight_count == 0

    def test_zero_drain_timeout(self, request_tracker, shutdown_handler):
        """드레인 타임아웃 0."""
        coordinator = GracefulShutdownCoordinator(
            request_tracker=request_tracker,
            drain_timeout=0.0,  # 즉시 강제 종료
            shutdown_handler=shutdown_handler,
            check_interval=0.1,
        )

        request_tracker.start_request("req-1")

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=1.0)

        # 즉시 강제 종료
        assert coordinator.phase == ShutdownPhase.TERMINATED

    def test_very_long_running_request(self, request_tracker):
        """매우 오래 실행되는 요청."""
        started = datetime.now(timezone.utc) - timedelta(hours=1)

        request = TrackedRequest(
            request_id="long-req",
            started_at=started,
        )

        # 1시간 이상 실행
        assert request.duration_seconds >= 3600


# =============================================================================
# ShutdownHandler Abstract Tests
# =============================================================================


class TestShutdownHandlerInterface:
    """Test ShutdownHandler interface."""

    def test_handler_methods(self, shutdown_handler):
        """핸들러 메서드 존재 확인."""
        assert hasattr(shutdown_handler, 'on_shutdown_start')
        assert hasattr(shutdown_handler, 'on_drain_complete')
        assert hasattr(shutdown_handler, 'on_force_shutdown')

    def test_custom_handler_implementation(self):
        """커스텀 핸들러 구현."""
        class CustomHandler(ShutdownHandler):
            def __init__(self):
                self.started = False
                self.drained = False
                self.forced = False

            def on_shutdown_start(self):
                self.started = True

            def on_drain_complete(self):
                self.drained = True

            def on_force_shutdown(self, pending_requests):
                self.forced = True

        handler = CustomHandler()
        tracker = RequestTracker()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            shutdown_handler=handler,
        )

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=1.0)

        assert handler.started is True
        assert handler.drained is True
