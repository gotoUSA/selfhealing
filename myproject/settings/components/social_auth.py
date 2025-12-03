"""
Social Authentication Configuration (django-allauth 65.12+)
소셜 로그인 관련 모든 설정을 관리합니다.

배포 가이드: docs/OAUTH_DEPLOYMENT_GUIDE.md 참조
"""

import os

# ==========================================
# Django Sites Framework (allauth 필수)
# ==========================================
SITE_ID = 1

# ==========================================
# allauth 기본 설정
# ==========================================

ACCOUNT_SIGNUP_FIELDS = [
    "email*",  # * = 필수
    "username*",  # * = 필수
    "password1*",
    "password2*",
]

ACCOUNT_EMAIL_VERIFICATION = "none"  # 소셜 로그인은 자동 인증
ACCOUNT_LOGIN_METHODS = {"email"}  # set 타입

# ==========================================
# 소셜 로그인 설정
# ==========================================
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_LOGIN_ON_GET = True

# ==========================================
# dj-rest-auth 7.0+ 설정
# ==========================================
# JWT_AUTH_SECURE는 production.py에서 True로 오버라이드됨
REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_HTTPONLY": False,  # 프론트엔드에서 토큰 직접 관리
    "JWT_AUTH_COOKIE": None,  # 쿠키 미사용
    "USER_DETAILS_SERIALIZER": "shopping.serializers.user_serializers.UserSerializer",
    "JWT_AUTH_COOKIE_USE_CSRF": False,
    "JWT_AUTH_SECURE": os.getenv("DJANGO_ENV", "local") == "production",  # 프로덕션에서만 True
    "JWT_AUTH_SAMESITE": "Lax",  # CSRF 보호
}

# JWT 토큰 모델 미사용 (SimpleJWT 사용)
REST_AUTH_TOKEN_MODEL = None
REST_AUTH_TOKEN_CREATOR = None

# allauth 어댑터 (기본값이지만 명시)
ACCOUNT_ADAPTER = "allauth.account.adapter.DefaultAccountAdapter"
SOCIALACCOUNT_ADAPTER = "shopping.adapters.CustomSocialAccountAdapter"

# ==========================================
# 소셜 로그인 제공자별 설정
# ==========================================
# 각 제공자의 Client ID/Secret은 환경변수로 관리
# 발급 방법: docs/OAUTH_DEPLOYMENT_GUIDE.md 참조
#
# Google: https://console.cloud.google.com/apis/credentials
# Kakao: https://developers.kakao.com/console/app
# Naver: https://developers.naver.com/apps

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
        },
        "APP": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
            "secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
    },
    "kakao": {
        # Kakao 동의항목에서 이메일 필수 동의 설정 필요
        "APP": {
            "client_id": os.getenv("KAKAO_REST_API_KEY", ""),
            "secret": os.getenv("KAKAO_CLIENT_SECRET", ""),
            "key": "",
        },
    },
    "naver": {
        # Naver는 검수 완료 후 일반 사용자 로그인 가능
        "APP": {
            "client_id": os.getenv("NAVER_CLIENT_ID", ""),
            "secret": os.getenv("NAVER_CLIENT_SECRET", ""),
            "key": "",
        },
    },
}

# 소셜 로그인 리다이렉트 URI (프론트엔드 콜백)
# 프로덕션: https://yourdomain.com/auth/callback
# 개발: http://localhost:3000/auth/callback
SOCIAL_LOGIN_REDIRECT_URI = os.getenv("SOCIAL_LOGIN_REDIRECT_URI", "http://localhost:8000/social/test/")

# ==========================================
# OAuth 서비스 설정 (SocialAuthService)
# ==========================================

# OAuth 콜백 URI (OAuth 제공자 콘솔에 등록한 것과 정확히 일치해야 함)
OAUTH_CALLBACK_URI = os.getenv("OAUTH_CALLBACK_URI", "http://localhost:8000/api/social/callback/")

# OAuth API 요청 타임아웃 (초)
OAUTH_REQUEST_TIMEOUT = int(os.getenv("OAUTH_REQUEST_TIMEOUT", "10"))

# OAuth API 요청 재시도 설정
OAUTH_RETRY_ATTEMPTS = int(os.getenv("OAUTH_RETRY_ATTEMPTS", "3"))
OAUTH_RETRY_WAIT_SECONDS = int(os.getenv("OAUTH_RETRY_WAIT_SECONDS", "1"))
