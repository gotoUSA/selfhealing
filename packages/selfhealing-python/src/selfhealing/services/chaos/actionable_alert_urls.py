"""
Chaos Actionable Alert URL Builder.

Chaos 알림에 포함될 Admin Deep Link를 생성합니다.

설계 원칙:
- 거버넌스 유지: 운영자가 Admin에 로그인하는 과정이 보안 인증(MFA)과 감사(Audit) 단계
- 설정 제로: 별도의 슬랙 앱 설정 불필요
- 비즈니스 가치: 원클릭 편의성보다 운영자 신원 확인과 감사 추적(Audit Trail) 우선
"""

from __future__ import annotations

import structlog
import os
from dataclasses import dataclass
from urllib.parse import urlencode

logger = structlog.get_logger()


@dataclass
class ChaosActionableUrls:
    """Chaos 알림에 포함될 URL 모음."""

    dashboard_url: str | None = None
    admin_stop_url: str | None = None  # 중단용 Admin URL
    admin_detail_url: str | None = None  # 상세 조회용 Admin URL
    runbook_url: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "dashboard_url": self.dashboard_url,
            "admin_stop_url": self.admin_stop_url,
            "admin_detail_url": self.admin_detail_url,
            "runbook_url": self.runbook_url,
        }

    def has_any_url(self) -> bool:
        """Check if any URL is set."""
        return any(
            [
                self.dashboard_url,
                self.admin_stop_url,
                self.admin_detail_url,
                self.runbook_url,
            ]
        )


class ChaosActionableAlertUrlBuilder:
    """
    Chaos Alert URL 빌더.

    23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md의 Admin Deep Link 방식을
    Chaos 실험에도 동일하게 적용합니다.

    장점:
    - 거버넌스 준수: Admin 로그인 과정이 MFA 및 Audit 단계
    - 설정 제로: 별도 슬랙 앱 설정 불필요
    - 감사 추적: 기술 실사 시 "운영자 신원 확인과 감사 추적 우선시" 설명 가능

    Environment Variables:
    - CHAOS_ADMIN_BASE_URL: Chaos Admin 기본 URL
    - CHAOS_DASHBOARD_URL: Grafana Chaos 대시보드 URL
    - CHAOS_RUNBOOK_URL: Chaos 운영 매뉴얼 URL

    Usage:
        builder = get_chaos_actionable_alert_url_builder()
        urls = builder.build_experiment_alert_urls(
            experiment_id="exp-001",
            target_service="payment-api",
        )
    """

    def __init__(self):
        """환경변수에서 기본 URL 로드."""
        self._admin_base_url = os.getenv(
            "CHAOS_ADMIN_BASE_URL", "/api/self-healing/chaos/"
        )
        self._dashboard_base_url = os.getenv("CHAOS_DASHBOARD_URL", "")
        self._runbook_base_url = os.getenv("CHAOS_RUNBOOK_URL", "")

        logger.debug(
            f"[ChaosAlertUrlBuilder] Initialized: "
            f"admin={bool(self._admin_base_url)}, "
            f"dashboard={bool(self._dashboard_base_url)}"
        )

    def build_experiment_alert_urls(
        self,
        experiment_id: str,
        target_service: str,
        trigger_time: str | None = None,
    ) -> ChaosActionableUrls:
        """
        실험 알림용 URL 생성.

        Args:
            experiment_id: 실험 ID
            target_service: 대상 서비스
            trigger_time: 이벤트 발생 시간

        Returns:
            ChaosActionableUrls: Admin Deep Link 포함된 URL 모음
        """
        return ChaosActionableUrls(
            dashboard_url=self._build_dashboard_url(target_service),
            admin_stop_url=self._build_admin_stop_url(
                experiment_id=experiment_id,
                reason="slack_alert_action",
            ),
            admin_detail_url=self._build_admin_detail_url(
                experiment_id=experiment_id,
                trigger_time=trigger_time,
            ),
            runbook_url=self._build_runbook_url("experiment-recovery"),
        )

    def build_emergency_stop_url(
        self,
        reason: str = "emergency",
    ) -> str:
        """
        긴급 전체 중단 Admin URL 생성.

        이 URL을 클릭하면 Admin 제어판의 Kill All 페이지로 이동합니다.
        운영자가 로그인한 상태에서만 동작하며, 모든 조작이 감사 기록됩니다.

        Args:
            reason: 중단 사유

        Returns:
            str: Admin Kill All 페이지 URL
        """
        base_url = self._admin_base_url.rstrip("/")
        params = urlencode(
            {
                "action": "kill_all",
                "reason": reason,
                "confirm": "required",  # 확인 필수 표시
            }
        )
        return f"{base_url}/control/kill-all/?{params}"

    def _build_dashboard_url(self, target_service: str) -> str | None:
        """대시보드 URL 생성."""
        if not self._dashboard_base_url:
            return None

        separator = "&" if "?" in self._dashboard_base_url else "?"
        return f"{self._dashboard_base_url}{separator}service={target_service}"

    def _build_admin_stop_url(
        self,
        experiment_id: str,
        reason: str = "manual",
    ) -> str:
        """
        개별 실험 중단 Admin URL.

        Slack Interactive 버튼 대신 Admin 페이지로 이동하여
        거버넌스를 유지합니다.
        """
        base_url = self._admin_base_url.rstrip("/")
        params = urlencode(
            {
                "experiment_id": experiment_id,
                "action": "stop",
                "reason": reason,
            }
        )
        return f"{base_url}/schedules/{experiment_id}/?{params}"

    def _build_admin_detail_url(
        self,
        experiment_id: str,
        trigger_time: str | None = None,
    ) -> str:
        """실험 상세 조회 Admin URL."""
        base_url = self._admin_base_url.rstrip("/")
        params = {"action": "review"}
        if trigger_time:
            params["trigger_time"] = trigger_time

        return f"{base_url}/schedules/{experiment_id}/?{urlencode(params)}"

    def _build_runbook_url(self, section: str | None = None) -> str | None:
        """Runbook URL 생성."""
        if not self._runbook_base_url:
            return None

        if section:
            return f"{self._runbook_base_url}#{section}"
        return self._runbook_base_url


# =============================================================================
# Singleton
# =============================================================================

_instance: ChaosActionableAlertUrlBuilder | None = None


def get_chaos_actionable_alert_url_builder() -> ChaosActionableAlertUrlBuilder:
    """싱글톤 인스턴스 반환."""
    global _instance
    if _instance is None:
        _instance = ChaosActionableAlertUrlBuilder()
    return _instance
