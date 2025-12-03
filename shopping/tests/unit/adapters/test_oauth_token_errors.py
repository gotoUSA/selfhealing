"""OAuth 토큰 에러 테스트

리팩토링 후: OAuth 로직이 SocialAuthService로 이동됨
"""

from http import HTTPStatus
from unittest.mock import Mock, patch

import pytest
import requests

from shopping.services.social_auth_service import SocialAuthError, SocialAuthService
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
    mock_response.text = f'{{"error": "{error}", "error_description": "{description}"}}'
    mock_response.json.return_value = {"error": error, "error_description": description}
    return mock_response


class TestOAuthTokenExchange:
    """OAuth 토큰 교환 (SocialAuthService)"""

    @pytest.mark.parametrize(
        "provider,status_code,error",
        [
            ("google", 400, "invalid_grant"),
            ("kakao", 401, "invalid_client"),
        ],
    )
    @patch("shopping.services.social_auth_service.requests.post")
    def test_invalid_response_raises_error(self, mock_post, provider, status_code, error):
        # Arrange
        mock_post.return_value = _create_error_response(status_code, error, "error")

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token(provider, "code", "http://callback")

        assert exc_info.value.code == "TOKEN_EXCHANGE_FAILED"

    @pytest.mark.parametrize(
        "exception",
        [
            requests.exceptions.Timeout("timeout"),
            requests.exceptions.ConnectionError("connection failed"),
        ],
    )
    @patch("shopping.services.social_auth_service.requests.post")
    def test_network_error_raises_error(self, mock_post, exception):
        # Arrange
        mock_post.side_effect = exception

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token("google", "code", "http://callback")

        assert exc_info.value.code in ("TOKEN_REQUEST_ERROR", "CONNECTION_ERROR")


class TestOAuthUserInfo:
    """OAuth 사용자 정보 조회 (SocialAuthService)"""

    @pytest.mark.parametrize("provider", ["google", "kakao", "naver"])
    @patch("shopping.services.social_auth_service.requests.get")
    def test_invalid_token_raises_error(self, mock_get, provider):
        # Arrange
        mock_get.return_value = _create_error_response(401, "invalid_token", "expired")

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.get_user_info(provider, "invalid_token")

        assert exc_info.value.code == "USER_INFO_FETCH_FAILED"

    @patch("shopping.services.social_auth_service.requests.get")
    def test_network_timeout_raises_error(self, mock_get):
        # Arrange
        mock_get.side_effect = requests.exceptions.Timeout()

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.get_user_info("google", "token")

        assert exc_info.value.code in ("USER_INFO_REQUEST_ERROR", "CONNECTION_ERROR")


class TestOAuthCallback:
    """OAuth 콜백 흐름 (View + Service 통합)"""

    @patch("shopping.views.social_auth_views.SocialAuthService.process_oauth_callback")
    def test_token_exchange_failure_redirects(self, mock_process):
        # Arrange
        mock_process.side_effect = SocialAuthError("토큰 교환 실패", code="TOKEN_EXCHANGE_FAILED")
        view = SocialCallbackView()

        # Act
        response = view.get(_create_mock_request(state="google_abc123"))

        # Assert
        assert response.status_code == HTTPStatus.FOUND
        assert "error" in response.url

    @patch("shopping.views.social_auth_views.SocialAuthService.process_oauth_callback")
    def test_userinfo_failure_redirects(self, mock_process):
        # Arrange
        mock_process.side_effect = SocialAuthError("사용자 정보 조회 실패", code="USER_INFO_FETCH_FAILED")
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
