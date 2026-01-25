"""
Background Sync Worker - WAL → 중앙 저장소 동기화.

ADR-005 (Fail-Open + WAL 기반 누락 0 보장) 구현의 핵심 컴포넌트.

동작 원리:
1. WAL에서 미동기화 엔트리 조회 (synced=False)
2. 중앙 저장소에 기록 시도
3. 성공 시 WAL 엔트리 정리 (cleanup_processed)
4. 실패 시 재시도 (exponential backoff)

Usage:
    from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig
    
    worker = AuditSyncWorker(
        wal=wal_instance,
        central_adapter=adapter,
    )
    worker.start()
    
    # 종료 시
    worker.stop()
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from selfhealing.settings.audit_sync import AuditSyncSettings

logger = logging.getLogger(__name__)


@dataclass
class SyncWorkerConfig:
    """Sync Worker 설정."""
    
    # 동기화 주기 (초)
    sync_interval_seconds: float = 1.0
    
    # 배치 크기
    batch_size: int = 100
    
    # 재시도 설정
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    retry_backoff_multiplier: float = 2.0
    max_retry_delay_seconds: float = 30.0
    
    # 오래된 엔트리 정리 기준 (초)
    cleanup_after_seconds: float = 3600.0  # 1시간
    
    # 메트릭 리포팅 주기 (초)
    metrics_interval_seconds: float = 60.0

    @classmethod
    def from_settings(
        cls,
        settings: "AuditSyncSettings | None" = None,
        **overrides,
    ) -> "SyncWorkerConfig":
        """
        Settings에서 SyncWorkerConfig 인스턴스 생성.

        Args:
            settings: AuditSyncSettings 인스턴스 (없으면 싱글톤 사용)
            **overrides: 개별 필드 오버라이드

        Returns:
            SyncWorkerConfig: Settings 기반 인스턴스
        """
        from selfhealing.settings.audit_sync import get_audit_sync_settings

        s = settings or get_audit_sync_settings()
        return cls(
            sync_interval_seconds=overrides.get(
                "sync_interval_seconds", s.sync_interval_seconds
            ),
            batch_size=overrides.get("batch_size", s.batch_size),
            max_retries=overrides.get("max_retries", s.max_retries),
            retry_delay_seconds=overrides.get(
                "retry_delay_seconds", s.retry_delay_seconds
            ),
            retry_backoff_multiplier=overrides.get(
                "retry_backoff_multiplier", s.retry_backoff_multiplier
            ),
            max_retry_delay_seconds=overrides.get(
                "max_retry_delay_seconds", s.max_retry_delay_seconds
            ),
            cleanup_after_seconds=overrides.get(
                "cleanup_after_seconds", s.cleanup_after_seconds
            ),
            metrics_interval_seconds=overrides.get(
                "metrics_interval_seconds", s.metrics_interval_seconds
            ),
        )
    
    @classmethod
    def from_env(cls) -> "SyncWorkerConfig":
        """
        환경변수에서 설정 로드.
        
        .. deprecated::
            Use `from_settings()` instead for Pydantic v2 Settings support.
        """
        import warnings
        warnings.warn(
            "from_env() is deprecated, use from_settings() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.from_settings()


@dataclass
class SyncStats:
    """동기화 통계."""
    
    total_synced: int = 0
    total_failed: int = 0
    total_retries: int = 0
    last_sync_time: Optional[float] = None
    last_sync_count: int = 0
    last_error: Optional[str] = None
    current_lag_entries: int = 0
    
    # 성능 통계
    avg_sync_duration_ms: float = 0.0
    _sync_durations: List[float] = field(default_factory=list)
    
    def record_sync_duration(self, duration_ms: float) -> None:
        """동기화 소요 시간 기록."""
        self._sync_durations.append(duration_ms)
        # 최근 100개만 유지
        if len(self._sync_durations) > 100:
            self._sync_durations = self._sync_durations[-100:]
        self.avg_sync_duration_ms = sum(self._sync_durations) / len(self._sync_durations)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "total_synced": self.total_synced,
            "total_failed": self.total_failed,
            "total_retries": self.total_retries,
            "last_sync_time": self.last_sync_time,
            "last_sync_count": self.last_sync_count,
            "last_error": self.last_error,
            "current_lag_entries": self.current_lag_entries,
            "avg_sync_duration_ms": round(self.avg_sync_duration_ms, 2),
        }


class AuditSyncWorker:
    """
    Background Sync Worker.
    
    WAL에 기록된 audit 이벤트를 중앙 저장소로 동기화하는 백그라운드 워커.
    
    Thread-safe하며, 단일 인스턴스로 운영.
    """
    
    _instance: Optional["AuditSyncWorker"] = None
    _instance_lock = threading.Lock()
    
    def __init__(
        self,
        wal: Any = None,
        central_adapter: Any = None,
        config: Optional[SyncWorkerConfig] = None,
        on_sync_complete: Optional[Callable[[int, int], None]] = None,
        on_sync_error: Optional[Callable[[Exception], None]] = None,
    ):
        """
        Initialize Sync Worker.
        
        Args:
            wal: WriteAheadLog 인스턴스 (None이면 audit_helpers에서 가져옴)
            central_adapter: 중앙 저장소 어댑터 (AuditLogAdapter)
            config: 워커 설정
            on_sync_complete: 동기화 완료 콜백 (synced_count, failed_count)
            on_sync_error: 동기화 에러 콜백
        """
        self._wal = wal
        self._central_adapter = central_adapter
        self._config = config or SyncWorkerConfig.from_env()
        self._on_sync_complete = on_sync_complete
        self._on_sync_error = on_sync_error
        
        self._stats = SyncStats()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        
        # 마지막 처리된 시퀀스 (WAL cleanup 용)
        self._last_processed_seq: int = 0
        
        logger.info(
            f"[AuditSyncWorker] Initialized with interval={self._config.sync_interval_seconds}s, "
            f"batch_size={self._config.batch_size}"
        )
    
    @classmethod
    def get_instance(
        cls,
        wal: Any = None,
        central_adapter: Any = None,
        config: Optional[SyncWorkerConfig] = None,
    ) -> "AuditSyncWorker":
        """Get or create singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(
                        wal=wal,
                        central_adapter=central_adapter,
                        config=config,
                    )
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (테스트용)."""
        with cls._instance_lock:
            if cls._instance:
                cls._instance.stop()
            cls._instance = None
    
    def _get_wal(self) -> Any:
        """WAL 인스턴스 가져오기."""
        if self._wal is not None:
            return self._wal
        
        # audit_helpers에서 가져오기
        try:
            from selfhealing.services.audit_helpers import _get_wal
            return _get_wal()
        except Exception as e:
            logger.warning(f"[AuditSyncWorker] Failed to get WAL: {e}")
            return None
    
    def _get_adapter(self) -> Any:
        """중앙 저장소 어댑터 가져오기."""
        if self._central_adapter is not None:
            return self._central_adapter
        
        # ProviderRegistry에서 가져오기
        try:
            from selfhealing.factory import ProviderRegistry
            return ProviderRegistry.get_audit_adapter()
        except Exception as e:
            logger.debug(f"[AuditSyncWorker] Adapter not available: {e}")
            return None
    
    def start(self) -> bool:
        """
        워커 시작.
        
        Returns:
            True: 시작 성공
            False: 이미 실행 중
        """
        with self._lock:
            if self._running:
                return False
            
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="AuditSyncWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info("[AuditSyncWorker] Started")
            return True
    
    def stop(self, timeout: float = 1.0) -> None:
        """
        워커 중지.
        
        Args:
            timeout: 종료 대기 시간 (초)
        """
        with self._lock:
            if not self._running:
                return
            
            self._stop_event.set()
            self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("[AuditSyncWorker] Thread did not stop gracefully")
        
        logger.info("[AuditSyncWorker] Stopped")
    
    def _run_loop(self) -> None:
        """메인 동기화 루프."""
        last_metrics_time = time.time()
        
        while not self._stop_event.is_set():
            try:
                # 동기화 수행
                synced, failed = self._sync_batch()
                
                if synced > 0 or failed > 0:
                    logger.debug(
                        f"[AuditSyncWorker] Synced: {synced}, Failed: {failed}"
                    )
                
                # 메트릭 리포팅
                now = time.time()
                if now - last_metrics_time >= self._config.metrics_interval_seconds:
                    self._report_metrics()
                    last_metrics_time = now
                
            except Exception as e:
                logger.error(f"[AuditSyncWorker] Sync loop error: {e}")
                if self._on_sync_error:
                    try:
                        self._on_sync_error(e)
                    except Exception:
                        pass
            
            # 다음 사이클까지 대기
            self._stop_event.wait(timeout=self._config.sync_interval_seconds)
    
    def _sync_batch(self) -> tuple[int, int]:
        """
        배치 동기화 수행.
        
        Returns:
            (synced_count, failed_count)
        """
        wal = self._get_wal()
        if wal is None:
            return 0, 0
        
        adapter = self._get_adapter()
        
        start_time = time.time()
        synced_count = 0
        failed_count = 0
        
        try:
            # 미처리 엔트리 조회
            entries = wal.recover_unprocessed(self._last_processed_seq)
            
            if not entries:
                return 0, 0
            
            # 배치 크기만큼만 처리
            batch = entries[:self._config.batch_size]
            
            with self._lock:
                self._stats.current_lag_entries = len(entries)
            
            for entry in batch:
                try:
                    # 중앙 저장소에 기록
                    if adapter:
                        self._sync_entry_to_adapter(adapter, entry)
                    
                    synced_count += 1
                    self._last_processed_seq = max(self._last_processed_seq, entry.sequence)
                    
                except Exception as e:
                    failed_count += 1
                    logger.warning(
                        f"[AuditSyncWorker] Failed to sync entry seq={entry.sequence}: {e}"
                    )
            
            # 처리 완료된 엔트리 정리
            if synced_count > 0:
                try:
                    wal.cleanup_processed(self._last_processed_seq)
                except Exception as e:
                    logger.warning(f"[AuditSyncWorker] Failed to cleanup WAL: {e}")
            
            # 통계 업데이트
            duration_ms = (time.time() - start_time) * 1000
            with self._lock:
                self._stats.total_synced += synced_count
                self._stats.total_failed += failed_count
                self._stats.last_sync_time = time.time()
                self._stats.last_sync_count = synced_count
                self._stats.record_sync_duration(duration_ms)
            
            # 콜백 호출
            if self._on_sync_complete and (synced_count > 0 or failed_count > 0):
                try:
                    self._on_sync_complete(synced_count, failed_count)
                except Exception:
                    pass
            
            return synced_count, failed_count
            
        except Exception as e:
            with self._lock:
                self._stats.last_error = str(e)
            raise
    
    def _sync_entry_to_adapter(self, adapter: Any, entry: Any) -> None:
        """
        단일 엔트리를 어댑터로 동기화.
        
        재시도 로직 포함.
        """
        delay = self._config.retry_delay_seconds
        last_error: Optional[Exception] = None
        
        for attempt in range(self._config.max_retries + 1):
            try:
                # AuditLogAdapter의 write 메서드 호출
                if hasattr(adapter, 'write'):
                    adapter.write(entry.data)
                elif hasattr(adapter, 'log'):
                    adapter.log(entry.data)
                else:
                    # 범용 로그
                    logger.info(f"[AuditSync] {entry.data}")
                
                return  # 성공
                
            except Exception as e:
                last_error = e
                if attempt < self._config.max_retries:
                    with self._lock:
                        self._stats.total_retries += 1
                    time.sleep(delay)
                    delay = min(
                        delay * self._config.retry_backoff_multiplier,
                        self._config.max_retry_delay_seconds,
                    )
        
        # 모든 재시도 실패
        if last_error:
            raise last_error
    
    def _report_metrics(self) -> None:
        """메트릭 리포팅."""
        try:
            from selfhealing.audit.resilience import AuditMetrics
            metrics = AuditMetrics.get_instance()
            
            with self._lock:
                stats = self._stats.to_dict()
            
            # 커스텀 메트릭 기록
            metrics.record_write("sync_worker", success=True, duration_ms=stats["avg_sync_duration_ms"])
            
            logger.debug(f"[AuditSyncWorker] Metrics: {stats}")
            
        except Exception as e:
            logger.debug(f"[AuditSyncWorker] Failed to report metrics: {e}")
    
    def sync_now(self) -> tuple[int, int]:
        """
        즉시 동기화 수행 (테스트/디버그용).
        
        Returns:
            (synced_count, failed_count)
        """
        return self._sync_batch()
    
    def get_stats(self) -> Dict[str, Any]:
        """동기화 통계 조회."""
        with self._lock:
            return self._stats.to_dict()
    
    def get_lag(self) -> int:
        """현재 동기화 지연 엔트리 수."""
        wal = self._get_wal()
        if wal is None:
            return 0
        
        try:
            entries = wal.recover_unprocessed(self._last_processed_seq)
            return len(entries)
        except Exception:
            return 0
    
    @property
    def is_running(self) -> bool:
        """워커 실행 중 여부."""
        return self._running


# =============================================================================
# Convenience Functions
# =============================================================================


def start_sync_worker(
    wal: Any = None,
    central_adapter: Any = None,
    config: Optional[SyncWorkerConfig] = None,
) -> AuditSyncWorker:
    """
    Sync Worker 시작 헬퍼 함수.
    
    싱글톤 인스턴스를 가져오고 시작합니다.
    """
    worker = AuditSyncWorker.get_instance(
        wal=wal,
        central_adapter=central_adapter,
        config=config,
    )
    worker.start()
    return worker


def stop_sync_worker() -> None:
    """Sync Worker 중지 헬퍼 함수."""
    try:
        worker = AuditSyncWorker.get_instance()
        worker.stop()
    except Exception:
        pass


def get_sync_stats() -> Optional[Dict[str, Any]]:
    """Sync Worker 통계 조회 헬퍼 함수."""
    try:
        worker = AuditSyncWorker.get_instance()
        return worker.get_stats()
    except Exception:
        return None
