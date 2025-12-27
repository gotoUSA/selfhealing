"""
State Cache - TTL 기반 로컬 캐싱 + Polling Jitter.

Circuit Breaker 상태를 로컬에 캐싱하여 네트워크 호출 최소화.
Polling Jitter로 Thundering Herd 방지.

Usage:
    from load_tests.utils.selfhealing.state_cache import CBStateCache
    
    # 콜백 설정
    def fetch_cb_state(service):
        return requests.get(f'{host}/api/self-healing/status/{service}/').json()
    
    CBStateCache.configure(fetch_callback=fetch_cb_state)
    
    # 캐시된 상태 조회 (~0ms 캐시 히트)
    state = CBStateCache.get_state('payment')
"""

import time
import random
import threading
import logging
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class CBStateCache:
    """
    Circuit Breaker 상태 캐시
    
    - TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
    - Polling Jitter로 Thundering Herd 방지
    - Thread-Safe 구현
    """
    
    _cache: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()
    _fetch_callback: Optional[Callable[[str], Dict]] = None
    
    # 설정 - V2.2 최적화 (더 짧은 TTL로 더 신선한 데이터)
    BASE_TTL = 3.0  # 기본 TTL (초) - 5.0 → 3.0
    JITTER_RANGE = 0.3  # ±0.3초 랜덤 지터 - 0.5 → 0.3
    
    # 동적 TTL 설정 (최적화 #2) - V2.2 범위 조정
    MIN_TTL = 2.0  # 최소 TTL (위험 상황) - 3.0 → 2.0
    MAX_TTL = 6.0  # 최대 TTL (여유 상황) - 10.0 → 6.0
    DYNAMIC_TTL_ENABLED = True
    
    # 시스템 상태 캐시 (AdaptiveJitter 연동용)
    _error_budget_remaining: Optional[float] = None
    _system_load: Optional[float] = None
    
    # 통계
    _stats = {
        "cache_hits": 0,
        "cache_misses": 0,
        "fetch_errors": 0,
        "dynamic_ttl_adjustments": 0,
    }
    
    @classmethod
    def configure(cls, fetch_callback: Callable[[str], Dict], base_ttl: float = 5.0, jitter_range: float = 0.5,
                  min_ttl: float = 3.0, max_ttl: float = 10.0, dynamic_ttl: bool = True):
        """
        상태 조회 콜백 설정.
        
        Args:
            fetch_callback: 서비스명을 받아 CB 상태를 가져오는 함수
            base_ttl: 기본 TTL (초)
            jitter_range: 지터 범위 (초)
            min_ttl: 최소 TTL (위험 상황)
            max_ttl: 최대 TTL (여유 상황)
            dynamic_ttl: 동적 TTL 활성화 여부
        """
        cls._fetch_callback = fetch_callback
        cls.BASE_TTL = base_ttl
        cls.JITTER_RANGE = jitter_range
        cls.MIN_TTL = min_ttl
        cls.MAX_TTL = max_ttl
        cls.DYNAMIC_TTL_ENABLED = dynamic_ttl
        logger.info(f"CBStateCache configured with TTL={base_ttl}s (dynamic: {min_ttl}~{max_ttl}s), jitter=±{jitter_range}s")
    
    @classmethod
    def update_system_state(cls, error_budget_remaining: Optional[float] = None, system_load: Optional[float] = None):
        """
        시스템 상태 업데이트 (동적 TTL 및 AdaptiveJitter 연동).
        
        Args:
            error_budget_remaining: 남은 에러 버짓 비율 (0.0 ~ 1.0)
            system_load: 현재 시스템 부하 (0.0 ~ 1.0)
        """
        with cls._lock:
            if error_budget_remaining is not None:
                cls._error_budget_remaining = error_budget_remaining
            if system_load is not None:
                cls._system_load = system_load
    
    @classmethod
    def get_system_state(cls) -> Dict[str, Optional[float]]:
        """현재 시스템 상태 반환."""
        with cls._lock:
            return {
                "error_budget_remaining": cls._error_budget_remaining,
                "system_load": cls._system_load,
            }
    
    @classmethod
    def get_state(cls, service: str, force_refresh: bool = False) -> Optional[Dict]:
        """
        CB 상태 조회 (캐시 우선).
        
        캐시 히트: ~0.01ms
        캐시 미스: 사령탑 호출 시간
        
        Args:
            service: 서비스 식별자
            force_refresh: True면 캐시 무시하고 새로 조회
            
        Returns:
            CB 상태 딕셔너리 또는 None
        """
        if not force_refresh:
            with cls._lock:
                cached = cls._cache.get(service)
                
                if cached and not cls._is_expired(cached):
                    cls._stats["cache_hits"] += 1
                    return cached['state']
        
        # 캐시 미스 → 사령탑 호출
        cls._stats["cache_misses"] += 1
        return cls._refresh(service)
    
    @classmethod
    def get_state_with_meta(cls, service: str) -> Dict[str, Any]:
        """
        상태와 캐시 메타데이터 함께 반환.
        
        Returns:
            {
                "state": {...},
                "from_cache": bool,
                "cache_age_ms": float,
                "ttl_remaining_ms": float
            }
        """
        with cls._lock:
            cached = cls._cache.get(service)
            
            if cached and not cls._is_expired(cached):
                cls._stats["cache_hits"] += 1
                now = time.time()
                return {
                    "state": cached['state'],
                    "from_cache": True,
                    "cache_age_ms": (now - cached['fetched_at']) * 1000,
                    "ttl_remaining_ms": (cached['expires_at'] - now) * 1000,
                }
        
        # 캐시 미스
        cls._stats["cache_misses"] += 1
        state = cls._refresh(service)
        
        return {
            "state": state,
            "from_cache": False,
            "cache_age_ms": 0,
            "ttl_remaining_ms": cls._calculate_ttl() * 1000 if state else 0,
        }
    
    @classmethod
    def invalidate(cls, service: str):
        """특정 서비스 캐시 무효화."""
        with cls._lock:
            if service in cls._cache:
                del cls._cache[service]
                logger.debug(f"Cache invalidated for service: {service}")
    
    @classmethod
    def invalidate_all(cls):
        """전체 캐시 무효화."""
        with cls._lock:
            cls._cache.clear()
            logger.info("All cache entries invalidated")
    
    @classmethod
    def warm_up(cls, services: list):
        """
        캐시 워밍업 - 여러 서비스 상태를 미리 조회.
        
        Args:
            services: 서비스명 리스트
        """
        for service in services:
            try:
                cls._refresh(service)
                logger.debug(f"Cache warmed up for: {service}")
            except Exception as e:
                logger.warning(f"Failed to warm up cache for {service}: {e}")
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """캐시 통계 반환."""
        with cls._lock:
            total = cls._stats["cache_hits"] + cls._stats["cache_misses"]
            hit_rate = cls._stats["cache_hits"] / total if total > 0 else 0
            
            return {
                **cls._stats,
                "total_requests": total,
                "hit_rate": hit_rate,
                "cached_services": len(cls._cache),
            }
    
    @classmethod
    def reset_stats(cls):
        """통계 초기화."""
        with cls._lock:
            cls._stats = {
                "cache_hits": 0,
                "cache_misses": 0,
                "fetch_errors": 0,
                "dynamic_ttl_adjustments": 0,
            }
    
    @classmethod
    def _is_expired(cls, cached: Dict) -> bool:
        """TTL 만료 확인."""
        return time.time() >= cached['expires_at']
    
    @classmethod
    def _calculate_ttl(cls) -> float:
        """
        상황 기반 동적 TTL 계산 (최적화 #2).
        
        - 여유 상황 (에러버짓 50%+, 부하 30%-): 긴 TTL (MAX_TTL)
        - 위험 상황 (에러버짓 20%-, 부하 80%+): 짧은 TTL (MIN_TTL)
        - 일반 상황: 기본 TTL (BASE_TTL)
        
        Thundering Herd 방지를 위해 Jitter 적용.
        """
        if cls.DYNAMIC_TTL_ENABLED:
            base = cls._calculate_dynamic_base_ttl()
        else:
            base = cls.BASE_TTL
        
        jitter = random.uniform(-cls.JITTER_RANGE, cls.JITTER_RANGE)
        return base + jitter
    
    @classmethod
    def _calculate_dynamic_base_ttl(cls) -> float:
        """시스템 상태 기반 동적 기본 TTL 계산."""
        with cls._lock:
            error_budget = cls._error_budget_remaining
            load = cls._system_load
        
        # 정보가 없으면 기본값
        if error_budget is None and load is None:
            return cls.BASE_TTL
        
        # 위험 상황: 짧은 TTL (빠른 상태 갱신 필요)
        is_budget_danger = error_budget is not None and error_budget < 0.2
        is_load_high = load is not None and load > 0.8
        
        if is_budget_danger or is_load_high:
            cls._stats["dynamic_ttl_adjustments"] += 1
            return cls.MIN_TTL
        
        # 여유 상황: 긴 TTL (네트워크 호출 최소화)
        is_budget_safe = error_budget is not None and error_budget > 0.5
        is_load_low = load is not None and load < 0.3
        
        if is_budget_safe and (is_load_low or load is None):
            cls._stats["dynamic_ttl_adjustments"] += 1
            return cls.MAX_TTL
        
        return cls.BASE_TTL
    
    @classmethod
    def _refresh(cls, service: str) -> Optional[Dict]:
        """사령탑에서 상태 가져와 캐시 갱신."""
        if not cls._fetch_callback:
            logger.warning("No fetch callback configured, returning None")
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
            
            logger.debug(f"Refreshed cache for {service}, TTL={ttl:.2f}s")
            return state
            
        except Exception as e:
            cls._stats["fetch_errors"] += 1
            logger.error(f"Failed to fetch CB state for {service}: {e}")
            
            # SafeDefaults 사용 (가능한 경우)
            try:
                from .defaults import SafeDefaults
                SafeDefaults.enter_degraded_mode()
                return SafeDefaults.get_cb_config()
            except ImportError:
                return None
