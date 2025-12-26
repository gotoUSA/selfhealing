"""
Error Budget API Views.

REST API endpoints for Error Budget management and Deployment Policy.

Endpoints:
- GET  /api/self-healing/error-budget/status/           - Get Error Budget status (V3: cached)
- GET  /api/self-healing/error-budget/history/          - Get budget consumption history
- POST /api/self-healing/error-budget/record/           - Record errors (Chaos Engineering)
- POST /api/self-healing/error-budget/exhaust/          - Simulate budget exhaustion (Test)
- POST /api/self-healing/error-budget/reset-simulation/ - Reset simulation stats
- GET  /api/self-healing/deployment-policy/verdict/     - Get deployment verdict
- POST /api/self-healing/deployment-policy/acknowledge/ - Acknowledge freeze
- POST /api/self-healing/deployment-policy/override/    - Approve override
- POST /api/self-healing/deployment-policy/lift/        - Lift freeze

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
실제 CI/CD 차단 기능은 없으며, 상태 조회 및 결정 기록만 제공합니다.

FAIL-SAFE DESIGN:
- Error Budget 시스템 장애 시 → 기본값 PROCEED (fail-open)
- 배포를 막는 것보다 시스템 가용성이 더 중요
- 장애 시에도 CI/CD 파이프라인이 중단되지 않도록 보장

V3 Optimization:
- L1 In-process cache (2s TTL) + L2 Redis cache (15s TTL)
- Target: P95 < 20ms for /error-budget/status/
"""

import logging
from typing import Any, Dict

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.error_budget_service import (
    get_error_budget_service,
    get_failsafe_verdict_response,
    get_failsafe_status_response,
    OverrideType,
    FreezeStatus,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Error Budget Views
# =============================================================================


class ErrorBudgetStatusView(APIView):
    """
    Error Budget 상태 조회 API.

    GET /api/self-healing/error-budget/status/

    Returns current Error Budget status including:
    - Budget remaining (minutes, percentage)
    - Burn rate (1h, 6h)
    - SLO information
    - Health status

    Query Parameters:
    - slo_name: SLO name to check (default: "availability")
    - nocache: Set to "true" to bypass cache (V3)
    
    V3 Optimization: Uses multi-tier cache for P95 < 20ms target.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            slo_name = request.query_params.get("slo_name", "availability")
            use_cache = request.query_params.get("nocache", "").lower() != "true"
            
            # V3: Use cached response for default SLO
            if use_cache and slo_name == "availability":
                try:
                    from selfhealing.services.precomputed_cache import get_cached_error_budget
                    return Response(get_cached_error_budget())
                except ImportError:
                    pass  # Fall through to direct computation

            # Direct computation for non-default SLO or if cache unavailable
            service = get_error_budget_service()
            budget_status = service.get_budget_status(slo_name)

            return Response(
                {
                    "status": "success",
                    "data": budget_status.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Status failed: {e}", exc_info=True)
            # FAIL-SAFE: 시스템 장애 시에도 기본 응답 제공 (200 OK)
            return Response(get_failsafe_status_response(str(e)))


class ErrorBudgetHistoryView(APIView):
    """
    Error Budget 결정 이력 조회 API.

    GET /api/self-healing/error-budget/history/

    Query Parameters:
    - limit: Maximum records to return (default: 50)
    - decision_type: Filter by type (freeze_acknowledged, override_approved, freeze_lifted)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            limit = int(request.query_params.get("limit", 50))
            decision_type = request.query_params.get("decision_type")

            service = get_error_budget_service()
            history = service.get_decision_history(limit=limit, decision_type=decision_type)

            return Response(
                {
                    "status": "success",
                    "data": {
                        "records": [r.to_dict() for r in history],
                        "count": len(history),
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] History failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetRecordView(APIView):
    """
    Error Budget 에러 기록 API (Chaos Engineering / Test).

    POST /api/self-healing/error-budget/record/

    Request Body:
    {
        "error_count": 10,           // 기록할 에러 수 (default: 1)
        "error_type": "timeout",     // 에러 유형 (default: "simulated")
        "service_name": "payment"    // 서비스 이름 (default: "test")
    }

    ⚠️ WARNING: 이 API는 테스트/Chaos 환경에서만 사용해야 합니다.
    실제 운영 환경에서는 사용하지 마십시오.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            # 다양한 형식 지원 (호환성)
            # Format 1: error_count (단순 에러 수)
            # Format 2: domain/severity/multiplier (도메인별 가중치)
            error_count = int(request.data.get("error_count", 1))
            error_type = request.data.get("error_type", "simulated")
            service_name = request.data.get("service_name", "test")
            
            # Extended format: domain, severity, multiplier
            domain = request.data.get("domain", service_name)
            severity = request.data.get("severity", "medium")
            multiplier = float(request.data.get("multiplier", 1.0))
            reason = request.data.get("reason", "")
            
            # severity에 따른 가중치 조정
            severity_weights = {
                "low": 1,
                "medium": 3,
                "high": 5,
                "critical": 10,
            }
            base_errors = severity_weights.get(severity, 1)
            effective_errors = int(base_errors * multiplier * error_count)

            if effective_errors < 1:
                effective_errors = 1

            service = get_error_budget_service()
            result = service.record_error(
                error_count=effective_errors,
                error_type=error_type,
                service_name=domain,
            )
            
            # 확장 정보 추가
            result["severity"] = severity
            result["multiplier"] = multiplier
            result["domain"] = domain
            result["effective_errors"] = effective_errors
            if reason:
                result["reason"] = reason

            logger.info(
                f"[ErrorBudgetAPI] Recorded {effective_errors} errors "
                f"(domain={domain}, severity={severity}, multiplier={multiplier})"
            )

            return Response(
                {
                    "status": "success",
                    "message": f"{effective_errors} error(s) recorded for budget consumption",
                    "data": result,
                    "remaining_percent": result.get("budget_remaining_percent"),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Record failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetExhaustView(APIView):
    """
    Error Budget 고갈 시뮬레이션 API (Chaos Engineering / Test).

    POST /api/self-healing/error-budget/exhaust/

    Request Body:
    {
        "target_remaining_percent": 0.0  // 목표 잔여 비율 (default: 0.0 = 완전 고갈)
    }

    ⚠️ WARNING: 이 API는 테스트/Chaos 환경에서만 사용해야 합니다.
    Error Budget 고갈 시나리오 테스트용입니다.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            target = float(request.data.get("target_remaining_percent", 0.0))

            if target < 0 or target > 100:
                return Response(
                    {
                        "status": "error",
                        "error": "target_remaining_percent must be between 0 and 100",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            result = service.simulate_budget_exhaustion(target_remaining_percent=target)

            logger.warning(
                f"[ErrorBudgetAPI] Budget exhaustion simulated: target={target}%"
            )

            return Response(
                {
                    "status": "success",
                    "message": f"Budget exhaustion simulated to {target}%",
                    "data": result,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Exhaust failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ErrorBudgetResetSimulationView(APIView):
    """
    Error Budget 시뮬레이션 통계 초기화 API.

    POST /api/self-healing/error-budget/reset-simulation/

    시뮬레이션으로 기록된 에러/요청 통계를 초기화합니다.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            service = get_error_budget_service()
            result = service.reset_simulated_stats()

            logger.info("[ErrorBudgetAPI] Simulation stats reset")

            return Response(
                {
                    "status": "success",
                    "message": "Simulation stats reset",
                    "data": result,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ErrorBudgetAPI] Reset simulation failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Deployment Policy Views
# =============================================================================


class DeploymentVerdictView(APIView):
    """
    배포 가능 여부 판정 API.

    GET /api/self-healing/deployment-policy/verdict/

    Returns deployment verdict including:
    - status: proceed, caution, warning, freeze_recommended
    - can_deploy: boolean
    - requires_override: boolean
    - message: Human-readable recommendation
    - allowed_deployment_types: List of allowed deployment types

    Query Parameters:
    - slo_name: SLO name to evaluate (default: "availability")

    Note: This is an ADVISORY endpoint. It does not block deployments.
    CI/CD tools can query this endpoint to display warnings to operators.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            slo_name = request.query_params.get("slo_name", "availability")

            service = get_error_budget_service()
            verdict = service.get_deployment_verdict(slo_name)

            # 활성 Override 확인
            active_override = service.check_active_override()

            response_data = verdict.to_dict()

            if active_override:
                response_data["active_override"] = active_override.to_dict()
                response_data["verdict"]["has_active_override"] = True
            else:
                response_data["verdict"]["has_active_override"] = False

            return Response(
                {
                    "status": "success",
                    "data": response_data,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Verdict failed: {e}", exc_info=True)
            # FAIL-SAFE: 시스템 장애 시에도 기본 PROCEED 응답 (200 OK)
            # CI/CD 파이프라인이 Error Budget 시스템 장애로 중단되면 안 됨
            return Response(get_failsafe_verdict_response(str(e)))


class DeploymentFreezeAcknowledgeView(APIView):
    """
    배포 동결 확정 API.

    POST /api/self-healing/deployment-policy/acknowledge/

    Request Body:
    {
        "justification": "Error budget critical, pausing all deployments"
    }

    Records that the operator has acknowledged the freeze recommendation
    and confirms the deployment freeze.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")

            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "동결 확정 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.acknowledge_freeze(
                decided_by=decided_by,
                justification=justification,
            )

            logger.info(f"[DeploymentPolicy] Freeze acknowledged by {decided_by}: {justification}")

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결이 확정되었습니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Acknowledge failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeploymentOverrideView(APIView):
    """
    배포 동결 무시(Override) 승인 API.

    POST /api/self-healing/deployment-policy/override/

    Request Body:
    {
        "justification": "Critical security patch for CVE-2024-XXXX",
        "override_type": "security_patch",  // hotfix, security_patch, executive_approval, rollback
        "deployment_id": "deploy-abc123",   // optional
        "deployment_name": "payment-service v1.2.3",  // optional
        "expires_hours": 4  // optional, default 4
    }

    Records that the operator has decided to override the freeze recommendation
    and proceed with deployment. This creates an audit trail.

    IMPORTANT: This is a governance record. It does NOT automatically
    enable deployments. CI/CD systems should check for active overrides.
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")
            override_type_str = request.data.get("override_type", "")
            deployment_id = request.data.get("deployment_id")
            deployment_name = request.data.get("deployment_name")
            expires_hours = int(request.data.get("expires_hours", 4))

            # Validation
            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "Override 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not override_type_str:
                return Response(
                    {
                        "status": "error",
                        "error": "override_type is required",
                        "message": "Override 유형을 선택해주세요.",
                        "valid_types": [t.value for t in OverrideType],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                override_type = OverrideType(override_type_str)
            except ValueError:
                return Response(
                    {
                        "status": "error",
                        "error": f"Invalid override_type: {override_type_str}",
                        "valid_types": [t.value for t in OverrideType],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.approve_override(
                decided_by=decided_by,
                justification=justification,
                override_type=override_type,
                deployment_id=deployment_id,
                deployment_name=deployment_name,
                expires_hours=expires_hours,
            )

            logger.warning(
                f"[DeploymentPolicy] Override approved by {decided_by}: "
                f"type={override_type.value}, deployment={deployment_name}"
            )

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결 무시가 승인되었습니다. 이 결정은 감사 로그에 기록됩니다.",
                    "warning": "Error Budget이 낮은 상태에서의 배포는 추가 장애 위험이 있습니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Override failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeploymentFreezeLiftView(APIView):
    """
    배포 동결 해제 API.

    POST /api/self-healing/deployment-policy/lift/

    Request Body:
    {
        "justification": "Error budget recovered to healthy level"
    }

    Records that the deployment freeze has been lifted.
    This should be called when:
    - Error budget has recovered to healthy levels
    - The situation has been resolved
    - Management decides to resume normal operations
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        try:
            justification = request.data.get("justification", "")

            if not justification:
                return Response(
                    {
                        "status": "error",
                        "error": "justification is required",
                        "message": "동결 해제 사유를 입력해주세요.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_error_budget_service()
            decided_by = getattr(request.user, "username", str(request.user))

            record = service.lift_freeze(
                decided_by=decided_by,
                justification=justification,
            )

            logger.info(f"[DeploymentPolicy] Freeze lifted by {decided_by}: {justification}")

            return Response(
                {
                    "status": "success",
                    "message": "배포 동결이 해제되었습니다. 일반 배포가 가능합니다.",
                    "data": record.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Lift failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ActiveOverrideView(APIView):
    """
    활성 Override 조회 API.

    GET /api/self-healing/deployment-policy/active-override/

    Returns the currently active override, if any.
    CI/CD systems can use this to check if a deployment is allowed
    despite the freeze recommendation.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            service = get_error_budget_service()
            active_override = service.check_active_override()

            if active_override:
                return Response(
                    {
                        "status": "success",
                        "has_active_override": True,
                        "data": active_override.to_dict(),
                        "timestamp": timezone.now().isoformat(),
                    }
                )
            else:
                return Response(
                    {
                        "status": "success",
                        "has_active_override": False,
                        "data": None,
                        "timestamp": timezone.now().isoformat(),
                    }
                )

        except Exception as e:
            logger.error(f"[DeploymentPolicyAPI] Active override check failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# Reconciliation Views (Shadow Budget)
# =============================================================================


class ReconciliationStatusView(APIView):
    """
    Reconciliation 상태 조회 API.

    GET /api/self-healing/reconciliation/status/

    Returns:
    - Fail-Safe period tracker status
    - Pending shadow budgets count
    - Excluded periods count
    - Configuration

    Core Principle: "시스템은 계산하고, 반영은 사람이 결정한다."
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            status_data = service.get_status()

            return Response(
                {
                    "status": "success",
                    "data": status_data,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Status failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class FailSafePeriodsView(APIView):
    """
    Fail-Safe 기간 목록 조회 API.

    GET /api/self-healing/reconciliation/failsafe-periods/

    Query Parameters:
    - limit: Maximum records (default: 50)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_period_tracker

            limit = int(request.query_params.get("limit", 50))
            tracker = get_period_tracker()
            periods = tracker.get_all_periods(limit=limit)

            return Response(
                {
                    "status": "success",
                    "data": {
                        "periods": [p.to_dict() for p in periods],
                        "count": len(periods),
                        "active_period": tracker.get_active_period().to_dict() if tracker.get_active_period() else None,
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] FailSafe periods failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetsView(APIView):
    """
    Shadow Budget 목록 조회 API.

    GET /api/self-healing/reconciliation/shadow-budgets/

    Query Parameters:
    - limit: Maximum records (default: 50)
    - pending_only: Only pending approval (default: false)

    POST /api/self-healing/reconciliation/shadow-budgets/

    Trigger shadow budget calculation for a specific period.

    Request Body:
    {
        "period_id": "uuid"
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            limit = int(request.query_params.get("limit", 50))
            pending_only = request.query_params.get("pending_only", "false").lower() == "true"

            service = get_reconciliation_service()

            if pending_only:
                budgets = service.get_pending_shadow_budgets()
            else:
                budgets = service.get_all_shadow_budgets(limit=limit)

            return Response(
                {
                    "status": "success",
                    "data": {
                        "shadow_budgets": [sb.to_dict() for sb in budgets],
                        "count": len(budgets),
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budgets failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            period_id = request.data.get("period_id")
            if not period_id:
                return Response(
                    {"status": "error", "error": "period_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            service = get_reconciliation_service()
            shadow = service.calculate_shadow_budget(period_id)

            if not shadow:
                return Response(
                    {"status": "error", "error": "Failed to calculate shadow budget"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget calculation failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetDetailView(APIView):
    """
    Shadow Budget 상세 조회 및 승인/거부 API.

    GET /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/approve/

    Request Body:
    {
        "justification": "로그 확인 완료, 반영함"
    }

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/reject/

    Request Body:
    {
        "reason": "Chaos Engineering 실험 기간이므로 제외"
    }
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            shadow = service.get_shadow_budget(calculation_id)

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "status": "success",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget detail failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetApproveView(APIView):
    """
    Shadow Budget 승인 API.

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/approve/

    승인 시 Primary Budget에 반영됩니다 (Capped 모드: 최대 10%p/cycle).
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            justification = request.data.get("justification", "")
            if not justification:
                return Response(
                    {"status": "error", "error": "justification is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            approved_by = getattr(request.user, "username", str(request.user))

            service = get_reconciliation_service()
            shadow = service.approve_shadow_budget(
                calculation_id=calculation_id,
                approved_by=approved_by,
                justification=justification,
            )

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found or invalid status"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "message": f"Shadow budget approved and applied (adjustment: {shadow.adjustment_percent:.2f}%)",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget approve failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ShadowBudgetRejectView(APIView):
    """
    Shadow Budget 거부 API (Excluded Period로 처리).

    POST /api/self-healing/reconciliation/shadow-budgets/{calculation_id}/reject/
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request, calculation_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            reason = request.data.get("reason", "")
            if not reason:
                return Response(
                    {"status": "error", "error": "reason is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            rejected_by = getattr(request.user, "username", str(request.user))

            service = get_reconciliation_service()
            shadow = service.reject_shadow_budget(
                calculation_id=calculation_id,
                rejected_by=rejected_by,
                reason=reason,
            )

            if not shadow:
                return Response(
                    {"status": "error", "error": "Shadow budget not found or invalid status"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "status": "success",
                    "message": "Shadow budget rejected, period excluded from calculation",
                    "data": shadow.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Shadow budget reject failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ExcludedPeriodsView(APIView):
    """
    Excluded Period 관리 API.

    GET /api/self-healing/reconciliation/excluded-periods/

    Query Parameters:
    - limit: Maximum records (default: 50)

    POST /api/self-healing/reconciliation/excluded-periods/

    수동으로 기간을 Budget 계산에서 제외.

    Request Body:
    {
        "start": "2024-01-15T10:00:00Z",
        "end": "2024-01-15T10:30:00Z",
        "reason": "Chaos Engineering 실험",
        "notes": "optional notes"
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            limit = int(request.query_params.get("limit", 50))
            service = get_reconciliation_service()
            periods = service.get_excluded_periods(limit=limit)

            return Response(
                {
                    "status": "success",
                    "data": {
                        "excluded_periods": [p.to_dict() for p in periods],
                        "count": len(periods),
                    },
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Excluded periods failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request: Request) -> Response:
        try:
            from datetime import datetime
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            start_str = request.data.get("start")
            end_str = request.data.get("end")
            reason = request.data.get("reason", "")

            if not all([start_str, end_str, reason]):
                return Response(
                    {"status": "error", "error": "start, end, and reason are required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            notes = request.data.get("notes", "")
            excluded_by = getattr(request.user, "username", str(request.user))

            service = get_reconciliation_service()
            exclusion = service.exclude_period(
                start=start,
                end=end,
                reason=reason,
                excluded_by=excluded_by,
                notes=notes,
            )

            return Response(
                {
                    "status": "success",
                    "message": "Period excluded from budget calculation",
                    "data": exclusion.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Exclude period failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ExcludedPeriodDetailView(APIView):
    """
    Excluded Period 삭제 API.

    DELETE /api/self-healing/reconciliation/excluded-periods/{exclusion_id}/

    제외된 기간을 다시 Budget 계산에 포함시킵니다.
    """

    permission_classes = [IsAdminUser]

    def delete(self, request: Request, exclusion_id: str) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            success = service.remove_exclusion(exclusion_id)

            if not success:
                return Response(
                    {"status": "error", "error": "Exclusion not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(
                {
                    "status": "success",
                    "message": "Exclusion removed, period re-included in calculation",
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Remove exclusion failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReconciliationConfigView(APIView):
    """
    Reconciliation 설정 조회/변경 API.

    GET /api/self-healing/reconciliation/config/

    PUT /api/self-healing/reconciliation/config/

    Request Body:
    {
        "auto_calculate": true,
        "auto_apply": false,
        "apply_mode": "capped",
        "max_adjustment_percent_per_cycle": 10.0
    }
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            config = service.get_config()

            return Response(
                {
                    "status": "success",
                    "data": config.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Config get failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        try:
            from selfhealing.services.error_budget.reconciliation import get_reconciliation_service

            service = get_reconciliation_service()
            config = service.update_config(**request.data)

            return Response(
                {
                    "status": "success",
                    "message": "Reconciliation config updated",
                    "data": config.to_dict(),
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[ReconciliationAPI] Config update failed: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
