"""
Throttle SLA Alert URL 빌더 단위 테스트.

대상: selfhealing/services/throttle/throttle_sla_alert_urls.py
- ThrottleSlaActionableUrls (dataclass)
- ThrottleSlaAlertUrlBuilder (URL 빌더)
- 싱글톤 get/reset 함수
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


class TestThrottleSlaActionableUrls:
    """ThrottleSlaActionableUrls 데이터클래스 테스트."""

    def test_to_dict_all_set(self):
        """모든 URL 설정 시 to_dict 확인."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls(
            dashboard_url="http://a.com",
            admin_url="http://b.com",
            runbook_url="http://c.com",
        )
        d = urls.to_dict()
        assert d == {
            "dashboard_url": "http://a.com",
            "admin_url": "http://b.com",
            "runbook_url": "http://c.com",
        }

    def test_to_dict_all_none(self):
        """모든 URL이 None일 때 to_dict 확인."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls()
        d = urls.to_dict()
        assert d == {"dashboard_url": None, "admin_url": None, "runbook_url": None}

    def test_to_dict_partial(self):
        """일부 URL만 설정 시 to_dict 확인."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls(admin_url="http://admin.com")
        d = urls.to_dict()
        assert d["dashboard_url"] is None
        assert d["admin_url"] == "http://admin.com"
        assert d["runbook_url"] is None

    def test_has_any_url_true(self):
        """URL이 하나라도 있으면 True."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls(runbook_url="http://rb.com")
        assert urls.has_any_url() is True

    def test_has_any_url_false(self):
        """모든 URL이 None이면 False."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaActionableUrls,
        )

        urls = ThrottleSlaActionableUrls()
        assert urls.has_any_url() is False


class TestThrottleSlaAlertUrlBuilder:
    """ThrottleSlaAlertUrlBuilder URL 빌더 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def test_all_env_vars_set(self):
        """모든 환경변수 설정 시 URL 생성."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="payment",
                event_type="sla_critical",
            )

            assert "payment" in urls.dashboard_url
            assert "payment" in urls.admin_url
            assert "sla-critical" in urls.runbook_url
            assert urls.has_any_url()

    def test_no_env_vars(self):
        """환경변수 미설정 시 모든 URL None."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="payment")

            assert urls.dashboard_url is None
            assert urls.admin_url is None
            assert urls.runbook_url is None
            assert not urls.has_any_url()

    def test_dashboard_only(self):
        """대시보드 환경변수만 설정."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
                "THROTTLE_SLA_ADMIN_BASE_URL": "",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="order")

            assert urls.dashboard_url is not None
            assert "order" in urls.dashboard_url
            assert urls.admin_url is None
            assert urls.runbook_url is None
            assert urls.has_any_url()

    def test_admin_only(self):
        """Admin 환경변수만 설정."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "",
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
                "THROTTLE_SLA_RUNBOOK_URL": "",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="search", event_type="sla_warning")

            assert urls.dashboard_url is None
            assert urls.admin_url is not None
            assert "search" in urls.admin_url
            assert "sla_warning" in urls.admin_url
            assert urls.runbook_url is None

    def test_dashboard_with_rtt(self):
        """대시보드 URL에 rtt 쿼리 파라미터 포함."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="payment", rtt_ms=250.5)

            assert "var-rtt=250" in urls.dashboard_url
            assert "var-service=payment" in urls.dashboard_url

    def test_dashboard_without_rtt(self):
        """rtt_ms=None이면 var-rtt 파라미터 미포함."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_DASHBOARD_URL": "https://grafana.internal/d/throttle",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(service_name="payment", rtt_ms=None)

            assert "var-service=payment" in urls.dashboard_url
            assert "var-rtt" not in urls.dashboard_url

    def test_runbook_anchor_format(self):
        """Runbook URL 앵커에서 underscore가 hyphen으로 변환."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_RUNBOOK_URL": "https://docs.internal/runbooks/sla",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="test",
                event_type="sla_critical",
            )

            assert urls.runbook_url.endswith("#sla-critical")

    def test_admin_url_includes_event_type(self):
        """Admin URL에 event_type 파라미터 포함."""
        with patch.dict(
            os.environ,
            {
                "THROTTLE_SLA_ADMIN_BASE_URL": "/admin/throttle/",
            },
        ):
            from selfhealing.services.throttle.throttle_sla_alert_urls import (
                ThrottleSlaAlertUrlBuilder,
            )

            builder = ThrottleSlaAlertUrlBuilder()
            urls = builder.build_sla_alert_urls(
                service_name="api",
                event_type="sla_warning",
            )

            assert "event=sla_warning" in urls.admin_url


class TestThrottleSlaAlertUrlBuilderSingleton:
    """싱글톤 패턴 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def teardown_method(self):
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            reset_throttle_sla_alert_url_builder,
        )

        reset_throttle_sla_alert_url_builder()

    def test_singleton_same_instance(self):
        """동일 인스턴스 반환."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            get_throttle_sla_alert_url_builder,
        )

        b1 = get_throttle_sla_alert_url_builder()
        b2 = get_throttle_sla_alert_url_builder()
        assert b1 is b2

    def test_reset_creates_new_instance(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            get_throttle_sla_alert_url_builder,
            reset_throttle_sla_alert_url_builder,
        )

        b1 = get_throttle_sla_alert_url_builder()
        reset_throttle_sla_alert_url_builder()
        b2 = get_throttle_sla_alert_url_builder()
        assert b2 is not b1

    def test_builder_type(self):
        """반환 타입이 ThrottleSlaAlertUrlBuilder."""
        from selfhealing.services.throttle.throttle_sla_alert_urls import (
            ThrottleSlaAlertUrlBuilder,
            get_throttle_sla_alert_url_builder,
        )

        builder = get_throttle_sla_alert_url_builder()
        assert isinstance(builder, ThrottleSlaAlertUrlBuilder)
