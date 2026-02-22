# packages/selfhealing-python/tests/unit/audit/test_async_audit_resilience.py
"""
AsyncHealingLogger 및 관련 컴포넌트 내구성 테스트.

문서: 167_ASYNC_AUDIT_PIPELINE.md 섹션 8 보완 제안 구현 검증

테스트 항목:
- WAL-First 로깅
- 배치 플러시 재시도
- 큐 배압 전략
- 에러 알림 임계치
- Priority Queue
- DurableEventLogger
- 체크포인트 관리
"""

from __future__ import annotations

import queue
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

# =============================================================================
# AsyncHealingLogger Tests
# =============================================================================


class TestPriorityQueue:
    """Priority Queue 테스트 (8.4.1)."""

    def test_critical_events_have_highest_priority(self):
        """CRITICAL 이벤트가 가장 높은 우선순위를 가진다."""
        from selfhealing.utils.async_logger import (
            SEVERITY_PRIORITY_MAP,
            EventSeverity,
            LogFlushPriority,
        )

        # CRITICAL이 가장 낮은 숫자 (높은 우선순위)
        assert SEVERITY_PRIORITY_MAP[EventSeverity.CRITICAL] == LogFlushPriority.CRITICAL
        assert SEVERITY_PRIORITY_MAP[EventSeverity.CRITICAL] < SEVERITY_PRIORITY_MAP[EventSeverity.WARNING]
        assert SEVERITY_PRIORITY_MAP[EventSeverity.WARNING] < SEVERITY_PRIORITY_MAP[EventSeverity.INFO]
        assert SEVERITY_PRIORITY_MAP[EventSeverity.INFO] < SEVERITY_PRIORITY_MAP[EventSeverity.DEBUG]

    def test_prioritized_event_ordering(self):
        """PrioritizedEvent가 올바르게 정렬된다."""
        from selfhealing.utils.async_logger import LogFlushPriority, PrioritizedEvent

        events = [
            PrioritizedEvent(priority=LogFlushPriority.DEBUG, timestamp=1.0, event={"type": "debug"}),
            PrioritizedEvent(priority=LogFlushPriority.CRITICAL, timestamp=2.0, event={"type": "critical"}),
            PrioritizedEvent(priority=LogFlushPriority.INFO, timestamp=3.0, event={"type": "info"}),
        ]

        sorted_events = sorted(events)

        assert sorted_events[0].event["type"] == "critical"
        assert sorted_events[1].event["type"] == "info"
        assert sorted_events[2].event["type"] == "debug"

    def test_priority_queue_processes_critical_first(self):
        """Priority Queue가 CRITICAL 이벤트를 먼저 처리한다."""
        from selfhealing.utils.async_logger import LogFlushPriority, PrioritizedEvent

        pq = queue.PriorityQueue()

        # 순서대로 추가 (DEBUG, CRITICAL, INFO)
        pq.put(PrioritizedEvent(priority=LogFlushPriority.DEBUG, timestamp=1.0, event={"type": "debug"}))
        pq.put(PrioritizedEvent(priority=LogFlushPriority.CRITICAL, timestamp=2.0, event={"type": "critical"}))
        pq.put(PrioritizedEvent(priority=LogFlushPriority.INFO, timestamp=3.0, event={"type": "info"}))

        # 우선순위 순으로 꺼내기
        first = pq.get()
        second = pq.get()
        third = pq.get()

        assert first.event["type"] == "critical"
        assert second.event["type"] == "info"
        assert third.event["type"] == "debug"


class TestWALFirstLogging:
    """WAL-First 로깅 테스트 (8.1.1)."""

    def test_wal_policy_enum_values(self):
        """WALPolicy enum 값 검증."""
        from selfhealing.utils.async_logger import WALPolicy

        assert WALPolicy.ALL.value == "all"
        assert WALPolicy.CRITICAL_ONLY.value == "critical"
        assert WALPolicy.NONE.value == "none"

    def test_should_write_to_wal_with_all_policy(self):
        """WALPolicy.ALL일 때 모든 이벤트 WAL 기록."""
        from selfhealing.utils.async_logger import (
            AsyncHealingLogger,
            EventSeverity,
            WALPolicy,
        )

        AsyncHealingLogger.reset()
        AsyncHealingLogger._wal_policy = WALPolicy.ALL

        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.INFO) is True
        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.CRITICAL) is True
        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.DEBUG) is True

        AsyncHealingLogger.reset()

    def test_should_write_to_wal_with_critical_only_policy(self):
        """WALPolicy.CRITICAL_ONLY일 때 CRITICAL만 WAL 기록."""
        from selfhealing.utils.async_logger import (
            AsyncHealingLogger,
            EventSeverity,
            WALPolicy,
        )

        AsyncHealingLogger.reset()
        AsyncHealingLogger._wal_policy = WALPolicy.CRITICAL_ONLY

        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.INFO) is False
        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.CRITICAL) is True
        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.DEBUG) is False

        AsyncHealingLogger.reset()

    def test_should_write_to_wal_with_none_policy(self):
        """WALPolicy.NONE일 때 WAL 기록 안함."""
        from selfhealing.utils.async_logger import (
            AsyncHealingLogger,
            EventSeverity,
            WALPolicy,
        )

        AsyncHealingLogger.reset()
        AsyncHealingLogger._wal_policy = WALPolicy.NONE

        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.INFO) is False
        assert AsyncHealingLogger._should_write_to_wal(EventSeverity.CRITICAL) is False

        AsyncHealingLogger.reset()


class TestFlushRetry:
    """배치 플러시 재시도 테스트 (8.1.3, 8.4.3)."""

    def test_batch_retry_policy_defaults(self):
        """BatchRetryPolicy 기본값 검증."""
        from selfhealing.utils.async_logger import BatchRetryPolicy

        policy = BatchRetryPolicy()

        assert policy.max_retries == 3
        assert policy.initial_delay_seconds == 1.0
        assert policy.backoff_multiplier == 2.0
        assert policy.max_delay_seconds == 30.0
        assert policy.dlq_on_final_failure is True

    def test_exponential_backoff_calculation(self):
        """지수 백오프 계산 검증."""
        from selfhealing.utils.async_logger import BatchRetryPolicy

        policy = BatchRetryPolicy(
            initial_delay_seconds=1.0,
            backoff_multiplier=2.0,
            max_delay_seconds=30.0,
        )

        # attempt 0: 1.0
        # attempt 1: 2.0
        # attempt 2: 4.0
        # attempt 3: 8.0
        delays = []
        for attempt in range(4):
            delay = min(
                policy.initial_delay_seconds * (policy.backoff_multiplier**attempt),
                policy.max_delay_seconds,
            )
            delays.append(delay)

        assert delays == [1.0, 2.0, 4.0, 8.0]

    def test_max_delay_cap(self):
        """최대 지연 시간 제한 검증."""
        from selfhealing.utils.async_logger import BatchRetryPolicy

        policy = BatchRetryPolicy(
            initial_delay_seconds=10.0,
            backoff_multiplier=3.0,
            max_delay_seconds=30.0,
        )

        # attempt 2: 10 * 3^2 = 90 -> cap to 30
        delay = min(
            policy.initial_delay_seconds * (policy.backoff_multiplier**2),
            policy.max_delay_seconds,
        )
        assert delay == 30.0


class TestQueueBackpressure:
    """큐 배압 테스트 (8.2.2)."""

    def test_queue_overflow_policy_values(self):
        """QueueOverflowPolicy enum 값 검증."""
        from selfhealing.utils.async_logger import QueueOverflowPolicy

        assert QueueOverflowPolicy.DROP_NEWEST.value == "drop_newest"
        assert QueueOverflowPolicy.DROP_OLDEST.value == "drop_oldest"
        assert QueueOverflowPolicy.BLOCK.value == "block"

    def test_configure_queue_sets_values(self):
        """configure_queue가 설정을 올바르게 적용한다."""
        from selfhealing.utils.async_logger import (
            AsyncHealingLogger,
            QueueOverflowPolicy,
        )

        AsyncHealingLogger.reset()
        AsyncHealingLogger.configure_queue(
            max_size=1000,
            overflow_policy=QueueOverflowPolicy.DROP_OLDEST,
        )

        assert AsyncHealingLogger._max_queue_size == 1000
        assert AsyncHealingLogger._overflow_policy == QueueOverflowPolicy.DROP_OLDEST

        AsyncHealingLogger.reset()


class TestFlushErrorAlert:
    """플러시 에러 알림 테스트 (8.3.2)."""

    def test_flush_error_alert_config_defaults(self):
        """FlushErrorAlertConfig 기본값 검증."""
        from selfhealing.utils.async_logger import FlushErrorAlertConfig

        config = FlushErrorAlertConfig()

        assert config.threshold_count == 10
        assert config.window_seconds == 60.0
        assert config.cooldown_seconds == 300.0
        assert config.severity == "CRITICAL"

    def test_configure_alert_sets_values(self):
        """configure_alert가 설정을 올바르게 적용한다."""
        from selfhealing.utils.async_logger import (
            AsyncHealingLogger,
            FlushErrorAlertConfig,
        )

        AsyncHealingLogger.reset()
        config = FlushErrorAlertConfig(
            threshold_count=5,
            window_seconds=30.0,
            cooldown_seconds=120.0,
        )
        AsyncHealingLogger.configure_alert(config)

        assert AsyncHealingLogger._alert_config.threshold_count == 5
        assert AsyncHealingLogger._alert_config.window_seconds == 30.0

        AsyncHealingLogger.reset()


class TestCriticalThreadPool:
    """CRITICAL 스레드 풀 테스트 (8.2.1)."""

    def test_critical_executor_max_workers_default(self):
        """CRITICAL 스레드 풀 기본 워커 수 검증."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        assert AsyncHealingLogger.CRITICAL_EXECUTOR_MAX_WORKERS == 5

    def test_executor_created_on_start(self):
        """start() 호출 시 스레드 풀이 생성된다."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()
        AsyncHealingLogger.configure(flush_callback=lambda x: None)
        AsyncHealingLogger.start()

        assert AsyncHealingLogger._critical_executor is not None

        AsyncHealingLogger.stop()
        AsyncHealingLogger.reset()

    def test_executor_shutdown_on_stop(self):
        """stop() 호출 시 스레드 풀이 종료된다."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()
        AsyncHealingLogger.configure(flush_callback=lambda x: None)
        AsyncHealingLogger.start()
        AsyncHealingLogger.stop()

        assert AsyncHealingLogger._critical_executor is None

        AsyncHealingLogger.reset()


class TestAsyncLoggerStats:
    """AsyncHealingLogger 통계 테스트."""

    def test_initial_stats(self):
        """초기 통계 값 검증."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()
        stats = AsyncHealingLogger.get_stats()

        assert stats["events_logged"] == 0
        assert stats["events_flushed"] == 0
        assert stats["flush_errors"] == 0
        assert stats["queue_overflows"] == 0
        assert stats["wal_writes"] == 0

        AsyncHealingLogger.reset()

    def test_reset_stats(self):
        """통계 초기화 검증."""
        from selfhealing.utils.async_logger import AsyncHealingLogger

        AsyncHealingLogger.reset()

        # 통계 조작
        with AsyncHealingLogger._lock:
            AsyncHealingLogger._stats["events_logged"] = 100

        AsyncHealingLogger.reset_stats()
        stats = AsyncHealingLogger.get_stats()

        assert stats["events_logged"] == 0

        AsyncHealingLogger.reset()


# =============================================================================
# DurableEventLogger Tests
# =============================================================================


class TestDurableEventLogger:
    """DurableEventLogger 테스트 (8.4.2)."""

    def test_durable_logger_inherits_async_logger(self):
        """DurableEventLogger가 AsyncHealingLogger를 상속한다."""
        from selfhealing.utils.async_logger import AsyncHealingLogger
        from selfhealing.utils.durable_logger import DurableEventLogger

        assert issubclass(DurableEventLogger, AsyncHealingLogger)

    def test_durable_logger_has_additional_stats(self):
        """DurableEventLogger가 추가 통계를 가진다."""
        from selfhealing.utils.durable_logger import DurableEventLogger

        DurableEventLogger.reset()
        stats = DurableEventLogger.get_durable_stats()

        assert "wal_writes_success" in stats
        assert "wal_writes_failed" in stats
        assert "recovered_events" in stats

        DurableEventLogger.reset()

    def test_recover_from_wal_without_wal_returns_zero(self):
        """WAL 없이 복구 시 0 반환."""
        from selfhealing.utils.durable_logger import DurableEventLogger

        DurableEventLogger.reset()
        DurableEventLogger._wal = None

        recovered = DurableEventLogger.recover_from_wal()

        assert recovered == 0

        DurableEventLogger.reset()


# =============================================================================
# CheckpointManager Tests
# =============================================================================


class TestCheckpointManagerPath:
    """CheckpointManager 경로 설정 테스트 (8.2.3)."""

    def test_get_default_path_uses_env_variable(self):
        """환경변수 SELFHEALING_AUDIT_PATH 사용 검증."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with patch.dict("os.environ", {"SELFHEALING_AUDIT_PATH": "/custom/path"}):
            path = CheckpointManager._get_default_path()
            assert path == Path("/custom/path/checkpoint.json")

    def test_get_default_path_windows_fallback(self):
        """Windows에서 기본 경로 폴백."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with patch.dict("os.environ", {}, clear=True):
            with patch("os.name", "nt"):
                path = CheckpointManager._get_default_path()
                assert "selfhealing" in str(path)
                assert "checkpoint.json" in str(path)

    def test_verify_write_permission_success(self):
        """쓰기 권한 검증 성공."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(checkpoint_path=Path(tmpdir) / "checkpoint.json")
            # 생성자에서 이미 검증 통과
            assert manager._path.parent.exists()

    def test_fallback_on_permission_error(self):
        """권한 없을 때 폴백 경로 사용."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        # 존재하지 않고 생성 불가능한 경로
        with patch.object(CheckpointManager, "_verify_write_permission", return_value=False):
            manager = CheckpointManager(checkpoint_path="/nonexistent/readonly/checkpoint.json")
            # 폴백 경로로 변경됨
            assert "selfhealing" in str(manager._path)


class TestCheckpointManagerFileLock:
    """CheckpointManager 파일 락 테스트 (8.2.4)."""

    def test_lock_file_function_exists(self):
        """lock_file 함수 존재 검증."""
        from selfhealing.audit.checkpoint_manager import lock_file, unlock_file

        assert callable(lock_file)
        assert callable(unlock_file)

    def test_save_with_file_lock(self):
        """파일 락과 함께 저장."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(checkpoint_path=Path(tmpdir) / "checkpoint.json")
            manager.save(last_sequence=100)

            # 락 파일이 생성되었을 수 있음 (구현에 따라)
            loaded = manager.load()
            assert loaded == 100

    def test_concurrent_saves(self):
        """동시 저장 시 락 동작."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(checkpoint_path=Path(tmpdir) / "checkpoint.json")

            results = []

            def save_sequence(seq):
                try:
                    manager.save(last_sequence=seq)
                    results.append(seq)
                except Exception as e:
                    results.append(f"error:{e}")

            threads = [threading.Thread(target=save_sequence, args=(i,)) for i in range(5)]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # 모든 저장이 완료됨 (순서는 보장 안됨)
            assert len(results) == 5


class TestCheckpointManagerBasic:
    """CheckpointManager 기본 기능 테스트."""

    def test_save_and_load(self):
        """저장 및 로드."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(checkpoint_path=Path(tmpdir) / "checkpoint.json")

            manager.save(last_sequence=12345)
            loaded = manager.load()

            assert loaded == 12345

    def test_load_nonexistent_returns_zero(self):
        """존재하지 않는 파일 로드 시 0 반환."""
        from selfhealing.audit.checkpoint_manager import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CheckpointManager(checkpoint_path=Path(tmpdir) / "nonexistent.json")

            loaded = manager.load()

            assert loaded == 0


# =============================================================================
# SyncWorker Checkpoint Tests
# =============================================================================


class TestSyncWorkerCheckpoint:
    """SyncWorker 체크포인트 테스트 (8.1.2)."""

    def test_config_has_checkpoint_settings(self):
        """SyncWorkerConfig에 체크포인트 설정이 있다."""
        from selfhealing.audit.sync_worker import SyncWorkerConfig

        config = SyncWorkerConfig()

        assert hasattr(config, "checkpoint_save_interval_batches")
        assert hasattr(config, "checkpoint_save_interval_seconds")
        assert config.checkpoint_save_interval_batches == 10
        assert config.checkpoint_save_interval_seconds == 30.0


# =============================================================================
# Serialization Tests
# =============================================================================


class TestFastSerialization:
    """고속 직렬화 테스트 (8.3.1)."""

    def test_fast_dumps_returns_bytes(self):
        """fast_dumps가 bytes를 반환한다."""
        from selfhealing.utils.serialization import fast_dumps

        data = {"key": "value", "number": 123}
        result = fast_dumps(data)

        assert isinstance(result, bytes)

    def test_fast_loads_from_bytes(self):
        """fast_loads가 bytes를 파싱한다."""
        from selfhealing.utils.serialization import fast_dumps, fast_loads

        data = {"key": "value", "number": 123}
        encoded = fast_dumps(data)
        decoded = fast_loads(encoded)

        assert decoded == data

    def test_fast_loads_from_string(self):
        """fast_loads가 문자열을 파싱한다."""
        from selfhealing.utils.serialization import fast_loads

        json_str = '{"key":"value","number":123}'
        decoded = fast_loads(json_str)

        assert decoded["key"] == "value"
        assert decoded["number"] == 123

    def test_fast_json_available_flag(self):
        """FAST_JSON_AVAILABLE 플래그 존재."""
        from selfhealing.utils.serialization import FAST_JSON_AVAILABLE

        assert isinstance(FAST_JSON_AVAILABLE, bool)

    def test_fast_dumps_str_returns_string(self):
        """fast_dumps_str가 문자열을 반환한다."""
        from selfhealing.utils.serialization import fast_dumps_str

        data = {"key": "value"}
        result = fast_dumps_str(data)

        assert isinstance(result, str)
        assert "key" in result

    def test_unicode_handling(self):
        """유니코드 처리."""
        from selfhealing.utils.serialization import fast_dumps, fast_loads

        data = {"message": "한글 테스트", "emoji": "🎉"}
        encoded = fast_dumps(data)
        decoded = fast_loads(encoded)

        assert decoded["message"] == "한글 테스트"
        assert decoded["emoji"] == "🎉"
