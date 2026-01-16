"""
Metrics Provider Wrapper for AutoTuning.

Wraps a metrics adapter to provide a simplified interface
for the AutoTuning service.

This was extracted from services/auto_tuning/service.py for
better testability and reusability.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MetricsAdapterProtocol(Protocol):
    """Protocol for metrics adapters."""
    
    def fetch_current_metrics(self) -> dict[str, float]:
        """Fetch current metrics from the adapter."""
        ...


class MetricsProviderWrapper:
    """
    메트릭 어댑터를 MetricsProvider 인터페이스로 래핑.
    
    AutoTuning 서비스에서 사용하는 간단한 어댑터.
    
    Usage:
        >>> adapter = PrometheusMetricsAdapter()
        >>> provider = MetricsProviderWrapper(adapter)
        >>> error_rate = provider.get_error_rate()
    """
    
    def __init__(self, adapter: Any):
        """
        Initialize MetricsProviderWrapper.
        
        Args:
            adapter: Metrics adapter with fetch_current_metrics method
        """
        self._adapter = adapter
    
    def get_error_rate(self) -> float:
        """
        현재 에러율을 반환합니다.
        
        Returns:
            에러율 (0.0 ~ 1.0)
        """
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("error_rate", 0.0)
    
    def get_latency_p99(self) -> float:
        """
        P99 레이턴시를 반환합니다.
        
        Returns:
            P99 레이턴시 (밀리초)
        """
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("p99_latency_ms", 0.0)
    
    def get_throughput(self) -> float:
        """
        현재 처리량을 반환합니다.
        
        Returns:
            처리량 (RPS)
        """
        metrics = self._adapter.fetch_current_metrics()
        return metrics.get("throughput_rps", 0.0)
    
    @property
    def raw_adapter(self) -> Any:
        """원본 어댑터에 접근합니다."""
        return self._adapter


__all__ = [
    "MetricsProviderWrapper",
    "MetricsAdapterProtocol",
]
