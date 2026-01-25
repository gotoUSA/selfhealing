"""
Recovery Celery Tasks.

Celery 기반 복구 태스크 모듈입니다.

주요 태스크:
- check_recovery_trigger_task: 복구 트리거 조건 확인
- execute_recovery_step_task: 복구 단계 실행
- monitor_recovery_health_task: 복구 중 건강 상태 모니터링
- check_stale_pending_recoveries_task: 방치된 복구 승인 알림

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#10.2.4
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [21] CeleryTaskSettings 참조.
"""

import logging
from typing import Optional, Any, Dict
from datetime import datetime, timezone

# Celery는 선택적 의존성
try:
    from celery import shared_task
except ImportError:
    # Celery가 없으면 dummy decorator
    def shared_task(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

from selfhealing.services.coordination.enums import RecoveryStatus
from selfhealing.services.coordination.recovery_coordinator import (
    get_recovery_coordinator,
)
from selfhealing.services.coordination.recovery_circuit_breaker import (
    get_recovery_circuit_breaker,
    RecoveryMetricsSnapshot,
)
from selfhealing.services.coordination.pending_recovery_approval import (
    get_pending_recovery_approval_manager,
)
from selfhealing.services.coordination.regional_recovery_policy import (
    get_regional_recovery_policy_engine,
)
from selfhealing.settings.celery_task import get_celery_task_settings
from selfhealing.settings.recovery_tasks import get_recovery_tasks_settings


logger = logging.getLogger(__name__)


def _get_task_settings():
    """Get CeleryTaskSettings (cached)."""
    return get_celery_task_settings()


# =============================================================================
# Constants (from CeleryTaskSettings)
# =============================================================================

def _get_trigger_check_interval() -> int:
    """Get trigger check interval from settings."""
    return _get_task_settings().trigger_check_interval

def _get_health_monitor_interval() -> int:
    """Get health monitor interval from settings."""
    return _get_task_settings().health_monitor_interval

def _get_stale_check_interval() -> int:
    """Get stale check interval from settings."""
    return _get_task_settings().stale_check_interval

def _get_max_retries() -> int:
    """Get max retries from settings."""
    return _get_task_settings().max_retries

def _get_default_retry_delay() -> int:
    """Get default retry delay from settings."""
    return _get_task_settings().default_retry_delay


# =============================================================================
# Celery Task 설정값 캐싱 (모듈 로드 시점)
# Celery는 데코레이터 인자에서 함수 호출을 지원하므로 getter 직접 사용 가능
# =============================================================================

_recovery_settings = get_recovery_tasks_settings()


# =============================================================================
# Task: Check Recovery Trigger
# =============================================================================

@shared_task(
    name="selfhealing.check_recovery_trigger",
    bind=True,
    max_retries=_recovery_settings.check_trigger_max_retries,
    default_retry_delay=_recovery_settings.check_trigger_retry_delay,
    queue="selfhealing_recovery",
)
def check_recovery_trigger_task(
    self,
    namespace: str = "global",
    error_rate: Optional[float] = None,
    success_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    복구 트리거 조건 확인 태스크.
    
    Emergency 상태에서 복구 조건이 충족되었는지 확인하고,
    조건 충족 시 복구를 시작합니다.
    
    복구 조건:
    - error_rate < 10% (10분 이상 유지)
    - 또는 success_rate >= 90% (10분 이상 유지)
    
    Args:
        namespace: 네임스페이스
        error_rate: 현재 에러율 (선택, 없으면 메트릭에서 조회)
        success_rate: 현재 성공률 (선택, 없으면 메트릭에서 조회)
    
    Returns:
        Dict containing:
        - triggered: 복구가 트리거되었는지
        - session_id: 시작된 세션 ID (있으면)
        - reason: 트리거 이유 또는 불발 이유
    """
    logger.info(
        f"[check_recovery_trigger] namespace={namespace}, "
        f"error_rate={error_rate}, success_rate={success_rate}"
    )
    
    try:
        coordinator = get_recovery_coordinator()
        
        # 현재 상태 확인
        current_status = coordinator.get_current_status()
        
        if current_status != RecoveryStatus.EMERGENCY:
            return {
                "triggered": False,
                "reason": f"Not in EMERGENCY state: {current_status.value}",
                "namespace": namespace,
            }
        
        # 메트릭에서 에러율 조회 (없으면 전달받은 값 사용)
        if error_rate is None:
            error_rate = _fetch_current_error_rate(namespace)
        
        if success_rate is None:
            success_rate = 1.0 - error_rate if error_rate is not None else None
        
        # 복구 조건 확인
        policy_engine = get_regional_recovery_policy_engine()
        config = policy_engine.get_config(namespace)
        
        stability_minutes = config.stability_check_minutes
        recovery_threshold = config.recovery_error_threshold
        
        if error_rate is not None and error_rate < recovery_threshold:
            # 안정화 조건 확인 (별도 히스토리 필요)
            is_stable = _check_stability_duration(
                namespace=namespace,
                error_rate=error_rate,
                required_minutes=stability_minutes,
            )
            
            if not is_stable:
                return {
                    "triggered": False,
                    "reason": (
                        f"Stability not yet confirmed "
                        f"(need {stability_minutes} minutes < {recovery_threshold:.0%})"
                    ),
                    "namespace": namespace,
                    "current_error_rate": error_rate,
                }
            
            # 수동 승인 필요 여부 확인
            if config.require_manual_approval:
                approval_manager = get_pending_recovery_approval_manager()
                
                # 기존 대기 중인 승인 요청이 있는지 확인
                existing = approval_manager.get_request_by_session_or_pending(
                    namespace=namespace
                )
                
                if existing is None:
                    # 새 승인 요청 생성
                    request = approval_manager.create_request(
                        session_id=f"pending-{namespace}-{datetime.now(timezone.utc).isoformat()}",
                        namespace=namespace,
                        trigger_level="RECOVERY",
                        timeout_minutes=config.approval_timeout_minutes,
                        metadata={
                            "error_rate": error_rate,
                            "triggered_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    
                    return {
                        "triggered": False,
                        "reason": "Manual approval required",
                        "approval_request_id": request.request_id,
                        "namespace": namespace,
                        "status": RecoveryStatus.READY_TO_RESTORE.value,
                    }
                
                # 이미 승인 대기 중
                return {
                    "triggered": False,
                    "reason": "Waiting for manual approval",
                    "approval_request_id": existing.request_id,
                    "namespace": namespace,
                }
            
            # 자동 복구 시작
            session = coordinator.start_recovery(
                namespace=namespace,
                trigger_source="automatic",
            )
            
            logger.info(
                f"[check_recovery_trigger] Recovery STARTED: "
                f"session_id={session.session_id}, namespace={namespace}"
            )
            
            # 다음 스텝 실행 예약
            execute_recovery_step_task.delay(
                session_id=session.session_id,
                namespace=namespace,
            )
            
            return {
                "triggered": True,
                "session_id": session.session_id,
                "reason": f"Error rate {error_rate:.1%} < threshold {recovery_threshold:.0%}",
                "namespace": namespace,
            }
        
        return {
            "triggered": False,
            "reason": f"Error rate {error_rate:.1%} >= threshold {recovery_threshold:.0%}",
            "namespace": namespace,
            "current_error_rate": error_rate,
        }
        
    except Exception as e:
        logger.exception(f"[check_recovery_trigger] Error: {e}")
        raise self.retry(exc=e)


# =============================================================================
# Task: Execute Recovery Step
# =============================================================================

@shared_task(
    name="selfhealing.execute_recovery_step",
    bind=True,
    max_retries=_recovery_settings.execute_step_max_retries,
    default_retry_delay=_recovery_settings.execute_step_retry_delay,
    queue="selfhealing_recovery",
)
def execute_recovery_step_task(
    self,
    session_id: str,
    namespace: str = "global",
) -> Dict[str, Any]:
    """
    복구 단계 실행 태스크.
    
    현재 복구 세션의 다음 단계를 실행하고,
    더 남은 단계가 있으면 다음 실행을 예약합니다.
    
    Args:
        session_id: 복구 세션 ID
        namespace: 네임스페이스
    
    Returns:
        Dict containing:
        - step_executed: 실행된 단계 이름
        - status: 현재 상태
        - completed: 전체 복구 완료 여부
    """
    logger.info(
        f"[execute_recovery_step] session_id={session_id}, namespace={namespace}"
    )
    
    try:
        coordinator = get_recovery_coordinator()
        
        # 세션 확인
        session = coordinator.get_session(namespace, session_id)
        if session is None:
            return {
                "error": f"Session not found: {session_id}",
                "status": "not_found",
            }
        
        # 중단 또는 완료된 세션 확인
        if session.status in [RecoveryStatus.ABORTED, RecoveryStatus.COMPLETED]:
            return {
                "step_executed": None,
                "status": session.status.value,
                "completed": session.status == RecoveryStatus.COMPLETED,
                "reason": f"Session already {session.status.value}",
            }
        
        # 회로 차단기 확인
        circuit_breaker = get_recovery_circuit_breaker()
        if circuit_breaker.is_permanently_open(namespace):
            # 영구 차단됨 - 세션 중단
            coordinator.abort_recovery(
                session_id=session_id,
                reason="CircuitBreaker permanently open",
            )
            return {
                "step_executed": None,
                "status": RecoveryStatus.ABORTED.value,
                "completed": False,
                "reason": "CircuitBreaker permanently open - manual intervention required",
            }
        
        # 다음 단계 실행
        result = coordinator.execute_next_step(session_id)
        
        if result is None:
            # 모든 단계 완료
            return {
                "step_executed": None,
                "status": RecoveryStatus.COMPLETED.value,
                "completed": True,
                "reason": "All steps completed",
            }
        
        step_name = result.get("step_name", "unknown")
        success = result.get("success", False)
        
        if not success:
            # 단계 실패 - 재시도 또는 중단
            error_msg = result.get("error", "Unknown error")
            logger.warning(
                f"[execute_recovery_step] Step failed: {step_name}, error={error_msg}"
            )
            
            # 재시도 가능 여부 확인
            retry_count = result.get("retry_count", 0)
            max_retries = result.get("max_retries", 3)
            
            if retry_count < max_retries:
                # 지연 후 재시도
                delay = min(30 * (2 ** retry_count), 300)  # 최대 5분
                execute_recovery_step_task.apply_async(
                    args=[session_id, namespace],
                    countdown=delay,
                )
                
                return {
                    "step_executed": step_name,
                    "status": "retrying",
                    "completed": False,
                    "retry_count": retry_count + 1,
                    "next_retry_delay": delay,
                }
            
            # 최대 재시도 초과 - 세션 중단
            coordinator.abort_recovery(
                session_id=session_id,
                reason=f"Step {step_name} failed after {max_retries} retries",
            )
            
            return {
                "step_executed": step_name,
                "status": RecoveryStatus.ABORTED.value,
                "completed": False,
                "reason": f"Max retries exceeded for step {step_name}",
            }
        
        # 성공 - 대기 시간 확인
        wait_seconds = result.get("wait_before_next", 0)
        
        # 다음 단계가 있으면 예약
        session = coordinator.get_session(namespace, session_id)
        if session and session.status == RecoveryStatus.RECOVERING:
            if wait_seconds > 0:
                # 지연 후 다음 단계
                execute_recovery_step_task.apply_async(
                    args=[session_id, namespace],
                    countdown=wait_seconds,
                )
            else:
                # 즉시 다음 단계
                execute_recovery_step_task.delay(session_id, namespace)
        
        return {
            "step_executed": step_name,
            "status": RecoveryStatus.RECOVERING.value,
            "completed": False,
            "next_step_delay": wait_seconds,
        }
        
    except Exception as e:
        logger.exception(f"[execute_recovery_step] Error: {e}")
        raise self.retry(exc=e)


# =============================================================================
# Task: Monitor Recovery Health
# =============================================================================

@shared_task(
    name="selfhealing.monitor_recovery_health",
    bind=True,
    max_retries=_recovery_settings.monitor_recovery_max_retries,
    default_retry_delay=_recovery_settings.monitor_recovery_retry_delay,
    queue="selfhealing_recovery",
)
def monitor_recovery_health_task(
    self,
    namespace: str = "global",
    error_rate: Optional[float] = None,
    total_requests: int = 100,
) -> Dict[str, Any]:
    """
    복구 중 건강 상태 모니터링 태스크.
    
    복구 진행 중 시스템 건강 상태를 확인하고,
    재장애 발생 시 회로 차단기를 트립합니다.
    
    Args:
        namespace: 네임스페이스
        error_rate: 현재 에러율 (선택, 없으면 메트릭에서 조회)
        total_requests: 총 요청 수
    
    Returns:
        Dict containing:
        - healthy: 건강 상태
        - tripped: 회로 차단 여부
        - should_re_escalate: 재에스컬레이션 필요 여부
    """
    logger.debug(f"[monitor_recovery_health] namespace={namespace}")
    
    try:
        coordinator = get_recovery_coordinator()
        
        # 복구 중이 아니면 스킵
        current_status = coordinator.get_current_status()
        if current_status != RecoveryStatus.RECOVERING:
            return {
                "healthy": True,
                "tripped": False,
                "reason": f"Not recovering: {current_status.value}",
            }
        
        # 에러율 조회
        if error_rate is None:
            error_rate = _fetch_current_error_rate(namespace)
        
        if error_rate is None:
            return {
                "healthy": True,
                "tripped": False,
                "reason": "No error rate available",
            }
        
        # 회로 차단기 확인
        circuit_breaker = get_recovery_circuit_breaker()
        
        snapshot = RecoveryMetricsSnapshot(
            namespace=namespace,
            total_requests=total_requests,
            failure_count=int(total_requests * error_rate),
            error_rate=error_rate,
        )
        
        result = circuit_breaker.check_and_trip(namespace, snapshot)
        
        if result["tripped"]:
            logger.warning(
                f"[monitor_recovery_health] CircuitBreaker TRIPPED: "
                f"namespace={namespace}, reason={result['reason']}"
            )
            
            # 재에스컬레이션 필요 시 Emergency 재진입
            if result.get("should_re_escalate"):
                # 현재 세션 중단
                active_session = coordinator.get_active_session()
                if active_session:
                    coordinator.abort_recovery(
                        session_id=active_session.session_id,
                        reason="Re-escalation due to re-failure",
                    )
                
                # Emergency 재진입 (별도 태스크 또는 이벤트로 처리)
                logger.critical(
                    f"[monitor_recovery_health] RE-ESCALATION required: "
                    f"namespace={namespace}"
                )
        
        return {
            "healthy": not result["tripped"],
            "tripped": result["tripped"],
            "should_re_escalate": result.get("should_re_escalate", False),
            "state": result["state"],
            "trip_count": result.get("trip_count", 0),
            "current_error_rate": error_rate,
        }
        
    except Exception as e:
        logger.exception(f"[monitor_recovery_health] Error: {e}")
        raise self.retry(exc=e)


# =============================================================================
# Task: Check Stale Pending Recoveries
# =============================================================================

@shared_task(
    name="selfhealing.check_stale_pending_recoveries",
    bind=True,
    max_retries=_recovery_settings.cleanup_stale_max_retries,
    queue="selfhealing_notifications",
)
def check_stale_pending_recoveries_task(
    self,
    stale_threshold_minutes: int = 30,
) -> Dict[str, Any]:
    """
    방치된 복구 승인 요청 확인 태스크.
    
    지정된 시간 이상 방치된 승인 요청을 확인하고,
    알림을 발송합니다.
    
    Args:
        stale_threshold_minutes: 방치 기준 시간 (분)
    
    Returns:
        Dict containing:
        - stale_count: 방치된 요청 수
        - reminded_count: 알림 발송된 수
        - expired_count: 만료 처리된 수
    """
    logger.info(
        f"[check_stale_pending_recoveries] "
        f"threshold={stale_threshold_minutes} minutes"
    )
    
    try:
        manager = get_pending_recovery_approval_manager()
        
        # 만료 처리
        expired = manager.expire_old_requests()
        expired_count = len(expired)
        
        if expired_count > 0:
            logger.warning(
                f"[check_stale_pending_recoveries] "
                f"Expired {expired_count} requests"
            )
        
        # 방치된 요청 확인 및 리마인더 발송
        reminded = manager.check_and_send_reminders()
        reminded_count = len(reminded)
        
        # 방치된 요청 목록
        stale = manager.list_stale_requests(stale_threshold_minutes)
        stale_count = len(stale)
        
        if stale_count > 0:
            logger.warning(
                f"[check_stale_pending_recoveries] "
                f"{stale_count} stale requests found"
            )
        
        return {
            "stale_count": stale_count,
            "reminded_count": reminded_count,
            "expired_count": expired_count,
            "stale_requests": [
                {
                    "request_id": r.request_id,
                    "namespace": r.namespace,
                    "waiting_minutes": r.get_waiting_time_minutes(),
                }
                for r in stale
            ],
        }
        
    except Exception as e:
        logger.exception(f"[check_stale_pending_recoveries] Error: {e}")
        return {
            "error": str(e),
            "stale_count": 0,
            "reminded_count": 0,
            "expired_count": 0,
        }


# =============================================================================
# Task: Cleanup Old Recovery Sessions
# =============================================================================

@shared_task(
    name="selfhealing.cleanup_old_recovery_sessions",
    queue="selfhealing_maintenance",
)
def cleanup_old_recovery_sessions_task(
    max_age_hours: int = 168,  # 7일
) -> Dict[str, Any]:
    """
    오래된 복구 세션 정리 태스크.
    
    지정된 기간 이상 된 완료/중단된 세션을 정리합니다.
    
    Args:
        max_age_hours: 보관 기간 (시간)
    
    Returns:
        Dict containing:
        - cleaned_count: 정리된 세션 수
    """
    logger.info(f"[cleanup_old_recovery_sessions] max_age={max_age_hours}h")
    
    try:
        approval_manager = get_pending_recovery_approval_manager()
        
        # 오래된 승인 요청 정리
        cleaned = approval_manager.cleanup_old_requests(max_age_hours=max_age_hours)
        
        logger.info(
            f"[cleanup_old_recovery_sessions] Cleaned {cleaned} old requests"
        )
        
        return {
            "cleaned_count": cleaned,
        }
        
    except Exception as e:
        logger.exception(f"[cleanup_old_recovery_sessions] Error: {e}")
        return {
            "error": str(e),
            "cleaned_count": 0,
        }


# =============================================================================
# Helper Functions
# =============================================================================

def _fetch_current_error_rate(namespace: str) -> Optional[float]:
    """
    현재 에러율 조회.
    
    실제 구현에서는 Prometheus/메트릭 서비스에서 조회합니다.
    
    Args:
        namespace: 네임스페이스
    
    Returns:
        에러율 (0.0 ~ 1.0) 또는 None
    """
    # TODO: 실제 메트릭 서비스 연동
    # 현재는 placeholder
    try:
        # from selfhealing.services.metrics import get_error_rate
        # return get_error_rate(namespace=namespace, window_minutes=5)
        return None
    except Exception:
        return None


def _check_stability_duration(
    namespace: str,
    error_rate: float,
    required_minutes: int,
) -> bool:
    """
    안정화 지속 시간 확인.
    
    지정된 시간 동안 에러율이 임계값 미만으로 유지되었는지 확인합니다.
    
    Args:
        namespace: 네임스페이스
        error_rate: 현재 에러율
        required_minutes: 필요한 안정화 시간 (분)
    
    Returns:
        안정화 여부
    """
    # TODO: 실제 히스토리 서비스 연동
    # 현재는 placeholder - 항상 True 반환
    try:
        # from selfhealing.services.metrics import check_error_rate_stable
        # return check_error_rate_stable(
        #     namespace=namespace,
        #     threshold=0.10,
        #     duration_minutes=required_minutes,
        # )
        return True
    except Exception:
        return True


# =============================================================================
# Beat Schedule Configuration
# =============================================================================

def get_recovery_beat_schedule() -> Dict[str, Any]:
    """
    Recovery 태스크용 Celery Beat 스케줄.
    
    RecoveryTasksSettings에서 interval 설정을 가져와 스케줄을 생성합니다.
    celery.py에서 이 함수를 호출하여 스케줄을 등록합니다.
    
    Returns:
        Beat 스케줄 딕셔너리
    """
    settings = get_recovery_tasks_settings()
    
    return {
        "check-recovery-trigger-every-minute": {
            "task": "selfhealing.check_recovery_trigger",
            "schedule": settings.trigger_check_interval,
            "args": ("global",),
            "options": {"queue": "selfhealing_recovery"},
        },
        "monitor-recovery-health-every-30s": {
            "task": "selfhealing.monitor_recovery_health",
            "schedule": settings.health_monitor_interval,
            "args": ("global",),
            "options": {"queue": "selfhealing_recovery"},
        },
        "check-stale-pending-every-10min": {
            "task": "selfhealing.check_stale_pending_recoveries",
            "schedule": settings.stale_check_interval * 60,
            "options": {"queue": "selfhealing_notifications"},
        },
        "cleanup-old-sessions-daily": {
            "task": "selfhealing.cleanup_old_recovery_sessions",
            "schedule": 86400,  # 24시간 (고정)
            "options": {"queue": "selfhealing_maintenance"},
        },
    }
