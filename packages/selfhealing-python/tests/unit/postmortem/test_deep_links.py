"""
Postmortem Deep Links 테스트.

PostmortemDeepLinks 데이터클래스와 PostmortemDeepLinkBuilder 테스트입니다.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest import mock

import pytest


class TestPostmortemDeepLinks:
    """PostmortemDeepLinks 데이터클래스 테스트."""

    def test_empty_deep_links_to_dict(self):
        """빈 딥링크의 to_dict 동작 검증."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinks

        links = PostmortemDeepLinks()
        result = links.to_dict()

        assert result["dashboard_url"] is None
        assert result["runbook_url"] is None
        assert result["postmortem_url"] is None
        assert result["timeline_url"] is None
        assert result["audit_log_url"] is None
        assert result["metrics_url"] is None
        assert result["admin_url"] is None
        assert result["audit_evidence_link"] is None

    def test_has_any_url_returns_false_for_empty(self):
        """모든 URL이 None인 경우 has_any_url은 False를 반환."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinks

        links = PostmortemDeepLinks()
        assert links.has_any_url() is False

    def test_has_any_url_returns_true_with_url(self):
        """하나라도 URL이 있으면 has_any_url은 True를 반환."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinks

        links = PostmortemDeepLinks(dashboard_url="https://grafana.internal/d/test")
        assert links.has_any_url() is True

    def test_get_primary_links(self):
        """get_primary_links는 주요 링크만 반환."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinks

        links = PostmortemDeepLinks(
            postmortem_url="https://pm.internal/PM-001",
            dashboard_url="https://grafana.internal/d/test",
            runbook_url="https://docs.internal/runbook",
            timeline_url="https://pm.internal/timeline/PM-001",
            audit_log_url="https://audit.internal/logs",
        )

        primary = links.get_primary_links()

        assert primary["postmortem_url"] == "https://pm.internal/PM-001"
        assert primary["dashboard_url"] == "https://grafana.internal/d/test"
        assert primary["runbook_url"] == "https://docs.internal/runbook"
        assert "timeline_url" not in primary
        assert "audit_log_url" not in primary


class TestPostmortemDeepLinkBuilder:
    """PostmortemDeepLinkBuilder 테스트."""

    @pytest.fixture(autouse=True)
    def reset_builder(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )

        reset_postmortem_deep_link_builder()
        yield
        reset_postmortem_deep_link_builder()

    def test_build_without_env_vars_returns_empty(self):
        """환경변수 없이 빌드하면 모든 URL이 None."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        links = builder.build_postmortem_links(
            incident_id="PM-001",
            service_name="payment_service",
        )

        assert links.dashboard_url is None
        assert links.runbook_url is None
        assert links.postmortem_url is None

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_BASE_URL": "https://pm.internal",
            "POSTMORTEM_TIMELINE_URL": "https://pm.internal/timeline",
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb",
            "CB_RUNBOOK_URL": "https://docs.internal/runbook",
            "PROMETHEUS_URL": "https://prometheus.internal",
            "AUDIT_LOG_BASE_URL": "https://audit.internal/logs",
            "CB_ADMIN_BASE_URL": "/admin/selfhealing/cb/",
            "AUDIT_EVIDENCE_BASE_URL": "https://audit.internal/evidence",
            "GRAFANA_ORG_ID": "1",
        },
    )
    def test_build_postmortem_links_with_env_vars(self):
        """환경변수 설정 시 모든 URL이 올바르게 생성."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        links = builder.build_postmortem_links(
            incident_id="PM-20260128-001",
            service_name="payment_service",
            start_time="2026-01-28T10:00:00Z",
            end_time="2026-01-28T10:30:00Z",
            namespace="production",
        )

        assert links.postmortem_url == "https://pm.internal/PM-20260128-001"
        assert links.timeline_url == "https://pm.internal/timeline/PM-20260128-001"
        assert links.runbook_url == "https://docs.internal/runbook"
        assert links.dashboard_url is not None
        assert "var-service=payment_service" in links.dashboard_url
        assert "orgId=1" in links.dashboard_url
        assert links.metrics_url is not None
        assert "payment_service" in links.metrics_url
        assert links.admin_url is not None
        assert "service_id=payment_service" in links.admin_url

    @mock.patch.dict(
        os.environ,
        {
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb",
        },
    )
    def test_build_grafana_url_with_time_range(self):
        """시간 범위를 포함한 Grafana URL 생성."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        url = builder.build_grafana_url(
            service_name="order_service",
            start_time="2026-01-28T10:00:00+00:00",
            end_time="2026-01-28T10:30:00+00:00",
        )

        assert url is not None
        assert "var-service=order_service" in url
        assert "from=" in url
        assert "to=" in url
        # from/to 파라미터가 13자리 Unix ms 형식인지 확인 (숫자 13자리)
        import re

        assert re.search(r"from=\d{13}", url) is not None
        assert re.search(r"to=\d{13}", url) is not None

    @mock.patch.dict(
        os.environ,
        {
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb?dashboard=test",
        },
    )
    def test_build_grafana_url_with_existing_query_params(self):
        """이미 쿼리 파라미터가 있는 대시보드 URL 처리."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        url = builder.build_grafana_url(service_name="test_service")

        assert url is not None
        assert "dashboard=test" in url
        assert "&var-service=test_service" in url

    @mock.patch.dict(
        os.environ,
        {
            "PROMETHEUS_URL": "https://prometheus.internal",
        },
    )
    def test_build_prometheus_url(self):
        """Prometheus 쿼리 URL 생성."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        url = builder.build_prometheus_url(
            service_name="api_gateway",
            start_time="2026-01-28T10:00:00Z",
            end_time="2026-01-28T10:30:00Z",
        )

        assert url is not None
        assert "prometheus.internal/graph" in url
        assert "g0.expr=" in url
        assert "api_gateway" in url
        assert "g0.range_input=" in url
        assert "g0.tab=0" in url

    @mock.patch.dict(
        os.environ,
        {
            "AUDIT_EVIDENCE_BASE_URL": "https://audit.internal/evidence",
        },
    )
    def test_build_cascade_evidence_url(self):
        """CascadeEvent 증적 링크 생성."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        links = builder.build_postmortem_links(
            incident_id="PM-001",
            service_name="payment",
            cascade_event_id="cascade-abc123",
            evidence_hash="sha256:abcdef123456",
        )

        assert links.audit_evidence_link is not None
        assert "cascade/cascade-abc123" in links.audit_evidence_link
        # URL 인코딩 여부와 관계없이 verify 파라미터 존재 확인
        assert "verify=" in links.audit_evidence_link
        assert "abcdef123456" in links.audit_evidence_link

    def test_build_cascade_evidence_url_without_id_returns_none(self):
        """CascadeEvent ID 없이는 증적 링크가 None."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        links = builder.build_postmortem_links(
            incident_id="PM-001",
            service_name="payment",
        )

        assert links.audit_evidence_link is None

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_BASE_URL": "https://pm.internal",
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb",
        },
    )
    def test_build_notification_links(self):
        """알림용 간소화된 링크 생성."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        links = builder.build_notification_links(
            incident_id="PM-001",
            service_name="payment",
        )

        assert links.postmortem_url is not None
        assert links.dashboard_url is not None
        # 알림용에는 metrics_url, admin_url이 없음 (audit_log_url은 있음)
        assert links.metrics_url is None
        assert links.admin_url is None

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_BASE_URL": "https://pm.internal",
        },
    )
    def test_is_configured_returns_true(self):
        """최소 하나의 URL이 설정되면 is_configured는 True."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        assert builder.is_configured() is True

    def test_is_configured_returns_false_without_env(self):
        """환경변수 없이는 is_configured는 False."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        assert builder.is_configured() is False

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_BASE_URL": "https://pm.internal",
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb",
        },
    )
    def test_get_config_status(self):
        """설정 상태 딕셔너리 반환."""
        from selfhealing.services.postmortem.deep_links import PostmortemDeepLinkBuilder

        builder = PostmortemDeepLinkBuilder()
        status = builder.get_config_status()

        assert status["postmortem_configured"] is True
        assert status["dashboard_configured"] is True
        assert status["runbook_configured"] is False
        assert status["prometheus_configured"] is False


class TestPostmortemDeepLinkBuilderSingleton:
    """싱글톤 패턴 테스트."""

    @pytest.fixture(autouse=True)
    def reset_builder(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )

        reset_postmortem_deep_link_builder()
        yield
        reset_postmortem_deep_link_builder()

    def test_get_returns_same_instance(self):
        """get_postmortem_deep_link_builder는 동일 인스턴스 반환."""
        from selfhealing.services.postmortem.deep_links import (
            get_postmortem_deep_link_builder,
        )

        builder1 = get_postmortem_deep_link_builder()
        builder2 = get_postmortem_deep_link_builder()

        assert builder1 is builder2

    def test_reset_creates_new_instance(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.services.postmortem.deep_links import (
            get_postmortem_deep_link_builder,
            reset_postmortem_deep_link_builder,
        )

        builder1 = get_postmortem_deep_link_builder()
        reset_postmortem_deep_link_builder()
        builder2 = get_postmortem_deep_link_builder()

        assert builder1 is not builder2
