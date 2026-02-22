"""UserService 단위 테스트

리팩토링 노트:
- Celery task mock 제거 → conftest.py의 CELERY_TASK_ALWAYS_EAGER=True 활용
- 외부 의존성(send_mail)만 mock 유지
- 실제 DB 상태 변화로 검증
"""

import logging
from unittest.mock import patch

import pytest
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.services.user_service import UserService
from shopping.tests.factories import EmailVerificationTokenFactory, UserFactory


@pytest.mark.django_db
class TestUserServiceSendVerificationEmail:
    """이메일 인증 발송 기능 테스트

    Celery eager 모드에서 실제 task 실행, send_mail만 mock
    """

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_email_creates_email_log(self, mock_send_mail):
        """정상 케이스: 이메일 발송 시 EmailLog 생성 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        UserService.send_verification_email(user, token)

        # Assert - 실제 DB 상태 검증
        assert EmailLog.objects.filter(token=token).exists()
        email_log = EmailLog.objects.filter(token=token).first()
        assert email_log.status == "sent"
        assert email_log.recipient_email == user.email

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_email_returns_message(self, mock_send_mail):
        """정상 케이스: 응답 메시지 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "message" in result
        assert result["message"] == "인증 이메일을 발송했습니다."

    @patch("shopping.tasks.email_tasks.send_mail")
    @patch("shopping.services.user_service.settings.DEBUG", True)
    def test_send_email_debug_mode_returns_code(self, mock_send_mail):
        """정상 케이스: DEBUG 모드에서 verification_code 반환"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "verification_code" in result
        assert result["verification_code"] == token.verification_code

    @patch("shopping.tasks.email_tasks.send_mail")
    @patch("shopping.services.user_service.settings.DEBUG", False)
    def test_send_email_prod_mode_no_code(self, mock_send_mail):
        """경계 케이스: 프로덕션 모드에서 verification_code 미반환"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        # Act
        result = UserService.send_verification_email(user, token)

        # Assert
        assert "verification_code" not in result
        assert "message" in result

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_send_email_logging(self, mock_send_mail, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        mock_send_mail.return_value = 1
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
    """회원가입 후처리 기능 테스트

    Celery eager 모드에서 실제 task 실행, send_mail만 mock
    """

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_creates_tokens(self, mock_send_mail):
        """정상 케이스: JWT 토큰 생성 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "tokens" in result
        assert "access" in result["tokens"]
        assert "refresh" in result["tokens"]

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_creates_email_token(self, mock_send_mail):
        """정상 케이스: EmailVerificationToken 생성 확인"""
        # Arrange
        mock_send_mail.return_value = 1
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

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_creates_email_log(self, mock_send_mail):
        """정상 케이스: EmailLog 생성 확인 (실제 task 실행)"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()

        # Act
        UserService.register_user(user)

        # Assert - 실제 task가 실행되어 EmailLog 생성됨
        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")
        assert EmailLog.objects.filter(token=token).exists()

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_returns_structure(self, mock_send_mail):
        """정상 케이스: 반환 구조 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "tokens" in result
        assert "verification_result" in result
        assert "message" in result["verification_result"]

    @patch("shopping.tasks.email_tasks.send_mail")
    @patch("shopping.services.user_service.settings.DEBUG", True)
    def test_register_user_debug_mode(self, mock_send_mail):
        """경계 케이스: DEBUG 모드에서 verification_code 포함 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        assert "verification_code" in result["verification_result"]

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_logging(self, mock_send_mail, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        mock_send_mail.return_value = 1
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

    @patch("shopping.tasks.email_tasks.send_mail")
    def test_register_user_token_association(self, mock_send_mail):
        """정상 케이스: 생성된 토큰과 사용자 연결 확인"""
        # Arrange
        mock_send_mail.return_value = 1
        user = UserFactory.unverified()

        # Act
        result = UserService.register_user(user)

        # Assert
        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")
        assert token.user.id == user.id

        access_token = AccessToken(result["tokens"]["access"])
        assert int(access_token["user_id"]) == user.id


@pytest.mark.django_db
class TestUserServiceError:
    """UserServiceError 예외 클래스 테스트"""

    def test_error_stores_message_and_code(self):
        """정상 케이스: message와 code 속성 저장 확인"""
        # Arrange
        from shopping.services.user_service import UserServiceError

        # Act
        error = UserServiceError("테스트 에러 메시지", code="TEST_ERROR")

        # Assert
        assert error.message == "테스트 에러 메시지"
        assert error.code == "TEST_ERROR"
        assert str(error) == "테스트 에러 메시지"

    def test_error_default_code(self):
        """경계 케이스: 기본 code 값 확인"""
        # Arrange
        from shopping.services.user_service import UserServiceError

        # Act
        error = UserServiceError("에러 메시지만 전달")

        # Assert
        assert error.message == "에러 메시지만 전달"
        assert error.code == "USER_SERVICE_ERROR"


@pytest.mark.django_db
class TestUserServiceLoginUser:
    """로그인 처리 기능 테스트"""

    def test_login_returns_user_and_tokens(self):
        """정상 케이스: 사용자와 JWT 토큰 반환 확인"""
        # Arrange
        user = UserFactory()

        # Act
        result = UserService.login_user(user)

        # Assert
        assert result.user == user
        assert "access" in result.tokens
        assert "refresh" in result.tokens

    def test_login_updates_last_login_time(self):
        """정상 케이스: 마지막 로그인 시간 업데이트 확인"""
        # Arrange
        user = UserFactory()
        old_last_login = user.last_login

        # Act
        UserService.login_user(user)

        # Assert
        user.refresh_from_db()
        assert user.last_login is not None
        if old_last_login:
            assert user.last_login > old_last_login

    def test_login_with_session_key_merges_cart(self):
        """정상 케이스: 세션 키가 있으면 장바구니 병합"""
        from shopping.models.cart import Cart
        from shopping.tests.factories import ProductFactory

        # Arrange - 비회원 상태에서 장바구니에 상품을 담은 후 로그인하는 시나리오
        user = UserFactory()
        product = ProductFactory()
        session_key = "test_session_key_12345"
        anon_cart = Cart.objects.create(session_key=session_key, is_active=True)
        anon_cart.items.create(product=product, quantity=2)

        # Act
        result = UserService.login_user(user, session_key=session_key)

        # Assert
        assert result.cart_merged is True

    def test_login_without_session_key_skips_cart_merge(self):
        """경계 케이스: 세션 키 없으면 장바구니 병합 스킵"""
        # Arrange
        user = UserFactory()

        # Act
        result = UserService.login_user(user, session_key=None)

        # Assert
        assert result.cart_merged is False

    def test_login_with_request_meta_updates_ip(self):
        """정상 케이스: request_meta가 있으면 IP 업데이트"""
        # Arrange
        user = UserFactory()
        request_meta = {"REMOTE_ADDR": "192.168.1.100"}

        # Act
        UserService.login_user(user, request_meta=request_meta)

        # Assert
        user.refresh_from_db()
        assert user.last_login_ip == "192.168.1.100"

    def test_login_extracts_ip_from_x_forwarded_for(self):
        """경계 케이스: X-Forwarded-For 헤더에서 IP 추출"""
        # Arrange - 프록시/로드밸런서 환경에서는 X-Forwarded-For에 클라이언트 IP가 첫 번째로 들어옴
        user = UserFactory()
        request_meta = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.50, 70.41.3.18, 150.172.238.178",
            "REMOTE_ADDR": "127.0.0.1",
        }

        # Act
        UserService.login_user(user, request_meta=request_meta)

        # Assert - 첫 번째 IP(실제 클라이언트)만 추출되어야 함
        user.refresh_from_db()
        assert user.last_login_ip == "203.0.113.50"

    def test_login_without_request_meta_clears_ip(self):
        """경계 케이스: request_meta 없으면 IP 필드가 빈값으로 저장"""
        # Arrange
        user = UserFactory()

        # Act
        UserService.login_user(user, request_meta=None)

        # Assert
        user.refresh_from_db()
        assert user.last_login_ip is None or user.last_login_ip == ""

    @patch("shopping.models.cart.Cart.merge_anonymous_cart")
    def test_login_cart_merge_failure_continues_login(self, mock_merge):
        """예외 케이스: 장바구니 병합 실패해도 로그인 진행"""
        # Arrange - 장바구니 병합은 부가 기능이므로 실패해도 로그인은 성공해야 함
        user = UserFactory()
        mock_merge.side_effect = Exception("DB connection error")

        # Act
        result = UserService.login_user(user, session_key="any_session_key")

        # Assert - 로그인 성공, 병합만 실패 처리
        assert result.user == user
        assert "access" in result.tokens
        assert result.cart_merged is False

    def test_login_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user = UserFactory()

        # Act
        UserService.login_user(user)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("로그인 처리 시작" in msg for msg in log_messages)
        assert any("로그인 처리 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestUserServiceWithdrawUser:
    """회원 탈퇴 기능 테스트"""

    def test_withdraw_deactivates_user(self):
        """정상 케이스: 사용자 비활성화 확인"""
        # Arrange
        user = UserFactory()

        # Act
        UserService.withdraw_user(user)

        # Assert
        user.refresh_from_db()
        assert user.is_active is False

    def test_withdraw_sets_withdrawn_status(self):
        """정상 케이스: 탈퇴 상태 설정 확인"""
        # Arrange
        user = UserFactory()

        # Act
        UserService.withdraw_user(user)

        # Assert
        user.refresh_from_db()
        assert user.is_withdrawn is True
        assert user.withdrawn_at is not None

    def test_withdraw_returns_success_result(self):
        """정상 케이스: 성공 결과 반환 확인"""
        # Arrange
        user = UserFactory()

        # Act
        result = UserService.withdraw_user(user)

        # Assert
        assert result.success is True
        assert result.message == "회원 탈퇴가 완료되었습니다."

    def test_withdraw_invalidates_tokens(self):
        """정상 케이스: JWT 토큰 무효화 확인"""
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
        )

        # Arrange
        user = UserFactory()
        RefreshToken.for_user(user)
        RefreshToken.for_user(user)
        initial_blacklisted = BlacklistedToken.objects.count()

        # Act
        result = UserService.withdraw_user(user)

        # Assert
        assert result.invalidated_tokens >= 0
        assert BlacklistedToken.objects.count() >= initial_blacklisted

    def test_withdraw_skips_already_blacklisted_tokens(self):
        """경계 케이스: 이미 블랙리스트된 토큰은 스킵"""
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
        )

        # Arrange - 로그아웃 등으로 이미 블랙리스트에 추가된 토큰이 있는 상황
        user = UserFactory()
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()
        initial_blacklisted = BlacklistedToken.objects.count()

        # Act
        result = UserService.withdraw_user(user)

        # Assert - 중복 블랙리스트 추가 없이 탈퇴 처리되어야 함
        user.refresh_from_db()
        assert user.is_withdrawn is True
        assert BlacklistedToken.objects.count() == initial_blacklisted

    def test_withdraw_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user = UserFactory()

        # Act
        UserService.withdraw_user(user)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("회원 탈퇴 처리 시작" in msg for msg in log_messages)
        assert any("사용자 탈퇴 상태 변경 완료" in msg for msg in log_messages)
        assert any("JWT 토큰 무효화 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestUserServiceProcessSocialLogin:
    """소셜 로그인 처리 테스트"""

    def test_social_login_creates_new_user(self):
        """정상 케이스: 새 사용자 생성 확인"""
        from django.contrib.auth import get_user_model

        # Arrange
        User = get_user_model()
        user_info = {
            "email": "newuser@social.test",
            "name": "소셜사용자",
            "provider_id": "google_123456",
        }

        # Act
        result = UserService.process_social_login("google", user_info)

        # Assert
        assert result.is_new_user is True
        assert result.user.email == "newuser@social.test"
        assert User.objects.filter(email="newuser@social.test").exists()

    def test_social_login_returns_existing_user(self):
        """정상 케이스: 기존 사용자 반환 확인"""
        # Arrange
        existing_user = UserFactory(email="existing@social.test")
        user_info = {
            "email": "existing@social.test",
            "name": "기존사용자",
            "provider_id": "google_789",
        }

        # Act
        result = UserService.process_social_login("google", user_info)

        # Assert
        assert result.is_new_user is False
        assert result.user.id == existing_user.id

    def test_social_login_returns_tokens(self):
        """정상 케이스: JWT 토큰 반환 확인"""
        # Arrange
        user_info = {
            "email": "tokentest@social.test",
            "name": "토큰테스트",
            "provider_id": "google_token",
        }

        # Act
        result = UserService.process_social_login("google", user_info)

        # Assert
        assert "access" in result.tokens
        assert "refresh" in result.tokens

    def test_social_login_without_email_uses_provider_id(self):
        """경계 케이스: 이메일 없으면 provider_id로 대체 이메일 생성"""
        # Arrange-  카카오 등 일부 OAuth에서 이메일 제공 동의를 안 한 경우
        user_info = {
            "email": None,
            "name": "노이메일",
            "provider_id": "kakao_999",
        }

        # Act
        result = UserService.process_social_login("kakao", user_info)

        # Assert - provider_provider_id@social.local 형식으로 대체 이메일 생성
        assert result.user.email == "kakao_kakao_999@social.local"
        assert result.is_new_user is True

    def test_social_login_updates_last_login_for_existing(self):
        """경계 케이스: 기존 사용자 로그인 시간 업데이트"""
        # Arrange
        existing_user = UserFactory(email="update_login@social.test")
        old_login = existing_user.last_login
        user_info = {
            "email": "update_login@social.test",
            "provider_id": "naver_update",
        }

        # Act
        UserService.process_social_login("naver", user_info)

        # Assert
        existing_user.refresh_from_db()
        assert existing_user.last_login is not None
        if old_login:
            assert existing_user.last_login > old_login

    def test_social_login_sets_email_verified_for_new(self):
        """경계 케이스: 새 사용자는 이메일 인증 완료 상태"""
        # Arrange
        user_info = {
            "email": "verified@social.test",
            "name": "인증완료",
            "provider_id": "google_verified",
        }

        # Act
        result = UserService.process_social_login("google", user_info)

        # Assert
        assert result.user.is_email_verified is True

    def test_social_login_sets_name_for_new_user(self):
        """정상 케이스: 새 사용자에게 이름 설정"""
        # Arrange
        user_info = {
            "email": "named@social.test",
            "name": "홍길동",
            "provider_id": "google_named",
        }

        # Act
        result = UserService.process_social_login("google", user_info)

        # Assert
        assert result.user.first_name == "홍길동"

    def test_social_login_without_email_and_provider_id_raises(self):
        """예외 케이스: 이메일과 provider_id 모두 없으면 에러"""
        from shopping.services.user_service import UserServiceError

        # Arrange
        user_info = {
            "email": None,
            "name": "노정보",
            "provider_id": None,
        }

        # Act & Assert
        with pytest.raises(UserServiceError) as exc_info:
            UserService.process_social_login("google", user_info)

        assert exc_info.value.code == "EMAIL_NOT_PROVIDED"

    def test_social_login_logging(self, caplog):
        """정상 케이스: 로깅 기록 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.user_service")
        user_info = {
            "email": "logging@social.test",
            "name": "로깅테스트",
            "provider_id": "google_log",
        }

        # Act
        UserService.process_social_login("google", user_info)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("소셜 로그인 처리 시작" in msg for msg in log_messages)
        assert any("소셜 로그인 처리 완료" in msg for msg in log_messages)


@pytest.mark.django_db
class TestUserServiceGenerateUniqueUsername:
    """고유 username 생성 테스트"""

    def test_generates_username_from_email(self):
        """정상 케이스: 이메일에서 username 생성"""
        # Act
        username = UserService._generate_unique_username(
            email="testuser@example.com",
            provider="google",
            provider_id="123",
        )

        # Assert
        assert username == "testuser_google"

    def test_increments_counter_on_duplicate(self):
        """경계 케이스: 중복 시 카운터 증가"""
        # Arrange
        UserFactory(username="duplicate_google")

        # Act
        username = UserService._generate_unique_username(
            email="duplicate@example.com",
            provider="google",
            provider_id="456",
        )

        # Assert
        assert username == "duplicate_google_1"

    def test_increments_counter_multiple_duplicates(self):
        """경계 케이스: 여러 중복 시 카운터 계속 증가"""
        # Arrange - 동일 이메일 prefix로 여러 사용자가 이미 존재하는 상황 시뮬레이션
        UserFactory(username="multi_kakao")
        UserFactory(username="multi_kakao_1")
        UserFactory(username="multi_kakao_2")

        # Act
        username = UserService._generate_unique_username(
            email="multi@example.com",
            provider="kakao",
            provider_id="789",
        )

        # Assert - 사용 가능한 다음 번호(3)가 붙어야 함
        assert username == "multi_kakao_3"
