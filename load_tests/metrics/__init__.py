"""
load_tests.metrics - 커스텀 메트릭

Locust 이벤트 훅 및 P99.9 등 확장 메트릭
GAP-08: Observability Contract 포함
"""

from .custom_metrics import MetricsCollector, get_metrics_collector
from .event_hooks import setup_event_hooks
from .observability_contract import (
    PipelineStage,
    TraceContext,
    ObservabilityMetrics,
    ObservabilityReportGenerator,
    trace_request,
    get_observability_metrics,
    setup_observability_hooks,
)

__all__ = [
    # Custom Metrics
    "MetricsCollector",
    "get_metrics_collector",
    "setup_event_hooks",
    # Observability Contract (GAP-08)
    "PipelineStage",
    "TraceContext",
    "ObservabilityMetrics",
    "ObservabilityReportGenerator",
    "trace_request",
    "get_observability_metrics",
    "setup_observability_hooks",
]
