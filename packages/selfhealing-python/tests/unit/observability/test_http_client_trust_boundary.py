"""
SelfHealingHttpClient Trust Boundary 단위 테스트.

대상: selfhealing.services.http_client.SelfHealingHttpClient._should_propagate_context
검증:
- propagate_context=False 명시 시 무조건 차단
- propagate_context=True(기본) 시 DNS suffix 매칭으로 내부/외부 판별
- URL 파싱 실패 시 Fail-Closed (차단)

테스트 분류 (UNIT_TEST_GUIDELINES §0):
- Behavior: _should_propagate_context() 동작 검증 (소스 참조)
- Contract: propagate_context 기본값 및 _DEFAULT_INTERNAL_DNS_SUFFIXES 계약 검증

참조 소스:
- services/http_client.py (_should_propagate_context, _DEFAULT_INTERNAL_DNS_SUFFIXES)
- settings/cell_topology.py (CellTopologySettings.internal_dns_suffixes)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.http_client import (
    _DEFAULT_INTERNAL_DNS_SUFFIXES,
    SelfHealingHttpClient,
)


class TestDefaultInternalDnsSuffixesContract:
    """_DEFAULT_INTERNAL_DNS_SUFFIXES 설계 계약값 검증."""

    def test_contains_kubernetes_suffix(self):
        """Kubernetes 서비스 DNS 접미사가 포함되어야 한다."""
        assert ".svc.cluster.local" in _DEFAULT_INTERNAL_DNS_SUFFIXES

    def test_contains_internal_suffix(self):
        """내부 DNS 규약 접미사가 포함되어야 한다."""
        assert ".internal" in _DEFAULT_INTERNAL_DNS_SUFFIXES


class TestPropagateContextFlagContract:
    """propagate_context 파라미터 기본값 계약 검증."""

    def test_default_propagate_context_true(self):
        """propagate_context 기본값은 True이다."""
        client = SelfHealingHttpClient()
        assert client._propagate_context is True

    def test_explicit_false_stored(self):
        """propagate_context=False 명시 시 인스턴스에 저장된다."""
        client = SelfHealingHttpClient(propagate_context=False)
        assert client._propagate_context is False


class TestShouldPropagateContextBehavior:
    """_should_propagate_context() 동작 검증."""

    def test_propagate_false_always_blocks(self):
        """propagate_context=False면 어떤 URL이든 차단한다."""
        client = SelfHealingHttpClient(propagate_context=False)
        assert client._should_propagate_context("http://order-svc.default.svc.cluster.local/api") is False

    def test_internal_kubernetes_url_propagates(self):
        """Kubernetes 내부 서비스 URL은 전파를 허용한다."""
        client = SelfHealingHttpClient()
        with patch("selfhealing.settings.cell_topology.get_cell_topology_settings") as mock_settings:
            mock_settings.return_value = MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"])
            assert client._should_propagate_context("http://payment-svc.default.svc.cluster.local/api/v1/pay") is True

    def test_internal_dns_suffix_propagates(self):
        """.internal 접미사는 내부 서비스로 전파를 허용한다."""
        client = SelfHealingHttpClient()
        with patch("selfhealing.settings.cell_topology.get_cell_topology_settings") as mock_settings:
            mock_settings.return_value = MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"])
            assert client._should_propagate_context("http://auth-service.internal/token") is True

    def test_external_url_blocked(self):
        """외부 서드파티 URL은 차단한다."""
        client = SelfHealingHttpClient()
        with patch("selfhealing.settings.cell_topology.get_cell_topology_settings") as mock_settings:
            mock_settings.return_value = MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"])
            assert client._should_propagate_context("https://api.tosspayments.com/v1/payments") is False

    def test_aws_s3_blocked(self):
        """AWS S3 같은 클라우드 서비스 URL도 차단한다."""
        client = SelfHealingHttpClient()
        with patch("selfhealing.settings.cell_topology.get_cell_topology_settings") as mock_settings:
            mock_settings.return_value = MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"])
            assert client._should_propagate_context("https://s3.amazonaws.com/bucket/key") is False

    def test_empty_hostname_blocked(self):
        """호스트네임이 빈 URL은 차단한다."""
        client = SelfHealingHttpClient()
        assert client._should_propagate_context("") is False

    def test_malformed_url_blocked(self):
        """잘못된 형식의 URL은 Fail-Closed로 차단한다."""
        client = SelfHealingHttpClient()
        assert client._should_propagate_context("not-a-url") is False

    def test_settings_none_suffixes_uses_fallback(self):
        """Settings에서 internal_dns_suffixes가 None이면 기본 폴백을 사용한다."""
        client = SelfHealingHttpClient()
        with patch("selfhealing.settings.cell_topology.get_cell_topology_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                internal_dns_suffixes=None,
            )
            # .svc.cluster.local은 기본 폴백에 포함
            assert client._should_propagate_context("http://svc.default.svc.cluster.local/api") is True

    def test_settings_load_exception_blocks(self):
        """Settings 로드에서 예외 발생 시 Fail-Closed로 차단한다."""
        client = SelfHealingHttpClient()
        with patch(
            "selfhealing.settings.cell_topology.get_cell_topology_settings",
            side_effect=RuntimeError("settings error"),
        ):
            assert client._should_propagate_context("http://order-svc.default.svc.cluster.local/api") is False


class TestExecuteRequestBaggageConditionalBehavior:
    """_execute_request()의 조건부 Baggage 동기화 동작 검증."""

    def test_blocks_baggage_for_external_url(self):
        """외부 URL 요청 시 sync_contextvars_to_baggage()가 호출되지 않는다."""
        client = SelfHealingHttpClient(propagate_context=False)
        sync_called = False

        def mock_sync():
            nonlocal sync_called
            sync_called = True
            return "mock-token"

        with (
            patch(
                "selfhealing.observability.baggage.sync_contextvars_to_baggage",
                side_effect=mock_sync,
            ),
            patch("selfhealing.observability.baggage.detach_baggage_token"),
            patch("requests.get", return_value=MagicMock(status_code=200)),
        ):
            client.get("https://api.external.com/resource")

        assert sync_called is False

    def test_propagates_baggage_for_internal_url(self):
        """내부 URL 요청 시 sync_contextvars_to_baggage()가 호출된다."""
        client = SelfHealingHttpClient()
        sync_called = False

        def mock_sync():
            nonlocal sync_called
            sync_called = True
            return "mock-token"

        with (
            patch(
                "selfhealing.observability.baggage.sync_contextvars_to_baggage",
                side_effect=mock_sync,
            ),
            patch("selfhealing.observability.baggage.detach_baggage_token"),
            patch("requests.get", return_value=MagicMock(status_code=200)),
            patch(
                "selfhealing.settings.cell_topology.get_cell_topology_settings",
            ) as mock_settings,
        ):
            mock_settings.return_value = MagicMock(internal_dns_suffixes=[".svc.cluster.local"])
            client.get("http://order-svc.default.svc.cluster.local/api")

        assert sync_called is True
