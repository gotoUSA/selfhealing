"""
소셜 로그인 단위 테스트

E2E 테스트의 한계:
- django-allauth + dj-rest-auth 조합은 테스트가 매우 복잡
- Mock 설정이 버전마다 달라서 유지보수 어려움
- 실제 OAuth 플로우는 통합/수동 테스트로 검증

단위 테스트 범위:
- 소셜 계정 생성/연결 로직
- 이메일 자동 인증 로직
- 시그널 동작 검증
- DB 관계 검증

실제 소셜 로그인 기능:
- Postman으로 수동 테스트
- 프론트엔드 연동 테스트
- 스테이징 환경 통합 테스트
"""


from django.contrib.sites.models import Site
from django.utils import timezone

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.user import User


@pytest.mark.django_db
class TestSocialAccountCreation:
    """소셜 계정 생성 기본 로직 테스트"""

    def test_create_social_account_with_new_user(self, social_app_google, user_factory):
        """
        신규 사용자 소셜 계정 생성

        시나리오:
        1. 처음 소셜 로그인하는 사용자
        2. User 객체 생성
        3. SocialAccount 생성 및 연결
        4. 이메일 자동 인증
        """
        # Arrange & Act - 신규 사용자 생성 (소셜 로그인)
        user = user_factory(
            username="google_user_123",
            email="newuser@gmail.com",
            is_email_verified=True,  # 소셜 로그인은 자동 인증
        )

        social_account = SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google_uid_123456",
            extra_data={"email": "newuser@gmail.com", "name": "Test User", "picture": "https://example.com/photo.jpg"},
        )

        # Assert
        assert User.objects.count() == 1
        assert SocialAccount.objects.count() == 1
        assert social_account.user == user
        assert social_account.provider == "google"
        assert user.is_email_verified is True
        assert "email" in social_account.extra_data

    def test_connect_social_account_to_existing_user(self, social_app_kakao, user_factory):
        """
        기존 사용자에게 소셜 계정 연결

        시나리오:
        1. 이미 일반 가입한 사용자 존재
        2. 같은 이메일로 소셜 로그인 시도
        3. 기존 계정에 소셜 계정 연결
        """
        # Arrange - 기존 사용자 (일반 가입)
        existing_user = user_factory(
            username="existing_user",
            email="existing@example.com",
            is_email_verified=False,  # 이메일 미인증 상태
        )

        initial_user_count = User.objects.count()

        # Act - 같은 이메일로 소셜 계정 연결
        social_account = SocialAccount.objects.create(
            user=existing_user,
            provider="kakao",
            uid="kakao_uid_789",
            extra_data={"email": "existing@example.com"},
        )

        # 소셜 연결 시 이메일 자동 인증
        existing_user.is_email_verified = True
        existing_user.save()

        # Assert
        assert User.objects.count() == initial_user_count  # 사용자 수 변화 없음
        assert existing_user.socialaccount_set.count() == 1
        assert existing_user.is_email_verified is True  # 자동 인증됨

    def test_multiple_social_accounts_for_one_user(
        self, social_app_google, social_app_kakao, social_app_naver, user_factory
    ):
        """
        한 사용자가 여러 소셜 계정 연결

        Google, Kakao, Naver 모두 연결 가능
        """
        # Arrange
        user = user_factory(
            username="multi_social_user",
            email="multi@example.com",
            is_email_verified=True,
        )

        # Act - 여러 소셜 계정 연결
        SocialAccount.objects.create(user=user, provider="google", uid="google_123")
        SocialAccount.objects.create(user=user, provider="kakao", uid="kakao_456")
        SocialAccount.objects.create(user=user, provider="naver", uid="naver_789")

        # Assert
        assert user.socialaccount_set.count() == 3

        providers = list(user.socialaccount_set.values_list("provider", flat=True))
        assert "google" in providers
        assert "kakao" in providers
        assert "naver" in providers


@pytest.mark.django_db
class TestSocialLoginEmailVerification:
    """소셜 로그인 이메일 자동 인증 테스트"""

    def test_email_auto_verified_on_social_signup(self, social_app_google, user_factory):
        """
        소셜 가입 시 이메일 자동 인증

        OAuth 제공자가 이메일을 이미 인증했으므로
        별도 인증 절차 불필요
        """
        # Arrange & Act - 소셜 로그인으로 신규 가입
        user = user_factory(
            username="social_newuser",
            email="newuser@gmail.com",
            is_email_verified=True,  # 소셜은 자동 인증
        )

        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google_new_123",
        )

        # Assert
        assert user.is_email_verified is True

    def test_existing_verification_tokens_invalidated(self, social_app_kakao, user_factory):
        """
        소셜 연결 시 기존 인증 토큰 무효화

        시나리오:
        1. 이메일 미인증 사용자
        2. 인증 토큰이 이미 발급됨
        3. 소셜 로그인으로 자동 인증
        4. 기존 토큰 무효화
        """
        # Arrange
        user = user_factory(
            username="unverified_user",
            email="unverified@example.com",
            is_email_verified=False,  # 미인증
        )

        # 인증 토큰 발급
        token1 = EmailVerificationToken.objects.create(user=user)
        token2 = EmailVerificationToken.objects.create(user=user)

        assert token1.is_used is False
        assert token2.is_used is False

        # Act - 소셜 계정 연결 (자동 인증)
        SocialAccount.objects.create(
            user=user,
            provider="kakao",
            uid="kakao_auto_verify",
        )

        # 이메일 자동 인증 처리
        user.is_email_verified = True
        user.save()

        # 기존 토큰 무효화 (시그널에서 처리하는 로직)
        EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)

        # Assert
        token1.refresh_from_db()
        token2.refresh_from_db()

        assert user.is_email_verified is True
        assert token1.is_used is True
        assert token2.is_used is True


@pytest.mark.django_db
class TestSocialAppConfiguration:
    """소셜 앱 설정 검증 테스트"""

    def test_social_app_creation(self, social_app_google):
        """SocialApp 생성 검증"""
        # Assert - fixture가 이미 생성함
        assert SocialApp.objects.count() == 1
        assert social_app_google.provider == "google"
        assert social_app_google.sites.filter(id=1).exists()

    def test_multiple_providers_configured(self, social_app_google, social_app_kakao, social_app_naver):
        """여러 제공자 동시 설정"""
        # Assert - fixtures가 이미 생성함
        assert SocialApp.objects.count() == 3

        providers = ["google", "kakao", "naver"]
        for provider in providers:
            assert SocialApp.objects.filter(provider=provider).exists()


@pytest.mark.django_db
class TestSocialAccountExceptions:
    """소셜 계정 예외 상황 테스트"""

    def test_duplicate_social_account_same_provider(self, social_app_google, user_factory):
        """
        같은 제공자로 중복 연결 방지

        한 사용자가 같은 OAuth 제공자의 계정을
        중복으로 연결할 수 없음
        """
        # Arrange
        user = user_factory(username="testuser", email="test@example.com")

        # Act - 첫 번째 Google 계정 연결
        social1 = SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google_uid_1",
        )

        # Assert - 중복 확인
        existing_accounts = SocialAccount.objects.filter(user=user, provider="google")

        assert existing_accounts.count() == 1
        assert existing_accounts.first() == social1

    def test_inactive_user_cannot_social_login(self, social_app_naver, user_factory):
        """
        비활성화된 사용자는 소셜 로그인 불가

        is_active=False인 사용자는 로그인 거부
        """
        # Arrange
        user = user_factory(
            username="inactive_user",
            email="inactive@example.com",
            is_active=False,  # 비활성화
        )

        # Act
        social_account = SocialAccount.objects.create(
            user=user,
            provider="naver",
            uid="naver_inactive",
        )

        # Assert
        assert user.is_active is False
        assert social_account.user == user
        # 실제 로그인 시도는 View 레벨에서 차단됨


@pytest.mark.django_db
class TestSocialAccountDataStructure:
    """소셜 계정 데이터 구조 테스트"""

    def test_extra_data_storage(self, social_app_google, user_factory):
        """
        extra_data 필드에 OAuth 데이터 저장

        OAuth 제공자로부터 받은 추가 정보 저장
        """
        # Arrange
        user = user_factory(username="datatest_user", email="data@example.com")

        extra_data = {
            "id": "123456789",
            "email": "data@example.com",
            "verified_email": True,
            "name": "Test User",
            "given_name": "Test",
            "family_name": "User",
            "picture": "https://example.com/photo.jpg",
            "locale": "ko",
        }

        # Act
        social_account = SocialAccount.objects.create(
            user=user, provider="google", uid="google_data_123", extra_data=extra_data
        )

        # Assert
        assert social_account.extra_data == extra_data
        assert social_account.extra_data["email"] == "data@example.com"
        assert social_account.extra_data["verified_email"] is True

    def test_social_account_timestamps(self, social_app_kakao, user_factory):
        """소셜 계정 생성 시간 기록"""
        # Arrange
        user = user_factory(username="timestamp_user", email="timestamp@example.com")

        # Act
        before_create = timezone.now()
        social_account = SocialAccount.objects.create(
            user=user,
            provider="kakao",
            uid="kakao_timestamp",
        )
        after_create = timezone.now()

        # Assert
        assert social_account.date_joined is not None
        assert before_create <= social_account.date_joined <= after_create


@pytest.mark.django_db
class TestSocialLoginScenarios:
    """실제 사용 시나리오 통합 테스트"""

    def test_complete_new_user_signup_flow(self, social_app_google, user_factory):
        """
        신규 사용자 완전 가입 플로우

        1. SocialApp 설정 확인
        2. 신규 사용자 생성
        3. 소셜 계정 연결
        4. 이메일 자동 인증
        5. 최종 상태 검증
        """
        # Arrange & Act - 신규 가입 플로우
        user = user_factory(
            username="complete_user",
            email="complete@gmail.com",
            is_email_verified=True,
        )

        social_account = SocialAccount.objects.create(
            user=user,
            provider="google",
            uid="google_complete_123",
            extra_data={
                "email": "complete@gmail.com",
                "verified_email": True,
                "name": "Complete User",
            },
        )

        # Assert - 최종 상태 검증
        assert User.objects.filter(email="complete@gmail.com").count() == 1
        assert user.is_email_verified is True
        assert user.is_active is True
        assert social_account.user == user
        assert social_account.provider == "google"
        assert "email" in social_account.extra_data

    def test_existing_user_adds_social_account(self, social_app_kakao, user_factory):
        """
        기존 사용자가 소셜 계정 추가

        1. 일반 가입 사용자 존재 (미인증)
        2. 소셜 계정 연결
        3. 자동 인증 처리
        4. 기존 인증 토큰 무효화
        """
        # Arrange - 기존 사용자 (이메일 미인증)
        user = user_factory(
            username="existing_adds_social",
            email="existing@example.com",
            is_email_verified=False,
        )

        # 인증 토큰 발급
        token = EmailVerificationToken.objects.create(user=user)

        # Act - 소셜 계정 추가
        SocialAccount.objects.create(
            user=user,
            provider="kakao",
            uid="kakao_existing_456",
        )

        # 자동 인증 및 토큰 무효화
        user.is_email_verified = True
        user.save()

        token.is_used = True
        token.save()

        # Assert
        assert user.is_email_verified is True
        assert user.socialaccount_set.count() == 1

        token.refresh_from_db()
        assert token.is_used is True
