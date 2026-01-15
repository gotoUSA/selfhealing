"""
Statistics Adapters Package.

This package provides adapters for the StatisticsRepositoryInterface.
"""

from selfhealing.adapters.statistics.null import NullStatisticsRepository

__all__ = [
    "NullStatisticsRepository",
]


def get_django_adapter():
    """Get Django statistics adapter (requires Django)."""
    from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
    return DjangoStatisticsAdapter
