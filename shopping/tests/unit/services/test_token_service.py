"""TokenService 단위 테스트

JWT 토큰 관련 비즈니스 로직 테스트
- Refresh Token 검증 및 갱신
- 토큰 블랙리스트 처리
"""

import logging
from unittest.mock import patch

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.services.token_service import (
    TokenRefreshResult,
    TokenService,
    TokenServiceError,
)
from shopping.tests.factories import UserFactory


class TestTokenServiceError:
    """TokenServiceError 예외 클래스 테스트"""

    def test_error_stores_message_and_code(self):
        """정상 케이스: message와 code 속성 저장 확인"""
        # Act
        error = TokenServiceError("토큰이 유효하지 않습니다", code="TOKEN_INVALID")

        # Assert
        assert error.message == "토큰이 유효하지 않습니다"
        assert error.code == "TOKEN_INVALID"
        assert str(error) == "토큰이 유효하지 않습니다"

    def test_error_default_code(self):
        """경계 케이스: 기본 code 값 확인"""
        # Act
        error = TokenServiceError("에러 메시지")

        # Assert
        assert error.code == "TOKEN_SERVICE_ERROR"


class TestTokenRefreshResult:
    """TokenRefreshResult dataclass 테스트"""

    def test_result_with_access_token_only(self):
        """정상 케이스: access_token만 설정"""
        # Act
        result = TokenRefreshResult(access_token="new_access_token")

        # Assert
        assert result.access_token == "new_access_token"
        assert result.refresh_token is None

    def test_result_with_both_tokens(self):
        """정상 케이스: 두 토큰 모두 설정 (ROTATE_REFRESH_TOKENS=True)"""
        # Act
        result = TokenRefreshResult(
            access_token="new_access",
            refresh_token="new_refresh",
        )

        # Assert
        assert result.access_token == "new_access"
        assert result.refresh_token == "new_refresh"


@pytest.mark.django_db
class TestValidateAndRefreshToken:
    """Refresh Token 검증 및 갱신 테스트"""

    def test_refresh_token_success(self):
        """정상 케이스: 토큰 갱신 성공"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # Act
        result = TokenService.validate_and_refresh_token(str(refresh))

        # Assert
        assert isinstance(result, TokenRefreshResult)
        assert result.access_token is not None
        assert len(result.access_token) > 0

    def test_refresh_token_empty_raises(self):
        """예외 케이스: 빈 토큰 전달"""
        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token("")

        assert exc_info.value.code == "TOKEN_REQUIRED"

    def test_refresh_token_none_raises(self):
        """예외 케이스: None 토큰 전달"""
        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token(None)

        assert exc_info.value.code == "TOKEN_REQUIRED"

    def test_refresh_token_invalid_format_raises(self):
        """예외 케이스: 잘못된 JWT 형식"""
        # Arrange - 유효하지 않은 JWT 문자열
        invalid_token = "not.a.valid.jwt.token"

        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token(invalid_token)

        assert exc_info.value.code == "INVALID_FORMAT"

    def test_refresh_token_blacklisted_raises(self):
        """예외 케이스: 블랙리스트된 토큰"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()  # 블랙리스트에 추가

        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token(str(refresh))

        assert exc_info.value.code == "TOKEN_BLACKLISTED"

    def test_refresh_token_expired_raises(self):
        """예외 케이스: 만료된 토큰"""
        # Arrange - 만료된 토큰 시뮬레이션 (유효하지 않은 토큰으로 대체)
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # 토큰 만료 시뮬레이션: access token을 refresh token으로 사용
        # (타입이 다르므로 검증 실패)
        access_token = str(refresh.access_token)

        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token(access_token)

        assert exc_info.value.code == "TOKEN_INVALID"

    @patch("django.conf.settings.SIMPLE_JWT", {"ROTATE_REFRESH_TOKENS": True, "BLACKLIST_AFTER_ROTATION": False})
    def test_refresh_token_rotation_enabled(self):
        """경계 케이스: ROTATE_REFRESH_TOKENS=True일 때 새 refresh token 반환"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # Act
        result = TokenService.validate_and_refresh_token(str(refresh))

        # Assert
        assert result.access_token is not None
        # 새 refresh_token이 생성됨
        assert result.refresh_token is not None

    def test_refresh_token_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.token_service")
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # Act
        TokenService.validate_and_refresh_token(str(refresh))

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("토큰 갱신 완료" in msg for msg in log_messages)

    def test_refresh_token_blacklist_warning_logged(self, caplog):
        """예외 케이스: 블랙리스트 토큰 사용 시 경고 로깅"""
        # Arrange
        caplog.set_level(logging.WARNING, logger="shopping.services.token_service")
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()

        # Act & Assert
        with pytest.raises(TokenServiceError):
            TokenService.validate_and_refresh_token(str(refresh))

        log_messages = [record.message for record in caplog.records]
        assert any("블랙리스트된 토큰" in msg for msg in log_messages)


@pytest.mark.django_db
class TestBlacklistToken:
    """토큰 블랙리스트 추가 테스트"""

    def test_blacklist_token_success(self):
        """정상 케이스: 토큰 블랙리스트 추가 성공"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # Act
        result = TokenService.blacklist_token(str(refresh))

        # Assert
        assert result is True

    def test_blacklist_token_empty_raises(self):
        """예외 케이스: 빈 토큰 전달"""
        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.blacklist_token("")

        assert exc_info.value.code == "TOKEN_REQUIRED"

    def test_blacklist_token_none_raises(self):
        """예외 케이스: None 토큰 전달"""
        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.blacklist_token(None)

        assert exc_info.value.code == "TOKEN_REQUIRED"

    def test_blacklist_token_invalid_raises(self):
        """예외 케이스: 유효하지 않은 토큰"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)  # access token은 blacklist 불가

        # Act & Assert
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.blacklist_token(access_token)

        assert exc_info.value.code == "TOKEN_INVALID"

    def test_blacklist_token_already_blacklisted_raises(self):
        """예외 케이스: 이미 블랙리스트된 토큰"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()  # 먼저 블랙리스트 추가

        # Act & Assert - 두 번째 블랙리스트 시도
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.blacklist_token(str(refresh))

        assert exc_info.value.code == "TOKEN_INVALID"

    def test_blacklist_token_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.token_service")
        user = UserFactory()
        refresh = RefreshToken.for_user(user)

        # Act
        TokenService.blacklist_token(str(refresh))

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("블랙리스트 추가 완료" in msg for msg in log_messages)

    def test_blacklist_prevents_refresh(self):
        """정상 케이스: 블랙리스트 후 갱신 불가"""
        # Arrange
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        TokenService.blacklist_token(str(refresh))

        # Act & Assert - 블랙리스트된 토큰으로 갱신 시도
        with pytest.raises(TokenServiceError) as exc_info:
            TokenService.validate_and_refresh_token(str(refresh))

        assert exc_info.value.code == "TOKEN_BLACKLISTED"
