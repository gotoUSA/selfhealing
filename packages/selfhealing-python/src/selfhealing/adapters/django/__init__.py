"""
Django Adapters Package for Self-Healing System.

This package provides Django-specific adapters for statistics and dashboards.
Note: Runtime repositories use Redis (not Django ORM) for performance.

Provides:
- AbstractFailedOperation: Domain-free abstract model for DLQ entries
- DjangoStatisticsAdapter: Statistics adapter using Django ORM

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter

# Lazy import for AbstractFailedOperation (requires Django)
def get_abstract_failed_operation():
    """
    Get AbstractFailedOperation model class.
    
    This is a lazy import to avoid Django dependency at module load time.
    
    Returns:
        AbstractFailedOperation class
        
    Raises:
        ImportError: If Django is not installed
    """
    from selfhealing.adapters.django.models import AbstractFailedOperation
    return AbstractFailedOperation


__all__ = [
    "DjangoStatisticsAdapter",
    "get_abstract_failed_operation",
]
