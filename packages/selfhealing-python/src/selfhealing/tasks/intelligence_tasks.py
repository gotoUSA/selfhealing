"""
🧠 지능 레인 (Analyze & Learn) Celery Tasks

Phase 3 구현: 자율 운영 지능 레인 태스크들

Tasks:
1. CheckSLADriftTask - SLA 드리프트 감지 및 경고
2. AnalyzeForensicPendingTask - Pending 상태 장기 체류 항목 포렌식 분석
3. AnalyzeCrossStageInsightsTask - Stage 간 학습 인사이트 분석

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §3, §4
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from selfhealing.tasks.base import BaseNotifyingTask
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Task 1: Check SLA Drift (기존 마이그레이션)
# =============================================================================


class CheckSLADriftTask(BaseNotifyingTask):
    """
    SLA 드리프트 감지 및 경고.
    
    설정된 SLA 임계값과 실제 복구 성능 간의 차이를 분석합니다.
    
    스케줄: 1시간마다
    큐: analysis
    알림: 경고 발생 시 즉시 (REALTIME)
    
    Returns:
        dict: {
            "success": bool,
            "warnings_count": int,
            "warnings": list,
            "metrics": dict,
        }
    """

    name = "selfhealing.check_sla_drift"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.REALTIME,
        threshold=1,  # 경고 1개 이상일 때만 알림
        threshold_field="warnings_count",
        default_severity="warning",
        cooldown_seconds=3600,  # 1시간
    )

    def run(self) -> Dict[str, Any]:
        """SLA 드리프트 감지 태스크 실행."""
        logger.info("[CheckSLADrift] Starting SLA drift detection")
        
        try:
            from selfhealing.tasks.drift_detection import SLADriftDetector
            from selfhealing.services.sla_policy import get_sla_thresholds
            
            # SLADriftDetector를 직접 사용하거나 Django 어댑터 사용
            try:
                from shopping.tasks.drift_detection_tasks import check_and_report_sla_drift
                result = check_and_report_sla_drift()
            except ImportError:
                # 독립 실행 (테스트용)
                result = {
                    "success": True,
                    "warnings": [],
                    "metrics": {},
                }
            
            warnings = result.get("warnings", [])
            
            logger.info(f"[CheckSLADrift] Completed with {len(warnings)} warning(s)")
            
            return {
                "success": result.get("success", True),
                "warnings_count": len(warnings),
                "warnings": warnings,
                "metrics": result.get("metrics", {}),
            }
            
        except Exception as e:
            logger.error(f"[CheckSLADrift] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "warnings_count": 0,
            }

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """경고 수에 따른 심각도 결정."""
        count = result.get("warnings_count", 0)
        if count >= 5:
            return "critical"
        elif count >= 1:
            return "warning"
        return "info"

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ SLA 드리프트 감지 실패: {result['error']}"
        
        count = result.get("warnings_count", 0)
        if count == 0:
            return "✅ SLA 드리프트 없음 - 모든 지표 정상"
        
        return f"⚠️ SLA 드리프트 감지: {count}개 경고 발생"


# =============================================================================
# Task 2: Analyze Forensic Pending (P1)
# =============================================================================


class AnalyzeForensicPendingTask(BaseNotifyingTask):
    """
    Pending 상태 장기 체류 항목 포렌식 분석.
    
    DLQ에서 오래 머무르는 항목을 분석하여 패턴과 권장 조치를 제공합니다.
    
    스케줄: 30분마다
    큐: analysis
    알림: 의심 항목 10개 이상일 때 즉시 (REALTIME)
    
    Args:
        threshold_minutes: 분석 기준 시간 (기본 60분)
    
    Returns:
        dict: {
            "success": bool,
            "suspicious_count": int,
            "stuck_patterns": list,
            "recommendations": list,
        }
    """

    name = "selfhealing.analyze_forensic_pending"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.REALTIME,
        threshold=10,  # 10개 이상일 때만
        threshold_field="suspicious_count",
        default_severity="warning",
        cooldown_seconds=3600,  # 1시간
    )

    def run(self, threshold_minutes: int = 60) -> Dict[str, Any]:
        """포렌식 분석 태스크 실행."""
        logger.info(
            f"[AnalyzeForensicPending] Starting analysis for items "
            f"pending over {threshold_minutes} minutes"
        )
        
        try:
            # Django 어댑터 사용 시도
            try:
                from shopping.tasks.drift_detection_tasks import analyze_pending_operations
                raw_result = analyze_pending_operations(batch_size=100)
            except ImportError:
                # 독립 실행 (테스트용)
                raw_result = {
                    "success": True,
                    "analyzed_count": 0,
                    "results_by_action": {},
                }
            
            analyzed_count = raw_result.get("analyzed_count", 0)
            results_by_action = raw_result.get("results_by_action", {})
            
            # 의심스러운 항목 수 계산 (requires_review 또는 stuck)
            suspicious_count = (
                results_by_action.get("requires_review", 0) +
                results_by_action.get("stuck", 0) +
                results_by_action.get("unknown", 0)
            )
            
            # 패턴 추출
            stuck_patterns = self._extract_patterns(results_by_action)
            
            # 권장 사항 생성
            recommendations = self._generate_recommendations(
                results_by_action, 
                suspicious_count
            )
            
            logger.info(
                f"[AnalyzeForensicPending] Completed - "
                f"analyzed={analyzed_count}, suspicious={suspicious_count}"
            )
            
            return {
                "success": True,
                "analyzed_count": analyzed_count,
                "suspicious_count": suspicious_count,
                "stuck_patterns": stuck_patterns,
                "recommendations": recommendations,
                "results_by_action": results_by_action,
            }
            
        except Exception as e:
            logger.error(f"[AnalyzeForensicPending] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "suspicious_count": 0,
            }

    def _extract_patterns(self, results_by_action: Dict[str, int]) -> list:
        """결과에서 패턴 추출."""
        patterns = []
        for action, count in results_by_action.items():
            if count > 0:
                patterns.append({
                    "action": action,
                    "count": count,
                    "percentage": 0,  # 전체 대비 비율 (계산 필요)
                })
        return patterns

    def _generate_recommendations(
        self, 
        results_by_action: Dict[str, int],
        suspicious_count: int
    ) -> list:
        """권장 사항 생성."""
        recommendations = []
        
        if results_by_action.get("stuck", 0) > 5:
            recommendations.append(
                "다수의 stuck 항목 발견 - 수동 검토 권장"
            )
        
        if results_by_action.get("requires_review", 0) > 10:
            recommendations.append(
                "검토 필요 항목 다수 - DLQ 대시보드 확인 필요"
            )
        
        if suspicious_count > 20:
            recommendations.append(
                "의심 항목 급증 - 시스템 상태 점검 권장"
            )
        
        return recommendations

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """의심 항목 수에 따른 심각도 결정."""
        count = result.get("suspicious_count", 0)
        if count >= 50:
            return "critical"
        elif count >= 10:
            return "warning"
        return "info"

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 포렌식 분석 실패: {result['error']}"
        
        patterns_count = len(result.get("stuck_patterns", []))
        
        return (
            f"🔍 포렌식 분석 결과\n"
            f"• 의심 항목: {result['suspicious_count']}건\n"
            f"• 패턴: {patterns_count}개 발견"
        )


# =============================================================================
# Task 3: Analyze Cross-Stage Insights (P3)
# =============================================================================


class AnalyzeCrossStageInsightsTask(BaseNotifyingTask):
    """
    Stage 간 학습 인사이트 분석.
    
    여러 Stage에서 발견된 공통 패턴을 분석하여 인사이트를 제공합니다.
    
    스케줄: 매일 22:00
    큐: analysis
    알림: 인사이트 3개 이상일 때만 (일일 요약에 포함)
    
    Returns:
        dict: {
            "success": bool,
            "insight_count": int,
            "insights": list,
            "recommendations": list,
        }
    """

    name = "selfhealing.analyze_cross_stage_insights"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        threshold=3,  # 인사이트 3개 이상일 때만
        threshold_field="insight_count",
        default_severity="info",
    )

    def run(self) -> Dict[str, Any]:
        """Cross-Stage 인사이트 분석 태스크 실행."""
        logger.info("[AnalyzeCrossStageInsights] Starting cross-stage analysis")
        
        try:
            from selfhealing.services.learning import get_learning_service
            
            service = get_learning_service()
            raw_insights = service.get_cross_stage_insights()
            
            # 인사이트 구조화
            insights = self._structure_insights(raw_insights)
            
            # 권장 사항 생성
            recommendations = self._generate_recommendations(raw_insights, insights)
            
            logger.info(
                f"[AnalyzeCrossStageInsights] Completed with "
                f"{len(insights)} insights"
            )
            
            return {
                "success": True,
                "insight_count": len(insights),
                "insights": insights,
                "recommendations": recommendations,
                "raw_data": raw_insights,
            }
            
        except Exception as e:
            logger.error(f"[AnalyzeCrossStageInsights] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "insight_count": 0,
            }

    def _structure_insights(self, raw_insights: Dict[str, Any]) -> list:
        """원시 데이터에서 인사이트 구조화."""
        insights = []
        
        common_patterns = raw_insights.get("common_patterns", {})
        for pattern_name, stages in common_patterns.items():
            insights.append({
                "type": "common_pattern",
                "name": pattern_name,
                "stages": stages,
                "stage_count": len(stages),
                "recommendation": f"패턴 '{pattern_name}'이(가) {len(stages)}개 Stage에서 발견됨",
            })
        
        # 추가 인사이트 (제안 대기 수 등)
        suggestions_pending = raw_insights.get("suggestions_pending", 0)
        if suggestions_pending > 0:
            insights.append({
                "type": "pending_suggestions",
                "count": suggestions_pending,
                "recommendation": f"{suggestions_pending}개의 적용 대기 제안이 있습니다",
            })
        
        return insights

    def _generate_recommendations(
        self,
        raw_insights: Dict[str, Any],
        structured_insights: list,
    ) -> list:
        """권장 사항 생성."""
        recommendations = []
        
        total_patterns = raw_insights.get("total_patterns", 0)
        common_patterns = raw_insights.get("common_patterns", {})
        
        if len(common_patterns) > 3:
            recommendations.append(
                "다수의 공통 패턴 발견 - 글로벌 정책 검토 권장"
            )
        
        if total_patterns > 20:
            recommendations.append(
                "학습된 패턴 수가 많음 - 패턴 정리 고려"
            )
        
        # 구조화된 인사이트에서 권장 사항 추출
        for insight in structured_insights:
            if insight.get("recommendation"):
                recommendations.append(insight["recommendation"])
        
        return recommendations

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ Cross-Stage 인사이트 분석 실패: {result['error']}"
        
        return f"🧠 학습 인사이트: {result['insight_count']}개 발견"


# =============================================================================
# Task 4: Check Recovery Transitions (기존 태스크 알림 추가)
# =============================================================================


class CheckRecoveryTransitionsTask(BaseNotifyingTask):
    """
    Circuit Breaker 복구 상태 체크.
    
    CB 상태 변화를 감지하고 복구 완료 시 알림을 발송합니다.
    
    스케줄: 2분마다
    큐: realtime
    알림: 상태 변화 시 즉시 (REALTIME)
    
    Returns:
        dict: {
            "success": bool,
            "transitions_count": int,
            "circuits_recovered": list,
        }
    """

    name = "selfhealing.check_recovery_transitions"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.REALTIME,
        threshold=1,  # 변화 1개 이상일 때만
        threshold_field="transitions_count",
        default_severity="info",
        cooldown_seconds=120,  # 2분
    )

    def run(self) -> Dict[str, Any]:
        """복구 상태 체크 태스크 실행."""
        logger.info("[CheckRecoveryTransitions] Checking circuit breaker states")
        
        try:
            # Django 어댑터 사용 시도
            try:
                from shopping.tasks.self_healing_celery_tasks import (
                    check_recovery_transitions,
                )
                result = check_recovery_transitions()
            except ImportError:
                # 독립 실행 (테스트용)
                from selfhealing.core.circuit_breaker import (
                    get_circuit_breaker_registry,
                )
                registry = get_circuit_breaker_registry()
                
                result = {
                    "success": True,
                    "transitions_count": 0,
                    "circuits_recovered": [],
                    "checked_circuits": len(registry.list_all()) if registry else 0,
                }
            
            transitions = result.get("transitions_count", 0)
            recovered = result.get("circuits_recovered", [])
            
            logger.info(
                f"[CheckRecoveryTransitions] Completed - "
                f"transitions={transitions}, recovered={len(recovered)}"
            )
            
            return {
                "success": True,
                "transitions_count": transitions,
                "circuits_recovered": recovered,
            }
            
        except Exception as e:
            logger.error(f"[CheckRecoveryTransitions] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "transitions_count": 0,
            }

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """상태에 따른 심각도 결정."""
        recovered = result.get("circuits_recovered", [])
        if len(recovered) > 0:
            return "info"  # 복구는 좋은 소식
        return "info"

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 복구 상태 체크 실패: {result['error']}"
        
        recovered = result.get("circuits_recovered", [])
        if len(recovered) > 0:
            circuits = ", ".join(recovered[:3])
            if len(recovered) > 3:
                circuits += f" 외 {len(recovered) - 3}개"
            return f"✅ Circuit Breaker 복구: {circuits}"
        
        return f"ℹ️ Circuit Breaker 상태 변화: {result['transitions_count']}건"


# =============================================================================
# Task Registry (Celery 등록용)
# =============================================================================


# 태스크 클래스 목록 (Celery 등록 시 사용)
INTELLIGENCE_TASKS = [
    CheckSLADriftTask,
    AnalyzeForensicPendingTask,
    AnalyzeCrossStageInsightsTask,
    CheckRecoveryTransitionsTask,
]


# =============================================================================
# Celery shared_task 래퍼 (Django 프로젝트 연동용)
# =============================================================================


def register_intelligence_tasks_with_celery(app):
    """
    Celery app에 지능 레인 태스크 등록.
    
    Usage:
        from celery import Celery
        from selfhealing.tasks.intelligence_tasks import (
            register_intelligence_tasks_with_celery,
        )
        
        app = Celery('myproject')
        register_intelligence_tasks_with_celery(app)
    """
    for task_class in INTELLIGENCE_TASKS:
        wrapped = type(
            task_class.__name__,
            (task_class, app.Task),
            {
                "name": task_class.name,
                "bind": True,
            },
        )
        app.register_task(wrapped())
        logger.info(f"[IntelligenceTasks] Registered: {task_class.name}")


# =============================================================================
# Beat Schedule 정의
# =============================================================================


def get_intelligence_beat_schedule() -> Dict[str, Any]:
    """
    지능 레인 Beat Schedule 반환.
    
    Returns:
        dict: Celery Beat Schedule 설정
    """
    from celery.schedules import crontab
    
    return {
        # 2분마다 - 복구 상태 체크
        "check-recovery-transitions": {
            "task": "selfhealing.check_recovery_transitions",
            "schedule": crontab(minute="*/2"),
            "options": {"queue": "realtime"},
        },
        # 30분마다 - 포렌식 분석
        "analyze-forensic-pending": {
            "task": "selfhealing.analyze_forensic_pending",
            "schedule": crontab(minute="*/30"),
            "options": {"queue": "analysis"},
            "kwargs": {"threshold_minutes": 60},
        },
        # 1시간마다 - SLA 드리프트 체크
        "check-sla-drift": {
            "task": "selfhealing.check_sla_drift",
            "schedule": crontab(minute=0),  # 매 정시
            "options": {"queue": "analysis"},
        },
        # 매일 22:00 - Cross-Stage 인사이트
        "analyze-cross-stage-insights": {
            "task": "selfhealing.analyze_cross_stage_insights",
            "schedule": crontab(hour=22, minute=0),
            "options": {"queue": "analysis"},
        },
    }


__all__ = [
    # Task Classes
    "CheckSLADriftTask",
    "AnalyzeForensicPendingTask",
    "AnalyzeCrossStageInsightsTask",
    "CheckRecoveryTransitionsTask",
    # Registry
    "INTELLIGENCE_TASKS",
    "register_intelligence_tasks_with_celery",
    "get_intelligence_beat_schedule",
]
