"""Kakao OAuth Edge Cases 테스트"""

from unittest.mock import Mock, patch

import pytest

from shopping.adapters import CustomSocialAccountAdapter


def _create_mock_user(email=""):
    """테스트용 Mock User 생성"""
    mock_user = Mock()
    mock_user.is_email_verified = False
    mock_user.email = email
    return mock_user


def _create_mock_sociallogin(provider="kakao", extra_data=None):
    """테스트용 Mock SocialLogin 생성"""
    mock_sociallogin = Mock()
    mock_sociallogin.account = Mock()
    mock_sociallogin.account.provider = provider
    mock_sociallogin.account.extra_data = extra_data or {}
    return mock_sociallogin


class TestKakaoPopulateUser:
    """Kakao populate_user 처리"""

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_no_email_consent_sets_verified_true(self, mock_parent_populate):
        """이메일 미동의 사용자도 is_email_verified=True"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()

        # Act
        result = adapter.populate_user(Mock(), Mock(), {"name": "카카오유저"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_has_email_false_sets_verified_true(self, mock_parent_populate):
        """has_email=False여도 is_email_verified=True"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": 123456789,
                "kakao_account": {"has_email": False, "profile": {"nickname": "닉네임유저"}},
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"name": "닉네임유저"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_unverified_kakao_email_sets_verified_true(self, mock_parent_populate):
        """카카오 미인증 이메일이어도 is_email_verified=True"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user("unverified@kakao.com")
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": 123456789,
                "kakao_account": {
                    "has_email": True,
                    "is_email_valid": False,
                    "is_email_verified": False,
                    "email": "unverified@kakao.com",
                },
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "unverified@kakao.com"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_email_needs_agreement_sets_verified_true(self, mock_parent_populate):
        """동의 필요한 경우에도 is_email_verified=True"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": 123456789,
                "kakao_account": {"has_email": True, "email_needs_agreement": True},
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"name": "동의필요유저"})

        # Assert
        assert result.is_email_verified is True


class TestKakaoMissingPayload:
    """Kakao payload 필드 누락 처리"""

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_missing_kakao_account_handles_gracefully(self, mock_parent_populate):
        """kakao_account 필드 없어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(extra_data={"id": 123456789})

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_missing_profile_handles_gracefully(self, mock_parent_populate):
        """profile 필드 없어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user("noname@kakao.com")
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": 123456789,
                "kakao_account": {"has_email": True, "email": "noname@kakao.com"},
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "noname@kakao.com"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_nickname_none_handles_gracefully(self, mock_parent_populate):
        """nickname=None이어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user("test@kakao.com")
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": 123456789,
                "kakao_account": {"email": "test@kakao.com", "profile": {"nickname": None}},
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "test@kakao.com", "name": None})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_string_id_handles_gracefully(self, mock_parent_populate):
        """id가 문자열이어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(
            extra_data={
                "id": "123456789",
                "kakao_account": {"email": "stringid@kakao.com"},
            }
        )
        sociallogin.account.uid = "123456789"

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "stringid@kakao.com"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_empty_extra_data_handles_gracefully(self, mock_parent_populate):
        """extra_data 빈 객체여도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_mock_sociallogin(extra_data={})

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result.is_email_verified is True


@pytest.mark.django_db
class TestKakaoSaveUser:
    """Kakao 사용자 저장 처리"""

    def test_no_email_user_saves_successfully(self):
        """이메일 없는 사용자도 저장 성공"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.kakao()
        adapter = CustomSocialAccountAdapter()
        user = User(username="kakao_no_email_user", email="")
        user.set_password("unused_password")
        social_account = SocialAccount(
            provider="kakao",
            uid="save_no_email_kakao",
            extra_data={"id": 111222333, "kakao_account": {"has_email": False}},
        )
        sociallogin = SocialLogin(user=user, account=social_account)
        mock_request = Mock()
        mock_request.session = {}

        # Act
        saved_user = adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        assert saved_user.pk is not None
        assert saved_user.is_email_verified is True
        assert saved_user.email == ""


@pytest.mark.django_db
class TestKakaoPreSocialLogin:
    """Kakao pre_social_login 처리"""

    def test_no_email_skips_account_connect(self):
        """이메일 없으면 기존 계정 연결 시도 안 함"""
        # Arrange
        adapter = CustomSocialAccountAdapter()
        mock_sociallogin = Mock()
        mock_sociallogin.is_existing = False
        mock_sociallogin.account = Mock()
        mock_sociallogin.account.provider = "kakao"
        mock_sociallogin.account.extra_data = {
            "id": 123456789,
            "kakao_account": {"has_email": False},
        }

        # Act
        adapter.pre_social_login(Mock(), mock_sociallogin)

        # Assert
        if hasattr(mock_sociallogin, "connect"):
            mock_sociallogin.connect.assert_not_called()

    def test_nested_email_processes_without_error(self):
        """kakao_account 내부 email도 정상 처리"""
        # Arrange
        adapter = CustomSocialAccountAdapter()
        mock_sociallogin = Mock()
        mock_sociallogin.is_existing = False
        mock_sociallogin.account = Mock()
        mock_sociallogin.account.provider = "kakao"
        mock_sociallogin.account.extra_data = {
            "id": 123456789,
            "kakao_account": {"has_email": True, "email": "nested@kakao.com"},
        }

        # Act & Assert
        adapter.pre_social_login(Mock(), mock_sociallogin)
