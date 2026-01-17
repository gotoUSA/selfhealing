"""
Django Adapters Package for Self-Healing System.

This package provides Django-specific adapters for statistics and dashboards.
Note: Runtime repositories use Redis (not Django ORM) for performance.

Provides:
- SelfHealingConfig: Django AppConfig for Self-Healing system
- create_selfhealing_groups: RBAC group creation signal handler
- AbstractFailedOperation: Domain-free abstract model for DLQ entries
- DjangoStatisticsAdapter: Statistics adapter using Django ORM
"""

from selfhealing.adapters.django.statistics import DjangoStatisticsAdapter
from selfhealing.adapters.django.apps import (
    SelfHealingConfig,
    create_selfhealing_groups,
    SELFHEALING_GROUPS,
)


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
    # AppConfig
    "SelfHealingConfig",
    "create_selfhealing_groups",
    "SELFHEALING_GROUPS",
    # Adapters
    "DjangoStatisticsAdapter",
    "get_abstract_failed_operation",
]
