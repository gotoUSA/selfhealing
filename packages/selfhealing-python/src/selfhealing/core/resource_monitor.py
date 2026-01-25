"""
Container/VM Resource Monitor.

cgroup v1/v2 지원 리소스 모니터링 유틸리티.

Cgroup 기반으로 컨테이너의 메모리/CPU 제한을 감지하고,
Chaos Experiment의 Resource Exhaustion이 안전 한계 내에서 동작하도록 합니다.

설정값은 StateCacheSettings를 통해 환경변수로 오버라이드 가능:
- SELFHEALING_STATE_CACHE_RESOURCE_SAFETY_MARGIN
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from selfhealing.settings.state_cache import get_state_cache_settings

logger = logging.getLogger(__name__)


class CgroupResourceMonitor:
    """
    Cgroup 기반 리소스 모니터.
    
    Memory, CPU 제한 감지 및 현재 사용량 조회.
    cgroup v1 및 v2 모두 지원.
    
    Usage:
        max_bytes = CgroupResourceMonitor.get_memory_max_bytes()
        current_bytes = CgroupResourceMonitor.get_memory_current_bytes()
        available = CgroupResourceMonitor.get_available_memory_bytes()
    """
    
    # cgroup v2 경로 (Kubernetes 1.25+, Docker 20.10+)
    CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
    CGROUP_V2_MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")
    
    # cgroup v1 경로 (레거시 호환)
    CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    CGROUP_V1_MEMORY_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

    @classmethod
    def _get_default_safety_margin(cls) -> float:
        """기본 안전 마진 (15%). StateCacheSettings에서 로드."""
        return get_state_cache_settings().resource_safety_margin
    
    @classmethod
    def get_memory_max_bytes(cls) -> Optional[int]:
        """
        컨테이너 메모리 제한 (bytes).
        
        Returns:
            메모리 제한 (bytes). None = 제한 없음 또는 감지 불가.
        """
        try:
            # cgroup v2 먼저 시도
            if cls.CGROUP_V2_MEMORY_MAX.exists():
                content = cls.CGROUP_V2_MEMORY_MAX.read_text().strip()
                if content != "max":  # "max" = 제한 없음
                    return int(content)
                return None
            
            # cgroup v1 폴백
            if cls.CGROUP_V1_MEMORY_LIMIT.exists():
                value = int(cls.CGROUP_V1_MEMORY_LIMIT.read_text().strip())
                # 매우 큰 값은 사실상 무제한 (9EB 정도)
                if value < 2**62:
                    return value
                return None
            
            return None
        except Exception as e:
            logger.debug(f"[CgroupResourceMonitor] Failed to read memory max: {e}")
            return None
    
    @classmethod
    def get_memory_current_bytes(cls) -> Optional[int]:
        """
        현재 메모리 사용량 (bytes).
        
        Returns:
            현재 사용량 (bytes). None = 감지 불가.
        """
        try:
            if cls.CGROUP_V2_MEMORY_CURRENT.exists():
                return int(cls.CGROUP_V2_MEMORY_CURRENT.read_text().strip())
            
            if cls.CGROUP_V1_MEMORY_USAGE.exists():
                return int(cls.CGROUP_V1_MEMORY_USAGE.read_text().strip())
            
            return None
        except Exception as e:
            logger.debug(f"[CgroupResourceMonitor] Failed to read memory current: {e}")
            return None
    
    @classmethod
    def get_available_memory_bytes(
        cls,
        safety_margin: Optional[float] = None,
    ) -> Optional[int]:
        """
        안전하게 사용 가능한 메모리 (bytes).
        
        OOM Killer 발동을 방지하기 위해 안전 마진을 적용합니다.
        
        Args:
            safety_margin: OOM 방지 여유분 비율 (기본 15%, 환경변수로 설정 가능)
        
        Returns:
            (max - current) * (1 - safety_margin). None = 계산 불가.
            
        Example:
            # 1GB 제한, 700MB 사용 중, 15% 마진
            # available = (1024MB - 700MB) * 0.85 = 275MB
        """
        if safety_margin is None:
            safety_margin = cls._get_default_safety_margin()
        
        max_bytes = cls.get_memory_max_bytes()
        current_bytes = cls.get_memory_current_bytes()
        
        if max_bytes is None or current_bytes is None:
            return None
        
        available = max_bytes - current_bytes
        safe_available = int(available * (1.0 - safety_margin))
        
        logger.debug(
            f"[CgroupResourceMonitor] max={max_bytes / 1024 / 1024:.0f}MB, "
            f"current={current_bytes / 1024 / 1024:.0f}MB, "
            f"available={available / 1024 / 1024:.0f}MB, "
            f"safe(margin={safety_margin * 100:.0f}%)={safe_available / 1024 / 1024:.0f}MB"
        )
        
        return max(0, safe_available)
    
    @classmethod
    def get_memory_usage_percent(cls) -> Optional[float]:
        """
        현재 메모리 사용률 (%).
        
        Returns:
            사용률 0.0~100.0. None = 계산 불가.
        """
        max_bytes = cls.get_memory_max_bytes()
        current_bytes = cls.get_memory_current_bytes()
        
        if max_bytes is None or current_bytes is None or max_bytes == 0:
            return None
        
        return (current_bytes / max_bytes) * 100.0
    
    @classmethod
    def is_memory_constrained(cls) -> bool:
        """
        컨테이너가 메모리 제한이 설정되어 있는지 확인.
        
        Returns:
            True if cgroup 메모리 제한이 설정됨.
        """
        return cls.get_memory_max_bytes() is not None
    
    @classmethod
    def check_safe_for_exhaustion(
        cls,
        requested_bytes: int,
        safety_margin: Optional[float] = None,
    ) -> tuple[bool, int]:
        """
        ResourceExhaustion 실험에서 요청된 메모리가 안전한지 확인.
        
        Args:
            requested_bytes: 요청된 메모리 (bytes)
            safety_margin: 안전 마진 (기본 15%, 환경변수로 설정 가능)
        
        Returns:
            (is_safe, actual_bytes_to_use)
            - is_safe: 요청량이 안전 한계 내인지
            - actual_bytes_to_use: 실제 사용해야 할 bytes (캡 적용됨)
        """
        if safety_margin is None:
            safety_margin = cls._get_default_safety_margin()
        
        available = cls.get_available_memory_bytes(safety_margin)
        
        if available is None:
            # cgroup 감지 불가 - 제한 없이 허용
            logger.warning(
                "[CgroupResourceMonitor] Cannot detect cgroup limits, "
                "allowing full requested amount"
            )
            return True, requested_bytes
        
        if requested_bytes <= available:
            return True, requested_bytes
        
        # 안전 한계 초과 - 캡 적용
        logger.warning(
            f"[CgroupResourceMonitor] Requested {requested_bytes / 1024 / 1024:.0f}MB "
            f"exceeds safe limit {available / 1024 / 1024:.0f}MB, capping"
        )
        return False, available


# Backward compatibility alias
CgroupMemoryMonitor = CgroupResourceMonitor
