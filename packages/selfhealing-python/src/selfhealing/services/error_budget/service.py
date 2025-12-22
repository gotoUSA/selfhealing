"""
Integrated Error Budget Service

Calculator, Advisor, Recorder를 통합하여
Error Budget 관리를 위한 단일 진입점을 제공합니다.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from selfhealing.slo import SLOConfig
from selfhealing.services.error_budget.enums import OverrideType
from selfhealing.services.error_budget.models import (
    ErrorBudgetStatus,
    DeploymentVerdict,
    FreezeDecisionRecord,
)
from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder


class ErrorBudgetService:
    """
    통합 Error Budget 서비스.

    Calculator, Advisor, Recorder를 통합하여
    Error Budget 관리를 위한 단일 진입점을 제공합니다.
    """

    def __init__(
        self,
        slo_config: Optional[SLOConfig] = None,
        get_failed_operation_stats: Optional[Callable] = None,
        get_request_stats: Optional[Callable] = None,
        persist_record: Optional[Callable] = None,
        emit_metric: Optional[Callable] = None,
        emit_otel_event: Optional[Callable] = None,
    ):
        """초기화."""
        self.calculator = ErrorBudgetCalculator(
            slo_config=slo_config,
            get_failed_operation_stats=get_failed_operation_stats,
            get_request_stats=get_request_stats,
        )
        self.advisor = DeploymentPolicyAdvisor(calculator=self.calculator)
        self.recorder = FreezeDecisionRecorder(
            advisor=self.advisor,
            persist_record=persist_record,
            emit_metric=emit_metric,
            emit_otel_event=emit_otel_event,
        )

    def get_budget_status(self, slo_name: str = "availability") -> ErrorBudgetStatus:
        """Error Budget 상태 조회."""
        return self.calculator.calculate_budget_status(slo_name)

    def get_deployment_verdict(self, slo_name: str = "availability") -> DeploymentVerdict:
        """배포 가능 여부 판정."""
        return self.advisor.get_deployment_verdict(slo_name)

    def acknowledge_freeze(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """배포 동결 확정."""
        return self.recorder.record_freeze_acknowledged(decided_by, justification)

    def approve_override(
        self,
        decided_by: str,
        justification: str,
        override_type: OverrideType,
        deployment_id: Optional[str] = None,
        deployment_name: Optional[str] = None,
        expires_hours: int = 4,
    ) -> FreezeDecisionRecord:
        """배포 동결 무시 승인."""
        return self.recorder.record_override_approved(
            decided_by=decided_by,
            justification=justification,
            override_type=override_type,
            deployment_id=deployment_id,
            deployment_name=deployment_name,
            expires_hours=expires_hours,
        )

    def lift_freeze(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """배포 동결 해제."""
        return self.recorder.record_freeze_lifted(decided_by, justification)

    def get_decision_history(
        self,
        limit: int = 50,
        decision_type: Optional[str] = None,
    ) -> List[FreezeDecisionRecord]:
        """결정 이력 조회."""
        return self.recorder.get_decision_history(limit, decision_type)

    def check_active_override(self) -> Optional[FreezeDecisionRecord]:
        """활성 Override 확인."""
        return self.advisor.check_active_override()


# =============================================================================
# Factory Functions
# =============================================================================


_service_instance: Optional[ErrorBudgetService] = None


def get_error_budget_service() -> ErrorBudgetService:
    """
    ErrorBudgetService 싱글톤 인스턴스 반환.

    Returns:
        ErrorBudgetService 인스턴스
    """
    global _service_instance

    if _service_instance is None:
        # 기본 설정으로 생성
        # 실제 환경에서는 DI로 주입받거나 설정에서 로드
        _service_instance = ErrorBudgetService()

    return _service_instance


def configure_error_budget_service(
    slo_config: Optional[SLOConfig] = None,
    get_failed_operation_stats: Optional[Callable] = None,
    get_request_stats: Optional[Callable] = None,
    persist_record: Optional[Callable] = None,
    emit_metric: Optional[Callable] = None,
    emit_otel_event: Optional[Callable] = None,
) -> ErrorBudgetService:
    """
    ErrorBudgetService 설정 및 인스턴스 반환.

    애플리케이션 시작 시 호출하여 서비스를 설정합니다.

    Returns:
        설정된 ErrorBudgetService 인스턴스
    """
    global _service_instance

    _service_instance = ErrorBudgetService(
        slo_config=slo_config,
        get_failed_operation_stats=get_failed_operation_stats,
        get_request_stats=get_request_stats,
        persist_record=persist_record,
        emit_metric=emit_metric,
        emit_otel_event=emit_otel_event,
    )

    return _service_instance
