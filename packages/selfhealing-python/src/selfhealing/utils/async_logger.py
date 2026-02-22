# packages/selfhealing-python/src/selfhealing/utils/async_logger.py
"""
비동기 힐링 이벤트 로거 (Platinum SLA 최적화)

Zero-Latency Logging을 위한 비동기 이벤트 버퍼링
복구 경로에서 ~100ms 단축

주요 기능:
- Priority Queue 기반 이벤트 처리 (CRITICAL 우선)
- ThreadPoolExecutor 기반 CRITICAL 이벤트 처리 (스레드 폭발 방지)
- WAL-First 로깅 (데이터 유실 방지)
- 배치 플러시 재시도 (지수 백오프)
- 큐 크기 제한 및 배압 전략
- 에러 임계치 기반 자동 알림
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.settings.batch import get_batch_settings

if TYPE_CHECKING:
    from selfhealing.audit.wal import WriteAheadLog

__all__ = [
    "AsyncHealingLogger",
    "EventSeverity",
    "LogFlushPriority",
    "PrioritizedEvent",
    "WALPolicy",
    "QueueOverflowPolicy",
    "BatchRetryPolicy",
    "FlushErrorAlertConfig",
]

logger = structlog.get_logger()


# =============================================================================
# Enums and Data Classes
# =============================================================================


class EventSeverity(IntEnum):
    """이벤트 심각도 (Batch Flush Policy)"""

    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3  # CB Open, 장애 감지 → 즉시 전송


class LogFlushPriority:
    """로그 플러시 우선순위 상수 (낮을수록 높은 우선순위, PriorityQueue용)."""

    CRITICAL = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3


# Severity → Priority 매핑
SEVERITY_PRIORITY_MAP: dict[EventSeverity, int] = {
    EventSeverity.CRITICAL: LogFlushPriority.CRITICAL,
    EventSeverity.WARNING: LogFlushPriority.WARNING,
    EventSeverity.INFO: LogFlushPriority.INFO,
    EventSeverity.DEBUG: LogFlushPriority.DEBUG,
}


@dataclass(order=True)
class PrioritizedEvent:
    """우선순위 기반 이벤트 래퍼 (PriorityQueue용)."""

    priority: int  # 낮을수록 높은 우선순위
    timestamp: float = field(compare=False)
    event: dict[str, Any] = field(compare=False)


class WALPolicy(str, Enum):
    """WAL 기록 정책."""

    ALL = "all"  # 모든 이벤트 WAL 기록
    CRITICAL_ONLY = "critical"  # CRITICAL만 WAL 기록 (권장)
    NONE = "none"  # WAL 미사용 (기존 동작)


class QueueOverflowPolicy(str, Enum):
    """큐 오버플로우 정책."""

    DROP_NEWEST = "drop_newest"  # 새 이벤트 드랍 (기본, 간단)
    DROP_OLDEST = "drop_oldest"  # 오래된 이벤트 드랍 (RingBuffer 방식)
    BLOCK = "block"  # 블로킹 (Non-blocking 위반)


@dataclass
class BatchRetryPolicy:
    """배치 플러시 재시도 정책 (지수 백오프)."""

    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0
    dlq_on_final_failure: bool = True  # 최종 실패 시 DLQ 이동


@dataclass
class FlushErrorAlertConfig:
    """플러시 에러 알림 설정."""

    threshold_count: int = 10  # 임계치 (N회)
    window_seconds: float = 60.0  # 시간 윈도우 (초)
    cooldown_seconds: float = 300.0  # 알림 쿨다운 (5분)
    severity: str = "CRITICAL"  # 알림 등급


# =============================================================================
# AsyncHealingLogger
# =============================================================================


class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거

    주요 특징:
    - Priority Queue: CRITICAL 이벤트 우선 처리
    - ThreadPoolExecutor: CRITICAL 이벤트 스레드 풀 (스레드 폭발 방지)
    - WAL-First: 메모리 큐 전에 WAL 기록 (데이터 유실 방지)
    - 배치 재시도: 지수 백오프 적용
    - 큐 크기 제한: 배압 전략으로 메모리 보호
    - 에러 알림: 임계치 초과 시 자동 알림

    Usage:
        def send_to_command_center(events):
            requests.post('http://command-center/events', json=events)

        AsyncHealingLogger.configure(flush_callback=send_to_command_center)
        AsyncHealingLogger.start()

        # 일반 이벤트 (배치 처리)
        AsyncHealingLogger.log({'type': 'retry', 'service': 'payment'})

        # CRITICAL 이벤트 (우선순위 처리)
        AsyncHealingLogger.log({'type': 'cb_open', 'service': 'payment'}, EventSeverity.CRITICAL)
    """

    # 기본 큐 (Priority Queue로 대체됨)
    _queue: queue.Queue | None = None
    _priority_queue: queue.PriorityQueue | None = None

    _running: bool = False
    _worker_thread: threading.Thread | None = None
    _flush_callback: Callable[[list[dict]], None] | None = None
    _lock = threading.RLock()
    _settings_cache: Any | None = None

    # WAL 관련
    _wal: WriteAheadLog | None = None
    _wal_policy: WALPolicy = WALPolicy.CRITICAL_ONLY

    # CRITICAL 이벤트 스레드 풀
    _critical_executor: ThreadPoolExecutor | None = None
    CRITICAL_EXECUTOR_MAX_WORKERS: int = 5

    # 큐 설정
    _overflow_policy: QueueOverflowPolicy = QueueOverflowPolicy.DROP_NEWEST
    _max_queue_size: int = 5000

    # 성능2: atomic 카운터 (qsize() 대신 사용)
    _queue_count: int = 0
    _queue_count_lock = threading.Lock()

    # 재시도 관련
    _retry_policy: BatchRetryPolicy = BatchRetryPolicy()
    _pending_retries: list[tuple[list[dict], int, float]] = []

    # 에러 알림 관련
    _alert_config: FlushErrorAlertConfig = FlushErrorAlertConfig()
    _error_timestamps: deque = deque(maxlen=100)
    _last_alert_time: float = 0

    # 설정은 BatchSettings에서 가져옴
    IMMEDIATE_SEVERITIES = {EventSeverity.CRITICAL}

    # 통계
    _stats = {
        "events_logged": 0,
        "events_flushed": 0,
        "immediate_flushes": 0,
        "batch_flushes": 0,
        "flush_errors": 0,
        "wal_writes": 0,
        "queue_overflows": 0,
        "pending_retries": 0,
        "dlq_moved": 0,
        "total_retries": 0,
        "alerts_sent": 0,
    }

    @classmethod
    def _get_settings(cls):
        """Get BatchSettings (cached for performance)."""
        if cls._settings_cache is None:
            cls._settings_cache = get_batch_settings()
        return cls._settings_cache

    @classmethod
    def _get_batch_size(cls) -> int:
        """Get batch size from settings."""
        return cls._get_settings().logger_batch_size

    @classmethod
    def _get_flush_interval(cls) -> float:
        """Get flush interval from settings."""
        return cls._get_settings().flush_interval

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------

    @classmethod
    def configure(cls, flush_callback: Callable[[list[dict]], None]) -> None:
        """
        배치 전송 콜백 설정

        Args:
            flush_callback: 배치 이벤트를 받아 사령탑에 전송하는 함수
        """
        with cls._lock:
            cls._flush_callback = flush_callback

    @classmethod
    def configure_wal(
        cls,
        wal: WriteAheadLog,
        policy: WALPolicy = WALPolicy.CRITICAL_ONLY,
    ) -> None:
        """
        WAL 인스턴스 및 정책 설정.

        Args:
            wal: WriteAheadLog 인스턴스
            policy: WAL 기록 정책
        """
        with cls._lock:
            cls._wal = wal
            cls._wal_policy = policy
        logger.info(
            "async_healing_logger.wal_configured",
            policy=policy.value,
        )

    @classmethod
    def configure_queue(
        cls,
        max_size: int = 5000,
        overflow_policy: QueueOverflowPolicy = QueueOverflowPolicy.DROP_NEWEST,
    ) -> None:
        """
        큐 설정.

        Args:
            max_size: 최대 큐 크기
            overflow_policy: 오버플로우 정책
        """
        with cls._lock:
            cls._max_queue_size = max_size
            cls._overflow_policy = overflow_policy
        logger.debug(
            "async_healing_logger.queue_configured",
            max_size=max_size,
            overflow_policy=overflow_policy.value,
        )

    @classmethod
    def configure_retry(cls, policy: BatchRetryPolicy) -> None:
        """
        재시도 정책 설정.

        Args:
            policy: 배치 재시도 정책
        """
        with cls._lock:
            cls._retry_policy = policy
        logger.debug(
            "async_healing_logger.retry_policy_configured",
            policy=policy.max_retries,
        )

    @classmethod
    def configure_alert(cls, config: FlushErrorAlertConfig) -> None:
        """
        에러 알림 설정.

        Args:
            config: 플러시 에러 알림 설정
        """
        with cls._lock:
            cls._alert_config = config
        logger.debug(
            "async_healing_logger.alert_configured",
            config=config.threshold_count,
        )

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    @classmethod
    def start(cls) -> None:
        """백그라운드 워커 시작"""
        with cls._lock:
            if cls._running:
                return
            cls._running = True

            # 설정에서 max_queue_size 로드
            try:
                settings = cls._get_settings()
                cls._max_queue_size = getattr(settings, "async_logger_max_queue_size", 5000)
            except Exception:
                pass

            # Priority Queue 초기화 (크기 제한 없음, 별도 관리)
            cls._priority_queue = queue.PriorityQueue()
            cls._queue = queue.Queue(maxsize=cls._max_queue_size)  # 호환성 유지

            # CRITICAL 이벤트 전용 스레드 풀 생성
            cls._critical_executor = ThreadPoolExecutor(
                max_workers=cls.CRITICAL_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="CriticalAuditFlush",
            )

            # 재시도 목록 초기화
            cls._pending_retries = []

            cls._worker_thread = threading.Thread(target=cls._worker, daemon=True)
            cls._worker_thread.start()
            logger.debug("async_healing_logger.background_worker_started")

    @classmethod
    def stop(cls, timeout: float = 5.0) -> None:
        """백그라운드 워커 중지"""
        with cls._lock:
            if not cls._running:
                return
            cls._running = False

        if cls._worker_thread:
            cls._worker_thread.join(timeout=timeout)

        # 스레드 풀 종료
        if cls._critical_executor:
            cls._critical_executor.shutdown(wait=True, cancel_futures=False)
            cls._critical_executor = None

        logger.debug("async_healing_logger.background_worker_stopped")

    # -------------------------------------------------------------------------
    # WAL Support
    # -------------------------------------------------------------------------

    @classmethod
    def _should_write_to_wal(cls, severity: EventSeverity) -> bool:
        """WAL 기록 여부 결정."""
        if cls._wal_policy == WALPolicy.ALL:
            return True
        if cls._wal_policy == WALPolicy.CRITICAL_ONLY:
            return severity in cls.IMMEDIATE_SEVERITIES
        return False

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        """
        이벤트 로깅 (논블로킹, ~0.01ms)

        처리 순서:
        1. WAL-First: WAL에 먼저 기록 (설정에 따라)
        2. Priority Queue에 추가 (CRITICAL 우선 처리)

        Args:
            event: 힐링 이벤트 딕셔너리
            severity: 이벤트 심각도
        """
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        # WAL-First: 메모리 큐 전에 WAL 기록
        wal_seq = -1
        if cls._wal and cls._should_write_to_wal(severity):
            try:
                wal_seq = cls._wal.write(enriched_event)
                with cls._lock:
                    cls._stats["wal_writes"] += 1
            except Exception as e:
                logger.warning(
                    "async_healing_logger.wal_write_failed",
                    error=e,
                )

        enriched_event["_wal_seq"] = wal_seq

        with cls._lock:
            cls._stats["events_logged"] += 1

        # Priority 결정
        priority = SEVERITY_PRIORITY_MAP.get(severity, LogFlushPriority.INFO)
        prioritized = PrioritizedEvent(
            priority=priority,
            timestamp=time.time(),
            event=enriched_event,
        )

        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL: 스레드 풀 사용 (스레드 폭발 방지)
            if cls._critical_executor:
                cls._critical_executor.submit(cls._flush_immediate, [enriched_event])
            else:
                # Fallback: 스레드 풀 미초기화 시 직접 스레드 생성
                threading.Thread(target=cls._flush_immediate, args=([enriched_event],), daemon=True).start()
        else:
            # 일반: Priority Queue에 추가 (배압 적용)
            cls._enqueue_with_backpressure(prioritized)

    @classmethod
    def _enqueue_with_backpressure(cls, prioritized: PrioritizedEvent) -> None:
        """배압 전략을 적용하여 큐에 추가 (성능2: atomic 카운터 사용)."""
        if cls._priority_queue is None:
            return

        try:
            # 성능2: qsize() 대신 atomic 카운터로 크기 체크 (Lock 범위 최소화)
            with cls._queue_count_lock:
                current_count = cls._queue_count
                is_full = current_count >= cls._max_queue_size

                if is_full:
                    cls._stats["queue_overflows"] += 1

                    if cls._overflow_policy == QueueOverflowPolicy.DROP_NEWEST:
                        logger.warning("async_healing_logger.queue_full_dropping_newest")
                        return
                    elif cls._overflow_policy == QueueOverflowPolicy.DROP_OLDEST:
                        # 성능2: atomic하게 get + put 수행
                        try:
                            cls._priority_queue.get_nowait()
                            # 카운터는 그대로 (get 후 put이므로)
                        except queue.Empty:
                            pass
                        logger.warning("async_healing_logger.queue_full_dropping_oldest")
                        # DROP_OLDEST에서는 아래에서 put
                    # BLOCK은 put() 사용 (Non-blocking 위반이므로 권장 안함)

                # 큐에 추가하고 카운터 증가
                cls._priority_queue.put_nowait(prioritized)
                if not is_full:
                    cls._queue_count += 1

        except queue.Full:
            with cls._lock:
                cls._stats["queue_overflows"] += 1
            logger.warning("async_healing_logger.queue_full_event_dropped")

    @classmethod
    def flush(cls) -> None:
        """수동 플러시 (즉시 모든 대기 이벤트 전송)"""
        events = []
        extracted_count = 0

        # Priority Queue에서 모든 이벤트 추출
        if cls._priority_queue:
            while not cls._priority_queue.empty():
                try:
                    prioritized = cls._priority_queue.get_nowait()
                    events.append(prioritized.event)
                    extracted_count += 1
                except queue.Empty:
                    break

        # 기존 Queue에서도 추출 (호환성)
        if cls._queue:
            while not cls._queue.empty():
                try:
                    events.append(cls._queue.get_nowait())
                    extracted_count += 1
                except queue.Empty:
                    break

        # 성능2: 카운터 업데이트
        if extracted_count > 0:
            with cls._queue_count_lock:
                cls._queue_count = max(0, cls._queue_count - extracted_count)

        if events:
            cls._flush_batch(events)

    @classmethod
    def get_stats(cls) -> dict[str, int]:
        """로거 통계 조회"""
        with cls._lock:
            stats = cls._stats.copy()
            # 성능2: atomic 카운터 사용
            with cls._queue_count_lock:
                stats["current_queue_size"] = cls._queue_count
            return stats

    @classmethod
    def reset_stats(cls) -> None:
        """통계 초기화"""
        with cls._lock:
            cls._stats = {
                "events_logged": 0,
                "events_flushed": 0,
                "immediate_flushes": 0,
                "batch_flushes": 0,
                "flush_errors": 0,
                "wal_writes": 0,
                "queue_overflows": 0,
                "pending_retries": 0,
                "dlq_moved": 0,
                "total_retries": 0,
                "alerts_sent": 0,
            }

    # -------------------------------------------------------------------------
    # Worker
    # -------------------------------------------------------------------------

    @classmethod
    def _worker(cls) -> None:
        """Priority Queue 기반 배치 처리 워커."""
        batch: list[dict] = []
        critical_batch: list[dict] = []
        last_flush = time.time()

        while cls._running:
            # 1. 대기 중인 재시도 처리
            cls._process_pending_retries()

            # 2. Priority Queue에서 이벤트 추출
            try:
                if cls._priority_queue:
                    prioritized = cls._priority_queue.get(timeout=0.5)

                    # 성능2: 카운터 감소
                    with cls._queue_count_lock:
                        cls._queue_count = max(0, cls._queue_count - 1)

                    if prioritized.priority == LogFlushPriority.CRITICAL:
                        # CRITICAL은 별도 배치로 즉시 처리
                        critical_batch.append(prioritized.event)
                    else:
                        batch.append(prioritized.event)
            except queue.Empty:
                pass

            # 3. CRITICAL 배치 즉시 플러시
            if critical_batch:
                cls._flush_batch(critical_batch)
                critical_batch = []

            # 4. 일반 배치 조건부 플러시
            if cls._should_flush(batch, last_flush):
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()

        # 종료 시 남은 이벤트 처리
        if critical_batch:
            cls._flush_batch(critical_batch)
        if batch:
            cls._flush_batch(batch)

        # 남은 재시도도 처리
        for events, attempt, _ in cls._pending_retries:
            cls._flush_with_retry(events, attempt)

    @classmethod
    def _should_flush(cls, batch: list[dict], last_flush: float) -> bool:
        """배치 플러시 조건 확인."""
        if not batch:
            return False

        batch_size = cls._get_batch_size()
        flush_interval = cls._get_flush_interval()

        return len(batch) >= batch_size or (time.time() - last_flush >= flush_interval)

    # -------------------------------------------------------------------------
    # Flush with Retry
    # -------------------------------------------------------------------------

    @classmethod
    def _flush_batch(cls, events: list[dict]) -> None:
        """배치 플러시 (재시도 지원)."""
        cls._flush_with_retry(events, attempt=0)

    @classmethod
    def _flush_with_retry(cls, events: list[dict], attempt: int) -> None:
        """지수 백오프를 적용한 배치 플러시."""
        if not cls._flush_callback or not events:
            return

        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats["events_flushed"] += len(events)
                cls._stats["batch_flushes"] += 1
            logger.debug(
                "async_healing_logger.flushed_events",
                count=len(events),
            )

        except Exception as e:
            with cls._lock:
                cls._stats["flush_errors"] += 1
                cls._error_timestamps.append(time.time())

            # 알림 체크
            cls._check_and_send_alert()

            if attempt < cls._retry_policy.max_retries:
                # 재시도 스케줄링
                delay = min(
                    cls._retry_policy.initial_delay_seconds * (cls._retry_policy.backoff_multiplier**attempt),
                    cls._retry_policy.max_delay_seconds,
                )
                next_retry = time.time() + delay

                with cls._lock:
                    cls._pending_retries.append((events, attempt + 1, next_retry))
                    cls._stats["pending_retries"] = len(cls._pending_retries)
                    cls._stats["total_retries"] += 1

                logger.warning(
                    "async_healing_logger.flush_failed_retry_after",
                    value=attempt + 1,
                    cls=cls._retry_policy.max_retries,
                    delay=delay,
                    error=e,
                )
            else:
                # 최종 실패
                logger.error(
                    "async_healing_logger.flush_failed_after_retries",
                    attempt=attempt,
                    error=e,
                )

                if cls._retry_policy.dlq_on_final_failure:
                    cls._move_to_dlq(events, str(e))
                else:
                    # WAL에 시퀀스가 있으면 SyncWorker가 재처리
                    logger.warning("async_healing_logger.events_lost_no_dlq")

    @classmethod
    def _process_pending_retries(cls) -> None:
        """대기 중인 재시도 처리 (워커에서 주기적 호출)."""
        now = time.time()
        remaining = []

        with cls._lock:
            retries = cls._pending_retries[:]
            cls._pending_retries = []

        for events, attempt, next_retry in retries:
            if now >= next_retry:
                cls._flush_with_retry(events, attempt)
            else:
                remaining.append((events, attempt, next_retry))

        with cls._lock:
            cls._pending_retries.extend(remaining)
            cls._stats["pending_retries"] = len(cls._pending_retries)

    @classmethod
    def _move_to_dlq(cls, events: list[dict], error_message: str) -> None:
        """최종 실패 이벤트를 DLQ로 이동."""
        try:
            from selfhealing.services.dlq.store_operations import DLQStoreOperations

            dlq = DLQStoreOperations()
            for event in events:
                dlq.store(
                    source="AsyncHealingLogger",
                    payload=event,
                    error_message=error_message,
                    max_retries=0,
                )

            with cls._lock:
                cls._stats["dlq_moved"] += len(events)

            logger.info(
                "async_healing_logger.moved_events_dlq",
                count=len(events),
            )

        except ImportError:
            logger.warning("async_healing_logger.dlq_available_events_lost")
        except Exception as e:
            logger.exception(
                "async_healing_logger.dlq_store_failed",
                error=e,
            )

    # -------------------------------------------------------------------------
    # Alert
    # -------------------------------------------------------------------------

    @classmethod
    def _check_and_send_alert(cls) -> None:
        """에러 임계치 확인 및 알림 발송."""
        now = time.time()

        # 쿨다운 체크
        if now - cls._last_alert_time < cls._alert_config.cooldown_seconds:
            return

        # 시간 윈도우 내 에러 수 계산
        window_start = now - cls._alert_config.window_seconds
        recent_errors = sum(1 for ts in cls._error_timestamps if ts >= window_start)

        if recent_errors >= cls._alert_config.threshold_count:
            cls._send_flush_error_alert(recent_errors)
            cls._last_alert_time = now

    @classmethod
    def _send_flush_error_alert(cls, error_count: int) -> None:
        """UnifiedNotificationManager를 통한 알림 발송."""
        try:
            from selfhealing.services.unified_notification import (
                NotificationSeverity,
                UnifiedNotificationManager,
            )

            manager = UnifiedNotificationManager()
            manager.notify(
                title="[AsyncAuditLogger] 플러시 에러 임계치 초과",
                message=(
                    f"{cls._alert_config.window_seconds}초 내 {error_count}회 플러시 실패. "
                    f"데이터 유실 위험 - 즉시 확인 필요"
                ),
                severity=NotificationSeverity[cls._alert_config.severity],
                source="AsyncHealingLogger",
                details={
                    "error_count": error_count,
                    "threshold": cls._alert_config.threshold_count,
                    "window_seconds": cls._alert_config.window_seconds,
                    "queue_size": cls._priority_queue.qsize() if cls._priority_queue else 0,
                },
            )

            with cls._lock:
                cls._stats["alerts_sent"] += 1

            logger.info(
                "async_healing_logger.flush_error_alert_sent",
                error_count=error_count,
            )

        except ImportError:
            logger.warning("async_healing_logger.unifiednotificationmanager_available")
        except Exception as e:
            logger.exception(
                "async_healing_logger.failed_send_alert",
                error=e,
            )

    # -------------------------------------------------------------------------
    # Immediate Flush
    # -------------------------------------------------------------------------

    @classmethod
    def _flush_immediate(cls, events: list[dict]) -> None:
        """즉시 전송 (CRITICAL 이벤트용)"""
        if not cls._flush_callback or not events:
            return

        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats["events_flushed"] += len(events)
                cls._stats["immediate_flushes"] += 1
            logger.debug(
                "async_healing_logger.flushed_events_immediate",
                count=len(events),
            )
        except Exception as e:
            with cls._lock:
                cls._stats["flush_errors"] += 1
                cls._error_timestamps.append(time.time())
            logger.warning(
                "async_healing_logger.immediate_flush_failed",
                error=e,
            )
            cls._check_and_send_alert()

    # -------------------------------------------------------------------------
    # Reset
    # -------------------------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        cls.stop()
        with cls._lock:
            cls._queue = None
            cls._priority_queue = None
            cls._running = False
            cls._worker_thread = None
            cls._flush_callback = None
            cls._wal = None
            cls._wal_policy = WALPolicy.CRITICAL_ONLY
            cls._critical_executor = None
            cls._pending_retries = []
            cls._error_timestamps = deque(maxlen=100)
            cls._last_alert_time = 0
            cls._stats = {
                "events_logged": 0,
                "events_flushed": 0,
                "immediate_flushes": 0,
                "batch_flushes": 0,
                "flush_errors": 0,
                "wal_writes": 0,
                "queue_overflows": 0,
                "pending_retries": 0,
                "dlq_moved": 0,
                "total_retries": 0,
                "alerts_sent": 0,
            }
