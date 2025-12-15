"""
OpenTelemetry Adapter for Self-Healing System

PURPOSE:
    This adapter exports self-healing decision events and operational signals
    to external APM platforms (Datadog, New Relic, Elastic, CloudWatch)
    via OpenTelemetry protocol.

NON-GOALS (DO NOT IMPLEMENT):
    ❌ No automatic request tracing
    ❌ No HTTP/DB/framework instrumentation
    ❌ No per-request span creation
    ❌ No deep framework hooks
    ❌ No hot-path logging
    ❌ No Prometheus replacement
    ❌ No mandatory OTel dependency

WHAT THIS EXPORTS:
    ✅ Circuit Breaker state transitions
    ✅ Retry attempts and exhaustion
    ✅ DLQ enqueue / replay events
    ✅ Rate limit triggered events
    ✅ SLO threshold events
    ✅ Policy evaluation outcomes
    ✅ Manual vs automatic decision boundaries

DESIGN PRINCIPLES:
    - Optional extension (graceful NO-OP when OTel not installed)
    - Disabled by default, requires explicit enablement
    - Event-based emission, not request-based
    - No PII or sensitive data export
    - Coarse-grained decision spans only

Reference: docs/capability-audit/capablitity_정의/06-OBSERVABILITY-FORENSICS.md
"""

from __future__ import annotations

from .config import OpenTelemetryConfig
from .adapter import (
    OpenTelemetryAdapter,
    get_opentelemetry_adapter,
)
from .events import (
    SelfHealingEventType,
    emit_selfhealing_event,
)
from .spans import (
    start_decision_span,
    end_decision_span,
    DecisionSpanContext,
)

__all__ = [
    # Configuration
    "OpenTelemetryConfig",
    # Adapter
    "OpenTelemetryAdapter",
    "get_opentelemetry_adapter",
    # Events
    "SelfHealingEventType",
    "emit_selfhealing_event",
    # Spans
    "start_decision_span",
    "end_decision_span",
    "DecisionSpanContext",
]
