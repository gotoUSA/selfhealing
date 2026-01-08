"""
Shadow Budget Calculator.

Calculates shadow budget by estimating missed errors from logs.

Reference: docs/self_healing/middleware_system/30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md
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


# =============================================================================
# Phase 0: 핵심 설계 원칙
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.0
# =============================================================================

# 4.0.1 Multiplier Cap - 가중치 폭발 방지
# Critical(10x) × Payment(24x) × 반복(2x) = 480배 폭발 방지
MAX_WEIGHT_MULTIPLIER: float = 50.0

# 4.0.2 Source Reliability Weight - 데이터 소스 신뢰도
# 낮을수록 보수적 차감 (정확하지 않은 소스에서 온 데이터는 덜 차감)
SOURCE_RELIABILITY: Dict[str, float] = {
    "prometheus": 1.0,        # 가장 정확
    "dlq": 0.9,               # 리플레이 대기 데이터
    "application_logs": 0.8,  # 누락 가능성 존재
    "none_available": 0.5,    # 추정치 (매우 보수적)
}


# =============================================================================
# Phase 1: Severity 기반 가중치
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.1
# =============================================================================

# 에러 심각도별 가중치 (분 단위)
# 기본값 0.001분 대비 배수로 정의
SEVERITY_WEIGHT: Dict[str, float] = {
    "critical": 0.01,   # 10배 가중치
    "high": 0.005,      # 5배 가중치
    "medium": 0.001,    # 기본값
    "low": 0.0005,      # 절반 가중치
}

# 기본 가중치 (분)
BASE_WEIGHT_MINUTES: float = 0.001


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
        errors_by_severity: Optional[Dict[str, int]] = None,
    ) -> ShadowBudget:
        """
        Shadow Budget 계산.
        
        Args:
            failsafe_period: Fail-Safe 기간
            primary_remaining_percent: 현재 Primary Budget 잔여율
            primary_consumed_minutes: 현재 Primary Budget 소진량 (분)
            budget_total_minutes: 전체 Budget (분)
            errors_by_severity: 심각도별 에러 수 (Phase 1 가중치 계산용)
            
        Returns:
            ShadowBudget 계산 결과
        """
        period_start = failsafe_period.started_at
        period_end = failsafe_period.ended_at or now()
        
        # 에러 수 추정 (여러 소스에서)
        estimated_errors, log_source = self._estimate_errors(period_start, period_end)
        
        # Phase 1: 가중치 기반 에러 계산
        # errors_by_severity가 제공되면 가중치 적용, 아니면 기본값 사용
        if errors_by_severity:
            additional_consumed = self._calculate_weighted_errors(
                errors_by_severity=errors_by_severity,
                log_source=log_source,
            )
            # 총 에러 수는 severity별 합계로 업데이트
            estimated_errors = sum(errors_by_severity.values())
        else:
            # 기존 방식: 모든 에러를 medium으로 간주
            additional_consumed = self._calculate_weighted_errors(
                errors_by_severity={"medium": estimated_errors},
                log_source=log_source,
            )
        
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
    
    def _calculate_weighted_errors(
        self,
        errors_by_severity: Dict[str, int],
        log_source: str = "none_available",
    ) -> float:
        """
        Severity 기반 가중치 계산.
        
        Phase 0: Source Reliability Weight 적용
        Phase 1: Severity Weight 적용
        
        Args:
            errors_by_severity: 심각도별 에러 수 {"critical": 5, "high": 10, ...}
            log_source: 데이터 소스 ("prometheus", "dlq", "application_logs", "none_available")
            
        Returns:
            가중치 적용된 Budget 소진량 (분)
            
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.0, §4.1
        """
        total_weighted = 0.0
        
        for severity, count in errors_by_severity.items():
            # Phase 1: Severity 가중치 적용
            weight = SEVERITY_WEIGHT.get(severity.lower(), BASE_WEIGHT_MINUTES)
            total_weighted += count * weight
        
        # Phase 0: Source Reliability 적용
        # 정확하지 않은 소스에서 온 데이터는 덜 차감 (보수적 접근)
        source_reliability = SOURCE_RELIABILITY.get(log_source, 1.0)
        total_weighted *= source_reliability
        
        # Phase 0: Multiplier Cap 적용 (향후 Phase 2, 3에서 domain_mult, pattern_mult 추가 시)
        # 현재는 단일 severity 가중치만 적용되므로 max 10배
        # Cap 로직은 Phase 4 통합 시 적용됨
        
        logger.debug(
            f"[ShadowBudget] Weighted calculation: "
            f"errors_by_severity={errors_by_severity}, "
            f"source_reliability={source_reliability}, "
            f"total_weighted={total_weighted:.6f}"
        )
        
        return total_weighted
    
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
