"""
Create Self-Healing RBAC Groups.

Creates the following groups for role-based access control:
- selfhealing_viewer: Read-only access to status and dashboard
- selfhealing_operator: DLQ replay, archive operations
- selfhealing_admin: Full access including CB control and config changes

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from django.db import migrations


def create_groups(apps, schema_editor):
    """Create self-healing permission groups."""
    Group = apps.get_model("auth", "Group")

    # Create groups (get_or_create to be idempotent)
    Group.objects.get_or_create(name="selfhealing_viewer")
    Group.objects.get_or_create(name="selfhealing_operator")
    Group.objects.get_or_create(name="selfhealing_admin")


def remove_groups(apps, schema_editor):
    """Remove self-healing permission groups."""
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__startswith="selfhealing_").delete()


class Migration(migrations.Migration):
    """Migration to create RBAC groups for Self-Healing system."""

    dependencies = [
        ("shopping", "0028_rename_failedpayment_to_failedexternalrequest"),
        ("auth", "__latest__"),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
