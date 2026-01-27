"""
Error Budget Gate - Alert Manager.

Gate 상태 변경 시 알림 발송 관리.
Fail-Open 발동, Rate Limit 초과 등 중요 이벤트에 대해 알림을 발송합니다.

Reference:
- docs/self_healing/12_ERROR_BUDGET.md
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GateAlertManager:
    """
    Gate 상태 변경 시 알림 발송 관리.

    Fail-Open 발동, Rate Limit 초과 등 중요 이벤트에 대해
    Slack/PagerDuty 등으로 알림을 발송합니다.
    쿨다운 적용으로 알림 폭주를 방지합니다.
    """

    def __init__(self, cooldown_seconds: int = 300):
        self._cooldown_seconds = cooldown_seconds
        self._last_alert_times: dict[str, datetime] = {}
        self._lock = threading.RLock()

    def update_config(self, cooldown_seconds: int) -> None:
        """설정 동적 업데이트."""
        with self._lock:
            self._cooldown_seconds = cooldown_seconds

    def _can_send_alert(self, alert_type: str) -> bool:
        """쿨다운 확인."""
        with self._lock:
            last_time = self._last_alert_times.get(alert_type)
            if last_time is None:
                return True
            elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
            return elapsed >= self._cooldown_seconds

    def _record_alert_sent(self, alert_type: str) -> None:
        """알림 발송 기록."""
        with self._lock:
            self._last_alert_times[alert_type] = datetime.now(timezone.utc)

    def send_fail_open_alert(
        self, reason: str, rate_limit_remaining: int | None = None
    ) -> bool:
        """
        Fail-Open 발동 알림.

        Returns:
            bool: 알림 발송 여부 (쿨다운 중이면 False)
        """
        alert_type = "fail_open"
        if not self._can_send_alert(alert_type):
            logger.debug(f"[GateAlert] Skipping {alert_type} alert (cooldown)")
            return False

        try:
            self._send_notification(
                title="🔶 Error Budget Gate: Fail-Open 발동",
                message=(
                    f"Error Budget 조회에 실패하여 Fail-Open 모드로 전환되었습니다.\n"
                    f"• 사유: {reason}\n"
                    f"• Rate Limit 잔여: {rate_limit_remaining if rate_limit_remaining is not None else 'N/A'}\n"
                    f"• 조치: Error Budget 서비스 상태를 확인하세요."
                ),
                severity="warning",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send fail_open alert: {e}")
            return False

    def send_rate_limit_exceeded_alert(self) -> bool:
        """Rate Limit 초과 알림."""
        alert_type = "rate_limit_exceeded"
        if not self._can_send_alert(alert_type):
            return False

        try:
            self._send_notification(
                title="🔴 Error Budget Gate: Rate Limit 초과",
                message=(
                    "Fail-Open 상태에서 Rate Limit을 초과하여 자동화가 차단되었습니다.\n"
                    "Error Budget 서비스를 즉시 복구하세요."
                ),
                severity="critical",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send rate_limit alert: {e}")
            return False

    def send_circuit_open_alert(self, failure_count: int) -> bool:
        """Circuit Breaker Open 알림."""
        alert_type = "circuit_open"
        if not self._can_send_alert(alert_type):
            return False

        try:
            self._send_notification(
                title="🔴 Error Budget Gate: Circuit Breaker Open",
                message=(
                    f"Error Budget 서비스가 {failure_count}회 연속 실패하여 Circuit Breaker가 열렸습니다.\n"
                    "서비스 상태를 확인하세요."
                ),
                severity="critical",
            )
            self._record_alert_sent(alert_type)
            return True
        except Exception as e:
            logger.warning(f"[GateAlert] Failed to send circuit_open alert: {e}")
            return False

    def _send_notification(self, title: str, message: str, severity: str) -> None:
        """실제 알림 발송 (Notification 서비스 연동)."""
        try:
            from selfhealing.services.notification import get_notification_service

            service = get_notification_service()
            service.send(
                channel="slack",  # 또는 설정에 따라
                title=title,
                message=message,
                severity=severity,
                tags=["error_budget_gate", "fail_open"],
            )
            logger.info(f"[GateAlert] Sent alert: {title}")
        except ImportError:
            # Notification 서비스가 없으면 로그만
            logger.warning(f"[GateAlert] {title}: {message}")
        except Exception as e:
            logger.warning(f"[GateAlert] Notification failed: {e}")

    def get_status(self) -> dict[str, Any]:
        """알림 상태 조회."""
        with self._lock:
            return {
                "cooldown_seconds": self._cooldown_seconds,
                "last_alerts": {
                    k: v.isoformat() for k, v in self._last_alert_times.items()
                },
            }

    def reset(self) -> None:
        """알림 쿨다운 리셋."""
        with self._lock:
            self._last_alert_times.clear()
            logger.info("[GateAlert] Alert cooldowns reset")


__all__ = [
    "GateAlertManager",
]
