"""
Error Budget Service

SRE Error Budget 계산기 및 배포 정책 어드바이저.

Core Principle: "시스템은 조언하고, 결정은 사람이 한다."
이 모듈은 오직 '상태 선언'과 '권고 데이터'만 제공하며,
실제 CI/CD 차단 등의 강제 동작은 수행하지 않습니다.

Features:
- Error Budget 잔여량 계산 (SLO 기반)
- Burn Rate 계산 (Fast/Slow)
- 배포 동결 권고 (Freeze Advisor)
- 결정 기록 (Audit Trail)

Reference:
- docs/self_healing/08_OBSERVABILITY.md
- Google SRE Workbook - Alerting on SLOs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol

from selfhealing.core.timezone import now
from selfhealing.slo import SLO, SLOConfig, SLOStatus, SLI

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationRepository

logger = logging.getLogger(__name__)


# =============================================================================
# Enums & Constants
# =============================================================================


class FreezeStatus(str, Enum):
    """배포 동결 상태."""

    PROCEED = "proceed"
    """정상 - 배포 진행 가능."""

    CAUTION = "caution"
    """주의 - 경고 표시 후 진행 가능 (수동 확인 권장)."""

    WARNING = "warning"
    """경고 - 신규 기능 배포 자제, 안정화 우선."""

    FREEZE_RECOMMENDED = "freeze_recommended"
    """동결 권고 - 긴급 패치 외 모든 신규 배포 중단 권고."""


class OverrideType(str, Enum):
    """배포 동결 무시 유형."""

    HOTFIX = "hotfix"
    """긴급 버그 수정."""

    SECURITY_PATCH = "security_patch"
    """보안 패치."""

    EXECUTIVE_APPROVAL = "executive_approval"
    """경영진 승인."""

    ROLLBACK = "rollback"
    """롤백 배포."""


# =============================================================================
# Dynamic Threshold Getters (Runtime Config 참조)
# =============================================================================


def _get_error_budget_config() -> dict:
    """
    RuntimeConfigManager에서 Error Budget 설정을 가져옵니다.

    API로 동적 변경된 설정이 반영됩니다.
    """
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        return manager.get_error_budget_config()
    except Exception:
        # Fallback to defaults if runtime config unavailable
        return {}


def get_error_budget_thresholds() -> dict:
    """
    Error Budget 임계값을 가져옵니다 (동적 설정 지원).

    Returns:
        dict: {healthy, caution, warning, critical} 임계값 (%)
    """
    config = _get_error_budget_config()
    return {
        "healthy": config.get("threshold_healthy", 75.0),
        "caution": config.get("threshold_caution", 50.0),
        "warning": config.get("threshold_warning", 20.0),
        "critical": config.get("threshold_critical", 0.0),
    }


def get_burn_rate_thresholds() -> dict:
    """
    Burn Rate 임계값을 가져옵니다 (동적 설정 지원).

    Returns:
        dict: {fast_critical, fast_warning, slow_warning, slow_info} 임계값
    """
    config = _get_error_budget_config()
    return {
        "fast_critical": config.get("burn_rate_fast_critical", 14.4),
        "fast_warning": config.get("burn_rate_fast_warning", 6.0),
        "slow_warning": config.get("burn_rate_slow_warning", 3.0),
        "slow_info": config.get("burn_rate_slow_info", 1.0),
    }


# Legacy constants (for backward compatibility)
# 이 상수들은 더 이상 직접 사용하지 말고 get_*_thresholds() 함수를 사용하세요.
ERROR_BUDGET_THRESHOLDS = {
    "healthy": 75.0,
    "caution": 50.0,
    "warning": 20.0,
    "critical": 0.0,
}

BURN_RATE_THRESHOLDS = {
    "fast_critical": 14.4,
    "fast_warning": 6.0,
    "slow_warning": 3.0,
    "slow_info": 1.0,
}


# =============================================================================
# Fail-Safe Response Generators
# =============================================================================
#
# DESIGN PRINCIPLE: Fail-Open + Self-Reporting
# - Error Budget 시스템 장애 시 배포를 막는 것보다 허용하는 것이 더 안전함
# - CI/CD 파이프라인이 Error Budget 시스템 장애로 중단되면 안 됨
# - 운영자는 degraded_mode=True를 보고 수동 확인 필요성 인지
# - 🚨 중요: Fail-Safe 발동 시 즉시 알림 발송 (침묵하는 장애 방지)
#

# Fail-Safe 발동 횟수 추적 (Prometheus 메트릭용)
_failsafe_counter = 0


def _send_failsafe_alert(component: str, error_message: str, fallback_action: str) -> None:
    """
    Fail-Safe 발동 시 알림 발송.

    "침묵하는 장애" 방지를 위해 Fail-Safe가 작동하면
    즉시 운영팀에 알림을 보냅니다.
    """
    global _failsafe_counter
    _failsafe_counter += 1

    # 1. 로그 (항상 남김)
    logger.critical(
        f"[FAIL-SAFE] {component} 시스템 장애로 Fail-Safe 모드 전환. "
        f"Error: {error_message}, Fallback: {fallback_action}, "
        f"Count: {_failsafe_counter}"
    )

    # 2. AlertAdapter를 통한 알림 (설정된 경우)
    try:
        from selfhealing.adapters.alert import get_alert_adapter

        adapter = get_alert_adapter()
        if adapter is not None:
            adapter.alert_failsafe_activated(
                component=component,
                error_message=error_message,
                fallback_action=fallback_action,
            )
    except ImportError:
        # AlertAdapter가 설정되지 않은 경우 - 로그만 남김
        logger.warning("[FAIL-SAFE] AlertAdapter not configured, skipping alert")
    except Exception as alert_error:
        # 알림 발송 실패해도 Fail-Safe 응답은 반환해야 함
        logger.error(f"[FAIL-SAFE] Failed to send alert: {alert_error}")

    # 3. Prometheus 메트릭 증가 (가능한 경우)
    try:
        from selfhealing.services.metrics import record_failsafe_triggered

        record_failsafe_triggered(component=component)
    except ImportError:
        pass
    except Exception:
        pass


def get_failsafe_verdict_response(error_message: str) -> Dict[str, Any]:
    """
    Error Budget 시스템 장애 시 반환할 Fail-Safe 응답.

    Returns a PROCEED verdict to ensure deployments are not blocked
    when the Error Budget system is unavailable.

    🚨 IMPORTANT: 이 함수가 호출되면 자동으로 CRITICAL 알림이 발송됩니다.
    """
    # Fail-Safe 알림 발송 (침묵하는 장애 방지)
    _send_failsafe_alert(
        component="error_budget",
        error_message=error_message,
        fallback_action="PROCEED (배포 허용)",
    )

    return {
        "status": "degraded",  # 정상이 아님을 명시
        "data": {
            "verdict": {
                "status": FreezeStatus.PROCEED.value,  # Fail-open: 기본 허용
                "can_deploy": True,
                "requires_override": False,
                "has_active_override": False,
            },
            "message": "⚠️ Error Budget 시스템 일시적 오류. 기본값 PROCEED 적용됨.",
            "recommendation": "Error Budget 시스템 상태를 확인하세요. 현재 배포는 허용됩니다.",
            "reasons": [],
            "allowed_deployment_types": ["feature", "enhancement", "refactor", "hotfix", "security_patch", "rollback"],
        },
        "degraded_mode": True,
        "error": error_message,
        "failsafe_applied": True,
        "alert_sent": True,  # 알림이 발송되었음을 표시
        "timestamp": now().isoformat(),
    }


def get_failsafe_status_response(error_message: str) -> Dict[str, Any]:
    """
    Error Budget 상태 조회 실패 시 Fail-Safe 응답.

    Returns a healthy status to ensure systems do not falsely alarm
    when the Error Budget system is unavailable.

    🚨 IMPORTANT: 이 함수가 호출되면 자동으로 CRITICAL 알림이 발송됩니다.
    """
    # Fail-Safe 알림 발송 (침묵하는 장애 방지)
    _send_failsafe_alert(
        component="error_budget_status",
        error_message=error_message,
        fallback_action="HEALTHY 상태 가정",
    )

    return {
        "status": "degraded",
        "data": {
            "slo": {
                "name": "unknown",
                "target": 0.999,
                "target_percentage": "99.90%",
                "window_days": 30,
            },
            "budget": {
                "total_minutes": 43.2,
                "consumed_minutes": 0,
                "remaining_minutes": 43.2,
                "remaining_percent": 100.0,  # 알 수 없으면 full budget 가정
            },
            "burn_rate": {
                "rate_1h": 0.0,
                "rate_6h": 0.0,
                "is_fast_burn": False,
                "is_slow_burn": False,
            },
            "health": {
                "is_healthy": True,  # Fail-open: 건강하다고 가정
                "is_critical": False,
            },
        },
        "degraded_mode": True,
        "error": error_message,
        "failsafe_applied": True,
        "alert_sent": True,  # 알림이 발송되었음을 표시
        "timestamp": now().isoformat(),
    }


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ErrorBudgetStatus:
    """Error Budget 현재 상태."""

    # SLO 정보
    slo_name: str
    slo_target: float  # e.g., 0.999 (99.9%)
    window_days: int

    # Budget 상태
    budget_total_minutes: float
    budget_consumed_minutes: float
    budget_remaining_minutes: float
    budget_remaining_percent: float

    # Burn Rate
    burn_rate_1h: float = 0.0
    burn_rate_6h: float = 0.0

    # 측정 정보
    measured_at: datetime = field(default_factory=now)
    error_count_window: int = 0
    total_requests_window: int = 0

    @property
    def is_healthy(self) -> bool:
        """버짓이 건강한 상태인지."""
        thresholds = get_error_budget_thresholds()
        return self.budget_remaining_percent >= thresholds["healthy"]

    @property
    def is_over_budget(self) -> bool:
        """SLO 위반 상태인지 (버짓 100% 초과 소진)."""
        return self.budget_remaining_percent < 0

    @property
    def is_critical(self) -> bool:
        """버짓이 위험 상태인지."""
        thresholds = get_error_budget_thresholds()
        return self.budget_remaining_percent < thresholds["warning"]

    @property
    def has_fast_burn(self) -> bool:
        """빠른 소진이 발생 중인지."""
        thresholds = get_burn_rate_thresholds()
        return self.burn_rate_1h >= thresholds["fast_warning"]

    @property
    def has_slow_burn(self) -> bool:
        """느린 소진이 발생 중인지."""
        thresholds = get_burn_rate_thresholds()
        return self.burn_rate_6h >= thresholds["slow_warning"]

    def to_dict(self) -> Dict[str, Any]:
        """API 응답용 딕셔너리 변환."""
        return {
            "slo": {
                "name": self.slo_name,
                "target": self.slo_target,
                "target_percentage": f"{self.slo_target * 100:.2f}%",
                "window_days": self.window_days,
            },
            "budget": {
                "total_minutes": round(self.budget_total_minutes, 2),
                "consumed_minutes": round(self.budget_consumed_minutes, 2),
                "remaining_minutes": round(self.budget_remaining_minutes, 2),
                "remaining_percent": round(self.budget_remaining_percent, 2),
                "is_over_budget": self.is_over_budget,
            },
            "burn_rate": {
                "rate_1h": round(self.burn_rate_1h, 2),
                "rate_6h": round(self.burn_rate_6h, 2),
                "is_fast_burn": self.has_fast_burn,
                "is_slow_burn": self.has_slow_burn,
            },
            "health": {
                "is_healthy": self.is_healthy,
                "is_critical": self.is_critical,
                "is_over_budget": self.is_over_budget,
            },
            "metrics": {
                "error_count_window": self.error_count_window,
                "total_requests_window": self.total_requests_window,
            },
            "measured_at": self.measured_at.isoformat(),
        }


@dataclass
class DeploymentVerdict:
    """배포 정책 판정 결과."""

    status: FreezeStatus
    budget_status: ErrorBudgetStatus

    # 권고 메시지
    message: str
    recommendation: str

    # 상세 정보
    reasons: List[str] = field(default_factory=list)
    allowed_deployment_types: List[str] = field(default_factory=list)

    # 타임스탬프
    evaluated_at: datetime = field(default_factory=now)

    @property
    def can_deploy(self) -> bool:
        """배포 진행 가능 여부 (권고 기준)."""
        return self.status in (FreezeStatus.PROCEED, FreezeStatus.CAUTION)

    @property
    def requires_override(self) -> bool:
        """Override가 필요한 상태인지."""
        return self.status == FreezeStatus.FREEZE_RECOMMENDED

    def to_dict(self) -> Dict[str, Any]:
        """API 응답용 딕셔너리 변환."""
        return {
            "verdict": {
                "status": self.status.value,
                "can_deploy": self.can_deploy,
                "requires_override": self.requires_override,
            },
            "message": self.message,
            "recommendation": self.recommendation,
            "reasons": self.reasons,
            "allowed_deployment_types": self.allowed_deployment_types,
            "budget_status": self.budget_status.to_dict(),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass
class FreezeDecisionRecord:
    """배포 동결 결정 기록."""

    decision_id: str
    decision_type: str  # "freeze_acknowledged", "override_approved", "freeze_lifted"
    decided_by: str
    decided_at: datetime

    # 결정 당시 상태
    budget_remaining_percent: float
    freeze_status: FreezeStatus

    # 사유
    justification: str
    override_type: Optional[OverrideType] = None

    # 유효기간 (override의 경우)
    expires_at: Optional[datetime] = None

    # 관련 배포 정보
    deployment_id: Optional[str] = None
    deployment_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "decision_id": self.decision_id,
            "decision_type": self.decision_type,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "budget_remaining_percent": self.budget_remaining_percent,
            "freeze_status": self.freeze_status.value,
            "justification": self.justification,
            "override_type": self.override_type.value if self.override_type else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "deployment_id": self.deployment_id,
            "deployment_name": self.deployment_name,
        }


# =============================================================================
# Error Budget Calculator
# =============================================================================


class ErrorBudgetCalculator:
    """
    Error Budget 계산기.

    SLO 대비 현재 에러 버짓 소진량을 계산합니다.
    DLQ 유입량 및 장애 시간을 기반으로 계산합니다.
    """

    def __init__(
        self,
        slo_config: Optional[SLOConfig] = None,
        get_failed_operation_stats: Optional[Callable[..., Dict]] = None,
        get_request_stats: Optional[Callable[..., Dict]] = None,
    ):
        """
        초기화.

        Args:
            slo_config: SLO 설정. None이면 기본값 사용.
            get_failed_operation_stats: DLQ 통계 조회 함수
            get_request_stats: 요청 통계 조회 함수 (Prometheus 등)
        """
        self.slo_config = slo_config or SLOConfig.default_config()
        self._get_failed_operation_stats = get_failed_operation_stats
        self._get_request_stats = get_request_stats

    def calculate_budget_status(
        self,
        slo_name: str = "availability",
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> ErrorBudgetStatus:
        """
        Error Budget 상태 계산.

        Args:
            slo_name: SLO 이름
            window_start: 윈도우 시작 시간 (None이면 SLO window 사용)
            window_end: 윈도우 종료 시간 (None이면 현재)

        Returns:
            ErrorBudgetStatus
        """
        slo = self.slo_config.get_slo(slo_name)
        if not slo:
            # 기본 availability SLO 사용
            slo = SLO(
                name="availability",
                sli=SLI.AVAILABILITY,
                target=0.999,
                window_days=30,
            )

        current_time = window_end or now()
        if window_start is None:
            window_start = current_time - timedelta(days=slo.window_days)

        # Budget 총량 (분 단위)
        budget_total_minutes = slo.error_budget_minutes_per_window

        # 에러/요청 통계 조회
        error_count = 0
        total_requests = 0

        if self._get_failed_operation_stats:
            try:
                stats = self._get_failed_operation_stats(
                    start_time=window_start,
                    end_time=current_time,
                )
                error_count = stats.get("total_errors", 0)
            except Exception as e:
                logger.warning(f"[ErrorBudget] Failed to get error stats: {e}")

        if self._get_request_stats:
            try:
                stats = self._get_request_stats(
                    start_time=window_start,
                    end_time=current_time,
                )
                total_requests = stats.get("total_requests", 0)
            except Exception as e:
                logger.warning(f"[ErrorBudget] Failed to get request stats: {e}")

        # Budget 소진량 계산
        # 방법 1: DLQ 기반 (에러 건수 / 허용 에러)
        # Note: consumed_ratio는 1.0을 초과할 수 있음 (SLO 위반 시 음수 버짓)
        if total_requests > 0:
            error_rate = error_count / total_requests
            allowed_error_rate = slo.error_budget
            consumed_ratio = (error_rate / allowed_error_rate) if allowed_error_rate > 0 else 0
        else:
            # 요청 통계가 없으면 DLQ 건수 기반 추정
            # 예: 1000건당 1건 에러 허용 시 (99.9% SLO)
            estimated_total = max(error_count * 1000, 100000)  # 최소 100k 가정
            consumed_ratio = (error_count / estimated_total) / slo.error_budget

        budget_consumed_minutes = budget_total_minutes * consumed_ratio
        budget_remaining_minutes = budget_total_minutes - budget_consumed_minutes
        budget_remaining_percent = (
            (budget_remaining_minutes / budget_total_minutes * 100) if budget_total_minutes > 0 else 100.0
        )

        # Burn Rate 계산
        burn_rate_1h = self._calculate_burn_rate(
            slo=slo,
            window_hours=1,
            current_time=current_time,
        )
        burn_rate_6h = self._calculate_burn_rate(
            slo=slo,
            window_hours=6,
            current_time=current_time,
        )

        return ErrorBudgetStatus(
            slo_name=slo.name,
            slo_target=slo.target,
            window_days=slo.window_days,
            budget_total_minutes=budget_total_minutes,
            budget_consumed_minutes=budget_consumed_minutes,
            budget_remaining_minutes=budget_remaining_minutes,
            budget_remaining_percent=budget_remaining_percent,
            burn_rate_1h=burn_rate_1h,
            burn_rate_6h=burn_rate_6h,
            measured_at=current_time,
            error_count_window=error_count,
            total_requests_window=total_requests,
        )

    def _calculate_burn_rate(
        self,
        slo: SLO,
        window_hours: int,
        current_time: datetime,
    ) -> float:
        """
        특정 시간 윈도우의 Burn Rate 계산.

        Burn Rate = (실제 에러율 / 허용 에러율)

        예: Burn Rate 14.4 = 1시간에 에러 버짯 2% 소진 속도
        """
        window_start = current_time - timedelta(hours=window_hours)

        error_count = 0
        total_requests = 0

        if self._get_failed_operation_stats:
            try:
                stats = self._get_failed_operation_stats(
                    start_time=window_start,
                    end_time=current_time,
                )
                error_count = stats.get("total_errors", 0)
            except Exception:
                pass

        if self._get_request_stats:
            try:
                stats = self._get_request_stats(
                    start_time=window_start,
                    end_time=current_time,
                )
                total_requests = stats.get("total_requests", 0)
            except Exception:
                pass

        if total_requests == 0 or slo.error_budget == 0:
            return 0.0

        actual_error_rate = error_count / total_requests
        burn_rate = actual_error_rate / slo.error_budget

        return burn_rate


# =============================================================================
# Deployment Policy Advisor
# =============================================================================


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
        calculator: Optional[ErrorBudgetCalculator] = None,
    ):
        """
        초기화.

        Args:
            calculator: Error Budget 계산기
        """
        self.calculator = calculator or ErrorBudgetCalculator()

        # 활성 Override 목록
        self._active_overrides: Dict[str, FreezeDecisionRecord] = {}

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
    ) -> tuple[FreezeStatus, str, str, List[str]]:
        """상태 평가 및 메시지 생성."""
        reasons = []
        remaining = budget_status.budget_remaining_percent

        # 동적 임계값 가져오기
        eb_thresholds = get_error_budget_thresholds()
        br_thresholds = get_burn_rate_thresholds()

        # Fast Burn 체크 (최우선)
        if budget_status.burn_rate_1h >= br_thresholds["fast_critical"]:
            reasons.append(
                f"Fast Burn Rate 위험: {budget_status.burn_rate_1h:.1f}x " f"(임계값: {br_thresholds['fast_critical']}x)"
            )
            return (
                FreezeStatus.FREEZE_RECOMMENDED,
                "🔴 긴급: Error Budget이 급속히 소진되고 있습니다.",
                "즉시 원인 분석이 필요합니다. 모든 신규 배포를 중단하고 안정화에 집중하세요.",
                reasons,
            )

        # Budget 잔여량 기반 판정
        if remaining < eb_thresholds["warning"]:
            reasons.append(f"Error Budget 잔여량 위험: {remaining:.1f}% " f"(임계값: {eb_thresholds['warning']}%)")
            return (
                FreezeStatus.FREEZE_RECOMMENDED,
                "🔴 현재 에러 버짓이 소진되었습니다. 긴급 패치 외의 모든 신규 배포 중단을 권고합니다.",
                "비상 대응 모드로 전환하세요. 모든 리소스를 안정화 작업에 투입하세요.",
                reasons,
            )

        if remaining < eb_thresholds["caution"]:
            reasons.append(f"Error Budget 잔여량 경고: {remaining:.1f}% " f"(임계값: {eb_thresholds['caution']}%)")

            # Slow Burn 추가 체크
            if budget_status.has_slow_burn:
                reasons.append(f"Slow Burn Rate 감지: {budget_status.burn_rate_6h:.1f}x")

            return (
                FreezeStatus.WARNING,
                "🟠 Error Budget 경고 수준입니다. 신규 기능 배포를 자제해주세요.",
                "배포 동결을 고려하고, 기존 이슈 해결에 집중하세요.",
                reasons,
            )

        if remaining < eb_thresholds["healthy"]:
            reasons.append(f"Error Budget 주의: {remaining:.1f}% " f"(권장: {eb_thresholds['healthy']}% 이상)")
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

    def _get_allowed_deployment_types(self, status: FreezeStatus) -> List[str]:
        """상태별 허용 배포 유형."""
        if status == FreezeStatus.PROCEED:
            return ["feature", "enhancement", "refactor", "hotfix", "security_patch", "rollback"]
        elif status == FreezeStatus.CAUTION:
            return ["feature", "hotfix", "security_patch", "rollback"]
        elif status == FreezeStatus.WARNING:
            return ["hotfix", "security_patch", "rollback"]
        else:  # FREEZE_RECOMMENDED
            return ["security_patch", "rollback"]

    def check_active_override(self, deployment_id: Optional[str] = None) -> Optional[FreezeDecisionRecord]:
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


# =============================================================================
# Freeze Decision Recorder (Audit Trail)
# =============================================================================


class FreezeDecisionRecorder:
    """
    배포 동결 결정 기록기.

    배포 동결 확정, Override 승인 등의 결정을 Audit Trail에 기록합니다.
    """

    def __init__(
        self,
        advisor: Optional[DeploymentPolicyAdvisor] = None,
        persist_record: Optional[Callable[[FreezeDecisionRecord], None]] = None,
        emit_metric: Optional[Callable[[str, Dict], None]] = None,
        emit_otel_event: Optional[Callable[[str, Dict], None]] = None,
        alert_adapter: Optional[Any] = None,  # AlertAdapter for escalations
    ):
        """
        초기화.

        Args:
            advisor: 배포 정책 어드바이저
            persist_record: 기록 저장 함수
            emit_metric: 메트릭 발행 함수
            emit_otel_event: OpenTelemetry 이벤트 발행 함수
            alert_adapter: 알림 어댑터 (에스컬레이션용)
        """
        self.advisor = advisor or DeploymentPolicyAdvisor()
        self._persist_record = persist_record
        self._emit_metric = emit_metric
        self._emit_otel_event = emit_otel_event
        self._alert_adapter = alert_adapter

        # In-memory 기록 (영속화 함수가 없는 경우)
        self._records: List[FreezeDecisionRecord] = []

    def record_freeze_acknowledged(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 확정 기록.

        운영자가 동결 권고를 확인하고 동결을 확정할 때 호출.

        Args:
            decided_by: 결정자 (사용자명 또는 ID)
            justification: 결정 사유

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"freeze_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="freeze_acknowledged",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
        )

        self._save_and_emit(record)

        logger.info(
            f"[FreezeDecision] Freeze acknowledged by {decided_by}: "
            f"budget={verdict.budget_status.budget_remaining_percent:.1f}%"
        )

        return record

    def record_override_approved(
        self,
        decided_by: str,
        justification: str,
        override_type: OverrideType,
        deployment_id: Optional[str] = None,
        deployment_name: Optional[str] = None,
        expires_hours: int = 4,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 무시(Override) 승인 기록.

        운영자가 동결 권고를 무시하고 배포를 강행할 때 호출.
        에스컬레이션이 활성화된 경우, 상위 채널에 알림을 발송합니다.

        Args:
            decided_by: 결정자
            justification: 결정 사유
            override_type: Override 유형
            deployment_id: 배포 ID
            deployment_name: 배포 이름
            expires_hours: Override 유효 시간

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"override_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="override_approved",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
            override_type=override_type,
            expires_at=now() + timedelta(hours=expires_hours),
            deployment_id=deployment_id,
            deployment_name=deployment_name,
        )

        # Advisor에 활성 Override 등록
        self.advisor._active_overrides[record.decision_id] = record

        self._save_and_emit(record)

        # 에스컬레이션 알림 발송
        self._send_override_escalation(
            override_type=override_type,
            requester=decided_by,
            reason=justification,
            service_name=deployment_name,
        )

        logger.warning(
            f"[FreezeDecision] Override approved by {decided_by}: "
            f"type={override_type.value}, deployment={deployment_name}, "
            f"budget={verdict.budget_status.budget_remaining_percent:.1f}%"
        )

        return record

    def _send_override_escalation(
        self,
        override_type: OverrideType,
        requester: str,
        reason: str,
        service_name: Optional[str] = None,
    ) -> None:
        """
        Override 에스컬레이션 알림 발송.

        RuntimeConfig의 escalation_enabled가 True일 때만 발송합니다.
        """
        try:
            # 설정 확인
            config = _get_error_budget_config()
            if not config.get("escalation_enabled", True):
                logger.debug("[FreezeDecision] Escalation disabled, skipping")
                return

            escalation_channel = config.get("escalation_channel", "#governance")
            escalation_mention = config.get("escalation_mention", "@cto @security")

            # 메트릭 기록
            from selfhealing.services.metrics import record_override_escalation

            record_override_escalation(override_type.value)

            # AlertAdapter가 있으면 에스컬레이션 알림 발송
            if self._alert_adapter:
                self._alert_adapter.alert_override_escalation(
                    override_type=override_type.value,
                    requester=requester,
                    reason=reason,
                    service_name=service_name,
                    escalation_channel=escalation_channel,
                    escalation_mention=escalation_mention,
                )
                logger.info(
                    f"[FreezeDecision] Escalation alert sent: " f"type={override_type.value}, channel={escalation_channel}"
                )
            else:
                logger.warning(
                    f"[FreezeDecision] No alert adapter configured, "
                    f"escalation logged only: type={override_type.value}, "
                    f"requester={requester}, reason={reason}"
                )
        except Exception as e:
            # 에스컬레이션 실패는 Override 자체를 막지 않음
            logger.error(f"[FreezeDecision] Failed to send escalation: {e}")

    def record_freeze_lifted(
        self,
        decided_by: str,
        justification: str,
    ) -> FreezeDecisionRecord:
        """
        배포 동결 해제 기록.

        Error Budget이 회복되거나 운영자가 동결을 해제할 때 호출.

        Args:
            decided_by: 결정자
            justification: 해제 사유

        Returns:
            FreezeDecisionRecord
        """
        verdict = self.advisor.get_deployment_verdict()

        record = FreezeDecisionRecord(
            decision_id=f"lift_{now().strftime('%Y%m%d%H%M%S')}",
            decision_type="freeze_lifted",
            decided_by=decided_by,
            decided_at=now(),
            budget_remaining_percent=verdict.budget_status.budget_remaining_percent,
            freeze_status=verdict.status,
            justification=justification,
        )

        # 활성 Override 모두 해제
        self.advisor._active_overrides.clear()

        self._save_and_emit(record)

        logger.info(
            f"[FreezeDecision] Freeze lifted by {decided_by}: " f"budget={verdict.budget_status.budget_remaining_percent:.1f}%"
        )

        return record

    def get_decision_history(
        self,
        limit: int = 50,
        decision_type: Optional[str] = None,
    ) -> List[FreezeDecisionRecord]:
        """
        결정 이력 조회.

        Args:
            limit: 최대 조회 건수
            decision_type: 결정 유형 필터

        Returns:
            결정 기록 목록
        """
        records = self._records

        if decision_type:
            records = [r for r in records if r.decision_type == decision_type]

        return sorted(records, key=lambda r: r.decided_at, reverse=True)[:limit]

    def _save_and_emit(self, record: FreezeDecisionRecord) -> None:
        """기록 저장 및 이벤트 발행."""
        # In-memory 저장
        self._records.append(record)

        # 영속화
        if self._persist_record:
            try:
                self._persist_record(record)
            except Exception as e:
                logger.error(f"[FreezeDecision] Failed to persist record: {e}")

        # 메트릭 발행
        if self._emit_metric:
            try:
                self._emit_metric(
                    "freeze_decision",
                    {
                        "decision_type": record.decision_type,
                        "freeze_status": record.freeze_status.value,
                        "budget_remaining": record.budget_remaining_percent,
                    },
                )
            except Exception as e:
                logger.warning(f"[FreezeDecision] Failed to emit metric: {e}")

        # OpenTelemetry 이벤트 발행
        if self._emit_otel_event:
            try:
                self._emit_otel_event(
                    f"selfhealing.deployment.{record.decision_type}",
                    record.to_dict(),
                )
            except Exception as e:
                logger.warning(f"[FreezeDecision] Failed to emit OTel event: {e}")


# =============================================================================
# Integrated Error Budget Service
# =============================================================================


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
# Factory Function
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
