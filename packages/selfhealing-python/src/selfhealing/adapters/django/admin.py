"""
Django Admin for Self-Healing Models.

Self-Healing 모델의 Django Admin 기본 클래스를 제공합니다.
호스트 앱에서 상속하여 사용하면 Admin 설정을 재사용할 수 있습니다.

Example:
    # myapp/admin.py
    from django.contrib import admin
    from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin
    from myapp.models import PostmortemRecord

    @admin.register(PostmortemRecord)
    class PostmortemRecordAdmin(BasePostmortemRecordAdmin):
        pass  # 모든 설정 상속
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

try:
    from django.contrib import admin
    from django.utils.html import format_html

    DJANGO_AVAILABLE = True
except ImportError:
    DJANGO_AVAILABLE = False


if TYPE_CHECKING:
    from django.http import HttpRequest


logger = logging.getLogger(__name__)


# =============================================================================
# Base Postmortem Record Admin
# =============================================================================


class BasePostmortemRecordAdmin(admin.ModelAdmin if DJANGO_AVAILABLE else object):
    """
    Postmortem Record 기본 Admin 설정.

    장애 사후 분석 기록의 조회 및 관리를 위한 Admin 기본 클래스입니다.
    호스트 앱에서 상속하여 사용하면 별도 설정 없이 Admin을 사용할 수 있습니다.

    Features:
        - 인시던트 목록: 시작 시간, 지속 시간, 영향 서비스 등
        - 필터링: 날짜, 출처(auto/manual)
        - 검색: 인시던트 ID, 서비스명
        - 읽기 전용: 시스템에서만 생성, 수정 불가

    Example:
        from django.contrib import admin
        from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin
        from myapp.models import PostmortemRecord

        @admin.register(PostmortemRecord)
        class PostmortemRecordAdmin(BasePostmortemRecordAdmin):
            pass
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use BasePostmortemRecordAdmin. " "Install it with: pip install django")

    # =========================================================================
    # List View Configuration
    # =========================================================================

    list_display = [
        "incident_id",
        "started_at",
        "duration_display",
        "affected_services_display",
        "source_display",
        "created_at",
    ]

    list_filter = [
        "source",
        "started_at",
        "created_at",
    ]

    search_fields = [
        "incident_id",
        "affected_services",
    ]

    ordering = ["-started_at"]

    date_hierarchy = "started_at"

    # =========================================================================
    # Detail View Configuration
    # =========================================================================

    readonly_fields = [
        "id",
        "incident_id",
        "started_at",
        "resolved_at",
        "duration_seconds",
        "affected_services",
        "timeline",
        "auto_actions",
        "recommendations",
        "system_snapshot",
        "created_at",
        "source",
    ]

    fieldsets = (
        (
            "Incident Overview",
            {
                "fields": (
                    "id",
                    "incident_id",
                    "source",
                ),
            },
        ),
        (
            "Timeline",
            {
                "fields": (
                    "started_at",
                    "resolved_at",
                    "duration_seconds",
                ),
            },
        ),
        (
            "Impact Analysis",
            {
                "fields": ("affected_services",),
            },
        ),
        (
            "Detailed Data",
            {
                "fields": (
                    "timeline",
                    "auto_actions",
                    "recommendations",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "System Snapshot",
            {
                "fields": ("system_snapshot",),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at",),
                "classes": ("collapse",),
            },
        ),
    )

    # =========================================================================
    # Permission Methods
    # =========================================================================

    def has_add_permission(self, request: "HttpRequest") -> bool:
        """
        Postmortem 레코드 수동 추가 비허용.

        Postmortem 레코드는 시스템에서 자동으로 생성되므로
        Admin에서 수동 추가를 허용하지 않습니다.
        """
        return False

    def has_change_permission(
        self,
        request: "HttpRequest",
        obj: Any = None,
    ) -> bool:
        """
        Postmortem 레코드 수정 비허용.

        장애 기록의 무결성을 위해 수정을 허용하지 않습니다.
        """
        return False

    def has_delete_permission(
        self,
        request: "HttpRequest",
        obj: Any = None,
    ) -> bool:
        """
        관리자만 삭제 가능.

        일반 staff 사용자는 삭제할 수 없으며,
        superuser만 삭제 권한이 있습니다.
        """
        return request.user.is_superuser

    # =========================================================================
    # Display Methods
    # =========================================================================

    def duration_display(self, obj: Any) -> str:
        """
        지속 시간을 읽기 쉬운 형식으로 표시.

        Args:
            obj: PostmortemRecord 인스턴스

        Returns:
            포맷된 지속 시간 문자열
            - 60초 미만: "45초"
            - 60초~3600초: "5.0분"
            - 3600초 이상: "2.00시간"
            - None: "-"
        """
        if obj.duration_seconds is None:
            return "-"

        seconds = obj.duration_seconds
        if seconds < 60:
            return f"{seconds:.0f}초"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}분"
        else:
            hours = seconds / 3600
            return f"{hours:.2f}시간"

    duration_display.short_description = "Duration"
    duration_display.admin_order_field = "duration_seconds"

    def affected_services_display(self, obj: Any) -> str:
        """
        영향받은 서비스 목록을 간략하게 표시.

        3개 이하면 전체 표시, 4개 이상이면 앞 3개만 표시하고
        나머지는 "(+N more)" 형식으로 축약합니다.

        Args:
            obj: PostmortemRecord 인스턴스

        Returns:
            포맷된 서비스 목록 문자열
        """
        services = obj.affected_services or []
        if not services:
            return "-"

        if len(services) <= 3:
            return ", ".join(services)
        else:
            return format_html(
                "{} <span style='color: #888;'>(+{} more)</span>",
                ", ".join(services[:3]),
                len(services) - 3,
            )

    affected_services_display.short_description = "Affected Services"

    def source_display(self, obj: Any) -> str:
        """
        출처를 아이콘과 색상으로 구분하여 표시.

        Args:
            obj: PostmortemRecord 인스턴스

        Returns:
            HTML 포맷된 출처 문자열
            - auto: 🤖 Auto (파란색)
            - manual: 👤 Manual (녹색)
        """
        if obj.source == "auto":
            return format_html('<span style="color: blue; font-weight: bold;">🤖 Auto</span>')
        else:
            return format_html('<span style="color: green; font-weight: bold;">👤 Manual</span>')

    source_display.short_description = "Source"
    source_display.admin_order_field = "source"
