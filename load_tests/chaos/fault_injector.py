"""
장애 주입기 - Fault Injector

Netflix Chaos Monkey 스타일의 제어된 장애 주입
"""

import os
import time
import random
from enum import Enum
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass


class FaultType(Enum):
    """장애 유형"""
    LATENCY = "latency"           # 지연
    ERROR_500 = "error_500"       # 서버 에러
    ERROR_503 = "error_503"       # 서비스 불가
    TIMEOUT = "timeout"           # 타임아웃
    CONNECTION_RESET = "connection_reset"  # 연결 끊김


@dataclass
class FaultConfig:
    """장애 설정"""
    fault_type: FaultType
    probability: float  # 0.0 ~ 1.0
    min_latency_ms: int = 0
    max_latency_ms: int = 0
    enabled: bool = True


class FaultInjector:
    """
    카오스 장애 주입기
    
    환경변수로 활성화/설정 가능:
    - CHAOS_ENABLED: true/false
    - CHAOS_PROBABILITY: 0.0 ~ 1.0
    - CHAOS_LATENCY_MIN_MS: 최소 지연 (ms)
    - CHAOS_LATENCY_MAX_MS: 최대 지연 (ms)
    """

    # 기본 장애 설정
    DEFAULT_FAULTS: Dict[FaultType, FaultConfig] = {
        FaultType.LATENCY: FaultConfig(
            fault_type=FaultType.LATENCY,
            probability=0.10,  # 10%
            min_latency_ms=500,
            max_latency_ms=3000,
        ),
        FaultType.ERROR_500: FaultConfig(
            fault_type=FaultType.ERROR_500,
            probability=0.05,  # 5%
        ),
        FaultType.ERROR_503: FaultConfig(
            fault_type=FaultType.ERROR_503,
            probability=0.03,  # 3%
        ),
        FaultType.TIMEOUT: FaultConfig(
            fault_type=FaultType.TIMEOUT,
            probability=0.02,  # 2%
        ),
        FaultType.CONNECTION_RESET: FaultConfig(
            fault_type=FaultType.CONNECTION_RESET,
            probability=0.01,  # 1%
        ),
    }

    def __init__(self, custom_faults: Optional[Dict[FaultType, FaultConfig]] = None):
        """
        Args:
            custom_faults: 커스텀 장애 설정 (기본값 덮어쓰기)
        """
        self.enabled = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
        self.global_probability = float(os.getenv("CHAOS_PROBABILITY", "0.10"))
        
        # 환경변수에서 지연 설정 로드
        self.latency_min = int(os.getenv("CHAOS_LATENCY_MIN_MS", "500"))
        self.latency_max = int(os.getenv("CHAOS_LATENCY_MAX_MS", "3000"))
        
        # 장애 설정 초기화
        self.faults = self.DEFAULT_FAULTS.copy()
        if custom_faults:
            self.faults.update(custom_faults)
        
        # 환경변수 기반 지연 설정 업데이트
        if FaultType.LATENCY in self.faults:
            self.faults[FaultType.LATENCY].min_latency_ms = self.latency_min
            self.faults[FaultType.LATENCY].max_latency_ms = self.latency_max

        # 활성화된 장애 목록
        self._active_faults: list = []
        
        # 통계
        self.stats = {
            "total_checks": 0,
            "faults_injected": 0,
            "by_type": {ft.value: 0 for ft in FaultType},
        }

    def enable(self):
        """장애 주입 활성화"""
        self.enabled = True

    def disable(self):
        """장애 주입 비활성화"""
        self.enabled = False

    def set_probability(self, probability: float):
        """전역 확률 설정"""
        self.global_probability = max(0.0, min(1.0, probability))

    def activate_fault(self, fault_type: FaultType):
        """특정 장애 타입 활성화"""
        if fault_type not in self._active_faults:
            self._active_faults.append(fault_type)

    def deactivate_fault(self, fault_type: FaultType):
        """특정 장애 타입 비활성화"""
        if fault_type in self._active_faults:
            self._active_faults.remove(fault_type)

    def activate_all(self):
        """모든 장애 타입 활성화"""
        self._active_faults = list(FaultType)

    def deactivate_all(self):
        """모든 장애 타입 비활성화"""
        self._active_faults.clear()

    def should_inject(self, fault_type: Optional[FaultType] = None) -> bool:
        """
        장애 주입 여부 결정
        
        Args:
            fault_type: 특정 장애 타입 (None이면 랜덤)
            
        Returns:
            장애 주입 여부
        """
        if not self.enabled:
            return False

        self.stats["total_checks"] += 1

        if fault_type:
            config = self.faults.get(fault_type)
            if config and config.enabled:
                return random.random() < config.probability
            return False
        else:
            # 전역 확률로 결정
            return random.random() < self.global_probability

    def inject_latency(
        self, 
        min_ms: Optional[int] = None, 
        max_ms: Optional[int] = None,
    ) -> int:
        """
        지연 주입
        
        Args:
            min_ms: 최소 지연 (ms)
            max_ms: 최대 지연 (ms)
            
        Returns:
            주입된 지연 시간 (ms)
        """
        config = self.faults.get(FaultType.LATENCY)
        min_latency = min_ms or (config.min_latency_ms if config else 500)
        max_latency = max_ms or (config.max_latency_ms if config else 3000)

        latency_ms = random.randint(min_latency, max_latency)
        time.sleep(latency_ms / 1000.0)
        
        self.stats["faults_injected"] += 1
        self.stats["by_type"]["latency"] += 1
        
        return latency_ms

    def maybe_inject_latency(
        self,
        min_ms: Optional[int] = None,
        max_ms: Optional[int] = None,
    ) -> Optional[int]:
        """
        확률에 따라 지연 주입
        
        Returns:
            주입된 지연 시간 (ms) 또는 None
        """
        if self.should_inject(FaultType.LATENCY):
            return self.inject_latency(min_ms, max_ms)
        return None

    def get_random_fault(self) -> Optional[FaultType]:
        """
        랜덤 장애 타입 선택
        
        Returns:
            선택된 장애 타입 또는 None
        """
        if not self.enabled:
            return None

        # 활성화된 장애 중에서 확률 기반 선택
        active = self._active_faults or list(FaultType)
        
        for fault_type in active:
            if self.should_inject(fault_type):
                self.stats["faults_injected"] += 1
                self.stats["by_type"][fault_type.value] += 1
                return fault_type
        
        return None

    def wrap_request(
        self, 
        request_func: Callable, 
        *args, 
        **kwargs,
    ) -> Any:
        """
        요청 함수를 감싸서 장애 주입
        
        Args:
            request_func: 원본 요청 함수
            *args, **kwargs: 요청 함수 인자
            
        Returns:
            요청 결과 또는 장애 응답
        """
        fault = self.get_random_fault()
        
        if fault == FaultType.LATENCY:
            self.inject_latency()
            return request_func(*args, **kwargs)
        
        elif fault == FaultType.TIMEOUT:
            # 타임아웃 시뮬레이션 (긴 지연)
            time.sleep(30)  # 30초 지연
            return request_func(*args, **kwargs)
        
        # 다른 장애 타입은 요청 후 처리 필요
        return request_func(*args, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        """통계 조회"""
        return self.stats.copy()

    def reset_stats(self):
        """통계 초기화"""
        self.stats = {
            "total_checks": 0,
            "faults_injected": 0,
            "by_type": {ft.value: 0 for ft in FaultType},
        }


# 전역 인스턴스
_default_injector: Optional[FaultInjector] = None


def get_fault_injector() -> FaultInjector:
    """기본 FaultInjector 인스턴스 반환"""
    global _default_injector
    if _default_injector is None:
        _default_injector = FaultInjector()
    return _default_injector


def inject_chaos(probability: float = 0.10) -> Optional[FaultType]:
    """
    간편한 카오스 주입 함수
    
    Usage:
        from load_tests.chaos import inject_chaos
        
        fault = inject_chaos(0.10)  # 10% 확률
        if fault:
            print(f"Injected: {fault}")
    """
    injector = get_fault_injector()
    injector.set_probability(probability)
    injector.enable()
    return injector.get_random_fault()
