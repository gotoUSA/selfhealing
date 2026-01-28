"""
Postmortem Deep Links Builder.

Postmortem 생성 시 관련 시스템으로의 딥링크를 생성하여
운영자가 빠르게 컨텍스트에 접근할 수 있도록 합니다.

Features:
- Grafana 대시보드 URL (시간 범위 포함)
- Prometheus 메트릭 쿼리 URL
- Postmortem 상세 페이지 URL
- 타임라인 뷰 URL
- 감사 로그 URL
- CascadeEvent 증적 링크

Environment Variables:
- POSTMORTEM_BASE_URL: Postmortem 웹 UI 기본 URL
- POSTMORTEM_TIMELINE_URL: 타임라인 뷰 URL
- CB_DASHBOARD_URL: Grafana 대시보드 URL (기존 설정 재사용)
- CB_RUNBOOK_URL: Runbook URL (기존 설정 재사용)
- PROMETHEUS_URL: Prometheus 웹 UI URL
- AUDIT_LOG_BASE_URL: 감사 로그 UI URL
- AUDIT_EVIDENCE_BASE_URL: CascadeEvent 증적 페이지 URL
- CB_ADMIN_BASE_URL: Admin 제어판 URL
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


@dataclass
class PostmortemDeepLinks:
    """
    Postmortem에 포함될 딥링크 모음.

    Attributes:
        dashboard_url: Grafana 대시보드 URL
        runbook_url: Runbook URL
        postmortem_url: Postmortem 상세 페이지 URL
        timeline_url: 타임라인 뷰 URL
        audit_log_url: 감사 로그 URL
        metrics_url: Prometheus 메트릭 URL
        admin_url: Admin 제어판 URL
        audit_evidence_link: CascadeEvent 증적 링크
    """

    dashboard_url: str | None = None
    runbook_url: str | None = None
    postmortem_url: str | None = None
    timeline_url: str | None = None
    audit_log_url: str | None = None
    metrics_url: str | None = None
    admin_url: str | None = None
    audit_evidence_link: str | None = None

    def to_dict(self) -> dict:
        """URL들을 딕셔너리로 변환."""
        return {
            "dashboard_url": self.dashboard_url,
            "runbook_url": self.runbook_url,
            "postmortem_url": self.postmortem_url,
            "timeline_url": self.timeline_url,
            "audit_log_url": self.audit_log_url,
            "metrics_url": self.metrics_url,
            "admin_url": self.admin_url,
            "audit_evidence_link": self.audit_evidence_link,
        }

    def has_any_url(self) -> bool:
        """최소 하나의 URL이 설정되어 있는지 확인."""
        return any(
            [
                self.dashboard_url,
                self.runbook_url,
                self.postmortem_url,
                self.timeline_url,
                self.audit_log_url,
                self.metrics_url,
                self.admin_url,
                self.audit_evidence_link,
            ]
        )

    def get_primary_links(self) -> dict:
        """
        주요 링크만 반환 (알림에 사용).

        Returns:
            postmortem_url, dashboard_url, runbook_url만 포함
        """
        return {
            "postmortem_url": self.postmortem_url,
            "dashboard_url": self.dashboard_url,
            "runbook_url": self.runbook_url,
        }


class PostmortemDeepLinkBuilder:
    """
    Postmortem 딥링크 빌더.

    환경변수에서 기본 URL을 읽어와서 Postmortem용 URL을 생성합니다.

    Usage:
        builder = get_postmortem_deep_link_builder()
        links = builder.build_postmortem_links(
            incident_id="PM-20260128-001",
            service_name="payment_service",
            start_time="2026-01-28T10:00:00Z",
            end_time="2026-01-28T10:30:00Z",
            namespace="production",
        )
    """

    def __init__(self):
        """환경변수에서 기본 URL 로드."""
        # Postmortem 전용 URL
        self._postmortem_base_url = os.getenv("POSTMORTEM_BASE_URL", "")
        self._timeline_base_url = os.getenv("POSTMORTEM_TIMELINE_URL", "")

        # 기존 Circuit Breaker URL 재사용
        self._dashboard_base_url = os.getenv("CB_DASHBOARD_URL", "")
        self._runbook_base_url = os.getenv("CB_RUNBOOK_URL", "")
        self._admin_base_url = os.getenv("CB_ADMIN_BASE_URL", "")

        # 모니터링 및 감사 URL
        self._prometheus_base_url = os.getenv("PROMETHEUS_URL", "")
        self._audit_log_base_url = os.getenv("AUDIT_LOG_BASE_URL", "")
        self._audit_evidence_base_url = os.getenv("AUDIT_EVIDENCE_BASE_URL", "")

        # Grafana 조직 ID
        self._grafana_org_id = os.getenv("GRAFANA_ORG_ID", "1")

        logger.debug(
            f"[PostmortemDeepLinkBuilder] Initialized with "
            f"postmortem={bool(self._postmortem_base_url)}, "
            f"dashboard={bool(self._dashboard_base_url)}, "
            f"prometheus={bool(self._prometheus_base_url)}"
        )

    def build_postmortem_links(
        self,
        incident_id: str,
        service_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        namespace: str = "default",
        cascade_event_id: str | None = None,
        evidence_hash: str | None = None,
    ) -> PostmortemDeepLinks:
        """
        Postmortem용 전체 링크 생성.

        Args:
            incident_id: Postmortem ID
            service_name: 서비스명
            start_time: 인시던트 시작 시각 (ISO format)
            end_time: 인시던트 종료 시각 (ISO format)
            namespace: 네임스페이스
            cascade_event_id: 연결된 CascadeEvent ID (옵션)
            evidence_hash: CascadeEvent 해시 (변조 방지용)

        Returns:
            PostmortemDeepLinks: 딥링크 모음
        """
        return PostmortemDeepLinks(
            dashboard_url=self.build_grafana_url(
                service_name=service_name,
                start_time=start_time,
                end_time=end_time,
            ),
            runbook_url=self._build_runbook_url(),
            postmortem_url=self._build_postmortem_detail_url(incident_id),
            timeline_url=self._build_timeline_url(incident_id),
            audit_log_url=self._build_audit_log_url(
                service_name=service_name,
                namespace=namespace,
                start_time=start_time,
                end_time=end_time,
            ),
            metrics_url=self.build_prometheus_url(
                service_name=service_name,
                start_time=start_time,
                end_time=end_time,
            ),
            admin_url=self._build_admin_url(service_name),
            audit_evidence_link=self._build_cascade_evidence_url(
                cascade_event_id=cascade_event_id,
                evidence_hash=evidence_hash,
            ),
        )

    def build_notification_links(
        self,
        incident_id: str,
        service_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> PostmortemDeepLinks:
        """
        알림용 간소화된 링크 (주요 링크만).

        Args:
            incident_id: Postmortem ID
            service_name: 서비스명
            start_time: 인시던트 시작 시각
            end_time: 인시던트 종료 시각

        Returns:
            PostmortemDeepLinks: 주요 딥링크만 포함
        """
        return PostmortemDeepLinks(
            postmortem_url=self._build_postmortem_detail_url(incident_id),
            dashboard_url=self.build_grafana_url(
                service_name=service_name,
                start_time=start_time,
                end_time=end_time,
            ),
            runbook_url=self._build_runbook_url(),
            audit_log_url=self._build_audit_log_url(
                service_name=service_name,
                start_time=start_time,
                end_time=end_time,
            ),
        )

    def build_grafana_url(
        self,
        service_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> str | None:
        """
        시간 범위를 포함한 Grafana 대시보드 URL 생성.

        Args:
            service_name: 서비스명 (var-service 파라미터)
            start_time: 시작 시각 (ISO format)
            end_time: 종료 시각 (ISO format)

        Returns:
            Grafana URL (설정 없으면 None)
        """
        if not self._dashboard_base_url:
            return None

        params = {
            "var-service": service_name,
            "orgId": self._grafana_org_id,
        }

        # 시간 범위를 Unix milliseconds로 변환
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                params["from"] = str(int(start_dt.timestamp() * 1000))
            except (ValueError, AttributeError):
                pass

        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                params["to"] = str(int(end_dt.timestamp() * 1000))
            except (ValueError, AttributeError):
                pass

        separator = "&" if "?" in self._dashboard_base_url else "?"
        return f"{self._dashboard_base_url}{separator}{urlencode(params)}"

    def build_prometheus_url(
        self,
        service_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> str | None:
        """
        Prometheus 메트릭 쿼리 URL 생성.

        Args:
            service_name: 서비스명
            start_time: 시작 시각 (ISO format)
            end_time: 종료 시각 (ISO format)

        Returns:
            Prometheus URL (설정 없으면 None)
        """
        if not self._prometheus_base_url:
            return None

        # 기본 PromQL 쿼리: 서비스 에러율
        promql = f'rate(http_requests_total{{service="{service_name}",status=~"5.."}}[5m])'

        # 시간 범위 계산
        range_input = "1h"
        if start_time and end_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                duration_seconds = (end_dt - start_dt).total_seconds()
                # 여유 시간 추가
                duration_minutes = int(duration_seconds / 60) + 30
                range_input = f"{duration_minutes}m"
            except (ValueError, AttributeError):
                pass

        params = {
            "g0.expr": promql,
            "g0.range_input": range_input,
            "g0.tab": "0",  # Graph 탭
        }

        base_url = self._prometheus_base_url.rstrip("/")
        return f"{base_url}/graph?{urlencode(params)}"

    def _build_postmortem_detail_url(self, incident_id: str) -> str | None:
        """Postmortem 상세 페이지 URL 생성."""
        if not self._postmortem_base_url:
            return None

        base_url = self._postmortem_base_url.rstrip("/")
        return f"{base_url}/{incident_id}"

    def _build_timeline_url(self, incident_id: str) -> str | None:
        """타임라인 뷰 URL 생성."""
        if not self._timeline_base_url:
            return None

        base_url = self._timeline_base_url.rstrip("/")
        return f"{base_url}/{incident_id}"

    def _build_runbook_url(self, context: str | None = None) -> str | None:
        """Runbook URL 생성."""
        if not self._runbook_base_url:
            return None

        if context:
            return f"{self._runbook_base_url}#{context}"

        return self._runbook_base_url

    def _build_admin_url(self, service_name: str) -> str | None:
        """Admin 제어판 URL 생성."""
        if not self._admin_base_url:
            return None

        params = {"service_id": service_name, "action": "postmortem_review"}
        base_url = self._admin_base_url.rstrip("/") + "/"
        return f"{base_url}?{urlencode(params)}"

    def _build_audit_log_url(
        self,
        service_name: str,
        namespace: str = "default",
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> str | None:
        """감사 로그 URL 생성."""
        if not self._audit_log_base_url:
            return None

        params = {
            "service": service_name,
            "namespace": namespace,
        }

        if start_time:
            params["from"] = start_time
        if end_time:
            params["to"] = end_time

        base_url = self._audit_log_base_url.rstrip("/")
        return f"{base_url}?{urlencode(params)}"

    def _build_cascade_evidence_url(
        self,
        cascade_event_id: str | None,
        evidence_hash: str | None = None,
    ) -> str | None:
        """
        CascadeEvent 증적 링크 생성.

        감사 대비 무결성 검증용 해시를 포함합니다.

        Args:
            cascade_event_id: CascadeEvent ID
            evidence_hash: 해시 (변조 방지 검증용)

        Returns:
            증적 URL (ID 없으면 None)
        """
        if not cascade_event_id:
            return None

        if not self._audit_evidence_base_url:
            return None

        base_url = self._audit_evidence_base_url.rstrip("/")
        url = f"{base_url}/cascade/{cascade_event_id}"

        if evidence_hash:
            url = f"{url}?verify={evidence_hash}"

        return url

    def is_configured(self) -> bool:
        """최소 하나의 URL이 환경변수로 설정되어 있는지 확인."""
        return any(
            [
                self._postmortem_base_url,
                self._dashboard_base_url,
                self._runbook_base_url,
                self._prometheus_base_url,
                self._audit_log_base_url,
            ]
        )

    def get_config_status(self) -> dict:
        """현재 URL 설정 상태 반환 (디버깅용)."""
        return {
            "postmortem_configured": bool(self._postmortem_base_url),
            "timeline_configured": bool(self._timeline_base_url),
            "dashboard_configured": bool(self._dashboard_base_url),
            "runbook_configured": bool(self._runbook_base_url),
            "prometheus_configured": bool(self._prometheus_base_url),
            "audit_log_configured": bool(self._audit_log_base_url),
            "audit_evidence_configured": bool(self._audit_evidence_base_url),
            "admin_configured": bool(self._admin_base_url),
        }


# =============================================================================
# Singleton Pattern
# =============================================================================

_instance: PostmortemDeepLinkBuilder | None = None


def get_postmortem_deep_link_builder() -> PostmortemDeepLinkBuilder:
    """
    PostmortemDeepLinkBuilder 싱글톤 인스턴스 반환.

    Returns:
        PostmortemDeepLinkBuilder: URL 빌더 인스턴스
    """
    global _instance
    if _instance is None:
        _instance = PostmortemDeepLinkBuilder()
    return _instance


def reset_postmortem_deep_link_builder() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _instance
    _instance = None
