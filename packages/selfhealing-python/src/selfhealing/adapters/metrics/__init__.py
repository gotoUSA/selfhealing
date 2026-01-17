"""
Metric Source Adapters for Self-Healing System.

This module provides pluggable adapters for collecting metrics from various
data sources without direct dependency on user's database schema.
"""

from selfhealing.adapters.metrics.base import MetricSourceAdapter
from selfhealing.adapters.metrics.factory import get_metric_adapter

__all__ = [
    "MetricSourceAdapter",
    "get_metric_adapter",
]
