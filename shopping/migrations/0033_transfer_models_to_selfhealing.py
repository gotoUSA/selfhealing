# 223 Host App Decoupling: 모델 소유권을 selfhealing 패키지로 이전
# DB 변경 없음 — state만 제거하여 shopping 앱에서 모델을 해제

from django.conf import settings
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

    # selfhealing 라이브러리는 선택 의존성이다. 설치돼 있을 때만 그 패키지의
    # migration을 선행 조건으로 건다 (없으면 state 제거만 수행, DB 변경 없음).
    dependencies = [
        ("shopping", "0032_remove_legacy_models"),
        *(
            [("selfhealing", "0002_add_dlq_and_security_models")]
            if getattr(settings, "SELFHEALING_AVAILABLE", False)
            else []
        ),
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
    ]
