"""
Error Budget Gate - In-Memory Rate Limiter.

Redis 의존 없이 동작하는 메모리 기반 Sliding Window Rate Limiter.
Fail-Open 상황(Redis/DB 장애)에서도 무한 요청을 방지하기 위한 최후의 방어선.

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """
    Redis 의존 없이 동작하는 메모리 기반 Sliding Window Rate Limiter.
    
    Fail-Open 상황(Redis/DB 장애)에서도 무한 요청을 방지하기 위한
    최후의 방어선입니다. 외부 의존성 없이 순수 메모리로 동작합니다.
    
    특징:
    - Thread-safe (RLock 사용)
    - Sliding window 알고리즘
    - 자동 정리 (오래된 타임스탬프 제거)
    - 설정 동적 변경 지원
    """
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            max_requests: 윈도우 내 최대 요청 횟수
            window_seconds: 슬라이딩 윈도우 크기 (초)
        """
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.RLock()
    
    def update_limits(self, max_requests: int, window_seconds: int) -> None:
        """Rate limit 설정 동적 업데이트."""
        with self._lock:
            self._max_requests = max_requests
            self._window_seconds = window_seconds
            logger.info(
                f"[RateLimiter] Updated limits: {max_requests} requests / {window_seconds}s"
            )
    
    def _cleanup_old_timestamps(self, now: float) -> None:
        """윈도우 밖의 오래된 타임스탬프 정리."""
        cutoff = now - self._window_seconds
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]
    
    def try_acquire(self) -> tuple[bool, int, datetime]:
        """
        요청 허용 여부 확인 및 카운트 증가.
        
        Returns:
            tuple of:
                - allowed: 요청 허용 여부
                - remaining: 남은 요청 횟수
                - reset_at: 윈도우 리셋 시각
        """
        with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            self._cleanup_old_timestamps(now)
            
            remaining = max(0, self._max_requests - len(self._timestamps))
            reset_at = datetime.fromtimestamp(
                now + self._window_seconds, tz=timezone.utc
            )
            
            if len(self._timestamps) < self._max_requests:
                self._timestamps.append(now)
                remaining = max(0, self._max_requests - len(self._timestamps))
                return True, remaining, reset_at
            else:
                return False, 0, reset_at
    
    def get_status(self) -> Dict[str, Any]:
        """현재 Rate Limiter 상태 조회."""
        with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            self._cleanup_old_timestamps(now)
            
            return {
                "current_count": len(self._timestamps),
                "max_requests": self._max_requests,
                "window_seconds": self._window_seconds,
                "remaining": max(0, self._max_requests - len(self._timestamps)),
            }
    
    def reset(self) -> None:
        """Rate limiter 초기화 (테스트/관리용)."""
        with self._lock:
            self._timestamps.clear()
            logger.info("[RateLimiter] Reset - all timestamps cleared")


__all__ = [
    "InMemoryRateLimiter",
]
