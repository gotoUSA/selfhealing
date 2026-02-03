"""
Canary Rollout Audit Logging.

모든 Canary 액션에 대한 포렌식 로그.
롤아웃 시작, 프로모션, 롤백 등 모든 액션을 추적합니다.

Reference:
    - audit/self_audit.py
    - services/audit/__init__.py
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Usage:
    from selfhealing.services.canary.audit import log_canary_action

    log_canary_action(
        action="start",
        rollout=rollout,
        safety_check_result={"chaos_guard": "passed"},
    )
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from selfhealing.utils.time import utc_now

if TYPE_CHECKING:
    from selfhealing.services.canary.models import CanaryRollout

logger = logging.getLogger(__name__)


# 지원하는 Canary 액션 유형
CANARY_ACTIONS = [
    "create",  # 롤아웃 생성
    "start",  # 롤아웃 시작
    "promote",  # 다음 단계로 프로모션
    "rollback",  # 롤백
    "pause",  # 일시 중지
    "resume",  # 재개
    "complete",  # 완료
    "panic_rollback",  # 긴급 롤백
    "cancel",  # 취소
    "force_promote",  # 강제 프로모션 (메트릭 무시)
    "governance_blocked",  # 거버넌스 체크로 차단됨
    "governance_bypass",  # Break Glass로 거버넌스 우회함 (PIR 필수)
]


def log_canary_action(
    action: str,
    rollout: "CanaryRollout",
    safety_check_result: dict[str, Any] | None = None,
    additional_context: dict[str, Any] | None = None,
) -> None:
    """
    Canary Rollout 액션 Audit 로그.

    포렌식 및 컴플라이언스를 위해 모든 Canary 액션을 기록합니다.

    Args:
        action: 액션 유형 (start, promote, rollback, pause, resume 등)
        rollout: 롤아웃 정보
        safety_check_result: 사전 검사 결과 (카오스 충돌, 버전 체크 등)
        additional_context: 추가 컨텍스트 (실패 사유, 메트릭 등)

    Example:
        log_canary_action(
            action="start",
            rollout=rollout,
            safety_check_result={
                "chaos_guard": "passed",
                "config_lock": "acquired",
            },
            additional_context={
                "triggerd_by": "api",
            },
        )
    """
    # 해시 계산 (변경 추적용)
    previous_hash = _compute_hash(rollout.previous_values)
    new_hash = _compute_hash(rollout.new_values)

    # Audit 엔트리 구성
    audit_entry = {
        # 필수 식별 필드
        "canary_rollout_id": rollout.id,
        "config_type": rollout.config_type,
        "action": action,
        # 버전 해시 (값 비교용)
        "previous_version_hash": previous_hash,
        "new_version_hash": new_hash,
        # 상태 정보
        "state": rollout.state.value,
        "current_stage": (rollout.current_stage.name if rollout.current_stage else None),
        "current_stage_index": rollout.current_stage_index,
        "affected_clusters": rollout.affected_clusters,
        # 메타데이터
        "initiated_by": rollout.created_by,
        "reason": rollout.reason,
        "timestamp": utc_now().isoformat(),
        # 안전 검사 결과
        "safety_check_result": safety_check_result or {"checked": False},
    }

    # 추가 컨텍스트 병합
    if additional_context:
        audit_entry["additional_context"] = additional_context

    # 롤백인 경우 롤백 사유 추가
    if rollout.rollback_reason:
        audit_entry["rollback_reason"] = rollout.rollback_reason

    # 표준 로그 (항상)
    log_level = _get_log_level(action)
    logger.log(
        log_level,
        f"[CanaryAudit] {action}: rollout={rollout.id}, "
        f"config={rollout.config_type}, stage={rollout.current_stage_index}, "
        f"clusters={rollout.affected_clusters}",
    )

    # Audit 시스템 연동 (가능한 경우)
    _send_to_audit_system(action, rollout, audit_entry)


def log_canary_error(
    action: str,
    rollout_id: str,
    config_type: str,
    error: Exception,
    operator: str,
) -> None:
    """
    Canary 작업 실패 로그.

    Args:
        action: 시도한 액션
        rollout_id: 롤아웃 ID
        config_type: 설정 유형
        error: 발생한 예외
        operator: 작업자
    """
    audit_entry = {
        "event_type": "canary_error",
        "action": action,
        "rollout_id": rollout_id,
        "config_type": config_type,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "operator": operator,
        "timestamp": utc_now().isoformat(),
    }

    logger.error(f"[CanaryAudit] Error: action={action}, rollout={rollout_id}, " f"error={type(error).__name__}: {error}")

    try:
        from selfhealing.services.audit import log_system_control_audit

        log_system_control_audit(
            action=f"canary_{action}_error",
            target=config_type,
            result="failed",
            operator=operator,
            details=audit_entry,
        )
    except Exception as e:
        logger.debug(f"[CanaryAudit] Audit system error: {e}")


def log_canary_metrics_check(
    rollout_id: str,
    stage_name: str,
    metrics: dict[str, Any],
    passed: bool,
    failure_reason: str | None = None,
) -> None:
    """
    메트릭 검사 결과 로그.

    프로모션 전 메트릭 검사 결과를 기록합니다.

    Args:
        rollout_id: 롤아웃 ID
        stage_name: 단계 이름
        metrics: 수집된 메트릭
        passed: 검사 통과 여부
        failure_reason: 실패 사유 (통과 실패 시)
    """
    log_level = logging.INFO if passed else logging.WARNING

    logger.log(
        log_level,
        f"[CanaryAudit] Metrics check: rollout={rollout_id}, "
        f"stage={stage_name}, passed={passed}" + (f", reason={failure_reason}" if failure_reason else ""),
    )

    try:
        from selfhealing.services.audit import log_system_control_audit

        log_system_control_audit(
            action="canary_metrics_check",
            target=f"rollout:{rollout_id}",
            result="passed" if passed else "failed",
            operator="system",
            details={
                "rollout_id": rollout_id,
                "stage_name": stage_name,
                "metrics": metrics,
                "passed": passed,
                "failure_reason": failure_reason,
                "timestamp": utc_now().isoformat(),
            },
        )
    except Exception as e:
        logger.debug(f"[CanaryAudit] Audit system error: {e}")


def _compute_hash(values: dict[str, Any]) -> str:
    """
    설정값 해시 계산.

    동일한 값은 항상 동일한 해시를 생성합니다.
    """
    try:
        sorted_str = json.dumps(values, sort_keys=True, default=str)
        return hashlib.sha256(sorted_str.encode()).hexdigest()[:16]
    except Exception:
        return "hash_error"


def _get_log_level(action: str) -> int:
    """액션에 따른 로그 레벨 결정."""
    critical_actions = ["panic_rollback", "rollback"]
    warning_actions = ["force_promote", "pause"]

    if action in critical_actions:
        return logging.WARNING
    elif action in warning_actions:
        return logging.WARNING
    else:
        return logging.INFO


def _send_to_audit_system(
    action: str,
    rollout: "CanaryRollout",
    audit_entry: dict[str, Any],
) -> None:
    """Audit 시스템으로 로그 전송."""
    try:
        from selfhealing.services.audit import log_system_control_audit

        log_system_control_audit(
            action=f"canary_{action}",
            target=rollout.config_type,
            result="success",
            operator=rollout.created_by,
            details=audit_entry,
        )
    except ImportError:
        logger.debug("[CanaryAudit] Audit system not available")
    except Exception as e:
        logger.debug(f"[CanaryAudit] Audit log failed: {e}")
