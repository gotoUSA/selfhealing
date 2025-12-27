# packages/selfhealing-python/src/selfhealing/core/state_cache.py
"""
Circuit Breaker 상태 캐시 (Platinum SLA 최적화)

TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
Polling Jitter로 Thundering Herd 방지

Reference: docs/self_healing/21_PLATINUM_SLA_OPTIMIZATION_PLAN.md
"""

import time
import random
import threading
from typing import Dict, Any, Optional, Callable

__all__ = ["CBStateCache"]


class CBStateCache:
    """
    Circuit Breaker 상태 캐시
    
    - TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
    - Polling Jitter로 Thundering Herd 방지
    - Thread-Safe 구현
    
    Usage:
        def fetch_cb_state(service):
            return requests.get(f'http://command-center/cb/{service}').json()
        
        CBStateCache.configure(fetch_callback=fetch_cb_state)
        state = CBStateCache.get_state('payment')
    """
    
    _cache: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()
    _fetch_callback: Optional[Callable[[str], Dict]] = None
    
    # 설정
    BASE_TTL = 5.0  # 기본 TTL (초)
    JITTER_RANGE = 0.5  # ±0.5초 랜덤 지터
    
    @classmethod
    def configure(cls, fetch_callback: Callable[[str], Dict]) -> None:
        """
        상태 조회 콜백 설정
        
        Args:
            fetch_callback: 서비스명을 받아 사령탑에서 CB 상태를 가져오는 함수
        """
        cls._fetch_callback = fetch_callback
    
    @classmethod
    def get_state(cls, service: str) -> Optional[Dict]:
        """
        CB 상태 조회 (캐시 우선)
        
        캐시 히트: ~0.01ms
        캐시 미스: 사령탑 호출 시간
        
        Args:
            service: 서비스 식별자
            
        Returns:
            CB 상태 딕셔너리 또는 None
        """
        with cls._lock:
            cached = cls._cache.get(service)
            
            if cached and not cls._is_expired(cached):
                return cached['state']
        
        # 캐시 미스 → 사령탑 호출
        return cls._refresh(service)
    
    @classmethod
    def set_state(cls, service: str, state: Dict[str, Any]) -> None:
        """
        CB 상태를 캐시에 직접 설정 (테스트 또는 수동 업데이트용)
        
        Args:
            service: 서비스 식별자
            state: CB 상태 딕셔너리
        """
        ttl = cls._calculate_ttl()
        
        with cls._lock:
            cls._cache[service] = {
                'state': state,
                'fetched_at': time.time(),
                'expires_at': time.time() + ttl,
            }
    
    @classmethod
    def invalidate(cls, service: str) -> None:
        """특정 서비스 캐시 무효화"""
        with cls._lock:
            cls._cache.pop(service, None)
    
    @classmethod
    def invalidate_all(cls) -> None:
        """전체 캐시 무효화"""
        with cls._lock:
            cls._cache.clear()
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """캐시 통계 조회"""
        with cls._lock:
            now = time.time()
            total = len(cls._cache)
            expired = sum(1 for v in cls._cache.values() if now >= v['expires_at'])
            return {
                'total_entries': total,
                'expired_entries': expired,
                'active_entries': total - expired,
            }
    
    @classmethod
    def _is_expired(cls, cached: Dict) -> bool:
        """TTL 만료 확인"""
        return time.time() >= cached['expires_at']
    
    @classmethod
    def _calculate_ttl(cls) -> float:
        """
        Jitter가 적용된 TTL 계산
        
        Thundering Herd 방지를 위해 4.5초 ~ 5.5초 사이 랜덤
        """
        jitter = random.uniform(-cls.JITTER_RANGE, cls.JITTER_RANGE)
        return cls.BASE_TTL + jitter
    
    @classmethod
    def _refresh(cls, service: str) -> Optional[Dict]:
        """사령탑에서 상태 가져와 캐시 갱신"""
        if not cls._fetch_callback:
            return None
        
        try:
            state = cls._fetch_callback(service)
            ttl = cls._calculate_ttl()
            
            with cls._lock:
                cls._cache[service] = {
                    'state': state,
                    'fetched_at': time.time(),
                    'expires_at': time.time() + ttl,
                }
            
            return state
            
        except Exception:
            # 사령탑 연결 실패 → DegradedModeHandler 사용
            from selfhealing.core.degraded_mode_handler import DegradedModeHandler
            DegradedModeHandler.enter_degraded_mode()
            return DegradedModeHandler.get_cb_config()
    
    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        with cls._lock:
            cls._cache.clear()
            cls._fetch_callback = None
