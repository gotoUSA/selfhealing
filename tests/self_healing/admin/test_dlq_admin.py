"""
DLQ Admin 상속 테스트.

shopping 앱의 FailedOperationAdmin이 BaseDLQEntryAdmin을
올바르게 상속하는지 확인합니다.
"""

import pytest
from unittest.mock import MagicMock


class TestFailedOperationAdminInheritance:
    """Admin 상속 관계 테스트."""

    def test_inherits_from_base_dlq_admin(self):
        """FailedOperationAdmin이 BaseDLQEntryAdmin을 상속하는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert issubclass(FailedOperationAdmin, BaseDLQEntryAdmin)

    def test_list_display_inherited(self):
        """list_display가 상속되어 있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert FailedOperationAdmin.list_display == BaseDLQEntryAdmin.list_display

    def test_list_filter_inherited(self):
        """list_filter가 상속되어 있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert FailedOperationAdmin.list_filter == BaseDLQEntryAdmin.list_filter

    def test_search_fields_inherited(self):
        """search_fields가 상속되어 있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert FailedOperationAdmin.search_fields == BaseDLQEntryAdmin.search_fields

    def test_actions_inherited(self):
        """actions가 상속되어 있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert FailedOperationAdmin.actions == BaseDLQEntryAdmin.actions

    def test_fieldsets_inherited(self):
        """fieldsets가 상속되어 있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        assert FailedOperationAdmin.fieldsets == BaseDLQEntryAdmin.fieldsets


class TestFailedOperationAdminMethods:
    """Admin 메서드가 상속되어 동작하는지 테스트."""

    def test_status_display_inherited(self):
        """status_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin

        admin = FailedOperationAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.status = "pending"
        obj.get_status_display.return_value = "Pending"

        result = admin.status_display(obj)
        assert "orange" in result
        assert "Pending" in result

    def test_entity_display_inherited(self):
        """entity_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin

        admin = FailedOperationAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.entity_type = "order"
        obj.entity_id = "12345"

        result = admin.entity_display(obj)
        assert "Order" in result
        assert "12345" in result


class TestFailedOperationAdminCustomization:
    """호스트 앱에서 오버라이드 가능한지 테스트."""

    def test_get_user_admin_url_customized(self):
        """get_user_admin_url이 shopping 앱에 맞게 커스터마이즈 되어있는지 확인."""
        from shopping.admin.dlq_admin import FailedOperationAdmin

        admin = FailedOperationAdmin(MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 1

        url = admin.get_user_admin_url(user)
        # URL 형식: /admin/shopping/user/{id}/change/
        assert "/admin/shopping/user/" in url
        assert "/change/" in url

    def test_can_override_list_display(self):
        """list_display를 오버라이드할 수 있는지 확인."""
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin

        class CustomAdmin(BaseDLQEntryAdmin):
            list_display = ["id", "domain", "status_display"]

        assert CustomAdmin.list_display == ["id", "domain", "status_display"]
        assert CustomAdmin.list_display != BaseDLQEntryAdmin.list_display
