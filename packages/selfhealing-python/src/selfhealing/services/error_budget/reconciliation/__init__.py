"""
Error Budget Reconciliation Package.

Circuit Breaker Fail-Safe 기간 동안 누락된 에러 소진량을 사후 정산하여
Shadow Budget을 계산하고, 승인 시 Primary Budget에 반영합니다.

Modules:
    - enums: ReconciliationStatus, ApplyMode
    - models: FailSafePeriod, ShadowBudget, ExcludedPeriod, ReconciliationConfig
    - period_tracker: FailSafePeriodTracker
    - shadow_calculator: ShadowBudgetCalculator
    - service: ErrorBudgetReconciliationService

Usage:
    from selfhealing.services.error_budget.reconciliation import (
        get_reconciliation_service,
        configure_reconciliation_service,
        ReconciliationStatus,
        ApplyMode,
    )

    # Simple usage
    service = get_reconciliation_service()

    # Full configuration
    service = configure_reconciliation_service(
        get_prometheus_errors=my_prometheus_func,
        apply_adjustment=my_apply_func,
    )
"""

from __future__ import annotations

from collections.abc import Callable

# Enums
from .enums import ApplyMode, ReconciliationStatus

# Models
from .models import (
    ExcludedPeriod,
    FailSafePeriod,
    ReconciliationConfig,
    ShadowBudget,
)

# Classes
from .period_tracker import FailSafePeriodTracker
from .service import ErrorBudgetReconciliationService
from .shadow_calculator import ShadowBudgetCalculator

__all__ = [
    # Enums
    "ReconciliationStatus",
    "ApplyMode",
    # Models
    "FailSafePeriod",
    "ShadowBudget",
    "ExcludedPeriod",
    "ReconciliationConfig",
    # Classes
    "FailSafePeriodTracker",
    "ShadowBudgetCalculator",
    "ErrorBudgetReconciliationService",
    # Singleton functions
    "get_period_tracker",
    "get_reconciliation_service",
    "configure_reconciliation_service",
]


# =============================================================================
# Singleton Factory
# =============================================================================


_reconciliation_service: ErrorBudgetReconciliationService | None = None
_period_tracker: FailSafePeriodTracker | None = None


def get_period_tracker() -> FailSafePeriodTracker:
    """FailSafePeriodTracker 싱글톤 인스턴스 반환."""
    global _period_tracker
    if _period_tracker is None:
        _period_tracker = FailSafePeriodTracker()
    return _period_tracker


def get_reconciliation_service() -> ErrorBudgetReconciliationService:
    """ErrorBudgetReconciliationService 싱글톤 인스턴스 반환."""
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = ErrorBudgetReconciliationService(
            period_tracker=get_period_tracker(),
        )
    return _reconciliation_service


def configure_reconciliation_service(
    config: ReconciliationConfig | None = None,
    get_error_logs: Callable | None = None,
    get_prometheus_errors: Callable | None = None,
    get_dlq_entries: Callable | None = None,
    get_current_budget: Callable | None = None,
    apply_adjustment: Callable | None = None,
) -> ErrorBudgetReconciliationService:
    """
    Reconciliation 서비스 설정.

    Args:
        config: Reconciliation 설정
        get_error_logs: 애플리케이션 로그에서 에러 조회 함수
        get_prometheus_errors: Prometheus에서 에러 카운트 조회 함수
        get_dlq_entries: DLQ 엔트리 수 조회 함수
        get_current_budget: 현재 Budget 상태 조회 함수
        apply_adjustment: Primary Budget에 조정 적용 함수

    Returns:
        ErrorBudgetReconciliationService 인스턴스
    """
    global _reconciliation_service, _period_tracker

    if _period_tracker is None:
        _period_tracker = FailSafePeriodTracker()

    shadow_calculator = ShadowBudgetCalculator(
        get_error_logs=get_error_logs,
        get_prometheus_errors=get_prometheus_errors,
        get_dlq_entries=get_dlq_entries,
    )

    _reconciliation_service = ErrorBudgetReconciliationService(
        config=config or ReconciliationConfig(),
        period_tracker=_period_tracker,
        shadow_calculator=shadow_calculator,
        get_current_budget=get_current_budget,
        apply_adjustment=apply_adjustment,
    )

    return _reconciliation_service
