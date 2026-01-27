"""
Django migration to create 'selfhealing_chaos_tester' group.

This group grants permission to access X-Test/Chaos API endpoints.
Users in this group can perform chaos testing and X-Test-Mode operations.

Related: 138_XTEST_PERMISSION_DUAL_LOCK.md
"""

from django.contrib.auth.models import Group
from django.db import migrations


def create_chaos_tester_group(apps, schema_editor):
    """selfhealing_chaos_tester 그룹 생성."""
    Group.objects.get_or_create(name="selfhealing_chaos_tester")


def remove_chaos_tester_group(apps, schema_editor):
    """selfhealing_chaos_tester 그룹 삭제 (롤백용)."""
    Group.objects.filter(name="selfhealing_chaos_tester").delete()


class Migration(migrations.Migration):
    """selfhealing_chaos_tester 그룹 생성 마이그레이션."""

    dependencies = [
        ("shopping", "0030_auditlog"),
    ]

    operations = [
        migrations.RunPython(
            create_chaos_tester_group,
            reverse_code=remove_chaos_tester_group,
        ),
    ]
