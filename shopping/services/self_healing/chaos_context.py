"""
Chaos Context - DEPRECATED

.. deprecated:: 0.1.0
    This module has moved to the selfhealing package.

    Before (deprecated):
        from shopping.services.self_healing.chaos_context import ChaosExperimentContext

    After:
        from selfhealing.services.chaos_context import ChaosExperimentContext

This is a compatibility shim. All functionality has been moved to:
    packages/selfhealing-python/src/selfhealing/services/chaos_context.py

Reference: docs/self_healing/11_FORENSIC_ADVISOR.md
"""

import warnings

warnings.warn(
    "Importing from shopping.services.self_healing.chaos_context is deprecated. "
    "Please migrate to 'from selfhealing.services.chaos_context import ...' "
    "See packages/selfhealing-python/docs/MIGRATION.md for details.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new location for backwards compatibility
from selfhealing.services.chaos_context import (
    ChaosExperimentType,
    ChaosExperimentStatus,
    ChaosExperimentContext,
    is_chaos_experiment,
    get_chaos_context,
    attach_chaos_context,
    resolve_chaos_experiment,
    create_chaos_context,
)


def resolve_expired_chaos_experiments() -> int:
    """
    Django-specific implementation to resolve expired chaos experiments.

    This function uses Django ORM to find and resolve expired experiments.
    The core logic is in selfhealing package, this is the Django adapter.
    """
    from django.utils import timezone
    from shopping.models.failed_operation import FailedOperation

    pending_chaos = FailedOperation.objects.filter(
        status__in=["pending", "replayed"],
        metadata__is_chaos_experiment=True,
    )

    resolved_count = 0
    now = timezone.now()

    for operation in pending_chaos:
        context = get_chaos_context(operation)
        if context and context.auto_resolve and context.is_expired():
            context.mark_completed("Auto-resolved: Experiment duration expired")
            operation.metadata["chaos_experiment_context"] = context.to_dict()

            operation.status = "resolved"
            operation.resolution_type = "auto_replay"
            operation.resolution_note = "[CHAOS Experiment] Auto-resolved: Experiment duration expired"
            operation.resolved_at = now

            operation.save(
                update_fields=[
                    "status",
                    "resolution_type",
                    "resolution_note",
                    "resolved_at",
                    "metadata",
                    "updated_at",
                ]
            )
            resolved_count += 1

    return resolved_count


__all__ = [
    "ChaosExperimentType",
    "ChaosExperimentStatus",
    "ChaosExperimentContext",
    "is_chaos_experiment",
    "get_chaos_context",
    "attach_chaos_context",
    "resolve_chaos_experiment",
    "resolve_expired_chaos_experiments",
    "create_chaos_context",
]
