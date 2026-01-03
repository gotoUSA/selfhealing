"""
Error Budget Enums and Constants

배포 동결 상태 Enum과 동적 임계값 관련 함수들을 정의합니다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict

from selfhealing.core.timezone import now


logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class FreezeStatus(str, Enum):
    """배포 동결 상태."""

    PROCEED = "proceed"
    """정상 - 배포 진행 가능."""

    CAUTION = "caution"
    """주의 - 경고 표시 후 진행 가능 (수동 확인 권장)."""

    WARNING = "warning"
    """경고 - 신규 기능 배포 자제, 안정화 우선."""

    FREEZE_RECOMMENDED = "freeze_recommended"
    """동결 권고 - 긴급 패치 외 모든 신규 배포 중단 권고."""


class OverrideType(str, Enum):
    """배포 동결 무시 유형."""

    HOTFIX = "hotfix"
    """긴급 버그 수정."""

    SECURITY_PATCH = "security_patch"
    """보안 패치."""

    EXECUTIVE_APPROVAL = "executive_approval"
    """경영진 승인."""

    ROLLBACK = "rollback"
    """롤백 배포."""


# =============================================================================
# Dynamic Threshold Getters (Runtime Config 참조)
# =============================================================================


def _get_error_budget_config() -> dict:
    """
    RuntimeConfigManager에서 Error Budget 설정을 가져옵니다.

    API로 동적 변경된 설정이 반영됩니다.
    """
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        return manager.get_error_budget_config()
    except Exception:
        # Fallback to defaults if runtime config unavailable
        return {}


def get_error_budget_thresholds() -> dict:
    """
    Error Budget 임계값을 가져옵니다 (동적 설정 지원).

    Returns:
        dict: {healthy, caution, warning, critical} 임계값 (%)
    """
    config = _get_error_budget_config()
    return {
        "healthy": config.get("threshold_healthy", 75.0),
        "caution": config.get("threshold_caution", 50.0),
        "warning": config.get("threshold_warning", 20.0),
        "critical": config.get("threshold_critical", 0.0),
    }


def get_burn_rate_thresholds() -> dict:
    """
    Burn Rate 임계값을 가져옵니다 (동적 설정 지원).

    Returns:
        dict: {fast_critical, fast_warning, slow_warning, slow_info} 임계값
    """
    config = _get_error_budget_config()
    return {
        "fast_critical": config.get("burn_rate_fast_critical", 14.4),
        "fast_warning": config.get("burn_rate_fast_warning", 6.0),
        "slow_warning": config.get("burn_rate_slow_warning", 3.0),
        "slow_info": config.get("burn_rate_slow_info", 1.0),
    }


# Legacy constants (for backward compatibility)
# 이 상수들은 더 이상 직접 사용하지 말고 get_*_thresholds() 함수를 사용하세요.
ERROR_BUDGET_THRESHOLDS = {
    "healthy": 75.0,
    "caution": 50.0,
    "warning": 20.0,
    "critical": 0.0,
}

BURN_RATE_THRESHOLDS = {
    "fast_critical": 14.4,
    "fast_warning": 6.0,
    "slow_warning": 3.0,
    "slow_info": 1.0,
}


# =============================================================================
# Fail-Safe Response Generators
# =============================================================================
#
# DESIGN PRINCIPLE: Fail-Open + Self-Reporting
# - Error Budget 시스템 장애 시 배포를 막는 것보다 허용하는 것이 더 안전함
# - CI/CD 파이프라인이 Error Budget 시스템 장애로 중단되면 안 됨
# - 운영자는 degraded_mode=True를 보고 수동 확인 필요성 인지
# - 🚨 중요: Fail-Safe 발동 시 즉시 알림 발송 (침묵하는 장애 방지)
#

# Fail-Safe 발동 횟수 추적 (Prometheus 메트릭용)
_failsafe_counter = 0


def _send_failsafe_alert(component: str, error_message: str, fallback_action: str) -> None:
    """
    Fail-Safe 발동 시 알림 발송.

    "침묵하는 장애" 방지를 위해 Fail-Safe가 작동하면
    즉시 운영팀에 알림을 보냅니다.
    """
    global _failsafe_counter
    _failsafe_counter += 1

    # 1. 로그 (항상 남김)
    logger.critical(
        f"[FAIL-SAFE] {component} 시스템 장애로 Fail-Safe 모드 전환. "
        f"Error: {error_message}, Fallback: {fallback_action}, "
        f"Count: {_failsafe_counter}"
    )

    # 2. AlertAdapter를 통한 알림 (설정된 경우)
    try:
        from selfhealing.adapters.alert import get_alert_adapter

        adapter = get_alert_adapter()
        if adapter is not None:
            adapter.alert_failsafe_activated(
                component=component,
                error_message=error_message,
                fallback_action=fallback_action,
            )
    except ImportError:
        # AlertAdapter가 설정되지 않은 경우 - 로그만 남김
        logger.warning("[FAIL-SAFE] AlertAdapter not configured, skipping alert")
    except Exception as alert_error:
        # 알림 발송 실패해도 Fail-Safe 응답은 반환해야 함
        logger.error(f"[FAIL-SAFE] Failed to send alert: {alert_error}")

    # 3. Prometheus 메트릭 증가 (가능한 경우)
    try:
        from selfhealing.services.metrics.recorders import record_failsafe_triggered

        record_failsafe_triggered(component=component)
    except ImportError:
        pass
    except Exception:
        pass


def get_failsafe_verdict_response(error_message: str) -> Dict[str, Any]:
    """
    Error Budget 시스템 장애 시 반환할 Fail-Safe 응답.

    Returns a PROCEED verdict to ensure deployments are not blocked
    when the Error Budget system is unavailable.

    🚨 IMPORTANT: 이 함수가 호출되면 자동으로 CRITICAL 알림이 발송됩니다.
    """
    # Fail-Safe 알림 발송 (침묵하는 장애 방지)
    _send_failsafe_alert(
        component="error_budget",
        error_message=error_message,
        fallback_action="PROCEED (배포 허용)",
    )

    return {
        "status": "degraded",  # 정상이 아님을 명시
        "data": {
            "verdict": {
                "status": FreezeStatus.PROCEED.value,  # Fail-open: 기본 허용
                "can_deploy": True,
                "requires_override": False,
                "has_active_override": False,
            },
            "message": "⚠️ Error Budget 시스템 일시적 오류. 기본값 PROCEED 적용됨.",
            "recommendation": "Error Budget 시스템 상태를 확인하세요. 현재 배포는 허용됩니다.",
            "reasons": [],
            "allowed_deployment_types": ["feature", "enhancement", "refactor", "hotfix", "security_patch", "rollback"],
        },
        "degraded_mode": True,
        "error": error_message,
        "failsafe_applied": True,
        "alert_sent": True,  # 알림이 발송되었음을 표시
        "timestamp": now().isoformat(),
    }


def get_failsafe_status_response(error_message: str) -> Dict[str, Any]:
    """
    Error Budget 상태 조회 실패 시 Fail-Safe 응답.

    Returns a healthy status to ensure systems do not falsely alarm
    when the Error Budget system is unavailable.

    🚨 IMPORTANT: 이 함수가 호출되면 자동으로 CRITICAL 알림이 발송됩니다.
    """
    # Fail-Safe 알림 발송 (침묵하는 장애 방지)
    _send_failsafe_alert(
        component="error_budget_status",
        error_message=error_message,
        fallback_action="HEALTHY 상태 가정",
    )

    return {
        "status": "degraded",
        "data": {
            "slo": {
                "name": "unknown",
                "target": 0.999,
                "target_percentage": "99.90%",
                "window_days": 30,
            },
            "budget": {
                "total_minutes": 43.2,
                "consumed_minutes": 0,
                "remaining_minutes": 43.2,
                "remaining_percent": 100.0,  # 알 수 없으면 full budget 가정
            },
            "burn_rate": {
                "rate_1h": 0.0,
                "rate_6h": 0.0,
                "is_fast_burn": False,
                "is_slow_burn": False,
            },
            "health": {
                "is_healthy": True,  # Fail-open: 건강하다고 가정
                "is_critical": False,
            },
        },
        "degraded_mode": True,
        "error": error_message,
        "failsafe_applied": True,
        "alert_sent": True,  # 알림이 발송되었음을 표시
        "timestamp": now().isoformat(),
    }
