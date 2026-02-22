"""
SelfHealingHttpClient Baggage Pre-request Hook 단위 테스트.

대상: selfhealing.services.http_client.SelfHealingHttpClient._execute_request
검증:
- 수동 Deadline 헤더 주입이 제거되었는지
- Pre-request Baggage 동기화가 내부 URL에서 수행되는지
"""

from unittest.mock import MagicMock, patch

import pytest

# Trust Boundary 필터를 통과하는 내부 서비스 URL
_INTERNAL_TEST_URL = "http://order-svc.default.svc.cluster.local/api"


class TestHttpClientDeadlineHeaderRemovalContract:
    """수동 Deadline 헤더 주입 제거 계약 검증."""

    def test_get_headers_does_not_contain_deadline_header(self):
        """_get_headers()가 더 이상 X-Deadline-Remaining을 포함하지 않는다."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient()

        # Deadline ContextVar에 값을 설정해도 헤더에 포함되지 않아야 함
        with patch(
            "selfhealing.scaling.deadline_context.get_propagation_header_value",
            return_value="2500ms",
        ):
            headers = client._get_headers()
            assert "X-Deadline-Remaining" not in headers

    def test_get_headers_still_contains_chaos_headers(self):
        """Chaos 실험 헤더는 제거되지 않고 유지된다."""
        from selfhealing.services.http_client import (
            CHAOS_EXPERIMENT_ID_HEADER,
            SYNTHETIC_HEADER,
            SelfHealingHttpClient,
            _is_chaos_request,
        )

        client = SelfHealingHttpClient()
        client._experiment_id = "exp-123"

        token = _is_chaos_request.set(True)
        try:
            headers = client._get_headers()
            assert SYNTHETIC_HEADER in headers
            assert CHAOS_EXPERIMENT_ID_HEADER in headers
        finally:
            _is_chaos_request.set(False)


class TestHttpClientBaggagePreRequestHookBehavior:
    """_execute_request() Baggage pre-request hook 동작 검증."""

    def test_syncs_baggage_before_request(self):
        """HTTP 요청 전에 sync_contextvars_to_baggage()가 호출된다."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient()
        sync_called = False

        def mock_sync():
            nonlocal sync_called
            sync_called = True
            return "mock-token"

        with (
            patch("selfhealing.observability.baggage.sync_contextvars_to_baggage", side_effect=mock_sync),
            patch("selfhealing.observability.baggage.detach_baggage_token"),
            patch("requests.get", return_value=MagicMock(status_code=200)),
            patch(
                "selfhealing.settings.cell_topology.get_cell_topology_settings",
                return_value=MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"]),
            ),
        ):
            client.get(_INTERNAL_TEST_URL)

        assert sync_called is True

    def test_detaches_token_after_request(self):
        """HTTP 요청 후 detach_baggage_token()이 호출된다."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient()
        sentinel_token = object()
        detached_token = None

        def mock_detach(t):
            nonlocal detached_token
            detached_token = t

        with (
            patch(
                "selfhealing.observability.baggage.sync_contextvars_to_baggage",
                return_value=sentinel_token,
            ),
            patch(
                "selfhealing.observability.baggage.detach_baggage_token",
                side_effect=mock_detach,
            ),
            patch("requests.get", return_value=MagicMock(status_code=200)),
            patch(
                "selfhealing.settings.cell_topology.get_cell_topology_settings",
                return_value=MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"]),
            ),
        ):
            client.get(_INTERNAL_TEST_URL)

        assert detached_token is sentinel_token

    def test_detaches_token_on_request_exception(self):
        """HTTP 요청에서 예외 발생 시에도 token이 detach 된다."""
        from selfhealing.services.http_client import SelfHealingHttpClient

        client = SelfHealingHttpClient()
        sentinel_token = object()
        detach_called = False

        def mock_detach(t):
            nonlocal detach_called
            detach_called = True

        with (
            patch(
                "selfhealing.observability.baggage.sync_contextvars_to_baggage",
                return_value=sentinel_token,
            ),
            patch(
                "selfhealing.observability.baggage.detach_baggage_token",
                side_effect=mock_detach,
            ),
            patch("requests.get", side_effect=ConnectionError("timeout")),
            patch(
                "selfhealing.settings.cell_topology.get_cell_topology_settings",
                return_value=MagicMock(internal_dns_suffixes=[".svc.cluster.local", ".internal"]),
            ),
        ):
            with pytest.raises(ConnectionError):
                client.get(_INTERNAL_TEST_URL)

        assert detach_called is True
