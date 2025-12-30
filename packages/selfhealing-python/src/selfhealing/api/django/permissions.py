"""
RBAC Permission Classes for Self-Healing Control API.

Provides role-based access control for the Self-Healing system:
- Viewer: Read-only access (dashboard, status, audit logs)
- Operator: Operational tasks (DLQ replay, archive)
- Admin: Full access (CB control, system enable/disable, config changes)
- EmergencyEscalation: Break Glass pattern for emergency mode changes
- ThresholdBased: Risk-based access control for high-impact operations

Reference: docs/self_healing/10_OPERATIONS_GUIDE.md (권한 테이블)
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Dict, Optional

from rest_framework.permissions import BasePermission

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def _is_auth_disabled() -> bool:
    """Check if SelfHealing auth is disabled for testing."""
    return os.environ.get("DISABLE_SELFHEALING_AUTH", "").lower() in ("true", "1", "yes")


class IsSelfHealingAuthenticated(BasePermission):
    """
    인증된 사용자만 접근 허용 (테스트 환경 바이패스 지원).
    
    DISABLE_SELFHEALING_AUTH=true 환경 변수가 설정되면
    인증 없이도 접근을 허용합니다.
    """
    
    message = "인증이 필요합니다."
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True
        
        return bool(request.user and request.user.is_authenticated)


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
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True
            
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
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True
            
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
            # 테스트 환경에서 인증 바이패스
            if _is_auth_disabled():
                return True
                
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


class EmergencyEscalationPermission(BasePermission):
    """
    긴급 에스컬레이션 권한 (Break Glass Pattern).

    일방향 긴급권:
    - STRICT 전환: Operator도 가능 (긴급 상황)
    - NORMAL 복구: Admin만 가능 (승인 필요)

    사용 시나리오:
    - Admin 부재 중 시스템 폭주
    - 운영자가 긴급히 STRICT 모드로 전환 필요

    Reference:
    - AWS Break Glass Pattern
    - Google SRE Emergency Access
    - PCI-DSS Break Glass Procedure
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
    """

    message = "긴급 에스컬레이션 권한이 없습니다. STRICT 전환은 Operator 이상, NORMAL 복구는 Admin만 가능합니다."
    EMERGENCY_EXPIRY_HOURS = 4  # 긴급 모드 자동 만료 시간

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        모드 전환에 대한 권한 체크.

        Args:
            request: HTTP 요청 객체 (data.mode 필드 사용)
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        if not request.user or not request.user.is_authenticated:
            return False

        target_mode = request.data.get("mode", "").upper()

        # STRICT 전환 = Operator도 가능 (일방향 긴급권)
        if target_mode == "STRICT":
            has_perm = IsOperator().has_permission(request, view)
            if has_perm:
                logger.warning(
                    f"[RBAC] Emergency escalation to STRICT by operator: "
                    f"user={request.user}, expiry_hours={self.EMERGENCY_EXPIRY_HOURS}"
                )
            return has_perm

        # NORMAL 복구 = Admin만 가능
        if target_mode == "NORMAL":
            return IsSelfHealingAdmin().has_permission(request, view)

        # 기타 모드 변경 = Admin만
        return IsSelfHealingAdmin().has_permission(request, view)


class ThresholdBasedPermission(BasePermission):
    """
    임계값 기반 동적 권한 (Risk-Based Access Control).

    오차율(discrepancy_rate)에 따라 필요 권한 레벨 결정:
    - 15% 이하: Operator 승인
    - 30% 이하: Admin 승인
    - 30% 초과: Admin 승인 + 경고 로그 (4-Eyes 권장)

    사용처:
    - 정합성 조정 승인
    - 대규모 변경 승인

    임계값 우선순위:
    1. RuntimeConfigManager (governance config)
    2. 환경변수 (SELFHEALING_THRESHOLD_*)
    3. 기본값 (0.15, 0.30)

    환경변수로 임계값 조정 가능:
    - SELFHEALING_THRESHOLD_OPERATOR: Operator 승인 상한 (기본: 0.15)
    - SELFHEALING_THRESHOLD_ADMIN: Admin 승인 상한 (기본: 0.30)

    Reference:
    - 은행권 거래 승인 레벨
    - GitHub PR 리뷰어 수 조정
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    """

    message = "해당 작업의 임계값이 권한 레벨을 초과합니다."

    def __init__(self) -> None:
        """임계값을 런타임 설정 또는 환경변수에서 로드."""
        super().__init__()
        self._cached_thresholds: Optional[Dict[str, float]] = None

    def _get_thresholds(self) -> Dict[str, float]:
        """
        임계값을 가져옵니다.

        우선순위:
        1. RuntimeConfigManager (governance config)
        2. 환경변수
        3. 기본값

        Returns:
            Dict[str, float]: operator_approve, admin_approve 임계값
        """
        # 런타임 설정 조회 시도
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            governance = manager.get_governance_config()
            if governance:
                return {
                    "operator_approve": governance.get("threshold_operator", 0.15),
                    "admin_approve": governance.get("threshold_admin", 0.30),
                }
        except Exception as e:
            logger.debug(f"[RBAC] RuntimeConfigManager unavailable, using env/defaults: {e}")

        # 환경변수 폴백
        return {
            "operator_approve": float(
                os.environ.get("SELFHEALING_THRESHOLD_OPERATOR", "0.15")
            ),
            "admin_approve": float(
                os.environ.get("SELFHEALING_THRESHOLD_ADMIN", "0.30")
            ),
        }

    @property
    def thresholds(self) -> Dict[str, float]:
        """임계값 속성 (매 요청 시 새로 조회)."""
        return self._get_thresholds()

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        임계값 기반 권한 체크.

        Args:
            request: HTTP 요청 객체 (data.discrepancy_rate 필드 사용)
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        if not request.user or not request.user.is_authenticated:
            return False

        discrepancy = request.data.get("discrepancy_rate", 0)

        try:
            discrepancy = float(discrepancy)
        except (TypeError, ValueError):
            discrepancy = 0

        current_thresholds = self.thresholds

        # 임계값별 권한 체크
        if discrepancy <= current_thresholds["operator_approve"]:
            return IsOperator().has_permission(request, view)

        if discrepancy <= current_thresholds["admin_approve"]:
            return IsSelfHealingAdmin().has_permission(request, view)

        # 30% 초과: 4-Eyes 원칙 (현재는 Admin + 경고 로그)
        return self._check_high_risk_approval(request, view, discrepancy, current_thresholds)

    def _check_high_risk_approval(
        self, request: Request, view: APIView, discrepancy: float,
        thresholds: Optional[Dict[str, float]] = None
    ) -> bool:
        """
        4-Eyes 원칙: 고위험 작업은 Admin + 경고 로그.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체
            discrepancy: 오차율
            thresholds: 현재 임계값 (없으면 조회)

        Returns:
            bool: Admin 권한 여부

        Note:
            추후 듀얼 승인 워크플로우로 확장 가능
        """
        if thresholds is None:
            thresholds = self.thresholds

        if IsSelfHealingAdmin().has_permission(request, view):
            logger.warning(
                f"[RBAC] High-risk operation approved by single admin: "
                f"discrepancy={discrepancy:.1%}, user={request.user}, "
                f"threshold_exceeded={thresholds['admin_approve']:.1%}"
            )
            return True
        return False


# Backward compatibility aliases
SelfHealingViewer = IsViewer
SelfHealingOperator = IsOperator
SelfHealingAdmin = IsSelfHealingAdmin
