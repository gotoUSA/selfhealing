"""
Schemathesis 테스트 전용 설정

OpenAPI 스키마 기반 자동 API 테스트를 위한 fixture들
"""

import pytest
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def auth_token(user):
    """
    Schemathesis 테스트용 JWT Access Token 생성

    user fixture를 사용하여 유효한 JWT 토큰 발급
    """
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def seller_auth_token(seller_user):
    """
    판매자용 JWT Access Token 생성

    상품 등록/수정 등 판매자 권한 필요 엔드포인트 테스트용
    """
    refresh = RefreshToken.for_user(seller_user)
    return str(refresh.access_token)


@pytest.fixture
def auth_headers(auth_token):
    """
    인증 헤더 딕셔너리 반환

    Schemathesis case.call(headers=auth_headers)에서 사용
    """
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def seller_auth_headers(seller_auth_token):
    """
    판매자 인증 헤더 딕셔너리 반환
    """
    return {"Authorization": f"Bearer {seller_auth_token}"}


# 제외할 엔드포인트 패턴
EXCLUDED_ENDPOINTS = [
    # Webhook - 외부 서비스 콜백
    "/api/payments/toss/webhook/",
    # 이메일 발송 관련
    "/api/auth/password-reset/",
    "/api/auth/verify-email/",
]

# 인증 불필요 엔드포인트 패턴
PUBLIC_ENDPOINTS = [
    "/api/products/",
    "/api/categories/",
    "/api/auth/register/",
    "/api/auth/login/",
    "/api/schema/",
]
