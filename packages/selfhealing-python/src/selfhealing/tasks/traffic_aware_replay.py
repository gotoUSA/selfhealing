"""
🚦 Traffic-Aware Replay Task (Track 3)

Phase 2 구현: 트래픽 상태 인식 DLQ Replay

트래픽이 정상화되었을 때만 DLQ Replay를 수행합니다.
매 1분마다 Beat Schedule로 실행되며, 다음 조건을 모두 만족할 때만 replay:

Health Checks:
1. Circuit Breaker State == CLOSED
2. Error Budget > critical_threshold
3. Governance 체크 통과 (Kill Switch, Emergency Mode)

Reference:
- docs/self_healing/middleware_system/19_DLQ_AUTOMATION_BLUEPRINT.md §5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from selfhealing.tasks.base import BaseNotifyingTask
from selfhealing.tasks.notification_policy import (
    NotificationPolicy,
    NotificationTiming,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Traffic Health Status
# =============================================================================


@dataclass
class TrafficHealthStatus:
    """트래픽 건강 상태 결과."""

    is_healthy: bool
    reason: str
    checks: Dict[str, bool] = field(default_factory=dict)

    @classmethod
    def healthy(cls, checks: Dict[str, bool]) -> "TrafficHealthStatus":
        """건강한 상태 팩토리."""
        return cls(is_healthy=True, reason="All checks passed", checks=checks)

    @classmethod
    def unhealthy(cls, reason: str, checks: Dict[str, bool]) -> "TrafficHealthStatus":
        """비정상 상태 팩토리."""
        return cls(is_healthy=False, reason=reason, checks=checks)


def check_traffic_health(domain: Optional[str] = None) -> TrafficHealthStatus:
    """
    트래픽 건강 상태를 확인합니다.

    Checks:
    1. Circuit Breaker State (domain 지정 시)
    2. Error Budget Gate
    3. Governance (Kill Switch, Emergency Mode)

    Args:
        domain: 특정 도메인의 CB 상태 확인 (선택사항)

    Returns:
        TrafficHealthStatus with is_healthy flag and check results
    """
    checks: Dict[str, bool] = {}

    # Check 1: Circuit Breaker State (도메인이 지정된 경우에만)
    if domain:
        try:
            from selfhealing.services.circuit_breaker_service import (
                get_circuit_breaker_service,
                CircuitState,
            )

            cb_service = get_circuit_breaker_service()
            cb_state = cb_service.get_state(domain)
            checks["circuit_breaker"] = cb_state == CircuitState.CLOSED

            if not checks["circuit_breaker"]:
                return TrafficHealthStatus.unhealthy(
                    reason=f"Circuit breaker is {cb_state} for domain '{domain}'",
                    checks=checks,
                )
        except ImportError:
            logger.debug("[TrafficHealth] CircuitBreakerService not available, skipping CB check")
            checks["circuit_breaker"] = True  # 사용 불가 시 통과
        except Exception as e:
            logger.warning(f"[TrafficHealth] CB check failed: {e}")
            checks["circuit_breaker"] = True  # 예외 시 fail-open

    # Check 2: Error Budget Gate
    try:
        from selfhealing.services.error_budget_gate import get_error_budget_gate

        gate = get_error_budget_gate()
        checks["error_budget"] = gate.is_replay_allowed()

        if not checks["error_budget"]:
            return TrafficHealthStatus.unhealthy(
                reason="Error budget insufficient for replay",
                checks=checks,
            )
    except ImportError:
        logger.debug("[TrafficHealth] ErrorBudgetGate not available, skipping")
        checks["error_budget"] = True  # 사용 불가 시 통과
    except Exception as e:
        logger.warning(f"[TrafficHealth] Error budget check failed: {e}")
        checks["error_budget"] = True  # 예외 시 fail-open

    # Check 3: Governance (Kill Switch, Emergency Mode)
    try:
        from selfhealing.services.governance_checks import check_all_governance

        governance = check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,
            check_error_budget=False,  # 이미 위에서 체크함
            operation_name="traffic_aware_replay",
            service_name="TrafficAwareReplayTask",
            domain=domain or "dlq",
            audit_on_block=False,  # 배치 스케줄이므로 audit 생략
        )
        checks["governance"] = governance.allowed

        if not checks["governance"]:
            return TrafficHealthStatus.unhealthy(
                reason=governance.block_message,
                checks=checks,
            )
    except ImportError:
        logger.debug("[TrafficHealth] GovernanceChecks not available, skipping")
        checks["governance"] = True
    except Exception as e:
        logger.warning(f"[TrafficHealth] Governance check failed: {e}")
        checks["governance"] = True  # 예외 시 fail-open

    return TrafficHealthStatus.healthy(checks)


# =============================================================================
# Traffic-Aware Replay Task (Track 3)
# =============================================================================


class TrafficAwareReplayTask(BaseNotifyingTask):
    """
    트래픽 상태 인식 DLQ Replay.

    트래픽이 정상일 때만 DLQ Replay를 수행합니다.
    RuntimeConfig의 track3_enabled 설정에 따라 활성화/비활성화됩니다.

    스케줄: 1분마다
    큐: dlq
    알림: replay 성공 시 (ON_SUCCESS)

    Args:
        domain: 특정 도메인만 replay (선택사항)
        max_items: 최대 replay 건수 (선택사항, RuntimeConfig 우선)

    Returns:
        dict: {
            "status": "completed" | "skipped" | "disabled",
            "reason": str,
            "total": int,
            "success": int,
            "failed": int,
            "checks": dict,
        }
    """

    name = "selfhealing.traffic_aware_replay"

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AFTER,
        threshold=1,  # 1개 이상 replay 시 알림
        threshold_field="success",
        default_severity="info",
        cooldown_seconds=300,  # 5분
    )

    def run(
        self,
        domain: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Traffic-Aware Replay 실행.

        1. RuntimeConfig에서 Track 3 설정 로드
        2. Traffic Health Check 수행
        3. 모든 체크 통과 시 Replay 실행

        Args:
            domain: 특정 도메인만 replay
            max_items: 최대 replay 건수

        Returns:
            dict with status, counts, and check results
        """
        logger.info(f"[TrafficAwareReplay] Starting check (domain={domain})")

        # 1. RuntimeConfig에서 Track 3 설정 로드
        config = self._get_replay_automation_config()

        if not config.get("track3_enabled", False):
            logger.debug("[TrafficAwareReplay] Track 3 is disabled")
            return {
                "status": "disabled",
                "reason": "Track 3 is disabled in RuntimeConfig",
                "total": 0,
                "success": 0,
                "failed": 0,
                "checks": {},
            }

        effective_max_items = max_items or config.get("track3_max_items", 30)

        # 2. Traffic Health Check
        health_status = check_traffic_health(domain)

        if not health_status.is_healthy:
            logger.info(
                f"[TrafficAwareReplay] Skipping - traffic unhealthy: "
                f"{health_status.reason}"
            )
            return {
                "status": "skipped",
                "reason": health_status.reason,
                "total": 0,
                "success": 0,
                "failed": 0,
                "checks": health_status.checks,
            }

        # 3. Replay 실행
        logger.info(
            f"[TrafficAwareReplay] Health OK, executing replay "
            f"(max_items={effective_max_items})"
        )

        try:
            result = self._execute_replay(domain, effective_max_items)

            logger.info(
                f"[TrafficAwareReplay] Completed: "
                f"total={result['total']}, success={result['success']}, "
                f"failed={result['failed']}"
            )

            return {
                "status": "completed",
                "reason": "Replay executed successfully",
                "total": result["total"],
                "success": result["success"],
                "failed": result["failed"],
                "checks": health_status.checks,
            }

        except Exception as e:
            logger.error(f"[TrafficAwareReplay] Replay failed: {e}", exc_info=True)
            return {
                "status": "error",
                "reason": str(e),
                "total": 0,
                "success": 0,
                "failed": 0,
                "checks": health_status.checks,
            }

    def _get_replay_automation_config(self) -> Dict[str, Any]:
        """RuntimeConfig에서 replay_automation 설정을 로드합니다."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            return manager._get_config("replay_automation")
        except ImportError:
            logger.debug("[TrafficAwareReplay] RuntimeConfigManager not available")
            return {}
        except Exception as e:
            logger.warning(f"[TrafficAwareReplay] Failed to load config: {e}")
            return {}

    def _execute_replay(
        self, domain: Optional[str], max_items: int
    ) -> Dict[str, int]:
        """ReplayService를 통해 실제 replay를 수행합니다."""
        try:
            from selfhealing.services.replay_service import ReplayService

            service = ReplayService()
            batch_result = service.replay_batch(
                domain=domain,
                max_items=max_items,
            )

            return {
                "total": batch_result.total,
                "success": batch_result.success_count,
                "failed": batch_result.failed_count,
            }
        except ImportError:
            logger.error("[TrafficAwareReplay] ReplayService not available")
            raise RuntimeError("ReplayService not available")

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """결과에 따른 심각도 결정."""
        status = result.get("status", "")
        if status == "error":
            return "warning"
        elif result.get("failed", 0) > result.get("success", 0):
            return "warning"
        return "info"

    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        status = result.get("status", "")

        if status == "disabled":
            return "⏸️ Track 3 비활성화 - Traffic-Aware Replay 스킵"
        elif status == "skipped":
            return f"⏭️ Traffic-Aware Replay 스킵: {result.get('reason', '')}"
        elif status == "error":
            return f"❌ Traffic-Aware Replay 오류: {result.get('reason', '')}"
        elif status == "completed":
            total = result.get("total", 0)
            success = result.get("success", 0)
            failed = result.get("failed", 0)
            if total == 0:
                return "✅ Traffic-Aware Replay 완료 - 대기 항목 없음"
            return f"✅ Traffic-Aware Replay: {success}/{total} 성공, {failed} 실패"

        return "Traffic-Aware Replay 완료"


# =============================================================================
# Task Registry for Celery
# =============================================================================


# Task 목록 (register_with_celery에서 사용)
TRAFFIC_AWARE_TASKS = [
    TrafficAwareReplayTask,
]


def register_traffic_aware_tasks_with_celery(app) -> None:
    """Celery 앱에 Traffic-Aware 태스크를 등록합니다."""
    for task_class in TRAFFIC_AWARE_TASKS:
        app.register_task(task_class())
        logger.debug(f"[TrafficAware] Registered task: {task_class.name}")


def get_traffic_aware_beat_schedule() -> Dict[str, Any]:
    """
    Traffic-Aware Replay Beat 스케줄을 반환합니다.

    Returns:
        dict: Celery Beat schedule configuration
    """
    from celery.schedules import crontab

    return {
        # Track 3: Traffic-Aware Replay - 1분마다
        "traffic-aware-replay": {
            "task": "selfhealing.traffic_aware_replay",
            "schedule": crontab(minute="*"),  # 매 1분
            "options": {"queue": "dlq"},
            "kwargs": {},  # RuntimeConfig에서 동적으로 로드
        },
    }


__all__ = [
    "TrafficHealthStatus",
    "check_traffic_health",
    "TrafficAwareReplayTask",
    "TRAFFIC_AWARE_TASKS",
    "register_traffic_aware_tasks_with_celery",
    "get_traffic_aware_beat_schedule",
]
