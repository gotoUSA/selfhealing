"""
Django Adapters Package for Self-Healing System.

This package provides Django-specific adapters for statistics and dashboards.
Note: Runtime repositories use Redis (not Django ORM) for performance.

Provides:
- SelfHealingConfig: Django AppConfig for Self-Healing system
- create_selfhealing_groups: RBAC group creation signal handler
- AbstractFailedOperation: Domain-free abstract model for DLQ entries
- AbstractPostmortemRecord: Domain-free abstract model for Postmortem records
- BasePostmortemRecordAdmin: Base Admin class for Postmortem records
- DjangoStatisticsAdapter: Statistics adapter using Django ORM
"""

from selfhealing.adapters.django.apps import (
    SELFHEALING_GROUPS,
    SelfHealingConfig,
    create_selfhealing_groups,
)
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


def get_abstract_postmortem_record():
    """
    Get AbstractPostmortemRecord model class.

    This is a lazy import to avoid Django dependency at module load time.

    Returns:
        AbstractPostmortemRecord class

    Raises:
        ImportError: If Django is not installed
    """
    from selfhealing.adapters.django.models import AbstractPostmortemRecord

    return AbstractPostmortemRecord


def get_base_postmortem_admin():
    """
    Get BasePostmortemRecordAdmin class.

    This is a lazy import to avoid Django dependency at module load time.

    Returns:
        BasePostmortemRecordAdmin class

    Raises:
        ImportError: If Django is not installed
    """
    from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

    return BasePostmortemRecordAdmin


__all__ = [
    # AppConfig
    "SelfHealingConfig",
    "create_selfhealing_groups",
    "SELFHEALING_GROUPS",
    # Adapters
    "DjangoStatisticsAdapter",
    # Lazy imports for models
    "get_abstract_failed_operation",
    "get_abstract_postmortem_record",
    "get_base_postmortem_admin",
]
