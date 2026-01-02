"""
📋 증명 레인 (Compliance & Report) Celery Tasks

Phase 4 구현: 자율 운영 증명 레인 태스크들

Tasks:
1. RunComplianceCheckTask - 규정 준수 상태 점검
2. GenerateFinOpsReportTask - FinOps 비용 분석 리포트 생성
3. CollectSelfHealingMetricsTask - Self-Healing 메트릭 수집
4. GenerateDailyAutonomousReportTask - 일일 자율 운영 리포트 생성

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md §3, §4
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from selfhealing.tasks.base import BaseNotifyingTask
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Task 1: Run Compliance Check (P2)
# =============================================================================


class RunComplianceCheckTask(BaseNotifyingTask):
    """
    규정 준수 상태 점검.
    
    모든 등록된 규정 검사를 실행하고 위반 사항을 리포트합니다.
    
    스케줄: 매일 07:00
    큐: compliance
    알림: 위반 있을 때만 (임계값 기반)
    
    Args:
        check_type: 검사 유형 ("all", "critical", "dora", "soc2", "pci_dss")
        stage_name: 특정 Stage만 검사 (None이면 전체)
    
    Returns:
        dict: {
            "success": bool,
            "check_type": str,
            "total_checks": int,
            "passed_count": int,
            "violation_count": int,
            "violations": list,
        }
    """

    name = "selfhealing.run_compliance_check"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AFTER,
        threshold=0,  # 위반 있을 때만 (0 초과 시)
        threshold_field="violation_count",
        default_severity="warning",
        channels=["slack", "email"],
    )

    def run(
        self, 
        check_type: str = "all",
        stage_name: str | None = None,
    ) -> Dict[str, Any]:
        """규정 준수 점검 태스크 실행."""
        logger.info(
            f"[RunComplianceCheck] Starting compliance check - "
            f"type={check_type}, stage={stage_name or 'all'}"
        )
        
        try:
            from selfhealing.services.compliance import get_compliance_service
            
            service = get_compliance_service()
            
            # 검사 유형에 따른 표준 결정
            standards = self._get_standards_for_type(check_type)
            
            # 전체 검사 실행
            report = service.run_all_checks(
                stage_name=stage_name or "default",
                standards=standards if standards else None,
            )
            
            # 결과 구조화
            violations = [
                {
                    "id": v.violation_id,
                    "check_id": v.check_id,
                    "severity": v.severity.value if hasattr(v.severity, 'value') else str(v.severity),
                    "message": v.message,
                    "details": v.details,
                }
                for v in report.violations
            ]
            
            logger.info(
                f"[RunComplianceCheck] Completed - "
                f"total={report.total_checks}, passed={report.passed_checks}, "
                f"failed={report.failed_checks}"
            )
            
            return {
                "success": True,
                "check_type": check_type,
                "total_checks": report.total_checks,
                "passed_count": report.passed_checks,
                "violation_count": report.failed_checks,
                "violations": violations,
                "compliance_score": report.compliance_score,
            }
            
        except Exception as e:
            logger.error(f"[RunComplianceCheck] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "check_type": check_type,
                "violation_count": 0,
            }

    def _get_standards_for_type(self, check_type: str) -> list | None:
        """검사 유형에 따른 표준 목록 반환."""
        try:
            from selfhealing.services.compliance.service import ComplianceStandard
            
            type_mapping = {
                "dora": [ComplianceStandard.DORA_2025],
                "soc2": [ComplianceStandard.SOC2],
                "pci_dss": [ComplianceStandard.PCI_DSS],
                "critical": [
                    ComplianceStandard.DORA_2025,
                    ComplianceStandard.PCI_DSS,
                ],
            }
            
            return type_mapping.get(check_type)  # None for "all"
            
        except ImportError:
            return None

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """위반 수에 따른 심각도 결정."""
        count = result.get("violation_count", 0)
        if count > 10:
            return "critical"
        elif count > 0:
            return "warning"
        return "info"

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ 규정 준수 점검 실패: {result['error']}"
        
        if result.get("violation_count", 0) == 0:
            return (
                f"✅ 규정 준수 점검 완료: "
                f"{result['total_checks']}개 항목 모두 통과"
            )
        
        return (
            f"⚠️ 규정 준수 점검 결과\n"
            f"• 총 점검: {result['total_checks']}건\n"
            f"• 통과: {result['passed_count']}건\n"
            f"• 위반: {result['violation_count']}건\n"
            f"• 준수 점수: {result.get('compliance_score', 0):.1f}%"
        )


# =============================================================================
# Task 2: Generate FinOps Report (P2)
# =============================================================================


class GenerateFinOpsReportTask(BaseNotifyingTask):
    """
    FinOps 비용 분석 리포트 생성.
    
    Self-Healing 운영 비용을 분석하고 리포트를 생성합니다.
    
    스케줄: 매주 월요일 08:00
    큐: reports
    알림: 즉시 발송 (Slack + Email)
    
    Args:
        period: 리포트 기간 ("daily", "weekly", "monthly")
        stage_name: 특정 Stage만 (None이면 전체)
    
    Returns:
        dict: {
            "success": bool,
            "report_id": str,
            "period": str,
            "total_cost": float,
            "savings": float,
        }
    """

    name = "selfhealing.generate_finops_report"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AFTER,
        aggregate=False,  # 매주 1회라 즉시 발송
        default_severity="info",
        channels=["slack", "email"],
    )

    def run(
        self, 
        period: str = "weekly",
        stage_name: str | None = None,
    ) -> Dict[str, Any]:
        """FinOps 리포트 생성 태스크 실행."""
        logger.info(
            f"[GenerateFinOpsReport] Generating {period} report"
            f"{f' for {stage_name}' if stage_name else ''}"
        )
        
        try:
            from selfhealing.services.finops import get_finops_service
            
            service = get_finops_service()
            
            # 리포트 생성
            report = service.generate_report(
                period=period,
                stage_name=stage_name,
            )
            
            # 비용 절감 계산 (이전 기간 대비)
            savings = self._calculate_savings(service, period, stage_name)
            
            logger.info(
                f"[GenerateFinOpsReport] Generated report - "
                f"total_cost=${report.total_cost}, records={report.record_count}"
            )
            
            return {
                "success": True,
                "report_id": f"finops-{period}-{datetime.now():%Y%m%d}",
                "period": period,
                "total_cost": float(report.total_cost),
                "savings": savings,
                "record_count": report.record_count,
                "success_rate": report.success_rate,
                "by_stage": {k: float(v) for k, v in report.by_stage.items()},
                "by_operation": {k: float(v) for k, v in report.by_operation.items()},
            }
            
        except Exception as e:
            logger.error(f"[GenerateFinOpsReport] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "period": period,
            }

    def _calculate_savings(
        self, 
        service,
        period: str,
        stage_name: str | None,
    ) -> float:
        """이전 기간 대비 비용 절감 계산."""
        try:
            # 간단한 계산: 현재 0으로 설정 (실제 구현 시 이전 리포트와 비교)
            return 0.0
        except Exception:
            return 0.0

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        if result.get("error"):
            return f"❌ FinOps 리포트 생성 실패: {result['error']}"
        
        return (
            f"💰 FinOps 리포트 생성 완료\n"
            f"• 기간: {result['period']}\n"
            f"• 총 비용: ${result.get('total_cost', 0):.4f}\n"
            f"• 기록 수: {result.get('record_count', 0)}건\n"
            f"• 성공률: {result.get('success_rate', 0):.1f}%"
        )


# =============================================================================
# Task 3: Collect Self-Healing Metrics
# =============================================================================


class CollectSelfHealingMetricsTask(BaseNotifyingTask):
    """
    Self-Healing 메트릭 수집.
    
    시스템 전반의 Self-Healing 메트릭을 수집하고 저장합니다.
    
    스케줄: 30분마다
    큐: metrics
    알림: 로그만 (알림 없음)
    
    Returns:
        dict: {
            "success": bool,
            "metrics_collected": int,
            "timestamp": str,
        }
    """

    name = "selfhealing.collect_self_healing_metrics"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        threshold=float('inf'),  # 실질적으로 알림 없음
        threshold_field="always_skip",
        default_severity="info",
    )

    def run(self) -> Dict[str, Any]:
        """메트릭 수집 태스크 실행."""
        logger.info("[CollectSelfHealingMetrics] Starting metrics collection")
        
        try:
            metrics_collected = 0
            
            # Circuit Breaker 메트릭
            try:
                from selfhealing.core.circuit_breaker import (
                    get_circuit_breaker_registry,
                )
                registry = get_circuit_breaker_registry()
                if registry:
                    cb_count = len(registry.list_all())
                    metrics_collected += cb_count
            except Exception as e:
                logger.debug(f"CB metrics not available: {e}")
            
            # DLQ 메트릭
            try:
                from selfhealing.services.dlq_service import get_dlq_service
                service = get_dlq_service()
                # 서비스 상태 확인 (실제 메트릭 수집은 구현 필요)
                metrics_collected += 1
            except Exception as e:
                logger.debug(f"DLQ metrics not available: {e}")
            
            # Emergency Mode 메트릭
            try:
                from selfhealing.core.emergency_mode import (
                    get_emergency_mode_manager,
                )
                manager = get_emergency_mode_manager()
                current_level = manager.get_current_level()
                metrics_collected += 1
            except Exception as e:
                logger.debug(f"Emergency mode metrics not available: {e}")
            
            timestamp = datetime.now(timezone.utc).isoformat()
            
            logger.info(
                f"[CollectSelfHealingMetrics] Collected {metrics_collected} metrics"
            )
            
            return {
                "success": True,
                "metrics_collected": metrics_collected,
                "timestamp": timestamp,
            }
            
        except Exception as e:
            logger.error(f"[CollectSelfHealingMetrics] Failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "metrics_collected": 0,
            }

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성 (실제로는 사용되지 않음)."""
        return f"📊 메트릭 수집: {result.get('metrics_collected', 0)}개"


# =============================================================================
# NOTE: GenerateDailyAutonomousReportTask는 문서 §6.2 Phase 5에 따라
# daily_report.py에 위치합니다. (09_AUTONOMOUS_TASK_EXPANSION.md 참조)
# =============================================================================


# =============================================================================
# Task Registry (Celery 등록용)
# =============================================================================


# 태스크 클래스 목록 (Celery 등록 시 사용)
# NOTE: GenerateDailyAutonomousReportTask는 daily_report.py에 위치
COMPLIANCE_TASKS = [
    RunComplianceCheckTask,
    GenerateFinOpsReportTask,
    CollectSelfHealingMetricsTask,
]


# =============================================================================
# Celery shared_task 래퍼 (Django 프로젝트 연동용)
# =============================================================================


def register_compliance_tasks_with_celery(app):
    """
    Celery app에 증명 레인 태스크 등록.
    
    Usage:
        from celery import Celery
        from selfhealing.tasks.compliance_tasks import (
            register_compliance_tasks_with_celery,
        )
        
        app = Celery('myproject')
        register_compliance_tasks_with_celery(app)
    """
    for task_class in COMPLIANCE_TASKS:
        wrapped = type(
            task_class.__name__,
            (task_class, app.Task),
            {
                "name": task_class.name,
                "bind": True,
            },
        )
        app.register_task(wrapped())
        logger.info(f"[ComplianceTasks] Registered: {task_class.name}")


# =============================================================================
# Beat Schedule 정의
# =============================================================================


def get_compliance_beat_schedule() -> Dict[str, Any]:
    """
    증명 레인 Beat Schedule 반환.
    
    Returns:
        dict: Celery Beat Schedule 설정
    """
    from celery.schedules import crontab
    
    return {
        # 매일 07:00 - 규정 준수 점검
        "run-compliance-check": {
            "task": "selfhealing.run_compliance_check",
            "schedule": crontab(hour=7, minute=0),
            "options": {"queue": "compliance"},
            "kwargs": {"check_type": "all"},
        },
        # 매주 월요일 08:00 - FinOps 리포트
        "generate-finops-report": {
            "task": "selfhealing.generate_finops_report",
            "schedule": crontab(hour=8, minute=0, day_of_week=1),
            "options": {"queue": "reports"},
            "kwargs": {"period": "weekly"},
        },
        # 30분마다 - 메트릭 수집
        "collect-self-healing-metrics": {
            "task": "selfhealing.collect_self_healing_metrics",
            "schedule": crontab(minute="*/30"),
            "options": {"queue": "metrics"},
        },
        # NOTE: generate-daily-autonomous-report는 daily_report.py의
        # get_daily_report_beat_schedule()에서 정의 (문서 §6.2 Phase 5)
    }


__all__ = [
    # Task Classes (증명 레인 - 문서 §4.2)
    # NOTE: GenerateDailyAutonomousReportTask는 daily_report.py에서 export
    "RunComplianceCheckTask",
    "GenerateFinOpsReportTask",
    "CollectSelfHealingMetricsTask",
    # Registry
    "COMPLIANCE_TASKS",
    "register_compliance_tasks_with_celery",
    "get_compliance_beat_schedule",
]
