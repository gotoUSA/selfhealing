"""
Metric Source Adapters for Self-Healing System.

This module provides pluggable adapters for collecting metrics from various
data sources without direct dependency on user's database schema.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from selfhealing.adapters.metrics.base import MetricSourceAdapter
from selfhealing.adapters.metrics.factory import get_metric_adapter

__all__ = [
    "MetricSourceAdapter",
    "get_metric_adapter",
]
