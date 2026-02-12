# 223 Host App Decoupling: 모델 소유권을 selfhealing 패키지로 이전
# DB 변경 없음 — state만 제거하여 shopping 앱에서 모델을 해제

from django.db import migrations


class Migration(migrations.Migration):
    """
    shopping 앱에서 selfhealing 전용 모델의 Django state를 제거한다.

    - FailedOperation → selfhealing.adapters.django.models.FailedOperation
    - FailedExternalRequest → selfhealing.adapters.django.models.FailedExternalRequest
    - SecurityIncident → selfhealing.adapters.django.models.SecurityIncident
    - PostmortemRecord → selfhealing.adapters.django.models.PostmortemRecord (이미 패키지 0001에 존재)

    DB 테이블은 변경하지 않는다 (SeparateDatabaseAndState).
    selfhealing 0002 migration이 state_operations으로 해당 모델을 등록한다.
    """

    dependencies = [
        ("shopping", "0032_remove_legacy_models"),
        ("selfhealing", "0002_add_dlq_and_security_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="FailedOperation"),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="FailedExternalRequest"),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="SecurityIncident"),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="PostmortemRecord"),
            ],
            database_operations=[],
        ),
    ]
