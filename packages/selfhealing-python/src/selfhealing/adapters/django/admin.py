"""
Django Admin for Self-Healing Models.

Self-Healing 모델의 Django Admin 기본 클래스를 제공합니다.
호스트 앱에서 상속하여 사용하면 Admin 설정을 재사용할 수 있습니다.

Available Base Admin Classes:
    - BasePostmortemRecordAdmin: 장애 사후 분석 기록 Admin
    - BaseDLQEntryAdmin: Dead Letter Queue (실패 작업) Admin
    - BaseCircuitBreakerStateAdmin: Circuit Breaker 상태 Admin

Example:
    # myapp/admin.py
    from django.contrib import admin
    from selfhealing.adapters.django.admin import (
        BasePostmortemRecordAdmin,
        BaseDLQEntryAdmin,
        BaseCircuitBreakerStateAdmin,
    )
    from myapp.models import PostmortemRecord, FailedOperation, CircuitBreakerState

    @admin.register(PostmortemRecord)
    class PostmortemRecordAdmin(BasePostmortemRecordAdmin):
        pass  # 모든 설정 상속

    @admin.register(FailedOperation)
    class FailedOperationAdmin(BaseDLQEntryAdmin):
        pass

    @admin.register(CircuitBreakerState)
    class CircuitBreakerStateAdmin(BaseCircuitBreakerStateAdmin):
        pass
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

try:
    from django.contrib import admin
    from django.urls import reverse
    from django.utils.html import format_html

    DJANGO_AVAILABLE = True
except ImportError:
    DJANGO_AVAILABLE = False


if TYPE_CHECKING:
    from django.http import HttpRequest


logger = structlog.get_logger()


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

    def has_add_permission(self, request: HttpRequest) -> bool:
        """
        Postmortem 레코드 수동 추가 비허용.

        Postmortem 레코드는 시스템에서 자동으로 생성되므로
        Admin에서 수동 추가를 허용하지 않습니다.
        """
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: Any = None,
    ) -> bool:
        """
        Postmortem 레코드 수정 비허용.

        장애 기록의 무결성을 위해 수정을 허용하지 않습니다.
        """
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
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


# =============================================================================
# Base DLQ Entry Admin (Dead Letter Queue / Failed Operation)
# =============================================================================


class BaseDLQEntryAdmin(admin.ModelAdmin if DJANGO_AVAILABLE else object):
    """
    Dead Letter Queue 기본 Admin 설정.

    실패한 작업(DLQ Entry)의 조회, 재시도, 해결을 위한 Admin 기본 클래스입니다.
    호스트 앱에서 상속하여 사용하면 별도 설정 없이 Admin을 사용할 수 있습니다.

    Features:
        - 실패 작업 목록: 도메인, 실패 유형, 상태, 재시도 횟수 등
        - 필터링: 도메인, 상태, 실패 유형, 생성일
        - 검색: 에러 메시지, 엔티티 정보
        - Admin Actions: 재시도, 해결됨/거부됨/검토필요 마킹

    Example:
        from django.contrib import admin
        from selfhealing.adapters.django.admin import BaseDLQEntryAdmin
        from myapp.models import FailedOperation

        @admin.register(FailedOperation)
        class FailedOperationAdmin(BaseDLQEntryAdmin):
            pass
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use BaseDLQEntryAdmin. " "Install it with: pip install django")

    # =========================================================================
    # List View Configuration
    # =========================================================================

    list_display = [
        "id",
        "domain",
        "failure_type",
        "status_display",
        "entity_display",
        "user_link",
        "retry_count",
        "created_at",
        "resolved_at",
    ]

    list_filter = [
        "domain",
        "status",
        "failure_type",
        "created_at",
        "resolved_at",
    ]

    search_fields = [
        "failure_type",
        "error_code",
        "error_message",
        "entity_type",
        "entity_id",
        "user__username",
        "user__email",
    ]

    ordering = ["-created_at"]

    # =========================================================================
    # Detail View Configuration
    # =========================================================================

    readonly_fields = [
        "domain",
        "failure_type",
        "entity_type",
        "entity_id",
        "user",
        "snapshot_data",
        "error_code",
        "error_message",
        "retry_count",
        "max_retries",
        "last_retry_at",
        "request_data",
        "response_data",
        "metadata",
        "next_action_hint",
        "recommended_action",
        "created_at",
        "updated_at",
        "expires_at",
    ]

    actions = [
        "replay_selected",
        "mark_as_resolved",
        "mark_as_rejected",
        "mark_as_requires_review",
    ]

    fieldsets = (
        (
            "Classification",
            {
                "fields": ("domain", "failure_type", "status"),
            },
        ),
        (
            "References",
            {
                "fields": ("entity_type", "entity_id", "user"),
            },
        ),
        (
            "Error Details",
            {
                "fields": (
                    "error_code",
                    "error_message",
                    "next_action_hint",
                    "recommended_action",
                ),
            },
        ),
        (
            "Retry Information",
            {
                "fields": ("retry_count", "max_retries", "last_retry_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Resolution",
            {
                "fields": (
                    "resolved_at",
                    "resolved_by",
                    "resolution_type",
                    "resolution_note",
                ),
            },
        ),
        (
            "Forensic Data",
            {
                "fields": (
                    "snapshot_data",
                    "request_data",
                    "response_data",
                    "metadata",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at", "expires_at"),
                "classes": ("collapse",),
            },
        ),
    )

    # =========================================================================
    # Display Methods
    # =========================================================================

    def status_display(self, obj: Any) -> str:
        """
        상태를 색상 인디케이터와 함께 표시.

        Args:
            obj: FailedOperation 인스턴스

        Returns:
            HTML 포맷된 상태 문자열
        """
        colors = {
            "pending": "orange",
            "reviewing": "blue",
            "replayed": "purple",
            "requires_review": "red",
            "resolved": "green",
            "rejected": "gray",
            "archived": "lightgray",
            "expired": "lightgray",
        }
        color = colors.get(obj.status, "black")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_display.short_description = "Status"
    status_display.admin_order_field = "status"

    def entity_display(self, obj: Any) -> str:
        """
        엔티티 타입과 ID를 표시.

        Args:
            obj: FailedOperation 인스턴스

        Returns:
            포맷된 엔티티 정보 또는 "-"
        """
        if obj.entity_type and obj.entity_id:
            return format_html(
                '<span style="font-weight: bold;">{}</span> #{}',
                obj.entity_type.title(),
                obj.entity_id,
            )
        return "-"

    entity_display.short_description = "Entity"

    def user_link(self, obj: Any) -> str:
        """
        사용자 링크를 표시.

        Args:
            obj: FailedOperation 인스턴스

        Returns:
            사용자 Admin 링크 또는 "-"

        Note:
            호스트 앱에서 User 모델의 admin URL을 오버라이드할 수 있습니다.
        """
        if obj.user:
            try:
                # 기본적으로 shopping 앱의 User 모델 사용
                # 호스트 앱에서 오버라이드 가능
                url = self.get_user_admin_url(obj.user)
                return format_html('<a href="{}">{}</a>', url, obj.user.username)
            except Exception:
                return obj.user.username
        return "-"

    user_link.short_description = "User"

    def get_user_admin_url(self, user: Any) -> str:
        """
        사용자 Admin URL을 반환.

        호스트 앱에서 오버라이드하여 다른 User 모델을 사용할 수 있습니다.

        Args:
            user: User 인스턴스

        Returns:
            Admin 변경 페이지 URL
        """
        return reverse("admin:shopping_user_change", args=[user.id])

    # =========================================================================
    # Admin Actions
    # =========================================================================

    @admin.action(description="Replay selected DLQ entries")
    def replay_selected(self, request: HttpRequest, queryset) -> None:
        """
        선택된 DLQ 항목을 재시도.

        pending 또는 requires_review 상태인 항목만 재시도합니다.
        최대 재시도 횟수를 초과한 항목은 건너뜁니다.
        """
        from selfhealing.services import get_replay_service

        service = get_replay_service()
        success_count = 0
        fail_count = 0

        for entry in queryset.filter(status__in=["pending", "requires_review"]):
            if entry.retry_count >= entry.max_retries:
                fail_count += 1
                continue

            result = service.replay_single(entry.id)
            if result.success:
                success_count += 1
            else:
                fail_count += 1

        self.message_user(
            request,
            f"Replay complete: {success_count} succeeded, {fail_count} failed.",
        )

    @admin.action(description="Mark as RESOLVED")
    def mark_as_resolved(self, request: HttpRequest, queryset) -> None:
        """선택된 항목을 해결됨으로 마킹."""
        count = 0
        for entry in queryset:
            entry.mark_as_resolved(
                resolved_by=request.user,
                note=f"Manually resolved by {request.user.username}",
                resolution_type="manual_fix",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as resolved.")

    @admin.action(description="Mark as REJECTED (unrecoverable)")
    def mark_as_rejected(self, request: HttpRequest, queryset) -> None:
        """선택된 항목을 거부됨(복구 불가)으로 마킹."""
        count = 0
        for entry in queryset:
            entry.mark_as_rejected(
                resolved_by=request.user,
                note=f"Rejected by {request.user.username}",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as rejected.")

    @admin.action(description="Mark as REQUIRES_REVIEW")
    def mark_as_requires_review(self, request: HttpRequest, queryset) -> None:
        """선택된 항목을 검토 필요로 마킹."""
        count = 0
        for entry in queryset:
            entry.mark_as_requires_review(
                note=f"Escalated by {request.user.username}",
            )
            count += 1

        self.message_user(request, f"Marked {count} entries as requiring review.")


# =============================================================================
# Base Circuit Breaker State Admin
# =============================================================================


class BaseCircuitBreakerStateAdmin(admin.ModelAdmin if DJANGO_AVAILABLE else object):
    """
    Circuit Breaker 상태 기본 Admin 설정.

    외부 서비스 Circuit Breaker 상태 관리를 위한 Admin 기본 클래스입니다.
    강제 열기/닫기, 리셋 등의 운영 제어 기능을 제공합니다.

    Features:
        - 서킷 상태 목록: 서비스명, 상태, 실패/성공 횟수 등
        - 필터링: 상태, 수동 제어 여부, 생성일
        - 검색: 서비스명, 제어 사유
        - Admin Actions: 강제 열기/닫기, DLQ 재시도와 함께 닫기, 리셋

    Example:
        from django.contrib import admin
        from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin
        from myapp.models import CircuitBreakerState

        @admin.register(CircuitBreakerState)
        class CircuitBreakerStateAdmin(BaseCircuitBreakerStateAdmin):
            pass
    """

    if not DJANGO_AVAILABLE:
        raise ImportError("Django is required to use BaseCircuitBreakerStateAdmin. " "Install it with: pip install django")

    # =========================================================================
    # List View Configuration
    # =========================================================================

    list_display = [
        "service_name",
        "state_display",
        "failure_count",
        "success_count",
        "manually_controlled_display",
        "controlled_by_id",
        "opened_at",
        "updated_at",
    ]

    list_filter = [
        "state",
        "manually_controlled",
        "created_at",
    ]

    search_fields = [
        "service_name",
        "control_reason",
    ]

    ordering = ["-updated_at"]

    # =========================================================================
    # Detail View Configuration
    # =========================================================================

    readonly_fields = [
        "failure_count",
        "success_count",
        "last_failure_at",
        "opened_at",
        "created_at",
        "updated_at",
    ]

    actions = [
        "force_open_selected",
        "force_close_selected",
        "force_close_with_replay",
        "reset_selected",
    ]

    fieldsets = (
        (
            "Service Information",
            {
                "fields": ("service_name", "state"),
            },
        ),
        (
            "Counters",
            {
                "fields": ("failure_count", "success_count", "last_failure_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Manual Control",
            {
                "fields": ("manually_controlled", "controlled_by_id", "control_reason"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("opened_at", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    # =========================================================================
    # Display Methods
    # =========================================================================

    def state_display(self, obj: Any) -> str:
        """
        상태를 색상 인디케이터와 함께 표시.

        Args:
            obj: CircuitBreakerState 인스턴스

        Returns:
            HTML 포맷된 상태 문자열
            - closed: 녹색 (정상)
            - open: 빨간색 (차단)
            - half_open: 주황색 (테스트 중)
        """
        colors = {
            "closed": "green",
            "open": "red",
            "half_open": "orange",
        }
        color = colors.get(obj.state, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_state_display(),
        )

    state_display.short_description = "State"
    state_display.admin_order_field = "state"

    def manually_controlled_display(self, obj: Any) -> str:
        """
        수동 제어 상태를 표시.

        Args:
            obj: CircuitBreakerState 인스턴스

        Returns:
            HTML 포맷된 제어 상태 문자열
        """
        if obj.manually_controlled:
            return format_html('<span style="color: blue;">✓ Manual</span>')
        return format_html('<span style="color: gray;">Auto</span>')

    manually_controlled_display.short_description = "Control"

    # =========================================================================
    # Admin Actions
    # =========================================================================

    @admin.action(description="Force OPEN selected circuits (block requests)")
    def force_open_selected(self, request: HttpRequest, queryset) -> None:
        """
        선택된 서킷 브레이커를 강제로 열기.

        열린 서킷은 해당 서비스로의 모든 요청을 차단합니다.
        """
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_open(
                service_name=circuit.service_name,
                reason=f"Admin action by {request.user.username}",
                controlled_by=request.user,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully opened {count} circuit breaker(s).",
        )

    @admin.action(description="Force CLOSE selected circuits (allow requests)")
    def force_close_selected(self, request: HttpRequest, queryset) -> None:
        """
        선택된 서킷 브레이커를 강제로 닫기 (DLQ 재시도 없음).

        닫힌 서킷은 해당 서비스로의 요청을 허용합니다.
        """
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_close(
                service_name=circuit.service_name,
                reason=f"Admin action by {request.user.username}",
                controlled_by=request.user,
                trigger_replay=False,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully closed {count} circuit breaker(s).",
        )

    @admin.action(description="Force CLOSE with DLQ replay")
    def force_close_with_replay(self, request: HttpRequest, queryset) -> None:
        """
        선택된 서킷 브레이커를 닫고 DLQ 항목을 재시도.

        서킷을 닫은 후 해당 서비스의 DLQ 항목들을 자동으로 재시도합니다.
        """
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.force_close(
                service_name=circuit.service_name,
                reason=f"Admin action with replay by {request.user.username}",
                controlled_by=request.user,
                trigger_replay=True,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully closed {count} circuit breaker(s) with DLQ replay triggered.",
        )

    @admin.action(description="Reset selected circuits to initial state")
    def reset_selected(self, request: HttpRequest, queryset) -> None:
        """
        선택된 서킷 브레이커를 초기 상태로 리셋.

        카운터와 상태를 모두 초기화합니다.
        """
        from selfhealing.services import get_circuit_breaker_service

        service = get_circuit_breaker_service()
        count = 0

        for circuit in queryset:
            result = service.reset(
                service_name=circuit.service_name,
                reason=f"Admin reset by {request.user.username}",
                controlled_by=request.user,
            )
            if result.success:
                count += 1

        self.message_user(
            request,
            f"Successfully reset {count} circuit breaker(s).",
        )


# =============================================================================
# Auto-register concrete models (223 Host App Decoupling)
# =============================================================================


def _auto_register_concrete_admin():
    """
    Auto-register concrete models provided by the selfhealing package.

    This is called at module-load time and registers admin classes
    for FailedOperation, FailedExternalRequest, SecurityIncident,
    and PostmortemRecord provided by the package.

    Uses admin.site.is_registered() to avoid double registration
    when the host app already registers its own admin classes.
    """
    if not DJANGO_AVAILABLE:
        return

    try:
        from selfhealing.adapters.django.models import (
            FailedExternalRequest,
            FailedOperation,
            SecurityIncident,
        )

        # FailedOperation Admin
        if not admin.site.is_registered(FailedOperation):

            @admin.register(FailedOperation)
            class FailedOperationAdmin(BaseDLQEntryAdmin):
                """Auto-registered FailedOperation admin from selfhealing package."""

                def get_user_admin_url(self, user: Any) -> str:
                    """Dynamic user admin URL based on AUTH_USER_MODEL."""
                    from django.conf import settings as django_settings

                    user_model = django_settings.AUTH_USER_MODEL
                    app_label, model_name = user_model.split(".")
                    return reverse(
                        f"admin:{app_label}_{model_name.lower()}_change",
                        args=[user.pk],
                    )

        # FailedExternalRequest Admin
        if not admin.site.is_registered(FailedExternalRequest):

            @admin.register(FailedExternalRequest)
            class FailedExternalRequestAdmin(admin.ModelAdmin):
                """Auto-registered FailedExternalRequest admin."""

                list_display = [
                    "id",
                    "domain",
                    "failure_type",
                    "status",
                    "entity_type",
                    "entity_id",
                    "retry_count",
                    "created_at",
                ]
                list_filter = ["domain", "status", "failure_type", "created_at"]
                search_fields = ["error_code", "error_message", "entity_type", "entity_id"]
                ordering = ["-created_at"]
                readonly_fields = [
                    "domain",
                    "entity_type",
                    "entity_id",
                    "failure_type",
                    "error_code",
                    "error_message",
                    "retry_count",
                    "last_retry_at",
                    "request_data",
                    "response_data",
                    "metadata",
                    "created_at",
                    "updated_at",
                    "expires_at",
                ]

        # SecurityIncident Admin
        if not admin.site.is_registered(SecurityIncident):

            @admin.register(SecurityIncident)
            class SecurityIncidentAdmin(admin.ModelAdmin):
                """Auto-registered SecurityIncident admin."""

                list_display = [
                    "id",
                    "incident_type",
                    "severity",
                    "status",
                    "source_ip",
                    "detected_at",
                ]
                list_filter = ["incident_type", "severity", "status", "detected_at"]
                search_fields = ["description", "source_ip", "investigation_notes"]
                ordering = ["-detected_at"]
                readonly_fields = [
                    "incident_type",
                    "severity",
                    "source_ip",
                    "user_agent",
                    "raw_request",
                    "detected_at",
                    "updated_at",
                ]

    except Exception as e:
        logger.debug(
            "self_healing.admin_auto_registration_skipped",
            error=e,
        )


_auto_register_concrete_admin()
