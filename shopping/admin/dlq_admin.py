"""
Admin configuration for Failed Operation (DLQ) domain.

FailedOperation 모델의 Django Admin 설정.
selfhealing 패키지의 BaseDLQEntryAdmin을 상속하여 사용합니다.
"""

from django.contrib import admin
from django.urls import reverse

from selfhealing.adapters.django.admin import BaseDLQEntryAdmin
from shopping.models.failed_operation import FailedOperation


@admin.register(FailedOperation)
class FailedOperationAdmin(BaseDLQEntryAdmin):
    """
    Failed Operation (DLQ) Admin 설정.

    selfhealing 패키지의 BaseDLQEntryAdmin을 상속하여
    모든 기본 설정을 재사용합니다.

    호스트 앱에서 추가 커스터마이징이 필요한 경우
    이 클래스에서 오버라이드할 수 있습니다.
    """

    def get_user_admin_url(self, user):
        """shopping 앱의 User 모델 Admin URL을 반환."""
        return reverse("admin:shopping_user_change", args=[user.id])
