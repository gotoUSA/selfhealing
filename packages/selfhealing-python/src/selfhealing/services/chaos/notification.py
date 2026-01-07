"""
Chaos Experiment Notification.

카오스 실험 상태 변경 시 알림을 발송합니다.
Admin Deep Link 방식으로 거버넌스를 유지합니다.

Design Reference:
- 24_CHAOS_INTEGRATION_PLAN.md §3.6, §6.3
- 23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md §7.2

Principle:
- Slack Interactive 버튼 대신 Admin Deep Link 사용
- 거버넌스와 감사 추적 보장
- NotificationCategory.CHAOS 사용으로 독립적 중복 방지
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def send_chaos_experiment_alert(
    experiment_id: str,
    experiment_type: str,
    target_service: str,
    event_type: str,  # "started" | "stopped" | "failed"
    details: Dict[str, Any],
) -> bool:
    """
    카오스 실험 알림 발송.

    Slack Interactive 버튼 대신 Admin Deep Link를 사용하여
    거버넌스와 감사 추적을 보장합니다.

    Args:
        experiment_id: 실험 ID
        experiment_type: 실험 유형
        target_service: 대상 서비스
        event_type: 이벤트 유형 ("started", "stopped", "failed")
        details: 추가 상세 정보

    Returns:
        bool: 발송 성공 여부
    """
    try:
        from selfhealing.services.unified_notification import (
            get_unified_notification_manager,
            NotificationPayload,
            NotificationPriority,
            NotificationCategory,
        )
        from selfhealing.services.chaos.actionable_alert_urls import (
            get_chaos_actionable_alert_url_builder,
        )

        # Actionable URL 생성 (Admin Deep Link)
        url_builder = get_chaos_actionable_alert_url_builder()
        actionable_urls = url_builder.build_experiment_alert_urls(
            experiment_id=experiment_id,
            target_service=target_service,
        )

        # 이벤트 유형별 메시지
        title_map = {
            "started": f"🧪 Chaos Experiment Started: {target_service}",
            "stopped": f"✅ Chaos Experiment Completed: {target_service}",
            "failed": f"❌ Chaos Experiment Failed: {target_service}",
        }

        priority_map = {
            "started": NotificationPriority.INFO,
            "stopped": NotificationPriority.LOW,
            "failed": NotificationPriority.HIGH,
        }

        manager = get_unified_notification_manager()
        manager.notify(
            NotificationPayload(
                title=title_map.get(event_type, f"Chaos: {event_type}"),
                message=f"Experiment {experiment_id} ({experiment_type})",
                priority=priority_map.get(event_type, NotificationPriority.MEDIUM),
                category=NotificationCategory.CHAOS,
                source="chaos_experiment",
                dedup_key=f"chaos:{experiment_id}:{event_type}",
                metadata={
                    "experiment_id": experiment_id,
                    "experiment_type": experiment_type,
                    "target_service": target_service,
                    "event_type": event_type,
                    # Admin Deep Link URLs
                    "dashboard_url": actionable_urls.dashboard_url,
                    "admin_url": actionable_urls.admin_stop_url,
                    "runbook_url": actionable_urls.runbook_url,
                    **details,
                },
            )
        )

        logger.info(
            f"[ChaosNotification] Sent {event_type} alert for {experiment_id}"
        )
        return True

    except Exception as e:
        logger.warning(f"[ChaosNotification] Failed to send alert: {e}")
        return False
