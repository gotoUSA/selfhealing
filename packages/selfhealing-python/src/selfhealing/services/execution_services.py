"""
Chaos Execution Service

Chaos 실험 스케줄링 및 실행을 위한 서비스 레이어.

Thin Task, Fat Service 원칙:
    - Celery Task (chaos_scheduler.py)는 단순히 이 서비스를 호출
    - 모든 비즈니스 로직, 안전 체크, 상태 관리는 이 서비스에서 수행

Features:
    - 스케줄된 실험 실행
    - 사전 비행 안전 체크 (SafetyGuard 통합)
    - 일일 복원력 보고서 생성
    - 승인 대기 알림

Reference:
- docs/self_healing/CHAOS_ENGINEERING.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from selfhealing.core.timezone import now
from selfhealing.services.governance_checks import (
    GovernanceCheckMixin,
    GovernanceCheckResult,
    check_all_governance,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class ExperimentExecutionResult:
    """실험 실행 결과."""
    
    checked: int = 0
    """체크된 실험 수."""
    
    executed: int = 0
    """실행된 실험 수."""
    
    skipped: int = 0
    """스킵된 실험 수."""
    
    blocked: int = 0
    """차단된 실험 수."""
    
    errors: List[Dict[str, Any]] = field(default_factory=list)
    """에러 목록."""
    
    experiments: List[Dict[str, Any]] = field(default_factory=list)
    """개별 실험 결과."""
    
    governance_blocked: bool = False
    """거버넌스에 의해 전체 차단되었는지."""
    
    governance_block_reason: str = ""
    """거버넌스 차단 사유."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "checked": self.checked,
            "executed": self.executed,
            "skipped": self.skipped,
            "blocked": self.blocked,
            "errors": self.errors,
            "experiments": self.experiments,
            "governance_blocked": self.governance_blocked,
            "governance_block_reason": self.governance_block_reason,
        }


@dataclass
class DailyReportResult:
    """일일 보고서 결과."""
    
    success: bool
    report_id: Optional[str] = None
    grade: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "report_id": self.report_id,
            "grade": self.grade,
            "summary": self.summary,
            "error": self.error,
        }


@dataclass
class ApprovalCleanupResult:
    """승인 정리 결과."""
    
    schedule_expired: int = 0
    blast_radius_expired: int = 0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_expired": self.schedule_expired,
            "blast_radius_expired": self.blast_radius_expired,
            "errors": self.errors,
        }


@dataclass
class PendingApprovalCheckResult:
    """대기 중인 승인 체크 결과."""
    
    pending_schedules: int = 0
    pending_blast_radius: int = 0
    alerts_sent: int = 0
    notification_status: str = "sent"
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pending_schedules": self.pending_schedules,
            "pending_blast_radius": self.pending_blast_radius,
            "alerts_sent": self.alerts_sent,
            "notification_status": self.notification_status,
            "error": self.error,
        }


# =============================================================================
# Chaos Execution Service
# =============================================================================


class ChaosExecutionService(GovernanceCheckMixin):
    """
    Chaos 실험 실행 서비스.
    
    SafetyGuard와 통합하여 모든 안전 체크를 수행하고,
    스케줄된 Chaos 실험을 실행합니다.
    
    Usage:
        service = get_chaos_execution_service()
        
        # 스케줄된 실험 실행
        result = service.run_scheduled_experiments()
        
        # 일일 보고서 생성
        report = service.generate_daily_report()
    """
    
    _instance: Optional["ChaosExecutionService"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
    
    # =========================================================================
    # Main Entry Points
    # =========================================================================
    
    def run_scheduled_experiments(self) -> ExperimentExecutionResult:
        """
        스케줄된 chaos 실험 실행.
        
        Celery Beat에서 5분마다 호출됩니다.
        
        Workflow:
        1. 거버넌스 체크 (Kill Switch, ErrorBudget)
        2. 스케줄러에서 실행 예정 실험 조회
        3. 각 실험에 대해 SafetyGuard 사전 비행 체크
        4. 승인된 실험 실행
        5. 결과 기록
        
        Returns:
            ExperimentExecutionResult
        """
        result = ExperimentExecutionResult()
        
        # 1. 거버넌스 체크 - Kill Switch는 체크하지만 Emergency는 체크 안 함
        #    (Chaos는 ErrorBudget 체크만 하면 됨)
        governance_result = check_all_governance(
            check_kill_switch=True,
            check_emergency=False,  # Chaos 실험은 Emergency 체크 불필요
            check_error_budget=True,
            operation_name="run_scheduled_experiments",
            service_name="ChaosExecutionService",
            domain="chaos",
        )
        
        if not governance_result.allowed:
            result.governance_blocked = True
            result.governance_block_reason = governance_result.block_message
            logger.warning(
                f"[ChaosExecutionService] Experiments blocked by governance: "
                f"{governance_result.block_message}"
            )
            return result
        
        try:
            from selfhealing.services.chaos.scheduler import get_chaos_scheduler
            from selfhealing.services.chaos.safety_guard import get_safety_guard
            
            scheduler = get_chaos_scheduler()
            safety_guard = get_safety_guard()
            
            # 2. 실행 예정 실험 조회
            due_experiments = scheduler.get_due_experiments()
            result.checked = len(due_experiments)
            
            if not due_experiments:
                logger.debug("[ChaosExecutionService] No experiments due for execution")
                return result
            
            logger.info(
                f"[ChaosExecutionService] Found {len(due_experiments)} due experiments"
            )
            
            # 3. 각 실험 처리
            for experiment in due_experiments:
                exp_result = self._process_single_experiment(
                    experiment=experiment,
                    scheduler=scheduler,
                    safety_guard=safety_guard,
                )
                
                if exp_result["status"] == "executed":
                    result.executed += 1
                elif exp_result["status"] == "skipped":
                    result.skipped += 1
                elif exp_result["status"] in ("blocked", "pending_approval"):
                    result.blocked += 1
                elif exp_result["status"] == "error":
                    result.errors.append(exp_result)
                
                result.experiments.append(exp_result)
            
            logger.info(
                f"[ChaosExecutionService] Completed: {result.executed} executed, "
                f"{result.skipped} skipped, {result.blocked} blocked"
            )
            
        except Exception as e:
            logger.exception("[ChaosExecutionService] Error in run_scheduled_experiments")
            result.errors.append({"error": str(e)})
        
        return result
    
    def _process_single_experiment(
        self,
        experiment,
        scheduler,
        safety_guard,
    ) -> Dict[str, Any]:
        """단일 실험 처리."""
        try:
            exp_id = experiment.id
            
            # Kill Switch 체크
            if scheduler.is_kill_switch_active():
                logger.warning(
                    f"[ChaosExecutionService] Kill switch active, skipping {exp_id}"
                )
                return {
                    "id": exp_id,
                    "status": "blocked",
                    "reason": "kill_switch_active",
                }
            
            # SafetyGuard 사전 비행 체크
            safety_result = safety_guard.pre_flight_check(
                experiment_type=experiment.experiment_type,
                blast_radius=experiment.blast_radius,
                target_service=experiment.target_service,
            )
            
            if not safety_result.is_safe:
                logger.warning(
                    f"[ChaosExecutionService] Safety check failed for {exp_id}: "
                    f"{safety_result.block_reasons}"
                )
                scheduler.skip_experiment(
                    exp_id,
                    reason=f"Safety check failed: {safety_result.block_reasons}",
                )
                return {
                    "id": exp_id,
                    "status": "skipped",
                    "reason": str(safety_result.block_reasons),
                }
            
            # 승인 필요 여부 체크
            if experiment.requires_approval and not experiment.is_approved:
                logger.info(
                    f"[ChaosExecutionService] Experiment {exp_id} awaiting approval"
                )
                return {
                    "id": exp_id,
                    "status": "pending_approval",
                }
            
            # 실험 실행
            exec_result = scheduler.execute_experiment(exp_id)
            
            if exec_result.success:
                logger.info(f"[ChaosExecutionService] Executed experiment {exp_id}")
                return {
                    "id": exp_id,
                    "status": "executed",
                    "result": exec_result.to_dict() if hasattr(exec_result, 'to_dict') else str(exec_result),
                }
            else:
                error_msg = str(exec_result.error) if hasattr(exec_result, 'error') else "Unknown error"
                logger.error(f"[ChaosExecutionService] Failed to execute {exp_id}")
                return {
                    "id": exp_id,
                    "status": "error",
                    "error": error_msg,
                }
                
        except Exception as e:
            logger.exception(f"[ChaosExecutionService] Error executing {experiment.id}")
            return {
                "id": experiment.id,
                "status": "error",
                "error": str(e),
            }
    
    def generate_daily_report(self) -> DailyReportResult:
        """
        일일 복원력 보고서 생성.
        
        매일 6 AM UTC에 호출됩니다.
        
        Returns:
            DailyReportResult
        """
        try:
            from selfhealing.services.chaos.reports import get_report_generator
            
            generator = get_report_generator()
            report = generator.generate_daily_report()
            
            logger.info(
                f"[ChaosExecutionService] Daily report generated: {report.report_id}, "
                f"grade={report.grade}"
            )
            
            return DailyReportResult(
                success=True,
                report_id=report.report_id,
                grade=report.grade,
                summary={
                    "total_experiments": report.total_experiments,
                    "passed": report.passed_count,
                    "failed": report.failed_count,
                    "sla_compliance": report.sla_compliance_percent,
                },
            )
            
        except Exception as e:
            logger.exception("[ChaosExecutionService] Error generating daily report")
            return DailyReportResult(success=False, error=str(e))
    
    def cleanup_expired_approvals(self) -> ApprovalCleanupResult:
        """
        만료된 승인 요청 정리.
        
        매시간 호출됩니다.
        
        Returns:
            ApprovalCleanupResult
        """
        result = ApprovalCleanupResult()
        
        try:
            from selfhealing.services.chaos.scheduler import get_chaos_scheduler
            from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
            
            scheduler = get_chaos_scheduler()
            manager = get_blast_radius_manager()
            
            # 스케줄 승인 만료 처리
            result.schedule_expired = scheduler.expire_pending_approvals()
            
            # 폭발 반경 승인 만료 처리
            result.blast_radius_expired = manager.expire_pending_approvals()
            
            total = result.schedule_expired + result.blast_radius_expired
            if total > 0:
                logger.info(f"[ChaosExecutionService] Expired {total} pending approvals")
            
        except Exception as e:
            logger.exception("[ChaosExecutionService] Error cleaning up approvals")
            result.errors.append(str(e))
        
        return result
    
    def check_pending_approvals(self) -> PendingApprovalCheckResult:
        """
        대기 중인 승인 확인 및 알림.
        
        30분마다 호출됩니다.
        
        Returns:
            PendingApprovalCheckResult
        """
        result = PendingApprovalCheckResult()
        
        try:
            from selfhealing.services.chaos.scheduler import get_chaos_scheduler
            from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
            
            scheduler = get_chaos_scheduler()
            manager = get_blast_radius_manager()
            
            pending_schedules = scheduler.list_schedules(pending_approval_only=True)
            pending_blast = manager.get_pending_approvals()
            
            result.pending_schedules = len(pending_schedules)
            result.pending_blast_radius = len(pending_blast)
            
            total_pending = result.pending_schedules + result.pending_blast_radius
            
            if total_pending > 0:
                logger.info(
                    f"[ChaosExecutionService] {total_pending} experiments pending approval"
                )
                
                # 알림 발송 시도
                try:
                    from selfhealing.services.notification import send_pending_approval_alert
                    send_pending_approval_alert(
                        pending_count=total_pending,
                        schedules=pending_schedules,
                        blast_radius=pending_blast,
                    )
                    result.alerts_sent = 1
                except ImportError:
                    result.alerts_sent = 0
                    result.notification_status = "not_configured"
            
        except Exception as e:
            logger.exception("[ChaosExecutionService] Error checking pending approvals")
            result.error = str(e)
        
        return result


# =============================================================================
# ConfigApplyService
# =============================================================================


class ConfigApplyService(GovernanceCheckMixin):
    """
    설정 적용 서비스.
    
    지연된 설정 변경 및 그레이스풀 설정 적용을 담당합니다.
    
    Usage:
        service = get_config_apply_service()
        
        # 대기 중인 설정 적용
        result = service.apply_pending_changes()
    """
    
    _instance: Optional["ConfigApplyService"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
    
    def apply_pending_changes(self) -> Dict[str, Any]:
        """
        대기 중인 설정 변경 적용.
        
        Celery Beat에서 5초마다 호출됩니다.
        
        Note:
            Config Apply는 Emergency 체크만 수행합니다.
            Kill Switch는 체크하지 않습니다 - 복구 경로 확보를 위해.
        
        Returns:
            적용 결과 딕셔너리
        """
        # Emergency Mode 체크 - LEVEL_2 이상에서 설정 적용 차단
        # Kill Switch는 체크하지 않음 (복구 퇴로 확보)
        governance_result = check_all_governance(
            check_kill_switch=False,  # 의도적으로 체크 안 함
            check_emergency=True,
            emergency_min_level=2,
            check_error_budget=False,  # 설정 적용은 예산 체크 불필요
            operation_name="apply_pending_changes",
            service_name="ConfigApplyService",
            domain="config",
        )
        
        if not governance_result.allowed:
            logger.warning(
                f"[ConfigApplyService] Config changes blocked: "
                f"{governance_result.block_message}"
            )
            return {
                "status": "blocked",
                "reason": governance_result.block_message,
                "message": "Config changes blocked during emergency mode",
            }
        
        try:
            from selfhealing.services.pending_config import get_pending_config_service
            from selfhealing.services.runtime_config import get_runtime_config_manager
            
            pending_service = get_pending_config_service()
            config_manager = get_runtime_config_manager()
            
            due_changes = pending_service.get_due_changes()
            
            if not due_changes:
                return {"status": "success", "applied": 0, "message": "No pending changes due"}
            
            applied_count = 0
            failed_count = 0
            results = []
            
            for change in due_changes:
                try:
                    result = config_manager.apply_pending_change(change.id)
                    
                    if result.get("status") == "applied":
                        applied_count += 1
                        logger.info(f"[ConfigApplyService] Applied pending change {change.id}")
                    else:
                        failed_count += 1
                        logger.error(
                            f"[ConfigApplyService] Failed to apply {change.id}: "
                            f"{result.get('error')}"
                        )
                    
                    results.append({
                        "id": change.id,
                        "config_type": change.config_type,
                        "status": result.get("status"),
                    })
                    
                except Exception as e:
                    failed_count += 1
                    pending_service.mark_failed(change.id, str(e))
                    logger.error(
                        f"[ConfigApplyService] Exception applying {change.id}: {e}",
                        exc_info=True
                    )
                    results.append({
                        "id": change.id,
                        "config_type": change.config_type,
                        "status": "error",
                        "error": str(e),
                    })
            
            return {
                "status": "success",
                "applied": applied_count,
                "failed": failed_count,
                "results": results,
            }
            
        except Exception as e:
            logger.error(f"[ConfigApplyService] Error: {e}", exc_info=True)
            raise
    
    def apply_graceful_change(
        self,
        pending_id: str,
        max_wait_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        그레이스풀 설정 변경 적용.
        
        진행 중인 작업 완료를 기다린 후 적용합니다.
        
        Args:
            pending_id: 대기 중인 설정 ID
            max_wait_seconds: 최대 대기 시간 (초)
        
        Returns:
            적용 결과 딕셔너리
        """
        # Emergency 체크
        governance_result = check_all_governance(
            check_kill_switch=False,
            check_emergency=True,
            emergency_min_level=2,
            check_error_budget=False,
            operation_name="apply_graceful_change",
            service_name="ConfigApplyService",
            domain="config",
        )
        
        if not governance_result.allowed:
            return {
                "status": "blocked",
                "reason": governance_result.block_message,
            }
        
        try:
            from selfhealing.services.pending_config import get_pending_config_service
            from selfhealing.services.runtime_config import get_runtime_config_manager
            from selfhealing.services.in_progress_tracker import get_in_progress_tracker
            
            pending_service = get_pending_config_service()
            config_manager = get_runtime_config_manager()
            tracker = get_in_progress_tracker()
            
            # 변경 정보 조회
            change = pending_service.get_change(pending_id)
            if not change:
                return {
                    "status": "error",
                    "error": f"Change not found: {pending_id}",
                }
            
            # 진행 중인 작업 체크
            in_progress = tracker.count_in_progress(change.config_type)
            
            if in_progress > 0:
                # 진행 중인 작업이 있으면 재시도 필요
                return {
                    "status": "retry",
                    "in_progress_count": in_progress,
                    "message": f"{in_progress} operations in progress",
                }
            
            # 적용
            result = config_manager.apply_pending_change(pending_id)
            return result
            
        except Exception as e:
            logger.error(f"[ConfigApplyService] Graceful apply error: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
            }


# =============================================================================
# Factory Functions
# =============================================================================


_chaos_execution_service_instance: Optional[ChaosExecutionService] = None
_config_apply_service_instance: Optional[ConfigApplyService] = None


def get_chaos_execution_service() -> ChaosExecutionService:
    """ChaosExecutionService 싱글톤 인스턴스 반환."""
    global _chaos_execution_service_instance
    if _chaos_execution_service_instance is None:
        _chaos_execution_service_instance = ChaosExecutionService()
    return _chaos_execution_service_instance


def get_config_apply_service() -> ConfigApplyService:
    """ConfigApplyService 싱글톤 인스턴스 반환."""
    global _config_apply_service_instance
    if _config_apply_service_instance is None:
        _config_apply_service_instance = ConfigApplyService()
    return _config_apply_service_instance


__all__ = [
    # Chaos
    "ChaosExecutionService",
    "ExperimentExecutionResult",
    "DailyReportResult",
    "ApprovalCleanupResult",
    "PendingApprovalCheckResult",
    "get_chaos_execution_service",
    # Config Apply
    "ConfigApplyService",
    "get_config_apply_service",
]
