"""
load_tests.metrics - 커스텀 메트릭

Locust 이벤트 훅 및 P99.9 등 확장 메트릭
"""

from .custom_metrics import MetricsCollector, get_metrics_collector
from .event_hooks import setup_event_hooks

__all__ = [
    "MetricsCollector",
    "get_metrics_collector",
    "setup_event_hooks",
]
