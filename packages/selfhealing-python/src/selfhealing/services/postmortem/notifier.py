"""
Postmortem Notification Service.

Postmortem 생성 시 알림을 발송합니다.
Slack Block Kit 형식과 Webhook을 지원합니다.

Features:
- Postmortem 자동 생성 알림
- 그룹 Postmortem 생성 알림
- Emergency Postmortem 생성 알림
- Slack Block Kit 메시지 구조
- 딥링크 버튼 포함

Environment Variables:
- POSTMORTEM_NOTIFICATION_ENABLED: 알림 활성화 여부 (기본: true)
- POSTMORTEM_SLACK_WEBHOOK: Slack Incoming Webhook URL
- POSTMORTEM_NOTIFICATION_CHANNELS: 활성화할 채널 (기본: slack)
"""

from __future__ import annotations

import json
import structlog
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger()


@dataclass
class PostmortemNotificationConfig:
    """Postmortem 알림 설정."""

    enabled: bool = True
    slack_webhook_url: str = ""
    channels: list[str] = field(default_factory=lambda: ["slack"])

    @classmethod
    def from_env(cls) -> PostmortemNotificationConfig:
        """환경변수에서 설정 로드."""
        enabled_str = os.getenv("POSTMORTEM_NOTIFICATION_ENABLED", "true")
        enabled = enabled_str.lower() in ("true", "1", "yes")

        channels_str = os.getenv("POSTMORTEM_NOTIFICATION_CHANNELS", "slack")
        channels = [ch.strip() for ch in channels_str.split(",") if ch.strip()]

        return cls(
            enabled=enabled,
            slack_webhook_url=os.getenv("POSTMORTEM_SLACK_WEBHOOK", ""),
            channels=channels,
        )


@dataclass
class PostmortemNotificationPayload:
    """Postmortem 알림 페이로드."""

    incident_id: str
    """Postmortem ID."""

    title: str
    """알림 제목."""

    service_name: str
    """영향받은 서비스."""

    duration_seconds: float
    """인시던트 지속 시간."""

    generation_type: str
    """생성 유형: auto, group, emergency."""

    deep_links: dict[str, str | None] = field(default_factory=dict)
    """딥링크 모음."""

    affected_services: list[str] = field(default_factory=list)
    """영향받은 서비스 목록 (그룹 Postmortem용)."""

    severity: str = "medium"
    """심각도: low, medium, high, critical."""

    namespace: str = "default"
    """네임스페이스."""

    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """생성 시각."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "service_name": self.service_name,
            "duration_seconds": self.duration_seconds,
            "generation_type": self.generation_type,
            "deep_links": self.deep_links,
            "affected_services": self.affected_services,
            "severity": self.severity,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class SlackBlockKitBuilder:
    """
    Slack Block Kit 메시지 빌더.

    Postmortem 알림용 Block Kit 구조를 생성합니다.
    """

    @staticmethod
    def build_postmortem_message(payload: PostmortemNotificationPayload) -> dict:
        """
        Postmortem 알림용 Block Kit 메시지 생성.

        구조:
        - Header: Postmortem 생성 알림
        - Section: 인시던트 요약
        - Fields: 서비스, 지속시간, 영향도
        - Actions: 딥링크 버튼들
        - Context: 생성 시각, 자동/수동 구분
        """
        blocks = []

        # Header
        generation_emoji = {
            "auto": "🔄",
            "group": "📦",
            "emergency": "🚨",
        }.get(payload.generation_type, "📋")

        header_text = f"{generation_emoji} Postmortem 생성: {payload.incident_id}"
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text,
                    "emoji": True,
                },
            }
        )

        # Divider
        blocks.append({"type": "divider"})

        # Section: 요약
        duration_minutes = int(payload.duration_seconds / 60)
        summary_text = (
            f"*{payload.service_name}* 서비스에서 인시던트가 발생하여 "
            f"자동으로 Postmortem이 생성되었습니다.\n"
            f"지속 시간: *{duration_minutes}분*"
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": summary_text,
                },
            }
        )

        # Fields: 상세 정보
        fields = [
            {
                "type": "mrkdwn",
                "text": f"*서비스:*\n{payload.service_name}",
            },
            {
                "type": "mrkdwn",
                "text": f"*네임스페이스:*\n{payload.namespace}",
            },
            {
                "type": "mrkdwn",
                "text": f"*심각도:*\n{payload.severity.upper()}",
            },
            {
                "type": "mrkdwn",
                "text": f"*생성 유형:*\n{payload.generation_type}",
            },
        ]

        # 그룹 Postmortem인 경우 영향받은 서비스 추가
        if payload.affected_services and len(payload.affected_services) > 1:
            services_text = ", ".join(payload.affected_services[:5])
            if len(payload.affected_services) > 5:
                services_text += f" 외 {len(payload.affected_services) - 5}개"
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*영향받은 서비스:*\n{services_text}",
                }
            )

        blocks.append(
            {
                "type": "section",
                "fields": fields,
            }
        )

        # Actions: 딥링크 버튼들
        buttons = []

        if payload.deep_links.get("postmortem_url"):
            buttons.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📄 상세 보기",
                        "emoji": True,
                    },
                    "url": payload.deep_links["postmortem_url"],
                    "style": "primary",
                }
            )

        if payload.deep_links.get("dashboard_url"):
            buttons.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📊 대시보드",
                        "emoji": True,
                    },
                    "url": payload.deep_links["dashboard_url"],
                }
            )

        if payload.deep_links.get("runbook_url"):
            buttons.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📖 Runbook",
                        "emoji": True,
                    },
                    "url": payload.deep_links["runbook_url"],
                }
            )

        if payload.deep_links.get("audit_log_url"):
            buttons.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📜 감사 로그",
                        "emoji": True,
                    },
                    "url": payload.deep_links["audit_log_url"],
                }
            )

        if buttons:
            blocks.append(
                {
                    "type": "actions",
                    "elements": buttons[:4],  # Slack 최대 버튼 수 제한
                }
            )

        # Context: 메타 정보
        context_elements = [
            {
                "type": "mrkdwn",
                "text": f"생성 시각: {payload.created_at}",
            },
        ]

        if payload.generation_type == "auto":
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": "🤖 자동 생성",
                }
            )
        elif payload.generation_type == "emergency":
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": "🚨 긴급 자동 생성",
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": context_elements,
            }
        )

        return {
            "blocks": blocks,
            "text": header_text,  # Fallback text
        }


class PostmortemNotifier:
    """
    Postmortem 알림 발송 서비스.

    Usage:
        notifier = get_postmortem_notifier()
        notifier.notify_postmortem_created(
            incident_id="PM-20260128-001",
            service_name="payment_service",
            duration_seconds=1800,
            deep_links=deep_links.to_dict(),
        )
    """

    def __init__(self, config: PostmortemNotificationConfig | None = None):
        """Initialize with config."""
        self._config = config or PostmortemNotificationConfig.from_env()
        self._block_builder = SlackBlockKitBuilder()

        logger.debug(
            f"[PostmortemNotifier] Initialized with " f"enabled={self._config.enabled}, " f"channels={self._config.channels}"
        )

    def notify_postmortem_created(
        self,
        incident_id: str,
        service_name: str,
        duration_seconds: float,
        generation_type: str = "auto",
        deep_links: dict[str, str | None] | None = None,
        severity: str = "medium",
        namespace: str = "default",
        affected_services: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Postmortem 생성 알림 발송.

        Args:
            incident_id: Postmortem ID
            service_name: 영향받은 서비스
            duration_seconds: 인시던트 지속 시간
            generation_type: 생성 유형 (auto, group, emergency)
            deep_links: 딥링크 딕셔너리
            severity: 심각도
            namespace: 네임스페이스
            affected_services: 영향받은 서비스 목록
            metadata: 추가 메타데이터

        Returns:
            bool: 발송 성공 여부
        """
        if not self._config.enabled:
            logger.debug("postmortem_notifier.notification_disabled")
            return True

        payload = PostmortemNotificationPayload(
            incident_id=incident_id,
            title=f"[Postmortem] {incident_id} 자동 생성",
            service_name=service_name,
            duration_seconds=duration_seconds,
            generation_type=generation_type,
            deep_links=deep_links or {},
            affected_services=affected_services or [service_name],
            severity=severity,
            namespace=namespace,
            metadata=metadata or {},
        )

        success = True

        # Slack 채널로 발송
        if "slack" in self._config.channels:
            slack_success = self._send_slack_notification(payload)
            success = success and slack_success

        # Unified Notification Manager 통합
        try:
            self._send_via_unified_manager(payload)
        except Exception as e:
            logger.warning(
                "postmortem_notifier.unified_manager_failed",
                error=e,
            )

        return success

    def _send_slack_notification(self, payload: PostmortemNotificationPayload) -> bool:
        """Slack Webhook으로 알림 발송."""
        if not self._config.slack_webhook_url:
            logger.debug("postmortem_notifier.slack_webhook_configured")
            return True  # 설정 안 됨 = 성공으로 간주

        try:
            import urllib.request
            import urllib.error

            message = self._block_builder.build_postmortem_message(payload)
            data = json.dumps(message).encode("utf-8")

            request = urllib.request.Request(
                self._config.slack_webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 200:
                    logger.info(
                        "postmortem_notifier.slack_notification_sent",
                        payload=payload.incident_id,
                    )
                    return True
                else:
                    logger.warning(
                        "postmortem_notifier.slack_response",
                        response=response.status,
                    )
                    return False

        except urllib.error.URLError as e:
            logger.warning(
                "postmortem_notifier.slack_webhook_failed",
                error=e,
            )
            return False
        except Exception as e:
            logger.warning(
                "postmortem_notifier.slack_notification_error",
                error=e,
            )
            return False

    def _send_via_unified_manager(self, payload: PostmortemNotificationPayload) -> None:
        """Unified Notification Manager를 통해 발송."""
        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                get_unified_notification_manager,
            )

            # 심각도에 따른 우선순위 매핑
            priority_map = {
                "low": NotificationPriority.LOW,
                "medium": NotificationPriority.MEDIUM,
                "high": NotificationPriority.HIGH,
                "critical": NotificationPriority.CRITICAL,
            }

            manager = get_unified_notification_manager()
            manager.notify(
                NotificationPayload(
                    title=payload.title,
                    message=(f"Service: {payload.service_name}, " f"Duration: {int(payload.duration_seconds / 60)}min"),
                    priority=priority_map.get(payload.severity, NotificationPriority.MEDIUM),
                    category=NotificationCategory.OPERATIONS,
                    source="postmortem_notifier",
                    dedup_key=f"postmortem:{payload.incident_id}",
                    metadata={
                        "incident_id": payload.incident_id,
                        "service_name": payload.service_name,
                        "generation_type": payload.generation_type,
                        "deep_links": payload.deep_links,
                        **payload.metadata,
                    },
                )
            )

        except ImportError:
            logger.debug("postmortem_notifier.unifiednotificationmanager_available")

    def send_webhook(
        self,
        webhook_url: str,
        payload: PostmortemNotificationPayload,
    ) -> bool:
        """
        임의의 Webhook URL로 알림 발송.

        Args:
            webhook_url: Webhook URL
            payload: 알림 페이로드

        Returns:
            bool: 발송 성공 여부
        """
        try:
            import urllib.request
            import urllib.error

            data = json.dumps(payload.to_dict()).encode("utf-8")

            request = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status == 200

        except Exception as e:
            logger.warning(
                "postmortem_notifier.webhook_failed",
                error=e,
            )
            return False

    def is_enabled(self) -> bool:
        """알림 활성화 여부 확인."""
        return self._config.enabled

    def get_config(self) -> dict:
        """현재 설정 반환."""
        return {
            "enabled": self._config.enabled,
            "slack_configured": bool(self._config.slack_webhook_url),
            "channels": self._config.channels,
        }


# =============================================================================
# Singleton Pattern
# =============================================================================

_instance: PostmortemNotifier | None = None


def get_postmortem_notifier() -> PostmortemNotifier:
    """
    PostmortemNotifier 싱글톤 인스턴스 반환.

    Returns:
        PostmortemNotifier: 알림 발송 서비스 인스턴스
    """
    global _instance
    if _instance is None:
        _instance = PostmortemNotifier()
    return _instance


def reset_postmortem_notifier() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _instance
    _instance = None
