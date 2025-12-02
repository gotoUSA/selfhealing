"""OAuth 토큰 에러 테스트"""

from http import HTTPStatus
from unittest.mock import Mock, patch

import pytest
import requests

from shopping.views.social_auth_views import SocialCallbackView


def _create_mock_request(code="test_auth_code", state="google_abc123", error=None):
    mock_request = Mock()
    mock_request.GET = {"code": code, "state": state}
    if error:
        mock_request.GET["error"] = error
        mock_request.GET["error_description"] = f"{error} description"
    return mock_request


def _create_error_response(status_code, error, description):
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {"error": error, "error_description": description}
    return mock_response


class TestOAuthTokenExchange:
    """OAuth 토큰 교환"""

    @pytest.mark.parametrize(
        "provider,status_code,error",
        [
            ("google", 400, "invalid_grant"),
            ("kakao", 401, "invalid_client"),
        ],
    )
    @patch("shopping.views.social_auth_views.requests.post")
    def test_invalid_response_returns_none(self, mock_post, provider, status_code, error):
        # Arrange
        mock_post.return_value = _create_error_response(status_code, error, "error")
        view = SocialCallbackView()

        # Act
        result = view._exchange_code_for_token(provider, "code", _create_mock_request())

        # Assert
        assert result is None

    @pytest.mark.parametrize(
        "exception",
        [
            requests.exceptions.Timeout("timeout"),
            requests.exceptions.ConnectionError("connection failed"),
        ],
    )
    @patch("shopping.views.social_auth_views.requests.post")
    def test_network_error_returns_none(self, mock_post, exception):
        # Arrange
        mock_post.side_effect = exception
        view = SocialCallbackView()

        # Act
        result = view._exchange_code_for_token("google", "code", _create_mock_request())

        # Assert
        assert result is None


class TestOAuthUserInfo:
    """OAuth 사용자 정보 조회"""

    @pytest.mark.parametrize("provider", ["google", "kakao", "naver"])
    @patch("shopping.views.social_auth_views.requests.get")
    def test_invalid_token_returns_none(self, mock_get, provider):
        # Arrange
        mock_get.return_value = _create_error_response(401, "invalid_token", "expired")
        view = SocialCallbackView()

        # Act
        result = view._get_user_info(provider, "invalid_token")

        # Assert
        assert result is None

    @patch("shopping.views.social_auth_views.requests.get")
    def test_network_timeout_returns_none(self, mock_get):
        # Arrange
        mock_get.side_effect = requests.exceptions.Timeout()
        view = SocialCallbackView()

        # Act
        result = view._get_user_info("google", "token")

        # Assert
        assert result is None


class TestOAuthCallback:
    """OAuth 콜백 흐름"""

    @patch.object(SocialCallbackView, "_exchange_code_for_token", return_value=None)
    def test_token_exchange_failure_redirects(self, mock_exchange):
        # Arrange
        view = SocialCallbackView()

        # Act
        response = view.get(_create_mock_request(state="google_abc123"))

        # Assert
        assert response.status_code == HTTPStatus.FOUND
        assert "error" in response.url

    @patch.object(SocialCallbackView, "_get_user_info", return_value=None)
    @patch.object(SocialCallbackView, "_exchange_code_for_token", return_value={"access_token": "token"})
    def test_userinfo_failure_redirects(self, mock_exchange, mock_userinfo):
        # Arrange
        view = SocialCallbackView()

        # Act
        response = view.get(_create_mock_request(state="kakao_abc123"))

        # Assert
        assert response.status_code == HTTPStatus.FOUND
        assert "error" in response.url

    @pytest.mark.parametrize(
        "request_getter",
        [
            lambda: _create_mock_request(error="access_denied"),
            lambda: Mock(GET={"state": "google_abc123"}),
            lambda: _create_mock_request(state="unknown_abc123"),
        ],
    )
    def test_invalid_request_redirects(self, request_getter):
        # Arrange
        view = SocialCallbackView()

        # Act
        response = view.get(request_getter())

        # Assert
        assert response.status_code == HTTPStatus.FOUND
        assert "error" in response.url
