"""
Auth 테스트 전용 Fixture

전역 conftest.py의 fixture를 사용하면서 auth 테스트 특화 fixture 정의.
"""

import base64
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.sites.models import Site
from django.utils import timezone

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.password_reset import PasswordResetToken
from shopping.models.user import User

# ==========================================
# 상수 정의
# ==========================================

SOCIAL_APP_CONFIG = {
    "google": {
        "name": "Google Test App",
        "client_id": "test_google_client_id_unified",
        "secret": "test_google_secret_unified",
    },
    "kakao": {
        "name": "Kakao Test App",
        "client_id": "test_kakao_client_id_unified",
        "secret": "test_kakao_secret_unified",
    },
    "naver": {
        "name": "Naver Test App",
        "client_id": "test_naver_client_id_unified",
        "secret": "test_naver_secret_unified",
    },
}

PROVIDER_CONFIGS = {
    "google": {
        "email_path": ["email"],
        "uid_path": ["id"],
        "uid_to_str": False,
        "adapter_path": "allauth.socialaccount.providers.google.views.GoogleOAuth2Adapter.complete_login",
    },
    "kakao": {
        "email_path": ["kakao_account", "email"],
        "uid_path": ["id"],
        "uid_to_str": True,
        "adapter_path": "allauth.socialaccount.providers.kakao.views.KakaoOAuth2Adapter.complete_login",
    },
    "naver": {
        "email_path": ["response", "email"],
        "uid_path": ["response", "id"],
        "uid_to_str": False,
        "adapter_path": "allauth.socialaccount.providers.naver.views.NaverOAuth2Adapter.complete_login",
    },
}


@pytest.fixture(autouse=True, scope="function")
def reset_social_models(db, settings):
    """각 테스트 전후 소셜 로그인 관련 모델 정리"""
    settings.SOCIALACCOUNT_PROVIDERS = {}
    SocialAccount.objects.all().delete()
    SocialApp.objects.all().delete()
    User.objects.all().delete()
    Site.objects.exclude(id=1).delete()
    Site.objects.get_or_create(id=1, defaults={"domain": "testserver", "name": "testserver"})
    yield
    SocialAccount.objects.all().delete()
    SocialApp.objects.all().delete()
    User.objects.all().delete()


# ==========================================
# JWT 토큰 Fixtures
# ==========================================


@pytest.fixture
def expired_access_token(user):
    """만료된 Access Token (30분 경과)"""
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(minutes=-30, seconds=-1))
    return str(token)


@pytest.fixture
def expired_refresh_token(user):
    """만료된 Refresh Token (7일 경과)"""
    token = RefreshToken.for_user(user)
    token.set_exp(lifetime=timedelta(days=-7, seconds=-1))
    return str(token)


@pytest.fixture
def invalid_token():
    """형식이 잘못된 JWT 토큰"""
    return "this_is_not_a_valid_jwt_token_12345"


@pytest.fixture
def tampered_token(user):
    """서명이 조작된 JWT 토큰 (보안 테스트용)"""
    token = AccessToken.for_user(user)
    token_str = str(token)
    parts = token_str.split(".")

    if len(parts) != 3:
        return token_str + "tampered"

    header, payload, signature = parts
    payload_padded = payload + "=" * (4 - len(payload) % 4)
    try:
        decoded_payload = base64.urlsafe_b64decode(payload_padded)
        payload_data = json.loads(decoded_payload)
        payload_data["user_id"] = 99999
        tampered_payload_bytes = json.dumps(payload_data).encode("utf-8")
        tampered_payload = base64.urlsafe_b64encode(tampered_payload_bytes).decode("utf-8").rstrip("=")
        return f"{header}.{tampered_payload}.{signature}"
    except Exception:
        return f"{header}.{payload}modified.{signature}"


@pytest.fixture
def blacklisted_refresh_token(api_client, get_tokens):
    """블랙리스트에 등록된 Refresh Token (로그아웃 후 무효화)"""
    from django.urls import reverse

    tokens = get_tokens
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    api_client.post(reverse("auth-logout"), {"refresh": tokens["refresh"]})
    api_client.credentials()
    return tokens["refresh"]


# ==========================================
# 이메일 인증 Fixtures
# ==========================================


@pytest.fixture
def verification_token(unverified_user):
    """유효한 이메일 인증 토큰"""
    return EmailVerificationToken.objects.create(user=unverified_user)


@pytest.fixture
def expired_verification_token(unverified_user):
    """만료된 이메일 인증 토큰 (24시간 경과)"""
    token = EmailVerificationToken.objects.create(user=unverified_user)
    token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
    token.save()
    return token


@pytest.fixture
def used_verification_token(unverified_user):
    """이미 사용된 이메일 인증 토큰"""
    token = EmailVerificationToken.objects.create(user=unverified_user)
    token.mark_as_used()
    return token


@pytest.fixture
def recent_verification_token(unverified_user):
    """방금 생성된 인증 토큰 (1분 미만) - 재발송 제한 테스트용"""
    token = EmailVerificationToken.objects.create(user=unverified_user)
    token.created_at = timezone.now() - timedelta(seconds=30)
    token.save()
    return token


@pytest.fixture
def verification_token_factory(db):
    """이메일 인증 토큰 팩토리"""

    def _create_token(user, hours_ago=0, minutes_ago=0, is_used=False):
        token = EmailVerificationToken.objects.create(user=user)
        if hours_ago > 0 or minutes_ago > 0:
            token.created_at = timezone.now() - timedelta(hours=hours_ago, minutes=minutes_ago)
            token.save()
        if is_used:
            token.mark_as_used()
        return token

    return _create_token


# ==========================================
# 소셜 로그인 Fixtures
# ==========================================


@pytest.fixture
def social_site():
    """Django Site 객체 (소셜 앱에 필요)"""
    return Site.objects.get(id=1)


def _create_social_app(provider, social_site):
    """소셜 앱 생성 헬퍼"""
    config = SOCIAL_APP_CONFIG[provider]
    app = SocialApp.objects.create(
        provider=provider,
        name=config["name"],
        client_id=config["client_id"],
        secret=config["secret"],
    )
    app.sites.add(social_site)
    return app


@pytest.fixture
def social_app_google(db, social_site):
    """Google 소셜 앱 설정"""
    return _create_social_app("google", social_site)


@pytest.fixture
def social_app_kakao(db, social_site):
    """Kakao 소셜 앱 설정"""
    return _create_social_app("kakao", social_site)


@pytest.fixture
def social_app_naver(db, social_site):
    """Naver 소셜 앱 설정"""
    return _create_social_app("naver", social_site)


@pytest.fixture
def mock_google_oauth_data():
    """Google OAuth 응답 Mock 데이터"""
    return {
        "id": "google_user_id_123456",
        "email": "testuser@gmail.com",
        "verified_email": True,
        "name": "Test User",
        "given_name": "Test",
        "family_name": "User",
        "picture": "https://lh3.googleusercontent.com/a/default-user",
        "locale": "ko",
    }


@pytest.fixture
def mock_kakao_oauth_data():
    """Kakao OAuth 응답 Mock 데이터"""
    return {
        "id": 123456789,
        "connected_at": "2025-01-28T10:00:00Z",
        "kakao_account": {
            "profile_needs_agreement": False,
            "profile": {"nickname": "테스트유저", "profile_image_url": "http://k.kakaocdn.net/img.jpg"},
            "has_email": True,
            "email_needs_agreement": False,
            "is_email_valid": True,
            "is_email_verified": True,
            "email": "testuser@kakao.com",
        },
    }


@pytest.fixture
def mock_naver_oauth_data():
    """Naver OAuth 응답 Mock 데이터"""
    return {
        "resultcode": "00",
        "message": "success",
        "response": {
            "id": "naver_user_id_12345",
            "email": "testuser@naver.com",
            "name": "테스트",
            "nickname": "테스터",
            "profile_image": "https://ssl.pstatic.net/static/pwe/address/img_profile.png",
            "age": "20-29",
            "gender": "M",
            "birthday": "01-28",
            "birthyear": "1990",
        },
    }


# ==========================================
# 유틸리티 Fixtures
# ==========================================


@pytest.fixture
def mock_time():
    """시간 고정 유틸리티"""

    @contextmanager
    def _freeze_time(frozen_datetime):
        with patch("django.utils.timezone.now", return_value=frozen_datetime):
            yield frozen_datetime

    return _freeze_time


# ==========================================
# 비밀번호 재설정 Fixtures
# ==========================================


@pytest.fixture
def password_reset_token(user):
    """유효한 비밀번호 재설정 토큰"""
    return PasswordResetToken.objects.create(user=user)


@pytest.fixture
def expired_password_reset_token(user):
    """만료된 비밀번호 재설정 토큰 (24시간 경과)"""
    token = PasswordResetToken.objects.create(user=user)
    token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
    token.save()
    return token


# ==========================================
# 소셜 로그인 Mock 헬퍼
# ==========================================


def _create_social_login_mock(mocker, provider, oauth_data, provider_config):
    """소셜 로그인 Mock 생성 헬퍼 함수"""
    email = oauth_data
    for key in provider_config["email_path"]:
        email = email[key]

    user = User.objects.create(
        email=email,
        username=email.split("@")[0],
        is_email_verified=True,
        is_active=True,
    )

    mock_social_login = MagicMock(spec=SocialLogin)
    mock_social_login.user = user
    mock_social_login.account = MagicMock(spec=SocialAccount)
    mock_social_login.account.provider = provider

    uid = oauth_data
    for key in provider_config["uid_path"]:
        uid = uid[key]
    if provider_config.get("uid_to_str"):
        uid = str(uid)
    mock_social_login.account.uid = uid
    mock_social_login.account.extra_data = oauth_data

    mock_user = MagicMock()
    mock_user.email = email
    mock_user.username = email.split("@")[0]
    mock_user.is_email_verified = True
    mock_user.is_active = True
    mock_user.pk = None
    mock_user.id = None
    mock_user.emailaddress_set.all.return_value = []
    mock_user.emailaddress_set.filter.return_value.exists.return_value = False

    mock_social_login.user = mock_user
    mock_social_login.is_existing = False
    mock_social_login.state = MagicMock()

    mock_email = MagicMock()
    mock_email.email = email
    mock_email.verified = True
    mock_email.primary = True
    mock_social_login.email_addresses = [mock_email]

    return mocker.patch(
        provider_config["adapter_path"],
        side_effect=lambda request, app, token, **kwargs: mock_social_login,
    )


# ==========================================
# 기존 사용자 관련 Fixtures
# ==========================================


@pytest.fixture
def user_with_google_account(db, user, social_site):
    """이미 Google 계정이 연결된 사용자"""
    app = _create_social_app("google", social_site)
    SocialAccount.objects.create(
        user=user,
        provider="google",
        uid="googld_user_id_123456",
        extra_data={"email": user.email},
    )
    return user


@pytest.fixture
def user_with_multiple_social_accounts(db, user, social_site):
    """여러 소셜 계정이 연결된 사용자 (Google + Kakao + Naver)"""
    for provider_name in SOCIAL_APP_CONFIG:
        _create_social_app(provider_name, social_site)

    social_accounts = [
        ("google", "google_uid_123", "user@gmail.com"),
        ("kakao", "kakao_uid_456", "user@kakao.com"),
        ("naver", "naver_uid_789", "user@naver.com"),
    ]
    for provider, uid, email in social_accounts:
        SocialAccount.objects.create(user=user, provider=provider, uid=uid, extra_data={"email": email})

    return user


# ==========================================
# 데이터 Fixtures & Factories
# ==========================================


@pytest.fixture
def valid_login_data():
    """정상 로그인 데이터"""
    return {"username": "testuser", "password": "testpass123"}


@pytest.fixture
def valid_registration_data():
    """정상 회원가입 데이터"""
    return {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "testpass123!",
        "password2": "testpass123!",
    }


@pytest.fixture
def password_change_data():
    """비밀번호 변경 데이터"""
    return {
        "old_password": "testpass123",
        "new_password": "NewSecurePass456!",
        "new_password2": "NewSecurePass456!",
    }


@pytest.fixture
def profile_update_data():
    """프로필 업데이트 데이터"""
    return {"first_name": "수정된", "last_name": "이름", "phone_number": "010-9999-8888"}


@pytest.fixture
def registration_data_factory():
    """회원가입 데이터 팩토리"""

    def _create_data(username="newuser", email=None, password="testpass123!", **kwargs):
        email = email or f"{username}@example.com"
        return {
            "username": username,
            "email": email,
            "password": password,
            "password2": kwargs.get("password2", password),
            **kwargs,
        }

    return _create_data


@pytest.fixture
def login_data_factory():
    """로그인 데이터 팩토리"""

    def _create_data(username="testuser", password="testpass123"):
        return {"username": username, "password": password}

    return _create_data


@pytest.fixture
def password_change_data_factory():
    """비밀번호 변경 데이터 팩토리"""

    def _create_data(old_password="testpass123", new_password="NewSecurePass456!", new_password2=None):
        return {
            "old_password": old_password,
            "new_password": new_password,
            "new_password2": new_password2 or new_password,
        }

    return _create_data


@pytest.fixture
def password_reset_confirm_data_factory(user):
    """비밀번호 재설정 확인 데이터 팩토리"""

    def _create_data(token, email=None, new_password="NewSecurePass123!", new_password2=None):
        return {
            "email": email or user.email,
            "token": str(token),
            "new_password": new_password,
            "new_password2": new_password2 or new_password,
        }

    return _create_data


@pytest.fixture
def user_with_points(user):
    """특정 포인트를 가진 사용자 (5000 포인트)"""
    user.points = 5000
    user.save()
    return user


@pytest.fixture
def second_user(db):
    """두 번째 테스트 사용자"""
    return User.objects.create_user(
        username="seconduser",
        email="second@example.com",
        password="testpass123",
        is_email_verified=True,
    )
