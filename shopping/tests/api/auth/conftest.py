"""
Auth 테스트 전용 Fixture

전역 conftest.py의 fixture는 그대로 사용하고,
auth 테스트에만 필요한 특화된 fixture를 정의합니다.

사용 가능한 전역 fixture:
- api_client: DRF APIClient
- user: 기본 사용자 (인증 완료)
- seller_user: 판매자 사용자
- unverified_user: 이메일 미인증 사용자
- inactive_user: 비활성화된 사용자
- withdrawn_user: 탈퇴한 사용자
- authenticated_client: 인증된 클라이언트
- seller_authenticated_client: 판매자 인증 클라이언트
- get_tokens: JWT 토큰 발급 헬퍼
- user_factory: 사용자 생성 팩토리
"""

import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock

from django.contrib.sites.models import Site
from django.utils import timezone

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from shopping.models.email_verification import EmailVerificationToken
from shopping.models.user import User

# ==========================================
# 소셜 앱 설정 상수 (모든 fixture에서 동일하게 사용)
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


@pytest.fixture(autouse=True, scope="function")
def reset_social_models(db, settings):
    """
    각 테스트 전후 소셜 로그인 관련 모델 정리

    단위 테스트용 - 간단한 정리만 수행
    """
    # 기본 설정
    settings.SOCIALACCOUNT_PROVIDERS = {}

    # 데이터 정리
    SocialAccount.objects.all().delete()
    SocialApp.objects.all().delete()
    User.objects.all().delete()

    # Site 설정
    Site.objects.exclude(id=1).delete()
    Site.objects.get_or_create(id=1, defaults={"domain": "testserver", "name": "testserver"})

    yield

    # 테스트 후 정리
    SocialAccount.objects.all().delete()
    SocialApp.objects.all().delete()
    User.objects.all().delete()


# ==========================================
# 1. JWT 토큰 관련 Fixture
# ==========================================


@pytest.fixture
def expired_access_token(user):
    """
    만료된 Access Token (30분 경과)

    사용 예시:
        def test_expired_token(api_client, expired_access_token):
            api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {expired_access_token}")
            response = api_client.get("/api/auth/profile/")
            assert response.status_code == 401
    """
    token = AccessToken.for_user(user)
    # 30분 + 1초 전에 만료되도록 설정
    token.set_exp(lifetime=timedelta(minutes=-30, seconds=-1))
    return str(token)


@pytest.fixture
def expired_refresh_token(user):
    """
    만료된 Refresh Token (7일 경과)

    사용 예시:
        def test_refresh_with_expired_token(api_client, expired_refresh_token):
            response = api_client.post("/api/auth/token/refresh/",
                                      {"refresh": expired_refresh_token})
            assert response.status_code == 401
    """
    token = RefreshToken.for_user(user)
    # 7일 + 1초 전에 만료되도록 설정
    token.set_exp(lifetime=timedelta(days=-7, seconds=-1))
    return str(token)


@pytest.fixture
def invalid_token():
    """
    형식이 잘못된 JWT 토큰

    완전히 잘못된 문자열 (JWT 형식이 아님)

    사용 예시:
        def test_malformed_token(api_client, invalid_token):
            api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {invalid_token}")
            response = api_client.get("/api/auth/profile/")
            assert response.status_code == 401
    """
    return "this_is_not_a_valid_jwt_token_12345"


@pytest.fixture
def tampered_token(user):
    """
    서명이 조작된 JWT 토큰 (보안 테스트용)

    정상적인 JWT 구조를 가지지만 서명(signature)이 변조된 토큰
    JWT는 header.payload.signature 구조인데,
    payload를 변경하고 서명은 그대로 두면 검증 실패

    사용 예시:
        def test_tampered_token(api_client, tampered_token):
            api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
            response = api_client.get("/api/auth/profile/")
            assert response.status_code == 401
    """
    # 정상 토큰 생성
    token = AccessToken.for_user(user)
    token_str = str(token)

    # JWT는 header.payload.signature 형태
    parts = token_str.split(".")

    if len(parts) == 3:
        header, payload, signature = parts

        # payload 디코딩 (padding 추가)
        payload_padded = payload + "=" * (4 - len(payload) % 4)
        try:
            decoded_payload = base64.urlsafe_b64decode(payload_padded)
            payload_data = json.loads(decoded_payload)

            # user_id를 변조 (다른 사용자 ID로 변경)
            payload_data["user_id"] = 99999  # 존재하지 않는 ID

            # 변조된 payload를 다시 인코딩
            tampered_payload_bytes = json.dumps(payload_data).encode("utf-8")
            tampered_payload = base64.urlsafe_b64encode(tampered_payload_bytes).decode("utf-8").rstrip("=")

            # 변조된 토큰 생성 (서명은 그대로)
            tampered = f"{header}.{tampered_payload}.{signature}"
            return tampered

        except Exception:
            # 디코딩 실패 시 간단한 변조
            return f"{header}.{payload}modified.{signature}"

    # 형식이 이상한 경우 기본값
    return token_str + "tampered"


@pytest.fixture
def blacklisted_refresh_token(api_client, get_tokens):
    """
    블랙리스트에 등록된 Refresh Token

    로그아웃 후 무효화된 토큰 (재사용 불가)

    사용 예시:
        def test_use_blacklisted_token(api_client, blacklisted_refresh_token):
            response = api_client.post("/api/auth/token/refresh/",
                                      {"refresh": blacklisted_refresh_token})
            assert response.status_code == 401
    """
    from django.urls import reverse

    tokens = get_tokens
    access_token = tokens["access"]
    refresh_token = tokens["refresh"]

    # 인증 설정
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

    # 로그아웃하여 refresh token을 블랙리스트에 추가
    logout_url = reverse("auth-logout")
    api_client.post(logout_url, {"refresh": refresh_token})

    # 인증 헤더 제거 (다음 테스트를 위해)
    api_client.credentials()

    return refresh_token


# ==========================================
# 2. 이메일 인증 관련 Fixture
# ==========================================


@pytest.fixture
def verification_token(unverified_user):
    """
    유효한 이메일 인증 토큰

    방금 생성되어 아직 사용되지 않은 정상 토큰 (24시간 이내)

    사용 예시:
        def test_verify_email_success(api_client, verification_token):
            response = api_client.get("/api/auth/email/verify/",
                                     {"token": str(verification_token.token)})
            assert response.status_code == 200
    """
    return EmailVerificationToken.objects.create(user=unverified_user)


@pytest.fixture
def expired_verification_token(unverified_user):
    """
    만료된 이메일 인증 토큰 (24시간 경과)

    사용 예시:
        def test_verify_expired_token(api_client, expired_verification_token):
            response = api_client.get("/api/auth/email/verify/",
                                     {"token": str(expired_verification_token.token)})
            assert response.status_code == 400
            assert "만료" in response.data["error"]
    """
    token = EmailVerificationToken.objects.create(user=unverified_user)
    # 24시간 + 1초 전으로 설정
    token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
    token.save()
    return token


@pytest.fixture
def used_verification_token(unverified_user):
    """
    이미 사용된 이메일 인증 토큰

    인증 완료된 토큰 (재사용 불가)

    사용 예시:
        def test_reuse_verification_token(api_client, used_verification_token):
            response = api_client.get("/api/auth/email/verify/",
                                     {"token": str(used_verification_token.token)})
            assert response.status_code == 400
            assert "이미 사용" in response.data["error"]
    """
    token = EmailVerificationToken.objects.create(user=unverified_user)
    # 사용됨 처리
    token.mark_as_used()
    return token


@pytest.fixture
def recent_verification_token(unverified_user):
    """
    방금 생성된 인증 토큰 (1분 미만)

    재발송 제한 테스트용 (1분 이내 재발송 불가)

    사용 예시:
        def test_resend_too_soon(api_client, unverified_user, recent_verification_token):
            # recent_verification_token이 이미 존재하므로
            response = api_client.post("/api/auth/email/resend/")
            assert response.status_code == 400
            assert "1분 후" in response.data["error"]
    """
    token = EmailVerificationToken.objects.create(user=unverified_user)
    # 30초 전에 생성 (1분 미만)
    token.created_at = timezone.now() - timedelta(seconds=30)
    token.save()
    return token


@pytest.fixture
def verification_token_factory(db):
    """
    이메일 인증 토큰 팩토리 (유연한 토큰 생성)

    다양한 상태의 토큰을 쉽게 생성

    사용 예시:
        def test_multiple_tokens(verification_token_factory, unverified_user):
            # 유효한 토큰
            valid_token = verification_token_factory(user=unverified_user)

            # 만료된 토큰
            expired = verification_token_factory(
                user=unverified_user,
                hours_ago=25
            )

            # 사용된 토큰
            used = verification_token_factory(
                user=unverified_user,
                is_used=True
            )
    """

    def _create_token(user, hours_ago=0, minutes_ago=0, is_used=False):
        """
        Args:
            user: 사용자 객체
            hours_ago: 몇 시간 전에 생성되었는지
            minutes_ago: 몇 분 전에 생성되었는지
            is_used: 사용 여부
        """
        token = EmailVerificationToken.objects.create(user=user)

        # 생성 시간 조정
        if hours_ago > 0 or minutes_ago > 0:
            token.created_at = timezone.now() - timedelta(hours=hours_ago, minutes=minutes_ago)
            token.save()

        # 사용됨 처리
        if is_used:
            token.mark_as_used()

        return token

    return _create_token


# ==========================================
# 3. 소셜 로그인 관련 Fixture
# ==========================================


@pytest.fixture
def social_site():
    """
    Django Site 객체 (소셜 앱에 필요)

    allauth는 django.contrib.sites를 사용하므로 필수

    - get_or_create 대신 get만 사용 (authouse fixture에서 생성)
    - 중복 생성 방지
    """
    # autouse fixture에서 이미 생성했으므로 get만 사용
    site = Site.objects.get(id=1)
    return site


@pytest.fixture
def social_app_google(db, social_site):
    """
    Google 소셜 앱 설정

    테스트용 Google OAuth 앱 설정
    get_or_create를 사용하여 중복 생성 완전 방지

    사용 예시:
        def test_google_login(api_client, social_app_google):
            # social_app_google이 설정된 상태에서 테스트
            response = api_client.post("/api/auth/social/google/", {...})
    """
    config = SOCIAL_APP_CONFIG["google"]

    # 단순 생성 (reset_social_models가 이미 정리함)
    app = SocialApp.objects.create(
        provider="google",
        name=config["name"],
        client_id=config["client_id"],
        secret=config["secret"],
    )

    # sites 관계 설정
    app.sites.add(social_site)

    return app


@pytest.fixture
def social_app_kakao(db, social_site):
    """
    Kakao 소셜 앱 설정

    테스트용 Kakao OAuth 앱 설정
    reset_social_models가 이미 정리하므로 단순 생성만 수행

    사용 예시:
        def test_kakao_login(api_client, social_app_kakao):
            response = api_client.post("/api/auth/social/kakao/", {...})
    """
    config = SOCIAL_APP_CONFIG["kakao"]

    # 단순 생성 (reset_social_models가 이미 정리함)
    app = SocialApp.objects.create(
        provider="kakao",
        name=config["name"],
        client_id=config["client_id"],
        secret=config["secret"],
    )

    # sites 관계 설정
    app.sites.add(social_site)

    return app


@pytest.fixture
def social_app_naver(db, social_site):
    """
    Naver 소셜 앱 설정

    테스트용 Naver OAuth 앱 설정
    get_or_create를 사용하여 중복 생성 완전 방지

    사용 예시:
        def test_naver_login(api_client, social_app_naver):
            response = api_client.post("/api/auth/social/naver/", {...})
    """
    config = SOCIAL_APP_CONFIG["naver"]

    # 단순 생성 (reset_social_models가 이미 정리함)
    app = SocialApp.objects.create(
        provider="naver",
        name=config["name"],
        client_id=config["client_id"],
        secret=config["secret"],
    )

    # sites 관계 설정
    app.sites.add(social_site)

    return app


@pytest.fixture
def mock_google_oauth_data():
    """
    Google OAuth 응답 Mock 데이터

    Google에서 받는 사용자 정보 형식

    사용 예시:
        def test_google_oauth(mocker, mock_google_oauth_data):
            mock = mocker.patch("allauth.socialaccount.providers.google...")
            mock.return_value = mock_google_oauth_data
    """
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
    """
    Kakao OAuth 응답 Mock 데이터

    Kakao에서 받는 사용자 정보 형식

    사용 예시:
        def test_kakao_oauth(mocker, mock_kakao_oauth_data):
            mock = mocker.patch("allauth.socialaccount.providers.kakao...")
            mock.return_value = mock_kakao_oauth_data
    """
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
    """
    Naver OAuth 응답 Mock 데이터

    Naver에서 받는 사용자 정보 형식

    사용 예시:
        def test_naver_oauth(mocker, mock_naver_oauth_data):
            mock = mocker.patch("allauth.socialaccount.providers.naver...")
            mock.return_value = mock_naver_oauth_data
    """
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
# 4. 유틸리티 Fixture
# ==========================================


@pytest.fixture
def mock_time():
    """
    시간 고정 유틸리티 (pytest-freezegun 대체)

    특정 시간으로 고정하여 시간 의존 테스트 수행

    사용 예시:
        def test_with_fixed_time(mock_time):
            frozen_time = datetime(2025, 1, 28, 10, 0, 0, tzinfo=dt_timezone.utc)
            with mock_time(frozen_time):
                # 이 블록 안에서는 시간이 고정됨
                token = EmailVerificationToken.objects.create(...)
                assert token.created_at == frozen_time
    """
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def _freeze_time(frozen_datetime):
        with patch("django.utils.timezone.now", return_value=frozen_datetime):
            yield frozen_datetime

    return _freeze_time


# ==========================================
# 5. 비밀번호 재설정 관련 Fixture
# ==========================================

from shopping.models.password_reset import PasswordResetToken


@pytest.fixture
def password_reset_token(user):
    """
    유효한 비밀번호 재설정 토큰

    방금 생성되어 아직 사용되지 않은 정상 토큰 (24시간 이내)

    사용 예시:
        def test_reset_password(api_client, password_reset_token):
            response = api_client.post("/api/auth/password/reset/confirm/", {
                "token": str(password_reset_token.token),
                "new_password": "NewPass123!",
                "new_password2": "NewPass123!"
            })
            assert response.status_code == 200
    """
    return PasswordResetToken.objects.create(user=user)


@pytest.fixture
def expired_password_reset_token(user):
    """
    만료된 비밀번호 재설정 토큰 (24시간 경과)

    사용 예시:
        def test_reset_with_expired_token(api_client, expired_password_reset_token):
            response = api_client.post("/api/auth/password/reset/confirm/", {
                "token": str(expired_password_reset_token.token),
                "new_password": "NewPass123!",
                "new_password2": "NewPass123!"
            })
            assert response.status_code == 400
            assert "만료" in response.data
    """
    token = PasswordResetToken.objects.create(user=user)
    # 24시간 + 1초 전으로 설정
    token.created_at = timezone.now() - timedelta(hours=24, seconds=1)
    token.save()
    return token


# ==========================================
# 6. 소셜 로그인 Mock 헬퍼 함수
# ==========================================


def _create_social_login_mock(mocker, provider, oauth_data, provider_config):
    """
    소셜 로그인 Mock 생성 헬퍼 함수

    - 실제 User 인스턴스 사용

    Args:
        mocker: pytest-mock의 mocker fixture
        provider: provider 이름 (google, kakao, naver)
        oauth_data: OAuth 응답 데이터
        provider_config: provider별 설정 (email_path, uid_path, adapter_path)
    """
    # email 추출 (nested path 지원)
    email = oauth_data
    for key in provider_config["email_path"]:
        email = email[key]

    # 실제 User 생성 (DB에 저장)
    user = User.objects.create(
        email=email,
        username=email.split("@")[0],
        is_email_verified=True,
        is_active=True,
    )

    # SocialLogin은 Mock
    mock_social_login = MagicMock(spec=SocialLogin)
    mock_social_login.user = user  # 실제 User
    mock_social_login.account = MagicMock(spec=SocialAccount)
    mock_social_login.account.provider = provider

    # uid 추출 (nested path 지원)
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
    mock_user.pk = None  # 신규 사용자
    mock_user.id = None
    # django-allauth가 email_addresses를 QuerySet처럼 사용
    mock_user.emailaddress_set.all.return_value = []
    mock_user.emailaddress_set.filter.return_value.exists.return_value = False

    mock_social_login.user = mock_user
    mock_social_login.is_existing = False
    mock_social_login.state = MagicMock()  # 모든 provider에 추가

    mock_email = MagicMock()
    mock_email.email = email
    mock_email.verified = True
    mock_email.primary = True
    mock_social_login.email_addresses = [mock_email]

    # 패치 방식 통일 (side_effect로)
    mock = mocker.patch(
        provider_config["adapter_path"],
        side_effect=lambda request, app, token, **kwargs: mock_social_login,
    )
    return mock


# Provider별 설정
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


# ==========================================
# 7. 기존 사용자 관련 Fixture
# ==========================================


@pytest.fixture
def user_with_google_account(db, user, social_site):
    """
    이미 Google 계정이 연결된 사용자

    소셜 계정 연결 테스트용
    reset_social_models가 이미 정리하므로 단순 생성만 수행

    사용 예시:
        def test_duplicate_social_account(user_with_google_account):
            # 이미 Google 계정이 연결된 상태에서 테스트
            assert user_with_google_account.socialaccount_set.count() == 1
    """
    config = SOCIAL_APP_CONFIG["google"]

    # Google SocialApp 생성
    app = SocialApp.objects.create(
        provider="google",
        name=config["name"],
        client_id=config["client_id"],
        secret=config["secret"],
    )

    # sites 관계 설정
    app.sites.add(social_site)

    # SocialAccount 생성
    SocialAccount.objects.create(
        user=user,
        provider="google",
        uid="googld_user_id_123456",  # mock_google_oauth_data의 id와 동일하게
        extra_data={"email": user.email},
    )

    return user


@pytest.fixture
def user_with_multiple_social_accounts(db, user, social_site):
    """
    여러 소셜 계정이 연결된 사용자

    Google + Kakao + Naver 모두 연결됨
    reset_social_models가 이미 정리하므로 단순 생성만 수행

    사용 예시:
        def test_multiple_providers(user_with_multiple_social_accounts):
            assert user_with_multiple_social_accounts.socialaccount_set.count() == 3
    """
    # 각 provider의 SocialApp 생성
    for provider_name, config in SOCIAL_APP_CONFIG.items():
        app = SocialApp.objects.create(
            provider=provider_name,
            name=config["name"],
            client_id=config["client_id"],
            secret=config["secret"],
        )

        # sites 관계 설정
        app.sites.add(social_site)

    # 각 provider에 대한 SocialAccount 생성
    social_accounts_config = [
        ("google", "google_uid_123", "user@gmail.com"),
        ("kakao", "kakao_uid_456", "user@kakao.com"),
        ("naver", "naver_uid_789", "user@naver.com"),
    ]

    for provider, uid, email in social_accounts_config:
        SocialAccount.objects.create(user=user, provider=provider, uid=uid, extra_data={"email": email})

    return user


# ==========================================
# 8. 데이터 Fixture & Factory (재사용 가능한 테스트 데이터)
# ==========================================


@pytest.fixture
def valid_login_data():
    """
    정상 로그인 데이터

    사용 예시:
        def test_login(api_client, valid_login_data):
            response = api_client.post("/api/auth/login/", valid_login_data)
    """
    return {"username": "testuser", "password": "testpass123"}


@pytest.fixture
def valid_registration_data():
    """
    정상 회원가입 데이터

    사용 예시:
        def test_register(api_client, valid_registration_data):
            response = api_client.post("/api/auth/register/", valid_registration_data)
    """
    return {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "testpass123!",
        "password2": "testpass123!",
    }


@pytest.fixture
def password_change_data():
    """
    비밀번호 변경 데이터

    사용 예시:
        def test_change_password(authenticated_client, password_change_data):
            response = authenticated_client.post("/api/auth/password/change/", password_change_data)
    """
    return {
        "old_password": "testpass123",
        "new_password": "NewSecurePass456!",
        "new_password2": "NewSecurePass456!",
    }


@pytest.fixture
def profile_update_data():
    """
    프로필 업데이트 데이터

    사용 예시:
        def test_update_profile(authenticated_client, profile_update_data):
            response = authenticated_client.patch("/api/auth/profile/", profile_update_data)
    """
    return {
        "first_name": "수정된",
        "last_name": "이름",
        "phone_number": "010-9999-8888",
    }


@pytest.fixture
def registration_data_factory():
    """
    회원가입 데이터 팩토리 - 유연한 생성

    사용 예시:
        def test_multiple_users(api_client, registration_data_factory):
            data1 = registration_data_factory(username="user1")
            data2 = registration_data_factory(username="user2", email="custom@example.com")
    """

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
    """
    로그인 데이터 팩토리

    사용 예시:
        def test_login(api_client, login_data_factory):
            data = login_data_factory(username="testuser", password="testpass123")
    """

    def _create_data(username="testuser", password="testpass123"):
        return {"username": username, "password": password}

    return _create_data


@pytest.fixture
def password_change_data_factory():
    """
    비밀번호 변경 데이터 팩토리

    사용 예시:
        def test_change_password(authenticated_client, password_change_data_factory):
            data = password_change_data_factory(
                old_password="testpass123",
                new_password="NewPass456!"
            )
    """

    def _create_data(old_password="testpass123", new_password="NewSecurePass456!", new_password2=None):
        return {
            "old_password": old_password,
            "new_password": new_password,
            "new_password2": new_password2 or new_password,
        }

    return _create_data


@pytest.fixture
def password_reset_confirm_data_factory(user):
    """
    비밀번호 재설정 확인 데이터 팩토리

    사용 예시:
        def test_reset_password(api_client, password_reset_token, password_reset_confirm_data_factory):
            data = password_reset_confirm_data_factory(
                token=password_reset_token.token,
                new_password="NewPass123!"
            )
            response = api_client.post("/api/auth/password/reset/confirm/", data)
    """

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
    """
    특정 포인트를 가진 사용자 (5000 포인트)

    사용 예시:
        def test_withdrawal_with_points(authenticated_client, user_with_points):
            assert user_with_points.points == 5000
    """
    user.points = 5000
    user.save()
    return user


@pytest.fixture
def second_user(db):
    """
    두 번째 테스트 사용자

    사용 예시:
        def test_different_users(user, second_user):
            assert user.id != second_user.id
    """
    return User.objects.create_user(
        username="seconduser",
        email="second@example.com",
        password="testpass123",
        is_email_verified=True,
    )
