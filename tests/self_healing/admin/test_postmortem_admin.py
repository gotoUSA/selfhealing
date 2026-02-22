"""
Postmortem Admin 단위 테스트.

shopping 앱의 PostmortemRecordAdmin이 BasePostmortemRecordAdmin을
올바르게 상속하는지 확인합니다.
"""

from unittest.mock import MagicMock


class TestPostmortemRecordAdminInheritance:
    """Admin 상속 관계 테스트."""

    def test_inherits_from_base_postmortem_admin(self):
        """PostmortemRecordAdmin이 BasePostmortemRecordAdmin을 상속하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert issubclass(PostmortemRecordAdmin, BasePostmortemRecordAdmin)

    def test_list_display_inherited(self):
        """list_display가 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.list_display == BasePostmortemRecordAdmin.list_display

    def test_list_filter_inherited(self):
        """list_filter가 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.list_filter == BasePostmortemRecordAdmin.list_filter

    def test_search_fields_inherited(self):
        """search_fields가 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.search_fields == BasePostmortemRecordAdmin.search_fields

    def test_ordering_inherited(self):
        """ordering이 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.ordering == BasePostmortemRecordAdmin.ordering

    def test_readonly_fields_inherited(self):
        """readonly_fields가 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.readonly_fields == BasePostmortemRecordAdmin.readonly_fields

    def test_fieldsets_inherited(self):
        """fieldsets가 상속되어 있는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        assert PostmortemRecordAdmin.fieldsets == BasePostmortemRecordAdmin.fieldsets


class TestPostmortemRecordAdminMethods:
    """Admin 메서드가 상속되어 동작하는지 테스트."""

    def test_duration_display_inherited(self):
        """duration_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.duration_seconds = 300

        result = admin.duration_display(obj)
        assert "5.0분" in result

    def test_affected_services_display_inherited(self):
        """affected_services_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.affected_services = ["payment", "order"]

        result = admin.affected_services_display(obj)
        assert "payment" in result
        assert "order" in result

    def test_source_display_inherited(self):
        """source_display 메서드가 상속되어 동작하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())
        obj = MagicMock()
        obj.source = "auto"

        result = admin.source_display(obj)
        assert "Auto" in result


class TestPostmortemRecordAdminPermissions:
    """Admin 권한 메서드가 상속되어 동작하는지 테스트."""

    def test_has_add_permission_inherited(self):
        """has_add_permission이 상속되어 False 반환하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())
        request = MagicMock()

        assert admin.has_add_permission(request) is False

    def test_has_change_permission_inherited(self):
        """has_change_permission이 상속되어 False 반환하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())
        request = MagicMock()

        assert admin.has_change_permission(request) is False

    def test_has_delete_permission_superuser_inherited(self):
        """has_delete_permission이 상속되어 superuser만 True 반환하는지 확인."""
        from shopping.admin.postmortem_admin import PostmortemRecordAdmin

        admin = PostmortemRecordAdmin(MagicMock(), MagicMock())

        request_superuser = MagicMock()
        request_superuser.user.is_superuser = True
        assert admin.has_delete_permission(request_superuser) is True

        request_regular = MagicMock()
        request_regular.user.is_superuser = False
        assert admin.has_delete_permission(request_regular) is False


class TestPostmortemRecordAdminCustomization:
    """호스트 앱에서 오버라이드 가능한지 테스트."""

    def test_can_override_list_display(self):
        """list_display를 오버라이드할 수 있는지 확인."""
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin

        class CustomAdmin(BasePostmortemRecordAdmin):
            list_display = ["incident_id", "started_at"]

        assert CustomAdmin.list_display == ["incident_id", "started_at"]
        assert CustomAdmin.list_display != BasePostmortemRecordAdmin.list_display

    def test_can_override_permission_methods(self):
        """권한 메서드를 오버라이드할 수 있는지 확인."""
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin
        from django.contrib.admin.sites import AdminSite
        from shopping.models import PostmortemRecord

        class CustomAdmin(BasePostmortemRecordAdmin):
            def has_delete_permission(self, request, obj=None):
                return True  # 모두 삭제 가능하도록 변경

        site = AdminSite()
        admin = CustomAdmin(model=PostmortemRecord, admin_site=site)
        request = MagicMock()
        request.user.is_superuser = False

        # 기본은 False지만 오버라이드로 True
        assert admin.has_delete_permission(request) is True
