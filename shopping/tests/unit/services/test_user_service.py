"""UserService 단위 테스트"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from shopping.models.email_verification import EmailVerificationToken
from shopping.services.user_service import UserService
from shopping.tests.factories import EmailVerificationTokenFactory, UserFactory


@pytest.mark.django_db
class TestUserServiceSendVerificationEmail:
    """이메일 인증 발송 기능 테스트"""

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_send_email_task_called(self, mock_task):
        """정상 케이스: Celery 태스크 호출 확인"""
        # Arrange
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        UserService.send_verification_email(user, token)

        # Assert
        mock_task.assert_called_once_with(
            user_id=user.id, token_id=token.id, is_resend=False
        )

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_send_email_returns_message(self, mock_task):
        """정상 케이스: 응답 메시지 확인"""
        # Arrange
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "message" in result
        assert result["message"] == "인증 이메일을 발송했습니다."

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    @patch("shopping.services.user_service.settings.DEBUG", True)
    def test_send_email_debug_mode_returns_code(self, mock_task):
        """정상 케이스: DEBUG 모드에서 verification_code 반환"""
        # Arrange
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "verification_code" in result
        assert result["verification_code"] == token.verification_code

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    @patch("shopping.services.user_service.settings.DEBUG", False)
    def test_send_email_prod_mode_no_code(self, mock_task):
        """경계 케이스: 프로덕션 모드에서 verification_code 미반환"""
        # Arrange
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "verification_code" not in result
        assert "message" in result

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_send_email_logging(self, mock_task, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        UserService.send_verification_email(user, token)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("이메일 인증 발송 시작" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("이메일 인증 발송 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestUserServiceCreateTokensForUser:
    """JWT 토큰 생성 기능 테스트"""

    def test_create_tokens_success(self):
        """정상 케이스: 토큰 생성 성공"""
        # Arrange
        user = UserFactory()

        # Act
        tokens = UserService.create_tokens_for_user(user)

        # Assert
        assert tokens is not None
        assert isinstance(tokens, dict)

    def test_create_tokens_structure(self):
        """정상 케이스: access, refresh 키 존재 확인"""
        # Arrange
        user = UserFactory()

        # Act
        tokens = UserService.create_tokens_for_user(user)

        # Assert
        assert "access" in tokens
        assert "refresh" in tokens
        assert isinstance(tokens["access"], str)
        assert isinstance(tokens["refresh"], str)

    def test_create_tokens_valid(self):
        """정상 케이스: 생성된 토큰이 유효한지 확인"""
        # Arrange
        user = UserFactory()

        # Act
        tokens = UserService.create_tokens_for_user(user)

        # Assert
        access_token = AccessToken(tokens["access"])
        refresh_token = RefreshToken(tokens["refresh"])

        assert int(access_token["user_id"]) == user.id
        assert int(refresh_token["user_id"]) == user.id

    def test_create_tokens_different_users(self):
        """경계 케이스: 다른 사용자는 다른 토큰 생성"""
        # Arrange
        user1 = UserFactory()
        user2 = UserFactory()

        # Act
        tokens1 = UserService.create_tokens_for_user(user1)
        tokens2 = UserService.create_tokens_for_user(user2)

        # Assert
        assert tokens1["access"] != tokens2["access"]
        assert tokens1["refresh"] != tokens2["refresh"]

    def test_create_tokens_multiple_calls(self):
        """경계 케이스: 여러 번 호출 시 매번 새로운 토큰 생성"""
        # Arrange
        user = UserFactory()

        # Act
        tokens1 = UserService.create_tokens_for_user(user)
        tokens2 = UserService.create_tokens_for_user(user)

        # Assert
        assert tokens1["access"] != tokens2["access"]
        assert tokens1["refresh"] != tokens2["refresh"]

    def test_create_tokens_inactive_user(self):
        """경계 케이스: 비활성 사용자도 토큰 생성 가능"""
        # Arrange
        user = UserFactory.inactive()

        # Act
        tokens = UserService.create_tokens_for_user(user)

        # Assert
        assert "access" in tokens
        assert "refresh" in tokens

    def test_create_tokens_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user = UserFactory()

        # Act
        UserService.create_tokens_for_user(user)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("JWT 토큰 생성" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any(f"username={user.username}" in msg for msg in log_messages)


@pytest.mark.django_db
class TestUserServiceRegisterUser:
    """회원가입 후처리 기능 테스트"""

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_creates_tokens(self, mock_task):
        """정상 케이스: JWT 토큰 생성 확인"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "tokens" in result
        assert "access" in result["tokens"]
        assert "refresh" in result["tokens"]

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_creates_email_token(self, mock_task):
        """정상 케이스: EmailVerificationToken 생성 확인"""
        # Arrange
        user = UserFactory.unverified()
        initial_token_count = EmailVerificationToken.objects.filter(user=user).count()

        # Act
        UserService.register_user(user)

        # Assert
        final_token_count = EmailVerificationToken.objects.filter(user=user).count()
        assert final_token_count == initial_token_count + 1

        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")
        assert token.user == user
        assert token.is_used is False

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_sends_email(self, mock_task):
        """정상 케이스: 이메일 발송 확인"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        UserService.register_user(user)

        # Assert
        mock_task.assert_called_once()
        call_kwargs = mock_task.call_args[1]
        assert call_kwargs["user_id"] == user.id
        assert call_kwargs["is_resend"] is False

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_returns_structure(self, mock_task):
        """정상 케이스: 반환 구조 확인"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "tokens" in result
        assert "verification_result" in result
        assert "message" in result["verification_result"]

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    @patch("shopping.services.user_service.settings.DEBUG", True)
    def test_register_user_debug_mode(self, mock_task):
        """경계 케이스: DEBUG 모드에서 verification_code 포함 확인"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "verification_code" in result["verification_result"]

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_logging(self, mock_task, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user = UserFactory.unverified()

        # Act
        UserService.register_user(user)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("회원가입 후처리 시작" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("이메일 인증 토큰 생성" in msg for msg in log_messages)
        assert any("회원가입 후처리 완료" in msg for msg in log_messages)

    @patch("shopping.tasks.email_tasks.send_verification_email_task.delay")
    def test_register_user_token_association(self, mock_task):
        """정상 케이스: 생성된 토큰과 사용자 연결 확인"""
        # Arrange
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")
        assert token.user.id == user.id

        access_token = AccessToken(result["tokens"]["access"])
        assert int(access_token["user_id"]) == user.id
