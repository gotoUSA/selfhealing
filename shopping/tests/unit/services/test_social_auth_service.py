"""SocialAuthService 단위 테스트

OAuth 제공자(Google, Kakao, Naver)와의 통신 및 인증 처리 테스트
- Authorization code → Access token 교환
- 사용자 정보 조회
- 사용자 정보 정규화
- 네트워크 오류 처리
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from shopping.services.social_auth_service import (
    NormalizedUserInfo,
    OAuthTokens,
    SocialAuthError,
    SocialAuthService,
)
from shopping.tests.factories import OAuthDataBuilder


class TestSocialAuthError:
    """SocialAuthError 예외 클래스 테스트"""

    def test_error_stores_message_and_code(self):
        """정상 케이스: message와 code 속성 저장 확인"""
        # Act
        error = SocialAuthError("토큰 교환 실패", code="TOKEN_EXCHANGE_FAILED")

        # Assert
        assert error.message == "토큰 교환 실패"
        assert error.code == "TOKEN_EXCHANGE_FAILED"
        assert str(error) == "토큰 교환 실패"

    def test_error_default_code(self):
        """경계 케이스: 기본 code 값 확인"""
        # Act
        error = SocialAuthError("에러 메시지만 전달")

        # Assert
        assert error.code == "SOCIAL_AUTH_ERROR"


class TestOAuthTokens:
    """OAuthTokens dataclass 테스트"""

    def test_oauth_tokens_required_field(self):
        """정상 케이스: 필수 필드만 설정"""
        # Act
        tokens = OAuthTokens(access_token="test_access_token")

        # Assert
        assert tokens.access_token == "test_access_token"
        assert tokens.token_type == "Bearer"
        assert tokens.refresh_token is None
        assert tokens.expires_in is None

    def test_oauth_tokens_all_fields(self):
        """정상 케이스: 모든 필드 설정"""
        # Act
        tokens = OAuthTokens(
            access_token="access_123",
            token_type="Bearer",
            refresh_token="refresh_456",
            expires_in=3600,
        )

        # Assert
        assert tokens.access_token == "access_123"
        assert tokens.refresh_token == "refresh_456"
        assert tokens.expires_in == 3600


class TestNormalizedUserInfo:
    """NormalizedUserInfo dataclass 테스트"""

    def test_normalized_user_info_with_all_fields(self):
        """정상 케이스: 모든 필드 설정"""
        # Act
        info = NormalizedUserInfo(
            email="user@example.com",
            name="홍길동",
            provider_id="google_123",
            profile_image="https://example.com/image.jpg",
        )

        # Assert
        assert info.email == "user@example.com"
        assert info.name == "홍길동"
        assert info.provider_id == "google_123"
        assert info.profile_image == "https://example.com/image.jpg"

    def test_normalized_user_info_optional_fields(self):
        """경계 케이스: profile_image 미설정"""
        # Act
        info = NormalizedUserInfo(
            email="user@example.com",
            name="홍길동",
            provider_id="google_123",
        )

        # Assert
        assert info.profile_image is None


class TestSupportedProviders:
    """OAuth 제공자 지원 확인 테스트"""

    def test_get_supported_providers_returns_list(self):
        """정상 케이스: 지원 provider 목록 반환"""
        # Act
        providers = SocialAuthService.get_supported_providers()

        # Assert
        assert isinstance(providers, list)
        assert "google" in providers
        assert "kakao" in providers
        assert "naver" in providers

    @pytest.mark.parametrize(
        "provider,expected",
        [
            ("google", True),
            ("kakao", True),
            ("naver", True),
            ("facebook", False),
            ("apple", False),
            (None, False),
            ("", False),
        ],
    )
    def test_is_supported_provider(self, provider, expected):
        """정상/경계 케이스: provider 지원 여부 확인"""
        # Act
        result = SocialAuthService.is_supported_provider(provider)

        # Assert
        assert result is expected


class TestExchangeCodeForToken:
    """Authorization code → Access token 교환 테스트"""

    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_exchange_code_success(self, mock_post):
        """정상 케이스: 토큰 교환 성공"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "ya29.test_access_token",
            "token_type": "Bearer",
            "refresh_token": "1//test_refresh",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        # Act
        result = SocialAuthService.exchange_code_for_token(
            provider="google",
            code="test_auth_code",
            redirect_uri="http://localhost:8000/callback",
        )

        # Assert
        assert isinstance(result, OAuthTokens)
        assert result.access_token == "ya29.test_access_token"
        assert result.token_type == "Bearer"
        assert result.refresh_token == "1//test_refresh"

    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_exchange_code_uses_default_redirect_uri(self, mock_post):
        """정상 케이스: redirect_uri None일 때 설정에서 기본값 사용"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "test_token"}
        mock_post.return_value = mock_response

        # Act
        SocialAuthService.exchange_code_for_token(
            provider="google",
            code="test_code",
            redirect_uri=None,  # 기본값 사용
        )

        # Assert - 요청이 발생했는지 확인
        mock_post.assert_called_once()

    def test_exchange_code_unsupported_provider_raises(self):
        """예외 케이스: 미지원 provider"""
        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token(
                provider="facebook",
                code="test_code",
            )

        assert exc_info.value.code == "UNSUPPORTED_PROVIDER"
        assert "facebook" in str(exc_info.value)

    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_exchange_code_http_error_raises(self, mock_post):
        """예외 케이스: HTTP 에러 응답 (200 아닌 경우)"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_grant"
        mock_post.return_value = mock_response

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token(
                provider="google",
                code="invalid_code",
            )

        assert exc_info.value.code == "TOKEN_EXCHANGE_FAILED"

    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_exchange_code_missing_access_token_raises(self, mock_post):
        """예외 케이스: 응답에 access_token 없음"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "error": "invalid_request",
            "error_description": "Missing required parameter",
        }
        mock_post.return_value = mock_response

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token(
                provider="google",
                code="test_code",
            )

        assert exc_info.value.code == "TOKEN_EXCHANGE_FAILED"

    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_exchange_code_network_error_raises(self, mock_post):
        """예외 케이스: 네트워크 오류"""
        # Arrange
        mock_post.side_effect = requests.RequestException("Connection timeout")

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.exchange_code_for_token(
                provider="google",
                code="test_code",
            )

        assert exc_info.value.code == "TOKEN_REQUEST_ERROR"


class TestGetUserInfo:
    """OAuth 제공자로부터 사용자 정보 조회 테스트"""

    @patch("shopping.services.social_auth_service.requests.get")
    def test_get_user_info_success(self, mock_get):
        """정상 케이스: 사용자 정보 조회 성공"""
        # Arrange
        google_response = OAuthDataBuilder.google()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = google_response
        mock_get.return_value = mock_response

        # Act
        result = SocialAuthService.get_user_info(
            provider="google",
            access_token="valid_access_token",
        )

        # Assert
        assert result["email"] == "testuser@gmail.com"
        assert result["id"] == "google_user_id_123456"

    def test_get_user_info_unsupported_provider_raises(self):
        """예외 케이스: 미지원 provider"""
        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.get_user_info(
                provider="twitter",
                access_token="test_token",
            )

        assert exc_info.value.code == "UNSUPPORTED_PROVIDER"

    @patch("shopping.services.social_auth_service.requests.get")
    def test_get_user_info_http_error_raises(self, mock_get):
        """예외 케이스: HTTP 에러 응답"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.get_user_info(
                provider="google",
                access_token="expired_token",
            )

        assert exc_info.value.code == "USER_INFO_FETCH_FAILED"

    @patch("shopping.services.social_auth_service.requests.get")
    def test_get_user_info_network_error_raises(self, mock_get):
        """예외 케이스: 네트워크 오류"""
        # Arrange
        mock_get.side_effect = requests.RequestException("Network unreachable")

        # Act & Assert
        with pytest.raises(SocialAuthError) as exc_info:
            SocialAuthService.get_user_info(
                provider="google",
                access_token="test_token",
            )

        assert exc_info.value.code == "USER_INFO_REQUEST_ERROR"


class TestNormalizeUserInfo:
    """사용자 정보 정규화 테스트"""

    def test_normalize_google_response(self):
        """정상 케이스: Google 응답 정규화"""
        # Arrange
        raw_data = OAuthDataBuilder.google(
            email="google_user@gmail.com",
            user_id="google_12345",
        )

        # Act
        result = SocialAuthService.normalize_user_info("google", raw_data)

        # Assert
        assert isinstance(result, NormalizedUserInfo)
        assert result.email == "google_user@gmail.com"
        assert result.name == "Test User"
        assert result.provider_id == "google_12345"

    def test_normalize_kakao_response(self):
        """정상 케이스: Kakao 응답 정규화"""
        # Arrange
        raw_data = OAuthDataBuilder.kakao(
            email="kakao_user@kakao.com",
            user_id=9876543210,
        )

        # Act
        result = SocialAuthService.normalize_user_info("kakao", raw_data)

        # Assert
        assert result.email == "kakao_user@kakao.com"
        assert result.name == "테스트유저"
        assert result.provider_id == "9876543210"

    def test_normalize_naver_response(self):
        """정상 케이스: Naver 응답 정규화"""
        # Arrange
        raw_data = OAuthDataBuilder.naver(
            email="naver_user@naver.com",
            user_id="naver_abc123",
        )

        # Act
        result = SocialAuthService.normalize_user_info("naver", raw_data)

        # Assert
        assert result.email == "naver_user@naver.com"
        assert result.name == "테스트"  # name 우선, 없으면 nickname
        assert result.provider_id == "naver_abc123"

    def test_normalize_naver_uses_nickname_when_no_name(self):
        """경계 케이스: Naver name 없으면 nickname 사용"""
        # Arrange
        raw_data = {
            "response": {
                "id": "naver_no_name",
                "email": "noname@naver.com",
                "nickname": "별명사용자",
                # name 필드 없음
            }
        }

        # Act
        result = SocialAuthService.normalize_user_info("naver", raw_data)

        # Assert
        assert result.name == "별명사용자"

    def test_normalize_unknown_provider_returns_empty(self):
        """경계 케이스: 알 수 없는 provider는 빈 정보 반환"""
        # Arrange
        raw_data = {"email": "test@unknown.com", "name": "Unknown User"}

        # Act
        result = SocialAuthService.normalize_user_info("unknown_provider", raw_data)

        # Assert
        assert result.email is None
        assert result.name is None
        assert result.provider_id is None


class TestProcessOAuthCallback:
    """OAuth 콜백 전체 처리 테스트"""

    @patch("shopping.services.social_auth_service.requests.get")
    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_process_oauth_callback_success(self, mock_post, mock_get):
        """정상 케이스: OAuth 전체 플로우 성공"""
        # Arrange - 토큰 교환 응답
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {"access_token": "test_access_token"}
        mock_post.return_value = token_response

        # Arrange - 사용자 정보 조회 응답
        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = OAuthDataBuilder.google(
            email="oauth_user@gmail.com",
            user_id="google_oauth_123",
        )
        mock_get.return_value = userinfo_response

        # Act
        result = SocialAuthService.process_oauth_callback(
            provider="google",
            code="valid_auth_code",
            redirect_uri="http://localhost:8000/callback",
        )

        # Assert
        assert result["email"] == "oauth_user@gmail.com"
        assert result["provider_id"] == "google_oauth_123"
        assert result["name"] == "Test User"

    @patch("shopping.services.social_auth_service.requests.get")
    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test_id", "KAKAO_CLIENT_SECRET": "test_secret"})
    def test_process_oauth_callback_kakao(self, mock_post, mock_get):
        """정상 케이스: Kakao OAuth 플로우"""
        # Arrange
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {"access_token": "kakao_token"}
        mock_post.return_value = token_response

        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = OAuthDataBuilder.kakao(
            email="kakao@kakao.com",
            user_id=1234567890,
        )
        mock_get.return_value = userinfo_response

        # Act
        result = SocialAuthService.process_oauth_callback(
            provider="kakao",
            code="kakao_auth_code",
        )

        # Assert
        assert result["email"] == "kakao@kakao.com"
        assert result["provider_id"] == "1234567890"

    @patch("shopping.services.social_auth_service.requests.get")
    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"NAVER_CLIENT_ID": "test_id", "NAVER_CLIENT_SECRET": "test_secret"})
    def test_process_oauth_callback_naver(self, mock_post, mock_get):
        """정상 케이스: Naver OAuth 플로우"""
        # Arrange
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {"access_token": "naver_token"}
        mock_post.return_value = token_response

        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = OAuthDataBuilder.naver(
            email="naver@naver.com",
            user_id="naver_id_456",
        )
        mock_get.return_value = userinfo_response

        # Act
        result = SocialAuthService.process_oauth_callback(
            provider="naver",
            code="naver_auth_code",
        )

        # Assert
        assert result["email"] == "naver@naver.com"
        assert result["provider_id"] == "naver_id_456"

    @patch("shopping.services.social_auth_service.requests.get")
    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_process_oauth_callback_uses_default_redirect_uri(self, mock_post, mock_get):
        """정상 케이스: redirect_uri None일 때 기본값 사용"""
        # Arrange
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {"access_token": "test_token"}
        mock_post.return_value = token_response

        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = OAuthDataBuilder.google()
        mock_get.return_value = userinfo_response

        # Act - redirect_uri=None으로 호출
        result = SocialAuthService.process_oauth_callback(
            provider="google",
            code="test_code",
            redirect_uri=None,
        )

        # Assert - 에러 없이 정상 처리됨
        assert "email" in result

    @patch("shopping.services.social_auth_service.requests.get")
    @patch("shopping.services.social_auth_service.requests.post")
    @patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    def test_process_oauth_callback_logging(self, mock_post, mock_get, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        import logging

        caplog.set_level(logging.INFO, logger="shopping.services.social_auth_service")

        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {"access_token": "test_token"}
        mock_post.return_value = token_response

        userinfo_response = MagicMock()
        userinfo_response.status_code = 200
        userinfo_response.json.return_value = OAuthDataBuilder.google()
        mock_get.return_value = userinfo_response

        # Act
        SocialAuthService.process_oauth_callback(
            provider="google",
            code="test_code",
        )

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("OAuth 콜백 처리 시작" in msg for msg in log_messages)
        assert any("OAuth 콜백 처리 완료" in msg for msg in log_messages)
