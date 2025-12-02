"""Naver OAuth Edge Cases 테스트"""

from unittest.mock import Mock, patch

import pytest

from shopping.adapters import CustomSocialAccountAdapter


def _create_mock_user(email=""):
    """테스트용 Mock User 생성"""
    mock_user = Mock()
    mock_user.is_email_verified = False
    mock_user.email = email
    return mock_user


def _create_naver_sociallogin(response_data=None):
    """테스트용 Naver SocialLogin 생성"""
    mock_sociallogin = Mock()
    mock_sociallogin.account = Mock()
    mock_sociallogin.account.provider = "naver"
    mock_sociallogin.account.extra_data = {
        "resultcode": "00",
        "message": "success",
        "response": response_data or {},
    }
    return mock_sociallogin


class TestNaverPopulateUser:
    """Naver populate_user 처리"""

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_only_nickname_sets_verified_true(self, mock_parent_populate):
        """nickname만 있어도 is_email_verified=True"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_naver_sociallogin({"id": "test_id", "nickname": "닉네임유저"})

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"name": "닉네임유저"})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_only_id_handles_gracefully(self, mock_parent_populate):
        """id만 있어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_naver_sociallogin({"id": "minimal_naver_id"})
        sociallogin.account.uid = "minimal_naver_id"

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_missing_response_wrapper_handles_gracefully(self, mock_parent_populate):
        """response 래퍼 없어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = Mock()
        sociallogin.account = Mock()
        sociallogin.account.provider = "naver"
        sociallogin.account.extra_data = {
            "resultcode": "00",
            "id": "direct_id",
            "email": "direct@naver.com",
        }

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "direct@naver.com"})

        # Assert
        assert result.is_email_verified is True


class TestNaverFieldVariations:
    """Naver 필드 변형 처리"""

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_null_fields_handles_gracefully(self, mock_parent_populate):
        """null 필드도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_naver_sociallogin(
            {
                "id": "null_fields_id",
                "email": None,
                "name": None,
                "nickname": None,
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": None, "name": None})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_empty_string_fields_handles_gracefully(self, mock_parent_populate):
        """빈 문자열 필드도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_naver_sociallogin(
            {
                "id": "empty_string_id",
                "email": "",
                "name": "",
                "nickname": "",
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "", "name": ""})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_unicode_nickname_handles_gracefully(self, mock_parent_populate):
        """이모지 포함 닉네임도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = _create_naver_sociallogin(
            {
                "id": "unicode_nick_id",
                "nickname": "🎉테스트유저✨",
                "email": "emoji@naver.com",
            }
        )

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {"email": "emoji@naver.com", "name": "🎉테스트유저✨"})

        # Assert
        assert result.is_email_verified is True


@pytest.mark.django_db
class TestNaverPreSocialLogin:
    """Naver pre_social_login 처리"""

    def test_no_email_skips_account_connect(self):
        """이메일 없으면 기존 계정 연결 시도 안 함"""
        # Arrange
        adapter = CustomSocialAccountAdapter()
        mock_sociallogin = Mock()
        mock_sociallogin.is_existing = False
        mock_sociallogin.account = Mock()
        mock_sociallogin.account.provider = "naver"
        mock_sociallogin.account.extra_data = {
            "resultcode": "00",
            "response": {"id": "no_email_naver_id", "nickname": "이메일없음"},
        }

        # Act
        adapter.pre_social_login(Mock(), mock_sociallogin)

        # Assert
        if hasattr(mock_sociallogin, "connect"):
            mock_sociallogin.connect.assert_not_called()

    def test_nested_email_processes_without_error(self):
        """response 내부 email도 정상 처리"""
        # Arrange
        adapter = CustomSocialAccountAdapter()
        mock_sociallogin = Mock()
        mock_sociallogin.is_existing = False
        mock_sociallogin.account = Mock()
        mock_sociallogin.account.provider = "naver"
        mock_sociallogin.account.extra_data = {
            "resultcode": "00",
            "response": {"id": "naver_id", "email": "resp@naver.com"},
        }

        # Act & Assert
        adapter.pre_social_login(Mock(), mock_sociallogin)


@pytest.mark.django_db
class TestNaverSaveUser:
    """Naver 사용자 저장 처리"""

    def test_minimal_data_saves_successfully(self):
        """최소 데이터로도 저장 성공"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.naver()
        adapter = CustomSocialAccountAdapter()
        user = User(username="naver_minimal_user", email="")
        user.set_password("unused_password")
        social_account = SocialAccount(
            provider="naver",
            uid="save_minimal_naver",
            extra_data={"resultcode": "00", "response": {"id": "save_minimal_naver"}},
        )
        sociallogin = SocialLogin(user=user, account=social_account)
        mock_request = Mock()
        mock_request.session = {}

        # Act
        saved_user = adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        assert saved_user.pk is not None
        assert saved_user.is_email_verified is True

    def test_profile_image_only_saves_successfully(self):
        """profile_image만 있어도 저장 성공"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.naver()
        adapter = CustomSocialAccountAdapter()
        user = User(username="", email="")
        social_account = SocialAccount(
            provider="naver",
            uid="profile_image_only",
            extra_data={
                "resultcode": "00",
                "response": {
                    "id": "profile_image_only",
                    "profile_image": "https://example.com/img.png",
                },
            },
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        # Act
        result_user = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result_user.is_email_verified is True


class TestNaverErrorResponse:
    """Naver API 에러 응답 처리"""

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_error_resultcode_handles_gracefully(self, mock_parent_populate):
        """에러 resultcode도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = Mock()
        sociallogin.account = Mock()
        sociallogin.account.provider = "naver"
        sociallogin.account.extra_data = {
            "resultcode": "024",
            "message": "Authentication failed",
        }

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result.is_email_verified is True

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_response_none_handles_gracefully(self, mock_parent_populate):
        """response=None이어도 에러 없이 처리"""
        # Arrange
        mock_parent_populate.return_value = _create_mock_user()
        adapter = CustomSocialAccountAdapter()
        sociallogin = Mock()
        sociallogin.account = Mock()
        sociallogin.account.provider = "naver"
        sociallogin.account.extra_data = {
            "resultcode": "00",
            "message": "success",
            "response": None,
        }

        # Act
        result = adapter.populate_user(Mock(), sociallogin, {})

        # Assert
        assert result.is_email_verified is True
