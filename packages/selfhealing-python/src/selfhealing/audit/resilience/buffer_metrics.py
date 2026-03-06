"""Prometheus metrics for audit buffer stats."""

from __future__ import annotations

from typing import Any


def _get_buffer_metrics():
    """Lazy-init Prometheus metrics for buffer stats."""
    try:
        from prometheus_client import Gauge

        if not hasattr(_get_buffer_metrics, "_entries"):
            _get_buffer_metrics._entries = Gauge(
                "selfhealing_audit_buffer_entries",
                "Current audit buffer entry count",
                ["buffer"],
            )
            _get_buffer_metrics._dropped = Gauge(
                "selfhealing_audit_buffer_dropped_total",
                "Total dropped audit buffer entries (cumulative)",
                ["buffer"],
            )
            _get_buffer_metrics._usage = Gauge(
                "selfhealing_audit_buffer_usage_percent",
                "Audit buffer usage percentage",
                ["buffer"],
            )
        return (
            _get_buffer_metrics._entries,
            _get_buffer_metrics._dropped,
            _get_buffer_metrics._usage,
        )
    except ImportError:
        return None, None, None


def emit_buffer_stats(buffer_name: str, stats: dict[str, Any]) -> None:
    """Emit buffer stats to Prometheus gauges."""
    entries_gauge, dropped_gauge, usage_gauge = _get_buffer_metrics()
    if entries_gauge is None:
        return

    entries_gauge.labels(buffer=buffer_name).set(stats["count"])
    dropped_gauge.labels(buffer=buffer_name).set(stats["total_dropped"])
    if stats.get("usage_percent") is not None:
        usage_gauge.labels(buffer=buffer_name).set(stats["usage_percent"])
