"""
Throttle 감사 로그 및 CascadeEvent 연동 모듈.

감사 이벤트:
- throttle_limit_adjusted: limit 변경 시 기록
- throttle_emergency_sync: Emergency 연동 시 기록
- throttle_cb_sync: CB 연동 시 기록
- throttle_sla_breach: SLA 위반 시 기록

CascadeEvent 기록 정책:
- 비상 상황으로 인한 강제 강등만 기록 (일반 rate limit은 메트릭만)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 감사 이벤트 타입 상수
# =============================================================================

AUDIT_THROTTLE_LIMIT_ADJUSTED = "throttle_limit_adjusted"
AUDIT_THROTTLE_EMERGENCY_SYNC = "throttle_emergency_sync"
AUDIT_THROTTLE_CB_SYNC = "throttle_cb_sync"
AUDIT_THROTTLE_SLA_BREACH = "throttle_sla_breach"


def _build_audit_data(
    action: str,
    *,
    old_limit: int | None = None,
    new_limit: int | None = None,
    reason: str | None = None,
    trigger_source: str | None = None,
    emergency_level: int | None = None,
    applied_multiplier: float | None = None,
    service_name: str | None = None,
    cb_state: str | None = None,
    rtt_ms: float | None = None,
    threshold_ms: int | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """감사 데이터 딕셔너리 구성."""
    audit_data: dict[str, Any] = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # None이 아닌 값만 추가
    optional_fields = {
        "old_limit": old_limit,
        "new_limit": new_limit,
        "reason": reason,
        "trigger_source": trigger_source,
        "emergency_level": emergency_level,
        "applied_multiplier": applied_multiplier,
        "service_name": service_name,
        "cb_state": cb_state,
        "rtt_ms": rtt_ms,
        "threshold_ms": threshold_ms,
    }

    for key, value in optional_fields.items():
        if value is not None:
            audit_data[key] = value

    if extra_data:
        audit_data.update(extra_data)

    return audit_data


def record_throttle_audit(
    action: str,
    old_limit: int | None = None,
    new_limit: int | None = None,
    reason: str | None = None,
    trigger_source: str | None = None,
    emergency_level: int | None = None,
    applied_multiplier: float | None = None,
    service_name: str | None = None,
    cb_state: str | None = None,
    rtt_ms: float | None = None,
    threshold_ms: int | None = None,
    extra_data: dict[str, Any] | None = None,
) -> None:
    """
    Throttle 감사 이벤트 기록.

    Args:
        action: 감사 이벤트 유형 (AUDIT_THROTTLE_* 상수)
        old_limit: 이전 limit 값
        new_limit: 새 limit 값
        reason: 조정 사유
        trigger_source: 트리거 소스 (emergency, cb, gradient 등)
        emergency_level: Emergency 레벨 (0-3)
        applied_multiplier: 적용된 배율
        service_name: 서비스 이름
        cb_state: Circuit Breaker 상태
        rtt_ms: RTT (ms)
        threshold_ms: SLA 임계값 (ms)
        extra_data: 추가 데이터
    """
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()

        # 감사 데이터 구성
        audit_data = _build_audit_data(
            action,
            old_limit=old_limit,
            new_limit=new_limit,
            reason=reason,
            trigger_source=trigger_source,
            emergency_level=emergency_level,
            applied_multiplier=applied_multiplier,
            service_name=service_name,
            cb_state=cb_state,
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            extra_data=extra_data,
        )

        # CascadeEvent로 기록 (비상 상황 강등만)
        if action in (AUDIT_THROTTLE_EMERGENCY_SYNC, AUDIT_THROTTLE_CB_SYNC):
            _record_cascade_event(auditor, action, audit_data)

        logger.debug(f"[ThrottleAudit] Recorded {action}: {audit_data}")

    except ImportError:
        logger.debug("[ThrottleAudit] Audit module not available")
    except Exception as e:
        logger.warning(f"[ThrottleAudit] Failed to record audit: {e}")


def _record_cascade_event(auditor, action: str, audit_data: dict[str, Any]) -> None:
    """
    CascadeEvent로 Throttle 강등 이벤트 기록.

    비상 상황(Emergency/CB)으로 인한 강등만 기록.
    일반 Rate Limit Rejected는 메트릭만 기록.
    """
    try:
        trigger_type = _map_action_to_trigger_type(action)
        effects = _build_throttle_effects(audit_data)

        auditor.record(
            trigger_type=trigger_type,
            trigger_details=audit_data,
            effects=effects,
            namespace="default",
            triggered_by="throttle",
        )
        logger.debug(f"[ThrottleAudit] CascadeEvent recorded for {action}")

    except Exception as e:
        logger.warning(f"[ThrottleAudit] Failed to record CascadeEvent: {e}")


def _map_action_to_trigger_type(action: str) -> str:
    """감사 액션을 CascadeEvent trigger_type으로 매핑."""
    mapping = {
        AUDIT_THROTTLE_EMERGENCY_SYNC: "THROTTLE_EMERGENCY_SYNC",
        AUDIT_THROTTLE_CB_SYNC: "THROTTLE_CB_SYNC",
        AUDIT_THROTTLE_LIMIT_ADJUSTED: "THROTTLE_LIMIT_ADJUSTED",
        AUDIT_THROTTLE_SLA_BREACH: "THROTTLE_SLA_BREACH",
    }
    return mapping.get(action, "THROTTLE_UNKNOWN")


def _build_throttle_effects(audit_data: dict[str, Any]) -> list[dict[str, Any]]:
    """감사 데이터에서 CascadeEvent effects 구성."""
    effects = []

    old_limit = audit_data.get("old_limit")
    new_limit = audit_data.get("new_limit")

    if old_limit is not None and new_limit is not None:
        effects.append(
            {
                "action_type": "THROTTLE_LIMIT_CHANGE",
                "success": True,
                "details": {
                    "old_limit": old_limit,
                    "new_limit": new_limit,
                    "change_percent": round((new_limit - old_limit) / old_limit * 100, 2) if old_limit > 0 else 0,
                },
            }
        )

    emergency_level = audit_data.get("emergency_level")
    if emergency_level is not None:
        effects.append(
            {
                "action_type": "EMERGENCY_LEVEL_APPLIED",
                "success": True,
                "details": {
                    "level": emergency_level,
                    "multiplier": audit_data.get("applied_multiplier"),
                },
            }
        )

    cb_state = audit_data.get("cb_state")
    if cb_state:
        effects.append(
            {
                "action_type": "CB_STATE_APPLIED",
                "success": True,
                "details": {
                    "cb_state": cb_state,
                    "service_name": audit_data.get("service_name"),
                },
            }
        )

    return effects


def record_throttle_limit_adjusted(
    old_limit: int,
    new_limit: int,
    reason: str,
    trigger_source: str,
) -> None:
    """limit 변경 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_LIMIT_ADJUSTED,
        old_limit=old_limit,
        new_limit=new_limit,
        reason=reason,
        trigger_source=trigger_source,
    )


def record_throttle_emergency_sync(
    old_limit: int,
    new_limit: int,
    emergency_level: int,
    applied_multiplier: float,
) -> None:
    """Emergency 연동 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_EMERGENCY_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        emergency_level=emergency_level,
        applied_multiplier=applied_multiplier,
        trigger_source="emergency_mode",
    )


def record_throttle_cb_sync(
    old_limit: int,
    new_limit: int,
    service_name: str,
    cb_state: str,
) -> None:
    """CB 연동 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_CB_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        service_name=service_name,
        cb_state=cb_state,
        trigger_source="circuit_breaker",
    )


def record_throttle_sla_breach(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
) -> None:
    """SLA 위반 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_BREACH,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        trigger_source="sla_threshold",
    )
