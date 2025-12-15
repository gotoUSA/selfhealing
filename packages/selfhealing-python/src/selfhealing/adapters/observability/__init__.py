"""
Observability Adapters for Self-Healing System

This module provides optional observability adapters that extend
the self-healing system's telemetry capabilities beyond Prometheus.

Available Adapters:
    - OpenTelemetry: Exports self-healing decision events to APM platforms

IMPORTANT:
    - These adapters are OPTIONAL extensions
    - The core system works identically without them
    - Prometheus/Grafana integration remains the primary observability layer

Reference: docs/capability-audit/capablitity_정의/06-OBSERVABILITY-FORENSICS.md
"""

from selfhealing.adapters.observability.opentelemetry import (
    OpenTelemetryAdapter,
    OpenTelemetryConfig,
    get_opentelemetry_adapter,
    emit_selfhealing_event,
    start_decision_span,
    end_decision_span,
)

__all__ = [
    "OpenTelemetryAdapter",
    "OpenTelemetryConfig",
    "get_opentelemetry_adapter",
    "emit_selfhealing_event",
    "start_decision_span",
    "end_decision_span",
]
