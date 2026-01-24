# packages/selfhealing-python/src/selfhealing/utils/async_logger.py
"""
비동기 힐링 이벤트 로거 (Platinum SLA 최적화)

Zero-Latency Logging을 위한 비동기 이벤트 버퍼링
복구 경로에서 ~100ms 단축

Reference:
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [19] BatchSettings 참조.
"""

import queue
import threading
import time
import logging
from enum import Enum
from typing import Dict, List, Callable, Optional, Any

from selfhealing.settings.batch import get_batch_settings

__all__ = ["AsyncHealingLogger", "EventSeverity"]

logger = logging.getLogger(__name__)


class EventSeverity(Enum):
    """이벤트 심각도 (Batch Flush Policy)"""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3  # CB Open, 장애 감지 → 즉시 전송


class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거
    
    - 일반 이벤트: 배치로 모아서 전송
    - CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
    
    Usage:
        def send_to_command_center(events):
            requests.post('http://command-center/events', json=events)
        
        AsyncHealingLogger.configure(flush_callback=send_to_command_center)
        AsyncHealingLogger.start()
        
        # 일반 이벤트 (배치 처리)
        AsyncHealingLogger.log({'type': 'retry', 'service': 'payment'})
        
        # CRITICAL 이벤트 (즉시 전송)
        AsyncHealingLogger.log({'type': 'cb_open', 'service': 'payment'}, EventSeverity.CRITICAL)
    """
    
    _queue: queue.Queue = queue.Queue()
    _running: bool = False
    _worker_thread: Optional[threading.Thread] = None
    _flush_callback: Optional[Callable[[List[Dict]], None]] = None
    _lock = threading.RLock()
    _settings_cache: Optional[Any] = None
    
    # 설정은 BatchSettings에서 가져옴
    IMMEDIATE_SEVERITIES = {EventSeverity.CRITICAL}
    
    # 통계
    _stats = {
        'events_logged': 0,
        'events_flushed': 0,
        'immediate_flushes': 0,
        'batch_flushes': 0,
        'flush_errors': 0,
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
    
    @classmethod
    def configure(cls, flush_callback: Callable[[List[Dict]], None]) -> None:
        """
        배치 전송 콜백 설정
        
        Args:
            flush_callback: 배치 이벤트를 받아 사령탑에 전송하는 함수
        """
        with cls._lock:
            cls._flush_callback = flush_callback
    
    @classmethod
    def start(cls) -> None:
        """백그라운드 워커 시작"""
        with cls._lock:
            if cls._running:
                return
            cls._running = True
            cls._worker_thread = threading.Thread(target=cls._worker, daemon=True)
            cls._worker_thread.start()
            logger.debug("[AsyncHealingLogger] Background worker started")
    
    @classmethod
    def stop(cls, timeout: float = 5.0) -> None:
        """백그라운드 워커 중지"""
        with cls._lock:
            if not cls._running:
                return
            cls._running = False
        
        if cls._worker_thread:
            cls._worker_thread.join(timeout=timeout)
            logger.debug("[AsyncHealingLogger] Background worker stopped")
    
    @classmethod
    def log(cls, event: Dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        """
        이벤트 로깅 (논블로킹, ~0.01ms)
        
        Args:
            event: 힐링 이벤트 딕셔너리
            severity: 이벤트 심각도
        """
        enriched_event = {
            **event,
            'severity': severity.name,
            'timestamp': time.time(),
        }
        
        with cls._lock:
            cls._stats['events_logged'] += 1
        
        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL: 즉시 전송 (별도 스레드)
            threading.Thread(
                target=cls._flush_immediate, 
                args=([enriched_event],),
                daemon=True
            ).start()
        else:
            # 일반: 배치 대기
            cls._queue.put(enriched_event)
    
    @classmethod
    def flush(cls) -> None:
        """수동 플러시 (즉시 모든 대기 이벤트 전송)"""
        events = []
        while not cls._queue.empty():
            try:
                events.append(cls._queue.get_nowait())
            except queue.Empty:
                break
        
        if events:
            cls._flush_batch(events)
    
    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """로거 통계 조회"""
        with cls._lock:
            return cls._stats.copy()
    
    @classmethod
    def reset_stats(cls) -> None:
        """통계 초기화"""
        with cls._lock:
            cls._stats = {
                'events_logged': 0,
                'events_flushed': 0,
                'immediate_flushes': 0,
                'batch_flushes': 0,
                'flush_errors': 0,
            }
    
    @classmethod
    def _worker(cls) -> None:
        """배치 처리 워커"""
        batch: List[Dict] = []
        last_flush = time.time()
        
        while cls._running:
            try:
                event = cls._queue.get(timeout=1.0)
                batch.append(event)
            except queue.Empty:
                pass
            
            # 배치 사이즈 도달 또는 시간 경과 시 전송 (설정에서 가져옴)
            batch_size = cls._get_batch_size()
            flush_interval = cls._get_flush_interval()
            should_flush = (
                len(batch) >= batch_size or
                (batch and time.time() - last_flush >= flush_interval)
            )
            
            if should_flush:
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()
        
        # 종료 시 남은 이벤트 처리
        if batch:
            cls._flush_batch(batch)
    
    @classmethod
    def _flush_batch(cls, events: List[Dict]) -> None:
        """배치 전송"""
        if not cls._flush_callback or not events:
            return
        
        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats['events_flushed'] += len(events)
                cls._stats['batch_flushes'] += 1
            logger.debug(f"[AsyncHealingLogger] Flushed {len(events)} events (batch)")
        except Exception as e:
            # 전송 실패해도 서비스는 계속
            with cls._lock:
                cls._stats['flush_errors'] += 1
            logger.warning(f"[AsyncHealingLogger] Batch flush failed: {e}")
    
    @classmethod
    def _flush_immediate(cls, events: List[Dict]) -> None:
        """즉시 전송"""
        if not cls._flush_callback or not events:
            return
        
        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats['events_flushed'] += len(events)
                cls._stats['immediate_flushes'] += 1
            logger.debug(f"[AsyncHealingLogger] Flushed {len(events)} events (immediate)")
        except Exception as e:
            with cls._lock:
                cls._stats['flush_errors'] += 1
            logger.warning(f"[AsyncHealingLogger] Immediate flush failed: {e}")
    
    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        cls.stop()
        with cls._lock:
            cls._queue = queue.Queue()
            cls._running = False
            cls._worker_thread = None
            cls._flush_callback = None
            cls._stats = {
                'events_logged': 0,
                'events_flushed': 0,
                'immediate_flushes': 0,
                'batch_flushes': 0,
                'flush_errors': 0,
            }
