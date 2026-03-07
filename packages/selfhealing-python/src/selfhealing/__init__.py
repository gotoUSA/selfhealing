"""
Self-Healing Reliability Layer for Python Applications

A framework-agnostic library providing circuit breaker, dead letter queue,
retry mechanisms, and automatic recovery for distributed systems.
"""

__version__ = "0.1.0"
__author__ = "SelfHealing Contributors"

from selfhealing.interfaces.repositories import (
    CircuitBreakerStateEnum as CircuitState,
)

# structlog을 stdlib logging wrapper로 초기화.
# 기존 OTEL LoggingInstrumentor, IncidentLogHandler 등은 변경 없이 작동한다.
try:
    from selfhealing.observability.structlog_config import configure_structlog

    configure_structlog()
except Exception:  # noqa: BLE001 — 초기화 실패가 패키지 임포트를 막지 않도록
    pass

__all__ = [
    "__version__",
    "CircuitState",
]
