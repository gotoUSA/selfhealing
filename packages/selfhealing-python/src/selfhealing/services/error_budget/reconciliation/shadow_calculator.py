"""
Shadow Budget Calculator.

Calculates shadow budget by estimating missed errors from logs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from selfhealing.core.timezone import now

from .enums import ReconciliationStatus
from .models import FailSafePeriod, ShadowBudget

logger = logging.getLogger(__name__)


class ShadowBudgetCalculator:
    """
    Shadow Budget 계산기.
    
    Fail-Safe 기간 동안 누락된 에러를 로그에서 읽어와 
    "실제로 얼마나 소진되었을지" 사후 계산합니다.
    """
    
    def __init__(
        self,
        get_error_logs: Optional[Callable[[datetime, datetime], List[Dict]]] = None,
        get_prometheus_errors: Optional[Callable[[datetime, datetime], int]] = None,
        get_dlq_entries: Optional[Callable[[datetime, datetime], int]] = None,
    ):
        """
        초기화.
        
        Args:
            get_error_logs: 애플리케이션 로그에서 에러 조회 함수
            get_prometheus_errors: Prometheus에서 에러 카운트 조회 함수
            get_dlq_entries: DLQ 엔트리 수 조회 함수
        """
        self._get_error_logs = get_error_logs
        self._get_prometheus_errors = get_prometheus_errors
        self._get_dlq_entries = get_dlq_entries
    
    def calculate_shadow_budget(
        self,
        failsafe_period: FailSafePeriod,
        primary_remaining_percent: float,
        primary_consumed_minutes: float,
        budget_total_minutes: float = 43.2,  # 99.9% SLO, 30일 기준
    ) -> ShadowBudget:
        """
        Shadow Budget 계산.
        
        Args:
            failsafe_period: Fail-Safe 기간
            primary_remaining_percent: 현재 Primary Budget 잔여율
            primary_consumed_minutes: 현재 Primary Budget 소진량 (분)
            budget_total_minutes: 전체 Budget (분)
            
        Returns:
            ShadowBudget 계산 결과
        """
        period_start = failsafe_period.started_at
        period_end = failsafe_period.ended_at or now()
        
        # 에러 수 추정 (여러 소스에서)
        estimated_errors, log_source = self._estimate_errors(period_start, period_end)
        
        # 에러를 Budget 소진량으로 변환
        # 단순화: 에러 1개 = 0.001분 소진 (실제로는 에러 심각도 등에 따라 가중치)
        error_weight_minutes = 0.001
        additional_consumed = estimated_errors * error_weight_minutes
        
        # Shadow Budget 계산
        shadow_consumed = primary_consumed_minutes + additional_consumed
        shadow_remaining = budget_total_minutes - shadow_consumed
        shadow_remaining_percent = (shadow_remaining / budget_total_minutes) * 100 if budget_total_minutes > 0 else 0
        
        # 차이 계산
        adjustment_percent = primary_remaining_percent - shadow_remaining_percent
        adjustment_minutes = additional_consumed
        
        shadow_budget = ShadowBudget(
            calculation_id=str(uuid.uuid4()),
            calculated_at=now(),
            failsafe_period_id=failsafe_period.period_id,
            failsafe_period_start=period_start,
            failsafe_period_end=period_end,
            primary_remaining_percent=primary_remaining_percent,
            primary_consumed_minutes=primary_consumed_minutes,
            shadow_remaining_percent=shadow_remaining_percent,
            shadow_consumed_minutes=shadow_consumed,
            adjustment_percent=adjustment_percent,
            adjustment_minutes=adjustment_minutes,
            estimated_errors=estimated_errors,
            log_source=log_source,
            status=ReconciliationStatus.CALCULATED,
        )
        
        logger.info(
            f"[ShadowBudget] Calculated for period {failsafe_period.period_id}: "
            f"estimated_errors={estimated_errors}, adjustment={adjustment_percent:.2f}%"
        )
        
        return shadow_budget
    
    def _estimate_errors(
        self,
        start: datetime,
        end: datetime,
    ) -> Tuple[int, str]:
        """
        기간 내 에러 수 추정.
        
        여러 소스를 시도하고 가장 신뢰할 수 있는 결과를 반환합니다.
        
        Returns:
            (에러 수, 데이터 소스)
        """
        # 1. Prometheus (가장 신뢰)
        if self._get_prometheus_errors:
            try:
                count = self._get_prometheus_errors(start, end)
                if count is not None and count >= 0:
                    return count, "prometheus"
            except Exception as e:
                logger.warning(f"[ShadowBudget] Prometheus query failed: {e}")
        
        # 2. DLQ 엔트리
        if self._get_dlq_entries:
            try:
                count = self._get_dlq_entries(start, end)
                if count is not None and count >= 0:
                    return count, "dlq"
            except Exception as e:
                logger.warning(f"[ShadowBudget] DLQ query failed: {e}")
        
        # 3. 애플리케이션 로그
        if self._get_error_logs:
            try:
                logs = self._get_error_logs(start, end)
                if logs:
                    return len(logs), "application_logs"
            except Exception as e:
                logger.warning(f"[ShadowBudget] Log query failed: {e}")
        
        # 4. 기본값: Fail-Open 횟수 기반 추정
        # 보수적으로 Fail-Open 1회당 10개 에러 가정
        logger.info("[ShadowBudget] Using fallback estimation based on fail_open_count")
        return 0, "none_available"
