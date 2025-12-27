"""
Async Healing Logger - 비동기 이벤트 버퍼링.

힐링 이벤트를 메모리 큐에 담고 Background Worker가 배치 처리.
복구 경로에서 ~100ms 단축 효과.

Usage:
    from load_tests.utils.selfhealing.async_logger import AsyncHealingLogger, EventSeverity
    
    # 콜백 설정
    def send_to_command_center(events):
        requests.post(f'{host}/api/self-healing/events/batch/', json=events)
    
    AsyncHealingLogger.configure(flush_callback=send_to_command_center)
    AsyncHealingLogger.start()
    
    # 이벤트 로깅 (논블로킹, ~0.01ms)
    AsyncHealingLogger.log({'type': 'cb_open', 'service': 'payment'}, EventSeverity.CRITICAL)
"""

import queue
import threading
import time
import logging
from enum import Enum
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger(__name__)


class EventSeverity(Enum):
    """이벤트 심각도 (Batch Flush Policy)."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3  # CB Open, 장애 감지 → 즉시 전송


class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거.
    
    - 일반 이벤트: 배치로 모아서 전송
    - CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
    - Thread-Safe
    """
    
    _queue: queue.Queue = queue.Queue()
    _running: bool = False
    _worker_thread: Optional[threading.Thread] = None
    _flush_callback: Optional[Callable[[List[Dict]], None]] = None
    _lock = threading.Lock()
    
    # 설정 - V2.2 최적화 (배치 크기 축소, 플러시 빈도 증가)
    BATCH_SIZE = 5  # 10 → 5 (더 빠른 플러시)
    FLUSH_INTERVAL = 2.0  # 5.0 → 2.0초 (더 빠른 응답)
    IMMEDIATE_SEVERITIES = {EventSeverity.CRITICAL, EventSeverity.WARNING}
    MAX_QUEUE_SIZE = 5000  # 10000 → 5000 (메모리 최적화)
    
    # 통계
    _stats = {
        "events_logged": 0,
        "events_flushed": 0,
        "immediate_flushes": 0,
        "batch_flushes": 0,
        "flush_errors": 0,
        "queue_overflows": 0,
    }
    
    @classmethod
    def configure(
        cls,
        flush_callback: Callable[[List[Dict]], None],
        batch_size: int = 10,
        flush_interval: float = 5.0,
        max_queue_size: int = 10000,
    ):
        """
        배치 전송 콜백 설정.
        
        Args:
            flush_callback: 배치 이벤트를 받아 사령탑에 전송하는 함수
            batch_size: 배치 크기
            flush_interval: 플러시 간격 (초)
            max_queue_size: 최대 큐 크기
        """
        cls._flush_callback = flush_callback
        cls.BATCH_SIZE = batch_size
        cls.FLUSH_INTERVAL = flush_interval
        cls.MAX_QUEUE_SIZE = max_queue_size
        logger.info(f"AsyncHealingLogger configured: batch_size={batch_size}, interval={flush_interval}s")
    
    @classmethod
    def start(cls):
        """백그라운드 워커 시작."""
        with cls._lock:
            if cls._running:
                return
            cls._running = True
            cls._worker_thread = threading.Thread(target=cls._worker, daemon=True, name="AsyncHealingLogger")
            cls._worker_thread.start()
            logger.info("AsyncHealingLogger worker started")
    
    @classmethod
    def stop(cls, timeout: float = 5.0):
        """백그라운드 워커 중지."""
        with cls._lock:
            if not cls._running:
                return
            cls._running = False
        
        if cls._worker_thread:
            cls._worker_thread.join(timeout=timeout)
            logger.info("AsyncHealingLogger worker stopped")
    
    @classmethod
    def log(
        cls,
        event: Dict[str, Any],
        severity: EventSeverity = EventSeverity.INFO,
    ):
        """
        이벤트 로깅 (논블로킹, ~0.01ms).
        
        Args:
            event: 힐링 이벤트 딕셔너리
            severity: 이벤트 심각도
        """
        # 이벤트 메타데이터 추가
        enriched_event = {
            **event,
            'severity': severity.name,
            'timestamp': time.time(),
            'timestamp_iso': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime()),
        }
        
        cls._stats["events_logged"] += 1
        
        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL/WARNING: 즉시 전송 (별도 스레드)
            cls._stats["immediate_flushes"] += 1
            threading.Thread(
                target=cls._flush_immediate, 
                args=([enriched_event],),
                daemon=True,
                name="ImmediateFlush"
            ).start()
        else:
            # 일반: 배치 대기
            try:
                if cls._queue.qsize() < cls.MAX_QUEUE_SIZE:
                    cls._queue.put_nowait(enriched_event)
                else:
                    cls._stats["queue_overflows"] += 1
                    logger.warning("AsyncHealingLogger queue full, event dropped")
            except queue.Full:
                cls._stats["queue_overflows"] += 1
    
    @classmethod
    def log_cb_event(cls, service: str, state: str, reason: str = "", **kwargs):
        """Circuit Breaker 이벤트 로깅 헬퍼."""
        severity = EventSeverity.CRITICAL if state in ["OPEN", "BLOCKED"] else EventSeverity.INFO
        cls.log({
            "type": "circuit_breaker",
            "service": service,
            "state": state,
            "reason": reason,
            **kwargs
        }, severity)
    
    @classmethod
    def log_recovery_event(cls, service: str, recovery_time_ms: float, success: bool = True, **kwargs):
        """복구 이벤트 로깅 헬퍼."""
        cls.log({
            "type": "recovery",
            "service": service,
            "recovery_time_ms": recovery_time_ms,
            "success": success,
            **kwargs
        }, EventSeverity.INFO)
    
    @classmethod
    def log_emergency_event(cls, level: str, action: str, reason: str = "", **kwargs):
        """Emergency 이벤트 로깅 헬퍼."""
        severity = EventSeverity.CRITICAL if action == "trigger" else EventSeverity.WARNING
        cls.log({
            "type": "emergency",
            "level": level,
            "action": action,
            "reason": reason,
            **kwargs
        }, severity)
    
    @classmethod
    def flush_now(cls):
        """수동으로 즉시 플러시."""
        batch = []
        while not cls._queue.empty():
            try:
                batch.append(cls._queue.get_nowait())
            except queue.Empty:
                break
        
        if batch:
            cls._flush_batch(batch)
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """통계 반환."""
        return {
            **cls._stats,
            "queue_size": cls._queue.qsize(),
            "is_running": cls._running,
        }
    
    @classmethod
    def reset_stats(cls):
        """통계 초기화."""
        cls._stats = {
            "events_logged": 0,
            "events_flushed": 0,
            "immediate_flushes": 0,
            "batch_flushes": 0,
            "flush_errors": 0,
            "queue_overflows": 0,
        }
    
    @classmethod
    def _worker(cls):
        """배치 처리 워커."""
        batch: List[Dict] = []
        last_flush = time.time()
        
        while cls._running:
            try:
                event = cls._queue.get(timeout=1.0)
                batch.append(event)
            except queue.Empty:
                pass
            
            # 배치 사이즈 도달 또는 시간 경과 시 전송
            should_flush = (
                len(batch) >= cls.BATCH_SIZE or
                (batch and time.time() - last_flush >= cls.FLUSH_INTERVAL)
            )
            
            if should_flush:
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()
        
        # 종료 시 남은 이벤트 처리
        if batch:
            cls._flush_batch(batch)
        
        # 큐에 남은 이벤트도 처리
        remaining = []
        while not cls._queue.empty():
            try:
                remaining.append(cls._queue.get_nowait())
            except queue.Empty:
                break
        if remaining:
            cls._flush_batch(remaining)
    
    @classmethod
    def _flush_batch(cls, events: List[Dict]):
        """배치 전송."""
        if not cls._flush_callback or not events:
            return
        
        try:
            cls._flush_callback(events)
            cls._stats["events_flushed"] += len(events)
            cls._stats["batch_flushes"] += 1
            logger.debug(f"Flushed {len(events)} events")
        except Exception as e:
            cls._stats["flush_errors"] += 1
            logger.error(f"Failed to flush events: {e}")
            # 전송 실패해도 서비스는 계속
    
    @classmethod
    def _flush_immediate(cls, events: List[Dict]):
        """즉시 전송."""
        cls._flush_batch(events)
