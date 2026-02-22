"""
단위 테스트 — SelfHealingHttpClient의 Deadline timeout 조정.

테스트 항목:
- deadline 활성 시 X-Deadline-Remaining 헤더가 _get_headers()에 포함되지 않음
  (266 문서에 따라 OTel Baggage 전파로 대체됨)
- deadline 기반 timeout 자동 축소
- timeout이 deadline보다 짧으면 timeout 유지
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.scaling.deadline_context import (
    DEADLINE_HEADER,
    _request_deadline,
    clear_deadline,
    set_deadline,
)
from selfhealing.services.http_client import SelfHealingHttpClient


@pytest.fixture(autouse=True)
def _reset_deadline():
    """각 테스트 전후로 deadline ContextVar를 초기화한다."""
    _request_deadline.set(None)
    yield
    _request_deadline.set(None)


class TestHttpClientDeadlineHeaderRemovalBehavior:
    """SelfHealingHttpClient Deadline 헤더가 OTel Baggage로 대체된 동작 검증."""

    @pytest.fixture
    def client(self):
        with patch("selfhealing.settings.http_client.get_http_client_settings") as mock_settings:
            settings = MagicMock()
            settings.default_timeout = 30.0
            mock_settings.return_value = settings
            return SelfHealingHttpClient()

    def test_deadline_header_no_longer_in_get_headers(self, client):
        """deadline 활성 시에도 _get_headers()에 X-Deadline-Remaining이 포함되지 않는다.

        OTel Baggage 전파로 대체되었으므로 수동 헤더 주입은 제거됨.
        """
        set_deadline(3000.0)

        headers = client._get_headers()

        assert DEADLINE_HEADER not in headers

    def test_no_deadline_no_header(self, client):
        """deadline 미설정 시에도 헤더가 포함되지 않는다."""
        headers = client._get_headers()

        assert DEADLINE_HEADER not in headers

    def test_expired_deadline_no_header(self, client):
        """만료된 deadline 시 헤더가 포함되지 않는다."""
        set_deadline(0.0)

        headers = client._get_headers()

        assert DEADLINE_HEADER not in headers


class TestHttpClientTimeoutAdjustmentBehavior:
    """SelfHealingHttpClient Deadline 기반 timeout 조정 동작 검증."""

    @pytest.fixture
    def client(self):
        with patch("selfhealing.settings.http_client.get_http_client_settings") as mock_settings:
            settings = MagicMock()
            settings.default_timeout = 30.0
            mock_settings.return_value = settings
            return SelfHealingHttpClient()

    @patch("selfhealing.services.http_client.req_lib", create=True)
    def test_timeout_adjusted_to_deadline(self, mock_req_lib, client):
        """deadline(2초) < default_timeout(30초) → timeout이 ~2초로 축소."""
        import requests as req_lib

        set_deadline(2050.0)  # buffer 차감 후 ~2000ms = 2초

        with patch.object(req_lib, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            client.get("http://example.com/test")

            call_kwargs = mock_get.call_args
            timeout_used = call_kwargs.kwargs.get("timeout", call_kwargs[1].get("timeout"))
            # deadline_timeout ~ 2.0초, default_timeout = 30초
            # deadline_timeout < default_timeout → deadline_timeout 사용
            assert timeout_used is not None
            assert timeout_used < 30.0
            assert timeout_used <= 2.1  # ~2초 + 작은 오차

    @patch("selfhealing.services.http_client.req_lib", create=True)
    def test_timeout_not_adjusted_when_no_deadline(self, mock_req_lib, client):
        """deadline 미설정 → 기본 timeout 유지."""
        import requests as req_lib

        with patch.object(req_lib, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            client.get("http://example.com/test")

            call_kwargs = mock_get.call_args
            timeout_used = call_kwargs.kwargs.get("timeout", call_kwargs[1].get("timeout"))
            assert timeout_used == client.default_timeout

    @patch("selfhealing.services.http_client.req_lib", create=True)
    def test_explicit_shorter_timeout_preserved(self, mock_req_lib, client):
        """명시적 timeout(1초) < deadline(30초) → 명시적 timeout 유지."""
        import requests as req_lib

        set_deadline(30050.0)  # buffer 차감 후 ~30000ms = 30초

        with patch.object(req_lib, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            client.get("http://example.com/test", timeout=1.0)

            call_kwargs = mock_get.call_args
            timeout_used = call_kwargs.kwargs.get("timeout", call_kwargs[1].get("timeout"))
            # 명시적 timeout 1초를 사용하되, deadline(30초)보다 짧으므로 그대로
            assert timeout_used == 1.0
