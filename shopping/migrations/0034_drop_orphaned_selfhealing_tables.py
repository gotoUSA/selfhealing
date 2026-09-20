# selfhealing 라이브러리(선택 의존성)가 없는 환경 정리.
# 0033은 FailedOperation·FailedExternalRequest·SecurityIncident를 Django state에서만
# 제거했다(테이블 소유권을 라이브러리로 넘기기 위해). 라이브러리가 없으면 그 테이블이
# 어느 앱에도 속하지 않은 채 DB에 남아 shopping_users 등을 FK로 참조하고, 테스트 DB
# flush(TRUNCATE)가 "referenced in a foreign key constraint"로 실패한다.
# 라이브러리가 설치돼 있으면 그 패키지의 모델이 테이블을 소유하므로 아무것도 하지 않는다.

from django.conf import settings
from django.db import migrations

ORPHANED_TABLES = (
    "failed_operations",
    "security_incidents",
    "shopping_failed_external_request",
)


def drop_orphaned_tables(apps, schema_editor):
    if getattr(settings, "SELFHEALING_AVAILABLE", False):
        return
    cascade = " CASCADE" if schema_editor.connection.vendor == "postgresql" else ""
    for table in ORPHANED_TABLES:
        schema_editor.execute(f'DROP TABLE IF EXISTS "{table}"{cascade}')


class Migration(migrations.Migration):
    dependencies = [
        ("shopping", "0033_transfer_models_to_selfhealing"),
    ]

    operations = [
        migrations.RunPython(drop_orphaned_tables, migrations.RunPython.noop),
    ]
