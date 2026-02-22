"""
Config Apply Service

지연된 설정 변경 및 그레이스풀 설정 적용을 담당하는 서비스 레이어.
"""

from __future__ import annotations

import structlog
from typing import Any

from selfhealing.services.governance_checks import (
    GovernanceCheckMixin,
    check_all_governance,
)

logger = structlog.get_logger()


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

    _instance: ConfigApplyService | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

    def apply_pending_changes(self) -> dict[str, Any]:
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
                "config_apply_service.config_changes_blocked",
                governance_result=governance_result.block_message,
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
                return {
                    "status": "success",
                    "applied": 0,
                    "message": "No pending changes due",
                }

            applied_count = 0
            failed_count = 0
            results = []

            for change in due_changes:
                try:
                    result = config_manager.apply_pending_change(change.id)

                    if result.get("status") == "applied":
                        applied_count += 1
                        logger.info(
                            "config_apply_service.applied_pending_change",
                            change=change.id,
                        )
                    else:
                        failed_count += 1
                        logger.error(
                            "config_apply_service.failed_apply",
                            change=change.id,
                            result=result.get('error'),
                        )

                    results.append(
                        {
                            "id": change.id,
                            "config_type": change.config_type,
                            "status": result.get("status"),
                        }
                    )

                except Exception as e:
                    failed_count += 1
                    pending_service.mark_failed(change.id, str(e))
                    logger.error(
                        f"[ConfigApplyService] Exception applying {change.id}: {e}",
                        exc_info=True,
                    )
                    results.append(
                        {
                            "id": change.id,
                            "config_type": change.config_type,
                            "status": "error",
                            "error": str(e),
                        }
                    )

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
    ) -> dict[str, Any]:
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
            from selfhealing.services.in_progress_tracker import get_in_progress_tracker
            from selfhealing.services.pending_config import get_pending_config_service
            from selfhealing.services.runtime_config import get_runtime_config_manager

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


_config_apply_service_instance: ConfigApplyService | None = None


def get_config_apply_service() -> ConfigApplyService:
    """ConfigApplyService 싱글톤 인스턴스 반환."""
    global _config_apply_service_instance
    if _config_apply_service_instance is None:
        _config_apply_service_instance = ConfigApplyService()
    return _config_apply_service_instance
