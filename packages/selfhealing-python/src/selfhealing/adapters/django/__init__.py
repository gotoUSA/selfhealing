"""
Django Adapters Package for Self-Healing System.

This package provides Django-specific adapters for statistics and dashboards.
Note: Runtime repositories use Redis (not Django ORM) for performance.

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter

__all__ = [
    "DjangoStatisticsAdapter",
]
