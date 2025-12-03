"""
CustomSocialAccountAdapter 테스트

소셜 로그인 어댑터의 통합 동작을 검증합니다.
- Integration Test: 실제 allauth 객체 사용
- Security Test: is_email_verified 보안 검증

성능 최적화:
- Integration 테스트만 @pytest.mark.django_db 사용
- Factory 패턴으로 테스트 데이터 재사용

보안 테스트:
- is_email_verified 자동 설정 검증
- 이메일 미제공/중복 시나리오 처리
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from shopping.adapters import CustomSocialAccountAdapter


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
