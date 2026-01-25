"""
Crisis Multiplier for Error Budget.

Emergency Level에 따라 Error Budget 소진 가중치를 적용합니다.

Phase 1 핵심 기반:
- CrisisMultiplierConfig: 가중치 설정
- CrisisMultiplierProvider: 30초 캐시 + invalidate_cache()

Features:
- Emergency Level별 가중치 조회 (NORMAL=1.0x, LEVEL_3=5.0x)
- TTL 기반 캐시 (30초)
- 런타임 가중치 오버라이드
- max_multiplier를 통한 안전 제한

Usage:
    from selfhealing.services.error_budget.multiplier import (
        CrisisMultiplierConfig,
        CrisisMultiplierProvider,
        get_crisis_multiplier_provider,
    )
    
    # 기본 Provider 사용
    provider = get_crisis_multiplier_provider()
    multiplier = provider.get_current_multiplier()
    
    # 커스텀 설정
    config = CrisisMultiplierConfig(
        multipliers={EmergencyLevel.LEVEL_3: 10.0},
        max_multiplier=15.0,
    )
    provider = CrisisMultiplierProvider(config=config)

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any

from selfhealing.services.emergency_mode.enums import EmergencyLevel


logger = logging.getLogger(__name__)


# =============================================================================
# Settings Helpers
# =============================================================================

def _get_multiplier_cache_ttl() -> float:
    """ErrorBudgetSettings에서 Multiplier 캐시 TTL을 가져온다."""
    try:
        from selfhealing.settings.error_budget import get_error_budget_settings
        return get_error_budget_settings().multiplier_cache_ttl
    except Exception:
        return 30.0  # fallback


def _get_multiplier_max() -> float:
    """ErrorBudgetSettings에서 최대 Multiplier를 가져온다."""
    try:
        from selfhealing.settings.error_budget import get_error_budget_settings
        return get_error_budget_settings().multiplier_max
    except Exception:
        return 10.0  # fallback


# =============================================================================
# Constants (하위 호환성용)
# =============================================================================

DEFAULT_CACHE_TTL_SECONDS = 30.0
"""기본 캐시 TTL (하위 호환성용 레거시 상수)."""

DEFAULT_MAX_MULTIPLIER = 10.0
"""기본 최대 가중치 (하위 호환성용 레거시 상수)."""


# =============================================================================
# Default Multipliers
# =============================================================================

DEFAULT_CRISIS_MULTIPLIERS: Dict[EmergencyLevel, float] = {
    EmergencyLevel.NORMAL: 1.0,   # 기본 소진율
    EmergencyLevel.LEVEL_1: 1.5,  # 경미한 위기: 1.5배
    EmergencyLevel.LEVEL_2: 3.0,  # 중간 위기: 3배
    EmergencyLevel.LEVEL_3: 5.0,  # 심각한 위기: 5배
}
"""Emergency Level별 기본 가중치."""


# =============================================================================
# CrisisMultiplierConfig
# =============================================================================

@dataclass
class CrisisMultiplierConfig:
    """
    위기 가중치 설정.
    
    Emergency Level별 Error Budget 소진 가중치를 정의합니다.
    
    Attributes:
        multipliers: Level별 가중치 매핑
        enabled: 기능 활성화 여부
        max_multiplier: 최대 허용 가중치 (과도한 소진 방지)
    
    Example:
        # 기본 설정
        config = CrisisMultiplierConfig()
        
        # 커스텀 설정
        config = CrisisMultiplierConfig(
            multipliers={
                EmergencyLevel.NORMAL: 1.0,
                EmergencyLevel.LEVEL_3: 10.0,
            },
            max_multiplier=15.0,
        )
    """
    
    multipliers: Dict[EmergencyLevel, float] = field(
        default_factory=lambda: dict(DEFAULT_CRISIS_MULTIPLIERS)
    )
    """Emergency Level별 가중치 매핑."""
    
    enabled: bool = True
    """Crisis Multiplier 활성화 여부. False면 항상 1.0 반환."""
    
    max_multiplier: float = field(default_factory=_get_multiplier_max)
    """최대 허용 가중치 (안전 제한). Settings에서 가져옴."""
    
    def get_multiplier(self, level: EmergencyLevel) -> float:
        """
        레벨에 해당하는 가중치 반환.
        
        Args:
            level: Emergency 레벨
        
        Returns:
            가중치 값 (기본 1.0, max_multiplier 이하)
        """
        if not self.enabled:
            return 1.0
        
        multiplier = self.multipliers.get(level, 1.0)
        return min(multiplier, self.max_multiplier)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CrisisMultiplierConfig":
        """
        딕셔너리에서 CrisisMultiplierConfig 생성.
        
        Args:
            data: 설정 딕셔너리
                - multipliers: {"NORMAL": 1.0, "LEVEL_3": 5.0, ...}
                - enabled: bool
                - max_multiplier: float
        
        Returns:
            CrisisMultiplierConfig 인스턴스
        """
        multipliers = {}
        for level_name, value in data.get("multipliers", {}).items():
            try:
                # EmergencyLevel enum 이름 또는 값으로 파싱
                if isinstance(level_name, str):
                    level = EmergencyLevel[level_name]
                else:
                    level = EmergencyLevel(level_name)
                multipliers[level] = float(value)
            except (KeyError, ValueError):
                logger.warning(
                    f"[CrisisMultiplierConfig] Invalid level: {level_name}"
                )
                continue
        
        return cls(
            multipliers=multipliers or dict(DEFAULT_CRISIS_MULTIPLIERS),
            enabled=data.get("enabled", True),
            max_multiplier=data.get("max_multiplier", DEFAULT_MAX_MULTIPLIER),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        딕셔너리로 변환.
        
        Returns:
            설정 딕셔너리
        """
        return {
            "multipliers": {
                level.name: value
                for level, value in self.multipliers.items()
            },
            "enabled": self.enabled,
            "max_multiplier": self.max_multiplier,
        }


# =============================================================================
# CrisisMultiplierProvider
# =============================================================================

class CrisisMultiplierProvider:
    """
    위기 가중치 제공자.
    
    현재 Emergency Level에 따른 Error Budget 소진 가중치를 제공합니다.
    
    Features:
    - Emergency Level 기반 가중치 조회
    - 설정 가능한 가중치
    - TTL 캐시 (30초, Check on Use 패턴)
    - invalidate_cache()로 즉시 무효화 (격상 시)
    
    Usage:
        provider = CrisisMultiplierProvider()
        
        # 현재 가중치 조회
        multiplier = provider.get_current_multiplier()
        
        # 네임스페이스 지정
        multiplier = provider.get_current_multiplier(namespace="seoul")
        
        # 캐시 즉시 무효화 (Emergency 격상 시)
        provider.invalidate_cache()
    
    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
    """
    
    def __init__(
        self,
        config: Optional[CrisisMultiplierConfig] = None,
        cache_ttl: Optional[float] = None,
    ):
        """
        CrisisMultiplierProvider 초기화.
        
        Args:
            config: 가중치 설정 (None이면 기본값 사용)
            cache_ttl: 캐시 TTL (초). None이면 Settings에서 가져옴.
        """
        self.config = config or CrisisMultiplierConfig()
        self._emergency_tracker = None
        self._cache_ttl = cache_ttl if cache_ttl is not None else _get_multiplier_cache_ttl()
        
        # 캐시 상태
        self._cached_multiplier: Optional[float] = None
        self._cached_namespace: Optional[str] = None
        self._cache_timestamp: float = 0.0
    
    def _get_emergency_tracker(self):
        """
        EmergencyTracker 획득 (lazy loading).
        
        Returns:
            NamespacedEmergencyTracker 인스턴스
        """
        if self._emergency_tracker is None:
            from selfhealing.services.namespace_emergency import (
                get_namespaced_emergency_tracker,
            )
            self._emergency_tracker = get_namespaced_emergency_tracker()
        return self._emergency_tracker
    
    def get_current_multiplier(
        self,
        namespace: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> float:
        """
        현재 Crisis Multiplier 조회.
        
        Args:
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
            bypass_cache: True면 캐시 무시하고 조회
        
        Returns:
            현재 가중치 값 (1.0 ~ max_multiplier)
        """
        # 캐시 확인 (같은 namespace만)
        now = time.time()
        if (
            not bypass_cache
            and self._cached_multiplier is not None
            and self._cached_namespace == namespace
            and now - self._cache_timestamp < self._cache_ttl
        ):
            return self._cached_multiplier
        
        # Emergency Level 조회
        try:
            tracker = self._get_emergency_tracker()
            state = tracker.get_effective_state(namespace=namespace)
            level = state.emergency_level
        except Exception as e:
            logger.warning(
                f"[CrisisMultiplier] Failed to get emergency level, "
                f"using NORMAL: {e}"
            )
            level = EmergencyLevel.NORMAL
        
        # 가중치 조회
        multiplier = self.config.get_multiplier(level)
        
        # 캐시 저장
        self._cached_multiplier = multiplier
        self._cached_namespace = namespace
        self._cache_timestamp = now
        
        logger.debug(
            f"[CrisisMultiplier] Level={level.name}, "
            f"multiplier={multiplier}x, namespace={namespace}"
        )
        
        return multiplier
    
    def invalidate_cache(self) -> None:
        """
        캐시 무효화.
        
        Emergency 격상 시 이벤트 버스를 통해 호출되어
        30초 캐시 대기 없이 즉시 새 가중치를 적용합니다.
        """
        self._cached_multiplier = None
        self._cached_namespace = None
        self._cache_timestamp = 0.0
        
        logger.debug("[CrisisMultiplier] Cache invalidated")
    
    def set_multiplier_override(
        self,
        level: EmergencyLevel,
        multiplier: float,
    ) -> None:
        """
        특정 레벨의 가중치 오버라이드 (런타임 설정).
        
        Args:
            level: 대상 레벨
            multiplier: 새 가중치 (max_multiplier 이하로 제한됨)
        """
        capped_multiplier = min(multiplier, self.config.max_multiplier)
        self.config.multipliers[level] = capped_multiplier
        self.invalidate_cache()
        
        logger.info(
            f"[CrisisMultiplier] Override set: "
            f"level={level.name}, multiplier={capped_multiplier}"
        )
    
    def get_config(self) -> CrisisMultiplierConfig:
        """
        현재 설정 반환.
        
        Returns:
            CrisisMultiplierConfig 인스턴스
        """
        return self.config
    
    def get_all_multipliers(self) -> Dict[str, float]:
        """
        모든 레벨의 가중치 반환.
        
        Returns:
            레벨명 -> 가중치 매핑
        """
        return {
            level.name: self.config.get_multiplier(level)
            for level in EmergencyLevel
        }


# =============================================================================
# Singleton
# =============================================================================

_multiplier_provider: Optional[CrisisMultiplierProvider] = None
_provider_lock = None


def _get_lock():
    """threading.Lock 획득 (lazy)."""
    global _provider_lock
    if _provider_lock is None:
        import threading
        _provider_lock = threading.Lock()
    return _provider_lock


def get_crisis_multiplier_provider() -> CrisisMultiplierProvider:
    """
    CrisisMultiplierProvider 싱글톤 반환.
    
    Returns:
        CrisisMultiplierProvider 인스턴스
    """
    global _multiplier_provider
    if _multiplier_provider is None:
        with _get_lock():
            if _multiplier_provider is None:
                _multiplier_provider = CrisisMultiplierProvider()
    return _multiplier_provider


def configure_crisis_multiplier_provider(
    config: Optional[CrisisMultiplierConfig] = None,
    cache_ttl: float = DEFAULT_CACHE_TTL_SECONDS,
) -> CrisisMultiplierProvider:
    """
    CrisisMultiplierProvider 싱글톤 설정.
    
    Args:
        config: 가중치 설정
        cache_ttl: 캐시 TTL (초)
    
    Returns:
        설정된 CrisisMultiplierProvider 인스턴스
    """
    global _multiplier_provider
    with _get_lock():
        _multiplier_provider = CrisisMultiplierProvider(
            config=config,
            cache_ttl=cache_ttl,
        )
    return _multiplier_provider


def reset_crisis_multiplier_provider() -> None:
    """
    싱글톤 리셋 (테스트용).
    """
    global _multiplier_provider
    _multiplier_provider = None
