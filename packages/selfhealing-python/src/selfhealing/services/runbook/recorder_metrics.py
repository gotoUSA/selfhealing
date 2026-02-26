"""
RunbookPlaybackRecorder Prometheus 메트릭.

SelfHealingMetrics(metrics/prometheus.py) 패턴에 따라
prometheus_client Counter를 정의한다.

채널별(cascade, learning, postmortem, event_bus) 기록 성공/실패를 추적하여
기록 유실을 AlertManager/Grafana에서 모니터링할 수 있도록 한다.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

try:
    from prometheus_client import Counter

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment, misc]

if PROMETHEUS_AVAILABLE:
    RECORDER_SUCCESS = Counter(
        "selfhealing_runbook_recorder_success_total",
        "Successful recordings by channel",
        ["channel"],
    )

    RECORDER_ERROR = Counter(
        "selfhealing_runbook_recorder_error_total",
        "Failed recordings by channel and error type",
        ["channel", "error_type"],
    )
else:
    # Graceful degradation — Prometheus 미설치 환경에서도 동작

    class _NoopLabelCounter:
        """prometheus_client 미설치 시 조용히 무시하는 스텁."""

        def labels(self, **kwargs):  # noqa: ARG002
            return self

        def inc(self, amount: float = 1):  # noqa: ARG002
            pass

    RECORDER_SUCCESS = _NoopLabelCounter()  # type: ignore[assignment]
    RECORDER_ERROR = _NoopLabelCounter()  # type: ignore[assignment]
