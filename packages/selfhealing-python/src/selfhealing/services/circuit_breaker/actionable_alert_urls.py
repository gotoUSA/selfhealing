"""
Actionable Alert URL Builder.

Circuit Breaker 알림에 포함될 실행 가능한 링크들을 생성합니다.

Features:
- Dashboard URL: Grafana Circuit Breaker 대시보드
- Admin URL: Admin 제어판 (쿼리 파라미터로 컨텍스트 전달)
- Runbook URL: 장애 대응 매뉴얼

Environment Variables:
- CB_DASHBOARD_URL: Grafana 대시보드 기본 URL (예: https://grafana.internal/d/circuit-breaker)
- CB_ADMIN_BASE_URL: Admin 제어판 기본 URL (예: /admin/selfhealing/circuitbreaker/)
- CB_RUNBOOK_URL: Runbook 기본 URL (예: https://docs.internal/runbooks/circuit-breaker-recovery)
"""

from __future__ import annotations

import structlog
import os
from dataclasses import dataclass
from urllib.parse import urlencode

logger = structlog.get_logger()


@dataclass
class ActionableUrls:
    """
    Actionable Alert에 포함될 URL 모음.

    Attributes:
        dashboard_url: Grafana 대시보드 URL (읽기 전용)
        admin_url: Admin 제어판 URL (쿼리 파라미터로 컨텍스트 전달)
        runbook_url: 장애 대응 매뉴얼 URL
    """

    dashboard_url: str | None = None
    admin_url: str | None = None
    runbook_url: str | None = None

    def to_dict(self) -> dict:
        """URL들을 딕셔너리로 변환."""
        return {
            "dashboard_url": self.dashboard_url,
            "admin_url": self.admin_url,
            "runbook_url": self.runbook_url,
        }

    def has_any_url(self) -> bool:
        """최소 하나의 URL이 설정되어 있는지 확인."""
        return any([self.dashboard_url, self.admin_url, self.runbook_url])


class ActionableAlertUrlBuilder:
    """
    Actionable Alert URL 빌더.

    환경변수에서 기본 URL을 읽어와서 서비스별/상황별 URL을 생성합니다.

    설계 원칙:
    - 거버넌스 유지: 모든 조작이 Admin을 통해 감사 기록
    - 컨텍스트 유지: 쿼리 파라미터로 해당 서비스 즉시 조회
    - 안전성: 운영자가 상태 확인 후 판단 가능

    Usage:
        builder = get_actionable_alert_url_builder()
        urls = builder.build_cb_open_urls(
            service_name="payment_service",
            trigger_time="2026-01-06T10:00:00Z",
        )
    """

    def __init__(self):
        """환경변수에서 기본 URL 로드."""
        self._dashboard_base_url = os.getenv("CB_DASHBOARD_URL", "")
        self._admin_base_url = os.getenv("CB_ADMIN_BASE_URL", "")
        self._runbook_base_url = os.getenv("CB_RUNBOOK_URL", "")

        logger.debug(
            f"[ActionableAlertUrlBuilder] Initialized with "
            f"dashboard={bool(self._dashboard_base_url)}, "
            f"admin={bool(self._admin_base_url)}, "
            f"runbook={bool(self._runbook_base_url)}"
        )

    def build_cb_open_urls(
        self,
        service_name: str,
        trigger_time: str | None = None,
    ) -> ActionableUrls:
        """
        CB OPEN 이벤트에 대한 Actionable URL들을 생성.

        Args:
            service_name: Circuit Breaker가 열린 서비스 이름
            trigger_time: 이벤트 발생 시간 (ISO 8601 형식)

        Returns:
            ActionableUrls: 대시보드, Admin, Runbook URL 모음
        """
        return ActionableUrls(
            dashboard_url=self._build_dashboard_url(service_name),
            admin_url=self._build_admin_url(
                service_name=service_name,
                action="review",
                trigger_time=trigger_time,
            ),
            runbook_url=self._build_runbook_url(),
        )

    def build_cb_closed_urls(
        self,
        service_name: str,
        recovery_time: str | None = None,
    ) -> ActionableUrls:
        """
        CB CLOSED (복구) 이벤트에 대한 Actionable URL들을 생성.

        Args:
            service_name: Circuit Breaker가 닫힌 서비스 이름
            recovery_time: 복구 완료 시간 (ISO 8601 형식)

        Returns:
            ActionableUrls: 대시보드, Admin URL 모음 (Runbook은 불필요)
        """
        return ActionableUrls(
            dashboard_url=self._build_dashboard_url(service_name),
            admin_url=self._build_admin_url(
                service_name=service_name,
                action="history",
                trigger_time=recovery_time,
            ),
            runbook_url=None,  # 복구 시에는 Runbook 불필요
        )

    def build_governance_blocked_urls(
        self,
        service_name: str,
        reason: str,
    ) -> ActionableUrls:
        """
        Governance Blocked 이벤트에 대한 Actionable URL들을 생성.

        Blast Radius 정책에 의해 CB 작업이 차단될 때 사용.

        Args:
            service_name: 차단된 서비스 이름
            reason: 차단 사유

        Returns:
            ActionableUrls: 대시보드, Admin, Runbook URL 모음
        """
        return ActionableUrls(
            dashboard_url=self._build_dashboard_url(service_name),
            admin_url=self._build_admin_url(
                service_name=service_name,
                action="governance_review",
            ),
            runbook_url=self._build_runbook_url("governance"),
        )

    def _build_dashboard_url(self, service_name: str) -> str | None:
        """
        Grafana 대시보드 URL 생성.

        환경변수 CB_DASHBOARD_URL이 설정되어 있으면 서비스 파라미터를 추가.

        Examples:
            - Base: https://grafana.internal/d/circuit-breaker
            - Result: https://grafana.internal/d/circuit-breaker?service=payment_service
        """
        if not self._dashboard_base_url:
            return None

        # URL에 이미 ? 가 있는지 확인
        separator = "&" if "?" in self._dashboard_base_url else "?"
        return f"{self._dashboard_base_url}{separator}service={service_name}"

    def _build_admin_url(
        self,
        service_name: str,
        action: str = "review",
        trigger_time: str | None = None,
    ) -> str | None:
        """
        Admin 제어판 URL 생성.

        쿼리 파라미터로 컨텍스트를 전달하여 해당 서비스를 즉시 조회할 수 있도록 함.

        설계 원칙:
        - 원클릭 해제 대신 Admin 제어판으로 이동
        - 거버넌스 유지 (모든 조작이 감사 기록)

        Examples:
            - Base: /admin/selfhealing/circuitbreaker/
            - Result: /admin/selfhealing/circuitbreaker/?service_id=payment_service&action=review
        """
        if not self._admin_base_url:
            return None

        params = {
            "service_id": service_name,
            "action": action,
        }

        if trigger_time:
            params["trigger_time"] = trigger_time

        # URL에 이미 ? 가 있는지 확인
        base_url = self._admin_base_url.rstrip("/") + "/"
        return f"{base_url}?{urlencode(params)}"

    def _build_runbook_url(self, context: str | None = None) -> str | None:
        """
        Runbook URL 생성.

        Args:
            context: 추가 컨텍스트 (예: 'governance' → governance 섹션으로 이동)

        Examples:
            - Base: https://docs.internal/runbooks/circuit-breaker-recovery
            - Result: https://docs.internal/runbooks/circuit-breaker-recovery#governance
        """
        if not self._runbook_base_url:
            return None

        if context:
            return f"{self._runbook_base_url}#{context}"

        return self._runbook_base_url

    def is_configured(self) -> bool:
        """최소 하나의 URL이 환경변수로 설정되어 있는지 확인."""
        return any(
            [
                self._dashboard_base_url,
                self._admin_base_url,
                self._runbook_base_url,
            ]
        )

    def get_config_status(self) -> dict:
        """현재 URL 설정 상태 반환 (디버깅용)."""
        return {
            "dashboard_configured": bool(self._dashboard_base_url),
            "admin_configured": bool(self._admin_base_url),
            "runbook_configured": bool(self._runbook_base_url),
        }


# =============================================================================
# Singleton Pattern
# =============================================================================

_instance: ActionableAlertUrlBuilder | None = None


def get_actionable_alert_url_builder() -> ActionableAlertUrlBuilder:
    """
    ActionableAlertUrlBuilder 싱글톤 인스턴스 반환.

    Returns:
        ActionableAlertUrlBuilder: URL 빌더 인스턴스
    """
    global _instance
    if _instance is None:
        _instance = ActionableAlertUrlBuilder()
    return _instance


def reset_actionable_alert_url_builder() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _instance
    _instance = None
