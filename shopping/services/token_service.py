"""토큰 서비스 레이어

JWT 토큰 관련 비즈니스 로직을 처리합니다.
- Refresh Token 검증 및 갱신
- 토큰 블랙리스트 처리
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import jwt as pyjwt
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)


class TokenServiceError(Exception):
    """토큰 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "TOKEN_SERVICE_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class TokenRefreshResult:
    """토큰 갱신 결과"""

    access_token: str
    refresh_token: str | None = None  # ROTATE_REFRESH_TOKENS=True인 경우만


class TokenService:
    """JWT 토큰 관련 비즈니스 로직을 처리하는 서비스"""

    @staticmethod
    def validate_and_refresh_token(refresh_token: str) -> TokenRefreshResult:
        """
        Refresh Token을 검증하고 새로운 Access Token을 발급한다.

        검증 순서:
        1. 토큰 형식 검증 (JWT 디코딩)
        2. 블랙리스트 확인
        3. SimpleJWT 토큰 검증 (만료, 서명 등)
        4. 새 토큰 발급

        Args:
            refresh_token: 검증할 Refresh Token

        Returns:
            TokenRefreshResult: 새로운 access_token, refresh_token(회전 설정 시)

        Raises:
            TokenServiceError: 토큰이 유효하지 않은 경우
        """
        if not refresh_token:
            raise TokenServiceError("refresh token이 필요합니다.", code="TOKEN_REQUIRED")

        # 1. JWT 형식 검증 및 jti 추출
        try:
            decoded = pyjwt.decode(refresh_token, options={"verify_signature": False})
            jti = decoded.get("jti")
        except pyjwt.exceptions.DecodeError:
            logger.warning("토큰 형식 오류: 잘못된 JWT 형식")
            raise TokenServiceError("Invalid token format", code="INVALID_FORMAT")

        # 2. 블랙리스트 확인
        if jti and BlacklistedToken.objects.filter(token__jti=jti).exists():
            logger.warning(f"블랙리스트된 토큰 사용 시도: jti={jti}")
            raise TokenServiceError("Token is blacklisted", code="TOKEN_BLACKLISTED")

        # 3. SimpleJWT 토큰 검증 (만료, 서명 등)
        try:
            token = RefreshToken(refresh_token)
        except TokenError as e:
            logger.warning(f"토큰 검증 실패: {e.args[0]}")
            raise TokenServiceError(str(e.args[0]), code="TOKEN_INVALID")

        # 4. 새 토큰 발급
        access_token = str(token.access_token)

        # ROTATE_REFRESH_TOKENS=True인 경우 새 refresh token 생성
        new_refresh_token = None
        from django.conf import settings

        if getattr(settings, "SIMPLE_JWT", {}).get("ROTATE_REFRESH_TOKENS", False):
            # 기존 토큰 블랙리스트 추가 (BLACKLIST_AFTER_ROTATION=True인 경우)
            if getattr(settings, "SIMPLE_JWT", {}).get("BLACKLIST_AFTER_ROTATION", False):
                try:
                    token.blacklist()
                except Exception:
                    pass  # 이미 블랙리스트에 있거나 실패해도 무시

            # 새 refresh token 생성
            token.set_jti()
            token.set_exp()
            new_refresh_token = str(token)

        logger.info("토큰 갱신 완료")
        return TokenRefreshResult(
            access_token=access_token,
            refresh_token=new_refresh_token,
        )

    @staticmethod
    def blacklist_token(refresh_token: str) -> bool:
        """
        Refresh Token을 블랙리스트에 추가한다.

        Args:
            refresh_token: 블랙리스트에 추가할 Refresh Token

        Returns:
            bool: 블랙리스트 추가 성공 여부

        Raises:
            TokenServiceError: 토큰이 유효하지 않은 경우
        """
        if not refresh_token:
            raise TokenServiceError("Refresh token이 필요합니다.", code="TOKEN_REQUIRED")

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            logger.info("토큰 블랙리스트 추가 완료")
            return True
        except TokenError as e:
            logger.warning(f"토큰 블랙리스트 추가 실패: {e.args[0]}")
            raise TokenServiceError(str(e.args[0]), code="TOKEN_INVALID")
