"""
Pending Sequence Watchdog (Self-Cleanup).

Provides background monitoring and cleanup of stale pending sequences.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PendingSequenceWatchdog:
    """
    Background watchdog for cleaning stale pending sequences.
    
    Problem:
        PENDING entries may be orphaned if process crashes after reserve
        but before commit/abort. Global TTL (60s) is too long for responsiveness.
    
    Solution:
        Local watchdog thread monitors own reservations.
        On write failure, immediately cleans up (no TTL wait).
    
    Pattern source:
        audit/audit_watchdog.py#L150-270 (daemon thread pattern)
        api/django/rate_limit.py#L176-178 (cleanup interval pattern)
    
    Usage:
        watchdog = PendingSequenceWatchdog(redis_client)
        watchdog.start()
        
        seq = watchdog.register_pending(5)
        try:
            do_write()
            watchdog.mark_committed(seq)
        except:
            watchdog.mark_failed(seq)  # Immediate cleanup
    """
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        check_interval_seconds: float = 5.0,
        stale_threshold_seconds: float = 30.0,
    ):
        """
        Initialize pending sequence watchdog.
        
        Args:
            redis_client: Redis client
            key_prefix: Key prefix for Redis keys
            check_interval_seconds: How often to check for stale entries
            stale_threshold_seconds: Age after which entry is considered stale
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._check_interval = check_interval_seconds
        self._stale_threshold = stale_threshold_seconds
        
        # Track local pending sequences
        self._local_pending: Dict[int, float] = {}  # seq -> monotonic_time
        self._lock = threading.RLock()
        
        # Background thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        
        # Stats
        self._cleaned_count = 0
    
    def start(self) -> None:
        """Start watchdog thread."""
        with self._lock:
            if self._is_running:
                return
            
            self._is_running = True
            self._stop_event.clear()
            
            self._thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="PendingSequenceWatchdog",
            )
            self._thread.start()
            logger.info("[PendingWatchdog] Started")
    
    def stop(self, timeout: float = 5.0) -> None:
        """Stop watchdog thread."""
        with self._lock:
            if not self._is_running:
                return
            
            self._stop_event.set()
            
            if self._thread:
                self._thread.join(timeout=timeout)
            
            self._is_running = False
            logger.info(f"[PendingWatchdog] Stopped. Cleaned: {self._cleaned_count}")
    
    def register_pending(self, sequence: int) -> None:
        """Register a pending sequence for tracking."""
        with self._lock:
            self._local_pending[sequence] = time.monotonic()
    
    def mark_committed(self, sequence: int) -> None:
        """Mark sequence as committed (remove from tracking)."""
        with self._lock:
            self._local_pending.pop(sequence, None)
    
    def mark_failed(self, sequence: int) -> None:
        """
        Mark sequence as failed (immediate cleanup).
        
        Unlike waiting for TTL, this cleans up immediately.
        """
        with self._lock:
            self._local_pending.pop(sequence, None)
        
        # Immediate Redis cleanup
        try:
            pending_key = f"{self._key_prefix}audit:hash_chain:pending:{sequence}"
            self._redis.delete(pending_key)
            self._cleaned_count += 1
            logger.debug(f"[PendingWatchdog] Immediately cleaned seq {sequence}")
        except Exception as e:
            logger.warning(f"[PendingWatchdog] Cleanup failed for seq {sequence}: {e}")
    
    def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while not self._stop_event.is_set():
            try:
                self._cleanup_stale_local()
            except Exception as e:
                logger.error(f"[PendingWatchdog] Cleanup error: {e}")
            
            self._stop_event.wait(timeout=self._check_interval)
    
    def _cleanup_stale_local(self) -> None:
        """Clean up locally tracked stale entries."""
        now = time.monotonic()
        stale_sequences = []
        
        with self._lock:
            for seq, start_time in list(self._local_pending.items()):
                if now - start_time > self._stale_threshold:
                    stale_sequences.append(seq)
                    del self._local_pending[seq]
        
        for seq in stale_sequences:
            try:
                pending_key = f"{self._key_prefix}audit:hash_chain:pending:{seq}"
                deleted = self._redis.delete(pending_key)
                if deleted:
                    self._cleaned_count += 1
                    logger.info(f"[PendingWatchdog] Cleaned stale seq {seq}")
            except Exception as e:
                logger.warning(f"[PendingWatchdog] Stale cleanup failed for {seq}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get watchdog statistics."""
        with self._lock:
            return {
                "local_pending_count": len(self._local_pending),
                "cleaned_count": self._cleaned_count,
                "is_running": self._is_running,
            }
