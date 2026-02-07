"""
Stage 27: Graceful Shutdown Tests

Scenarios:
1. Normal request tracking
2. Graceful shutdown with drain
3. Timeout and forced shutdown
4. Signal handling
5. Request context manager
"""

import pytest
import time
import threading
from datetime import datetime, timezone
from unittest.mock import Mock

from selfhealing.core.shutdown_coordinator import (
    ShutdownPhase,
    RequestState,
    RequestTracker,
    GracefulShutdownCoordinator,
    ShutdownHandler,
    TrackedRequest,
)
from selfhealing.core.request_context import (
    RequestLifecycleContext,
    track_request,
)


class TestRequestTracking:
    """Request tracking tests"""

    def test_track_request_lifecycle(self):
        """요청 추적 라이프사이클"""
        tracker = RequestTracker()

        # 요청 시작
        request = tracker.start_request(
            request_id="req_001",
            endpoint="/api/orders",
            method="POST",
        )

        assert request.state == RequestState.IN_PROGRESS
        assert tracker.get_pending_count() == 1

        # 요청 완료
        tracker.end_request("req_001", success=True)

        assert tracker.get_pending_count() == 0

    def test_multiple_concurrent_requests(self):
        """동시 다중 요청 추적"""
        tracker = RequestTracker()

        # 여러 요청 시작
        for i in range(10):
            tracker.start_request(f"req_{i}")

        assert tracker.get_pending_count() == 10

        # 일부 완료
        for i in range(5):
            tracker.end_request(f"req_{i}")

        assert tracker.get_pending_count() == 5

    def test_abort_all_requests(self):
        """모든 요청 중단"""
        tracker = RequestTracker()

        for i in range(5):
            tracker.start_request(f"req_{i}")

        aborted = tracker.abort_all()

        assert len(aborted) == 5
        assert all(r.state == RequestState.ABORTED for r in aborted)

    def test_request_with_metadata(self):
        """메타데이터와 함께 요청 추적"""
        tracker = RequestTracker()

        request = tracker.start_request(
            request_id="req_001", endpoint="/api/payments", method="POST", metadata={"user_id": 123, "amount": 1000}
        )

        assert request.metadata["user_id"] == 123
        assert request.metadata["amount"] == 1000

    def test_end_nonexistent_request(self):
        """존재하지 않는 요청 종료"""
        tracker = RequestTracker()

        result = tracker.end_request("nonexistent")

        assert result is None

    def test_completed_count(self):
        """완료된 요청 카운트"""
        tracker = RequestTracker()

        for i in range(3):
            tracker.start_request(f"req_{i}")

        for i in range(3):
            tracker.end_request(f"req_{i}")

        assert tracker.completed_count == 3

    def test_request_duration(self):
        """요청 지속 시간"""
        tracker = RequestTracker()

        request = tracker.start_request("req_001")
        time.sleep(0.1)

        assert request.duration_seconds >= 0.1


class TestGracefulShutdown:
    """Graceful shutdown coordinator tests"""

    def test_normal_shutdown_no_pending(self):
        """대기 요청 없을 때 정상 종료"""
        tracker = RequestTracker()
        on_complete = Mock()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
            on_shutdown_complete=on_complete,
        )

        assert coordinator.is_accepting_requests() is True

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        assert on_complete.called

    def test_drain_pending_requests(self):
        """대기 요청 드레인"""
        tracker = RequestTracker()

        # 요청 시작
        tracker.start_request("req_001")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        # 종료 시작
        coordinator.initiate_shutdown()

        assert coordinator.is_accepting_requests() is False
        assert coordinator.is_shutting_down() is True

        # 요청 완료
        tracker.end_request("req_001")

        # 종료 대기
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        stats = coordinator.get_stats()
        assert stats.aborted_count == 0

    def test_timeout_forces_shutdown(self):
        """타임아웃 시 강제 종료"""
        tracker = RequestTracker()

        # 완료되지 않을 요청
        tracker.start_request("slow_req")

        handler = Mock(spec=ShutdownHandler)

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=0.5,  # 짧은 타임아웃
            shutdown_handler=handler,
        )

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        assert handler.on_force_shutdown.called

        stats = coordinator.get_stats()
        assert stats.aborted_count == 1

    def test_reject_new_requests_during_drain(self):
        """드레인 중 새 요청 거부"""
        tracker = RequestTracker()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        assert coordinator.is_accepting_requests() is True

        coordinator.initiate_shutdown()

        assert coordinator.is_accepting_requests() is False

    def test_shutdown_stats_during_drain(self):
        """드레인 중 통계"""
        tracker = RequestTracker()
        tracker.start_request("req_001")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=10.0,
        )

        coordinator.initiate_shutdown()
        time.sleep(0.1)

        stats = coordinator.get_stats()

        assert stats.phase == ShutdownPhase.DRAINING
        assert stats.in_flight_count == 1
        assert stats.remaining_drain_time is not None
        assert stats.remaining_drain_time < 10.0

    def test_double_initiate_shutdown(self):
        """이중 종료 요청 무시"""
        tracker = RequestTracker()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        coordinator.initiate_shutdown()
        coordinator.initiate_shutdown()  # 두 번째 호출은 무시되어야 함

        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED

    def test_shutdown_handler_callbacks(self):
        """셧다운 핸들러 콜백"""
        tracker = RequestTracker()

        class TestHandler(ShutdownHandler):
            def __init__(self):
                self.start_called = False
                self.drain_called = False
                self.force_called = False

            def on_shutdown_start(self):
                self.start_called = True

            def on_drain_complete(self):
                self.drain_called = True

            def on_force_shutdown(self, pending):
                self.force_called = True

        handler = TestHandler()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
            shutdown_handler=handler,
        )

        coordinator.initiate_shutdown()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert handler.start_called is True
        assert handler.drain_called is True
        assert handler.force_called is False  # 요청이 없으므로 강제 종료 아님


class TestRequestLifecycleContext:
    """Request context manager tests"""

    def test_context_manager_tracking(self):
        """컨텍스트 매니저로 요청 추적"""
        tracker = RequestTracker()

        with RequestLifecycleContext(tracker, endpoint="/api/test") as ctx:
            assert tracker.get_pending_count() == 1
            ctx.set_metadata("user_id", 123)

        assert tracker.get_pending_count() == 0

    def test_context_manager_exception_handling(self):
        """예외 발생 시 실패로 마킹"""
        tracker = RequestTracker()

        try:
            with RequestLifecycleContext(tracker, request_id="fail_req") as ctx:
                raise ValueError("Test error")
        except ValueError:
            pass

        # 요청은 종료되었지만 실패로 기록됨
        assert tracker.get_pending_count() == 0

    def test_track_request_helper(self):
        """track_request 헬퍼 함수"""
        tracker = RequestTracker()

        with track_request(tracker, endpoint="/api/orders") as ctx:
            assert ctx.request_id is not None
            assert tracker.get_pending_count() == 1

        assert tracker.get_pending_count() == 0

    def test_context_mark_failed(self):
        """실패 마킹"""
        tracker = RequestTracker()

        with RequestLifecycleContext(tracker, request_id="req_001") as ctx:
            ctx.mark_failed()

        # 요청이 종료됨
        assert tracker.get_pending_count() == 0

    def test_context_set_metadata(self):
        """메타데이터 설정"""
        tracker = RequestTracker()

        with RequestLifecycleContext(tracker, request_id="req_001") as ctx:
            ctx.set_metadata("order_id", "ORD-123")
            ctx.set_metadata("amount", 5000)

        # 정상 종료
        assert tracker.get_pending_count() == 0


class TestShutdownIntegration:
    """통합 시나리오 테스트"""

    def test_realistic_shutdown_scenario(self):
        """
        실제 시나리오: 배포 중 graceful shutdown
        - 진행 중인 요청 3개
        - 2개는 정상 완료
        - 1개는 타임아웃으로 중단
        """
        tracker = RequestTracker()

        # 진행 중인 요청들
        tracker.start_request("fast_1")
        tracker.start_request("fast_2")
        tracker.start_request("slow_1")  # 이건 완료 안 됨

        shutdown_log = []

        class TestHandler(ShutdownHandler):
            def on_shutdown_start(self):
                shutdown_log.append("start")

            def on_drain_complete(self):
                shutdown_log.append("drained")

            def on_force_shutdown(self, pending):
                shutdown_log.append(f"forced:{len(pending)}")

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=0.5,
            shutdown_handler=TestHandler(),
        )

        # 종료 시작
        coordinator.initiate_shutdown()

        # 빠른 요청들 완료
        time.sleep(0.1)
        tracker.end_request("fast_1")
        tracker.end_request("fast_2")

        # 종료 대기
        coordinator.wait_for_shutdown(timeout=2.0)

        assert "start" in shutdown_log
        assert "forced:1" in shutdown_log  # slow_1 강제 중단

        stats = coordinator.get_stats()
        assert stats.aborted_count == 1

    def test_concurrent_request_handling(self):
        """동시 요청 처리"""
        tracker = RequestTracker()

        def simulate_request(request_id, duration):
            tracker.start_request(request_id)
            time.sleep(duration)
            tracker.end_request(request_id)

        threads = []
        for i in range(5):
            t = threading.Thread(target=simulate_request, args=(f"req_{i}", 0.1))
            threads.append(t)
            t.start()

        time.sleep(0.05)
        assert tracker.get_pending_count() > 0  # 일부 요청이 진행 중

        for t in threads:
            t.join()

        assert tracker.get_pending_count() == 0  # 모든 요청 완료

    def test_shutdown_with_context_managers(self):
        """컨텍스트 매니저와 함께 셧다운"""
        tracker = RequestTracker()

        coordinator = GracefulShutdownCoordinator(
            request_tracker=tracker,
            drain_timeout=5.0,
        )

        # 컨텍스트 매니저로 요청 시작
        def make_request():
            with track_request(tracker, endpoint="/api/test"):
                time.sleep(0.2)

        request_thread = threading.Thread(target=make_request)
        request_thread.start()

        time.sleep(0.05)

        # 셧다운 시작
        coordinator.initiate_shutdown()

        request_thread.join()
        coordinator.wait_for_shutdown(timeout=2.0)

        assert coordinator.phase == ShutdownPhase.TERMINATED
        stats = coordinator.get_stats()
        assert stats.aborted_count == 0  # 요청이 정상 완료됨
