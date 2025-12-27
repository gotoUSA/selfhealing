"""
Adaptive Jitter - 지능형 Jitter 계산기.

시스템 상태에 따라 Jitter 범위를 동적으로 조절:
- 여유로운 상황: 최소 지터 (빠른 복구)
- 위험 상황: 최대 지터 (Thundering Herd 방지)

Usage:
    from load_tests.utils.selfhealing.adaptive_jitter import AdaptiveJitter
    
    # 기본 사용
    jitter = AdaptiveJitter.calculate()
    
    # 상황 기반 사용
    jitter = AdaptiveJitter.calculate(
        error_budget_remaining=0.15,  # 15% 남음 (위험)
        current_load=0.85,            # 85% 부하 (높음)
    )
    
    # 밀리초 단위
    jitter_ms = AdaptiveJitter.calculate_ms()
"""

import random
import time
import threading
import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class SystemState(Enum):
    """시스템 상태."""
    RELAXED = "relaxed"     # 여유로운 상태
    NORMAL = "normal"       # 보통 상태
    STRESSED = "stressed"   # 위험 상태


class AdaptiveJitter:
    """
    지능형 Jitter 계산기.
    
    시스템 상태에 따라 Jitter 범위를 동적으로 조절:
    - 여유로운 상황: 최소 지터 (빠른 복구)
    - 위험 상황: 최대 지터 (Thundering Herd 방지)
    """
    
    _lock = threading.Lock()
    
    # Jitter 범위 설정 (초) - V2.2 최적화
    JITTER_RELAXED = (0, 0.02)      # 여유: 0~20ms (최적화됨)
    JITTER_NORMAL = (0.02, 0.06)    # 보통: 20~60ms (최적화됨)
    JITTER_STRESSED = (0.05, 0.15)  # 위험: 50~150ms (최적화됨)
    
    # 임계값 - V2.2 최적화 (Relaxed 진입 조건 완화)
    ERROR_BUDGET_DANGER_THRESHOLD = 0.15  # 에러 버짓 15% 이하 → 위험
    ERROR_BUDGET_SAFE_THRESHOLD = 0.8     # 에러 버짓 80% 이상 → 여유 (50%→80%)
    LOAD_HIGH_THRESHOLD = 0.9             # 부하 90% 이상 → 위험 (80%→90%)
    LOAD_LOW_THRESHOLD = 0.5              # 부하 50% 이하 → 여유 (30%→50%)
    
    # 상태 캐시 (외부에서 주입)
    _cached_error_budget: Optional[float] = None
    _cached_load: Optional[float] = None
    _cache_updated_at: float = 0
    _cache_ttl: float = 5.0  # 5초
    
    # 통계
    _stats = {
        "relaxed_count": 0,
        "normal_count": 0,
        "stressed_count": 0,
        "total_jitter_ms": 0,
    }
    
    @classmethod
    def calculate(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ) -> float:
        """
        상황에 맞는 Jitter 값 계산.
        
        Args:
            error_budget_remaining: 남은 에러 버짓 비율 (0.0 ~ 1.0)
            current_load: 현재 시스템 부하 (0.0 ~ 1.0)
            
        Returns:
            적용할 Jitter 값 (초)
        """
        # 캐시된 값 사용 (외부에서 주입한 경우)
        if error_budget_remaining is None:
            error_budget_remaining = cls._get_cached_error_budget()
        if current_load is None:
            current_load = cls._get_cached_load()
        
        state = cls._determine_state(error_budget_remaining, current_load)
        jitter_range = cls._get_jitter_range(state)
        jitter = random.uniform(*jitter_range)
        
        # 통계 업데이트
        with cls._lock:
            cls._stats[f"{state.value}_count"] += 1
            cls._stats["total_jitter_ms"] += jitter * 1000
        
        logger.debug(f"AdaptiveJitter: state={state.value}, jitter={jitter*1000:.1f}ms")
        return jitter
    
    @classmethod
    def calculate_ms(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ) -> int:
        """밀리초 단위로 반환."""
        return int(cls.calculate(error_budget_remaining, current_load) * 1000)
    
    @classmethod
    def get_state(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ) -> SystemState:
        """현재 시스템 상태 반환."""
        if error_budget_remaining is None:
            error_budget_remaining = cls._get_cached_error_budget()
        if current_load is None:
            current_load = cls._get_cached_load()
        
        return cls._determine_state(error_budget_remaining, current_load)
    
    @classmethod
    def update_cache(cls, error_budget: Optional[float] = None, load: Optional[float] = None):
        """외부에서 상태 정보 주입."""
        with cls._lock:
            if error_budget is not None:
                cls._cached_error_budget = error_budget
            if load is not None:
                cls._cached_load = load
            cls._cache_updated_at = time.time()
    
    @classmethod
    def apply_jitter_sleep(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ):
        """Jitter만큼 슬립 (복구 시 사용)."""
        jitter = cls.calculate(error_budget_remaining, current_load)
        if jitter > 0:
            time.sleep(jitter)
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """통계 반환."""
        with cls._lock:
            total = (
                cls._stats["relaxed_count"] + 
                cls._stats["normal_count"] + 
                cls._stats["stressed_count"]
            )
            avg_jitter = cls._stats["total_jitter_ms"] / total if total > 0 else 0
            
            return {
                **cls._stats,
                "total_calculations": total,
                "avg_jitter_ms": avg_jitter,
            }
    
    @classmethod
    def reset_stats(cls):
        """통계 초기화."""
        with cls._lock:
            cls._stats = {
                "relaxed_count": 0,
                "normal_count": 0,
                "stressed_count": 0,
                "total_jitter_ms": 0,
            }
    
    @classmethod
    def configure_thresholds(
        cls,
        error_budget_danger: float = 0.2,
        error_budget_safe: float = 0.5,
        load_high: float = 0.8,
        load_low: float = 0.3,
    ):
        """임계값 설정."""
        cls.ERROR_BUDGET_DANGER_THRESHOLD = error_budget_danger
        cls.ERROR_BUDGET_SAFE_THRESHOLD = error_budget_safe
        cls.LOAD_HIGH_THRESHOLD = load_high
        cls.LOAD_LOW_THRESHOLD = load_low
    
    @classmethod
    def configure_jitter_ranges(
        cls,
        relaxed: tuple = (0, 0.05),
        normal: tuple = (0.03, 0.1),
        stressed: tuple = (0.1, 0.3),
    ):
        """Jitter 범위 설정."""
        cls.JITTER_RELAXED = relaxed
        cls.JITTER_NORMAL = normal
        cls.JITTER_STRESSED = stressed
    
    @classmethod
    def _determine_state(
        cls,
        error_budget_remaining: Optional[float],
        current_load: Optional[float]
    ) -> SystemState:
        """시스템 상태 판단."""
        # 정보가 없으면 보통 상태
        if error_budget_remaining is None and current_load is None:
            return SystemState.NORMAL
        
        # 위험 상황 판단
        is_budget_danger = (
            error_budget_remaining is not None and 
            error_budget_remaining < cls.ERROR_BUDGET_DANGER_THRESHOLD
        )
        is_load_high = (
            current_load is not None and 
            current_load > cls.LOAD_HIGH_THRESHOLD
        )
        
        # 여유 상황 판단
        is_budget_safe = (
            error_budget_remaining is not None and 
            error_budget_remaining > cls.ERROR_BUDGET_SAFE_THRESHOLD
        )
        is_load_low = (
            current_load is not None and 
            current_load < cls.LOAD_LOW_THRESHOLD
        )
        
        # 위험: 최대 지터
        if is_budget_danger or is_load_high:
            return SystemState.STRESSED
        
        # 여유: 최소 지터 (둘 다 여유로워야)
        if is_budget_safe and (is_load_low or current_load is None):
            return SystemState.RELAXED
        
        # 보통: 중간 지터
        return SystemState.NORMAL
    
    @classmethod
    def _get_jitter_range(cls, state: SystemState) -> tuple:
        """상태에 따른 Jitter 범위."""
        if state == SystemState.RELAXED:
            return cls.JITTER_RELAXED
        elif state == SystemState.STRESSED:
            return cls.JITTER_STRESSED
        else:
            return cls.JITTER_NORMAL
    
    @classmethod
    def _get_cached_error_budget(cls) -> Optional[float]:
        """캐시된 에러 버짓 조회."""
        with cls._lock:
            if time.time() - cls._cache_updated_at > cls._cache_ttl:
                return None
            return cls._cached_error_budget
    
    @classmethod
    def _get_cached_load(cls) -> Optional[float]:
        """캐시된 부하 조회."""
        with cls._lock:
            if time.time() - cls._cache_updated_at > cls._cache_ttl:
                return None
            return cls._cached_load
