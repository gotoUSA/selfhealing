"""
CustomSocialAccountAdapter 테스트

소셜 로그인 어댑터의 동작을 검증합니다.
- pre_social_login: 기존 계정 연결 로직
- is_auto_signup_allowed: 자동 가입 허용
- populate_user: 이메일 인증 설정
- save_user: 사용자 저장 및 인증 상태 보장
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from shopping.adapters import CustomSocialAccountAdapter


# ==========================================
# pre_social_login 테스트
# ==========================================


@pytest.mark.django_db
class TestPreSocialLogin:
    """pre_social_login 메서드 테스트"""

    def setup_method(self):
        self.adapter = CustomSocialAccountAdapter()

    def _create_mock_sociallogin(self, provider: str, extra_data: dict, is_existing: bool = False):
        """테스트용 Mock SocialLogin 생성 헬퍼"""
        mock_account = Mock()
        mock_account.provider = provider
        mock_account.extra_data = extra_data

        sociallogin = Mock()
        sociallogin.is_existing = is_existing
        sociallogin.account = mock_account

        return sociallogin

    def test_existing_social_account_skips_processing(self):
        """이미 연결된 소셜 계정은 처리를 건너뛴다"""
        # Arrange
        sociallogin = self._create_mock_sociallogin(
            provider="google",
            extra_data={"email": "test@gmail.com"},
            is_existing=True,
        )
        mock_request = Mock()

        # Act
        result = self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert
        assert result is None
        sociallogin.connect.assert_not_called()

    def test_google_email_extraction_connects_verified_user(self):
        """Google extra_data에서 이메일을 추출하여 인증된 사용자에 연결한다"""
        from shopping.tests.factories import UserFactory

        # Arrange
        existing_user = UserFactory(email="existing@gmail.com", is_email_verified=True)

        sociallogin = self._create_mock_sociallogin(
            provider="google",
            extra_data={"email": "existing@gmail.com"},
        )
        mock_request = Mock()

        # Act
        self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert
        sociallogin.connect.assert_called_once_with(mock_request, existing_user)

    def test_kakao_email_extraction_from_kakao_account(self):
        """Kakao의 kakao_account.email에서 이메일을 추출한다"""
        from shopping.tests.factories import UserFactory

        # Arrange
        existing_user = UserFactory(email="kakao@test.com", is_email_verified=True)

        # Kakao는 extra_data에 직접 email이 없고 kakao_account 안에 있음
        sociallogin = self._create_mock_sociallogin(
            provider="kakao",
            extra_data={"kakao_account": {"email": "kakao@test.com"}},
        )
        mock_request = Mock()

        # Act
        self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert
        sociallogin.connect.assert_called_once_with(mock_request, existing_user)

    def test_naver_email_extraction_from_response(self):
        """Naver의 response.email에서 이메일을 추출한다"""
        from shopping.tests.factories import UserFactory

        # Arrange
        existing_user = UserFactory(email="naver@test.com", is_email_verified=True)

        # Naver는 response 안에 email이 있음
        sociallogin = self._create_mock_sociallogin(
            provider="naver",
            extra_data={"response": {"email": "naver@test.com"}},
        )
        mock_request = Mock()

        # Act
        self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert
        sociallogin.connect.assert_called_once_with(mock_request, existing_user)

    def test_no_email_skips_processing(self):
        """이메일이 없으면 처리를 건너뛴다"""
        # Arrange
        sociallogin = self._create_mock_sociallogin(
            provider="google",
            extra_data={},  # 이메일 없음
        )
        mock_request = Mock()

        # Act
        result = self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert
        assert result is None
        sociallogin.connect.assert_not_called()

    def test_user_with_social_account_connects(self):
        """이미 소셜 계정이 있는 사용자에 새 소셜 계정을 연결한다"""
        from shopping.tests.factories import SocialAccountFactory, UserFactory

        # Arrange
        existing_user = UserFactory(email="multi@test.com", is_email_verified=False)
        SocialAccountFactory.google(user=existing_user)  # 기존 소셜 계정이 있음

        # 새로운 카카오 계정으로 로그인 시도
        sociallogin = self._create_mock_sociallogin(
            provider="kakao",
            extra_data={"kakao_account": {"email": "multi@test.com"}},
        )
        mock_request = Mock()

        # Act
        self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert - 기존 소셜 계정이 있으므로 연결됨
        sociallogin.connect.assert_called_once_with(mock_request, existing_user)

    def test_unverified_user_without_social_skips_connect(self):
        """미인증 사용자(소셜 계정 없음)는 연결하지 않는다"""
        from shopping.tests.factories import UserFactory

        # Arrange
        UserFactory(email="unverified@test.com", is_email_verified=False)

        sociallogin = self._create_mock_sociallogin(
            provider="google",
            extra_data={"email": "unverified@test.com"},
        )
        mock_request = Mock()

        # Act
        self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert - 미인증 + 소셜 계정 없음 → 연결 안됨
        sociallogin.connect.assert_not_called()

    def test_nonexistent_user_allows_signup(self):
        """존재하지 않는 사용자는 신규 가입으로 진행된다"""
        # Arrange
        sociallogin = self._create_mock_sociallogin(
            provider="google",
            extra_data={"email": "newuser@gmail.com"},
        )
        mock_request = Mock()

        # Act
        result = self.adapter.pre_social_login(mock_request, sociallogin)

        # Assert - 새 사용자이므로 connect 호출 없이 신규 가입 진행
        assert result is None
        sociallogin.connect.assert_not_called()


# ==========================================
# is_auto_signup_allowed 테스트
# ==========================================


@pytest.mark.django_db
class TestIsAutoSignupAllowed:
    """is_auto_signup_allowed 메서드 테스트"""

    def setup_method(self):
        self.adapter = CustomSocialAccountAdapter()

    def test_always_returns_true(self):
        """항상 True를 반환한다"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        social_account = SocialAccount(provider="google", uid="test_uid")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()

        # Act
        result = self.adapter.is_auto_signup_allowed(mock_request, sociallogin)

        # Assert
        assert result is True


# ==========================================
# populate_user 테스트
# ==========================================


@pytest.mark.django_db
class TestPopulateUser:
    """populate_user 메서드 테스트"""

    def setup_method(self):
        self.adapter = CustomSocialAccountAdapter()

    def test_sets_email_verified_true(self):
        """is_email_verified를 True로 설정한다"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        social_account = SocialAccount(provider="google", uid="test_uid")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {"email": "test@gmail.com", "name": "Test User"}

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.is_email_verified is True

    def test_preserves_user_data_from_parent(self):
        """부모 메서드에서 설정한 데이터를 유지한다"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(username="", email="")
        social_account = SocialAccount(provider="google", uid="test_uid")
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        data = {
            "email": "populated@gmail.com",
            "first_name": "First",
            "last_name": "Last",
        }

        # Act
        result_user = self.adapter.populate_user(mock_request, sociallogin, data)

        # Assert
        assert result_user.email == "populated@gmail.com"
        assert result_user.is_email_verified is True


# ==========================================
# save_user 테스트
# ==========================================


@pytest.mark.django_db
class TestSaveUser:
    """save_user 메서드 테스트"""

    def setup_method(self):
        self.adapter = CustomSocialAccountAdapter()

    def test_saves_user_with_verified_email(self):
        """사용자를 저장하고 이메일 인증 상태를 유지한다"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(
            username="savetest",
            email="save@test.com",
            is_email_verified=True,
        )
        user.set_password("test_password")

        social_account = SocialAccount(
            provider="google",
            uid="save_user_uid",
            extra_data={},
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        mock_request.session = {}

        # Act
        saved_user = self.adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        assert saved_user.pk is not None
        assert saved_user.is_email_verified is True

    def test_forces_verified_when_unset(self):
        """is_email_verified가 False일 때 True로 강제 설정한다"""
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from shopping.models.user import User
        from shopping.tests.factories import SocialAppFactory

        # Arrange
        SocialAppFactory.google()

        user = User(
            username="forcetest",
            email="force@test.com",
            is_email_verified=False,  # 명시적으로 False
        )
        user.set_password("test_password")

        social_account = SocialAccount(
            provider="google",
            uid="force_verified_uid",
            extra_data={},
        )
        sociallogin = SocialLogin(user=user, account=social_account)

        mock_request = Mock()
        mock_request.session = {}

        # super().save_user가 is_email_verified를 False로 저장하도록 mock
        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user,
        ):
            user.save()  # 먼저 저장
            user.is_email_verified = False  # 다시 False로 설정
            user.save(update_fields=["is_email_verified"])

            # Act
            saved_user = self.adapter.save_user(mock_request, sociallogin, form=None)

        # Assert
        saved_user.refresh_from_db()
        assert saved_user.is_email_verified is True


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
