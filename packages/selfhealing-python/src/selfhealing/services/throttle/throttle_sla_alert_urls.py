"""
Throttle SLA Actionable Alert URL Builder.

환경변수에서 기본 URL을 읽어 서비스별 대시보드/Admin/Runbook 링크를 생성합니다.

Environment Variables:
- THROTTLE_SLA_DASHBOARD_URL: Grafana Throttle 대시보드 URL
- THROTTLE_SLA_ADMIN_BASE_URL: Throttle Admin 제어판 URL
- THROTTLE_SLA_RUNBOOK_URL: SLA 장애 대응 Runbook URL
"""

from __future__ import annotations

import structlog
import os
from dataclasses import dataclass
from urllib.parse import urlencode

logger = structlog.get_logger()


@dataclass
class ThrottleSlaActionableUrls:
    """SLA 알림에 포함될 대시보드/Admin/Runbook URL 모음."""

    dashboard_url: str | None = None
    admin_url: str | None = None
    runbook_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "dashboard_url": self.dashboard_url,
            "admin_url": self.admin_url,
            "runbook_url": self.runbook_url,
        }

    def has_any_url(self) -> bool:
        return any([self.dashboard_url, self.admin_url, self.runbook_url])


class ThrottleSlaAlertUrlBuilder:
    """
    Throttle SLA Actionable Alert URL 빌더.

    설계 원칙:
    - 거버넌스 유지: Admin 제어판으로 이동 (원클릭 해제 불가)
    - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
    - 안전성: 운영자가 상태 확인 후 판단
    """

    def __init__(self):
        self._dashboard_base_url = os.getenv("THROTTLE_SLA_DASHBOARD_URL", "")
        self._admin_base_url = os.getenv("THROTTLE_SLA_ADMIN_BASE_URL", "")
        self._runbook_base_url = os.getenv("THROTTLE_SLA_RUNBOOK_URL", "")

        logger.debug(
            f"[ThrottleSlaAlertUrlBuilder] Initialized: "
            f"dashboard={bool(self._dashboard_base_url)}, "
            f"admin={bool(self._admin_base_url)}, "
            f"runbook={bool(self._runbook_base_url)}"
        )

    def build_sla_alert_urls(
        self,
        service_name: str,
        event_type: str = "sla_warning",
        rtt_ms: float | None = None,
    ) -> ThrottleSlaActionableUrls:
        """SLA 이벤트에 대한 Actionable URL을 생성."""
        return ThrottleSlaActionableUrls(
            dashboard_url=self._build_dashboard_url(service_name, rtt_ms),
            admin_url=self._build_admin_url(service_name, event_type),
            runbook_url=self._build_runbook_url(event_type),
        )

    def _build_dashboard_url(self, service_name: str, rtt_ms: float | None) -> str | None:
        if not self._dashboard_base_url:
            return None
        params = {"var-service": service_name}
        if rtt_ms is not None:
            params["var-rtt"] = str(int(rtt_ms))
        return f"{self._dashboard_base_url}?{urlencode(params)}"

    def _build_admin_url(self, service_name: str, event_type: str) -> str | None:
        if not self._admin_base_url:
            return None
        params = {"service": service_name, "event": event_type}
        return f"{self._admin_base_url}?{urlencode(params)}"

    def _build_runbook_url(self, event_type: str) -> str | None:
        if not self._runbook_base_url:
            return None
        anchor = event_type.replace("_", "-")
        return f"{self._runbook_base_url}#{anchor}"


# --- 싱글톤 ---
_builder_instance: ThrottleSlaAlertUrlBuilder | None = None


def get_throttle_sla_alert_url_builder() -> ThrottleSlaAlertUrlBuilder:
    """싱글톤 인스턴스 반환."""
    global _builder_instance
    if _builder_instance is None:
        _builder_instance = ThrottleSlaAlertUrlBuilder()
    return _builder_instance


def reset_throttle_sla_alert_url_builder() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _builder_instance
    _builder_instance = None
