"""토큰 서비스 레이어

JWT 토큰 관련 비즈니스 로직을 처리합니다.
- Refresh Token 검증 및 갱신
- 토큰 블랙리스트 처리
- 사용자 토큰 전체 무효화 (비밀번호 변경·재설정·회원 탈퇴)
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
        4. 계정 상태 확인 (탈퇴·비활성 계정 거부)
        5. 회전 시 제출된 토큰 차지 (블랙리스트 행을 만든 요청 하나만 통과)
        6. 새 토큰 발급 (새 refresh 토큰은 발급 목록에 등록)

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

        # 4. 계정 상태 확인 (SimpleJWT 기본 갱신 뷰와 같은 규칙)
        TokenService._ensure_user_active(token)

        from django.conf import settings

        jwt_settings = getattr(settings, "SIMPLE_JWT", {})
        rotate = jwt_settings.get("ROTATE_REFRESH_TOKENS", False)

        # 5. 제출된 토큰 차지: 같은 토큰으로 동시에 들어온 요청 중 블랙리스트 행을 만든
        #    요청 하나만 새 토큰을 받는다. 2번 확인과 여기 사이의 틈은 행의 유일 제약이 판정한다.
        if rotate and jwt_settings.get("BLACKLIST_AFTER_ROTATION", False):
            _, claimed = token.blacklist()
            if not claimed:
                logger.warning(f"이미 회전된 토큰으로 갱신 시도: jti={jti}")
                raise TokenServiceError("Token is blacklisted", code="TOKEN_BLACKLISTED")

        # 6. 새 토큰 발급
        access_token = str(token.access_token)

        new_refresh_token = None
        if rotate:
            token.set_jti()
            token.set_exp()
            token.set_iat()
            # 새 refresh 토큰을 발급 목록에 등록 — 전체 무효화(revoke_all_for_user)가 이 목록을 본다
            token.outstand()
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

    @staticmethod
    def revoke_all_for_user(user, keep_refresh_token: str | None = None) -> int:
        """
        사용자의 살아 있는 Refresh Token을 모두 블랙리스트에 추가한다.

        비밀번호 변경·재설정, 회원 탈퇴에서 호출한다. 로그인과 회전으로 발급한 토큰은
        모두 발급 목록(OutstandingToken)에 있으므로 여기서 한 번에 찾는다.

        Args:
            user: 대상 사용자
            keep_refresh_token: 남겨 둘 토큰 (비밀번호를 바꾼 현재 기기의 토큰)

        Returns:
            int: 새로 블랙리스트에 추가한 토큰 수
        """
        from django.utils import timezone
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        keep_jti = None
        if keep_refresh_token:
            try:
                keep_jti = RefreshToken(keep_refresh_token)["jti"]
            except TokenError:
                keep_jti = None

        pending = OutstandingToken.objects.filter(
            user=user,
            expires_at__gt=timezone.now(),
            blacklistedtoken__isnull=True,
        )
        if keep_jti:
            pending = pending.exclude(jti=keep_jti)

        rows = [BlacklistedToken(token=outstanding) for outstanding in pending]
        BlacklistedToken.objects.bulk_create(rows, ignore_conflicts=True)

        logger.info(f"사용자 토큰 전체 무효화: user_id={user.pk}, count={len(rows)}, kept={keep_jti is not None}")
        return len(rows)

    @staticmethod
    def _ensure_user_active(token: RefreshToken) -> None:
        """토큰 주인이 아직 로그인할 수 있는 계정인지 확인한다."""
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.settings import api_settings

        user_id = token.payload.get(api_settings.USER_ID_CLAIM)
        user = None
        if user_id is not None:
            user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()

        if user is None or not api_settings.USER_AUTHENTICATION_RULE(user):
            logger.warning(f"비활성 계정의 토큰 갱신 시도: user_id={user_id}")
            raise TokenServiceError("No active account found for the given token.", code="USER_INACTIVE")
