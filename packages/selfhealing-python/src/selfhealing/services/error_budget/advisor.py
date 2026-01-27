"""
Deployment Policy Advisor

Error Budget 상태를 기반으로 배포 가능 여부를 판정하고,
권고 사항을 생성합니다.

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
실제 배포 차단은 수행하지 않으며, 권고만 제공합니다.
"""

from __future__ import annotations

from selfhealing.core.timezone import now
from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
from selfhealing.services.error_budget.enums import (
    FreezeStatus,
    get_burn_rate_thresholds,
    get_error_budget_thresholds,
)
from selfhealing.services.error_budget.models import (
    DeploymentVerdict,
    ErrorBudgetStatus,
    FreezeDecisionRecord,
)


class DeploymentPolicyAdvisor:
    """
    배포 정책 어드바이저.

    Error Budget 상태를 기반으로 배포 가능 여부를 판정하고,
    권고 사항을 생성합니다.

    Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
    실제 배포 차단은 수행하지 않으며, 권고만 제공합니다.
    """

    def __init__(
        self,
        calculator: ErrorBudgetCalculator | None = None,
    ):
        """
        초기화.

        Args:
            calculator: Error Budget 계산기
        """
        self.calculator = calculator or ErrorBudgetCalculator()

        # 활성 Override 목록
        self._active_overrides: dict[str, FreezeDecisionRecord] = {}

    def get_deployment_verdict(
        self,
        slo_name: str = "availability",
    ) -> DeploymentVerdict:
        """
        배포 가능 여부 판정.

        Args:
            slo_name: 평가할 SLO 이름

        Returns:
            DeploymentVerdict
        """
        budget_status = self.calculator.calculate_budget_status(slo_name)

        # 상태 결정
        status, message, recommendation, reasons = self._evaluate_status(budget_status)

        # 허용되는 배포 유형 결정
        allowed_types = self._get_allowed_deployment_types(status)

        return DeploymentVerdict(
            status=status,
            budget_status=budget_status,
            message=message,
            recommendation=recommendation,
            reasons=reasons,
            allowed_deployment_types=allowed_types,
        )

    def _evaluate_status(
        self,
        budget_status: ErrorBudgetStatus,
    ) -> tuple[FreezeStatus, str, str, list[str]]:
        """상태 평가 및 메시지 생성."""
        reasons = []
        remaining = budget_status.budget_remaining_percent

        # 동적 임계값 가져오기
        eb_thresholds = get_error_budget_thresholds()
        br_thresholds = get_burn_rate_thresholds()

        # Fast Burn 체크 (최우선)
        if budget_status.burn_rate_1h >= br_thresholds["fast_critical"]:
            reasons.append(
                f"Fast Burn Rate 위험: {budget_status.burn_rate_1h:.1f}x "
                f"(임계값: {br_thresholds['fast_critical']}x)"
            )
            return (
                FreezeStatus.FREEZE_RECOMMENDED,
                "🔴 긴급: Error Budget이 급속히 소진되고 있습니다.",
                "즉시 원인 분석이 필요합니다. 모든 신규 배포를 중단하고 안정화에 집중하세요.",
                reasons,
            )

        # Budget 잔여량 기반 판정
        if remaining < eb_thresholds["warning"]:
            reasons.append(
                f"Error Budget 잔여량 위험: {remaining:.1f}% "
                f"(임계값: {eb_thresholds['warning']}%)"
            )
            return (
                FreezeStatus.FREEZE_RECOMMENDED,
                "🔴 현재 에러 버짓이 소진되었습니다. 긴급 패치 외의 모든 신규 배포 중단을 권고합니다.",
                "비상 대응 모드로 전환하세요. 모든 리소스를 안정화 작업에 투입하세요.",
                reasons,
            )

        if remaining < eb_thresholds["caution"]:
            reasons.append(
                f"Error Budget 잔여량 경고: {remaining:.1f}% "
                f"(임계값: {eb_thresholds['caution']}%)"
            )

            # Slow Burn 추가 체크
            if budget_status.has_slow_burn:
                reasons.append(
                    f"Slow Burn Rate 감지: {budget_status.burn_rate_6h:.1f}x"
                )

            return (
                FreezeStatus.WARNING,
                "🟠 Error Budget 경고 수준입니다. 신규 기능 배포를 자제해주세요.",
                "배포 동결을 고려하고, 기존 이슈 해결에 집중하세요.",
                reasons,
            )

        if remaining < eb_thresholds["healthy"]:
            reasons.append(
                f"Error Budget 주의: {remaining:.1f}% "
                f"(권장: {eb_thresholds['healthy']}% 이상)"
            )
            return (
                FreezeStatus.CAUTION,
                "🟡 Error Budget 주의 수준입니다. 배포 시 주의가 필요합니다.",
                "신규 배포 전 충분한 테스트와 점진적 롤아웃을 권장합니다.",
                reasons,
            )

        return (
            FreezeStatus.PROCEED,
            "🟢 Error Budget 정상 수준입니다. 일반 개발을 진행할 수 있습니다.",
            "정상적인 개발 및 배포를 진행하세요.",
            reasons,
        )

    def _get_allowed_deployment_types(self, status: FreezeStatus) -> list[str]:
        """상태별 허용 배포 유형."""
        if status == FreezeStatus.PROCEED:
            return [
                "feature",
                "enhancement",
                "refactor",
                "hotfix",
                "security_patch",
                "rollback",
            ]
        elif status == FreezeStatus.CAUTION:
            return ["feature", "hotfix", "security_patch", "rollback"]
        elif status == FreezeStatus.WARNING:
            return ["hotfix", "security_patch", "rollback"]
        else:  # FREEZE_RECOMMENDED
            return ["security_patch", "rollback"]

    def check_active_override(
        self, deployment_id: str | None = None
    ) -> FreezeDecisionRecord | None:
        """
        활성 Override 확인.

        Args:
            deployment_id: 특정 배포 ID (None이면 전체 체크)

        Returns:
            활성 Override가 있으면 해당 레코드, 없으면 None
        """
        current_time = now()

        for override_id, record in list(self._active_overrides.items()):
            # 만료 체크
            if record.expires_at and record.expires_at < current_time:
                del self._active_overrides[override_id]
                continue

            # 특정 배포 ID 체크
            if deployment_id and record.deployment_id != deployment_id:
                continue

            return record

        return None
