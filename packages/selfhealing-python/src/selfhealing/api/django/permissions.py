"""
RBAC Permission Classes for Self-Healing Control API.

Provides role-based access control for the Self-Healing system:
- Viewer: Read-only access (dashboard, status, audit logs)
- Operator: Operational tasks (DLQ replay, archive)
- Admin: Full access (CB control, system enable/disable, config changes)
- EmergencyEscalation: Break Glass pattern for emergency mode changes
- ThresholdBased: Risk-based access control for high-impact operations
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def _is_auth_disabled() -> bool:
    """Check if SelfHealing auth is disabled for testing.

    Security Hardening (214_SECURITY_VULNERABILITY_FIXES):
    - 프로덕션 환경에서는 DISABLE_SELFHEALING_AUTH 환경변수가 설정되어도
      절대로 인증을 우회할 수 없음 (Fail-Secure)
    - ENVIRONMENT=production 또는 DJANGO_SETTINGS_MODULE에 'prod'가 포함된 경우 차단
    - 인증 바이패스 시도 시 WARNING 로그 기록
    """
    # Fail-Secure: 프로덕션 환경에서는 절대 바이패스 불가
    environment = os.environ.get("ENVIRONMENT", "development").lower()
    django_settings = os.environ.get("DJANGO_SETTINGS_MODULE", "").lower()

    is_production = environment in ("production", "prod", "live") or "prod" in django_settings

    if is_production:
        # 프로덕션에서 바이패스 시도 감지 시 경고 로그
        if os.environ.get("DISABLE_SELFHEALING_AUTH", "").lower() in ("true", "1", "yes"):
            logger.error(
                "[SECURITY] DISABLE_SELFHEALING_AUTH is set in PRODUCTION environment! "
                "Auth bypass is BLOCKED. Remove this environment variable immediately. "
                f"ENVIRONMENT={environment}, DJANGO_SETTINGS_MODULE={django_settings}"
            )
        return False

    return os.environ.get("DISABLE_SELFHEALING_AUTH", "").lower() in (
        "true",
        "1",
        "yes",
    )


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
        return request.user.groups.filter(name__in=["selfhealing_operator", "selfhealing_admin"]).exists()


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
    - STRICT 전환: Operator도 가능 (긴급 상황) + 사유 필수
    - NORMAL 복구: Admin만 가능 (승인 필요)

    사용 시나리오:
    - Admin 부재 중 시스템 폭주
    - 운영자가 긴급히 STRICT 모드로 전환 필요

    강제 Audit:
    - STRICT 전환 시 reason 필수 (사후 검토 보장)
    - 모든 전환은 EmergencyModeTracker에 기록됨

    Reference:
    - AWS Break Glass Pattern
    - Google SRE Emergency Access
    - PCI-DSS Break Glass Procedure
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
    """

    message = "긴급 에스컬레이션 권한이 없습니다. STRICT 전환은 Operator 이상, NORMAL 복구는 Admin만 가능합니다."

    @property
    def emergency_expiry_hours(self) -> int:
        """긴급 모드 자동 만료 시간 (Settings에서 로드)."""
        try:
            from selfhealing.settings.governance import get_governance_settings

            return get_governance_settings().emergency_expiry_hours
        except ImportError:
            return 4  # 기본값

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        모드 전환에 대한 권한 체크.

        STRICT 전환 시 reason 필수 검증 (강제 Audit).

        Args:
            request: HTTP 요청 객체 (data.mode, data.reason 필드 사용)
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        if not request.user or not request.user.is_authenticated:
            return False

        target_mode = request.data.get("mode", "").upper()
        reason = request.data.get("reason", "").strip()

        # STRICT 전환 = Operator도 가능 (일방향 긴급권) + 사유 필수
        if target_mode == "STRICT":
            # 강제 Audit: 사유 필수 검증
            if not reason:
                self.message = "STRICT 모드 전환 시 reason(사유)은 필수입니다. 사후 감사를 위해 전환 사유를 입력해주세요."
                logger.warning(f"[RBAC] STRICT escalation denied - reason required: " f"user={request.user}")
                return False

            has_perm = IsOperator().has_permission(request, view)
            if has_perm:
                logger.warning(
                    f"[RBAC] Emergency escalation to STRICT by operator: "
                    f"user={request.user}, reason={reason[:50]}, "
                    f"expiry_hours={self.emergency_expiry_hours}"
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
    - 30% ~ dual_approval 임계값: Admin + 경고 로그
    - dual_approval 임계값 초과: 4-Eyes 듀얼 승인 필수

    사용처:
    - 정합성 조정 승인
    - 대규모 변경 승인

    임계값 우선순위:
    1. RuntimeConfigManager (governance config)
    2. 환경변수 (SELFHEALING_THRESHOLD_*)
    3. 기본값 (0.15, 0.30, 0.50)

    환경변수로 임계값 조정 가능:
    - SELFHEALING_THRESHOLD_OPERATOR: Operator 승인 상한 (기본: 0.15)
    - SELFHEALING_THRESHOLD_ADMIN: Admin 승인 상한 (기본: 0.30)
    - SELFHEALING_THRESHOLD_DUAL_APPROVAL: 듀얼 승인 강제 임계값 (기본: 0.50)

    Reference:
    - 은행권 거래 승인 레벨
    - GitHub PR 리뷰어 수 조정
    - PCI-DSS Dual Control Requirements
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
    """

    message = "해당 작업의 임계값이 권한 레벨을 초과합니다."

    # 듀얼 승인 관련 메시지
    DUAL_APPROVAL_REQUIRED_MSG = (
        "고위험 작업입니다. 4-Eyes 듀얼 승인이 필요합니다. "
        "approval_id를 제공하거나, 먼저 승인 요청을 생성해주세요. "
        "POST /api/self-healing/governance/approval-requests/"
    )

    def __init__(self) -> None:
        """임계값을 런타임 설정 또는 환경변수에서 로드."""
        super().__init__()
        self._cached_thresholds: dict[str, float] | None = None

    def _get_thresholds(self) -> dict[str, float]:
        """
        임계값을 가져옵니다.

        우선순위:
        1. RuntimeConfigManager (governance config)
        2. 환경변수
        3. 기본값

        Returns:
            Dict[str, float]: operator_approve, admin_approve, dual_approval 임계값
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
                    "dual_approval": governance.get("threshold_dual_approval", 0.50),
                }
        except Exception as e:
            logger.debug(f"[RBAC] RuntimeConfigManager unavailable, using Settings: {e}")

        # Settings 폴백 (환경변수 대신)
        try:
            from selfhealing.settings.governance import get_governance_settings

            settings = get_governance_settings()
            return {
                "operator_approve": settings.threshold_operator,
                "admin_approve": settings.threshold_admin,
                "dual_approval": getattr(settings, "threshold_dual_approval", 0.50),
            }
        except ImportError:
            # 기본값 폴백
            return {
                "operator_approve": 0.15,
                "admin_approve": 0.30,
                "dual_approval": 0.50,
            }

    @property
    def thresholds(self) -> dict[str, float]:
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

        # admin_approve 초과, dual_approval 이하: Admin + 경고 로그
        if discrepancy <= current_thresholds["dual_approval"]:
            return self._check_high_risk_approval(request, view, discrepancy, current_thresholds)

        # dual_approval 초과: 4-Eyes 듀얼 승인 강제
        return self._check_dual_approval_required(request, view, discrepancy, current_thresholds)

    def _check_high_risk_approval(
        self,
        request: Request,
        view: APIView,
        discrepancy: float,
        thresholds: dict[str, float] | None = None,
    ) -> bool:
        """
        고위험 작업: Admin + 경고 로그 (30% ~ 50% 구간).

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체
            discrepancy: 오차율
            thresholds: 현재 임계값 (없으면 조회)

        Returns:
            bool: Admin 권한 여부
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

    def _check_dual_approval_required(
        self,
        request: Request,
        view: APIView,
        discrepancy: float,
        thresholds: dict[str, float] | None = None,
    ) -> bool:
        """
        4-Eyes 듀얼 승인 강제 (50% 초과).

        approval_id가 제공되고 해당 요청이 APPROVED 상태인 경우에만 허용.
        승인되지 않은 경우 403 Forbidden과 함께 안내 메시지 반환.

        Args:
            request: HTTP 요청 객체 (data.approval_id 필드 사용)
            view: 뷰 객체
            discrepancy: 오차율
            thresholds: 현재 임계값 (없으면 조회)

        Returns:
            bool: 승인된 approval_id가 있으면 True, 아니면 False
        """
        if thresholds is None:
            thresholds = self.thresholds

        # Admin 권한 필수
        if not IsSelfHealingAdmin().has_permission(request, view):
            return False

        approval_id = request.data.get("approval_id")
        actor = getattr(request.user, "username", str(request.user))

        # approval_id가 없으면 거부 + 안내 메시지
        if not approval_id:
            self.message = self.DUAL_APPROVAL_REQUIRED_MSG
            logger.warning(
                f"[RBAC] Dual approval required but no approval_id provided: "
                f"discrepancy={discrepancy:.1%}, user={actor}, "
                f"threshold={thresholds['dual_approval']:.1%}"
            )
            # 알림 발송 (승인 요청이 필요하다는 것을 Admin들에게 알림)
            self._notify_dual_approval_needed(actor, discrepancy, request)
            return False

        # approval_id 검증
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            all_requests = manager.get_approval_requests()

            for approval_request in all_requests:
                if approval_request["id"] == approval_id:
                    # 상태 확인
                    if approval_request["status"] != "APPROVED":
                        self.message = (
                            f"승인 요청 '{approval_id}'이(가) 아직 승인되지 않았습니다. "
                            f"현재 상태: {approval_request['status']}"
                        )
                        logger.warning(
                            f"[RBAC] Dual approval request not approved: "
                            f"approval_id={approval_id}, status={approval_request['status']}"
                        )
                        return False

                    # 요청자 != 현재 사용자 확인 (4-Eyes: 승인자가 실행자가 아니어도 됨)
                    # 승인은 되었으므로 진행 허용
                    logger.info(
                        f"[RBAC] Dual approval verified: "
                        f"approval_id={approval_id}, "
                        f"requested_by={approval_request['requested_by']}, "
                        f"approved_by={approval_request['approved_by']}, "
                        f"executed_by={actor}, "
                        f"discrepancy={discrepancy:.1%}"
                    )
                    return True

            # approval_id를 찾지 못함
            self.message = f"승인 요청 '{approval_id}'을(를) 찾을 수 없습니다."
            logger.warning(f"[RBAC] Approval request not found: approval_id={approval_id}")
            return False

        except Exception as e:
            logger.error(f"[RBAC] Failed to verify dual approval: {e}")
            self.message = f"듀얼 승인 검증 중 오류가 발생했습니다: {e}"
            return False

    def _notify_dual_approval_needed(self, actor: str, discrepancy: float, request: Request) -> None:
        """
        듀얼 승인이 필요할 때 Admin들에게 알림 발송.

        Args:
            actor: 요청자 username
            discrepancy: 오차율
            request: HTTP 요청 객체
        """
        try:
            from selfhealing.services.security_notification import (
                SecurityNotificationService,
            )

            service = SecurityNotificationService()
            if not service.config.enabled:
                return

            # 알림 발송 (HIGH severity로 Slack + Email)
            service.notify_security_incident_by_id(
                incident_id=0,  # 실제 incident가 아니므로 0
                incident_type="dual_approval_required",
                severity="high",
                description=(
                    f"[4-Eyes Required] 고위험 작업에 듀얼 승인이 필요합니다.\n"
                    f"요청자: {actor}\n"
                    f"오차율: {discrepancy:.1%}\n"
                    f"작업: {request.path}\n"
                    f"승인 요청 생성 후 다른 Admin의 승인이 필요합니다."
                ),
                source_ip=self._get_client_ip(request),
                action_taken="awaiting_dual_approval",
            )
            logger.info(f"[RBAC] Dual approval notification sent for user={actor}")

        except Exception as e:
            # Best-effort: 알림 실패해도 권한 체크는 계속
            logger.warning(f"[RBAC] Failed to send dual approval notification: {e}")

    def _get_client_ip(self, request: Request) -> str | None:
        """클라이언트 IP 추출."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


class IsPanicRollbackAuthorized(BasePermission):
    """
    긴급 롤백(Panic Rollback) 권한.

    Canary Rollout 긴급 전체 롤백을 위한 Break Glass 권한.
    4-Eyes 승인 없이 단일 승인자가 긴급 실행 가능.

    허용 조건:
    - Admin 권한 보유자
    - 또는 Emergency Escalation 권한 보유자 (reason 필수)

    강제 Audit:
    - 모든 긴급 롤백은 CanaryAudit에 기록됨
    - reason 필수 (사후 검토 보장)

    Reference:
    - AWS Break Glass Pattern
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
    """

    message = "긴급 롤백 권한이 없습니다. " "Admin 또는 Emergency Escalation 권한이 필요합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        긴급 롤백 권한 체크.

        Args:
            request: HTTP 요청 객체 (data.reason 필드 사용)
            view: 뷰 객체

        Returns:
            bool: 권한 여부
        """
        # 테스트 환경에서 인증 바이패스
        if _is_auth_disabled():
            return True

        if not request.user or not request.user.is_authenticated:
            return False

        # reason 필수 검증 (강제 Audit)
        reason = request.data.get("reason", "").strip()
        if not reason:
            self.message = "긴급 롤백 시 reason(사유)은 필수입니다. " "사후 감사를 위해 롤백 사유를 입력해주세요."
            logger.warning(f"[RBAC] Panic rollback denied - reason required: " f"user={request.user}")
            return False

        # Admin은 항상 허용
        if IsSelfHealingAdmin().has_permission(request, view):
            logger.warning(f"[RBAC] Panic rollback authorized (Admin): " f"user={request.user}, reason={reason[:50]}")
            return True

        # Operator + Emergency Escalation (Break Glass)
        if IsOperator().has_permission(request, view):
            logger.warning(
                f"[RBAC] Panic rollback authorized (Emergency Escalation): " f"user={request.user}, reason={reason[:50]}"
            )
            return True

        return False


class HasChaosTestPermission(BasePermission):
    """
    X-Test/Chaos 실험 API 권한 (2중 보안 장치 - 1차 Django RBAC).

    X-Test-Mode API에 대한 Django RBAC 기반 권한 클래스.
    헤더 검증(XTestModeMixin.check_chaos_permission)과 함께 2중 보안을 구성합니다.

    허용 조건 (OR):
    - 테스트 바이패스: DISABLE_SELFHEALING_AUTH=true
    - Django superuser
    - selfhealing_admin 그룹 멤버
    - selfhealing_chaos_tester 그룹 멤버

    차단 조건 (무조건):
    - ENVIRONMENT == production (Fail-Secure)

    로깅:
    - 권한 거부: WARNING (사용자, 이유)
    - 프로덕션 차단: ERROR
    - 권한 허용: DEBUG

    보안:
    - Fail-Secure: 모든 예외는 거부로 처리
    """

    message = "X-Test/Chaos 실험 권한이 없습니다. " "selfhealing_admin 또는 selfhealing_chaos_tester 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        X-Test/Chaos API 접근 권한 체크.

        Args:
            request: HTTP 요청 객체
            view: 뷰 객체

        Returns:
            bool: 권한 여부

        Note:
            Fail-Secure: 모든 예외 발생 시 거부
        """
        try:
            # 1. 테스트 환경 바이패스 (DISABLE_SELFHEALING_AUTH=true)
            if _is_auth_disabled():
                logger.debug(
                    f"[RBAC] X-Test permission bypassed (auth disabled): " f"path={getattr(request, 'path', 'unknown')}"
                )
                return True

            # 2. 프로덕션 환경 무조건 차단 (Fail-Secure)
            environment = os.environ.get("ENVIRONMENT", "development").lower()
            if environment == "production":
                logger.error(
                    f"[RBAC] X-Test access DENIED in production: "
                    f"user={request.user}, path={getattr(request, 'path', 'unknown')}, "
                    f"ip={self._get_client_ip(request)}"
                )
                self.message = (
                    "X-Test/Chaos API는 프로덕션 환경에서 사용할 수 없습니다. " "보안 정책에 따라 접근이 차단되었습니다."
                )
                return False

            # 3. 인증 필요
            if not request.user or not request.user.is_authenticated:
                logger.warning(
                    f"[RBAC] X-Test permission denied (not authenticated): " f"path={getattr(request, 'path', 'unknown')}"
                )
                self.message = "X-Test/Chaos API 접근에는 인증이 필요합니다."
                return False

            # 4. Django superuser 자동 허용
            if request.user.is_superuser:
                logger.debug(f"[RBAC] X-Test permission granted (superuser): " f"user={request.user}")
                return True

            # 5. 그룹 기반 권한 체크 (selfhealing_admin 또는 selfhealing_chaos_tester)
            allowed_groups = ["selfhealing_admin", "selfhealing_chaos_tester"]
            if request.user.groups.filter(name__in=allowed_groups).exists():
                user_groups = list(request.user.groups.filter(name__in=allowed_groups).values_list("name", flat=True))
                logger.debug(f"[RBAC] X-Test permission granted (group): " f"user={request.user}, groups={user_groups}")
                return True

            # 6. 권한 없음 - 거부
            logger.warning(
                f"[RBAC] X-Test permission denied (no group): " f"user={request.user}, required_groups={allowed_groups}"
            )
            return False

        except Exception as e:
            # Fail-Secure: 예외 발생 시 거부
            logger.error(
                f"[RBAC] X-Test permission check failed (deny): " f"error={e}, user={getattr(request, 'user', 'unknown')}"
            )
            self.message = "권한 확인 중 오류가 발생했습니다. 접근이 거부되었습니다."
            return False

    def _get_client_ip(self, request: Request) -> str | None:
        """클라이언트 IP 추출."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


# Backward compatibility aliases
SelfHealingViewer = IsViewer
SelfHealingOperator = IsOperator
SelfHealingAdmin = IsSelfHealingAdmin
