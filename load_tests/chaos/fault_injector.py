"""
Fault Injector - Chaos Engineering

Netflix Chaos Monkey style controlled fault injection
"""

import os
import time
import random
from enum import Enum
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass


class FaultType(Enum):
    """Fault types"""

    LATENCY = "latency"  # Latency
    ERROR_500 = "error_500"  # Server error
    ERROR_503 = "error_503"  # Service unavailable
    TIMEOUT = "timeout"  # Timeout
    CONNECTION_RESET = "connection_reset"  # Connection reset


@dataclass
class FaultConfig:
    """Fault configuration"""

    fault_type: FaultType
    probability: float  # 0.0 ~ 1.0
    min_latency_ms: int = 0
    max_latency_ms: int = 0
    enabled: bool = True


class FaultInjector:
    """
    Chaos Fault Injector

    Can be enabled/configured via environment variables:
    - CHAOS_ENABLED: true/false
    - CHAOS_PROBABILITY: 0.0 ~ 1.0
    - CHAOS_LATENCY_MIN_MS: Minimum latency (ms)
    - CHAOS_LATENCY_MAX_MS: Maximum latency (ms)
    """

    # Default fault configuration
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
            custom_faults: Custom fault configuration (overrides defaults)
        """
        self.enabled = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
        self.global_probability = float(os.getenv("CHAOS_PROBABILITY", "0.10"))

        # Load latency settings from environment variables
        self.latency_min = int(os.getenv("CHAOS_LATENCY_MIN_MS", "500"))
        self.latency_max = int(os.getenv("CHAOS_LATENCY_MAX_MS", "3000"))

        # Initialize fault configuration
        self.faults = self.DEFAULT_FAULTS.copy()
        if custom_faults:
            self.faults.update(custom_faults)

        # Update latency settings from environment variables
        if FaultType.LATENCY in self.faults:
            self.faults[FaultType.LATENCY].min_latency_ms = self.latency_min
            self.faults[FaultType.LATENCY].max_latency_ms = self.latency_max

        # Active fault list
        self._active_faults: list = []

        # Statistics
        self.stats = {
            "total_checks": 0,
            "faults_injected": 0,
            "by_type": {ft.value: 0 for ft in FaultType},
        }

    def enable(self):
        """Enable fault injection"""
        self.enabled = True

    def disable(self):
        """Disable fault injection"""
        self.enabled = False

    def set_probability(self, probability: float):
        """Set global probability"""
        self.global_probability = max(0.0, min(1.0, probability))

    def activate_fault(self, fault_type: FaultType):
        """Activate specific fault type"""
        if fault_type not in self._active_faults:
            self._active_faults.append(fault_type)

    def deactivate_fault(self, fault_type: FaultType):
        """Deactivate specific fault type"""
        if fault_type in self._active_faults:
            self._active_faults.remove(fault_type)

    def activate_all(self):
        """Activate all fault types"""
        self._active_faults = list(FaultType)

    def deactivate_all(self):
        """Deactivate all fault types"""
        self._active_faults.clear()

    def should_inject(self, fault_type: Optional[FaultType] = None) -> bool:
        """
        Determine whether to inject fault

        Args:
            fault_type: Specific fault type (None for random)

        Returns:
            Whether to inject fault
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
            # Decide based on global probability
            return random.random() < self.global_probability

    def inject_latency(
        self,
        min_ms: Optional[int] = None,
        max_ms: Optional[int] = None,
    ) -> int:
        """
        Inject latency

        Args:
            min_ms: Minimum latency (ms)
            max_ms: Maximum latency (ms)

        Returns:
            Injected latency time (ms)
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
        Inject latency based on probability

        Returns:
            Injected latency time (ms) or None
        """
        if self.should_inject(FaultType.LATENCY):
            return self.inject_latency(min_ms, max_ms)
        return None

    def get_random_fault(self) -> Optional[FaultType]:
        """
        Select random fault type

        Returns:
            Selected fault type or None
        """
        if not self.enabled:
            return None

        # Probability-based selection from active faults
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
        Wrap request function to inject faults

        Args:
            request_func: Original request function
            *args, **kwargs: Request function arguments

        Returns:
            Request result or fault response
        """
        fault = self.get_random_fault()

        if fault == FaultType.LATENCY:
            self.inject_latency()
            return request_func(*args, **kwargs)

        elif fault == FaultType.TIMEOUT:
            # Timeout simulation (long delay)
            time.sleep(30)  # 30 second delay
            return request_func(*args, **kwargs)

        # Other fault types need post-request handling
        return request_func(*args, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        return self.stats.copy()

    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "total_checks": 0,
            "faults_injected": 0,
            "by_type": {ft.value: 0 for ft in FaultType},
        }


# Global instance
_default_injector: Optional[FaultInjector] = None


def get_fault_injector() -> FaultInjector:
    """Return default FaultInjector instance"""
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
