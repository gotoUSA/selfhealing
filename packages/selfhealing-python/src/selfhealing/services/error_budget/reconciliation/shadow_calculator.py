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

# =============================================================================
# Phase 2: Domain SLA 기반 가중치
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.2
# =============================================================================

# 기본 SLA 시간 (도메인이 설정되지 않은 경우)
DEFAULT_SLA_HOURS: int = 24


# =============================================================================
# Phase 3: Learning 패턴 기반 가중치
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.3
# =============================================================================

# 패턴 발생 횟수별 가중치 배수
PATTERN_OCCURRENCE_WEIGHT: Dict[str, float] = {
    "high": 2.0,      # 10회 이상 → 2배
    "medium": 1.5,    # 5회 이상 → 1.5배
    "low": 1.2,       # 3회 이상 → 1.2배
    "none": 1.0,      # 3회 미만 → 1배
}


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
        domain: str = "unknown",
        failure_type: str = "unknown",
    ) -> ShadowBudget:
        """
        Shadow Budget 계산.
        
        Args:
            failsafe_period: Fail-Safe 기간
            primary_remaining_percent: 현재 Primary Budget 잔여율
            primary_consumed_minutes: 현재 Primary Budget 소진량 (분)
            budget_total_minutes: 전체 Budget (분)
            errors_by_severity: 심각도별 에러 수 (Phase 1 가중치 계산용)
            domain: 도메인 (Phase 2 가중치 계산용, 예: "payment", "order")
            failure_type: 실패 유형 (Phase 3 가중치 계산용, 예: "timeout")
            
        Returns:
            ShadowBudget 계산 결과
        """
        period_start = failsafe_period.started_at
        period_end = failsafe_period.ended_at or now()
        
        # 에러 수 추정 (여러 소스에서)
        estimated_errors, log_source = self._estimate_errors(period_start, period_end)
        
        # Phase 1~3: 가중치 기반 에러 계산
        # errors_by_severity가 제공되면 가중치 적용, 아니면 기본값 사용
        if errors_by_severity:
            additional_consumed = self._calculate_weighted_errors(
                errors_by_severity=errors_by_severity,
                log_source=log_source,
                domain=domain,
                failure_type=failure_type,
            )
            # 총 에러 수는 severity별 합계로 업데이트
            estimated_errors = sum(errors_by_severity.values())
        else:
            # 기존 방식: 모든 에러를 medium으로 간주
            additional_consumed = self._calculate_weighted_errors(
                errors_by_severity={"medium": estimated_errors},
                log_source=log_source,
                domain=domain,
                failure_type=failure_type,
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
        domain: str = "unknown",
        failure_type: str = "unknown",
    ) -> float:
        """
        가중치 기반 Budget 소진량 계산.
        
        Phase 0: Source Reliability Weight 적용
        Phase 1: Severity Weight 적용
        Phase 2: Domain SLA Weight 적용
        Phase 3: Learning Pattern Weight 적용
        
        Args:
            errors_by_severity: 심각도별 에러 수 {"critical": 5, "high": 10, ...}
            log_source: 데이터 소스 ("prometheus", "dlq", "application_logs", "none_available")
            domain: 도메인 (예: "payment", "order")
            failure_type: 실패 유형 (예: "timeout", "validation_error")
            
        Returns:
            가중치 적용된 Budget 소진량 (분)
            
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.0, §4.1, §4.2, §4.3
        """
        total_weighted = 0.0
        
        for severity, count in errors_by_severity.items():
            # Phase 1: Severity 가중치 적용
            severity_weight = SEVERITY_WEIGHT.get(severity.lower(), BASE_WEIGHT_MINUTES)
            total_weighted += count * severity_weight
        
        # Phase 0: Source Reliability 적용
        source_reliability = SOURCE_RELIABILITY.get(log_source, 1.0)
        
        # Phase 2: Domain SLA 가중치 적용
        domain_multiplier = self._get_domain_weight(domain)
        
        # Phase 3: Learning 패턴 가중치 적용
        pattern_multiplier = self._get_pattern_weight(domain, failure_type)
        
        # 최종 가중치 계산 (Cap 적용)
        final_multiplier = source_reliability * domain_multiplier * pattern_multiplier
        final_multiplier = min(final_multiplier, MAX_WEIGHT_MULTIPLIER)  # Phase 0: Cap 적용
        
        total_weighted *= final_multiplier
        
        logger.debug(
            f"[ShadowBudget] Weighted calculation: "
            f"errors_by_severity={errors_by_severity}, "
            f"source_reliability={source_reliability}, "
            f"domain_mult={domain_multiplier:.2f}, "
            f"pattern_mult={pattern_multiplier:.2f}, "
            f"final_mult={final_multiplier:.2f}, "
            f"total_weighted={total_weighted:.6f}"
        )
        
        return total_weighted
    
    def _get_domain_weight(self, domain: str) -> float:
        """
        Domain SLA 기반 가중치 계산.
        
        SLA가 짧을수록 해당 도메인이 더 중요함 → 역수 가중치 적용.
        예: payment(1h SLA) → 24배, notification(24h SLA) → 1배
        
        Args:
            domain: 도메인 이름 (예: "payment", "order", "notification")
            
        Returns:
            도메인 가중치 배수 (1.0 ~ 24.0)
            
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.2
        """
        try:
            from selfhealing.settings import get_config
            
            sla_config = get_config().sla
            domain_hours = sla_config.thresholds_by_domain.get(
                domain.lower(), DEFAULT_SLA_HOURS
            )
            
            # SLA가 짧을수록 더 중요 → 역수 가중치
            # 0으로 나누기 방지
            if domain_hours <= 0:
                domain_hours = DEFAULT_SLA_HOURS
            
            weight = float(DEFAULT_SLA_HOURS) / domain_hours
            
            logger.debug(
                f"[ShadowBudget] Domain weight: domain={domain}, "
                f"sla_hours={domain_hours}, weight={weight:.2f}"
            )
            
            return weight
            
        except Exception as e:
            logger.warning(f"[ShadowBudget] Failed to get domain weight: {e}")
            return 1.0  # 기본값
    
    def _get_pattern_weight(self, domain: str, failure_type: str) -> float:
        """
        Learning 패턴 기반 가중치 계산.
        
        반복 발생하는 에러 패턴은 더 높은 가중치 부여.
        - 10회 이상: 2.0배
        - 5회 이상: 1.5배
        - 3회 이상: 1.2배
        - 3회 미만: 1.0배
        
        Args:
            domain: 도메인 이름
            failure_type: 실패 유형
            
        Returns:
            패턴 가중치 배수 (1.0 ~ 2.0)
            
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.3
        """
        try:
            from selfhealing.services.learning import LearningService
            from selfhealing.services.learning.models import PatternType
            
            service = LearningService()
            pattern_name = f"{domain}:{failure_type}"
            
            # 패턴 조회 (FAILURE 유형)
            patterns = service.get_patterns(pattern_type=PatternType.FAILURE)
            
            # 이름 매칭 패턴 찾기
            matching_pattern = None
            for pattern in patterns:
                if pattern.name == pattern_name:
                    matching_pattern = pattern
                    break
            
            if not matching_pattern:
                return PATTERN_OCCURRENCE_WEIGHT["none"]
            
            # occurrence_count 기반 가중치
            occurrence_count = matching_pattern.occurrence_count
            
            if occurrence_count >= 10:
                weight = PATTERN_OCCURRENCE_WEIGHT["high"]
            elif occurrence_count >= 5:
                weight = PATTERN_OCCURRENCE_WEIGHT["medium"]
            elif occurrence_count >= 3:
                weight = PATTERN_OCCURRENCE_WEIGHT["low"]
            else:
                weight = PATTERN_OCCURRENCE_WEIGHT["none"]
            
            logger.debug(
                f"[ShadowBudget] Pattern weight: pattern={pattern_name}, "
                f"occurrence_count={occurrence_count}, weight={weight:.2f}"
            )
            
            return weight
            
        except Exception as e:
            logger.warning(f"[ShadowBudget] Failed to get pattern weight: {e}")
            return 1.0  # 기본값 (Learning 서비스 장애 시)
    
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
    
    # =========================================================================
    # Phase 6: Pending Reconciliation Freeze
    # Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.6
    # =========================================================================
    
    def _notify_pending_freeze(self, shadow: ShadowBudget) -> None:
        """
        대규모 조정(>5%) 시 배포 동결 신호.
        
        Pending Reconciliation 승인 대기 중 배포/자동 튜닝 동결.
        
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.6
        """
        if shadow.adjustment_percent <= 5.0:
            return  # 소규모 조정은 무시
        
        try:
            from selfhealing.services.circuit_breaker.freeze_mode import (
                FreezeModeManager,
            )
            
            manager = FreezeModeManager()
            manager.activate(
                reason=(
                    f"Pending Reconciliation: {shadow.adjustment_percent:.2f}% adjustment. "
                    f"Awaiting approval for calculation_id={shadow.calculation_id}"
                ),
                activated_by="shadow_budget_calculator",
            )
            
            logger.warning(
                f"[ShadowBudget] Freeze Mode ACTIVATED for large adjustment: "
                f"{shadow.adjustment_percent:.2f}%"
            )
            
            # Audit 이벤트 기록
            self._record_audit_event(
                event_type="pending_reconciliation_freeze",
                details={
                    "calculation_id": shadow.calculation_id,
                    "adjustment_percent": shadow.adjustment_percent,
                    "action": "freeze_activated",
                },
            )
            
        except Exception as e:
            logger.warning(f"[ShadowBudget] Failed to activate freeze: {e}")
    
    def _deactivate_pending_freeze(self, calculation_id: str, reason: str) -> None:
        """
        Pending Reconciliation 완료(승인/거부) 시 동결 해제.
        
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.6
        """
        try:
            from selfhealing.services.circuit_breaker.freeze_mode import (
                FreezeModeManager,
            )
            
            manager = FreezeModeManager()
            if manager.is_active():
                manager.deactivate(
                    reason=f"Reconciliation resolved: {reason}",
                    deactivated_by="shadow_budget_calculator",
                )
                
                logger.info(
                    f"[ShadowBudget] Freeze Mode DEACTIVATED: calculation_id={calculation_id}"
                )
                
        except Exception as e:
            logger.warning(f"[ShadowBudget] Failed to deactivate freeze: {e}")
    
    # =========================================================================
    # Phase 9: 알림 연동
    # Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §6
    # =========================================================================
    
    def _notify_shadow_budget_calculated(self, shadow: ShadowBudget) -> None:
        """
        Shadow Budget 계산 완료 알림.
        
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §6.3
        """
        try:
            from selfhealing.services.unified_notification import (
                get_unified_notification_manager,
                NotificationPayload,
                NotificationPriority,
                NotificationCategory,
            )
            
            # 대규모 조정 시 우선순위 상향
            priority = (
                NotificationPriority.HIGH 
                if shadow.adjustment_percent > 5.0 
                else NotificationPriority.MEDIUM
            )
            
            payload = NotificationPayload(
                title="Shadow Budget 승인 대기",
                message=(
                    f"Fail-Safe 기간 동안 {shadow.estimated_errors}개 에러 추정. "
                    f"예상 조정: {shadow.adjustment_percent:.2f}%. 검토가 필요합니다."
                ),
                priority=priority,
                category=NotificationCategory.APPROVAL,
                source="shadow_budget_calculator",
                metadata={
                    "calculation_id": shadow.calculation_id,
                    "estimated_errors": shadow.estimated_errors,
                    "adjustment_percent": shadow.adjustment_percent,
                    "log_source": shadow.log_source,
                    "failsafe_period_id": shadow.failsafe_period_id,
                },
            )
            
            manager = get_unified_notification_manager()
            manager.send(payload)
            
            logger.debug(
                f"[ShadowBudget] Notification sent for calculation_id={shadow.calculation_id}"
            )
            
        except Exception as e:
            logger.warning(f"[ShadowBudget] Notification failed (non-critical): {e}")
    
    # =========================================================================
    # Audit 이벤트 기록 헬퍼
    # =========================================================================
    
    def _record_audit_event(
        self,
        event_type: str,
        details: Dict[str, Any],
    ) -> None:
        """
        Audit 이벤트 기록.
        
        Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §5
        """
        try:
            from selfhealing.audit.event_buffer import AuditEventType, AuditEvent
            from selfhealing.audit.continuous_audit import get_audit_recorder
            
            # 문자열을 enum으로 변환
            audit_type = getattr(
                AuditEventType, 
                event_type.upper(), 
                AuditEventType.GENERIC
            )
            
            event = AuditEvent(
                event_type=audit_type,
                source="shadow_budget_calculator",
                details=details,
                actor_type="system",
            )
            
            recorder = get_audit_recorder()
            if recorder:
                recorder.record(event)
                
        except Exception as e:
            logger.debug(f"[ShadowBudget] Audit recording failed (non-critical): {e}")
