"""
Postmortem 동적 Action Items 생성 유틸리티.

타임라인 이벤트를 기반으로 동적 actions 및 recommendations를 생성하는 순수 함수.
Django 의존성 없이 사용 가능.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING


# 이벤트 타입별 Action 메시지 매핑
EVENT_ACTION_MAP = {
    "circuit_breaker_opened": "Circuit Breaker OPEN 전환",
    "circuit_breaker_half_opened": "Circuit Breaker 복구 시도 (HALF_OPEN)",
    "circuit_breaker_closed": "Circuit Breaker 정상 복구 (CLOSED)",
    "error_budget_critical": "Error Budget 임계치 경고",
    "error_budget_warning": "Error Budget 경고 수준 도달",
    "emergency_activated": "비상 모드 활성화",
    "kill_switch_activated": "Kill Switch 활성화",
    "dlq_item_added": "DLQ에 항목 적재됨",
    "dlq_replay_blocked": "DLQ Replay 차단됨",
}


def generate_dynamic_actions(
    timeline: list,
    affected_services: list,
    duration_seconds: float | None,
    current_timestamp: str | None = None,
) -> tuple[list, list]:
    """
    타임라인과 분석 결과를 기반으로 동적 action items 및 recommendations 생성.

    Args:
        timeline: 이벤트 타임라인 리스트
        affected_services: 영향받은 서비스 리스트
        duration_seconds: 인시던트 지속 시간 (초)
        current_timestamp: 현재 시각 (ISO 형식), 기본 메시지용

    Returns:
        tuple: (auto_actions, recommendations)
            - auto_actions: 자동 수행된 조치 리스트 (Google SRE 표준 구조)
            - recommendations: 권장 사항 문자열 리스트
    """
    auto_actions = []
    recommendations = []

    # 중복 방지를 위한 이벤트 추적 (이벤트 키, 서비스명)
    seen_actions: set[tuple[str, str]] = set()

    for event in timeline:
        event_type = event.get("event_type", "").lower()
        service = event.get("details", {}).get("service_name", "")
        timestamp = event.get("timestamp", "")

        for key, action_text in EVENT_ACTION_MAP.items():
            if key in event_type and (key, service) not in seen_actions:
                seen_actions.add((key, service))
                auto_actions.append(
                    {
                        "action": action_text,
                        "status": "completed",
                        "timestamp": timestamp,
                        "service": service,
                    }
                )
                break

    # 액션이 없으면 기본 메시지
    if not auto_actions:
        auto_actions.append(
            {
                "action": "인시던트 기록됨",
                "status": "completed",
                "timestamp": current_timestamp or "",
                "service": None,
            }
        )

    # Recommendations 생성 (복구 시간 기준)
    if duration_seconds is not None:
        if duration_seconds > 120:
            recommendations.append(f"복구 시간이 {duration_seconds:.0f}초로 2분을 초과함 - SLA 검토 필요")
        elif duration_seconds > 60:
            recommendations.append(f"복구 시간이 {duration_seconds:.0f}초로 목표(60초) 초과 - 개선 검토 권장")

    # Recommendations 생성 (다중 서비스 장애 기준)
    if len(affected_services) > 3:
        recommendations.append(f"다중 서비스 장애 ({len(affected_services)}개) - 공통 원인 분석 필요")

    # Recommendations 생성 (Fast Fail 미발생 검사)
    # CB OPEN이 발생했으나 HALF_OPEN 또는 CLOSED로 전환되지 않은 경우
    has_cb_open = any("circuit_breaker_opened" in (key, "") for key, _ in seen_actions)
    has_cb_recovery = any(key in ("circuit_breaker_half_opened", "circuit_breaker_closed") for key, _ in seen_actions)
    if has_cb_open and not has_cb_recovery:
        recommendations.append("Fast Fail 미동작 - CB 설정 점검 필요")

    # 기본 recommendation
    if not recommendations:
        recommendations.append("장애 근본 원인 분석 및 재발 방지 검토 권장")

    return auto_actions, recommendations
