"""
RBAC Permission Classes for Self-Healing Control API.

Provides role-based access control for the Self-Healing system:
- Viewer: Read-only access (dashboard, status, audit logs)
- Operator: Operational tasks (DLQ replay, archive)
- Admin: Full access (CB control, system enable/disable, config changes)

Reference: docs/self_healing/10_OPERATIONS_GUIDE.md (권한 테이블)
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class IsViewer(BasePermission):
    """
    읽기 전용 권한 (Viewer 역할).

    허용되는 작업:
    - GET /status, GET /dashboard
    - GET /audit (감사 로그 조회)
    - GET /dlq/list, GET /dlq/<pk> (DLQ 조회)
    - GET /system/status (시스템 상태 조회)

    조건:
    - 인증된 사용자
    - staff 또는 'selfhealing_viewer' 그룹 멤버
    """

    message = "Self-Healing 조회 권한이 필요합니다. selfhealing_viewer 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin/Staff는 항상 허용
        if request.user.is_staff:
            return True

        # selfhealing_viewer, operator, admin 그룹 멤버십 확인
        # (상위 권한은 하위 권한 포함)
        return request.user.groups.filter(
            name__in=["selfhealing_viewer", "selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsOperator(BasePermission):
    """
    운영자 권한 (Operator 역할).

    허용되는 작업:
    - 모든 Viewer 권한
    - POST /dlq/replay (DLQ 리플레이)
    - POST /dlq/cleanup/archive (DLQ 아카이브)
    - POST /dlq/<pk>/retry (개별 항목 재시도)
    - POST /dlq/<pk>/resolve (개별 항목 해결)

    조건:
    - 인증된 사용자
    - superuser 또는 'selfhealing_operator' 또는 'selfhealing_admin' 그룹 멤버
    """

    message = "Self-Healing 운영자 권한이 필요합니다. selfhealing_operator 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin은 항상 허용
        if request.user.is_staff and request.user.is_superuser:
            return True

        # selfhealing_operator 또는 selfhealing_admin 그룹 멤버십 확인
        return request.user.groups.filter(
            name__in=["selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsSelfHealingAdmin(BasePermission):
    """
    관리자 권한 (Admin 역할).

    허용되는 작업:
    - 모든 Operator 권한
    - POST /control/ (CB 수동 제어: allow/block)
    - POST /system/enable, /system/disable (킬 스위치)
    - PUT /config/* (설정 변경)
    - POST /dlq/cleanup/purge (DLQ 영구 삭제)
    - Chaos Engineering 설정 변경

    조건:
    - 인증된 사용자
    - Django superuser 또는 'selfhealing_admin' 그룹 멤버

    보안:
    - Fail-Secure: 권한 확인 실패 시 거부
    """

    message = "Self-Healing 관리자 권한이 필요합니다. selfhealing_admin 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        요청 레벨 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부

        Note:
            Fail-Secure: 예외 발생 시 거부
        """
        try:
            if not request.user or not request.user.is_authenticated:
                return False

            # Django superuser
            if request.user.is_superuser:
                return True

            # selfhealing_admin 그룹
            return request.user.groups.filter(name="selfhealing_admin").exists()

        except Exception as e:
            # Fail-Secure: 오류 시 거부
            logger.warning(f"[RBAC] Permission check failed (deny): {e}")
            return False


# Backward compatibility aliases
SelfHealingViewer = IsViewer
SelfHealingOperator = IsOperator
SelfHealingAdmin = IsSelfHealingAdmin
