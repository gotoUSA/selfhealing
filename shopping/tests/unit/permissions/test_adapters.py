"""
CustomSocialAccountAdapter 테스트

소셜 로그인 어댑터의 동작을 검증합니다.
- Unit Test: Mock 기반 빠른 테스트
- Integration Test: 실제 allauth 객체 사용

성능 최적화:
- Unit 테스트는 DB 접근 없이 순수 로직만 검증
- Integration 테스트만 @pytest.mark.django_db 사용
- Factory 패턴으로 테스트 데이터 재사용

보안 테스트:
- is_email_verified 자동 설정 검증
- 이메일 미제공/중복 시나리오 처리
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import pytest

from shopping.adapters import CustomSocialAccountAdapter

if TYPE_CHECKING:
    from django.http import HttpRequest


# ==========================================
# Unit Tests (Mock 기반 - DB 접근 없음)
# ==========================================


class TestCustomSocialAccountAdapterUnit:
    """
    CustomSocialAccountAdapter Unit Tests

    Mock을 사용하여 DB 없이 순수 로직만 테스트합니다.
    빠른 실행 속도를 위해 django_db 마커를 사용하지 않습니다.
    """

    def setup_method(self):
        """각 테스트 전 어댑터 인스턴스 생성"""
        self.adapter = CustomSocialAccountAdapter()

    # -----------------------------------------
    # is_auto_signup_allowed 테스트
    # -----------------------------------------

    def test_is_auto_signup_allowed_returns_true(self):
        """is_auto_signup_allowed는 항상 True를 반환해야 함"""
        # Arrange
        mock_request = Mock(spec=["user", "session"])
        mock_sociallogin = Mock()

        # Act
        result = self.adapter.is_auto_signup_allowed(mock_request, mock_sociallogin)

        # Assert
        assert result is True

    def test_is_auto_signup_allowed_with_none_request(self):
        """request가 None이어도 True 반환"""
        # Arrange
        mock_sociallogin = Mock()

        # Act
        result = self.adapter.is_auto_signup_allowed(None, mock_sociallogin)

        # Assert
        assert result is True

    def test_is_auto_signup_allowed_with_none_sociallogin(self):
        """sociallogin이 None이어도 True 반환"""
        # Arrange
        mock_request = Mock()

        # Act
        result = self.adapter.is_auto_signup_allowed(mock_request, None)

        # Assert
        assert result is True

    @pytest.mark.parametrize(
        "request_type,sociallogin_type",
        [
            (Mock(), Mock()),
            (None, Mock()),
            (Mock(), None),
            (None, None),
        ],
        ids=[
            "both_valid",
            "request_none",
            "sociallogin_none",
            "both_none",
        ],
    )
    def test_is_auto_signup_allowed_various_inputs(self, request_type, sociallogin_type):
        """다양한 입력 조합에서도 항상 True 반환"""
        # Act
        result = self.adapter.is_auto_signup_allowed(request_type, sociallogin_type)

        # Assert
        assert result is True

    # -----------------------------------------
    # populate_user 테스트
    # -----------------------------------------

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_populate_user_sets_email_verified(self, mock_parent_populate):
        """populate_user는 is_email_verified를 True로 설정해야 함"""
        # Arrange
        mock_request = Mock()
        mock_sociallogin = Mock()
        mock_data = {"email": "test@example.com", "name": "Test User"}

        # 부모 클래스의 populate_user가 반환할 mock user
        mock_user = Mock()
        mock_user.is_email_verified = False
        mock_parent_populate.return_value = mock_user

        # Act
        result = self.adapter.populate_user(mock_request, mock_sociallogin, mock_data)

        # Assert
        assert result.is_email_verified is True
        mock_parent_populate.assert_called_once_with(mock_request, mock_sociallogin, mock_data)

    @patch("shopping.adapters.DefaultSocialAccountAdapter.populate_user")
    def test_populate_user_preserves_other_attributes(self, mock_parent_populate):
        """populate_user는 다른 속성을 보존해야 함"""
        # Arrange
        mock_request = Mock()
        mock_sociallogin = Mock()
        mock_data = {"email": "test@example.com"}

        mock_user = Mock()
        mock_user.email = "test@example.com"
        mock_user.username = "testuser"
        mock_user.is_email_verified = False
        mock_parent_populate.return_value = mock_user

        # Act
        result = self.adapter.populate_user(mock_request, mock_sociallogin, mock_data)

        # Assert
        assert result.email == "test@example.com"
        assert result.username == "testuser"
        assert result.is_email_verified is True

    # -----------------------------------------
    # save_user 테스트
    # -----------------------------------------

    @patch("shopping.adapters.DefaultSocialAccountAdapter.save_user")
    def test_save_user_ensures_email_verified_when_false(self, mock_parent_save):
        """save_user는 is_email_verified가 False면 True로 변경하고 저장해야 함"""
        # Arrange
        mock_request = Mock()
        mock_sociallogin = Mock()

        mock_user = Mock()
        mock_user.is_email_verified = False
        mock_user.save = Mock()
        mock_parent_save.return_value = mock_user

        # Act
        result = self.adapter.save_user(mock_request, mock_sociallogin)

        # Assert
        assert result.is_email_verified is True
        mock_user.save.assert_called_once_with(update_fields=["is_email_verified"])

    @patch("shopping.adapters.DefaultSocialAccountAdapter.save_user")
    def test_save_user_skips_save_when_already_verified(self, mock_parent_save):
        """save_user는 이미 인증된 경우 추가 저장하지 않아야 함"""
        # Arrange
        mock_request = Mock()
        mock_sociallogin = Mock()

        mock_user = Mock()
        mock_user.is_email_verified = True
        mock_user.save = Mock()
        mock_parent_save.return_value = mock_user

        # Act
        result = self.adapter.save_user(mock_request, mock_sociallogin)

        # Assert
        assert result.is_email_verified is True
        mock_user.save.assert_not_called()

    @patch("shopping.adapters.DefaultSocialAccountAdapter.save_user")
    def test_save_user_with_form_parameter(self, mock_parent_save):
        """save_user는 form 파라미터를 부모에게 전달해야 함"""
        # Arrange
        mock_request = Mock()
        mock_sociallogin = Mock()
        mock_form = Mock()

        mock_user = Mock()
        mock_user.is_email_verified = True
        mock_parent_save.return_value = mock_user

        # Act
        self.adapter.save_user(mock_request, mock_sociallogin, mock_form)

        # Assert
        mock_parent_save.assert_called_once_with(mock_request, mock_sociallogin, mock_form)


# ==========================================
# Integration Tests (실제 allauth 객체 사용)
# ==========================================


@pytest.mark.django_db
class TestCustomSocialAccountAdapterIntegration:
    """
    CustomSocialAccountAdapter Integration Tests

    실제 django-allauth 객체를 사용하여 통합 동작을 검증합니다.
    DB 접근이 필요하므로 django_db 마커를 사용합니다.
    """

    def setup_method(self):
        """각 테스트 전 어댑터 인스턴스 생성"""
        self.adapter = CustomSocialAccountAdapter()

    # -----------------------------------------
    # 실제 SocialLogin 객체 테스트
    # -----------------------------------------

    def test_populate_user_with_real_sociallogin_google(self):
        """Google 소셜 로그인으로 사용자 생성 시 이메일 인증 설정"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        social_account = SocialAccount(provider="google", uid="google_123")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {
            "email": "googleuser@gmail.com",
            "name": "Google User",
            "first_name": "Google",
            "last_name": "User",
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True
        assert result_user.email == "googleuser@gmail.com"

    def test_populate_user_with_real_sociallogin_kakao(self):
        """Kakao 소셜 로그인으로 사용자 생성 시 이메일 인증 설정"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.kakao()

        user = User(username="", email="")
        social_account = SocialAccount(provider="kakao", uid="123456789")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {
            "email": "kakaouser@kakao.com",
            "name": "카카오유저",
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True

    def test_save_user_creates_verified_user(self):
        """save_user로 생성된 사용자는 이메일 인증 완료 상태여야 함"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(
            username="newgoogleuser",
            email="newuser@gmail.com",
        )
        user.set_password("unused_password")
        social_account = SocialAccount(
            provider="google",
            uid="new_google_uid_456",
            extra_data={"email": "newuser@gmail.com"},
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        mock_request.session = {}

        # Act
        saved_user = self.adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        assert saved_user.pk is not None
        assert saved_user.is_email_verified is True
        assert saved_user.username == "newgoogleuser"

    # -----------------------------------------
    # 보안 테스트 케이스
    # -----------------------------------------

    def test_populate_user_without_email(self):
        """이메일 없이 소셜 로그인해도 is_email_verified는 True로 설정"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.kakao()

        user = User(username="", email="")
        social_account = SocialAccount(provider="kakao", uid="no_email_user")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {"name": "NoEmailUser"}  # 이메일 없음

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        # OAuth 제공자가 인증한 것으로 간주
        assert result_user.is_email_verified is True

    def test_multiple_social_providers_same_email(self):
        """같은 이메일로 다른 OAuth 제공자 사용 시 처리"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()
        SocialAppFactory.kakao()

        shared_email = "shared@example.com"

        # Google 로그인
        google_user = User(username="", email="")
        google_account = SocialAccount(provider="google", uid="google_shared")
        google_login = SocialLogin(user=google_user, account=google_account)

        # Kakao 로그인
        kakao_user = User(username="", email="")
        kakao_account = SocialAccount(provider="kakao", uid="kakao_shared")
        kakao_login = SocialLogin(user=kakao_user, account=kakao_account)

        mock_request = Mock()
        data = {"email": shared_email}

        # Act
        google_result = self.adapter.populate_user(mock_request, google_login, data)
        kakao_result = self.adapter.populate_user(mock_request, kakao_login, data)

        # Assert
        assert google_result.is_email_verified is True
        assert kakao_result.is_email_verified is True
        assert google_result.email == shared_email
        assert kakao_result.email == shared_email

    def test_is_auto_signup_allowed_with_existing_sociallogin(self):
        """기존 SocialLogin 객체로 is_auto_signup_allowed 테스트"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory, UserFactory

        # Arrange
        SocialAppFactory.google()
        existing_user = UserFactory()

        social_account = SocialAccount(
            user=existing_user,
            provider="google",
            uid="existing_google_uid",
        )
        sociallogin = SocialLogin(user=existing_user, account=social_account)

        mock_request = Mock()

        # Act
        result = self.adapter.is_auto_signup_allowed(mock_request, sociallogin)

        # Assert
        assert result is True


# ==========================================
# Edge Case & Security Tests
# ==========================================


@pytest.mark.django_db
class TestCustomSocialAccountAdapterSecurity:
    """
    보안 관련 엣지 케이스 테스트

    is_email_verified 설정이 올바르게 동작하는지 검증합니다.
    """

    def setup_method(self):
        """각 테스트 전 어댑터 인스턴스 생성"""
        self.adapter = CustomSocialAccountAdapter()

    def test_email_verified_cannot_be_bypassed(self):
        """
        is_email_verified는 어댑터를 통해서만 설정 가능

        직접 User 생성 시 기본값은 False임을 확인
        """
        from shopping.models.user import User

        # Arrange & Act
        direct_user = User(username="directuser", email="direct@test.com")

        # Assert
        assert direct_user.is_email_verified is False

    def test_adapter_always_sets_verified_true(self):
        """어댑터는 어떤 상황에서도 is_email_verified=True 설정"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.naver()

        # 명시적으로 False로 설정한 User
        user = User(username="", email="", is_email_verified=False)
        social_account = SocialAccount(provider="naver", uid="naver_test")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {"email": "naver@test.com"}

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True

    def test_save_user_handles_concurrent_modification(self):
        """
        save_user 호출 시 is_email_verified 상태 변경 처리

        populate_user와 save_user 사이에 상태가 변경되어도
        최종적으로 True가 보장되어야 함
        """
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(
            username="concurrentuser",
            email="concurrent@test.com",
            is_email_verified=False,  # populate_user에서 True로 변경됨
        )
        user.set_password("test_password")

        social_account = SocialAccount(
            provider="google",
            uid="concurrent_google_uid",
            extra_data={},
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        mock_request.session = {}

        # Act
        # populate_user 호출
        self.adapter.populate_user(mock_request, sociallogin, {"email": "concurrent@test.com"})

        # 중간에 False로 변경 (시뮬레이션)
        user.is_email_verified = False

        # save_user 호출
        saved_user = self.adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        # save_user가 재확인하여 True로 설정
        saved_user.refresh_from_db()
        assert saved_user.is_email_verified is True

    def test_empty_extra_data_handling(self):
        """extra_data가 비어있어도 정상 동작"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        social_account = SocialAccount(
            provider="google",
            uid="empty_extra_data",
            extra_data={},  # 빈 데이터
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {}  # 빈 데이터

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True


# ==========================================
# Provider-Specific Tests
# ==========================================


@pytest.mark.django_db
class TestProviderSpecificBehavior:
    """
    OAuth 제공자별 특수 동작 테스트

    Google, Kakao, Naver 각 제공자의 데이터 형식에 맞는
    어댑터 동작을 검증합니다.
    """

    def setup_method(self):
        """각 테스트 전 어댑터 인스턴스 생성"""
        self.adapter = CustomSocialAccountAdapter()

    def test_google_oauth_data_format(self):
        """Google OAuth 응답 형식 처리"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import OAuthDataBuilder, SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        google_data = OAuthDataBuilder.google()

        social_account = SocialAccount(
            provider="google",
            uid=google_data["id"],
            extra_data=google_data,
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {
            "email": google_data["email"],
            "name": google_data["name"],
            "first_name": google_data.get("given_name"),
            "last_name": google_data.get("family_name"),
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True
        assert result_user.email == google_data["email"]

    def test_kakao_oauth_data_format(self):
        """Kakao OAuth 응답 형식 처리"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import OAuthDataBuilder, SocialAppFactory

        # Arrange
        SocialAppFactory.kakao()

        user = User(username="", email="")
        kakao_data = OAuthDataBuilder.kakao()

        social_account = SocialAccount(
            provider="kakao",
            uid=str(kakao_data["id"]),
            extra_data=kakao_data,
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        kakao_account = kakao_data.get("kakao_account", {})
        data = {
            "email": kakao_account.get("email"),
            "name": kakao_account.get("profile", {}).get("nickname"),
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True

    def test_naver_oauth_data_format(self):
        """Naver OAuth 응답 형식 처리"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import OAuthDataBuilder, SocialAppFactory

        # Arrange
        SocialAppFactory.naver()

        user = User(username="", email="")
        naver_data = OAuthDataBuilder.naver()
        naver_response = naver_data.get("response", {})

        social_account = SocialAccount(
            provider="naver",
            uid=naver_response["id"],
            extra_data=naver_data,
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {
            "email": naver_response.get("email"),
            "name": naver_response.get("name"),
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True
        assert result_user.email == naver_response["email"]
