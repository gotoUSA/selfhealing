"""
Django App Configuration for Self-Healing.

This allows the selfhealing.adapters.django module to be used
as a Django app in INSTALLED_APPS.

RBAC Groups:
    The selfhealing package creates the following groups via post_migrate signal:
    - selfhealing_viewer: Read-only access (dashboard, status, audit logs)
    - selfhealing_operator: Operational tasks (DLQ replay, archive)
    - selfhealing_admin: Full access (CB control, system enable/disable, config)

    This approach:
    - Does NOT pollute host app's migrations
    - Runs only after migrations complete (DB ready guaranteed)
    - Is idempotent (safe to run multiple times)
    - Is the industry standard (used by django-allauth, django-guardian)
"""

import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

# RBAC group definitions
SELFHEALING_GROUPS = [
    "selfhealing_viewer",
    "selfhealing_operator", 
    "selfhealing_admin",
]


def create_selfhealing_groups(sender, **kwargs):
    """
    Create RBAC groups for Self-Healing system.
    
    Called via post_migrate signal - runs only after migrations complete.
    Uses get_or_create for idempotency.
    
    Also logs environment variable snapshot for audit trail.
    """
    from django.contrib.auth.models import Group
    
    created_groups = []
    existing_groups = []
    
    for group_name in SELFHEALING_GROUPS:
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            created_groups.append(group_name)
        else:
            existing_groups.append(group_name)
    
    if created_groups:
        logger.info(
            f"[SelfHealing] RBAC groups created: {created_groups}"
        )
    
    if existing_groups and created_groups:
        logger.debug(
            f"[SelfHealing] RBAC groups already existed: {existing_groups}"
        )
    
    # Log environment variable snapshot (Phase 2: 환경변수 Audit)
    try:
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit
        log_env_snapshot_to_audit()
    except ImportError:
        logger.debug("[SelfHealing] env_snapshot module not available")
    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(f"[SelfHealing] Failed to log env snapshot: {e}")


class SelfHealingConfig(AppConfig):
    """Django app configuration for self-healing."""

    name = "selfhealing.adapters.django"
    label = "selfhealing"
    verbose_name = "Self-Healing System"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """
        Called when the app is ready.
        
        Connects post_migrate signal for RBAC group creation.
        This ensures groups are created after migrations complete,
        not on every server start.
        """
        # Import admin to register admin classes
        try:
            from selfhealing.adapters.django import admin  # noqa: F401
        except ImportError:
            pass
        
        # Connect post_migrate signal for RBAC group creation
        # sender=self ensures it only runs when this app's migrations complete
        post_migrate.connect(
            create_selfhealing_groups,
            sender=self,
            dispatch_uid="selfhealing_create_rbac_groups",
        )
