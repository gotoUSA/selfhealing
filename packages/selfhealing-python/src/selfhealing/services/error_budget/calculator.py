"""
Error Budget Calculator

SLO 대비 현재 에러 버짓 소진량을 계산합니다.
DLQ 유입량 및 장애 시간을 기반으로 계산합니다.

합성 요청(X-Test-Mode, Chaos 실험) 필터링:
- exclude_synthetic=True 시 합성 에러를 에러 버짓에서 제외
- exclude_chaos는 deprecated되어 exclude_synthetic으로 통합됨
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from selfhealing.core.timezone import now
from selfhealing.slo import SLO, SLOConfig, SLI
from selfhealing.services.error_budget.models import ErrorBudgetStatus


logger = logging.getLogger(__name__)


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
        exclude_chaos: Optional[bool] = None,
        exclude_synthetic: bool = True,
    ) -> ErrorBudgetStatus:
        """
        Error Budget 상태 계산.

        Args:
            slo_name: SLO 이름
            window_start: 윈도우 시작 시간 (None이면 SLO window 사용)
            window_end: 윈도우 종료 시간 (None이면 현재)
            exclude_chaos: (deprecated) exclude_synthetic 사용 권장.
                           None이 아니면 exclude_synthetic으로 해석됨.
            exclude_synthetic: 합성 트래픽(Chaos + X-Test) 제외 여부 (기본: True)
                               True이면 is_chaos_experiment=True 또는 
                               source="x-test-mode"인 에러는 예산 소진에서 제외됨

        Returns:
            ErrorBudgetStatus
        """
        # exclude_chaos deprecated 처리
        if exclude_chaos is not None:
            warnings.warn(
                "exclude_chaos is deprecated, use exclude_synthetic instead. "
                "exclude_synthetic covers both Chaos and X-Test-Mode traffic.",
                DeprecationWarning,
                stacklevel=2,
            )
            exclude_synthetic = exclude_chaos

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
                # exclude_synthetic 파라미터 우선 시도
                stats = self._get_failed_operation_stats(
                    start_time=window_start,
                    end_time=current_time,
                    exclude_synthetic=exclude_synthetic,
                )
                error_count = stats.get("total_errors", 0)
            except TypeError:
                # 이전 버전 호환: exclude_synthetic/exclude_chaos 미지원 시
                try:
                    stats = self._get_failed_operation_stats(
                        start_time=window_start,
                        end_time=current_time,
                        exclude_chaos=exclude_synthetic,
                    )
                    error_count = stats.get("total_errors", 0)
                except TypeError:
                    # 파라미터 없는 버전
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
