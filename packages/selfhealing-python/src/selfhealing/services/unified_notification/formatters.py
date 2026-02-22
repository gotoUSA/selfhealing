"""
Unified Notification Slack Block Kit Formatters.

Actionable Alert formatters for Circuit Breaker and SLA notifications.
"""

from __future__ import annotations

from typing import Any

from .models import NotificationPayload, NotificationPriority

# =============================================================================
# Slack Block Kit Formatters - Actionable Alert
# =============================================================================


def format_cb_slack_blocks(
    payload: NotificationPayload,
    priority: NotificationPriority,
) -> dict[str, Any]:
    """
        Circuit Breaker 알림용 Slack Block Kit 메시지 포맷.

    Actionable Alert 설계 원칙:
        - 거버넌스 유지: 원클릭 해제 대신 Admin 제어판으로 이동
        - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
        - 안전성: 운영자가 상태 확인 후 판단 가능

        Args:
            payload: 알림 페이로드
            priority: 효과적 우선순위 (에스컬레이션 적용 후)

        Returns:
            Slack Block Kit 형식의 메시지 딕셔너리
    """
    severity_emoji = {
        NotificationPriority.CRITICAL: "🔴",
        NotificationPriority.HIGH: "🟠",
        NotificationPriority.MEDIUM: "🟡",
        NotificationPriority.LOW: "🔵",
        NotificationPriority.INFO: "⚪",
    }.get(priority, "⚪")

    metadata = payload.metadata or {}
    service_name = metadata.get("service_name", "unknown")
    trace_url = metadata.get("trace_url")
    trigger_time = metadata.get("trigger_time", "")

    # Actionable URLs
    dashboard_url = metadata.get("dashboard_url")
    admin_url = metadata.get("admin_url")
    runbook_url = metadata.get("runbook_url")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {payload.title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service:*\n{service_name}"},
                {"type": "mrkdwn", "text": f"*Priority:*\n{priority.value.upper()}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Message:*\n{payload.message}",
            },
        },
    ]

    # Trace URL 섹션 (있는 경우)
    if trace_url:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Trace:*\n<{trace_url}|View in Jaeger>",
                },
            }
        )

    # Actionable 버튼 섹션
    action_elements = []

    if dashboard_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📊 Dashboard",
                    "emoji": True,
                },
                "url": dashboard_url,
                "action_id": "view_dashboard",
            }
        )

    if admin_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "⚙️ Admin Panel",
                    "emoji": True,
                },
                "url": admin_url,
                "action_id": "view_admin",
                "style": "primary",
            }
        )

    if runbook_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "📖 Runbook",
                    "emoji": True,
                },
                "url": runbook_url,
                "action_id": "view_runbook",
            }
        )

    if action_elements:
        blocks.append(
            {
                "type": "actions",
                "elements": action_elements,
            }
        )

    # 컨텍스트 섹션 (타임스탬프)
    context_text = f"Event: {payload.category.value}"
    if trigger_time:
        context_text += f" | Time: {trigger_time}"

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": context_text,
                },
            ],
        }
    )

    return {"blocks": blocks}


def format_sla_slack_blocks(
    payload: NotificationPayload,
    priority: NotificationPriority,
) -> dict[str, Any]:
    """
    SLA 알림용 Slack Block Kit 메시지 포맷.

    RTT/Threshold/Limit/Service 필드와 함께
    RTT 변화율(%), Region 정보, Actionable 버튼을 포함합니다.
    URL은 ThrottleSlaAlertUrlBuilder에서 동적 생성됩니다.

    Args:
        payload: 알림 페이로드
        priority: 효과적 우선순위 (에스컬레이션 적용 후)

    Returns:
        Slack Block Kit 형식의 메시지 딕셔너리
    """
    from selfhealing.services.throttle.throttle_sla_alert_urls import (
        get_throttle_sla_alert_url_builder,
    )

    metadata = payload.metadata or {}
    service_name = metadata.get("service_name", "default")
    region = metadata.get("region")
    rtt_ms = metadata.get("rtt_ms", 0)
    rtt_change_percent = metadata.get("rtt_change_percent")

    # URL 빌더에서 동적 생성
    builder = get_throttle_sla_alert_url_builder()
    urls = builder.build_sla_alert_urls(
        service_name=service_name,
        event_type=metadata.get("event_type", "sla_warning"),
        rtt_ms=rtt_ms,
    )

    severity_emoji = {
        NotificationPriority.CRITICAL: "\U0001f534",
        NotificationPriority.HIGH: "\U0001f7e0",
        NotificationPriority.MEDIUM: "\U0001f7e1",
    }.get(priority, "\u26aa")

    fields = [
        {"type": "mrkdwn", "text": f"*RTT:*\n{rtt_ms:.1f}ms"},
        {"type": "mrkdwn", "text": f"*Threshold:*\n{metadata.get('threshold_ms', 0)}ms"},
        {"type": "mrkdwn", "text": f"*Current Limit:*\n{metadata.get('current_limit', 0)}"},
        {"type": "mrkdwn", "text": f"*Service:*\n{service_name}"},
    ]

    # RTT 변화율이 있으면 추가
    if rtt_change_percent is not None:
        fields.append({"type": "mrkdwn", "text": f"*RTT Change:*\n{rtt_change_percent:+.1f}%"})

    # Region이 있으면 추가
    if region:
        fields.append({"type": "mrkdwn", "text": f"*Region:*\n{region}"})

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {payload.title}",
                "emoji": True,
            },
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Details:*\n{payload.message}"},
        },
    ]

    # Actionable 버튼: URL이 설정된 것만 포함
    action_elements = []
    if urls.dashboard_url:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "\U0001f4ca Grafana Dashboard", "emoji": True},
                "url": urls.dashboard_url,
                "action_id": "view_throttle_dashboard",
            }
        )
    if urls.admin_url:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "\u2699\ufe0f Throttle Admin", "emoji": True},
                "url": urls.admin_url,
                "action_id": "view_throttle_admin",
                "style": "primary",
            }
        )
    if urls.runbook_url:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "\U0001f4d6 SLA Runbook", "emoji": True},
                "url": urls.runbook_url,
                "action_id": "view_sla_runbook",
            }
        )

    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})

    return {"blocks": blocks}


def format_cb_notification_with_actions(payload: NotificationPayload) -> dict[str, Any]:
    """
    Circuit Breaker 알림을 Actionable Alert 형식으로 포맷.

    이 함수는 SecurityNotificationService에서 호출되어
    Slack으로 전송될 메시지를 Actionable 버튼이 포함된 Block Kit 형식으로 변환합니다.

    Args:
        payload: 알림 페이로드

    Returns:
        Actionable 버튼이 포함된 Slack Block Kit 메시지
    """
    try:
        from selfhealing.services.emergency_mode import get_emergency_manager

        manager = get_emergency_manager()
        level = manager.get_current_level()

        # Emergency Level에 따른 우선순위 조정
        priority = payload.priority
        if level >= 3 and priority in (
            NotificationPriority.LOW,
            NotificationPriority.INFO,
            NotificationPriority.MEDIUM,
        ):
            priority = NotificationPriority.HIGH
        elif level >= 2 and priority in (
            NotificationPriority.LOW,
            NotificationPriority.INFO,
        ):
            priority = NotificationPriority.MEDIUM

    except ImportError:
        priority = payload.priority
    except Exception:
        priority = payload.priority

    return format_cb_slack_blocks(payload, priority)
