"""
load_tests.metrics - 커스텀 메트릭

Locust 이벤트 훅 및 P99.9 등 확장 메트릭
"""

from .custom_metrics import MetricsCollector
from .event_hooks import setup_event_hooks

__all__ = [
    "MetricsCollector",
    "setup_event_hooks",
]
