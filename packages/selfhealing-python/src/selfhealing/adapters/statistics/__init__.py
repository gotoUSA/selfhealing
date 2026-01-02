"""
Statistics Adapters Package.

This package provides adapters for the StatisticsRepositoryInterface.
"""

from selfhealing.adapters.statistics.null import NullStatisticsRepository

__all__ = [
    "NullStatisticsRepository",
]

# Lazy imports for optional adapters
def get_django_adapter():
    """Get Django statistics adapter (requires Django)."""
    from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
    return DjangoStatisticsAdapter


def get_sqlalchemy_adapter():
    """Get SQLAlchemy statistics adapter (requires SQLAlchemy)."""
    from selfhealing.adapters.sqlalchemy.statistics import SQLAlchemyStatisticsAdapter
    return SQLAlchemyStatisticsAdapter
