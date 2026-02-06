"""
SLA 알림 메시지 템플릿 모듈.

각 함수는 상태 비의존적(stateless)이며, dict를 반환합니다.
Warning/Critical/Recovered 메시지를 생성하는 순수 함수를 제공합니다.
"""

from __future__ import annotations

from typing import Any


def build_sla_warning_message(
    rtt_ms: float,
    threshold_ms: float,
    current_limit: int,
    previous_limit: int,
    gradient: float,
    rtt_change_percent: float | None = None,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """
    SLA Warning 메시지 생성.

    Returns:
        {title, severity, message, details, actions}
    """
    change_info = ""
    if rtt_change_percent is not None:
        change_info = f" (변화율: {rtt_change_percent:+.1f}%)"

    region_info = f" [{region}]" if region else ""

    return {
        "title": f"⚠️ SLA Warning: Response Time Threshold Exceeded{region_info}",
        "severity": "high",
        "message": (
            f"Response time ({rtt_ms:.1f}ms) exceeded warning threshold "
            f"({threshold_ms}ms){change_info}.\n"
            f"Throttle limit reduced: {previous_limit} → {current_limit}\n"
            f"Service: {service_name} | Gradient: {gradient:.3f}"
        ),
        "details": {
            "rtt_ms": rtt_ms,
            "threshold_ms": threshold_ms,
            "current_limit": current_limit,
            "previous_limit": previous_limit,
            "gradient": gradient,
            "rtt_change_percent": rtt_change_percent,
            "event_type": "sla_warning",
        },
        "actions": ["check_dashboard", "review_throttle_config"],
    }


def build_sla_critical_message(
    rtt_ms: float,
    threshold_ms: float,
    current_limit: int,
    previous_limit: int,
    reduction_percent: float,
    gradient: float,
    rtt_change_percent: float | None = None,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """SLA Critical 메시지 생성."""
    change_info = ""
    if rtt_change_percent is not None:
        change_info = f" (변화율: {rtt_change_percent:+.1f}%)"

    region_info = f" [{region}]" if region else ""

    return {
        "title": f"🔴 SLA CRITICAL: Severe Response Time Degradation{region_info}",
        "severity": "critical",
        "message": (
            f"CRITICAL: Response time ({rtt_ms:.1f}ms) exceeded critical threshold "
            f"({threshold_ms}ms){change_info}.\n"
            f"Aggressive throttling: {previous_limit} → {current_limit} "
            f"(-{reduction_percent}%)\n"
            f"Service: {service_name} | Gradient: {gradient:.3f}\n\n"
            f"Immediate action may be required."
        ),
        "details": {
            "rtt_ms": rtt_ms,
            "threshold_ms": threshold_ms,
            "current_limit": current_limit,
            "previous_limit": previous_limit,
            "reduction_percent": reduction_percent,
            "gradient": gradient,
            "rtt_change_percent": rtt_change_percent,
            "event_type": "sla_critical",
        },
        "actions": ["check_dashboard", "review_throttle_config", "escalate_oncall"],
    }


def build_sla_recovered_message(
    previous_limit: int,
    new_limit: int,
    rtt_ms: float,
    region: str | None = None,
    service_name: str = "default",
) -> dict[str, Any]:
    """SLA 복구 메시지 생성."""
    region_info = f" [{region}]" if region else ""

    return {
        "title": f"✅ SLA Recovered: Throttle Limit Restored{region_info}",
        "severity": "medium",
        "message": (
            f"Throttle limit recovered: {previous_limit} → {new_limit}\n"
            f"Current RTT: {rtt_ms:.1f}ms | Service: {service_name}"
        ),
        "details": {
            "previous_limit": previous_limit,
            "new_limit": new_limit,
            "rtt_ms": rtt_ms,
            "event_type": "limit_recovered",
        },
        "actions": ["verify_recovery"],
    }
