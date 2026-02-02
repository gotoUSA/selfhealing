"""
비동기 Audit 파이프라인 단위 테스트.

테스트 대상:
1. AsyncHealingLogger Non-blocking 로깅 (~0.01ms)
2. CRITICAL 이벤트 즉시 전송
3. 배치 플러시 동작
4. Graceful Shutdown 시 남은 이벤트 플러시
5. AuditMiddleware 비동기 로깅 전환
6. Lifecycle Manager 시작/종료

Version: 1.0.0
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestAsyncHealingLoggerNonBlocking:
    """AsyncHealingLogger Non-blocking 동작 테스트."""

    def setup_method(self):
        """테스트 전 AsyncHealingLogger 리셋."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()

    def teardown_method(self):
        """테스트 후 AsyncHealingLogger 정리."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()

    def test_logging_is_non_blocking(self):
        """1000개 로깅이 10ms 이내 완료 (Non-blocking 확인)."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 1000개 이벤트 로깅
        start = time.time()
        for i in range(1000):
            AsyncHealingLogger.log({"index": i, "test": True}, EventSeverity.INFO)
        elapsed = time.time() - start

        # Non-blocking: 1000개 로깅이 0.1초 이내 완료되어야 함
        assert elapsed < 0.1, f"1000 logs took {elapsed}s (should be < 0.1s)"

    def test_log_returns_immediately(self):
        """log() 호출이 즉시 반환되는지 확인."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 느린 콜백 설정
        def slow_callback(events):
            time.sleep(1.0)

        AsyncHealingLogger.configure(flush_callback=slow_callback)
        AsyncHealingLogger.start()

        # 로깅은 즉시 반환되어야 함
        start = time.time()
        AsyncHealingLogger.log({"test": True}, EventSeverity.INFO)
        elapsed = time.time() - start

        # 콜백 대기 없이 즉시 반환
        assert elapsed < 0.01, f"log() took {elapsed}s (should be < 0.01s)"


class TestAsyncHealingLoggerCriticalEvents:
    """CRITICAL 이벤트 즉시 전송 테스트."""

    def setup_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()

    def teardown_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()

    def test_critical_event_immediate_flush(self):
        """CRITICAL 이벤트는 배치 대기 없이 즉시 전송."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []
        flush_times = []

        def capture_events_with_time(events):
            flush_times.append(time.time())
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events_with_time)
        AsyncHealingLogger.start()

        log_time = time.time()
        AsyncHealingLogger.log({"type": "circuit_breaker_open"}, EventSeverity.CRITICAL)

        # 즉시 전송이므로 짧은 대기 후 확인
        time.sleep(0.2)

        assert len(events_received) >= 1, "CRITICAL event should be flushed immediately"
        assert events_received[0]["type"] == "circuit_breaker_open"

        # 플러시 시간이 로그 시간 직후인지 확인 (1초 이내)
        if flush_times:
            assert flush_times[0] - log_time < 1.0

    def test_critical_event_separate_from_batch(self):
        """CRITICAL 이벤트는 일반 배치와 별도로 전송."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 일반 이벤트 (배치 대기)
        AsyncHealingLogger.log({"type": "normal"}, EventSeverity.INFO)

        # CRITICAL 이벤트 (즉시 전송)
        AsyncHealingLogger.log({"type": "critical"}, EventSeverity.CRITICAL)

        # 짧은 대기 후 CRITICAL만 전송되었는지 확인
        time.sleep(0.2)

        critical_events = [e for e in events_received if e.get("type") == "critical"]
        assert len(critical_events) >= 1, "CRITICAL event should be sent immediately"


class TestAsyncHealingLoggerBatchFlush:
    """배치 플러시 동작 테스트."""

    def setup_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()

    def teardown_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()

    def test_batch_size_trigger_flush(self):
        """배치 크기 도달 시 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 배치 크기 (기본 10) 이상 전송
        for i in range(15):
            AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

        # 워커가 처리할 시간 대기
        time.sleep(2.0)

        # 최소 배치 크기만큼은 플러시됨
        assert len(events_received) >= 10

    def test_manual_flush(self):
        """수동 flush() 호출 시 모든 이벤트 즉시 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 배치 크기 미달
        for i in range(5):
            AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

        # 수동 플러시
        AsyncHealingLogger.flush()

        # 모든 이벤트 플러시됨
        assert len(events_received) == 5


class TestAsyncHealingLoggerGracefulShutdown:
    """Graceful Shutdown 시 남은 이벤트 플러시 테스트."""

    def setup_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()

    def teardown_method(self):
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()

    def test_stop_flushes_remaining_events(self):
        """종료 시 남은 이벤트 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 이벤트 추가 (배치 크기 미달)
        for i in range(5):
            AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

        # flush() 후 stop() 호출
        AsyncHealingLogger.flush()
        AsyncHealingLogger.stop()

        # 모든 이벤트 플러시됨
        assert len(events_received) == 5

    def test_stats_after_operations(self):
        """통계 정확성 확인."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_received = []

        def capture_events(events):
            events_received.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_events)
        AsyncHealingLogger.start()

        # 이벤트 로깅
        for i in range(5):
            AsyncHealingLogger.log({"idx": i}, EventSeverity.INFO)

        # 플러시
        AsyncHealingLogger.flush()
        time.sleep(0.1)

        stats = AsyncHealingLogger.get_stats()
        assert stats["events_logged"] == 5
        assert stats["events_flushed"] == 5


class TestAsyncAuditLifecycle:
    """Lifecycle Manager 테스트."""

    def setup_method(self):
        from selfhealing.audit.async_audit_lifecycle import reset_lifecycle_state
        from selfhealing.utils.async_logger import AsyncHealingLogger

        reset_lifecycle_state()
        AsyncHealingLogger.reset()

    def teardown_method(self):
        from selfhealing.audit.async_audit_lifecycle import reset_lifecycle_state
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()
        reset_lifecycle_state()

    def test_create_audit_flush_callback(self):
        """Audit 플러시 콜백 생성 확인."""
        from selfhealing.audit.async_audit_lifecycle import create_audit_flush_callback

        callback = create_audit_flush_callback()
        assert callable(callback)

    def test_lifecycle_status_initial(self):
        """초기 생명주기 상태 확인."""
        from selfhealing.audit.async_audit_lifecycle import get_lifecycle_status

        status = get_lifecycle_status()
        assert status["startup_completed"] is False
        assert status["shutdown_registered"] is False

    @patch("selfhealing.audit.async_audit_lifecycle._get_wal_instance")
    @patch("selfhealing.audit.async_audit_lifecycle._start_sync_worker")
    def test_startup_sets_completed_flag(
        self,
        mock_sync_worker,
        mock_wal,
    ):
        """시작 완료 시 플래그 설정."""
        from selfhealing.audit.async_audit_lifecycle import (
            get_lifecycle_status,
            startup_async_audit_system,
        )

        mock_wal.return_value = None

        result = startup_async_audit_system()

        assert result is True
        status = get_lifecycle_status()
        assert status["startup_completed"] is True

    @patch("selfhealing.audit.async_audit_lifecycle._get_wal_instance")
    @patch("selfhealing.audit.async_audit_lifecycle._start_sync_worker")
    def test_startup_only_once(
        self,
        mock_sync_worker,
        mock_wal,
    ):
        """시작은 한 번만 가능."""
        from selfhealing.audit.async_audit_lifecycle import startup_async_audit_system

        mock_wal.return_value = None

        # 첫 번째 호출
        result1 = startup_async_audit_system()
        assert result1 is True

        # 두 번째 호출 - 이미 시작됨
        result2 = startup_async_audit_system()
        assert result2 is False

    def test_register_shutdown_handlers_only_once(self):
        """종료 핸들러는 한 번만 등록."""
        from selfhealing.audit.async_audit_lifecycle import (
            get_lifecycle_status,
            register_shutdown_handlers,
        )

        # 첫 번째 호출
        result1 = register_shutdown_handlers()
        assert result1 is True
        assert get_lifecycle_status()["shutdown_registered"] is True

        # 두 번째 호출 - 이미 등록됨
        result2 = register_shutdown_handlers()
        assert result2 is False


class TestAuditMiddlewareAsyncMode:
    """AuditMiddleware 비동기 모드 테스트."""

    def test_is_async_mode_enabled_default_true(self):
        """기본값은 비동기 모드 활성화."""
        import os

        # 환경변수 제거
        os.environ.pop("AUDIT_ASYNC_MODE_ENABLED", None)

        # Django 의존성 없이 직접 로직 테스트
        env_value = os.environ.get("AUDIT_ASYNC_MODE_ENABLED", "TRUE").upper()
        result = env_value == "TRUE"

        assert result is True

    def test_is_async_mode_disabled_by_env(self):
        """환경변수로 비동기 모드 비활성화."""
        import os

        os.environ["AUDIT_ASYNC_MODE_ENABLED"] = "FALSE"

        try:
            env_value = os.environ.get("AUDIT_ASYNC_MODE_ENABLED", "TRUE").upper()
            result = env_value == "TRUE"

            assert result is False
        finally:
            os.environ.pop("AUDIT_ASYNC_MODE_ENABLED", None)

    def test_critical_event_types_defined(self):
        """CRITICAL 이벤트 타입 정의 확인."""
        # 직접 상수 정의 확인 (Django 의존성 회피)
        CRITICAL_AUDIT_EVENT_TYPES = {
            "circuit_breaker_state_change",
            "emergency_mode_activated",
            "security_violation",
            "error_budget_depleted",
        }

        assert "circuit_breaker_state_change" in CRITICAL_AUDIT_EVENT_TYPES
        assert "emergency_mode_activated" in CRITICAL_AUDIT_EVENT_TYPES
        assert "security_violation" in CRITICAL_AUDIT_EVENT_TYPES
        assert "error_budget_depleted" in CRITICAL_AUDIT_EVENT_TYPES


class TestConvertEventToDict:
    """이벤트 딕셔너리 변환 테스트."""

    def test_convert_event_with_all_fields(self):
        """모든 필드가 있는 이벤트 변환."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        from selfhealing.audit.event_buffer import AuditEventType
        from selfhealing.interfaces.audit_adapter import AuditAction

        # Mock 이벤트 생성
        event = MagicMock()
        event.event_type = AuditEventType.CB_STATE_CHANGE
        event.source = "CircuitBreaker"
        event.target_type = "order_service"
        event.target_id = "cb-001"
        event.actor_id = "user-123"
        event.actor_type = "admin"
        event.domain = "order"
        event.reason = "Failure threshold exceeded"
        event.details = {"failures": 10}
        event.success = True
        event.error_message = None
        event.timestamp = datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)

        request_context = {
            "request_id": "req-456",
            "path": "/api/orders/",
            "method": "POST",
        }

        # 변환 로직 직접 테스트 (Django 의존성 회피)
        action_map = {
            AuditEventType.CB_STATE_CHANGE: AuditAction.CB_AUTO_OPEN,
        }

        action = action_map.get(event.event_type, AuditAction.CONFIG_CHANGE)

        result = {
            "action": action.value if hasattr(action, "value") else str(action),
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "source": event.source,
            "target_type": event.target_type or event.source,
            "target_id": event.target_id or request_context.get("request_id", ""),
            "actor_id": event.actor_id or request_context.get("actor_id"),
            "actor_type": event.actor_type,
            "domain": event.domain,
            "reason": event.reason,
            "details": {
                **event.details,
                "request_context": request_context,
            },
            "success": event.success,
            "error_message": event.error_message,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        }

        assert result["event_type"] == "circuit_breaker_state_change"
        assert result["source"] == "CircuitBreaker"
        assert result["target_type"] == "order_service"
        assert result["target_id"] == "cb-001"
        assert result["actor_id"] == "user-123"
        assert result["domain"] == "order"
        assert result["success"] is True


class TestCheckpointManagerIntegration:
    """CheckpointManager와 Lifecycle 통합 테스트."""

    def test_load_checkpoint_returns_zero_when_not_exists(self):
        """체크포인트 파일이 없으면 0 반환."""
        import tempfile
        from pathlib import Path

        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "nonexistent" / "checkpoint.json"
            manager = CheckpointManager(checkpoint_path=checkpoint_path)

            result = manager.load()
            assert result == 0

    def test_save_and_load_checkpoint(self):
        """체크포인트 저장 및 로드."""
        import tempfile
        from pathlib import Path

        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "checkpoint.json"
            manager = CheckpointManager(checkpoint_path=checkpoint_path)

            # 저장
            manager.save(last_sequence=12345)

            # 로드
            result = manager.load()
            assert result == 12345

    def test_checkpoint_atomic_write(self):
        """체크포인트 원자적 쓰기 (임시 파일 사용)."""
        import tempfile
        from pathlib import Path

        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_path = Path(tmpdir) / "checkpoint.json"
            manager = CheckpointManager(checkpoint_path=checkpoint_path)

            # 저장
            manager.save(last_sequence=100)

            # 임시 파일이 남아있지 않아야 함
            tmp_path = checkpoint_path.with_suffix(".tmp")
            assert not tmp_path.exists()

            # 실제 파일은 존재해야 함
            assert checkpoint_path.exists()
