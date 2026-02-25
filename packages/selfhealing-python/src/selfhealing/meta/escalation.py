"""
Escalation Manager - 인간 개입 요청.

자동 복구 실패 시 PagerDuty, Slack 등을 통해
인간에게 에스컬레이션합니다.

지원 채널:
- PagerDuty: CRITICAL 레벨 알림
- Slack: WARNING, ERROR, CRITICAL 레벨 알림
- Webhook: 커스텀 엔드포인트 (확장용)
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings

logger = structlog.get_logger()


class EscalationLevel(str, Enum):
    """에스컬레이션 심각도 레벨."""

    INFO = "info"
    """정보성 알림."""

    WARNING = "warning"
    """주의 필요."""

    ERROR = "error"
    """에러 발생."""

    CRITICAL = "critical"
    """긴급 개입 필요."""


@dataclass
class EscalationEvent:
    """에스컬레이션 이벤트."""

    level: EscalationLevel
    """심각도 레벨."""

    title: str
    """알림 제목."""

    description: str
    """상세 설명."""

    component: str
    """관련 컴포넌트."""

    details: dict[str, Any] = field(default_factory=dict)
    """추가 상세 정보."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """발생 시각."""


@dataclass
class EscalationResult:
    """에스컬레이션 결과."""

    success: bool
    """성공 여부."""

    channels_sent: list[str]
    """전송 성공 채널 목록."""

    channels_failed: list[str]
    """전송 실패 채널 목록."""

    error_message: str | None = None
    """에러 메시지."""


class EscalationManager:
    """
    Escalation Manager.

    자동 복구 실패 시 인간에게 에스컬레이션합니다.

    기능:
    - 심각도에 따른 채널 선택
    - 쿨다운 관리 (알림 폭탄 방지)
    - 실패 시 폴백 처리

    사용 예시:
        manager = EscalationManager()

        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title="DLQ Consumer Stuck",
            description="DLQ consumer stopped processing for 5 minutes",
            component="dlq",
        )

        result = manager.escalate(event)
        if result.success:
            print("Escalation sent")
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
    ):
        """
        초기화.

        Args:
            settings: Meta-Watchdog 설정 (None이면 기본값)
        """
        self._settings = settings or get_meta_watchdog_settings()
        self._lock = threading.RLock()
        self._last_escalation: dict[str, float] = {}

    def _can_escalate(self, component: str) -> bool:
        """
        쿨다운 확인.

        Args:
            component: 컴포넌트 이름

        Returns:
            에스컬레이션 가능 여부
        """
        with self._lock:
            last_time = self._last_escalation.get(component, 0)
            return time.time() - last_time > self._settings.escalation_cooldown_seconds

    def _record_escalation(self, component: str) -> None:
        """
        에스컬레이션 기록.

        Args:
            component: 컴포넌트 이름
        """
        with self._lock:
            self._last_escalation[component] = time.time()

    def _is_maintenance_component(self, component: str) -> bool:
        """
        유지보수 중인 컴포넌트인지 확인.

        Args:
            component: 컴포넌트 이름

        Returns:
            유지보수 중 여부
        """
        return component in self._settings.maintenance_components

    def escalate(self, event: EscalationEvent) -> EscalationResult:
        """
        에스컬레이션 실행.

        Args:
            event: 에스컬레이션 이벤트

        Returns:
            EscalationResult
        """
        # 비활성화 확인
        if not self._settings.escalation_enabled:
            logger.debug("escalation.escalation_disabled")
            return EscalationResult(
                success=False,
                channels_sent=[],
                channels_failed=[],
                error_message="Escalation disabled",
            )

        # Dry-run 모드 확인
        if self._settings.dry_run_mode:
            logger.info(
                "escalation.dry_run_escalation",
                _event=event.component,
                title=event.title,
            )
            return EscalationResult(
                success=True,
                channels_sent=["dry_run"],
                channels_failed=[],
            )

        # 유지보수 컴포넌트 확인
        if self._is_maintenance_component(event.component):
            logger.debug(
                "escalation.maintenance_skipped",
                _event=event.component,
            )
            return EscalationResult(
                success=False,
                channels_sent=[],
                channels_failed=[],
                error_message="Component in maintenance",
            )

        # 쿨다운 확인
        if not self._can_escalate(event.component):
            logger.debug(
                "escalation.cooldown_active",
                _event=event.component,
            )
            return EscalationResult(
                success=False,
                channels_sent=[],
                channels_failed=[],
                error_message="Cooldown active",
            )

        channels_sent: list[str] = []
        channels_failed: list[str] = []

        # CRITICAL → PagerDuty
        if event.level == EscalationLevel.CRITICAL:
            if self._send_pagerduty(event):
                channels_sent.append("pagerduty")
            else:
                channels_failed.append("pagerduty")

        # WARNING 이상 → Slack
        if event.level in (
            EscalationLevel.WARNING,
            EscalationLevel.ERROR,
            EscalationLevel.CRITICAL,
        ):
            if self._send_slack(event):
                channels_sent.append("slack")
            else:
                channels_failed.append("slack")

        # 하나라도 성공하면 성공으로 처리
        success = len(channels_sent) > 0

        if success:
            self._record_escalation(event.component)
            logger.warning(
                "escalation.escalated",
                _event=event.component,
                title=event.title,
                channels_sent=channels_sent,
            )

        return EscalationResult(
            success=success,
            channels_sent=channels_sent,
            channels_failed=channels_failed,
        )

    def _send_pagerduty(self, event: EscalationEvent) -> bool:
        """
        PagerDuty 알림 전송.

        Args:
            event: 에스컬레이션 이벤트

        Returns:
            전송 성공 여부
        """
        if not self._settings.pagerduty_routing_key:
            logger.debug("escalation.pagerduty_configured")
            return False

        try:
            payload = {
                "routing_key": self._settings.pagerduty_routing_key,
                "event_action": "trigger",
                "dedup_key": f"selfhealing-{event.component}-{event.title}",
                "payload": {
                    "summary": f"[Self-Healing] {event.title}",
                    "severity": self._settings.pagerduty_severity,
                    "source": "selfhealing-meta-watchdog",
                    "component": event.component,
                    "timestamp": event.timestamp.isoformat(),
                    "custom_details": {
                        "description": event.description,
                        "level": event.level.value,
                        **event.details,
                    },
                },
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "https://events.pagerduty.com/v2/enqueue",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 202:
                    logger.info(
                        "escalation.pagerduty_sent",
                        _event=event.title,
                    )
                    return True

            return False
        except urllib.error.URLError as e:
            logger.exception(
                "escalation.pagerduty_network_error",
                error=e,
            )
            return False
        except Exception as e:
            logger.exception(
                "escalation.pagerduty_error",
                error=e,
            )
            return False

    def _send_slack(self, event: EscalationEvent) -> bool:
        """
        Slack 알림 전송.

        Args:
            event: 에스컬레이션 이벤트

        Returns:
            전송 성공 여부
        """
        if not self._settings.slack_webhook_url:
            logger.debug("escalation.slack_configured")
            return False

        try:
            emoji = {
                EscalationLevel.INFO: "ℹ️",
                EscalationLevel.WARNING: "⚠️",
                EscalationLevel.ERROR: "❌",
                EscalationLevel.CRITICAL: "🚨",
            }.get(event.level, "❓")

            color = {
                EscalationLevel.INFO: "#36a64f",
                EscalationLevel.WARNING: "#ff9800",
                EscalationLevel.ERROR: "#f44336",
                EscalationLevel.CRITICAL: "#d32f2f",
            }.get(event.level, "#808080")

            payload = {
                "text": f"{emoji} *[Self-Healing]* {event.title}",
                "attachments": [
                    {
                        "color": color,
                        "blocks": [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (f"{emoji} *[Self-Healing]* {event.title}\n\n" f"{event.description}"),
                                },
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": (
                                            f"*Component:* {event.component} | "
                                            f"*Level:* {event.level.value} | "
                                            f"*Time:* {event.timestamp.isoformat()}"
                                        ),
                                    },
                                ],
                            },
                        ],
                    }
                ],
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._settings.slack_webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 200:
                    logger.info(
                        "escalation.slack_sent",
                        _event=event.title,
                    )
                    return True

            return False
        except urllib.error.URLError as e:
            logger.exception(
                "escalation.slack_network_error",
                error=e,
            )
            return False
        except Exception as e:
            logger.exception(
                "escalation.slack_error",
                error=e,
            )
            return False

    def get_last_escalation_time(self, component: str) -> float | None:
        """
        마지막 에스컬레이션 시각 조회.

        Args:
            component: 컴포넌트 이름

        Returns:
            Unix timestamp (없으면 None)
        """
        with self._lock:
            return self._last_escalation.get(component)

    def reset_cooldown(self, component: str | None = None) -> None:
        """
        쿨다운 리셋.

        Args:
            component: 특정 컴포넌트만 리셋 (None이면 전체)
        """
        with self._lock:
            if component:
                self._last_escalation.pop(component, None)
            else:
                self._last_escalation.clear()
